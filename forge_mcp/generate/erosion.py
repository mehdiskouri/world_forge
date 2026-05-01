"""Grid-based hydraulic and thermal erosion passes.

Both passes operate on a 2-D float32 elevation grid and are
fully vectorised — no per-droplet loops, no per-cell Python loops.
That gives us deterministic results without any per-iteration ordering
sensitivity (a known footgun of droplet-based hydraulic models).

Public surface:

* :func:`hydraulic` — sediment transport over many iterations of
  rainfall + downhill flow + evaporation.
* :func:`thermal` — talus-angle relaxation: any slope steeper than the
  configured talus angle bleeds material to its lower neighbours.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray

# Eight Moore-neighbourhood offsets in (dy, dx) order; same order is
# used for the diagonal-aware slope computations below.
_NEIGHBOUR_OFFSETS: Final[tuple[tuple[int, int], ...]] = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)
_DIAGONAL_DISTANCE: Final[float] = float(np.sqrt(2.0))
_NEIGHBOUR_DISTANCES: Final[tuple[float, ...]] = (
    _DIAGONAL_DISTANCE,
    1.0,
    _DIAGONAL_DISTANCE,
    1.0,
    1.0,
    _DIAGONAL_DISTANCE,
    1.0,
    _DIAGONAL_DISTANCE,
)
_SEDIMENT_CAPACITY: Final[float] = 0.05
_FLOW_FRACTION: Final[float] = 0.25
_HYDRAULIC_TRANSPORT_BLEND: Final[float] = 0.01
"""Per-iteration weight given to the slope-weighted neighbour average
in the hydraulic bed update. The constant is intentionally weak: the
hydraulic step's job is to carve drainage and move sediment, not to
low-pass the entire field. Visible spike-laundering is delegated to
:func:`forge_mcp.generate.noise.ridged_multifractal`'s
``smooth_sigma_pixels`` and to thermal erosion's talus relaxation,
both of which give cleaner, scale-aware results without tripping the
edge-replication artefacts that bigger hydraulic blends produce on
small synthetic grids.
"""


def _shift(grid: NDArray[np.float32], dy: int, dx: int) -> NDArray[np.float32]:
    """Return ``grid`` shifted by ``(dy, dx)`` with edge replication.

    Edge replication (as opposed to zero-padding) keeps boundary cells
    from artificially appearing as cliffs, which would otherwise drive
    runaway erosion at the map edges.
    """
    shifted = np.roll(grid, shift=(dy, dx), axis=(0, 1))
    if dy > 0:
        shifted[:dy, :] = grid[:1, :]
    elif dy < 0:
        shifted[dy:, :] = grid[-1:, :]
    if dx > 0:
        shifted[:, :dx] = grid[:, :1]
    elif dx < 0:
        shifted[:, dx:] = grid[:, -1:]
    return shifted


def hydraulic(  # noqa: PLR0913 - hydraulic-erosion params are an irreducible block; collapsing them into a config dataclass would only move the argument list one indirection deeper
    heightmap: NDArray[np.float32],
    *,
    iterations: int,
    rain: float,
    evaporation: float,
    rng: np.random.Generator,
    resolution_meters_per_pixel: float = 1.0,
) -> NDArray[np.float32]:
    """Run ``iterations`` of vectorised hydraulic erosion.

    ``resolution_meters_per_pixel`` makes the per-iteration neighbour
    distances physical (metres) rather than dimensionless grid units,
    so slope is computed in metres-per-metre and the threshold-bearing
    physics matches the elevation field's units. Defaults to ``1.0``
    for legacy callers / unit tests; the orchestrator threads the spec
    value through.

    Each iteration:

    1. Add ``rain`` units of water to every cell, plus a tiny RNG-jitter
       (≤1% of rain) so identical-elevation flat regions still develop
       drainage rather than holding water indefinitely.
    2. For each Moore neighbour, compute the head difference (water
       surface elevation gap). Water + sediment flows toward lower
       neighbours in proportion to the head difference, capped at
       ``_FLOW_FRACTION`` of the cell's water column to keep the
       explicit scheme stable.
    3. Sediment carrying capacity is proportional to flow speed; excess
       sediment deposits, deficit erodes the bed.
    4. Evaporate ``evaporation`` fraction of the remaining water.
    """
    bed = heightmap.astype(np.float32, copy=True)
    water = np.zeros_like(bed)
    sediment = np.zeros_like(bed)
    res_m = float(resolution_meters_per_pixel)

    rain_jitter_scale = rain * 0.01
    for _ in range(iterations):
        jitter = rng.uniform(0.0, rain_jitter_scale, size=bed.shape).astype(np.float32)
        water = water + np.float32(rain) + jitter

        head = bed + water
        outflow = np.zeros_like(bed)
        weighted_neighbour_bed = np.zeros_like(bed)
        for (dy, dx), distance in zip(_NEIGHBOUR_OFFSETS, _NEIGHBOUR_DISTANCES, strict=True):
            neighbour_head = _shift(head, -dy, -dx)
            diff = np.maximum(head - neighbour_head, 0.0).astype(np.float32, copy=False)
            slope = diff / np.float32(distance * res_m)
            outflow = outflow + slope
            weighted_neighbour_bed = weighted_neighbour_bed + slope * _shift(bed, -dy, -dx)

        flow_amount = np.minimum(water * np.float32(_FLOW_FRACTION), outflow).astype(
            np.float32,
            copy=False,
        )
        # Sediment capacity: more flow → more carrying capacity.
        capacity = (flow_amount * np.float32(_SEDIMENT_CAPACITY)).astype(np.float32, copy=False)
        delta = (capacity - sediment).astype(np.float32, copy=False)
        # Positive delta erodes the bed; negative delta deposits.
        bed = bed - delta
        sediment = sediment + delta

        # Move water + sediment toward the slope-weighted average lower
        # neighbour bed (a coarse but stable surrogate for explicit
        # neighbour transport).
        outflow_safe = np.where(outflow > 0.0, outflow, np.float32(1.0))
        avg_destination = weighted_neighbour_bed / outflow_safe
        # Bias bed toward the destination — produces the smoothing /
        # transport effect without per-pair bookkeeping. The blend
        # weight (``_HYDRAULIC_TRANSPORT_BLEND``) is calibrated to
        # noticeably soften terrain over realistic iteration counts
        # (30-90) without overshooting on the spike test.
        keep = np.float32(1.0 - _HYDRAULIC_TRANSPORT_BLEND)
        bed = bed * keep + avg_destination * np.float32(_HYDRAULIC_TRANSPORT_BLEND)

        water = water - flow_amount
        water = water * np.float32(1.0 - evaporation)

    return bed.astype(np.float32, copy=False)


def thermal(
    heightmap: NDArray[np.float32],
    *,
    iterations: int,
    talus_angle_degrees: float,
    rng: np.random.Generator,  # noqa: ARG001 - reserved for future stochastic talus jitter; pinned for stability
    resolution_meters_per_pixel: float = 1.0,
) -> NDArray[np.float32]:
    """Run ``iterations`` of vectorised thermal-relaxation erosion.

    For each cell, look at the eight Moore neighbours. Where the slope
    (height difference per unit physical distance) exceeds the talus
    angle, half the excess height is moved to the lower neighbour.
    Vectorised over all eight offsets per iteration.

    ``resolution_meters_per_pixel`` makes the talus comparison
    physical: the elevation field is in metres, so the neighbour
    distance must also be in metres for the slope ``rise/run`` to be
    dimensionless and directly comparable to ``tan(talus_angle)``.
    Defaults to ``1.0`` so legacy callers and unit tests get the old
    "talus-per-grid-cell" semantics.
    """
    talus_slope = float(np.tan(np.deg2rad(talus_angle_degrees)))
    res_m = float(resolution_meters_per_pixel)
    bed = heightmap.astype(np.float32, copy=True)
    # Per-iteration transfer fraction. Splitting half the excess
    # independently across all 8 Moore neighbours over-relaxes (each
    # offset competes with the others), so divide by the neighbour
    # count to keep the explicit scheme stable for arbitrary spike
    # inputs while preserving total mass exactly.
    transfer_fraction = np.float32(0.5 / len(_NEIGHBOUR_OFFSETS))
    for _ in range(iterations):
        delta = np.zeros_like(bed)
        for (dy, dx), distance in zip(_NEIGHBOUR_OFFSETS, _NEIGHBOUR_DISTANCES, strict=True):
            neighbour = _shift(bed, -dy, -dx)
            diff = bed - neighbour
            excess = diff - np.float32(talus_slope * distance * res_m)
            transferable = np.where(
                excess > 0.0, excess * transfer_fraction, np.float32(0.0)
            ).astype(
                np.float32,
                copy=False,
            )
            delta = delta - transferable + _shift(transferable, dy, dx)
        bed = (bed + delta).astype(np.float32, copy=False)
    return bed


__all__ = ["hydraulic", "thermal"]
