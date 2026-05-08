"""Integration test for the Phase 6-e Stage D ``procedural_grass`` recipe.

End-to-end smoke against a real Blender 5.0 host: create a single
``procedural_grass`` archetype, apply it to a region, generate, open
the resulting ``.blend``, and assert the terrain object carries the
``forge.instancer.<plan_id>.0`` Geometry-Nodes modifier produced by
the Stage D adapter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.realize import BlenderProcess
from forge_mcp.server.tools.generation import generate_region
from forge_mcp.server.tools.materials import (
    apply_material,
    create_material_archetype,
    resolve_material,
)

from tests.integration.conftest import bootstrap_region

if TYPE_CHECKING:
    from pathlib import Path

    from forge_mcp.project.service import ProjectService


def _ok(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is True, envelope
    return cast("dict[str, object]", envelope["result"])


@pytest.mark.blender_integration
def test_procedural_grass_attaches_geometry_nodes_modifier(
    tmp_path: Path,
    isolated_service: ProjectService,  # noqa: ARG001 - autouses set_service
    real_blender_factory: None,  # noqa: ARG001 - autouses set_realizer_factory
) -> None:
    """A procedural_grass-only plan attaches a NODES modifier on the terrain object."""
    rid = bootstrap_region(tmp_path)
    archetype = _ok(
        create_material_archetype(
            "meadow",
            "procedural_grass",
            parameters={
                "density_per_m2": 5.0,
                "blade_height_m": 0.20,
                "blade_color": [0.18, 0.55, 0.18, 1.0],
                "slope_max_cos": 0.7,
                "rotation_jitter_deg": 180.0,
                "scale_jitter": 0.3,
                "translucency": 0.4,
                "seed": 7,
            },
        ),
    )
    archetype_id = cast("str", archetype["node_id"])
    _ok(apply_material(archetype_id, rid, attrs={"scope": "region", "priority": 0}))

    result = _ok(generate_region(rid))
    object_name = f"terrain_{rid}"
    realization = result["realization"]
    assert isinstance(realization, dict)
    band = realization.get("elevation_band")
    assert isinstance(band, list)
    elevation_band_pair = 2
    assert len(band) == elevation_band_pair
    elevation_min = float(cast("float", band[0]))
    elevation_max = float(cast("float", band[1]))

    preview = _ok(
        resolve_material(
            rid,
            mesh_name=object_name,
            elevation_min=elevation_min,
            elevation_max=elevation_max,
        ),
    )
    expected_plan_id = preview["plan_id"]
    assert isinstance(expected_plan_id, str)
    assert expected_plan_id.startswith("mplan_")
    layers = preview["layers"]
    assert isinstance(layers, list)
    assert len(layers) == 1
    layer = cast("dict[str, object]", layers[0])
    assert layer["recipe"] == "procedural_grass"
    instancer = layer.get("instancer")
    assert isinstance(instancer, dict), layer
    assert instancer.get("kind") == "geometry_nodes"

    expected_modifier_name = f"forge.instancer.{expected_plan_id}.0"
    expected_group_name = f"forge.geom.grass.{expected_plan_id}.0"
    blend_path = cast("str", result["blend_path"])
    with BlenderProcess() as proc:
        proc.client.call("bpy.ops.wm.open_mainfile", {"filepath": blend_path})
        modifier_names = proc.client.call(
            "get_property",
            {"collection": "objects", "name": object_name, "path": "modifiers"},
        )
        node_group_names = proc.client.call(
            "get_property",
            {"collection": "node_groups", "name": expected_group_name, "path": "name"},
        )

    assert isinstance(modifier_names, dict)
    mod_value = modifier_names.get("value")
    assert isinstance(mod_value, list)
    joined = " | ".join(repr(item) for item in mod_value)
    assert expected_modifier_name in joined, joined
    assert isinstance(node_group_names, dict)
    assert node_group_names.get("value") == expected_group_name, node_group_names
