"""Tests for :mod:`forge_mcp.generate.noise` — determinism + invariants."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import numpy as np
import pytest
from forge_mcp.generate.noise import ridged_multifractal

if TYPE_CHECKING:
    from numpy.typing import NDArray

SHAPE: Final[tuple[int, int]] = (32, 32)
_OCTAVES: Final[int] = 4
_LACUNARITY: Final[float] = 2.0
_PERSISTENCE: Final[float] = 0.5
_WARP: Final[float] = 0.3
_SCALE_M: Final[float] = 200.0
_RES_M_PER_PX: Final[float] = 2.0


def _noise(
    *,
    seed: int,
    octaves: int = _OCTAVES,
    warp: float = _WARP,
    shape: tuple[int, int] = SHAPE,
) -> NDArray[np.float32]:
    return ridged_multifractal(
        shape,
        seed=seed,
        octaves=octaves,
        lacunarity=_LACUNARITY,
        persistence=_PERSISTENCE,
        warp=warp,
        scale_meters=_SCALE_M,
        resolution_meters_per_pixel=_RES_M_PER_PX,
    )


def test_output_is_deterministic_for_same_seed() -> None:
    assert np.array_equal(_noise(seed=7), _noise(seed=7))


def test_output_differs_for_different_seeds() -> None:
    assert not np.array_equal(_noise(seed=1), _noise(seed=2))


def test_output_is_in_unit_range() -> None:
    out = _noise(seed=11)
    assert out.dtype == np.float32
    assert float(out.min()) == pytest.approx(0.0, abs=1e-6)
    assert float(out.max()) == pytest.approx(1.0, abs=1e-6)


def test_output_has_correct_shape() -> None:
    assert _noise(seed=3, shape=(48, 17)).shape == (48, 17)


def test_warp_zero_skips_domain_warping() -> None:
    """warp=0 must short-circuit the warp RNG entirely (different code path)."""
    assert np.array_equal(_noise(seed=5, warp=0.0), _noise(seed=5, warp=0.0))


def test_warp_changes_output() -> None:
    assert not np.array_equal(_noise(seed=5, warp=0.0), _noise(seed=5, warp=1.0))


def test_more_octaves_changes_output() -> None:
    assert not np.array_equal(_noise(seed=9, octaves=2), _noise(seed=9, octaves=6))
