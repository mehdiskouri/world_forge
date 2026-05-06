"""Tests for the symmetric boundary-contract negotiator.

Phase 6 Stage B gate: deterministic, symmetric (order-independent),
disjoint-band fail-loud, stream-crossing alignment, and
infeasibility-error structured fields.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from forge_mcp.boundary.contract import (
    DEFAULT_SAMPLE_SPACING_M,
    MIN_SAMPLES,
    TOLERANCE_BAND_FRACTION,
    TOLERANCE_BAND_MAX_M,
    BoundaryContractInfeasibleError,
    negotiate_boundary_contract,
)
from forge_mcp.project.schemas import (
    BoundaryId,
    BoundaryRecord,
    ElevationContinuityContract,
    GenerationMetadata,
    RegionId,
    SpecBody,
    StreamCrossingContract,
    StreamFeatureInjector,
    TerrainAxisSpec,
    TerrainGeneratorParams,
)

NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def _spec(
    *,
    elevation_band: tuple[float, float] = (0.0, 100.0),
    feature_injectors: tuple[StreamFeatureInjector, ...] = (),
) -> SpecBody:
    return SpecBody(
        axes={
            "terrain": TerrainAxisSpec(
                params=TerrainGeneratorParams(
                    octaves=4,
                    lacunarity=2.0,
                    persistence=0.5,
                    warp=0.3,
                    scale_meters=128.0,
                ),
                elevation_band=elevation_band,
                resolution_meters_per_pixel=2.0,
                feature_injectors=feature_injectors,
            ),
        },
        generation_metadata=GenerationMetadata(
            compiler_version="0.3.0",
            generators_used=("ridged_multifractal_v1",),
            bpy_hypergraph_version="0.1.0",
            blender_version="5.0.0",
        ),
    )


def _boundary(
    *,
    region_a: str = "region_a",
    region_b: str = "region_b",
    shared_edge: tuple[tuple[float, float], tuple[float, float]] = ((0.0, 0.0), (1024.0, 0.0)),
    length_meters: float = 1024.0,
) -> BoundaryRecord:
    return BoundaryRecord(
        boundary_id=BoundaryId(f"boundary_{region_a}__{region_b}"),
        region_a=RegionId(region_a),
        region_b=RegionId(region_b),
        shared_edge=shared_edge,
        length_meters=length_meters,
        created_at=NOW,
        modified_at=NOW,
    )


# ---------------------------------------------------------------------------
# Elevation contract
# ---------------------------------------------------------------------------


def test_elevation_contract_emitted_with_overlap() -> None:
    boundary = _boundary()
    a = _spec(elevation_band=(0.0, 200.0))
    b = _spec(elevation_band=(50.0, 300.0))
    contracts = negotiate_boundary_contract(boundary, a, b)
    assert len(contracts) == 1
    elevation = contracts[0]
    assert isinstance(elevation, ElevationContinuityContract)
    assert elevation.low_m == 50.0  # noqa: PLR2004 - overlap arithmetic
    assert elevation.high_m == 200.0  # noqa: PLR2004 - overlap arithmetic
    # All samples lie inside the overlap band.
    assert all(50.0 <= s <= 200.0 for s in elevation.samples)  # noqa: PLR2004 - band bounds
    expected_n = max(MIN_SAMPLES, round(boundary.length_meters / DEFAULT_SAMPLE_SPACING_M))
    assert len(elevation.samples) == expected_n


def test_elevation_contract_disjoint_bands_raises() -> None:
    boundary = _boundary()
    a = _spec(elevation_band=(0.0, 100.0))
    b = _spec(elevation_band=(200.0, 300.0))
    with pytest.raises(BoundaryContractInfeasibleError) as info:
        negotiate_boundary_contract(boundary, a, b)
    err = info.value
    assert err.reason == "elevation_bands_disjoint"
    assert err.boundary_id == "boundary_region_a__region_b"
    assert err.region_a == "region_a"
    assert err.region_b == "region_b"
    assert err.details["band_a"] == [0.0, 100.0]
    assert err.details["band_b"] == [200.0, 300.0]


def test_elevation_contract_touching_bands_raises() -> None:
    """Bands that share only one endpoint produce a zero-width overlap and fail."""
    boundary = _boundary()
    a = _spec(elevation_band=(0.0, 100.0))
    b = _spec(elevation_band=(100.0, 200.0))
    with pytest.raises(BoundaryContractInfeasibleError) as info:
        negotiate_boundary_contract(boundary, a, b)
    assert info.value.reason == "elevation_bands_disjoint"


def test_tolerance_clamped_to_max() -> None:
    """A wide overlap band hits the absolute tolerance cap, not the 5% rule."""
    boundary = _boundary()
    a = _spec(elevation_band=(0.0, 1000.0))
    b = _spec(elevation_band=(0.0, 1000.0))
    elevation = negotiate_boundary_contract(boundary, a, b)[0]
    assert isinstance(elevation, ElevationContinuityContract)
    fraction_value = (elevation.high_m - elevation.low_m) * TOLERANCE_BAND_FRACTION
    expected = min(fraction_value, TOLERANCE_BAND_MAX_M)
    assert elevation.tolerance_m == expected
    assert elevation.tolerance_m == TOLERANCE_BAND_MAX_M  # sanity: cap is hit


def test_tolerance_uses_fraction_for_narrow_overlap() -> None:
    """A narrow overlap stays under the cap and uses the 5%-of-band rule."""
    boundary = _boundary()
    a = _spec(elevation_band=(0.0, 20.0))
    b = _spec(elevation_band=(0.0, 20.0))
    elevation = negotiate_boundary_contract(boundary, a, b)[0]
    assert isinstance(elevation, ElevationContinuityContract)
    expected = (elevation.high_m - elevation.low_m) * TOLERANCE_BAND_FRACTION
    assert elevation.tolerance_m == expected
    assert elevation.tolerance_m < TOLERANCE_BAND_MAX_M


def test_sample_count_floors_at_minimum() -> None:
    """Short edges still get :data:`MIN_SAMPLES` samples."""
    boundary = _boundary(length_meters=10.0, shared_edge=((0.0, 0.0), (10.0, 0.0)))
    a = _spec()
    b = _spec()
    elevation = negotiate_boundary_contract(boundary, a, b)[0]
    assert isinstance(elevation, ElevationContinuityContract)
    assert len(elevation.samples) == MIN_SAMPLES


def test_sample_count_scales_with_length() -> None:
    """A 4 km edge at 64 m spacing yields 64 samples."""
    boundary = _boundary(length_meters=4096.0, shared_edge=((0.0, 0.0), (4096.0, 0.0)))
    a = _spec()
    b = _spec()
    elevation = negotiate_boundary_contract(
        boundary, a, b, sample_spacing_m=DEFAULT_SAMPLE_SPACING_M
    )[0]
    assert isinstance(elevation, ElevationContinuityContract)
    assert len(elevation.samples) == round(4096.0 / DEFAULT_SAMPLE_SPACING_M)


# ---------------------------------------------------------------------------
# Determinism (NF-2.1 extension)
# ---------------------------------------------------------------------------


def test_negotiation_byte_identical_across_runs() -> None:
    boundary = _boundary()
    a = _spec(elevation_band=(50.0, 200.0))
    b = _spec(elevation_band=(50.0, 200.0))
    c1 = negotiate_boundary_contract(boundary, a, b)
    c2 = negotiate_boundary_contract(boundary, a, b)
    assert c1 == c2


def test_negotiation_independent_of_endpoint_evaluation_order() -> None:
    """Calling with swapped specs (against the canonical region order) is a programming
    error in normal use, but the elevation samples are seeded from the BoundaryRecord's
    own canonical (region_a, region_b, length) tuple, so passing the same boundary with
    swapped specs MUST still produce the same elevation samples (different bands would
    of course give different overlaps; here both regions share a band).
    """
    boundary = _boundary()
    a = _spec(elevation_band=(50.0, 200.0))
    b = _spec(elevation_band=(50.0, 200.0))
    forward = negotiate_boundary_contract(boundary, a, b)
    swapped = negotiate_boundary_contract(boundary, b, a)
    assert forward == swapped


def test_different_lengths_yield_different_samples() -> None:
    a = _spec(elevation_band=(0.0, 100.0))
    b = _spec(elevation_band=(0.0, 100.0))
    short = negotiate_boundary_contract(
        _boundary(length_meters=1024.0, shared_edge=((0.0, 0.0), (1024.0, 0.0))), a, b
    )[0]
    long = negotiate_boundary_contract(
        _boundary(length_meters=2048.0, shared_edge=((0.0, 0.0), (2048.0, 0.0))), a, b
    )[0]
    assert isinstance(short, ElevationContinuityContract)
    assert isinstance(long, ElevationContinuityContract)
    assert short.samples != long.samples[: len(short.samples)]


def test_different_endpoint_pairs_yield_different_samples() -> None:
    a = _spec(elevation_band=(0.0, 100.0))
    b = _spec(elevation_band=(0.0, 100.0))
    pair1 = negotiate_boundary_contract(_boundary(region_a="alpha", region_b="beta"), a, b)[0]
    pair2 = negotiate_boundary_contract(_boundary(region_a="delta", region_b="gamma"), a, b)[0]
    assert isinstance(pair1, ElevationContinuityContract)
    assert isinstance(pair2, ElevationContinuityContract)
    assert pair1.samples != pair2.samples


# ---------------------------------------------------------------------------
# Stream-crossing contract
# ---------------------------------------------------------------------------


def _stream_on_edge(
    *,
    midpoint_x: float,
    side: int,
    width_meters: float = 6.0,
) -> StreamFeatureInjector:
    """Build a stream injector whose anchors cross the y=0 shared edge.

    ``side`` selects which of the two stream endpoints lies on the
    shared edge: ``+1`` for a region whose interior is at +y (the
    stream flows down out of the region across the edge — ``anchor_in``
    is interior, ``anchor_out`` is on the edge), ``-1`` for a region
    whose interior is at -y (stream flows down into the region across
    the edge — ``anchor_in`` is on the edge, ``anchor_out`` is
    interior). Both regions therefore have streams flowing in the same
    -y direction across the same crossing point, which is the only
    physically-meaningful continuous-stream configuration.
    """
    edge_anchor = (midpoint_x, 0.0)
    interior_anchor = (midpoint_x, side * 100.0)
    if side > 0:
        anchor_in, anchor_out = interior_anchor, edge_anchor
    else:
        anchor_in, anchor_out = edge_anchor, interior_anchor
    return StreamFeatureInjector(
        anchor_in=anchor_in,
        anchor_out=anchor_out,
        width_meters=width_meters,
        carving_depth=2.0,
    )


def test_stream_crossing_contract_emitted_when_anchors_align() -> None:
    boundary = _boundary()
    a = _spec(feature_injectors=(_stream_on_edge(midpoint_x=512.0, side=1),))
    b = _spec(feature_injectors=(_stream_on_edge(midpoint_x=512.0, side=-1),))
    contracts = negotiate_boundary_contract(boundary, a, b)
    assert len(contracts) == 2  # noqa: PLR2004 - elevation + stream-crossing
    crossing = next(c for c in contracts if isinstance(c, StreamCrossingContract))
    assert crossing.crossing_point == (512.0, 0.0)
    assert crossing.width_m == 6.0  # noqa: PLR2004 - average of equal anchor widths
    assert crossing.depth_m == 2.0  # noqa: PLR2004 - average of equal carving depths
    # Flow direction is unit (validated by the model).
    assert math.isclose(math.hypot(*crossing.flow_direction), 1.0, abs_tol=1e-6)


def test_no_stream_contract_when_one_region_lacks_stream() -> None:
    boundary = _boundary()
    a = _spec(feature_injectors=(_stream_on_edge(midpoint_x=512.0, side=1),))
    b = _spec()  # no streams
    contracts = negotiate_boundary_contract(boundary, a, b)
    assert len(contracts) == 1
    assert isinstance(contracts[0], ElevationContinuityContract)


def test_no_stream_contract_when_anchors_off_edge() -> None:
    """Phase 3 deterministic-anchor fallback: no explicit anchors, no contract."""
    boundary = _boundary()
    no_anchor_stream = StreamFeatureInjector(width_meters=5.0, carving_depth=2.0)
    a = _spec(feature_injectors=(no_anchor_stream,))
    b = _spec(feature_injectors=(no_anchor_stream,))
    contracts = negotiate_boundary_contract(boundary, a, b)
    assert len(contracts) == 1


def test_stream_misaligned_raises() -> None:
    boundary = _boundary()
    a = _spec(feature_injectors=(_stream_on_edge(midpoint_x=200.0, side=1, width_meters=4.0),))
    b = _spec(feature_injectors=(_stream_on_edge(midpoint_x=800.0, side=-1, width_meters=4.0),))
    with pytest.raises(BoundaryContractInfeasibleError) as info:
        negotiate_boundary_contract(boundary, a, b)
    assert info.value.reason == "stream_crossing_misaligned"
    assert info.value.details["offset_m"] == 600.0  # noqa: PLR2004 - 800-200


def test_stream_width_mismatch_raises() -> None:
    boundary = _boundary()
    a = _spec(feature_injectors=(_stream_on_edge(midpoint_x=512.0, side=1, width_meters=2.0),))
    b = _spec(feature_injectors=(_stream_on_edge(midpoint_x=512.0, side=-1, width_meters=10.0),))
    with pytest.raises(BoundaryContractInfeasibleError) as info:
        negotiate_boundary_contract(boundary, a, b)
    assert info.value.reason == "stream_crossing_width_mismatch"
    assert info.value.details["ratio"] == 5.0  # noqa: PLR2004 - 10/2


def test_stream_angle_mismatch_raises() -> None:
    """Two streams that cross the same point but flow at >30 deg differ."""
    boundary = _boundary()
    # Region a stream flows straight down through (512, 0).
    a_stream = StreamFeatureInjector(
        anchor_in=(512.0, 100.0),
        anchor_out=(512.0, 0.0),
        width_meters=6.0,
        carving_depth=2.0,
    )
    # Region b stream comes in at a 60 deg angle from the +x side.
    b_stream = StreamFeatureInjector(
        anchor_in=(512.0, 0.0),
        anchor_out=(
            512.0 + math.sin(math.radians(60.0)) * 100.0,
            -math.cos(math.radians(60.0)) * 100.0,
        ),
        width_meters=6.0,
        carving_depth=2.0,
    )
    a = _spec(feature_injectors=(a_stream,))
    b = _spec(feature_injectors=(b_stream,))
    with pytest.raises(BoundaryContractInfeasibleError) as info:
        negotiate_boundary_contract(boundary, a, b)
    assert info.value.reason == "stream_crossing_angle_mismatch"
    angle_dev_obj = info.value.details["angle_deviation_deg"]
    assert isinstance(angle_dev_obj, float)
    assert angle_dev_obj > 30.0  # noqa: PLR2004 - tolerance threshold


# ---------------------------------------------------------------------------
# Schema contract: all returned objects round-trip through Pydantic
# ---------------------------------------------------------------------------


def test_returned_contracts_round_trip_through_json() -> None:
    boundary = _boundary()
    a = _spec(
        elevation_band=(0.0, 200.0), feature_injectors=(_stream_on_edge(midpoint_x=512.0, side=1),)
    )
    b = _spec(
        elevation_band=(50.0, 300.0),
        feature_injectors=(_stream_on_edge(midpoint_x=512.0, side=-1),),
    )
    contracts = negotiate_boundary_contract(boundary, a, b)
    for contract in contracts:
        round_tripped = type(contract).model_validate(contract.model_dump(mode="json"))
        assert round_tripped == contract
