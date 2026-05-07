"""Tests for :mod:`forge_mcp.hypergraph.traversal`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from forge_mcp.hypergraph.core import Hypergraph, UnknownLayerError
from forge_mcp.hypergraph.traversal import (
    has_directed_cycle,
    inspect_boundary,
    list_boundaries,
    query_layer,
)
from forge_mcp.project.schemas import (
    BoundaryId,
    BoundaryRecord,
    Edge,
    EdgeId,
    NodeId,
    RegionId,
)

NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def _edge(edge_id: str, *endpoints: str) -> Edge:
    return Edge(
        edge_id=EdgeId(edge_id),
        layer="spatial_containment",
        endpoints=tuple(NodeId(e) for e in endpoints),
        created_at=NOW,
        modified_at=NOW,
    )


def _seeded_hg() -> Hypergraph:
    # world_root - region_a
    #     |
    #     +- region_b - region_c
    hg = Hypergraph(layers=("spatial_containment",))
    view = hg.layer("spatial_containment")
    view.add_edge(_edge("e1", "world_root", "region_a"))
    view.add_edge(_edge("e2", "world_root", "region_b"))
    view.add_edge(_edge("e3", "region_b", "region_c"))
    return hg


def test_query_layer_full_walk_is_sorted_and_deterministic() -> None:
    hg = _seeded_hg()
    once = query_layer(hg, "spatial_containment")
    twice = query_layer(hg, "spatial_containment")
    assert once == twice
    # All four nodes show up; the BFS seeds in sorted order.
    assert set(once) == {
        NodeId("region_a"),
        NodeId("region_b"),
        NodeId("region_c"),
        NodeId("world_root"),
    }


def test_query_layer_from_root_respects_depth() -> None:
    hg = _seeded_hg()
    depth_zero = query_layer(hg, "spatial_containment", root=NodeId("world_root"), depth=0)
    assert depth_zero == (NodeId("world_root"),)
    depth_one = query_layer(hg, "spatial_containment", root=NodeId("world_root"), depth=1)
    assert depth_one == (NodeId("world_root"), NodeId("region_a"), NodeId("region_b"))
    full = query_layer(hg, "spatial_containment", root=NodeId("world_root"))
    assert NodeId("region_c") in full


def test_query_layer_predicate_filters_yielded_nodes() -> None:
    hg = _seeded_hg()
    only_regions = query_layer(
        hg,
        "spatial_containment",
        predicate=lambda nid: nid.startswith("region_"),
    )
    assert NodeId("world_root") not in only_regions
    assert NodeId("region_a") in only_regions


def test_query_layer_rejects_negative_depth() -> None:
    hg = _seeded_hg()
    with pytest.raises(ValueError, match="non-negative"):
        query_layer(hg, "spatial_containment", root=NodeId("world_root"), depth=-1)


def test_query_layer_unknown_layer_raises() -> None:
    hg = _seeded_hg()
    with pytest.raises(UnknownLayerError):
        query_layer(hg, "missing")


# ---------------------------------------------------------------------------
# Boundary helpers
# ---------------------------------------------------------------------------


def _boundary(boundary_id: str, a: str, b: str) -> BoundaryRecord:
    return BoundaryRecord(
        boundary_id=BoundaryId(boundary_id),
        region_a=RegionId(a),
        region_b=RegionId(b),
        shared_edge=((0.0, 0.0), (1.0, 0.0)),
        length_meters=1.0,
        created_at=NOW,
        modified_at=NOW,
    )


def test_list_boundaries_is_lex_sorted() -> None:
    boundaries = {
        BoundaryId("boundary_b"): _boundary("boundary_b", "region_a", "region_b"),
        BoundaryId("boundary_a"): _boundary("boundary_a", "region_a", "region_c"),
    }
    assert list_boundaries(boundaries) == (
        BoundaryId("boundary_a"),
        BoundaryId("boundary_b"),
    )


def test_inspect_boundary_round_trip() -> None:
    boundaries = {BoundaryId("boundary_a"): _boundary("boundary_a", "region_a", "region_b")}
    assert inspect_boundary(boundaries, BoundaryId("boundary_a")).region_a == "region_a"
    with pytest.raises(KeyError):
        inspect_boundary(boundaries, BoundaryId("nope"))


# ---------------------------------------------------------------------------
# has_directed_cycle
# ---------------------------------------------------------------------------


def _directed_edge(
    edge_id: str, src: str, dst: str, *, layer: str = "material_composition"
) -> Edge:
    return Edge(
        edge_id=EdgeId(edge_id),
        layer=layer,
        endpoints=(NodeId(src), NodeId(dst)),
        directed=True,
        created_at=NOW,
        modified_at=NOW,
    )


def test_has_directed_cycle_detects_back_edge() -> None:
    hg = Hypergraph(layers=("material_composition",))
    view = hg.layer("material_composition")
    view.add_edge(_directed_edge("e1", "a", "b"))
    view.add_edge(_directed_edge("e2", "b", "c"))
    view.add_edge(_directed_edge("e3", "c", "a"))
    cycle = has_directed_cycle(hg, "material_composition")
    assert cycle is not None
    assert set(cycle) == {NodeId("a"), NodeId("b"), NodeId("c")}


def test_has_directed_cycle_returns_none_on_dag() -> None:
    hg = Hypergraph(layers=("material_composition",))
    view = hg.layer("material_composition")
    view.add_edge(_directed_edge("e1", "a", "b"))
    view.add_edge(_directed_edge("e2", "a", "c"))
    view.add_edge(_directed_edge("e3", "b", "d"))
    view.add_edge(_directed_edge("e4", "c", "d"))
    assert has_directed_cycle(hg, "material_composition") is None


def test_has_directed_cycle_ignores_undirected_edges() -> None:
    hg = Hypergraph(layers=("material_composition",))
    view = hg.layer("material_composition")
    view.add_edge(
        Edge(
            edge_id=EdgeId("e1"),
            layer="material_composition",
            endpoints=(NodeId("a"), NodeId("b")),
            directed=False,
            created_at=NOW,
            modified_at=NOW,
        ),
    )
    assert has_directed_cycle(hg, "material_composition") is None
