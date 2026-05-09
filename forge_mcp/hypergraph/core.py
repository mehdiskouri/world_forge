"""Multi-layer in-memory hypergraph for the open Forge project.

Phase 2 Stage D introduces the hand-rolled multilayer hypergraph that
the MCP query tools (Stage G) sit on top of. Layers are independent;
the same node pair can appear in any number of them. The graph is a
projection of :class:`forge_mcp.project.service.ProjectState` and round-
trips back to the per-layer ``edges/<layer>.json`` files via
:func:`Hypergraph.to_persistence`.

Why hand-rolled (no networkx):

* networkx's stubs are partial and bring ``Any`` everywhere — we'd
  burn the whole strict-mypy budget on third-party type holes;
* Phase 2 graphs are tiny (hundreds of nodes); the loops fit on a page;
* every edge is a hyperedge (``endpoints: tuple[NodeId, ...]``), which
  networkx models awkwardly anyway.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from forge_mcp._io.atomic import dump_json
from forge_mcp.project.schemas import (
    Edge,
    EdgeId,
    EdgeLayerFile,
    EnvironmentNode,
    MaterialArchetypeNode,
    NodeId,
    RegionNode,
    SubRegionNode,
    WorldRootNode,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

    from forge_mcp.project.service import ProjectState


NodeRecord = RegionNode | WorldRootNode | MaterialArchetypeNode | SubRegionNode | EnvironmentNode
"""Every node kind that can live in the hypergraph (Phase 6-f)."""


def _node_id_of(record: NodeRecord) -> NodeId:
    """Return the ``NodeId`` view of ``record``'s identifier.

    ``RegionNode.node_id`` is typed ``RegionId``; ``WorldRootNode.node_id``
    is typed ``NodeId``. Both are runtime ``str``; the wrap normalises
    the static type so the surrounding dicts can be keyed on ``NodeId``.
    """
    return NodeId(str(record.node_id))


class HypergraphError(Exception):
    """Base class for in-memory hypergraph errors."""


class UnknownLayerError(HypergraphError):
    """Raised when an operation references a layer that does not exist."""


class UnknownNodeError(HypergraphError):
    """Raised when an operation references a node that does not exist."""


class UnknownEdgeError(HypergraphError):
    """Raised when an operation references an edge that does not exist."""


class DuplicateEdgeError(HypergraphError):
    """Raised when an edge id collides with one already in its layer."""


class EdgeLayerMismatchError(HypergraphError):
    """Raised when an edge's ``layer`` does not match the receiving view."""


# ---------------------------------------------------------------------------
# Layer view
# ---------------------------------------------------------------------------


@dataclass
class LayerView:
    """One typed slice of the hypergraph: all edges declaring ``layer == name``."""

    name: str
    _edges: dict[EdgeId, Edge] = field(default_factory=dict)
    _adjacency: dict[NodeId, set[EdgeId]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------
    def add_edge(self, edge: Edge) -> None:
        """Insert ``edge`` into this layer.

        Raises :class:`EdgeLayerMismatchError` if ``edge.layer`` does not
        match this view's name, or :class:`DuplicateEdgeError` on id
        collision (callers are expected to deduplicate before retrying).
        """
        if edge.layer != self.name:
            msg = f"edge {edge.edge_id!r} layer {edge.layer!r} != view {self.name!r}"
            raise EdgeLayerMismatchError(msg)
        if edge.edge_id in self._edges:
            msg = f"edge {edge.edge_id!r} already present in layer {self.name!r}"
            raise DuplicateEdgeError(msg)
        self._edges[edge.edge_id] = edge
        for endpoint in edge.endpoints:
            self._adjacency.setdefault(endpoint, set()).add(edge.edge_id)

    def remove_edge(self, edge_id: EdgeId) -> Edge:
        """Remove and return the edge identified by ``edge_id``.

        Raises :class:`UnknownEdgeError` if the edge is not in this
        layer.
        """
        if edge_id not in self._edges:
            msg = f"edge {edge_id!r} not in layer {self.name!r}"
            raise UnknownEdgeError(msg)
        edge = self._edges.pop(edge_id)
        for endpoint in edge.endpoints:
            bucket = self._adjacency.get(endpoint)
            if bucket is None:
                continue
            bucket.discard(edge_id)
            if not bucket:
                del self._adjacency[endpoint]
        return edge

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------
    def edges(self) -> tuple[Edge, ...]:
        """Return all edges in this layer, sorted by ``edge_id`` for determinism."""
        return tuple(self._edges[eid] for eid in sorted(self._edges))

    def edges_for(self, node: NodeId) -> tuple[Edge, ...]:
        """Return every edge in this layer that touches ``node`` (deterministic)."""
        ids = sorted(self._adjacency.get(node, set()))
        return tuple(self._edges[eid] for eid in ids)

    def neighbors(self, node: NodeId) -> tuple[NodeId, ...]:
        """Return the unique neighbours of ``node`` across this layer (sorted).

        For hyperedges, every other endpoint of every incident edge
        counts as a neighbour. Self-loops contribute no neighbours.
        """
        seen: set[NodeId] = set()
        for edge in self.edges_for(node):
            for endpoint in edge.endpoints:
                if endpoint != node:
                    seen.add(endpoint)
        return tuple(sorted(seen))

    def __contains__(self, edge_id: object) -> bool:
        """True iff ``edge_id`` names an edge already in this layer."""
        return isinstance(edge_id, str) and edge_id in self._edges

    def __len__(self) -> int:
        """Return the number of edges in this layer."""
        return len(self._edges)


# ---------------------------------------------------------------------------
# Hypergraph
# ---------------------------------------------------------------------------


class Hypergraph:
    """Container for a typed multi-layer hypergraph.

    Layers are explicitly registered (``__init__`` takes the layer
    names) so missing-layer references raise loudly. Adding a new layer
    after construction is intentional and goes through
    :meth:`register_layer`.
    """

    def __init__(self, layers: Iterable[str]) -> None:
        """Build an empty hypergraph with the given registered layers."""
        self._nodes: dict[NodeId, NodeRecord] = {}
        self._layers: dict[str, LayerView] = {name: LayerView(name=name) for name in layers}

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def from_project(cls, state: ProjectState) -> Hypergraph:
        """Build a hypergraph from the in-memory state of an open project.

        Includes the synthetic ``world_root`` node so containment edges
        from ``world_root → region`` resolve.
        """
        hg = cls(layers=state.metadata.registered_layers)
        # Seed the synthetic world root. Phase-2 only persists region
        # nodes per region file; the world-root record is reconstructed
        # from ProjectMetadata to keep the on-disk layout minimal.
        world_root = state.world_root or WorldRootNode(
            node_id=state.metadata.world_node_id,
            name="World",
            created_at=state.metadata.created_at,
        )
        hg.add_node(world_root)
        for region in state.regions.values():
            hg.add_node(region)
        for archetype in state.archetypes.values():
            hg.add_node(archetype)
        for sub_region in state.sub_regions.values():
            hg.add_node(sub_region)
        for environment in state.environments.values():
            hg.add_node(environment)
        for layer, edges in state.edges.items():
            view = hg.layer(layer)
            for edge in edges:
                view.add_edge(edge)
        return hg

    def to_persistence(self) -> Iterator[tuple[Path, str]]:
        """Yield the (path, body) pairs the caller must atomically write.

        ``ProjectService`` owns the actual write (via
        :func:`forge_mcp._io.atomic.atomic_write_text`); the hypergraph
        only knows the canonical filename for each layer. The path is
        relative because the hypergraph is project-root-agnostic; the
        service rebases it onto its own ``ProjectPaths``.
        """
        from pathlib import Path  # noqa: PLC0415 - lazy import for mypy TYPE_CHECKING dance

        for name, view in sorted(self._layers.items()):
            layer_file = EdgeLayerFile(layer=name, edges=view.edges())
            yield Path("edges") / f"{name}.json", dump_json(layer_file)

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------
    def register_layer(self, name: str) -> LayerView:
        """Add a new empty layer; raise if one already exists by that name."""
        if name in self._layers:
            msg = f"layer {name!r} already registered"
            raise HypergraphError(msg)
        view = LayerView(name=name)
        self._layers[name] = view
        return view

    def add_node(self, record: NodeRecord) -> None:
        """Insert ``record``; raise if a node with the same id already exists."""
        node_id = _node_id_of(record)
        if node_id in self._nodes:
            msg = f"node {node_id!r} already in hypergraph"
            raise HypergraphError(msg)
        self._nodes[node_id] = record

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------
    @property
    def nodes(self) -> dict[NodeId, NodeRecord]:
        """Return a *view* of the node table (callers must not mutate)."""
        return self._nodes

    @property
    def layers(self) -> dict[str, LayerView]:
        """Return a *view* of the layer table (callers must not mutate)."""
        return self._layers

    def layer(self, name: str) -> LayerView:
        """Return the :class:`LayerView` for ``name`` or raise."""
        try:
            return self._layers[name]
        except KeyError as exc:
            msg = f"unknown layer {name!r}"
            raise UnknownLayerError(msg) from exc

    def node(self, node_id: NodeId) -> NodeRecord:
        """Return the node record for ``node_id`` or raise."""
        try:
            return self._nodes[node_id]
        except KeyError as exc:
            msg = f"unknown node {node_id!r}"
            raise UnknownNodeError(msg) from exc

    def __contains__(self, node_id: object) -> bool:
        """True iff ``node_id`` names a node already in the graph."""
        return isinstance(node_id, str) and node_id in self._nodes


__all__ = [
    "DuplicateEdgeError",
    "EdgeLayerMismatchError",
    "Hypergraph",
    "HypergraphError",
    "LayerView",
    "NodeRecord",
    "UnknownEdgeError",
    "UnknownLayerError",
    "UnknownNodeError",
]
