"""MCP tools for material archetypes, applications, compositions, and resolution.

Phase 6-bis Phase C: exposes the service-layer CRUD (Phase A) and the
deterministic resolver (Phase B) over the v1 MCP tool surface.

All tools return the standard ``{"ok": True, "result": ...}`` /
``{"ok": False, "error": {...}}`` envelope; coercion of JSON-shaped
inputs (recipe strings, scope strings, mask dicts) is centralised in
``_coerce_*`` helpers so individual tool wrappers stay thin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from forge_mcp.project.schemas import (
    LAYER_MATERIAL_APPLICATION,
    MaterialApplicationAttrs,
    MaterialArchetypeId,
    MaterialCompositionAttrs,
    MaterialRecipe,
    NodeId,
    RegionId,
)
from forge_mcp.project.service import (
    ArchetypeInUseError,
    MaterialCompositionCycleError,
    MaterialEdgePolicyError,
    NoOpenProjectError,
    UnknownArchetypeError,
    UnknownMaterialEdgeError,
    UnknownTargetNodeError,
)
from forge_mcp.realize.material import resolve_plan
from forge_mcp.realize.material.defaults import RecipeParameterError
from forge_mcp.realize.material.resolver import ResolverError
from forge_mcp.server.tools import get_service
from forge_mcp.server.tools._responses import fail, ok

if TYPE_CHECKING:
    from forge_mcp._types import JsonValue


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


def _coerce_recipe(value: object) -> MaterialRecipe:
    """Coerce a JSON string to :class:`MaterialRecipe`."""
    if isinstance(value, MaterialRecipe):
        return value
    if not isinstance(value, str):
        msg = f"recipe must be a string, got {type(value).__name__}"
        raise TypeError(msg)
    try:
        return MaterialRecipe(value)
    except ValueError as exc:
        msg = f"unknown material recipe {value!r}"
        raise ValueError(msg) from exc


def _coerce_parameters(value: object) -> dict[str, JsonValue]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        msg = "parameters must be a JSON object"
        raise TypeError(msg)
    out: dict[str, JsonValue] = {}
    for key, sub in value.items():
        if not isinstance(key, str):
            msg = "parameter keys must be strings"
            raise TypeError(msg)
        out[key] = sub
    return out


def _coerce_tags(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        msg = "tags must be a list of strings"
        raise TypeError(msg)
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            msg = "tags must be strings"
            raise TypeError(msg)
        out.append(item)
    return tuple(out)


def _coerce_application_attrs(value: object) -> MaterialApplicationAttrs:
    if value is None:
        msg = "attrs is required"
        raise TypeError(msg)
    return MaterialApplicationAttrs.model_validate(value)


def _coerce_composition_attrs(value: object) -> MaterialCompositionAttrs:
    if value is None:
        msg = "attrs is required"
        raise TypeError(msg)
    return MaterialCompositionAttrs.model_validate(value)


# ---------------------------------------------------------------------------
# Archetype CRUD
# ---------------------------------------------------------------------------


def create_material_archetype(
    name: str,
    recipe: object,
    parameters: object = None,
    tags: object = None,
    notes: str = "",
) -> dict[str, object]:
    """Create a new material archetype node."""
    try:
        recipe_enum = _coerce_recipe(recipe)
    except (TypeError, ValueError) as exc:
        return fail("invalid_recipe", str(exc))
    try:
        params = _coerce_parameters(parameters)
    except TypeError as exc:
        return fail("invalid_parameters", str(exc))
    try:
        tag_tuple = _coerce_tags(tags)
    except TypeError as exc:
        return fail("invalid_tags", str(exc))
    try:
        archetype = get_service().create_material_archetype(
            name,
            recipe_enum,
            params,
            tags=tag_tuple,
            notes=notes,
        )
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except ValidationError as exc:
        return fail("invalid_archetype", str(exc))
    return ok(archetype.model_dump(mode="json"))


def update_material_archetype(
    archetype_id: str,
    name: str | None = None,
    parameters: object = None,
    tags: object = None,
    notes: str | None = None,
) -> dict[str, object]:
    """Apply a partial update to an existing archetype."""
    params: dict[str, JsonValue] | None = None
    if parameters is not None:
        try:
            params = _coerce_parameters(parameters)
        except TypeError as exc:
            return fail("invalid_parameters", str(exc))
    tag_tuple: tuple[str, ...] | None = None
    if tags is not None:
        try:
            tag_tuple = _coerce_tags(tags)
        except TypeError as exc:
            return fail("invalid_tags", str(exc))
    try:
        archetype = get_service().update_material_archetype(
            MaterialArchetypeId(archetype_id),
            name=name,
            parameters=params,
            tags=tag_tuple,
            notes=notes,
        )
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownArchetypeError as exc:
        return fail("unknown_archetype", str(exc))
    return ok(archetype.model_dump(mode="json"))


def delete_material_archetype(archetype_id: str) -> dict[str, object]:
    """Delete an archetype iff no material edge references it."""
    try:
        get_service().delete_material_archetype(MaterialArchetypeId(archetype_id))
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownArchetypeError as exc:
        return fail("unknown_archetype", str(exc))
    except ArchetypeInUseError as exc:
        return fail("archetype_in_use", str(exc))
    return ok({"deleted": archetype_id})


def list_material_archetypes() -> dict[str, object]:
    """Return a deterministic summary of every archetype."""
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    summaries = [
        {
            "archetype_id": str(a.node_id),
            "name": a.name,
            "recipe": a.recipe.value,
            "tags": list(a.tags),
        }
        for a in sorted(state.archetypes.values(), key=lambda x: str(x.node_id))
    ]
    return ok({"archetypes": summaries})


def get_material_archetype(archetype_id: str) -> dict[str, object]:
    """Return one archetype's full record."""
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    archetype = state.archetypes.get(MaterialArchetypeId(archetype_id))
    if archetype is None:
        return fail("unknown_archetype", f"unknown archetype {archetype_id!r}")
    return ok(archetype.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Application CRUD
# ---------------------------------------------------------------------------


def apply_material(  # noqa: PLR0911
    archetype_id: str,
    target_node_id: str,
    attrs: object = None,
) -> dict[str, object]:
    """Apply an archetype to a target node (region or world root)."""
    try:
        attrs_model = _coerce_application_attrs(attrs)
    except TypeError as exc:
        return fail("invalid_attrs", str(exc))
    except ValidationError as exc:
        return fail("invalid_attrs", str(exc))
    try:
        edge = get_service().apply_material(
            MaterialArchetypeId(archetype_id),
            NodeId(target_node_id),
            attrs=attrs_model,
        )
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownArchetypeError as exc:
        return fail("unknown_archetype", str(exc))
    except UnknownTargetNodeError as exc:
        return fail("unknown_target_node", str(exc))
    except MaterialEdgePolicyError as exc:
        return fail("invalid_edge", str(exc))
    return ok(edge.model_dump(mode="json"))


def unapply_material(edge_id: str) -> dict[str, object]:
    """Remove a ``material_application`` edge."""
    try:
        get_service().unapply_material(edge_id)
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownMaterialEdgeError as exc:
        return fail("unknown_material_edge", str(exc))
    return ok({"deleted": edge_id})


def list_material_applications() -> dict[str, object]:
    """Return every ``material_application`` edge in deterministic order."""
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    edges = sorted(
        state.edges.get(LAYER_MATERIAL_APPLICATION, ()),
        key=lambda e: e.edge_id,
    )
    return ok({"applications": [e.model_dump(mode="json") for e in edges]})


# ---------------------------------------------------------------------------
# Composition CRUD
# ---------------------------------------------------------------------------


def compose_material(  # noqa: PLR0911
    parent_archetype_id: str,
    child_archetype_id: str,
    attrs: object = None,
) -> dict[str, object]:
    """Add a directed ``material_composition`` edge ``parent -> child``."""
    try:
        attrs_model = _coerce_composition_attrs(attrs)
    except TypeError as exc:
        return fail("invalid_attrs", str(exc))
    except ValidationError as exc:
        return fail("invalid_attrs", str(exc))
    try:
        edge = get_service().compose_material(
            MaterialArchetypeId(parent_archetype_id),
            MaterialArchetypeId(child_archetype_id),
            attrs=attrs_model,
        )
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownArchetypeError as exc:
        return fail("unknown_archetype", str(exc))
    except MaterialEdgePolicyError as exc:
        return fail("invalid_edge", str(exc))
    except MaterialCompositionCycleError as exc:
        return fail("composition_cycle", str(exc))
    return ok(edge.model_dump(mode="json"))


def uncompose_material(edge_id: str) -> dict[str, object]:
    """Remove a ``material_composition`` edge."""
    try:
        get_service().uncompose_material(edge_id)
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownMaterialEdgeError as exc:
        return fail("unknown_material_edge", str(exc))
    return ok({"deleted": edge_id})


# ---------------------------------------------------------------------------
# Resolution preview
# ---------------------------------------------------------------------------


def resolve_material(
    region_id: str,
    mesh_name: str = "preview",
    elevation_min: float = 0.0,
    elevation_max: float = 1.0,
) -> dict[str, object]:
    """Compute the composite material plan for ``region_id`` (read-only)."""
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    try:
        plan = resolve_plan(
            state,
            RegionId(region_id),
            mesh_name=mesh_name,
            elevation_min=elevation_min,
            elevation_max=elevation_max,
        )
    except ResolverError as exc:
        return fail("resolver_error", str(exc))
    except RecipeParameterError as exc:
        return fail("invalid_recipe_parameters", str(exc))
    return ok(plan.model_dump(mode="json"))


__all__ = [
    "apply_material",
    "compose_material",
    "create_material_archetype",
    "delete_material_archetype",
    "get_material_archetype",
    "list_material_applications",
    "list_material_archetypes",
    "resolve_material",
    "unapply_material",
    "uncompose_material",
    "update_material_archetype",
]
