"""Pydantic v2 models for the on-disk Forge project format.

Mirrors ``AGENT/ARCHITECTURE.md`` §3 and ``AGENT/dev_phases/phase2.md``
Stage B verbatim. Every model is **frozen** and uses
``extra='forbid'`` so the on-disk schema is the contract: agents cannot
smuggle extra fields, and Pydantic raises ``ValidationError`` on stray
keys (which the MCP tool surface in Phase 2 Stage G turns into structured
errors).

JSON serialization style is locked separately by
:func:`forge_mcp.project.schema_export.dump_schema_json` and (in Stage C)
``forge_mcp._io.atomic.dump_json``: sorted keys, two-space indent,
trailing newline. Models themselves are agnostic; they round-trip through
``model_dump(mode='json')`` and ``model_validate``.

Pydantic ``BaseModel`` and ``field_validator`` stubs leak ``Any`` through
their descriptor / classmethod machinery, which trips mypy's
``disallow_any_explicit``. Each model class therefore carries a scoped
``# type: ignore[explicit-any]`` with the same one-line reason
established in Phase 1's :mod:`forge_mcp.descriptor.schema`.
"""

from __future__ import annotations

import math
from datetime import datetime  # noqa: TC003 - runtime needed by Pydantic for validation
from enum import StrEnum
from typing import ClassVar, Final, Literal, NewType
from uuid import UUID  # noqa: TC003 - runtime needed by Pydantic for validation

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from forge_mcp._types import JsonValue  # noqa: TC001 - runtime needed by Pydantic
from forge_mcp.descriptor.schema import StructuredDescriptor  # noqa: TC001 - runtime

# ---------------------------------------------------------------------------
# Identifier NewTypes
# ---------------------------------------------------------------------------
# NewTypes give us mypy strictness (region_id and spec_id can't be
# accidentally swapped) at zero runtime cost. They are all string-shaped
# on disk; format conventions are documented per type.

RegionId = NewType("RegionId", str)
"""Stable region identifier, ``slug-of-name`` plus optional ``-NN`` collision suffix."""

SpecId = NewType("SpecId", str)
"""Content-addressable spec identifier, ``spec_<6-hex>`` per Architecture §3.4."""

BoundaryId = NewType("BoundaryId", str)
"""Boundary record identifier, ``boundary_<region_a>__<region_b>``."""

LockId = NewType("LockId", str)
"""Lock record identifier, ``lock_<uuid4>``."""

HistoryEventId = NewType("HistoryEventId", str)
"""Zero-padded sequential history-event id, e.g. ``"0001"``."""

NodeId = NewType("NodeId", str)
"""Generic hypergraph node identifier (covers regions plus the world root)."""

EdgeId = NewType("EdgeId", str)
"""Generic hypergraph edge identifier."""

MaterialArchetypeId = NewType("MaterialArchetypeId", str)
"""Material-archetype node id (``material_<slug>`` plus optional ``_NN`` collision)."""

MaterialPlanId = NewType("MaterialPlanId", str)
"""Content-addressed composite-material plan id, ``mplan_<10-hex>``."""

SubRegionId = NewType("SubRegionId", str)
"""Sub-region node id, ``subregion_<slug>`` plus optional ``_NN`` collision suffix.

Phase 6-c addition. A sub-region is a predicate-typed child of a
:class:`RegionNode` whose spatial extent is defined by evaluating its
:class:`SubRegionPredicate` against the parent region's heightmap at
realize time — *not* by a stored polygon.
"""


# ---------------------------------------------------------------------------
# Geometric primitives
# ---------------------------------------------------------------------------

_MIN_POLYGON_VERTICES: Final[int] = 3
_AREA_EPSILON: Final[float] = 1e-9


def _signed_area(coords: tuple[tuple[float, float], ...]) -> float:
    """Return the signed area of ``coords`` via the shoelace formula.

    Positive for counter-clockwise orientation, negative for clockwise,
    zero for degenerate (collinear) input.
    """
    total = 0.0
    n = len(coords)
    for i in range(n):
        x1, y1 = coords[i]
        x2, y2 = coords[(i + 1) % n]
        total += (x2 - x1) * (y2 + y1)
    # Shoelace returns 2A; sign is what callers care about, not magnitude.
    return -total / 2.0


class Polygon2D(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """A closed 2D polygon defined by its vertices.

    Validation:

    * at least three vertices, all distinct;
    * non-degenerate (signed area magnitude > :data:`_AREA_EPSILON`);
    * canonicalized counter-clockwise on construction (so equal polygons
      compare equal regardless of input winding).

    Self-intersection detection is delegated to
    :mod:`forge_mcp.geometry.polygon` (Phase 2 Stage E) and is *not*
    enforced at the schema layer, by design: the schema layer rejects
    obvious garbage; the geometry layer enforces the richer invariants
    that need shapely.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    coords: tuple[tuple[float, float], ...]

    @field_validator("coords", mode="before")
    @classmethod
    def _canonicalize(
        cls,
        value: tuple[tuple[float, float], ...] | list[list[float]] | list[tuple[float, float]],
    ) -> tuple[tuple[float, float], ...]:
        # Coerce to nested tuples so we can hash + reverse losslessly.
        coerced = tuple((float(x), float(y)) for x, y in value)
        if len(coerced) < _MIN_POLYGON_VERTICES:
            msg = f"polygon needs >= {_MIN_POLYGON_VERTICES} vertices, got {len(coerced)}"
            raise ValueError(msg)
        if len(set(coerced)) != len(coerced):
            msg = "polygon vertices must be distinct"
            raise ValueError(msg)
        area = _signed_area(coerced)
        if math.fabs(area) <= _AREA_EPSILON:
            msg = "polygon is degenerate (zero area)"
            raise ValueError(msg)
        ccw = coerced if area > 0 else tuple(reversed(coerced))
        # Rotate so the lex-min vertex sits first; combined with CCW
        # winding this gives a canonical representation, so two equal
        # polygons compare equal regardless of input ordering.
        start = min(range(len(ccw)), key=ccw.__getitem__)
        return ccw[start:] + ccw[:start]


class Bounds2D(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Axis-aligned 2D bounding box."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    min: tuple[float, float]
    max: tuple[float, float]

    @model_validator(mode="after")
    def _check_order(self) -> Bounds2D:
        if self.min[0] > self.max[0] or self.min[1] > self.max[1]:
            msg = f"Bounds2D min={self.min} must be <= max={self.max} component-wise"
            raise ValueError(msg)
        return self


class WorldBounds(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """World-level rectangular bounds in meters.

    v1 only supports rectangles; richer shapes are deferred. ``units`` is
    a literal so on-disk projects self-document the unit convention.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["rectangle"] = "rectangle"
    min: tuple[float, float]
    max: tuple[float, float]
    units: Literal["meters"] = "meters"

    @model_validator(mode="after")
    def _check_order(self) -> WorldBounds:
        if self.min[0] >= self.max[0] or self.min[1] >= self.max[1]:
            msg = f"WorldBounds requires positive extent, got min={self.min}, max={self.max}"
            raise ValueError(msg)
        return self


class SpatialBounds(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Per-region spatial bounds: a polygon plus optional elevation range."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["polygon"] = "polygon"
    coords: Polygon2D
    elevation_range: tuple[float, float] | None = None

    @field_validator("elevation_range")
    @classmethod
    def _check_elevation(cls, value: tuple[float, float] | None) -> tuple[float, float] | None:
        if value is not None and value[0] > value[1]:
            msg = f"elevation_range low must be <= high, got {value}"
            raise ValueError(msg)
        return value


# ---------------------------------------------------------------------------
# Hypergraph node + edge records
# ---------------------------------------------------------------------------


class WorldRootNode(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """The synthetic root node every project carries on disk.

    Lives at ``nodes/world.json``. Distinct from :class:`RegionNode` so
    the union ``WorldRootNode | RegionNode`` is unambiguous on load.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    node_id: NodeId
    kind: Literal["world_root"] = "world_root"
    name: str
    created_at: datetime


class RegionTier(StrEnum):
    """Region tier (Architecture §3.2).

    v1 ships only ``UNIQUE`` regions; the broader enum is reserved.
    """

    UNIQUE = "unique"


class RegionNode(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """One region: spatial bounds, optional descriptor, optional spec linkage."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    node_id: RegionId
    kind: Literal["region"] = "region"
    tier: RegionTier = RegionTier.UNIQUE
    scale_level: int = 2
    parent_node: NodeId
    children: tuple[NodeId, ...] = ()
    name: str
    spec_id: SpecId | None = None
    """``None`` until generation runs (Phase 3); persisted as ``null`` on disk."""
    spatial_bounds: SpatialBounds
    tags: tuple[str, ...] = ()
    seed: int
    created_at: datetime
    modified_at: datetime
    structured_descriptor: StructuredDescriptor | None = None
    """Phase-2 addition (F-7.3): persisted on ``create_region`` / ``update_region``."""


class MaterialRecipe(StrEnum):
    """Names of the v1 recipe templates implemented by the Blender adapter.

    Each recipe is a small Python builder in
    ``scripts/blender/adapter.py`` that constructs a Blender shader
    node group from a parameter dict. Adding a new recipe requires (1)
    adding the enum value here, (2) adding a parameter validator to
    :func:`forge_mcp.realize.material.defaults.validate_recipe_parameters`,
    and (3) adding a builder to the adapter's recipe registry.
    """

    PRINCIPLED_HEIGHT_RAMP = "principled_height_ramp"
    """Elevation-driven Principled BSDF with a configurable color ramp.

    Reproduces the Phase-4 monolithic terrain material exactly; ships
    as the default archetype when a region has no material applications.
    """

    TRIPLANAR_ROCK = "triplanar_rock"
    """Procedural triplanar projection driving a Principled BSDF."""

    FLAT_COLOR = "flat_color"
    """Minimal solid-color Principled BSDF; useful as a base layer."""

    PBR_LAYERED = "pbr_layered"
    """Generalised procedural Principled BSDF (Phase 6-e Stage B).

    Combines a configurable base color with optional Voronoi base-color
    variation, Noise-driven roughness variation, and a Bump-node normal
    detail layer. All inputs are procedural — no image textures. Use
    this for any surface (rock, grass, snow, sand, dirt, organics) that
    needs more than the legacy ``flat_color`` / ``triplanar_rock``
    knobs without leaving the procedural family.
    """


class MaterialArchetypeNode(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """One reusable material archetype.

    Archetypes live in the same node table as regions and the world
    root so material edges (in the ``material_application`` and
    ``material_composition`` layers) have node-only endpoints.

    ``recipe`` selects the adapter shader-graph template;
    ``parameters`` is the recipe-specific parameter dict (a Pydantic
    union would compose poorly with the override-merge step the
    resolver performs, so v1 keeps parameters as a dict and validates
    its shape against the recipe via
    :func:`forge_mcp.realize.material.defaults.validate_recipe_parameters`).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    node_id: MaterialArchetypeId
    kind: Literal["material_archetype"] = "material_archetype"
    name: str
    recipe: MaterialRecipe
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    tags: tuple[str, ...] = ()
    notes: str = ""
    created_at: datetime
    modified_at: datetime

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not value.strip():
            msg = "material archetype name must be non-empty"
            raise ValueError(msg)
        return value


# ---------------------------------------------------------------------------
# Material mask + edge-attribute models
# ---------------------------------------------------------------------------


class HeightRampMask(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Height-driven mix mask: full strength above ``high_m``, zero below ``low_m``."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["height_ramp"] = "height_ramp"
    low_m: float
    high_m: float

    @model_validator(mode="after")
    def _check_band(self) -> HeightRampMask:
        if not self.low_m < self.high_m:
            msg = f"height_ramp mask requires low_m < high_m, got [{self.low_m}, {self.high_m}]"
            raise ValueError(msg)
        return self


class SlopeMask(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Slope-driven mix mask: full strength above ``high``, zero below ``low``.

    ``low`` / ``high`` are slope thresholds in the cosine-of-normal
    domain ``[0, 1]`` where ``1`` is flat ground (normal pointing
    straight up) and ``0`` is a vertical cliff. ``softness`` is an
    additive band width for smoothstep blending; ``0`` is a hard step.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["slope"] = "slope"
    low: float = Field(ge=0.0, le=1.0)
    high: float = Field(ge=0.0, le=1.0)
    softness: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_band(self) -> SlopeMask:
        if not self.low < self.high:
            msg = f"slope mask requires low < high, got [{self.low}, {self.high}]"
            raise ValueError(msg)
        return self


class ConstantMask(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Constant mix factor in ``[0, 1]``; ``1.0`` is fully replace, ``0.0`` skip."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["constant"] = "constant"
    value: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Sub-region predicates (Phase 6-c)
# ---------------------------------------------------------------------------
# A sub-region is "what its predicate says it is": the predicate is the
# extent. v1 ships four predicate kinds; the union is discriminated on
# the literal ``kind`` field exactly like :data:`MaskSpec`.

_ASPECT_DEGREES_MAX: Final[float] = 360.0
_SLOPE_DEGREES_MAX: Final[float] = 90.0


class HeightBandPredicate(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Selects pixels whose elevation lies in ``[low_m, high_m)``."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["height_band"] = "height_band"
    low_m: float
    high_m: float

    @model_validator(mode="after")
    def _check_band(self) -> HeightBandPredicate:
        if not self.low_m < self.high_m:
            msg = (
                f"height_band predicate requires low_m < high_m, got [{self.low_m}, {self.high_m}]"
            )
            raise ValueError(msg)
        return self


class SlopePredicate(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Selects pixels whose slope (degrees, ``[0, 90]``) lies in ``[min_deg, max_deg)``.

    Slope is computed by :func:`forge_mcp.analyze.terrain_analysis._slope_and_aspect`
    in degrees (``0`` = flat, ``90`` = vertical), matching the analysis surface so
    predicates are authored in the same units operators see.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["slope"] = "slope"
    min_deg: float = Field(ge=0.0, le=_SLOPE_DEGREES_MAX)
    max_deg: float = Field(ge=0.0, le=_SLOPE_DEGREES_MAX)

    @model_validator(mode="after")
    def _check_band(self) -> SlopePredicate:
        if not self.min_deg < self.max_deg:
            msg = (
                f"slope predicate requires min_deg < max_deg, got [{self.min_deg}, {self.max_deg}]"
            )
            raise ValueError(msg)
        return self


class AspectPredicate(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Selects pixels whose aspect (compass bearing, ``[0, 360)``) lies in the range.

    Both bounds live in ``[0, 360)``. When ``min_deg < max_deg`` the
    selected band is the half-open interval ``[min_deg, max_deg)``;
    when ``min_deg > max_deg`` the band wraps through north
    (``[min_deg, 360) and [0, max_deg)``), letting agents author "north-
    facing" predicates without two-step composition. ``min_deg ==
    max_deg`` is rejected as degenerate (zero-area band).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["aspect"] = "aspect"
    min_deg: float = Field(ge=0.0, lt=_ASPECT_DEGREES_MAX)
    max_deg: float = Field(ge=0.0, lt=_ASPECT_DEGREES_MAX)

    @model_validator(mode="after")
    def _check_band(self) -> AspectPredicate:
        if self.min_deg == self.max_deg:
            msg = (
                f"aspect predicate requires min_deg != max_deg, "
                f"got [{self.min_deg}, {self.max_deg})"
            )
            raise ValueError(msg)
        return self


class DistanceToStreamPredicate(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Selects pixels within ``max_m`` meters of the parent region's stream geometry.

    Evaluated against the persisted
    :class:`forge_mcp.generate.stream.StreamGeometry` for the parent
    region. Predicate evaluates to all-``False`` (and
    :func:`preview_sub_region_coverage` returns ``coverage_fraction == 0.0``)
    when the parent has no stream.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["distance_to_stream"] = "distance_to_stream"
    max_m: float = Field(gt=0.0)


SubRegionPredicate = (
    HeightBandPredicate | SlopePredicate | AspectPredicate | DistanceToStreamPredicate
)
"""Discriminated union over v1 sub-region predicate kinds, keyed on ``kind``."""


class PredicateMask(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """MaskSpec variant whose mix factor IS a sub-region predicate.

    Phase 6-c: when a ``material_application`` edge targets a
    :class:`SubRegionNode`, the resolver wraps the sub-region's
    predicate in :class:`PredicateMask` and threads it as the layer's
    mask. Combination semantics (predicate AND application's own mask)
    are documented in
    :func:`forge_mcp.realize.material.resolver._collect_sub_region_applications`.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["predicate"] = "predicate"
    predicate: SubRegionPredicate


MaskSpec = HeightRampMask | SlopeMask | ConstantMask | PredicateMask
"""Discriminated union over v1 mask kinds, keyed on ``kind``."""


class SubRegionNode(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """A predicate-typed child of a :class:`RegionNode` (Phase 6-c).

    Sub-regions exist to escape polygon-based subdivision: a region is
    rarely materially homogeneous, but its heterogeneity follows
    semantic axes (elevation band, slope, aspect, proximity to water)
    rather than arbitrary boundaries. The sub-region's ``predicate``
    *is* its extent — it is evaluated at realize time against the
    parent region's heightmap (and stream geometry, for
    :class:`DistanceToStreamPredicate`), so re-rolling the parent
    region updates sub-region coverage without any sub-region edits.

    Sub-regions are first-class nodes in the hypergraph: they live on
    disk under ``<project>/sub_regions/<id>.json``, the parent region
    appends them to ``RegionNode.children``, and an automatic
    ``spatial_containment`` edge ``(region → sub_region)`` is
    maintained by the project service so the realizer can walk
    children deterministically.

    Lifetime invariants (enforced in
    :mod:`forge_mcp.project.service`):

    * ``parent_node`` must be an existing :class:`RegionNode`. v1
      forbids sub-region nesting (a sub-region cannot parent another
      sub-region) — predicate composition is the intended escape hatch.
    * Deletion is rejected when any ``material_application`` edge
      targets the sub-region; agents must unapply first.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    node_id: SubRegionId
    kind: Literal["sub_region"] = "sub_region"
    parent_node: RegionId
    name: str
    predicate: SubRegionPredicate
    tags: tuple[str, ...] = ()
    notes: str = ""
    created_at: datetime
    modified_at: datetime

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not value.strip():
            msg = "sub-region name must be non-empty"
            raise ValueError(msg)
        return value


class MaterialScope(StrEnum):
    """Scope of a material application; resolver precedence is *most specific wins*.

    Only ``WORLD`` and ``REGION`` resolve to real targets in v1 (no
    sub-region or asset node types ship yet). The other values are
    accepted in the schema so downstream additions don't break
    persisted projects.
    """

    WORLD = "world"
    REGION_GROUP = "region_group"
    REGION = "region"
    SUB_REGION = "sub_region"
    ASSET = "asset"


_SCOPE_PRECEDENCE: Final[dict[MaterialScope, int]] = {
    MaterialScope.WORLD: 0,
    MaterialScope.REGION_GROUP: 1,
    MaterialScope.REGION: 2,
    MaterialScope.SUB_REGION: 3,
    MaterialScope.ASSET: 4,
}


def material_scope_specificity(scope: MaterialScope) -> int:
    """Return the resolver-precedence rank of ``scope`` (higher = more specific)."""
    return _SCOPE_PRECEDENCE[scope]


class MaterialApplicationAttrs(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Strict shape of ``Edge.attrs`` for the ``material_application`` layer.

    Stored on disk as a plain JSON object inside ``Edge.attrs``;
    materialized by :func:`material_application_attrs` on read and
    serialized via ``model_dump`` on write. Keeping this as an
    *attrs validator* rather than a new Edge subclass preserves the
    layer-string-keyed Edge surface every other layer already uses.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    scope: MaterialScope
    priority: int = 0
    parameter_overrides: dict[str, JsonValue] = Field(default_factory=dict)
    mask: MaskSpec | None = None


class MaterialCompositionMode(StrEnum):
    """Composition modes for the ``material_composition`` layer."""

    EXTENDS = "extends"
    """``parent extends child`` flattens ``child.parameters`` under ``parent.parameters``."""

    COMPOSES = "composes"
    """``parent composes child`` stacks ``child`` as an extra plan layer behind ``parent``."""


class MaterialCompositionAttrs(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Strict shape of ``Edge.attrs`` for the ``material_composition`` layer.

    The edge endpoints are always two material archetype ids:
    ``(parent_id, child_id)``. ``mode`` selects flatten-vs-stack
    semantics; ``mask`` and ``weight`` only apply to ``COMPOSES``
    edges and are ignored for ``EXTENDS`` (resolver enforces the
    constraint).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    mode: MaterialCompositionMode
    mask: MaskSpec | None = None
    weight: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_mode_constraints(self) -> MaterialCompositionAttrs:
        if self.mode is MaterialCompositionMode.EXTENDS and (
            self.mask is not None or self.weight is not None
        ):
            msg = "extends composition edges must not carry mask or weight"
            raise ValueError(msg)
        return self


def material_application_attrs(edge: Edge) -> MaterialApplicationAttrs:
    """Validate ``edge.attrs`` against :class:`MaterialApplicationAttrs`.

    Raises ``pydantic.ValidationError`` on shape mismatch. Use this in
    every reader that crosses the schema boundary so the loose
    ``Edge.attrs`` map stays a single point of truth on disk while
    consumers see strongly-typed records.
    """
    return MaterialApplicationAttrs.model_validate(edge.attrs)


def material_composition_attrs(edge: Edge) -> MaterialCompositionAttrs:
    """Validate ``edge.attrs`` against :class:`MaterialCompositionAttrs`."""
    return MaterialCompositionAttrs.model_validate(edge.attrs)


# ---------------------------------------------------------------------------
# Material layer name constants (Phase 6-bis)
# ---------------------------------------------------------------------------


LAYER_SPATIAL_CONTAINMENT: Final[str] = "spatial_containment"
LAYER_SPATIAL_ADJACENCY: Final[str] = "spatial_adjacency"
LAYER_HYDROLOGY: Final[str] = "hydrology"
LAYER_MATERIAL_APPLICATION: Final[str] = "material_application"
LAYER_MATERIAL_COMPOSITION: Final[str] = "material_composition"

DEFAULT_REGISTERED_LAYERS: Final[tuple[str, ...]] = (
    LAYER_SPATIAL_CONTAINMENT,
    LAYER_SPATIAL_ADJACENCY,
    LAYER_HYDROLOGY,
    LAYER_MATERIAL_APPLICATION,
    LAYER_MATERIAL_COMPOSITION,
)
"""Layer set every fresh project ships with; mirrors :data:`ProjectMetadata.registered_layers`."""


class Edge(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """A hyperedge in one of the project's typed layers."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    edge_id: EdgeId
    layer: str
    endpoints: tuple[NodeId, ...]
    directed: bool = False
    attrs: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime
    modified_at: datetime

    @field_validator("endpoints")
    @classmethod
    def _check_endpoints(cls, value: tuple[NodeId, ...]) -> tuple[NodeId, ...]:
        if len(value) < 2:  # noqa: PLR2004 - hyperedge minimum is binary
            msg = f"edge needs >= 2 endpoints, got {len(value)}"
            raise ValueError(msg)
        return value


class EdgeLayerFile(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """On-disk shape of ``edges/<layer>.json`` — wraps the edge list for diff-stability."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    layer: str
    edges: tuple[Edge, ...] = ()


# ---------------------------------------------------------------------------
# Spec, boundary, lock, history
# ---------------------------------------------------------------------------


# ----- Spec body sub-models (Phase 3 Stage A: Architecture §3.4 typed shape) ---

AnchorPoint = tuple[float, float]
"""``(x, y)`` world-frame coordinates in meters; entry/exit anchors for streams."""


class TerrainGeneratorParams(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Tunable parameters for the ``ridged_multifractal_v1`` terrain generator.

    Field semantics live in :mod:`forge_mcp.generate.noise` (Phase 3
    Stage D); this model is the persistence shape only.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    octaves: int = Field(ge=1, le=12)
    lacunarity: float = Field(gt=1.0, le=4.0)
    persistence: float = Field(gt=0.0, lt=1.0)
    warp: float = Field(ge=0.0, le=2.0)
    scale_meters: float = Field(gt=0.0)
    ridged: bool = True
    """Whether to apply the ``1 - |perlin|`` ridge operator per octave.

    True for archetypes with crisp ridgelines (alpine, canyon, mesa,
    coastal cliffs, volcanic). False for soft archetypes (rolling
    hills, plains, marsh, boreal lowland, dunes) where the ridge
    operator would otherwise plant sharp creases everywhere.
    """
    smooth_sigma_pixels: float = Field(default=0.0, ge=0.0, le=8.0)
    """Optional Gaussian post-smooth applied to the noise field, in
    pixels (sigma). Zero disables. Used to launder away sub-mesh
    high-frequency content that would otherwise alias as per-vertex
    spikes after subsampling to the realizer mesh.
    """


class HydraulicErosionPass(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Grid-based hydraulic erosion post-pass."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["hydraulic_erosion"] = "hydraulic_erosion"
    iterations: int = Field(ge=1, le=512)
    rain: float = Field(gt=0.0, le=1.0)
    evaporation: float = Field(ge=0.0, lt=1.0)


class ThermalErosionPass(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Talus-angle thermal relaxation post-pass."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["thermal_erosion"] = "thermal_erosion"
    iterations: int = Field(ge=1, le=512)
    talus_angle_degrees: float = Field(gt=0.0, lt=90.0)


PostPass = HydraulicErosionPass | ThermalErosionPass
"""Discriminated union of post-pass kinds, keyed on ``kind``."""


class StreamFeatureInjector(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Stream-feature injector spec.

    ``anchor_in`` / ``anchor_out`` stay ``None`` until Phase 6 wires
    boundary contracts; the Phase 3 generator picks deterministic edge
    points when both are unset.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["stream"] = "stream"
    anchor_in: AnchorPoint | None = None
    anchor_out: AnchorPoint | None = None
    width_meters: float = Field(gt=0.0)
    carving_depth: float = Field(gt=0.0)


FeatureInjector = StreamFeatureInjector
"""Single-arm union today; widens when more feature kinds arrive."""


MacroShape = Literal[
    "none",
    "valley_trough",
    "mesa_terraces",
    "canyon_chasm",
    "lowland_lowpass",
    "dunes_ridges",
    "volcanic_cone",
    "coastal_cliff",
]
"""Discriminator for the optional per-archetype macro-shape pre-pass.

Applied in :mod:`forge_mcp.generate.macro_shape` to the unit-range
noise field *before* elevation-band remap, so each archetype carries
its characteristic large-scale silhouette (a U-trough for alpine
valleys, flat-topped terraces for mesas, a deep narrow chasm for
canyons, etc.) on top of the shared noise generator. ``"none"`` is the
identity and matches the pre-Phase-3-revision behaviour.
"""


class TerrainAxisSpec(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Per-axis spec for the terrain axis (the only v1 axis)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    generator: Literal["ridged_multifractal_v1"] = "ridged_multifractal_v1"
    params: TerrainGeneratorParams
    post_passes: tuple[PostPass, ...] = ()
    feature_injectors: tuple[FeatureInjector, ...] = ()
    elevation_band: tuple[float, float]
    resolution_meters_per_pixel: float = Field(gt=0.0)
    macro_shape: MacroShape = "none"
    """Per-archetype large-scale silhouette pre-pass; see :data:`MacroShape`."""
    macro_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    """Blend weight in ``[0, 1]`` for the macro-shape pre-pass.

    ``0.0`` is the identity (no contribution) regardless of
    ``macro_shape``; ``1.0`` is the archetype's full silhouette.
    """

    @field_validator("elevation_band")
    @classmethod
    def _check_elevation_band(cls, value: tuple[float, float]) -> tuple[float, float]:
        low, high = value
        if not low < high:
            msg = f"elevation_band low must be < high, got [{low}, {high}]"
            raise ValueError(msg)
        return value


class BoundaryRequirement(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Requirement a spec places on one of the region's boundaries.

    Phase 3 placeholder: ``elevation_continuity`` is the only kind, with
    no parameters yet. Phase 6 fills the contract negotiation in.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    boundary_id: BoundaryId
    kind: Literal["elevation_continuity"] = "elevation_continuity"


class SpecSummary(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Compact numerical summary populated by :mod:`forge_mcp.analyze`.

    Empty (all-zero) until ``forge.generate_region`` runs analysis;
    refreshed in place on every regeneration.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    mean_elevation: float = 0.0
    std_elevation: float = 0.0
    min_elevation: float = 0.0
    max_elevation: float = 0.0
    slope_p95_degrees: float = 0.0
    stream_length_meters: float | None = None


class GenerationMetadata(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Provenance for one materialized spec.

    Pinned versions let us refuse to load on mismatch (Architecture §15)
    and let CI catch silent generator drift.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    compiler_version: str
    generators_used: tuple[str, ...]
    bpy_hypergraph_version: str
    blender_version: str
    parent_spec_hash: str | None = None
    conflicts_resolved: tuple[str, ...] = ()


class SpecBody(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Typed spec body matching Architecture §3.4.

    Lifted out of :class:`SpecRecord` so the discriminated unions inside
    ``axes['terrain'].post_passes`` and ``feature_injectors`` are
    canonicalized in one place and round-trip cleanly through
    ``model_dump(mode='json')``.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    axes: dict[Literal["terrain"], TerrainAxisSpec]
    boundary_requirements: tuple[BoundaryRequirement, ...] = ()
    summary: SpecSummary = Field(default_factory=SpecSummary)
    generation_metadata: GenerationMetadata


class SpecRecord(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Persistence shape for ``specs/<spec_id>.json``.

    Phase 3 promotes ``body`` from an opaque mapping to the typed
    :class:`SpecBody` substructure (Architecture §3.4). ``spec_id`` is
    content-addressed off the canonical-JSON BLAKE2b of ``body`` —
    identical descriptor + seed + generator versions across regions
    therefore yield identical spec ids, by design.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    spec_id: SpecId
    descriptor: StructuredDescriptor
    body: SpecBody
    created_at: datetime


class ElevationContinuityContract(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Per-boundary elevation-continuity contract (Phase 6 Stage B).

    Both adjacent regions' generators must produce edge profiles that
    match ``samples`` within ``tolerance_m`` (per-sample, not RMS). The
    sampled height profile is parameterized along the shared edge from
    ``region_a`` to ``region_b`` (the canonical lex-sorted ordering on
    :class:`BoundaryRecord`), uniformly spaced at ``sample_spacing_m``.

    The contract is computed symmetrically by
    :func:`forge_mcp.boundary.contract.negotiate_boundary_contract`
    from both regions' :class:`SpecBody.axes['terrain'].elevation_band`.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["elevation_continuity"] = "elevation_continuity"
    low_m: float
    """Lower bound of the overlap interval used to seed sampling, in meters."""
    high_m: float
    """Upper bound of the overlap interval used to seed sampling, in meters."""
    samples: tuple[float, ...]
    """Target heights along the shared edge, ``low_m <= s <= high_m``."""
    sample_spacing_m: float = Field(gt=0.0)
    """Distance between adjacent samples in meters."""
    tolerance_m: float = Field(gt=0.0)
    """Per-sample max permitted deviation between contract and realized profile."""

    @model_validator(mode="after")
    def _check_band(self) -> ElevationContinuityContract:
        if not self.low_m < self.high_m:
            msg = f"contract band low must be < high, got [{self.low_m}, {self.high_m}]"
            raise ValueError(msg)
        if not self.samples:
            msg = "elevation contract must contain at least one sample"
            raise ValueError(msg)
        for index, value in enumerate(self.samples):
            if not self.low_m - self.tolerance_m <= value <= self.high_m + self.tolerance_m:
                msg = (
                    f"sample[{index}]={value} out of contract band "
                    f"[{self.low_m}, {self.high_m}] (tolerance {self.tolerance_m})"
                )
                raise ValueError(msg)
        return self


class StreamCrossingContract(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Per-boundary stream-crossing contract (Phase 6 Stage B).

    Present only when both adjacent regions have a stream injector
    whose planned path crosses the shared edge within
    ``stream.width_meters`` of the same point. The contract pins the
    crossing point, the average channel width and depth, and the flow
    direction (a unit vector pointing from ``region_a`` into
    ``region_b``).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["stream_crossing"] = "stream_crossing"
    crossing_point: tuple[float, float]
    """``(x, y)`` world-frame coordinates of the channel midpoint."""
    width_m: float = Field(gt=0.0)
    """Channel width at the crossing, in meters."""
    depth_m: float = Field(gt=0.0)
    """Carving depth below the contracted edge profile, in meters."""
    flow_direction: tuple[float, float]
    """Unit vector from ``region_a`` into ``region_b`` (validated)."""

    @field_validator("flow_direction")
    @classmethod
    def _check_unit(cls, value: tuple[float, float]) -> tuple[float, float]:
        magnitude = math.hypot(value[0], value[1])
        if not math.isclose(magnitude, 1.0, abs_tol=1e-6):
            msg = f"flow_direction must be a unit vector, got magnitude {magnitude}"
            raise ValueError(msg)
        return value


BoundaryContract = ElevationContinuityContract | StreamCrossingContract
"""Discriminated union over Phase-6 boundary-contract kinds, keyed on ``kind``."""


class BoundaryRecord(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """An adjacency boundary record between two regions.

    Phase 2 emits these on region create/update with ``contracts=()``;
    Phase 6 fills ``contracts`` via
    :func:`forge_mcp.boundary.contract.negotiate_boundary_contract`.
    A boundary may carry zero, one, or two contracts (one elevation,
    optionally one stream crossing). ``contracts`` is empty during the
    negotiation window between adjacency detection and first
    generation.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    boundary_id: BoundaryId
    kind: Literal["adjacency"] = "adjacency"
    region_a: RegionId
    region_b: RegionId
    shared_edge: tuple[tuple[float, float], tuple[float, float]]
    length_meters: float
    contracts: tuple[BoundaryContract, ...] = ()
    created_at: datetime
    modified_at: datetime

    @model_validator(mode="after")
    def _check_pair(self) -> BoundaryRecord:
        if self.region_a == self.region_b:
            msg = "boundary endpoints must differ"
            raise ValueError(msg)
        if self.region_a > self.region_b:
            msg = (
                f"boundary endpoints must be lex-sorted: region_a={self.region_a!r} "
                f"region_b={self.region_b!r}"
            )
            raise ValueError(msg)
        if self.length_meters <= 0:
            msg = f"length_meters must be positive, got {self.length_meters}"
            raise ValueError(msg)
        return self


class LockKind(StrEnum):
    """Lock kinds (Architecture §10.2)."""

    PROPERTY = "property"
    FEATURE = "feature"
    REGION = "region"


class LockRecord(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Persisted lock entry.

    Phase 2 only persists + lists locks; Stage F's :class:`LockStore`
    hands these to Phase 7 which adds enforcement. The ``payload`` shape
    depends on ``kind`` and is intentionally free-form here; the
    Phase-7 lock applier is the schema authority.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    lock_id: LockId
    region_id: RegionId
    kind: LockKind
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime
    modified_at: datetime


class LockStoreFile(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """On-disk shape of ``locks/locks.json``."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    locks: tuple[LockRecord, ...] = ()


class HistoryActor(StrEnum):
    """Who performed a recorded history event."""

    AGENT = "agent"
    USER = "user"
    SYSTEM = "system"


class HistoryEventKind(StrEnum):
    """Phase-2 history event kinds.

    Later phases extend this enum (generation, realization, lock
    application, etc.). New kinds are additive and require a schema
    refresh via ``forge-schema-export --write``.
    """

    CREATE_PROJECT = "create_project"
    OPEN_PROJECT = "open_project"
    SAVE_PROJECT = "save_project"
    CLOSE_PROJECT = "close_project"
    CREATE_REGION = "create_region"
    UPDATE_REGION = "update_region"
    DELETE_REGION = "delete_region"
    GENERATE_REGION = "generate_region"
    REROLL_SEED = "reroll_seed"
    AUDIT_RECORDED = "audit_recorded"
    MATERIAL_ARCHETYPE_CREATED = "material_archetype_created"
    MATERIAL_ARCHETYPE_UPDATED = "material_archetype_updated"
    MATERIAL_ARCHETYPE_DELETED = "material_archetype_deleted"
    MATERIAL_APPLIED = "material_applied"
    MATERIAL_UNAPPLIED = "material_unapplied"
    MATERIAL_COMPOSED = "material_composed"
    MATERIAL_UNCOMPOSED = "material_uncomposed"
    SUB_REGION_CREATED = "sub_region_created"
    SUB_REGION_UPDATED = "sub_region_updated"
    SUB_REGION_DELETED = "sub_region_deleted"


class HistoryEvent(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """One append-only history-log entry.

    Files are named ``history/{event_id}_{kind}.json``; sequence numbers
    are monotonic per project and gaps are forbidden (enforced in Stage
    F).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    event_id: HistoryEventId
    kind: HistoryEventKind
    at: datetime
    actor: HistoryActor
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("event_id")
    @classmethod
    def _check_event_id(cls, value: str) -> HistoryEventId:
        if not (value.isdigit() and len(value) >= _HISTORY_EVENT_ID_MIN_DIGITS):
            msg = (
                f"event_id must be a zero-padded decimal "
                f"with >= {_HISTORY_EVENT_ID_MIN_DIGITS} digits, got {value!r}"
            )
            raise ValueError(msg)
        return HistoryEventId(value)


_HISTORY_EVENT_ID_MIN_DIGITS: Final[int] = 4


class AuditRecord(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Placeholder audit record so the ``audits/`` folder has a known shape.

    Populated by Phase 5 (audit subagent). Phase 2 only commits the model
    + JSON-Schema export so downstream phases inherit a stable surface.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    audit_id: str
    region_id: RegionId
    at: datetime
    findings: tuple[str, ...] = ()
    payload: dict[str, JsonValue] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Top-level project metadata
# ---------------------------------------------------------------------------


class ProjectMetadata(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """``project.json`` shape (Architecture §3.1)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    project_id: UUID
    name: str
    forge_version: str
    blender_version: str
    bpy_hypergraph_version: str
    descriptor_schema_version: str
    created_at: datetime
    modified_at: datetime
    world_node_id: NodeId
    registered_layers: tuple[str, ...] = DEFAULT_REGISTERED_LAYERS
    world_bounds: WorldBounds


__all__ = [
    "DEFAULT_REGISTERED_LAYERS",
    "LAYER_HYDROLOGY",
    "LAYER_MATERIAL_APPLICATION",
    "LAYER_MATERIAL_COMPOSITION",
    "LAYER_SPATIAL_ADJACENCY",
    "LAYER_SPATIAL_CONTAINMENT",
    "AnchorPoint",
    "AuditRecord",
    "BoundaryContract",
    "BoundaryId",
    "BoundaryRecord",
    "BoundaryRequirement",
    "Bounds2D",
    "ConstantMask",
    "Edge",
    "EdgeId",
    "EdgeLayerFile",
    "ElevationContinuityContract",
    "FeatureInjector",
    "GenerationMetadata",
    "HeightRampMask",
    "HistoryActor",
    "HistoryEvent",
    "HistoryEventId",
    "HistoryEventKind",
    "HydraulicErosionPass",
    "LockId",
    "LockKind",
    "LockRecord",
    "LockStoreFile",
    "MacroShape",
    "MaskSpec",
    "MaterialApplicationAttrs",
    "MaterialArchetypeId",
    "MaterialArchetypeNode",
    "MaterialCompositionAttrs",
    "MaterialCompositionMode",
    "MaterialPlanId",
    "MaterialRecipe",
    "MaterialScope",
    "NodeId",
    "Polygon2D",
    "PostPass",
    "ProjectMetadata",
    "RegionId",
    "RegionNode",
    "RegionTier",
    "SlopeMask",
    "SpatialBounds",
    "SpecBody",
    "SpecId",
    "SpecRecord",
    "SpecSummary",
    "StreamCrossingContract",
    "StreamFeatureInjector",
    "TerrainAxisSpec",
    "TerrainGeneratorParams",
    "ThermalErosionPass",
    "WorldBounds",
    "WorldRootNode",
    "material_application_attrs",
    "material_composition_attrs",
    "material_scope_specificity",
]
