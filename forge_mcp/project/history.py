"""Append-only history log for the open project (Phase 2 Stage F).

Wraps the bookkeeping that lives in :class:`forge_mcp.project.service.ProjectService`
behind a single typed object so:

* the file-naming convention (``{event_id}_{kind}.json``) lives in one
  place;
* monotonic, gap-free sequence numbers are enforced on every append;
* Phase 7's ``undo`` replay can iterate events in either direction
  through one disk-backed API.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from pydantic import ValidationError

from forge_mcp._io.atomic import write_json
from forge_mcp.project.schemas import (
    HistoryActor,
    HistoryEvent,
    HistoryEventId,
    HistoryEventKind,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from datetime import datetime
    from pathlib import Path


_EVENT_ID_DIGITS: Final[int] = 4
_FILENAME_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<event_id>\d{4,})_(?P<kind>[a-z_]+)\.json$",
)


class HistoryError(Exception):
    """Base class for history-log errors."""


class HistoryGapError(HistoryError):
    """Raised when the on-disk history sequence has a gap.

    A monotonic, gap-free sequence is the contract that lets Phase 7's
    ``undo`` replay events without sorting surprises.
    """


def _format_event_id(seq: int) -> HistoryEventId:
    """Zero-pad ``seq`` to the schema's ≥4-digit minimum."""
    return HistoryEventId(f"{seq:0{_EVENT_ID_DIGITS}d}")


class HistoryLog:
    """Append-only writer + reader over ``history/`` for one project."""

    def __init__(self, history_dir: Path, *, count: int) -> None:
        """Bind the log to ``history_dir`` and seed the in-memory counter."""
        self._dir = history_dir
        self._count = count

    @property
    def count(self) -> int:
        """Return the number of events the log has appended so far."""
        return self._count

    def append(
        self,
        kind: HistoryEventKind,
        *,
        at: datetime,
        actor: HistoryActor = HistoryActor.AGENT,
        payload: Mapping[str, object] | None = None,
    ) -> HistoryEvent:
        """Atomically write one event and bump the in-memory counter."""
        seq = self._count + 1
        event = HistoryEvent(
            event_id=_format_event_id(seq),
            kind=kind,
            at=at,
            actor=actor,
            # ``HistoryEvent.payload`` is typed ``dict[str, JsonValue]``;
            # callers pass ``Mapping[str, object]`` and Pydantic
            # validates the values for us.
            payload=dict(payload or {}),  # type: ignore[arg-type]  # validated by Pydantic
        )
        write_json(self._event_path(event.event_id, event.kind), event)
        self._count = seq
        return event

    def iter_events(
        self,
        *,
        reverse: bool = False,
        limit: int | None = None,
    ) -> Iterator[HistoryEvent]:
        """Yield events from disk in sequence order.

        Walks ``history_dir``, parses every filename, asserts the
        sequence is monotonic and gap-free, and yields the deserialized
        :class:`HistoryEvent` objects. ``reverse=True`` iterates newest
        first; ``limit`` caps the number of events yielded.
        """
        entries: list[tuple[int, Path]] = []
        if self._dir.is_dir():
            for path in self._dir.glob("*.json"):
                match = _FILENAME_RE.match(path.name)
                if match is None:
                    msg = f"unrecognised history filename: {path.name}"
                    raise HistoryError(msg)
                entries.append((int(match.group("event_id")), path))
        entries.sort()
        for expected, (actual, _path) in enumerate(entries, start=1):
            if expected != actual:
                msg = (
                    f"history sequence has a gap: expected event_id "
                    f"{expected:04d}, found {actual:04d}"
                )
                raise HistoryGapError(msg)
        if reverse:
            entries.reverse()
        for yielded, (_seq, path) in enumerate(entries):
            if limit is not None and yielded >= limit:
                return
            try:
                event = HistoryEvent.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as exc:
                msg = f"failed to load history event {path.name}: {exc}"
                raise HistoryError(msg) from exc
            yield event

    def _event_path(self, event_id: HistoryEventId, kind: HistoryEventKind) -> Path:
        return self._dir / f"{event_id}_{kind.value}.json"


class HistoryUndoNotImplementedError(NotImplementedError):
    """Raised by :func:`undo` until Phase 7 lands the replay engine."""


def undo() -> None:
    """Stub for the Phase-7 ``undo`` MCP tool.

    Defined here (and re-exported) so the MCP tool surface in Stage G
    can register the tool today and return a structured error to the
    agent. The Phase-7 implementation will replace this body with the
    real replay-against-:class:`HistoryLog`.
    """
    msg = "undo is implemented in Phase 7"
    raise HistoryUndoNotImplementedError(msg)


__all__ = [
    "HistoryError",
    "HistoryGapError",
    "HistoryLog",
    "HistoryUndoNotImplementedError",
    "undo",
]
