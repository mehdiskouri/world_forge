r"""Edge-conform pass: blend a heightmap toward a boundary-contract profile.

Phase 6 Stage C. Given an axis-aligned side of the heightmap (north,
south, east, west) and a contract sample sequence along that side,
:func:`apply_edge_contract` blends the heightmap toward the contract
within an inland-falloff band so the seam is :math:`C^1` continuous at
the edge and falls away to pure noise inland via a smoothstep
falloff:

.. math::

    s(u) = 3u^2 - 2u^3,\\quad u \\in [0, 1]

where ``u = 0`` at the inland-falloff distance (no contract influence)
and ``u = 1`` at the edge (full contract).

The contract is parameterized along the edge by a uniform 1D linear
interpolant over ``samples``; for an edge of pixel length ``L`` and a
contract of ``N`` samples, sample ``i`` is anchored at the pixel
index ``i * (L - 1) / (N - 1)``.

Pure NumPy; no I/O; deterministic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

import numpy as np

from forge_mcp.generate.heightmap import Heightmap

if TYPE_CHECKING:
    from numpy.typing import NDArray


EdgeSide = Literal["north", "south", "east", "west"]
"""Heightmap-side discriminator.

* ``"north"`` — first row (``y == 0``).
* ``"south"`` — last row (``y == H - 1``).
* ``"west"`` — first column (``x == 0``).
* ``"east"`` — last column (``x == W - 1``).

Convention matches :class:`forge_mcp.generate.heightmap.Heightmap`'s
``data`` indexing: ``data[y, x]``, with ``y`` increasing downward in
pixel space and the world ``+y`` axis aligned with ``-y`` pixels (the
realizer mirrors before display, see Phase 4 spike notes).
"""

_EDGE_SIDES: Final[tuple[EdgeSide, ...]] = ("north", "south", "east", "west")


def _smoothstep(u: NDArray[np.float32]) -> NDArray[np.float32]:
    """Return ``3u^2 - 2u^3`` clipped to ``[0, 1]``."""
    clipped = np.clip(u, 0.0, 1.0)
    return clipped * clipped * (3.0 - 2.0 * clipped)


def _resample_samples_to_pixels(
    samples: tuple[float, ...],
    pixel_count: int,
) -> NDArray[np.float32]:
    """Linearly interpolate ``samples`` to ``pixel_count`` evenly-spaced points."""
    sample_array = np.asarray(samples, dtype=np.float32)
    if pixel_count <= 0:
        msg = f"pixel_count must be positive, got {pixel_count}"
        raise ValueError(msg)
    if sample_array.size == 0:
        msg = "samples must be non-empty"
        raise ValueError(msg)
    if sample_array.size == 1:
        return np.full(pixel_count, sample_array[0], dtype=np.float32)
    src_positions = np.linspace(0.0, 1.0, sample_array.size, dtype=np.float64)
    dst_positions = np.linspace(0.0, 1.0, pixel_count, dtype=np.float64)
    return np.interp(dst_positions, src_positions, sample_array).astype(np.float32)


def _falloff_axis(
    height: int,
    width: int,
    side: EdgeSide,
    inland_pixels: float,
) -> NDArray[np.float32]:
    """Return an ``(H, W)`` smoothstep mask in ``[0, 1]`` peaking at ``side``.

    ``inland_pixels`` is the falloff distance in pixels (``inland_falloff_m
    / resolution_meters_per_pixel``); pixels at distance ``>=
    inland_pixels`` from the edge get mask value ``0``.
    """
    if inland_pixels <= 0.0:
        return np.zeros((height, width), dtype=np.float32)
    ys = np.arange(height, dtype=np.float32)
    xs = np.arange(width, dtype=np.float32)
    if side == "north":
        depth = ys[:, None]  # (H, 1) broadcast across W
    elif side == "south":
        depth = (height - 1 - ys)[:, None]
    elif side == "west":
        depth = xs[None, :]  # (1, W) broadcast across H
    else:  # east
        depth = (width - 1 - xs)[None, :]
    u = 1.0 - depth / float(inland_pixels)
    return _smoothstep(u.astype(np.float32))


def _contract_target_field(
    height: int,
    width: int,
    side: EdgeSide,
    samples: tuple[float, ...],
) -> NDArray[np.float32]:
    """Return an ``(H, W)`` field where the value at every pixel matches the contract.

    The field is constant along the inland axis (so the blend mask is
    what determines how far the contract reaches inland) and varies
    along the edge axis according to a linear interpolation of
    ``samples``.
    """
    if side in ("north", "south"):
        edge_profile = _resample_samples_to_pixels(samples, width)
        # Broadcast across rows: every row gets the same edge-axis profile.
        return np.broadcast_to(edge_profile[None, :], (height, width)).astype(np.float32, copy=True)
    edge_profile = _resample_samples_to_pixels(samples, height)
    # west / east: broadcast across columns.
    return np.broadcast_to(edge_profile[:, None], (height, width)).astype(np.float32, copy=True)


def apply_edge_contract(
    heightmap: Heightmap,
    *,
    side: EdgeSide,
    samples: tuple[float, ...],
    inland_falloff_m: float,
) -> Heightmap:
    """Return ``heightmap`` blended toward ``samples`` on ``side`` with smoothstep falloff.

    Args:
        heightmap: The heightmap to blend.
        side: Which heightmap side the contract pins.
        samples: Contract sample heights along the edge, world-meters.
        inland_falloff_m: Distance over which the contract influence
            falls to zero, in world meters. Typical value
            ``max(20.0, length_m * 0.05)`` per Phase 6 plan §Stage B.

    Returns:
        A new :class:`Heightmap` with the same metadata but blended
        ``data``. The blend is :math:`H' = (1 - m) H + m T` where
        ``m`` is the smoothstep mask and ``T`` is the contract
        target field.
    """
    if inland_falloff_m <= 0.0:
        msg = f"inland_falloff_m must be positive, got {inland_falloff_m}"
        raise ValueError(msg)
    height, width = heightmap.shape
    inland_pixels = inland_falloff_m / heightmap.resolution_meters_per_pixel
    mask = _falloff_axis(height, width, side, inland_pixels)
    target = _contract_target_field(height, width, side, samples)
    blended = (1.0 - mask) * heightmap.data + mask * target
    return Heightmap(
        data=blended.astype(np.float32, copy=False),
        resolution_meters_per_pixel=heightmap.resolution_meters_per_pixel,
        origin=heightmap.origin,
        elevation_band=heightmap.elevation_band,
    )


def edge_profile(
    heightmap: Heightmap,
    side: EdgeSide,
) -> NDArray[np.float32]:
    """Return the 1D heightmap profile along ``side``.

    Used by post-condition checks: after generation, the realized
    edge profile is compared (via :func:`numpy.interp` resampling)
    against the contract's ``samples`` within ``tolerance_m``.
    """
    if side == "north":
        return heightmap.data[0, :].astype(np.float32, copy=False)
    if side == "south":
        return heightmap.data[-1, :].astype(np.float32, copy=False)
    if side == "west":
        return heightmap.data[:, 0].astype(np.float32, copy=False)
    return heightmap.data[:, -1].astype(np.float32, copy=False)


__all__ = [
    "EdgeSide",
    "apply_edge_contract",
    "edge_profile",
]
