"""Tests for the Phase 7 Stage G cleanup MCP tools."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.project.service import ProjectService
from forge_mcp.server.tools import get_service, set_service
from forge_mcp.server.tools.cleanup import (
    find_lock_conflicts,
    find_orphans,
    find_stale_realizations,
    purge_orphans,
)
from forge_mcp.server.tools.locks import lock_property
from forge_mcp.server.tools.projects import create_project
from forge_mcp.server.tools.regions import create_region, delete_region

if TYPE_CHECKING:
    from pathlib import Path

_BOUNDS: dict[str, object] = {"min": [-10.0, -10.0], "max": [10.0, 10.0]}
_SQUARE = [[0.0, 0.0], [8.0, 0.0], [8.0, 8.0], [0.0, 8.0]]


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
    _ok(create_project(str(tmp_path), "Cleanup", _BOUNDS))
    region = _ok(create_region("R", _SQUARE))
    return cast("str", region["node_id"])


# ---------------------------------------------------------------------------
# no-open-project paths
# ---------------------------------------------------------------------------
def test_all_tools_require_open_project() -> None:
    for tool in (find_orphans, find_stale_realizations, find_lock_conflicts):
        err = _err(tool())
        assert err["code"] == "no_open_project"
    err = _err(purge_orphans())
    assert err["code"] == "no_open_project"


# ---------------------------------------------------------------------------
# find_orphans
# ---------------------------------------------------------------------------
def test_find_orphans_clean_project(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    result = _ok(find_orphans())
    assert result == {
        "specs": [],
        "material_applications": [],
        "environment_bindings": [],
    }


def test_find_orphans_detects_dangling_spec(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    paths = get_service().state.paths
    paths.specs_dir.mkdir(parents=True, exist_ok=True)
    orphan = paths.specs_dir / "spec_orphan.json"
    orphan.write_text("{}", encoding="utf-8")
    result = _ok(find_orphans())
    specs = result["specs"]
    assert isinstance(specs, list)
    assert len(specs) == 1
    entry = specs[0]
    assert isinstance(entry, dict)
    assert entry["spec_id"] == "spec_orphan"


# ---------------------------------------------------------------------------
# find_stale_realizations
# ---------------------------------------------------------------------------
def test_find_stale_realizations_empty(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    result = _ok(find_stale_realizations())
    assert result == {"stale": []}


def test_find_stale_realizations_detects_orphan_blend(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    paths = get_service().state.paths
    paths.blender_dir.mkdir(parents=True, exist_ok=True)
    blend = paths.blender_dir / "ghost_region.blend"
    blend.write_bytes(b"BLENDER")
    stale = _ok(find_stale_realizations())["stale"]
    assert isinstance(stale, list)
    assert len(stale) == 1
    entry = stale[0]
    assert isinstance(entry, dict)
    assert entry["region_id"] == "ghost_region"
    assert entry["reason"] == "region_deleted"


def test_find_stale_realizations_detects_newer_spec(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    paths = get_service().state.paths
    # Synthesize a spec linked to the region, then a blend, then bump
    # the spec mtime so it's newer.
    from forge_mcp.project.schemas import RegionId, SpecId  # noqa: PLC0415

    spec_id = SpecId("spec_test")
    spec_path = paths.spec_path(spec_id)
    paths.specs_dir.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("{}", encoding="utf-8")
    get_service().link_region_to_spec(RegionId(region_id), spec_id)

    paths.blender_dir.mkdir(parents=True, exist_ok=True)
    blend = paths.blender_dir / f"{region_id}.blend"
    blend.write_bytes(b"BLENDER")
    # Force spec mtime newer than blend.
    new_time = blend.stat().st_mtime + 10
    os.utime(spec_path, (new_time, new_time))

    stale = _ok(find_stale_realizations())["stale"]
    assert isinstance(stale, list)
    assert len(stale) == 1
    entry = stale[0]
    assert isinstance(entry, dict)
    assert entry["reason"] == "spec_newer_than_blend"


# ---------------------------------------------------------------------------
# find_lock_conflicts
# ---------------------------------------------------------------------------
def test_find_lock_conflicts_clean(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    _ok(lock_property(region_id, "name"))
    result = _ok(find_lock_conflicts())
    assert result == {"conflicts": []}


def test_find_lock_conflicts_detects_missing_target(tmp_path: Path) -> None:
    region_id = _bootstrap(tmp_path)
    lock_envelope = _ok(lock_property(region_id, "name"))
    lock = lock_envelope["lock"]
    assert isinstance(lock, dict)
    lock_id = lock["lock_id"]
    _ok(delete_region(region_id))
    conflicts = _ok(find_lock_conflicts())["conflicts"]
    assert isinstance(conflicts, list)
    assert len(conflicts) == 1
    entry = conflicts[0]
    assert isinstance(entry, dict)
    assert entry["lock_id"] == lock_id
    assert entry["reason"] == "target_missing"


# ---------------------------------------------------------------------------
# purge_orphans
# ---------------------------------------------------------------------------
def test_purge_orphans_dry_run_does_not_delete(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    paths = get_service().state.paths
    paths.specs_dir.mkdir(parents=True, exist_ok=True)
    orphan = paths.specs_dir / "spec_orphan.json"
    orphan.write_text("{}", encoding="utf-8")
    result = _ok(purge_orphans())
    assert result["dry_run"] is True
    would_remove = result["would_remove"]
    assert isinstance(would_remove, list)
    assert len(would_remove) == 1
    assert orphan.exists(), "dry-run must not delete files"


def test_purge_orphans_explicit_false_deletes(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    paths = get_service().state.paths
    paths.specs_dir.mkdir(parents=True, exist_ok=True)
    orphan = paths.specs_dir / "spec_orphan.json"
    orphan.write_text("{}", encoding="utf-8")
    result = _ok(purge_orphans(dry_run=False))
    assert result["dry_run"] is False
    removed = result["removed"]
    assert isinstance(removed, list)
    assert len(removed) == 1
    assert not orphan.exists()
    # And a follow-up scan reports nothing.
    follow_up = _ok(find_orphans())
    assert follow_up["specs"] == []
