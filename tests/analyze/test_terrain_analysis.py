"""Tests for :mod:`forge_mcp.analyze.terrain_analysis`."""

from __future__ import annotations

import numpy as np
import pytest
from forge_mcp.analyze.terrain_analysis import (
    ElevationStats,
    PredicateGrids,
    SlopeStats,
    StreamSummary,
    TerrainAnalysis,
    analyze,
    compute_predicate_grids,
)
from forge_mcp.generate.heightmap import Heightmap
from forge_mcp.generate.stream import StreamGeometry

RES = 2.0
ORIGIN = (0.0, 0.0)
BAND = (0.0, 100.0)
SHAPE = (16, 16)
_ASPECT_BIN_COUNT = 8


def _flat(value: float = 50.0) -> Heightmap:
    return Heightmap(
        data=np.full(SHAPE, value, dtype=np.float32),
        resolution_meters_per_pixel=RES,
        origin=ORIGIN,
        elevation_band=BAND,
    )


def _tilted(slope_rise: float = 1.0) -> Heightmap:
    """Plane that rises by ``slope_rise`` meters per pixel along the +y axis."""
    height, width = SHAPE
    rows = (np.arange(height, dtype=np.float32) * slope_rise).reshape(-1, 1)
    cols = np.zeros((1, width), dtype=np.float32)
    return Heightmap(
        data=(rows + cols).astype(np.float32),
        resolution_meters_per_pixel=RES,
        origin=ORIGIN,
        elevation_band=BAND,
    )


def test_analyze_returns_full_structure_without_stream() -> None:
    result = analyze(_flat(), None)
    assert isinstance(result, TerrainAnalysis)
    assert isinstance(result.elevation, ElevationStats)
    assert isinstance(result.slope_degrees, SlopeStats)
    assert len(result.aspect_distribution) == _ASPECT_BIN_COUNT
    assert result.stream is None


def test_flat_terrain_has_zero_slope_and_constant_elevation() -> None:
    result = analyze(_flat(42.0), None)
    assert result.elevation.mean == pytest.approx(42.0)
    assert result.elevation.std == pytest.approx(0.0)
    assert result.slope_degrees.max == pytest.approx(0.0)
    # Aspect of a perfectly flat heightmap is uniform-by-fallback.
    assert result.aspect_distribution == tuple([0.125] * 8)


def test_tilted_plane_has_known_slope() -> None:
    """Plane rising 1 m/pixel at 2 m/pixel resolution → slope = atan(0.5) ≈ 26.57°."""
    result = analyze(_tilted(1.0), None)
    expected_slope_degrees = float(np.degrees(np.arctan(1.0 / RES)))
    # Sobel underestimates slightly at the edges; relax tolerance.
    assert result.slope_degrees.p50 == pytest.approx(expected_slope_degrees, abs=2.0)


def test_aspect_distribution_sums_to_one_when_terrain_has_slope() -> None:
    result = analyze(_tilted(1.0), None)
    assert sum(result.aspect_distribution) == pytest.approx(1.0)


def test_spike_terrain_max_elevation_matches_input() -> None:
    grid = np.zeros(SHAPE, dtype=np.float32)
    grid[8, 8] = 99.0
    hm = Heightmap(
        data=grid,
        resolution_meters_per_pixel=RES,
        origin=ORIGIN,
        elevation_band=BAND,
    )
    result = analyze(hm, None)
    assert result.elevation.max == pytest.approx(99.0)


def test_stream_summary_includes_length_and_anchors() -> None:
    hm = _tilted(1.0)
    geo = StreamGeometry(
        path=((0.0, 0.0), (10.0, 0.0), (10.0, 10.0)),
        width_meters=4.0,
        carving_depth=2.0,
        anchor_in=(0.0, 0.0),
        anchor_out=(10.0, 10.0),
    )
    result = analyze(hm, geo)
    assert isinstance(result.stream, StreamSummary)
    assert result.stream.length_meters == pytest.approx(20.0)
    assert result.stream.anchor_in == (0.0, 0.0)
    assert result.stream.anchor_out == (10.0, 10.0)


def test_stream_summary_zero_length_segments_are_skipped() -> None:
    """Coincident path points must not crash the gradient calculation."""
    hm = _flat()
    geo = StreamGeometry(
        path=((0.0, 0.0), (0.0, 0.0), (4.0, 0.0)),
        width_meters=2.0,
        carving_depth=1.0,
        anchor_in=(0.0, 0.0),
        anchor_out=(4.0, 0.0),
    )
    result = analyze(hm, geo)
    assert result.stream is not None
    assert result.stream.length_meters == pytest.approx(4.0)


def test_compute_predicate_grids_no_stream_yields_none_distance() -> None:
    grids = compute_predicate_grids(_flat(50.0), None)
    assert isinstance(grids, PredicateGrids)
    assert grids.distance_to_stream_grid is None
    assert grids.elevation_grid.shape == SHAPE
    assert grids.slope_grid.shape == SHAPE
    assert grids.aspect_grid.shape == SHAPE


def test_compute_predicate_grids_elevation_matches_input() -> None:
    grids = compute_predicate_grids(_flat(33.0), None)
    assert float(grids.elevation_grid.mean()) == pytest.approx(33.0)


def test_compute_predicate_grids_with_stream_emits_distance_grid() -> None:
    geo = StreamGeometry(
        path=((0.0, 0.0), (10.0, 0.0)),
        width_meters=2.0,
        carving_depth=1.0,
        anchor_in=(0.0, 0.0),
        anchor_out=(10.0, 0.0),
    )
    grids = compute_predicate_grids(_flat(50.0), geo)
    assert grids.distance_to_stream_grid is not None
    assert grids.distance_to_stream_grid.shape == SHAPE
    # The pixel at the origin should be exactly on the stream → 0 m distance.
    assert float(grids.distance_to_stream_grid[0, 0]) == pytest.approx(0.0)
    # Pixels far from the stream should have positive distance.
    assert float(grids.distance_to_stream_grid[-1, -1]) > 0.0


def test_compute_predicate_grids_slope_matches_analyze() -> None:
    """Sanity: predicate-grid slope IS the analyze slope (same routine)."""
    hm = _tilted(1.0)
    grids = compute_predicate_grids(hm, None)
    expected_slope = float(np.degrees(np.arctan(1.0 / RES)))
    # Sobel underestimates at edges; check the interior.
    interior = grids.slope_grid[2:-2, 2:-2]
    assert float(np.median(interior)) == pytest.approx(expected_slope, abs=2.0)
