"""Polygon validity and pair-wise overlap / shared-edge tests via shapely.

The Phase-2 Pydantic ``Polygon2D`` validator only enforces structural
invariants (≥3 distinct vertices, non-degenerate, CCW canonical). True
self-intersection detection and pairwise spatial relations require a
real geometry kernel; we delegate to shapely 2.x and keep the
import surface confined to this module so the rest of the codebase
stays geometry-library-agnostic.

Public API:

* :func:`validate_polygon` — raises :class:`PolygonInvalidError` if the
  shapely polygon is not OGC-valid (self-intersection, etc.) or if the
  computed area is not strictly positive.
* :func:`polygons_overlap` — true iff the polygons' intersection has
  positive area (a shared edge alone is *not* overlap).
* :func:`shared_edge` — the longest connected linestring on the shared
  boundary of two polygons, returned as a ``(start, end)`` segment, or
  ``None`` if the polygons are non-adjacent or only meet at a point.
* :func:`segment_length` — Euclidean length of a segment.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

from shapely.geometry import LineString, MultiLineString, Polygon

if TYPE_CHECKING:
    from collections.abc import Iterable


Coords = tuple[tuple[float, float], ...]
Segment = tuple[tuple[float, float], tuple[float, float]]

_AREA_EPSILON: Final[float] = 1e-9
_LENGTH_EPSILON: Final[float] = 1e-9
_MIN_VERTICES: Final[int] = 3


class PolygonInvalidError(Exception):
    """Raised when a polygon fails shapely's validity check."""

    def __init__(self, reason: str, coords: Coords) -> None:
        """Store the structured failure reason and the offending coords."""
        super().__init__(reason)
        self.reason = reason
        self.coords = coords


def _to_polygon(coords: Coords) -> Polygon:
    """Coerce ``coords`` into a shapely ``Polygon`` (no validation)."""
    # shapely accepts any iterable of (x, y); we hand it the tuple
    # untouched so users can keep their canonical ordering.
    return Polygon(coords)


def validate_polygon(coords: Coords) -> None:
    """Validate ``coords`` as an OGC-valid polygon with positive area.

    Raises :class:`PolygonInvalidError` on:

    * fewer than three vertices;
    * duplicate vertices;
    * shapely-detected self-intersection / topology errors;
    * effectively-zero area (collinear / degenerate input).
    """
    if len(coords) < _MIN_VERTICES:
        msg = f"polygon needs >= {_MIN_VERTICES} vertices, got {len(coords)}"
        raise PolygonInvalidError(msg, coords)
    if len(set(coords)) != len(coords):
        msg = "polygon vertices must be distinct"
        raise PolygonInvalidError(msg, coords)
    polygon = _to_polygon(coords)
    if not polygon.is_valid:
        # shapely's ``explain_validity`` returns a free-form English
        # diagnostic; passing it through gives the agent enough to act.
        from shapely.validation import explain_validity  # noqa: PLC0415 - local

        msg = f"shapely rejects polygon: {explain_validity(polygon)}"
        raise PolygonInvalidError(msg, coords)
    if polygon.area <= _AREA_EPSILON:
        msg = f"polygon has effectively-zero area ({polygon.area})"
        raise PolygonInvalidError(msg, coords)


def polygons_overlap(a: Coords, b: Coords) -> bool:
    """Return True iff ``a`` and ``b`` overlap on positive area.

    Edge-touching (zero-area shared boundary) does not count as overlap.
    """
    pa = _to_polygon(a)
    pb = _to_polygon(b)
    if not pa.intersects(pb):
        return False
    return pa.intersection(pb).area > _AREA_EPSILON


def shared_edge(a: Coords, b: Coords) -> Segment | None:
    """Return the longest shared boundary segment between ``a`` and ``b``.

    Returns ``None`` if the polygons do not touch on a positive-length
    boundary (i.e. they are disjoint or only meet at a single point).

    The segment is the ``(start, end)`` pair of the longest connected
    component of ``boundary(a) ∩ boundary(b)``. Multiple shared
    components (e.g. two regions touching across two disjoint runs) are
    *not* merged; the longest one wins. Phase-6 contract math will need
    a richer return type, but Phase-2 only consumes this for the
    boundary-stub ``shared_edge`` field.
    """
    pa = _to_polygon(a)
    pb = _to_polygon(b)
    if not pa.intersects(pb):
        return None
    # The boundary of a Polygon is a LinearRing; intersecting two
    # LinearRings can yield a Point, MultiPoint, LineString, or
    # MultiLineString, depending on how the polygons meet.
    raw = pa.boundary.intersection(pb.boundary)
    longest: LineString | None = None
    longest_len = 0.0
    if isinstance(raw, LineString):
        if raw.length > _LENGTH_EPSILON:
            longest = raw
            longest_len = raw.length
    elif isinstance(raw, MultiLineString):
        for piece in raw.geoms:
            if piece.length > longest_len:
                longest = piece
                longest_len = piece.length
    # Anything else (Point, MultiPoint, GeometryCollection that lacks
    # any LineString) means the polygons touch at most at a point — not
    # an adjacency we care about.
    if longest is None:
        return None
    coords = list(longest.coords)
    if len(coords) < 2:  # noqa: PLR2004 - a LineString needs ≥2 vertices to be non-degenerate
        return None
    start = (float(coords[0][0]), float(coords[0][1]))
    end = (float(coords[-1][0]), float(coords[-1][1]))
    return (start, end)


def segment_length(seg: Segment) -> float:
    """Return the Euclidean length of ``seg``."""
    (x1, y1), (x2, y2) = seg
    return math.hypot(x2 - x1, y2 - y1)


def segments_total_length(segments: Iterable[Segment]) -> float:
    """Return the summed Euclidean length of every segment in ``segments``."""
    return sum((segment_length(s) for s in segments), 0.0)


__all__ = [
    "Coords",
    "PolygonInvalidError",
    "Segment",
    "polygons_overlap",
    "segment_length",
    "segments_total_length",
    "shared_edge",
    "validate_polygon",
]
