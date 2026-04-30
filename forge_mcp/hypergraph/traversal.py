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
    from forge_mcp.project.schemas import BoundaryId, BoundaryStub, NodeId


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


def list_boundaries(boundaries: Mapping[BoundaryId, BoundaryStub]) -> tuple[BoundaryId, ...]:
    """Return every boundary id, lex-sorted for diff stability."""
    return tuple(sorted(boundaries.keys()))


def inspect_boundary(
    boundaries: Mapping[BoundaryId, BoundaryStub],
    boundary_id: BoundaryId,
) -> BoundaryStub:
    """Return the boundary stub for ``boundary_id`` or raise ``KeyError``."""
    return boundaries[boundary_id]


__all__ = ["NodePredicate", "inspect_boundary", "list_boundaries", "query_layer"]
