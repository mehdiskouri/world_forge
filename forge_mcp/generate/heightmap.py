"""Canonical heightmap container + atomic NPY/PNG persistence.

A :class:`Heightmap` carries the elevation grid along with the
geo-referencing metadata required to interpret it: pixel resolution
(meters per pixel), world origin (meters), and the elevation band
(meters above sea level) the grid maps into.

Two on-disk formats:

* ``.npy`` — float32 numpy dump, the lossless source of truth. Written
  through :func:`forge_mcp._io.atomic.atomic_write_bytes` so a crash
  mid-write leaves either the previous file intact or no file at all.
  Sidecar ``.json`` carries the geo-referencing metadata.
* ``.png`` — 16-bit single-channel PNG, scaled to the heightmap's value
  range. Lossy by design — used for Blender displacement and the agent
  preview channel; never as the source of truth.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

import numpy as np
from PIL import Image

from forge_mcp._io.atomic import atomic_write_bytes, atomic_write_text

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray

_PNG_UINT16_MAX: Final[int] = 65535
_NPY_HEADER_MAGIC: Final[bytes] = b"\x93NUMPY"


@dataclass(frozen=True, slots=True)
class Heightmap:
    """Immutable elevation grid with geo-referencing metadata.

    ``data`` is shape ``(H, W)`` float32, in meters above sea level —
    the values themselves already live inside ``elevation_band``.
    ``origin`` is the world-coordinate (meters) of the ``(0, 0)`` pixel
    corner; ``resolution_meters_per_pixel`` lets callers convert any
    pixel coordinate to world meters without consulting the spec.
    """

    data: NDArray[np.float32]
    resolution_meters_per_pixel: float
    origin: tuple[float, float]
    elevation_band: tuple[float, float]

    @property
    def shape(self) -> tuple[int, int]:
        """Return ``(height, width)`` in pixels."""
        h, w = self.data.shape
        return (int(h), int(w))


def _sidecar_path(npy_path: Path) -> Path:
    return npy_path.with_suffix(npy_path.suffix + ".meta.json")


def save_npy(hm: Heightmap, path: Path) -> None:
    """Atomically write ``hm`` as a float32 ``.npy`` plus JSON sidecar.

    The sidecar carries ``resolution_meters_per_pixel``, ``origin``, and
    ``elevation_band`` — everything :func:`load_npy` needs to fully
    reconstruct the :class:`Heightmap`. Both writes are atomic; on a
    crash between them the sidecar is missing and ``load_npy`` raises a
    clean :class:`FileNotFoundError` rather than returning a half-built
    object.
    """
    buffer = io.BytesIO()
    np.save(buffer, hm.data.astype(np.float32, copy=False), allow_pickle=False)
    atomic_write_bytes(path, buffer.getvalue())
    sidecar = {
        "resolution_meters_per_pixel": hm.resolution_meters_per_pixel,
        "origin": list(hm.origin),
        "elevation_band": list(hm.elevation_band),
    }
    atomic_write_text(_sidecar_path(path), json.dumps(sidecar, indent=2, sort_keys=True) + "\n")


def load_npy(path: Path) -> Heightmap:
    """Inverse of :func:`save_npy`. Raises if the sidecar is missing."""
    raw = np.load(path, allow_pickle=False)
    data = np.asarray(raw, dtype=np.float32)
    sidecar_text = _sidecar_path(path).read_text(encoding="utf-8")
    sidecar = cast("dict[str, object]", json.loads(sidecar_text))
    origin_raw = cast("list[float]", sidecar["origin"])
    band_raw = cast("list[float]", sidecar["elevation_band"])
    return Heightmap(
        data=data,
        resolution_meters_per_pixel=float(cast("float", sidecar["resolution_meters_per_pixel"])),
        origin=(float(origin_raw[0]), float(origin_raw[1])),
        elevation_band=(float(band_raw[0]), float(band_raw[1])),
    )


def save_png16(hm: Heightmap, path: Path) -> None:
    """Atomically write a 16-bit single-channel PNG preview of ``hm``.

    Values are linearly rescaled from the heightmap's value range to
    the full 16-bit dynamic range. Lossy (quantization + range clamp);
    intended only as a preview and as Blender displacement input.
    """
    data = hm.data.astype(np.float32, copy=False)
    lo = float(data.min())
    hi = float(data.max())
    span = hi - lo
    if span <= 0.0:
        scaled = np.zeros_like(data, dtype=np.uint16)
    else:
        normalized = (data - lo) / span
        scaled = (normalized * _PNG_UINT16_MAX).astype(np.uint16)
    # Pillow 13 removes the ``mode=`` kwarg of ``fromarray`` in favour
    # of letting the array's dtype dictate the mode; uint16 already
    # maps unambiguously to ``"I;16"``, so no kwarg is needed.
    image = Image.fromarray(scaled)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    atomic_write_bytes(path, buffer.getvalue())


__all__ = ["Heightmap", "load_npy", "save_npy", "save_png16"]
