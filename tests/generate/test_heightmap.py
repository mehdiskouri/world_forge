"""Tests for :mod:`forge_mcp.generate.heightmap` — atomic round-trips."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from forge_mcp.generate.heightmap import Heightmap, load_npy, save_npy, save_png16
from PIL import Image

if TYPE_CHECKING:
    from pathlib import Path


_PNG_UINT16_MAX = 65535


def _sample(shape: tuple[int, int] = (8, 12)) -> Heightmap:
    h, w = shape
    rows = np.linspace(0.0, 1.0, h, dtype=np.float32).reshape(-1, 1)
    cols = np.linspace(0.0, 1.0, w, dtype=np.float32).reshape(1, -1)
    data = (rows + cols) * 50.0
    return Heightmap(
        data=data.astype(np.float32),
        resolution_meters_per_pixel=2.0,
        origin=(100.0, -50.0),
        elevation_band=(0.0, 100.0),
    )


def test_save_load_roundtrip_preserves_data_and_metadata(tmp_path: Path) -> None:
    hm = _sample()
    path = tmp_path / "hm.npy"
    save_npy(hm, path)
    loaded = load_npy(path)
    assert np.array_equal(loaded.data, hm.data)
    assert loaded.resolution_meters_per_pixel == hm.resolution_meters_per_pixel
    assert loaded.origin == hm.origin
    assert loaded.elevation_band == hm.elevation_band


def test_shape_property_returns_height_width() -> None:
    hm = _sample((6, 9))
    assert hm.shape == (6, 9)


def test_save_png16_writes_uint16_png(tmp_path: Path) -> None:
    hm = _sample()
    path = tmp_path / "hm.png"
    save_png16(hm, path)
    image = Image.open(path)
    assert image.mode == "I;16"
    assert image.size == (hm.shape[1], hm.shape[0])
    arr = np.array(image)
    assert arr.dtype == np.uint16
    # Linear top-left=min, bottom-right=max contract.
    assert arr[0, 0] == 0
    assert arr[-1, -1] == _PNG_UINT16_MAX


def test_save_png16_handles_constant_heightmap(tmp_path: Path) -> None:
    hm = Heightmap(
        data=np.full((4, 4), 42.0, dtype=np.float32),
        resolution_meters_per_pixel=1.0,
        origin=(0.0, 0.0),
        elevation_band=(0.0, 100.0),
    )
    path = tmp_path / "flat.png"
    save_png16(hm, path)
    arr = np.array(Image.open(path))
    assert arr.dtype == np.uint16
    assert arr.max() == 0  # span==0 → all zeros
