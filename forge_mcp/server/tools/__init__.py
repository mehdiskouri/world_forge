"""Process-wide :class:`ProjectService` singleton for the MCP tool surface.

The MCP transport (stdio) gives every tool call its own Python frame
but they all share one process. Routing every tool through this
singleton keeps ``ProjectService``'s "at most one open project at a
time" invariant intact without threading the instance through every
function signature.

Tests use :func:`set_service` to inject a fresh instance per test.

The same singleton pattern carries the optional Phase-4 realizer
factory: tools that need to drive Blender call
:func:`get_realizer_factory` to obtain a callable returning a context
manager around a :class:`~forge_mcp.realize.engine.RealizerEngine`.
When no factory is installed the realization-aware tools fall back to
a graceful ``realizer_not_configured`` error envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_mcp.project.service import ProjectService

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from forge_mcp.realize.engine import RealizerEngine

    RealizerFactory = Callable[[], AbstractContextManager[RealizerEngine]]

_service: ProjectService = ProjectService()
_realizer_factory: RealizerFactory | None = None


def get_service() -> ProjectService:
    """Return the process-wide :class:`ProjectService` instance."""
    return _service


def set_service(service: ProjectService) -> None:
    """Replace the process-wide instance. Intended for tests only."""
    global _service  # noqa: PLW0603 - module-level singleton is the point
    _service = service


def get_realizer_factory() -> RealizerFactory | None:
    """Return the installed realizer factory, or ``None`` if none is set."""
    return _realizer_factory


def set_realizer_factory(factory: RealizerFactory | None) -> None:
    """Install (or clear) the realizer factory used by Phase-4 tools."""
    global _realizer_factory  # noqa: PLW0603 - module-level singleton is the point
    _realizer_factory = factory


__all__ = [
    "get_realizer_factory",
    "get_service",
    "set_realizer_factory",
    "set_service",
]
