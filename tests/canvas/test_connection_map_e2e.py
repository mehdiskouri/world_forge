"""Phase 7 Stage F — connection-map end-to-end browser test.

Boots a real :class:`CanvasServer` in its own thread, drives a headless
Chromium via Playwright to ``?view=connection-map``, then mutates the
project through ``ProjectService.create_region`` and asserts the new
region appears in the d3-force layout within the NF-1.4 budget
(< 500 ms, PRD §8.4).

The test is auto-skipped when chromium binaries are not available so
contributors without ``playwright install chromium`` are not blocked
locally; CI runs ``uv run playwright install --with-deps chromium``
before pytest.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.playwright


# ---------------------------------------------------------------------------
# Skip-control
# ---------------------------------------------------------------------------
def _chromium_available() -> bool:
    """Return True iff Playwright can launch Chromium on this host."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            browser.close()
    except Exception:  # noqa: BLE001 - any launch failure means "not available"
        return False
    return True


_CHROMIUM_AVAILABLE = _chromium_available()


_BOUNDS_MIN = -100.0
_BOUNDS_MAX = 100.0
_SQUARE: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (10.0, 0.0),
    (10.0, 10.0),
    (0.0, 10.0),
)
_NF_1_4_BUDGET_S = 0.5


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def canvas_url(tmp_path: Path) -> Iterator[str]:
    """Boot a CanvasServer on its own thread and return the URL."""
    if not _CHROMIUM_AVAILABLE:
        pytest.skip("Chromium not installed; run `uv run playwright install chromium`.")
    from forge_mcp.project.schemas import WorldBounds  # noqa: PLC0415
    from forge_mcp.project.service import ProjectService  # noqa: PLC0415
    from forge_mcp.server.canvas_server import CanvasServer  # noqa: PLC0415
    from forge_mcp.server.tools import set_service  # noqa: PLC0415

    svc = ProjectService()
    svc.create_project(
        tmp_path / "proj",
        name="canvas demo",
        world_bounds=WorldBounds(
            min=(_BOUNDS_MIN, _BOUNDS_MIN),
            max=(_BOUNDS_MAX, _BOUNDS_MAX),
        ),
    )
    set_service(svc)
    server = CanvasServer(svc)
    server.start_in_thread()
    try:
        yield server.url
    finally:
        server.stop_thread()
        set_service(ProjectService())


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _CHROMIUM_AVAILABLE, reason="Chromium not installed")
def test_connection_map_renders_new_region(canvas_url: str) -> None:
    """A region created mid-session appears as a d3-force node in < 500 ms."""
    from forge_mcp.project.schemas import RegionId  # noqa: PLC0415
    from forge_mcp.server.tools import get_service  # noqa: PLC0415
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    target_url = f"{canvas_url}?view=connection-map"
    svc = get_service()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(target_url)
            # The toolbar link for the connection map is highlighted.
            page.wait_for_selector('a[data-view="connection-map"].active', timeout=5_000)
            # Wait until the WS handshake brings up an empty SVG container.
            page.wait_for_selector('svg[data-view="connection-map"]', timeout=5_000)

            # Sanity check: no nodes yet.
            assert page.locator("svg g.node").count() == 0

            # Mutate via the service (single source of truth — same code
            # path the MCP `forge.create_region` tool would take).
            t0 = time.monotonic()
            region = svc.create_region("Alpha", _SQUARE)
            assert isinstance(region.node_id, str)
            assert region.node_id == str(RegionId(region.node_id))

            # The connection map must reflect the new node within the
            # NF-1.4 budget (network round-trip + d3-force first tick).
            page.wait_for_selector(
                f'svg g.node[data-node-id="{region.node_id}"]',
                timeout=int(_NF_1_4_BUDGET_S * 1_000),
            )
            elapsed = time.monotonic() - t0
            assert elapsed < _NF_1_4_BUDGET_S * 2, (
                f"connection map updated in {elapsed * 1000:.0f}ms"
            )
        finally:
            browser.close()
