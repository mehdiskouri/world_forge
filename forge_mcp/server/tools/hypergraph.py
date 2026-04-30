"""``forge.query_layer`` / ``list_boundaries`` / ``inspect_boundary``."""

from __future__ import annotations

from forge_mcp.hypergraph.core import Hypergraph, UnknownLayerError
from forge_mcp.hypergraph.traversal import (
    inspect_boundary as _inspect_boundary,
)
from forge_mcp.hypergraph.traversal import (
    list_boundaries as _list_boundaries,
)
from forge_mcp.hypergraph.traversal import (
    query_layer as _query_layer,
)
from forge_mcp.project.schemas import BoundaryId, NodeId
from forge_mcp.project.service import NoOpenProjectError
from forge_mcp.server.tools import get_service
from forge_mcp.server.tools._responses import fail, ok


def query_layer(
    layer: str,
    root_node: str | None = None,
    depth: int | None = None,
) -> dict[str, object]:
    """Run a BFS over one hypergraph layer."""
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    hg = Hypergraph.from_project(state)
    try:
        nodes = _query_layer(
            hg,
            layer,
            root=NodeId(root_node) if root_node is not None else None,
            depth=depth,
        )
    except UnknownLayerError as exc:
        return fail("unknown_layer", str(exc))
    return ok({"layer": layer, "nodes": [str(n) for n in nodes]})


def list_boundaries() -> dict[str, object]:
    """Return all boundary records in deterministic order."""
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    boundaries = _list_boundaries(state.boundaries)
    return ok(
        {
            "boundaries": [state.boundaries[bid].model_dump(mode="json") for bid in boundaries],
        },
    )


def inspect_boundary(boundary_id: str) -> dict[str, object]:
    """Return one boundary record by id."""
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    try:
        boundary = _inspect_boundary(state.boundaries, BoundaryId(boundary_id))
    except KeyError:
        return fail("unknown_boundary", f"unknown boundary {boundary_id!r}")
    return ok(boundary.model_dump(mode="json"))


__all__ = ["inspect_boundary", "list_boundaries", "query_layer"]
