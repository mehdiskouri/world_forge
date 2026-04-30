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


class SpecRecord(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Persistence shape for ``specs/<spec_id>.json``.

    Phase 2 keeps the body opaque (``Mapping[str, JsonValue]``); Phase 3
    introduces the typed ``axes`` / ``generation_metadata`` substructure.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    spec_id: SpecId
    descriptor: StructuredDescriptor
    body: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime


class BoundaryStub(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """An adjacency boundary record without its Phase-6 contract.

    Stage E (Phase 2) emits these on region create/update; Stage E in
    Phase 6 fills the ``contract`` field. ``contract`` is currently
    typed as ``None`` to make the gap loud at the schema layer.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    boundary_id: BoundaryId
    kind: Literal["adjacency"] = "adjacency"
    region_a: RegionId
    region_b: RegionId
    shared_edge: tuple[tuple[float, float], tuple[float, float]]
    length_meters: float
    contract: None = None
    created_at: datetime
    modified_at: datetime

    @model_validator(mode="after")
    def _check_pair(self) -> BoundaryStub:
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
    registered_layers: tuple[str, ...] = (
        "spatial_containment",
        "spatial_adjacency",
        "hydrology",
    )
    world_bounds: WorldBounds


__all__ = [
    "AuditRecord",
    "BoundaryId",
    "BoundaryStub",
    "Bounds2D",
    "Edge",
    "EdgeId",
    "EdgeLayerFile",
    "HistoryActor",
    "HistoryEvent",
    "HistoryEventId",
    "HistoryEventKind",
    "LockId",
    "LockKind",
    "LockRecord",
    "LockStoreFile",
    "NodeId",
    "Polygon2D",
    "ProjectMetadata",
    "RegionId",
    "RegionNode",
    "RegionTier",
    "SpatialBounds",
    "SpecId",
    "SpecRecord",
    "WorldBounds",
    "WorldRootNode",
]
