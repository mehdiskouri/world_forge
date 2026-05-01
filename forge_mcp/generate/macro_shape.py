"""Per-archetype macro-shape pre-pass on the unit-range noise field.

The shared :func:`forge_mcp.generate.noise.ridged_multifractal` produces
the same kind of fractal field for every archetype; the
:data:`forge_mcp.project.schemas.MacroShape` pre-pass is what makes a
"valley" actually look U-shaped, a "mesa" terraced, a "canyon" cut by a
narrow chasm, etc. The pre-pass runs *before* the elevation-band
remap, so it operates on a ``[0, 1]``-valued grid and returns one too.

Design contract
---------------
* Pure: given the same ``(field, shape, strength, seed)`` returns
  byte-identical output. Ordering of any RNG draws is fixed.
* Identity at ``strength=0.0`` for every shape (lets the caller switch
  the macro on or off without spec-id churn beyond the weight itself).
* Output is clamped into ``[0, 1]`` so downstream code keeps its
  range invariants.
* Shape selection is a closed :data:`MacroShape` literal. Adding a
  shape requires bumping
  :data:`forge_mcp.descriptor.map_to_spec.COMPILER_VERSION` and
  refreshing every content-addressed spec id in the eval set.

The shape parameter table
-------------------------
Each shape's intrinsic strength comes from
:data:`_SHAPE_PARAMS`; the per-call ``strength`` blends the shape's
target field with the input field (``out = (1 - s) * field + s *
shaped``). This keeps the pre-pass referentially transparent across
all archetypes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, assert_never, cast

import numpy as np

from forge_mcp.generate.deterministic import make_rng

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from forge_mcp.project.schemas import MacroShape

_TERRACE_LEVELS: Final[int] = 5
"""Number of flat plateau levels the ``mesa_terraces`` shape quantises to."""

_VOLCANIC_SUMMIT_FRACTION: Final[float] = 0.95
"""Cone target value at the summit before blending."""

_COASTAL_LAND_FRACTION: Final[float] = 0.55
"""Fraction of the diagonal axis that is "land" in ``coastal_cliff``."""

_DUNES_RIDGE_COUNT: Final[float] = 6.0
"""Sinusoidal ridge count along the wind axis for ``dunes_ridges``."""

_CANYON_WIDTH_FRACTION: Final[float] = 0.07
"""Half-width of the chasm gaussian, as a fraction of grid extent."""

_VALLEY_TROUGH_WIDTH: Final[float] = 0.55
"""Half-width (in normalised grid units) of the alpine valley trough."""

_VALLEY_TROUGH_DEPTH: Final[float] = 0.7
"""Maximum depth (in unit-noise space) the trough subtracts."""

_LOWLAND_TILT_RANGE: Final[float] = 0.15
"""Per-axis tilt added by ``lowland_lowpass`` to suggest a drainage gradient."""


def _normalised_axes(shape: tuple[int, int]) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    """Return ``(yy, xx)`` grids in ``[0, 1]`` over the given shape."""
    height, width = shape
    yy = np.linspace(0.0, 1.0, height, dtype=np.float32).reshape(-1, 1)
    xx = np.linspace(0.0, 1.0, width, dtype=np.float32).reshape(1, -1)
    yy_grid = np.broadcast_to(yy, shape).astype(np.float32, copy=True)
    xx_grid = np.broadcast_to(xx, shape).astype(np.float32, copy=True)
    return yy_grid, xx_grid


def _valley_trough(field: NDArray[np.float32]) -> NDArray[np.float32]:
    """U-shaped trough subtracted along the y-axis midline.

    The y-axis is treated as the "across-valley" direction; a parabolic
    profile from 1 at the centre to 0 at ``±_VALLEY_TROUGH_WIDTH``
    multiplies the field's "depression amount", which is then
    subtracted from the noise. The result reads as a U-valley with
    noise riding on the rim.
    """
    yy, _ = _normalised_axes(field.shape)
    centre = 0.5
    distance = np.abs(yy - centre) / _VALLEY_TROUGH_WIDTH
    parabola = np.maximum(0.0, 1.0 - distance * distance).astype(np.float32, copy=False)
    depression = (parabola * _VALLEY_TROUGH_DEPTH).astype(np.float32, copy=False)
    return cast(
        "NDArray[np.float32]",
        np.clip(field - depression, 0.0, 1.0).astype(np.float32, copy=False),
    )


def _mesa_terraces(field: NDArray[np.float32]) -> NDArray[np.float32]:
    """Quantise the field into ``_TERRACE_LEVELS`` flat plateaus.

    The plateaus are equal-width slices of the unit interval; small
    noise inside one plateau is laundered to that plateau's value,
    producing the flat-topped step look characteristic of mesas. Only
    the plateau quantisation is applied here; talus risers between
    plateaus are produced downstream by thermal erosion's relaxation
    against a steep talus angle.
    """
    quantised = (np.floor(field * _TERRACE_LEVELS) / float(_TERRACE_LEVELS - 1)).astype(
        np.float32, copy=False
    )
    return cast(
        "NDArray[np.float32]",
        np.clip(quantised, 0.0, 1.0).astype(np.float32, copy=False),
    )


def _canyon_chasm(field: NDArray[np.float32]) -> NDArray[np.float32]:
    """Cut a narrow gaussian chasm along the diagonal.

    A 1-D gaussian centred on the line ``x == y`` carves a valley with
    sharp shoulders. Width and depth are tuned so the chasm reads as
    a slot canyon at the eval-set 1024 m world.
    """
    yy, xx = _normalised_axes(field.shape)
    diag_distance = np.abs(yy - xx)
    sigma = _CANYON_WIDTH_FRACTION
    chasm = np.exp(-(diag_distance * diag_distance) / (2.0 * sigma * sigma)).astype(
        np.float32, copy=False
    )
    return cast(
        "NDArray[np.float32]",
        np.clip(field - chasm, 0.0, 1.0).astype(np.float32, copy=False),
    )


def _lowland_lowpass(
    field: NDArray[np.float32],
    rng: np.random.Generator,
) -> NDArray[np.float32]:
    """Apply a subtle tilt to suggest a drainage gradient.

    The macro shape itself does not low-pass — that is the noise
    layer's ``smooth_sigma_pixels`` parameter's job. Here we just add a
    small linear tilt with an RNG-derived axis and sign so meandering
    streams (Phase 3 :mod:`stream`) have a deterministic downhill
    direction to follow. Keeps the field in ``[0, 1]`` after blending.
    """
    yy, xx = _normalised_axes(field.shape)
    angle = float(rng.uniform(0.0, 2.0 * np.pi))
    tilt = (xx * np.cos(angle) + yy * np.sin(angle)).astype(np.float32, copy=False)
    centred = (tilt - 0.5).astype(np.float32, copy=False)
    return cast(
        "NDArray[np.float32]",
        np.clip(field + centred * _LOWLAND_TILT_RANGE, 0.0, 1.0).astype(np.float32, copy=False),
    )


def _dunes_ridges(field: NDArray[np.float32]) -> NDArray[np.float32]:
    """Add a sinusoidal ridge train along the x-axis.

    Wind-blown dunes are characterised by repeating crescent ridges
    perpendicular to the prevailing wind. We approximate this with a
    cosine train along x, which the noise layer's domain warp then
    breaks up into wavy individual dunes.
    """
    _, xx = _normalised_axes(field.shape)
    crest = (0.5 + 0.5 * np.cos(2.0 * np.pi * _DUNES_RIDGE_COUNT * xx)).astype(
        np.float32, copy=False
    )
    return cast(
        "NDArray[np.float32]",
        np.clip(0.5 * field + 0.5 * crest, 0.0, 1.0).astype(np.float32, copy=False),
    )


def _volcanic_cone(field: NDArray[np.float32]) -> NDArray[np.float32]:
    """Single dominant cone centred on the grid.

    A radial linear cone from ``_VOLCANIC_SUMMIT_FRACTION`` at the
    centre to 0 at the corners is blended with the noise; downstream
    erosion shapes the radial drainage.
    """
    yy, xx = _normalised_axes(field.shape)
    radius = np.hypot(yy - 0.5, xx - 0.5).astype(np.float32, copy=False)
    cone = np.clip(
        _VOLCANIC_SUMMIT_FRACTION - radius * (_VOLCANIC_SUMMIT_FRACTION / 0.5),
        0.0,
        1.0,
    ).astype(np.float32, copy=False)
    return cast(
        "NDArray[np.float32]",
        np.clip(0.5 * field + 0.5 * cone, 0.0, 1.0).astype(np.float32, copy=False),
    )


def _coastal_cliff(field: NDArray[np.float32]) -> NDArray[np.float32]:
    """Sharp escarpment dividing low sea-floor from high inland plateau.

    Below ``_COASTAL_LAND_FRACTION`` along the diagonal axis the field
    is laundered toward 0 (sea), above it toward 1 (inland). The
    transition zone is one cell wide so subsequent thermal erosion
    builds the actual cliff face.
    """
    yy, xx = _normalised_axes(field.shape)
    diag = (0.5 * (yy + xx)).astype(np.float32, copy=False)
    sea_mask = diag < _COASTAL_LAND_FRACTION
    inland = field * 0.4 + 0.6
    sea = field * 0.2
    return cast(
        "NDArray[np.float32]",
        np.where(sea_mask, sea, inland).astype(np.float32, copy=False),
    )


def apply_macro_shape(
    field: NDArray[np.float32],
    *,
    shape: MacroShape,
    strength: float,
    seed: int,
) -> NDArray[np.float32]:
    """Blend ``field`` with the per-archetype macro silhouette.

    Args:
        field: Unit-range ``[0, 1]`` noise field.
        shape: Discriminator selecting the per-archetype shape.
        strength: Blend weight in ``[0, 1]``; ``0`` is identity,
            ``1`` is the pure shape.
        seed: Region seed; only consumed by shapes that need
            deterministic randomness (``lowland_lowpass`` picks a tilt
            axis).

    Returns:
        A unit-range float32 field with the same shape as ``field``.
    """
    if strength <= 0.0 or shape == "none":
        return field
    if shape == "valley_trough":
        target = _valley_trough(field)
    elif shape == "mesa_terraces":
        target = _mesa_terraces(field)
    elif shape == "canyon_chasm":
        target = _canyon_chasm(field)
    elif shape == "lowland_lowpass":
        target = _lowland_lowpass(field, make_rng(seed, purpose="macro.lowland_tilt"))
    elif shape == "dunes_ridges":
        target = _dunes_ridges(field)
    elif shape == "volcanic_cone":
        target = _volcanic_cone(field)
    elif shape == "coastal_cliff":
        target = _coastal_cliff(field)
    else:
        assert_never(shape)
    blend = float(strength)
    blended = ((1.0 - blend) * field + blend * target).astype(np.float32, copy=False)
    return cast(
        "NDArray[np.float32]",
        np.clip(blended, 0.0, 1.0).astype(np.float32, copy=False),
    )


__all__ = ["apply_macro_shape"]
