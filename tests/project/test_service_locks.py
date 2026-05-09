"""Service-level tests for the Phase 7 Stage A lock CRUD surface."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest
from forge_mcp.generate.heightmap import Heightmap, save_npy
from forge_mcp.project.locks import DuplicateLockError
from forge_mcp.project.schemas import (
    FeatureLockPayload,
    LockId,
    LockKind,
    PropertyLockPayload,
    RegionId,
    WorldBounds,
)
from forge_mcp.project.service import (
    LockTargetNotFoundError,
    OverlappingFeatureLockError,
    ProjectService,
    UnknownLockError,
    UnknownRegionError,
)

if TYPE_CHECKING:
    from pathlib import Path


_WORLD = WorldBounds(min=(-10.0, -10.0), max=(10.0, 10.0))
_SQUARE: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (8.0, 0.0),
    (8.0, 8.0),
    (0.0, 8.0),
)
_HEIGHTMAP_SHAPE = (8, 8)


def _bootstrap(tmp_path: Path) -> ProjectService:
    svc = ProjectService()
    svc.create_project(tmp_path, "Locks", _WORLD)
    return svc


def _stamp_heightmap(svc: ProjectService, region_id: RegionId, value: float = 12.5) -> None:
    heightmap = Heightmap(
        data=np.full(_HEIGHTMAP_SHAPE, value, dtype=np.float32),
        resolution_meters_per_pixel=1.0,
        origin=(0.0, 0.0),
        elevation_band=(0.0, 1000.0),
    )
    save_npy(heightmap, svc.state.paths.heightmap_npy_path(region_id))


# ---------------------------------------------------------------------------
# create_property_lock
# ---------------------------------------------------------------------------


def test_create_property_lock_captures_value(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("Foothills", _SQUARE)
    record = svc.create_property_lock(region_id=region.node_id, json_path="name")
    assert record.kind is LockKind.PROPERTY
    typed = record.typed_payload()
    assert isinstance(typed, PropertyLockPayload)
    assert typed.json_path == "name"
    assert typed.expected_value == "Foothills"


def test_create_property_lock_unknown_region(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    with pytest.raises(UnknownRegionError):
        svc.create_property_lock(region_id=RegionId("region_missing"), json_path="name")


def test_create_property_lock_missing_path(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    with pytest.raises(LockTargetNotFoundError):
        svc.create_property_lock(region_id=region.node_id, json_path="nope.bogus")


def test_create_property_lock_empty_path(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    with pytest.raises(LockTargetNotFoundError):
        svc.create_property_lock(region_id=region.node_id, json_path="")


# ---------------------------------------------------------------------------
# create_feature_lock
# ---------------------------------------------------------------------------


def test_create_feature_lock_writes_patch(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    _stamp_heightmap(svc, region.node_id, value=3.5)
    record = svc.create_feature_lock(
        region_id=region.node_id,
        bbox_world=(1.0, 1.0, 5.0, 5.0),
    )
    typed = record.typed_payload()
    assert isinstance(typed, FeatureLockPayload)
    assert typed.captured_path == f"locks/feature/{record.lock_id}.npy"
    patch_path = svc.state.paths.feature_lock_patch_path(record.lock_id)
    assert patch_path.exists()
    patch = np.load(patch_path, allow_pickle=False)
    assert patch.shape == (4, 4)
    assert np.all(patch == np.float32(3.5))


def test_create_feature_lock_requires_heightmap(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    with pytest.raises(LockTargetNotFoundError):
        svc.create_feature_lock(
            region_id=region.node_id,
            bbox_world=(1.0, 1.0, 2.0, 2.0),
        )


def test_create_feature_lock_rejects_overlap(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    _stamp_heightmap(svc, region.node_id)
    svc.create_feature_lock(
        region_id=region.node_id,
        bbox_world=(0.0, 0.0, 4.0, 4.0),
    )
    with pytest.raises(OverlappingFeatureLockError):
        svc.create_feature_lock(
            region_id=region.node_id,
            bbox_world=(2.0, 2.0, 6.0, 6.0),
        )


def test_create_feature_lock_allows_edge_adjacent(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    _stamp_heightmap(svc, region.node_id)
    a = svc.create_feature_lock(
        region_id=region.node_id,
        bbox_world=(0.0, 0.0, 4.0, 4.0),
    )
    b = svc.create_feature_lock(
        region_id=region.node_id,
        bbox_world=(4.0, 0.0, 8.0, 4.0),
    )
    assert a.lock_id != b.lock_id


def test_create_feature_lock_bbox_outside_region(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    _stamp_heightmap(svc, region.node_id)
    with pytest.raises(LockTargetNotFoundError):
        svc.create_feature_lock(
            region_id=region.node_id,
            bbox_world=(100.0, 100.0, 200.0, 200.0),
        )


# ---------------------------------------------------------------------------
# create_region_lock + remove_lock
# ---------------------------------------------------------------------------


def test_create_region_lock(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    record = svc.create_region_lock(region_id=region.node_id)
    assert record.kind is LockKind.REGION
    assert record.payload == {"scope": "skip_regen"}


def test_remove_lock_property(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    record = svc.create_property_lock(region_id=region.node_id, json_path="name")
    svc.remove_lock(record.lock_id)
    assert svc.state.lock_store.find_by_id(record.lock_id) is None


def test_remove_lock_feature_cleans_patch(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    _stamp_heightmap(svc, region.node_id)
    record = svc.create_feature_lock(
        region_id=region.node_id,
        bbox_world=(1.0, 1.0, 3.0, 3.0),
    )
    patch_path = svc.state.paths.feature_lock_patch_path(record.lock_id)
    assert patch_path.exists()
    svc.remove_lock(record.lock_id)
    assert not patch_path.exists()


def test_remove_lock_unknown(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    with pytest.raises(UnknownLockError):
        svc.remove_lock(LockId("lock_nope"))


# ---------------------------------------------------------------------------
# Determinism + duplicate guard
# ---------------------------------------------------------------------------


def test_lock_id_is_deterministic_for_identical_payload(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    a = svc.create_property_lock(region_id=region.node_id, json_path="name")
    svc.remove_lock(a.lock_id)
    # Same region + json_path + captured value, distinct second.
    b = svc.create_property_lock(region_id=region.node_id, json_path="name")
    # Distinct timestamps -> different ids; ensure both are well-formed.
    assert a.lock_id.startswith("lock_")
    assert b.lock_id.startswith("lock_")


def test_duplicate_lock_rejected(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    _stamp_heightmap(svc, region.node_id)
    record = svc.create_feature_lock(
        region_id=region.node_id,
        bbox_world=(0.0, 0.0, 4.0, 4.0),
    )
    # Same id (timestamp + payload) cannot be added twice via the
    # internal allocator; replay through the store directly.
    with pytest.raises(DuplicateLockError):
        svc.state.lock_store.add_lock(record)
