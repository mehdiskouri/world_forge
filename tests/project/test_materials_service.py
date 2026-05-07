"""Tests for the Phase 6-bis material-archetype + edge service surface."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from forge_mcp.project.schemas import (
    HeightRampMask,
    MaterialApplicationAttrs,
    MaterialArchetypeId,
    MaterialCompositionAttrs,
    MaterialCompositionMode,
    MaterialRecipe,
    MaterialScope,
    NodeId,
    WorldBounds,
)
from forge_mcp.project.service import (
    ArchetypeInUseError,
    MaterialCompositionCycleError,
    MaterialEdgePolicyError,
    ProjectService,
    UnknownArchetypeError,
    UnknownMaterialEdgeError,
    UnknownTargetNodeError,
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


def _bootstrap(tmp_path: Path) -> ProjectService:
    svc = ProjectService()
    svc.create_project(tmp_path, "Eldoria", _WORLD)
    return svc


# ---------------------------------------------------------------------------
# Archetype CRUD
# ---------------------------------------------------------------------------


def test_create_archetype_persists_to_nodes_dir(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    arch = svc.create_material_archetype(
        "Granite Cliff",
        MaterialRecipe.TRIPLANAR_ROCK,
        {"base_color": [0.3, 0.3, 0.32, 1.0], "scale_meters": 2.0},
    )
    assert arch.node_id == MaterialArchetypeId("material_granite_cliff")
    assert arch.recipe is MaterialRecipe.TRIPLANAR_ROCK
    on_disk = (svc.state.paths.nodes_dir / f"{arch.node_id}.json").read_text(
        encoding="utf-8",
    )
    assert "triplanar_rock" in on_disk


def test_archetype_id_collision_appends_suffix(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    a = svc.create_material_archetype("Rock", MaterialRecipe.FLAT_COLOR, {})
    b = svc.create_material_archetype("Rock", MaterialRecipe.FLAT_COLOR, {})
    assert a.node_id == MaterialArchetypeId("material_rock")
    assert b.node_id != a.node_id
    assert b.node_id.startswith("material_rock_")


def test_update_archetype_changes_parameters_and_modified_at(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    arch = svc.create_material_archetype("Rock", MaterialRecipe.FLAT_COLOR, {"a": 1})
    updated = svc.update_material_archetype(arch.node_id, parameters={"a": 2}, notes="v2")
    assert updated.parameters == {"a": 2}
    assert updated.notes == "v2"
    assert updated.modified_at >= arch.modified_at


def test_update_unknown_archetype_raises(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    with pytest.raises(UnknownArchetypeError):
        svc.update_material_archetype(MaterialArchetypeId("material_missing"), name="x")


def test_delete_archetype_removes_file(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    arch = svc.create_material_archetype("Doomed", MaterialRecipe.FLAT_COLOR, {})
    path = svc.state.paths.nodes_dir / f"{arch.node_id}.json"
    assert path.exists()
    svc.delete_material_archetype(arch.node_id)
    assert not path.exists()
    assert arch.node_id not in svc.state.archetypes


def test_delete_archetype_in_use_is_refused(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R1", _SQUARE)
    arch = svc.create_material_archetype("In Use", MaterialRecipe.FLAT_COLOR, {})
    svc.apply_material(
        arch.node_id,
        NodeId(str(region.node_id)),
        attrs=MaterialApplicationAttrs(scope=MaterialScope.REGION),
    )
    with pytest.raises(ArchetypeInUseError):
        svc.delete_material_archetype(arch.node_id)


# ---------------------------------------------------------------------------
# apply / unapply material
# ---------------------------------------------------------------------------


def test_apply_material_creates_directed_edge(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    arch = svc.create_material_archetype("Mat", MaterialRecipe.FLAT_COLOR, {})
    edge = svc.apply_material(
        arch.node_id,
        NodeId(str(region.node_id)),
        attrs=MaterialApplicationAttrs(
            scope=MaterialScope.REGION,
            priority=5,
            mask=HeightRampMask(low_m=0.0, high_m=1.0),
        ),
    )
    assert edge.directed
    assert edge.endpoints == (NodeId(str(arch.node_id)), NodeId(str(region.node_id)))
    priority = edge.attrs["priority"]
    expected_priority = 5
    assert priority == expected_priority
    on_disk = svc.state.paths.edge_layer_path("material_application")
    assert edge.edge_id in on_disk.read_text(encoding="utf-8")


def test_apply_material_world_scope_requires_world_root(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    arch = svc.create_material_archetype("Mat", MaterialRecipe.FLAT_COLOR, {})
    with pytest.raises(MaterialEdgePolicyError):
        svc.apply_material(
            arch.node_id,
            NodeId(str(region.node_id)),
            attrs=MaterialApplicationAttrs(scope=MaterialScope.WORLD),
        )


def test_apply_material_unknown_target_raises(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    arch = svc.create_material_archetype("Mat", MaterialRecipe.FLAT_COLOR, {})
    with pytest.raises(UnknownTargetNodeError):
        svc.apply_material(
            arch.node_id,
            NodeId("region_missing"),
            attrs=MaterialApplicationAttrs(scope=MaterialScope.REGION),
        )


def test_apply_material_unknown_archetype_raises(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    with pytest.raises(UnknownArchetypeError):
        svc.apply_material(
            MaterialArchetypeId("material_missing"),
            NodeId(str(region.node_id)),
            attrs=MaterialApplicationAttrs(scope=MaterialScope.REGION),
        )


def test_unapply_material_removes_edge(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    arch = svc.create_material_archetype("Mat", MaterialRecipe.FLAT_COLOR, {})
    edge = svc.apply_material(
        arch.node_id,
        NodeId(str(region.node_id)),
        attrs=MaterialApplicationAttrs(scope=MaterialScope.REGION),
    )
    svc.unapply_material(edge.edge_id)
    assert not svc.state.edges["material_application"]


def test_unapply_unknown_edge_raises(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    with pytest.raises(UnknownMaterialEdgeError):
        svc.unapply_material("matapp_nope")


# ---------------------------------------------------------------------------
# compose / uncompose
# ---------------------------------------------------------------------------


def test_compose_material_creates_directed_extends_edge(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    parent = svc.create_material_archetype("Parent", MaterialRecipe.FLAT_COLOR, {})
    child = svc.create_material_archetype("Child", MaterialRecipe.FLAT_COLOR, {})
    edge = svc.compose_material(
        parent.node_id,
        child.node_id,
        attrs=MaterialCompositionAttrs(mode=MaterialCompositionMode.EXTENDS),
    )
    assert edge.directed
    assert edge.endpoints == (
        NodeId(str(parent.node_id)),
        NodeId(str(child.node_id)),
    )


def test_compose_material_self_loop_refused(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    arch = svc.create_material_archetype("Mat", MaterialRecipe.FLAT_COLOR, {})
    with pytest.raises(MaterialEdgePolicyError):
        svc.compose_material(
            arch.node_id,
            arch.node_id,
            attrs=MaterialCompositionAttrs(mode=MaterialCompositionMode.EXTENDS),
        )


def test_compose_material_cycle_refused_and_rolled_back(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    a = svc.create_material_archetype("A", MaterialRecipe.FLAT_COLOR, {})
    b = svc.create_material_archetype("B", MaterialRecipe.FLAT_COLOR, {})
    svc.compose_material(
        a.node_id,
        b.node_id,
        attrs=MaterialCompositionAttrs(mode=MaterialCompositionMode.EXTENDS),
    )
    with pytest.raises(MaterialCompositionCycleError):
        svc.compose_material(
            b.node_id,
            a.node_id,
            attrs=MaterialCompositionAttrs(mode=MaterialCompositionMode.EXTENDS),
        )
    # Roll-back: still exactly one composition edge persisted.
    assert len(svc.state.edges["material_composition"]) == 1


def test_uncompose_material_removes_edge(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    a = svc.create_material_archetype("A", MaterialRecipe.FLAT_COLOR, {})
    b = svc.create_material_archetype("B", MaterialRecipe.FLAT_COLOR, {})
    edge = svc.compose_material(
        a.node_id,
        b.node_id,
        attrs=MaterialCompositionAttrs(mode=MaterialCompositionMode.EXTENDS),
    )
    svc.uncompose_material(edge.edge_id)
    assert not svc.state.edges["material_composition"]


def test_compose_unknown_archetype_raises(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    a = svc.create_material_archetype("A", MaterialRecipe.FLAT_COLOR, {})
    with pytest.raises(UnknownArchetypeError):
        svc.compose_material(
            a.node_id,
            MaterialArchetypeId("material_missing"),
            attrs=MaterialCompositionAttrs(mode=MaterialCompositionMode.EXTENDS),
        )


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------


def test_archetypes_round_trip_through_close_open(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    arch = svc.create_material_archetype(
        "Granite",
        MaterialRecipe.TRIPLANAR_ROCK,
        {"base_color": [0.5, 0.5, 0.5, 1.0]},
        tags=("rock", "rough"),
    )
    region = svc.create_region("R", _SQUARE)
    edge = svc.apply_material(
        arch.node_id,
        NodeId(str(region.node_id)),
        attrs=MaterialApplicationAttrs(
            scope=MaterialScope.REGION,
            mask=HeightRampMask(low_m=2.0, high_m=8.0),
        ),
    )
    svc.close_project()
    svc2 = ProjectService()
    svc2.open_project(tmp_path)
    assert arch.node_id in svc2.state.archetypes
    reloaded_edge = svc2.state.edges["material_application"][0]
    assert reloaded_edge.edge_id == edge.edge_id
    mask = reloaded_edge.attrs["mask"]
    assert isinstance(mask, dict)
    assert mask["kind"] == "height_ramp"


# ---------------------------------------------------------------------------
# Schema validators
# ---------------------------------------------------------------------------


def test_height_ramp_mask_rejects_inverted_band() -> None:
    with pytest.raises(ValueError, match="low_m < high_m"):
        HeightRampMask(low_m=10.0, high_m=2.0)


def test_composition_extends_rejects_mask_or_weight() -> None:
    with pytest.raises(ValueError, match="must not carry mask or weight"):
        MaterialCompositionAttrs(
            mode=MaterialCompositionMode.EXTENDS,
            mask=HeightRampMask(low_m=0.0, high_m=1.0),
        )
    with pytest.raises(ValueError, match="must not carry mask or weight"):
        MaterialCompositionAttrs(mode=MaterialCompositionMode.EXTENDS, weight=0.5)
