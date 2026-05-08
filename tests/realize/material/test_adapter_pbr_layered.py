"""Unit tests for the Phase 6-e Stage B ``pbr_layered`` recipe builder."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

    from tests.realize.material._bpy_fake import FakeBpy, FakeNode


def _build(
    adapter: tuple[ModuleType, FakeBpy],
    parameters: dict[str, object],
) -> tuple[ModuleType, FakeNode]:
    module, _ = adapter
    mat = module.bpy.data.materials.new("pbr_test")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    out = module._build_pbr_layered(nodes, links, parameters)  # noqa: SLF001 - direct builder unit
    bsdfs = nodes.of_type("ShaderNodeBsdfPrincipled")
    assert len(bsdfs) == 1
    bsdf = bsdfs[0]
    assert out is bsdf.outputs["BSDF"]
    return module, bsdf


def test_pbr_layered_minimal_params_is_flat_color_equivalent(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    """With only ``base_color`` set, all optional sub-graphs must be omitted."""
    module, bsdf = _build(adapter, {"base_color": [0.2, 0.4, 0.6, 1.0]})
    mat = next(iter(module.bpy.data.materials._items.values()))  # noqa: SLF001 - test introspection
    nodes = list(mat.node_tree.nodes)
    links = list(mat.node_tree.links)
    assert bsdf.inputs["Base Color"].default_value == (0.2, 0.4, 0.6, 1.0)
    expected_roughness = 0.5
    assert bsdf.inputs["Roughness"].default_value == expected_roughness
    assert bsdf.inputs["Metallic"].default_value == 0.0
    assert not [n for n in nodes if n.bl_idname == "ShaderNodeTexVoronoi"]
    assert not [n for n in nodes if n.bl_idname == "ShaderNodeTexNoise"]
    assert not [n for n in nodes if n.bl_idname == "ShaderNodeBump"]
    assert len([n for n in nodes if n.bl_idname == "ShaderNodeNewGeometry"]) == 1
    scale_nodes = [
        n
        for n in nodes
        if n.bl_idname == "ShaderNodeVectorMath" and getattr(n, "operation", None) == "SCALE"
    ]
    assert len(scale_nodes) == 1
    base_color_links = [(s, d) for s, d in links if d.node is bsdf and d.name == "Base Color"]
    assert not base_color_links


def test_pbr_layered_voronoi_variation_wires_mix_to_base_color(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    module, bsdf = _build(
        adapter,
        {
            "base_color": [0.5, 0.5, 0.5, 1.0],
            "base_color_variation": 0.3,
            "triplanar_scale_m": 2.0,
        },
    )
    mat = next(iter(module.bpy.data.materials._items.values()))  # noqa: SLF001
    nodes = list(mat.node_tree.nodes)
    links = list(mat.node_tree.links)
    voronoi = [n for n in nodes if n.bl_idname == "ShaderNodeTexVoronoi"]
    assert len(voronoi) == 1
    mixes = [n for n in nodes if n.bl_idname == "ShaderNodeMixRGB"]
    assert len(mixes) == 1
    mix = mixes[0]
    expected_fac = 0.3
    assert mix.inputs["Fac"].default_value == expected_fac
    assert mix.inputs["Color1"].default_value == (0.5, 0.5, 0.5, 1.0)
    color2_sources = [s for s, d in links if d.node is mix and d.name == "Color2"]
    assert len(color2_sources) == 1
    assert color2_sources[0] is voronoi[0].outputs["Color"]
    base_color_sources = [s for s, d in links if d.node is bsdf and d.name == "Base Color"]
    assert len(base_color_sources) == 1
    assert base_color_sources[0] is mix.outputs["Color"]
    scale_node = next(
        n
        for n in nodes
        if n.bl_idname == "ShaderNodeVectorMath" and getattr(n, "operation", None) == "SCALE"
    )
    expected_scale = 2.0
    assert scale_node.inputs["Scale"].default_value == expected_scale


def test_pbr_layered_roughness_variation_wires_noise_to_roughness(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    module, bsdf = _build(
        adapter,
        {
            "base_color": [0.7, 0.7, 0.7, 1.0],
            "roughness": 0.6,
            "roughness_variation": 0.4,
        },
    )
    mat = next(iter(module.bpy.data.materials._items.values()))  # noqa: SLF001
    nodes = list(mat.node_tree.nodes)
    links = list(mat.node_tree.links)
    noises = [n for n in nodes if n.bl_idname == "ShaderNodeTexNoise"]
    assert len(noises) == 1
    mixes = [n for n in nodes if n.bl_idname == "ShaderNodeMixRGB"]
    assert len(mixes) == 1
    rmix = mixes[0]
    expected_fac = 0.4
    assert rmix.inputs["Fac"].default_value == expected_fac
    assert rmix.inputs["Color1"].default_value == (0.6, 0.6, 0.6, 1.0)
    rough_sources = [s for s, d in links if d.node is bsdf and d.name == "Roughness"]
    assert len(rough_sources) == 1
    assert rough_sources[0] is rmix.outputs["Color"]


def test_pbr_layered_normal_detail_attaches_bump(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    module, bsdf = _build(
        adapter,
        {
            "base_color": [0.3, 0.3, 0.3, 1.0],
            "normal_detail": 0.25,
        },
    )
    mat = next(iter(module.bpy.data.materials._items.values()))  # noqa: SLF001
    nodes = list(mat.node_tree.nodes)
    links = list(mat.node_tree.links)
    bumps = [n for n in nodes if n.bl_idname == "ShaderNodeBump"]
    assert len(bumps) == 1
    bump = bumps[0]
    expected_strength = 0.25
    assert bump.inputs["Strength"].default_value == expected_strength
    normal_sources = [s for s, d in links if d.node is bsdf and d.name == "Normal"]
    assert len(normal_sources) == 1
    assert normal_sources[0] is bump.outputs["Normal"]


def test_pbr_layered_metallic_and_clearcoat_set_default_values(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    """Metallic and Coat Weight get plain defaults, never linked from a node."""
    module, bsdf = _build(
        adapter,
        {
            "base_color": [0.8, 0.8, 0.8, 1.0],
            "metallic": 0.9,
            "clearcoat": 0.5,
        },
    )
    mat = next(iter(module.bpy.data.materials._items.values()))  # noqa: SLF001
    links = list(mat.node_tree.links)
    expected_metallic = 0.9
    expected_clearcoat = 0.5
    assert bsdf.inputs["Metallic"].default_value == expected_metallic
    assert bsdf.inputs["Coat Weight"].default_value == expected_clearcoat
    assert not [(s, d) for s, d in links if d.node is bsdf and d.name == "Metallic"]
    assert not [(s, d) for s, d in links if d.node is bsdf and d.name == "Coat Weight"]
