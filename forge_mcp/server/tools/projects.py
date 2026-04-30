"""``forge.create_project`` / ``open_project`` / ``save_project`` / ``close_project``."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from forge_mcp.project.schemas import WorldBounds
from forge_mcp.project.service import (
    NoOpenProjectError,
    ProjectAlreadyExistsError,
    ProjectError,
    ProjectFormatError,
    ProjectNotFoundError,
    ProjectVersionError,
)
from forge_mcp.server.tools import get_service
from forge_mcp.server.tools._responses import fail, ok


def create_project(
    path: str,
    name: str,
    world_bounds: dict[str, object],
) -> dict[str, object]:
    """Create + open a new project at ``path``.

    ``world_bounds`` is a :class:`WorldBounds` payload (rectangle min/max
    in meters); we validate it before touching disk so a malformed
    request can't half-bootstrap the tree.
    """
    try:
        bounds = WorldBounds.model_validate(world_bounds)
    except ValidationError as exc:
        return fail("invalid_world_bounds", str(exc))
    try:
        metadata = get_service().create_project(Path(path), name, bounds)
    except ProjectAlreadyExistsError as exc:
        return fail("project_already_exists", str(exc))
    except ProjectError as exc:
        return fail("project_error", str(exc))
    return ok(metadata.model_dump(mode="json"))


def open_project(path: str) -> dict[str, object]:
    """Load an existing project at ``path``."""
    try:
        metadata = get_service().open_project(Path(path))
    except ProjectNotFoundError as exc:
        return fail("project_not_found", str(exc))
    except ProjectVersionError as exc:
        return fail("project_version_mismatch", str(exc))
    except ProjectFormatError as exc:
        return fail("project_format_error", str(exc))
    return ok(metadata.model_dump(mode="json"))


def save_project() -> dict[str, object]:
    """Flush the open project to disk."""
    try:
        get_service().save_project()
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    return ok({"saved": True})


def close_project() -> dict[str, object]:
    """Close the open project, flushing any pending writes."""
    try:
        get_service().close_project()
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    return ok({"closed": True})


__all__ = ["close_project", "create_project", "open_project", "save_project"]
