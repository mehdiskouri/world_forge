"""``forge.history`` (read-only) and ``forge.undo`` (Phase 7 Stage E)."""

from __future__ import annotations

from forge_mcp.project.history import HistoryError
from forge_mcp.project.service import CannotUndoError, NoOpenProjectError
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
    """Pop the latest snapshot off the undo ring and restore the prior state."""
    try:
        event = get_service().undo()
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except CannotUndoError as exc:
        return fail("cannot_undo", str(exc))
    return ok({"undone": True, "event": event.model_dump(mode="json")})


__all__ = ["history", "undo"]
