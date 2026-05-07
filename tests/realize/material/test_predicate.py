"""Tests for :mod:`forge_mcp.realize.material.predicate`."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

import numpy as np
import pytest
from forge_mcp.project.schemas import (
    AspectPredicate,
    DistanceToStreamPredicate,
    HeightBandPredicate,
    SlopePredicate,
)
from forge_mcp.realize.material.predicate import evaluate_predicate

if TYPE_CHECKING:
    from numpy.typing import NDArray

_SHAPE = (4, 4)
_BAND_LOW = 10.0
_BAND_HIGH = 20.0
_SLOPE_LOW = 5.0
_SLOPE_HIGH = 15.0
_DISTANCE_MAX = 5.0


class _Grids(TypedDict):
    elevation_grid: NDArray[np.float32]
    slope_grid: NDArray[np.float32]
    aspect_grid: NDArray[np.float32]
    distance_to_stream_grid: NDArray[np.float32] | None


def _flat(value: float) -> NDArray[np.float32]:
    return np.full(_SHAPE, value, dtype=np.float32)


def _grids(
    *,
    elevation: float = 0.0,
    slope: float = 0.0,
    aspect: float = 0.0,
    distance: float | None = 0.0,
) -> _Grids:
    distance_grid = None if distance is None else _flat(distance)
    return {
        "elevation_grid": _flat(elevation),
        "slope_grid": _flat(slope),
        "aspect_grid": _flat(aspect),
        "distance_to_stream_grid": distance_grid,
    }


def test_height_band_selects_in_band() -> None:
    pred = HeightBandPredicate(low_m=_BAND_LOW, high_m=_BAND_HIGH)
    mask = evaluate_predicate(pred, **_grids(elevation=15.0))
    assert mask.all()


def test_height_band_rejects_out_of_band_above() -> None:
    pred = HeightBandPredicate(low_m=_BAND_LOW, high_m=_BAND_HIGH)
    mask = evaluate_predicate(pred, **_grids(elevation=25.0))
    assert not mask.any()


def test_height_band_rejects_out_of_band_below() -> None:
    pred = HeightBandPredicate(low_m=_BAND_LOW, high_m=_BAND_HIGH)
    mask = evaluate_predicate(pred, **_grids(elevation=5.0))
    assert not mask.any()


def test_height_band_half_open_excludes_high() -> None:
    pred = HeightBandPredicate(low_m=_BAND_LOW, high_m=_BAND_HIGH)
    mask = evaluate_predicate(pred, **_grids(elevation=_BAND_HIGH))
    assert not mask.any()


def test_slope_band_selects_in_band() -> None:
    pred = SlopePredicate(min_deg=_SLOPE_LOW, max_deg=_SLOPE_HIGH)
    mask = evaluate_predicate(pred, **_grids(slope=10.0))
    assert mask.all()


def test_aspect_simple_band() -> None:
    pred = AspectPredicate(min_deg=45.0, max_deg=135.0)
    mask_in = evaluate_predicate(pred, **_grids(aspect=90.0))
    mask_out = evaluate_predicate(pred, **_grids(aspect=200.0))
    assert mask_in.all()
    assert not mask_out.any()


def test_aspect_wrap_through_north() -> None:
    """min > max means wrap through 0°."""
    pred = AspectPredicate(min_deg=315.0, max_deg=45.0)
    mask_north = evaluate_predicate(pred, **_grids(aspect=350.0))
    mask_just_after_zero = evaluate_predicate(pred, **_grids(aspect=10.0))
    mask_south = evaluate_predicate(pred, **_grids(aspect=180.0))
    assert mask_north.all()
    assert mask_just_after_zero.all()
    assert not mask_south.any()


def test_distance_to_stream_selects_within_max() -> None:
    pred = DistanceToStreamPredicate(max_m=_DISTANCE_MAX)
    mask = evaluate_predicate(pred, **_grids(distance=3.0))
    assert mask.all()


def test_distance_to_stream_rejects_beyond_max() -> None:
    pred = DistanceToStreamPredicate(max_m=_DISTANCE_MAX)
    mask = evaluate_predicate(pred, **_grids(distance=10.0))
    assert not mask.any()


def test_distance_to_stream_no_stream_evaluates_false() -> None:
    pred = DistanceToStreamPredicate(max_m=_DISTANCE_MAX)
    mask = evaluate_predicate(pred, **_grids(distance=None))
    assert not mask.any()
    assert mask.shape == _SHAPE


def test_predicate_mask_shape_matches_input() -> None:
    pred = HeightBandPredicate(low_m=_BAND_LOW, high_m=_BAND_HIGH)
    grids = _grids(elevation=15.0)
    mask = evaluate_predicate(pred, **grids)
    assert mask.shape == _SHAPE
    assert mask.dtype == np.bool_


def test_height_band_partial_coverage() -> None:
    pred = HeightBandPredicate(low_m=_BAND_LOW, high_m=_BAND_HIGH)
    elevation = np.array(
        [[5.0, 15.0, 25.0, 12.0]] * 4,
        dtype=np.float32,
    )
    mask = evaluate_predicate(
        pred,
        elevation_grid=elevation,
        slope_grid=_flat(0.0),
        aspect_grid=_flat(0.0),
        distance_to_stream_grid=None,
    )
    expected = np.array([[False, True, False, True]] * 4)
    assert np.array_equal(mask, expected)


@pytest.mark.parametrize(
    ("low", "high", "value", "expected"),
    [
        (0.0, 10.0, 0.0, True),
        (0.0, 10.0, 10.0, False),
        (0.0, 10.0, -1.0, False),
    ],
)
def test_height_band_boundary_table(
    low: float,
    high: float,
    value: float,
    *,
    expected: bool,
) -> None:
    pred = HeightBandPredicate(low_m=low, high_m=high)
    mask = evaluate_predicate(pred, **_grids(elevation=value))
    assert bool(mask.all()) is expected
