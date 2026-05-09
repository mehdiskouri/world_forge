"""End-to-end tests for the environment MCP tools (Phase 6-f Stage F)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.project.service import ProjectService
from forge_mcp.server.tools import set_service
from forge_mcp.server.tools.environments import (
    bind_environment,
    create_environment,
    delete_environment,
    get_environment,
    list_environments,
    resolve_environment_tool,
    unbind_environment,
    update_environment,
)
from forge_mcp.server.tools.projects import create_project
from forge_mcp.server.tools.regions import create_region

if TYPE_CHECKING:
    from pathlib import Path

_BOUNDS: dict[str, object] = {"min": [-10.0, -10.0], "max": [10.0, 10.0]}
_SQUARE = [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]
_NOON_UTC: str = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC).isoformat()
_DEFAULT_PARAMS: dict[str, object] = {
    "datetime_utc": _NOON_UTC,
    "latitude_deg": 51.5,
    "longitude_deg": -0.1,
    "season": "summer",
}


@pytest.fixture(autouse=True)
def _isolated_service() -> None:
    set_service(ProjectService())


def _ok(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    assert isinstance(result, dict)
    return result


def _err(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is False, envelope
    error = envelope["error"]
    assert isinstance(error, dict)
    return error


def _bootstrap(tmp_path: Path) -> str:
    _ok(create_project(str(tmp_path), "E World", _BOUNDS))
    region = _ok(create_region("R", _SQUARE))
    return cast("str", region["node_id"])


def _create_default_env(name: str = "London Summer Noon") -> str:
    rec = _ok(create_environment(name, "clear", dict(_DEFAULT_PARAMS)))
    return cast("str", rec["node_id"])


# ---------------------------------------------------------------------------
# CRUD envelopes
# ---------------------------------------------------------------------------


def test_create_environment_returns_record(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    rec = _ok(create_environment("E", "clear", dict(_DEFAULT_PARAMS), tags=["sky"]))
    assert rec["recipe"] == "clear"
    assert rec["name"] == "E"
    assert rec["tags"] == ["sky"]


def test_create_environment_no_open_project() -> None:
    err = _err(create_environment("E", "clear", dict(_DEFAULT_PARAMS)))
    assert err["code"] == "no_open_project"


def test_create_environment_invalid_recipe(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(create_environment("E", "hdri", dict(_DEFAULT_PARAMS)))
    assert err["code"] == "invalid_recipe"


def test_create_environment_invalid_parameters(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(create_environment("E", "clear", "not-a-dict"))
    assert err["code"] == "invalid_parameters"


def test_create_environment_missing_parameters(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(create_environment("E", "clear", parameters=None))
    assert err["code"] == "invalid_parameters"


def test_create_environment_invalid_tags(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(create_environment("E", "clear", dict(_DEFAULT_PARAMS), tags="notalist"))
    assert err["code"] == "invalid_tags"


def test_update_environment_partial(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    env_id = _create_default_env()
    updated = _ok(update_environment(env_id, name="Renamed", recipe="overcast"))
    assert updated["name"] == "Renamed"
    assert updated["recipe"] == "overcast"


def test_update_environment_unknown(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(update_environment("env_missing", name="X"))
    assert err["code"] == "unknown_environment"


def test_update_environment_invalid_recipe(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    env_id = _create_default_env()
    err = _err(update_environment(env_id, recipe="hdri"))
    assert err["code"] == "invalid_recipe"


def test_delete_environment_unbound(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    env_id = _create_default_env()
    res = _ok(delete_environment(env_id))
    assert res == {"deleted": env_id}


def test_delete_environment_in_use(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    env_id = _create_default_env()
    _ok(bind_environment(region_id, env_id))
    err = _err(delete_environment(env_id))
    assert err["code"] == "environment_in_use"


def test_delete_environment_unknown(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(delete_environment("env_missing"))
    assert err["code"] == "unknown_environment"


def test_list_environments_orders_by_id(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    a = _create_default_env("A")
    b = _create_default_env("B")
    res = _ok(list_environments())
    envs = res["environments"]
    assert isinstance(envs, list)
    ids: list[str] = [cast("str", cast("dict[str, object]", e)["environment_id"]) for e in envs]
    assert sorted(ids) == ids
    assert {a, b}.issubset(ids)


def test_get_environment_returns_full_record(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    env_id = _create_default_env()
    rec = _ok(get_environment(env_id))
    assert rec["node_id"] == env_id


def test_get_environment_unknown(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(get_environment("env_missing"))
    assert err["code"] == "unknown_environment"


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------


def test_bind_unbind_environment_to_region(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    env_id = _create_default_env()
    bound = _ok(bind_environment(region_id, env_id))
    assert bound == {"scope_node_id": region_id, "environment_id": env_id}
    cleared = _ok(unbind_environment(region_id))
    assert cleared == {"scope_node_id": region_id}


def test_bind_environment_unknown_scope(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    env_id = _create_default_env()
    err = _err(bind_environment("region_does_not_exist", env_id))
    assert err["code"] == "unknown_scope"


def test_bind_environment_unknown_environment(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    err = _err(bind_environment(region_id, "env_missing"))
    assert err["code"] == "unknown_environment"


def test_unbind_environment_unknown_scope(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(unbind_environment("region_does_not_exist"))
    assert err["code"] == "unknown_scope"


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_resolve_environment_default_payload(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    res = _ok(resolve_environment_tool())
    assert res["recipe"] == "clear"
    plan_id = res["plan_id"]
    assert isinstance(plan_id, str)
    assert plan_id.startswith("eplan_")
    assert res["scope_label"] == "default"


def test_resolve_environment_with_region_binding(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    env_id = _create_default_env()
    _ok(bind_environment(region_id, env_id))
    res = _ok(resolve_environment_tool(region_id))
    assert res["scope_label"] == f"region:{region_id}"
    assert res["source_environment_id"] == env_id


def test_resolve_environment_unknown_region(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(resolve_environment_tool("region_does_not_exist"))
    assert err["code"] == "resolver_error"


def test_resolve_environment_no_open_project() -> None:
    err = _err(resolve_environment_tool())
    assert err["code"] == "no_open_project"
