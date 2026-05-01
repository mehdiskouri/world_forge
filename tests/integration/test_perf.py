"""Performance smoke for the integration suite (NF-1 budgets).

These tests are gated on ``$FORGE_BLENDER_BIN`` *and* tagged ``slow``;
the canonical headless run filters them out (``-m "not slow"``). They
exist so a Blender-equipped host can sanity-check the v1 budgets
without standing up the full bench harness.
"""

from __future__ import annotations

import time
from pathlib import Path  # noqa: TC003 - used as runtime constructor
from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.server.tools.generation import generate_region, render_view

from tests.integration.conftest import bootstrap_region

if TYPE_CHECKING:
    from forge_mcp.project.service import ProjectService

GENERATE_BUDGET_SECONDS = 60.0
RENDER_BUDGET_SECONDS = 20.0


def _ok(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is True, envelope
    return cast("dict[str, object]", envelope["result"])


@pytest.mark.slow
@pytest.mark.blender_integration
def test_generate_region_under_60s(
    tmp_path: Path,
    isolated_service: ProjectService,  # noqa: ARG001
    real_blender_factory: None,  # noqa: ARG001
) -> None:
    rid = bootstrap_region(tmp_path)
    start = time.monotonic()
    _ok(generate_region(rid))
    elapsed = time.monotonic() - start
    assert elapsed < GENERATE_BUDGET_SECONDS, (
        f"generate_region exceeded NF-1.1 budget: {elapsed:.2f}s > {GENERATE_BUDGET_SECONDS}s"
    )


@pytest.mark.slow
@pytest.mark.blender_integration
def test_render_view_default_under_20s(
    tmp_path: Path,
    isolated_service: ProjectService,  # noqa: ARG001
    real_blender_factory: None,  # noqa: ARG001
) -> None:
    rid = bootstrap_region(tmp_path)
    _ok(generate_region(rid))
    start = time.monotonic()
    _ok(render_view(rid, view_kind="perspective_se", resolution="default"))
    elapsed = time.monotonic() - start
    assert elapsed < RENDER_BUDGET_SECONDS, (
        f"render_view exceeded budget: {elapsed:.2f}s > {RENDER_BUDGET_SECONDS}s"
    )
