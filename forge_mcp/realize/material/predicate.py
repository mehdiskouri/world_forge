"""Pure-numpy evaluator for :class:`SubRegionPredicate`.

Evaluates a v1 sub-region predicate against pre-computed per-pixel grids
(``elevation_grid``, ``slope_grid``, ``aspect_grid``,
``distance_to_stream_grid``) and returns a boolean mask of the same shape.

The grids are produced by
:func:`forge_mcp.analyze.terrain_analysis.compute_predicate_grids` from
the parent region's persisted :class:`Heightmap` (and optionally
:class:`StreamGeometry`). This module is intentionally side-effect free
and import-light so the resolver, the coverage-preview tool, and the
Blender adapter shadow tests can all reuse it.

Combination semantics are owned by the resolver — see
:mod:`forge_mcp.realize.material.resolver`. This module's only job is the
truth table for one predicate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

import numpy as np

from forge_mcp.project.schemas import (
    AspectPredicate,
    DistanceToStreamPredicate,
    HeightBandPredicate,
    SlopePredicate,
)

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from forge_mcp.project.schemas import SubRegionPredicate


def evaluate_predicate(
    predicate: SubRegionPredicate,
    *,
    elevation_grid: NDArray[np.float32],
    slope_grid: NDArray[np.float32],
    aspect_grid: NDArray[np.float32],
    distance_to_stream_grid: NDArray[np.float32] | None,
) -> NDArray[np.bool_]:
    """Return a per-pixel boolean mask where ``predicate`` selects.

    All grids must share the parent heightmap's shape. ``aspect_grid``
    is in compass degrees ``[0, 360)`` matching
    :func:`forge_mcp.analyze.terrain_analysis._slope_and_aspect`.
    ``distance_to_stream_grid`` is in world meters; ``None`` means the
    parent region has no stream and a
    :class:`DistanceToStreamPredicate` evaluates to all-``False``.

    Half-open semantics ``[low, high)`` mirror the schema's docstrings.
    """
    if isinstance(predicate, HeightBandPredicate):
        return (elevation_grid >= predicate.low_m) & (elevation_grid < predicate.high_m)
    if isinstance(predicate, SlopePredicate):
        return (slope_grid >= predicate.min_deg) & (slope_grid < predicate.max_deg)
    if isinstance(predicate, AspectPredicate):
        if predicate.min_deg < predicate.max_deg:
            return (aspect_grid >= predicate.min_deg) & (aspect_grid < predicate.max_deg)
        # Wrap through north: [min, 360) and [0, max).
        return (aspect_grid >= predicate.min_deg) | (aspect_grid < predicate.max_deg)
    if isinstance(predicate, DistanceToStreamPredicate):
        if distance_to_stream_grid is None:
            return np.zeros(elevation_grid.shape, dtype=np.bool_)
        return distance_to_stream_grid <= predicate.max_m
    # mypy proves the union is exhaustive; this assertion documents the invariant
    # for human readers and would catch any future predicate kind added without
    # updating this dispatch.
    assert_never(predicate)


__all__ = ["evaluate_predicate"]
