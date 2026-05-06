"""Terrain orchestrator: SpecRecord -> Heightmap + StreamGeometry.

Wires together :mod:`forge_mcp.generate.noise`,
:mod:`forge_mcp.generate.macro_shape`,
:mod:`forge_mcp.generate.erosion`, and
:mod:`forge_mcp.generate.stream`. Pure (no IO); the caller persists
results via :class:`forge_mcp.project.service.ProjectService`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from forge_mcp.generate import boundary_conform, erosion, macro_shape, noise, stream
from forge_mcp.generate.deterministic import make_rng
from forge_mcp.generate.heightmap import Heightmap
from forge_mcp.project.schemas import (
    HydraulicErosionPass,
    StreamFeatureInjector,
    ThermalErosionPass,
)

if TYPE_CHECKING:
    from forge_mcp.generate.boundary_conform import EdgeSide
    from forge_mcp.generate.stream import StreamGeometry
    from forge_mcp.project.schemas import (
        FeatureInjector,
        PostPass,
        SpecRecord,
        TerrainAxisSpec,
    )


@dataclass(frozen=True, slots=True)
class EdgeContract:
    """Per-side edge-conform input bag.

    The terrain orchestrator accepts one :class:`EdgeContract` per
    heightmap side; the edge-conform pass blends the heightmap toward
    ``samples`` within ``inland_falloff_m`` of the side using a
    smoothstep falloff. ``contract_id`` is plumbed through to
    ``generators_used`` so realization traces record which boundary
    each pass came from.
    """

    side: EdgeSide
    samples: tuple[float, ...]
    inland_falloff_m: float
    contract_id: str


@dataclass(frozen=True, slots=True)
class BoundaryConditions:
    """Optional boundary-driven inputs for :func:`run`.

    Phase 6 Stage C. ``edge_contracts`` is the only field today; Stage H
    integration tests will exercise the stream-anchor override path
    once Phase 3's stream injector grows the explicit-anchor entry
    point. ``conflicts_resolved`` carries any conflict tags the
    boundary-application step recorded so the spec metadata picks
    them up.
    """

    edge_contracts: tuple[EdgeContract, ...] = ()
    conflicts_resolved: tuple[str, ...] = ()

    @classmethod
    def empty(cls) -> BoundaryConditions:
        """Return the no-op :class:`BoundaryConditions` instance."""
        return cls()


@dataclass(frozen=True, slots=True)
class TerrainGenerationResult:
    """Output of :func:`run` — heightmap, optional stream, generators used.

    ``generators_used`` records every named pass that contributed to
    the heightmap, in execution order. The orchestrator hands this
    tuple to :class:`forge_mcp.project.schemas.GenerationMetadata` so
    the persisted spec carries an exact provenance trail.
    """

    heightmap: Heightmap
    stream_geometry: StreamGeometry | None
    generators_used: tuple[str, ...]


def _apply_post_pass(
    heightmap: Heightmap,
    pass_spec: PostPass,
    *,
    seed: int,
) -> tuple[Heightmap, str]:
    """Dispatch one post-pass; return ``(new_heightmap, generator_name)``."""
    if isinstance(pass_spec, HydraulicErosionPass):
        rng = make_rng(seed, purpose="erosion.hydraulic")
        new_data = erosion.hydraulic(
            heightmap.data,
            iterations=pass_spec.iterations,
            rain=pass_spec.rain,
            evaporation=pass_spec.evaporation,
            rng=rng,
            resolution_meters_per_pixel=heightmap.resolution_meters_per_pixel,
        )
        return (
            Heightmap(
                data=new_data,
                resolution_meters_per_pixel=heightmap.resolution_meters_per_pixel,
                origin=heightmap.origin,
                elevation_band=heightmap.elevation_band,
            ),
            "erosion.hydraulic",
        )
    if isinstance(pass_spec, ThermalErosionPass):
        rng = make_rng(seed, purpose="erosion.thermal")
        new_data = erosion.thermal(
            heightmap.data,
            iterations=pass_spec.iterations,
            talus_angle_degrees=pass_spec.talus_angle_degrees,
            rng=rng,
            resolution_meters_per_pixel=heightmap.resolution_meters_per_pixel,
        )
        return (
            Heightmap(
                data=new_data,
                resolution_meters_per_pixel=heightmap.resolution_meters_per_pixel,
                origin=heightmap.origin,
                elevation_band=heightmap.elevation_band,
            ),
            "erosion.thermal",
        )
    assert_never(pass_spec)


def _apply_feature_injector(
    heightmap: Heightmap,
    injector: FeatureInjector,
    *,
    seed: int,
) -> tuple[Heightmap, StreamGeometry | None, str]:
    """Dispatch one feature injector; return updated heightmap + name."""
    if isinstance(injector, StreamFeatureInjector):
        new_hm, geometry = stream.inject_stream(heightmap, injector, seed=seed)
        return (new_hm, geometry, "stream.injector")
    assert_never(injector)


def _apply_elevation_band(
    heightmap: Heightmap,
    band: tuple[float, float],
) -> Heightmap:
    """Linearly remap a ``[0, 1]``-valued grid into ``[lo, hi]`` meters."""
    lo, hi = band
    rescaled = (heightmap.data * (hi - lo) + lo).astype(heightmap.data.dtype, copy=False)
    return Heightmap(
        data=rescaled,
        resolution_meters_per_pixel=heightmap.resolution_meters_per_pixel,
        origin=heightmap.origin,
        elevation_band=band,
    )


def _shape_from_spec(axis: TerrainAxisSpec) -> tuple[int, int]:
    """Square heightmap whose side covers a fixed number of pixels.

    Phase-3 hard-codes a 256-pixel side (≈512 m at the default 2 m/px
    resolution). Phase-4 plugs in the real region-bounds → pixel-shape
    derivation. The stub keeps tests fast and is invisible to the spec
    contract.
    """
    _ = axis
    return (256, 256)


def run(
    spec: SpecRecord,
    *,
    seed: int,
    shape: tuple[int, int] | None = None,
    boundary_conditions: BoundaryConditions | None = None,
) -> TerrainGenerationResult:
    """Materialise ``spec`` into a heightmap and optional stream geometry.

    ``shape`` defaults to a fixed Phase-3 grid; tests override it for
    speed. Determinism is keyed off ``seed`` (the region's seed); every
    RNG inside the pipeline derives from that single integer via
    :func:`make_rng` and a unique purpose tag, so identical (spec,
    seed) pairs produce byte-identical heightmaps.

    When ``boundary_conditions`` is supplied (Phase 6 Stage C), the
    edge-conform pass blends the heightmap toward each contract's
    samples after the elevation-band remap and before erosion, so the
    erosion passes reshape blended terrain rather than overwriting the
    contract.
    """
    axis = spec.body.axes["terrain"]
    grid_shape = shape if shape is not None else _shape_from_spec(axis)
    base = noise.ridged_multifractal(
        grid_shape,
        seed=seed,
        octaves=axis.params.octaves,
        lacunarity=axis.params.lacunarity,
        persistence=axis.params.persistence,
        warp=axis.params.warp,
        scale_meters=axis.params.scale_meters,
        resolution_meters_per_pixel=axis.resolution_meters_per_pixel,
        ridged=axis.params.ridged,
        smooth_sigma_pixels=axis.params.smooth_sigma_pixels,
    )
    generators_used: list[str] = ["noise.ridged_multifractal"]
    if axis.macro_strength > 0.0 and axis.macro_shape != "none":
        base = macro_shape.apply_macro_shape(
            base,
            shape=axis.macro_shape,
            strength=axis.macro_strength,
            seed=seed,
        )
        generators_used.append(f"macro.{axis.macro_shape}")
    heightmap = Heightmap(
        data=base,
        resolution_meters_per_pixel=axis.resolution_meters_per_pixel,
        origin=(0.0, 0.0),
        elevation_band=(0.0, 1.0),
    )
    heightmap = _apply_elevation_band(heightmap, axis.elevation_band)

    if boundary_conditions is not None:
        for contract in boundary_conditions.edge_contracts:
            heightmap = boundary_conform.apply_edge_contract(
                heightmap,
                side=contract.side,
                samples=contract.samples,
                inland_falloff_m=contract.inland_falloff_m,
            )
            generators_used.append(f"boundary.edge_conform.{contract.side}")

    for pass_spec in axis.post_passes:
        heightmap, generator_name = _apply_post_pass(heightmap, pass_spec, seed=seed)
        generators_used.append(generator_name)

    stream_geometry: StreamGeometry | None = None
    for injector in axis.feature_injectors:
        heightmap, geometry, generator_name = _apply_feature_injector(
            heightmap,
            injector,
            seed=seed,
        )
        if geometry is not None:
            stream_geometry = geometry
        generators_used.append(generator_name)

    return TerrainGenerationResult(
        heightmap=heightmap,
        stream_geometry=stream_geometry,
        generators_used=tuple(generators_used),
    )


__all__ = ["TerrainGenerationResult", "run"]
