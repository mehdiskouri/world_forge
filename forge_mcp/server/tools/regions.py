"""``forge.create_region`` / ``update_region`` / ``delete_region`` / list / get."""

from __future__ import annotations

from pydantic import ValidationError

from forge_mcp.descriptor.schema import StructuredDescriptor
from forge_mcp.project.schemas import RegionId
from forge_mcp.project.service import (
    NoOpenProjectError,
    RegionOverlapError,
    RegionPolygonError,
    UnknownRegionError,
)
from forge_mcp.server.tools import get_service
from forge_mcp.server.tools._responses import fail, ok


def _coerce_polygon(value: object) -> tuple[tuple[float, float], ...]:
    """Coerce a JSON-shaped polygon into the typed tuple-of-tuples form."""
    if not isinstance(value, list):
        msg = "polygon_coords must be a list of [x, y] pairs"
        raise TypeError(msg)
    coerced: list[tuple[float, float]] = []
    for raw in value:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:  # noqa: PLR2004 - 2D pair
            msg = "each polygon coordinate must be a length-2 [x, y] pair"
            raise TypeError(msg)
        x, y = raw
        coerced.append((float(x), float(y)))
    return tuple(coerced)


def _coerce_descriptor(value: object) -> StructuredDescriptor | None:
    if value is None:
        return None
    return StructuredDescriptor.model_validate(value)


def create_region(
    name: str,
    polygon_coords: object,
    structured_descriptor: object = None,
    seed: int | None = None,
) -> dict[str, object]:
    """Create a new region; returns the persisted record or a structured error."""
    try:
        coords = _coerce_polygon(polygon_coords)
    except TypeError as exc:
        return fail("invalid_polygon_coords", str(exc))
    try:
        descriptor = _coerce_descriptor(structured_descriptor)
    except ValidationError as exc:
        return fail("invalid_descriptor", str(exc))
    try:
        region = get_service().create_region(
            name,
            coords,
            structured_descriptor=descriptor,
            seed=seed,
        )
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except RegionPolygonError as exc:
        return fail("invalid_polygon", str(exc))
    except RegionOverlapError as exc:
        return fail("region_overlap", str(exc))
    return ok(region.model_dump(mode="json"))


def update_region(  # noqa: PLR0911 - one return per structured-error category is the point
    region_id: str,
    name: str | None = None,
    polygon_coords: object = None,
    structured_descriptor: object = None,
    *,
    clear_descriptor: bool = False,
) -> dict[str, object]:
    """Apply a partial update to an existing region."""
    coords: tuple[tuple[float, float], ...] | None = None
    if polygon_coords is not None:
        try:
            coords = _coerce_polygon(polygon_coords)
        except TypeError as exc:
            return fail("invalid_polygon_coords", str(exc))
    try:
        descriptor = _coerce_descriptor(structured_descriptor)
    except ValidationError as exc:
        return fail("invalid_descriptor", str(exc))
    try:
        region = get_service().update_region(
            RegionId(region_id),
            name=name,
            polygon_coords=coords,
            structured_descriptor=descriptor,
            clear_descriptor=clear_descriptor,
        )
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownRegionError as exc:
        return fail("unknown_region", str(exc))
    except RegionPolygonError as exc:
        return fail("invalid_polygon", str(exc))
    except RegionOverlapError as exc:
        return fail("region_overlap", str(exc))
    return ok(region.model_dump(mode="json"))


def delete_region(region_id: str) -> dict[str, object]:
    """Delete a region and any boundaries it participates in."""
    try:
        get_service().delete_region(RegionId(region_id))
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownRegionError as exc:
        return fail("unknown_region", str(exc))
    return ok({"deleted": region_id})


def list_regions() -> dict[str, object]:
    """Return a deterministic list of region summaries."""
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    summaries = [
        {
            "region_id": str(r.node_id),
            "name": r.name,
            "tier": r.tier.value,
            "scale_level": r.scale_level,
            "has_descriptor": r.structured_descriptor is not None,
        }
        for r in sorted(state.regions.values(), key=lambda x: str(x.node_id))
    ]
    return ok({"regions": summaries})


def get_region(region_id: str) -> dict[str, object]:
    """Return one region's full record."""
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    region = state.regions.get(RegionId(region_id))
    if region is None:
        return fail("unknown_region", f"unknown region {region_id!r}")
    return ok(region.model_dump(mode="json"))


__all__ = [
    "create_region",
    "delete_region",
    "get_region",
    "list_regions",
    "update_region",
]
