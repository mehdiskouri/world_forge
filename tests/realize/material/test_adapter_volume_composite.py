"""Unit tests for the Phase 6-e Stage E parallel Volume composite.

Covers:

* the ``_normalize_builder_result`` helper that lets recipe builders
  return either a bare surface socket (the legacy convention used by
  every Stage A/B/C builder) or a ``(surface, volume)`` tuple,
* the ``_handle_material_build_composite`` Volume socket plumbing,
  including byte-identical behaviour when no layer emits a volume,
* the snow / water builder retrofits that opt into a volume shader
  via ``volume_scatter_density`` / ``volume_absorption_density``.

The composite handler is exercised end-to-end against the bpy fake
(rather than only via ``make integration``) so we can assert on the
recorded node-graph topology — specifically, that the
``ShaderNodeOutputMaterial.Volume`` input is wired only when the plan
actually contributes a volume layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

    from tests.realize.material._bpy_fake import FakeBpy, FakeNode

_DENSITY_HALF = 0.5


# ---------------------------------------------------------------------------
# _normalize_builder_result
# ---------------------------------------------------------------------------


def test_normalize_bare_socket_returns_none_volume(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    module, _ = adapter
    sentinel = object()
    surface, volume = module._normalize_builder_result(sentinel)  # noqa: SLF001
    assert surface is sentinel
    assert volume is None


def test_normalize_tuple_returns_both(adapter: tuple[ModuleType, FakeBpy]) -> None:
    module, _ = adapter
    s, v = object(), object()
    surface, volume = module._normalize_builder_result((s, v))  # noqa: SLF001
    assert surface is s
    assert volume is v


def test_normalize_rejects_wrong_arity(adapter: tuple[ModuleType, FakeBpy]) -> None:
    module, _ = adapter
    with pytest.raises(ValueError, match="length 3"):
        module._normalize_builder_result((1, 2, 3))  # noqa: SLF001


# ---------------------------------------------------------------------------
# Composite handler — Volume socket only when a layer emits one.
# ---------------------------------------------------------------------------


def _output_node(material_name: str, fake: FakeBpy) -> FakeNode:
    mat = fake.data.materials.get(material_name)
    assert mat is not None, material_name
    outs = [n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial"]
    assert len(outs) == 1
    return outs[0]


def test_composite_surface_only_leaves_volume_unconnected(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    """Plans with no volume contributions wire Surface only — byte-identical pre-Stage-E."""
    module, fake = adapter
    fake.data.objects.add("terrain_x")
    plan = {
        "plan_id": "stage_e_unit_surface_only",
        "layers": [
            {
                "recipe": "flat_color",
                "parameters": {"color": [0.5, 0.5, 0.5, 1.0]},
            },
        ],
    }
    result = module._handle_material_build_composite(  # noqa: SLF001
        {"target_object": "terrain_x", "plan": plan},
    )
    output = _output_node(result["material_name"], fake)
    mat = fake.data.materials.get(result["material_name"])
    assert mat is not None
    surface_links = mat.node_tree.links.for_input(output, "Surface")
    volume_links = mat.node_tree.links.for_input(output, "Volume")
    assert len(surface_links) == 1
    assert len(volume_links) == 0


def test_composite_single_volume_layer_links_volume(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    module, fake = adapter
    fake.data.objects.add("terrain_x")
    plan = {
        "plan_id": "stage_e_unit_single_volume",
        "layers": [
            {
                "recipe": "procedural_snow",
                "parameters": {
                    "sparkle_density": 0.0,
                    "drift_strength": 0.0,
                    "volume_scatter_density": 0.4,
                },
            },
        ],
    }
    result = module._handle_material_build_composite(  # noqa: SLF001
        {"target_object": "terrain_x", "plan": plan},
    )
    output = _output_node(result["material_name"], fake)
    mat = fake.data.materials.get(result["material_name"])
    assert mat is not None
    assert len(mat.node_tree.links.for_input(output, "Surface")) == 1
    assert len(mat.node_tree.links.for_input(output, "Volume")) == 1
    scatters = [n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeVolumeScatter"]
    assert len(scatters) == 1


def test_composite_two_volume_layers_mix_via_mixshader(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    """Two layers with volumes: composite_volume runs through a MixShader."""
    module, fake = adapter
    fake.data.objects.add("terrain_x")
    plan = {
        "plan_id": "stage_e_unit_two_volumes",
        "layers": [
            {
                "recipe": "procedural_snow",
                "parameters": {
                    "sparkle_density": 0.0,
                    "drift_strength": 0.0,
                    "volume_scatter_density": 0.4,
                },
            },
            {
                "recipe": "procedural_water",
                "parameters": {
                    "wave_strength": 0.0,
                    "volume_absorption_density": 0.2,
                },
                "weight": 0.5,
            },
        ],
    }
    result = module._handle_material_build_composite(  # noqa: SLF001
        {"target_object": "terrain_x", "plan": plan},
    )
    mat = fake.data.materials.get(result["material_name"])
    assert mat is not None
    output = _output_node(result["material_name"], fake)
    # Volume must be linked, and via a MixShader (not a direct shader output).
    volume_sources = mat.node_tree.links.for_input(output, "Volume")
    assert len(volume_sources) == 1
    assert volume_sources[0].node.bl_idname == "ShaderNodeMixShader"
    # Surface mix is independent of volume mix; both MixShaders present.
    mixers = [n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeMixShader"]
    expected_mixers = 2
    assert len(mixers) == expected_mixers


def test_composite_surface_volume_mixed_layer_only_volume_skips_volume_mix(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    """First layer surface-only, second emits volume → composite_volume = volume directly.

    No MixShader on the Volume side because there's nothing to mix against;
    forcing one would scope the volume to ``fac < 1`` everywhere, defeating
    the layer's intent of contributing a volume.
    """
    module, fake = adapter
    fake.data.objects.add("terrain_x")
    plan = {
        "plan_id": "stage_e_unit_mixed_layer",
        "layers": [
            {
                "recipe": "flat_color",
                "parameters": {"color": [0.2, 0.2, 0.2, 1.0]},
            },
            {
                "recipe": "procedural_water",
                "parameters": {
                    "wave_strength": 0.0,
                    "volume_absorption_density": 0.3,
                },
                "weight": 0.7,
            },
        ],
    }
    result = module._handle_material_build_composite(  # noqa: SLF001
        {"target_object": "terrain_x", "plan": plan},
    )
    mat = fake.data.materials.get(result["material_name"])
    assert mat is not None
    output = _output_node(result["material_name"], fake)
    volume_sources = mat.node_tree.links.for_input(output, "Volume")
    assert len(volume_sources) == 1
    assert volume_sources[0].node.bl_idname == "ShaderNodeVolumeAbsorption"


# ---------------------------------------------------------------------------
# Builder-level retrofits.
# ---------------------------------------------------------------------------


def test_procedural_snow_volume_density_returns_tuple(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    module, _ = adapter
    mat = module.bpy.data.materials.new("snow_vol")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    result = module._build_procedural_snow(  # noqa: SLF001
        nodes,
        links,
        {"sparkle_density": 0.0, "drift_strength": 0.0, "volume_scatter_density": 0.5},
    )
    assert isinstance(result, tuple)
    expected_arity = 2
    assert len(result) == expected_arity
    surface, volume = result
    bsdfs = nodes.of_type("ShaderNodeBsdfPrincipled")
    scatters = nodes.of_type("ShaderNodeVolumeScatter")
    assert len(bsdfs) == 1
    assert len(scatters) == 1
    assert surface is bsdfs[0].outputs["BSDF"]
    assert volume is scatters[0].outputs["Volume"]
    assert scatters[0].inputs["Density"].default_value == _DENSITY_HALF


def test_procedural_snow_no_volume_density_returns_socket(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    module, _ = adapter
    mat = module.bpy.data.materials.new("snow_no_vol")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    result = module._build_procedural_snow(nodes, links, {})  # noqa: SLF001
    assert not isinstance(result, tuple)
    assert not nodes.of_type("ShaderNodeVolumeScatter")


def test_procedural_water_volume_absorption_uses_color_override(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    module, _ = adapter
    mat = module.bpy.data.materials.new("water_vol")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    result = module._build_procedural_water(  # noqa: SLF001
        nodes,
        links,
        {
            "wave_strength": 0.0,
            "volume_absorption_density": 0.7,
            "volume_absorption_color": [0.1, 0.4, 0.6, 1.0],
        },
    )
    assert isinstance(result, tuple)
    absorbs = nodes.of_type("ShaderNodeVolumeAbsorption")
    assert len(absorbs) == 1
    expected_density = 0.7
    assert absorbs[0].inputs["Density"].default_value == expected_density
    assert absorbs[0].inputs["Color"].default_value == (0.1, 0.4, 0.6, 1.0)


def test_procedural_water_no_volume_density_returns_socket(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    module, _ = adapter
    mat = module.bpy.data.materials.new("water_no_vol")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    result = module._build_procedural_water(  # noqa: SLF001
        nodes,
        links,
        {"wave_strength": 0.0},
    )
    assert not isinstance(result, tuple)
    assert not nodes.of_type("ShaderNodeVolumeAbsorption")
