"""Bounded undo stack with on-disk persistence (Phase 7 Stage E).

The plan (`AGENT/dev_phases/phase7.md` Stage E) calls for a bounded
ring-buffer of pre-mutation :class:`ProjectState` snapshots so the
``forge.undo`` MCP tool can roll back the most recent mutation.
Snapshots also persist to ``<project>/.undo/<NNNN>.json`` so undo
survives close/open. Heightmaps and Blender realizations are not
captured (too large); per the plan, undo restores state and leaves
on-disk realization artefacts stale.

The :class:`StateSnapshot` model collects every mutable Pydantic
container on :class:`ProjectState`; :class:`UndoStack` owns the
in-memory deque plus the on-disk ring and exposes ``push``/``pop``/
``__len__``/``clear``. ``ProjectService`` is the only caller of
``push``; the public ``ProjectService.undo`` method is the only caller
of ``pop``.
"""

from __future__ import annotations

import re
from collections import deque
from contextlib import suppress
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field

from forge_mcp._io.atomic import atomic_write_text

# Pydantic needs these symbols at runtime to resolve the forward
# references created by ``from __future__ import annotations`` (see the
# ``StateSnapshot.model_rebuild`` call at the bottom of this module).
from forge_mcp.project.schemas import (  # noqa: TC001 - runtime needed for Pydantic rebuild
    BoundaryId,
    BoundaryRecord,
    Edge,
    EnvironmentNode,
    EnvironmentNodeId,
    LockRecord,
    MaterialArchetypeId,
    MaterialArchetypeNode,
    ProjectMetadata,
    RegionId,
    RegionNode,
    SubRegionId,
    SubRegionNode,
    WorldRootNode,
)

if TYPE_CHECKING:
    from pathlib import Path


UNDO_STACK_LIMIT: Final[int] = 50
"""Maximum number of snapshots retained per project (PRD §F-10.5 ring)."""

_SNAPSHOT_FILE_RE = re.compile(r"^(?P<seq>\d{6})\.json$")


class StateSnapshot(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Pydantic-serialisable copy of every mutable :class:`ProjectState` field.

    History events and on-disk heightmaps/realizations are intentionally
    excluded (history is append-only and never undone; heightmaps are
    too large for the ring per the locked plan).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    metadata: ProjectMetadata
    world_root: WorldRootNode | None = None
    regions: dict[RegionId, RegionNode] = Field(default_factory=dict)
    sub_regions: dict[SubRegionId, SubRegionNode] = Field(default_factory=dict)
    archetypes: dict[MaterialArchetypeId, MaterialArchetypeNode] = Field(default_factory=dict)
    environments: dict[EnvironmentNodeId, EnvironmentNode] = Field(default_factory=dict)
    boundaries: dict[BoundaryId, BoundaryRecord] = Field(default_factory=dict)
    edges: dict[str, list[Edge]] = Field(default_factory=dict)
    lock_records: tuple[LockRecord, ...] = ()


class UndoStack:
    """Bounded LIFO of :class:`StateSnapshot` mirrored to ``.undo/<seq>.json``.

    The stack is FIFO-evicted at ``maxlen`` (50 by default); the next
    monotonic sequence is tracked in ``_next_seq`` and persisted in the
    file name so :meth:`load` can rebuild the order without an index
    file.
    """

    def __init__(self, undo_dir: Path, *, maxlen: int = UNDO_STACK_LIMIT) -> None:
        """Bind the stack to ``undo_dir`` (created lazily on first push)."""
        self._dir = undo_dir
        self._maxlen = maxlen
        self._snapshots: deque[tuple[int, StateSnapshot]] = deque(maxlen=maxlen)
        self._next_seq: int = 0

    # ------------------------------------------------------------------
    # Disk lifecycle
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, undo_dir: Path, *, maxlen: int = UNDO_STACK_LIMIT) -> UndoStack:
        """Rebuild a stack from on-disk snapshots in ``undo_dir``.

        Missing directory or no matching files yield an empty stack;
        malformed entries are silently dropped (an undo file that fails
        validation cannot be restored anyway, and we prefer to keep the
        rest of the ring usable).
        """
        stack = cls(undo_dir, maxlen=maxlen)
        if not undo_dir.is_dir():
            return stack
        loaded: list[tuple[int, StateSnapshot]] = []
        for path in undo_dir.iterdir():
            match = _SNAPSHOT_FILE_RE.match(path.name)
            if match is None:
                continue
            seq = int(match.group("seq"))
            try:
                snap = StateSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):  # pragma: no cover - malformed disk entry
                continue
            loaded.append((seq, snap))
        loaded.sort(key=lambda item: item[0])
        # Cap to ``maxlen`` keeping the newest; older overflow files
        # are removed from disk so the in-memory and on-disk views match.
        if len(loaded) > maxlen:
            for seq, _snap in loaded[:-maxlen]:
                cls._unlink_seq(undo_dir, seq)
            loaded = loaded[-maxlen:]
        stack._snapshots.extend(loaded)
        stack._next_seq = (loaded[-1][0] + 1) if loaded else 0
        return stack

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def __len__(self) -> int:
        """Return the current number of retained snapshots."""
        return len(self._snapshots)

    def push(self, snapshot: StateSnapshot) -> None:
        """Append ``snapshot`` and persist; FIFO-evict the oldest at capacity."""
        seq = self._next_seq
        self._next_seq += 1
        evicted: tuple[int, StateSnapshot] | None = None
        if len(self._snapshots) == self._maxlen:
            evicted = self._snapshots[0]
        self._snapshots.append((seq, snapshot))
        self._dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self._dir / f"{seq:06d}.json",
            snapshot.model_dump_json(),
        )
        if evicted is not None:
            self._unlink_seq(self._dir, evicted[0])

    def pop(self) -> StateSnapshot | None:
        """Remove and return the most recent snapshot (or ``None`` if empty)."""
        if not self._snapshots:
            return None
        seq, snap = self._snapshots.pop()
        self._unlink_seq(self._dir, seq)
        return snap

    def peek(self) -> StateSnapshot | None:
        """Return the most recent snapshot without removing it (``None`` if empty)."""
        if not self._snapshots:
            return None
        return self._snapshots[-1][1]

    def clear(self) -> None:
        """Drop every retained snapshot, on-disk included."""
        for seq, _snap in self._snapshots:
            self._unlink_seq(self._dir, seq)
        self._snapshots.clear()
        self._next_seq = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _unlink_seq(undo_dir: Path, seq: int) -> None:
        path = undo_dir / f"{seq:06d}.json"
        if path.exists():
            with suppress(OSError):  # pragma: no cover - races/permissions
                path.unlink()


__all__ = [
    "UNDO_STACK_LIMIT",
    "StateSnapshot",
    "UndoStack",
]


# ``from __future__ import annotations`` turns every type hint into a
# string, so Pydantic needs an explicit rebuild pass to resolve them
# against the imports actually present in this module.
StateSnapshot.model_rebuild(_types_namespace=globals())
