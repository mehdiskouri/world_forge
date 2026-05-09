"""Tests for the Phase 6-f environment CRUD + binding surface on :class:`ProjectService`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from forge_mcp.project.schemas import (
    EnvironmentNodeId,
    EnvironmentParameters,
    EnvironmentRecipe,
    NodeId,
    Season,
    WorldBounds,
)
from forge_mcp.project.service import (
    EnvironmentInUseError,
    ProjectService,
    UnknownEnvironmentError,
    UnknownScopeError,
)

if TYPE_CHECKING:
    from pathlib import Path

_WORLD = WorldBounds(min=(-10.0, -10.0), max=(10.0, 10.0))
_SQUARE: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (1.0, 0.0),
    (1.0, 1.0),
    (0.0, 1.0),
)
_NOON_UTC = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)


def _params(**overrides: object) -> EnvironmentParameters:
    """Build a default :class:`EnvironmentParameters` with optional overrides."""
    base: dict[str, object] = {
        "datetime_utc": _NOON_UTC,
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
# Bootstrap / paths
# ---------------------------------------------------------------------------


def test_environments_dir_is_bootstrapped(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    assert svc.state.paths.environments_dir.is_dir()


def test_world_root_persisted_with_environment_id_field(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    assert svc.state.world_root is not None
    assert svc.state.world_root.environment_id is None
    assert svc.state.paths.world_node_path.exists()


# ---------------------------------------------------------------------------
# create_environment
# ---------------------------------------------------------------------------


def test_create_environment_persists_node(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    env = svc.create_environment("Default Sky", EnvironmentRecipe.CLEAR, _params())
    assert env.node_id == EnvironmentNodeId("env_default_sky")
    assert env.recipe is EnvironmentRecipe.CLEAR
    on_disk = svc.state.paths.environment_path(env.node_id)
    assert on_disk.exists()
    body = on_disk.read_text(encoding="utf-8")
    assert '"recipe": "clear"' in body


def test_create_environment_id_collision_appends_suffix(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    a = svc.create_environment("Sky", EnvironmentRecipe.CLEAR, _params())
    b = svc.create_environment("Sky", EnvironmentRecipe.OVERCAST, _params())
    assert a.node_id == EnvironmentNodeId("env_sky")
    assert b.node_id != a.node_id
    assert b.node_id.startswith("env_sky_")


# ---------------------------------------------------------------------------
# update_environment
# ---------------------------------------------------------------------------


def test_update_environment_partial_update(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    env = svc.create_environment("Sky", EnvironmentRecipe.CLEAR, _params())
    updated = svc.update_environment(
        env.node_id,
        recipe=EnvironmentRecipe.SUNSET,
        notes="late afternoon",
    )
    assert updated.recipe is EnvironmentRecipe.SUNSET
    assert updated.notes == "late afternoon"
    assert updated.modified_at >= env.created_at


def test_update_unknown_environment_raises(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    with pytest.raises(UnknownEnvironmentError):
        svc.update_environment(EnvironmentNodeId("env_missing"), notes="x")


# ---------------------------------------------------------------------------
# delete_environment
# ---------------------------------------------------------------------------


def test_delete_environment_clears_disk(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    env = svc.create_environment("Sky", EnvironmentRecipe.CLEAR, _params())
    path = svc.state.paths.environment_path(env.node_id)
    svc.delete_environment(env.node_id)
    assert env.node_id not in svc.state.environments
    assert not path.exists()


def test_delete_bound_environment_raises(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    assert svc.state.world_root is not None
    env = svc.create_environment("Sky", EnvironmentRecipe.CLEAR, _params())
    svc.bind_environment(svc.state.world_root.node_id, env.node_id)
    with pytest.raises(EnvironmentInUseError):
        svc.delete_environment(env.node_id)


# ---------------------------------------------------------------------------
# bind_environment / unbind_environment
# ---------------------------------------------------------------------------


def test_bind_environment_to_world_root(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    assert svc.state.world_root is not None
    env = svc.create_environment("Sky", EnvironmentRecipe.CLEAR, _params())
    svc.bind_environment(svc.state.world_root.node_id, env.node_id)
    assert svc.state.world_root is not None
    assert svc.state.world_root.environment_id == env.node_id
    # Persisted to disk.
    persisted = svc.state.paths.world_node_path.read_text(encoding="utf-8")
    assert env.node_id in persisted


def test_bind_environment_to_region(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("Foothills", _SQUARE)
    env = svc.create_environment("Sky", EnvironmentRecipe.CLEAR, _params())
    svc.bind_environment(NodeId(str(region.node_id)), env.node_id)
    bound = svc.state.regions[region.node_id]
    assert bound.environment_id == env.node_id


def test_bind_environment_unknown_scope_raises(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    env = svc.create_environment("Sky", EnvironmentRecipe.CLEAR, _params())
    with pytest.raises(UnknownScopeError):
        svc.bind_environment(NodeId("region_missing"), env.node_id)


def test_bind_unknown_environment_raises(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    assert svc.state.world_root is not None
    with pytest.raises(UnknownEnvironmentError):
        svc.bind_environment(svc.state.world_root.node_id, EnvironmentNodeId("env_missing"))


def test_unbind_environment_clears_world_root(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    assert svc.state.world_root is not None
    env = svc.create_environment("Sky", EnvironmentRecipe.CLEAR, _params())
    svc.bind_environment(svc.state.world_root.node_id, env.node_id)
    svc.unbind_environment(svc.state.world_root.node_id)
    assert svc.state.world_root is not None
    assert svc.state.world_root.environment_id is None


def test_unbind_environment_clears_region(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("Foothills", _SQUARE)
    env = svc.create_environment("Sky", EnvironmentRecipe.CLEAR, _params())
    svc.bind_environment(NodeId(str(region.node_id)), env.node_id)
    svc.unbind_environment(NodeId(str(region.node_id)))
    assert svc.state.regions[region.node_id].environment_id is None


def test_unbind_environment_no_op_when_unbound(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    assert svc.state.world_root is not None
    # Should not raise.
    svc.unbind_environment(svc.state.world_root.node_id)


# ---------------------------------------------------------------------------
# Round-trip via open_project
# ---------------------------------------------------------------------------


def test_environments_round_trip_through_open_project(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    assert svc.state.world_root is not None
    region = svc.create_region("Foothills", _SQUARE)
    env_default = svc.create_environment(
        "Day",
        EnvironmentRecipe.CLEAR,
        _params(),
    )
    env_override = svc.create_environment(
        "Dusk",
        EnvironmentRecipe.SUNSET,
        _params(),
    )
    svc.bind_environment(svc.state.world_root.node_id, env_default.node_id)
    svc.bind_environment(NodeId(str(region.node_id)), env_override.node_id)

    reopened = ProjectService()
    reopened.open_project(tmp_path)
    assert set(reopened.state.environments) == {env_default.node_id, env_override.node_id}
    assert reopened.state.world_root is not None
    assert reopened.state.world_root.environment_id == env_default.node_id
    assert reopened.state.regions[region.node_id].environment_id == env_override.node_id


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_environment_parameters_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="tzinfo"):
        EnvironmentParameters(
            datetime_utc=datetime(2026, 6, 21, 12, 0, 0),  # noqa: DTZ001 - intentional
            latitude_deg=0.0,
            longitude_deg=0.0,
        )


def test_environment_parameters_rejects_out_of_range_latitude() -> None:
    with pytest.raises(ValueError, match="latitude"):
        EnvironmentParameters(
            datetime_utc=_NOON_UTC,
            latitude_deg=120.0,
            longitude_deg=0.0,
        )


def test_environment_parameters_rejects_bad_rgba() -> None:
    with pytest.raises(ValueError, match="components"):
        EnvironmentParameters(
            sun_color=(2.0, 0.0, 0.0, 1.0),
            datetime_utc=_NOON_UTC,
            latitude_deg=0.0,
            longitude_deg=0.0,
        )
