"""End-to-end tests for the Phase 7 Stage A lock MCP tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import numpy as np
import pytest
from forge_mcp.generate.heightmap import Heightmap, save_npy
from forge_mcp.project.service import ProjectService
from forge_mcp.server.tools import set_service
from forge_mcp.server.tools.locks import (
    lock_feature,
    lock_property,
    lock_region,
    unlock,
)
from forge_mcp.server.tools.projects import create_project
from forge_mcp.server.tools.regions import create_region

if TYPE_CHECKING:
    from pathlib import Path


_BOUNDS: dict[str, object] = {"min": [-10.0, -10.0], "max": [10.0, 10.0]}
_SQUARE = [[0.0, 0.0], [8.0, 0.0], [8.0, 8.0], [0.0, 8.0]]
_HEIGHTMAP_SHAPE = (8, 8)


@pytest.fixture(autouse=True)
def _isolated_service() -> None:
    set_service(ProjectService())


def _ok(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    assert isinstance(result, dict)
    return result


def _err(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is False, envelope
    error = envelope["error"]
    assert isinstance(error, dict)
    return error


def _bootstrap(tmp_path: Path) -> str:
    _ok(create_project(str(tmp_path), "Locks", _BOUNDS))
    region = _ok(create_region("R", _SQUARE))
    return cast("str", region["node_id"])


def _stamp_heightmap(region_id: str, value: float = 5.0) -> None:
    from forge_mcp.project.schemas import RegionId  # noqa: PLC0415 - test-only helper
    from forge_mcp.server.tools import get_service  # noqa: PLC0415 - test-only helper

    paths = get_service().state.paths
    npy_path = paths.heightmap_npy_path(RegionId(region_id))
    save_npy(
        Heightmap(
            data=np.full(_HEIGHTMAP_SHAPE, value, dtype=np.float32),
            resolution_meters_per_pixel=1.0,
            origin=(0.0, 0.0),
            elevation_band=(0.0, 1000.0),
        ),
        npy_path,
    )


# ---------------------------------------------------------------------------
# lock_property
# ---------------------------------------------------------------------------


def test_lock_property_round_trip(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    result = _ok(lock_property(region_id, "name"))
    lock = result["lock"]
    assert isinstance(lock, dict)
    assert lock["kind"] == "property"
    payload = lock["payload"]
    assert isinstance(payload, dict)
    assert payload["json_path"] == "name"
    assert payload["expected_value"] == "R"


def test_lock_property_no_open_project() -> None:
    err = _err(lock_property("region_x", "name"))
    assert err["code"] == "no_open_project"


def test_lock_property_unknown_region(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(lock_property("region_missing", "name"))
    assert err["code"] == "unknown_region"


def test_lock_property_target_not_found(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    err = _err(lock_property(region_id, "no.such.path"))
    assert err["code"] == "lock_target_not_found"


# ---------------------------------------------------------------------------
# lock_feature
# ---------------------------------------------------------------------------


def test_lock_feature_round_trip(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    _stamp_heightmap(region_id, value=2.25)
    result = _ok(lock_feature(region_id, [0.0, 0.0, 4.0, 4.0]))
    lock = result["lock"]
    assert isinstance(lock, dict)
    assert lock["kind"] == "feature"
    payload = lock["payload"]
    assert isinstance(payload, dict)
    assert payload["bbox_world"] == [0.0, 0.0, 4.0, 4.0]
    assert payload["captured_path"] == f"locks/feature/{lock['lock_id']}.npy"


def test_lock_feature_invalid_bbox_length(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    err = _err(lock_feature(region_id, [0.0, 0.0, 4.0]))
    assert err["code"] == "invalid_bbox"


def test_lock_feature_invalid_bbox_type(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    err = _err(lock_feature(region_id, [0.0, "x", 4.0, 4.0]))
    assert err["code"] == "invalid_bbox"


def test_lock_feature_invalid_bbox_bool(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    err = _err(lock_feature(region_id, [0.0, True, 4.0, 4.0]))
    assert err["code"] == "invalid_bbox"


def test_lock_feature_no_open_project() -> None:
    err = _err(lock_feature("region_x", [0.0, 0.0, 1.0, 1.0]))
    assert err["code"] == "no_open_project"


def test_lock_feature_unknown_region(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(lock_feature("region_missing", [0.0, 0.0, 1.0, 1.0]))
    assert err["code"] == "unknown_region"


def test_lock_feature_no_heightmap(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    err = _err(lock_feature(region_id, [0.0, 0.0, 1.0, 1.0]))
    assert err["code"] == "lock_target_not_found"


def test_lock_feature_overlap(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    _stamp_heightmap(region_id)
    _ok(lock_feature(region_id, [0.0, 0.0, 4.0, 4.0]))
    err = _err(lock_feature(region_id, [2.0, 2.0, 6.0, 6.0]))
    assert err["code"] == "overlapping_feature_lock"


# ---------------------------------------------------------------------------
# lock_region + unlock
# ---------------------------------------------------------------------------


def test_lock_region_round_trip(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    result = _ok(lock_region(region_id))
    lock = result["lock"]
    assert isinstance(lock, dict)
    assert lock["kind"] == "region"
    assert lock["payload"] == {"scope": "skip_regen"}


def test_lock_region_no_open_project() -> None:
    err = _err(lock_region("region_x"))
    assert err["code"] == "no_open_project"


def test_lock_region_unknown(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(lock_region("region_missing"))
    assert err["code"] == "unknown_region"


def test_unlock_round_trip(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    created = _ok(lock_property(region_id, "name"))
    lock = created["lock"]
    assert isinstance(lock, dict)
    lock_id = cast("str", lock["lock_id"])
    removed = _ok(unlock(lock_id))
    removed_lock = removed["lock"]
    assert isinstance(removed_lock, dict)
    assert removed_lock["lock_id"] == lock_id


def test_unlock_no_open_project() -> None:
    err = _err(unlock("lock_x"))
    assert err["code"] == "no_open_project"


def test_unlock_unknown(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(unlock("lock_missing"))
    assert err["code"] == "unknown_lock"
