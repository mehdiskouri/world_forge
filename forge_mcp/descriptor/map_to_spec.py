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

import math
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
    MacroShape,
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

    from forge_mcp.descriptor.region_extent import RegionExtent

__all__ = [
    "COMPILER_VERSION",
    "ELEVATION_BAND_CLAMPED_FLAG",
    "GENERATOR_NAME",
    "MAX_MEAN_SLOPE_DEG_BY_ARCHETYPE",
    "STREAM_PROFILES",
    "TERRAIN_PROFILES",
    "ElevationBandImplausibleError",
    "StreamProfile",
    "TerrainProfile",
    "map_to_spec",
]


COMPILER_VERSION: Final[str] = "0.3.0"
"""Bumped whenever the descriptor->spec mapping changes shape or behavior.

Recorded on :class:`GenerationMetadata.compiler_version`. Bumping
invalidates content-addressed spec ids and requires regenerating the
golden spec corpus.

History:

* ``0.1.0`` — initial Phase-3 mapping (single ridged-multifractal
  generator across every archetype, no macro-shape pre-pass, no
  per-archetype smoothing, octaves frequently > 6).
* ``0.2.0`` — Phase-3 visual-quality pass: each archetype now carries a
  ``MacroShape`` silhouette + a ``ridged`` toggle + a Gaussian
  post-smooth. Octave/persistence ceilings tightened so noise
  frequencies finer than the mesh sample interval no longer alias as
  per-vertex spikes. Erosion thresholds now interpret talus and slope
  in metres-per-metre rather than per-grid-cell, so the spec's
  ``resolution_meters_per_pixel`` finally affects the post-passes.
* ``0.3.0`` — Phase-6 Stage A region-extent-aware elevation-band
  scaling. ``map_to_spec`` now requires a ``region_extent`` keyword
  argument; the default-band path silently clamps the archetype's
  ``default_elevation_band`` to the per-archetype mean-slope ceiling
  (recorded in ``GenerationMetadata.conflicts_resolved``); explicit
  descriptor overrides that violate the ceiling raise
  :class:`ElevationBandImplausibleError`. Affects spec hashes for
  every archetype whose default band exceeds the ceiling at the
  region's bounding-box footprint.
"""

GENERATOR_NAME: Final[str] = "ridged_multifractal_v1"
"""The single Phase-3 terrain generator. Pinned on every spec body."""


@dataclass(frozen=True, slots=True)
class TerrainProfile:
    """Per-archetype lookup-table entry consumed by :func:`map_to_spec`.

    Field semantics:

    - ``octaves_base`` / ``lacunarity_base`` / ``persistence_base`` /
      ``warp_base`` / ``scale_meters_base`` — base noise params,
      perturbed by ``descriptor.terrain.ruggedness`` in
      :func:`map_to_spec`.
    - ``ridged`` — whether the noise stack applies the ``1 - |perlin|``
      ridge operator per octave. True for crisp archetypes (alpine,
      canyon, mesa, coastal, volcanic); False for soft archetypes where
      ridges would manufacture sharp creases that the descriptor never
      asked for.
    - ``smooth_sigma_pixels_base`` — final Gaussian post-smooth on the
      noise field, in pixels. Used by soft archetypes to launder away
      sub-mesh-resolution detail. Ruggedness reduces smoothing.
    - ``macro_shape`` / ``macro_strength_base`` — per-archetype
      large-scale silhouette (U-trough, terraces, chasm, …). Strength
      is in ``[0, 1]``; ruggedness scales it.
    - ``erosion_iterations_base`` — base hydraulic + thermal iteration
      count; ruggedness multiplies it.
    - ``hydraulic_rain`` / ``hydraulic_evaporation`` — fixed per
      archetype.
    - ``talus_angle_degrees_base`` — fixed per archetype. The erosion
      passes interpret this against the spec's
      ``resolution_meters_per_pixel``; the value is the literal
      slope-angle threshold in degrees.
    - ``default_elevation_band`` — meters; overridden by descriptor.
    - ``notes`` — short rationale surfaced via ``forge.inspect_spec``.
    """

    octaves_base: int
    lacunarity_base: float
    persistence_base: float
    warp_base: float
    scale_meters_base: float
    ridged: bool
    smooth_sigma_pixels_base: float
    macro_shape: MacroShape
    macro_strength_base: float
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
        octaves_base=4,
        lacunarity_base=2.1,
        persistence_base=0.42,
        warp_base=0.45,
        scale_meters_base=320.0,
        ridged=True,
        smooth_sigma_pixels_base=0.4,
        macro_shape="valley_trough",
        macro_strength_base=0.7,
        erosion_iterations_base=80,
        talus_angle_degrees_base=38.0,
        hydraulic_rain=0.18,
        hydraulic_evaporation=0.06,
        default_elevation_band=(800.0, 2400.0),
        notes="U-shaped trough across the y axis with ridged peaks on the rim.",
    ),
    TerrainPrimary.ALPINE_PEAKS: TerrainProfile(
        octaves_base=5,
        lacunarity_base=2.3,
        persistence_base=0.48,
        warp_base=0.4,
        scale_meters_base=300.0,
        ridged=True,
        smooth_sigma_pixels_base=0.3,
        macro_shape="none",
        macro_strength_base=0.0,
        erosion_iterations_base=60,
        talus_angle_degrees_base=42.0,
        hydraulic_rain=0.12,
        hydraulic_evaporation=0.05,
        default_elevation_band=(1500.0, 3500.0),
        notes="Crisp ridges; thermal erosion dominates over hydraulic.",
    ),
    TerrainPrimary.ROLLING_HILLS: TerrainProfile(
        octaves_base=3,
        lacunarity_base=2.0,
        persistence_base=0.35,
        warp_base=0.25,
        scale_meters_base=320.0,
        ridged=False,
        smooth_sigma_pixels_base=1.6,
        macro_shape="none",
        macro_strength_base=0.0,
        erosion_iterations_base=30,
        talus_angle_degrees_base=22.0,
        hydraulic_rain=0.08,
        hydraulic_evaporation=0.05,
        default_elevation_band=(80.0, 320.0),
        notes="Soft fBm with heavy post-smooth; no ridges, no macro shape.",
    ),
    TerrainPrimary.PLAINS: TerrainProfile(
        octaves_base=2,
        lacunarity_base=2.0,
        persistence_base=0.3,
        warp_base=0.1,
        scale_meters_base=400.0,
        ridged=False,
        smooth_sigma_pixels_base=2.0,
        macro_shape="none",
        macro_strength_base=0.0,
        erosion_iterations_base=15,
        talus_angle_degrees_base=15.0,
        hydraulic_rain=0.05,
        hydraulic_evaporation=0.04,
        default_elevation_band=(0.0, 60.0),
        notes="Near-flat with subtle undulations; minimal erosion.",
    ),
    TerrainPrimary.DESERT_MESA: TerrainProfile(
        octaves_base=3,
        lacunarity_base=2.4,
        persistence_base=0.4,
        warp_base=0.15,
        scale_meters_base=260.0,
        ridged=True,
        smooth_sigma_pixels_base=0.6,
        macro_shape="mesa_terraces",
        macro_strength_base=0.75,
        erosion_iterations_base=40,
        talus_angle_degrees_base=55.0,
        hydraulic_rain=0.02,
        hydraulic_evaporation=0.12,
        default_elevation_band=(400.0, 900.0),
        notes="Quantised flat-topped plateaus; sharp talus risers; dry hydrology.",
    ),
    TerrainPrimary.DESERT_DUNES: TerrainProfile(
        octaves_base=3,
        lacunarity_base=2.2,
        persistence_base=0.35,
        warp_base=0.8,
        scale_meters_base=120.0,
        ridged=False,
        smooth_sigma_pixels_base=0.8,
        macro_shape="dunes_ridges",
        macro_strength_base=0.6,
        erosion_iterations_base=20,
        talus_angle_degrees_base=33.0,
        hydraulic_rain=0.01,
        hydraulic_evaporation=0.15,
        default_elevation_band=(150.0, 280.0),
        notes="Repeating sinusoidal dunes warped by domain noise; no hydraulic detail.",
    ),
    TerrainPrimary.BOREAL_LOWLAND: TerrainProfile(
        octaves_base=3,
        lacunarity_base=2.0,
        persistence_base=0.4,
        warp_base=0.3,
        scale_meters_base=320.0,
        ridged=False,
        smooth_sigma_pixels_base=1.8,
        macro_shape="lowland_lowpass",
        macro_strength_base=0.5,
        erosion_iterations_base=45,
        talus_angle_degrees_base=22.0,
        hydraulic_rain=0.2,
        hydraulic_evaporation=0.04,
        default_elevation_band=(50.0, 280.0),
        notes="Soft glaciated lowland with a faint regional tilt; gentle slopes.",
    ),
    TerrainPrimary.MARSH: TerrainProfile(
        octaves_base=2,
        lacunarity_base=2.0,
        persistence_base=0.3,
        warp_base=0.2,
        scale_meters_base=200.0,
        ridged=False,
        smooth_sigma_pixels_base=2.4,
        macro_shape="lowland_lowpass",
        macro_strength_base=0.3,
        erosion_iterations_base=25,
        talus_angle_degrees_base=12.0,
        hydraulic_rain=0.25,
        hydraulic_evaporation=0.03,
        default_elevation_band=(0.0, 25.0),
        notes="Near-flat low-lying terrain; capillary channels; very wet.",
    ),
    TerrainPrimary.VOLCANIC_CONE: TerrainProfile(
        octaves_base=4,
        lacunarity_base=2.2,
        persistence_base=0.45,
        warp_base=0.2,
        scale_meters_base=280.0,
        ridged=True,
        smooth_sigma_pixels_base=0.5,
        macro_shape="volcanic_cone",
        macro_strength_base=0.7,
        erosion_iterations_base=35,
        talus_angle_degrees_base=40.0,
        hydraulic_rain=0.08,
        hydraulic_evaporation=0.06,
        default_elevation_band=(300.0, 2100.0),
        notes="Single dominant cone; radial drainage; moderate thermal erosion.",
    ),
    TerrainPrimary.COASTAL_CLIFFS: TerrainProfile(
        octaves_base=4,
        lacunarity_base=2.3,
        persistence_base=0.45,
        warp_base=0.3,
        scale_meters_base=200.0,
        ridged=True,
        smooth_sigma_pixels_base=0.4,
        macro_shape="coastal_cliff",
        macro_strength_base=0.7,
        erosion_iterations_base=50,
        talus_angle_degrees_base=60.0,
        hydraulic_rain=0.15,
        hydraulic_evaporation=0.07,
        default_elevation_band=(0.0, 180.0),
        notes="Sea-floor / inland plateau dichotomy; near-vertical talus along the cliff.",
    ),
    TerrainPrimary.RIVER_VALLEY: TerrainProfile(
        octaves_base=4,
        lacunarity_base=2.1,
        persistence_base=0.42,
        warp_base=0.4,
        scale_meters_base=300.0,
        ridged=True,
        smooth_sigma_pixels_base=0.6,
        macro_shape="valley_trough",
        macro_strength_base=0.5,
        erosion_iterations_base=70,
        talus_angle_degrees_base=28.0,
        hydraulic_rain=0.22,
        hydraulic_evaporation=0.05,
        default_elevation_band=(60.0, 420.0),
        notes="Broad fluvial trough with terraces; hydraulic carving dominant.",
    ),
    TerrainPrimary.CANYON: TerrainProfile(
        octaves_base=4,
        lacunarity_base=2.4,
        persistence_base=0.45,
        warp_base=0.25,
        scale_meters_base=260.0,
        ridged=True,
        smooth_sigma_pixels_base=0.4,
        macro_shape="canyon_chasm",
        macro_strength_base=0.85,
        erosion_iterations_base=90,
        talus_angle_degrees_base=65.0,
        hydraulic_rain=0.1,
        hydraulic_evaporation=0.08,
        default_elevation_band=(200.0, 1400.0),
        notes="Deep narrow chasm cut diagonally; near-vertical walls.",
    ),
}

STREAM_PROFILES: Final[Mapping[StreamCharacter, StreamProfile]] = {
    StreamCharacter.ALPINE_CREEK: StreamProfile(width_meters=3.0, carving_depth=2.0),
    StreamCharacter.MEANDERING_RIVER: StreamProfile(width_meters=18.0, carving_depth=4.0),
    StreamCharacter.DRY_WASH: StreamProfile(width_meters=8.0, carving_depth=1.0),
    StreamCharacter.NONE: StreamProfile(width_meters=0.0, carving_depth=0.0),
}


# ---------------------------------------------------------------------------
# Slope-plausibility ceilings — Phase 6 Stage A
# ---------------------------------------------------------------------------


MAX_MEAN_SLOPE_DEG_BY_ARCHETYPE: Final[Mapping[TerrainPrimary, float]] = {
    # Cliff-tolerant archetypes: dramatic talus is on-spec; allow up to ~55 degrees.
    TerrainPrimary.ALPINE_PEAKS: 55.0,
    TerrainPrimary.CANYON: 55.0,
    TerrainPrimary.COASTAL_CLIFFS: 55.0,
    TerrainPrimary.VOLCANIC_CONE: 55.0,
    # Standard archetypes: dramatic but still walkable; ~30 degrees mean.
    TerrainPrimary.ALPINE_VALLEY: 30.0,
    TerrainPrimary.DESERT_MESA: 30.0,
    TerrainPrimary.BOREAL_LOWLAND: 30.0,
    TerrainPrimary.MARSH: 30.0,
    TerrainPrimary.RIVER_VALLEY: 30.0,
    # Gentle archetypes: anything steeper would contradict the descriptor outright.
    TerrainPrimary.ROLLING_HILLS: 25.0,
    TerrainPrimary.PLAINS: 25.0,
    TerrainPrimary.DESERT_DUNES: 25.0,
}
"""Per-archetype mean-slope ceiling in degrees, used by
:func:`_resolve_elevation_band` to clamp the elevation band to a
slope-plausible relief for the region's horizontal footprint.

The ceiling is *aggressive*: it bounds the mean slope the resulting
heightmap is allowed to imply across the polygon's bounding box, not
the peak slope. Cliff-tolerant archetypes still have plenty of room
to grow near-vertical talus through the macro-shape pre-pass and
ridged noise; gentle archetypes are kept gentle so a small descriptor
override does not turn rolling hills into a wall.

Tested for exhaustiveness in
``tests/descriptor/test_map_to_spec.py::test_slope_ceiling_table_is_exhaustive``.
"""


ELEVATION_BAND_CLAMPED_FLAG: Final[str] = "elevation_band_clamped_to_extent"
"""Token recorded in :attr:`GenerationMetadata.conflicts_resolved` when
the default-band path silently shrunk the archetype's default
elevation band to fit the region extent. Explicit overrides do *not*
clamp silently — they raise :class:`ElevationBandImplausibleError`.
"""


class ElevationBandImplausibleError(ValueError):
    """Raised when ``descriptor.terrain.elevation_band`` is too tall for the region.

    The error carries the offending field path, the supplied band,
    the region extent, and the maximum band height permitted by the
    archetype's slope ceiling so callers can surface it as a
    structured validation envelope.

    Attributes:
        field: JSON pointer-ish path to the offending descriptor field.
        region_extent_m: Shorter polygon-bounding-box axis in metres.
        max_band_m: Maximum band height in metres permitted at this
            extent for the descriptor's archetype.
        supplied_band: The descriptor-supplied ``(low, high)`` band.
    """

    def __init__(
        self,
        *,
        field: str,
        region_extent_m: float,
        max_band_m: float,
        supplied_band: tuple[float, float],
    ) -> None:
        """Initialise the error with the offending field + clamp metadata."""
        self.field = field
        self.region_extent_m = region_extent_m
        self.max_band_m = max_band_m
        self.supplied_band = supplied_band
        height = supplied_band[1] - supplied_band[0]
        msg = (
            f"{field} = {supplied_band!r} implies {height:.1f} m of relief over a "
            f"{region_extent_m:.1f} m extent, exceeding the per-archetype mean-slope "
            f"ceiling (max permitted band height {max_band_m:.1f} m). Either widen "
            f"the region polygon or reduce the elevation band."
        )
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Modulators
# ---------------------------------------------------------------------------

_RUGGEDNESS_DEFAULT: Final[float] = 0.5
_OCTAVE_BONUS_MAX: Final[int] = 2
_OCTAVE_CEILING: Final[int] = 5
_PERSISTENCE_BONUS_MAX: Final[float] = 0.08
_PERSISTENCE_CEILING: Final[float] = 0.5
_EROSION_MULTIPLIER_MIN: Final[float] = 1.0
_EROSION_MULTIPLIER_RANGE: Final[float] = 0.5
_SMOOTH_REDUCTION_RANGE: Final[float] = 0.6
"""Ruggedness reduces ``smooth_sigma_pixels`` by up to this fraction
(rugged terrain wants its detail preserved; soft archetypes do not)."""
_MACRO_STRENGTH_BONUS_MAX: Final[float] = 0.15
"""Ruggedness pushes macro-shape strength up by up to this much, never
past 1.0. Pinned conservatively because the per-archetype base values
already encode the expected strength."""
_DEFAULT_RESOLUTION_M_PER_PX: Final[float] = 2.0


def _ruggedness(descriptor: StructuredDescriptor) -> float:
    """Return the descriptor's ruggedness in [0, 1], defaulting to 0.5."""
    value = descriptor.terrain.ruggedness
    return _RUGGEDNESS_DEFAULT if value is None else value


def _modulated_params(
    profile: TerrainProfile,
    ruggedness: float,
) -> TerrainGeneratorParams:
    """Apply ruggedness modulation to the profile's noise params.

    Octaves are capped at :data:`_OCTAVE_CEILING` so the finest octave
    cell never falls below the realizer mesh sample interval (a 256²
    mesh over a 1024 m vista samples one vertex per 4 m; with the
    profile scale_meters at 200-400 m, 5 octaves leaves the finest
    cell at 6-12 m, which the mesh can resolve without aliasing). The
    ruggedness ``smooth_sigma`` reduction lets crisp terrain keep its
    detail while soft terrain stays soft.
    """
    octaves = min(
        profile.octaves_base + round(ruggedness * _OCTAVE_BONUS_MAX),
        _OCTAVE_CEILING,
    )
    persistence = min(
        profile.persistence_base + ruggedness * _PERSISTENCE_BONUS_MAX,
        _PERSISTENCE_CEILING,
    )
    smooth_reduction = ruggedness * _SMOOTH_REDUCTION_RANGE
    smooth_sigma = max(
        0.0,
        profile.smooth_sigma_pixels_base * (1.0 - smooth_reduction),
    )
    return TerrainGeneratorParams(
        octaves=octaves,
        lacunarity=profile.lacunarity_base,
        persistence=persistence,
        warp=profile.warp_base,
        scale_meters=profile.scale_meters_base,
        ridged=profile.ridged,
        smooth_sigma_pixels=smooth_sigma,
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


def _max_band_meters(
    primary: TerrainPrimary,
    region_extent: RegionExtent,
) -> float:
    """Return the maximum band height plausible for ``primary`` at this extent.

    Computed as ``min_extent_m * tan(max_mean_slope_deg)``; a band
    taller than this implies a mean slope exceeding the per-archetype
    ceiling along the polygon's shortest axis.
    """
    ceiling_deg = MAX_MEAN_SLOPE_DEG_BY_ARCHETYPE[primary]
    return region_extent.min_extent_m * math.tan(math.radians(ceiling_deg))


def _clamp_band_to_extent(
    band: tuple[float, float],
    max_height: float,
) -> tuple[float, float]:
    """Symmetrically shrink ``band`` around its midpoint to ``max_height``.

    Bands already shorter than ``max_height`` are returned unchanged.
    """
    height = band[1] - band[0]
    if height <= max_height:
        return band
    midpoint = 0.5 * (band[0] + band[1])
    half = 0.5 * max_height
    return (midpoint - half, midpoint + half)


def _resolve_elevation_band(
    descriptor: StructuredDescriptor,
    profile: TerrainProfile,
    region_extent: RegionExtent,
) -> tuple[tuple[float, float], tuple[str, ...]]:
    """Resolve the spec's elevation band, clamped to the region extent.

    Two paths:

    * **Default-band path** (``descriptor.terrain.elevation_band`` is
      ``None``): start from ``profile.default_elevation_band`` and
      silently shrink it around its midpoint to fit the per-archetype
      slope ceiling. The clamp event is recorded in
      ``conflicts_resolved`` for traceability.
    * **Explicit-override path** (descriptor supplies a band): if it
      already fits the ceiling, pass it through unchanged. If it
      exceeds the ceiling, raise
      :class:`ElevationBandImplausibleError` so the caller can fail
      loud at the validation boundary instead of silently producing
      something the descriptor never asked for.

    Returns:
        A ``(band, conflicts_resolved)`` pair. The conflicts tuple is
        empty unless the default-band path triggered a clamp.
    """
    primary = descriptor.terrain.primary
    max_height = _max_band_meters(primary, region_extent)
    supplied = descriptor.terrain.elevation_band
    if supplied is not None:
        if (supplied[1] - supplied[0]) > max_height:
            raise ElevationBandImplausibleError(
                field="terrain.elevation_band",
                region_extent_m=region_extent.min_extent_m,
                max_band_m=max_height,
                supplied_band=supplied,
            )
        return supplied, ()
    clamped = _clamp_band_to_extent(profile.default_elevation_band, max_height)
    if clamped == profile.default_elevation_band:
        return clamped, ()
    return clamped, (ELEVATION_BAND_CLAMPED_FLAG,)


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


def map_to_spec(  # noqa: PLR0913 - one assembly site; all params are named keyword-only.
    descriptor: StructuredDescriptor,
    seed: int,  # noqa: ARG001 - reserved for Phase-6 anchor selection; pinned now for stability
    *,
    region_extent: RegionExtent,
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
        region_extent: Polygon-bounding-box footprint in metres. Used
            by :func:`_resolve_elevation_band` to clamp the elevation
            band to a slope-plausible relief.
        blender_version: Pinned Blender patch (e.g. ``"5.0.2"``).
        bpy_hypergraph_version: Pinned bpy hypergraph data version.
        now: Wall-clock for ``SpecRecord.created_at``; chosen by caller.

    Returns:
        A :class:`SpecRecord` whose ``spec_id`` is the BLAKE2b-6 hex of
        its canonical-JSON body.

    Raises:
        ElevationBandImplausibleError: When the descriptor supplies an
            explicit ``elevation_band`` whose height exceeds the
            archetype's mean-slope ceiling at the given region extent.
    """
    profile = TERRAIN_PROFILES[descriptor.terrain.primary]
    ruggedness = _ruggedness(descriptor)
    elevation_band, conflicts_resolved = _resolve_elevation_band(
        descriptor,
        profile,
        region_extent,
    )
    macro_strength = min(
        1.0,
        profile.macro_strength_base + ruggedness * _MACRO_STRENGTH_BONUS_MAX,
    )

    axis = TerrainAxisSpec(
        params=_modulated_params(profile, ruggedness),
        post_passes=_post_passes(profile, ruggedness),
        feature_injectors=_stream_injectors(descriptor.hydrology),
        elevation_band=elevation_band,
        resolution_meters_per_pixel=_DEFAULT_RESOLUTION_M_PER_PX,
        macro_shape=profile.macro_shape,
        macro_strength=macro_strength,
    )
    body = SpecBody(
        axes={"terrain": axis},
        generation_metadata=GenerationMetadata(
            compiler_version=COMPILER_VERSION,
            generators_used=(GENERATOR_NAME,),
            bpy_hypergraph_version=bpy_hypergraph_version,
            blender_version=blender_version,
            conflicts_resolved=conflicts_resolved,
        ),
    )
    return SpecRecord(
        spec_id=_content_address(body),
        descriptor=descriptor,
        body=body,
        created_at=now,
    )
