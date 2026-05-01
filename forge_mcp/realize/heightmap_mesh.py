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

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import numpy as np

if TYPE_CHECKING:
    from forge_mcp.generate.heightmap import Heightmap


MAX_RESOLUTION: Final[int] = 256
"""Per-axis vertex cap used by :func:`mesh_from_heightmap`."""

# --- Camera / sun framing constants (Architecture §5.7 v1 macros) ----------
#
# Blender's default camera (location=(0,0,0), rotation=(0,0,0),
# type=PERSP) and sun lamp (rotation=(0,0,0)) are useless for a region
# whose terrain spans hundreds of metres in x/y: the camera lands
# underground and the sun beams straight down with no slant, so the
# render comes out solid grey. The realizer macros therefore set every
# transform explicitly via set_property; the values are derived per-
# region from the heightmap's world extent + elevation band by
# :func:`scene_framing_from_heightmap`.
_ORTHO_FRAME_PADDING: Final[float] = 1.05  # 5% margin around the world bounds
_ORTHO_HEIGHT_CLEARANCE: Final[float] = 100.0  # metres above the highest peak
_PERSPECTIVE_OFFSET_FACTOR: Final[float] = 1.4  # camera distance vs. world span
_PERSPECTIVE_HEIGHT_FACTOR: Final[float] = 1.0  # camera lift vs. world span
_SUN_PITCH_RAD: Final[float] = math.radians(45.0)
_SUN_YAW_RAD: Final[float] = math.radians(-45.0)  # light comes from NW → SE-cam sees lit faces
_SUN_HEIGHT_CLEARANCE: Final[float] = 200.0  # metres above the highest peak


def _euler_xyz_look_at(
    camera: tuple[float, float, float],
    target: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Return Euler XYZ rotation (radians) so a Blender camera looks at a target.

    The camera is at ``camera`` and we want it to look at ``target`` with
    world up = +Z.

    Blender camera default forward is -Z. Decomposing the rotation as
    Rz(θz) · Rx(θx) applied to the default forward gives::

        forward = (-sin(θx)·sin(θz), sin(θx)·cos(θz), -cos(θx))

    so ``θx = arccos(-fz)`` and ``θz = atan2(-fx, fy)``. Roll is left
    at zero; with up = +Z and a non-vertical forward the resulting
    frame keeps the horizon level.
    """
    fx = target[0] - camera[0]
    fy = target[1] - camera[1]
    fz = target[2] - camera[2]
    norm = math.sqrt(fx * fx + fy * fy + fz * fz)
    if norm == 0.0:
        return (0.0, 0.0, 0.0)
    nx, ny, nz = fx / norm, fy / norm, fz / norm
    pitch = math.acos(max(-1.0, min(1.0, -nz)))
    yaw = math.atan2(-nx, ny)
    return (pitch, 0.0, yaw)


@dataclass(frozen=True, slots=True)
class SceneFraming:
    """Derived camera + sun placement for one realised region.

    All Euler rotations are in radians; locations are in world metres.
    """

    ortho_location: tuple[float, float, float]
    ortho_rotation_euler: tuple[float, float, float]
    ortho_scale: float
    perspective_location: tuple[float, float, float]
    perspective_rotation_euler: tuple[float, float, float]
    sun_location: tuple[float, float, float]
    sun_rotation_euler: tuple[float, float, float]


def scene_framing_from_heightmap(heightmap: Heightmap) -> SceneFraming:
    """Compute camera + sun placement framing the heightmap's world extent.

    The ortho camera looks straight down from above the highest peak
    with an ``ortho_scale`` sized to fit the longer world-axis plus a
    5% margin; the perspective camera sits to the south-east at
    ``span * 1.1`` away from the centre and looks back at the centre;
    the sun comes down from the NW so terrain relief casts visible
    shadows.
    """
    h, w = heightmap.shape
    res = float(heightmap.resolution_meters_per_pixel)
    ox, oy = heightmap.origin
    width_m = max(float(w - 1) * res, res)
    height_m = max(float(h - 1) * res, res)
    cx = float(ox) + width_m / 2.0
    cy = float(oy) + height_m / 2.0
    # ``mesh_from_heightmap`` emits vertices with ``z = data_meters``
    # already inside the elevation band, and the realizer's DISPLACE
    # modifier is currently configured with ``strength = 0`` (see
    # ``forge_mcp.server.tools.generation._run_realizer``) so the final
    # mesh's vertical extent matches the heightmap's elevation band.
    elev_lo, elev_hi = float(heightmap.elevation_band[0]), float(heightmap.elevation_band[1])
    mesh_top = elev_hi
    mesh_bottom = elev_lo
    mesh_center_z = (mesh_top + mesh_bottom) / 2.0
    xy_span = max(width_m, height_m)
    target = (cx, cy, mesh_center_z)
    # Place the perspective camera SE of the centre at a horizontal
    # distance proportional to the XY span (so the terrain fills the
    # frame at Blender's default 50 mm / 36 mm sensor) and lift it just
    # above the displaced peak so the line of sight clears the top.
    persp_loc = (
        cx + xy_span * _PERSPECTIVE_OFFSET_FACTOR,
        cy - xy_span * _PERSPECTIVE_OFFSET_FACTOR,
        mesh_top + xy_span * _PERSPECTIVE_HEIGHT_FACTOR,
    )
    return SceneFraming(
        ortho_location=(cx, cy, mesh_top + _ORTHO_HEIGHT_CLEARANCE + xy_span * 0.5),
        ortho_rotation_euler=(0.0, 0.0, 0.0),
        ortho_scale=xy_span * _ORTHO_FRAME_PADDING,
        perspective_location=persp_loc,
        perspective_rotation_euler=_euler_xyz_look_at(persp_loc, target),
        sun_location=(cx, cy, mesh_top + _SUN_HEIGHT_CLEARANCE),
        sun_rotation_euler=(_SUN_PITCH_RAD, 0.0, _SUN_YAW_RAD),
    )


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


__all__ = ["MAX_RESOLUTION", "SceneFraming", "mesh_from_heightmap", "scene_framing_from_heightmap"]
