"""End-to-end tests for the sub-region MCP tools (Phase 6-c Phase E)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import numpy as np
import pytest
from forge_mcp.generate.heightmap import Heightmap, save_npy
from forge_mcp.project.service import ProjectService
from forge_mcp.server.tools import set_service
from forge_mcp.server.tools.materials import (
    apply_material,
    create_material_archetype,
)
from forge_mcp.server.tools.projects import create_project
from forge_mcp.server.tools.regions import create_region
from forge_mcp.server.tools.sub_regions import (
    create_sub_region,
    delete_sub_region,
    get_sub_region,
    list_sub_regions,
    preview_sub_region_coverage,
    update_sub_region,
)

if TYPE_CHECKING:
    from pathlib import Path

_BOUNDS: dict[str, object] = {"min": [-10.0, -10.0], "max": [10.0, 10.0]}
_SQUARE = [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]
_SQUARE_B = [[5.0, 5.0], [7.0, 5.0], [7.0, 7.0], [5.0, 7.0]]
_HEIGHT_BAND_PRED: dict[str, object] = {
    "kind": "height_band",
    "low_m": 50.0,
    "high_m": 100.0,
}
_SLOPE_PRED: dict[str, object] = {"kind": "slope", "min_deg": 0.0, "max_deg": 45.0}
_HEIGHTMAP_SHAPE = (8, 8)
_FULL_COVERAGE_ELEVATION = 75.0
_OUT_OF_BAND_ELEVATION = 200.0
_EXPECTED_PRED_COUNT = 2


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
    _ok(create_project(str(tmp_path), "SR World", _BOUNDS))
    region = _ok(create_region("R", _SQUARE))
    return cast("str", region["node_id"])


def _persist_heightmap(region_id: str, value: float) -> None:
    """Stamp a flat heightmap onto disk for ``preview_sub_region_coverage``."""
    from forge_mcp.project.schemas import RegionId  # noqa: PLC0415 - test-only helper
    from forge_mcp.server.tools import get_service  # noqa: PLC0415 - test-only helper

    paths = get_service().state.paths
    npy_path = paths.heightmap_npy_path(RegionId(region_id))
    heightmap = Heightmap(
        data=np.full(_HEIGHTMAP_SHAPE, value, dtype=np.float32),
        resolution_meters_per_pixel=1.0,
        origin=(0.0, 0.0),
        elevation_band=(0.0, 1000.0),
    )
    save_npy(heightmap, npy_path)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_sub_region_returns_record(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    rec = _ok(create_sub_region(region_id, "Highlands", _HEIGHT_BAND_PRED, tags=["alpine"]))
    assert rec["name"] == "Highlands"
    assert rec["kind"] == "sub_region"
    predicate = rec["predicate"]
    assert isinstance(predicate, dict)
    assert predicate["kind"] == "height_band"


def test_create_sub_region_no_open_project() -> None:
    err = _err(create_sub_region("region_x", "X", _HEIGHT_BAND_PRED))
    assert err["code"] == "no_open_project"


def test_create_sub_region_unknown_parent(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(create_sub_region("region_missing", "X", _HEIGHT_BAND_PRED))
    assert err["code"] == "unknown_parent_region"


def test_create_sub_region_invalid_predicate_kind(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    err = _err(create_sub_region(region_id, "X", {"kind": "bogus"}))
    assert err["code"] == "invalid_predicate"


def test_create_sub_region_invalid_predicate_bounds(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    err = _err(
        create_sub_region(
            region_id,
            "X",
            {"kind": "height_band", "low_m": 100.0, "high_m": 50.0},
        ),
    )
    assert err["code"] == "invalid_predicate"


def test_create_sub_region_invalid_predicate_shape(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    err = _err(create_sub_region(region_id, "X", "not-a-dict"))
    assert err["code"] == "invalid_predicate"


def test_create_sub_region_missing_predicate(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    err = _err(create_sub_region(region_id, "X", None))
    assert err["code"] == "invalid_predicate"


def test_create_sub_region_invalid_tags(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    err = _err(create_sub_region(region_id, "X", _HEIGHT_BAND_PRED, tags=[1, 2]))
    assert err["code"] == "invalid_tags"


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_sub_region_changes_predicate(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    rec = _ok(create_sub_region(region_id, "Highlands", _HEIGHT_BAND_PRED))
    sub_id = cast("str", rec["node_id"])
    updated = _ok(update_sub_region(sub_id, predicate=_SLOPE_PRED))
    pred = updated["predicate"]
    assert isinstance(pred, dict)
    assert pred["kind"] == "slope"


def test_update_sub_region_unknown(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(update_sub_region("subregion_missing", name="x"))
    assert err["code"] == "unknown_sub_region"


def test_update_sub_region_invalid_predicate(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    rec = _ok(create_sub_region(region_id, "Highlands", _HEIGHT_BAND_PRED))
    sub_id = cast("str", rec["node_id"])
    err = _err(update_sub_region(sub_id, predicate={"kind": "bogus"}))
    assert err["code"] == "invalid_predicate"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_sub_region_succeeds_when_unused(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    rec = _ok(create_sub_region(region_id, "Highlands", _HEIGHT_BAND_PRED))
    sub_id = cast("str", rec["node_id"])
    _ok(delete_sub_region(sub_id))
    err = _err(get_sub_region(sub_id))
    assert err["code"] == "unknown_sub_region"


def test_delete_sub_region_unknown(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(delete_sub_region("subregion_missing"))
    assert err["code"] == "unknown_sub_region"


def test_delete_sub_region_in_use(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    rec = _ok(create_sub_region(region_id, "Highlands", _HEIGHT_BAND_PRED))
    sub_id = cast("str", rec["node_id"])
    arch = _ok(
        create_material_archetype("Snow", "flat_color", {"color": [1.0, 1.0, 1.0, 1.0]}),
    )
    _ok(
        apply_material(
            cast("str", arch["node_id"]),
            sub_id,
            attrs={"scope": "sub_region", "priority": 5},
        ),
    )
    err = _err(delete_sub_region(sub_id))
    assert err["code"] == "sub_region_in_use"


# ---------------------------------------------------------------------------
# List / get
# ---------------------------------------------------------------------------


def test_list_sub_regions_filter_by_parent(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    region_b = _ok(create_region("R2", _SQUARE_B))
    region_b_id = cast("str", region_b["node_id"])
    _ok(create_sub_region(region_id, "A", _HEIGHT_BAND_PRED))
    _ok(create_sub_region(region_b_id, "B", _SLOPE_PRED))
    full = _ok(list_sub_regions())
    subs = full["sub_regions"]
    assert isinstance(subs, list)
    assert len(subs) == _EXPECTED_PRED_COUNT
    filtered = _ok(list_sub_regions(parent_region_id=region_id))
    filtered_subs = filtered["sub_regions"]
    assert isinstance(filtered_subs, list)
    assert len(filtered_subs) == 1
    only = filtered_subs[0]
    assert isinstance(only, dict)
    assert only["parent_region_id"] == region_id


def test_get_sub_region_returns_record(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    rec = _ok(create_sub_region(region_id, "Highlands", _HEIGHT_BAND_PRED))
    sub_id = cast("str", rec["node_id"])
    fetched = _ok(get_sub_region(sub_id))
    assert fetched["node_id"] == sub_id


def test_get_sub_region_unknown(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(get_sub_region("subregion_missing"))
    assert err["code"] == "unknown_sub_region"


# ---------------------------------------------------------------------------
# Coverage preview
# ---------------------------------------------------------------------------


def test_preview_sub_region_coverage_full_band(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    rec = _ok(create_sub_region(region_id, "Highlands", _HEIGHT_BAND_PRED))
    sub_id = cast("str", rec["node_id"])
    _persist_heightmap(region_id, _FULL_COVERAGE_ELEVATION)
    result = _ok(preview_sub_region_coverage(sub_id))
    assert result["coverage_fraction"] == pytest.approx(1.0)
    assert result["vertex_count"] == _HEIGHTMAP_SHAPE[0] * _HEIGHTMAP_SHAPE[1]
    bbox = result["bbox_uv"]
    assert isinstance(bbox, list)


def test_preview_sub_region_coverage_out_of_band(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    rec = _ok(create_sub_region(region_id, "Highlands", _HEIGHT_BAND_PRED))
    sub_id = cast("str", rec["node_id"])
    _persist_heightmap(region_id, _OUT_OF_BAND_ELEVATION)
    result = _ok(preview_sub_region_coverage(sub_id))
    assert result["coverage_fraction"] == pytest.approx(0.0)
    assert result["vertex_count"] == 0
    assert result["bbox_uv"] is None


def test_preview_sub_region_coverage_not_generated(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    rec = _ok(create_sub_region(region_id, "Highlands", _HEIGHT_BAND_PRED))
    sub_id = cast("str", rec["node_id"])
    err = _err(preview_sub_region_coverage(sub_id))
    assert err["code"] == "not_generated"


def test_preview_sub_region_coverage_unknown(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(preview_sub_region_coverage("subregion_missing"))
    assert err["code"] == "unknown_sub_region"


# ---------------------------------------------------------------------------
# no_open_project guards
# ---------------------------------------------------------------------------


def test_update_sub_region_no_open_project() -> None:
    err = _err(update_sub_region("subregion_x", name="x"))
    assert err["code"] == "no_open_project"


def test_delete_sub_region_no_open_project() -> None:
    err = _err(delete_sub_region("subregion_x"))
    assert err["code"] == "no_open_project"


def test_list_sub_regions_no_open_project() -> None:
    err = _err(list_sub_regions())
    assert err["code"] == "no_open_project"


def test_get_sub_region_no_open_project() -> None:
    err = _err(get_sub_region("subregion_x"))
    assert err["code"] == "no_open_project"


def test_preview_sub_region_coverage_no_open_project() -> None:
    err = _err(preview_sub_region_coverage("subregion_x"))
    assert err["code"] == "no_open_project"


def test_update_sub_region_invalid_tags(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    rec = _ok(create_sub_region(region_id, "Highlands", _HEIGHT_BAND_PRED))
    sub_id = cast("str", rec["node_id"])
    err = _err(update_sub_region(sub_id, tags=[1, 2]))
    assert err["code"] == "invalid_tags"


def test_update_sub_region_predicate_invalid_shape(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    rec = _ok(create_sub_region(region_id, "Highlands", _HEIGHT_BAND_PRED))
    sub_id = cast("str", rec["node_id"])
    err = _err(update_sub_region(sub_id, predicate="not-a-dict"))
    assert err["code"] == "invalid_predicate"


def test_update_sub_region_partial_tags(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    rec = _ok(create_sub_region(region_id, "Highlands", _HEIGHT_BAND_PRED))
    sub_id = cast("str", rec["node_id"])
    updated = _ok(update_sub_region(sub_id, tags=["alpine", "high"]))
    assert updated["tags"] == ["alpine", "high"]
