"""Pure-Python deterministic mapping ``StructuredDescriptor + seed -> SpecRecord``.

Architecture §4.2 lookup-table-driven compilation. No LLM, no IO. The
``map_to_spec`` function is referentially transparent — given identical
inputs (descriptor + seed + version pins + ``now``), it returns a
byte-identical :class:`SpecRecord`.

The output ``spec_id`` is content-addressed: BLAKE2b over the
canonical-JSON serialization of the spec body. Two regions whose
descriptors and seeds happen to match therefore share a spec id —
intentional dedup property documented in
``AGENT/dev_phases/phase3.md`` Stage A.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2b
from typing import TYPE_CHECKING, Final

from forge_mcp._io.atomic import dump_json
from forge_mcp.descriptor.schema import (
    Hydrology,
    StreamCharacter,
    StructuredDescriptor,
    TerrainPrimary,
)
from forge_mcp.project.schemas import (
    GenerationMetadata,
    HydraulicErosionPass,
    PostPass,
    SpecBody,
    SpecId,
    SpecRecord,
    StreamFeatureInjector,
    TerrainAxisSpec,
    TerrainGeneratorParams,
    ThermalErosionPass,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

__all__ = [
    "COMPILER_VERSION",
    "GENERATOR_NAME",
    "STREAM_PROFILES",
    "TERRAIN_PROFILES",
    "StreamProfile",
    "TerrainProfile",
    "map_to_spec",
]


COMPILER_VERSION: Final[str] = "0.1.0"
"""Bumped whenever the descriptor->spec mapping changes shape or behavior.

Recorded on :class:`GenerationMetadata.compiler_version`. Bumping
invalidates content-addressed spec ids and requires regenerating the
golden spec corpus.
"""

GENERATOR_NAME: Final[str] = "ridged_multifractal_v1"
"""The single Phase-3 terrain generator. Pinned on every spec body."""


@dataclass(frozen=True, slots=True)
class TerrainProfile:
    """Per-archetype lookup-table entry consumed by :func:`map_to_spec`.

    Field semantics:

    - ``octaves_base`` / ``lacunarity_base`` / ``persistence_base`` /
      ``warp_base`` / ``scale_meters_base`` — base ridged-multifractal
      params, perturbed by descriptor.terrain.ruggedness in
      :func:`map_to_spec`.
    - ``erosion_iterations_base`` — base hydraulic + thermal iteration
      count; ruggedness multiplies it.
    - ``hydraulic_rain`` / ``hydraulic_evaporation`` — fixed per
      archetype.
    - ``talus_angle_degrees_base`` — fixed per archetype.
    - ``default_elevation_band`` — meters; overridden by descriptor.
    - ``notes`` — short rationale surfaced via ``forge.inspect_spec``.
    """

    octaves_base: int
    lacunarity_base: float
    persistence_base: float
    warp_base: float
    scale_meters_base: float
    erosion_iterations_base: int
    talus_angle_degrees_base: float
    hydraulic_rain: float
    hydraulic_evaporation: float
    default_elevation_band: tuple[float, float]
    notes: str


@dataclass(frozen=True, slots=True)
class StreamProfile:
    """Per-character lookup for stream feature injection."""

    width_meters: float
    carving_depth: float


# ---------------------------------------------------------------------------
# Lookup tables — the entire descriptor-> spec mapping lives here.
# ---------------------------------------------------------------------------

TERRAIN_PROFILES: Final[Mapping[TerrainPrimary, TerrainProfile]] = {
    TerrainPrimary.ALPINE_VALLEY: TerrainProfile(
        octaves_base=6,
        lacunarity_base=2.1,
        persistence_base=0.55,
        warp_base=0.6,
        scale_meters_base=180.0,
        erosion_iterations_base=80,
        talus_angle_degrees_base=38.0,
        hydraulic_rain=0.18,
        hydraulic_evaporation=0.06,
        default_elevation_band=(800.0, 2400.0),
        notes="Steep U-shaped relief; strong hydraulic carving along the valley floor.",
    ),
    TerrainPrimary.ALPINE_PEAKS: TerrainProfile(
        octaves_base=7,
        lacunarity_base=2.3,
        persistence_base=0.6,
        warp_base=0.4,
        scale_meters_base=220.0,
        erosion_iterations_base=60,
        talus_angle_degrees_base=42.0,
        hydraulic_rain=0.12,
        hydraulic_evaporation=0.05,
        default_elevation_band=(1500.0, 3500.0),
        notes="High-frequency ridges; thermal erosion dominates over hydraulic.",
    ),
    TerrainPrimary.ROLLING_HILLS: TerrainProfile(
        octaves_base=4,
        lacunarity_base=2.0,
        persistence_base=0.45,
        warp_base=0.3,
        scale_meters_base=140.0,
        erosion_iterations_base=30,
        talus_angle_degrees_base=28.0,
        hydraulic_rain=0.08,
        hydraulic_evaporation=0.05,
        default_elevation_band=(80.0, 320.0),
        notes="Soft rolling relief, moderate erosion, no sharp ridges.",
    ),
    TerrainPrimary.PLAINS: TerrainProfile(
        octaves_base=3,
        lacunarity_base=2.0,
        persistence_base=0.35,
        warp_base=0.15,
        scale_meters_base=200.0,
        erosion_iterations_base=15,
        talus_angle_degrees_base=20.0,
        hydraulic_rain=0.05,
        hydraulic_evaporation=0.04,
        default_elevation_band=(0.0, 60.0),
        notes="Low-relief flat with subtle undulations; minimal erosion.",
    ),
    TerrainPrimary.DESERT_MESA: TerrainProfile(
        octaves_base=5,
        lacunarity_base=2.4,
        persistence_base=0.5,
        warp_base=0.2,
        scale_meters_base=160.0,
        erosion_iterations_base=40,
        talus_angle_degrees_base=55.0,
        hydraulic_rain=0.02,
        hydraulic_evaporation=0.12,
        default_elevation_band=(400.0, 900.0),
        notes="Flat-topped step plateaus with sharp talus risers; dry hydrology.",
    ),
    TerrainPrimary.DESERT_DUNES: TerrainProfile(
        octaves_base=4,
        lacunarity_base=2.2,
        persistence_base=0.4,
        warp_base=0.9,
        scale_meters_base=80.0,
        erosion_iterations_base=20,
        talus_angle_degrees_base=33.0,
        hydraulic_rain=0.01,
        hydraulic_evaporation=0.15,
        default_elevation_band=(150.0, 280.0),
        notes="Wind-warped low ridges; no hydraulic detail; aeolian look.",
    ),
    TerrainPrimary.BOREAL_LOWLAND: TerrainProfile(
        octaves_base=4,
        lacunarity_base=2.0,
        persistence_base=0.45,
        warp_base=0.35,
        scale_meters_base=160.0,
        erosion_iterations_base=45,
        talus_angle_degrees_base=25.0,
        hydraulic_rain=0.2,
        hydraulic_evaporation=0.04,
        default_elevation_band=(50.0, 280.0),
        notes="Soft glaciated lowland; abundant hydrology; gentle slopes.",
    ),
    TerrainPrimary.MARSH: TerrainProfile(
        octaves_base=3,
        lacunarity_base=2.0,
        persistence_base=0.3,
        warp_base=0.2,
        scale_meters_base=110.0,
        erosion_iterations_base=25,
        talus_angle_degrees_base=18.0,
        hydraulic_rain=0.25,
        hydraulic_evaporation=0.03,
        default_elevation_band=(0.0, 25.0),
        notes="Near-flat with capillary channels; very wet; very low relief.",
    ),
    TerrainPrimary.VOLCANIC_CONE: TerrainProfile(
        octaves_base=5,
        lacunarity_base=2.2,
        persistence_base=0.55,
        warp_base=0.25,
        scale_meters_base=200.0,
        erosion_iterations_base=35,
        talus_angle_degrees_base=40.0,
        hydraulic_rain=0.08,
        hydraulic_evaporation=0.06,
        default_elevation_band=(300.0, 2100.0),
        notes="Single dominant cone; radial drainage; moderate thermal erosion.",
    ),
    TerrainPrimary.COASTAL_CLIFFS: TerrainProfile(
        octaves_base=5,
        lacunarity_base=2.3,
        persistence_base=0.5,
        warp_base=0.3,
        scale_meters_base=120.0,
        erosion_iterations_base=50,
        talus_angle_degrees_base=60.0,
        hydraulic_rain=0.15,
        hydraulic_evaporation=0.07,
        default_elevation_band=(0.0, 180.0),
        notes="Sharp coastal escarpment; near-vertical talus; strong wave-cut feel.",
    ),
    TerrainPrimary.RIVER_VALLEY: TerrainProfile(
        octaves_base=5,
        lacunarity_base=2.1,
        persistence_base=0.5,
        warp_base=0.45,
        scale_meters_base=170.0,
        erosion_iterations_base=70,
        talus_angle_degrees_base=30.0,
        hydraulic_rain=0.22,
        hydraulic_evaporation=0.05,
        default_elevation_band=(60.0, 420.0),
        notes="Broad fluvial valley with terraces; hydraulic carving dominant.",
    ),
    TerrainPrimary.CANYON: TerrainProfile(
        octaves_base=6,
        lacunarity_base=2.5,
        persistence_base=0.6,
        warp_base=0.35,
        scale_meters_base=140.0,
        erosion_iterations_base=90,
        talus_angle_degrees_base=65.0,
        hydraulic_rain=0.1,
        hydraulic_evaporation=0.08,
        default_elevation_band=(200.0, 1400.0),
        notes="Deep narrow chasm with sheer walls; hydraulic + thermal sharpening.",
    ),
}

STREAM_PROFILES: Final[Mapping[StreamCharacter, StreamProfile]] = {
    StreamCharacter.ALPINE_CREEK: StreamProfile(width_meters=3.0, carving_depth=2.0),
    StreamCharacter.MEANDERING_RIVER: StreamProfile(width_meters=18.0, carving_depth=4.0),
    StreamCharacter.DRY_WASH: StreamProfile(width_meters=8.0, carving_depth=1.0),
    StreamCharacter.NONE: StreamProfile(width_meters=0.0, carving_depth=0.0),
}


# ---------------------------------------------------------------------------
# Modulators
# ---------------------------------------------------------------------------

_RUGGEDNESS_DEFAULT: Final[float] = 0.5
_OCTAVE_BONUS_MAX: Final[int] = 3
_PERSISTENCE_BONUS_MAX: Final[float] = 0.15
_PERSISTENCE_CEILING: Final[float] = 0.95
_EROSION_MULTIPLIER_MIN: Final[float] = 1.0
_EROSION_MULTIPLIER_RANGE: Final[float] = 0.5
_DEFAULT_RESOLUTION_M_PER_PX: Final[float] = 2.0


def _ruggedness(descriptor: StructuredDescriptor) -> float:
    """Return the descriptor's ruggedness in [0, 1], defaulting to 0.5."""
    value = descriptor.terrain.ruggedness
    return _RUGGEDNESS_DEFAULT if value is None else value


def _modulated_params(
    profile: TerrainProfile,
    ruggedness: float,
) -> TerrainGeneratorParams:
    """Apply ruggedness modulation to the profile's noise params."""
    octaves = profile.octaves_base + round(ruggedness * _OCTAVE_BONUS_MAX)
    persistence = min(
        profile.persistence_base + ruggedness * _PERSISTENCE_BONUS_MAX,
        _PERSISTENCE_CEILING,
    )
    return TerrainGeneratorParams(
        octaves=octaves,
        lacunarity=profile.lacunarity_base,
        persistence=persistence,
        warp=profile.warp_base,
        scale_meters=profile.scale_meters_base,
    )


def _post_passes(
    profile: TerrainProfile,
    ruggedness: float,
) -> tuple[PostPass, ...]:
    """Return the (hydraulic, thermal) post-pass tuple, ruggedness-scaled."""
    multiplier = _EROSION_MULTIPLIER_MIN + ruggedness * _EROSION_MULTIPLIER_RANGE
    iterations = max(1, round(profile.erosion_iterations_base * multiplier))
    return (
        HydraulicErosionPass(
            iterations=iterations,
            rain=profile.hydraulic_rain,
            evaporation=profile.hydraulic_evaporation,
        ),
        ThermalErosionPass(
            iterations=iterations,
            talus_angle_degrees=profile.talus_angle_degrees_base,
        ),
    )


def _stream_injectors(hydrology: Hydrology | None) -> tuple[StreamFeatureInjector, ...]:
    """Return a single stream injector if the descriptor calls for one."""
    if hydrology is None or not hydrology.has_stream:
        return ()
    character = hydrology.stream_character
    if character is None or character is StreamCharacter.NONE:
        return ()
    sp = STREAM_PROFILES[character]
    return (StreamFeatureInjector(width_meters=sp.width_meters, carving_depth=sp.carving_depth),)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _content_address(body: SpecBody) -> SpecId:
    """Derive a deterministic ``spec_<6-hex>`` id from the body's canonical JSON."""
    digest = blake2b(dump_json(body).encode("utf-8"), digest_size=6).hexdigest()
    return SpecId(f"spec_{digest}")


def map_to_spec(
    descriptor: StructuredDescriptor,
    seed: int,  # noqa: ARG001 - reserved for Phase-6 anchor selection; pinned now for stability
    *,
    blender_version: str,
    bpy_hypergraph_version: str,
    now: datetime,
) -> SpecRecord:
    """Compile ``descriptor`` into a fully-typed :class:`SpecRecord`.

    Pure function: identical inputs (including ``now``) produce a
    byte-identical output. Caller is responsible for choosing ``now``
    deterministically when reproducibility across runs is required.

    The ``seed`` is reserved on the function signature so callers can
    already pin it; the Phase-3 mapping itself does not consume it
    (anchor selection is the only seed-consumer and lands in Phase 6).
    Keeping it on the signature now means the eventual addition is not
    a signature break.

    Args:
        descriptor: Validated structured descriptor.
        seed: Region seed; persisted for downstream generators.
        blender_version: Pinned Blender patch (e.g. ``"5.0.2"``).
        bpy_hypergraph_version: Pinned bpy hypergraph data version.
        now: Wall-clock for ``SpecRecord.created_at``; chosen by caller.

    Returns:
        A :class:`SpecRecord` whose ``spec_id`` is the BLAKE2b-6 hex of
        its canonical-JSON body.
    """
    profile = TERRAIN_PROFILES[descriptor.terrain.primary]
    ruggedness = _ruggedness(descriptor)
    elevation_band = descriptor.terrain.elevation_band or profile.default_elevation_band

    axis = TerrainAxisSpec(
        params=_modulated_params(profile, ruggedness),
        post_passes=_post_passes(profile, ruggedness),
        feature_injectors=_stream_injectors(descriptor.hydrology),
        elevation_band=elevation_band,
        resolution_meters_per_pixel=_DEFAULT_RESOLUTION_M_PER_PX,
    )
    body = SpecBody(
        axes={"terrain": axis},
        generation_metadata=GenerationMetadata(
            compiler_version=COMPILER_VERSION,
            generators_used=(GENERATOR_NAME,),
            bpy_hypergraph_version=bpy_hypergraph_version,
            blender_version=blender_version,
        ),
    )
    return SpecRecord(
        spec_id=_content_address(body),
        descriptor=descriptor,
        body=body,
        created_at=now,
    )
