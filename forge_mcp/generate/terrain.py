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

import numpy as np

from forge_mcp.generate import boundary_conform, erosion, macro_shape, noise, stream
from forge_mcp.generate.deterministic import make_rng
from forge_mcp.generate.heightmap import Heightmap
from forge_mcp.project.schemas import (
    HydraulicErosionPass,
    StreamFeatureInjector,
    ThermalErosionPass,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from numpy.typing import NDArray

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
class FeatureLockPatch:
    """One feature-lock patch ready to blend into a regenerated heightmap.

    ``bbox_world`` matches the lock's
    :attr:`forge_mcp.project.schemas.FeatureLockPayload.bbox_world` —
    an axis-aligned ``(x0, y0, x1, y1)`` rectangle in the same frame
    as :attr:`forge_mcp.generate.heightmap.Heightmap.origin`. ``data``
    is the raw float32 patch loaded from
    ``locks/feature/<lock_id>.npy``; the caller resolves and loads
    the file (terrain stays IO-free).
    """

    bbox_world: tuple[float, float, float, float]
    data: NDArray[np.float32]


class FeatureLockPatchError(Exception):
    """Base class for feature-lock-blend failures (Phase 7 Stage C)."""


class FeatureLockPatchMissingError(FeatureLockPatchError):
    """Raised when a feature lock's captured ``.npy`` patch is missing on disk."""


class FeatureLockOutOfBoundsError(FeatureLockPatchError):
    """Raised when ``bbox_world`` does not intersect the heightmap frame."""


_FEATHER_PIXELS: int = 4


def _cosine_feather_weights(
    shape: tuple[int, int],
    feather: int = _FEATHER_PIXELS,
) -> NDArray[np.float32]:
    """Return per-pixel weights tapering from 0 at the edge to 1 in the interior.

    Uses a half-cosine ramp (``0.5 * (1 - cos(pi * t))``) over the
    outer ``feather`` pixels of each side. The ramp degenerates to a
    full taper across the whole patch when the patch is smaller than
    ``2 * feather`` so very small locks still blend smoothly.
    """
    height, width = shape
    rows = np.ones(height, dtype=np.float32)
    cols = np.ones(width, dtype=np.float32)
    row_band = min(feather, height // 2)
    col_band = min(feather, width // 2)
    for i in range(row_band):
        t = (i + 0.5) / max(row_band, 1)
        w = np.float32(0.5 * (1.0 - np.cos(np.pi * t)))
        rows[i] = w
        rows[height - 1 - i] = w
    for j in range(col_band):
        t = (j + 0.5) / max(col_band, 1)
        w = np.float32(0.5 * (1.0 - np.cos(np.pi * t)))
        cols[j] = w
        cols[width - 1 - j] = w
    return rows[:, None] * cols[None, :]


def _apply_feature_lock_patches(
    heightmap: Heightmap,
    patches: Sequence[FeatureLockPatch],
) -> Heightmap:
    """Blend each ``FeatureLockPatch`` into ``heightmap`` with a cosine feather.

    The patch's ``bbox_world`` is converted to pixel indices using the
    heightmap's :attr:`origin` and :attr:`resolution_meters_per_pixel`,
    matching the same coordinate convention
    :meth:`forge_mcp.project.service.ProjectService._capture_feature_patch`
    used to slice the patch. Out-of-frame bboxes raise
    :class:`FeatureLockOutOfBoundsError`; dimension mismatches between
    the captured patch and the destination window are absorbed by
    cropping the patch.
    """
    if not patches:
        return heightmap
    data = heightmap.data.astype(np.float32, copy=True)
    height, width = data.shape
    ox, oy = heightmap.origin
    res = heightmap.resolution_meters_per_pixel
    for patch in patches:
        x0, y0, x1, y1 = patch.bbox_world
        col0 = int(np.floor((x0 - ox) / res))
        col1 = int(np.ceil((x1 - ox) / res))
        row0 = int(np.floor((y0 - oy) / res))
        row1 = int(np.ceil((y1 - oy) / res))
        col0_c = max(0, min(width, col0))
        col1_c = max(0, min(width, col1))
        row0_c = max(0, min(height, row0))
        row1_c = max(0, min(height, row1))
        if col1_c <= col0_c or row1_c <= row0_c:
            msg = (
                f"feature-lock bbox {patch.bbox_world!r} does not intersect "
                f"the regenerated heightmap (origin={heightmap.origin!r}, "
                f"resolution={res!r}, shape={heightmap.shape!r})"
            )
            raise FeatureLockOutOfBoundsError(msg)
        ph_total, pw_total = patch.data.shape
        po_row0 = max(0, min(ph_total, row0_c - row0))
        po_row1 = max(0, min(ph_total, po_row0 + (row1_c - row0_c)))
        po_col0 = max(0, min(pw_total, col0_c - col0))
        po_col1 = max(0, min(pw_total, po_col0 + (col1_c - col0_c)))
        sub_patch = patch.data[po_row0:po_row1, po_col0:po_col1].astype(
            np.float32,
            copy=False,
        )
        eff_h = min(row1_c - row0_c, sub_patch.shape[0])
        eff_w = min(col1_c - col0_c, sub_patch.shape[1])
        if eff_h <= 0 or eff_w <= 0:
            msg = f"feature-lock patch is empty after clipping for bbox {patch.bbox_world!r}"
            raise FeatureLockOutOfBoundsError(msg)
        sub_patch = sub_patch[:eff_h, :eff_w]
        existing = data[row0_c : row0_c + eff_h, col0_c : col0_c + eff_w]
        weights = _cosine_feather_weights((eff_h, eff_w))
        blended = weights * sub_patch + (1.0 - weights) * existing
        data[row0_c : row0_c + eff_h, col0_c : col0_c + eff_w] = blended.astype(
            data.dtype,
            copy=False,
        )
    return Heightmap(
        data=data,
        resolution_meters_per_pixel=heightmap.resolution_meters_per_pixel,
        origin=heightmap.origin,
        elevation_band=heightmap.elevation_band,
    )


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
    feature_locks: Sequence[FeatureLockPatch] = (),
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

    When ``feature_locks`` is non-empty (Phase 7 Stage C), each patch
    is blended back into the heightmap *after* erosion / boundary
    conform and *before* feature injection, with a 4-pixel cosine
    feather. The orchestrator stays IO-free; the caller is responsible
    for loading each patch's ``.npy`` payload into a
    :class:`FeatureLockPatch`.
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

    if feature_locks:
        heightmap = _apply_feature_lock_patches(heightmap, feature_locks)
        generators_used.append("locks.feature_blend")

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


__all__ = [
    "FeatureLockOutOfBoundsError",
    "FeatureLockPatch",
    "FeatureLockPatchError",
    "FeatureLockPatchMissingError",
    "TerrainGenerationResult",
    "run",
]
