"""Tests for the bounded undo ring (Phase 7 Stage E)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from forge_mcp.project.schemas import HistoryEventKind, WorldBounds
from forge_mcp.project.service import (
    CannotUndoError,
    ProjectService,
)
from forge_mcp.project.undo import UNDO_STACK_LIMIT, UndoStack

if TYPE_CHECKING:
    from pathlib import Path

WORLD = WorldBounds(min=(-100.0, -100.0), max=(100.0, 100.0))
SQUARE: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (10.0, 0.0),
    (10.0, 10.0),
    (0.0, 10.0),
)
SQUARE2: tuple[tuple[float, float], ...] = (
    (20.0, 20.0),
    (30.0, 20.0),
    (30.0, 30.0),
    (20.0, 30.0),
)


def _bootstrap(tmp_path: Path) -> ProjectService:
    svc = ProjectService()
    svc.create_project(tmp_path, "Eldoria", WORLD)
    return svc


# ---------------------------------------------------------------------------
# Baseline behaviour
# ---------------------------------------------------------------------------


def test_baseline_snapshot_is_pushed_on_create(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    assert len(svc.state.undo_stack) == 1
    assert svc.state.paths.undo_dir.is_dir()
    files = list(svc.state.paths.undo_dir.glob("*.json"))
    assert len(files) == 1


def test_undo_at_baseline_raises(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    with pytest.raises(CannotUndoError):
        svc.undo()


# ---------------------------------------------------------------------------
# Region create/update undo
# ---------------------------------------------------------------------------


def test_undo_after_create_region_removes_region(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("South", SQUARE)
    region_path = svc.state.paths.region_path(region.node_id)
    assert region.node_id in svc.state.regions
    assert region_path.is_file()

    event = svc.undo()
    assert event.kind is HistoryEventKind.UNDO
    assert region.node_id not in svc.state.regions
    assert not region_path.exists()


def test_undo_after_update_region_restores_prior_fields(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("South", SQUARE)
    original_name = region.name
    svc.update_region(region.node_id, name="Renamed")
    assert svc.state.regions[region.node_id].name == "Renamed"

    svc.undo()
    assert svc.state.regions[region.node_id].name == original_name


# ---------------------------------------------------------------------------
# Disk persistence across reopen
# ---------------------------------------------------------------------------


def test_undo_persists_across_reopen(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    region = svc.create_region("South", SQUARE)
    root = svc.state.paths.root
    svc.close_project()

    svc2 = ProjectService()
    svc2.open_project(root)
    assert region.node_id in svc2.state.regions
    assert len(svc2.state.undo_stack) >= 2  # noqa: PLR2004 - baseline + create_region

    svc2.undo()
    assert region.node_id not in svc2.state.regions


def test_open_existing_project_without_undo_dir_seeds_baseline(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    root = svc.state.paths.root
    svc.close_project()

    # Wipe the undo directory to mimic a project written before Stage E.
    undo_dir = root / ".undo"
    for path in undo_dir.glob("*.json"):
        path.unlink()

    svc2 = ProjectService()
    svc2.open_project(root)
    assert len(svc2.state.undo_stack) == 1
    with pytest.raises(CannotUndoError):
        svc2.undo()


# ---------------------------------------------------------------------------
# Eviction
# ---------------------------------------------------------------------------


def test_ring_evicts_oldest_snapshot_past_limit(tmp_path: Path) -> None:
    svc = _bootstrap(tmp_path)
    # Drive the ring well past the limit.  Each rename is one undoable
    # mutation, so we make ``UNDO_STACK_LIMIT`` of them on top of the
    # baseline snapshot.
    region = svc.create_region("South", SQUARE)
    for index in range(UNDO_STACK_LIMIT + 5):
        svc.update_region(region.node_id, name=f"Iter-{index}")

    assert len(svc.state.undo_stack) == UNDO_STACK_LIMIT
    files = sorted(svc.state.paths.undo_dir.glob("*.json"))
    assert len(files) == UNDO_STACK_LIMIT


# ---------------------------------------------------------------------------
# UndoStack.load corner cases
# ---------------------------------------------------------------------------


def test_undo_stack_load_skips_non_matching_files(tmp_path: Path) -> None:
    undo_dir = tmp_path / ".undo"
    undo_dir.mkdir()
    (undo_dir / "not_a_snapshot.txt").write_text("noise", encoding="utf-8")
    stack = UndoStack.load(undo_dir)
    assert len(stack) == 0
