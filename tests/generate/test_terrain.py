"""Tests for :mod:`forge_mcp.generate.terrain` orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
from forge_mcp.descriptor.map_to_spec import map_to_spec
from forge_mcp.descriptor.region_extent import RegionExtent
from forge_mcp.descriptor.schema import (
    Hydrology,
    StreamCharacter,
    StructuredDescriptor,
    Terrain,
    TerrainPrimary,
)
from forge_mcp.generate.terrain import run

if TYPE_CHECKING:
    from forge_mcp.project.schemas import SpecRecord

NOW = datetime(2026, 5, 1, tzinfo=UTC)
BLENDER = "5.0.2"
BPY_HG = "blender-5.0.2-v1"
SHAPE = (32, 32)
EXTENT = RegionExtent(width_m=4000.0, height_m=4000.0, area_m2=4000.0 * 4000.0)


def _spec(
    primary: TerrainPrimary = TerrainPrimary.ROLLING_HILLS,
    *,
    hydrology: Hydrology | None = None,
) -> SpecRecord:
    descriptor = StructuredDescriptor(
        terrain=Terrain(primary=primary),
        hydrology=hydrology,
    )
    return map_to_spec(
        descriptor,
        seed=0,
        region_extent=EXTENT,
        blender_version=BLENDER,
        bpy_hypergraph_version=BPY_HG,
        now=NOW,
    )


def test_run_is_deterministic_for_same_seed() -> None:
    spec = _spec()
    a = run(spec, seed=42, shape=SHAPE)
    b = run(spec, seed=42, shape=SHAPE)
    assert np.array_equal(a.heightmap.data, b.heightmap.data)
    assert a.generators_used == b.generators_used


def test_run_changes_with_seed() -> None:
    spec = _spec()
    a = run(spec, seed=1, shape=SHAPE)
    b = run(spec, seed=2, shape=SHAPE)
    assert not np.array_equal(a.heightmap.data, b.heightmap.data)


def test_generators_used_includes_post_passes_in_order() -> None:
    result = run(_spec(), seed=7, shape=SHAPE)
    # Phase-3 mapper always emits hydraulic then thermal, no stream.
    assert result.generators_used == (
        "noise.ridged_multifractal",
        "erosion.hydraulic",
        "erosion.thermal",
    )
    assert result.stream_geometry is None


def test_stream_injector_runs_and_records_geometry() -> None:
    spec = _spec(
        TerrainPrimary.RIVER_VALLEY,
        hydrology=Hydrology(has_stream=True, stream_character=StreamCharacter.MEANDERING_RIVER),
    )
    result = run(spec, seed=11, shape=SHAPE)
    assert result.stream_geometry is not None
    assert "stream.injector" in result.generators_used
    # Stream injector must run after the post-passes.
    assert result.generators_used.index("stream.injector") > result.generators_used.index(
        "erosion.thermal",
    )


def test_elevation_band_is_applied() -> None:
    spec = _spec(TerrainPrimary.ALPINE_PEAKS)
    band = spec.body.axes["terrain"].elevation_band
    result = run(spec, seed=3, shape=SHAPE)
    assert result.heightmap.elevation_band == band
    # Output values must (approximately) live inside the requested band;
    # erosion can push slightly outside the [lo, hi] bounds, so just
    # check the bulk of mass lies inside the expanded envelope.
    lo, hi = band
    span = hi - lo
    assert float(result.heightmap.data.min()) >= lo - span
    assert float(result.heightmap.data.max()) <= hi + span


def test_resolution_propagates_from_spec() -> None:
    spec = _spec()
    result = run(spec, seed=5, shape=SHAPE)
    assert result.heightmap.resolution_meters_per_pixel == (
        spec.body.axes["terrain"].resolution_meters_per_pixel
    )
