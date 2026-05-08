"""Unit tests for the Phase 6-e Stage C biome-targeted procedural recipes.

Covers ``procedural_snow``, ``procedural_sand``, and
``procedural_water`` shader-graph builders. Each test exercises the
sub-graphs that the recipe layers on top of a Principled BSDF — the
sparkle / drift paths for snow, the grain / wet-band / ripple paths
for sand, the IOR / wave-field path for water — and asserts the
recorded node graph wires the right inputs into the right BSDF
sockets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

_FLOAT_EPSILON = 1e-9

if TYPE_CHECKING:
    from types import ModuleType

    from tests.realize.material._bpy_fake import FakeBpy, FakeLinks, FakeNode


def _build(
    adapter: tuple[ModuleType, FakeBpy],
    builder_name: str,
    parameters: dict[str, object],
) -> tuple[ModuleType, FakeNode, list[FakeNode], FakeLinks]:
    module, _ = adapter
    mat = module.bpy.data.materials.new(f"{builder_name}_test")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    builder = getattr(module, builder_name)
    out = builder(nodes, links, parameters)
    bsdfs = nodes.of_type("ShaderNodeBsdfPrincipled")
    assert len(bsdfs) == 1
    bsdf = bsdfs[0]
    assert out is bsdf.outputs["BSDF"]
    return module, bsdf, list(nodes), links


# ---------------------------------------------------------------------------
# procedural_snow
# ---------------------------------------------------------------------------


def test_procedural_snow_defaults_set_subsurface_and_low_roughness(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    _, bsdf, nodes, _ = _build(adapter, "_build_procedural_snow", {})
    expected_roughness = 0.15
    expected_sss = 0.4
    assert bsdf.inputs["Roughness"].default_value == expected_roughness
    assert bsdf.inputs["Subsurface Weight"].default_value == expected_sss
    assert bsdf.inputs["Base Color"].default_value == (0.95, 0.96, 0.98, 1.0)
    # Sparkle path on by default -> Voronoi present.
    voronois = [n for n in nodes if n.bl_idname == "ShaderNodeTexVoronoi"]
    assert len(voronois) == 1
    # Drift path on by default -> Noise + Bump present.
    noises = [n for n in nodes if n.bl_idname == "ShaderNodeTexNoise"]
    bumps = [n for n in nodes if n.bl_idname == "ShaderNodeBump"]
    assert len(noises) == 1
    assert len(bumps) == 1


def test_procedural_snow_sparkle_zero_omits_voronoi(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    _, _, nodes, _ = _build(
        adapter,
        "_build_procedural_snow",
        {"sparkle_density": 0.0, "drift_strength": 0.0},
    )
    assert not [n for n in nodes if n.bl_idname == "ShaderNodeTexVoronoi"]
    assert not [n for n in nodes if n.bl_idname == "ShaderNodeTexNoise"]
    assert not [n for n in nodes if n.bl_idname == "ShaderNodeBump"]


def test_procedural_snow_sparkle_threshold_reflects_density(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    _, _, nodes, _ = _build(
        adapter,
        "_build_procedural_snow",
        {"sparkle_density": 0.7, "drift_strength": 0.0},
    )
    threshold = next(
        n
        for n in nodes
        if n.bl_idname == "ShaderNodeMath" and getattr(n, "operation", None) == "GREATER_THAN"
    )
    expected_threshold = 1.0 - 0.7
    actual = float(threshold.inputs[1].default_value)  # type: ignore[arg-type]
    assert abs(actual - expected_threshold) < _FLOAT_EPSILON


# ---------------------------------------------------------------------------
# procedural_sand
# ---------------------------------------------------------------------------


def test_procedural_sand_defaults_emit_grain_and_ripples(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    _, bsdf, nodes, links = _build(adapter, "_build_procedural_sand", {})
    expected_roughness = 0.85
    assert bsdf.inputs["Roughness"].default_value == expected_roughness
    voronois = [n for n in nodes if n.bl_idname == "ShaderNodeTexVoronoi"]
    expected_voronoi_count = 2
    assert len(voronois) == expected_voronoi_count
    waves = [n for n in nodes if n.bl_idname == "ShaderNodeTexWave"]
    assert len(waves) == 1
    bumps = [n for n in nodes if n.bl_idname == "ShaderNodeBump"]
    assert len(bumps) == 1
    base_color_sources = links.for_input(bsdf, "Base Color")
    assert len(base_color_sources) == 1


def test_procedural_sand_no_grain_uses_constant_base_color(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    _, bsdf, nodes, _ = _build(
        adapter,
        "_build_procedural_sand",
        {"grain_amount": 0.0, "ripple_strength": 0.0},
    )
    assert bsdf.inputs["Base Color"].default_value == (0.78, 0.66, 0.45, 1.0)
    assert not [n for n in nodes if n.bl_idname == "ShaderNodeTexVoronoi"]
    assert not [n for n in nodes if n.bl_idname == "ShaderNodeTexWave"]


def test_procedural_sand_wet_band_adds_smoothstep_and_mix(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    _, bsdf, nodes, links = _build(
        adapter,
        "_build_procedural_sand",
        {
            "grain_amount": 0.0,
            "ripple_strength": 0.0,
            "wet_band": {"low_m": 0.0, "high_m": 2.0, "darken": 0.4},
        },
    )
    smooths = [
        n
        for n in nodes
        if n.bl_idname == "ShaderNodeMath" and getattr(n, "operation", None) == "SMOOTHSTEP"
    ]
    assert len(smooths) == 1
    smooth = smooths[0]
    expected_high = 2.0
    expected_low = 0.0
    assert smooth.inputs[1].default_value == expected_high
    assert smooth.inputs[2].default_value == expected_low
    sep_xyz = [n for n in nodes if n.bl_idname == "ShaderNodeSeparateXYZ"]
    assert len(sep_xyz) == 1
    base_color_sources = links.for_input(bsdf, "Base Color")
    assert len(base_color_sources) == 1


# ---------------------------------------------------------------------------
# procedural_water
# ---------------------------------------------------------------------------


def test_procedural_water_defaults_set_ior_and_transmission(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    _, bsdf, nodes, _ = _build(adapter, "_build_procedural_water", {})
    expected_ior = 1.33
    expected_transmission = 1.0
    assert bsdf.inputs["IOR"].default_value == expected_ior
    assert bsdf.inputs["Transmission Weight"].default_value == expected_transmission
    assert bsdf.inputs["Roughness"].default_value == 0.0
    # Wave path on by default -> Voronoi + Noise + Bump.
    assert [n for n in nodes if n.bl_idname == "ShaderNodeTexVoronoi"]
    assert [n for n in nodes if n.bl_idname == "ShaderNodeTexNoise"]
    assert [n for n in nodes if n.bl_idname == "ShaderNodeBump"]


def test_procedural_water_calm_omits_wave_field(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    _, bsdf, nodes, links = _build(
        adapter,
        "_build_procedural_water",
        {"wave_strength": 0.0},
    )
    assert not [n for n in nodes if n.bl_idname == "ShaderNodeTexVoronoi"]
    assert not [n for n in nodes if n.bl_idname == "ShaderNodeTexNoise"]
    assert not [n for n in nodes if n.bl_idname == "ShaderNodeBump"]
    assert not links.for_input(bsdf, "Normal")


def test_procedural_water_custom_ior_is_applied(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    _, bsdf, _, _ = _build(
        adapter,
        "_build_procedural_water",
        {"ior": 1.5, "transmission": 0.8, "roughness": 0.1, "wave_strength": 0.0},
    )
    expected_ior = 1.5
    expected_transmission = 0.8
    expected_roughness = 0.1
    assert bsdf.inputs["IOR"].default_value == expected_ior
    assert bsdf.inputs["Transmission Weight"].default_value == expected_transmission
    assert bsdf.inputs["Roughness"].default_value == expected_roughness
