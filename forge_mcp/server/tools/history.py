"""``forge.history`` (read-only) and ``forge.undo`` (Phase-7 stub)."""

from __future__ import annotations

from forge_mcp.project.history import HistoryError, HistoryUndoNotImplementedError
from forge_mcp.project.history import undo as _undo
from forge_mcp.project.service import NoOpenProjectError
from forge_mcp.server.tools import get_service
from forge_mcp.server.tools._responses import fail, ok


def history(limit: int | None = None) -> dict[str, object]:
    """Return the history events of the open project, oldest first."""
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    try:
        events = list(state.history.iter_events(limit=limit))
    except HistoryError as exc:
        return fail("history_error", str(exc))
    return ok({"events": [e.model_dump(mode="json") for e in events]})


def undo() -> dict[str, object]:
    """Phase-2 stub: returns a structured ``not_implemented`` error."""
    try:
        _undo()
    except HistoryUndoNotImplementedError as exc:
        return fail("not_implemented", str(exc), details={"available_in_phase": 7})
    # Defensive: ``_undo`` always raises; this branch is unreachable today.
    return ok({"undone": True})  # pragma: no cover  # exhaustiveness


__all__ = ["history", "undo"]
