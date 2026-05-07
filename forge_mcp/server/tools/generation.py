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
from forge_mcp.project.schemas import RegionId, SpecId, SpecRecord
from forge_mcp.project.service import (
    NoOpenProjectError,
    UnknownRegionError,
    UnknownSpecError,
    _now,
)
from forge_mcp.realize.engine import RealizerError
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
_PNG_MAX_BYTES: Final[dict[str, int]] = {
    RESOLUTION_PREVIEW: 350_000,
    RESOLUTION_DEFAULT: 1_000_000,
    RESOLUTION_FULL: 4_000_000,
}

_DEFAULT_PLAN_MESH_PREFIX: Final[str] = "terrain_"

_RENDER_ENGINE: Final[str] = "BLENDER_EEVEE"  # Blender 5.0 enum identifier


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
        blend_filepath=str(blend_filepath),
        heightmap_image_filepath=str(heightmap_image_filepath),
        displace_strength=displace_strength,
    )


def _run_realizer(
    service: ProjectService,
    region_id: RegionId,
    spec_id: SpecId,
    heightmap: Heightmap,
    targets: tuple[tuple[str, str], ...],
) -> _RealizationArtifacts | None:
    """Drive the realizer, persisting all outputs atomically.

    ``targets`` is a tuple of ``(view_kind, resolution)`` pairs to
    render after the single ``realize_region`` macro completes. Returns
    ``None`` when no realizer factory is installed.
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
    )

    plan: list[tuple[str, str, Path, Path, Path, RenderPreviewInputs]] = []
    for view_kind, resolution in targets:
        preview_path = paths.preview_path(region_id, view_kind, resolution)
        preview_tmp = preview_path.with_name(preview_path.name + ".tmp")
        trace_path = paths.realization_trace_path(region_id, view_kind, resolution)
        width, height = _RESOLUTIONS[resolution]
        render_inputs = RenderPreviewInputs(
            filepath=str(preview_tmp),
            resolution_x=width,
            resolution_y=height,
            camera_name=_camera_name_for_view(region_id, view_kind),
            engine=_RENDER_ENGINE,
            png_max_bytes=_PNG_MAX_BYTES[resolution],
        )
        plan.append((view_kind, resolution, preview_path, preview_tmp, trace_path, render_inputs))

    with factory() as engine:
        realize_result = realize_region(engine, realize_inputs)
        render_results = [render_preview(engine, item[5]) for item in plan]

    # Atomic publish: only after every render succeeded.
    os.replace(blend_tmp, blend_path)  # noqa: PTH105 - need os primitive on Path
    views: list[_ViewArtifacts] = []
    for (view_kind, resolution, preview_path, preview_tmp, trace_path, _ri), render_result in zip(
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
        "render_resolution": list(_RESOLUTIONS[view.resolution]),
        "render_file_size_bytes": _render_size(view.render_result),
    }


def generate_region(region_id: str) -> dict[str, object]:  # noqa: PLR0911 - one return per failure surface
    """Compile + generate + analyze a region. Persists spec, heightmap, analysis."""
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
        result = terrain_generator.run(
            spec,
            seed=region.seed,
            boundary_conditions=_collect_boundary_conditions(service, region, rid),
        )
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
        )
    except RealizerError as exc:
        return fail("realizer_failed", str(exc))
    if artifacts is not None:
        blend_path = str(artifacts.blend_path)
        previews = {view.view_kind: _view_summary(view) for view in artifacts.views}
        realization_summary = {
            "macro": artifacts.realize_result.macro,
            "sequence_id": artifacts.realize_result.sequence_id,
            "render_engine": _RENDER_ENGINE,
            "default_resolution": list(_RESOLUTIONS[DEFAULT_RESOLUTION]),
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
) -> dict[str, object]:
    """Re-render a previously-generated region for one ``(view_kind, resolution)``.

    ``view_kind`` is one of ``"ortho_top"`` / ``"perspective_se"``;
    ``resolution`` is one of ``"preview"`` (512x384) / ``"default"``
    (1024x768) / ``"full"`` (2048x1536). The realizer rebuilds the
    scene from the persisted heightmap each call so the on-disk
    ``.blend`` stays honest about what the most recent render drew.
    """
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
        )
    except RealizerError as exc:
        return fail("realizer_failed", str(exc))
    assert artifacts is not None  # noqa: S101 - factory guarded above
    view = artifacts.views[0]
    return ok(
        {
            "region_id": region_id,
            "blend_path": str(artifacts.blend_path),
            "render_engine": _RENDER_ENGINE,
            **_view_summary(view),
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
