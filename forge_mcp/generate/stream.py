"""Deterministic stream carving on a finished heightmap.

Given a :class:`StreamFeatureInjector` spec entry and a heightmap, this
module:

1. Picks deterministic entry/exit anchor points on the map boundary
   (lowest cell on opposite edges) when the spec leaves them unset.
   Phase-6 will replace this with real cross-region anchor reconciliation.
2. Traces a steepest-descent path from entry to exit, with a small
   RNG-derived perpendicular jitter so the channel meanders rather than
   running pixel-perfect down a Perlin gradient line.
3. Carves a Gaussian-falloff channel of the requested width and depth
   into the heightmap.

Returns the modified heightmap plus a :class:`StreamGeometry` Pydantic
model describing the realised path — that geometry is what
:mod:`forge_mcp.analyze.terrain_analysis` later consumes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import numpy as np
from pydantic import BaseModel, ConfigDict

from forge_mcp.generate.deterministic import make_rng
from forge_mcp.generate.heightmap import Heightmap

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from forge_mcp.project.schemas import StreamFeatureInjector

# Steepest-descent uses the 8 Moore neighbours plus "stay" — staying
# allows the path to lurch sideways under jitter without overshooting.
_STEP_OFFSETS: Final[tuple[tuple[int, int], ...]] = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 0),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)
_JITTER_STRENGTH: Final[float] = 0.5  # in heightmap-elevation units
_MAX_PATH_MULTIPLIER: Final[int] = 4  # hard cap = N * (H + W) steps
_GAUSS_FALLOFF_SIGMAS: Final[float] = 2.0  # channel ends at ~2 sigma


class StreamGeometry(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Realised stream path + carving parameters in world coordinates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: tuple[tuple[float, float], ...]
    width_meters: float
    carving_depth: float
    anchor_in: tuple[float, float]
    anchor_out: tuple[float, float]


def _lowest_edge_pixel(
    data: NDArray[np.float32],
    edge: str,
) -> tuple[int, int]:
    """Return the ``(row, col)`` of the lowest cell on the given edge."""
    height, width = data.shape
    if edge == "north":
        col = int(np.argmin(data[0, :]))
        return (0, col)
    if edge == "south":
        col = int(np.argmin(data[-1, :]))
        return (height - 1, col)
    if edge == "west":
        row = int(np.argmin(data[:, 0]))
        return (row, 0)
    # east
    row = int(np.argmin(data[:, -1]))
    return (row, width - 1)


def _pick_anchors(data: NDArray[np.float32]) -> tuple[tuple[int, int], tuple[int, int]]:
    """Pick entry/exit pixels on the lower-elevation pair of edges.

    Of the two opposite edge-pairs (N/S vs W/E), choose the pair whose
    minimum-edge-cell sum is lower — i.e. the pair the water "wants" to
    flow between. Stable and deterministic given the heightmap.
    """
    north = _lowest_edge_pixel(data, "north")
    south = _lowest_edge_pixel(data, "south")
    west = _lowest_edge_pixel(data, "west")
    east = _lowest_edge_pixel(data, "east")
    ns_score = float(data[north]) + float(data[south])
    we_score = float(data[west]) + float(data[east])
    if ns_score <= we_score:
        return (north, south)
    return (west, east)


def _trace_path(
    data: NDArray[np.float32],
    start: tuple[int, int],
    goal: tuple[int, int],
    rng: np.random.Generator,
) -> tuple[tuple[int, int], ...]:
    """Steepest-descent path from ``start`` to ``goal`` with RNG jitter.

    Cap path length at :data:`_MAX_PATH_MULTIPLIER` * ``(H + W)`` so a
    pathological landscape (e.g. a basin trapping the walker) cannot
    spin forever.
    """
    height, width = data.shape
    max_steps = _MAX_PATH_MULTIPLIER * (height + width)
    visited: list[tuple[int, int]] = [start]
    seen: set[tuple[int, int]] = {start}
    current = start
    for _ in range(max_steps):
        if current == goal:
            break
        cy, cx = current
        best_score = float("inf")
        best_step = current
        # Bias score by Manhattan distance to goal so the walker does
        # not get permanently trapped in a local minimum.
        for dy, dx in _STEP_OFFSETS:
            ny, nx = cy + dy, cx + dx
            if not (0 <= ny < height and 0 <= nx < width):
                continue
            if (ny, nx) in seen and (ny, nx) != goal:
                continue
            elevation = float(data[ny, nx])
            jitter = float(rng.uniform(-_JITTER_STRENGTH, _JITTER_STRENGTH))
            distance_to_goal = abs(ny - goal[0]) + abs(nx - goal[1])
            score = elevation + jitter + 0.01 * distance_to_goal
            if score < best_score:
                best_score = score
                best_step = (ny, nx)
        if best_step == current:
            # No progress possible — give up rather than loop.
            break
        current = best_step
        visited.append(current)
        seen.add(current)
    if visited[-1] != goal:
        visited.append(goal)
    return tuple(visited)


def _carve(
    data: NDArray[np.float32],
    path: tuple[tuple[int, int], ...],
    *,
    width_pixels: float,
    carving_depth: float,
) -> NDArray[np.float32]:
    """Carve a Gaussian-falloff channel along ``path`` into ``data``.

    For each pixel, find the closest path point and lower the cell by
    ``carving_depth * exp(-(d/sigma)^2/2)`` where ``sigma = width_pixels / 2``.
    Vectorised over all pixels.
    """
    height, width = data.shape
    # Defensive zero-checks: schema enforces width/depth > 0, but the
    # private helper is called from the analysis path too where a
    # zero-length path is conceivable.
    if not path or width_pixels <= 0.0 or carving_depth <= 0.0:  # pragma: no cover
        return data
    path_array = np.array(path, dtype=np.float32)  # shape (P, 2) as (y, x)
    ys = np.arange(height, dtype=np.float32).reshape(-1, 1, 1)
    xs = np.arange(width, dtype=np.float32).reshape(1, -1, 1)
    dy = ys - path_array[:, 0].reshape(1, 1, -1)
    dx = xs - path_array[:, 1].reshape(1, 1, -1)
    dist_squared = (dy * dy + dx * dx).min(axis=2)
    sigma = max(width_pixels / 2.0, 1e-3)
    falloff = np.exp(-0.5 * dist_squared / (sigma * sigma)).astype(np.float32, copy=False)
    # Mask cells beyond ~2 sigma to keep the carve bounded.
    falloff = np.where(
        dist_squared <= (sigma * _GAUSS_FALLOFF_SIGMAS) ** 2,
        falloff,
        np.float32(0.0),
    ).astype(np.float32, copy=False)
    return (data - falloff * np.float32(carving_depth)).astype(np.float32, copy=False)


def _pixel_to_world(
    pixel: tuple[int, int],
    hm: Heightmap,
) -> tuple[float, float]:
    """Convert a ``(row, col)`` pixel to world ``(x, y)`` meters."""
    ox, oy = hm.origin
    res = hm.resolution_meters_per_pixel
    return (ox + pixel[1] * res, oy + pixel[0] * res)


def inject_stream(
    heightmap: Heightmap,
    injector: StreamFeatureInjector,
    *,
    seed: int,
) -> tuple[Heightmap, StreamGeometry]:
    """Carve ``injector`` into ``heightmap`` and return new ``(hm, geometry)``.

    Pure (no IO). Determinism is guaranteed by routing all randomness
    through :func:`forge_mcp.generate.deterministic.make_rng` with
    ``purpose='stream.path_jitter'``; identical inputs produce
    byte-identical outputs.
    """
    data = heightmap.data
    if injector.anchor_in is not None and injector.anchor_out is not None:
        # Phase-6 will reach this branch once boundary reconciliation
        # plumbs anchor coordinates into the spec; for now Phase-3
        # exclusively exercises the auto-pick branch.
        in_pixel = _world_to_pixel(injector.anchor_in, heightmap)  # pragma: no cover
        out_pixel = _world_to_pixel(injector.anchor_out, heightmap)  # pragma: no cover
    else:
        in_pixel, out_pixel = _pick_anchors(data)

    rng = make_rng(seed, purpose="stream.path_jitter")
    path = _trace_path(data, in_pixel, out_pixel, rng)

    width_pixels = injector.width_meters / heightmap.resolution_meters_per_pixel
    carved = _carve(
        data,
        path,
        width_pixels=width_pixels,
        carving_depth=injector.carving_depth,
    )
    new_hm = Heightmap(
        data=carved,
        resolution_meters_per_pixel=heightmap.resolution_meters_per_pixel,
        origin=heightmap.origin,
        elevation_band=heightmap.elevation_band,
    )
    geometry = StreamGeometry(
        path=tuple(_pixel_to_world(p, heightmap) for p in path),
        width_meters=injector.width_meters,
        carving_depth=injector.carving_depth,
        anchor_in=_pixel_to_world(in_pixel, heightmap),
        anchor_out=_pixel_to_world(out_pixel, heightmap),
    )
    return new_hm, geometry


def _world_to_pixel(
    world: tuple[float, float], hm: Heightmap
) -> tuple[int, int]:  # pragma: no cover
    """Inverse of :func:`_pixel_to_world` — used by the Phase-6 anchor branch."""
    ox, oy = hm.origin
    res = hm.resolution_meters_per_pixel
    height, width = hm.data.shape
    col = round((world[0] - ox) / res)
    row = round((world[1] - oy) / res)
    col = max(0, min(width - 1, col))
    row = max(0, min(height - 1, row))
    return (row, col)


__all__ = ["StreamGeometry", "inject_stream"]
