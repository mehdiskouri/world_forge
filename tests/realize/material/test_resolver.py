"""Tests for the composite-material resolver and plan IR."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from forge_mcp.project.schemas import (
    HeightRampMask,
    MaterialApplicationAttrs,
    MaterialCompositionAttrs,
    MaterialCompositionMode,
    MaterialRecipe,
    MaterialScope,
    NodeId,
    SlopeMask,
    WorldBounds,
)
from forge_mcp.project.service import ProjectService
from forge_mcp.realize.material import (
    DEFAULT_TERRAIN_ARCHETYPE_ID,
    CompositeMaterialPlan,
    compute_plan_id,
    default_terrain_archetype,
    make_plan,
    resolve_plan,
    validate_recipe_parameters,
)
from forge_mcp.realize.material.defaults import RecipeParameterError
from forge_mcp.realize.material.plan import ResolvedLayer
from forge_mcp.realize.material.resolver import ResolverError

if TYPE_CHECKING:
    from pathlib import Path

    from forge_mcp.project.schemas import RegionId

_WORLD = WorldBounds(min=(-10.0, -10.0), max=(10.0, 10.0))
_SQUARE: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (2.0, 0.0),
    (2.0, 2.0),
    (0.0, 2.0),
)


def _bootstrap(tmp_path: Path) -> tuple[ProjectService, RegionId]:
    svc = ProjectService()
    svc.create_project(tmp_path, "Eldoria", _WORLD)
    region = svc.create_region("R", _SQUARE)
    return svc, region.node_id


# ---------------------------------------------------------------------------
# plan id + helpers
# ---------------------------------------------------------------------------


def test_compute_plan_id_is_deterministic_and_format() -> None:
    archetype = default_terrain_archetype(elevation_min=0.0, elevation_max=10.0)
    layer = ResolvedLayer(
        archetype_id=archetype.node_id,
        recipe=archetype.recipe,
        parameters=dict(archetype.parameters),
    )
    region_id = "region_alpha"
    plan_id_a = compute_plan_id(region_id, "terrain_alpha", (layer,))  # type: ignore[arg-type]
    plan_id_b = compute_plan_id(region_id, "terrain_alpha", (layer,))  # type: ignore[arg-type]
    assert plan_id_a == plan_id_b
    assert plan_id_a.startswith("mplan_")
    expected_length = len("mplan_") + 20
    assert len(plan_id_a) == expected_length


def test_make_plan_rejects_empty_layers() -> None:
    with pytest.raises(ValueError, match=">= 1 layer"):
        make_plan("region_x", "mesh_x", ())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# resolver — empty applications
# ---------------------------------------------------------------------------


def test_resolve_plan_falls_back_to_default_archetype(tmp_path: Path) -> None:
    svc, region_id = _bootstrap(tmp_path)
    plan = resolve_plan(
        svc.state,
        region_id,
        mesh_name="terrain_R",
        elevation_min=0.0,
        elevation_max=10.0,
    )
    assert isinstance(plan, CompositeMaterialPlan)
    assert len(plan.layers) == 1
    assert plan.layers[0].archetype_id == DEFAULT_TERRAIN_ARCHETYPE_ID
    assert plan.layers[0].recipe is MaterialRecipe.PRINCIPLED_HEIGHT_RAMP


def test_default_plan_id_changes_with_elevation_band(tmp_path: Path) -> None:
    svc, region_id = _bootstrap(tmp_path)
    plan_a = resolve_plan(
        svc.state,
        region_id,
        mesh_name="m",
        elevation_min=0.0,
        elevation_max=10.0,
    )
    plan_b = resolve_plan(
        svc.state,
        region_id,
        mesh_name="m",
        elevation_min=0.0,
        elevation_max=20.0,
    )
    assert plan_a.plan_id != plan_b.plan_id


# ---------------------------------------------------------------------------
# resolver — single application
# ---------------------------------------------------------------------------


def test_resolve_plan_single_application_uses_archetype_params(tmp_path: Path) -> None:
    svc, region_id = _bootstrap(tmp_path)
    arch = svc.create_material_archetype(
        "Granite",
        MaterialRecipe.TRIPLANAR_ROCK,
        {"base_color": [0.4, 0.4, 0.45, 1.0], "scale_meters": 2.0},
    )
    svc.apply_material(
        arch.node_id,
        NodeId(str(region_id)),
        attrs=MaterialApplicationAttrs(scope=MaterialScope.REGION, priority=10),
    )
    plan = resolve_plan(
        svc.state,
        region_id,
        mesh_name="m",
        elevation_min=0.0,
        elevation_max=10.0,
    )
    assert len(plan.layers) == 1
    layer = plan.layers[0]
    assert layer.archetype_id == arch.node_id
    expected_scale = 2.0
    assert layer.parameters["scale_meters"] == expected_scale


def test_resolve_plan_applies_parameter_overrides(tmp_path: Path) -> None:
    svc, region_id = _bootstrap(tmp_path)
    arch = svc.create_material_archetype(
        "Mat",
        MaterialRecipe.TRIPLANAR_ROCK,
        {"base_color": [0.5, 0.5, 0.5, 1.0], "scale_meters": 1.0},
    )
    svc.apply_material(
        arch.node_id,
        NodeId(str(region_id)),
        attrs=MaterialApplicationAttrs(
            scope=MaterialScope.REGION,
            parameter_overrides={"scale_meters": 5.0},
        ),
    )
    plan = resolve_plan(
        svc.state,
        region_id,
        mesh_name="m",
        elevation_min=0.0,
        elevation_max=1.0,
    )
    expected_scale = 5.0
    assert plan.layers[0].parameters["scale_meters"] == expected_scale
    assert plan.layers[0].parameters["base_color"] == [0.5, 0.5, 0.5, 1.0]


# ---------------------------------------------------------------------------
# resolver — precedence + scope inheritance
# ---------------------------------------------------------------------------


def test_resolve_plan_world_application_inherits_to_region(tmp_path: Path) -> None:
    svc, region_id = _bootstrap(tmp_path)
    world_arch = svc.create_material_archetype(
        "World Base",
        MaterialRecipe.FLAT_COLOR,
        {"color": [0.1, 0.1, 0.1, 1.0]},
    )
    svc.apply_material(
        world_arch.node_id,
        svc.state.metadata.world_node_id,
        attrs=MaterialApplicationAttrs(scope=MaterialScope.WORLD),
    )
    plan = resolve_plan(
        svc.state,
        region_id,
        mesh_name="m",
        elevation_min=0.0,
        elevation_max=1.0,
    )
    assert len(plan.layers) == 1
    assert plan.layers[0].archetype_id == world_arch.node_id


def test_resolve_plan_region_overrides_world_in_render_order(tmp_path: Path) -> None:
    svc, region_id = _bootstrap(tmp_path)
    world_arch = svc.create_material_archetype(
        "World",
        MaterialRecipe.FLAT_COLOR,
        {"color": [0.1, 0.1, 0.1, 1.0]},
    )
    region_arch = svc.create_material_archetype(
        "Region",
        MaterialRecipe.FLAT_COLOR,
        {"color": [0.9, 0.0, 0.0, 1.0]},
    )
    svc.apply_material(
        world_arch.node_id,
        svc.state.metadata.world_node_id,
        attrs=MaterialApplicationAttrs(scope=MaterialScope.WORLD),
    )
    svc.apply_material(
        region_arch.node_id,
        NodeId(str(region_id)),
        attrs=MaterialApplicationAttrs(scope=MaterialScope.REGION),
    )
    plan = resolve_plan(
        svc.state,
        region_id,
        mesh_name="m",
        elevation_min=0.0,
        elevation_max=1.0,
    )
    expected_layer_count = 2
    assert len(plan.layers) == expected_layer_count
    # Most-specific application renders last (top of stack).
    assert plan.layers[0].archetype_id == world_arch.node_id
    assert plan.layers[1].archetype_id == region_arch.node_id


# ---------------------------------------------------------------------------
# resolver — composition
# ---------------------------------------------------------------------------


def test_resolve_plan_extends_flattens_parameters(tmp_path: Path) -> None:
    svc, region_id = _bootstrap(tmp_path)
    base = svc.create_material_archetype(
        "Base",
        MaterialRecipe.TRIPLANAR_ROCK,
        {"base_color": [0.5, 0.5, 0.5, 1.0], "scale_meters": 1.0},
    )
    leaf = svc.create_material_archetype(
        "Leaf",
        MaterialRecipe.TRIPLANAR_ROCK,
        {"base_color": [0.9, 0.1, 0.1, 1.0]},
    )
    svc.compose_material(
        leaf.node_id,
        base.node_id,
        attrs=MaterialCompositionAttrs(mode=MaterialCompositionMode.EXTENDS),
    )
    svc.apply_material(
        leaf.node_id,
        NodeId(str(region_id)),
        attrs=MaterialApplicationAttrs(scope=MaterialScope.REGION),
    )
    plan = resolve_plan(
        svc.state,
        region_id,
        mesh_name="m",
        elevation_min=0.0,
        elevation_max=1.0,
    )
    assert len(plan.layers) == 1
    params = plan.layers[0].parameters
    assert params["base_color"] == [0.9, 0.1, 0.1, 1.0]  # leaf wins
    assert params["scale_meters"] == 1.0  # inherited from base


def test_resolve_plan_composes_prepends_child_layers(tmp_path: Path) -> None:
    svc, region_id = _bootstrap(tmp_path)
    parent = svc.create_material_archetype(
        "Top",
        MaterialRecipe.FLAT_COLOR,
        {"color": [0.9, 0.9, 0.9, 1.0]},
    )
    child = svc.create_material_archetype(
        "Bottom",
        MaterialRecipe.FLAT_COLOR,
        {"color": [0.1, 0.1, 0.1, 1.0]},
    )
    svc.compose_material(
        parent.node_id,
        child.node_id,
        attrs=MaterialCompositionAttrs(
            mode=MaterialCompositionMode.COMPOSES,
            mask=HeightRampMask(low_m=0.0, high_m=1.0),
            weight=0.5,
        ),
    )
    svc.apply_material(
        parent.node_id,
        NodeId(str(region_id)),
        attrs=MaterialApplicationAttrs(scope=MaterialScope.REGION),
    )
    plan = resolve_plan(
        svc.state,
        region_id,
        mesh_name="m",
        elevation_min=0.0,
        elevation_max=1.0,
    )
    expected_layer_count = 2
    assert len(plan.layers) == expected_layer_count
    # Composed child renders below the primary.
    assert plan.layers[0].archetype_id == child.node_id
    expected_weight = 0.5
    assert plan.layers[0].weight == expected_weight
    assert plan.layers[1].archetype_id == parent.node_id


def test_resolver_rejects_multi_parent_extends(tmp_path: Path) -> None:
    svc, region_id = _bootstrap(tmp_path)
    leaf = svc.create_material_archetype("L", MaterialRecipe.FLAT_COLOR, {"color": [0, 0, 0, 1]})
    a = svc.create_material_archetype("A", MaterialRecipe.FLAT_COLOR, {"color": [1, 0, 0, 1]})
    b = svc.create_material_archetype("B", MaterialRecipe.FLAT_COLOR, {"color": [0, 1, 0, 1]})
    svc.compose_material(
        leaf.node_id,
        a.node_id,
        attrs=MaterialCompositionAttrs(mode=MaterialCompositionMode.EXTENDS),
    )
    svc.compose_material(
        leaf.node_id,
        b.node_id,
        attrs=MaterialCompositionAttrs(mode=MaterialCompositionMode.EXTENDS),
    )
    svc.apply_material(
        leaf.node_id,
        NodeId(str(region_id)),
        attrs=MaterialApplicationAttrs(scope=MaterialScope.REGION),
    )
    with pytest.raises(ResolverError, match="extends >1 base"):
        resolve_plan(
            svc.state,
            region_id,
            mesh_name="m",
            elevation_min=0.0,
            elevation_max=1.0,
        )


# ---------------------------------------------------------------------------
# Recipe parameter validators
# ---------------------------------------------------------------------------


def test_validate_recipe_parameters_accepts_default() -> None:
    archetype = default_terrain_archetype(elevation_min=0.0, elevation_max=1.0)
    validate_recipe_parameters(archetype.recipe, archetype.parameters)


def test_validate_recipe_parameters_rejects_missing_keys() -> None:
    with pytest.raises(RecipeParameterError, match="missing required parameters"):
        validate_recipe_parameters(MaterialRecipe.PRINCIPLED_HEIGHT_RAMP, {})


def test_validate_recipe_parameters_rejects_bad_color_ramp() -> None:
    with pytest.raises(RecipeParameterError, match="non-empty list"):
        validate_recipe_parameters(
            MaterialRecipe.PRINCIPLED_HEIGHT_RAMP,
            {
                "color_ramp_stops": [],
                "slope_threshold": 0.5,
                "elevation_min": 0.0,
                "elevation_max": 1.0,
            },
        )


def test_validate_recipe_parameters_flat_color_requires_rgba() -> None:
    with pytest.raises(RecipeParameterError, match="RGBA"):
        validate_recipe_parameters(
            MaterialRecipe.FLAT_COLOR,
            {"color": [0.1, 0.2, 0.3]},
        )


def test_validate_recipe_parameters_triplanar_requires_base_color() -> None:
    with pytest.raises(RecipeParameterError, match="missing required"):
        validate_recipe_parameters(MaterialRecipe.TRIPLANAR_ROCK, {})


def test_validate_recipe_parameters_pbr_layered_minimal_ok() -> None:
    """``pbr_layered`` accepts just ``base_color`` (all knobs optional)."""
    validate_recipe_parameters(
        MaterialRecipe.PBR_LAYERED,
        {"base_color": [0.5, 0.5, 0.5, 1.0]},
    )


def test_validate_recipe_parameters_pbr_layered_rejects_out_of_range_unit() -> None:
    with pytest.raises(RecipeParameterError, match="must be in"):
        validate_recipe_parameters(
            MaterialRecipe.PBR_LAYERED,
            {"base_color": [0.5, 0.5, 0.5, 1.0], "roughness": 1.5},
        )


def test_validate_recipe_parameters_pbr_layered_rejects_nonpositive_scale() -> None:
    with pytest.raises(RecipeParameterError, match="positive"):
        validate_recipe_parameters(
            MaterialRecipe.PBR_LAYERED,
            {"base_color": [0.5, 0.5, 0.5, 1.0], "triplanar_scale_m": 0.0},
        )


def test_validate_recipe_parameters_procedural_snow_accepts_empty() -> None:
    """All ``procedural_snow`` knobs are optional with sensible defaults."""
    validate_recipe_parameters(MaterialRecipe.PROCEDURAL_SNOW, {})


def test_validate_recipe_parameters_procedural_snow_rejects_bad_unit() -> None:
    with pytest.raises(RecipeParameterError, match="must be in"):
        validate_recipe_parameters(
            MaterialRecipe.PROCEDURAL_SNOW,
            {"sparkle_density": 1.5},
        )


def test_validate_recipe_parameters_procedural_sand_accepts_empty() -> None:
    validate_recipe_parameters(MaterialRecipe.PROCEDURAL_SAND, {})


def test_validate_recipe_parameters_procedural_sand_rejects_inverted_wet_band() -> None:
    with pytest.raises(RecipeParameterError, match="low_m < high_m"):
        validate_recipe_parameters(
            MaterialRecipe.PROCEDURAL_SAND,
            {"wet_band": {"low_m": 5.0, "high_m": 1.0, "darken": 0.5}},
        )


def test_validate_recipe_parameters_procedural_sand_rejects_missing_wet_band_key() -> None:
    with pytest.raises(RecipeParameterError, match="missing required key"):
        validate_recipe_parameters(
            MaterialRecipe.PROCEDURAL_SAND,
            {"wet_band": {"low_m": 0.0, "high_m": 1.0}},
        )


def test_validate_recipe_parameters_procedural_water_accepts_empty() -> None:
    validate_recipe_parameters(MaterialRecipe.PROCEDURAL_WATER, {})


def test_validate_recipe_parameters_procedural_water_rejects_nonpositive_ior() -> None:
    with pytest.raises(RecipeParameterError, match="positive"):
        validate_recipe_parameters(MaterialRecipe.PROCEDURAL_WATER, {"ior": 0.0})


def test_resolve_plan_serialises_slope_mask_for_adapter(tmp_path: Path) -> None:
    """SlopeMask must round-trip into the layer's serialised mask dict.

    Phase 6-e Stage A fixed a bug where the adapter silently no-opped
    ``kind == "slope"`` masks. This test guards the contract that the
    resolver still hands the adapter a fully populated slope-mask
    payload (``kind``, ``low``, ``high``, ``softness``) so the new
    :func:`scripts.blender.adapter._build_slope_mask_factor` builder
    receives the expected keys.
    """
    svc, region_id = _bootstrap(tmp_path)
    parent = svc.create_material_archetype(
        "Cliff",
        MaterialRecipe.FLAT_COLOR,
        {"color": [0.5, 0.5, 0.5, 1.0]},
    )
    child = svc.create_material_archetype(
        "Grass",
        MaterialRecipe.FLAT_COLOR,
        {"color": [0.1, 0.5, 0.1, 1.0]},
    )
    svc.compose_material(
        parent.node_id,
        child.node_id,
        attrs=MaterialCompositionAttrs(
            mode=MaterialCompositionMode.COMPOSES,
            mask=SlopeMask(low=0.7, high=0.95, softness=0.05),
            weight=1.0,
        ),
    )
    svc.apply_material(
        parent.node_id,
        NodeId(str(region_id)),
        attrs=MaterialApplicationAttrs(scope=MaterialScope.REGION),
    )
    plan = resolve_plan(
        svc.state,
        region_id,
        mesh_name="m",
        elevation_min=0.0,
        elevation_max=1.0,
    )
    layer_dump = plan.layers[0].model_dump(mode="json")
    assert layer_dump["mask"] == {
        "kind": "slope",
        "low": 0.7,
        "high": 0.95,
        "softness": 0.05,
    }
