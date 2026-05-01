"""Unit tests for the heightmap-to-mesh helper."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest
from forge_mcp.generate.heightmap import Heightmap
from forge_mcp.realize.heightmap_mesh import mesh_from_heightmap

if TYPE_CHECKING:
    from numpy.typing import NDArray


def _hm(
    data: NDArray[np.float32],
    res: float = 2.0,
    origin: tuple[float, float] = (10.0, 20.0),
) -> Heightmap:
    return Heightmap(
        data=data.astype(np.float32),
        resolution_meters_per_pixel=res,
        origin=origin,
        elevation_band=(0.0, 1.0),
    )


_EXPECTED_VERTS_3X4 = 12
_EXPECTED_FACES_3X4 = 6


def test_mesh_from_heightmap_emits_one_vertex_per_pixel() -> None:
    data = np.zeros((3, 4), dtype=np.float32)
    vertices, faces = mesh_from_heightmap(_hm(data))
    assert len(vertices) == _EXPECTED_VERTS_3X4
    # 2 row-strips * 3 column-strips = 6 quads
    assert len(faces) == _EXPECTED_FACES_3X4


def test_mesh_from_heightmap_world_coords_track_origin_and_resolution() -> None:
    data = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    vertices, _ = mesh_from_heightmap(_hm(data, res=2.5, origin=(100.0, 200.0)))
    # row 0 col 0
    assert vertices[0] == (100.0, 200.0, 1.0)
    # row 0 col 1
    assert vertices[1] == (102.5, 200.0, 2.0)
    # row 1 col 0
    assert vertices[2] == (100.0, 202.5, 3.0)
    # row 1 col 1
    assert vertices[3] == (102.5, 202.5, 4.0)


def test_mesh_from_heightmap_face_winding_is_quad_indices() -> None:
    data = np.zeros((2, 2), dtype=np.float32)
    _, faces = mesh_from_heightmap(_hm(data))
    assert faces == [(0, 1, 3, 2)]


def test_mesh_from_heightmap_rejects_too_small_grid() -> None:
    data = np.zeros((1, 5), dtype=np.float32)
    with pytest.raises(ValueError, match="at least 2x2"):
        mesh_from_heightmap(_hm(data))


def test_mesh_from_heightmap_rejects_invalid_max_resolution() -> None:
    data = np.zeros((4, 4), dtype=np.float32)
    with pytest.raises(ValueError, match="max_resolution must be >= 2"):
        mesh_from_heightmap(_hm(data), max_resolution=1)


_SMALL_CAP = 4
_EXPECTED_VERTS_CAPPED = _SMALL_CAP * _SMALL_CAP
_EXPECTED_FACES_CAPPED = (_SMALL_CAP - 1) * (_SMALL_CAP - 1)


def test_mesh_from_heightmap_subsamples_when_above_cap() -> None:
    # 8x8 source, cap 4 -> 4x4 mesh of 16 vertices and 9 quads.
    data = np.arange(64, dtype=np.float32).reshape((8, 8))
    vertices, faces = mesh_from_heightmap(
        _hm(data, res=1.0, origin=(0.0, 0.0)),
        max_resolution=_SMALL_CAP,
    )
    assert len(vertices) == _EXPECTED_VERTS_CAPPED
    assert len(faces) == _EXPECTED_FACES_CAPPED
    # First and last vertex track outer extent of the source grid.
    first_x, first_y, _ = vertices[0]
    last_x, last_y, _ = vertices[-1]
    assert first_x == 0.0
    assert first_y == 0.0
    assert last_x == 7.0  # noqa: PLR2004 - source grid extent
    assert last_y == 7.0  # noqa: PLR2004 - source grid extent
    # Subsampled elevations are byte-identical picks from the source grid.
    assert {round(v[2]) for v in vertices}.issubset(set(range(64)))


def test_mesh_from_heightmap_passes_through_when_below_cap() -> None:
    data = np.zeros((4, 4), dtype=np.float32)
    vertices, faces = mesh_from_heightmap(_hm(data), max_resolution=_SMALL_CAP)
    assert len(vertices) == _EXPECTED_VERTS_CAPPED
    assert len(faces) == _EXPECTED_FACES_CAPPED
