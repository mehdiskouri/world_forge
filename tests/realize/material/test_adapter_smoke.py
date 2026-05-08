"""Smoke tests for the bpy fake module and adapter loader.

These tests validate the test scaffolding itself: the
:class:`tests.realize.material._bpy_fake.FakeBpy` module is a
sufficient stand-in for ``bpy`` such that
``scripts/blender/adapter.py`` imports under it and the existing
recipe builders construct sane node graphs.

If these break, every other adapter unit test in this folder breaks
too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

    from tests.realize.material._bpy_fake import FakeBpy


def test_fake_bpy_imports_adapter_module(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    """The adapter must import cleanly under the fake."""
    module, _ = adapter
    builders = module._RECIPE_BUILDERS  # noqa: SLF001 - test inspects adapter registry
    assert "flat_color" in builders
    assert "principled_height_ramp" in builders
    assert "triplanar_rock" in builders


def test_flat_color_builder_records_principled_bsdf(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    """``_build_flat_color`` should add a single Principled BSDF and set Base Color."""
    module, _ = adapter
    tree = module.bpy.data.materials.new("test_mat")
    tree.use_nodes = True
    nodes = tree.node_tree.nodes
    links = tree.node_tree.links
    out = module._build_flat_color(  # noqa: SLF001 - direct adapter builder unit test
        nodes,
        links,
        {"color": [0.25, 0.5, 0.75, 1.0]},
    )
    bsdfs = nodes.of_type("ShaderNodeBsdfPrincipled")
    assert len(bsdfs) == 1
    bsdf = bsdfs[0]
    assert bsdf.inputs["Base Color"].default_value == (0.25, 0.5, 0.75, 1.0)
    assert out is bsdf.outputs["BSDF"]


def test_slope_mask_builder_wires_normal_to_smoothstep(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    """Stage A ``_build_slope_mask_factor`` must wire Normal->Z->Abs->Smoothstep->Mul."""
    module, _ = adapter
    tree = module.bpy.data.materials.new("slope_mat")
    tree.use_nodes = True
    nodes = tree.node_tree.nodes
    links = tree.node_tree.links
    module._build_slope_mask_factor(nodes, links, 0.7, 0.95, 0.05, 1.0)  # noqa: SLF001
    smooths = [
        n for n in nodes.of_type("ShaderNodeMath") if getattr(n, "operation", None) == "SMOOTHSTEP"
    ]
    assert len(smooths) == 1
    smooth = smooths[0]
    expected_low = 0.7 - 0.05
    expected_high = 0.95 + 0.05
    assert smooth.inputs[1].default_value == expected_low
    assert smooth.inputs[2].default_value == expected_high
    sep = nodes.of_type("ShaderNodeSeparateXYZ")
    assert len(sep) == 1
    abs_nodes = [
        n for n in nodes.of_type("ShaderNodeMath") if getattr(n, "operation", None) == "ABSOLUTE"
    ]
    assert len(abs_nodes) == 1
    geom = nodes.of_type("ShaderNodeNewGeometry")
    assert len(geom) == 1
    sep_vec_sources = links.for_input(sep[0], "Vector")
    assert len(sep_vec_sources) == 1
    assert sep_vec_sources[0] is geom[0].outputs["Normal"]
