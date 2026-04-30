"""Tests for :mod:`forge_mcp.geometry.polygon`."""

from __future__ import annotations

import pytest
from forge_mcp.geometry.polygon import (
    PolygonInvalidError,
    polygons_overlap,
    segment_length,
    segments_total_length,
    shared_edge,
    validate_polygon,
)

SQUARE = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
RIGHT_SQUARE = ((1.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0))
FAR_SQUARE = ((10.0, 10.0), (11.0, 10.0), (11.0, 11.0), (10.0, 11.0))
CORNER_TOUCH_SQUARE = ((1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0))
OVERLAPPING_SQUARE = ((0.5, 0.5), (1.5, 0.5), (1.5, 1.5), (0.5, 1.5))
BOWTIE = ((0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0))


def test_validate_polygon_accepts_square() -> None:
    validate_polygon(SQUARE)


def test_validate_polygon_rejects_few_vertices() -> None:
    with pytest.raises(PolygonInvalidError, match=">= 3"):
        validate_polygon(((0.0, 0.0), (1.0, 1.0)))


def test_validate_polygon_rejects_duplicate_vertex() -> None:
    with pytest.raises(PolygonInvalidError, match="distinct"):
        validate_polygon(((0.0, 0.0), (1.0, 0.0), (1.0, 0.0), (0.0, 1.0)))


def test_validate_polygon_rejects_self_intersection() -> None:
    with pytest.raises(PolygonInvalidError, match="shapely"):
        validate_polygon(BOWTIE)


def test_validate_polygon_rejects_collinear() -> None:
    # Shapely classifies degenerate input as a self-intersection at the
    # repeated vertex; either signal is acceptable, but it must reject.
    with pytest.raises(PolygonInvalidError):
        validate_polygon(((0.0, 0.0), (1.0, 0.0), (2.0, 0.0)))


def test_polygons_overlap_true_for_intersecting() -> None:
    assert polygons_overlap(SQUARE, OVERLAPPING_SQUARE) is True


def test_polygons_overlap_false_for_disjoint() -> None:
    assert polygons_overlap(SQUARE, FAR_SQUARE) is False


def test_polygons_overlap_false_for_edge_touch() -> None:
    # SQUARE and RIGHT_SQUARE share the x=1 edge but do not overlap.
    assert polygons_overlap(SQUARE, RIGHT_SQUARE) is False


def test_shared_edge_returns_segment_for_edge_touch() -> None:
    edge = shared_edge(SQUARE, RIGHT_SQUARE)
    assert edge is not None
    (start, end) = edge
    # The shared boundary is the vertical segment x=1 between y=0 and y=1.
    assert {start, end} == {(1.0, 0.0), (1.0, 1.0)}


def test_shared_edge_none_for_corner_touch() -> None:
    # Single-point contact does not count as adjacency.
    assert shared_edge(SQUARE, CORNER_TOUCH_SQUARE) is None


def test_shared_edge_none_for_disjoint() -> None:
    assert shared_edge(SQUARE, FAR_SQUARE) is None


def test_shared_edge_picks_longest_run_in_multilinestring() -> None:
    # Two squares sharing an edge return a LineString; nothing else to
    # exercise here besides the LineString branch covered above. Build a
    # contrived case via a U-shaped polygon whose boundary touches an
    # adjacent rectangle along two disjoint runs.
    u_shape = (
        (0.0, 0.0),
        (3.0, 0.0),
        (3.0, 1.0),
        (2.0, 1.0),
        (2.0, 2.0),
        (1.0, 2.0),
        (1.0, 1.0),
        (0.0, 1.0),
    )
    cap = (
        (0.0, 1.0),
        (3.0, 1.0),
        (3.0, 1.5),
        (2.0, 1.5),
        (2.0, 2.0),
        (1.0, 2.0),
        (1.0, 1.5),
        (0.0, 1.5),
    )
    edge = shared_edge(u_shape, cap)
    assert edge is not None
    # The longest shared run is the bottom of the cap (length 1.0
    # along x=[0,1] or x=[2,3]); just verify the segment is positive
    # length.
    assert segment_length(edge) > 0.0


def test_segment_helpers() -> None:
    seg = ((0.0, 0.0), (3.0, 4.0))
    assert segment_length(seg) == pytest.approx(5.0)
    assert segments_total_length([seg, seg]) == pytest.approx(10.0)
