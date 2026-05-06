# Plan: Phase 6 — Boundary Contracts + Popup Canvas + Region-Extent Scaling

End state: PRD success §8.1 (seam test) passes — two adjacent regions with contrasting descriptors generate, their shared edge is visually plausible (no cliff, no gap, no z-fighting), and a stream that crosses the boundary flows continuously across both sides. The popup canvas page is reachable as a standalone browser tab and a VSCode webview, lets the user draw region polygons, and posts back as `create_region`/`update_region`. WebSocket update latency under 500 ms (NF-1.4). The Phase-5 elevation-band carry-over is fixed at the same time because both pieces share the same plumbing (region polygon extent → spec inputs).

> **Strictness rules persist.** ruff ALL + mypy strict + 90% branch coverage on `forge_mcp/`. Connection-map view ships as a *skeleton* in Phase 6 — live updates throughout the full Forge state graph land in Phase 7.

## Hard architectural constraint reminders
- Forge contains zero LLM calls. Canvas is presentational; tool invocations always go through the MCP server. The browser never bypasses the server (Architecture §15: "MCP server is the source of truth; canvas page and any agent client are presentational").
- The elevation-band fix is a **content/mapping** change, not an API redesign. `descriptor.terrain.elevation_band` stays user-overridable; the clamp is a per-archetype slope-plausibility ceiling that triggers either silent normalization (default-band path) or a structured error (explicit-override path that violates the ceiling).
- Boundary contracts are **deterministic** in v1: given two adjacent regions' specs, the elevation continuity contract and any stream anchors must be reproducible byte-for-byte (NF-2.1).

## Scope summary
- **Region-extent-aware elevation-band scaling** (folds in [`AGENT/follow_ups/phase5-elevation-band-scaling.md`](../../AGENT/follow_ups/phase5-elevation-band-scaling.md) end-to-end).
- **Boundary contract solver** in `forge_mcp/boundary/`: elevation-continuity negotiation along shared edges; stream crossings with anchor matching; conflict surfacing (lock-vs-contract, contract-vs-contract).
- **Generation pipeline integration**: Phase 3 `terrain.run` consumes `BoundaryConditions` (edge elevation profiles + stream anchor points) when a region's spec carries `boundary_requirements`. The Phase 3 stream injector consumes anchor-in/anchor-out endpoints.
- **Popup canvas server** (`forge_mcp/server/canvas_server.py`): FastAPI mini-app embedded in the MCP process; HTTP serves `canvas_page/`; WebSocket pushes canonical project-state snapshots; client posts back through MCP-tool-equivalent JSON endpoints.
- **Canvas frontend** (`forge_mcp/canvas_page/`): vanilla TS + Konva.js (polygon drawing) + d3-force (connection-map skeleton). No build pipeline beyond `tsc` + esbuild bundling; no React.
- **Two delivery modes**: standalone browser tab (primary; works for every MCP client) and VSCode webview (enhancement; tested with Claude Code in VSCode).
- **MCP tools**: extend `inspect_boundary` (Phase 2 stub graduates), add `list_boundaries` returning `BoundaryRecord[]` with full contracts, surface lock-vs-contract conflicts via structured error from `generate_region`/`update_region`.
- **Skill updates**: `forge.plan` gets a "multi-region adjacency" section + 2 worked examples; `forge.connect` gets canvas-aware traversal patterns.
- **Seam test rigging** (PRD §8.1): a deterministic pair-of-regions test fixture in `tests/integration/test_seam.py` that runs end-to-end against real Blender and asserts elevation continuity along the shared edge within tolerance.

## Out of scope for Phase 6 (do not scaffold)
- Locks (Phase 7). Conflict surfacing handles "lock present" as opaque state; the lock store stays Phase-2 minimal until Phase 7 makes it real.
- Reroll/undo flow updates beyond what's needed to keep boundaries consistent on regenerate (Phase 7).
- Connection-map *live* updates throughout every Forge state change (Phase 7). Phase 6 ships the connection-map *view* with one-shot snapshots on canvas open + on canvas-initiated change.
- Multiplayer/shared editing (PRD §4 explicit).
- Canvas authentication / auth tokens. v1 binds canvas server to `127.0.0.1` only; documented as the entire security model. (No remote-access story; agents that need cross-machine access run over MCP, not canvas.)
- Drag-to-resize / boolean polygon ops on canvas. v1 ships: draw polygon, name region, edit polygon vertex positions, delete region.
- Vegetation/asset overlay layers in the canvas (PRD §4 forbids).
- Audit verdict overlays in the canvas (Phase 5 ships verdicts; Phase 7+ may surface them in the connection map).

## Stage A — Region-extent-aware elevation-band scaling (carry-over fix)

**Why this stage is in Phase 6:** the carry-over note explicitly defers here because both the boundary solver and the elevation-band clamp need the same datum: the polygon's bounding-box extent threaded into `map_to_spec`. Implement the plumbing once.

1. **Plumb extent into `map_to_spec`**: extend the call site in `forge_mcp/server/tools/regions.py` (and wherever else descriptors flow into specs) to compute `RegionExtent(width_m, height_m, area_m2)` from the polygon and pass it as a new required argument: `map_to_spec(descriptor, region_extent)`. `region_extent` becomes part of the deterministic mapping contract — adding it bumps the `compiler_version` in `GenerationMetadata`.
2. **Per-archetype slope-plausibility ceiling table** in `forge_mcp/descriptor/terrain_profiles.py` (or wherever `TerrainProfile` lives). Per the carry-over note:
   - Cliff-tolerant: `alpine_peaks`, `canyon`, `coastal_cliffs`, `volcanic_cone` → 55°.
   - Standard: `alpine_valley`, `desert_mesa`, `boreal_lowland`, `marsh`, `river_valley` → 30°.
   - Gentle: `rolling_hills`, `plains`, `desert_dunes` → 25°.
   Stored as a `MAX_MEAN_SLOPE_DEG_BY_ARCHETYPE: Mapping[TerrainPrimary, float]` constant; CI test asserts the table is exhaustive.
3. **Two clamp behaviours** in `_resolve_elevation_band`:
   - *Default-band path* (descriptor leaves `elevation_band` unset): compute `max_band_meters = extent_m * tan(max_mean_slope_deg)`; clamp `default_elevation_band` height symmetrically around its midpoint to fit. Recorded as `conflicts_resolved: ["elevation_band_clamped_to_extent"]` in `GenerationMetadata` for traceability.
   - *Explicit-override path* (descriptor supplies `elevation_band`): if the override exceeds the ceiling, raise a structured `ElevationBandImplausibleError(field="terrain.elevation_band", region_extent_m, max_band_m, supplied_band)`. Surfaces through `create_region`/`update_region`/`generate_region` as a validation error with the offending field path.
4. **Tests** (in `tests/descriptor/test_map_to_spec.py`):
   - Archetype × {200 m, 1 km, 4 km} polygon matrix; assert resulting band stays within ceiling on the default-band path.
   - Property-style test: for each archetype × extent, run the noise stack on a 64² heightmap (cheap; deterministic) and assert `_slope_stats(...)` mean slope ≤ ceiling + 10° tolerance.
   - Override-path tests: explicit override within ceiling → pass; override above ceiling → `ElevationBandImplausibleError` with the right field/values.
   - Carry-over case: 200 m × 200 m alpine_valley default → mean slope ≤ 30° + tolerance (the original symptom).
5. **Eval-set refresh**: update `forge_mcp/skills/forge.plan/eval_set.json` entries that included `elevation_band` overrides on tiny polygons — either widen the polygon or remove the override. Skill byte-identity test (Phase 5) catches drift.
6. **Walkthrough patch**: bump the alpine-valley demo polygon in `docs/p5_sanity_walkthrough.md` from 200 m × 200 m to 4 km × 4 km. The Phase 5 sanity gate now goes green naturally on the next run; the audit verdict for the same descriptor flips `geometric_validity` to `pass`.
7. **Compiler-version bump**: `GenerationMetadata.compiler_version` ticks (e.g. `0.1.0 → 0.2.0`); CI drift check on spec golden files updated.

## Stage B — Boundary contract solver

Architecture §8 (referenced as "unchanged from v2.0 in mechanism"). Phase 2 already emits `BoundaryStub` records with `contract: None`. Phase 6 makes the contract real.

1. **Contract schema** in `forge_mcp/project/schemas.py`:
   - `ElevationContinuityContract(low_m, high_m, samples: tuple[float, ...], sample_spacing_m, tolerance_m)`. `samples` is a 1D heightmap profile along the shared edge, sampled at `sample_spacing_m` intervals; both adjacent regions' generators must produce edge profiles within `tolerance_m` of these samples.
   - `StreamCrossingContract(crossing_point: tuple[float, float], width_m, depth_m, flow_direction: tuple[float, float])`. Optional; present only when both regions have streams whose paths intersect the shared edge.
   - `BoundaryContract` discriminated union over the two kinds; `BoundaryRecord` replaces the `BoundaryStub.contract: None` field with `contract: BoundaryContract | None` (None still allowed during the negotiation window between adjacency detection and first generation).
2. **Negotiation algorithm** in `forge_mcp/boundary/contract.py`:
   - **Symmetry**: contract is computed from both regions' specs, NOT from one region's heightmap. Both sides see the same contract object; both feed it as a generator input.
   - **Elevation continuity**: take both regions' `elevation_band`, find their **overlap interval**; if disjoint, raise `BoundaryContractInfeasibleError(reason="elevation_bands_disjoint", region_a, region_b, band_a, band_b)`. From the overlap, sample `N = max(8, round(length_m / sample_spacing_m))` evenly-spaced target heights using a deterministic LCG seeded by `(region_a, region_b, length_m)` — symmetric across regions. `tolerance_m = 0.05 * (band_high - band_low)` (5% of band height; bounded ±2 m).
   - **Stream crossings**: for each region's `feature_injectors[stream]`, project the planned stream path onto the shared edge (the Phase 3 deterministic stream injector already produces a path). If both project to the same edge within `width_m` tolerance, emit a `StreamCrossingContract` with the midpoint and average width.
   - **Determinism gate**: contract dict round-trips byte-identical when computed in either order `(A, B)` or `(B, A)`. Tested with a property test.
3. **Generator integration** in `forge_mcp/generate/terrain.py`:
   - New `BoundaryConditions` input bag passed to `terrain.run(spec, seed, boundary_conditions=None)`. When present, after the noise stack runs, an **edge-conform pass** blends the heightmap toward the contract's edge samples within an `inland_falloff_m = max(20.0, length_m * 0.05)` band. The blend is a smoothstep falloff so the seam is C¹ continuous at the edge and falls away to pure noise inland.
   - Stream injector: when `boundary_conditions.stream_anchors` is non-empty, replace the Phase 3 deterministic anchor-in/anchor-out picker with the contract's anchors. The deterministic-edge fallback Phase 3 used remains the no-contract path.
4. **Conflict surfacing** in `forge_mcp/server/tools/generation.py`:
   - Lock-vs-contract: if a region has a feature lock (Phase 7 gives this real bite; Phase 6 keeps the surface) on heightmap data near the shared edge, the edge-conform pass would overwrite locked elevation. v1 surfaces a `BoundaryContractConflictError(reason="lock_overlaps_edge_band", region_id, lock_id, boundary_id)` with structured fields; the actual blend-back logic that resolves it ships in Phase 7. For Phase 6, lock store is empty in normal use, so this raises only in tests.
   - Contract-vs-contract: a region with two boundary contracts whose edge-bands overlap (e.g. two short adjacent neighbours) — the engine averages the targets in the overlap region; if averaging would violate either contract's tolerance, raise `BoundaryContractConflictError(reason="contract_overlap_violates_tolerance", boundary_a, boundary_b)`.
5. **Persistence**: `BoundaryRecord` serialized under `<project>/edges/spatial_adjacency.json` per Architecture §3 (already the Phase 2 home for `BoundaryStub`). Migration is in-place: Phase 6 reads existing stubs, fills `contract` on first regeneration of either adjacent region. Atomic write via `_io/atomic.py`.

## Stage C — Generation flow rewiring + tools

1. **Boundary contract refresh trigger**: when `create_region` or `update_region` produces or modifies an adjacency, the `ProjectService` immediately recomputes the contract for that boundary (cheap; pure spec data). Both adjacent specs' `boundary_requirements` lists update to reference the boundary id; the spec's content hash bumps because `boundary_requirements` is part of `SpecBody`.
2. **`generate_region`** now:
   - Loads contracts for every boundary the region participates in.
   - Builds `BoundaryConditions` from those contracts.
   - Calls `terrain.run(spec, seed, boundary_conditions)`.
   - If a sibling region across a boundary already has a realized heightmap, the contract is consumed *as-is* (the contract was negotiated symmetrically; no second-pass blending needed).
   - On generation success, asserts the realized heightmap's edge profile matches the contract within tolerance via a post-condition check; mismatch → structured error (signals a generator bug, not a user error).
3. **MCP tools**:
   - `forge.list_boundaries(region_id?)` returns `tuple[BoundaryRecord, ...]` with full contracts.
   - `forge.inspect_boundary(boundary_id)` returns `BoundaryRecord` plus computed metrics (`elevation_overlap_m`, `samples_count`, `has_stream_crossing`).
   - `forge.generate_region` failure modes extended: `boundary_contract_infeasible`, `boundary_contract_conflict`, `elevation_band_implausible`. Each has structured fields; surface stage tag `stage: "boundary_negotiation"` in addition to existing tags.
4. **Determinism extension to NF-2.1**: the determinism contract now includes `boundary_conditions` as part of the input tuple. Two regions generated in either order produce identical heightmaps when their adjacent neighbour's spec is identical.

## Stage D — Canvas server (HTTP + WebSocket)

1. **Process model**: FastAPI mini-app embedded in the MCP server process (Architecture §10: "Embedded HTTP server in Forge process"). Started lazily on first `forge.canvas_url()` MCP tool call (new tool); returns `http://127.0.0.1:<port>/`. Port chosen from a random free port in 49152–65535; persisted to `<project>/.forge/canvas.lock` for the duration of the project session.
2. **HTTP endpoints** (canvas server is a *thin* shim — it does not duplicate MCP tool logic; it delegates to the same `ProjectService` instances):
   - `GET /` — serves bundled `index.html` + assets from `forge_mcp/canvas_page/dist/`.
   - `GET /api/state` — full project snapshot: `{regions, boundaries, hypergraph_layers}`. Same shape WebSocket pushes.
   - `POST /api/regions` — body validates against the same Pydantic models `forge.create_region` accepts; calls into `ProjectService.create_region` directly. Identical validation, identical errors.
   - `PATCH /api/regions/{region_id}` → `update_region`.
   - `DELETE /api/regions/{region_id}` → `delete_region`.
   - `GET /api/canvas-state` — Phase 2 `forge.get_canvas_state` shape (already exists).
   - `GET /healthz` — for the smoke test.
3. **WebSocket** at `/ws`:
   - On connect: server sends one `{"type": "snapshot", "state": <full_state>}`.
   - On every `ProjectService` mutation (create_region/update_region/delete_region/save_project), server broadcasts `{"type": "patch", "ops": [...]}` to all connected clients. Patch is JSON-Patch RFC 6902 against the previous snapshot.
   - Phase 6 wires the broadcast for canvas-initiated mutations; **agent-initiated** mutations also broadcast. Tools-from-MCP path goes through `ProjectService` so the broadcast is automatic.
   - Heartbeat: client sends `{"type": "ping"}` every 30 s; server replies `{"type": "pong"}`. Stale connections (no ping in 90 s) closed.
   - **NF-1.4**: latency from `ProjectService` mutation completion to client receiving the patch under 500 ms — measured by an in-process timer in the integration test.
4. **Security model** (the entire security model):
   - Bind to `127.0.0.1` only. Document explicitly in `docs/canvas.md`.
   - No auth, no CSRF tokens. The HTTP server trusts everything on localhost. v1 acceptable per §4 ("no shared editing").
   - CORS: `Access-Control-Allow-Origin: *` because the VSCode webview origin is `vscode-webview://` and standalone tab is `http://127.0.0.1:<port>`. Documented.
5. **Lifecycle**: canvas server stops when the MCP server stops (FastAPI lifespan tied to MCP server lifespan). `<project>/.forge/canvas.lock` deleted on clean shutdown.
6. **Process module**: `forge_mcp/server/canvas_server.py` exposes `CanvasServer(project_service, host="127.0.0.1", port=0)` with `start()` / `stop()` async methods. MCP tool `forge.canvas_url()` returns the URL; tool `forge.canvas_status()` returns `{running, url, connected_clients}`.

## Stage E — Canvas frontend (`forge_mcp/canvas_page/`)

1. **Stack**:
   - Vanilla TypeScript (no React, no framework). `tsc --strict`.
   - Konva.js for polygon drawing on a 2D canvas (Architecture §10 explicit).
   - d3-force for connection-map layout (skeleton; Phase 7 lights up live updates).
   - esbuild for single-file bundling: `forge_mcp/canvas_page/src/main.ts` → `forge_mcp/canvas_page/dist/main.js`. No external CDN; everything bundled.
2. **Two views (tabs)**:
   - **Canvas tab**: pannable + zoomable Konva stage; tools to draw a new region polygon (click-to-add-vertex, Esc to cancel, double-click to close). On polygon close, modal asks for region name; submit → `POST /api/regions`. Existing regions render as filled polygons with name labels. Click region → opens edit panel (vertex drag + name edit + descriptor preview). Delete via panel button → `DELETE /api/regions/{id}`.
   - **Connection map tab**: nodes (regions) and edges (boundaries) laid out via d3-force. Layer toggle: `containment`, `spatial_adjacency`, `hydrology`. Phase 6 ships static layout — relayout on any state change; Phase 7 adds smooth transitions and per-event highlighting.
3. **State management**: minimal vanilla TS — single `ProjectStore` class holding the latest snapshot, applies JSON-Patch ops on `patch` messages, fires `state-changed` events. Konva and d3-force subscribe.
4. **Code generation from Pydantic** (Architecture §14 open question 3, recommended path): a build step (`scripts/canvas/generate_types.py`) reads `schemas/*.schema.json` and emits `forge_mcp/canvas_page/src/types.generated.ts`. CI verifies the generated file is in sync (no drift). Keeps polygon validation rules etc. shared between Python and TS.
5. **Build**: `make canvas-build` runs `tsc --noEmit` + esbuild bundle + types codegen; produces `forge_mcp/canvas_page/dist/`. Wheel ships the `dist/` outputs (built in CI). The canvas server serves them via FastAPI `StaticFiles`.
6. **Manual smoke checklist** in `docs/canvas.md`: open standalone tab, draw two polygons, observe both views update; same in VSCode webview.

## Stage F — VSCode webview delivery

1. **Discovery**: from inside Claude Code in VSCode, `forge.canvas_url()` returns the loopback URL. The user clicks/cmd-clicks; VSCode webview opens. No special integration required for v1 — VSCode webviews accept arbitrary localhost URLs in `iframe` mode.
2. **Webview-friendly content**: ensure no `Content-Security-Policy` headers block `frame-ancestors`; set `X-Frame-Options: ALLOWALL` on canvas server responses (acceptable since loopback only).
3. **Test pass**: load canvas in VSCode webview; verify drawing works; verify WebSocket connects.
4. **Documentation**: `docs/canvas.md` records the two delivery modes and the known limitation that webview WebSockets sometimes need an explicit `ws://127.0.0.1:<port>/ws` (vs origin-relative path).
5. **No webview-specific code path** in canvas frontend — same bundle works in both modes (PRD §13.1).

## Stage G — Skill content updates

1. **`forge.plan/SKILL.md`** — add a "Multi-region adjacency" section with two worked examples: (a) two adjacent regions with similar archetypes (alpine_valley + alpine_peaks); (b) contrasting archetypes with shared stream (alpine_valley + rolling_hills, stream crossing). Each example shows: descriptor for both regions; the implied `BoundaryRecord`; the agent's tool call sequence (`create_region` × 2, observe boundary auto-created via `list_boundaries`, then `generate_region` × 2). Pitfall: don't try to set `boundary_requirements` directly in the descriptor — they're auto-derived from adjacency.
2. **`forge.connect/SKILL.md`** — add canvas-aware traversal patterns: `list_boundaries(region_id)` to enumerate a region's neighbours; `inspect_boundary(boundary_id)` to surface contract details; how the connection-map view renders the layers. Note the canvas URL discovery via `forge.canvas_url()`.
3. **Skill version bumps**: both skills' versions tick to reflect the schema additions; CI byte-identity tests (Phase 5) re-pass after the embedded examples' descriptor JSON re-validates.

## Stage H — Tests + integration suite

1. **Unit tests** (CI):
   - `tests/descriptor/test_map_to_spec.py` — Stage A coverage matrix.
   - `tests/boundary/test_contract.py` — symmetry property test (round-trip in both orderings); infeasibility cases; tolerance-band math; sample-count correctness; deterministic LCG bit-identity.
   - `tests/boundary/test_adjacency.py` — extends Phase 2 adjacency tests with contract auto-derivation on create_region.
   - `tests/generate/test_terrain_with_contract.py` — `terrain.run` with synthetic `BoundaryConditions`; assert edge profile within tolerance; assert inland >> falloff_m unchanged from no-contract path.
   - `tests/server/test_canvas_server.py` — FastAPI TestClient: GET/POST/PATCH/DELETE round-trip; WebSocket connect + snapshot + patch broadcast (use `httpx.AsyncClient` + `pytest-asyncio`); 127.0.0.1-bind verified.
   - `tests/server/test_boundary_tools.py` — `list_boundaries`, `inspect_boundary` tool round-trip; failure modes structured.
   - `tests/canvas_page/test_types_codegen.py` — generated `types.generated.ts` matches a canonical golden.
2. **Integration tests** (local, gated on `FORGE_BLENDER_BIN` per Phase 4 convention):
   - `tests/integration/test_seam.py` — **PRD §8.1 seam test rigging.** Creates two adjacent 1 km × 1 km regions ("rugged alpine valley" + "rolling foothills"), generates both, opens both `.blend` files programmatically, samples a 64-point profile along the shared edge from each region's heightmap, asserts max diff ≤ contract `tolerance_m`. Runs end-to-end against Blender.
   - `tests/integration/test_stream_crossing.py` — same but with shared stream. Asserts stream geometry continuity at the crossing point (stream widths within ±10%, flow direction angle diff < 5°).
3. **Canvas frontend tests**: minimal — `tsc --noEmit` is the gate. v1 ships no JS unit tests (defer to Phase 8 if needed). Manual smoke checklist in `docs/canvas.md`.
4. **NF-1.4 latency test**: in `tests/server/test_canvas_server.py`, a parametrized test triggers `ProjectService.create_region`, measures wall-clock time until WebSocket client receives the patch; asserts ≤ 500 ms on dev hardware (with 100 ms safety margin in CI).
5. **Coverage**: 90% branch on `forge_mcp/boundary/`, `forge_mcp/server/canvas_server.py`, `forge_mcp/descriptor/` (carry-over fix). Canvas frontend code is excluded from Python coverage (different language).

## Stage I — Bench, docs, ROADMAP close-out

1. **Seam-test acceptance artifacts**: `docs/eval/phase6/seam/{transcript.md, region_a.png, region_b.png, edge_profile_diff.png, contract.json}` from a real run.
2. **Docs**:
   - `docs/canvas.md` (new) — canvas architecture, delivery modes, security model, build instructions, smoke checklist.
   - `docs/boundary_contracts.md` (new) — contract schema, negotiation algorithm, conflict modes, generator integration.
   - `docs/p6_verification_walkthrough.md` (new) — gate checklist for Phase 6.
   - `docs/p5_sanity_walkthrough.md` (extend) — note the polygon-extent fix and the now-passing `geometric_validity` verdict.
   - `docs/eval/phase6/README.md` — seam test acceptance + sample artifacts.
3. **Architecture appendix**: append "Phase 6 measurements" — observed seam tolerance, NF-1.4 latency on dev box, canvas bundle size, browser/webview compatibility matrix.
4. **`AGENT/dev_phases/phase6.md`** committed.
5. **`AGENT/ROADMAP.md`** Phase 6 marked complete in closing PR.
6. **Carry-over note** [`AGENT/follow_ups/phase5-elevation-band-scaling.md`](../../AGENT/follow_ups/phase5-elevation-band-scaling.md) — annotate with "Resolved in Phase 6 PR `region-extent-elevation-band-clamp`" and link to the merged PR.

## Step ordering and dependencies
- **A** (extent plumbing + clamp) is the prerequisite — Stage B's contract solver wants the same `RegionExtent` datum threaded through; do this first so it's reusable.
- **B** (contract solver) depends on A; pure-Python, no Blender; can run in parallel with D once A is merged.
- **C** (generation rewiring + tools) depends on B.
- **D** (canvas server) is largely independent of A/B/C — depends only on Phase 2 `ProjectService`. Can run in full parallel.
- **E** (canvas frontend) depends on D's HTTP + WebSocket contract being stable.
- **F** (VSCode webview) depends on E.
- **G** (skill updates) depends on B + C (boundary tools must exist) + D (canvas URL tool must exist).
- **H** (tests) interleaves with each stage; integration suite lands with C/F.
- **I** (docs + close-out) is last.

## Branches (one PR per concern; descriptive)
1. `region-extent-elevation-band-clamp` — Stage A (carry-over fix) + Stage A tests + walkthrough patch + eval-set refresh.
2. `boundary-contract-solver` — Stage B (schemas, contract.py, adjacency wiring) + unit tests.
3. `terrain-generator-boundary-conditions` — Stage C generator integration + `list_boundaries`/`inspect_boundary` tools + structured error surfacing.
4. `canvas-server-http-websocket` — Stage D + Stage H canvas-server unit tests + 127.0.0.1 binding.
5. `canvas-frontend-konva-d3` — Stage E + types codegen + Make target.
6. `canvas-vscode-webview-pass` — Stage F + manual smoke checklist + docs/canvas.md initial draft.
7. `multi-region-skill-updates` — Stage G (forge.plan + forge.connect updates).
8. `seam-test-and-stream-crossing` — Stage H integration tests (Blender-gated).
9. `phase6-bench-and-docs` — Stage I close-out (last; depends on 1–8).

## Relevant files (Phase 6 tree additions)
```
forge_mcp/
├── boundary/                          # Phase 2 had only adjacency; Phase 6 fills the rest
│   ├── __init__.py
│   ├── adjacency.py                   # extended: trigger contract recompute on edge change
│   ├── contract.py                    # NEW: negotiate elevation continuity + stream crossings
│   └── apply.py                       # NEW: BoundaryConditions construction for terrain.run
├── descriptor/
│   ├── map_to_spec.py                 # extended: region_extent param + clamp logic
│   └── terrain_profiles.py            # extended: MAX_MEAN_SLOPE_DEG_BY_ARCHETYPE
├── generate/
│   └── terrain.py                     # extended: boundary_conditions input + edge-conform pass
├── project/
│   └── schemas.py                     # extended: ElevationContinuityContract, StreamCrossingContract, BoundaryRecord
├── server/
│   ├── canvas_server.py               # NEW: FastAPI mini-app + WebSocket
│   └── tools/
│       ├── boundaries.py              # NEW: list_boundaries, inspect_boundary
│       ├── canvas.py                  # NEW: canvas_url, canvas_status
│       └── generation.py              # extended: structured error stages
├── skills/
│   ├── forge.plan/SKILL.md            # extended: multi-region adjacency
│   └── forge.connect/SKILL.md         # extended: canvas-aware traversal
└── canvas_page/                       # NEW
    ├── src/
    │   ├── main.ts
    │   ├── store.ts
    │   ├── canvas_view.ts
    │   ├── connection_map_view.ts
    │   └── types.generated.ts         # generated from schemas/
    ├── dist/                          # built by `make canvas-build`
    ├── index.html
    ├── styles.css
    └── package.json                   # tsc + esbuild deps
scripts/
└── canvas/
    └── generate_types.py              # JSON Schema → TS codegen
schemas/
├── boundary_record.schema.json        # NEW (generated)
└── boundary_contract.schema.json      # NEW (generated)
tests/
├── boundary/
│   ├── test_contract.py
│   └── test_adjacency.py              # extended
├── descriptor/test_map_to_spec.py     # extended (Stage A)
├── generate/test_terrain_with_contract.py
├── server/
│   ├── test_canvas_server.py
│   └── test_boundary_tools.py
├── canvas_page/test_types_codegen.py
└── integration/
    ├── test_seam.py
    └── test_stream_crossing.py
docs/
├── canvas.md
├── boundary_contracts.md
├── p6_verification_walkthrough.md
├── p5_sanity_walkthrough.md           # extended
└── eval/phase6/...
```

## Verification (Phase 6 gate)
1. All nine branches merged; CI green on `main`.
2. `pytest --cov=forge_mcp --cov-fail-under=90 --cov-branch` exits 0; band 90–95%.
3. `forge-schema-export --check` exits 0 (boundary contract schema published; descriptor schema unchanged).
4. `mypy` strict exits 0; no new ignores.
5. **Canvas TypeScript**: `tsc --noEmit --strict` passes; `make canvas-build` produces `dist/main.js`; bundle size ≤ 500 KB gzipped (canvas + connection map + d3-force + Konva).
6. **Types codegen drift check**: regenerated `types.generated.ts` matches committed copy byte-for-byte.
7. **NF-1.4 latency**: in-process WebSocket roundtrip ≤ 500 ms (with 100 ms CI margin).
8. **Carry-over fix verified**: rerun the Phase-5 sanity walkthrough on the bumped 4 km × 4 km region; audit verdict's `geometric_validity` flips from `warn` to `pass`. Recorded under `docs/eval/phase6/p5_carry_over/`.
9. **Local integration (Blender-gated)**: `FORGE_BLENDER_BIN=… bash scripts/run_integration.sh` exits 0; seam test asserts max edge-profile diff ≤ contract tolerance; stream crossing test asserts width/direction continuity within thresholds.
10. **Manual seam test (PRD §8.1)** — friendly tester draws two adjacent regions on the canvas (alpine valley + rolling foothills), generates both, rotates around the seam in Blender, reports "no cliff, no gap, no z-fighting". Recorded under `docs/eval/phase6/seam/`.
11. **Manual stream-crossing acceptance** — same tester draws a stream that crosses a boundary, observes continuous flow. Recorded.
12. **Canvas delivery**: standalone browser tab works; VSCode webview works in Claude Code. Smoke checklist in `docs/canvas.md` completed on at least one machine.
13. **Strictness audit**: zero new `# type: ignore` / `# noqa` without code+reason; canvas server binds to `127.0.0.1` only (CI grep guard); no LLM-client imports anywhere in `forge_mcp/`.
14. **Determinism extension**: regenerating both regions in either order produces byte-identical heightmaps (asserted in `tests/integration/test_seam.py`).

## Decisions baked in
- **Region-extent threading is the single new datum**; both Stage A (clamp) and Stage B (contract sample-count) reuse it. `RegionExtent` is a frozen `NamedTuple` in `forge_mcp/descriptor/`.
- **Per-archetype slope ceiling table is exhaustive**; CI test asserts every `TerrainPrimary` enum value has an entry.
- **Default-band clamp is silent** (recorded in `conflicts_resolved`); explicit-override clamp **fails loud** (`ElevationBandImplausibleError` with field path) — matches the carry-over note's prescription.
- **Contracts are deterministic and symmetric**; LCG seeded by `(region_a, region_b, length_m)` ensures both adjacency directions hash identically.
- **Edge-conform pass uses smoothstep falloff** (C¹ continuous) over `inland_falloff_m = max(20.0, length_m * 0.05)`. Constants documented in `boundary_contracts.md`.
- **Lock-vs-contract conflict is surfaced but not resolved in Phase 6**; resolution lands in Phase 7 alongside feature locks.
- **Canvas server binds to `127.0.0.1` only**; that is the entire security model. Documented.
- **Canvas frontend is vanilla TS + Konva + d3-force, esbuild-bundled**; no React, no Next.js, no CDN. Single bundle ≤ 500 KB gzipped.
- **Two delivery modes (standalone tab + VSCode webview) share one bundle**; no client-specific code.
- **Connection-map view ships as a skeleton** (one-shot snapshots); full live updates land in Phase 7.
- **Pydantic → TypeScript codegen** is the single source of truth for shared types; CI drift check.
- **Seam-test rigging is the PRD §8.1 acceptance fixture**; lives under `tests/integration/` and is Blender-gated.
- **Compiler-version bumps to `0.2.0`** (carry-over fix changes the determinism contract); spec golden files updated in the same PR as Stage A.

## Confirmed decisions (2026-05-06)
1. **Stream crossing geometry**: require alignment within tolerance; emit `BoundaryContractInfeasibleError(reason="stream_crossing_misaligned", angle_diff_deg, tolerance_deg)` otherwise. The plan skill gets a pitfall entry teaching the agent to either align stream descriptors or omit one side. Tolerance: angle diff ≤ 30° between projected stream paths at the shared edge; width ratio within 2×.
2. **Connection-map layer scope**: Phase 6 ships **only `spatial_adjacency`** static (matches seam-test focus). Layer-toggle UI + `containment` + `hydrology` layers move to Phase 7 alongside live updates. Phase 6 skill content (`forge.connect`) accordingly mentions only the adjacency layer in canvas context.
3. **Canvas bundle distribution**: ship pre-built `dist/` inside the wheel (built in CI). Wheel adds the `forge_mcp/canvas_page/dist/` artifacts; CI gate refuses the build if `dist/` is stale relative to `src/`. End users need zero Node.js. Repo `.gitignore` excludes `dist/` from commits (CI is the single producer).

## Open questions to confirm with user
1. **Stream crossing geometry**: when adjacent regions both have streams approaching the shared edge from non-aligned angles, do we (a) require the planner to align them within tolerance and fail loud otherwise (recommended; pushes intent clarification back to the agent), or (b) let the contract auto-bend each side toward the midpoint (more permissive but introduces non-deterministic "best-fit" math).
2. **Connection-map layer toggles in Phase 6 skeleton**: ship all three layers (`containment`, `spatial_adjacency`, `hydrology`) static, or ship only `spatial_adjacency` for the seam-test focus and add the others in Phase 7? (Recommended: all three static; the d3-force layout is the same code regardless of which layer is rendered, so the cost is one extra UI toggle.)
3. **Canvas bundle distribution**: ship pre-built `dist/` in the wheel (recommended; zero Node.js dependency for end users) vs. require `npm install && npm run build` post-install (smaller wheel, but adds a Node toolchain prerequisite).
