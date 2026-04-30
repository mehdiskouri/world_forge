"""Tests for :mod:`forge_mcp.generate.erosion`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

import numpy as np
import pytest
from forge_mcp.generate.deterministic import make_rng
from forge_mcp.generate.erosion import hydraulic, thermal
from forge_mcp.generate.noise import ridged_multifractal

if TYPE_CHECKING:
    from numpy.typing import NDArray

SHAPE: Final[tuple[int, int]] = (32, 32)


def _terrain(seed: int = 13) -> NDArray[np.float32]:
    base = ridged_multifractal(
        SHAPE,
        seed=seed,
        octaves=3,
        lacunarity=2.0,
        persistence=0.5,
        warp=0.0,
        scale_meters=200.0,
        resolution_meters_per_pixel=2.0,
    )
    return cast("NDArray[np.float32]", (base * 100.0).astype(np.float32))


def _max_slope_degrees(grid: NDArray[np.float32]) -> float:
    """Return the steepest finite-difference slope in the grid (degrees)."""
    dy, dx = np.gradient(grid)
    rise = np.hypot(dy, dx)
    return float(np.degrees(np.arctan(rise.max())))


def test_hydraulic_is_deterministic_for_same_seed() -> None:
    grid = _terrain()
    a = hydraulic(
        grid,
        iterations=5,
        rain=0.5,
        evaporation=0.1,
        rng=make_rng(7, purpose="erosion.hydraulic"),
    )
    b = hydraulic(
        grid,
        iterations=5,
        rain=0.5,
        evaporation=0.1,
        rng=make_rng(7, purpose="erosion.hydraulic"),
    )
    assert np.array_equal(a, b)


def test_hydraulic_differs_for_different_seeds() -> None:
    grid = _terrain()
    a = hydraulic(
        grid, iterations=5, rain=0.5, evaporation=0.1, rng=make_rng(1, purpose="erosion.hydraulic")
    )
    b = hydraulic(
        grid, iterations=5, rain=0.5, evaporation=0.1, rng=make_rng(2, purpose="erosion.hydraulic")
    )
    assert not np.array_equal(a, b)


def test_hydraulic_smooths_terrain() -> None:
    """Hydraulic erosion should reduce the steepest slope on a noisy grid."""
    grid = _terrain()
    eroded = hydraulic(
        grid,
        iterations=20,
        rain=0.5,
        evaporation=0.1,
        rng=make_rng(7, purpose="erosion.hydraulic"),
    )
    assert _max_slope_degrees(eroded) < _max_slope_degrees(grid)


def test_thermal_is_deterministic_for_same_seed() -> None:
    grid = _terrain()
    a = thermal(
        grid, iterations=5, talus_angle_degrees=30.0, rng=make_rng(7, purpose="erosion.thermal")
    )
    b = thermal(
        grid, iterations=5, talus_angle_degrees=30.0, rng=make_rng(7, purpose="erosion.thermal")
    )
    assert np.array_equal(a, b)


def test_thermal_relaxes_steep_slopes_below_talus_angle() -> None:
    """A spike sharper than the talus angle must be relaxed toward it."""
    grid = np.zeros((9, 9), dtype=np.float32)
    grid[4, 4] = 100.0  # extremely steep spike
    talus = 25.0
    out = thermal(
        grid,
        iterations=80,
        talus_angle_degrees=talus,
        rng=make_rng(0, purpose="erosion.thermal"),
    )
    # Mass conservation: total volume preserved within float-rounding tolerance.
    assert float(out.sum()) == pytest.approx(float(grid.sum()), rel=1e-4)
    # Spike was knocked down.
    assert float(out[4, 4]) < float(grid[4, 4])


def test_thermal_zero_iterations_is_identity() -> None:
    grid = _terrain()
    out = thermal(
        grid, iterations=0, talus_angle_degrees=30.0, rng=make_rng(0, purpose="erosion.thermal")
    )
    assert np.array_equal(out, grid)
