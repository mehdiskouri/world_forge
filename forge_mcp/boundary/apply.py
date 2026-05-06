"""Translate a region's :class:`BoundaryRecord` set into terrain-generator inputs.

Phase 6 Stage C. The terrain generator consumes
:class:`forge_mcp.generate.terrain.BoundaryConditions`; this module
walks a region's boundary records and converts each axis-aligned
shared edge with an :class:`ElevationContinuityContract` into an
:class:`EdgeContract` (heightmap-side, sample sequence, inland
falloff). Non-axis-aligned edges and edges whose sample direction
disagrees with the region's polygon orientation are dropped with a
``boundary_edge_geometry_unmappable`` conflict tag rather than
silently failing — the contract is still persisted for the
:func:`forge_mcp.server.tools.hypergraph.inspect_boundary` surface.

Stream-anchor overrides are not wired in this module yet; they will
land in Stage H once Phase 3's stream injector grows the explicit
edge-anchor entry point.

Pure; no I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from forge_mcp.generate.terrain import BoundaryConditions, EdgeContract
from forge_mcp.project.schemas import ElevationContinuityContract

if TYPE_CHECKING:
    from collections.abc import Iterable

    from forge_mcp.generate.boundary_conform import EdgeSide
    from forge_mcp.project.schemas import (
        BoundaryRecord,
        Polygon2D,
        RegionId,
        RegionNode,
    )


_AXIS_ALIGNED_TOL_M: Final[float] = 1e-3
"""Maximum coordinate deviation before a shared edge counts as axis-aligned."""

_INLAND_FALLOFF_FLOOR_M: Final[float] = 20.0
"""Lower bound on the inland-falloff distance, in meters."""

_INLAND_FALLOFF_FRACTION: Final[float] = 0.05
"""Fraction of the shared-edge length used for the inland-falloff distance."""

_CONFLICT_UNMAPPABLE: Final[str] = "boundary_edge_geometry_unmappable"
"""Conflict tag recorded when a contract cannot be mapped to a heightmap side."""


def _polygon_bbox(polygon: Polygon2D) -> tuple[float, float, float, float]:
    """Return ``(x_min, y_min, x_max, y_max)`` of the polygon's vertices."""
    xs = [coord[0] for coord in polygon.coords]
    ys = [coord[1] for coord in polygon.coords]
    return min(xs), min(ys), max(xs), max(ys)


def _classify_edge(  # noqa: PLR0911 - one return per side + non-axis-aligned
    edge: tuple[tuple[float, float], tuple[float, float]],
    bbox: tuple[float, float, float, float],
) -> EdgeSide | None:
    """Return which heightmap side ``edge`` lies along, or ``None``.

    The mapping uses the convention from
    :data:`forge_mcp.generate.boundary_conform.EdgeSide`:

    * ``"north"`` — top row, ``y == y_max`` of the bbox.
    * ``"south"`` — bottom row, ``y == y_min``.
    * ``"east"`` — right column, ``x == x_max``.
    * ``"west"`` — left column, ``x == x_min``.

    ``None`` when the edge is non-axis-aligned or off the bbox.
    """
    (x0, y0), (x1, y1) = edge
    x_min, y_min, x_max, y_max = bbox
    # Horizontal edge?
    if abs(y0 - y1) <= _AXIS_ALIGNED_TOL_M:
        if abs(y0 - y_min) <= _AXIS_ALIGNED_TOL_M:
            return "south"
        if abs(y0 - y_max) <= _AXIS_ALIGNED_TOL_M:
            return "north"
        return None
    # Vertical edge?
    if abs(x0 - x1) <= _AXIS_ALIGNED_TOL_M:
        if abs(x0 - x_min) <= _AXIS_ALIGNED_TOL_M:
            return "west"
        if abs(x0 - x_max) <= _AXIS_ALIGNED_TOL_M:
            return "east"
        return None
    return None


def _inland_falloff_for(length_m: float) -> float:
    """Return the inland-falloff distance for an edge of length ``length_m``."""
    return max(_INLAND_FALLOFF_FLOOR_M, length_m * _INLAND_FALLOFF_FRACTION)


def _orient_samples(
    samples: tuple[float, ...],
    edge: tuple[tuple[float, float], tuple[float, float]],
    side: EdgeSide,
) -> tuple[float, ...]:
    """Reverse ``samples`` if the contract direction disagrees with the heightmap axis.

    The contract samples run along the shared edge from ``edge[0]`` to
    ``edge[1]`` (preserved by the boundary record's lex-sorted
    canonicalization). The heightmap edge runs:

    * ``south`` / ``north``: ``+x`` from west to east.
    * ``west`` / ``east``: ``+y`` from south to north.

    So the contract orientation is "natural" when ``edge[0]`` is the
    smaller coordinate along the heightmap-edge axis; otherwise we
    reverse the samples to keep the contract physically anchored to
    the right end of the edge.
    """
    (x0, y0), (x1, y1) = edge
    if side in ("south", "north"):
        return samples if x0 <= x1 else tuple(reversed(samples))
    # west / east
    return samples if y0 <= y1 else tuple(reversed(samples))


def build_boundary_conditions(
    region: RegionNode,
    boundaries: Iterable[BoundaryRecord],
) -> BoundaryConditions:
    """Return :class:`BoundaryConditions` for ``region``.

    Walks ``boundaries`` (typically every record in
    :class:`forge_mcp.project.service.ProjectState.boundaries`) and
    keeps the ones ``region`` participates in. For each elevation
    contract on a kept boundary, attempts to map the shared edge to a
    heightmap side via :func:`_classify_edge`. Successful mappings
    become :class:`EdgeContract` entries; unmapped contracts are
    silently dropped but recorded as ``conflicts_resolved``.
    """
    bbox = _polygon_bbox(region.spatial_bounds.coords)
    edge_contracts: list[EdgeContract] = []
    conflicts: list[str] = []
    for boundary in boundaries:
        if region.node_id not in (boundary.region_a, boundary.region_b):
            continue
        side = _classify_edge(boundary.shared_edge, bbox)
        for contract in boundary.contracts:
            if not isinstance(contract, ElevationContinuityContract):
                continue
            if side is None:
                conflicts.append(_CONFLICT_UNMAPPABLE)
                continue
            samples = _orient_samples(contract.samples, boundary.shared_edge, side)
            edge_contracts.append(
                EdgeContract(
                    side=side,
                    samples=samples,
                    inland_falloff_m=_inland_falloff_for(boundary.length_meters),
                    contract_id=str(boundary.boundary_id),
                ),
            )
    return BoundaryConditions(
        edge_contracts=tuple(edge_contracts),
        conflicts_resolved=tuple(conflicts),
    )


def participating_boundaries(
    region_id: RegionId,
    boundaries: Iterable[BoundaryRecord],
) -> tuple[BoundaryRecord, ...]:
    """Return every boundary record where ``region_id`` is one of the endpoints."""
    return tuple(b for b in boundaries if region_id in (b.region_a, b.region_b))


__all__ = [
    "build_boundary_conditions",
    "participating_boundaries",
]
