"""Tests for :mod:`forge_mcp.generate.macro_shape`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest
from forge_mcp.generate.macro_shape import apply_macro_shape

if TYPE_CHECKING:
    from forge_mcp.project.schemas import MacroShape
    from numpy.typing import NDArray


_SHAPE: tuple[int, int] = (32, 32)


def _field(value: float = 0.5) -> NDArray[np.float32]:
    """Return a constant unit-range field for deterministic shape probes."""
    return np.full(_SHAPE, value, dtype=np.float32)


_ALL_SHAPES: tuple[MacroShape, ...] = (
    "none",
    "valley_trough",
    "mesa_terraces",
    "canyon_chasm",
    "lowland_lowpass",
    "dunes_ridges",
    "volcanic_cone",
    "coastal_cliff",
)


@pytest.mark.parametrize("shape", _ALL_SHAPES)
def test_strength_zero_is_identity(shape: MacroShape) -> None:
    """``strength=0`` returns the input field byte-for-byte."""
    field = _field(0.42)
    out = apply_macro_shape(field, shape=shape, strength=0.0, seed=1)
    np.testing.assert_array_equal(out, field)


def test_none_shape_is_identity_at_full_strength() -> None:
    """``shape="none"`` short-circuits regardless of strength."""
    field = _field(0.42)
    out = apply_macro_shape(field, shape="none", strength=1.0, seed=1)
    np.testing.assert_array_equal(out, field)


@pytest.mark.parametrize("shape", _ALL_SHAPES)
def test_output_stays_in_unit_range(shape: MacroShape) -> None:
    """Every shape clamps its output to ``[0, 1]``."""
    rng = np.random.default_rng(7)
    field = rng.uniform(0.0, 1.0, size=_SHAPE).astype(np.float32)
    out = apply_macro_shape(field, shape=shape, strength=1.0, seed=3)
    assert out.dtype == np.float32
    assert out.shape == _SHAPE
    assert float(out.min()) >= 0.0
    assert float(out.max()) <= 1.0


@pytest.mark.parametrize("shape", _ALL_SHAPES)
def test_deterministic_for_fixed_seed(shape: MacroShape) -> None:
    """Same inputs, byte-identical outputs (covers RNG-seeded shapes)."""
    field = _field(0.5)
    a = apply_macro_shape(field, shape=shape, strength=0.8, seed=42)
    b = apply_macro_shape(field, shape=shape, strength=0.8, seed=42)
    np.testing.assert_array_equal(a, b)


def test_lowland_lowpass_seed_changes_tilt_axis() -> None:
    """Different seeds yield different tilt directions."""
    field = _field(0.5)
    a = apply_macro_shape(field, shape="lowland_lowpass", strength=1.0, seed=1)
    b = apply_macro_shape(field, shape="lowland_lowpass", strength=1.0, seed=999)
    assert not np.array_equal(a, b)


def test_valley_trough_lowers_centre_below_rim() -> None:
    """The midline (centre row) ends up lower than the top/bottom rows."""
    field = _field(0.8)
    out = apply_macro_shape(field, shape="valley_trough", strength=1.0, seed=0)
    centre_row = float(out[_SHAPE[0] // 2, :].mean())
    rim_row = float(out[0, :].mean())
    assert centre_row < rim_row


def test_mesa_terraces_quantises_to_few_levels() -> None:
    """Output of mesa_terraces collapses to a small number of plateau values."""
    rng = np.random.default_rng(0)
    field = rng.uniform(0.0, 1.0, size=_SHAPE).astype(np.float32)
    out = apply_macro_shape(field, shape="mesa_terraces", strength=1.0, seed=0)
    unique_levels = np.unique(out)
    # Five plateau slices => at most six unique quantised values.
    max_levels = 6
    assert unique_levels.size <= max_levels


def test_canyon_chasm_minimum_lies_on_diagonal() -> None:
    """The deepest cell sits on or near the ``x == y`` diagonal."""
    field = _field(0.9)
    out = apply_macro_shape(field, shape="canyon_chasm", strength=1.0, seed=0)
    flat_index = int(np.argmin(out))
    row, col = divmod(flat_index, _SHAPE[1])
    assert abs(row - col) <= 1


def test_volcanic_cone_centre_higher_than_corners() -> None:
    """Cone summit at the grid centre is higher than the corner cells."""
    field = _field(0.3)
    out = apply_macro_shape(field, shape="volcanic_cone", strength=1.0, seed=0)
    centre = float(out[_SHAPE[0] // 2, _SHAPE[1] // 2])
    corners = float(np.mean([out[0, 0], out[0, -1], out[-1, 0], out[-1, -1]]))
    assert centre > corners


def test_coastal_cliff_splits_into_two_regimes() -> None:
    """Sea cells (top-left) end up below land cells (bottom-right)."""
    field = _field(0.5)
    out = apply_macro_shape(field, shape="coastal_cliff", strength=1.0, seed=0)
    sea = float(out[0, 0])
    land = float(out[-1, -1])
    assert sea < land


def test_dunes_ridges_periodic_along_x() -> None:
    """Dune crests repeat along the x-axis."""
    field = _field(0.5)
    out = apply_macro_shape(field, shape="dunes_ridges", strength=1.0, seed=0)
    row = out[0, :]
    # Multiple local maxima along a row implies a ridge train.
    interior = row[1:-1]
    is_peak = (interior > row[:-2]) & (interior > row[2:])
    min_peaks = 3
    assert int(is_peak.sum()) >= min_peaks
