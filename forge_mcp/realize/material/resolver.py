"""Pure-function resolver: project state → composite material plan.

The resolver never mutates state. It reads the ``spatial_containment``,
``material_application``, and ``material_composition`` layers, expands
each application through its archetype's extends/composes graph, sorts
the resulting layers by *most-specific-on-top* precedence, and emits a
deterministic :class:`CompositeMaterialPlan`.

Determinism: the plan id depends only on the resolved layers, so two
regions that resolve to the same flattened layer set share a single
Blender material across the realization.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_mcp.project.schemas import (
    LAYER_MATERIAL_APPLICATION,
    LAYER_MATERIAL_COMPOSITION,
    LAYER_SPATIAL_CONTAINMENT,
    Edge,
    MaterialApplicationAttrs,
    MaterialArchetypeId,
    MaterialArchetypeNode,
    MaterialCompositionAttrs,
    MaterialCompositionMode,
    MaterialRecipe,
    NodeId,
    PredicateMask,
    RegionId,
    SubRegionNode,
    material_scope_specificity,
)
from forge_mcp.realize.material.defaults import (
    default_terrain_archetype,
    validate_recipe_parameters,
)
from forge_mcp.realize.material.plan import (
    CompositeMaterialPlan,
    ProceduralInstancer,
    ResolvedLayer,
    make_plan,
)

if TYPE_CHECKING:
    from forge_mcp._types import JsonValue
    from forge_mcp.project.service import ProjectState


class ResolverError(ValueError):
    """Raised when the project state cannot produce a valid material plan."""


_INSTANCER_RECIPES: frozenset[MaterialRecipe] = frozenset(
    {MaterialRecipe.PROCEDURAL_GRASS},
)
"""Phase 6-e Stage F: recipes routed through the geometry-nodes instancer.

Stage D adds ``MaterialRecipe.PROCEDURAL_GRASS``. When an
archetype's recipe is in this set, :func:`_expand_application`
emits the layer with a populated :attr:`ResolvedLayer.instancer`
instead of as a surface contribution; the composite material
handler skips the surface mix for those layers, and the realiser
routes them through ``material.attach_instancer``.
"""


def _instancer_for(
    recipe: MaterialRecipe,
    parameters: dict[str, JsonValue],
) -> ProceduralInstancer | None:
    """Return a :class:`ProceduralInstancer` when the recipe routes to one.

    Pulls ``density_per_m2`` and ``seed`` from the layer parameters
    (defaulted), so two regions with identical instancer setups
    content-hash to the same plan id.
    """
    if recipe not in _INSTANCER_RECIPES:
        return None
    raw_density = parameters.get("density_per_m2", 200.0)
    raw_seed = parameters.get("seed", 0)
    if not isinstance(raw_density, (int, float)) or isinstance(raw_density, bool):
        msg = f"{recipe!s} requires numeric density_per_m2, got {raw_density!r}"
        raise ResolverError(msg)
    if not isinstance(raw_seed, int) or isinstance(raw_seed, bool):
        msg = f"{recipe!s} requires integer seed, got {raw_seed!r}"
        raise ResolverError(msg)
    return ProceduralInstancer(density_per_m2=float(raw_density), seed=raw_seed)


def resolve_plan(
    state: ProjectState,
    region_id: RegionId,
    *,
    mesh_name: str,
    elevation_min: float,
    elevation_max: float,
) -> CompositeMaterialPlan:
    """Resolve the composite material plan for ``region_id``'s mesh.

    Falls back to a synthetic Phase-4-compatible default archetype when
    no ``material_application`` edge targets the region or any of its
    spatial-containment ancestors.
    """
    chain = _ancestor_chain(state, region_id)
    sub_region_targets = _sub_region_targets(state, region_id)
    apps = _collect_applications(state, chain | set(sub_region_targets.keys()))

    if not apps:
        archetype = default_terrain_archetype(
            elevation_min=elevation_min,
            elevation_max=elevation_max,
        )
        validate_recipe_parameters(archetype.recipe, archetype.parameters)
        layer = ResolvedLayer(
            archetype_id=archetype.node_id,
            recipe=archetype.recipe,
            parameters=dict(archetype.parameters),
        )
        return make_plan(region_id, mesh_name, (layer,))

    layers: list[ResolvedLayer] = []
    for edge, attrs in apps:
        target = edge.endpoints[1]
        sub_region = sub_region_targets.get(target)
        predicate_mask = (
            PredicateMask(predicate=sub_region.predicate) if sub_region is not None else None
        )
        layers.extend(_expand_application(state, edge, attrs, predicate_mask=predicate_mask))
    return make_plan(region_id, mesh_name, tuple(layers))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ancestor_chain(state: ProjectState, region_id: RegionId) -> set[NodeId]:
    """Return the set of ``{region_id, ..., world_root}`` via spatial_containment.

    Walks parent → child edges (``directed=True``, endpoints
    ``(parent, child)``) backwards. Undirected containment edges are
    treated as parent links by inspecting both endpoints; this is
    consistent with the Phase-2 boundary stubs which ship undirected.
    """
    chain: set[NodeId] = {NodeId(str(region_id))}
    parents: dict[NodeId, set[NodeId]] = {}
    for edge in state.edges.get(LAYER_SPATIAL_CONTAINMENT, ()):
        if len(edge.endpoints) != 2:  # noqa: PLR2004 - binary containment only
            continue
        a, b = edge.endpoints[0], edge.endpoints[1]
        # Treat 'a' as the parent of 'b' for directed edges; for
        # undirected edges record both directions so the walk is robust
        # to whichever ordering the writer chose.
        parents.setdefault(b, set()).add(a)
        if not edge.directed:
            parents.setdefault(a, set()).add(b)
    frontier: list[NodeId] = [NodeId(str(region_id))]
    while frontier:
        node = frontier.pop()
        for parent in parents.get(node, ()):
            if parent in chain:
                continue
            chain.add(parent)
            frontier.append(parent)
    chain.add(state.metadata.world_node_id)
    return chain


def _sub_region_targets(
    state: ProjectState,
    region_id: RegionId,
) -> dict[NodeId, SubRegionNode]:
    """Return ``{NodeId(sub_region_id): SubRegionNode}`` for ``region_id``'s children.

    Phase 6-c: sub-region applications target sub-region ids, not the
    parent region id. The resolver therefore extends the
    ``_collect_applications`` target set to cover them and remembers
    the originating sub-region so the predicate can be wrapped into a
    :class:`PredicateMask` on the resulting layers.
    """
    parent = NodeId(str(region_id))
    return {
        NodeId(str(sub.node_id)): sub
        for sub in state.sub_regions.values()
        if NodeId(str(sub.parent_node)) == parent
    }


def _collect_applications(
    state: ProjectState,
    chain: set[NodeId],
) -> list[tuple[Edge, MaterialApplicationAttrs]]:
    """Return material_application edges targeting any node in ``chain``, sorted.

    Sort order is *render order* — the most specific / highest priority
    application ends up last so it composites on top in the adapter.
    Ties break on edge_id for determinism.
    """
    out: list[tuple[Edge, MaterialApplicationAttrs]] = []
    for edge in state.edges.get(LAYER_MATERIAL_APPLICATION, ()):
        if len(edge.endpoints) != 2:  # noqa: PLR2004 - binary applications only
            continue
        target = edge.endpoints[1]
        if target not in chain:
            continue
        attrs = MaterialApplicationAttrs.model_validate(edge.attrs)
        out.append((edge, attrs))
    out.sort(
        key=lambda pair: (
            material_scope_specificity(pair[1].scope),
            pair[1].priority,
            pair[0].edge_id,
        ),
    )
    return out


def _expand_application(
    state: ProjectState,
    application_edge: Edge,
    attrs: MaterialApplicationAttrs,
    *,
    predicate_mask: PredicateMask | None = None,
) -> list[ResolvedLayer]:
    """Expand one application edge into its ordered list of resolved layers.

    Order within an application: composed children render *under* the
    primary archetype, so the primary archetype is appended *last*.

    When ``predicate_mask`` is non-``None`` (the application targets a
    sub-region) every emitted layer carries it on
    :attr:`ResolvedLayer.predicate_mask`. The application's own
    :attr:`MaterialApplicationAttrs.mask` is left on
    :attr:`ResolvedLayer.mask` unchanged: the adapter combines them as
    ``predicate_factor * mask_factor`` so the predicate gates the
    region of effect while ``mask`` modulates within it.
    """
    archetype_id = MaterialArchetypeId(str(application_edge.endpoints[0]))
    primary = state.archetypes.get(archetype_id)
    if primary is None:
        msg = (
            f"material_application edge {application_edge.edge_id!r} references "
            f"unknown archetype {archetype_id!r}"
        )
        raise ResolverError(msg)
    composes_edges = _composes_children(state, archetype_id)
    layers: list[ResolvedLayer] = []
    # Composed children render below; iterate in deterministic edge order.
    for comp_edge, comp_attrs, child_archetype in composes_edges:
        params = _merge_extends_chain(state, child_archetype)
        validate_recipe_parameters(child_archetype.recipe, params)
        layers.append(
            ResolvedLayer(
                archetype_id=child_archetype.node_id,
                recipe=child_archetype.recipe,
                parameters=params,
                mask=comp_attrs.mask,
                weight=comp_attrs.weight,
                predicate_mask=predicate_mask,
                source_application_edge_id=application_edge.edge_id,
                source_composition_edge_id=comp_edge.edge_id,
                instancer=_instancer_for(child_archetype.recipe, params),
            ),
        )
    primary_params = _merge_extends_chain(state, primary)
    primary_params = _deep_merge(primary_params, dict(attrs.parameter_overrides))
    validate_recipe_parameters(primary.recipe, primary_params)
    layers.append(
        ResolvedLayer(
            archetype_id=primary.node_id,
            recipe=primary.recipe,
            parameters=primary_params,
            mask=attrs.mask,
            predicate_mask=predicate_mask,
            source_application_edge_id=application_edge.edge_id,
            instancer=_instancer_for(primary.recipe, primary_params),
        ),
    )
    return layers


def _composes_children(
    state: ProjectState,
    parent_id: MaterialArchetypeId,
) -> list[tuple[Edge, MaterialCompositionAttrs, MaterialArchetypeNode]]:
    """Return ``(edge, attrs, child_archetype)`` triples for COMPOSES edges, sorted."""
    out: list[tuple[Edge, MaterialCompositionAttrs, MaterialArchetypeNode]] = []
    parent_node = NodeId(str(parent_id))
    for edge in state.edges.get(LAYER_MATERIAL_COMPOSITION, ()):
        if len(edge.endpoints) != 2 or edge.endpoints[0] != parent_node:  # noqa: PLR2004
            continue
        attrs = MaterialCompositionAttrs.model_validate(edge.attrs)
        if attrs.mode is not MaterialCompositionMode.COMPOSES:
            continue
        child_id = MaterialArchetypeId(str(edge.endpoints[1]))
        child = state.archetypes.get(child_id)
        if child is None:
            msg = (
                f"material_composition edge {edge.edge_id!r} references "
                f"unknown child archetype {child_id!r}"
            )
            raise ResolverError(msg)
        out.append((edge, attrs, child))
    out.sort(key=lambda triple: triple[0].edge_id)
    return out


def _merge_extends_chain(
    state: ProjectState,
    archetype: MaterialArchetypeNode,
) -> dict[str, JsonValue]:
    """Flatten the EXTENDS chain rooted at ``archetype``; child params win.

    Walks ``archetype`` ↑ via EXTENDS edges (``parent extends child``
    means ``parent`` declares ``child`` as a base it overrides). Cycles
    are impossible by service-layer construction
    (:class:`MaterialCompositionCycleError`), but the walk caps at
    project-archetype-count to remain robust against malformed input.
    """
    seen: set[MaterialArchetypeId] = set()
    cursor: MaterialArchetypeNode | None = archetype
    chain: list[MaterialArchetypeNode] = []
    while cursor is not None:
        if cursor.node_id in seen:
            msg = f"extends chain contains a cycle through {cursor.node_id!r}"
            raise ResolverError(msg)
        seen.add(cursor.node_id)
        chain.append(cursor)
        if len(chain) > len(state.archetypes) + 1:
            msg = "extends chain exceeded archetype count; corrupt project"
            raise ResolverError(msg)
        cursor = _extends_parent(state, cursor.node_id)
    # ``chain[0]`` is the leaf (most specific); apply oldest-first so the
    # leaf's parameters override the ancestors.
    merged: dict[str, JsonValue] = {}
    for node in reversed(chain):
        merged = _deep_merge(merged, dict(node.parameters))
    return merged


def _extends_parent(
    state: ProjectState,
    archetype_id: MaterialArchetypeId,
) -> MaterialArchetypeNode | None:
    """Return the EXTENDS parent of ``archetype_id`` if exactly one exists.

    More than one parent is a configuration error (an archetype can
    only extend a single base in v1 to keep merge order well-defined);
    raises :class:`ResolverError` rather than silently picking one.
    """
    parents: list[MaterialArchetypeNode] = []
    child_node = NodeId(str(archetype_id))
    for edge in state.edges.get(LAYER_MATERIAL_COMPOSITION, ()):
        if len(edge.endpoints) != 2:  # noqa: PLR2004
            continue
        if edge.endpoints[0] != child_node:
            continue
        attrs = MaterialCompositionAttrs.model_validate(edge.attrs)
        if attrs.mode is not MaterialCompositionMode.EXTENDS:
            continue
        parent_id = MaterialArchetypeId(str(edge.endpoints[1]))
        parent = state.archetypes.get(parent_id)
        if parent is None:
            msg = f"extends edge {edge.edge_id!r} references unknown archetype {parent_id!r}"
            raise ResolverError(msg)
        parents.append(parent)
    if not parents:
        return None
    if len(parents) > 1:
        msg = (
            f"archetype {archetype_id!r} extends >1 base "
            f"({[p.node_id for p in parents]!r}); v1 supports a single base"
        )
        raise ResolverError(msg)
    return parents[0]


def _deep_merge(
    base: dict[str, JsonValue],
    overrides: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Return ``base`` overlaid with ``overrides``; nested dicts merge recursively.

    Lists and scalar values from ``overrides`` *replace* the base value
    (no element-wise merge); this matches the principle of least
    surprise for material parameter overrides.
    """
    out: dict[str, JsonValue] = dict(base)
    for key, value in overrides.items():
        existing = out.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            out[key] = _deep_merge(existing, value)
        else:
            out[key] = value
    return out


__all__ = ["ResolverError", "resolve_plan"]
