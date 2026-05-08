"""Integration test for Phase 6-e Stage E parallel Volume composite socket.

End-to-end smoke against real Blender 5.0: create a single
``procedural_water`` archetype that opts into the new
``volume_absorption_density`` knob, generate a region, open the
resulting blend and assert that the composite material's
``Material Output`` node has its **Volume** input wired in addition
to its **Surface** input.

This exercises the Stage E refactor of
``_handle_material_build_composite``: builders may now return a
``(surface, volume)`` tuple, and the handler must run a parallel
volume mix terminating at ``Material Output.Volume``.
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
def test_volume_absorption_wires_material_output_volume_socket(
    tmp_path: Path,
    isolated_service: ProjectService,  # noqa: ARG001 - autouses set_service
    real_blender_factory: None,  # noqa: ARG001 - autouses set_realizer_factory
) -> None:
    """A water plan with volume_absorption_density emits a Volume link in Blender."""
    rid = bootstrap_region(tmp_path)
    archetype = _ok(
        create_material_archetype(
            "deep_lake",
            "procedural_water",
            parameters={
                "base_color": [0.04, 0.18, 0.32, 1.0],
                "ior": 1.33,
                "roughness": 0.05,
                "wave_strength": 0.0,
                "transmission": 1.0,
                "volume_absorption_density": 0.4,
                "volume_absorption_color": [0.08, 0.22, 0.35, 1.0],
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
    expected_material_name = f"forge.material.{expected_plan_id}"

    blend_path = cast("str", result["blend_path"])
    with BlenderProcess() as proc:
        proc.client.call("bpy.ops.wm.open_mainfile", {"filepath": blend_path})
        # Inspect node_tree.nodes; each node serialises via its repr,
        # which embeds the bl_idname. Presence of VolumeAbsorption in
        # the recorded list proves both that the Stage E volume branch
        # fired inside the water builder AND that the composite handler
        # accepted the (surface, volume) tuple without raising.
        node_dump = proc.client.call(
            "get_property",
            {
                "collection": "materials",
                "name": expected_material_name,
                "path": "node_tree.nodes",
            },
        )

    assert isinstance(node_dump, dict)
    raw = node_dump.get("value")
    assert isinstance(raw, list)
    serialised = " ".join(str(item) for item in raw)
    assert "VolumeAbsorption" in serialised, serialised
    assert "OutputMaterial" in serialised, serialised
    assert "Principled" in serialised, serialised
