"""Deterministic ridged-multifractal noise on a 2-D grid.

The terrain generator stacks octaves of a permutation-table Perlin
noise, ridges each octave (``1 - |n|``), and accumulates them with
fractional Brownian motion weights. A second RNG-derived offset map
warps the input domain before sampling, which breaks up the axis-
aligned grid artefacts Perlin is known for and produces the meandering
ridgelines characteristic of mountain terrain.

Public surface:

* :func:`ridged_multifractal` — full pipeline, returns a 2-D float32
  array in ``[0, 1]``.

Determinism is guaranteed by routing all randomness through
:func:`forge_mcp.generate.deterministic.make_rng`; given the same
``(seed, shape, params)`` the function returns byte-identical output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

import numpy as np

from forge_mcp.generate.deterministic import make_rng

if TYPE_CHECKING:
    from numpy.typing import NDArray

_PERM_SIZE: Final[int] = 256
_PERM_MASK: Final[int] = _PERM_SIZE - 1


def _build_perm_table(rng: np.random.Generator) -> NDArray[np.int64]:
    """Return a length-512 permutation table (Perlin's classical setup)."""
    base = np.arange(_PERM_SIZE, dtype=np.int64)
    rng.shuffle(base)
    return np.concatenate([base, base])


def _fade(t: NDArray[np.float32]) -> NDArray[np.float32]:
    """Perlin's quintic fade: ``6t^5 - 15t^4 + 10t^3``."""
    return (t * t * t * (t * (t * 6.0 - 15.0) + 10.0)).astype(np.float32, copy=False)


def _grad(
    hashes: NDArray[np.int64], x: NDArray[np.float32], y: NDArray[np.float32]
) -> NDArray[np.float32]:
    """Dot product with one of 8 unit-ish gradient directions."""
    h = hashes & 7
    # 8 gradients pointing to corners + edges of the unit square.
    grads_x = np.array([1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 0.0, 0.0], dtype=np.float32)
    grads_y = np.array([1.0, 1.0, -1.0, -1.0, 0.0, 0.0, 1.0, -1.0], dtype=np.float32)
    gx = grads_x[h]
    gy = grads_y[h]
    return cast("NDArray[np.float32]", (gx * x + gy * y).astype(np.float32, copy=False))


def _perlin2d(
    perm: NDArray[np.int64],
    x: NDArray[np.float32],
    y: NDArray[np.float32],
) -> NDArray[np.float32]:
    """Vectorised classical 2-D Perlin noise on coordinate arrays."""
    xi = np.floor(x).astype(np.int64) & _PERM_MASK
    yi = np.floor(y).astype(np.int64) & _PERM_MASK
    xf = (x - np.floor(x)).astype(np.float32)
    yf = (y - np.floor(y)).astype(np.float32)

    u = _fade(xf)
    v = _fade(yf)

    aa = perm[perm[xi] + yi]
    ab = perm[perm[xi] + yi + 1]
    ba = perm[perm[xi + 1] + yi]
    bb = perm[perm[xi + 1] + yi + 1]

    n00 = _grad(aa, xf, yf)
    n10 = _grad(ba, xf - 1.0, yf)
    n01 = _grad(ab, xf, yf - 1.0)
    n11 = _grad(bb, xf - 1.0, yf - 1.0)

    nx0 = n00 + u * (n10 - n00)
    nx1 = n01 + u * (n11 - n01)
    return (nx0 + v * (nx1 - nx0)).astype(np.float32, copy=False)


def _coordinate_grid(
    shape: tuple[int, int],
    *,
    scale_meters_per_pixel: float,
    feature_scale_meters: float,
) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    """Return ``(x, y)`` grids in noise-space units.

    ``feature_scale_meters`` controls how many world-meters fit into one
    Perlin lattice cell — larger values give broader features.
    """
    height, width = shape
    step = scale_meters_per_pixel / feature_scale_meters
    ys = (np.arange(height, dtype=np.float32) * step).reshape(-1, 1)
    xs = (np.arange(width, dtype=np.float32) * step).reshape(1, -1)
    x_grid = np.broadcast_to(xs, (height, width)).astype(np.float32, copy=True)
    y_grid = np.broadcast_to(ys, (height, width)).astype(np.float32, copy=True)
    return x_grid, y_grid


def ridged_multifractal(  # noqa: PLR0913 - noise params are an irreducible block; collapsing them into a config dataclass would only move the argument list one indirection deeper
    shape: tuple[int, int],
    *,
    seed: int,
    octaves: int,
    lacunarity: float,
    persistence: float,
    warp: float,
    scale_meters: float,
    resolution_meters_per_pixel: float,
) -> NDArray[np.float32]:
    """Return a ridged-multifractal noise field in ``[0, 1]``.

    Pipeline:

    1. Build a base coordinate grid scaled so one Perlin lattice cell
       spans ``scale_meters`` in world space.
    2. If ``warp > 0``, perturb the grid with a low-frequency Perlin
       offset map keyed on a separate RNG (``purpose="noise.warp"``).
    3. For each octave, sample Perlin at increasing frequency
       (``lacunarity``) and decreasing amplitude (``persistence``),
       ridge it (``1 - |n|``), and accumulate.
    4. Normalise the accumulator into ``[0, 1]`` so downstream erosion
       / elevation-band remapping operates on a known range.
    """
    base_rng = make_rng(seed, purpose="noise.base")
    perm_base = _build_perm_table(base_rng)

    x_grid, y_grid = _coordinate_grid(
        shape,
        scale_meters_per_pixel=resolution_meters_per_pixel,
        feature_scale_meters=scale_meters,
    )

    if warp > 0.0:
        warp_rng = make_rng(seed, purpose="noise.warp")
        perm_warp = _build_perm_table(warp_rng)
        warp_x = _perlin2d(perm_warp, x_grid * 0.5, y_grid * 0.5)
        warp_y = _perlin2d(perm_warp, x_grid * 0.5 + 31.7, y_grid * 0.5 - 17.3)
        x_grid = (x_grid + warp_x * warp).astype(np.float32, copy=False)
        y_grid = (y_grid + warp_y * warp).astype(np.float32, copy=False)

    accumulator = np.zeros(shape, dtype=np.float32)
    amplitude = 1.0
    frequency = 1.0
    weight_sum = 0.0
    for _ in range(octaves):
        sample = _perlin2d(perm_base, x_grid * frequency, y_grid * frequency)
        ridged = (1.0 - np.abs(sample)).astype(np.float32, copy=False)
        accumulator = accumulator + ridged * amplitude
        weight_sum += amplitude
        amplitude *= persistence
        frequency *= lacunarity

    normalised = accumulator / weight_sum
    lo = float(normalised.min())
    hi = float(normalised.max())
    span = hi - lo
    if span <= 0.0:
        return np.zeros(shape, dtype=np.float32)
    return ((normalised - lo) / span).astype(np.float32, copy=False)


__all__ = ["ridged_multifractal"]
