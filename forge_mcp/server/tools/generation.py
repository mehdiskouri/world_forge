"""``forge.generate_region`` / ``reroll_seed`` / ``analyze_region`` / ``inspect_spec``.

Phase-3 generation surface. All four tools are pure orchestrators over
:mod:`forge_mcp.descriptor.map_to_spec`, :mod:`forge_mcp.generate.terrain`,
and :mod:`forge_mcp.analyze.terrain_analysis`. Determinism is achieved by
threading the region's ``seed`` (rerollable) through both the spec
compiler and the terrain generators.
"""

from __future__ import annotations

from hashlib import blake2b
from typing import TYPE_CHECKING

from pydantic import ValidationError

from forge_mcp.analyze.terrain_analysis import TerrainAnalysis, analyze
from forge_mcp.descriptor.map_to_spec import map_to_spec
from forge_mcp.generate import terrain as terrain_generator
from forge_mcp.generate.heightmap import load_npy, save_npy, save_png16
from forge_mcp.generate.stream import StreamGeometry
from forge_mcp.project.schemas import RegionId, SpecId, SpecRecord
from forge_mcp.project.service import (
    NoOpenProjectError,
    UnknownRegionError,
    UnknownSpecError,
    _now,
)
from forge_mcp.server.tools import get_service
from forge_mcp.server.tools._responses import fail, ok

if TYPE_CHECKING:
    from forge_mcp.project.service import ProjectService


_REROLL_DIGEST_BYTES = 8


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


def generate_region(region_id: str) -> dict[str, object]:
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
    spec = map_to_spec(
        region.structured_descriptor,
        region.seed,
        blender_version=metadata.blender_version,
        bpy_hypergraph_version=metadata.bpy_hypergraph_version,
        now=_now(),
    )

    try:
        result = terrain_generator.run(spec, seed=region.seed)
    except (ValueError, RuntimeError) as exc:  # pragma: no cover - generator preconditions
        return fail("generation_failed", str(exc))

    analysis = analyze(result.heightmap, result.stream_geometry)
    new_summary = spec.body.summary.model_copy(update=_project_summary(analysis))
    new_body = spec.body.model_copy(update={"summary": new_summary})
    spec_with_summary: SpecRecord = spec.model_copy(update={"body": new_body})
    service.persist_spec(spec_with_summary)
    service.link_region_to_spec(rid, spec_with_summary.spec_id)

    npy_path, png_path, stream_path = _persist_realization(service, rid, result)
    service.record_generation(
        rid,
        spec_with_summary.spec_id,
        generators_used=result.generators_used,
    )
    return ok(
        {
            "region_id": region_id,
            "spec_id": str(spec_with_summary.spec_id),
            "heightmap_npy_path": npy_path,
            "heightmap_png_path": png_path,
            "stream_geometry_path": stream_path,
            "blend_path": None,
            "generators_used": list(result.generators_used),
            "analysis": analysis.model_dump(mode="json"),
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
    "reroll_seed",
]
