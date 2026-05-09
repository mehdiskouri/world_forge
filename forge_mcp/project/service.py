"""``ProjectService`` and the typed folder layout it owns.

The service is the single entry point for creating, opening, saving and
closing a Forge project. It owns:

* ``ProjectPaths`` — every on-disk path is named here so the rest of the
  code stops sprinkling ``Path(...) / "regions"`` literals;
* ``ProjectState`` — the in-memory cache (regions, edges, boundaries,
  locks, history-event count); rebuilt on ``open_project``, mutated by
  later phases through service methods;
* the bootstrap that materializes the documented folder tree
  (Architecture §3) and seeds the world-root node + per-layer edge
  files;
* the version / descriptor-schema gate that refuses to open projects
  the running Forge can't fully understand.

Phase-2 scope: this module does NOT do region CRUD, adjacency
detection, lock application, or full history replay. Those land in
later stages of Phase 2 (region tools, polygon validation, lock store)
and Phase 7 (undo). Stage C is just the persistence skeleton.
"""

from __future__ import annotations

import io
import json
import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import blake2b
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Final
from uuid import uuid4

import numpy as np
from pydantic import ValidationError

from forge_mcp._io.atomic import atomic_write_bytes, atomic_write_text, write_json
from forge_mcp._types import JsonValue  # noqa: TC001 - runtime needed for Mapping[str, JsonValue]
from forge_mcp.descriptor.schema import SCHEMA_VERSION as DESCRIPTOR_SCHEMA_VERSION
from forge_mcp.descriptor.schema import StructuredDescriptor
from forge_mcp.generate.heightmap import load_npy as _load_heightmap_npy
from forge_mcp.project.history import HistoryLog
from forge_mcp.project.locks import (
    DuplicateLockError,
    LockNotFoundError,
    LockStore,
    LockStoreError,
)
from forge_mcp.project.schemas import (
    DEFAULT_REGISTERED_LAYERS,
    LAYER_MATERIAL_APPLICATION,
    LAYER_MATERIAL_COMPOSITION,
    LAYER_SPATIAL_CONTAINMENT,
    BoundaryId,
    BoundaryRecord,
    Edge,
    EdgeId,
    EdgeLayerFile,
    EnvironmentNode,
    EnvironmentNodeId,
    EnvironmentParameters,
    EnvironmentRecipe,
    FeatureLockPayload,
    HistoryActor,
    HistoryEvent,
    HistoryEventId,
    HistoryEventKind,
    LockId,
    LockKind,
    LockRecord,
    LockStoreFile,
    MaterialApplicationAttrs,
    MaterialArchetypeId,
    MaterialArchetypeNode,
    MaterialCompositionAttrs,
    MaterialRecipe,
    NodeId,
    Polygon2D,
    ProjectMetadata,
    PropertyLockPayload,
    RegionId,
    RegionLockPayload,
    RegionNode,
    SpatialBounds,
    SpecId,
    SpecRecord,
    SubRegionId,
    SubRegionNode,
    SubRegionPredicate,
    WorldBounds,
    WorldRootNode,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


def _forge_version() -> str:
    """Return the installed ``forge`` package version, or a local sentinel."""
    try:
        return version("forge")
    except PackageNotFoundError:
        return "0.0.0+local"


# Phase-2 placeholders — Phase 4 wires the real Blender introspection.
# Pinning explicit constants here (rather than ``"unknown"``) keeps the
# on-disk shape honest: every project records the toolchain it was
# created against.
_BLENDER_VERSION_PLACEHOLDER: Final[str] = "5.0.0"
_BPY_HYPERGRAPH_VERSION_PLACEHOLDER: Final[str] = "0.0.0"
_WORLD_ROOT_NODE_ID: Final[NodeId] = NodeId("world_root")
_WORLD_ROOT_NAME: Final[str] = "World"

# Edge layers seeded into every fresh project. Mirrors
# ``ProjectMetadata.registered_layers`` but kept as a separate constant
# so a future "extra layers" knob can diverge without breaking either
# call site.
_DEFAULT_LAYERS: Final[tuple[str, ...]] = DEFAULT_REGISTERED_LAYERS

_GITIGNORE_BODY: Final[str] = "realizations/\n"


# ---------------------------------------------------------------------------
# Folder layout
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectPaths:
    """Every on-disk path that belongs to one Forge project.

    Constructed from the project root directory; all properties are
    derived. Centralizing the layout here makes the folder tree
    reviewable in one place and stops the rest of the codebase from
    growing string literals like ``"regions"`` or ``"locks/locks.json"``.
    """

    root: Path

    # --- Top-level files ---------------------------------------------------
    @property
    def metadata_path(self) -> Path:
        """``project.json`` — the :class:`ProjectMetadata` file."""
        return self.root / "project.json"

    @property
    def gitignore_path(self) -> Path:
        """``.gitignore`` — pre-seeded so realizations stay out of git."""
        return self.root / ".gitignore"

    # --- Directories -------------------------------------------------------
    @property
    def nodes_dir(self) -> Path:
        """Synthetic non-region nodes (currently just ``world.json``)."""
        return self.root / "nodes"

    @property
    def regions_dir(self) -> Path:
        """One ``<region_id>.json`` file per region."""
        return self.root / "regions"

    @property
    def edges_dir(self) -> Path:
        """One ``<layer>.json`` file per registered hypergraph layer."""
        return self.root / "edges"

    @property
    def specs_dir(self) -> Path:
        """One ``<spec_id>.json`` per generated spec (Phase 3 fills these)."""
        return self.root / "specs"

    @property
    def boundaries_dir(self) -> Path:
        """One ``<boundary_id>.json`` per adjacency boundary."""
        return self.root / "boundaries"

    @property
    def locks_dir(self) -> Path:
        """Holds the single ``locks.json`` (kept as a directory for symmetry)."""
        return self.root / "locks"

    @property
    def locks_path(self) -> Path:
        """``locks/locks.json`` — the :class:`LockStoreFile`."""
        return self.locks_dir / "locks.json"

    @property
    def feature_locks_dir(self) -> Path:
        """``locks/feature`` — one ``<lock_id>.npy`` patch per feature lock (Phase 7)."""
        return self.locks_dir / "feature"

    def feature_lock_patch_path(self, lock_id: LockId) -> Path:
        """Path of the captured heightmap patch for a feature lock (Phase 7)."""
        return self.feature_locks_dir / f"{lock_id}.npy"

    @property
    def history_dir(self) -> Path:
        """One ``<event_id>_<kind>.json`` per appended history event."""
        return self.root / "history"

    @property
    def realizations_dir(self) -> Path:
        """Phase-4 Blender realizations (gitignored by default)."""
        return self.root / "realizations"

    @property
    def audits_dir(self) -> Path:
        """Phase-5 audit subagent outputs."""
        return self.root / "audits"

    @property
    def world_node_path(self) -> Path:
        """``nodes/world.json`` — the synthetic :class:`WorldRootNode`."""
        return self.nodes_dir / "world.json"

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------
    def region_path(self, region_id: RegionId) -> Path:
        """Path of the per-region JSON for ``region_id``."""
        return self.regions_dir / f"{region_id}.json"

    @property
    def sub_regions_dir(self) -> Path:
        """One ``<sub_region_id>.json`` per :class:`SubRegionNode` (Phase 6-c)."""
        return self.root / "sub_regions"

    def sub_region_path(self, sub_region_id: SubRegionId) -> Path:
        """Path of the per-sub-region JSON for ``sub_region_id``."""
        return self.sub_regions_dir / f"{sub_region_id}.json"

    @property
    def environments_dir(self) -> Path:
        """One ``<environment_id>.json`` per :class:`EnvironmentNode` (Phase 6-f)."""
        return self.root / "environments"

    def environment_path(self, environment_id: EnvironmentNodeId) -> Path:
        """Path of the per-environment JSON for ``environment_id``."""
        return self.environments_dir / f"{environment_id}.json"

    def edge_layer_path(self, layer: str) -> Path:
        """Path of the per-layer edge JSON for ``layer``."""
        return self.edges_dir / f"{layer}.json"

    def boundary_path(self, boundary_id: BoundaryId) -> Path:
        """Path of the per-boundary JSON for ``boundary_id``."""
        return self.boundaries_dir / f"{boundary_id}.json"

    def history_event_path(self, event_id: HistoryEventId, kind: HistoryEventKind) -> Path:
        """Path of the history-log entry for ``event_id`` of ``kind``."""
        return self.history_dir / f"{event_id}_{kind.value}.json"

    def spec_path(self, spec_id: SpecId) -> Path:
        """Path of the canonical-JSON :class:`SpecRecord` for ``spec_id``."""
        return self.specs_dir / f"{spec_id}.json"

    @property
    def heightmaps_dir(self) -> Path:
        """``realizations/heightmap`` — one ``<region_id>.npy`` per generated region."""
        return self.realizations_dir / "heightmap"

    def heightmap_npy_path(self, region_id: RegionId) -> Path:
        """Path of the persisted float32 heightmap for ``region_id``."""
        return self.heightmaps_dir / f"{region_id}.npy"

    def heightmap_png_path(self, region_id: RegionId) -> Path:
        """Path of the 16-bit PNG preview for ``region_id``."""
        return self.heightmaps_dir / f"{region_id}.png"

    def stream_geometry_path(self, region_id: RegionId) -> Path:
        """Path of the optional persisted :class:`StreamGeometry` JSON."""
        return self.heightmaps_dir / f"{region_id}.stream.json"

    @property
    def blender_dir(self) -> Path:
        """``realizations/blender`` — one ``.blend`` plus preview/trace per region."""
        return self.realizations_dir / "blender"

    def blend_path(self, region_id: RegionId) -> Path:
        """Path of the persisted ``.blend`` for ``region_id``."""
        return self.blender_dir / f"{region_id}.blend"

    def preview_path(self, region_id: RegionId, view_kind: str, resolution: str) -> Path:
        """Path of the persisted preview PNG for one ``(view_kind, resolution)``.

        ``view_kind`` is one of ``"ortho_top"`` / ``"perspective_se"``;
        ``resolution`` is one of ``"preview"`` / ``"default"`` / ``"full"``.
        """
        return self.blender_dir / f"{region_id}.{view_kind}.{resolution}.png"

    def realization_trace_path(
        self,
        region_id: RegionId,
        view_kind: str,
        resolution: str,
    ) -> Path:
        """Path of the realization-trace sidecar JSON for one ``(view_kind, resolution)``."""
        return self.blender_dir / f"{region_id}.{view_kind}.{resolution}.trace.json"

    def all_directories(self) -> tuple[Path, ...]:
        """Every directory that must exist for the project to be valid."""
        return (
            self.root,
            self.nodes_dir,
            self.regions_dir,
            self.sub_regions_dir,
            self.edges_dir,
            self.specs_dir,
            self.boundaries_dir,
            self.locks_dir,
            self.feature_locks_dir,
            self.history_dir,
            self.realizations_dir,
            self.heightmaps_dir,
            self.blender_dir,
            self.audits_dir,
            self.environments_dir,
        )


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------


@dataclass
class ProjectState:
    """The in-memory mirror of one open project.

    Mutations are funneled through :class:`ProjectService` methods (Stage
    G adds the region CRUD entry points). Holding the state on the
    service keeps the MCP tool layer free of disk-IO bookkeeping.
    """

    paths: ProjectPaths
    metadata: ProjectMetadata
    regions: dict[RegionId, RegionNode] = field(default_factory=dict)
    archetypes: dict[MaterialArchetypeId, MaterialArchetypeNode] = field(default_factory=dict)
    sub_regions: dict[SubRegionId, SubRegionNode] = field(default_factory=dict)
    environments: dict[EnvironmentNodeId, EnvironmentNode] = field(default_factory=dict)
    boundaries: dict[BoundaryId, BoundaryRecord] = field(default_factory=dict)
    edges: dict[str, list[Edge]] = field(default_factory=dict)
    world_root: WorldRootNode | None = None
    """Cached world-root record (Phase 6-f). Lazily filled by ``open_project``."""
    history: HistoryLog = field(init=False)
    lock_store: LockStore = field(init=False)

    def __post_init__(self) -> None:
        """Bind sub-stores to the resolved paths."""
        self.history = HistoryLog(self.paths.history_dir, count=0)
        self.lock_store = LockStore(self.paths.locks_path)

    # --- Compatibility shims for callers that still read the flat fields ---
    @property
    def locks(self) -> list[LockRecord]:
        """Snapshot of the lock store as a list (for back-compat)."""
        return list(self.lock_store.records)

    @property
    def history_count(self) -> int:
        """Number of appended history events (for back-compat)."""
        return self.history.count


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProjectError(Exception):
    """Base class for project-layer errors raised by :class:`ProjectService`."""


class ProjectAlreadyExistsError(ProjectError):
    """Raised when ``create_project`` targets a non-empty / existing tree."""


class ProjectNotFoundError(ProjectError):
    """Raised when ``open_project`` is pointed at a path with no ``project.json``."""


class ProjectFormatError(ProjectError):
    """Raised when an existing project is structurally malformed on disk."""


class ProjectVersionError(ProjectError):
    """Raised when an existing project was written by an incompatible Forge."""


class NoOpenProjectError(ProjectError):
    """Raised when an operation is attempted with no project currently open."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """Return the current UTC time. Indirection point for ``freezegun`` later."""
    return datetime.now(tz=UTC)


class ProjectService:
    """Owns at most one open project at a time (per the v1 MCP server)."""

    def __init__(self) -> None:
        """Construct an empty service with no project loaded."""
        self._state: ProjectState | None = None
        self._subscribers: list[Callable[[str], None]] = []

    # ------------------------------------------------------------------
    # Mutation broadcast (Phase 6 Stage D)
    # ------------------------------------------------------------------
    def subscribe(self, callback: Callable[[str], None]) -> Callable[[], None]:
        """Register ``callback`` to be invoked after every state mutation.

        The callback receives the event kind tag (e.g.
        ``"create_region"``); the canvas server uses it to broadcast a
        fresh JSON-Patch snapshot to every connected WebSocket client.
        Returns an unsubscribe callable.

        Subscribers must be cheap and synchronous. The canvas server
        wraps its async broadcast in :func:`asyncio.run_coroutine_threadsafe`
        so the callback returns immediately; mutators are not blocked
        on socket I/O.
        """
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with suppress(ValueError):
                self._subscribers.remove(callback)

        return _unsubscribe

    def _notify(self, event: str) -> None:
        """Fan ``event`` out to every registered subscriber."""
        for callback in list(self._subscribers):
            with suppress(Exception):
                callback(event)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------
    @property
    def state(self) -> ProjectState:
        """Return the current state or raise :class:`NoOpenProjectError`."""
        if self._state is None:
            msg = "no project is currently open"
            raise NoOpenProjectError(msg)
        return self._state

    @property
    def is_open(self) -> bool:
        """True iff a project is currently loaded."""
        return self._state is not None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def create_project(
        self,
        root: Path,
        name: str,
        world_bounds: WorldBounds,
    ) -> ProjectMetadata:
        """Materialize a fresh project tree at ``root`` and load it.

        Refuses to clobber an existing ``project.json``; an empty
        directory (or a not-yet-existing path) is fine.
        """
        if not name.strip():
            msg = "project name must be non-empty"
            raise ProjectError(msg)
        paths = ProjectPaths(root=root)
        if paths.metadata_path.exists():
            msg = f"project already exists at {root}"
            raise ProjectAlreadyExistsError(msg)
        for directory in paths.all_directories():
            directory.mkdir(parents=True, exist_ok=True)

        now = _now()
        metadata = ProjectMetadata(
            project_id=uuid4(),
            name=name,
            forge_version=_forge_version(),
            blender_version=_BLENDER_VERSION_PLACEHOLDER,
            bpy_hypergraph_version=_BPY_HYPERGRAPH_VERSION_PLACEHOLDER,
            descriptor_schema_version=DESCRIPTOR_SCHEMA_VERSION,
            created_at=now,
            modified_at=now,
            world_node_id=_WORLD_ROOT_NODE_ID,
            registered_layers=_DEFAULT_LAYERS,
            world_bounds=world_bounds,
        )
        world_root = WorldRootNode(
            node_id=_WORLD_ROOT_NODE_ID,
            name=_WORLD_ROOT_NAME,
            created_at=now,
        )
        write_json(paths.metadata_path, metadata)
        write_json(paths.world_node_path, world_root)
        # ``.gitignore`` is plain text, not JSON; bypass the JSON dumper
        # but still go through the atomic helper so a crashed bootstrap
        # never leaves a half-written .gitignore.
        atomic_write_text(paths.gitignore_path, _GITIGNORE_BODY)
        edges_state: dict[str, list[Edge]] = {}
        for layer in _DEFAULT_LAYERS:
            layer_file = EdgeLayerFile(layer=layer)
            write_json(paths.edge_layer_path(layer), layer_file)
            edges_state[layer] = []
        write_json(paths.locks_path, LockStoreFile())

        state = ProjectState(
            paths=paths,
            metadata=metadata,
            edges=edges_state,
            world_root=world_root,
        )
        self._state = state
        self._append_history(
            HistoryEventKind.CREATE_PROJECT,
            payload={"project_id": str(metadata.project_id), "name": metadata.name},
            now=now,
        )
        return metadata

    def open_project(self, root: Path) -> ProjectMetadata:
        """Load an existing project from ``root`` into memory.

        Refuses on missing ``project.json``, malformed metadata, or a
        descriptor-schema version this Forge does not understand.
        """
        paths = ProjectPaths(root=root)
        if not paths.metadata_path.exists():
            msg = f"no project.json under {root}"
            raise ProjectNotFoundError(msg)
        try:
            raw = paths.metadata_path.read_text(encoding="utf-8")
            metadata = ProjectMetadata.model_validate_json(raw)
        except (OSError, ValidationError) as exc:
            msg = f"failed to load project.json: {exc}"
            raise ProjectFormatError(msg) from exc

        if metadata.descriptor_schema_version != DESCRIPTOR_SCHEMA_VERSION:
            msg = (
                f"project descriptor_schema_version "
                f"{metadata.descriptor_schema_version!r} is not supported "
                f"(this Forge speaks {DESCRIPTOR_SCHEMA_VERSION!r})"
            )
            raise ProjectVersionError(msg)

        for directory in (paths.nodes_dir, paths.regions_dir, paths.edges_dir):
            if not directory.is_dir():
                msg = f"project layout is missing required directory: {directory}"
                raise ProjectFormatError(msg)
        # ``sub_regions_dir`` is auto-created on bootstrap (Phase 6-c) but
        # treated as optional for projects authored against older Forge
        # checkouts; ``_load_sub_regions`` returns ``{}`` when missing.

        state = ProjectState(paths=paths, metadata=metadata)
        state.world_root = self._load_world_root(paths, metadata)
        state.regions = self._load_regions(paths)
        state.archetypes = self._load_archetypes(paths)
        state.sub_regions = self._load_sub_regions(paths)
        state.environments = self._load_environments(paths)
        state.edges = self._load_edges(paths, metadata.registered_layers)
        state.boundaries = self._load_boundaries(paths)
        try:
            state.lock_store = LockStore.load(paths.locks_path)
        except LockStoreError as exc:
            msg = f"failed to load locks.json: {exc}"
            raise ProjectFormatError(msg) from exc
        state.history = HistoryLog(paths.history_dir, count=self._count_history(paths))
        self._state = state

        self._append_history(
            HistoryEventKind.OPEN_PROJECT,
            payload={"project_id": str(metadata.project_id)},
        )
        return metadata

    def save_project(self) -> None:
        """Re-flush metadata, edge files, boundaries and locks to disk.

        Idempotent: callers may invoke ``save_project`` repeatedly with
        no semantic effect beyond touching ``modified_at`` and appending
        one ``save_project`` history event per call.
        """
        state = self.state
        now = _now()
        # Replace the in-memory metadata so subsequent saves observe the
        # new ``modified_at``. Pydantic models are frozen, hence
        # ``model_copy``.
        # The pydantic stub for ``model_copy`` returns ``Self`` but the
        # stub erases that to ``BaseModel``; keep the attribute typed.
        new_metadata: ProjectMetadata = state.metadata.model_copy(update={"modified_at": now})
        state.metadata = new_metadata
        write_json(state.paths.metadata_path, new_metadata)
        for region in state.regions.values():
            write_json(state.paths.region_path(region.node_id), region)
        for archetype in state.archetypes.values():
            write_json(self._archetype_path(state.paths, archetype.node_id), archetype)
        for sub_region in state.sub_regions.values():
            write_json(state.paths.sub_region_path(sub_region.node_id), sub_region)
        for layer, edges in state.edges.items():
            write_json(
                state.paths.edge_layer_path(layer),
                EdgeLayerFile(layer=layer, edges=tuple(edges)),
            )
        for boundary in state.boundaries.values():
            write_json(state.paths.boundary_path(boundary.boundary_id), boundary)
        write_json(state.paths.locks_path, LockStoreFile(locks=state.lock_store.records))
        self._append_history(HistoryEventKind.SAVE_PROJECT, now=now)
        self._notify("save_project")

    def close_project(self) -> None:
        """Flush any pending writes and drop the in-memory state."""
        # Append the close event *before* clearing the state so the
        # history file lands in the project we are closing.
        self._append_history(HistoryEventKind.CLOSE_PROJECT)
        self.save_project()
        self._state = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _append_history(
        self,
        kind: HistoryEventKind,
        *,
        payload: Mapping[str, object] | None = None,
        now: datetime | None = None,
    ) -> HistoryEvent:
        """Atomically append one history event to the open project."""
        state = self.state
        return state.history.append(
            kind,
            at=now if now is not None else _now(),
            actor=HistoryActor.AGENT,
            payload=payload,
        )

    @staticmethod
    def _load_regions(paths: ProjectPaths) -> dict[RegionId, RegionNode]:
        regions: dict[RegionId, RegionNode] = {}
        if not paths.regions_dir.is_dir():
            return regions
        for path in sorted(paths.regions_dir.glob("*.json")):
            try:
                region = RegionNode.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as exc:
                msg = f"failed to load region {path.name}: {exc}"
                raise ProjectFormatError(msg) from exc
            regions[region.node_id] = region
        return regions

    @staticmethod
    def _archetype_path(paths: ProjectPaths, archetype_id: MaterialArchetypeId) -> Path:
        """Return the on-disk path for ``archetype_id`` (under ``nodes/``)."""
        return paths.nodes_dir / f"{archetype_id}.json"

    @staticmethod
    def _load_archetypes(
        paths: ProjectPaths,
    ) -> dict[MaterialArchetypeId, MaterialArchetypeNode]:
        archetypes: dict[MaterialArchetypeId, MaterialArchetypeNode] = {}
        if not paths.nodes_dir.is_dir():
            return archetypes
        for path in sorted(paths.nodes_dir.glob("material_*.json")):
            try:
                archetype = MaterialArchetypeNode.model_validate_json(
                    path.read_text(encoding="utf-8"),
                )
            except (OSError, ValidationError) as exc:
                msg = f"failed to load material archetype {path.name}: {exc}"
                raise ProjectFormatError(msg) from exc
            archetypes[archetype.node_id] = archetype
        return archetypes

    @staticmethod
    def _load_sub_regions(
        paths: ProjectPaths,
    ) -> dict[SubRegionId, SubRegionNode]:
        """Load every persisted :class:`SubRegionNode` from ``sub_regions/``.

        Returns an empty dict when ``sub_regions/`` does not exist (older
        projects pre-Phase 6-c). Discriminated-union deserialisation of
        the embedded :data:`SubRegionPredicate` happens automatically via
        Pydantic's ``kind`` literal discriminator.
        """
        sub_regions: dict[SubRegionId, SubRegionNode] = {}
        if not paths.sub_regions_dir.is_dir():
            return sub_regions
        for path in sorted(paths.sub_regions_dir.glob("*.json")):
            try:
                sub_region = SubRegionNode.model_validate_json(
                    path.read_text(encoding="utf-8"),
                )
            except (OSError, ValidationError) as exc:
                msg = f"failed to load sub-region {path.name}: {exc}"
                raise ProjectFormatError(msg) from exc
            sub_regions[sub_region.node_id] = sub_region
        return sub_regions

    @staticmethod
    def _load_environments(
        paths: ProjectPaths,
    ) -> dict[EnvironmentNodeId, EnvironmentNode]:
        """Load every persisted :class:`EnvironmentNode` from ``environments/``.

        Returns an empty dict when ``environments/`` does not exist
        (older projects pre-Phase 6-f).
        """
        environments: dict[EnvironmentNodeId, EnvironmentNode] = {}
        if not paths.environments_dir.is_dir():
            return environments
        for path in sorted(paths.environments_dir.glob("*.json")):
            try:
                env = EnvironmentNode.model_validate_json(
                    path.read_text(encoding="utf-8"),
                )
            except (OSError, ValidationError) as exc:
                msg = f"failed to load environment {path.name}: {exc}"
                raise ProjectFormatError(msg) from exc
            environments[env.node_id] = env
        return environments

    @staticmethod
    def _load_world_root(paths: ProjectPaths, metadata: ProjectMetadata) -> WorldRootNode:
        """Load the persisted :class:`WorldRootNode`, or reconstruct from metadata.

        Older projects (pre-Phase 6-f) may have a ``nodes/world.json``
        without ``environment_id``; Pydantic accepts the missing field
        because it defaults to ``None``. When the file is absent
        entirely (very old checkouts) the record is reconstructed from
        :class:`ProjectMetadata` so existing tests round-trip without a
        bootstrap migration.
        """
        path = paths.world_node_path
        if path.exists():
            try:
                return WorldRootNode.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as exc:
                msg = f"failed to load world root: {exc}"
                raise ProjectFormatError(msg) from exc
        return WorldRootNode(
            node_id=metadata.world_node_id,
            name="World",
            created_at=metadata.created_at,
        )

    @staticmethod
    def _load_edges(
        paths: ProjectPaths,
        registered_layers: tuple[str, ...],
    ) -> dict[str, list[Edge]]:
        edges: dict[str, list[Edge]] = {}
        for layer in registered_layers:
            path = paths.edge_layer_path(layer)
            if not path.exists():
                edges[layer] = []
                continue
            try:
                layer_file = EdgeLayerFile.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as exc:
                msg = f"failed to load edge layer {layer!r}: {exc}"
                raise ProjectFormatError(msg) from exc
            if layer_file.layer != layer:
                msg = (
                    f"edge layer file {path.name} declares layer "
                    f"{layer_file.layer!r}, expected {layer!r}"
                )
                raise ProjectFormatError(msg)
            edges[layer] = list(layer_file.edges)
        return edges

    @staticmethod
    def _load_boundaries(paths: ProjectPaths) -> dict[BoundaryId, BoundaryRecord]:
        boundaries: dict[BoundaryId, BoundaryRecord] = {}
        if not paths.boundaries_dir.is_dir():
            return boundaries
        for path in sorted(paths.boundaries_dir.glob("*.json")):
            try:
                boundary = BoundaryRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as exc:
                msg = f"failed to load boundary {path.name}: {exc}"
                raise ProjectFormatError(msg) from exc
            boundaries[boundary.boundary_id] = boundary
        return boundaries

    @staticmethod
    def _count_history(paths: ProjectPaths) -> int:
        if not paths.history_dir.is_dir():
            return 0
        return sum(1 for _ in paths.history_dir.glob("*.json"))

    # ------------------------------------------------------------------
    # Region CRUD (Stage G)
    # ------------------------------------------------------------------
    def create_region(
        self,
        name: str,
        polygon_coords: tuple[tuple[float, float], ...],
        *,
        structured_descriptor: StructuredDescriptor | None = None,
        seed: int | None = None,
    ) -> RegionNode:
        """Validate, persist, and adjacency-stub a new region.

        Polygon validity is checked through
        :func:`forge_mcp.geometry.polygon.validate_polygon` (shapely);
        overlap with any existing region's polygon raises
        :class:`RegionOverlapError`. Adjacency stubs are emitted via
        :func:`forge_mcp.geometry.adjacency.detect_adjacencies` and the
        whole transaction is recorded as a single
        ``CREATE_REGION`` history event.
        """
        from forge_mcp.geometry.adjacency import detect_adjacencies  # noqa: PLC0415 - cycle break
        from forge_mcp.geometry.polygon import (  # noqa: PLC0415 - cycle break
            PolygonInvalidError,
            polygons_overlap,
            validate_polygon,
        )

        state = self.state
        try:
            validate_polygon(polygon_coords)
        except PolygonInvalidError as exc:
            raise RegionPolygonError(str(exc)) from exc

        for existing in state.regions.values():
            if polygons_overlap(polygon_coords, existing.spatial_bounds.coords.coords):
                msg = f"polygon overlaps existing region {existing.node_id!r} ({existing.name!r})"
                raise RegionOverlapError(msg)

        now = _now()
        region_id = self._allocate_region_id(name)
        region = RegionNode(
            node_id=region_id,
            parent_node=state.metadata.world_node_id,
            name=name,
            spatial_bounds=SpatialBounds(coords=Polygon2D(coords=polygon_coords)),
            seed=seed if seed is not None else 0,
            created_at=now,
            modified_at=now,
            structured_descriptor=structured_descriptor,
        )
        state.regions[region.node_id] = region
        write_json(state.paths.region_path(region.node_id), region)

        boundaries = detect_adjacencies(region, state.regions.values(), now=now)
        for boundary in boundaries:
            state.boundaries[boundary.boundary_id] = boundary
            write_json(state.paths.boundary_path(boundary.boundary_id), boundary)

        self._append_history(
            HistoryEventKind.CREATE_REGION,
            payload={
                "region_id": str(region.node_id),
                "name": region.name,
                "boundary_count": len(boundaries),
            },
            now=now,
        )
        self._notify("create_region")
        return region

    def update_region(
        self,
        region_id: RegionId,
        *,
        name: str | None = None,
        polygon_coords: tuple[tuple[float, float], ...] | None = None,
        structured_descriptor: StructuredDescriptor | None = None,
        clear_descriptor: bool = False,
    ) -> RegionNode:
        """Apply a partial update; re-runs adjacency if the polygon changed."""
        state = self.state
        existing = state.regions.get(region_id)
        if existing is None:
            msg = f"unknown region {region_id!r}"
            raise UnknownRegionError(msg)

        polygon_changed = polygon_coords is not None
        new_bounds = (
            self._validate_replacement_polygon(region_id, polygon_coords)
            if polygon_changed and polygon_coords is not None
            else existing.spatial_bounds
        )

        now = _now()
        update = self._compose_region_update(
            now=now,
            name=name,
            polygon_changed=polygon_changed,
            new_bounds=new_bounds,
            structured_descriptor=structured_descriptor,
            clear_descriptor=clear_descriptor,
        )
        new_region: RegionNode = existing.model_copy(update=update)
        state.regions[region_id] = new_region
        write_json(state.paths.region_path(region_id), new_region)

        boundary_count = 0
        if polygon_changed:
            boundary_count = self._refresh_boundaries(region_id, new_region, now)

        self._append_history(
            HistoryEventKind.UPDATE_REGION,
            payload={
                "region_id": str(region_id),
                "polygon_changed": polygon_changed,
                "boundary_count": boundary_count,
            },
            now=now,
        )
        self._notify("update_region")
        return new_region

    def _validate_replacement_polygon(
        self,
        region_id: RegionId,
        polygon_coords: tuple[tuple[float, float], ...],
    ) -> SpatialBounds:
        from forge_mcp.geometry.polygon import (  # noqa: PLC0415 - cycle break
            PolygonInvalidError,
            polygons_overlap,
            validate_polygon,
        )

        try:
            validate_polygon(polygon_coords)
        except PolygonInvalidError as exc:
            raise RegionPolygonError(str(exc)) from exc
        for other in self.state.regions.values():
            if other.node_id == region_id:
                continue
            if polygons_overlap(polygon_coords, other.spatial_bounds.coords.coords):
                msg = f"polygon overlaps existing region {other.node_id!r} ({other.name!r})"
                raise RegionOverlapError(msg)
        return SpatialBounds(coords=Polygon2D(coords=polygon_coords))

    @staticmethod
    def _compose_region_update(  # noqa: PLR0913 - flat kwargs are clearer here than a dataclass
        *,
        now: datetime,
        name: str | None,
        polygon_changed: bool,
        new_bounds: SpatialBounds,
        structured_descriptor: StructuredDescriptor | None,
        clear_descriptor: bool,
    ) -> dict[str, object]:
        update: dict[str, object] = {"modified_at": now}
        if name is not None:
            update["name"] = name
        if polygon_changed:
            update["spatial_bounds"] = new_bounds
        if clear_descriptor:
            update["structured_descriptor"] = None
        elif structured_descriptor is not None:
            update["structured_descriptor"] = structured_descriptor
        return update

    def _refresh_boundaries(
        self,
        region_id: RegionId,
        new_region: RegionNode,
        now: datetime,
    ) -> int:
        from forge_mcp.geometry.adjacency import detect_adjacencies  # noqa: PLC0415 - cycle break

        state = self.state
        stale = [
            bid for bid, b in state.boundaries.items() if region_id in (b.region_a, b.region_b)
        ]
        for bid in stale:
            state.boundaries.pop(bid)
            path = state.paths.boundary_path(bid)
            if path.exists():
                path.unlink()
        new_boundaries = detect_adjacencies(new_region, state.regions.values(), now=now)
        for boundary in new_boundaries:
            state.boundaries[boundary.boundary_id] = boundary
            write_json(state.paths.boundary_path(boundary.boundary_id), boundary)
        return len(new_boundaries)

    def delete_region(self, region_id: RegionId) -> None:
        """Delete a region and any boundaries it participates in."""
        state = self.state
        if region_id not in state.regions:
            msg = f"unknown region {region_id!r}"
            raise UnknownRegionError(msg)
        del state.regions[region_id]
        path = state.paths.region_path(region_id)
        if path.exists():
            path.unlink()
        stale = [
            bid for bid, b in state.boundaries.items() if region_id in (b.region_a, b.region_b)
        ]
        for bid in stale:
            state.boundaries.pop(bid)
            bpath = state.paths.boundary_path(bid)
            if bpath.exists():
                bpath.unlink()
        self._append_history(
            HistoryEventKind.DELETE_REGION,
            payload={"region_id": str(region_id), "boundary_count": len(stale)},
        )
        self._notify("delete_region")

    # ------------------------------------------------------------------
    # Spec + region-spec linkage (Phase 3)
    # ------------------------------------------------------------------
    def persist_spec(self, spec: SpecRecord) -> None:
        """Atomically persist ``spec`` to ``specs/<spec_id>.json``."""
        write_json(self.state.paths.spec_path(spec.spec_id), spec)

    def persist_boundary(self, boundary: BoundaryRecord) -> None:
        """Atomically persist ``boundary`` to ``boundaries/<boundary_id>.json``.

        Used by the Phase-6 contract-refresh path in
        :func:`forge_mcp.server.tools.generation.generate_region` after
        :func:`forge_mcp.boundary.refresh_contract_for` produces a
        contract-bearing :class:`BoundaryRecord`.
        """
        self.state.boundaries[boundary.boundary_id] = boundary
        write_json(self.state.paths.boundary_path(boundary.boundary_id), boundary)

    def load_spec(self, spec_id: SpecId) -> SpecRecord:
        """Load and return a previously-persisted spec."""
        path = self.state.paths.spec_path(spec_id)
        if not path.exists():
            msg = f"unknown spec {spec_id!r}"
            raise UnknownSpecError(msg)
        try:
            return SpecRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:  # pragma: no cover - I/O race
            msg = f"failed to load spec {spec_id!r}: {exc}"
            raise ProjectFormatError(msg) from exc

    def link_region_to_spec(self, region_id: RegionId, spec_id: SpecId) -> RegionNode:
        """Update ``region.spec_id`` and ``modified_at``; persist; return new region."""
        state = self.state
        existing = state.regions.get(region_id)
        if existing is None:
            msg = f"unknown region {region_id!r}"
            raise UnknownRegionError(msg)
        new_region: RegionNode = existing.model_copy(
            update={"spec_id": spec_id, "modified_at": _now()},
        )
        state.regions[region_id] = new_region
        write_json(state.paths.region_path(region_id), new_region)
        return new_region

    def reroll_region_seed(self, region_id: RegionId, new_seed: int) -> RegionNode:
        """Replace ``region.seed`` with ``new_seed``; persist; record history."""
        state = self.state
        existing = state.regions.get(region_id)
        if existing is None:
            msg = f"unknown region {region_id!r}"
            raise UnknownRegionError(msg)
        now = _now()
        new_region: RegionNode = existing.model_copy(
            update={"seed": new_seed, "modified_at": now},
        )
        state.regions[region_id] = new_region
        write_json(state.paths.region_path(region_id), new_region)
        self._append_history(
            HistoryEventKind.REROLL_SEED,
            payload={
                "region_id": str(region_id),
                "previous_seed": existing.seed,
                "new_seed": new_seed,
            },
            now=now,
        )
        return new_region

    def record_generation(
        self,
        region_id: RegionId,
        spec_id: SpecId,
        *,
        generators_used: tuple[str, ...],
    ) -> HistoryEvent:
        """Append the ``generate_region`` history event."""
        return self._append_history(
            HistoryEventKind.GENERATE_REGION,
            payload={
                "region_id": str(region_id),
                "spec_id": str(spec_id),
                "generators_used": list(generators_used),
            },
        )

    # ------------------------------------------------------------------
    # Sub-region CRUD (Phase 6-c)
    # ------------------------------------------------------------------
    def create_sub_region(
        self,
        parent_region_id: RegionId,
        name: str,
        predicate: SubRegionPredicate,
        *,
        tags: tuple[str, ...] = (),
        notes: str = "",
    ) -> SubRegionNode:
        """Create and persist a new :class:`SubRegionNode` under a region.

        Side effects:

        * appends the freshly-allocated id to the parent region's
          :attr:`RegionNode.children` tuple and rewrites the region JSON;
        * inserts a directed ``spatial_containment`` edge
          ``(parent_region_id, sub_region_id)`` so the realizer can walk
          children deterministically;
        * records a single ``SUB_REGION_CREATED`` history event.

        Raises :class:`UnknownParentRegionError` if ``parent_region_id``
        is not an existing region (sub-region nesting is not supported
        in v1 — pass a ``RegionId``).
        """
        state = self.state
        parent = state.regions.get(parent_region_id)
        if parent is None:
            msg = f"unknown parent region {parent_region_id!r}"
            raise UnknownParentRegionError(msg)
        now = _now()
        sub_region_id = self._allocate_sub_region_id(name)
        sub_region = SubRegionNode(
            node_id=sub_region_id,
            parent_node=parent_region_id,
            name=name,
            predicate=predicate,
            tags=tags,
            notes=notes,
            created_at=now,
            modified_at=now,
        )
        state.sub_regions[sub_region_id] = sub_region
        write_json(state.paths.sub_region_path(sub_region_id), sub_region)

        # Append the child id to the parent region's frozen children tuple.
        new_children = (*parent.children, NodeId(str(sub_region_id)))
        new_parent: RegionNode = parent.model_copy(
            update={"children": new_children, "modified_at": now},
        )
        state.regions[parent_region_id] = new_parent
        write_json(state.paths.region_path(parent_region_id), new_parent)

        # Insert the directed (parent_region -> sub_region) containment edge.
        edge_id = self._allocate_edge_id(LAYER_SPATIAL_CONTAINMENT, "cont")
        edge = Edge(
            edge_id=edge_id,
            layer=LAYER_SPATIAL_CONTAINMENT,
            endpoints=(NodeId(str(parent_region_id)), NodeId(str(sub_region_id))),
            directed=True,
            attrs={},
            created_at=now,
            modified_at=now,
        )
        state.edges.setdefault(LAYER_SPATIAL_CONTAINMENT, []).append(edge)
        write_json(
            state.paths.edge_layer_path(LAYER_SPATIAL_CONTAINMENT),
            EdgeLayerFile(
                layer=LAYER_SPATIAL_CONTAINMENT,
                edges=tuple(state.edges[LAYER_SPATIAL_CONTAINMENT]),
            ),
        )

        self._append_history(
            HistoryEventKind.SUB_REGION_CREATED,
            payload={
                "sub_region_id": str(sub_region_id),
                "parent_region_id": str(parent_region_id),
                "predicate_kind": predicate.kind,
                "name": name,
            },
            now=now,
        )
        self._notify("create_sub_region")
        return sub_region

    def update_sub_region(
        self,
        sub_region_id: SubRegionId,
        *,
        name: str | None = None,
        predicate: SubRegionPredicate | None = None,
        tags: tuple[str, ...] | None = None,
        notes: str | None = None,
    ) -> SubRegionNode:
        """Apply a partial update to an existing :class:`SubRegionNode`."""
        state = self.state
        existing = state.sub_regions.get(sub_region_id)
        if existing is None:
            msg = f"unknown sub-region {sub_region_id!r}"
            raise UnknownSubRegionError(msg)
        now = _now()
        update: dict[str, object] = {"modified_at": now}
        if name is not None:
            update["name"] = name
        if predicate is not None:
            update["predicate"] = predicate
        if tags is not None:
            update["tags"] = tags
        if notes is not None:
            update["notes"] = notes
        new_sub_region: SubRegionNode = existing.model_copy(update=update)
        state.sub_regions[sub_region_id] = new_sub_region
        write_json(state.paths.sub_region_path(sub_region_id), new_sub_region)
        self._append_history(
            HistoryEventKind.SUB_REGION_UPDATED,
            payload={"sub_region_id": str(sub_region_id)},
            now=now,
        )
        self._notify("update_sub_region")
        return new_sub_region

    def delete_sub_region(self, sub_region_id: SubRegionId) -> None:
        """Delete a sub-region iff no ``material_application`` edge targets it.

        Also removes the parent region's ``spatial_containment`` edge and
        prunes the id from :attr:`RegionNode.children`. Raises
        :class:`SubRegionInUseError` if any material application still
        references the sub-region.
        """
        state = self.state
        existing = state.sub_regions.get(sub_region_id)
        if existing is None:
            msg = f"unknown sub-region {sub_region_id!r}"
            raise UnknownSubRegionError(msg)
        node_id = NodeId(str(sub_region_id))
        for edge in state.edges.get(LAYER_MATERIAL_APPLICATION, ()):
            if node_id in edge.endpoints:
                msg = (
                    f"sub-region {sub_region_id!r} is still referenced by "
                    f"material application edge {edge.edge_id!r}"
                )
                raise SubRegionInUseError(msg)

        del state.sub_regions[sub_region_id]
        path = state.paths.sub_region_path(sub_region_id)
        with suppress(FileNotFoundError):
            path.unlink()

        # Drop the spatial_containment edge whose child endpoint is this id.
        containment = state.edges.get(LAYER_SPATIAL_CONTAINMENT, [])
        kept = [e for e in containment if node_id not in e.endpoints]
        if len(kept) != len(containment):
            state.edges[LAYER_SPATIAL_CONTAINMENT] = kept
            write_json(
                state.paths.edge_layer_path(LAYER_SPATIAL_CONTAINMENT),
                EdgeLayerFile(
                    layer=LAYER_SPATIAL_CONTAINMENT,
                    edges=tuple(kept),
                ),
            )

        # Prune the child id from the parent region's children tuple.
        parent_id = existing.parent_node
        parent = state.regions.get(parent_id)
        if parent is not None and node_id in parent.children:
            now = _now()
            new_children = tuple(c for c in parent.children if c != node_id)
            new_parent: RegionNode = parent.model_copy(
                update={"children": new_children, "modified_at": now},
            )
            state.regions[parent_id] = new_parent
            write_json(state.paths.region_path(parent_id), new_parent)

        self._append_history(
            HistoryEventKind.SUB_REGION_DELETED,
            payload={
                "sub_region_id": str(sub_region_id),
                "parent_region_id": str(parent_id),
            },
        )
        self._notify("delete_sub_region")

    # ------------------------------------------------------------------
    # Environment CRUD + binding (Phase 6-f)
    # ------------------------------------------------------------------
    def create_environment(
        self,
        name: str,
        recipe: EnvironmentRecipe,
        parameters: EnvironmentParameters,
        *,
        tags: tuple[str, ...] = (),
        notes: str = "",
    ) -> EnvironmentNode:
        """Create and persist a new :class:`EnvironmentNode`.

        The node is *unbound* on creation; call
        :meth:`bind_environment` to attach it to the world root or a
        region.
        """
        state = self.state
        now = _now()
        env_id = self._allocate_environment_id(name)
        env = EnvironmentNode(
            node_id=env_id,
            name=name,
            recipe=recipe,
            parameters=parameters,
            tags=tags,
            notes=notes,
            created_at=now,
            modified_at=now,
        )
        state.environments[env_id] = env
        write_json(state.paths.environment_path(env_id), env)
        self._append_history(
            HistoryEventKind.ENVIRONMENT_CREATED,
            payload={"environment_id": str(env_id), "recipe": recipe.value, "name": name},
            now=now,
        )
        self._notify("create_environment")
        return env

    def update_environment(  # noqa: PLR0913 - flat kwargs mirror create_environment shape
        self,
        environment_id: EnvironmentNodeId,
        *,
        name: str | None = None,
        recipe: EnvironmentRecipe | None = None,
        parameters: EnvironmentParameters | None = None,
        tags: tuple[str, ...] | None = None,
        notes: str | None = None,
    ) -> EnvironmentNode:
        """Apply a partial update to an existing :class:`EnvironmentNode`."""
        state = self.state
        existing = state.environments.get(environment_id)
        if existing is None:
            msg = f"unknown environment {environment_id!r}"
            raise UnknownEnvironmentError(msg)
        now = _now()
        update: dict[str, object] = {"modified_at": now}
        if name is not None:
            update["name"] = name
        if recipe is not None:
            update["recipe"] = recipe
        if parameters is not None:
            update["parameters"] = parameters
        if tags is not None:
            update["tags"] = tags
        if notes is not None:
            update["notes"] = notes
        new_env: EnvironmentNode = existing.model_copy(update=update)
        state.environments[environment_id] = new_env
        write_json(state.paths.environment_path(environment_id), new_env)
        self._append_history(
            HistoryEventKind.ENVIRONMENT_UPDATED,
            payload={"environment_id": str(environment_id)},
            now=now,
        )
        self._notify("update_environment")
        return new_env

    def delete_environment(self, environment_id: EnvironmentNodeId) -> None:
        """Delete an environment iff no scope (world or region) binds it."""
        state = self.state
        if environment_id not in state.environments:
            msg = f"unknown environment {environment_id!r}"
            raise UnknownEnvironmentError(msg)
        if state.world_root is not None and state.world_root.environment_id == environment_id:
            msg = f"environment {environment_id!r} is still bound to the world root; unbind first"
            raise EnvironmentInUseError(msg)
        for region in state.regions.values():
            if region.environment_id == environment_id:
                msg = (
                    f"environment {environment_id!r} is still bound to region "
                    f"{region.node_id!r}; unbind first"
                )
                raise EnvironmentInUseError(msg)
        del state.environments[environment_id]
        path = state.paths.environment_path(environment_id)
        with suppress(FileNotFoundError):
            path.unlink()
        self._append_history(
            HistoryEventKind.ENVIRONMENT_DELETED,
            payload={"environment_id": str(environment_id)},
        )
        self._notify("delete_environment")

    def bind_environment(
        self,
        scope_node_id: NodeId,
        environment_id: EnvironmentNodeId,
    ) -> None:
        """Bind ``environment_id`` to ``scope_node_id`` (world root or a region).

        Replaces any existing binding on the scope. The environment must
        already exist (call :meth:`create_environment` first); the
        scope must resolve to either the world-root node id or a known
        region id, otherwise :class:`UnknownScopeError` is raised.
        """
        state = self.state
        if environment_id not in state.environments:
            msg = f"unknown environment {environment_id!r}"
            raise UnknownEnvironmentError(msg)
        scope_str = str(scope_node_id)
        now = _now()
        if state.world_root is not None and scope_str == str(state.world_root.node_id):
            new_root: WorldRootNode = state.world_root.model_copy(
                update={"environment_id": environment_id},
            )
            state.world_root = new_root
            write_json(state.paths.world_node_path, new_root)
        elif RegionId(scope_str) in state.regions:
            region_id = RegionId(scope_str)
            region = state.regions[region_id]
            new_region: RegionNode = region.model_copy(
                update={"environment_id": environment_id, "modified_at": now},
            )
            state.regions[region_id] = new_region
            write_json(state.paths.region_path(region_id), new_region)
        else:
            msg = f"scope {scope_node_id!r} is neither the world root nor a known region"
            raise UnknownScopeError(msg)
        self._append_history(
            HistoryEventKind.ENVIRONMENT_BOUND,
            payload={"scope_node_id": scope_str, "environment_id": str(environment_id)},
            now=now,
        )
        self._notify("bind_environment")

    def unbind_environment(self, scope_node_id: NodeId) -> None:
        """Clear the environment binding on ``scope_node_id``.

        No-op when the scope already has no binding. Raises
        :class:`UnknownScopeError` when the scope is neither the world
        root nor a known region.
        """
        state = self.state
        scope_str = str(scope_node_id)
        now = _now()
        if state.world_root is not None and scope_str == str(state.world_root.node_id):
            if state.world_root.environment_id is None:
                return
            new_root: WorldRootNode = state.world_root.model_copy(
                update={"environment_id": None},
            )
            state.world_root = new_root
            write_json(state.paths.world_node_path, new_root)
        elif RegionId(scope_str) in state.regions:
            region_id = RegionId(scope_str)
            region = state.regions[region_id]
            if region.environment_id is None:
                return
            new_region: RegionNode = region.model_copy(
                update={"environment_id": None, "modified_at": now},
            )
            state.regions[region_id] = new_region
            write_json(state.paths.region_path(region_id), new_region)
        else:
            msg = f"scope {scope_node_id!r} is neither the world root nor a known region"
            raise UnknownScopeError(msg)
        self._append_history(
            HistoryEventKind.ENVIRONMENT_UNBOUND,
            payload={"scope_node_id": scope_str},
            now=now,
        )
        self._notify("unbind_environment")

    # ------------------------------------------------------------------
    # Lock CRUD (Phase 7 Stage A)
    # ------------------------------------------------------------------
    def create_property_lock(
        self,
        *,
        region_id: RegionId,
        json_path: str,
    ) -> LockRecord:
        """Create a :attr:`LockKind.PROPERTY` lock pinning ``json_path``.

        Captures the current value at ``json_path`` (resolved against the
        region's persisted JSON) as ``expected_value``. Raises
        :class:`UnknownRegionError` if the region does not exist or
        :class:`LockTargetNotFoundError` if ``json_path`` is missing or
        empty.
        """
        region = self._require_region(region_id)
        value = self._resolve_property_path(region, json_path)
        payload: dict[str, JsonValue] = {
            "json_path": json_path,
            "expected_value": value,
        }
        return self._create_lock(LockKind.PROPERTY, region_id, payload)

    def create_feature_lock(
        self,
        *,
        region_id: RegionId,
        bbox_world: tuple[float, float, float, float],
    ) -> LockRecord:
        """Create a :attr:`LockKind.FEATURE` lock capturing a heightmap patch.

        ``captured_seed``, ``captured_at`` and ``captured_path`` are
        derived deterministically by the service. Raises
        :class:`UnknownRegionError`, :class:`LockTargetNotFoundError`
        (no heightmap on disk or bbox outside the region), or
        :class:`OverlappingFeatureLockError` when the bbox overlaps an
        existing feature lock.
        """
        region = self._require_region(region_id)
        now = _now()
        # ``lock_id`` is allocated from a payload that does NOT yet
        # contain ``captured_path`` (which depends on ``lock_id``),
        # then ``captured_path`` is filled in canonically.
        bootstrap_payload: dict[str, JsonValue] = {
            "bbox_world": list(bbox_world),
            "captured_seed": region.seed,
            "captured_at": now.isoformat(),
        }
        lock_id = self._allocate_lock_id(LockKind.FEATURE, region_id, bootstrap_payload, now)
        canonical_payload: dict[str, JsonValue] = {
            **bootstrap_payload,
            "captured_path": f"locks/feature/{lock_id}.npy",
        }
        return self._create_lock(
            LockKind.FEATURE,
            region_id,
            canonical_payload,
            now=now,
            lock_id=lock_id,
        )

    def create_region_lock(self, *, region_id: RegionId) -> LockRecord:
        """Create a :attr:`LockKind.REGION` ``skip_regen`` lock."""
        self._require_region(region_id)
        return self._create_lock(
            LockKind.REGION,
            region_id,
            {"scope": "skip_regen"},
        )

    def remove_lock(self, lock_id: LockId) -> LockRecord:
        """Remove a lock by id; cleans up the ``.npy`` patch for FEATURE locks."""
        state = self.state
        record = state.lock_store.find_by_id(lock_id)
        if record is None:
            msg = f"unknown lock {lock_id!r}"
            raise UnknownLockError(msg)
        try:
            removed = state.lock_store.remove_lock(lock_id)
        except LockNotFoundError as exc:  # pragma: no cover - defensive race guard
            msg = f"unknown lock {lock_id!r}"
            raise UnknownLockError(msg) from exc
        if removed.kind is LockKind.FEATURE:
            with suppress(FileNotFoundError):
                state.paths.feature_lock_patch_path(lock_id).unlink()
        self._append_history(
            HistoryEventKind.LOCK_REMOVED,
            payload={
                "lock_id": str(lock_id),
                "region_id": str(removed.region_id),
                "kind": removed.kind.value,
            },
        )
        self._notify("remove_lock")
        return removed

    # ------------------------------------------------------------------
    # Lock CRUD internals
    # ------------------------------------------------------------------
    def _require_region(self, region_id: RegionId) -> RegionNode:
        state = self.state
        if region_id not in state.regions:
            msg = f"unknown region {region_id!r}"
            raise UnknownRegionError(msg)
        return state.regions[region_id]

    def _create_lock(
        self,
        kind: LockKind,
        region_id: RegionId,
        payload: Mapping[str, JsonValue],
        *,
        now: datetime | None = None,
        lock_id: LockId | None = None,
    ) -> LockRecord:
        """Validate, persist, and history-log a new :class:`LockRecord`.

        Per-kind validation runs the kind-specific Pydantic model
        (``ValidationError`` propagates unchanged). FEATURE locks also
        check overlap and capture the heightmap patch on disk.
        """
        # Per-kind payload validation.
        typed: PropertyLockPayload | FeatureLockPayload | RegionLockPayload
        match kind:
            case LockKind.PROPERTY:
                typed = PropertyLockPayload.model_validate(dict(payload))
            case LockKind.FEATURE:
                typed = FeatureLockPayload.model_validate(dict(payload))
            case LockKind.REGION:
                typed = RegionLockPayload.model_validate(dict(payload))
        canonical_payload: dict[str, JsonValue] = typed.model_dump(mode="json")

        state = self.state
        timestamp = now if now is not None else _now()
        chosen_id = (
            lock_id
            if lock_id is not None
            else self._allocate_lock_id(kind, region_id, canonical_payload, timestamp)
        )

        if isinstance(typed, FeatureLockPayload):
            existing_overlaps = state.lock_store.find_overlapping_features(
                region_id,
                typed.bbox_world,
            )
            if existing_overlaps:
                msg = f"feature lock would overlap existing lock {existing_overlaps[0].lock_id!r}"
                raise OverlappingFeatureLockError(msg)
            self._capture_feature_patch(region_id, chosen_id, typed)

        record = LockRecord(
            lock_id=chosen_id,
            region_id=region_id,
            kind=kind,
            payload=canonical_payload,
            created_at=timestamp,
            modified_at=timestamp,
        )
        try:
            state.lock_store.add_lock(record)
        except DuplicateLockError:
            if kind is LockKind.FEATURE:
                with suppress(FileNotFoundError):
                    state.paths.feature_lock_patch_path(chosen_id).unlink()
            raise
        self._append_history(
            HistoryEventKind.LOCK_CREATED,
            payload={
                "lock_id": str(chosen_id),
                "region_id": str(region_id),
                "kind": kind.value,
            },
            now=timestamp,
        )
        self._notify("create_lock")
        return record

    def _resolve_property_path(self, region: RegionNode, json_path: str) -> JsonValue:
        """Walk ``json_path`` (``a.b.c``) on the persisted region JSON.

        Returns the value at the path. Raises
        :class:`LockTargetNotFoundError` if any segment is missing.
        Sequence indices are not yet supported; segments are dict keys
        only. Empty ``json_path`` is rejected.
        """
        if not json_path:
            msg = "json_path must be non-empty"
            raise LockTargetNotFoundError(msg)
        node: JsonValue = region.model_dump(mode="json")
        for segment in json_path.split("."):
            if not isinstance(node, dict) or segment not in node:
                msg = f"json_path {json_path!r} not found on region {region.node_id!r}"
                raise LockTargetNotFoundError(msg)
            node = node[segment]
        return node

    def _capture_feature_patch(
        self,
        region_id: RegionId,
        lock_id: LockId,
        payload: FeatureLockPayload,
    ) -> None:
        """Slice the heightmap patch covered by ``payload.bbox_world`` and persist it."""
        state = self.state
        npy_path = state.paths.heightmap_npy_path(region_id)
        if not npy_path.exists():
            msg = (
                f"region {region_id!r} has no heightmap on disk; generate it "
                f"before locking a feature"
            )
            raise LockTargetNotFoundError(msg)
        heightmap = _load_heightmap_npy(npy_path)
        x0, y0, x1, y1 = payload.bbox_world
        ox, oy = heightmap.origin
        res = heightmap.resolution_meters_per_pixel
        col0 = int(np.floor((x0 - ox) / res))
        col1 = int(np.ceil((x1 - ox) / res))
        row0 = int(np.floor((y0 - oy) / res))
        row1 = int(np.ceil((y1 - oy) / res))
        height, width = heightmap.shape
        col0_c = max(0, min(width, col0))
        col1_c = max(0, min(width, col1))
        row0_c = max(0, min(height, row0))
        row1_c = max(0, min(height, row1))
        if col1_c <= col0_c or row1_c <= row0_c:
            msg = (
                f"feature bbox {payload.bbox_world!r} does not intersect region "
                f"{region_id!r} heightmap"
            )
            raise LockTargetNotFoundError(msg)
        patch = heightmap.data[row0_c:row1_c, col0_c:col1_c].astype(
            np.float32,
            copy=True,
        )
        buffer = io.BytesIO()
        np.save(buffer, patch, allow_pickle=False)
        atomic_write_bytes(state.paths.feature_lock_patch_path(lock_id), buffer.getvalue())

    def _allocate_lock_id(
        self,
        kind: LockKind,
        region_id: RegionId,
        payload: Mapping[str, JsonValue],
        created_at: datetime,
    ) -> LockId:
        """Return a deterministic ``lock_<10hex>`` id (blake2b digest).

        The digest covers ``(kind, region_id, payload, created_at)`` so two
        otherwise-identical locks created in the same second still differ
        by their ``created_at`` timestamp's microsecond field. The
        ``payload`` is hashed in its current shape (which may be the
        FEATURE bootstrap form lacking ``captured_path``) so the FEATURE
        lock-id can be derived *before* the canonical payload is built.
        """
        seed = {
            "kind": kind.value,
            "region_id": str(region_id),
            "payload": dict(payload),
            "created_at": created_at.isoformat(),
        }
        digest = blake2b(
            json.dumps(seed, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            digest_size=10,
        ).hexdigest()
        return LockId(f"lock_{digest}")

    # ------------------------------------------------------------------
    # Material archetype CRUD (Phase 6-bis)
    # ------------------------------------------------------------------
    def create_material_archetype(
        self,
        name: str,
        recipe: MaterialRecipe,
        parameters: Mapping[str, JsonValue],
        *,
        tags: tuple[str, ...] = (),
        notes: str = "",
    ) -> MaterialArchetypeNode:
        """Create and persist a new material archetype node.

        ``parameters`` is recipe-specific and validated by the resolver
        when a plan is built (Phase 6-bis Phase B). At this layer we
        only enforce the structural shape declared on
        :class:`~forge_mcp.project.schemas.MaterialArchetypeNode`.
        """
        state = self.state
        now = _now()
        archetype_id = self._allocate_archetype_id(name)
        archetype = MaterialArchetypeNode(
            node_id=archetype_id,
            name=name,
            recipe=recipe,
            parameters=dict(parameters),
            tags=tags,
            notes=notes,
            created_at=now,
            modified_at=now,
        )
        state.archetypes[archetype_id] = archetype
        write_json(self._archetype_path(state.paths, archetype_id), archetype)
        self._append_history(
            HistoryEventKind.MATERIAL_ARCHETYPE_CREATED,
            payload={
                "archetype_id": str(archetype_id),
                "recipe": recipe.value,
                "name": name,
            },
            now=now,
        )
        self._notify("create_material_archetype")
        return archetype

    def update_material_archetype(
        self,
        archetype_id: MaterialArchetypeId,
        *,
        name: str | None = None,
        parameters: Mapping[str, JsonValue] | None = None,
        tags: tuple[str, ...] | None = None,
        notes: str | None = None,
    ) -> MaterialArchetypeNode:
        """Apply a partial update to an existing archetype node."""
        state = self.state
        existing = state.archetypes.get(archetype_id)
        if existing is None:
            msg = f"unknown material archetype {archetype_id!r}"
            raise UnknownArchetypeError(msg)
        now = _now()
        update: dict[str, object] = {"modified_at": now}
        if name is not None:
            update["name"] = name
        if parameters is not None:
            update["parameters"] = dict(parameters)
        if tags is not None:
            update["tags"] = tags
        if notes is not None:
            update["notes"] = notes
        new_archetype: MaterialArchetypeNode = existing.model_copy(update=update)
        state.archetypes[archetype_id] = new_archetype
        write_json(self._archetype_path(state.paths, archetype_id), new_archetype)
        self._append_history(
            HistoryEventKind.MATERIAL_ARCHETYPE_UPDATED,
            payload={"archetype_id": str(archetype_id)},
            now=now,
        )
        self._notify("update_material_archetype")
        return new_archetype

    def delete_material_archetype(self, archetype_id: MaterialArchetypeId) -> None:
        """Delete an archetype iff no material edge references it."""
        state = self.state
        if archetype_id not in state.archetypes:
            msg = f"unknown material archetype {archetype_id!r}"
            raise UnknownArchetypeError(msg)
        node_id = NodeId(str(archetype_id))
        for layer_name in (LAYER_MATERIAL_APPLICATION, LAYER_MATERIAL_COMPOSITION):
            for edge in state.edges.get(layer_name, ()):
                if node_id in edge.endpoints:
                    msg = (
                        f"archetype {archetype_id!r} is still referenced by edge "
                        f"{edge.edge_id!r} in layer {layer_name!r}"
                    )
                    raise ArchetypeInUseError(msg)
        del state.archetypes[archetype_id]
        path = self._archetype_path(state.paths, archetype_id)
        with suppress(FileNotFoundError):
            path.unlink()
        self._append_history(
            HistoryEventKind.MATERIAL_ARCHETYPE_DELETED,
            payload={"archetype_id": str(archetype_id)},
        )
        self._notify("delete_material_archetype")

    # ------------------------------------------------------------------
    # Material edge CRUD (Phase 6-bis)
    # ------------------------------------------------------------------
    def apply_material(
        self,
        archetype_id: MaterialArchetypeId,
        target_node_id: NodeId,
        *,
        attrs: MaterialApplicationAttrs,
    ) -> Edge:
        """Add a ``material_application`` edge from ``archetype`` to ``target``.

        Endpoints are written as ``(archetype_id, target_node_id)`` —
        archetype first so the directed projection is unambiguous.
        Validates that the archetype exists, the target exists (region
        or world root), and that ``attrs.scope`` is consistent with the
        target's node kind.
        """
        state = self.state
        if archetype_id not in state.archetypes:
            msg = f"unknown material archetype {archetype_id!r}"
            raise UnknownArchetypeError(msg)
        self._require_target_node(target_node_id, attrs.scope)
        now = _now()
        edge_id = self._allocate_edge_id(LAYER_MATERIAL_APPLICATION, "matapp")
        edge = Edge(
            edge_id=edge_id,
            layer=LAYER_MATERIAL_APPLICATION,
            endpoints=(NodeId(str(archetype_id)), target_node_id),
            directed=True,
            attrs=attrs.model_dump(mode="json"),
            created_at=now,
            modified_at=now,
        )
        state.edges.setdefault(LAYER_MATERIAL_APPLICATION, []).append(edge)
        write_json(
            state.paths.edge_layer_path(LAYER_MATERIAL_APPLICATION),
            EdgeLayerFile(
                layer=LAYER_MATERIAL_APPLICATION,
                edges=tuple(state.edges[LAYER_MATERIAL_APPLICATION]),
            ),
        )
        self._append_history(
            HistoryEventKind.MATERIAL_APPLIED,
            payload={
                "edge_id": str(edge_id),
                "archetype_id": str(archetype_id),
                "target_node_id": str(target_node_id),
                "scope": attrs.scope.value,
            },
            now=now,
        )
        self._notify("apply_material")
        return edge

    def unapply_material(self, edge_id: str) -> None:
        """Remove the named ``material_application`` edge."""
        self._remove_material_edge(
            edge_id=edge_id,
            layer=LAYER_MATERIAL_APPLICATION,
            kind=HistoryEventKind.MATERIAL_UNAPPLIED,
            event_tag="unapply_material",
        )

    def compose_material(
        self,
        parent_archetype_id: MaterialArchetypeId,
        child_archetype_id: MaterialArchetypeId,
        *,
        attrs: MaterialCompositionAttrs,
    ) -> Edge:
        """Add a directed ``material_composition`` edge ``parent -> child``."""
        state = self.state
        if parent_archetype_id == child_archetype_id:
            msg = f"composition edge endpoints must differ (got {parent_archetype_id!r} twice)"
            raise MaterialEdgePolicyError(msg)
        for endpoint in (parent_archetype_id, child_archetype_id):
            if endpoint not in state.archetypes:
                msg = f"unknown material archetype {endpoint!r}"
                raise UnknownArchetypeError(msg)
        now = _now()
        edge_id = self._allocate_edge_id(LAYER_MATERIAL_COMPOSITION, "matcmp")
        edge = Edge(
            edge_id=edge_id,
            layer=LAYER_MATERIAL_COMPOSITION,
            endpoints=(NodeId(str(parent_archetype_id)), NodeId(str(child_archetype_id))),
            directed=True,
            attrs=attrs.model_dump(mode="json"),
            created_at=now,
            modified_at=now,
        )
        state.edges.setdefault(LAYER_MATERIAL_COMPOSITION, []).append(edge)
        cycle = self._composition_cycle_after_change()
        if cycle is not None:
            # Roll back and refuse.
            state.edges[LAYER_MATERIAL_COMPOSITION].pop()
            msg = (
                f"composition edge would introduce a cycle through "
                f"{' -> '.join(str(n) for n in cycle)}"
            )
            raise MaterialCompositionCycleError(msg)
        write_json(
            state.paths.edge_layer_path(LAYER_MATERIAL_COMPOSITION),
            EdgeLayerFile(
                layer=LAYER_MATERIAL_COMPOSITION,
                edges=tuple(state.edges[LAYER_MATERIAL_COMPOSITION]),
            ),
        )
        self._append_history(
            HistoryEventKind.MATERIAL_COMPOSED,
            payload={
                "edge_id": str(edge_id),
                "parent_archetype_id": str(parent_archetype_id),
                "child_archetype_id": str(child_archetype_id),
                "mode": attrs.mode.value,
            },
            now=now,
        )
        self._notify("compose_material")
        return edge

    def uncompose_material(self, edge_id: str) -> None:
        """Remove the named ``material_composition`` edge."""
        self._remove_material_edge(
            edge_id=edge_id,
            layer=LAYER_MATERIAL_COMPOSITION,
            kind=HistoryEventKind.MATERIAL_UNCOMPOSED,
            event_tag="uncompose_material",
        )

    # ------------------------------------------------------------------
    # Material helpers
    # ------------------------------------------------------------------
    def _remove_material_edge(
        self,
        *,
        edge_id: str,
        layer: str,
        kind: HistoryEventKind,
        event_tag: str,
    ) -> None:
        state = self.state
        bucket = state.edges.get(layer, [])
        for i, edge in enumerate(bucket):
            if edge.edge_id == edge_id:
                del bucket[i]
                write_json(
                    state.paths.edge_layer_path(layer),
                    EdgeLayerFile(layer=layer, edges=tuple(bucket)),
                )
                self._append_history(kind, payload={"edge_id": edge_id})
                self._notify(event_tag)
                return
        msg = f"unknown {layer} edge {edge_id!r}"
        raise UnknownMaterialEdgeError(msg)

    def _require_target_node(self, target_node_id: NodeId, scope: object) -> None:
        """Validate that ``target_node_id`` exists and matches ``scope``."""
        from forge_mcp.project.schemas import MaterialScope  # noqa: PLC0415 - cycle break

        state = self.state
        is_world = target_node_id == state.metadata.world_node_id
        is_region = RegionId(str(target_node_id)) in state.regions
        is_sub_region = SubRegionId(str(target_node_id)) in state.sub_regions
        if not (is_world or is_region or is_sub_region):
            msg = f"unknown application target node {target_node_id!r}"
            raise UnknownTargetNodeError(msg)
        if scope is MaterialScope.WORLD and not is_world:
            msg = (
                f"scope=world requires target=world_node_id "
                f"({state.metadata.world_node_id!r}), got {target_node_id!r}"
            )
            raise MaterialEdgePolicyError(msg)
        if scope is MaterialScope.REGION and not is_region:
            msg = f"scope=region requires target to be a region id, got {target_node_id!r}"
            raise MaterialEdgePolicyError(msg)
        if scope is MaterialScope.SUB_REGION and not is_sub_region:
            msg = f"scope=sub_region requires target to be a sub-region id, got {target_node_id!r}"
            raise MaterialEdgePolicyError(msg)

    def _composition_cycle_after_change(self) -> tuple[NodeId, ...] | None:
        from forge_mcp.hypergraph.core import Hypergraph  # noqa: PLC0415 - cycle break
        from forge_mcp.hypergraph.traversal import has_directed_cycle  # noqa: PLC0415

        hg = Hypergraph.from_project(self.state)
        return has_directed_cycle(hg, LAYER_MATERIAL_COMPOSITION)

    def _allocate_archetype_id(self, name: str) -> MaterialArchetypeId:
        """Return a fresh ``material_<slug>`` id, suffixing on collision."""
        base = _slugify(name) or "material"
        candidate = f"material_{base}"
        if MaterialArchetypeId(candidate) not in self.state.archetypes:
            return MaterialArchetypeId(candidate)
        for suffix_len in range(2, 9):
            for _ in range(16):
                token = uuid4().hex[:suffix_len]
                candidate = f"material_{base}_{token}"
                if MaterialArchetypeId(candidate) not in self.state.archetypes:
                    return MaterialArchetypeId(candidate)
        msg = f"could not allocate a unique archetype id for name {name!r}"
        raise ProjectError(msg)

    def _allocate_sub_region_id(self, name: str) -> SubRegionId:
        """Return a fresh ``subregion_<slug>`` id, suffixing on collision."""
        base = _slugify(name) or "sub_region"
        candidate = f"subregion_{base}"
        if SubRegionId(candidate) not in self.state.sub_regions:
            return SubRegionId(candidate)
        for suffix_len in range(2, 9):
            for _ in range(16):
                token = uuid4().hex[:suffix_len]
                candidate = f"subregion_{base}_{token}"
                if SubRegionId(candidate) not in self.state.sub_regions:
                    return SubRegionId(candidate)
        msg = f"could not allocate a unique sub-region id for name {name!r}"
        raise ProjectError(msg)

    def _allocate_environment_id(self, name: str) -> EnvironmentNodeId:
        """Return a fresh ``env_<slug>`` id, suffixing on collision."""
        base = _slugify(name) or "environment"
        candidate = f"env_{base}"
        if EnvironmentNodeId(candidate) not in self.state.environments:
            return EnvironmentNodeId(candidate)
        for suffix_len in range(2, 9):
            for _ in range(16):
                token = uuid4().hex[:suffix_len]
                candidate = f"env_{base}_{token}"
                if EnvironmentNodeId(candidate) not in self.state.environments:
                    return EnvironmentNodeId(candidate)
        msg = f"could not allocate a unique environment id for name {name!r}"
        raise ProjectError(msg)

    def _allocate_edge_id(self, layer: str, prefix: str) -> EdgeId:
        """Return a fresh, deterministic edge id within ``layer``."""
        existing = {edge.edge_id for edge in self.state.edges.get(layer, ())}
        for suffix_len in range(4, 13):
            for _ in range(16):
                token = uuid4().hex[:suffix_len]
                candidate = f"{prefix}_{token}"
                if candidate not in existing:
                    return EdgeId(candidate)
        msg = f"could not allocate a unique edge id in layer {layer!r}"
        raise ProjectError(msg)

    # ------------------------------------------------------------------
    # Internal: region id allocation
    # ------------------------------------------------------------------
    def _allocate_region_id(self, name: str) -> RegionId:
        """Return a fresh slug-based RegionId, suffixing on collision."""
        base = _slugify(name) or "region"
        candidate = f"region_{base}"
        if RegionId(candidate) not in self.state.regions:
            return RegionId(candidate)
        # Use a deterministic short hash on the existing-id count to keep
        # IDs predictable in tests; collisions are vanishingly rare in
        # Phase-2 workloads.
        for suffix_len in range(2, 9):
            for _ in range(16):
                token = uuid4().hex[:suffix_len]
                candidate = f"region_{base}_{token}"
                if RegionId(candidate) not in self.state.regions:
                    return RegionId(candidate)
        msg = f"could not allocate a unique region id for name {name!r}"
        raise ProjectError(msg)


_SLUG_RE: Final = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    """Lowercase + collapse non-alnum runs to single underscores; trim."""
    return _SLUG_RE.sub("_", value.lower()).strip("_")


class RegionError(ProjectError):
    """Base class for region-CRUD errors."""


class UnknownRegionError(RegionError):
    """Raised when a region id does not exist in the open project."""


class RegionPolygonError(RegionError):
    """Raised when a region polygon is malformed (delegates to geometry layer)."""


class RegionOverlapError(RegionError):
    """Raised when a region polygon overlaps an existing region."""


class UnknownSpecError(ProjectError):
    """Raised when a spec id does not exist on disk."""


class MaterialError(ProjectError):
    """Base class for material-CRUD errors."""


class UnknownArchetypeError(MaterialError):
    """Raised when a material archetype id does not exist."""


class ArchetypeInUseError(MaterialError):
    """Raised when deleting an archetype still referenced by a material edge."""


class UnknownTargetNodeError(MaterialError):
    """Raised when a material application targets an unknown node id."""


class UnknownMaterialEdgeError(MaterialError):
    """Raised when a material edge id does not exist in its layer."""


class MaterialEdgePolicyError(MaterialError):
    """Raised when a material edge violates the layer's endpoint or scope policy."""


class MaterialCompositionCycleError(MaterialError):
    """Raised when a composition edge would introduce a cycle."""


class SubRegionError(ProjectError):
    """Base class for sub-region-CRUD errors (Phase 6-c)."""


class UnknownSubRegionError(SubRegionError):
    """Raised when a sub-region id does not exist in the open project."""


class SubRegionInUseError(SubRegionError):
    """Raised when deleting a sub-region still referenced by a material edge."""


class UnknownParentRegionError(SubRegionError):
    """Raised when ``create_sub_region`` is called with an unknown parent region."""


class EnvironmentBindingError(ProjectError):
    """Base class for environment-CRUD/binding errors (Phase 6-f).

    Named with a ``Binding`` suffix to avoid shadowing the Python
    built-in :class:`EnvironmentError` (an alias for :class:`OSError`).
    """


class UnknownEnvironmentError(EnvironmentBindingError):
    """Raised when an environment id does not exist in the open project."""


class EnvironmentInUseError(EnvironmentBindingError):
    """Raised when deleting an environment still bound to a scope."""


class UnknownScopeError(EnvironmentBindingError):
    """Raised when a bind/unbind targets neither the world root nor a known region."""


class LockOperationError(ProjectError):
    """Base class for lock CRUD failures (Phase 7 Stage A)."""


class UnknownLockError(LockOperationError):
    """Raised when an operation references a ``lock_id`` that does not exist."""


class LockTargetNotFoundError(LockOperationError):
    """Raised when a property or feature lock targets a missing artefact.

    Examples: a ``json_path`` that does not resolve on the region, or a
    feature ``bbox_world`` that lies outside the region's heightmap (or
    where no heightmap has been generated yet).
    """


class OverlappingFeatureLockError(LockOperationError):
    """Raised when a new feature lock's bbox overlaps an existing one on the same region."""


__all__ = [
    "ArchetypeInUseError",
    "EnvironmentBindingError",
    "EnvironmentInUseError",
    "LockOperationError",
    "LockTargetNotFoundError",
    "MaterialCompositionCycleError",
    "MaterialEdgePolicyError",
    "MaterialError",
    "NoOpenProjectError",
    "OverlappingFeatureLockError",
    "ProjectAlreadyExistsError",
    "ProjectError",
    "ProjectFormatError",
    "ProjectNotFoundError",
    "ProjectPaths",
    "ProjectService",
    "ProjectState",
    "ProjectVersionError",
    "RegionError",
    "RegionOverlapError",
    "RegionPolygonError",
    "SubRegionError",
    "SubRegionInUseError",
    "UnknownArchetypeError",
    "UnknownEnvironmentError",
    "UnknownLockError",
    "UnknownMaterialEdgeError",
    "UnknownParentRegionError",
    "UnknownRegionError",
    "UnknownScopeError",
    "UnknownSpecError",
    "UnknownSubRegionError",
    "UnknownTargetNodeError",
]
