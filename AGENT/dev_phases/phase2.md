# Plan: Phase 2 — Project Format + MCP Tool Scaffolding

Lay down Forge's persistent data model and the MCP tools that operate on it. End state: a user can hand-edit a Forge project on disk, open it from an agent, list/create/update/delete regions, and watch JSON files appear under the right folders — with adjacency boundaries auto-stubbed, polygon overlap rejected, history appended, and `get_descriptor_schema` returning the live Phase 1 schema. No generation, no Blender, no LLM. Phase 2 is the "skeleton that all later phases hang flesh onto."

> **Strictness mandate carries over.** `ruff select=ALL` + `mypy strict` + `disallow_any_explicit` + 90% branch-coverage floor go live this phase. Schemas are auto-generated and CI-verified per `.github/instructions.md` §5. No new ignore entries; no `# type: ignore` without code; no `ignore_missing_imports`. Pydantic-related Phase-1-style scoped ignores (`type: ignore[explicit-any]`) are tolerated only at model class declarations, with the same inline reason.

## Scope summary
- Pydantic v2 models for: `project.json`, region nodes, edges (per layer), specs, boundaries, locks, history events, audits.
- `ProjectService` — create/open/save/close, atomic writes, folder-layout owner.
- In-memory multi-layer hypergraph + JSON serialization (containment, adjacency, hydrology layers).
- Append-only history log + `undo()` *stub* (full replay is Phase 7).
- `LockStore` *stub* — load/persist `locks.json`, list_locks; full lock semantics (apply/conflict) deferred to Phase 7.
- MCP tools: project tools, region CRUD, `get_descriptor_schema` (rewired to live module), basic hypergraph queries, history tools (`undo`/`history`).
- Polygon non-overlap validation (F-6.5).
- Adjacency auto-detection on region create/update → emits boundary stub records (F-6.6); contract math arrives Phase 6.
- `forge-schema-export` CLI command + CI drift check covering every published schema.
- Golden-file unit tests for every JSON shape; round-trip tests; CI coverage floor turned on.

## Out of scope for Phase 2 (do not scaffold)
- Descriptor → spec mapping / `TERRAIN_PROFILES` (Phase 3).
- Terrain generator, `analyze_region`, `generate_region` execution (Phase 3 / Phase 4).
- Blender realizer wiring (Phase 4).
- Skills authoring (Phase 5).
- Boundary contract math (Phase 6) — Phase 2 only emits empty contract stubs.
- Lock *application*, feature locks, conflict surfacing (Phase 7) — Phase 2 only persists the JSON store.
- Full `undo` replay logic (Phase 7) — Phase 2 ships a stub that errors with `NotImplementedError("Phase 7")`.
- Canvas server, WebSocket, popup page (Phase 6).
- `cache/descriptor_compile/` — explicitly deleted in PRD v3.0.

## Stage A — Dependencies + project bootstrapping
1. **Add runtime deps** via `uv add`:
   - `shapely>=2.0` — polygon validity, intersection, adjacency detection. Ships C extensions; type stubs are partial. We commit a thin local stub under `forge_mcp/_stubs/shapely/` for the surface we use (`Polygon`, `box`, `intersects`, `intersection`, `boundary`, `length`), and add `mypy_path = ["forge_mcp/_stubs"]` to `pyproject.toml`. **No `ignore_missing_imports`.**
   - `pydantic>=2.7` — already present from Phase 1.
3. **CI updates** in `.github/workflows/ci.yml`:
   - Append `--cov-fail-under=90` to the pytest invocation (was deferred from Phase 0).
   - Add a `schema-drift` step: `uv run forge-schema-export --check` must pass.

## Stage B — Pydantic schema layer (`forge_mcp/project/schemas.py`)
One module owns every Pydantic model that touches disk. Models are frozen; equality is structural; `extra="forbid"` everywhere; JSON output uses sorted keys + 2-space indent + trailing newline (write-time helper `dump_json(model)` enforces this for git-diff stability).

1. **Identifiers + primitives.**
   - `RegionId = NewType("RegionId", str)`, `SpecId = NewType("SpecId", str)`, `BoundaryId`, `LockId`, `HistoryEventId`, `NodeId`, `EdgeId`. NewTypes give us mypy strictness without runtime cost.
   - `Polygon2D(BaseModel)` — `coords: tuple[tuple[float, float], ...]`, with validators: ≥3 points, no self-intersection (delegated to shapely), CCW canonicalized at construction (so equality is canonical).
   - `Bounds2D(BaseModel)` — `min: tuple[float,float]`, `max: tuple[float,float]`.
   - `WorldBounds(BaseModel)` — `kind: Literal["rectangle"]`, `min`, `max`, `units: Literal["meters"]`.
   - `SpatialBounds(BaseModel)` — `kind: Literal["polygon"]`, `coords`, `elevation_range: tuple[float,float] | None`.
2. **`ProjectMetadata`** (`project.json`) — match Architecture §3.1 verbatim:
   - `project_id: UUID`, `name: str`, `forge_version: str`, `blender_version: str`, `bpy_hypergraph_version: str`, `descriptor_schema_version: str`, `created_at`, `modified_at` (timezone-aware UTC), `world_node_id: NodeId`, `registered_layers: tuple[str, ...]` (default `("spatial_containment", "spatial_adjacency", "hydrology")`), `world_bounds: WorldBounds`.
3. **`RegionNode`** — Architecture §3.2:
   - `node_id: RegionId`, `kind: Literal["region"]`, `tier: Literal["unique", ...] = "unique"` (Phase 2 only "unique" is allowed; broader enum lives in v2), `scale_level: int = 2`, `parent_node: NodeId`, `children: tuple[NodeId, ...] = ()`, `name: str`, `spec_id: SpecId | None = None` (None until generation), `spatial_bounds: SpatialBounds`, `tags: tuple[str, ...] = ()`, `seed: int`, `created_at`, `modified_at`, plus a Phase-2 addition: `structured_descriptor: StructuredDescriptor | None` (the descriptor is supplied by the agent on `create_region`/`update_region` per F-7.3 — even though map-to-spec runs in Phase 3, persistence has to start now).
4. **`Edge`** — generic record per layer:
   - `edge_id: EdgeId`, `layer: str`, `endpoints: tuple[NodeId, ...]` (hyperedges allowed, hence tuple not pair), `directed: bool = False`, `attrs: Mapping[str, JsonValue] = {}`, timestamps.
   - Layer files (`edges/spatial_containment.json` etc.) hold a top-level object `{"layer": ..., "edges": [Edge, ...]}` for diff stability.
5. **`SpecRecord` *placeholder***: Phase 2 persists spec records *only* if the agent supplies a pre-built one (rare). For the common path, regions have `spec_id = None` until Phase 3. The model + `specs/{spec_id}.json` writer is implemented (so Phase 3 only needs to fill the spec body), but the `axes`/`generation_metadata` substructure is left as a `Mapping[str, JsonValue]` blob keyed by the spec's content hash. Architecture §3.4 details land in Phase 3.
6. **`BoundaryStub`** — Phase 2 emits these on adjacency detection:
   - `boundary_id: BoundaryId`, `kind: Literal["adjacency"]`, `region_a: RegionId`, `region_b: RegionId`, `shared_edge: tuple[tuple[float,float], tuple[float,float]]` (segment endpoints), `length_meters: float`, `contract: None` (filled Phase 6), timestamps.
7. **`LockRecord`** — store-only Phase 2 shape:
   - `lock_id: LockId`, `region_id: RegionId`, `kind: Literal["property", "feature", "region"]`, `payload: Mapping[str, JsonValue]`, timestamps.
   - File: `locks/locks.json` as `{"locks": [LockRecord, ...]}`.
8. **`HistoryEvent`** — append-only:
   - `event_id: HistoryEventId` (zero-padded sequence "0001", "0002"...), `kind: Literal["create_project", "create_region", "update_region", "delete_region", "save_project", ...]`, `at: datetime`, `actor: Literal["agent", "user", "system"]`, `payload: Mapping[str, JsonValue]`.
   - One file per event: `history/{event_id}_{kind}.json`. Sequence numbers are monotonic per project; gaps are forbidden (CI test).
9. **`AuditRecord`** — placeholder model so the folder exists; populated by Phase 5.
10. **`JsonValue`** — recursive `str | int | float | bool | None | tuple[JsonValue, ...] | Mapping[str, JsonValue]`. Centralized in `forge_mcp/_types.py` (already partially defined at Phase 1's RPC boundary; consolidate here).
11. **Schema export.** `forge_mcp/project/schema_export.py` provides `iter_published_schemas() -> Iterable[tuple[str, dict[str, object]]]`, yielding pairs (`name`, JSON-Schema). Phase 2 publishes:
    - `descriptor.schema.json` (already committed; rerun for drift)
    - `project.schema.json`
    - `region.schema.json`
    - `edge.schema.json`
    - `boundary.schema.json`
    - `lock.schema.json`
    - `history_event.schema.json`
    Output directory: `schemas/` at repo root (not inside the package — this is the *published* surface).
12. **CLI** `forge-schema-export` declared in `[project.scripts]`. Modes: `--write` (overwrite committed files) and `--check` (exit non-zero on diff). `--check` runs in CI per `.github/instructions.md` §5.

## Stage C — `ProjectService` + folder layout (`forge_mcp/project/service.py`)
1. **`ProjectPaths`** dataclass — given a project root, exposes typed `Path` properties for every subdirectory (`nodes_dir`, `regions_dir`, `edges_dir`, `specs_dir`, `boundaries_dir`, `locks_path`, `history_dir`, `realizations_dir`, `audits_dir`, `gitignore_path`, `metadata_path`).
2. **`ProjectService`** — singleton-per-process state (the MCP server holds at most one open project at a time in v1):
   - `create_project(name, world_bounds) -> ProjectMetadata` — creates the directory tree, writes `project.json`, writes a baseline `.gitignore` listing `realizations/`, writes the `world_root` containment-graph node (a synthetic `NodeBase` record under `nodes/world.json`), seeds empty edge-layer files for each registered layer, appends a `create_project` history event.
   - `open_project(path) -> ProjectMetadata` — validates folder layout, refuses to open if `forge_version` < ours (forward-compat), refuses if `descriptor_schema_version` is unknown, hydrates an in-memory `ProjectState`.
   - `save_project()` — flushes any pending in-memory mutations atomically (see below). Idempotent.
   - `close_project()` — flushes + drops in-memory state.
3. **Atomic write helper** `forge_mcp/_io/atomic.py` — `atomic_write_text(path, data)` writes to `path.with_suffix(path.suffix + ".tmp.<pid>.<rand>")` then `os.replace`. Per NF-3.1 + `.github/instructions.md` §6. Single chokepoint; every JSON write goes through it.
4. **JSON dumper** `dump_json(model_or_dict) -> str` — `json.dumps(..., indent=2, sort_keys=True, separators=(",", ": "), ensure_ascii=False)` + trailing newline. Pydantic models go through `model.model_dump(mode="json")` first.
5. **`ProjectState`** in-memory cache:
   - `metadata: ProjectMetadata`
   - `regions: dict[RegionId, RegionNode]`
   - `boundaries: dict[BoundaryId, BoundaryStub]`
   - `edges: dict[str, list[Edge]]` keyed by layer name
   - `locks: list[LockRecord]`
   - `history_count: int`
   The state is rebuilt on `open_project` by walking the folder; mutations go through service methods that update both memory and disk.

## Stage D — Hypergraph in-memory representation (`forge_mcp/hypergraph/`)
New subpackage. Architecture §1 and §15 commit to "multilayer from day 1."

1. `core.py` — `Hypergraph` class:
   - `nodes: dict[NodeId, NodeRecord]` (where `NodeRecord` is the union of `RegionNode | WorldRootNode | ...` — Phase 2 has just region + world-root).
   - `layers: dict[str, LayerView]` where `LayerView` exposes `add_edge`, `remove_edge`, `edges_for(node)`, `neighbors(node)`. Layers are independent; the same pair of nodes can appear in multiple layers.
   - `from_project(state: ProjectState) -> Hypergraph` reconstructs from the in-memory state.
   - `to_persistence(hg) -> Iterable[(Path, str)]` returns the JSON files to write back (delegated through `ProjectService`).
2. `traversal.py` — Phase-2 minimum query API:
   - `query_layer(layer, root_node=None, depth=None, filter=None) -> Iterable[NodeId]` — BFS with optional depth + predicate.
   - `list_boundaries() -> list[BoundaryId]`, `inspect_boundary(boundary_id) -> BoundaryStub`.
3. **No graph library dependency.** networkx is overkill for ~hundreds of nodes and would cost typing pain. Hand-rolled stays strict-typed and small.

## Stage E — Polygon validation + adjacency detection (`forge_mcp/geometry/`)
New subpackage hiding shapely behind a typed facade.

1. `polygon.py`:
   - `validate_polygon(coords)` — shapely `Polygon(coords).is_valid`, area > 0, ≥3 distinct points; raises `PolygonInvalidError` with structured detail.
   - `polygons_overlap(a, b) -> bool` — shapely `intersection(a, b).area > eps`. (Shared edges are not overlap; only positive-area intersection counts.)
   - `shared_edge(a, b) -> Segment | None` — returns the shared boundary segment (longest connected portion of the polygon-boundary intersection), or None if non-adjacent.
2. `adjacency.py`:
   - `detect_adjacencies(new_region, all_regions) -> list[BoundaryStub]` — for every existing region, compute `shared_edge`; if it exists, emit a boundary stub. Pure function; `ProjectService` calls it on `create_region`/`update_region` and persists the result.
   - Returned stubs carry `length_meters` (sum of segment lengths); contract field stays `None` for Phase 2.
3. **Determinism**: outputs sorted by `(region_a, region_b)` lex order to keep diffs stable.

## Stage F — History + lock store (Phase 2 surface only)
1. `forge_mcp/project/history.py`:
   - `HistoryLog` — append-only writer. `append(event)` writes one file under `history/{event_id}_{kind}.json` atomically; updates `state.history_count`.
   - `iter_events(reverse=False, limit=None)` — disk-backed iterator (Phase 7's `undo` will replay via this).
   - `undo()` stub — raises `NotImplementedError("Phase 7")` *but* is registered as an MCP tool returning a structured error so the agent can discover the surface today (matches Architecture's tool list).
2. `forge_mcp/project/locks.py`:
   - `LockStore` — load/persist `locks/locks.json`. Phase 2 implements `list_locks(region_id=None)`, `add_lock`, `remove_lock` — so the file is honest about what's there. **No** lock *application* (that's Phase 7); `ProjectService` does not consult the lock store during region mutation in Phase 2.
   - The MCP tool surface for lock add/remove is **not** exposed in Phase 2 (per ROADMAP — locks are Phase 7). Only `list_locks` is exposed (read-only) to mirror the read API.

## Stage G — MCP tool surface (`forge_mcp/server/tools/`)
Each tool group gets its own module; they're registered into the existing FastMCP server in `forge_mcp/server/mcp.py`. Every tool input is a Pydantic model (FastMCP supports this directly); every output is a typed dict or Pydantic model.

1. `projects.py`:
   - `forge.create_project(name, world_bounds)`
   - `forge.open_project(path)`
   - `forge.save_project()`
   - `forge.close_project()`
2. `regions.py`:
   - `forge.create_region(name, polygon_coords, structured_descriptor=None, seed=None)` — generates `RegionId` (slugified name + 6-char hash suffix on collision), validates polygon, rejects on overlap (returns structured error not exception), runs adjacency detection, persists, appends history.
   - `forge.update_region(region_id, fields)` — partial update; revalidates polygon if changed; re-runs adjacency if polygon changed; appends history.
   - `forge.delete_region(region_id)` — removes region + any boundaries it participates in; appends history.
   - `forge.list_regions()` → list of region summaries.
   - `forge.get_region(region_id)` → full region.
3. `schema.py`:
   - `forge.get_descriptor_schema()` — *rewire* the Phase-1 placeholder to call `forge_mcp.descriptor.descriptor_json_schema()` directly (no try/except fallback). The Phase-1 fallback exists only because the descriptor module lived on a sibling branch.
4. `hypergraph.py`:
   - `forge.query_layer(layer, root_node=None, depth=None)`
   - `forge.list_boundaries()`
   - `forge.inspect_boundary(boundary_id)`
5. `history.py`:
   - `forge.history(limit=None)` — read-only.
   - `forge.undo()` — returns structured "not implemented in Phase 2" error (see Stage F).
6. `inspection.py` — `forge.list_locks(region_id=None)` only. (`lock_property`/`lock_feature`/`lock_region`/`unlock` deferred to Phase 7.)
7. **Generation tools (`generate_region`, `reroll_seed`, `analyze_region`, `render_view`)** — **not registered** in Phase 2 to keep the tool surface honest. Placeholder modules under `tools/` would violate `.github/instructions.md` §1 ("No premature scaffolding").

## Stage H — Tests + golden files (`tests/`)
Coverage floor goes live: 90% branch coverage on `forge_mcp/`. Mirror the source tree.

1. `tests/project/test_schemas.py` — every model: round-trip JSON, `extra="forbid"` rejects extra keys, frozen-ness asserted. Golden JSON files under `tests/fixtures/golden/` for: `project.json`, a region, an edge file per layer, a boundary stub, a lock record, a history event. Drift-check loads each fixture, parses to model, dumps via `dump_json`, asserts byte-equal.
2. `tests/project/test_service.py` — `create_project` produces the documented folder tree; reopening yields equal `ProjectMetadata`; concurrent writers fail safely (atomic write test using `pytest`'s `tmp_path` + a `monkeypatch`-induced crash mid-write to verify no half-written file remains).
3. `tests/project/test_history.py` — append produces monotonic gap-free sequence; `iter_events` order is deterministic.
4. `tests/geometry/test_polygon.py` — invalid polygons rejected with structured error; CCW canonicalization; `polygons_overlap` true-positive + true-negative + edge-touch (which is *not* overlap).
5. `tests/geometry/test_adjacency.py` — adjacency detection with: two squares sharing an edge → stub emitted; two squares sharing a corner only → no stub; two non-touching → no stub; three regions in an L → two stubs; deterministic ordering.
6. `tests/hypergraph/test_core.py` — multi-layer add/remove; `from_project` round-trip; `query_layer` BFS with depth + filter.
7. `tests/server/test_tools.py` — in-process tool invocation per the Phase 1 pattern; success + structured-error paths for region overlap, malformed polygon, malformed descriptor (delegates to descriptor.validate), unknown region_id, and `undo` returning the Phase-2 "not implemented" structured error. All MCP tools tested.
8. `tests/server/test_schema_export.py` — runs `forge-schema-export --check` against the committed `schemas/` and asserts zero diff.
9. **Coverage**: target 90–95%; defensive `match` exhaustiveness branches use `# pragma: no cover  # exhaustiveness` per `.github/instructions.md` §3.

## Stage I — Documentation updates
1. `README.md` — add a "Project format" subsection pointing at Architecture §3 and listing the published schemas.
2. `docs/project_format.md` (new) — short hand-author walkthrough so a developer can build a tiny project by hand and prove `open_project` ingests it. Verifies the format is genuinely git-friendly + agent-inspectable per PRD §6.5.
3. `AGENT/dev_phases/phase2.md` — committed plan (this document).
4. `AGENT/ROADMAP.md` — mark Phase 2 complete in the same PR that closes the phase (not before).

## Step ordering and dependencies
- Stage A (deps + CI) must land first to unblock everything.
- Stages B (schemas) and Stage E.1 (polygon validation) can land in parallel; Stage B is the more critical critical-path item.
- Stage C (`ProjectService`) depends on B + E.1 + atomic-write helper (E.1 has no deps).
- Stage D (hypergraph) depends on B.
- Stage E.2 (adjacency) depends on B + E.1.
- Stage F (history + lock store) depends on B + atomic-write helper.
- Stage G (MCP tools) depends on B, C, D, E, F.
- Stage H (tests) interleaves with each stage; coverage gate enabled in Stage A but only enforces once enough source lands.
- Stage I (docs) closes the phase.

## Branches (one PR per stage where useful, per phase 1 convention)
Descriptive names, no "phase" or "stage" prefix:
- `pydantic-project-schemas` — Stages A + B
- `project-service-and-atomic-io` — Stage C
- `multilayer-hypergraph-core` — Stage D
- `polygon-validation-and-adjacency` — Stage E
- `history-log-and-lock-store` — Stage F
- `mcp-tool-surface-v1` — Stage G + Stage H integration tests + Stage I
Stage A's CI changes can ride on the first PR; subsequent PRs inherit the coverage floor.

## Relevant files (final Phase 2 tree additions)
```
forge_mcp/
├── _io/
│   ├── __init__.py
│   └── atomic.py
├── _stubs/
│   └── shapely/                 # local stubs for the surface we use
├── _types.py                    # JsonValue alias consolidated here
├── geometry/
│   ├── __init__.py
│   ├── polygon.py
│   └── adjacency.py
├── hypergraph/
│   ├── __init__.py
│   ├── core.py
│   └── traversal.py
├── project/
│   ├── __init__.py
│   ├── schemas.py
│   ├── schema_export.py
│   ├── service.py
│   ├── history.py
│   └── locks.py
├── server/
│   ├── mcp.py                   # extended to register tool groups
│   └── tools/
│       ├── __init__.py
│       ├── projects.py
│       ├── regions.py
│       ├── schema.py
│       ├── hypergraph.py
│       ├── history.py
│       └── inspection.py
schemas/                          # NEW — generated, CI-verified
├── descriptor.schema.json
├── project.schema.json
├── region.schema.json
├── edge.schema.json
├── boundary.schema.json
├── lock.schema.json
└── history_event.schema.json
docs/
└── project_format.md
tests/
├── fixtures/golden/...
├── project/...
├── geometry/...
├── hypergraph/...
└── server/test_tools.py, test_schema_export.py
```

## Verification (Phase 2 gate)
1. CI green on `main` after `mcp-tool-surface-v1` merges; all six PRs landed cleanly.
2. `uv run pytest --cov=forge_mcp --cov-fail-under=90 --cov-branch` exits 0; coverage in 90–95% band.
3. `uv run forge-schema-export --check` exits 0 — every committed schema matches its Pydantic source.
4. `uv run mypy` exits 0 with strict + extras; zero new entries in any ignore list; zero `# type: ignore` without code.
5. `uv run ruff check .` and `uv run ruff format --check .` exit 0.
6. **Hand-author smoke test (manual, per `docs/project_format.md`):** copy the documented example tree into a tmp dir, run `forge-mcp`, ask Claude Code to call `forge.open_project` then `forge.list_regions` — expect the documented region appears.
7. **MCP from Claude Code (manual):** create project → create two adjacent square regions → `forge.list_boundaries` returns one stub → `forge.inspect_boundary` returns shared edge endpoints + length. Project directory diffs cleanly in git (sorted keys, no whitespace noise).
8. **Polygon overlap test:** attempt to create a region overlapping an existing one — receive a structured error, no partial write to disk.
9. **Determinism**: same sequence of tool calls on a clean project yields byte-identical files (including history sequence). Tested.
10. **Atomic-write crash test (automated):** `monkeypatch.setattr(os, "replace", boom)` mid-save; assert no `.tmp.*` orphan persists and original file is untouched.

## Decisions baked in
- **Geometry library**: shapely 2.x with a thin local stub package (`forge_mcp/_stubs/shapely/`) and `mypy_path` pointed at it. Honors the no-`ignore_missing_imports` rule.
- **Region IDs**: slugify(name) + collision suffix `-NN` if needed. Stable, human-readable, matches Architecture §3.2 examples (`region_alpheim_north`).
- **Spec IDs**: content-addressable, `spec_<first-6-of-blake2b-hex>`. Generation hashes the spec body; Phase 2 does not generate specs but the helper exists.
- **History event IDs**: zero-padded 4-digit decimal (`"0001"`) — fine until ~10k events; format documented as v1-only.
- **Hypergraph implementation**: hand-rolled, no networkx dep.
- **In-process state**: at most one open project per server process, mirroring v1's solo-developer assumption.
- **JSON style**: `indent=2`, `sort_keys=True`, `ensure_ascii=False`, trailing newline. Locked in `dump_json`.
- **Atomic writes**: `path.tmp.<pid>.<rand>` then `os.replace`. Single chokepoint.
- **Time**: timezone-aware UTC `datetime` everywhere, ISO 8601 strings on disk.
- **Schema export**: `schemas/` at repo root (published surface), regenerated by CLI, CI-checked.
- **Generation tools deliberately not registered** in Phase 2 to honor "no premature scaffolding."
- **Lock tools (write side) deliberately not registered** in Phase 2; only `list_locks` is exposed.
- **`undo` registered as a tool returning a structured "not implemented in Phase 2" error**, matching the published tool surface without faking behavior.

## Cross-cutting reminders (apply throughout)
- Every JSON write goes through `atomic_write_text` + `dump_json`. No `pathlib.Path.write_text` in production code paths.
- Every public function/class gets a Google-style docstring (`.github/instructions.md` §4).
- Every Pydantic model that exports to JSON Schema is wired into `iter_published_schemas`.
- Every new dep added with `uv add` *must* either ship type info or get a local stub — never `ignore_missing_imports`.
- Determinism applies to file output: sorted keys, sorted lists where ordering is semantically irrelevant, deterministic ID generation given inputs.

## Confirmed decisions (2026-04-30)
1. **Time source**: real `datetime.now(UTC)` in production code; `freezegun` (added as a dev dep) for deterministic test fixtures. No `Clock` protocol injection.
2. **Region IDs**: slugified name + `-NN` collision suffix.
3. **Generated schemas**: live at repo root `/schemas/`.

## Open questions to confirm with user
1. **Time source for IDs / `created_at`**: real `datetime.now(UTC)` (test via `freezegun`-style fixture) vs an injectable `Clock` protocol from day 1 (slightly more boilerplate, much easier deterministic tests). Recommend Option B.
2. **Region-ID strategy**: slug+collision-suffix (recommended; matches Architecture examples, human-readable) vs UUID (simpler; less readable).
3. **`schemas/` directory location**: repo root `/schemas/` (public surface, easy to link from README) vs `forge_mcp/schemas/` (ships with the package). Recommend root + a symlink/loader if package access ever becomes needed; v1 has no need to read its own schemas at runtime.
