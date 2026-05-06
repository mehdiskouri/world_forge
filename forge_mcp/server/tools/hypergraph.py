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
from forge_mcp.project.schemas import (
    BoundaryId,
    BoundaryRecord,
    ElevationContinuityContract,
    NodeId,
    StreamCrossingContract,
)
from forge_mcp.project.service import NoOpenProjectError
from forge_mcp.server.tools import get_service
from forge_mcp.server.tools._responses import fail, ok


def _boundary_metrics(boundary: BoundaryRecord) -> dict[str, object]:
    """Return Phase-6 inspection metrics for ``boundary``.

    ``elevation_overlap_m`` is the elevation contract's band span
    (``high_m - low_m``) or ``None`` when no elevation contract was
    negotiated. ``samples_count`` is the contracted sample sequence
    length (``0`` when absent). ``has_stream_crossing`` reports whether
    a :class:`StreamCrossingContract` was negotiated.
    """
    elevation = next(
        (c for c in boundary.contracts if isinstance(c, ElevationContinuityContract)),
        None,
    )
    has_stream = any(isinstance(c, StreamCrossingContract) for c in boundary.contracts)
    return {
        "elevation_overlap_m": (
            elevation.high_m - elevation.low_m if elevation is not None else None
        ),
        "samples_count": len(elevation.samples) if elevation is not None else 0,
        "has_stream_crossing": has_stream,
        "contract_count": len(boundary.contracts),
    }


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
    """Return all boundary records in deterministic order with Phase-6 metrics."""
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    boundaries = _list_boundaries(state.boundaries)
    return ok(
        {
            "boundaries": [
                {
                    "boundary": state.boundaries[bid].model_dump(mode="json"),
                    "metrics": _boundary_metrics(state.boundaries[bid]),
                }
                for bid in boundaries
            ],
        },
    )


def inspect_boundary(boundary_id: str) -> dict[str, object]:
    """Return one boundary record by id with Phase-6 metrics."""
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    try:
        boundary = _inspect_boundary(state.boundaries, BoundaryId(boundary_id))
    except KeyError:
        return fail("unknown_boundary", f"unknown boundary {boundary_id!r}")
    return ok(
        {
            "boundary": boundary.model_dump(mode="json"),
            "metrics": _boundary_metrics(boundary),
        },
    )


__all__ = ["inspect_boundary", "list_boundaries", "query_layer"]
