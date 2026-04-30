"""Process-wide :class:`ProjectService` singleton for the MCP tool surface.

The MCP transport (stdio) gives every tool call its own Python frame
but they all share one process. Routing every tool through this
singleton keeps ``ProjectService``'s "at most one open project at a
time" invariant intact without threading the instance through every
function signature.

Tests use :func:`set_service` to inject a fresh instance per test.
"""

from __future__ import annotations

from forge_mcp.project.service import ProjectService

_service: ProjectService = ProjectService()


def get_service() -> ProjectService:
    """Return the process-wide :class:`ProjectService` instance."""
    return _service


def set_service(service: ProjectService) -> None:
    """Replace the process-wide instance. Intended for tests only."""
    global _service  # noqa: PLW0603 - module-level singleton is the point
    _service = service


__all__ = ["get_service", "set_service"]
