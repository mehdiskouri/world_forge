"""Tests for the Phase 6-c sub-region CRUD surface on :class:`ProjectService`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from forge_mcp.project.schemas import (
    LAYER_MATERIAL_APPLICATION,
    LAYER_SPATIAL_CONTAINMENT,
    AspectPredicate,
    DistanceToStreamPredicate,
    HeightBandPredicate,
    MaterialApplicationAttrs,
    MaterialRecipe,
    MaterialScope,
    NodeId,
    RegionId,
    SlopePredicate,
    SubRegionId,
    WorldBounds,
)
from forge_mcp.project.service import (
    ProjectService,
    SubRegionInUseError,
    UnknownParentRegionError,
    UnknownSubRegionError,
)

if TYPE_CHECKING:
    from pathlib import Path

_WORLD = WorldBounds(min=(-10.0, -10.0), max=(10.0, 10.0))
_SQUARE: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (1.0, 0.0),
    (1.0, 1.0),
    (0.0, 1.0),
)
_PREDICATE_KIND_COUNT = 4
_REHYDRATE_LOW_M = 50.0


def _bootstrap(tmp_path: Path) -> ProjectService:
    svc = ProjectService()
    svc.create_project(tmp_path, "Eldoria", _WORLD)
    return svc


# ---------------------------------------------------------------------------
# Bootstrap / paths
# ---------------------------------------------------------------------------


def test_sub_regions_dir_is_bootstrapped(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    assert svc.state.paths.sub_regions_dir.is_dir()


# ---------------------------------------------------------------------------
# create_sub_region
# ---------------------------------------------------------------------------


def test_create_sub_region_persists_and_links_parent(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("Foothills", _SQUARE)
    sub = svc.create_sub_region(
        region.node_id,
        "High Band",
        HeightBandPredicate(low_m=100.0, high_m=500.0),
    )
    assert sub.node_id == SubRegionId("subregion_high_band")
    assert sub.parent_node == region.node_id

    # On disk
    on_disk_path = svc.state.paths.sub_region_path(sub.node_id)
    assert on_disk_path.exists()
    assert "height_band" in on_disk_path.read_text(encoding="utf-8")

    # Parent region updated with child id + spatial_containment edge present.
    parent = svc.state.regions[region.node_id]
    assert NodeId(str(sub.node_id)) in parent.children
    edges = svc.state.edges[LAYER_SPATIAL_CONTAINMENT]
    assert any(
        edge.endpoints == (NodeId(str(region.node_id)), NodeId(str(sub.node_id))) and edge.directed
        for edge in edges
    )


def test_create_sub_region_id_collision_appends_suffix(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    pred = SlopePredicate(min_deg=0.0, max_deg=10.0)
    a = svc.create_sub_region(region.node_id, "Flats", pred)
    b = svc.create_sub_region(region.node_id, "Flats", pred)
    assert a.node_id == SubRegionId("subregion_flats")
    assert b.node_id != a.node_id
    assert b.node_id.startswith("subregion_flats_")


def test_create_sub_region_with_unknown_parent_raises(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)

    with pytest.raises(UnknownParentRegionError):
        svc.create_sub_region(
            RegionId("region_missing"),
            "Orphan",
            HeightBandPredicate(low_m=0.0, high_m=10.0),
        )


def test_create_sub_region_with_each_predicate_kind_persists(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    svc.create_sub_region(
        region.node_id,
        "B",
        HeightBandPredicate(low_m=0.0, high_m=1.0),
    )
    svc.create_sub_region(
        region.node_id,
        "S",
        SlopePredicate(min_deg=0.0, max_deg=45.0),
    )
    svc.create_sub_region(
        region.node_id,
        "A",
        AspectPredicate(min_deg=270.0, max_deg=90.0),  # wrap through north
    )
    svc.create_sub_region(
        region.node_id,
        "D",
        DistanceToStreamPredicate(max_m=25.0),
    )
    assert len(svc.state.sub_regions) == _PREDICATE_KIND_COUNT


# ---------------------------------------------------------------------------
# update_sub_region
# ---------------------------------------------------------------------------


def test_update_sub_region_replaces_predicate(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    sub = svc.create_sub_region(
        region.node_id,
        "S",
        HeightBandPredicate(low_m=0.0, high_m=10.0),
    )
    new_pred = SlopePredicate(min_deg=10.0, max_deg=40.0)
    updated = svc.update_sub_region(sub.node_id, predicate=new_pred, notes="v2")
    assert updated.predicate == new_pred
    assert updated.notes == "v2"
    assert updated.modified_at >= sub.modified_at


def test_update_unknown_sub_region_raises(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    with pytest.raises(UnknownSubRegionError):
        svc.update_sub_region(
            SubRegionId("subregion_missing"),
            name="ghost",
        )


# ---------------------------------------------------------------------------
# delete_sub_region
# ---------------------------------------------------------------------------


def test_delete_sub_region_removes_file_edge_and_child_link(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    sub = svc.create_sub_region(
        region.node_id,
        "S",
        HeightBandPredicate(low_m=0.0, high_m=10.0),
    )
    path = svc.state.paths.sub_region_path(sub.node_id)
    assert path.exists()

    svc.delete_sub_region(sub.node_id)

    assert not path.exists()
    assert sub.node_id not in svc.state.sub_regions
    parent = svc.state.regions[region.node_id]
    assert NodeId(str(sub.node_id)) not in parent.children
    edges = svc.state.edges[LAYER_SPATIAL_CONTAINMENT]
    assert all(NodeId(str(sub.node_id)) not in e.endpoints for e in edges)


def test_delete_unknown_sub_region_raises(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    with pytest.raises(UnknownSubRegionError):
        svc.delete_sub_region(SubRegionId("subregion_missing"))


def test_delete_sub_region_in_use_is_refused(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    sub = svc.create_sub_region(
        region.node_id,
        "S",
        HeightBandPredicate(low_m=0.0, high_m=10.0),
    )
    arch = svc.create_material_archetype("Mat", MaterialRecipe.FLAT_COLOR, {})
    svc.apply_material(
        arch.node_id,
        NodeId(str(sub.node_id)),
        attrs=MaterialApplicationAttrs(scope=MaterialScope.SUB_REGION),
    )
    with pytest.raises(SubRegionInUseError):
        svc.delete_sub_region(sub.node_id)
    # The application edge must still be present.
    assert svc.state.edges[LAYER_MATERIAL_APPLICATION]


# ---------------------------------------------------------------------------
# Persistence round-trip: open_project rehydrates sub_regions.
# ---------------------------------------------------------------------------


def test_open_project_rehydrates_sub_regions(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    sub = svc.create_sub_region(
        region.node_id,
        "Hi",
        HeightBandPredicate(low_m=_REHYDRATE_LOW_M, high_m=200.0),
    )
    sub_id = sub.node_id
    svc.save_project()
    svc.close_project()

    svc2 = ProjectService()
    svc2.open_project(tmp_path)
    assert sub_id in svc2.state.sub_regions
    rehydrated = svc2.state.sub_regions[sub_id]
    assert rehydrated.parent_node == region.node_id
    assert isinstance(rehydrated.predicate, HeightBandPredicate)
    assert rehydrated.predicate.low_m == _REHYDRATE_LOW_M
