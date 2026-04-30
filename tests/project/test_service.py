"""Tests for :class:`forge_mcp.project.service.ProjectService`.

Covers project bootstrap, reopen round-trip, save idempotency, refusal
on incompatible descriptor-schema versions, and the history-event
side-effects mandated by Phase 2 Stage C.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from forge_mcp._io.atomic import write_json
from forge_mcp.descriptor.schema import SCHEMA_VERSION as DESCRIPTOR_SCHEMA_VERSION
from forge_mcp.project.schemas import (
    BoundaryId,
    BoundaryStub,
    Edge,
    EdgeId,
    LockId,
    LockKind,
    LockRecord,
    NodeId,
    Polygon2D,
    ProjectMetadata,
    RegionId,
    RegionNode,
    SpatialBounds,
    WorldBounds,
)
from forge_mcp.project.service import (
    NoOpenProjectError,
    ProjectAlreadyExistsError,
    ProjectError,
    ProjectFormatError,
    ProjectNotFoundError,
    ProjectPaths,
    ProjectService,
    ProjectVersionError,
)

if TYPE_CHECKING:
    from pathlib import Path

WORLD = WorldBounds(min=(-100.0, -100.0), max=(100.0, 100.0))


def _bootstrap(tmp_path: Path, name: str = "Eldoria") -> tuple[ProjectService, Path]:
    svc = ProjectService()
    svc.create_project(tmp_path, name, WORLD)
    return svc, tmp_path


# ---------------------------------------------------------------------------
# ProjectPaths
# ---------------------------------------------------------------------------


def test_paths_cover_documented_layout(tmp_path: Path) -> None:
    paths = ProjectPaths(root=tmp_path)
    assert paths.metadata_path == tmp_path / "project.json"
    assert paths.world_node_path == tmp_path / "nodes" / "world.json"
    assert paths.locks_path == tmp_path / "locks" / "locks.json"
    assert paths.gitignore_path == tmp_path / ".gitignore"
    assert tmp_path in paths.all_directories()


# ---------------------------------------------------------------------------
# create_project
# ---------------------------------------------------------------------------


def test_create_project_writes_documented_tree(tmp_path: Path) -> None:
    svc, _root = _bootstrap(tmp_path)
    paths = svc.state.paths

    for directory in paths.all_directories():
        assert directory.is_dir(), directory

    assert paths.metadata_path.is_file()
    assert paths.world_node_path.is_file()
    assert paths.locks_path.is_file()
    assert paths.gitignore_path.read_text(encoding="utf-8") == "realizations/\n"

    # one edge file per registered layer
    edge_files = sorted(p.name for p in paths.edges_dir.glob("*.json"))
    assert edge_files == [
        "hydrology.json",
        "spatial_adjacency.json",
        "spatial_containment.json",
    ]

    # exactly one history event: create_project
    history_files = list(paths.history_dir.glob("*.json"))
    assert len(history_files) == 1
    assert history_files[0].name == "0001_create_project.json"
    payload = json.loads(history_files[0].read_text(encoding="utf-8"))
    assert payload["kind"] == "create_project"
    assert payload["payload"]["name"] == "Eldoria"


def test_create_project_refuses_existing(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    other = ProjectService()
    with pytest.raises(ProjectAlreadyExistsError):
        other.create_project(tmp_path, "Other", WORLD)


def test_create_project_refuses_blank_name(tmp_path: Path) -> None:
    svc = ProjectService()
    with pytest.raises(ProjectError, match="non-empty"):
        svc.create_project(tmp_path, "   ", WORLD)


# ---------------------------------------------------------------------------
# open_project
# ---------------------------------------------------------------------------


def test_open_project_round_trip(tmp_path: Path) -> None:
    svc, root = _bootstrap(tmp_path)
    original = svc.state.metadata

    other = ProjectService()
    reopened = other.open_project(root)
    # ``modified_at`` is the only field that may shift on save; for a
    # freshly created + immediately reopened project nothing else moves.
    assert reopened.project_id == original.project_id
    assert reopened.name == original.name
    assert reopened.world_bounds == original.world_bounds

    # open_project appended its own history event.
    history_files = sorted((other.state.paths.history_dir).glob("*.json"))
    assert [p.name for p in history_files] == [
        "0001_create_project.json",
        "0002_open_project.json",
    ]
    assert other.state.history_count == 2  # noqa: PLR2004 - two appended events


def test_open_project_missing_metadata(tmp_path: Path) -> None:
    svc = ProjectService()
    with pytest.raises(ProjectNotFoundError):
        svc.open_project(tmp_path)


def test_open_project_rejects_unknown_descriptor_schema(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    paths = ProjectPaths(root=tmp_path)
    raw = json.loads(paths.metadata_path.read_text(encoding="utf-8"))
    raw["descriptor_schema_version"] = "9999.0"
    paths.metadata_path.write_text(json.dumps(raw), encoding="utf-8")

    other = ProjectService()
    with pytest.raises(ProjectVersionError, match=r"9999\.0"):
        other.open_project(tmp_path)


def test_open_project_rejects_malformed_metadata(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    paths = ProjectPaths(root=tmp_path)
    paths.metadata_path.write_text("{not json", encoding="utf-8")
    other = ProjectService()
    with pytest.raises(ProjectFormatError):
        other.open_project(tmp_path)


def test_open_project_rejects_missing_directory(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    paths = ProjectPaths(root=tmp_path)
    # Wipe the regions directory to simulate a torn checkout.
    for entry in paths.regions_dir.iterdir():
        entry.unlink()
    paths.regions_dir.rmdir()
    other = ProjectService()
    with pytest.raises(ProjectFormatError, match="regions"):
        other.open_project(tmp_path)


def test_open_project_rejects_mismatched_layer_file(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    paths = ProjectPaths(root=tmp_path)
    layer_path = paths.edge_layer_path("hydrology")
    write_json(layer_path, {"layer": "wrong", "edges": []})
    other = ProjectService()
    with pytest.raises(ProjectFormatError, match="hydrology"):
        other.open_project(tmp_path)


# ---------------------------------------------------------------------------
# save / close
# ---------------------------------------------------------------------------


def test_save_project_is_idempotent_and_bumps_modified_at(tmp_path: Path) -> None:
    svc, _ = _bootstrap(tmp_path)
    original_modified = svc.state.metadata.modified_at
    svc.save_project()
    saved_once = svc.state.metadata.modified_at
    svc.save_project()
    saved_twice = svc.state.metadata.modified_at

    # ``modified_at`` is monotonic non-decreasing.
    assert saved_once >= original_modified
    assert saved_twice >= saved_once

    # On-disk file matches in-memory metadata after each save.
    raw = ProjectMetadata.model_validate_json(
        svc.state.paths.metadata_path.read_text(encoding="utf-8"),
    )
    assert raw.modified_at == saved_twice
    assert raw.descriptor_schema_version == DESCRIPTOR_SCHEMA_VERSION


def test_close_project_drops_state(tmp_path: Path) -> None:
    svc, _ = _bootstrap(tmp_path)
    svc.close_project()
    assert not svc.is_open
    with pytest.raises(NoOpenProjectError):
        _ = svc.state


def test_state_raises_when_no_project_open() -> None:
    svc = ProjectService()
    assert not svc.is_open
    with pytest.raises(NoOpenProjectError):
        _ = svc.state


# ---------------------------------------------------------------------------
# round-tripping in-memory mutations through save / open
# ---------------------------------------------------------------------------


SQUARE = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))


def _seed_state(svc: ProjectService) -> tuple[RegionNode, BoundaryStub, LockRecord, Edge]:
    state = svc.state
    now = state.metadata.created_at
    region = RegionNode(
        node_id=RegionId("region_alpha"),
        parent_node=NodeId("world_root"),
        name="Alpha",
        spatial_bounds=SpatialBounds(coords=Polygon2D(coords=SQUARE)),
        seed=1,
        created_at=now,
        modified_at=now,
    )
    boundary = BoundaryStub(
        boundary_id=BoundaryId("boundary_a__b"),
        region_a=RegionId("region_alpha"),
        region_b=RegionId("region_beta"),
        shared_edge=((0.0, 0.0), (1.0, 0.0)),
        length_meters=1.0,
        created_at=now,
        modified_at=now,
    )
    lock = LockRecord(
        lock_id=LockId("lock_1"),
        region_id=RegionId("region_alpha"),
        kind=LockKind.PROPERTY,
        created_at=now,
        modified_at=now,
    )
    edge = Edge(
        edge_id=EdgeId("edge_world_alpha"),
        layer="spatial_containment",
        endpoints=(NodeId("world_root"), NodeId("region_alpha")),
        created_at=now,
        modified_at=now,
    )
    state.regions[region.node_id] = region
    state.boundaries[boundary.boundary_id] = boundary
    state.lock_store.add_lock(lock)
    state.edges["spatial_containment"].append(edge)
    return region, boundary, lock, edge


def test_save_then_reopen_round_trips_regions_boundaries_locks_edges(
    tmp_path: Path,
) -> None:
    svc, _ = _bootstrap(tmp_path)
    region, boundary, lock, edge = _seed_state(svc)
    svc.save_project()

    other = ProjectService()
    other.open_project(tmp_path)
    state = other.state
    assert state.regions[region.node_id] == region
    assert state.boundaries[boundary.boundary_id] == boundary
    assert state.locks == [lock]
    assert state.edges["spatial_containment"] == [edge]


def test_close_project_flushes_then_drops(tmp_path: Path) -> None:
    svc, _ = _bootstrap(tmp_path)
    region, _b, _l, _e = _seed_state(svc)
    svc.close_project()
    assert not svc.is_open

    other = ProjectService()
    other.open_project(tmp_path)
    assert region.node_id in other.state.regions


# ---------------------------------------------------------------------------
# malformed-on-disk error paths
# ---------------------------------------------------------------------------


def test_open_project_rejects_malformed_region(tmp_path: Path) -> None:
    svc, _ = _bootstrap(tmp_path)
    paths = svc.state.paths
    (paths.regions_dir / "broken.json").write_text("{not json", encoding="utf-8")
    other = ProjectService()
    with pytest.raises(ProjectFormatError, match="region"):
        other.open_project(tmp_path)


def test_open_project_rejects_malformed_boundary(tmp_path: Path) -> None:
    svc, _ = _bootstrap(tmp_path)
    paths = svc.state.paths
    (paths.boundaries_dir / "broken.json").write_text("{nope", encoding="utf-8")
    other = ProjectService()
    with pytest.raises(ProjectFormatError, match="boundary"):
        other.open_project(tmp_path)


def test_open_project_rejects_malformed_locks(tmp_path: Path) -> None:
    svc, _ = _bootstrap(tmp_path)
    paths = svc.state.paths
    paths.locks_path.write_text("{nope", encoding="utf-8")
    other = ProjectService()
    with pytest.raises(ProjectFormatError, match="locks"):
        other.open_project(tmp_path)


def test_open_project_handles_missing_locks_and_boundaries(tmp_path: Path) -> None:
    svc, _ = _bootstrap(tmp_path)
    paths = svc.state.paths
    paths.locks_path.unlink()
    for entry in paths.boundaries_dir.iterdir():
        entry.unlink()
    paths.boundaries_dir.rmdir()
    other = ProjectService()
    other.open_project(tmp_path)
    assert other.state.locks == []
    assert other.state.boundaries == {}


def test_paths_helpers_compose_layer_and_history_files(tmp_path: Path) -> None:
    paths = ProjectPaths(root=tmp_path)
    assert paths.region_path(RegionId("r1")) == tmp_path / "regions" / "r1.json"
    assert paths.boundary_path(BoundaryId("b1")) == tmp_path / "boundaries" / "b1.json"
    assert paths.edge_layer_path("hydrology") == tmp_path / "edges" / "hydrology.json"
