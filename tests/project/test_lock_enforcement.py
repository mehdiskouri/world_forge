"""Phase 7 Stage B: lock-enforcement tests for property locks."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import numpy as np
import pytest
from forge_mcp.generate.heightmap import Heightmap, save_npy
from forge_mcp.project.lock_enforcement import (
    LockViolationError,
    check_property_locks,
)
from forge_mcp.project.schemas import (
    NodeId,
    RegionId,
    WorldBounds,
)
from forge_mcp.project.service import ProjectService
from forge_mcp.server.tools import set_service
from forge_mcp.server.tools.regions import update_region

if TYPE_CHECKING:
    from pathlib import Path

    from forge_mcp._types import JsonValue


_WORLD = WorldBounds(min=(-10.0, -10.0), max=(10.0, 10.0))
_SQUARE: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (8.0, 0.0),
    (8.0, 8.0),
    (0.0, 8.0),
)


def _bootstrap(tmp_path: Path) -> ProjectService:
    svc = ProjectService()
    svc.create_project(tmp_path, "Locks", _WORLD)
    return svc


# ---------------------------------------------------------------------------
# check_property_locks unit behaviour
# ---------------------------------------------------------------------------


def test_check_property_locks_noop_without_locks(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    doc = region.model_dump(mode="json")
    check_property_locks(svc.state, NodeId(str(region.node_id)), doc, doc)


def test_check_property_locks_ignores_other_region(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    svc.create_property_lock(region_id=region.node_id, json_path="name")
    other_doc = {"name": "anything"}
    check_property_locks(
        svc.state,
        NodeId("region_unrelated"),
        other_doc,
        other_doc,
    )


def test_check_property_locks_ignores_non_property_locks(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    heightmap = Heightmap(
        data=np.full((8, 8), 3.5, dtype=np.float32),
        resolution_meters_per_pixel=1.0,
        origin=(0.0, 0.0),
        elevation_band=(0.0, 1000.0),
    )
    save_npy(heightmap, svc.state.paths.heightmap_npy_path(region.node_id))
    # Only create a feature lock — no property lock present.
    svc.create_feature_lock(
        region_id=region.node_id,
        bbox_world=(1.0, 1.0, 5.0, 5.0),
    )
    after_doc = region.model_copy(update={"name": "Different"}).model_dump(mode="json")
    # Even though a lock targets the region, it is FEATURE-kind and must be
    # ignored by the property-lock check.
    check_property_locks(
        svc.state,
        NodeId(str(region.node_id)),
        region.model_dump(mode="json"),
        after_doc,
    )


def test_check_property_locks_raises_on_value_change(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    record = svc.create_property_lock(region_id=region.node_id, json_path="name")
    after_doc = region.model_copy(update={"name": "Different"}).model_dump(mode="json")
    with pytest.raises(LockViolationError) as info:
        check_property_locks(
            svc.state,
            NodeId(str(region.node_id)),
            region.model_dump(mode="json"),
            after_doc,
        )
    assert info.value.lock_id == record.lock_id
    assert info.value.json_path == "name"
    assert info.value.expected == "R"
    assert info.value.actual == "Different"


def test_check_property_locks_raises_on_missing_path(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    svc.create_property_lock(region_id=region.node_id, json_path="name")
    after_doc = cast("JsonValue", {})
    with pytest.raises(LockViolationError):
        check_property_locks(
            svc.state,
            NodeId(str(region.node_id)),
            region.model_dump(mode="json"),
            after_doc,
        )


# ---------------------------------------------------------------------------
# end-to-end via ProjectService.update_region
# ---------------------------------------------------------------------------


def test_update_region_blocked_by_property_lock_on_name(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("Foothills", _SQUARE)
    svc.create_property_lock(region_id=region.node_id, json_path="name")
    with pytest.raises(LockViolationError):
        svc.update_region(region.node_id, name="Renamed")
    # state must remain untouched
    assert svc.state.regions[region.node_id].name == "Foothills"


def test_update_region_allowed_when_lock_targets_unrelated_path(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("Foothills", _SQUARE)
    # Lock a path the update will not change.
    svc.create_property_lock(region_id=region.node_id, json_path="kind")
    updated = svc.update_region(region.node_id, name="Renamed")
    assert updated.name == "Renamed"


def test_update_region_blocked_lock_unknown_region_id_does_not_apply(
    tmp_path: Path,
) -> None:
    svc = _bootstrap(tmp_path)
    region_a = svc.create_region("A", _SQUARE)
    region_b = svc.create_region(
        "B",
        ((0.0, -8.0), (8.0, -8.0), (8.0, 0.0), (0.0, 0.0)),
    )
    svc.create_property_lock(region_id=region_a.node_id, json_path="name")
    # Updating B should be unaffected by a lock on A.
    updated = svc.update_region(region_b.node_id, name="B2")
    assert updated.name == "B2"
    # Sanity: the lock on A still binds.
    with pytest.raises(LockViolationError):
        svc.update_region(region_a.node_id, name="A2")
    _ = RegionId  # keep import used


# ---------------------------------------------------------------------------
# tool envelope mapping
# ---------------------------------------------------------------------------


def test_update_region_tool_returns_lock_violation_envelope(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("Foothills", _SQUARE)
    record = svc.create_property_lock(region_id=region.node_id, json_path="name")
    set_service(svc)
    result = update_region(str(region.node_id), name="Renamed")
    assert result["ok"] is False
    error = result["error"]
    assert isinstance(error, dict)
    assert error["code"] == "lock_violation"
    details = error["details"]
    assert isinstance(details, dict)
    assert details["lock_id"] == str(record.lock_id)
    assert details["json_path"] == "name"
    assert details["expected"] == "Foothills"
    assert details["actual"] == "Renamed"
