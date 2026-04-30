"""Tests for :mod:`forge_mcp.descriptor.map_to_spec`.

Phase 3 Stage C: every :class:`TerrainPrimary` profile is exercised;
ruggedness modulation, elevation-band override, hydrology presence /
absence, and the content-addressing contract are all pinned.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from forge_mcp.descriptor.map_to_spec import (
    COMPILER_VERSION,
    GENERATOR_NAME,
    TERRAIN_PROFILES,
    map_to_spec,
)
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


def _compile(d: StructuredDescriptor, *, seed: int = SEED) -> SpecRecord:
    return map_to_spec(
        d,
        seed,
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
    a = map_to_spec(base, SEED, blender_version="5.0.2", bpy_hypergraph_version=BPY_HG, now=NOW)
    b = map_to_spec(base, SEED, blender_version="5.0.3", bpy_hypergraph_version=BPY_HG, now=NOW)
    assert a.spec_id != b.spec_id


def test_spec_id_independent_of_seed_in_phase_3() -> None:
    """Seed is reserved on the signature but unused by the Phase-3 mapper."""
    base = _descriptor(TerrainPrimary.PLAINS)
    a = _compile(base, seed=1)
    b = _compile(base, seed=2)
    assert a.spec_id == b.spec_id
