"""End-to-end tests for the v1 MCP tool surface.

The tools are pure functions over a process-wide
:class:`ProjectService` singleton; we replace the singleton in a fixture
so tests don't bleed state between cases. Timestamps are frozen with
:mod:`freezegun` so history payloads are deterministic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.project.service import ProjectService
from forge_mcp.server.tools import set_service
from forge_mcp.server.tools.history import history as history_tool
from forge_mcp.server.tools.history import undo as undo_tool
from forge_mcp.server.tools.hypergraph import inspect_boundary, list_boundaries, query_layer
from forge_mcp.server.tools.inspection import list_locks
from forge_mcp.server.tools.projects import (
    close_project,
    create_project,
    open_project,
    save_project,
)
from forge_mcp.server.tools.regions import (
    create_region,
    delete_region,
    get_region,
    list_regions,
    update_region,
)
from forge_mcp.server.tools.schema import get_descriptor_schema
from freezegun import freeze_time

if TYPE_CHECKING:
    from pathlib import Path


_FROZEN = "2024-01-01T12:00:00+00:00"

_BOUNDS: dict[str, object] = {
    "min": [0.0, 0.0],
    "max": [10.0, 10.0],
}

_SQUARE_A = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
_SQUARE_B = [[1.0, 0.0], [2.0, 0.0], [2.0, 1.0], [1.0, 1.0]]  # shares edge with A
_SQUARE_FAR = [[5.0, 5.0], [6.0, 5.0], [6.0, 6.0], [5.0, 6.0]]
_OVERLAP_A = [[0.5, 0.5], [1.5, 0.5], [1.5, 1.5], [0.5, 1.5]]


@pytest.fixture(autouse=True)
def _isolated_service() -> None:
    """Replace the module-level singleton with a fresh service per test."""
    set_service(ProjectService())


def _ok(envelope: dict[str, object]) -> dict[str, object]:
    """Assert the envelope is a success and return its ``result`` payload."""
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    assert isinstance(result, dict)
    return result


def _err(envelope: dict[str, object]) -> dict[str, object]:
    """Assert the envelope is a failure and return its ``error`` payload."""
    assert envelope["ok"] is False, envelope
    error = envelope["error"]
    assert isinstance(error, dict)
    return error


def _list(envelope: dict[str, object], key: str) -> list[object]:
    """Pull ``key`` out of an OK envelope's result and assert it's a list."""
    items = _ok(envelope)[key]
    assert isinstance(items, list)
    return items


# ---------------------------------------------------------------------------
# Project lifecycle
# ---------------------------------------------------------------------------


@freeze_time(_FROZEN)
def test_create_open_save_close_round_trip(tmp_path: Path) -> None:
    created = _ok(create_project(str(tmp_path), "Demo World", _BOUNDS))
    assert created["name"] == "Demo World"
    _ok(save_project())
    _ok(close_project())
    reopened = _ok(open_project(str(tmp_path)))
    assert reopened["name"] == "Demo World"


def test_open_project_unknown_path_returns_structured_error(tmp_path: Path) -> None:
    error = _err(open_project(str(tmp_path / "missing")))
    assert error["code"] == "project_not_found"


def test_create_project_rejects_invalid_bounds(tmp_path: Path) -> None:
    error = _err(
        create_project(
            str(tmp_path),
            "Bad",
            cast("dict[str, object]", {"min": [1.0, 1.0], "max": [0.0, 0.0]}),
        ),
    )
    assert error["code"] == "invalid_world_bounds"


def test_save_and_close_without_open_project_return_structured_error() -> None:
    assert _err(save_project())["code"] == "no_open_project"
    assert _err(close_project())["code"] == "no_open_project"


# ---------------------------------------------------------------------------
# Region CRUD
# ---------------------------------------------------------------------------


def _bootstrap(tmp_path: Path) -> None:
    _ok(create_project(str(tmp_path), "Demo", _BOUNDS))


@freeze_time(_FROZEN)
def test_create_region_persists_and_emits_history(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    region = _ok(create_region("Alpha", _SQUARE_A))
    assert region["name"] == "Alpha"
    node_id = region["node_id"]
    assert isinstance(node_id, str)
    assert node_id.startswith("region_alpha")
    listing = _ok(list_regions())
    regions = listing["regions"]
    assert isinstance(regions, list)
    assert len(regions) == 1
    events = _ok(history_tool())["events"]
    assert isinstance(events, list)
    kinds = [cast("dict[str, object]", e)["kind"] for e in events]
    assert "create_project" in kinds
    assert "create_region" in kinds


def test_create_region_rejects_overlap(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    _ok(create_region("Alpha", _SQUARE_A))
    error = _err(create_region("Beta", _OVERLAP_A))
    assert error["code"] == "region_overlap"


def test_create_region_rejects_invalid_polygon(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    error = _err(create_region("Bad", "not-a-polygon"))
    assert error["code"] == "invalid_polygon_coords"


def test_create_region_rejects_degenerate_polygon(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    error = _err(create_region("Bad", [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]))
    assert error["code"] == "invalid_polygon"


def test_create_region_emits_adjacency_boundary(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    _ok(create_region("Alpha", _SQUARE_A))
    _ok(create_region("Beta", _SQUARE_B))
    boundaries = _list(list_boundaries(), "boundaries")
    assert len(boundaries) == 1


def test_update_region_with_polygon_recomputes_adjacency(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    alpha = _ok(create_region("Alpha", _SQUARE_A))
    _ok(create_region("Beta", _SQUARE_B))
    assert len(_list(list_boundaries(), "boundaries")) == 1
    # Move Alpha far away so the shared edge disappears.
    _ok(update_region(str(alpha["node_id"]), polygon_coords=_SQUARE_FAR))
    assert len(_list(list_boundaries(), "boundaries")) == 0


def test_update_region_unknown_id_returns_structured_error(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    error = _err(update_region("region_missing", name="X"))
    assert error["code"] == "unknown_region"


def test_update_region_with_no_open_project_returns_structured_error() -> None:
    error = _err(update_region("region_x", name="Y"))
    assert error["code"] == "no_open_project"


def test_delete_region_removes_record_and_boundaries(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    a = _ok(create_region("Alpha", _SQUARE_A))
    _ok(create_region("Beta", _SQUARE_B))
    assert len(_list(list_boundaries(), "boundaries")) == 1
    _ok(delete_region(str(a["node_id"])))
    assert len(_list(list_regions(), "regions")) == 1
    assert _ok(list_boundaries())["boundaries"] == []


def test_delete_region_unknown_id_returns_structured_error(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    assert _err(delete_region("region_missing"))["code"] == "unknown_region"


def test_get_region_returns_full_record_or_structured_error(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    region = _ok(create_region("Alpha", _SQUARE_A))
    fetched = _ok(get_region(str(region["node_id"])))
    assert fetched["node_id"] == region["node_id"]
    assert _err(get_region("region_missing"))["code"] == "unknown_region"


def test_create_region_with_invalid_descriptor_returns_structured_error(
    tmp_path: Path,
) -> None:
    _bootstrap(tmp_path)
    error = _err(
        create_region(
            "Alpha",
            _SQUARE_A,
            structured_descriptor=cast("object", {"unknown_field": True}),
        ),
    )
    assert error["code"] == "invalid_descriptor"


# ---------------------------------------------------------------------------
# Schema / hypergraph / history / locks
# ---------------------------------------------------------------------------


def test_get_descriptor_schema_returns_real_schema() -> None:
    schema = _ok(get_descriptor_schema())
    assert "properties" in schema or "$schema" in schema


def test_query_layer_with_no_open_project_returns_structured_error() -> None:
    assert _err(query_layer("spatial_containment"))["code"] == "no_open_project"


def test_query_layer_unknown_layer_returns_structured_error(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    assert _err(query_layer("nope"))["code"] == "unknown_layer"


def test_inspect_boundary_unknown_id_returns_structured_error(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    assert _err(inspect_boundary("boundary_missing"))["code"] == "unknown_boundary"


def test_history_with_no_open_project_returns_structured_error() -> None:
    assert _err(history_tool())["code"] == "no_open_project"


def test_undo_returns_not_implemented_envelope() -> None:
    error = _err(undo_tool())
    assert error["code"] == "not_implemented"
    details = error["details"]
    assert isinstance(details, dict)
    assert details["available_in_phase"] == 7  # noqa: PLR2004 - documented Phase-7 marker


def test_list_locks_with_no_open_project_returns_structured_error() -> None:
    assert _err(list_locks())["code"] == "no_open_project"


def test_list_locks_returns_empty_for_fresh_project(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    assert _ok(list_locks())["locks"] == []


# ---------------------------------------------------------------------------
# Coverage fillers — error branches the happy paths above don't reach
# ---------------------------------------------------------------------------


def test_create_project_rejects_existing_path(tmp_path: Path) -> None:
    _ok(create_project(str(tmp_path), "Demo", _BOUNDS))
    # Must drop the open project before a second create_project on the same
    # path can even attempt to clobber the metadata file.
    _ok(close_project())
    error = _err(create_project(str(tmp_path), "Demo Two", _BOUNDS))
    assert error["code"] == "project_already_exists"


def test_create_project_rejects_blank_name(tmp_path: Path) -> None:
    error = _err(create_project(str(tmp_path), "   ", _BOUNDS))
    assert error["code"] == "project_error"


def test_open_project_translates_format_error(tmp_path: Path) -> None:
    _ok(create_project(str(tmp_path), "Demo", _BOUNDS))
    _ok(close_project())
    (tmp_path / "project.json").write_text("not json", encoding="utf-8")
    error = _err(open_project(str(tmp_path)))
    assert error["code"] == "project_format_error"


def test_open_project_translates_version_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ok(create_project(str(tmp_path), "Demo", _BOUNDS))
    _ok(close_project())
    set_service(ProjectService())
    # Pretend this Forge speaks a newer schema than what is on disk.
    from forge_mcp.descriptor import schema as descriptor_schema  # noqa: PLC0415
    from forge_mcp.project import service as service_module  # noqa: PLC0415

    monkeypatch.setattr(descriptor_schema, "SCHEMA_VERSION", "999.0.0")
    monkeypatch.setattr(service_module, "DESCRIPTOR_SCHEMA_VERSION", "999.0.0")
    error = _err(open_project(str(tmp_path)))
    assert error["code"] == "project_version_mismatch"


def test_create_region_with_invalid_pair_in_polygon(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    error = _err(create_region("Bad", [[0.0, 0.0], [1.0]]))
    assert error["code"] == "invalid_polygon_coords"


def test_update_region_invalid_polygon_and_descriptor(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    region = _ok(create_region("Alpha", _SQUARE_A))
    rid = str(region["node_id"])
    assert _err(update_region(rid, polygon_coords="nope"))["code"] == "invalid_polygon_coords"
    assert (
        _err(update_region(rid, polygon_coords=[[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]))["code"]
        == "invalid_polygon"
    )
    assert (
        _err(update_region(rid, structured_descriptor=cast("object", {"bad": True})))["code"]
        == "invalid_descriptor"
    )


def test_update_region_overlap_returns_structured_error(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    _ok(create_region("Alpha", _SQUARE_A))
    other = _ok(create_region("Beta", _SQUARE_FAR))
    error = _err(update_region(str(other["node_id"]), polygon_coords=_OVERLAP_A))
    assert error["code"] == "region_overlap"


def test_no_open_project_branches_for_region_inspection_tools() -> None:
    assert _err(delete_region("region_x"))["code"] == "no_open_project"
    assert _err(list_regions())["code"] == "no_open_project"
    assert _err(get_region("region_x"))["code"] == "no_open_project"
    assert _err(create_region("X", _SQUARE_A))["code"] == "no_open_project"
    assert _err(list_boundaries())["code"] == "no_open_project"
    assert _err(inspect_boundary("b"))["code"] == "no_open_project"


def test_query_layer_with_root_returns_subtree(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    _ok(create_region("Alpha", _SQUARE_A))
    result = _ok(query_layer("spatial_containment", root_node="world_root"))
    nodes = result["nodes"]
    assert isinstance(nodes, list)
    assert "world_root" in nodes


def test_inspect_boundary_returns_record(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    _ok(create_region("Alpha", _SQUARE_A))
    _ok(create_region("Beta", _SQUARE_B))
    boundaries = _list(list_boundaries(), "boundaries")
    first = cast("dict[str, object]", boundaries[0])
    boundary = _ok(inspect_boundary(str(first["boundary_id"])))
    assert boundary["boundary_id"] == first["boundary_id"]


def test_history_with_limit_caps_results(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    _ok(create_region("Alpha", _SQUARE_A))
    _ok(create_region("Beta", _SQUARE_FAR))
    capped = _list(history_tool(limit=1), "events")
    assert len(capped) == 1


def test_history_translates_history_error(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    # Drop a stray file under history/ that does not match the
    # expected ``NNNN_kind.json`` regex; HistoryLog.iter_events
    # raises HistoryError, which the tool envelope must surface.
    history_dir = tmp_path / "history"
    (history_dir / "garbage.json").write_text("{}", encoding="utf-8")
    error = _err(history_tool())
    assert error["code"] == "history_error"


def test_list_locks_filters_by_region(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    # No locks registered; filter is still a valid call path to cover.
    assert _ok(list_locks(region_id="region_alpha"))["locks"] == []
