"""End-to-end tests for the material MCP tools (Phase 6-bis Phase C)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.project.service import ProjectService
from forge_mcp.server.tools import set_service
from forge_mcp.server.tools.materials import (
    apply_material,
    compose_material,
    create_material_archetype,
    delete_material_archetype,
    get_material_archetype,
    list_material_applications,
    list_material_archetypes,
    resolve_material,
    unapply_material,
    uncompose_material,
    update_material_archetype,
)
from forge_mcp.server.tools.projects import create_project
from forge_mcp.server.tools.regions import create_region

if TYPE_CHECKING:
    from pathlib import Path

_BOUNDS: dict[str, object] = {"min": [-10.0, -10.0], "max": [10.0, 10.0]}
_SQUARE = [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]


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
    _ok(create_project(str(tmp_path), "M World", _BOUNDS))
    region = _ok(create_region("R", _SQUARE))
    return cast("str", region["node_id"])


# ---------------------------------------------------------------------------
# Archetype CRUD envelopes
# ---------------------------------------------------------------------------


def test_create_archetype_returns_record(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    rec = _ok(
        create_material_archetype(
            "Granite",
            "triplanar_rock",
            {"base_color": [0.4, 0.4, 0.45, 1.0]},
            tags=["rock"],
        ),
    )
    assert rec["recipe"] == "triplanar_rock"
    assert rec["name"] == "Granite"


def test_create_archetype_no_open_project() -> None:
    err = _err(create_material_archetype("X", "flat_color", {"color": [1, 1, 1, 1]}))
    assert err["code"] == "no_open_project"


def test_create_archetype_invalid_recipe(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(create_material_archetype("X", "nope", {}))
    assert err["code"] == "invalid_recipe"


def test_create_archetype_invalid_parameters(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(create_material_archetype("X", "flat_color", parameters="not-a-dict"))
    assert err["code"] == "invalid_parameters"


def test_update_archetype_unknown(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(update_material_archetype("missing_id", name="X"))
    assert err["code"] == "unknown_archetype"


def test_delete_archetype_in_use(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    arch = _ok(create_material_archetype("M", "flat_color", {"color": [0, 0, 0, 1]}))
    _ok(
        apply_material(
            cast("str", arch["node_id"]),
            region_id,
            attrs={"scope": "region"},
        ),
    )
    err = _err(delete_material_archetype(cast("str", arch["node_id"])))
    assert err["code"] == "archetype_in_use"


def test_list_and_get_archetype(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    arch = _ok(create_material_archetype("Z", "flat_color", {"color": [1, 1, 1, 1]}))
    listed = _ok(list_material_archetypes())
    items = listed["archetypes"]
    assert isinstance(items, list)
    assert len(items) == 1
    fetched = _ok(get_material_archetype(cast("str", arch["node_id"])))
    assert fetched["name"] == "Z"


def test_get_archetype_unknown(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(get_material_archetype("missing"))
    assert err["code"] == "unknown_archetype"


# ---------------------------------------------------------------------------
# Application + composition envelopes
# ---------------------------------------------------------------------------


def test_apply_material_unknown_archetype(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    err = _err(apply_material("missing", region_id, attrs={"scope": "region"}))
    assert err["code"] == "unknown_archetype"


def test_apply_material_invalid_attrs(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    arch = _ok(create_material_archetype("M", "flat_color", {"color": [0, 0, 0, 1]}))
    err = _err(apply_material(cast("str", arch["node_id"]), region_id, attrs=None))
    assert err["code"] == "invalid_attrs"


def test_apply_unapply_round_trip(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    arch = _ok(create_material_archetype("M", "flat_color", {"color": [0, 0, 0, 1]}))
    edge = _ok(
        apply_material(
            cast("str", arch["node_id"]),
            region_id,
            attrs={"scope": "region", "priority": 5},
        ),
    )
    listed = _ok(list_material_applications())
    apps = listed["applications"]
    assert isinstance(apps, list)
    assert len(apps) == 1
    _ok(unapply_material(cast("str", edge["edge_id"])))
    err = _err(unapply_material(cast("str", edge["edge_id"])))
    assert err["code"] == "unknown_material_edge"


def test_compose_cycle_detection(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    a = _ok(create_material_archetype("A", "flat_color", {"color": [1, 0, 0, 1]}))
    b = _ok(create_material_archetype("B", "flat_color", {"color": [0, 1, 0, 1]}))
    _ok(
        compose_material(
            cast("str", a["node_id"]),
            cast("str", b["node_id"]),
            attrs={"mode": "extends"},
        ),
    )
    err = _err(
        compose_material(
            cast("str", b["node_id"]),
            cast("str", a["node_id"]),
            attrs={"mode": "extends"},
        ),
    )
    assert err["code"] == "composition_cycle"


def test_compose_self_loop(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    a = _ok(create_material_archetype("A", "flat_color", {"color": [1, 0, 0, 1]}))
    err = _err(
        compose_material(
            cast("str", a["node_id"]),
            cast("str", a["node_id"]),
            attrs={"mode": "extends"},
        ),
    )
    assert err["code"] == "invalid_edge"


def test_uncompose_unknown(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    err = _err(uncompose_material("missing"))
    assert err["code"] == "unknown_material_edge"


# ---------------------------------------------------------------------------
# Resolve preview
# ---------------------------------------------------------------------------


def test_resolve_material_default_fallback(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    plan = _ok(
        resolve_material(region_id, mesh_name="terrain_R", elevation_min=0.0, elevation_max=10.0),
    )
    layers = plan["layers"]
    assert isinstance(layers, list)
    assert len(layers) == 1


def test_resolve_material_with_application(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    arch = _ok(create_material_archetype("M", "flat_color", {"color": [1, 1, 1, 1]}))
    _ok(
        apply_material(
            cast("str", arch["node_id"]),
            region_id,
            attrs={"scope": "region"},
        ),
    )
    plan = _ok(
        resolve_material(region_id, mesh_name="m", elevation_min=0.0, elevation_max=1.0),
    )
    layers = plan["layers"]
    assert isinstance(layers, list)
    assert len(layers) == 1
    layer = layers[0]
    assert isinstance(layer, dict)
    assert layer["recipe"] == "flat_color"


def test_resolve_material_no_open_project() -> None:
    err = _err(resolve_material("region_x"))
    assert err["code"] == "no_open_project"
