"""Symmetric boundary-contract negotiation.

The single public entry point :func:`negotiate_boundary_contract`
takes a :class:`BoundaryRecord` and the two adjacent regions'
:class:`SpecBody` objects and returns the tuple of contracts both
generators must honor when realizing their heightmaps.

Two contract kinds are produced:

* :class:`ElevationContinuityContract` — always emitted. The contract
  band is the **overlap** of both regions' ``elevation_band``; if the
  bands are disjoint, :class:`BoundaryContractInfeasibleError` is
  raised with structured fields the MCP tool surface translates into
  the ``boundary_contract_infeasible`` error code.

* :class:`StreamCrossingContract` — emitted only when both regions
  have a stream feature injector with **explicit** anchors that
  project onto the shared edge within stream width. Misalignment
  beyond width tolerance or angle tolerance raises
  :class:`BoundaryContractInfeasibleError`. When either region's
  stream injector relies on the Phase-3 deterministic-anchor fallback
  (``anchor_in``/``anchor_out`` ``None``), no stream contract is
  emitted; the streams stay independent (no-contract path).

Determinism:

* Sample heights are drawn from a deterministic LCG seeded off
  ``(region_a, region_b, length_meters)``. Because the boundary
  itself canonicalizes ``region_a < region_b`` (lex-sorted in
  :class:`BoundaryRecord`'s validator), the seed and therefore the
  sample sequence is order-independent.
* All floating-point math here is plain Python; no NumPy, so the
  output is bit-stable across platforms.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

from forge_mcp.project.schemas import (
    ElevationContinuityContract,
    StreamCrossingContract,
    StreamFeatureInjector,
)

if TYPE_CHECKING:
    from forge_mcp.project.schemas import (
        AnchorPoint,
        BoundaryContract,
        BoundaryRecord,
        FeatureInjector,
        RegionId,
        SpecBody,
    )


# Tunables
DEFAULT_SAMPLE_SPACING_M: Final[float] = 64.0
"""Default along-edge sample spacing. Picked so a 1 km edge yields ~16 samples."""

MIN_SAMPLES: Final[int] = 8
"""Minimum number of edge samples regardless of length / spacing."""

TOLERANCE_BAND_FRACTION: Final[float] = 0.05
"""Per-sample tolerance as a fraction of the contract band height."""

TOLERANCE_BAND_MAX_M: Final[float] = 2.0
"""Cap on the per-sample tolerance, in meters (Phase 6 plan §Stage B)."""

# Stream-crossing tolerances (Phase 6 confirmed-decisions block)
_STREAM_ANCHOR_EDGE_TOLERANCE_M: Final[float] = 1.0
"""Distance from the shared edge under which an anchor counts as 'on' the edge."""

_STREAM_WIDTH_RATIO_LIMIT: Final[float] = 2.0
"""Maximum permitted ratio of stream widths at a crossing (max/min)."""

_STREAM_ANGLE_TOLERANCE_DEG: Final[float] = 30.0
"""Maximum permitted flow-direction angle deviation across the crossing, in degrees."""

# LCG constants (numerical recipes 32-bit). Symmetric over endpoint order
# because the seed itself is computed from a sorted-pair hash.
_LCG_MULT: Final[int] = 1664525
_LCG_INC: Final[int] = 1013904223
_LCG_MOD: Final[int] = 2**32


class BoundaryContractInfeasibleError(ValueError):
    """Raised when two regions' specs cannot share a coherent boundary contract.

    Surfaced through the MCP tool envelope as
    ``fail("boundary_contract_infeasible", str(exc), details={...})``.

    Attributes:
        reason: Short machine-readable tag for the failure mode (e.g.
            ``"elevation_bands_disjoint"`` or
            ``"stream_crossing_misaligned"``).
        boundary_id: The boundary the contract was being negotiated
            for.
        region_a: First region of the boundary (lex-sorted).
        region_b: Second region of the boundary.
        details: Free-form dict carrying numbers relevant to the
            specific reason (band endpoints, anchor coordinates, width
            ratio, etc.).
    """

    def __init__(
        self,
        *,
        reason: str,
        boundary_id: str,
        region_a: str,
        region_b: str,
        details: dict[str, object],
    ) -> None:
        """Store structured fields and synthesize the message."""
        super().__init__(
            f"boundary {boundary_id!r} ({region_a} <-> {region_b}) infeasible: {reason}"
        )
        self.reason = reason
        self.boundary_id = boundary_id
        self.region_a = region_a
        self.region_b = region_b
        self.details = details


class BoundaryContractConflictError(ValueError):
    """Raised by Stage C when two contracts on the same region conflict.

    Stage B exposes the type so callers can catch it; the actual
    conflict-detection sites (lock-vs-contract, contract-vs-contract
    overlap) ship in Stage C.

    Attributes:
        reason: Short tag, e.g. ``"lock_overlaps_edge_band"`` or
            ``"contract_overlap_violates_tolerance"``.
        details: Free-form dict carrying the offending ids.
    """

    def __init__(self, *, reason: str, details: dict[str, object]) -> None:
        """Store structured fields and synthesize the message."""
        super().__init__(f"boundary contract conflict: {reason}")
        self.reason = reason
        self.details = details


def _seed_from_boundary(
    region_a: RegionId,
    region_b: RegionId,
    length_meters: float,
) -> int:
    """Return a deterministic 32-bit LCG seed for ``(region_a, region_b, length_m)``.

    Uses a SHA-256 digest of the canonical-sorted endpoint pair plus
    the length quantized to millimeters so different lengths give
    different sample sequences but the same length always gives the
    same sequence. Insensitive to endpoint order because the boundary
    itself enforces ``region_a < region_b``.
    """
    import hashlib  # noqa: PLC0415 - keep import local to the seed helper

    # Quantize to mm so float reprs don't perturb the seed across platforms.
    length_mm = round(length_meters * 1000.0)
    payload = f"{region_a}\x00{region_b}\x00{length_mm}".encode()
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:4], "big")


def _lcg_unit(state: int) -> tuple[int, float]:
    """Advance the LCG by one step; return ``(new_state, value_in_[0, 1))``."""
    new_state = (state * _LCG_MULT + _LCG_INC) % _LCG_MOD
    # Use upper 24 bits for the float to dodge low-bit periodicity.
    return new_state, (new_state >> 8) / float(1 << 24)


def _negotiate_elevation(
    boundary: BoundaryRecord,
    spec_a: SpecBody,
    spec_b: SpecBody,
    *,
    sample_spacing_m: float,
) -> ElevationContinuityContract:
    """Compute the elevation-continuity contract for one boundary."""
    band_a = spec_a.axes["terrain"].elevation_band
    band_b = spec_b.axes["terrain"].elevation_band
    overlap_low = max(band_a[0], band_b[0])
    overlap_high = min(band_a[1], band_b[1])
    if overlap_low >= overlap_high:
        raise BoundaryContractInfeasibleError(
            reason="elevation_bands_disjoint",
            boundary_id=str(boundary.boundary_id),
            region_a=str(boundary.region_a),
            region_b=str(boundary.region_b),
            details={
                "band_a": list(band_a),
                "band_b": list(band_b),
                "overlap_low_m": overlap_low,
                "overlap_high_m": overlap_high,
            },
        )
    # N samples deterministic LCG across the overlap.
    n_samples = max(MIN_SAMPLES, round(boundary.length_meters / sample_spacing_m))
    state = _seed_from_boundary(boundary.region_a, boundary.region_b, boundary.length_meters)
    span = overlap_high - overlap_low
    samples: list[float] = []
    for _ in range(n_samples):
        state, unit = _lcg_unit(state)
        samples.append(overlap_low + unit * span)
    tolerance_m = min(span * TOLERANCE_BAND_FRACTION, TOLERANCE_BAND_MAX_M)
    return ElevationContinuityContract(
        low_m=overlap_low,
        high_m=overlap_high,
        samples=tuple(samples),
        sample_spacing_m=sample_spacing_m,
        tolerance_m=tolerance_m,
    )


def _stream_injector(spec: SpecBody) -> StreamFeatureInjector | None:
    """Return the stream injector on ``spec`` or ``None`` if there is no stream.

    Phase 3 allows at most one stream injector per region; this helper
    folds the lookup so the stream-contract path is straightforward.
    """
    injectors: tuple[FeatureInjector, ...] = spec.axes["terrain"].feature_injectors
    for inj in injectors:
        if isinstance(inj, StreamFeatureInjector):
            return inj
    return None


def _project_anchor_onto_edge(
    anchor: AnchorPoint,
    edge: tuple[tuple[float, float], tuple[float, float]],
) -> tuple[tuple[float, float], float, float]:
    """Project ``anchor`` onto ``edge``; return ``(point, t, distance_m)``.

    ``t`` is the parameter along the edge (clamped to ``[0, 1]``);
    ``distance_m`` is the unsigned perpendicular distance from the
    anchor to the (unclamped) line through ``edge``.
    """
    (x0, y0), (x1, y1) = edge
    ax, ay = anchor
    dx, dy = x1 - x0, y1 - y0
    length_sq = dx * dx + dy * dy
    # length_sq == 0 is impossible: BoundaryRecord enforces length_meters > 0.
    t_raw = ((ax - x0) * dx + (ay - y0) * dy) / length_sq
    t = max(0.0, min(1.0, t_raw))
    px, py = x0 + t * dx, y0 + t * dy
    # Perpendicular distance uses the unclamped projection, so an
    # anchor parallel to but past the segment still measures its
    # distance to the line, which is what 'on the edge' should mean.
    perp_x = ax - (x0 + t_raw * dx)
    perp_y = ay - (y0 + t_raw * dy)
    distance_m = math.hypot(perp_x, perp_y)
    return (px, py), t, distance_m


def _edge_anchor_or_none(
    injector: StreamFeatureInjector,
    edge: tuple[tuple[float, float], tuple[float, float]],
) -> tuple[AnchorPoint, tuple[float, float], float] | None:
    """Return an anchor lying on ``edge`` plus its projection, if any.

    Looks at ``anchor_in`` then ``anchor_out``. Returns the first one
    within :data:`_STREAM_ANCHOR_EDGE_TOLERANCE_M` of the edge line and
    inside the segment (``0 <= t <= 1``). Returns ``None`` if neither
    qualifies — including when both anchors are ``None`` (the Phase-3
    deterministic-anchor fallback).
    """
    for anchor in (injector.anchor_in, injector.anchor_out):
        if anchor is None:
            continue
        projected, t, distance_m = _project_anchor_onto_edge(anchor, edge)
        if distance_m <= _STREAM_ANCHOR_EDGE_TOLERANCE_M and 0.0 <= t <= 1.0:
            return anchor, projected, t
    return None


def _negotiate_stream_crossing(
    boundary: BoundaryRecord,
    spec_a: SpecBody,
    spec_b: SpecBody,
) -> StreamCrossingContract | None:
    """Return a stream-crossing contract or ``None`` when no crossing applies.

    Raises :class:`BoundaryContractInfeasibleError` when both regions
    have explicit edge anchors but they disagree on the crossing
    point, channel width, or flow direction beyond tolerance.
    """
    inj_a = _stream_injector(spec_a)
    inj_b = _stream_injector(spec_b)
    if inj_a is None or inj_b is None:
        return None
    hit_a = _edge_anchor_or_none(inj_a, boundary.shared_edge)
    hit_b = _edge_anchor_or_none(inj_b, boundary.shared_edge)
    if hit_a is None or hit_b is None:
        return None
    anchor_a, point_a, t_a = hit_a
    anchor_b, point_b, t_b = hit_b
    # Width-tolerance: crossing points must lie within the average
    # channel width of each other along the shared edge.
    avg_width = 0.5 * (inj_a.width_meters + inj_b.width_meters)
    crossing_offset = math.hypot(point_a[0] - point_b[0], point_a[1] - point_b[1])
    if crossing_offset > avg_width:
        raise BoundaryContractInfeasibleError(
            reason="stream_crossing_misaligned",
            boundary_id=str(boundary.boundary_id),
            region_a=str(boundary.region_a),
            region_b=str(boundary.region_b),
            details={
                "anchor_a": list(anchor_a),
                "anchor_b": list(anchor_b),
                "point_a": list(point_a),
                "point_b": list(point_b),
                "offset_m": crossing_offset,
                "avg_width_m": avg_width,
            },
        )
    # Width-ratio tolerance.
    width_min = min(inj_a.width_meters, inj_b.width_meters)
    width_max = max(inj_a.width_meters, inj_b.width_meters)
    if width_max / width_min > _STREAM_WIDTH_RATIO_LIMIT:
        raise BoundaryContractInfeasibleError(
            reason="stream_crossing_width_mismatch",
            boundary_id=str(boundary.boundary_id),
            region_a=str(boundary.region_a),
            region_b=str(boundary.region_b),
            details={
                "width_a_m": inj_a.width_meters,
                "width_b_m": inj_b.width_meters,
                "ratio": width_max / width_min,
                "limit": _STREAM_WIDTH_RATIO_LIMIT,
            },
        )
    # Flow direction is the unit vector from anchor_a -> anchor_b.
    raw_dx = anchor_b[0] - anchor_a[0]
    raw_dy = anchor_b[1] - anchor_a[1]
    raw_norm = math.hypot(raw_dx, raw_dy)
    if raw_norm < _STREAM_ANCHOR_EDGE_TOLERANCE_M:
        # Anchors essentially coincide; orient along the edge normal
        # from region_a's interior to region_b's interior.
        (x0, y0), (x1, y1) = boundary.shared_edge
        edge_dx, edge_dy = x1 - x0, y1 - y0
        edge_norm = math.hypot(edge_dx, edge_dy)
        # Edge normal (rotated +90deg). Sign is fixed by region_a < region_b.
        flow_dx = -edge_dy / edge_norm
        flow_dy = edge_dx / edge_norm
    else:
        flow_dx = raw_dx / raw_norm
        flow_dy = raw_dy / raw_norm
    # Flow direction angle tolerance: each region's anchor pair
    # implies a per-region flow direction; compare those.
    angle_dev_deg = _flow_angle_deviation_deg(inj_a, inj_b)
    if angle_dev_deg is not None and angle_dev_deg > _STREAM_ANGLE_TOLERANCE_DEG:
        raise BoundaryContractInfeasibleError(
            reason="stream_crossing_angle_mismatch",
            boundary_id=str(boundary.boundary_id),
            region_a=str(boundary.region_a),
            region_b=str(boundary.region_b),
            details={
                "angle_deviation_deg": angle_dev_deg,
                "limit_deg": _STREAM_ANGLE_TOLERANCE_DEG,
            },
        )
    midpoint = (
        0.5 * (point_a[0] + point_b[0]),
        0.5 * (point_a[1] + point_b[1]),
    )
    # Use the larger 't' for symmetric reproducibility (the crossing
    # location is uniquely determined; this just stabilizes the choice).
    _ = (t_a + t_b) / 2.0
    return StreamCrossingContract(
        crossing_point=midpoint,
        width_m=avg_width,
        depth_m=0.5 * (inj_a.carving_depth + inj_b.carving_depth),
        flow_direction=(flow_dx, flow_dy),
    )


def _flow_angle_deviation_deg(
    inj_a: StreamFeatureInjector,
    inj_b: StreamFeatureInjector,
) -> float | None:
    """Return per-region flow-vector angular deviation in degrees, or ``None``.

    Returns ``None`` when either region's stream lacks both anchors
    (cannot derive a per-region flow vector).
    """
    if (
        inj_a.anchor_in is None
        or inj_a.anchor_out is None
        or inj_b.anchor_in is None
        or inj_b.anchor_out is None
    ):
        return None
    ax = inj_a.anchor_out[0] - inj_a.anchor_in[0]
    ay = inj_a.anchor_out[1] - inj_a.anchor_in[1]
    bx = inj_b.anchor_out[0] - inj_b.anchor_in[0]
    by = inj_b.anchor_out[1] - inj_b.anchor_in[1]
    norm_a = math.hypot(ax, ay)
    norm_b = math.hypot(bx, by)
    if norm_a == 0.0 or norm_b == 0.0:
        return None
    cos = (ax * bx + ay * by) / (norm_a * norm_b)
    cos_clamped = max(-1.0, min(1.0, cos))
    return math.degrees(math.acos(cos_clamped))


def negotiate_boundary_contract(
    boundary: BoundaryRecord,
    spec_a: SpecBody,
    spec_b: SpecBody,
    *,
    sample_spacing_m: float = DEFAULT_SAMPLE_SPACING_M,
) -> tuple[BoundaryContract, ...]:
    """Return the contract(s) for one boundary, symmetric in ``(spec_a, spec_b)``.

    Args:
        boundary: The :class:`BoundaryRecord` describing the shared
            edge. The record's ``region_a < region_b`` invariant is
            assumed; ``spec_a`` must be the spec for ``region_a``.
        spec_a: Spec body of ``boundary.region_a``.
        spec_b: Spec body of ``boundary.region_b``.
        sample_spacing_m: Along-edge sample spacing for the elevation
            contract. Defaults to :data:`DEFAULT_SAMPLE_SPACING_M`.

    Returns:
        A tuple containing exactly one
        :class:`ElevationContinuityContract` and, when both regions'
        stream injectors share the edge, one
        :class:`StreamCrossingContract`.

    Raises:
        BoundaryContractInfeasibleError: When the two regions'
            elevation bands are disjoint, or when both have explicit
            stream anchors that disagree beyond width / angle
            tolerance.
    """
    elevation = _negotiate_elevation(boundary, spec_a, spec_b, sample_spacing_m=sample_spacing_m)
    contracts: list[BoundaryContract] = [elevation]
    stream = _negotiate_stream_crossing(boundary, spec_a, spec_b)
    if stream is not None:
        contracts.append(stream)
    return tuple(contracts)


__all__ = [
    "DEFAULT_SAMPLE_SPACING_M",
    "MIN_SAMPLES",
    "TOLERANCE_BAND_FRACTION",
    "TOLERANCE_BAND_MAX_M",
    "BoundaryContractConflictError",
    "BoundaryContractInfeasibleError",
    "negotiate_boundary_contract",
]
