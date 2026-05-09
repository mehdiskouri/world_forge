"""Tests for :mod:`forge_mcp.realize.environment` (Phase 6-f Stage C)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from forge_mcp.project.schemas import (
    EnvironmentParameters,
    EnvironmentRecipe,
    NodeId,
    Season,
    WorldBounds,
)
from forge_mcp.project.service import ProjectService
from forge_mcp.realize.environment import (
    EnvironmentParameterError,
    EnvironmentResolverError,
    ResolvedEnvironment,
    compute_environment_plan_id,
    default_environment_node,
    resolve_environment,
    resolve_for_node,
    validate_environment_parameters,
)
from forge_mcp.realize.environment.defaults import (
    _VALIDATORS,
    DEFAULT_ENVIRONMENT_ID,
)

if TYPE_CHECKING:
    from pathlib import Path

_WORLD = WorldBounds(min=(-10.0, -10.0), max=(10.0, 10.0))
_SQUARE = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
_NOON = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)


def _params(**overrides: object) -> EnvironmentParameters:
    base: dict[str, object] = {
        "datetime_utc": _NOON,
        "latitude_deg": 51.5,
        "longitude_deg": -0.1,
        "season": Season.SUMMER,
    }
    base.update(overrides)
    return EnvironmentParameters(**base)  # type: ignore[arg-type]


def _bootstrap(tmp_path: Path) -> ProjectService:
    svc = ProjectService()
    svc.create_project(tmp_path, "Eldoria", _WORLD)
    return svc


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def test_validators_exhaustive_over_recipes() -> None:
    """Every :class:`EnvironmentRecipe` value has a matching validator."""
    assert set(_VALIDATORS) == set(EnvironmentRecipe)


def test_night_validator_rejects_daylight_intensity() -> None:
    params = _params(sun_intensity_w_m2=1000.0)
    with pytest.raises(EnvironmentParameterError, match="night"):
        validate_environment_parameters(EnvironmentRecipe.NIGHT, params)


def test_clear_validator_accepts_default_params() -> None:
    validate_environment_parameters(EnvironmentRecipe.CLEAR, _params())


# ---------------------------------------------------------------------------
# Default environment
# ---------------------------------------------------------------------------


def test_default_environment_node_has_stable_id() -> None:
    node = default_environment_node()
    assert node.node_id == DEFAULT_ENVIRONMENT_ID
    assert node.recipe is EnvironmentRecipe.CLEAR


# ---------------------------------------------------------------------------
# Resolver fallback chain
# ---------------------------------------------------------------------------


def test_resolver_falls_back_to_synthetic_default(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    plan = resolve_environment(svc.state)
    assert plan.scope_label == "default"
    assert plan.source_environment_id is None
    assert plan.recipe is EnvironmentRecipe.CLEAR


def test_resolver_uses_world_root_default(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    assert svc.state.world_root is not None
    env = svc.create_environment("Day", EnvironmentRecipe.OVERCAST, _params())
    svc.bind_environment(svc.state.world_root.node_id, env.node_id)
    plan = resolve_environment(svc.state)
    assert plan.scope_label == "world_root"
    assert plan.recipe is EnvironmentRecipe.OVERCAST
    assert plan.source_environment_id == str(env.node_id)


def test_resolver_region_override_beats_world_default(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    assert svc.state.world_root is not None
    region = svc.create_region("Foothills", _SQUARE)
    world_env = svc.create_environment("Day", EnvironmentRecipe.CLEAR, _params())
    region_env = svc.create_environment("Dusk", EnvironmentRecipe.SUNSET, _params())
    svc.bind_environment(svc.state.world_root.node_id, world_env.node_id)
    svc.bind_environment(NodeId(str(region.node_id)), region_env.node_id)
    plan = resolve_environment(svc.state, region.node_id)
    assert plan.scope_label == f"region:{region.node_id}"
    assert plan.recipe is EnvironmentRecipe.SUNSET
    assert plan.source_environment_id == str(region_env.node_id)


def test_resolver_inherits_world_when_region_unbound(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    assert svc.state.world_root is not None
    region = svc.create_region("Foothills", _SQUARE)
    world_env = svc.create_environment("Day", EnvironmentRecipe.CLEAR, _params())
    svc.bind_environment(svc.state.world_root.node_id, world_env.node_id)
    plan = resolve_environment(svc.state, region.node_id)
    assert plan.scope_label == "world_root"


def test_resolver_unknown_region_raises(tmp_path: Path) -> None:
    from forge_mcp.project.schemas import RegionId  # noqa: PLC0415 - local import

    svc = _bootstrap(tmp_path)
    with pytest.raises(EnvironmentResolverError, match="region"):
        resolve_environment(svc.state, RegionId("region_missing"))


def test_resolver_dangling_world_binding_raises(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    assert svc.state.world_root is not None
    env = svc.create_environment("Day", EnvironmentRecipe.CLEAR, _params())
    svc.bind_environment(svc.state.world_root.node_id, env.node_id)
    # Mutate state directly to simulate a dangling binding.
    svc.state.environments.pop(env.node_id)
    with pytest.raises(EnvironmentResolverError, match="missing"):
        resolve_environment(svc.state)


# ---------------------------------------------------------------------------
# Solar position propagation
# ---------------------------------------------------------------------------


def test_resolver_propagates_sun_direction(tmp_path: Path) -> None:
    """London noon summer solstice: elevation ~62 deg, azimuth ~180 deg."""
    svc = _bootstrap(tmp_path)
    assert svc.state.world_root is not None
    env = svc.create_environment("Day", EnvironmentRecipe.CLEAR, _params())
    svc.bind_environment(svc.state.world_root.node_id, env.node_id)
    plan = resolve_environment(svc.state)
    assert 60.0 < plan.sun_elevation_deg < 64.0  # noqa: PLR2004
    assert 175.0 < plan.sun_azimuth_deg < 185.0  # noqa: PLR2004
    # Vector is unit-length.
    length_sq = sum(c * c for c in plan.sun_vector)
    assert abs(length_sq - 1.0) < 1e-9  # noqa: PLR2004


# ---------------------------------------------------------------------------
# Plan id determinism + content addressing
# ---------------------------------------------------------------------------


def test_plan_id_is_deterministic(tmp_path: Path) -> None:
    svc1 = _bootstrap(tmp_path / "a")
    svc2 = _bootstrap(tmp_path / "b")
    a = resolve_environment(svc1.state)
    b = resolve_environment(svc2.state)
    assert a.plan_id == b.plan_id


def test_plan_id_changes_with_recipe(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    assert svc.state.world_root is not None
    env_clear = svc.create_environment("Day", EnvironmentRecipe.CLEAR, _params())
    svc.bind_environment(svc.state.world_root.node_id, env_clear.node_id)
    plan_clear = resolve_environment(svc.state)
    env_overcast = svc.create_environment("Cloudy", EnvironmentRecipe.OVERCAST, _params())
    svc.bind_environment(svc.state.world_root.node_id, env_overcast.node_id)
    plan_overcast = resolve_environment(svc.state)
    assert plan_clear.plan_id != plan_overcast.plan_id


def test_plan_id_excludes_scope_label() -> None:
    """Two scopes with byte-equal effective env hash to the same plan id."""
    payload = {
        "recipe": "clear",
        "sun_color": [1.0, 1.0, 1.0, 1.0],
        "sun_intensity_w_m2": 1000.0,
        "sun_azimuth_deg": 180.0,
        "sun_elevation_deg": 60.0,
        "sun_vector": [0.0, -0.5, 0.866],
        "sky_zenith_color": [0.1, 0.3, 0.65, 1.0],
        "sky_horizon_color": [0.55, 0.7, 0.85, 1.0],
        "ambient_color": [1.0, 1.0, 1.0, 1.0],
        "ambient_strength": 1.0,
        "fog_color": [0.8, 0.85, 0.9, 1.0],
        "fog_density": 0.0,
        "fog_height_falloff_m": 200.0,
        "season": "summer",
        "datetime_utc": "2026-06-21T12:00:00+00:00",
        "latitude_deg": 51.5,
        "longitude_deg": -0.1,
    }
    a = compute_environment_plan_id(payload)
    b = compute_environment_plan_id({**payload, "scope_label": "world_root"})
    assert a == b
    assert a.startswith("eplan_")


# ---------------------------------------------------------------------------
# resolve_for_node convenience
# ---------------------------------------------------------------------------


def test_resolve_for_node_routes_world_root(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    assert svc.state.world_root is not None
    plan = resolve_for_node(svc.state, svc.state.world_root.node_id)
    assert plan.scope_label == "default"


def test_resolve_for_node_routes_region(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("Foothills", _SQUARE)
    plan = resolve_for_node(svc.state, NodeId(str(region.node_id)))
    # No binding; falls back to default via region->world->default chain.
    assert plan.scope_label == "default"


# ---------------------------------------------------------------------------
# ResolvedEnvironment shape
# ---------------------------------------------------------------------------


def test_resolved_environment_is_frozen(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    plan = resolve_environment(svc.state)
    assert isinstance(plan, ResolvedEnvironment)
    with pytest.raises(ValueError, match="frozen"):
        plan.sun_intensity_w_m2 = 0.0
