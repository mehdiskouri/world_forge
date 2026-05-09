"""End-to-end environment acceptance against a real Blender 5.0 host.

Phase 6-f Stage G: drive ``forge.create_environment``, ``forge.bind_environment``,
and ``forge.generate_region`` through the MCP tool surface, then assert the
realized scene contains the content-addressed world + sun lamp produced by the
environment adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.realize import BlenderProcess
from forge_mcp.server.tools.environments import (
    bind_environment,
    create_environment,
    resolve_environment_tool,
)
from forge_mcp.server.tools.generation import generate_region

from tests.integration.conftest import bootstrap_region

if TYPE_CHECKING:
    from pathlib import Path

    from forge_mcp.project.service import ProjectService


_NOON_UTC: str = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC).isoformat()
_DEFAULT_PARAMS: dict[str, object] = {
    "datetime_utc": _NOON_UTC,
    "latitude_deg": 51.5,
    "longitude_deg": -0.1,
    "season": "summer",
}


def _ok(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is True, envelope
    return cast("dict[str, object]", envelope["result"])


@pytest.mark.blender_integration
def test_environment_plan_realizes_world_and_sun_in_blender(
    tmp_path: Path,
    isolated_service: ProjectService,  # noqa: ARG001 - autouses set_service
    real_blender_factory: None,  # noqa: ARG001 - autouses set_realizer_factory
) -> None:
    """Bound environment lands as forge.world.<plan_id> + forge.sun.<plan_id>."""
    rid = bootstrap_region(tmp_path)
    env = _ok(
        create_environment(
            "London Summer Noon",
            "clear",
            dict(_DEFAULT_PARAMS),
        ),
    )
    env_id = cast("str", env["node_id"])
    _ok(bind_environment(rid, env_id))

    # Resolve to discover the deterministic plan id we expect to see in Blender.
    resolved = _ok(resolve_environment_tool(rid))
    plan_id = cast("str", resolved["plan_id"])
    assert plan_id.startswith("eplan_")

    expected_world = f"forge.world.{plan_id}"
    expected_sun = f"forge.sun.{plan_id}"

    result = _ok(generate_region(rid))
    blend_path = cast("str", result["blend_path"])

    with BlenderProcess() as proc:
        proc.client.call("bpy.ops.wm.open_mainfile", {"filepath": blend_path})
        # KeyError is raised by get_property if the named datablock is absent;
        # the call succeeding confirms presence under the expected name.
        world_probe = proc.client.call(
            "get_property",
            {"collection": "worlds", "name": expected_world, "path": "name"},
        )
        sun_probe = proc.client.call(
            "get_property",
            {"collection": "lights", "name": expected_sun, "path": "type"},
        )
        scene_world = proc.client.call(
            "get_property",
            {"collection": "scenes", "name": "Scene", "path": "world.name"},
        )

    assert isinstance(world_probe, dict)
    assert world_probe.get("value") == expected_world
    assert isinstance(sun_probe, dict)
    assert sun_probe.get("value") == "SUN"
    assert isinstance(scene_world, dict)
    assert scene_world.get("value") == expected_world
