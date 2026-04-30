"""Adjacency detection: turn polygon-boundary contacts into boundary stubs.

Stage E (Phase 2). The :class:`forge_mcp.project.service.ProjectService`
calls :func:`detect_adjacencies` on every region create / update; the
returned :class:`BoundaryStub` records are persisted under
``boundaries/`` and (Phase 6) eventually fitted with contracts. Phase 2
only emits stubs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_mcp.geometry.polygon import segment_length, shared_edge
from forge_mcp.project.schemas import BoundaryId, BoundaryStub

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from forge_mcp.project.schemas import RegionNode


def _boundary_id(region_a: str, region_b: str) -> BoundaryId:
    """Return the canonical boundary id for an ordered region-id pair."""
    return BoundaryId(f"boundary_{region_a}__{region_b}")


def detect_adjacencies(
    new_region: RegionNode,
    other_regions: Iterable[RegionNode],
    *,
    now: datetime,
) -> list[BoundaryStub]:
    """Return one boundary stub per region in ``other_regions`` that touches.

    Adjacency = positive-length shared boundary (single edge or longer
    run). Corner-only contacts are excluded by
    :func:`forge_mcp.geometry.polygon.shared_edge`.

    The output list is sorted by ``(region_a, region_b)`` so persistence
    diffs are deterministic. The two endpoints inside each stub are
    lex-sorted (``region_a < region_b``) so callers don't have to.

    ``now`` is supplied by the caller so the entire batch shares one
    timestamp; this keeps the on-disk records aligned with the history
    event that emitted them.
    """
    new_coords = new_region.spatial_bounds.coords.coords
    stubs: list[BoundaryStub] = []
    for other in other_regions:
        if other.node_id == new_region.node_id:
            continue
        edge = shared_edge(new_coords, other.spatial_bounds.coords.coords)
        if edge is None:
            continue
        # Sort the endpoints so BoundaryStub's own validator is happy.
        a, b = sorted((str(new_region.node_id), str(other.node_id)))
        stub = BoundaryStub(
            boundary_id=_boundary_id(a, b),
            region_a=type(new_region.node_id)(a),
            region_b=type(new_region.node_id)(b),
            shared_edge=edge,
            length_meters=segment_length(edge),
            created_at=now,
            modified_at=now,
        )
        stubs.append(stub)
    stubs.sort(key=lambda s: (s.region_a, s.region_b))
    return stubs


__all__ = ["detect_adjacencies"]
