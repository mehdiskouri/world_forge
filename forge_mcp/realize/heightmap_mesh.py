"""Convert a :class:`Heightmap` into ``(vertices, faces)`` for the realizer.

The curated ``create_terrain_from_heightmap`` macro takes raw vertex /
face arrays and feeds them to ``mesh.from_pydata``. To keep the macro
itself stateless and Blender-agnostic, the mesh tessellation lives
host-side in this module.

Each pixel of the (subsampled) heightmap becomes one vertex; horizontally
adjacent pixels are joined into quad faces (two-triangle quads emitted as
4-tuple faces — Blender accepts polygon faces directly through
``mesh.from_pydata``). World coordinates are derived from the
heightmap's ``origin`` + ``resolution_meters_per_pixel`` so the mesh
lands in the same world frame the analyzer / planner already use.

Per the Phase-4 plan (Confirmed Decision #2), the mesh is clamped to
:data:`MAX_RESOLUTION` (256x256) regardless of source resolution; the
high-frequency detail is supposed to come from a displacement modifier
fed by the full-res 16-bit PNG. Without the cap a 1024x1024 heightmap
would emit a million vertices, which blows the NF-1.3 60 s budget on
``mesh.from_pydata`` alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import numpy as np

if TYPE_CHECKING:
    from forge_mcp.generate.heightmap import Heightmap


MAX_RESOLUTION: Final[int] = 256
"""Per-axis vertex cap used by :func:`mesh_from_heightmap`."""

_MIN_DIM: Final[int] = 2


def _subsample_indices(length: int, max_length: int) -> np.ndarray[tuple[int], np.dtype[np.intp]]:
    """Return integer indices that pick at most ``max_length`` evenly-spaced rows / columns.

    The first and last index are always included so the mesh keeps the
    heightmap's outer extent. Nearest-neighbour subsampling is preferred
    over bilinear so vertex elevations stay byte-identical to the source
    grid (determinism property the realization-trace relies on).
    """
    if length <= max_length:
        return np.arange(length, dtype=np.intp)
    return np.linspace(0, length - 1, num=max_length, dtype=np.intp)


def mesh_from_heightmap(
    heightmap: Heightmap,
    *,
    max_resolution: int = MAX_RESOLUTION,
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int, int]]]:
    """Return ``(vertices, faces)`` for ``mesh.from_pydata``.

    Vertices are emitted in row-major order: index ``y * W + x`` is the
    pixel at column ``x``, row ``y`` of the (possibly subsampled) grid.
    Faces are quads ``(top-left, top-right, bottom-right, bottom-left)``
    with consistent winding so Blender computes upward-facing normals.

    Args:
        heightmap: Source elevation grid.
        max_resolution: Per-axis vertex cap. Heightmaps larger than
            ``max_resolution`` along either axis are subsampled with
            nearest-neighbour to fit; defaults to :data:`MAX_RESOLUTION`.

    Returns:
        Vertex / face lists ready to ship as JSON to the adapter.

    Raises:
        ValueError: If the heightmap is smaller than ``2x2`` or
            ``max_resolution`` is below ``2``.
    """
    h, w = heightmap.shape
    if h < _MIN_DIM or w < _MIN_DIM:
        msg = f"heightmap must be at least 2x2 to tessellate, got {h}x{w}"
        raise ValueError(msg)
    if max_resolution < _MIN_DIM:
        msg = f"max_resolution must be >= 2, got {max_resolution}"
        raise ValueError(msg)

    res = float(heightmap.resolution_meters_per_pixel)
    ox, oy = heightmap.origin
    data = np.asarray(heightmap.data, dtype=np.float32)

    row_idx = _subsample_indices(h, max_resolution)
    col_idx = _subsample_indices(w, max_resolution)
    sampled = data[np.ix_(row_idx, col_idx)]
    sh, sw = sampled.shape

    xs = ox + col_idx.astype(np.float64) * res
    ys = oy + row_idx.astype(np.float64) * res
    grid_x, grid_y = np.meshgrid(xs, ys)
    vertices: list[tuple[float, float, float]] = [
        (float(grid_x[y, x]), float(grid_y[y, x]), float(sampled[y, x]))
        for y in range(sh)
        for x in range(sw)
    ]

    faces: list[tuple[int, int, int, int]] = []
    for y in range(sh - 1):
        row0 = y * sw
        row1 = row0 + sw
        for x in range(sw - 1):
            tl = row0 + x
            tr = tl + 1
            bl = row1 + x
            br = bl + 1
            faces.append((tl, tr, br, bl))
    return vertices, faces


__all__ = ["MAX_RESOLUTION", "mesh_from_heightmap"]
