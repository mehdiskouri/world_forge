"""Round-trip + validation tests for every model in :mod:`forge_mcp.project.schemas`.

Phase 2 Stage H gate: 90% branch coverage with `extra='forbid'`,
frozen-ness, validator paths, and JSON round-trip exercised for every
shape that touches disk.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from forge_mcp.descriptor.schema import StructuredDescriptor, Terrain, TerrainPrimary
from forge_mcp.project.schemas import (
    AuditRecord,
    BoundaryStub,
    Bounds2D,
    Edge,
    EdgeLayerFile,
    HistoryActor,
    HistoryEvent,
    HistoryEventId,
    HistoryEventKind,
    LockId,
    LockKind,
    LockRecord,
    LockStoreFile,
    NodeId,
    Polygon2D,
    ProjectMetadata,
    RegionId,
    RegionNode,
    RegionTier,
    SpatialBounds,
    SpecId,
    SpecRecord,
    WorldBounds,
    WorldRootNode,
)
from pydantic import ValidationError

NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
SQUARE_CCW = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
SQUARE_CW = ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0))
DESCRIPTOR = StructuredDescriptor(terrain=Terrain(primary=TerrainPrimary.PLAINS))


# ---------------------------------------------------------------------------
# Polygon2D
# ---------------------------------------------------------------------------


def test_polygon_canonicalizes_cw_to_ccw() -> None:
    ccw = Polygon2D(coords=SQUARE_CCW)
    cw = Polygon2D(coords=SQUARE_CW)
    # Both wind to the same canonical CCW tuple.
    assert ccw.coords == cw.coords


def test_polygon_accepts_lists() -> None:
    raw: list[list[float]] = [[0.0, 0.0], [2.0, 0.0], [1.0, 1.0]]
    p = Polygon2D.model_validate({"coords": raw})
    assert p.coords[0] == (0.0, 0.0)


def test_polygon_rejects_too_few_points() -> None:
    with pytest.raises(ValidationError, match=">= 3 vertices"):
        Polygon2D(coords=((0.0, 0.0), (1.0, 1.0)))


def test_polygon_rejects_duplicate_points() -> None:
    with pytest.raises(ValidationError, match="distinct"):
        Polygon2D(coords=((0.0, 0.0), (1.0, 1.0), (0.0, 0.0)))


def test_polygon_rejects_collinear() -> None:
    with pytest.raises(ValidationError, match="degenerate"):
        Polygon2D(coords=((0.0, 0.0), (1.0, 1.0), (2.0, 2.0)))


def test_polygon_is_frozen() -> None:
    p = Polygon2D(coords=SQUARE_CCW)
    with pytest.raises(ValidationError):
        p.coords = SQUARE_CW


def test_polygon_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        Polygon2D.model_validate({"coords": SQUARE_CCW, "extra": 1})


# ---------------------------------------------------------------------------
# Bounds2D / WorldBounds / SpatialBounds
# ---------------------------------------------------------------------------


def test_bounds2d_orders() -> None:
    expected_y_max = 2.0
    b = Bounds2D(min=(0.0, 0.0), max=(1.0, expected_y_max))
    assert b.max[1] == expected_y_max


def test_bounds2d_rejects_inverted() -> None:
    with pytest.raises(ValidationError, match="component-wise"):
        Bounds2D(min=(1.0, 0.0), max=(0.0, 1.0))


def test_world_bounds_default_units() -> None:
    wb = WorldBounds(min=(-100.0, -100.0), max=(100.0, 100.0))
    assert wb.units == "meters"
    assert wb.kind == "rectangle"


def test_world_bounds_rejects_zero_extent() -> None:
    with pytest.raises(ValidationError, match="positive extent"):
        WorldBounds(min=(0.0, 0.0), max=(0.0, 1.0))


def test_spatial_bounds_with_elevation() -> None:
    sb = SpatialBounds(coords=Polygon2D(coords=SQUARE_CCW), elevation_range=(0.0, 100.0))
    assert sb.elevation_range == (0.0, 100.0)


def test_spatial_bounds_rejects_inverted_elevation() -> None:
    with pytest.raises(ValidationError, match="elevation_range"):
        SpatialBounds(coords=Polygon2D(coords=SQUARE_CCW), elevation_range=(100.0, 0.0))


# ---------------------------------------------------------------------------
# RegionNode / WorldRootNode
# ---------------------------------------------------------------------------


def _region(**overrides: object) -> RegionNode:
    base: dict[str, object] = {
        "node_id": RegionId("region_test"),
        "parent_node": NodeId("world_root"),
        "name": "Test Region",
        "spatial_bounds": SpatialBounds(coords=Polygon2D(coords=SQUARE_CCW)),
        "seed": 42,
        "created_at": NOW,
        "modified_at": NOW,
    }
    base.update(overrides)
    return RegionNode(**base)  # type: ignore[arg-type]  # test factory


def test_region_round_trip() -> None:
    r = _region(structured_descriptor=DESCRIPTOR)
    raw = r.model_dump(mode="json")
    again = RegionNode.model_validate(raw)
    assert again == r


def test_region_default_tier_and_layers() -> None:
    r = _region()
    assert r.tier is RegionTier.UNIQUE
    assert r.kind == "region"
    assert r.spec_id is None
    assert r.children == ()
    assert r.tags == ()


def test_region_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        RegionNode.model_validate({**_region().model_dump(mode="json"), "extra": 1})


def test_world_root_node_round_trip() -> None:
    n = WorldRootNode(node_id=NodeId("world_root"), name="Test World", created_at=NOW)
    again = WorldRootNode.model_validate(n.model_dump(mode="json"))
    assert again == n


# ---------------------------------------------------------------------------
# Edge / EdgeLayerFile
# ---------------------------------------------------------------------------


def _edge(**overrides: object) -> Edge:
    base: dict[str, object] = {
        "edge_id": "edge_test",
        "layer": "spatial_containment",
        "endpoints": (NodeId("world_root"), NodeId("region_test")),
        "created_at": NOW,
        "modified_at": NOW,
    }
    base.update(overrides)
    return Edge(**base)  # type: ignore[arg-type]  # test factory


def test_edge_round_trip() -> None:
    e = _edge(attrs={"weight": 1, "label": "contains"})
    again = Edge.model_validate(e.model_dump(mode="json"))
    assert again == e


def _edge_with_endpoint(*endpoints: str) -> Edge:
    return _edge(endpoints=tuple(NodeId(e) for e in endpoints))


def test_edge_rejects_lone_endpoint() -> None:
    with pytest.raises(ValidationError, match=">= 2 endpoints"):
        _edge_with_endpoint("world_root")


def test_edge_layer_file_default_empty() -> None:
    f = EdgeLayerFile(layer="spatial_adjacency")
    assert f.edges == ()


def test_edge_layer_file_round_trip() -> None:
    f = EdgeLayerFile(layer="spatial_adjacency", edges=(_edge(),))
    assert EdgeLayerFile.model_validate(f.model_dump(mode="json")) == f


# ---------------------------------------------------------------------------
# Spec / Boundary / Lock / History / Audit
# ---------------------------------------------------------------------------


def test_spec_record_round_trip() -> None:
    s = SpecRecord(spec_id=SpecId("spec_abc123"), descriptor=DESCRIPTOR, created_at=NOW)
    assert SpecRecord.model_validate(s.model_dump(mode="json")) == s


def _boundary(**overrides: object) -> BoundaryStub:
    base: dict[str, object] = {
        "boundary_id": "boundary_a__b",
        "region_a": RegionId("region_a"),
        "region_b": RegionId("region_b"),
        "shared_edge": ((0.0, 0.0), (1.0, 0.0)),
        "length_meters": 1.0,
        "created_at": NOW,
        "modified_at": NOW,
    }
    base.update(overrides)
    return BoundaryStub(**base)  # type: ignore[arg-type]  # test factory


def test_boundary_round_trip() -> None:
    b = _boundary()
    assert BoundaryStub.model_validate(b.model_dump(mode="json")) == b


def test_boundary_rejects_self_loop() -> None:
    with pytest.raises(ValidationError, match="must differ"):
        _boundary(region_b=RegionId("region_a"))


def test_boundary_rejects_unsorted_endpoints() -> None:
    with pytest.raises(ValidationError, match="lex-sorted"):
        _boundary(region_a=RegionId("region_b"), region_b=RegionId("region_a"))


def test_boundary_rejects_zero_length() -> None:
    with pytest.raises(ValidationError, match="length_meters"):
        _boundary(length_meters=0.0)


def test_lock_record_and_store() -> None:
    lock = LockRecord(
        lock_id=LockId("lock_1"),
        region_id=RegionId("region_a"),
        kind=LockKind.PROPERTY,
        created_at=NOW,
        modified_at=NOW,
    )
    store = LockStoreFile(locks=(lock,))
    assert LockStoreFile.model_validate(store.model_dump(mode="json")) == store


def test_history_event_id_must_be_zero_padded() -> None:
    with pytest.raises(ValidationError, match="zero-padded"):
        HistoryEvent(
            event_id=HistoryEventId("1"),
            kind=HistoryEventKind.CREATE_REGION,
            at=NOW,
            actor=HistoryActor.AGENT,
        )


def test_history_event_round_trip() -> None:
    ev = HistoryEvent(
        event_id=HistoryEventId("0001"),
        kind=HistoryEventKind.CREATE_REGION,
        at=NOW,
        actor=HistoryActor.AGENT,
        payload={"region_id": "region_test"},
    )
    assert HistoryEvent.model_validate(ev.model_dump(mode="json")) == ev


def test_audit_record_round_trip() -> None:
    a = AuditRecord(audit_id="audit_1", region_id=RegionId("region_a"), at=NOW, findings=("ok",))
    assert AuditRecord.model_validate(a.model_dump(mode="json")) == a


# ---------------------------------------------------------------------------
# ProjectMetadata
# ---------------------------------------------------------------------------


def test_project_metadata_round_trip() -> None:
    pm = ProjectMetadata(
        project_id=UUID("00000000-0000-4000-8000-000000000000"),
        name="example",
        forge_version="0.0.0",
        blender_version="5.0.0",
        bpy_hypergraph_version="blender-5.0.0-v1",
        descriptor_schema_version="1.0",
        created_at=NOW,
        modified_at=NOW,
        world_node_id=NodeId("world_root"),
        world_bounds=WorldBounds(min=(-100.0, -100.0), max=(100.0, 100.0)),
    )
    assert ProjectMetadata.model_validate(pm.model_dump(mode="json")) == pm
    assert "spatial_containment" in pm.registered_layers
