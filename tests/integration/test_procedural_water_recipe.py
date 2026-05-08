"""Integration test for the Phase 6-e Stage C ``procedural_water`` recipe.

End-to-end smoke against a real Blender 5.0 host: create a single
``procedural_water`` archetype, apply it to a region, generate, and
assert the rendered material slot carries the expected
plan-id-derived name. ``procedural_water`` is the most distinctive of
the three Stage C recipes (transmission-dominant + IOR), so it
exercises socket lookups that the snow / sand recipes do not touch.
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
def test_procedural_water_recipe_renders_with_plan_id_slot(
    tmp_path: Path,
    isolated_service: ProjectService,  # noqa: ARG001 - autouses set_service
    real_blender_factory: None,  # noqa: ARG001 - autouses set_realizer_factory
) -> None:
    """A procedural_water-only plan reaches Blender as one slot named after the plan id."""
    rid = bootstrap_region(tmp_path)
    archetype = _ok(
        create_material_archetype(
            "lake_surface",
            "procedural_water",
            parameters={
                "base_color": [0.04, 0.18, 0.32, 1.0],
                "ior": 1.33,
                "roughness": 0.05,
                "wave_strength": 0.18,
                "wave_scale_m": 1.5,
                "transmission": 1.0,
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
    assert cast("dict[str, object]", layers[0])["recipe"] == "procedural_water"

    expected_material_name = f"forge.material.{expected_plan_id}"
    blend_path = cast("str", result["blend_path"])
    with BlenderProcess() as proc:
        proc.client.call("bpy.ops.wm.open_mainfile", {"filepath": blend_path})
        slot_list = proc.client.call(
            "get_property",
            {"collection": "objects", "name": object_name, "path": "data.materials"},
        )
        scene_counts = proc.client.call("scene.diff")

    assert isinstance(slot_list, dict)
    slot_value = slot_list.get("value")
    assert isinstance(slot_value, list)
    assert len(slot_value) == 1, slot_value
    slot_repr = slot_value[0]
    assert isinstance(slot_repr, str)
    assert expected_material_name in slot_repr, slot_repr
    assert isinstance(scene_counts, dict)
    assert scene_counts.get("materials") == 1
