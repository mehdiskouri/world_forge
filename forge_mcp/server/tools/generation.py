"""Generation + realization-view tools (forge.generate_region etc.).

Phase-3 generation surface plus the Phase-4 realizer wiring. Generation
itself remains a pure orchestrator over
:mod:`forge_mcp.descriptor.map_to_spec`,
:mod:`forge_mcp.generate.terrain`, and
:mod:`forge_mcp.analyze.terrain_analysis` — determinism is achieved by
threading the region's ``seed`` (rerollable) through both the spec
compiler and the terrain generators.

When a realizer factory is installed (see
:func:`forge_mcp.server.tools.set_realizer_factory`), ``generate_region``
also drives the curated v1 ``realize_region`` macro to materialise the
terrain into a Blender ``.blend`` plus a preview PNG, persisting both
atomically alongside a sidecar realization-trace JSON. ``render_view``
re-runs the same realize + render path at one of three pre-set
resolutions (preview / default / full) without touching the persisted
spec — useful for the agent's "render me a closer look" loop.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import blake2b
from typing import TYPE_CHECKING, Final

import numpy as np
from pydantic import ValidationError

from forge_mcp.analyze.terrain_analysis import TerrainAnalysis, analyze
from forge_mcp.boundary.apply import (
    build_boundary_conditions,
    participating_boundaries,
)
from forge_mcp.boundary.contract import (
    BoundaryContractInfeasibleError,
    refresh_contract_for,
)
from forge_mcp.descriptor.map_to_spec import (
    ElevationBandImplausibleError,
    map_to_spec,
)
from forge_mcp.descriptor.region_extent import RegionExtent
from forge_mcp.generate import terrain as terrain_generator
from forge_mcp.generate.heightmap import Heightmap, load_npy, save_npy, save_png16
from forge_mcp.generate.stream import StreamGeometry
from forge_mcp.project.schemas import (
    FeatureLockPayload,
    LockKind,
    RegionId,
    SpecId,
    SpecRecord,
)
from forge_mcp.project.service import (
    NoOpenProjectError,
    UnknownRegionError,
    UnknownSpecError,
    _now,
)
from forge_mcp.realize.engine import RealizerError
from forge_mcp.realize.environment import resolve_environment
from forge_mcp.realize.heightmap_mesh import (
    SceneFraming,
    mesh_from_heightmap,
    scene_framing_from_heightmap,
)
from forge_mcp.realize.macros import (
    RealizeRegionInputs,
    RenderPreviewInputs,
    realize_region,
    render_preview,
)
from forge_mcp.realize.material import resolve_plan
from forge_mcp.realize.realization import record_from_result, write_trace_record
from forge_mcp.realize.render_options import (
    DeviceUnavailableError,
    InvalidRenderOptionsError,
    RenderOptions,
    ResolvedRender,
    resolve_render_settings,
)
from forge_mcp.server.tools import get_realizer_factory, get_service
from forge_mcp.server.tools._responses import fail, ok

if TYPE_CHECKING:
    from pathlib import Path

    from forge_mcp.project.service import ProjectService
    from forge_mcp.realize.engine import RealizationResult
    from forge_mcp.realize.rpc import JsonValue


_REROLL_DIGEST_BYTES = 8


VIEW_ORTHO_TOP: Final[str] = "ortho_top"
VIEW_PERSPECTIVE_SE: Final[str] = "perspective_se"
DEFAULT_VIEW_KIND: Final[str] = VIEW_ORTHO_TOP

RESOLUTION_PREVIEW: Final[str] = "preview"
RESOLUTION_DEFAULT: Final[str] = "default"
RESOLUTION_FULL: Final[str] = "full"
DEFAULT_RESOLUTION: Final[str] = RESOLUTION_DEFAULT

_VIEW_KINDS: Final[tuple[str, ...]] = (VIEW_ORTHO_TOP, VIEW_PERSPECTIVE_SE)
_RESOLUTIONS: Final[dict[str, tuple[int, int]]] = {
    RESOLUTION_PREVIEW: (512, 384),
    RESOLUTION_DEFAULT: (1024, 768),
    RESOLUTION_FULL: (2048, 1536),
}

# Per-resolution PNG ceilings (bytes). Earlier measurements (~213 KB at
# the default resolution) were taken when cameras and lights were created
# but never positioned, so renders were near-blank gray. Once the
# realizer was fixed to frame the heightmap and orient the sun, default
# 1024x768 renders measured ~350 KB under zlib level 9 (post-retry); we
# add headroom and scale by pixel count for the other resolutions.
# Ceiling raised to 1.5 MB for default: high-detail profiles (alpine_peaks
# with ruggedness) produce ~1.13 MB renders that legitimately exceed 1 MB.
_PNG_MAX_BYTES: Final[dict[str, int]] = {
    RESOLUTION_PREVIEW: 350_000,
    RESOLUTION_DEFAULT: 1_500_000,
    RESOLUTION_FULL: 4_000_000,
}

_DEFAULT_PLAN_MESH_PREFIX: Final[str] = "terrain_"

# Phase 6-e Stage D: hard ceiling on instanced primitives across all
# instancer layers in a single region's resolved plan, summed as
# ``density_per_m2 * surface_area_m2``. Empirical: 5e6 instanced
# triangles renders in <10 s for a 4 km^2 region under EEVEE on a
# mid-range GPU; above that, EEVEE memory pressure starts evicting
# the heightmap texture and the realiser starts failing intermittently.
_MAX_INSTANCED_PRIMITIVES: Final[float] = 5_000_000.0


class GrassDensityTooHighError(ValueError):
    """Raised when ``density_per_m2 * surface_area_m2`` exceeds the cap.

    Carries structured fields the MCP server promotes to a typed
    error envelope so clients can distinguish density failures from
    other generation errors.
    """

    def __init__(
        self,
        *,
        region_id: RegionId,
        requested: float,
        cap: float,
        area_m2: float,
        density_per_m2: float,
    ) -> None:
        self.region_id = region_id
        self.requested = requested
        self.cap = cap
        self.area_m2 = area_m2
        self.density_per_m2 = density_per_m2
        super().__init__(
            f"procedural_grass density * area = {requested:.0f} exceeds "
            f"cap {cap:.0f} for region {region_id!r} "
            f"(density_per_m2={density_per_m2}, area_m2={area_m2})",
        )


def _check_instancer_density(
    instancer_layers: list[JsonValue],
    area_m2: float,
    *,
    region_id: RegionId,
) -> None:
    """Enforce the per-region instancer-density ceiling.

    Sums ``density_per_m2`` across every instancer layer (the v1
    cap is on total primitives across the region, since every
    layer's modifier contributes additively to render cost) and
    raises :class:`GrassDensityTooHighError` if the product with
    ``area_m2`` exceeds :data:`_MAX_INSTANCED_PRIMITIVES`.
    """
    total_density = 0.0
    for layer in instancer_layers:
        if not isinstance(layer, dict):
            continue
        instancer = layer.get("instancer")
        if not isinstance(instancer, dict):
            continue
        density = instancer.get("density_per_m2")
        if isinstance(density, (int, float)) and not isinstance(density, bool):
            total_density += float(density)
    requested = total_density * float(area_m2)
    if requested > _MAX_INSTANCED_PRIMITIVES:
        raise GrassDensityTooHighError(
            region_id=region_id,
            requested=requested,
            cap=_MAX_INSTANCED_PRIMITIVES,
            area_m2=float(area_m2),
            density_per_m2=total_density,
        )


def _camera_name_for_view(region_id: RegionId, view_kind: str) -> str:
    """Return the per-region camera object name for ``view_kind``."""
    if view_kind == VIEW_ORTHO_TOP:
        return f"cam_ortho_{region_id}"
    return f"cam_persp_{region_id}"


@dataclass(frozen=True, slots=True)
class _ViewArtifacts:
    """Per-(view, resolution) render outputs."""

    view_kind: str
    resolution: str
    preview_path: Path
    trace_path: Path
    render_result: RealizationResult
    resolved: ResolvedRender


@dataclass(frozen=True, slots=True)
class _RealizationArtifacts:
    """Filesystem locations + traces returned by :func:`_run_realizer`."""

    blend_path: Path
    realize_result: RealizationResult
    views: tuple[_ViewArtifacts, ...]
    plan_id: str
    elevation_band: tuple[float, float]


def _resolve_region(region_id_value: str) -> tuple[RegionId, object]:
    """Look up a region in the open project; return either the record or an error envelope."""
    region_id = RegionId(region_id_value)
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return region_id, fail("no_open_project", str(exc))
    region = state.regions.get(region_id)
    if region is None:
        return region_id, fail("unknown_region", f"unknown region {region_id_value!r}")
    return region_id, region


def _project_summary(analysis: TerrainAnalysis) -> dict[str, float | None]:
    """Project the rich :class:`TerrainAnalysis` onto :class:`SpecSummary` fields."""
    return {
        "mean_elevation": analysis.elevation.mean,
        "std_elevation": analysis.elevation.std,
        "min_elevation": analysis.elevation.min,
        "max_elevation": analysis.elevation.max,
        "slope_p95_degrees": analysis.slope_degrees.p95,
        "stream_length_meters": (
            None if analysis.stream is None else analysis.stream.length_meters
        ),
    }


def _persist_realization(
    service: ProjectService,
    region_id: RegionId,
    result: terrain_generator.TerrainGenerationResult,
) -> tuple[str, str, str | None]:
    """Persist heightmap + optional stream geometry; return relative paths."""
    paths = service.state.paths
    paths.heightmaps_dir.mkdir(parents=True, exist_ok=True)
    npy_path = paths.heightmap_npy_path(region_id)
    png_path = paths.heightmap_png_path(region_id)
    save_npy(result.heightmap, npy_path)
    save_png16(result.heightmap, png_path)
    stream_path: str | None = None
    geo_target = paths.stream_geometry_path(region_id)
    if result.stream_geometry is not None:
        from forge_mcp._io.atomic import atomic_write_text  # noqa: PLC0415 - tool-local IO

        atomic_write_text(
            geo_target,
            result.stream_geometry.model_dump_json(indent=2) + "\n",
        )
        stream_path = str(geo_target)
    elif geo_target.exists():
        # Stale geometry from a prior run with a stream injector; remove it.
        geo_target.unlink()
    return str(npy_path), str(png_path), stream_path


def _collect_boundary_conditions(
    service: ProjectService,
    region: object,
    region_id: RegionId,
) -> terrain_generator.BoundaryConditions:
    """Build :class:`BoundaryConditions` from already-persisted neighbour contracts.

    Phase 6 Stage C. Walks every boundary the region participates in;
    contract-bearing boundaries become :class:`EdgeContract` entries
    via :func:`build_boundary_conditions`. The first generation of a
    region with no contracted neighbours returns the empty bag.
    """
    from forge_mcp.project.schemas import RegionNode  # noqa: PLC0415 - local narrow

    assert isinstance(region, RegionNode)  # noqa: S101 - narrow for mypy strict
    boundaries = participating_boundaries(region_id, service.state.boundaries.values())
    return build_boundary_conditions(region, boundaries)


def _find_region_lock(
    service: ProjectService,
    region_id: RegionId,
) -> object | None:
    """Return the first :attr:`LockKind.REGION` lock on ``region_id`` or ``None``.

    Phase 7 Stage C. Used by :func:`generate_region` to short-circuit
    regeneration: existing realization artefacts are kept untouched
    and a ``region_lock_skipped`` history event is emitted.
    """
    locks = service.state.lock_store.find_by_region_and_kind(region_id, LockKind.REGION)
    return locks[0] if locks else None


def _load_feature_lock_patches(
    service: ProjectService,
    region_id: RegionId,
) -> tuple[terrain_generator.FeatureLockPatch, ...]:
    """Load every feature-lock patch on ``region_id`` from disk.

    Phase 7 Stage C. The orchestrator stays IO-free, so the tool layer
    resolves each :attr:`LockKind.FEATURE` lock's
    :attr:`FeatureLockPayload.captured_path`, loads the ``.npy`` body,
    and packages them into :class:`FeatureLockPatch` instances. Missing
    patch files raise :class:`FeatureLockPatchMissingError`.
    """
    records = service.state.lock_store.find_by_region_and_kind(region_id, LockKind.FEATURE)
    out: list[terrain_generator.FeatureLockPatch] = []
    for record in records:
        payload = record.typed_payload()
        if not isinstance(payload, FeatureLockPayload):  # pragma: no cover - schema guard
            continue
        patch_path = service.state.paths.feature_lock_patch_path(record.lock_id)
        if not patch_path.exists():
            msg = (
                f"feature-lock patch {patch_path!s} for lock {record.lock_id!r} is missing on disk"
            )
            raise terrain_generator.FeatureLockPatchMissingError(msg)
        data = np.load(patch_path, allow_pickle=False).astype(np.float32, copy=False)
        out.append(
            terrain_generator.FeatureLockPatch(
                bbox_world=payload.bbox_world,
                data=data,
            ),
        )
    return tuple(out)


def _refresh_participating_contracts(
    service: ProjectService,
    region_id: RegionId,
    spec: SpecRecord,
) -> dict[str, object] | None:
    """Renegotiate contracts on every boundary touching ``region_id``.

    Returns ``None`` on success, or an MCP error envelope when
    :func:`refresh_contract_for` raises
    :class:`BoundaryContractInfeasibleError`. Boundaries whose other
    side has no persisted spec yet are skipped silently — their
    contracts will be negotiated when the neighbour generates.
    """
    boundaries = participating_boundaries(region_id, service.state.boundaries.values())
    for boundary in boundaries:
        other_id = boundary.region_b if boundary.region_a == region_id else boundary.region_a
        other_region = service.state.regions.get(other_id)
        if other_region is None or other_region.spec_id is None:
            continue
        try:
            other_spec = service.load_spec(other_region.spec_id)
        except UnknownSpecError:  # pragma: no cover - already-stale link
            continue
        try:
            updated = refresh_contract_for(
                boundary,
                spec.body,
                other_spec.body,
                now=_now(),
            )
        except BoundaryContractInfeasibleError as exc:
            return fail(
                "boundary_contract_infeasible",
                str(exc),
                details={
                    "reason": exc.reason,
                    "boundary_id": str(exc.boundary_id),
                    "region_a": str(exc.region_a),
                    "region_b": str(exc.region_b),
                    "stage": "boundary_negotiation",
                    **exc.details,
                },
            )
        service.persist_boundary(updated)
    return None


def _build_realize_inputs(  # noqa: PLR0913 - one assembly site, all named
    region_id: RegionId,
    spec_id: SpecId,
    vertices: list[tuple[float, float, float]],
    faces: list[tuple[int, int, int, int]],
    blend_filepath: Path,
    heightmap_image_filepath: Path,
    displace_strength: float,
    framing: SceneFraming,
    plan: JsonValue,
    plan_id: str,
    instancer_layers: JsonValue,
    environment_plan: JsonValue,
    environment_plan_id: str,
) -> RealizeRegionInputs:
    """Assemble the per-region :class:`RealizeRegionInputs` payload."""
    rid_str = str(region_id)
    sid_str = str(spec_id)
    return RealizeRegionInputs(
        object_name=f"terrain_{rid_str}",
        vertices=vertices,
        faces=faces,
        region_id=rid_str,
        spec_id=sid_str,
        plan=plan,
        plan_id=plan_id,
        instancer_layers=instancer_layers,
        curve_name=f"stream_{rid_str}",
        ortho_camera_name=f"cam_ortho_{rid_str}",
        perspective_camera_name=f"cam_persp_{rid_str}",
        ortho_location=list(framing.ortho_location),
        ortho_rotation_euler=list(framing.ortho_rotation_euler),
        ortho_scale=framing.ortho_scale,
        perspective_location=list(framing.perspective_location),
        perspective_rotation_euler=list(framing.perspective_rotation_euler),
        sun_name=f"sun_{rid_str}",
        world_name=f"world_{rid_str}",
        sun_location=list(framing.sun_location),
        sun_rotation_euler=list(framing.sun_rotation_euler),
        environment_plan=environment_plan,
        environment_plan_id=environment_plan_id,
        blend_filepath=str(blend_filepath),
        heightmap_image_filepath=str(heightmap_image_filepath),
        displace_strength=displace_strength,
    )


def _run_realizer(  # noqa: PLR0913 - one assembly site, all named
    service: ProjectService,
    region_id: RegionId,
    spec_id: SpecId,
    heightmap: Heightmap,
    targets: tuple[tuple[str, str], ...],
    *,
    render_options: RenderOptions,
    legacy_default: bool,
) -> _RealizationArtifacts | None:
    """Drive the realizer, persisting all outputs atomically.

    ``targets`` is a tuple of ``(view_kind, resolution)`` pairs to
    render after the single ``realize_region`` macro completes. Returns
    ``None`` when no realizer factory is installed. ``render_options``
    is the validated user payload (use ``RenderOptions()`` for the
    no-override path).
    """
    factory = get_realizer_factory()
    if factory is None:
        return None

    paths = service.state.paths
    paths.blender_dir.mkdir(parents=True, exist_ok=True)
    blend_path = paths.blend_path(region_id)
    blend_tmp = blend_path.with_name(blend_path.name + ".tmp")
    heightmap_png = paths.heightmap_png_path(region_id)

    vertices, faces = mesh_from_heightmap(heightmap)
    # The mesh vertices emitted by ``mesh_from_heightmap`` already carry
    # ``z = data_meters`` (i.e. the heightmap value in metres above sea
    # level). Blender's DISPLACE modifier adds ``(texture - midlevel) *
    # strength`` along the vertex normal *on top* of that, so a non-zero
    # ``displace_strength`` would double the elevation and turn high-
    # relief regions (alpine, canyon) into unrenderable vertical spikes.
    # The displace step is kept attached for future detail-recovery use
    # when the heightmap is downsampled past ``MAX_RESOLUTION`` (not
    # currently exercised) but its strength must be zero today.
    displace_strength = 0.0
    framing = scene_framing_from_heightmap(heightmap)

    elevation_min = float(heightmap.elevation_band[0])
    elevation_max = float(heightmap.elevation_band[1])
    composite_plan = resolve_plan(
        service.state,
        region_id,
        mesh_name=f"{_DEFAULT_PLAN_MESH_PREFIX}{region_id}",
        elevation_min=elevation_min,
        elevation_max=elevation_max,
    )
    plan_payload = composite_plan.model_dump(mode="json")
    raw_plan_id = plan_payload.get("plan_id")
    if not isinstance(raw_plan_id, str):
        msg = f"resolved plan missing string plan_id, got {raw_plan_id!r}"
        raise TypeError(msg)
    plan_id_payload: str = raw_plan_id
    # Phase 6-e Stage F: split out instancer-bearing layers so the
    # composite material build skips them and the dedicated
    # ``material.attach_instancer`` step picks them up. Stage F's
    # resolver registry is empty, so this list is always empty until
    # Stage D wires PROCEDURAL_GRASS into the registry.
    instancer_layers_payload: list[JsonValue] = [
        layer
        for layer in plan_payload.get("layers", [])
        if isinstance(layer, dict) and layer.get("instancer") is not None
    ]
    # Phase 6-e Stage D: cap total instanced primitive count at
    # ``_MAX_INSTANCED_PRIMITIVES`` to keep EEVEE renders bounded for
    # the unattended ``forge.realize_region`` path.
    region_for_density = service.state.regions.get(region_id)
    if region_for_density is None:
        msg = f"region {region_id!r} disappeared between resolve and realize"
        raise LookupError(msg)
    region_extent_for_density = RegionExtent.from_polygon_coords(
        region_for_density.spatial_bounds.coords.coords,
    )
    _check_instancer_density(
        instancer_layers_payload,
        region_extent_for_density.area_m2,
        region_id=region_id,
    )

    # Phase 6-f Stage F: resolve the bound environment (region -> world
    # root -> hard-coded default) and fold its plan_id into the
    # realizer inputs so the composite ``realize_region`` macro can
    # build the world shader graph + cached SUN lamp via
    # ``world.build_environment``.
    resolved_environment = resolve_environment(service.state, region_id=region_id)
    environment_plan_payload = resolved_environment.model_dump(mode="json")
    environment_plan_id_payload = str(resolved_environment.plan_id)

    realize_inputs = _build_realize_inputs(
        region_id,
        spec_id,
        vertices,
        faces,
        blend_tmp,
        heightmap_png,
        displace_strength,
        framing,
        plan_payload,
        plan_id_payload,
        instancer_layers_payload,
        environment_plan_payload,
        environment_plan_id_payload,
    )

    plan: list[tuple[str, str, Path, Path, Path, RenderPreviewInputs, ResolvedRender]] = []

    with factory() as engine:
        # Phase 6-d: fold the per-tier defaults with the user's
        # ``render_options`` against the live device probe before any
        # render input is built.
        available_devices = engine.available_device_types
        for view_kind, resolution in targets:
            preview_path = paths.preview_path(region_id, view_kind, resolution)
            preview_tmp = preview_path.with_name(preview_path.name + ".tmp")
            trace_path = paths.realization_trace_path(region_id, view_kind, resolution)
            tier_width, tier_height = _RESOLUTIONS[resolution]
            resolved = resolve_render_settings(
                tier_width=tier_width,
                tier_height=tier_height,
                tier_png_max_bytes=_PNG_MAX_BYTES[resolution],
                options=render_options,
                available_device_types=available_devices,
                legacy_default=legacy_default,
            )
            render_inputs = RenderPreviewInputs(
                filepath=str(preview_tmp),
                resolution_x=resolved.width,
                resolution_y=resolved.height,
                camera_name=_camera_name_for_view(region_id, view_kind),
                engine=resolved.engine,
                device_type=resolved.device_type,
                cycles_samples=resolved.cycles_samples,
                png_max_bytes=resolved.png_max_bytes,
            )
            plan.append(
                (
                    view_kind,
                    resolution,
                    preview_path,
                    preview_tmp,
                    trace_path,
                    render_inputs,
                    resolved,
                ),
            )

        realize_result = realize_region(engine, realize_inputs)
        render_results = [render_preview(engine, item[5]) for item in plan]

    # Atomic publish: only after every render succeeded.
    os.replace(blend_tmp, blend_path)  # noqa: PTH105 - need os primitive on Path
    views: list[_ViewArtifacts] = []
    for (
        view_kind,
        resolution,
        preview_path,
        preview_tmp,
        trace_path,
        _ri,
        resolved,
    ), render_result in zip(
        plan,
        render_results,
        strict=True,
    ):
        os.replace(preview_tmp, preview_path)  # noqa: PTH105 - need os primitive on Path
        record = record_from_result(
            render_result,
            region_id=str(region_id),
            view_kind=view_kind,
            plan_id=str(composite_plan.plan_id),
        )
        write_trace_record(trace_path, record)
        views.append(
            _ViewArtifacts(
                view_kind=view_kind,
                resolution=resolution,
                preview_path=preview_path,
                trace_path=trace_path,
                render_result=render_result,
                resolved=resolved,
            ),
        )

    return _RealizationArtifacts(
        blend_path=blend_path,
        realize_result=realize_result,
        views=tuple(views),
        plan_id=str(composite_plan.plan_id),
        elevation_band=(elevation_min, elevation_max),
    )


def _view_summary(view: _ViewArtifacts) -> dict[str, object]:
    """Pack one :class:`_ViewArtifacts` into the response JSON shape."""
    return {
        "view_kind": view.view_kind,
        "resolution": view.resolution,
        "preview_path": str(view.preview_path),
        "realization_trace_path": str(view.trace_path),
        "render_resolution": [view.resolved.width, view.resolved.height],
        "render_file_size_bytes": _render_size(view.render_result),
        "render_engine": view.resolved.engine,
        "render_device_type": view.resolved.device_type,
        "render_cycles_samples": view.resolved.cycles_samples,
        "render_notes": list(view.resolved.notes),
    }


def _parse_render_options(
    payload: object | None,
) -> tuple[RenderOptions, bool] | dict[str, object]:
    """Validate ``render_options`` payload.

    Returns either ``(options, legacy_default)`` (where
    ``legacy_default`` is True iff the caller omitted the argument
    entirely — used to preserve byte-for-byte backwards compatibility)
    or an MCP error envelope.
    """
    if payload is None:
        return RenderOptions(), True
    if not isinstance(payload, dict):
        return fail(
            "invalid_render_options",
            f"render_options must be an object, got {type(payload).__name__}",
        )
    try:
        return RenderOptions.model_validate(payload), False
    except (ValidationError, InvalidRenderOptionsError) as exc:
        return fail("invalid_render_options", str(exc))


def generate_region(  # noqa: PLR0911, PLR0912, PLR0915, C901 - one return per failure surface
    region_id: str,
    *,
    render_options: dict[str, object] | None = None,
) -> dict[str, object]:
    """Compile + generate + analyze a region. Persists spec, heightmap, analysis.

    ``render_options`` (Phase 6-d) is an optional, validated bundle of
    user knobs over the per-tier render defaults (engine, device,
    width/height, ``cycles_samples``, ``png_max_bytes``). Omitting it
    reproduces the pre-Phase-6-d behaviour byte-for-byte.
    """
    parsed = _parse_render_options(render_options)
    if isinstance(parsed, dict):
        return parsed
    options_model, legacy_default = parsed
    rid, lookup = _resolve_region(region_id)
    if isinstance(lookup, dict):
        return lookup  # already-shaped error envelope
    region = lookup
    # mypy needs the narrowed type
    from forge_mcp.project.schemas import RegionNode  # noqa: PLC0415 - local narrow

    assert isinstance(region, RegionNode)  # noqa: S101 - narrow for mypy strict
    if region.structured_descriptor is None:
        return fail(
            "missing_descriptor",
            f"region {region_id!r} has no structured_descriptor; cannot generate",
        )

    service = get_service()

    # Phase 7 Stage C: a `LockKind.REGION` lock short-circuits regen.
    # Existing realization artefacts on disk are kept untouched; the
    # tool returns a structured envelope and a `region_lock_skipped`
    # history event records which lock fired.
    region_lock = _find_region_lock(service, rid)
    if region_lock is not None:
        from forge_mcp.project.schemas import LockRecord  # noqa: PLC0415 - local narrow

        assert isinstance(region_lock, LockRecord)  # noqa: S101 - narrow for mypy strict
        service.record_region_lock_skipped(rid, region_lock.lock_id)
        return fail(
            "region_lock_skipped",
            f"region {region_id!r} carries region lock "
            f"{str(region_lock.lock_id)!r}; regeneration skipped",
            details={
                "region_id": region_id,
                "lock_id": str(region_lock.lock_id),
            },
        )

    metadata = service.state.metadata
    region_extent = RegionExtent.from_polygon_coords(region.spatial_bounds.coords.coords)
    try:
        spec = map_to_spec(
            region.structured_descriptor,
            region.seed,
            region_extent=region_extent,
            blender_version=metadata.blender_version,
            bpy_hypergraph_version=metadata.bpy_hypergraph_version,
            now=_now(),
        )
    except ElevationBandImplausibleError as exc:
        return fail(
            "elevation_band_implausible",
            str(exc),
            details={
                "field": exc.field,
                "region_extent_m": exc.region_extent_m,
                "max_band_m": exc.max_band_m,
                "supplied_band": list(exc.supplied_band),
            },
        )

    try:
        feature_patches = _load_feature_lock_patches(service, rid)
    except terrain_generator.FeatureLockPatchMissingError as exc:
        return fail("feature_lock_patch_missing", str(exc))

    try:
        result = terrain_generator.run(
            spec,
            seed=region.seed,
            boundary_conditions=_collect_boundary_conditions(service, region, rid),
            feature_locks=feature_patches,
        )
    except terrain_generator.FeatureLockOutOfBoundsError as exc:
        return fail("feature_lock_out_of_bounds", str(exc))
    except (ValueError, RuntimeError) as exc:  # pragma: no cover - generator preconditions
        return fail("generation_failed", str(exc))

    analysis = analyze(result.heightmap, result.stream_geometry)
    new_summary = spec.body.summary.model_copy(update=_project_summary(analysis))
    new_body = spec.body.model_copy(update={"summary": new_summary})
    spec_with_summary: SpecRecord = spec.model_copy(update={"body": new_body})
    service.persist_spec(spec_with_summary)
    service.link_region_to_spec(rid, spec_with_summary.spec_id)

    # Phase 6 Stage C: refresh contracts for every boundary this region
    # participates in where the OTHER region already has a persisted
    # spec, then build the BoundaryConditions to feed back into
    # terrain.run on subsequent generations of this region. The first
    # generation runs with empty conditions; the second (after the
    # neighbour generates) picks up the contracts.
    contract_error = _refresh_participating_contracts(service, rid, spec_with_summary)
    if contract_error is not None:
        return contract_error

    npy_path, png_path, stream_path = _persist_realization(service, rid, result)
    service.record_generation(
        rid,
        spec_with_summary.spec_id,
        generators_used=result.generators_used,
    )

    blend_path: str | None = None
    realization_summary: dict[str, object] | None = None
    previews: dict[str, dict[str, object]] | None = None
    try:
        artifacts = _run_realizer(
            service,
            rid,
            spec_with_summary.spec_id,
            result.heightmap,
            tuple((view, DEFAULT_RESOLUTION) for view in _VIEW_KINDS),
            render_options=options_model,
            legacy_default=legacy_default,
        )
    except DeviceUnavailableError as exc:
        return fail(
            "device_unavailable",
            str(exc),
            details={"device": exc.device, "available": list(exc.available)},
        )
    except GrassDensityTooHighError as exc:
        return fail(
            "grass_density_too_high",
            str(exc),
            details={
                "region_id": str(exc.region_id),
                "requested": exc.requested,
                "cap": exc.cap,
                "area_m2": exc.area_m2,
                "density_per_m2": exc.density_per_m2,
            },
        )
    except RealizerError as exc:
        return fail("realizer_failed", str(exc))
    if artifacts is not None:
        blend_path = str(artifacts.blend_path)
        previews = {view.view_kind: _view_summary(view) for view in artifacts.views}
        first_view = artifacts.views[0]
        realization_summary = {
            "macro": artifacts.realize_result.macro,
            "sequence_id": artifacts.realize_result.sequence_id,
            "render_engine": first_view.resolved.engine,
            "render_device_type": first_view.resolved.device_type,
            "default_resolution": [first_view.resolved.width, first_view.resolved.height],
            "default_view_kind": DEFAULT_VIEW_KIND,
            "plan_id": str(artifacts.plan_id),
            "elevation_band": [
                float(artifacts.elevation_band[0]),
                float(artifacts.elevation_band[1]),
            ],
        }

    return ok(
        {
            "region_id": region_id,
            "spec_id": str(spec_with_summary.spec_id),
            "heightmap_npy_path": npy_path,
            "heightmap_png_path": png_path,
            "stream_geometry_path": stream_path,
            "blend_path": blend_path,
            "previews": previews,
            "realization": realization_summary,
            "generators_used": list(result.generators_used),
            "analysis": analysis.model_dump(mode="json"),
        },
    )


def _render_size(render_result: RealizationResult) -> int | None:
    """Pull ``file_size_bytes`` out of a render result, if present."""
    final = render_result.final_result
    if isinstance(final, dict):
        size = final.get("file_size_bytes")
        if isinstance(size, int):
            return size
    return None


def _validate_render_view_args(view_kind: str, resolution: str) -> dict[str, object] | None:
    """Return an error envelope for invalid view_kind / resolution, else ``None``."""
    if view_kind not in _VIEW_KINDS:
        return fail(
            "invalid_view_kind",
            f"view_kind must be one of {list(_VIEW_KINDS)!r}, got {view_kind!r}",
        )
    if resolution not in _RESOLUTIONS:
        return fail(
            "invalid_resolution",
            f"resolution must be one of {sorted(_RESOLUTIONS)!r}, got {resolution!r}",
        )
    return None


def _resolve_render_view_target(
    region_id: str,
    view_kind: str,
    resolution: str,
) -> tuple[RegionId, SpecId, Path] | dict[str, object]:
    """Validate ``render_view`` arguments and return the resolved target.

    Returns either ``(region_id, spec_id, heightmap_npy_path)`` on success
    or a ready-to-return error envelope.
    """
    err = _validate_render_view_args(view_kind, resolution)
    if err is not None:
        return err
    rid, lookup = _resolve_region(region_id)
    if isinstance(lookup, dict):
        return lookup
    from forge_mcp.project.schemas import RegionNode  # noqa: PLC0415 - local narrow

    assert isinstance(lookup, RegionNode)  # noqa: S101 - narrow for mypy strict
    if lookup.spec_id is None:
        return fail(
            "not_generated",
            f"region {region_id!r} has no spec_id; run forge.generate_region first",
        )
    npy_path = get_service().state.paths.heightmap_npy_path(rid)
    if not npy_path.exists():
        return fail(
            "not_generated",
            f"region {region_id!r} has no persisted heightmap; run forge.generate_region first",
        )
    if get_realizer_factory() is None:
        return fail(
            "realizer_not_configured",
            "no realizer factory installed; cannot render views",
        )
    return rid, lookup.spec_id, npy_path


def render_view(
    region_id: str,
    view_kind: str = DEFAULT_VIEW_KIND,
    resolution: str = DEFAULT_RESOLUTION,
    *,
    render_options: dict[str, object] | None = None,
) -> dict[str, object]:
    """Re-render a previously-generated region for one ``(view_kind, resolution)``.

    ``view_kind`` is one of ``"ortho_top"`` / ``"perspective_se"``;
    ``resolution`` is one of ``"preview"`` (512x384) / ``"default"``
    (1024x768) / ``"full"`` (2048x1536). The realizer rebuilds the
    scene from the persisted heightmap each call so the on-disk
    ``.blend`` stays honest about what the most recent render drew.

    ``render_options`` (Phase 6-d) accepts the same knobs as
    :func:`generate_region` and overrides the per-tier defaults.
    """
    parsed = _parse_render_options(render_options)
    if isinstance(parsed, dict):
        return parsed
    options_model, legacy_default = parsed
    target = _resolve_render_view_target(region_id, view_kind, resolution)
    if isinstance(target, dict):
        return target
    rid, spec_id, npy_path = target
    service = get_service()
    heightmap = load_npy(npy_path)
    try:
        artifacts = _run_realizer(
            service,
            rid,
            spec_id,
            heightmap,
            ((view_kind, resolution),),
            render_options=options_model,
            legacy_default=legacy_default,
        )
    except DeviceUnavailableError as exc:
        return fail(
            "device_unavailable",
            str(exc),
            details={"device": exc.device, "available": list(exc.available)},
        )
    except GrassDensityTooHighError as exc:
        return fail(
            "grass_density_too_high",
            str(exc),
            details={
                "region_id": str(exc.region_id),
                "requested": exc.requested,
                "cap": exc.cap,
                "area_m2": exc.area_m2,
                "density_per_m2": exc.density_per_m2,
            },
        )
    except RealizerError as exc:
        return fail("realizer_failed", str(exc))
    assert artifacts is not None  # noqa: S101 - factory guarded above
    view = artifacts.views[0]
    return ok(
        {
            "region_id": region_id,
            "blend_path": str(artifacts.blend_path),
            "render_engine": view.resolved.engine,
            "render_device_type": view.resolved.device_type,
            **_view_summary(view),
        },
    )


def list_render_devices(*, force_refresh: bool = False) -> dict[str, object]:
    """Return the host's Cycles compute-device probe.

    The probe is captured once when the realizer first connects to
    Blender (folded into the ``ping`` round-trip). Pass
    ``force_refresh=True`` to re-issue ``ping`` and pick up any
    add-on changes the user made in Blender's preferences without
    restarting the host.
    """
    factory = get_realizer_factory()
    if factory is None:
        return fail(
            "realizer_not_configured",
            "no realizer factory installed; cannot probe render devices",
        )
    with factory() as engine:
        if force_refresh:
            engine.refresh_render_devices()
        devices = list(engine.available_device_types)
        default = engine.default_device_type
    return ok(
        {
            "available_device_types": devices,
            "default_device_type": default,
        },
    )


def _derive_seed(region_id: RegionId, history_count: int) -> int:
    """Deterministically derive a fresh seed from ``(region_id, history_count)``."""
    digest = blake2b(
        f"{region_id}:{history_count}".encode(),
        digest_size=_REROLL_DIGEST_BYTES,
    ).digest()
    return int.from_bytes(digest, "big", signed=False)


def reroll_seed(region_id: str, seed: int | None = None) -> dict[str, object]:
    """Replace the region's seed (caller-provided or deterministically derived)."""
    rid, lookup = _resolve_region(region_id)
    if isinstance(lookup, dict):
        return lookup
    service = get_service()
    new_seed = seed if seed is not None else _derive_seed(rid, service.state.history.count)
    try:
        region = service.reroll_region_seed(rid, new_seed)
    except UnknownRegionError as exc:  # pragma: no cover - guarded above
        return fail("unknown_region", str(exc))
    return ok({"region_id": region_id, "seed": region.seed})


def analyze_region(region_id: str) -> dict[str, object]:
    """Re-analyze the persisted heightmap (and stream geometry if present)."""
    rid, lookup = _resolve_region(region_id)
    if isinstance(lookup, dict):
        return lookup
    service = get_service()
    npy_path = service.state.paths.heightmap_npy_path(rid)
    if not npy_path.exists():
        return fail(
            "not_generated",
            f"region {region_id!r} has no persisted heightmap; run forge.generate_region first",
        )
    heightmap = load_npy(npy_path)
    geo_path = service.state.paths.stream_geometry_path(rid)
    geometry: StreamGeometry | None = None
    if geo_path.exists():
        geometry = StreamGeometry.model_validate_json(geo_path.read_text(encoding="utf-8"))
    analysis = analyze(heightmap, geometry)
    return ok({"region_id": region_id, "analysis": analysis.model_dump(mode="json")})


def _resolve_spec_id(
    service: ProjectService,
    spec_id: str | None,
    region_id: str | None,
) -> tuple[SpecId | None, dict[str, object] | None]:
    """Pick the spec id from either argument; return ``(spec_id, error_envelope)``."""
    if region_id is not None:
        _rid, lookup = _resolve_region(region_id)
        if isinstance(lookup, dict):
            return None, lookup
        from forge_mcp.project.schemas import RegionNode  # noqa: PLC0415 - local narrow

        assert isinstance(lookup, RegionNode)  # noqa: S101 - narrow for mypy strict
        if lookup.spec_id is None:
            return None, fail(
                "not_generated",
                f"region {region_id!r} has no spec_id; run forge.generate_region first",
            )
        return lookup.spec_id, None
    assert spec_id is not None  # noqa: S101 - exactly-one-of guard in caller
    _ = service  # service handle unused on the spec-id branch
    return SpecId(spec_id), None


def inspect_spec(
    spec_id: str | None = None,
    region_id: str | None = None,
) -> dict[str, object]:
    """Return one persisted :class:`SpecRecord` by id, or via region-id indirection."""
    if (spec_id is None) == (region_id is None):
        return fail(
            "invalid_arguments",
            "exactly one of spec_id or region_id must be supplied",
        )
    service = get_service()
    target_spec_id, err = _resolve_spec_id(service, spec_id, region_id)
    if err is not None:
        return err
    assert target_spec_id is not None  # noqa: S101 - mypy narrow
    try:
        spec = service.load_spec(target_spec_id)
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownSpecError as exc:
        return fail("unknown_spec", str(exc))
    except ValidationError as exc:  # pragma: no cover - corrupted spec on disk
        return fail("invalid_spec", str(exc))
    return ok(spec.model_dump(mode="json"))


__all__ = [
    "analyze_region",
    "generate_region",
    "inspect_spec",
    "render_view",
    "reroll_seed",
]
