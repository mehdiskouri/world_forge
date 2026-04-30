"""Tests for :mod:`forge_mcp.hypergraph.core`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from forge_mcp.hypergraph.core import (
    DuplicateEdgeError,
    EdgeLayerMismatchError,
    Hypergraph,
    HypergraphError,
    UnknownEdgeError,
    UnknownLayerError,
    UnknownNodeError,
)
from forge_mcp.project.schemas import (
    Edge,
    EdgeId,
    NodeId,
    Polygon2D,
    RegionId,
    RegionNode,
    SpatialBounds,
    WorldBounds,
    WorldRootNode,
)
from forge_mcp.project.service import ProjectService

if TYPE_CHECKING:
    from pathlib import Path


NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
SQUARE = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
LAYERS = ("spatial_containment", "spatial_adjacency")


def _region(node_id: str, parent: str = "world_root") -> RegionNode:
    return RegionNode(
        node_id=RegionId(node_id),
        parent_node=NodeId(parent),
        name=node_id,
        spatial_bounds=SpatialBounds(coords=Polygon2D(coords=SQUARE)),
        seed=1,
        created_at=NOW,
        modified_at=NOW,
    )


def _world() -> WorldRootNode:
    return WorldRootNode(node_id=NodeId("world_root"), name="World", created_at=NOW)


def _edge(edge_id: str, layer: str, *endpoints: str) -> Edge:
    return Edge(
        edge_id=EdgeId(edge_id),
        layer=layer,
        endpoints=tuple(NodeId(e) for e in endpoints),
        created_at=NOW,
        modified_at=NOW,
    )


# ---------------------------------------------------------------------------
# LayerView basics
# ---------------------------------------------------------------------------


def test_layer_view_add_and_query() -> None:
    hg = Hypergraph(layers=LAYERS)
    view = hg.layer("spatial_containment")
    edge = _edge("e1", "spatial_containment", "world_root", "region_alpha")
    view.add_edge(edge)
    assert "e1" in view
    assert len(view) == 1
    assert view.edges_for(NodeId("world_root")) == (edge,)
    assert view.neighbors(NodeId("world_root")) == (NodeId("region_alpha"),)


def test_layer_view_rejects_layer_mismatch() -> None:
    hg = Hypergraph(layers=LAYERS)
    view = hg.layer("spatial_containment")
    with pytest.raises(EdgeLayerMismatchError, match="layer"):
        view.add_edge(_edge("e1", "hydrology", "a", "b"))


def test_layer_view_rejects_duplicate_edge() -> None:
    hg = Hypergraph(layers=LAYERS)
    view = hg.layer("spatial_containment")
    view.add_edge(_edge("e1", "spatial_containment", "a", "b"))
    with pytest.raises(DuplicateEdgeError, match="e1"):
        view.add_edge(_edge("e1", "spatial_containment", "a", "c"))


def test_layer_view_remove_edge_clears_adjacency() -> None:
    hg = Hypergraph(layers=LAYERS)
    view = hg.layer("spatial_containment")
    edge = _edge("e1", "spatial_containment", "a", "b")
    view.add_edge(edge)
    removed = view.remove_edge(EdgeId("e1"))
    assert removed == edge
    assert view.edges_for(NodeId("a")) == ()
    assert view.neighbors(NodeId("a")) == ()
    with pytest.raises(UnknownEdgeError):
        view.remove_edge(EdgeId("e1"))


def test_layer_view_neighbors_dedupes_hyperedge() -> None:
    hg = Hypergraph(layers=LAYERS)
    view = hg.layer("spatial_containment")
    view.add_edge(_edge("e1", "spatial_containment", "a", "b", "c"))
    view.add_edge(_edge("e2", "spatial_containment", "a", "b"))
    neighbours = view.neighbors(NodeId("a"))
    assert neighbours == (NodeId("b"), NodeId("c"))  # sorted, deduped


# ---------------------------------------------------------------------------
# Hypergraph node + layer registration
# ---------------------------------------------------------------------------


def test_register_layer_and_unknown_lookup() -> None:
    hg = Hypergraph(layers=("a",))
    hg.register_layer("b")
    assert "a" in hg.layers
    assert "b" in hg.layers
    with pytest.raises(HypergraphError, match="already"):
        hg.register_layer("a")
    with pytest.raises(UnknownLayerError, match="missing"):
        hg.layer("missing")


def test_add_node_and_lookup() -> None:
    hg = Hypergraph(layers=LAYERS)
    region = _region("region_alpha")
    hg.add_node(region)
    assert NodeId("region_alpha") in hg
    assert hg.node(NodeId("region_alpha")) == region
    with pytest.raises(HypergraphError, match="already"):
        hg.add_node(region)
    with pytest.raises(UnknownNodeError):
        hg.node(NodeId("ghost"))


def test_layer_view_contains_handles_non_string() -> None:
    hg = Hypergraph(layers=LAYERS)
    view = hg.layer("spatial_containment")
    non_string_key: object = 12345
    assert non_string_key not in view
    assert NodeId("ghost") not in hg


# ---------------------------------------------------------------------------
# from_project + to_persistence round-trip
# ---------------------------------------------------------------------------


def test_from_project_and_persistence_round_trip(tmp_path: Path) -> None:
    svc = ProjectService()
    svc.create_project(
        tmp_path,
        "Eldoria",
        WorldBounds(min=(-10.0, -10.0), max=(10.0, 10.0)),
    )
    region = _region("region_alpha")
    svc.state.regions[region.node_id] = region
    svc.state.edges["spatial_containment"].append(
        _edge("e1", "spatial_containment", "world_root", "region_alpha"),
    )

    hg = Hypergraph.from_project(svc.state)
    assert NodeId("world_root") in hg
    assert NodeId("region_alpha") in hg
    assert hg.layer("spatial_containment").edges_for(NodeId("world_root"))

    written = list(hg.to_persistence())
    # one entry per registered layer
    assert {p.name for p, _ in written} == {
        "hydrology.json",
        "spatial_adjacency.json",
        "spatial_containment.json",
    }
    containment = next(body for path, body in written if path.name == "spatial_containment.json")
    parsed = json.loads(containment)
    assert parsed["layer"] == "spatial_containment"
    assert parsed["edges"][0]["edge_id"] == "e1"


def test_from_project_seeds_world_root_even_without_regions(tmp_path: Path) -> None:
    svc = ProjectService()
    svc.create_project(
        tmp_path,
        "Empty",
        WorldBounds(min=(-1.0, -1.0), max=(1.0, 1.0)),
    )
    hg = Hypergraph.from_project(svc.state)
    assert NodeId("world_root") in hg
    assert isinstance(hg.node(NodeId("world_root")), type(_world()))
