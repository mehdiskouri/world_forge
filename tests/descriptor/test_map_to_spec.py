"""Tests for :mod:`forge_mcp.descriptor.map_to_spec`.

Phase 3 Stage C: every :class:`TerrainPrimary` profile is exercised;
ruggedness modulation, elevation-band override, hydrology presence /
absence, and the content-addressing contract are all pinned.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from forge_mcp.descriptor.map_to_spec import (
    COMPILER_VERSION,
    ELEVATION_BAND_CLAMPED_FLAG,
    GENERATOR_NAME,
    MAX_MEAN_SLOPE_DEG_BY_ARCHETYPE,
    TERRAIN_PROFILES,
    ElevationBandImplausibleError,
    map_to_spec,
)
from forge_mcp.descriptor.region_extent import RegionExtent
from forge_mcp.descriptor.schema import (
    Hydrology,
    StreamCharacter,
    StructuredDescriptor,
    Terrain,
    TerrainPrimary,
)
from forge_mcp.project.schemas import SpecRecord, StreamFeatureInjector

NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
BLENDER = "5.0.2"
BPY_HG = "blender-5.0.2-v1"
SEED = 42

# Default region extent for tests: km-scale, larger than every archetype's
# default elevation band would imply at the per-archetype slope ceiling, so
# the default-band clamp is a no-op for these fixtures unless the test
# overrides ``extent`` deliberately.
DEFAULT_EXTENT = RegionExtent(width_m=4000.0, height_m=4000.0, area_m2=4000.0 * 4000.0)


def _descriptor(
    primary: TerrainPrimary = TerrainPrimary.PLAINS,
    *,
    ruggedness: float | None = None,
    elevation_band: tuple[float, float] | None = None,
    hydrology: Hydrology | None = None,
) -> StructuredDescriptor:
    return StructuredDescriptor(
        terrain=Terrain(
            primary=primary,
            ruggedness=ruggedness,
            elevation_band=elevation_band,
        ),
        hydrology=hydrology,
    )


def _compile(
    d: StructuredDescriptor,
    *,
    seed: int = SEED,
    extent: RegionExtent = DEFAULT_EXTENT,
) -> SpecRecord:
    return map_to_spec(
        d,
        seed,
        region_extent=extent,
        blender_version=BLENDER,
        bpy_hypergraph_version=BPY_HG,
        now=NOW,
    )


# ---------------------------------------------------------------------------
# Coverage of every TerrainPrimary profile
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("primary", sorted(TERRAIN_PROFILES))
def test_every_terrain_primary_compiles(primary: TerrainPrimary) -> None:
    """Every enum value has a profile entry and yields a valid spec."""
    spec = _compile(_descriptor(primary))
    assert spec.body.axes["terrain"].generator == GENERATOR_NAME
    assert spec.body.generation_metadata.compiler_version == COMPILER_VERSION


# ---------------------------------------------------------------------------
# Modulators
# ---------------------------------------------------------------------------


def test_ruggedness_increases_octaves_and_persistence() -> None:
    low = _compile(_descriptor(TerrainPrimary.ALPINE_VALLEY, ruggedness=0.0))
    high = _compile(_descriptor(TerrainPrimary.ALPINE_VALLEY, ruggedness=1.0))
    assert high.body.axes["terrain"].params.octaves > low.body.axes["terrain"].params.octaves
    assert (
        high.body.axes["terrain"].params.persistence > low.body.axes["terrain"].params.persistence
    )


def test_ruggedness_increases_erosion_iterations() -> None:
    low = _compile(_descriptor(TerrainPrimary.CANYON, ruggedness=0.0))
    high = _compile(_descriptor(TerrainPrimary.CANYON, ruggedness=1.0))
    assert (
        high.body.axes["terrain"].post_passes[0].iterations
        > low.body.axes["terrain"].post_passes[0].iterations
    )


def test_ruggedness_default_is_midpoint() -> None:
    """Omitting ruggedness should match an explicit 0.5."""
    omitted = _compile(_descriptor(TerrainPrimary.ROLLING_HILLS))
    explicit = _compile(_descriptor(TerrainPrimary.ROLLING_HILLS, ruggedness=0.5))
    assert omitted.body == explicit.body


def test_elevation_band_descriptor_overrides_profile_default() -> None:
    custom = (10.0, 99.0)
    spec = _compile(_descriptor(TerrainPrimary.PLAINS, elevation_band=custom))
    assert spec.body.axes["terrain"].elevation_band == custom


def test_elevation_band_falls_back_to_profile_default_when_unset() -> None:
    spec = _compile(_descriptor(TerrainPrimary.ALPINE_PEAKS))
    assert (
        spec.body.axes["terrain"].elevation_band
        == TERRAIN_PROFILES[TerrainPrimary.ALPINE_PEAKS].default_elevation_band
    )


# ---------------------------------------------------------------------------
# Hydrology
# ---------------------------------------------------------------------------


def test_no_hydrology_means_no_feature_injectors() -> None:
    spec = _compile(_descriptor(hydrology=None))
    assert spec.body.axes["terrain"].feature_injectors == ()


def test_has_stream_false_means_no_feature_injectors() -> None:
    spec = _compile(
        _descriptor(hydrology=Hydrology(has_stream=False)),
    )
    assert spec.body.axes["terrain"].feature_injectors == ()


def test_stream_character_none_means_no_feature_injectors() -> None:
    spec = _compile(
        _descriptor(
            hydrology=Hydrology(has_stream=True, stream_character=StreamCharacter.NONE),
        ),
    )
    assert spec.body.axes["terrain"].feature_injectors == ()


def test_stream_character_meandering_river_emits_injector() -> None:
    spec = _compile(
        _descriptor(
            TerrainPrimary.RIVER_VALLEY,
            hydrology=Hydrology(
                has_stream=True,
                stream_character=StreamCharacter.MEANDERING_RIVER,
            ),
        ),
    )
    injectors = spec.body.axes["terrain"].feature_injectors
    assert len(injectors) == 1
    assert isinstance(injectors[0], StreamFeatureInjector)
    assert injectors[0].width_meters == pytest.approx(18.0)
    assert injectors[0].carving_depth == pytest.approx(4.0)


def test_has_stream_true_without_character_emits_no_injector() -> None:
    """Validation lives in :mod:`forge_mcp.descriptor.validate`; the
    mapper stays defensive and emits nothing in this state."""
    spec = _compile(_descriptor(hydrology=Hydrology(has_stream=True)))
    assert spec.body.axes["terrain"].feature_injectors == ()


# ---------------------------------------------------------------------------
# Content-addressing
# ---------------------------------------------------------------------------


def test_spec_id_is_stable_under_identical_inputs() -> None:
    a = _compile(_descriptor(TerrainPrimary.DESERT_MESA))
    b = _compile(_descriptor(TerrainPrimary.DESERT_MESA))
    assert a.spec_id == b.spec_id
    assert a.spec_id.startswith("spec_")


def test_spec_id_changes_when_descriptor_changes() -> None:
    a = _compile(_descriptor(TerrainPrimary.DESERT_MESA))
    b = _compile(_descriptor(TerrainPrimary.DESERT_MESA, ruggedness=0.9))
    assert a.spec_id != b.spec_id


def test_spec_id_changes_when_blender_version_changes() -> None:
    base = _descriptor(TerrainPrimary.DESERT_MESA)
    a = map_to_spec(
        base,
        SEED,
        region_extent=DEFAULT_EXTENT,
        blender_version="5.0.2",
        bpy_hypergraph_version=BPY_HG,
        now=NOW,
    )
    b = map_to_spec(
        base,
        SEED,
        region_extent=DEFAULT_EXTENT,
        blender_version="5.0.3",
        bpy_hypergraph_version=BPY_HG,
        now=NOW,
    )
    assert a.spec_id != b.spec_id


def test_spec_id_independent_of_seed_in_phase_3() -> None:
    """Seed is reserved on the signature but unused by the Phase-3 mapper."""
    base = _descriptor(TerrainPrimary.PLAINS)
    a = _compile(base, seed=1)
    b = _compile(base, seed=2)
    assert a.spec_id == b.spec_id


# ---------------------------------------------------------------------------
# Phase-6 Stage A: region-extent-aware elevation-band scaling
# ---------------------------------------------------------------------------


_SMALL_EXTENT = RegionExtent(width_m=200.0, height_m=200.0, area_m2=200.0 * 200.0)
_MEDIUM_EXTENT = RegionExtent(width_m=1000.0, height_m=1000.0, area_m2=1000.0 * 1000.0)
_KM_EXTENT = RegionExtent(width_m=4000.0, height_m=4000.0, area_m2=4000.0 * 4000.0)


def test_slope_ceiling_table_is_exhaustive() -> None:
    """Every TerrainPrimary value carries an entry in the slope-ceiling table."""
    assert set(MAX_MEAN_SLOPE_DEG_BY_ARCHETYPE) == set(TerrainPrimary)


@pytest.mark.parametrize("primary", sorted(TERRAIN_PROFILES))
@pytest.mark.parametrize(
    "extent",
    [_SMALL_EXTENT, _MEDIUM_EXTENT, _KM_EXTENT],
    ids=["small_200m", "medium_1km", "km_4km"],
)
def test_default_band_clamp_respects_slope_ceiling(
    primary: TerrainPrimary,
    extent: RegionExtent,
) -> None:
    """The default-band path always emits a band within the per-archetype ceiling."""
    spec = _compile(_descriptor(primary), extent=extent)
    band = spec.body.axes["terrain"].elevation_band
    height = band[1] - band[0]
    ceiling_deg = MAX_MEAN_SLOPE_DEG_BY_ARCHETYPE[primary]
    max_height = extent.min_extent_m * math.tan(math.radians(ceiling_deg))
    assert height <= max_height + 1e-6


def test_default_band_clamp_records_conflict_when_triggered() -> None:
    """Clamped default bands surface as ``conflicts_resolved`` for traceability."""
    # alpine_valley default band is 1600 m; at a 200 m extent and 30 deg
    # ceiling the max height is ~115 m, so the clamp must trigger.
    spec = _compile(_descriptor(TerrainPrimary.ALPINE_VALLEY), extent=_SMALL_EXTENT)
    assert ELEVATION_BAND_CLAMPED_FLAG in spec.body.generation_metadata.conflicts_resolved


def test_default_band_unclamped_records_no_conflict_at_km_extent() -> None:
    """km-scale regions do not trigger the default-band clamp for any archetype."""
    spec = _compile(_descriptor(TerrainPrimary.ALPINE_VALLEY), extent=_KM_EXTENT)
    assert spec.body.generation_metadata.conflicts_resolved == ()


def test_default_band_clamp_centres_on_profile_midpoint() -> None:
    """Clamped band shrinks symmetrically around the original midpoint."""
    profile_band = TERRAIN_PROFILES[TerrainPrimary.ALPINE_VALLEY].default_elevation_band
    profile_mid = 0.5 * (profile_band[0] + profile_band[1])
    spec = _compile(_descriptor(TerrainPrimary.ALPINE_VALLEY), extent=_SMALL_EXTENT)
    band = spec.body.axes["terrain"].elevation_band
    band_mid = 0.5 * (band[0] + band[1])
    assert band_mid == pytest.approx(profile_mid)


def test_explicit_override_within_ceiling_passes_through() -> None:
    """Descriptor overrides that respect the ceiling are passed through verbatim."""
    # 50 m relief over 200 m extent for a 30 deg ceiling (max 115 m) is fine.
    custom = (100.0, 150.0)
    spec = _compile(
        _descriptor(TerrainPrimary.ALPINE_VALLEY, elevation_band=custom),
        extent=_SMALL_EXTENT,
    )
    assert spec.body.axes["terrain"].elevation_band == custom
    assert spec.body.generation_metadata.conflicts_resolved == ()


def test_explicit_override_above_ceiling_raises() -> None:
    """Descriptor overrides that exceed the ceiling fail loud, not silent."""
    too_tall = (0.0, 1000.0)  # 1000 m relief over 200 m extent
    with pytest.raises(ElevationBandImplausibleError) as info:
        _compile(
            _descriptor(TerrainPrimary.ALPINE_VALLEY, elevation_band=too_tall),
            extent=_SMALL_EXTENT,
        )
    err = info.value
    assert err.field == "terrain.elevation_band"
    assert err.supplied_band == too_tall
    assert err.region_extent_m == pytest.approx(200.0)
    assert err.max_band_m > 0.0
    assert err.max_band_m < (too_tall[1] - too_tall[0])


def test_alpine_valley_carry_over_case_now_within_ceiling() -> None:
    """The Phase-5 sanity carry-over case (200 m alpine_valley) clamps cleanly.

    Asserts the implied mean slope of the resolved band stays within the
    archetype ceiling + a small tolerance, recovering the symptom from
    ``AGENT/follow_ups/phase5-elevation-band-scaling.md``.
    """
    spec = _compile(_descriptor(TerrainPrimary.ALPINE_VALLEY), extent=_SMALL_EXTENT)
    band = spec.body.axes["terrain"].elevation_band
    implied_slope_deg = math.degrees(math.atan2(band[1] - band[0], _SMALL_EXTENT.min_extent_m))
    ceiling = MAX_MEAN_SLOPE_DEG_BY_ARCHETYPE[TerrainPrimary.ALPINE_VALLEY]
    assert implied_slope_deg <= ceiling + 1e-6


def test_compiler_version_bumped_to_phase_6_stage_a() -> None:
    """Stage A bumps COMPILER_VERSION; spec records carry the new tag."""
    spec = _compile(_descriptor(TerrainPrimary.PLAINS))
    assert spec.body.generation_metadata.compiler_version == "0.3.0"
