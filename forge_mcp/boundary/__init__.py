"""Boundary contract negotiation (Phase 6 Stage B).

Phase 2 detects adjacencies and emits :class:`BoundaryRecord` stubs
with no contracts. Phase 6 negotiates the contracts both adjacent
regions' generators must honor when they realize their heightmaps.

The negotiation is **symmetric** — :func:`negotiate_boundary_contract`
returns byte-identical contracts whether called as
``(region_a, region_b)`` or ``(region_b, region_a)`` — and **pure** —
no Blender, no I/O, no random state outside the deterministic LCG
seeded off the boundary's ``(region_a, region_b, length_meters)``
tuple.
"""

from __future__ import annotations

from forge_mcp.boundary.contract import (
    DEFAULT_SAMPLE_SPACING_M,
    MIN_SAMPLES,
    TOLERANCE_BAND_FRACTION,
    TOLERANCE_BAND_MAX_M,
    BoundaryContractConflictError,
    BoundaryContractInfeasibleError,
    negotiate_boundary_contract,
)

__all__ = [
    "DEFAULT_SAMPLE_SPACING_M",
    "MIN_SAMPLES",
    "TOLERANCE_BAND_FRACTION",
    "TOLERANCE_BAND_MAX_M",
    "BoundaryContractConflictError",
    "BoundaryContractInfeasibleError",
    "negotiate_boundary_contract",
]
