"""Terrain analysis: heightmap + stream -> structured statistics.

Outputs a :class:`TerrainAnalysis` Pydantic model. The same call site
serves two consumers:

* ``forge.analyze_region`` returns it as the agent's perception
  payload;
* ``forge.generate_region`` projects its compact subset onto
  :class:`forge_mcp.project.schemas.SpecSummary` so the persisted spec
  carries the headline statistics inline.

All math is pure numpy; no IO; no LLM. Slope and aspect are derived
from sobel-filtered gradients of the heightmap so the analysis matches
exactly what a renderer would shade.
"""

from __future__ import annotations

from itertools import pairwise
from typing import TYPE_CHECKING, ClassVar, Final

import numpy as np
from pydantic import BaseModel, ConfigDict
from scipy.ndimage import sobel

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from forge_mcp.generate.heightmap import Heightmap
    from forge_mcp.generate.stream import StreamGeometry

_ASPECT_BIN_COUNT: Final[int] = 8
# Bins cover N, NE, E, SE, S, SW, W, NW in compass-degree order.
_ASPECT_BIN_WIDTH: Final[float] = 360.0 / _ASPECT_BIN_COUNT
_ASPECT_HALF_WIDTH: Final[float] = _ASPECT_BIN_WIDTH / 2.0
# Cells flatter than this are excluded from the aspect histogram —
# their direction is ill-defined and would just inject noise.
_FLAT_SLOPE_THRESHOLD_DEGREES: Final[float] = 0.5


class ElevationStats(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Summary statistics over the heightmap's elevation values (meters)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    mean: float
    std: float
    min: float
    max: float
    p05: float
    p50: float
    p95: float


class SlopeStats(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Summary statistics over per-pixel slope (degrees)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    mean: float
    p50: float
    p95: float
    max: float


class StreamSummary(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Path-length and gradient stats for the realised stream."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    length_meters: float
    mean_gradient_degrees: float
    anchor_in: tuple[float, float]
    anchor_out: tuple[float, float]


class TerrainAnalysis(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Full structured analysis of a generated terrain region."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    elevation: ElevationStats
    slope_degrees: SlopeStats
    aspect_distribution: tuple[float, ...]
    stream: StreamSummary | None


def _elevation_stats(data: NDArray[np.float32]) -> ElevationStats:
    flat = data.reshape(-1)
    return ElevationStats(
        mean=float(np.mean(flat)),
        std=float(np.std(flat)),
        min=float(np.min(flat)),
        max=float(np.max(flat)),
        p05=float(np.percentile(flat, 5)),
        p50=float(np.percentile(flat, 50)),
        p95=float(np.percentile(flat, 95)),
    )


def _slope_and_aspect(
    data: NDArray[np.float32],
    resolution_meters_per_pixel: float,
) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    """Return ``(slope_degrees, aspect_degrees)`` per pixel.

    Sobel-filtered gradients give a smoother estimate than naive
    finite-differences. Aspect is the compass bearing of the *downhill*
    direction (0° = north, increasing clockwise), wrapped to ``[0, 360)``.
    """
    # scipy's sobel divides by 8 internally; remaining factor accounts
    # for the meters-per-pixel scale so dy/dx are in meters/meters.
    dz_dy = (sobel(data, axis=0) / (8.0 * resolution_meters_per_pixel)).astype(
        np.float32, copy=False
    )
    dz_dx = (sobel(data, axis=1) / (8.0 * resolution_meters_per_pixel)).astype(
        np.float32, copy=False
    )
    slope_radians = np.arctan(np.hypot(dz_dy, dz_dx)).astype(np.float32, copy=False)
    slope_degrees = np.degrees(slope_radians).astype(np.float32, copy=False)
    # Downhill direction: the negative gradient. atan2(dx, dy) gives a
    # compass bearing where 0 = +y (north) and 90 = +x (east).
    aspect_radians = np.arctan2(-dz_dx, -dz_dy).astype(np.float32, copy=False)
    aspect_degrees = np.mod(np.degrees(aspect_radians), 360.0).astype(np.float32, copy=False)
    return slope_degrees, aspect_degrees


def _slope_stats(slope_degrees: NDArray[np.float32]) -> SlopeStats:
    flat = slope_degrees.reshape(-1)
    return SlopeStats(
        mean=float(np.mean(flat)),
        p50=float(np.percentile(flat, 50)),
        p95=float(np.percentile(flat, 95)),
        max=float(np.max(flat)),
    )


def _aspect_distribution(
    aspect_degrees: NDArray[np.float32],
    slope_degrees: NDArray[np.float32],
) -> tuple[float, ...]:
    """Return the 8-bin compass histogram of aspects, normalised to sum 1."""
    mask = slope_degrees > _FLAT_SLOPE_THRESHOLD_DEGREES
    if not bool(mask.any()):
        return tuple([1.0 / _ASPECT_BIN_COUNT] * _ASPECT_BIN_COUNT)
    valid = aspect_degrees[mask]
    # Shift so that bin 0 is centred on 0° (north).
    shifted = np.mod(valid + _ASPECT_HALF_WIDTH, 360.0)
    counts, _ = np.histogram(
        shifted,
        bins=_ASPECT_BIN_COUNT,
        range=(0.0, 360.0),
    )
    total = float(counts.sum())
    if total == 0.0:  # pragma: no cover  # defensive: mask guarantees > 0
        return tuple([1.0 / _ASPECT_BIN_COUNT] * _ASPECT_BIN_COUNT)
    return tuple(float(c) / total for c in counts)


def _stream_summary(
    stream: StreamGeometry,
    heightmap: Heightmap,
) -> StreamSummary:
    """Compute stream length and mean gradient from the geometry.

    Length is the sum of segment lengths in world meters. Mean gradient
    is the average per-segment elevation drop divided by the segment
    horizontal length, converted to degrees. Reads elevation from the
    *carved* heightmap — the actual channel surface — which is the
    intuitively right thing for a "stream gradient".
    """
    path = stream.path
    _MIN_PATH_POINTS = 2  # noqa: N806 - module constant inlined for locality
    if len(path) < _MIN_PATH_POINTS:  # pragma: no cover  # always >=2 (anchors)
        return StreamSummary(
            length_meters=0.0,
            mean_gradient_degrees=0.0,
            anchor_in=stream.anchor_in,
            anchor_out=stream.anchor_out,
        )
    res = heightmap.resolution_meters_per_pixel
    ox, oy = heightmap.origin
    total_length = 0.0
    total_gradient_radians = 0.0
    segment_count = 0
    height, width = heightmap.data.shape
    for (x0, y0), (x1, y1) in pairwise(path):
        dx = x1 - x0
        dy = y1 - y0
        segment_length = float(np.hypot(dx, dy))
        if segment_length == 0.0:
            continue
        col0 = max(0, min(width - 1, round((x0 - ox) / res)))
        row0 = max(0, min(height - 1, round((y0 - oy) / res)))
        col1 = max(0, min(width - 1, round((x1 - ox) / res)))
        row1 = max(0, min(height - 1, round((y1 - oy) / res)))
        elev_drop = float(heightmap.data[row0, col0] - heightmap.data[row1, col1])
        total_length += segment_length
        total_gradient_radians += float(np.arctan(elev_drop / segment_length))
        segment_count += 1
    mean_gradient_degrees = (
        float(np.degrees(total_gradient_radians / segment_count)) if segment_count else 0.0
    )
    return StreamSummary(
        length_meters=total_length,
        mean_gradient_degrees=mean_gradient_degrees,
        anchor_in=stream.anchor_in,
        anchor_out=stream.anchor_out,
    )


def analyze(
    heightmap: Heightmap,
    stream: StreamGeometry | None,
) -> TerrainAnalysis:
    """Compute the full :class:`TerrainAnalysis` for ``heightmap``.

    Pure: no IO, no RNG, deterministic given the inputs.
    """
    elevation = _elevation_stats(heightmap.data)
    slope_degrees, aspect_degrees = _slope_and_aspect(
        heightmap.data,
        heightmap.resolution_meters_per_pixel,
    )
    slope = _slope_stats(slope_degrees)
    aspect_distribution = _aspect_distribution(aspect_degrees, slope_degrees)
    stream_summary = _stream_summary(stream, heightmap) if stream is not None else None
    return TerrainAnalysis(
        elevation=elevation,
        slope_degrees=slope,
        aspect_distribution=aspect_distribution,
        stream=stream_summary,
    )


__all__ = [
    "ElevationStats",
    "SlopeStats",
    "StreamSummary",
    "TerrainAnalysis",
    "analyze",
]
