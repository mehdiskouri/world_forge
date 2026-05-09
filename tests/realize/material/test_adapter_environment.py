"""Adapter unit tests for ``world.build_environment`` (Phase 6-f Stage E).

The handler builds a content-addressed ``forge.world.<plan_id>`` shader
graph plus a ``forge.sun.<plan_id>`` SUN lamp from a flat
:class:`ResolvedEnvironment` payload (passed as a JSON dict). These
tests exercise the handler under the in-process bpy fake -- pixel-level
correctness is gated by ``make integration`` against real Blender.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from types import ModuleType

    from tests.realize.material._bpy_fake import FakeBpy, FakeLight, FakeWorld


_BASE_PLAN: dict[str, object] = {
    "recipe": "clear",
    "sun_color": [1.0, 0.95, 0.9, 1.0],
    "sun_intensity_w_m2": 1100.0,
    "sun_azimuth_deg": 180.0,
    "sun_elevation_deg": 60.0,
    "sun_vector": [0.0, -0.5, 0.8660254037844387],
    "sky_zenith_color": [0.05, 0.18, 0.45, 1.0],
    "sky_horizon_color": [0.7, 0.82, 0.92, 1.0],
    "ambient_color": [0.5, 0.6, 0.7, 1.0],
    "ambient_strength": 1.0,
    "fog_color": [0.7, 0.8, 0.9, 1.0],
    "fog_density": 0.0,
    "fog_height_falloff_m": 200.0,
}
_EXPECTED_SUN_INTENSITY: float = 1100.0
_EXPECTED_SUN_COLOR: tuple[float, float, float] = (1.0, 0.95, 0.9)
_EXPECTED_SUN_VECTOR: tuple[float, float, float] = (0.0, -0.5, 0.8660254037844387)


def _call(
    adapter: tuple[ModuleType, FakeBpy],
    plan: dict[str, object],
    plan_id: str,
) -> dict[str, object]:
    module, _ = adapter
    out: object = module._handle_world_build_environment(  # noqa: SLF001 - direct handler test
        {"plan": plan, "plan_id": plan_id},
    )
    return cast("dict[str, object]", out)


def _world(fake: FakeBpy, plan_id: str) -> FakeWorld:
    world = fake.data.worlds.get(f"forge.world.{plan_id}")
    assert world is not None
    return world


def _sun_light(fake: FakeBpy, plan_id: str) -> FakeLight:
    light = fake.data.lights.get(f"forge.sun.{plan_id}")
    assert light is not None
    return light


def test_build_environment_creates_world_and_sun(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    _, fake = adapter
    result = _call(adapter, dict(_BASE_PLAN), "eplan_aaaa000001")
    assert result == {
        "world_name": "forge.world.eplan_aaaa000001",
        "sun_name": "forge.sun.eplan_aaaa000001",
        "plan_id": "eplan_aaaa000001",
    }
    assert "forge.world.eplan_aaaa000001" in fake.data.worlds
    assert "forge.sun.eplan_aaaa000001" in fake.data.objects
    assert "forge.sun.eplan_aaaa000001" in fake.data.lights
    assert fake.context.scene.world is _world(fake, "eplan_aaaa000001")
    light = _sun_light(fake, "eplan_aaaa000001")
    assert light.type == "SUN"
    assert light.energy == _EXPECTED_SUN_INTENSITY
    assert light.color == _EXPECTED_SUN_COLOR


def test_build_environment_is_idempotent_on_plan_id(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    _, fake = adapter
    _call(adapter, dict(_BASE_PLAN), "eplan_idem000001")
    world_first = _world(fake, "eplan_idem000001")
    nodes_before = len(world_first.node_tree.nodes)
    _call(adapter, dict(_BASE_PLAN), "eplan_idem000001")
    world_second = _world(fake, "eplan_idem000001")
    assert world_first is world_second
    assert len(world_second.node_tree.nodes) == nodes_before
    assert len(fake.data.worlds) == 1
    assert len(fake.data.lights) == 1


def test_build_environment_gradient_wires_zenith_and_horizon(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    _, fake = adapter
    _call(adapter, dict(_BASE_PLAN), "eplan_grad00001a")
    world = _world(fake, "eplan_grad00001a")
    nodes = world.node_tree.nodes
    assert nodes.of_type("ShaderNodeBackground"), "missing Background shader"
    assert nodes.of_type("ShaderNodeOutputWorld"), "missing World output"
    ramps = nodes.of_type("ShaderNodeValToRGB")
    assert len(ramps) == 1
    ramp = ramps[0]
    assert ramp.color_ramp.elements[0].color == (0.7, 0.82, 0.92, 1.0)
    assert ramp.color_ramp.elements[1].color == (0.05, 0.18, 0.45, 1.0)


def test_build_environment_fog_adds_volume_scatter(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    _, fake = adapter
    plan = dict(_BASE_PLAN)
    plan["fog_density"] = 0.05
    _call(adapter, plan, "eplan_fog0000001")
    world = _world(fake, "eplan_fog0000001")
    scatter = world.node_tree.nodes.of_type("ShaderNodeVolumeScatter")
    assert len(scatter) == 1
    output = world.node_tree.nodes.of_type("ShaderNodeOutputWorld")[0]
    volume_links = world.node_tree.links.for_input(output, "Volume")
    assert len(volume_links) == 1


def test_build_environment_zero_fog_skips_volume(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    _, fake = adapter
    _call(adapter, dict(_BASE_PLAN), "eplan_dry0000001")
    world = _world(fake, "eplan_dry0000001")
    assert not world.node_tree.nodes.of_type("ShaderNodeVolumeScatter")
    output = world.node_tree.nodes.of_type("ShaderNodeOutputWorld")[0]
    assert not world.node_tree.links.for_input(output, "Volume")


def test_build_environment_procedural_sky_uses_nishita(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    _, fake = adapter
    plan = dict(_BASE_PLAN)
    plan["recipe"] = "procedural_sky"
    _call(adapter, plan, "eplan_sky0000001")
    world = _world(fake, "eplan_sky0000001")
    skies = world.node_tree.nodes.of_type("ShaderNodeTexSky")
    assert len(skies) == 1
    sky = skies[0]
    assert sky.bl_idname == "ShaderNodeTexSky"
    # ``sky_type``/``sun_direction`` are dynamic attributes the fake
    # accepts permissively; ``getattr`` keeps mypy out of bpy's
    # untyped node-attribute API while still asserting the values.
    assert cast("object", getattr(sky, "sky_type")) == "NISHITA"  # noqa: B009 - dynamic attr
    assert cast("object", getattr(sky, "sun_direction")) == _EXPECTED_SUN_VECTOR  # noqa: B009


def test_build_environment_rejects_unknown_recipe(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    plan = dict(_BASE_PLAN)
    plan["recipe"] = "hdri"
    with pytest.raises(ValueError, match="unknown environment recipe"):
        _call(adapter, plan, "eplan_bad0000001")


def test_build_environment_rejects_bad_plan_id(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    with pytest.raises(ValueError, match="plan_id"):
        _call(adapter, dict(_BASE_PLAN), "not-a-plan-id")


def test_build_environment_rejects_missing_plan(
    adapter: tuple[ModuleType, FakeBpy],
) -> None:
    module, _ = adapter
    with pytest.raises(ValueError, match="'plan' object"):
        module._handle_world_build_environment({"plan_id": "eplan_aaaaaaaaaa"})  # noqa: SLF001


def test_sun_rotation_zenith(adapter: tuple[ModuleType, FakeBpy]) -> None:
    """Sun directly overhead -> -Z aimed straight down -> rx == 0."""
    module, _ = adapter
    rx, ry, rz = module._sun_rotation_euler((0.0, 0.0, 1.0))  # noqa: SLF001
    assert ry == 0.0
    assert rz == 0.0
    assert math.isclose(rx, 0.0, abs_tol=1e-9)


def test_sun_rotation_north_horizon(adapter: tuple[ModuleType, FakeBpy]) -> None:
    """Sun on the northern horizon -> tilt -90deg about East, zero azimuth."""
    module, _ = adapter
    rx, ry, rz = module._sun_rotation_euler((0.0, 1.0, 0.0))  # noqa: SLF001
    assert ry == 0.0
    assert math.isclose(rz, 0.0, abs_tol=1e-9)
    assert math.isclose(rx, -math.pi / 2.0, abs_tol=1e-9)


def test_sun_rotation_east_horizon(adapter: tuple[ModuleType, FakeBpy]) -> None:
    """Sun on the eastern horizon -> azimuth = +pi/2 (clockwise from North)."""
    module, _ = adapter
    rx, _, rz = module._sun_rotation_euler((1.0, 0.0, 0.0))  # noqa: SLF001
    assert math.isclose(rz, math.pi / 2.0, abs_tol=1e-9)
    assert math.isclose(rx, -math.pi / 2.0, abs_tol=1e-9)
