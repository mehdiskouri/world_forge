"""Pure-function resolver: project state → :class:`ResolvedEnvironment`.

Resolution chain (most-specific wins):

1. If ``region_id`` is provided and the region's ``environment_id`` is
   set, use the bound :class:`EnvironmentNode`.
2. Else if the world root's ``environment_id`` is set, use it.
3. Else fall back to the synthetic default
   (:func:`forge_mcp.realize.environment.defaults.default_environment_node`).

The resolver computes solar position from the chosen environment's
``latitude_deg``, ``longitude_deg``, and ``datetime_utc`` via
:func:`forge_mcp.environment.sun.compute_sun_direction`, then content-
addresses the flat resolved payload to produce a stable
``forge.world.<plan_id>`` cache key.

Determinism: the resolver never mutates state; given equal inputs it
produces equal outputs (including byte-identical ``plan_id``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_mcp.environment.sun import compute_sun_direction
from forge_mcp.project.schemas import (
    EnvironmentNode,
    NodeId,
    RegionId,
)
from forge_mcp.realize.environment.defaults import (
    default_environment_node,
    validate_environment_parameters,
)
from forge_mcp.realize.environment.plan import (
    ResolvedEnvironment,
    compute_environment_plan_id,
)

if TYPE_CHECKING:
    from forge_mcp.project.service import ProjectState


class EnvironmentResolverError(ValueError):
    """Raised when project state cannot produce a valid environment plan.

    The most common cause is a scope binding that points at a missing
    environment node (e.g. the bound environment was deleted out from
    under the binding without unbinding first). ``ProjectService`` is
    expected to refuse such deletions, so this error is a defence in
    depth for direct state mutation.
    """


def resolve_environment(
    state: ProjectState,
    region_id: RegionId | None = None,
) -> ResolvedEnvironment:
    """Resolve the environment plan for ``region_id`` (or the world default).

    Args:
        state: A live :class:`~forge_mcp.project.service.ProjectState`.
        region_id: When provided, the region whose binding is consulted
            first. ``None`` skips straight to the world-root default.

    Returns:
        A frozen :class:`ResolvedEnvironment` with computed sun position.

    Raises:
        EnvironmentResolverError: If a scope binds an environment id
            that is not present in ``state.environments``.
    """
    node, scope_label = _select_environment(state, region_id)
    validate_environment_parameters(node.recipe, node.parameters)
    return _build_plan(node, scope_label)


def _select_environment(
    state: ProjectState,
    region_id: RegionId | None,
) -> tuple[EnvironmentNode, str]:
    """Walk the binding fallback chain and return ``(node, scope_label)``."""
    if region_id is not None:
        if region_id not in state.regions:
            msg = f"unknown region id: {region_id!r}"
            raise EnvironmentResolverError(msg)
        bound = state.regions[region_id].environment_id
        if bound is not None:
            if bound not in state.environments:
                msg = (
                    f"region {region_id!r} binds environment {bound!r} "
                    f"which is missing from state.environments"
                )
                raise EnvironmentResolverError(msg)
            return state.environments[bound], f"region:{region_id}"

    if state.world_root is not None:
        bound = state.world_root.environment_id
        if bound is not None:
            if bound not in state.environments:
                msg = (
                    f"world_root binds environment {bound!r} "
                    f"which is missing from state.environments"
                )
                raise EnvironmentResolverError(msg)
            return state.environments[bound], "world_root"

    return default_environment_node(), "default"


def _build_plan(node: EnvironmentNode, scope_label: str) -> ResolvedEnvironment:
    """Compute sun direction and content-address the resolved payload."""
    params = node.parameters
    sun = compute_sun_direction(
        params.latitude_deg,
        params.longitude_deg,
        params.datetime_utc,
    )
    payload: dict[str, object] = {
        "recipe": node.recipe.value,
        "sun_color": list(params.sun_color),
        "sun_intensity_w_m2": params.sun_intensity_w_m2,
        "sun_azimuth_deg": sun.azimuth_deg,
        "sun_elevation_deg": sun.elevation_deg,
        "sun_vector": list(sun.vector),
        "sky_zenith_color": list(params.sky_zenith_color),
        "sky_horizon_color": list(params.sky_horizon_color),
        "ambient_color": list(params.ambient_color),
        "ambient_strength": params.ambient_strength,
        "fog_color": list(params.fog_color),
        "fog_density": params.fog_density,
        "fog_height_falloff_m": params.fog_height_falloff_m,
        "season": params.season.value,
        "datetime_utc": params.datetime_utc.isoformat(),
        "latitude_deg": params.latitude_deg,
        "longitude_deg": params.longitude_deg,
    }
    plan_id = compute_environment_plan_id(payload)
    source_id = None if scope_label == "default" else str(node.node_id)
    return ResolvedEnvironment(
        plan_id=plan_id,
        recipe=node.recipe,
        sun_color=params.sun_color,
        sun_intensity_w_m2=params.sun_intensity_w_m2,
        sun_azimuth_deg=sun.azimuth_deg,
        sun_elevation_deg=sun.elevation_deg,
        sun_vector=sun.vector,
        sky_zenith_color=params.sky_zenith_color,
        sky_horizon_color=params.sky_horizon_color,
        ambient_color=params.ambient_color,
        ambient_strength=params.ambient_strength,
        fog_color=params.fog_color,
        fog_density=params.fog_density,
        fog_height_falloff_m=params.fog_height_falloff_m,
        season=params.season,
        datetime_utc=params.datetime_utc,
        latitude_deg=params.latitude_deg,
        longitude_deg=params.longitude_deg,
        scope_label=scope_label,
        source_environment_id=source_id,
    )


def resolve_for_node(
    state: ProjectState,
    node_id: NodeId,
) -> ResolvedEnvironment:
    """Convenience: resolve for ``node_id`` whether it's a region or the world root.

    Routes ``node_id == "world_root"`` to the world-default chain and
    everything else to the per-region chain. Useful for tool surfaces
    that take a single scope id without distinguishing between the two.
    """
    if state.world_root is not None and node_id == state.world_root.node_id:
        return resolve_environment(state, region_id=None)
    return resolve_environment(state, region_id=RegionId(str(node_id)))


__all__ = [
    "EnvironmentResolverError",
    "resolve_environment",
    "resolve_for_node",
]
