"""``forge.canvas_url`` / ``forge.canvas_status`` MCP tool implementations.

These tools own the lazy lifecycle of the popup
:class:`~forge_mcp.server.canvas_server.CanvasServer`. The first call to
:func:`canvas_url` constructs and starts the singleton in a background
thread (so it outlives the synchronous tool stack frame); subsequent
calls return the same URL. :func:`canvas_status` is read-only and never
starts the server.

The singleton is held at module level (mirroring
:mod:`forge_mcp.server.tools` for :class:`ProjectService`) so the same
URL is returned for the lifetime of the MCP process. Tests can inject
or clear the singleton via :func:`set_canvas_server`.
"""

from __future__ import annotations

from forge_mcp.server.canvas_server import CanvasServer
from forge_mcp.server.tools import get_service
from forge_mcp.server.tools._responses import fail, ok

_canvas_server: CanvasServer | None = None


def get_canvas_server() -> CanvasServer | None:
    """Return the live canvas server instance or ``None`` when not started."""
    return _canvas_server


def set_canvas_server(server: CanvasServer | None) -> None:
    """Replace the singleton. Intended for tests only."""
    global _canvas_server  # noqa: PLW0603 - module-level singleton is the point
    _canvas_server = server


def canvas_url() -> dict[str, object]:
    """Return the popup-canvas URL, starting the server lazily on first call."""
    global _canvas_server  # noqa: PLW0603 - module-level singleton is the point
    service = get_service()
    if not service.is_open:
        return fail("no_open_project", "open a project before requesting the canvas URL")
    server = _canvas_server
    if server is None or not server.is_running:
        server = CanvasServer(service)
        server.start_in_thread()
        _canvas_server = server
    return ok({"url": server.url, "host": server.host, "port": server.port})


def canvas_status() -> dict[str, object]:
    """Return the running state of the popup canvas server."""
    server = _canvas_server
    if server is None:
        return ok({"running": False, "url": None, "connected_clients": 0})
    return ok(
        {
            "running": server.is_running,
            "url": server.url,
            "connected_clients": server.connected_clients,
        },
    )


__all__ = [
    "canvas_status",
    "canvas_url",
    "get_canvas_server",
    "set_canvas_server",
]
