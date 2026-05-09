"""Phase 7 Stage C: feature-lock blending in :mod:`forge_mcp.generate.terrain`."""

from __future__ import annotations

import numpy as np
import pytest
from forge_mcp.generate.heightmap import Heightmap
from forge_mcp.generate.terrain import (
    FeatureLockOutOfBoundsError,
    FeatureLockPatch,
    _apply_feature_lock_patches,
    _cosine_feather_weights,
)


def _make_heightmap(value: float = 1.0, shape: tuple[int, int] = (16, 16)) -> Heightmap:
    return Heightmap(
        data=np.full(shape, value, dtype=np.float32),
        resolution_meters_per_pixel=1.0,
        origin=(0.0, 0.0),
        elevation_band=(0.0, 1000.0),
    )


def test_no_patches_returns_input_heightmap_unchanged() -> None:
    hm = _make_heightmap(1.0)
    out = _apply_feature_lock_patches(hm, ())
    assert out is hm


def test_patch_centre_replaces_destination() -> None:
    """Interior pixels of the patch carry full weight (1.0)."""
    hm = _make_heightmap(0.0, shape=(20, 20))
    patch_data = np.full((10, 10), 5.0, dtype=np.float32)
    patch = FeatureLockPatch(bbox_world=(5.0, 5.0, 15.0, 15.0), data=patch_data)
    out = _apply_feature_lock_patches(hm, (patch,))
    # The 4-pixel feather only touches the outer ring; the 2x2 centre is
    # fully replaced.
    centre = out.data[9:11, 9:11]
    assert np.allclose(centre, 5.0)


def test_patch_edge_pixels_are_feathered() -> None:
    """Boundary pixels mix patch and background per the cosine weight."""
    hm = _make_heightmap(0.0, shape=(20, 20))
    patch_data = np.full((10, 10), 10.0, dtype=np.float32)
    patch = FeatureLockPatch(bbox_world=(5.0, 5.0, 15.0, 15.0), data=patch_data)
    out = _apply_feature_lock_patches(hm, (patch,))
    # Top-left destination corner: row=5, col=5. Weight there is the
    # product of the row[0] and col[0] cosine weights.
    weights = _cosine_feather_weights((10, 10))
    expected_corner = float(weights[0, 0] * 10.0 + (1.0 - weights[0, 0]) * 0.0)
    assert out.data[5, 5] == pytest.approx(expected_corner)
    # Background just outside the patch is untouched.
    assert out.data[4, 4] == 0.0
    assert out.data[15, 15] == 0.0


def test_cosine_weights_are_monotone_from_edge_to_centre() -> None:
    weights = _cosine_feather_weights((20, 20))
    # Pick the row through the centre column; weights must rise then fall.
    line = weights[:, 10]
    rising = line[:10]
    falling = line[10:]
    eps = 1e-7
    assert np.all(np.diff(rising) >= -eps)
    assert np.all(np.diff(falling) <= eps)
    # Interior is exactly 1.0; corners are < 1.
    assert weights[10, 10] == pytest.approx(1.0)
    assert weights[0, 0] < 1.0


def test_out_of_frame_bbox_raises() -> None:
    hm = _make_heightmap(0.0, shape=(8, 8))
    # bbox_world fully to the right of the heightmap (which spans 0..8m).
    patch = FeatureLockPatch(
        bbox_world=(20.0, 20.0, 30.0, 30.0),
        data=np.zeros((10, 10), dtype=np.float32),
    )
    with pytest.raises(FeatureLockOutOfBoundsError):
        _apply_feature_lock_patches(hm, (patch,))


def test_multiple_patches_compose() -> None:
    """Two non-overlapping patches both get blended in."""
    hm = _make_heightmap(0.0, shape=(30, 30))
    p1 = FeatureLockPatch(
        bbox_world=(2.0, 2.0, 12.0, 12.0),
        data=np.full((10, 10), 4.0, dtype=np.float32),
    )
    p2 = FeatureLockPatch(
        bbox_world=(18.0, 18.0, 28.0, 28.0),
        data=np.full((10, 10), 7.0, dtype=np.float32),
    )
    out = _apply_feature_lock_patches(hm, (p1, p2))
    # Patches are 10x10 so the 4-pixel feather leaves a 2x2 unity
    # interior at local (4..5, 4..5).
    assert out.data[6, 6] == pytest.approx(4.0)
    assert out.data[22, 22] == pytest.approx(7.0)
    # The empty corridor between them stays at 0.
    assert out.data[15, 15] == 0.0
