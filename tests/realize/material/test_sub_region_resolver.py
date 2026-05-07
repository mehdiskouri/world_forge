"""Phase 6-c: resolver awareness of sub-region material applications."""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_mcp.project.schemas import (
    HeightBandPredicate,
    HeightRampMask,
    MaterialApplicationAttrs,
    MaterialRecipe,
    MaterialScope,
    NodeId,
    PredicateMask,
    SlopePredicate,
    WorldBounds,
)
from forge_mcp.project.service import ProjectService
from forge_mcp.realize.material import resolve_plan

if TYPE_CHECKING:
    from pathlib import Path

    from forge_mcp._types import JsonValue

_WORLD = WorldBounds(min=(-10.0, -10.0), max=(10.0, 10.0))
_SQUARE: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (2.0, 0.0),
    (2.0, 2.0),
    (0.0, 2.0),
)
_FLAT_COLOR_PARAMS: dict[str, JsonValue] = {"color": [0.5, 0.5, 0.5, 1.0]}
_HIGHLANDS_LOW_M = 100.0


def _bootstrap(tmp_path: Path) -> ProjectService:
    svc = ProjectService()
    svc.create_project(tmp_path, "Eldoria", _WORLD)
    return svc


def test_sub_region_application_emits_layer_with_predicate_mask(tmp_path: Path) -> None:
    """Applying to a sub-region wraps the predicate as `predicate_mask`."""
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    sub = svc.create_sub_region(
        region.node_id,
        "Highlands",
        HeightBandPredicate(low_m=100.0, high_m=500.0),
    )
    arch = svc.create_material_archetype("Snow", MaterialRecipe.FLAT_COLOR, _FLAT_COLOR_PARAMS)
    svc.apply_material(
        arch.node_id,
        NodeId(str(sub.node_id)),
        attrs=MaterialApplicationAttrs(scope=MaterialScope.SUB_REGION),
    )
    plan = resolve_plan(
        svc.state,
        region.node_id,
        mesh_name="terrain_r",
        elevation_min=0.0,
        elevation_max=1000.0,
    )
    # Exactly one layer (the sub-region application), carrying its predicate.
    assert len(plan.layers) == 1
    layer = plan.layers[0]
    assert layer.archetype_id == arch.node_id
    assert isinstance(layer.predicate_mask, PredicateMask)
    assert isinstance(layer.predicate_mask.predicate, HeightBandPredicate)
    assert layer.predicate_mask.predicate.low_m == _HIGHLANDS_LOW_M
    assert layer.mask is None  # application carries no own mask


def test_sub_region_application_combines_predicate_with_application_mask(
    tmp_path: Path,
) -> None:
    """When the application carries a mask, both predicate and mask survive."""
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    sub = svc.create_sub_region(
        region.node_id,
        "Slopes",
        SlopePredicate(min_deg=20.0, max_deg=60.0),
    )
    arch = svc.create_material_archetype("Rock", MaterialRecipe.FLAT_COLOR, _FLAT_COLOR_PARAMS)
    own_mask = HeightRampMask(low_m=200.0, high_m=300.0)
    svc.apply_material(
        arch.node_id,
        NodeId(str(sub.node_id)),
        attrs=MaterialApplicationAttrs(scope=MaterialScope.SUB_REGION, mask=own_mask),
    )
    plan = resolve_plan(
        svc.state,
        region.node_id,
        mesh_name="terrain_r",
        elevation_min=0.0,
        elevation_max=1000.0,
    )
    layer = plan.layers[0]
    assert isinstance(layer.predicate_mask, PredicateMask)
    assert isinstance(layer.predicate_mask.predicate, SlopePredicate)
    # Application's own mask must remain on `mask` so the adapter can
    # multiply both factors (predicate gates, mask modulates).
    assert layer.mask == own_mask


def test_region_only_plan_unchanged_when_sub_regions_present_but_unapplied(
    tmp_path: Path,
) -> None:
    """Adding a sub-region without applying material to it does NOT alter the plan."""
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    arch = svc.create_material_archetype("Base", MaterialRecipe.FLAT_COLOR, _FLAT_COLOR_PARAMS)
    svc.apply_material(
        arch.node_id,
        NodeId(str(region.node_id)),
        attrs=MaterialApplicationAttrs(scope=MaterialScope.REGION),
    )
    baseline = resolve_plan(
        svc.state,
        region.node_id,
        mesh_name="terrain_r",
        elevation_min=0.0,
        elevation_max=1000.0,
    )
    # Add a sub-region but apply nothing to it.
    svc.create_sub_region(
        region.node_id,
        "Idle",
        HeightBandPredicate(low_m=0.0, high_m=10.0),
    )
    after = resolve_plan(
        svc.state,
        region.node_id,
        mesh_name="terrain_r",
        elevation_min=0.0,
        elevation_max=1000.0,
    )
    assert baseline.plan_id == after.plan_id


def test_sub_region_layer_orders_after_region_layer(tmp_path: Path) -> None:
    """sub_region precedence (3) > region precedence (2): sub_region renders last."""
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    sub = svc.create_sub_region(
        region.node_id,
        "Top",
        HeightBandPredicate(low_m=100.0, high_m=500.0),
    )
    base_arch = svc.create_material_archetype("Base", MaterialRecipe.FLAT_COLOR, _FLAT_COLOR_PARAMS)
    sub_arch = svc.create_material_archetype("Top", MaterialRecipe.FLAT_COLOR, _FLAT_COLOR_PARAMS)
    svc.apply_material(
        base_arch.node_id,
        NodeId(str(region.node_id)),
        attrs=MaterialApplicationAttrs(scope=MaterialScope.REGION),
    )
    svc.apply_material(
        sub_arch.node_id,
        NodeId(str(sub.node_id)),
        attrs=MaterialApplicationAttrs(scope=MaterialScope.SUB_REGION),
    )
    plan = resolve_plan(
        svc.state,
        region.node_id,
        mesh_name="terrain_r",
        elevation_min=0.0,
        elevation_max=1000.0,
    )
    assert [layer.archetype_id for layer in plan.layers] == [
        base_arch.node_id,
        sub_arch.node_id,
    ]
    # Region layer carries no predicate_mask; sub_region layer does.
    assert plan.layers[0].predicate_mask is None
    assert plan.layers[1].predicate_mask is not None


def test_updating_sub_region_predicate_changes_plan_id(tmp_path: Path) -> None:
    """Predicate is part of the resolved layer body → plan_id is sensitive to it."""
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    sub = svc.create_sub_region(
        region.node_id,
        "S",
        HeightBandPredicate(low_m=100.0, high_m=200.0),
    )
    arch = svc.create_material_archetype("M", MaterialRecipe.FLAT_COLOR, _FLAT_COLOR_PARAMS)
    svc.apply_material(
        arch.node_id,
        NodeId(str(sub.node_id)),
        attrs=MaterialApplicationAttrs(scope=MaterialScope.SUB_REGION),
    )
    before = resolve_plan(
        svc.state,
        region.node_id,
        mesh_name="terrain_r",
        elevation_min=0.0,
        elevation_max=1000.0,
    )
    svc.update_sub_region(
        sub.node_id,
        predicate=HeightBandPredicate(low_m=300.0, high_m=400.0),
    )
    after = resolve_plan(
        svc.state,
        region.node_id,
        mesh_name="terrain_r",
        elevation_min=0.0,
        elevation_max=1000.0,
    )
    assert before.plan_id != after.plan_id


def test_resolve_plan_is_deterministic_with_sub_region(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("R", _SQUARE)
    sub = svc.create_sub_region(
        region.node_id,
        "S",
        HeightBandPredicate(low_m=100.0, high_m=200.0),
    )
    arch = svc.create_material_archetype("M", MaterialRecipe.FLAT_COLOR, _FLAT_COLOR_PARAMS)
    svc.apply_material(
        arch.node_id,
        NodeId(str(sub.node_id)),
        attrs=MaterialApplicationAttrs(scope=MaterialScope.SUB_REGION),
    )
    a = resolve_plan(
        svc.state,
        region.node_id,
        mesh_name="terrain_r",
        elevation_min=0.0,
        elevation_max=1000.0,
    )
    b = resolve_plan(
        svc.state,
        region.node_id,
        mesh_name="terrain_r",
        elevation_min=0.0,
        elevation_max=1000.0,
    )
    assert a.plan_id == b.plan_id
