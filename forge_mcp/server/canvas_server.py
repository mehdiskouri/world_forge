"""Phase-6 Stage D popup canvas server.

A FastAPI mini-app embedded in the same process as the MCP server.
Started lazily on the first ``forge.canvas_url`` MCP tool call,
serves the bundled ``forge_mcp/canvas_page/dist/`` static assets,
and exposes thin HTTP + WebSocket endpoints that delegate every
mutation back through :class:`forge_mcp.project.service.ProjectService`
so the MCP-tool code path stays the single source of truth.

Architecture commitments honoured here:

* **Single source of truth.** Every HTTP write endpoint goes through
  the same ``ProjectService`` method the MCP tool would call. Validation
  and history-event emission are not duplicated in the canvas layer.
* **Localhost only.** The :class:`CanvasServer` constructor refuses any
  host that is not ``127.0.0.1`` or ``localhost``. The MCP tool surface
  never lets the user pick the host.
* **No auth.** Documented in ``docs/canvas.md`` (Stage F). The server
  trusts everything on loopback.
* **Snapshot + JSON-Patch broadcast.** The WebSocket endpoint sends
  one full snapshot on connect and an RFC-6902 patch envelope on every
  subsequent ``ProjectService`` mutation. ``ProjectService.subscribe``
  is the hook (added in this stage); mutations notify every
  subscriber synchronously, the canvas server schedules an async
  broadcast on its event loop. No locks shared across threads.
* **NF-1.4 latency.** End-to-end mutation → broadcast under 500 ms is
  asserted by ``tests/server/test_canvas_server.py``.

Phase 6 Stage D ships the server. Stage E ships the bundled
``dist/`` assets; until then ``GET /`` returns a placeholder HTML
page. Phase 7 turns the connection-map updates live; this stage's
WebSocket protocol carries the patch envelope already.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Final

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from forge_mcp.descriptor.schema import StructuredDescriptor
from forge_mcp.project.service import (
    NoOpenProjectError,
    RegionOverlapError,
    RegionPolygonError,
    UnknownRegionError,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from forge_mcp.project.service import ProjectService


CANVAS_LOCK_FILENAME: Final[str] = "canvas.lock"
"""Per-project lock file under ``<project>/.forge/`` recording the URL."""

_ALLOWED_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "localhost"})
"""Bind hosts the canvas server is allowed to listen on (loopback only)."""

_HEARTBEAT_TIMEOUT_SECONDS: Final[float] = 90.0
"""Disconnect a WebSocket client that has not pinged in this many seconds."""

_PLACEHOLDER_HTML: Final[str] = (
    '<!doctype html><html><head><meta charset="utf-8">'
    "<title>Forge canvas</title></head><body>"
    "<h1>Forge canvas</h1>"
    "<p>Bundle not yet built. Run <code>make canvas-build</code>.</p>"
    "</body></html>"
)


class CanvasServerError(Exception):
    """Base class for canvas-server errors."""


class CanvasServerHostError(CanvasServerError):
    """Raised when an unsupported bind host is requested."""


class _RegionCreateBody(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Request body for ``POST /api/regions`` (mirrors ``forge.create_region``)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str
    polygon_coords: tuple[tuple[float, float], ...]
    structured_descriptor: dict[str, object] | None = None
    seed: int | None = None


class _RegionPatchBody(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Request body for ``PATCH /api/regions/{region_id}``."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str | None = None
    polygon_coords: tuple[tuple[float, float], ...] | None = None
    structured_descriptor: dict[str, object] | None = None
    clear_descriptor: bool = False


def _free_port() -> int:
    """Return a free TCP port on the loopback address."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _build_snapshot(service: ProjectService) -> dict[str, object]:
    """Return the canonical project snapshot used by the canvas frontend.

    Returns an empty payload if no project is open. Shape matches the
    ``forge.list_regions`` / ``forge.list_boundaries`` envelopes so the
    frontend can share types with the MCP surface.
    """
    if not service.is_open:
        return {"project_open": False, "regions": [], "boundaries": []}
    state = service.state
    return {
        "project_open": True,
        "project": {
            "name": state.metadata.name,
            "root": str(state.paths.root),
        },
        "regions": [region.model_dump(mode="json") for region in state.regions.values()],
        "boundaries": [b.model_dump(mode="json") for b in state.boundaries.values()],
    }


def _diff_snapshots(
    previous: dict[str, object],
    current: dict[str, object],
) -> list[dict[str, object]]:
    """Return a minimal RFC-6902 JSON-Patch describing the snapshot delta.

    Phase 6 Stage D ships a deliberately coarse implementation: when
    anything changes, the patch is one whole-document ``replace``.
    Stage E's frontend always applies the patch by replacing its
    cached snapshot, so the wire format is forward-compatible with a
    finer-grained diff in Phase 7.
    """
    if previous == current:
        return []
    return [{"op": "replace", "path": "", "value": current}]


@dataclass
class _ConnectionRegistry:
    """Holds every active WebSocket connection on the canvas server."""

    connections: list[WebSocket] = field(default_factory=list)

    def add(self, ws: WebSocket) -> None:
        self.connections.append(ws)

    def remove(self, ws: WebSocket) -> None:
        with contextlib.suppress(ValueError):
            self.connections.remove(ws)

    def __len__(self) -> int:
        return len(self.connections)


class CanvasServer:
    """Embedded FastAPI server backing the popup canvas.

    One instance per :class:`ProjectService`. Multiple instances on the
    same project would race on the lock file; the MCP tool surface
    enforces a single instance.
    """

    def __init__(
        self,
        service: ProjectService,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        """Bind the FastAPI app to ``service``; do not start the network yet.

        Raises:
            CanvasServerHostError: when ``host`` is not loopback.
        """
        if host not in _ALLOWED_HOSTS:
            msg = f"canvas server may only bind to loopback hosts {sorted(_ALLOWED_HOSTS)!r}"
            raise CanvasServerHostError(msg)
        self._service = service
        self._host = host
        self._port = port if port != 0 else _free_port()
        self._app = self._build_app()
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connections = _ConnectionRegistry()
        self._last_snapshot: dict[str, object] = _build_snapshot(service)
        self._unsubscribe: Callable[[], None] | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------
    @property
    def app(self) -> FastAPI:
        """Return the underlying FastAPI app (used by tests)."""
        return self._app

    @property
    def url(self) -> str:
        """Return the http URL the canvas server listens on."""
        return f"http://{self._host}:{self._port}/"

    @property
    def host(self) -> str:
        """Return the bind host."""
        return self._host

    @property
    def port(self) -> int:
        """Return the bind port."""
        return self._port

    @property
    def connected_clients(self) -> int:
        """Return the count of active WebSocket connections."""
        return len(self._connections)

    @property
    def is_running(self) -> bool:
        """True iff the uvicorn task is alive."""
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Start the uvicorn server in the background and write the lock file."""
        if self.is_running:
            return
        self._loop = asyncio.get_running_loop()
        self._unsubscribe = self._service.subscribe(self._on_mutation)
        config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level="warning",
            lifespan="on",
        )
        server = uvicorn.Server(config)
        self._server = server
        self._task = asyncio.create_task(server.serve(), name="forge-canvas-uvicorn")
        # Wait until uvicorn signals startup is complete. Polling is
        # the only contract uvicorn exposes (no startup event); the
        # idle interval is short enough to clear in tens of ms.
        while not server.started:  # noqa: ASYNC110 - uvicorn API is poll-only
            await asyncio.sleep(0.01)
        self._write_lock_file()

    async def stop(self) -> None:
        """Gracefully shut down uvicorn and clear the lock file."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._server = None
        self._delete_lock_file()

    def start_in_thread(self) -> None:
        """Spawn a background thread that owns its own asyncio loop and starts the server.

        Use this from synchronous contexts (e.g. the MCP tool surface)
        where the canvas server must outlive the calling stack frame.
        Blocks until the uvicorn worker reports ``started=True``.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        ready = threading.Event()

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            async def _bootstrap() -> None:
                await self.start()
                ready.set()

            try:
                loop.run_until_complete(_bootstrap())
                loop.run_forever()
            finally:
                with contextlib.suppress(Exception):
                    loop.run_until_complete(self.stop())
                loop.close()

        self._thread = threading.Thread(
            target=_run,
            name="forge-canvas-thread",
            daemon=True,
        )
        self._thread.start()
        ready.wait(timeout=10.0)

    def stop_thread(self) -> None:
        """Stop the background thread launched by :meth:`start_in_thread`."""
        if self._thread is None or self._loop is None:
            return
        loop = self._loop

        def _shutdown() -> None:
            loop.stop()

        loop.call_soon_threadsafe(_shutdown)
        self._thread.join(timeout=5.0)
        self._thread = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _build_app(self) -> FastAPI:
        app = FastAPI(title="Forge canvas", docs_url=None, redoc_url=None)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self._register_routes(app)
        return app

    def _register_routes(self, app: FastAPI) -> None:
        @app.get("/healthz")
        def _healthz() -> dict[str, object]:
            return {
                "ok": True,
                "project_open": self._service.is_open,
                "connected_clients": self.connected_clients,
            }

        @app.get("/", response_class=HTMLResponse)
        def _index() -> str:
            return _PLACEHOLDER_HTML

        @app.get("/api/state")
        def _state() -> dict[str, object]:
            return _build_snapshot(self._service)

        @app.get("/api/canvas-state")
        def _canvas_state() -> dict[str, object]:
            # Same shape as /api/state in Phase 6; kept as a separate
            # endpoint so Phase 7 can specialise the connection-map
            # payload without breaking the snapshot contract.
            return _build_snapshot(self._service)

        @app.post("/api/regions", status_code=201)
        def _create_region(body: _RegionCreateBody) -> JSONResponse:
            return self._delegate_create_region(body)

        @app.patch("/api/regions/{region_id}")
        def _update_region(region_id: str, body: _RegionPatchBody) -> JSONResponse:
            return self._delegate_update_region(region_id, body)

        @app.delete("/api/regions/{region_id}")
        def _delete_region(region_id: str) -> JSONResponse:
            return self._delegate_delete_region(region_id)

        @app.websocket("/ws")
        async def _ws(ws: WebSocket) -> None:
            await self._handle_ws(ws)

    # -- Mutation delegations ------------------------------------------
    def _delegate_create_region(self, body: _RegionCreateBody) -> JSONResponse:
        descriptor = self._coerce_descriptor(body.structured_descriptor)
        try:
            region = self._service.create_region(
                body.name,
                body.polygon_coords,
                structured_descriptor=descriptor,
                seed=body.seed,
            )
        except NoOpenProjectError as exc:
            return _envelope_error("no_open_project", str(exc), status=409)
        except RegionPolygonError as exc:
            return _envelope_error("invalid_polygon", str(exc), status=400)
        except RegionOverlapError as exc:
            return _envelope_error("region_overlap", str(exc), status=409)
        return JSONResponse(region.model_dump(mode="json"), status_code=201)

    def _delegate_update_region(
        self,
        region_id: str,
        body: _RegionPatchBody,
    ) -> JSONResponse:
        descriptor = self._coerce_descriptor(body.structured_descriptor)
        try:
            from forge_mcp.project.schemas import RegionId  # noqa: PLC0415 - local narrow

            region = self._service.update_region(
                RegionId(region_id),
                name=body.name,
                polygon_coords=body.polygon_coords,
                structured_descriptor=descriptor,
                clear_descriptor=body.clear_descriptor,
            )
        except NoOpenProjectError as exc:
            return _envelope_error("no_open_project", str(exc), status=409)
        except UnknownRegionError as exc:
            return _envelope_error("unknown_region", str(exc), status=404)
        except RegionPolygonError as exc:
            return _envelope_error("invalid_polygon", str(exc), status=400)
        except RegionOverlapError as exc:
            return _envelope_error("region_overlap", str(exc), status=409)
        return JSONResponse(region.model_dump(mode="json"))

    def _delegate_delete_region(self, region_id: str) -> JSONResponse:
        try:
            from forge_mcp.project.schemas import RegionId  # noqa: PLC0415 - local narrow

            self._service.delete_region(RegionId(region_id))
        except NoOpenProjectError as exc:
            return _envelope_error("no_open_project", str(exc), status=409)
        except UnknownRegionError as exc:
            return _envelope_error("unknown_region", str(exc), status=404)
        return JSONResponse({"deleted": region_id})

    @staticmethod
    def _coerce_descriptor(
        raw: dict[str, object] | None,
    ) -> StructuredDescriptor | None:
        if raw is None:
            return None
        try:
            return StructuredDescriptor.model_validate(raw)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # -- Mutation broadcast --------------------------------------------
    def _on_mutation(self, event: str) -> None:
        """Subscriber callback invoked by ``ProjectService`` after each mutation.

        Schedules :meth:`_broadcast_patch` on the canvas event loop. The
        method must return immediately so service mutators are not
        blocked on socket I/O — uvicorn runs on the same loop, so we
        use :func:`asyncio.run_coroutine_threadsafe` to hop threads if
        the mutator was invoked from a non-loop thread (FastAPI sync
        endpoints run in a threadpool).
        """
        if self._loop is None or self._loop.is_closed():  # pragma: no cover - shutdown race
            return
        coro = self._broadcast_patch(event)
        try:
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except RuntimeError:  # pragma: no cover - loop closed mid-call
            return

    async def _broadcast_patch(self, event: str) -> None:
        """Compute the patch since the last snapshot and fan it out."""
        current = _build_snapshot(self._service)
        ops = _diff_snapshots(self._last_snapshot, current)
        self._last_snapshot = current
        if not ops:
            return
        message = json.dumps({"type": "patch", "event": event, "ops": ops})
        for ws in list(self._connections.connections):
            try:
                await ws.send_text(message)
            except (RuntimeError, WebSocketDisconnect):  # pragma: no cover - drop dead client
                self._connections.remove(ws)

    async def _handle_ws(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        snapshot = _build_snapshot(self._service)
        self._last_snapshot = snapshot
        await ws.send_text(json.dumps({"type": "snapshot", "state": snapshot}))
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(
                        ws.receive_text(),
                        timeout=_HEARTBEAT_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    await ws.close(code=1001)
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict) and msg.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
        except WebSocketDisconnect:
            pass
        finally:
            self._connections.remove(ws)

    # -- Lock file -----------------------------------------------------
    def _lock_path(self) -> Path | None:
        if not self._service.is_open:
            return None
        forge_dir = self._service.state.paths.root / ".forge"
        forge_dir.mkdir(parents=True, exist_ok=True)
        return forge_dir / CANVAS_LOCK_FILENAME

    def _write_lock_file(self) -> None:
        path = self._lock_path()
        if path is None:
            return
        payload = {"url": self.url, "host": self._host, "port": self._port}
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    def _delete_lock_file(self) -> None:
        path = self._lock_path()
        if path is None:
            return
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def _envelope_error(code: str, message: str, *, status: int) -> JSONResponse:
    """Wrap a structured error in the same shape as the MCP tool envelopes."""
    return JSONResponse(
        {"ok": False, "error": {"code": code, "message": message}},
        status_code=status,
    )


__all__ = [
    "CANVAS_LOCK_FILENAME",
    "CanvasServer",
    "CanvasServerError",
    "CanvasServerHostError",
]
