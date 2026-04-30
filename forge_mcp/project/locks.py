"""Persistent lock store for the open project (Phase 2 Stage F).

Phase 2 only owns the lock *records*: list, add, remove, persist. The
:class:`LockApplicationError` semantics (refusing mutations that would
violate a lock) are intentionally Phase-7 work — the architecture wants
the surface visible to the agent today without committing to the full
enforcement engine yet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from forge_mcp._io.atomic import write_json
from forge_mcp.project.schemas import LockId, LockRecord, LockStoreFile, RegionId

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class LockStoreError(Exception):
    """Base class for lock-store errors."""


class LockNotFoundError(LockStoreError):
    """Raised when ``remove_lock`` targets a record that is not present."""


class DuplicateLockError(LockStoreError):
    """Raised when ``add_lock`` would write a duplicate ``lock_id``."""


def _key(record: LockRecord) -> LockId:
    return record.lock_id


class LockStore:
    """File-backed list of :class:`LockRecord` for one open project."""

    def __init__(self, path: Path, *, initial: Iterable[LockRecord] = ()) -> None:
        """Bind the store to ``path`` and seed the in-memory list."""
        self._path = path
        self._records: list[LockRecord] = list(initial)

    @classmethod
    def load(cls, path: Path) -> LockStore:
        """Read ``locks.json`` from disk; missing file = empty store."""
        if not path.exists():
            return cls(path)
        try:
            store = LockStoreFile.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            msg = f"failed to load locks.json: {exc}"
            raise LockStoreError(msg) from exc
        return cls(path, initial=store.locks)

    @property
    def records(self) -> tuple[LockRecord, ...]:
        """Return an immutable snapshot of the current lock records."""
        return tuple(self._records)

    def list_locks(self, region_id: RegionId | None = None) -> tuple[LockRecord, ...]:
        """Return locks, optionally filtered to one region."""
        if region_id is None:
            return tuple(self._records)
        return tuple(r for r in self._records if r.region_id == region_id)

    def add_lock(self, lock: LockRecord) -> None:
        """Append ``lock`` and flush. Rejects a duplicate ``lock_id``."""
        key = _key(lock)
        for existing in self._records:
            if _key(existing) == key:
                msg = f"a lock with id {lock.lock_id!r} already exists"
                raise DuplicateLockError(msg)
        self._records.append(lock)
        self._persist()

    def remove_lock(self, lock_id: LockId) -> LockRecord:
        """Remove the lock identified by ``lock_id`` and flush."""
        for index, existing in enumerate(self._records):
            if existing.lock_id == lock_id:
                removed = self._records.pop(index)
                self._persist()
                return removed
        msg = f"no lock with id {lock_id!r}"
        raise LockNotFoundError(msg)

    def _persist(self) -> None:
        write_json(self._path, LockStoreFile(locks=tuple(self._records)))


__all__ = [
    "DuplicateLockError",
    "LockNotFoundError",
    "LockStore",
    "LockStoreError",
]
