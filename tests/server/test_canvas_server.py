"""Tests for the Phase-6 Stage D embedded canvas server.

The tests stand up a real :class:`CanvasServer` against a freshly
created :class:`ProjectService`, exercise the HTTP and WebSocket
endpoints over a loopback socket, and assert the contract documented
in ``AGENT/dev_phases/phase6.md`` §Stage D:

* binds to 127.0.0.1 only;
* writes ``<project>/.forge/canvas.lock`` while running, removes it on
  stop;
* serves ``GET /healthz``, ``GET /api/state``, ``GET /api/canvas-state``;
* delegates region CRUD to ``ProjectService`` with identical errors;
* broadcasts a snapshot on WebSocket connect and a JSON-Patch envelope
  on every subsequent mutation, all within NF-1.4 (< 500 ms).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, cast

import httpx
import pytest
import websockets
from forge_mcp.project.service import ProjectService
from forge_mcp.server.canvas_server import (
    CANVAS_LOCK_FILENAME,
    CanvasServer,
    CanvasServerHostError,
)
from forge_mcp.server.tools import set_service
from forge_mcp.server.tools.canvas import (
    canvas_status,
    canvas_url,
    get_canvas_server,
    set_canvas_server,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

_BOUNDS = {"min": [0.0, 0.0], "max": [10.0, 10.0]}
_SQUARE_A = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
_SQUARE_B = [[2.0, 0.0], [3.0, 0.0], [3.0, 1.0], [2.0, 1.0]]

_HTTP_OK = 200
_HTTP_CREATED = 201
_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409
_NF_1_4_LATENCY_BUDGET_S = 0.5


def _make_service(tmp_path: Path) -> ProjectService:
    """Build a service with an open project (canvas requires it)."""
    svc = ProjectService()
    from forge_mcp.project.schemas import WorldBounds  # noqa: PLC0415 - local narrow

    svc.create_project(
        tmp_path / "proj",
        name="canvas demo",
        world_bounds=WorldBounds.model_validate(_BOUNDS),
    )
    return svc


@pytest.fixture(autouse=True)
def _reset_canvas_singleton() -> None:
    """Ensure the canvas-tool singleton does not leak between tests."""
    set_canvas_server(None)
    set_service(ProjectService())


@pytest.fixture
async def canvas(tmp_path: Path) -> AsyncIterator[CanvasServer]:
    """Boot a real canvas server bound to a free loopback port."""
    svc = _make_service(tmp_path)
    set_service(svc)
    server = CanvasServer(svc)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


def test_constructor_rejects_non_loopback_host() -> None:
    svc = ProjectService()
    with pytest.raises(CanvasServerHostError):
        CanvasServer(svc, host="0.0.0.0")  # noqa: S104 - intentional bad input


async def test_healthz_and_bind_address(canvas: CanvasServer) -> None:
    assert canvas.host == "127.0.0.1"
    async with httpx.AsyncClient(base_url=canvas.url) as client:
        response = await client.get("healthz")
    assert response.status_code == _HTTP_OK
    body = response.json()
    assert body["ok"] is True
    assert body["project_open"] is True
    assert body["connected_clients"] == 0


async def test_lock_file_written_and_cleaned(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    set_service(svc)
    server = CanvasServer(svc)
    lock_path = svc.state.paths.root / ".forge" / CANVAS_LOCK_FILENAME
    assert not lock_path.exists()
    await server.start()
    try:
        assert lock_path.exists()
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["url"] == server.url
        assert payload["host"] == "127.0.0.1"
        assert payload["port"] == server.port
    finally:
        await server.stop()
    assert not lock_path.exists()


async def test_state_endpoint_returns_snapshot(canvas: CanvasServer) -> None:
    async with httpx.AsyncClient(base_url=canvas.url) as client:
        response = await client.get("api/state")
        canvas_state = await client.get("api/canvas-state")
    body = response.json()
    assert body["project_open"] is True
    assert body["regions"] == []
    assert body["boundaries"] == []
    assert canvas_state.json() == body


async def test_index_returns_placeholder(canvas: CanvasServer) -> None:
    async with httpx.AsyncClient(base_url=canvas.url) as client:
        response = await client.get("/")
    assert response.status_code == _HTTP_OK
    assert "Forge canvas" in response.text


async def test_create_region_round_trip(canvas: CanvasServer) -> None:
    async with httpx.AsyncClient(base_url=canvas.url) as client:
        create = await client.post(
            "api/regions",
            json={"name": "Alpha", "polygon_coords": _SQUARE_A},
        )
        assert create.status_code == _HTTP_CREATED
        region_id = cast("str", create.json()["node_id"])
        listing = await client.get("api/state")
        patch = await client.patch(
            f"api/regions/{region_id}",
            json={"name": "Alpha Renamed"},
        )
        delete = await client.delete(f"api/regions/{region_id}")
    assert any(r["name"] == "Alpha" for r in listing.json()["regions"])
    assert patch.status_code == _HTTP_OK
    assert patch.json()["name"] == "Alpha Renamed"
    assert delete.status_code == _HTTP_OK


async def test_create_region_overlap_returns_409(canvas: CanvasServer) -> None:
    async with httpx.AsyncClient(base_url=canvas.url) as client:
        first = await client.post(
            "api/regions",
            json={"name": "A", "polygon_coords": _SQUARE_A},
        )
        assert first.status_code == _HTTP_CREATED
        dup = await client.post(
            "api/regions",
            json={"name": "B", "polygon_coords": _SQUARE_A},
        )
    assert dup.status_code == _HTTP_CONFLICT
    assert dup.json()["error"]["code"] == "region_overlap"


async def test_update_unknown_region_returns_404(canvas: CanvasServer) -> None:
    async with httpx.AsyncClient(base_url=canvas.url) as client:
        response = await client.patch(
            "api/regions/region-does-not-exist",
            json={"name": "ghost"},
        )
    assert response.status_code == _HTTP_NOT_FOUND
    assert response.json()["error"]["code"] == "unknown_region"


async def test_websocket_snapshot_and_patch_broadcast(
    canvas: CanvasServer,
) -> None:
    ws_url = canvas.url.replace("http://", "ws://") + "ws"
    async with websockets.connect(ws_url) as ws:
        snapshot_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
        assert snapshot_msg["type"] == "snapshot"
        assert snapshot_msg["state"]["project_open"] is True

        # Trigger a mutation from outside the server thread.
        async with httpx.AsyncClient(base_url=canvas.url) as client:
            start = time.perf_counter()
            response = await client.post(
                "api/regions",
                json={"name": "Live", "polygon_coords": _SQUARE_B},
            )
            assert response.status_code == _HTTP_CREATED

            patch_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
        elapsed = time.perf_counter() - start
        assert patch_msg["type"] == "patch"
        assert patch_msg["event"] == "create_region"
        assert patch_msg["ops"]
        assert elapsed < _NF_1_4_LATENCY_BUDGET_S, (
            f"NF-1.4 latency budget exceeded ({elapsed:.3f}s)"
        )


async def test_websocket_ping_pong(canvas: CanvasServer) -> None:
    ws_url = canvas.url.replace("http://", "ws://") + "ws"
    async with websockets.connect(ws_url) as ws:
        await asyncio.wait_for(ws.recv(), timeout=2.0)  # snapshot
        await ws.send(json.dumps({"type": "ping"}))
        pong = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
    assert pong["type"] == "pong"


async def test_canvas_status_tool_reports_running_state(
    canvas: CanvasServer,
) -> None:
    set_canvas_server(canvas)
    envelope = canvas_status()
    assert envelope["ok"] is True
    result = cast("dict[str, object]", envelope["result"])
    assert result["running"] is True
    assert result["url"] == canvas.url
    assert result["connected_clients"] == 0


def test_canvas_url_tool_requires_open_project() -> None:
    envelope = canvas_url()
    assert envelope["ok"] is False
    assert cast("dict[str, object]", envelope["error"])["code"] == "no_open_project"


def test_canvas_status_tool_when_not_running() -> None:
    envelope = canvas_status()
    assert envelope["ok"] is True
    result = cast("dict[str, object]", envelope["result"])
    assert result["running"] is False
    assert result["url"] is None
    assert result["connected_clients"] == 0


def test_canvas_url_tool_starts_server_in_thread(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    set_service(svc)
    try:
        envelope = canvas_url()
        assert envelope["ok"] is True
        result = cast("dict[str, object]", envelope["result"])
        url = cast("str", result["url"])
        assert url.startswith("http://127.0.0.1:")
        # healthz must be reachable from the calling thread.
        with httpx.Client(base_url=url) as client:
            response = client.get("healthz")
        assert response.status_code == _HTTP_OK

        server = get_canvas_server()
        assert server is not None
        # Second call returns the same URL (singleton reuse).
        again = canvas_url()
        assert cast("dict[str, object]", again["result"])["url"] == url
    finally:
        server = get_canvas_server()
        if server is not None:
            server.stop_thread()
            set_canvas_server(None)
