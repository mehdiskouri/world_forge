"""Composite-material resolver and plan IR (Phase 6-bis).

The resolver is a pure function: ``ProjectState → CompositeMaterialPlan``.
It walks the ``spatial_containment`` layer upward from a region, collects
every ``material_application`` edge whose target sits in that ancestor
chain, expands each application through its ``material_composition``
extends/composes edges, and emits a deterministic, content-addressed
plan that the Blender adapter can build into a single shader-graph
material.

Backwards-compat: when a region has no material applications the
resolver synthesizes the legacy default archetype (Phase 4's monolithic
``apply_terrain_material`` parameters), so existing realization tests
keep their shader output byte-for-byte.
"""

from __future__ import annotations

from forge_mcp.realize.material.defaults import (
    DEFAULT_TERRAIN_ARCHETYPE_ID,
    default_terrain_archetype,
    validate_recipe_parameters,
)
from forge_mcp.realize.material.plan import (
    CompositeMaterialPlan,
    ResolvedLayer,
    compute_plan_id,
    make_plan,
)
from forge_mcp.realize.material.resolver import resolve_plan

__all__ = [
    "DEFAULT_TERRAIN_ARCHETYPE_ID",
    "CompositeMaterialPlan",
    "ResolvedLayer",
    "compute_plan_id",
    "default_terrain_archetype",
    "make_plan",
    "resolve_plan",
    "validate_recipe_parameters",
]
