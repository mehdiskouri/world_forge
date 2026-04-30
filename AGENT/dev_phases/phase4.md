# Plan: Phase 4 — Blender 5.0 Realizer

End state: `forge.generate_region` runs the full Architecture §4.1 plan/realize loop end-to-end. Heightmap (Phase 3) → curated bpy hypergraph sequence in a long-lived headless Blender 5.0.0 → `.blend` saved + 1024×768 PNG previews (ortho_top + perspective_se) returned via MCP image content under 60 s end-to-end and under 200 KB encoded. IDProperties link Blender objects back to project node IDs (per Phase 1 verdict). New `forge.render_view` tool. The Blender adapter, RPC client, process manager, and bpy hypergraph data already exist from Phase 1; this phase wires the engine, implements the v1 macros, and threads everything through `generate_region`.

> **Strictness rules persist.** `ruff ALL` + `mypy strict` + `disallow_any_explicit` + 90% coverage. Adapter script under `scripts/blender/` keeps its existing carve-out (separate mypy step with `fake-bpy-module-*` stubs); host-side code stays in the main strict run. No `Any` outside the previously-scoped Pydantic ignores; the `JsonValue` recursive alias remains the RPC boundary type.

## Scope summary
- `RealizerEngine` (`forge_mcp/realize/engine.py`) — walks curated sequences from the bpy hypergraph, prefers `bpy.data.*` paths over `bpy.ops.*` per Architecture §5.7, enforces pre/post-conditions, emits structured execution traces.
- v1 macros (`forge_mcp/realize/macros.py`): `reset_scene`, `create_terrain_from_heightmap`, `apply_terrain_material`, `carve_stream`, `set_camera_overview`, `add_basic_lighting`, `render_preview`, `save_blend`, composite `realize_region`.
- Adapter extensions (`scripts/blender/adapter.py`) for the small set of methods macros require beyond Phase 1's surface (set_idprop on data-blocks by collection key, render to file path, mesh creation by raw vertex/face data, image-from-numpy load).
- IDProperty round-trip wired into the realize path (verdict from Phase 1 honored: real IDProperties if Phase 1 went green, scene-metadata-dict fallback if not).
- `forge.render_view(region_id, view_kind, resolution=None)` tool.
- `forge.generate_region` extended to invoke the realizer (its Phase-3 stub becomes real); response now carries `blend_path` + `preview_image` + `analysis`.
- Blender startup contract: realizer refuses to load if the running Blender's `version_tuple()` ≠ bpy hypergraph's `target_version` (Architecture §15 invariant).
- Local integration test suite (`scripts/run_integration.sh` / `make integration`) gated on `FORGE_BLENDER_BIN`; **not** in CI per ROADMAP confirmed decision.
- Bench harness extended: end-to-end NF-1.3 budget (60 s) + NF-1.5 (PNG ≤ 200 KB at 1024×768).

## Out of scope for Phase 4 (do not scaffold)
- General bpy planner (Architecture §5.7: "v2 adds the general planner"). v1 ships curated sequences only.
- Boundary contracts / stream anchors (Phase 6). `carve_stream` macro consumes whatever the Phase-3 generator produced; no contract negotiation here.
- Locks / lock honoring during regeneration (Phase 7). `generate_region` still ignores the lock store.
- Skills (Phase 5). The `forge.audit` subagent that verifies realization quality lands next phase.
- Canvas / popup updates on generation (Phase 6+). The realizer doesn't broadcast yet.
- Material / lighting variation per descriptor (e.g. snow vs sand). v1 ships ONE terrain material driven by elevation/slope and ONE lighting setup; descriptor variation is a v2 concern.
- Vegetation, settlements, biomes — explicitly PRD §4 out-of-scope.

## Stage A — Adapter + RPC surface extensions

Phase 1 covered `ping`, `bpy.ops.*`, `bpy.data.*.new`, `bpy.data.*.remove`, `set_property`, `get_property`, `set_idprop`, `get_idprop`. Phase 4 macros need a tightly-scoped extension; we resist adding generic dispatchers.

1. **`mesh.from_pydata`** method — takes `{name, vertices: [[x,y,z],...], edges: [], faces: [[i,j,k,l],...]}` and creates a `Mesh` data-block + `Object` wrapping it (the standard Blender pattern). This is the data-API path the heightmap mesh constructor will use, avoiding the `bpy.ops.mesh.primitive_plane_add` context dance entirely.
2. **`image.from_file`** method — loads a 16-bit PNG (already produced by Phase 3) into `bpy.data.images` and returns its name. Used by the displacement modifier path.
3. **`render.to_file`** method — sets `scene.render.filepath`, `scene.render.image_settings` (file_format=PNG, color_mode=RGB, color_depth=8, compression=15), `scene.render.resolution_x/y`, then calls `bpy.ops.render.render(write_still=True)`. Returns `{path, file_size_bytes, width, height}`. The render still goes through `bpy.ops.render.render` (no data API exists) — accepted; Architecture §5.4 explicitly flags render as context-needed.
4. **`material.build_terrain`** method — given `{material_name, color_ramp_stops: [...], slope_threshold: ...}` constructs a node-tree material via `bpy.data.materials.new()` + node-graph manipulation; assigns to a target object's first material slot. Pure data API. Encapsulating the node-graph construction adapter-side avoids exporting Blender's whole node API over JSON-RPC.
5. **`scene.diff`** method — returns counts of each `bpy.data.*` collection (objects, meshes, materials, images, lights, cameras, curves, worlds). Cheap; used as the `scene_state_diff` payload (Phase 1's stub value). Engine uses this for postcondition checks.
6. **Adapter is pure dispatch.** No business logic. Each method is a small function with type-stubbed `bpy` access (the `fake-bpy-module-*` stubs cover what we need; if a single API isn't typed in the stubs, the adapter file already has its scoped mypy carve-out).
7. **RPC client** (`forge_mcp/realize/rpc.py`) — extend the typed wrapper with new method constants in `RpcMethods` (`MESH_FROM_PYDATA = "mesh.from_pydata"` etc.). Keep `JsonValue` boundary; no `Any` leakage.
8. **Determinism**: where Blender randomness enters (e.g. cycles sampling), set `scene.cycles.seed` from the spec seed in `realize_region`. Render engine fixed to **EEVEE Next** for v1 (faster on dev hardware; deterministic given seed; Architecture §2.1 notes EEVEE/Cycles harmonization in 5.0 — we pick EEVEE Next for cost, accept Cycles in v2).

## Stage B — Realizer engine (`forge_mcp/realize/engine.py`)

Architecture §5.7 prototype with concrete typing.

1. **`RealizerEngine`** — constructor takes `(BpyHypergraph, BlenderProcess)`. On `__init__`, queries `ping`, asserts `parsed_blender_version == hypergraph.target_version`. Mismatch raises `BlenderVersionMismatchError`; refuses to operate (Architecture §15 invariant).
2. **`execute_macro(macro: MacroName, inputs: MacroInputs) -> RealizationResult`** — pulls the curated step list from the hypergraph (`alternative_paths` + `curated_sequences` files), iterates, for each step:
   1. **Bind params**: substitute `inputs` placeholders into the step's parameter template (small typed templater, no Jinja — straight `Mapping` lookup with `${name}` syntax).
   2. **Choose path**: if the step has an `alternative_paths` entry pointing at a `bpy.data.*` form, prefer it (Architecture §2.1 + §5.7 contract).
   3. **Pre-condition check**: scene-state-diff snapshot, optional predicates from the hypergraph's `effects` annotations (e.g. "requires `bpy.data.images[heightmap_name]` to exist").
   4. **RPC call** via the `RpcClient`.
   5. **Post-condition check**: re-read scene-state-diff, assert the expected delta (e.g. `objects` count increased by exactly 1 for `mesh.from_pydata`).
   6. **Trace append**: structured `RealizationTraceStep(call, params_redacted, duration_ms, scene_diff_before, scene_diff_after)`. Trace is stored in the `SpecRecord`'s `generation_metadata.realization_trace` (new Phase-4 sub-field) for reproducibility audits.
3. **Macro invocation pattern**: `engine.execute_macro("realize_region", {heightmap_path, stream_geometry, spec, region_id})`. The composite macro is itself defined as a sequence of sub-macros — engine recurses depth-1 (no general recursion to keep Phase 4 tight).
4. **Failure handling**: any RPC error or postcondition mismatch raises `RealizerStepError` with the trace up to the failing step; caller surfaces as a structured MCP error and rolls back via `BlenderProcess.restart()` if the failure mode is `RpcProtocolError` (transport/process-level), keeps process if it's a logical postcondition miss (signals a hypergraph data bug, not a Blender crash).
5. **No ad-hoc bpy calls.** Every method invoked goes through `RpcMethods` constants registered in the hypergraph. CI grep guard added: `grep "bpy\." forge_mcp/realize/macros.py` should match only docstrings (test asserts).

## Stage C — Curated sequences in the bpy hypergraph

Phase 1 emitted `forge_mcp/bpy_hypergraph/data/{operators,types,effects,alternative_paths}.json`. Phase 4 adds the curated-sequence file Architecture §5.3 step 6 promised: **`curated_sequences.json`**, hand-authored per the v1 macros below.

1. **Schema** (Pydantic model in `forge_mcp/bpy_hypergraph/sequences.py`):
   - `CuratedSequence(name: str, version: str, steps: tuple[SequenceStep, ...], inputs_schema: Mapping[str, str], outputs_schema: Mapping[str, str])`.
   - `SequenceStep(call: str, params: Mapping[str, JsonValue], expects: Mapping[str, JsonValue] | None)`.
   - `expects` declares the postcondition predicates the engine evaluates (e.g. `{"objects.delta": 1, "selected_object_kind": "MESH"}`).
2. **Each macro is one curated sequence.** `realize_region` is also one entry whose steps are method `seq:reset_scene`, `seq:create_terrain_from_heightmap`, etc. (the `seq:` prefix marks sub-sequence invocations, resolved by the engine once at depth 1).
3. **Determinism contract**: sequences are *content-addressable* — `sequence_id = blake2b(canonical_json(sequence))`. The v1 macro set is locked into `realize/v1/<macro>.json` and tested for byte-identity in CI.
4. **No Sphinx-doc or runtime introspection in this stage.** Sequences are hand-authored against the Phase-1 ingestion data; the engine validates that every `call` referenced by a sequence exists in the hypergraph (boot-time check).

## Stage D — v1 macros (`forge_mcp/realize/macros.py`)

Architecture §5.5. One function per macro; each builds the `MacroInputs` payload then calls `engine.execute_macro`. The actual sequencing lives in `curated_sequences.json` — `macros.py` is the typed Python facade.

1. **`reset_scene(engine)`** — clears `bpy.data.objects`, `meshes`, `materials`, `images`, `cameras`, `lights`, `worlds`, `curves` via `bpy.data.<coll>.remove(item)` loop. Postcondition: all eight collections empty.
2. **`create_terrain_from_heightmap(engine, *, heightmap: Heightmap, region_id: RegionId)`** — Two implementation options; pick **direct mesh** for determinism + IDProperty access:
   - Build vertex grid in host Python from `heightmap.data` (numpy → list-of-tuples; subsample if >256² for the realization path — full-res heightmap stays as the displacement texture).
   - Send `mesh.from_pydata` with `{name: f"terrain_{region_id}", vertices, faces}`; receive object name.
   - Set custom IDProperties `forge_node_id`, `forge_spec_id`, `forge_kind="terrain_mesh"` via `set_idprop`.
   - Apply `MODIFIER_DISPLACE` via the modifier-add data-API path with the 16-bit PNG as displacement texture and strength = (elevation_band[1] − elevation_band[0]).
   - Postcondition: object exists; modifier present; IDProperties readable on roundtrip.
3. **`apply_terrain_material(engine, *, region_id, elevation_band)`** — `material.build_terrain` adapter call with elevation-driven color ramp + slope-driven mix factor. Returns material name; assigns via `set_property`. v1 ships ONE material; descriptor variation is v2.
4. **`carve_stream(engine, *, stream_geometry: StreamGeometry | None, region_id)`** — no-op if `None`. Otherwise constructs a curve via `bpy.data.curves.new` + spline data, sets bevel depth = `width_meters/2`, applies a simple water-blue material. IDProperties `forge_node_id=region_id`, `forge_kind="stream_curve"`. Postcondition: curve object exists if stream provided.
5. **`set_camera_overview(engine, *, world_bounds: Bounds2D)`** — creates camera, computes orthographic frame from world bounds for `ortho_top` view (looking down -Z) plus a perspective camera positioned south-east at 45° elevation for `perspective_se`. Assigns scene render camera. Two cameras, named `cam_ortho_top` and `cam_persp_se`.
6. **`add_basic_lighting(engine)`** — Sun lamp at fixed angle (45° elevation, 135° azimuth) for shadow consistency across regions, plus a procedural sky world via `bpy.data.worlds.new` + node tree. Strength values are constants (deterministic).
7. **`render_preview(engine, *, view_kind: Literal["ortho_top", "perspective_se"], resolution: tuple[int, int], output_path: Path)`** — switches scene render camera to the matching camera, calls `render.to_file`. Postcondition: file exists, `file_size_bytes <= 200_000` for 1024×768 (NF-1.5). If oversized, retries once with PNG compression bumped from 15 → 30 before failing.
8. **`save_blend(engine, *, blend_path: Path)`** — `bpy.ops.wm.save_as_mainfile(filepath=...)`. Postcondition: file exists + non-empty.
9. **Composite `realize_region(engine, *, spec, heightmap, stream_geometry, region_id, output_dir)`** — orchestrates the above, returns `RealizationResult(blend_path, ortho_preview_path, perspective_preview_path, default_preview_path, render_engine="EEVEE_NEXT", duration_ms_per_step: tuple[...])`. `default_preview_path` is `ortho_top` (per F-12.4: "default-resolution preview").

## Stage E — IDProperty strategy (consume Phase 1 verdict)

The Phase 1 spike-2 IDProperty round-trip test recorded a verdict (real `obj["forge_*"]` IDProperties OR scene-metadata-dict fallback). Phase 4 honors that:

- **If verdict = real IDProperties** (expected case for 5.0.0 stable): macros call `set_idprop` directly. Adapter's `set_idprop` is the existing Phase-1 method.
- **If verdict = fallback**: a new module `forge_mcp/realize/idmeta.py` mediates: `set_object_meta(engine, obj_name, payload)` writes to `scene["forge_objects"][obj_name] = payload` instead. Macros call `idmeta.set_object_meta` exclusively; the implementation switch is one place.

The default macro implementation calls a thin `idmeta.set(...)` wrapper that switches on a constant from `forge_mcp/realize/__init__.py` reflecting the Phase-1 verdict. **No conditional branches in macros themselves.** Round-trip test (`tests/realize/test_idproperty_roundtrip.py` from Phase 1) extended to cover the realize path.

## Stage F — `generate_region` wiring + `render_view` tool

1. **`forge.generate_region`** (existing in Phase 3 with `blend_path: null`): replace the null with real realization.
   - Sequence: `descriptor → spec` (Phase 3) → `terrain.run(spec)` → `analyze` → `realizer.realize_region(spec, heightmap, stream)` → persist `realizations/blender/<region_id>.blend` + two preview PNGs.
   - Response now: `{spec_id, analysis, blend_path, preview: {ortho_top: <png_bytes via MCP image content>, perspective_se: <path>}, generators_used, realizer_version}` per F-12.4 + F-12.5.
   - Total budget: NF-1.3 = 60 s end-to-end. Logged duration breakdown per step.
2. **`forge.render_view(region_id, view_kind: Literal["ortho_top","perspective_se"], resolution: Literal["preview","default","full"]="default")`** — operates on the *existing* realization (errors if region has no `.blend`). Reopens the .blend (if not already in scene state — adapter tracks current open file), switches camera per `view_kind`, renders at the requested resolution (`preview=512×384`, `default=1024×768`, `full=2048×1536`), returns MCP image content. No regeneration. Per F-12.3.
3. **`forge.generate_region` failure modes** are surfaced as structured errors with stage tags (`stage: "spec_mapping" | "generation" | "analysis" | "realization"` + Blender step name if realization). Atomic file behavior: never overwrite the previous good `.blend` on failure — write to `<region_id>.blend.tmp` then `os.replace` per `_io/atomic.py`.
4. **Lock store still ignored** (Phase 7). `reroll_seed` now triggers a real Blender re-realization; the Phase-3 structured warning persists.

## Stage G — Tests + integration suite

Coverage stays 90% on `forge_mcp/`. Tests split between fast unit tests (mock `BlenderProcess`/`RpcClient`; CI) and integration tests (real Blender; local-only).

1. **Unit tests** (CI):
   - `tests/realize/test_engine.py` — engine pre/post checks against a fake `RpcClient` that replays canned responses; trace structure asserted; macro-not-found → structured error; postcondition mismatch → `RealizerStepError`.
   - `tests/realize/test_macros.py` — each macro builds the expected step list against a fake engine; IDProperty payload asserted; render parameters match resolution table; NF-1.5 retry logic exercised.
   - `tests/bpy_hypergraph/test_sequences.py` — `curated_sequences.json` parses; every `call` referenced exists in `operators.json` or is a `seq:` reference to another sequence; sequence content-hash matches committed value (CI lock).
   - `tests/realize/test_version_check.py` — engine refuses to construct on version mismatch.
   - `tests/server/test_generation_full.py` — `forge.generate_region` happy path with a fake realizer (asserts plumbing through to MCP image content); structured-error paths per stage tag.
2. **Integration tests** (local; gated on `FORGE_BLENDER_BIN`; **not** in CI per Phase 0 confirmed decision):
   - `tests/integration/test_realize_region.py` — end-to-end against Blender 5.0.0: 64² heightmap → realize → assert `.blend` exists (>1 KB), both PNG previews exist (≤200 KB each at 1024×768), IDProperties survive a save/reopen cycle (this *is* the Phase-1 round-trip test pulled into the realize path).
   - `tests/integration/test_render_view.py` — generate once, then call `render_view` for both view_kinds at all three resolutions; assert dimensions + reasonable file sizes.
   - `tests/integration/test_perf.py` (slow marker; opt-in) — 1024² heightmap end-to-end; reports duration; soft-asserts ≤60 s on dev machine; no CI gate.
3. **Integration runner**: `scripts/run_integration.sh`:
   ```
   set -euo pipefail
   : "${FORGE_BLENDER_BIN:?must point at Blender 5.0.0 binary}"
   exec uv run pytest tests/integration -m "not slow" "$@"
   ```
   `make integration` and `make perf` Makefile targets wrap this.
4. **Coverage handling**: integration-only modules (`engine.py`, `macros.py`) still need 90% branch coverage from the unit tests with fake RPC. The fake `RpcClient` is the critical fixture — a `tests/realize/conftest.py` provides `recording_rpc` and `replay_rpc` fixtures, plus a `golden_traces/` directory with canonical step-by-step traces per macro (one JSON per macro).

## Stage H — Bench, docs, ROADMAP close-out

1. **Bench harness**: `scripts/eval/bench_phase4.py` (parallel to Phase-3 eval harness) runs the 5-descriptor eval set through the *full* realize path locally. Outputs `docs/eval/phase4/<timestamp>/` with: contact sheet of `ortho_top` previews, JSON per-region timing breakdown, NF-1.3 / NF-1.5 pass-fail summary.
2. **Docs**:
   - `docs/realization.md` (new) — engine architecture, macro list, IDProperty strategy, version pinning.
   - `docs/blender_setup.md` (extend) — env var, Blender 5.0.0 install verification command, link to integration runner.
   - `docs/eval/phase4/README.md` — bench acceptance + sample artifacts.
3. **`AGENT/dev_phases/phase4.md`** committed.
4. **`AGENT/ROADMAP.md`** marked complete in the closing PR (not before).
5. **`AGENT/ARCHITECTURE.md`**: append "Phase 4 measurements" — actual end-to-end duration on dev box, confirmed render engine (EEVEE Next), confirmed IDProperty path, count of curated sequences shipped.

## Step ordering and dependencies
- Stage A (adapter + RPC extensions) is the prerequisite — every macro depends on it.
- Stage C (curated sequences JSON) can be drafted in parallel with A; its CI test depends on the schema in `bpy_hypergraph/sequences.py`.
- Stage B (engine) depends on A's `RpcMethods` extension and C's sequence schema.
- Stage D (macros) depends on B + C.
- Stage E (IDProperty strategy) lands inside D's `create_terrain_from_heightmap` — a single switching wrapper, not a separate PR if the Phase-1 verdict was clean.
- Stage F (`generate_region` wiring + `render_view`) depends on D.
- Stage G (tests) interleaves with A–F. Integration suite lands with F.
- Stage H (bench, docs) closes the phase.

## Branches (one PR per concern; descriptive, no phase prefix)
1. `adapter-mesh-render-material` — Stage A
2. `bpy-curated-sequences-schema` — Stage C
3. `realizer-engine` — Stage B (depends on 1, 2)
4. `realize-macros-v1` — Stage D + E (depends on 3)
5. `generate-region-realizer-wiring` — Stage F + integration suite + Stage G unit tests (depends on 4)
6. `phase4-bench-and-docs` — Stage H (last; depends on 5)

## Relevant files (final Phase 4 tree additions)
```
forge_mcp/
├── bpy_hypergraph/
│   ├── sequences.py                  # NEW: CuratedSequence Pydantic model + loader
│   └── data/
│       └── curated_sequences.json    # NEW: hand-authored v1 macros
├── realize/
│   ├── engine.py                     # NEW: RealizerEngine
│   ├── macros.py                     # NEW: typed facade for v1 macros
│   ├── idmeta.py                     # NEW: IDProperty / scene-metadata switch
│   ├── rpc.py                        # extended: new RpcMethods constants
│   └── blender_proc.py               # extended: track current open .blend
└── server/tools/
    ├── generation.py                 # extended: real realizer wiring
    └── inspection.py                 # extended: register render_view
scripts/
├── blender/adapter.py                # extended: 5 new methods
└── eval/bench_phase4.py              # NEW
tests/
├── realize/
│   ├── conftest.py                   # recording_rpc, replay_rpc fixtures + golden traces
│   ├── test_engine.py
│   ├── test_macros.py
│   └── test_version_check.py
├── bpy_hypergraph/test_sequences.py
├── integration/                      # NEW (gated on FORGE_BLENDER_BIN)
│   ├── test_realize_region.py
│   ├── test_render_view.py
│   └── test_perf.py
└── server/test_generation_full.py
docs/
├── realization.md
└── eval/phase4/...
```

## Verification (Phase 4 gate)
1. All branches merged; CI green on `main`.
2. `pytest --cov=forge_mcp --cov-fail-under=90 --cov-branch` exits 0; band 90–95%.
3. `forge-schema-export --check` exits 0 (sequence schema added to published surface).
4. `mypy` strict exits 0 on host code; secondary mypy on `scripts/blender/` exits 0 with `fake-bpy-module-*` stubs (Phase-1 carve-out).
5. **Local integration**: `FORGE_BLENDER_BIN=/path/to/blender bash scripts/run_integration.sh` exits 0; integration tests assert IDProperty round-trip + PNG ≤200 KB + .blend non-empty.
6. **Manual MCP smoke (Claude Code)**: open project → create region with descriptor → `forge.generate_region` returns `.blend` path + ortho preview + analysis in chat under 60 s; `forge.render_view(region_id, "perspective_se", "default")` returns the SE perspective PNG without re-running generation.
7. **Version refusal**: pointing `FORGE_BLENDER_BIN` at a non-5.0.0 binary causes `RealizerEngine` construction to raise `BlenderVersionMismatchError` with a clear message — verified by an integration test.
8. **Phase-4 bench (manual)**: 5-descriptor set realized end-to-end; contact sheet of ortho previews shows distinct, recognizable terrain; per-region timing under budget; bench artifacts committed under `docs/eval/phase4/<timestamp>/`.
9. **Strictness audit**: zero new `ignore` entries; zero `# type: ignore` without code; zero `ignore_missing_imports`; no `bpy.` import in `forge_mcp/` (only in `scripts/blender/`).
10. **Determinism**: re-running `forge.generate_region` with identical descriptor + seed twice on a clean project produces identical `.blend` (assert via `blake2b` of file bytes — gated on Blender producing deterministic save bytes; if not, fall back to asserting identical IDProperty payloads + identical render PNG bytes which are scenes-deterministic).

## Decisions baked in
- **Render engine: EEVEE Next** for v1 (faster, deterministic via `cycles.seed`). Cycles is v2.
- **One terrain material, one lighting setup.** Descriptor-driven material variation is out of scope.
- **Two cameras per scene**: `cam_ortho_top`, `cam_persp_se`. `default` view = `ortho_top`.
- **Render resolutions**: preview=512×384, default=1024×768, full=2048×1536.
- **Adapter extension is tightly scoped.** Five new methods, no generic dispatch widening.
- **Curated-sequence content addressing**: each sequence carries a `sequence_id = blake2b(canonical_json(seq))`; CI locks the v1 set.
- **Engine refuses on version mismatch.** Architecture §15 invariant enforced at construction.
- **No `bpy` import outside `scripts/blender/`.** CI grep guard.
- **Integration suite stays local-only.** ROADMAP confirmed decision; CI runs unit tests only.
- **Atomic `.blend` writes**: temp file + rename; never overwrite a good `.blend` on failure.
- **PNG oversize retry**: bump compression once before failing NF-1.5.
- **IDProperty strategy** = Phase-1 verdict, mediated through `idmeta.set` so macros are unaware of the choice.

## Confirmed decisions (2026-04-30)
1. **Render engine: EEVEE Next** for v1; Cycles deferred to v2.
2. **Mesh resolution policy**: subsample to 256² in `create_terrain_from_heightmap`; displacement modifier supplies high-frequency detail from the full-res 16-bit PNG.
3. **PNG budget**: 8-bit RGB, compression=15 with one auto-retry at 30; NF-1.5 (≤200 KB @ 1024×768) enforced as a postcondition; failure surfaces a structured error.

## Open questions to confirm with user
1. **Render engine choice**: EEVEE Next (recommended; fast, deterministic) vs Cycles (more PBR-correct but slower; bigger NF-1.3 risk).
2. **Heightmap mesh subsampling threshold**: clamp realize-path mesh resolution to 256² regardless of heightmap source resolution (recommended; the displacement modifier supplies the high-frequency detail) vs match source resolution 1:1 (heavier, but no aliasing risk).
3. **Render-PNG bit depth / compression**: 8-bit RGB compression=15 with one retry at 30 (recommended; matches NF-1.5 200 KB budget at 1024×768) vs higher fidelity + relax NF-1.5.
