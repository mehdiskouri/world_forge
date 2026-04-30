"""``forge.list_locks`` — Phase-2 read-only lock surface."""

from __future__ import annotations

from forge_mcp.project.schemas import RegionId
from forge_mcp.project.service import NoOpenProjectError
from forge_mcp.server.tools import get_service
from forge_mcp.server.tools._responses import fail, ok


def list_locks(region_id: str | None = None) -> dict[str, object]:
    """List locks, optionally filtered by region."""
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    locks = state.lock_store.list_locks(
        RegionId(region_id) if region_id is not None else None,
    )
    return ok({"locks": [lock.model_dump(mode="json") for lock in locks]})


__all__ = ["list_locks"]
