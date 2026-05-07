"""MCP tools for sub-region nodes (Phase 6-c Phase E).

Six envelope-shaped tools wrap the Phase 6-c service CRUD plus a
read-only ``preview_sub_region_coverage`` that runs the predicate
evaluator against the parent region's persisted heightmap (and stream
geometry, when present) without spinning up Blender.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter, ValidationError

from forge_mcp.analyze.terrain_analysis import compute_predicate_grids
from forge_mcp.generate.heightmap import load_npy
from forge_mcp.generate.stream import StreamGeometry
from forge_mcp.project.schemas import RegionId, SubRegionId, SubRegionPredicate
from forge_mcp.project.service import (
    NoOpenProjectError,
    SubRegionInUseError,
    UnknownParentRegionError,
    UnknownSubRegionError,
)
from forge_mcp.realize.material.predicate import evaluate_predicate
from forge_mcp.server.tools import get_service
from forge_mcp.server.tools._responses import fail, ok

if TYPE_CHECKING:
    from collections.abc import Iterable

# Pydantic adapter so the discriminated SubRegionPredicate union accepts a
# bare JSON dict from MCP callers and dispatches on the ``kind`` tag.
_PREDICATE_ADAPTER: TypeAdapter[SubRegionPredicate] = TypeAdapter(SubRegionPredicate)


def _coerce_predicate(value: object) -> SubRegionPredicate:
    if value is None:
        msg = "predicate is required"
        raise TypeError(msg)
    if isinstance(value, dict):
        return _PREDICATE_ADAPTER.validate_python(value)
    msg = f"predicate must be a JSON object, got {type(value).__name__}"
    raise TypeError(msg)


def _coerce_tags(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        msg = "tags must be a list of strings"
        raise TypeError(msg)
    items: Iterable[object] = value
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            msg = "tags must be strings"
            raise TypeError(msg)
        out.append(item)
    return tuple(out)


def create_sub_region(  # noqa: PLR0911 - distinct error envelopes per failure mode
    parent_region_id: str,
    name: str,
    predicate: object,
    tags: object = None,
    notes: str = "",
) -> dict[str, object]:
    """Create a predicate-typed sub-region under an existing region."""
    try:
        predicate_model = _coerce_predicate(predicate)
    except (TypeError, ValueError) as exc:
        return fail("invalid_predicate", str(exc))
    except ValidationError as exc:
        return fail("invalid_predicate", str(exc))
    try:
        tag_tuple = _coerce_tags(tags)
    except TypeError as exc:
        return fail("invalid_tags", str(exc))
    try:
        sub_region = get_service().create_sub_region(
            RegionId(parent_region_id),
            name,
            predicate_model,
            tags=tag_tuple,
            notes=notes,
        )
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownParentRegionError as exc:
        return fail("unknown_parent_region", str(exc))
    except ValidationError as exc:
        return fail("invalid_sub_region", str(exc))
    return ok(sub_region.model_dump(mode="json"))


def update_sub_region(
    sub_region_id: str,
    name: str | None = None,
    predicate: object = None,
    tags: object = None,
    notes: str | None = None,
) -> dict[str, object]:
    """Apply a partial update to an existing sub-region."""
    predicate_model: SubRegionPredicate | None = None
    if predicate is not None:
        try:
            predicate_model = _coerce_predicate(predicate)
        except (TypeError, ValueError) as exc:
            return fail("invalid_predicate", str(exc))
        except ValidationError as exc:
            return fail("invalid_predicate", str(exc))
    tag_tuple: tuple[str, ...] | None = None
    if tags is not None:
        try:
            tag_tuple = _coerce_tags(tags)
        except TypeError as exc:
            return fail("invalid_tags", str(exc))
    try:
        sub_region = get_service().update_sub_region(
            SubRegionId(sub_region_id),
            name=name,
            predicate=predicate_model,
            tags=tag_tuple,
            notes=notes,
        )
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownSubRegionError as exc:
        return fail("unknown_sub_region", str(exc))
    return ok(sub_region.model_dump(mode="json"))


def delete_sub_region(sub_region_id: str) -> dict[str, object]:
    """Delete a sub-region iff no material application targets it."""
    try:
        get_service().delete_sub_region(SubRegionId(sub_region_id))
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownSubRegionError as exc:
        return fail("unknown_sub_region", str(exc))
    except SubRegionInUseError as exc:
        return fail("sub_region_in_use", str(exc))
    return ok({"deleted": sub_region_id})


def list_sub_regions(parent_region_id: str | None = None) -> dict[str, object]:
    """Return a deterministic list of sub-region summaries.

    When ``parent_region_id`` is provided, only sub-regions whose
    parent matches are returned.
    """
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    parent_filter = RegionId(parent_region_id) if parent_region_id is not None else None
    summaries = [
        {
            "sub_region_id": str(s.node_id),
            "parent_region_id": str(s.parent_node),
            "name": s.name,
            "predicate_kind": s.predicate.kind,
            "tags": list(s.tags),
        }
        for s in sorted(state.sub_regions.values(), key=lambda x: str(x.node_id))
        if parent_filter is None or s.parent_node == parent_filter
    ]
    return ok({"sub_regions": summaries})


def get_sub_region(sub_region_id: str) -> dict[str, object]:
    """Return one sub-region's full record."""
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    sub_region = state.sub_regions.get(SubRegionId(sub_region_id))
    if sub_region is None:
        return fail("unknown_sub_region", f"unknown sub-region {sub_region_id!r}")
    return ok(sub_region.model_dump(mode="json"))


def preview_sub_region_coverage(sub_region_id: str) -> dict[str, object]:
    """Evaluate the sub-region's predicate against the parent's heightmap.

    Read-only. Returns ``vertex_count`` (pixels selected by the
    predicate), ``total_vertices`` (pixels in the parent heightmap),
    ``coverage_fraction`` (the ratio in ``[0, 1]``), and ``bbox_uv``
    (the axis-aligned bounding box of the selected pixels in
    normalised ``[0, 1]`` heightmap coordinates, ``null`` when nothing
    is selected). Fails with ``not_generated`` when the parent region
    has no persisted heightmap.
    """
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    sub_region = state.sub_regions.get(SubRegionId(sub_region_id))
    if sub_region is None:
        return fail("unknown_sub_region", f"unknown sub-region {sub_region_id!r}")
    parent_id = RegionId(str(sub_region.parent_node))
    npy_path = state.paths.heightmap_npy_path(parent_id)
    if not npy_path.exists():
        return fail(
            "not_generated",
            (
                f"parent region {parent_id!r} has no persisted heightmap; "
                f"run forge.generate_region first"
            ),
        )
    heightmap = load_npy(npy_path)
    geo_path = state.paths.stream_geometry_path(parent_id)
    stream: StreamGeometry | None = None
    if geo_path.exists():
        stream = StreamGeometry.model_validate_json(geo_path.read_text(encoding="utf-8"))
    grids = compute_predicate_grids(heightmap, stream)
    mask = evaluate_predicate(
        sub_region.predicate,
        elevation_grid=grids.elevation_grid,
        slope_grid=grids.slope_grid,
        aspect_grid=grids.aspect_grid,
        distance_to_stream_grid=grids.distance_to_stream_grid,
    )
    total = int(mask.size)
    selected = int(mask.sum())
    coverage = float(selected) / float(total) if total else 0.0
    bbox_uv: tuple[float, float, float, float] | None
    if selected:
        rows, cols = mask.nonzero()
        height, width = mask.shape
        u_min = float(cols.min()) / float(width)
        u_max = float(cols.max() + 1) / float(width)
        v_min = float(rows.min()) / float(height)
        v_max = float(rows.max() + 1) / float(height)
        bbox_uv = (u_min, v_min, u_max, v_max)
    else:
        bbox_uv = None
    return ok(
        {
            "sub_region_id": sub_region_id,
            "parent_region_id": str(parent_id),
            "vertex_count": selected,
            "total_vertices": total,
            "coverage_fraction": coverage,
            "bbox_uv": list(bbox_uv) if bbox_uv is not None else None,
        },
    )


__all__ = [
    "create_sub_region",
    "delete_sub_region",
    "get_sub_region",
    "list_sub_regions",
    "preview_sub_region_coverage",
    "update_sub_region",
]
