"""Tests for :mod:`forge_mcp.project.locks`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from forge_mcp.project.locks import (
    DuplicateLockError,
    LockNotFoundError,
    LockStore,
    LockStoreError,
)
from forge_mcp.project.schemas import LockId, LockKind, LockRecord, RegionId

if TYPE_CHECKING:
    from pathlib import Path


_NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


def _record(lock_id: str, region: str = "region_alpha") -> LockRecord:
    return LockRecord(
        lock_id=LockId(lock_id),
        region_id=RegionId(region),
        kind=LockKind.PROPERTY,
        created_at=_NOW,
        modified_at=_NOW,
    )


def test_load_returns_empty_store_when_file_missing(tmp_path: Path) -> None:
    store = LockStore.load(tmp_path / "locks.json")
    assert store.records == ()


def test_add_persists_and_lists(tmp_path: Path) -> None:
    path = tmp_path / "locks.json"
    store = LockStore(path)
    lock = _record("lock_1")
    store.add_lock(lock)
    assert store.list_locks() == (lock,)
    # Reload from disk to confirm persistence.
    reloaded = LockStore.load(path)
    assert reloaded.list_locks() == (lock,)


def test_list_locks_filters_by_region(tmp_path: Path) -> None:
    store = LockStore(tmp_path / "locks.json")
    a = _record("lock_a", region="region_alpha")
    b = _record("lock_b", region="region_beta")
    store.add_lock(a)
    store.add_lock(b)
    assert store.list_locks(RegionId("region_alpha")) == (a,)
    assert store.list_locks(RegionId("region_beta")) == (b,)


def test_add_rejects_duplicate_lock_id(tmp_path: Path) -> None:
    store = LockStore(tmp_path / "locks.json")
    store.add_lock(_record("lock_1"))
    with pytest.raises(DuplicateLockError, match="lock_1"):
        store.add_lock(_record("lock_1", region="region_beta"))


def test_remove_returns_record_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "locks.json"
    store = LockStore(path)
    lock = _record("lock_1")
    store.add_lock(lock)
    removed = store.remove_lock(LockId("lock_1"))
    assert removed == lock
    assert store.list_locks() == ()
    assert LockStore.load(path).list_locks() == ()


def test_remove_unknown_raises(tmp_path: Path) -> None:
    store = LockStore(tmp_path / "locks.json")
    with pytest.raises(LockNotFoundError, match="lock_x"):
        store.remove_lock(LockId("lock_x"))


def test_load_rejects_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "locks.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(LockStoreError, match="failed to load"):
        LockStore.load(path)
