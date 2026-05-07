"""Composite material plan IR + content-addressed plan id."""

from __future__ import annotations

from hashlib import blake2b
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from forge_mcp._io.atomic import dump_json
from forge_mcp._types import JsonValue  # noqa: TC001 - runtime needed by Pydantic
from forge_mcp.project.schemas import (
    EdgeId,
    MaskSpec,
    MaterialArchetypeId,
    MaterialPlanId,
    MaterialRecipe,
    RegionId,
)


class ResolvedLayer(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """One flattened layer in a :class:`CompositeMaterialPlan`.

    ``parameters`` is the merged dict (extends-chain + per-application
    overrides). ``mask`` is the mix mask used by the adapter to decide
    where this layer's contribution wins; ``None`` means full-strength
    coverage (only valid for the bottom layer). ``weight`` is an
    optional scalar mix factor applied on top of ``mask`` (used by
    composes edges).

    ``source_application_edge_id`` and ``source_composition_edge_id``
    let the adapter and the audit subagent attribute layers back to
    the hypergraph edge(s) that produced them; ``None`` marks the
    synthetic default layer.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    archetype_id: MaterialArchetypeId
    recipe: MaterialRecipe
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    mask: MaskSpec | None = None
    weight: float | None = None
    source_application_edge_id: EdgeId | None = None
    source_composition_edge_id: EdgeId | None = None


class CompositeMaterialPlan(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """A deterministic, content-addressed render plan for one mesh's material.

    ``plan_id`` is a 10-byte blake2b digest of the canonical-JSON
    encoding of ``{region_id, mesh_name, layers}`` (matching the
    ``spec_<digest>`` pattern in
    :mod:`forge_mcp.descriptor.map_to_spec`). Two regions whose
    resolved layers compare equal under ``model_dump(mode="json")`` get
    the same ``plan_id``, which is what lets the Blender adapter reuse
    a single ``forge.material.<plan_id>`` material across regions.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    plan_id: MaterialPlanId
    region_id: RegionId
    mesh_name: str
    layers: tuple[ResolvedLayer, ...]


def _layer_hash_body(
    region_id: RegionId,
    mesh_name: str,
    layers: tuple[ResolvedLayer, ...],
) -> dict[str, JsonValue]:
    """Return the canonical body that ``compute_plan_id`` hashes."""
    return {
        "region_id": str(region_id),
        "mesh_name": mesh_name,
        "layers": [layer.model_dump(mode="json") for layer in layers],
    }


def compute_plan_id(
    region_id: RegionId,
    mesh_name: str,
    layers: tuple[ResolvedLayer, ...],
) -> MaterialPlanId:
    """Return the content-addressed ``mplan_<10-hex>`` id for the given layers.

    The hash domain matches :func:`forge_mcp.descriptor.map_to_spec._content_address`
    (``blake2b`` over the canonical JSON body) so identical resolver
    inputs always produce identical Blender material names.
    """
    body = _layer_hash_body(region_id, mesh_name, layers)
    digest = blake2b(dump_json(body).encode("utf-8"), digest_size=10).hexdigest()
    return MaterialPlanId(f"mplan_{digest}")


def make_plan(
    region_id: RegionId,
    mesh_name: str,
    layers: tuple[ResolvedLayer, ...],
) -> CompositeMaterialPlan:
    """Build a :class:`CompositeMaterialPlan` with a freshly computed id."""
    if not layers:
        msg = "composite material plan requires >= 1 layer"
        raise ValueError(msg)
    return CompositeMaterialPlan(
        plan_id=compute_plan_id(region_id, mesh_name, layers),
        region_id=region_id,
        mesh_name=mesh_name,
        layers=layers,
    )


__all__ = [
    "CompositeMaterialPlan",
    "ResolvedLayer",
    "compute_plan_id",
    "make_plan",
]
