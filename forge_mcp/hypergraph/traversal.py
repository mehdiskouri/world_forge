"""Phase-2 query API for the multi-layer hypergraph.

Just the read-only surface we need to back the Phase-2 MCP tools
``forge.query_layer``, ``forge.list_boundaries``, and
``forge.inspect_boundary`` (Stage G). Anything more sophisticated
(shortest-path, contract-aware traversal, etc.) lives in later phases
and gets its own module.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from forge_mcp.hypergraph.core import Hypergraph
    from forge_mcp.project.schemas import BoundaryId, BoundaryRecord, NodeId


class NodePredicate(Protocol):
    """Callable contract for ``query_layer``'s ``filter`` argument."""

    def __call__(self, node_id: NodeId) -> bool:
        """Return True iff ``node_id`` should be yielded by the traversal."""


def query_layer(
    hg: Hypergraph,
    layer: str,
    *,
    root: NodeId | None = None,
    depth: int | None = None,
    predicate: NodePredicate | None = None,
) -> tuple[NodeId, ...]:
    """Breadth-first walk of ``layer``, returning matching node ids.

    Behaviour:

    * ``root=None`` walks every node *known to the layer* (i.e. anything
      that participates in at least one edge), in deterministic
      ``sorted`` order.
    * ``depth`` caps the BFS distance from ``root`` (depth 0 = root
      only; ``None`` = unbounded).
    * ``predicate`` filters the *yielded* set without affecting which
      nodes the walk traverses.
    * Output is deterministic: BFS order with sorted neighbour
      expansion. Repeated calls produce identical tuples.
    """
    view = hg.layer(layer)
    if depth is not None and depth < 0:
        msg = f"depth must be non-negative, got {depth}"
        raise ValueError(msg)

    visited: set[NodeId] = set()
    ordered: list[NodeId] = []
    queue: deque[tuple[NodeId, int]] = deque()

    if root is None:
        # No anchor: enumerate every node the layer mentions.
        seeds = sorted({endpoint for edge in view.edges() for endpoint in edge.endpoints})
        for seed in seeds:
            queue.append((seed, 0))
            visited.add(seed)
    else:
        queue.append((root, 0))
        visited.add(root)

    while queue:
        node, dist = queue.popleft()
        ordered.append(node)
        if depth is not None and dist >= depth:
            continue
        for neighbour in view.neighbors(node):
            if neighbour in visited:
                continue
            visited.add(neighbour)
            queue.append((neighbour, dist + 1))

    if predicate is None:
        return tuple(ordered)
    return tuple(n for n in ordered if predicate(n))


def list_boundaries(boundaries: Mapping[BoundaryId, BoundaryRecord]) -> tuple[BoundaryId, ...]:
    """Return every boundary id, lex-sorted for diff stability."""
    return tuple(sorted(boundaries.keys()))


def has_directed_cycle(hg: Hypergraph, layer: str) -> tuple[NodeId, ...] | None:
    """Detect whether ``layer`` (interpreted as a directed graph) contains a cycle.

    Edges in ``layer`` are taken to be directed pairs
    ``(endpoints[0], endpoints[1])`` (Phase 6-bis only persists binary
    composition edges; higher-arity directed edges fall outside the
    composition layer's contract). Returns the offending cycle as a
    sorted tuple of node ids when one is found, or ``None`` when the
    layer's directed projection is acyclic.

    The walk is iterative and runs in O(V + E); used by the
    ``material_composition`` write path to refuse cycle-introducing
    edges before they reach disk.
    """
    view = hg.layer(layer)
    successors: dict[NodeId, list[NodeId]] = {}
    for edge in view.edges():
        if not edge.directed or len(edge.endpoints) != 2:  # noqa: PLR2004 - binary edges only
            continue
        src, dst = edge.endpoints[0], edge.endpoints[1]
        successors.setdefault(src, []).append(dst)
    # Iterative DFS with three-colour marking.
    white, grey, black = 0, 1, 2
    colour: dict[NodeId, int] = {}
    parent: dict[NodeId, NodeId | None] = {}
    for root in sorted(successors):
        if colour.get(root, white) != white:
            continue
        stack: list[tuple[NodeId, int]] = [(root, 0)]
        parent[root] = None
        colour[root] = grey
        while stack:
            node, idx = stack[-1]
            children = successors.get(node, ())
            if idx >= len(children):
                colour[node] = black
                stack.pop()
                continue
            stack[-1] = (node, idx + 1)
            child = children[idx]
            state = colour.get(child, white)
            if state == grey:
                # Found a back-edge — collect cycle from `child` back to itself.
                cycle: list[NodeId] = [child]
                cursor: NodeId | None = node
                while cursor is not None and cursor != child:
                    cycle.append(cursor)
                    cursor = parent.get(cursor)
                cycle.reverse()
                return tuple(cycle)
            if state == white:
                parent[child] = node
                colour[child] = grey
                stack.append((child, 0))
    return None


def inspect_boundary(
    boundaries: Mapping[BoundaryId, BoundaryRecord],
    boundary_id: BoundaryId,
) -> BoundaryRecord:
    """Return the boundary stub for ``boundary_id`` or raise ``KeyError``."""
    return boundaries[boundary_id]


__all__ = [
    "NodePredicate",
    "has_directed_cycle",
    "inspect_boundary",
    "list_boundaries",
    "query_layer",
]
