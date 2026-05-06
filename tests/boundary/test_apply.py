"""Tests for :mod:`forge_mcp.boundary.apply`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from forge_mcp.boundary.apply import (
    _CONFLICT_UNMAPPABLE,
    build_boundary_conditions,
    participating_boundaries,
)
from forge_mcp.project.schemas import (
    BoundaryId,
    BoundaryRecord,
    ElevationContinuityContract,
    NodeId,
    Polygon2D,
    RegionId,
    RegionNode,
    SpatialBounds,
)


def _region(node_id: str = "alpha") -> RegionNode:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    return RegionNode(
        node_id=RegionId(node_id),
        parent_node=NodeId("world"),
        name=node_id,
        seed=42,
        spatial_bounds=SpatialBounds(
            coords=Polygon2D(
                coords=(
                    (0.0, 0.0),
                    (100.0, 0.0),
                    (100.0, 100.0),
                    (0.0, 100.0),
                ),
            ),
        ),
        created_at=now,
        modified_at=now,
    )


def _elevation_contract(low: float = 100.0, high: float = 200.0) -> ElevationContinuityContract:
    return ElevationContinuityContract(
        low_m=low,
        high_m=high,
        samples=(150.0, 160.0, 170.0, 180.0, 190.0, 195.0, 188.0, 175.0),
        sample_spacing_m=14.0,
        tolerance_m=2.0,
    )


def _boundary(  # noqa: PLR0913 - test factory aggregates fixture knobs
    *,
    region_a: str,
    region_b: str,
    edge: tuple[tuple[float, float], tuple[float, float]],
    contracts: tuple[ElevationContinuityContract, ...] = (),
    boundary_id: str = "b-001",
    length_meters: float = 100.0,
) -> BoundaryRecord:
    a, b = sorted([region_a, region_b])
    now = datetime(2025, 1, 1, tzinfo=UTC)
    return BoundaryRecord(
        boundary_id=BoundaryId(boundary_id),
        region_a=RegionId(a),
        region_b=RegionId(b),
        shared_edge=edge,
        length_meters=length_meters,
        contracts=contracts,
        created_at=now,
        modified_at=now,
    )


def test_build_returns_empty_when_region_not_in_any_boundary() -> None:
    region = _region("alpha")
    boundaries = (
        _boundary(
            region_a="beta",
            region_b="gamma",
            edge=((0.0, 0.0), (100.0, 0.0)),
        ),
    )
    bc = build_boundary_conditions(region, boundaries)
    assert bc.edge_contracts == ()
    assert bc.conflicts_resolved == ()


def test_build_picks_south_edge_for_y_min() -> None:
    region = _region("alpha")
    boundary = _boundary(
        region_a="alpha",
        region_b="beta",
        edge=((0.0, 0.0), (100.0, 0.0)),
        contracts=(_elevation_contract(),),
    )
    bc = build_boundary_conditions(region, [boundary])
    assert len(bc.edge_contracts) == 1
    assert bc.edge_contracts[0].side == "south"
    assert bc.edge_contracts[0].contract_id == "b-001"
    # Inland falloff = max(20, 100*0.05) = 20 m.
    assert bc.edge_contracts[0].inland_falloff_m == pytest.approx(20.0)


def test_build_picks_north_edge_for_y_max() -> None:
    region = _region("alpha")
    boundary = _boundary(
        region_a="alpha",
        region_b="beta",
        edge=((0.0, 100.0), (100.0, 100.0)),
        contracts=(_elevation_contract(),),
    )
    bc = build_boundary_conditions(region, [boundary])
    assert bc.edge_contracts[0].side == "north"


def test_build_picks_west_edge_for_x_min() -> None:
    region = _region("alpha")
    boundary = _boundary(
        region_a="alpha",
        region_b="beta",
        edge=((0.0, 0.0), (0.0, 100.0)),
        contracts=(_elevation_contract(),),
    )
    bc = build_boundary_conditions(region, [boundary])
    assert bc.edge_contracts[0].side == "west"


def test_build_picks_east_edge_for_x_max() -> None:
    region = _region("alpha")
    boundary = _boundary(
        region_a="alpha",
        region_b="beta",
        edge=((100.0, 0.0), (100.0, 100.0)),
        contracts=(_elevation_contract(),),
    )
    bc = build_boundary_conditions(region, [boundary])
    assert bc.edge_contracts[0].side == "east"


def test_build_records_conflict_for_non_axis_aligned_edge() -> None:
    region = _region("alpha")
    boundary = _boundary(
        region_a="alpha",
        region_b="beta",
        edge=((0.0, 0.0), (100.0, 100.0)),
        contracts=(_elevation_contract(),),
    )
    bc = build_boundary_conditions(region, [boundary])
    assert bc.edge_contracts == ()
    assert bc.conflicts_resolved == (_CONFLICT_UNMAPPABLE,)


def test_build_inland_falloff_floor_for_short_edge() -> None:
    region = _region("alpha")
    boundary = _boundary(
        region_a="alpha",
        region_b="beta",
        edge=((0.0, 0.0), (100.0, 0.0)),
        contracts=(_elevation_contract(),),
        length_meters=50.0,  # 50 * 0.05 = 2.5 m, floor wins.
    )
    bc = build_boundary_conditions(region, [boundary])
    assert bc.edge_contracts[0].inland_falloff_m == pytest.approx(20.0)


def test_build_inland_falloff_uses_fraction_for_long_edge() -> None:
    region = _region("alpha")
    boundary = _boundary(
        region_a="alpha",
        region_b="beta",
        edge=((0.0, 0.0), (100.0, 0.0)),
        contracts=(_elevation_contract(),),
        length_meters=2000.0,  # 2000 * 0.05 = 100 m.
    )
    bc = build_boundary_conditions(region, [boundary])
    assert bc.edge_contracts[0].inland_falloff_m == pytest.approx(100.0)


def test_build_reverses_samples_when_edge_runs_backwards() -> None:
    region = _region("alpha")
    contract = _elevation_contract()
    boundary = _boundary(
        region_a="alpha",
        region_b="beta",
        # Edge listed east-to-west on a south side: should reverse samples.
        edge=((100.0, 0.0), (0.0, 0.0)),
        contracts=(contract,),
    )
    bc = build_boundary_conditions(region, [boundary])
    assert bc.edge_contracts[0].samples == tuple(reversed(contract.samples))


def test_participating_boundaries_filters_by_region() -> None:
    a = _boundary(
        region_a="alpha",
        region_b="beta",
        edge=((0.0, 0.0), (100.0, 0.0)),
        boundary_id="b-1",
    )
    b = _boundary(
        region_a="gamma",
        region_b="delta",
        edge=((0.0, 0.0), (100.0, 0.0)),
        boundary_id="b-2",
    )
    out = participating_boundaries(RegionId("alpha"), [a, b])
    assert [str(x.boundary_id) for x in out] == ["b-1"]
