"""Tests for :mod:`forge_mcp.geometry.adjacency`."""

from __future__ import annotations

from datetime import UTC, datetime

from forge_mcp.geometry.adjacency import detect_adjacencies
from forge_mcp.project.schemas import (
    NodeId,
    Polygon2D,
    RegionId,
    RegionNode,
    SpatialBounds,
)

NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def _region(node_id: str, coords: tuple[tuple[float, float], ...]) -> RegionNode:
    return RegionNode(
        node_id=RegionId(node_id),
        parent_node=NodeId("world_root"),
        name=node_id,
        spatial_bounds=SpatialBounds(coords=Polygon2D(coords=coords)),
        seed=1,
        created_at=NOW,
        modified_at=NOW,
    )


SQUARE = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
EAST = ((1.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0))
NORTH = ((0.0, 1.0), (1.0, 1.0), (1.0, 2.0), (0.0, 2.0))
CORNER = ((1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0))
FAR = ((10.0, 10.0), (11.0, 10.0), (11.0, 11.0), (10.0, 11.0))


def test_detect_adjacencies_emits_one_stub_per_shared_edge() -> None:
    new = _region("region_alpha", SQUARE)
    others = [
        _region("region_east", EAST),
        _region("region_north", NORTH),
        _region("region_corner", CORNER),  # corner-touch only
        _region("region_far", FAR),  # disjoint
    ]
    stubs = detect_adjacencies(new, others, now=NOW)
    region_pairs = [(s.region_a, s.region_b) for s in stubs]
    assert region_pairs == [
        (RegionId("region_alpha"), RegionId("region_east")),
        (RegionId("region_alpha"), RegionId("region_north")),
    ]
    for stub in stubs:
        assert stub.length_meters > 0.0
        assert stub.created_at == NOW


def test_detect_adjacencies_skips_self() -> None:
    new = _region("region_alpha", SQUARE)
    stubs = detect_adjacencies(new, [new], now=NOW)
    assert stubs == []


def test_detect_adjacencies_sorts_endpoints_lex() -> None:
    new = _region("region_z", SQUARE)
    other = _region("region_a", EAST)
    stubs = detect_adjacencies(new, [other], now=NOW)
    assert len(stubs) == 1
    # boundary endpoints are lex-sorted regardless of which side is "new".
    assert stubs[0].region_a == RegionId("region_a")
    assert stubs[0].region_b == RegionId("region_z")
    assert stubs[0].boundary_id == "boundary_region_a__region_z"


def test_detect_adjacencies_is_deterministic() -> None:
    new = _region("region_alpha", SQUARE)
    others = [
        _region("region_north", NORTH),
        _region("region_east", EAST),
    ]
    stubs_one = detect_adjacencies(new, others, now=NOW)
    stubs_two = detect_adjacencies(new, list(reversed(others)), now=NOW)
    assert stubs_one == stubs_two
