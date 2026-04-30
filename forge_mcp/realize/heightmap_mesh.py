"""Convert a :class:`Heightmap` into ``(vertices, faces)`` for the realizer.

The curated ``create_terrain_from_heightmap`` macro takes raw vertex /
face arrays and feeds them to ``mesh.from_pydata``. To keep the macro
itself stateless and Blender-agnostic, the mesh tessellation lives
host-side in this module.

Each pixel of the heightmap becomes one vertex; horizontally adjacent
pixels are joined into quad faces (two-triangle quads emitted as 4-tuple
faces — Blender accepts polygon faces directly through
``mesh.from_pydata``). World coordinates are derived from the
heightmap's ``origin`` + ``resolution_meters_per_pixel`` so the mesh
lands in the same world frame the analyzer / planner already use.

For very large heightmaps callers should pre-decimate; this module does
no LOD of its own — it is a faithful one-vertex-per-pixel converter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from forge_mcp.generate.heightmap import Heightmap


def mesh_from_heightmap(
    heightmap: Heightmap,
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int, int]]]:
    """Return ``(vertices, faces)`` for ``mesh.from_pydata``.

    Vertices are emitted in row-major order: index ``y * W + x`` is the
    pixel at column ``x``, row ``y``. Faces are quads
    ``(top-left, top-right, bottom-right, bottom-left)`` with consistent
    winding so Blender computes upward-facing normals.

    Args:
        heightmap: Source elevation grid.

    Returns:
        Vertex / face lists ready to ship as JSON to the adapter.
    """
    h, w = heightmap.shape
    if h < 2 or w < 2:  # noqa: PLR2004 - 2 is the minimum quad-mesh dimension
        msg = f"heightmap must be at least 2x2 to tessellate, got {h}x{w}"
        raise ValueError(msg)

    res = float(heightmap.resolution_meters_per_pixel)
    ox, oy = heightmap.origin
    data = np.asarray(heightmap.data, dtype=np.float32)

    xs = ox + np.arange(w, dtype=np.float64) * res
    ys = oy + np.arange(h, dtype=np.float64) * res
    grid_x, grid_y = np.meshgrid(xs, ys)
    vertices: list[tuple[float, float, float]] = [
        (float(grid_x[y, x]), float(grid_y[y, x]), float(data[y, x]))
        for y in range(h)
        for x in range(w)
    ]

    faces: list[tuple[int, int, int, int]] = []
    for y in range(h - 1):
        row0 = y * w
        row1 = row0 + w
        for x in range(w - 1):
            tl = row0 + x
            tr = tl + 1
            bl = row1 + x
            br = bl + 1
            faces.append((tl, tr, br, bl))
    return vertices, faces


__all__ = ["mesh_from_heightmap"]
