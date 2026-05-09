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
from forge_mcp.project.schemas import (
    FeatureLockPayload,
    LockId,
    LockKind,
    LockRecord,
    PropertyLockPayload,
    RegionId,
    RegionLockPayload,
)

if TYPE_CHECKING:
    from pathlib import Path


_NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_SEED = 7


def _record(lock_id: str, region: str = "region_alpha") -> LockRecord:
    return LockRecord(
        lock_id=LockId(lock_id),
        region_id=RegionId(region),
        kind=LockKind.PROPERTY,
        payload={"json_path": "name", "expected_value": "Alpha"},
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


# ---------------------------------------------------------------------------
# Discriminated payload validation (Phase 7 Stage A)
# ---------------------------------------------------------------------------


def _feature_record(lock_id: str, bbox: tuple[float, float, float, float]) -> LockRecord:
    return LockRecord(
        lock_id=LockId(lock_id),
        region_id=RegionId("region_alpha"),
        kind=LockKind.FEATURE,
        payload={
            "bbox_world": list(bbox),
            "captured_seed": _SEED,
            "captured_at": _NOW.isoformat(),
            "captured_path": f"locks/feature/{lock_id}.npy",
        },
        created_at=_NOW,
        modified_at=_NOW,
    )


def _region_record(lock_id: str) -> LockRecord:
    return LockRecord(
        lock_id=LockId(lock_id),
        region_id=RegionId("region_alpha"),
        kind=LockKind.REGION,
        payload={"scope": "skip_regen"},
        created_at=_NOW,
        modified_at=_NOW,
    )


def test_property_payload_round_trip_via_typed_payload() -> None:
    record = _record("lock_p")
    typed = record.typed_payload()
    assert isinstance(typed, PropertyLockPayload)
    assert typed.json_path == "name"
    assert typed.expected_value == "Alpha"


def test_feature_payload_round_trip_via_typed_payload() -> None:
    record = _feature_record("lock_f", (0.0, 0.0, 4.0, 4.0))
    typed = record.typed_payload()
    assert isinstance(typed, FeatureLockPayload)
    assert typed.bbox_world == (0.0, 0.0, 4.0, 4.0)
    assert typed.captured_seed == _SEED


def test_region_payload_round_trip_via_typed_payload() -> None:
    typed = _region_record("lock_r").typed_payload()
    assert isinstance(typed, RegionLockPayload)
    assert typed.scope == "skip_regen"


def test_lock_record_rejects_property_missing_fields() -> None:
    from pydantic import ValidationError  # noqa: PLC0415 - localised import

    with pytest.raises(ValidationError):
        LockRecord(
            lock_id=LockId("lock_bad"),
            region_id=RegionId("region_alpha"),
            kind=LockKind.PROPERTY,
            payload={"json_path": "name"},
            created_at=_NOW,
            modified_at=_NOW,
        )


def test_feature_payload_rejects_inverted_bbox() -> None:
    from pydantic import ValidationError  # noqa: PLC0415 - localised import

    with pytest.raises(ValidationError):
        FeatureLockPayload.model_validate(
            {
                "bbox_world": [4.0, 4.0, 0.0, 0.0],
                "captured_seed": 1,
                "captured_at": _NOW.isoformat(),
                "captured_path": "locks/feature/lock_x.npy",
            },
        )


# ---------------------------------------------------------------------------
# Query helpers (Phase 7 Stage A)
# ---------------------------------------------------------------------------


def test_find_by_id_returns_match_and_none(tmp_path: Path) -> None:
    store = LockStore(tmp_path / "locks.json")
    record = _record("lock_a")
    store.add_lock(record)
    assert store.find_by_id(LockId("lock_a")) == record
    assert store.find_by_id(LockId("lock_missing")) is None


def test_find_by_target_filters_by_json_path(tmp_path: Path) -> None:
    store = LockStore(tmp_path / "locks.json")
    a = _record("lock_a")  # json_path == "name"
    b = LockRecord(
        lock_id=LockId("lock_b"),
        region_id=RegionId("region_alpha"),
        kind=LockKind.PROPERTY,
        payload={"json_path": "seed", "expected_value": 42},
        created_at=_NOW,
        modified_at=_NOW,
    )
    store.add_lock(a)
    store.add_lock(b)
    assert store.find_by_target(RegionId("region_alpha")) == (a, b)
    assert store.find_by_target(RegionId("region_alpha"), json_path="name") == (a,)
    assert store.find_by_target(RegionId("region_alpha"), json_path="missing") == ()


def test_find_overlapping_features_treats_edges_as_disjoint(tmp_path: Path) -> None:
    store = LockStore(tmp_path / "locks.json")
    base = _feature_record("lock_base", (0.0, 0.0, 4.0, 4.0))
    store.add_lock(base)

    region = RegionId("region_alpha")
    # Edge-touching: 4.0 == 4.0 -> not overlapping.
    assert store.find_overlapping_features(region, (4.0, 0.0, 8.0, 4.0)) == ()
    # Strict overlap.
    assert store.find_overlapping_features(region, (2.0, 2.0, 6.0, 6.0)) == (base,)
    # Different region -> never overlaps.
    assert (
        store.find_overlapping_features(
            RegionId("region_beta"),
            (0.0, 0.0, 4.0, 4.0),
        )
        == ()
    )
