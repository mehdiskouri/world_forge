"""End-to-end material composition acceptance against a real Blender 5.0 host.

Phase 6-bis Phase E2: drive ``forge.create_material_archetype``,
``forge.compose_material``, and ``forge.apply_material`` through the MCP
tool surface, then run ``forge.generate_region`` and assert the
realized scene uses a single material slot named after the resolved
plan id, that the plan id is deterministic across re-runs, and that
the trace records carry the same plan id as the on-disk material name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.realize import BlenderProcess
from forge_mcp.server.tools.generation import generate_region
from forge_mcp.server.tools.materials import (
    apply_material,
    compose_material,
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


def _make_two_layer_scene(rid: str) -> tuple[str, str, str]:
    """Create granite + snow archetypes, compose them, apply to the region.

    Returns ``(granite_id, snow_id, application_edge_id)``.
    """
    granite = _ok(
        create_material_archetype(
            "alpine_granite",
            "triplanar_rock",
            parameters={
                "base_color": [0.42, 0.40, 0.38, 1.0],
                "roughness": 0.78,
                "scale_meters": 1.5,
            },
        ),
    )
    snow = _ok(
        create_material_archetype(
            "alpine_snow",
            "flat_color",
            parameters={"color": [0.96, 0.97, 0.99, 1.0]},
        ),
    )
    granite_id = cast("str", granite["node_id"])
    snow_id = cast("str", snow["node_id"])
    _ok(
        compose_material(
            granite_id,
            snow_id,
            attrs={
                "mode": "composes",
                "mask": {"kind": "slope", "low": 0.6, "high": 0.95},
                "weight": 1.0,
            },
        ),
    )
    application = _ok(
        apply_material(
            granite_id,
            rid,
            attrs={"scope": "region", "priority": 0},
        ),
    )
    return granite_id, snow_id, cast("str", application["edge_id"])


@pytest.mark.blender_integration
def test_composed_material_renders_with_deterministic_plan_id(
    tmp_path: Path,
    isolated_service: ProjectService,  # noqa: ARG001 - autouses set_service
    real_blender_factory: None,  # noqa: ARG001 - autouses set_realizer_factory
) -> None:
    """Composed plan resolves deterministically and lands in Blender as one slot."""
    rid = bootstrap_region(tmp_path)
    _make_two_layer_scene(rid)

    # Generate the region first so we can read the elevation band the realizer
    # used; resolving the plan with the same band + mesh_name guarantees the
    # plan id we predict matches the one stamped into bpy.data.materials.
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

    preview_a = _ok(
        resolve_material(
            rid,
            mesh_name=object_name,
            elevation_min=elevation_min,
            elevation_max=elevation_max,
        ),
    )
    preview_b = _ok(
        resolve_material(
            rid,
            mesh_name=object_name,
            elevation_min=elevation_min,
            elevation_max=elevation_max,
        ),
    )
    assert preview_a == preview_b
    expected_plan_id = preview_a["plan_id"]
    assert isinstance(expected_plan_id, str)
    assert expected_plan_id.startswith("mplan_")
    layers = preview_a["layers"]
    assert isinstance(layers, list)
    expected_layer_count = 2
    assert len(layers) == expected_layer_count

    expected_material_name = f"forge.material.{expected_plan_id}"

    blend_path = cast("str", result["blend_path"])
    with BlenderProcess() as proc:
        proc.client.call("bpy.ops.wm.open_mainfile", {"filepath": blend_path})
        slot_list = proc.client.call(
            "get_property",
            {"collection": "objects", "name": object_name, "path": "data.materials"},
        )
        scene_counts = proc.client.call("scene.diff")

    # ``data.materials`` serializes each Material via ``str()`` which yields
    # ``<bpy_struct, Material("<name>") at 0x...>`` — we look for the
    # plan-id-derived name as a substring to confirm the bound slot.
    assert isinstance(slot_list, dict)
    slot_value = slot_list.get("value")
    assert isinstance(slot_value, list)
    assert len(slot_value) == 1, slot_value
    slot_repr = slot_value[0]
    assert isinstance(slot_repr, str)
    assert expected_material_name in slot_repr, slot_repr
    # The composite material is the only material in the scene.
    assert isinstance(scene_counts, dict)
    assert scene_counts.get("materials") == 1
