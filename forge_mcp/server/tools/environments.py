"""MCP tools for environment archetypes, binding, and resolution.

Phase 6-f Stage F: exposes the service-layer environment CRUD + binding
(Stage A) and the deterministic resolver (Stage C) over the v1 MCP
tool surface.

All tools return the standard ``{"ok": True, "result": ...}`` /
``{"ok": False, "error": {...}}`` envelope.
"""

from __future__ import annotations

from pydantic import ValidationError

from forge_mcp.project.schemas import (
    EnvironmentNodeId,
    EnvironmentParameters,
    EnvironmentRecipe,
    NodeId,
    RegionId,
)
from forge_mcp.project.service import (
    EnvironmentInUseError,
    NoOpenProjectError,
    UnknownEnvironmentError,
    UnknownScopeError,
)
from forge_mcp.realize.environment import (
    EnvironmentResolverError,
    resolve_environment,
)
from forge_mcp.realize.environment.defaults import EnvironmentParameterError
from forge_mcp.server.tools import get_service
from forge_mcp.server.tools._responses import fail, ok


def _coerce_recipe(value: object) -> EnvironmentRecipe:
    if isinstance(value, EnvironmentRecipe):
        return value
    if not isinstance(value, str):
        msg = f"recipe must be a string, got {type(value).__name__}"
        raise TypeError(msg)
    try:
        return EnvironmentRecipe(value)
    except ValueError as exc:
        msg = f"unknown environment recipe {value!r}"
        raise ValueError(msg) from exc


def _coerce_parameters(value: object) -> EnvironmentParameters:
    if value is None:
        msg = "parameters is required"
        raise TypeError(msg)
    return EnvironmentParameters.model_validate(value)


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


def create_environment(  # noqa: PLR0911
    name: str,
    recipe: object,
    parameters: object,
    tags: object = None,
    notes: str = "",
) -> dict[str, object]:
    """Create a new :class:`EnvironmentNode` (unbound)."""
    try:
        recipe_enum = _coerce_recipe(recipe)
    except (TypeError, ValueError) as exc:
        return fail("invalid_recipe", str(exc))
    try:
        params = _coerce_parameters(parameters)
    except TypeError as exc:
        return fail("invalid_parameters", str(exc))
    except ValidationError as exc:
        return fail("invalid_parameters", str(exc))
    try:
        tag_tuple = _coerce_tags(tags)
    except TypeError as exc:
        return fail("invalid_tags", str(exc))
    try:
        env = get_service().create_environment(
            name,
            recipe_enum,
            params,
            tags=tag_tuple,
            notes=notes,
        )
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except ValidationError as exc:
        return fail("invalid_environment", str(exc))
    return ok(env.model_dump(mode="json"))


def update_environment(  # noqa: PLR0911, PLR0913 - flat kwargs mirror service signature
    environment_id: str,
    name: str | None = None,
    recipe: object = None,
    parameters: object = None,
    tags: object = None,
    notes: str | None = None,
) -> dict[str, object]:
    """Apply a partial update to an existing environment."""
    recipe_enum: EnvironmentRecipe | None = None
    if recipe is not None:
        try:
            recipe_enum = _coerce_recipe(recipe)
        except (TypeError, ValueError) as exc:
            return fail("invalid_recipe", str(exc))
    params: EnvironmentParameters | None = None
    if parameters is not None:
        try:
            params = _coerce_parameters(parameters)
        except TypeError as exc:
            return fail("invalid_parameters", str(exc))
        except ValidationError as exc:
            return fail("invalid_parameters", str(exc))
    tag_tuple: tuple[str, ...] | None = None
    if tags is not None:
        try:
            tag_tuple = _coerce_tags(tags)
        except TypeError as exc:
            return fail("invalid_tags", str(exc))
    try:
        env = get_service().update_environment(
            EnvironmentNodeId(environment_id),
            name=name,
            recipe=recipe_enum,
            parameters=params,
            tags=tag_tuple,
            notes=notes,
        )
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownEnvironmentError as exc:
        return fail("unknown_environment", str(exc))
    return ok(env.model_dump(mode="json"))


def delete_environment(environment_id: str) -> dict[str, object]:
    """Delete an environment iff no scope still binds it."""
    try:
        get_service().delete_environment(EnvironmentNodeId(environment_id))
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownEnvironmentError as exc:
        return fail("unknown_environment", str(exc))
    except EnvironmentInUseError as exc:
        return fail("environment_in_use", str(exc))
    return ok({"deleted": environment_id})


def list_environments() -> dict[str, object]:
    """Return a deterministic summary of every environment node."""
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    summaries = [
        {
            "environment_id": str(e.node_id),
            "name": e.name,
            "recipe": e.recipe.value,
            "tags": list(e.tags),
        }
        for e in sorted(state.environments.values(), key=lambda x: str(x.node_id))
    ]
    return ok({"environments": summaries})


def get_environment(environment_id: str) -> dict[str, object]:
    """Return one environment's full record."""
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    env = state.environments.get(EnvironmentNodeId(environment_id))
    if env is None:
        return fail("unknown_environment", f"unknown environment {environment_id!r}")
    return ok(env.model_dump(mode="json"))


def bind_environment(scope_node_id: str, environment_id: str) -> dict[str, object]:
    """Bind ``environment_id`` to ``scope_node_id`` (world root or region)."""
    try:
        get_service().bind_environment(
            NodeId(scope_node_id),
            EnvironmentNodeId(environment_id),
        )
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownEnvironmentError as exc:
        return fail("unknown_environment", str(exc))
    except UnknownScopeError as exc:
        return fail("unknown_scope", str(exc))
    return ok({"scope_node_id": scope_node_id, "environment_id": environment_id})


def unbind_environment(scope_node_id: str) -> dict[str, object]:
    """Clear the environment binding on ``scope_node_id``."""
    try:
        get_service().unbind_environment(NodeId(scope_node_id))
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownScopeError as exc:
        return fail("unknown_scope", str(exc))
    return ok({"scope_node_id": scope_node_id})


def resolve_environment_tool(region_id: str | None = None) -> dict[str, object]:
    """Resolve the environment plan for ``region_id`` (or world default).

    Walks region -> world root -> hard-coded default. Returns the flat
    :class:`ResolvedEnvironment` payload that the adapter consumes.
    """
    try:
        state = get_service().state
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    try:
        plan = resolve_environment(
            state,
            region_id=RegionId(region_id) if region_id is not None else None,
        )
    except EnvironmentResolverError as exc:
        return fail("resolver_error", str(exc))
    except EnvironmentParameterError as exc:
        return fail("invalid_environment_parameters", str(exc))
    return ok(plan.model_dump(mode="json"))


__all__ = [
    "bind_environment",
    "create_environment",
    "delete_environment",
    "get_environment",
    "list_environments",
    "resolve_environment_tool",
    "unbind_environment",
    "update_environment",
]
