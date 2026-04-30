# Plan: Phase 3 — Descriptor Mapping + Terrain Generator

Make Forge's deterministic Python core actually generate terrain. End state: an agent passes a `StructuredDescriptor` + integer seed via `forge.create_region` (or `forge.update_region`); a follow-up `forge.generate_region` deterministically produces a heightmap + stream geometry + numerical analysis, persists a fully-typed `SpecRecord`, and returns a 16-bit heightmap PNG plus a JSON analysis payload to the agent. No Blender yet (Phase 4 wires the realizer); no LLM ever. Architecture §4.1 plan/realize loop is implemented through step 7 ("Analyze for perception payload") with step 6 ("Realize in Blender") stubbed as a no-op that just records the spec.

> **Strictness still rules.** `ruff ALL` + `mypy strict` + `disallow_any_explicit` + 90% branch coverage. New numpy/scipy code does not get to wave types around — `numpy.typing.NDArray[np.float32]` etc. on every public surface; `Any` is never explicit.

## Scope summary
- `forge_mcp/descriptor/map_to_spec.py` — pure-Python deterministic mapping `StructuredDescriptor + seed → TerrainSpec`.
- `forge_mcp/project/schemas.py` — promote `SpecRecord.body` from opaque `dict[str, JsonValue]` to typed `axes` / `boundary_requirements` / `summary` / `generation_metadata` per Architecture §3.4. Schema export + golden fixtures updated.
- `forge_mcp/generate/` — new subpackage: `deterministic.py` (RNG factory), `noise.py` (ridged multifractal), `erosion.py` (hydraulic + thermal), `stream.py` (anchor-stub injector), `terrain.py` (orchestrator), `heightmap.py` (`.npy` + 16-bit PNG I/O).
- `forge_mcp/analyze/terrain_analysis.py` — numerical analysis (elevation/slope/aspect stats; stream-presence summary).
- New MCP tools: `forge.generate_region`, `forge.reroll_seed`, `forge.analyze_region` — wired into the existing `tools/` package (the deliberate Phase-2 gap is closed).
- Five-descriptor evaluation set (R-2 mitigation) + iteration loop on `TERRAIN_PROFILES`.
- Determinism contract: byte-identical `.npy` + PNG given identical inputs, asserted in CI.
- Performance contract: ≤30 s for 1 km² @ 2 m/px on dev machine (NF-1.2). Benchmark harness committed; not enforced as a CI threshold (perf varies by runner) but reproducible locally.

## Out of scope for Phase 3 (do not scaffold)
- Blender execution / `RealizerEngine` / macros (Phase 4). `generate_region` returns the heightmap PNG and a stub `realization` block; `.blend` arrives Phase 4.
- `render_view` MCP tool — Phase 4 (it depends on Blender).
- Boundary contract math (Phase 6). Phase 3 spec includes a `boundary_requirements: tuple[BoundaryRequirement, ...] = ()` field but does not consult adjacent regions yet — stream anchors stay `None`.
- Lock application (Phase 7). `generate_region` ignores the lock store.
- Skills (Phase 5). The plan skill embeds the descriptor schema in Phase 5; Phase 3 only ensures `get_descriptor_schema` and the underlying mapping are correct.
- Connection map / canvas updates on generation (Phase 6+).

## Stage A — Dependencies + spec schema upgrade

1. **Add runtime deps** via `uv add`:
   - `numpy>=2.0` (stubs ship in-tree as `numpy.typing`).
   - `scipy>=1.13` (stubs ship via `scipy-stubs` published by SciPy; if the variant is not yet on PyPI under that name, pin `types-scipy` if available, otherwise add a thin local stub under `forge_mcp/_stubs/scipy/` for the surface we use — `scipy.ndimage.gaussian_filter`, `scipy.ndimage.sobel`. **No `ignore_missing_imports`.**).
   - `Pillow>=10.4` (stubs via `types-Pillow`) — sole purpose: write 16-bit PNGs that Blender can ingest as displacement maps in Phase 4.
2. **Defer numba** (PRD §6.8 mentions "optionally numba"). Phase 3 ships pure numpy/scipy; if benchmark fails NF-1.2 by >1.5×, open a follow-up issue but do **not** add numba reactively in Phase 3 — that would risk strictness debt (numba has weak typing) and is out of phase scope.
3. **Promote `SpecRecord` to the typed Architecture §3.4 shape.** New nested models in `forge_mcp/project/schemas.py`:
   - `TerrainGeneratorParams` — `octaves: int`, `lacunarity: float`, `persistence: float`, `warp: float`, `scale_meters: float`.
   - `PostPass` — discriminated union (`kind: Literal["hydraulic_erosion", "thermal_erosion"]` + per-kind fields).
   - `FeatureInjector` — discriminated union (`kind: Literal["stream"]` + `anchor_in: AnchorPoint | None`, `anchor_out: AnchorPoint | None`, `width_meters: float`, `carving_depth: float`). `AnchorPoint` is `tuple[float, float]` xy coords.
   - `TerrainAxisSpec` — `generator: Literal["ridged_multifractal_v1"]`, `params: TerrainGeneratorParams`, `post_passes: tuple[PostPass, ...]`, `feature_injectors: tuple[FeatureInjector, ...]`, `elevation_band: tuple[float, float]`, `resolution_meters_per_pixel: float`.
   - `BoundaryRequirement` — placeholder; stays empty until Phase 6 (just `boundary_id: BoundaryId` and a `kind: Literal["elevation_continuity"]` field for now).
   - `SpecSummary` — `mean_elevation`, `std_elevation`, `min_elevation`, `max_elevation`, `slope_p95_degrees`, `stream_length_meters: float | None`.
   - `GenerationMetadata` — `compiler_version`, `generators_used: tuple[str, ...]`, `bpy_hypergraph_version`, `blender_version`, `parent_spec_hash: str | None`, `conflicts_resolved: tuple[str, ...]`.
   - `SpecRecord.body` becomes a typed structure: `axes: dict[Literal["terrain"], TerrainAxisSpec]`, `boundary_requirements`, `summary: SpecSummary`, `generation_metadata: GenerationMetadata`. Existing Phase-2 `descriptor: StructuredDescriptor` field is reused.
   - **Schema export**: `iter_published_schemas` automatically picks the new shape up; `forge-schema-export --check` will catch the drift on first run. Update committed `schemas/spec.schema.json` (rename from any Phase-2 placeholder if needed).
   - **Golden fixtures** under `tests/fixtures/golden/specs/` updated.
4. **Spec content-addressing**: spec ID is now actually content-derived. `spec_id = "spec_" + blake2b(canonical_json(body), digest_size=6).hex()` where `canonical_json` calls `dump_json`. Same descriptor + same seed + same generator versions ⇒ same spec ID, regardless of which region called `generate_region`. Tested.

## Stage B — Deterministic RNG factory (`forge_mcp/generate/deterministic.py`)

Architecture §4.3: "All generators take an explicit RNG; no module-level state. Seeds derived deterministically per pass."

1. `make_rng(seed: int, *, pass_name: str) -> np.random.Generator` — derives a per-pass seed via `np.random.SeedSequence(seed).spawn_key(pass_name)` (or equivalent BLAKE2-based mixer; see step 2). One `np.random.Generator` per pass, never shared.
2. **Mixer choice**: `SeedSequence.spawn(n)` requires integer keys; we instead hash `(seed, pass_name)` via `blake2b` to a 128-bit integer and feed it as `entropy=` to a fresh `SeedSequence`. This makes "same seed, same pass_name → same Generator" explicit and audit-friendly. Pass names are constants:
   - `"noise.base"`, `"noise.warp"`, `"erosion.hydraulic"`, `"erosion.thermal"`, `"stream.path_jitter"`. Adding a pass name **bumps `generator_version`** and is a breaking determinism change — locked test asserts the constant set hasn't changed silently.
3. **No module-level RNG anywhere** — ruff's `S311` (suspicious `random` use) catches `random.*`; we add a CI grep guard for `np.random.default_rng()` outside `make_rng` (single-source-of-truth).

## Stage C — Descriptor → spec mapping (`forge_mcp/descriptor/map_to_spec.py`)

Architecture §4.2 lookup-table-driven, pure Python.

1. **`TERRAIN_PROFILES: Mapping[TerrainPrimary, TerrainProfile]`** — one entry per `TerrainPrimary` enum value (12 entries). Each `TerrainProfile` (frozen Pydantic model) carries:
   - `octaves_base`, `lacunarity_base`, `persistence_base`, `warp_base`, `scale_meters_base`
   - `erosion_iterations_base`, `talus_angle_degrees_base`, `hydraulic_rain`, `hydraulic_evaporation`
   - `default_elevation_band: tuple[float, float]`
   - `notes: str` — short human-readable rationale (shows up in `inspect_spec`).
2. **Modulators** — pure functions of the descriptor:
   - `ruggedness` ∈ [0,1] modulates `octaves` (+0..3), `persistence` (+0..0.15), `erosion_iterations` (×1..1.5).
   - `elevation_band` from descriptor overrides profile default if present.
   - Hydrology: if `has_stream` and `stream_character != none`, append a `FeatureInjector(kind="stream", ...)` with `width_meters`/`carving_depth` from a `STREAM_PROFILES` table keyed on `StreamCharacter`.
3. **`map_to_spec(descriptor: StructuredDescriptor, seed: int, *, blender_version: str, bpy_hypergraph_version: str) -> SpecRecord`** — pure function. Hashes the canonical body to derive `spec_id`. `created_at` injected by caller (pure ⇒ takes a `now: datetime`). Returns a fully-populated `SpecRecord` with empty `summary`/empty `boundary_requirements` (filled by the generator; summary fields come from analysis).
4. **Compiler version constant**: `COMPILER_VERSION = "0.1.0"` recorded on `GenerationMetadata.compiler_version`. Bumping requires regenerating golden fixtures.
5. **No LLM, no IO** — `map_to_spec` is pure and trivially unit-testable; full coverage easy.

## Stage D — Terrain generator core (`forge_mcp/generate/`)

All numpy/scipy. Public surfaces typed with `numpy.typing.NDArray[np.float32]` (heightmaps) and `NDArray[np.bool_]` (masks). No explicit `Any`; we add a tiny local stub under `forge_mcp/_stubs/scipy/ndimage.pyi` if the published stubs are insufficient.

1. **`heightmap.py`** — I/O + canonical types.
   - `Heightmap` dataclass: `data: NDArray[np.float32]` (shape `(H, W)`, units = meters, normalized so min=0 *only after* elevation-band remap), `resolution_meters_per_pixel: float`, `origin: tuple[float, float]` (world coords of pixel (0,0) corner), `elevation_band: tuple[float, float]`.
   - `save_npy(hm, path)` / `load_npy(path)` — atomic write via Stage 2's `atomic_write_text` (binary variant: `atomic_write_bytes`).
   - `save_png16(hm, path)` — Pillow `Image.fromarray((scaled * 65535).astype(np.uint16), mode="I;16").save(path)`. Lossy by design (PNG ingestion bit-depth); used only for Blender displacement and agent preview channel. Lossless `.npy` is the source of truth.
2. **`noise.py`** — ridged multifractal, deterministic per `make_rng("noise.base")`. Implementation: standard fBm of `1 - abs(simplex_or_perlin(...))`. Use a permutation-table based perlin noise (vectorized numpy; ~50 LoC). Domain warping uses a second RNG-derived offset map.
3. **`erosion.py`** — two passes:
   - `hydraulic(hm, *, iterations, rain, evaporation, rng) -> Heightmap` — droplet-based or grid-based; choose grid-based (vectorized; deterministic without per-droplet ordering issues).
   - `thermal(hm, *, iterations, talus_angle_degrees, rng) -> Heightmap` — grid-based talus relaxation.
4. **`stream.py`** — Phase-3 minimum: given a `FeatureInjector(kind="stream", ...)` with `anchor_in=None, anchor_out=None`, pick deterministic entry/exit points from the heightmap (lowest edge cell on opposite sides), trace a steepest-descent path with small RNG jitter, carve a channel of `width_meters` and `carving_depth`. Returns `(updated_hm, StreamGeometry)` where `StreamGeometry` is a frozen Pydantic model `path: tuple[tuple[float, float], ...]`, `width_meters`, `carving_depth`. Anchors-from-boundaries arrive Phase 6.
5. **`terrain.py`** — orchestrator:
   - `run(spec: SpecRecord) -> TerrainGenerationResult` where `TerrainGenerationResult` is `(heightmap, stream_geometry | None, generators_used: tuple[str, ...])`.
   - Pulls `seed` from spec, builds RNGs per pass via `make_rng`, runs noise → post-passes (in `post_passes` declared order) → feature injectors → returns. No persistence inside the generator (caller writes via `ProjectService`).
6. **Determinism harness** — every public generator function gets a `tests/generate/test_determinism.py` entry asserting two identical invocations produce byte-identical numpy arrays. CI gate.

## Stage E — Analysis (`forge_mcp/analyze/terrain_analysis.py`)

Pure numpy; no IO.

1. `analyze(heightmap: Heightmap, stream: StreamGeometry | None) -> TerrainAnalysis` — Pydantic model with:
   - `elevation: ElevationStats(mean, std, min, max, p05, p50, p95)`
   - `slope_degrees: SlopeStats(mean, p50, p95, max)` — slope from sobel-filtered gradients in m/m converted to degrees.
   - `aspect_distribution: tuple[float, ...]` — 8-bin compass histogram (N, NE, E, …) normalized to sum 1.
   - `stream: StreamSummary(length_meters, mean_gradient_degrees, anchor_in, anchor_out) | None`
2. **Used twice**: (a) populates `SpecRecord.summary` (compact subset); (b) returned to the agent as the `analysis` payload of `generate_region` and `analyze_region`. Same code path, no drift risk.

## Stage F — MCP tools (`forge_mcp/server/tools/generation.py`, +update `inspection.py`)

1. **`forge.generate_region(region_id, options=None)`**:
   - Loads region; rejects if `structured_descriptor is None` (structured error: "no descriptor — call create_region with one or update_region first").
   - Calls `descriptor.map_to_spec(...)`; persists `SpecRecord` to `specs/<spec_id>.json`; updates region's `spec_id`.
   - Calls `generate.terrain.run(spec)`; persists heightmap as `realizations/heightmap/<region_id>.npy` and `realizations/heightmap/<region_id>.png` (the `realizations/` dir is gitignored — Phase 0 done).
   - Calls `analyze.terrain_analysis.analyze(...)`; updates `SpecRecord.summary`; re-persists spec (content-addressing means spec ID changes only if descriptor/seed changes; summary is stored separately to keep `spec_id` content stable — actually: summary lives *inside* the SpecRecord but is computed during generation; we accept that two regions with identical descriptor+seed will share `spec_id` and identical summaries — desirable property).
   - Appends a `generate_region` history event with `{region_id, spec_id, generators_used, blender_version, bpy_hypergraph_version}`.
   - Returns MCP content: `{"spec_id", "analysis": ..., "preview_image_path": ..., "blend_path": null /* Phase 4 */}` plus an MCP image content block carrying the PNG bytes (per F-12.5: "Images returned via MCP image content; no separate transport").
2. **`forge.reroll_seed(region_id, *, seed=None)`**:
   - If `seed is None`, pick a new seed deterministically derivable from the region's history (e.g., `blake2b(region_id || history_count).int_at(8)` — keeps "reroll" itself deterministic given the project's history). Otherwise use the provided seed.
   - Updates region's `seed`; appends `reroll_seed` history event; calls `generate_region` flow.
   - **Locks not yet honored** (Phase 7); a comment + a structured-warning field in the response makes that explicit until Phase 7.
3. **`forge.analyze_region(region_id)`**:
   - Loads region's persisted heightmap (raises structured error if not yet generated).
   - Returns analysis JSON only — no image content. Cheap perception channel per PRD §6.12.
4. **`forge.inspect_spec(spec_id | region_id)`** (was deferred from Phase 2's tool list): returns the `SpecRecord` JSON. Now that specs are real, this becomes useful.
5. **`render_view` is NOT registered in Phase 3.** It depends on Blender previews; Phase 4. The Phase-2 commitment to "no premature scaffolding" still binds.

## Stage G — Eval set + iteration loop (`tests/descriptor/eval_terrain.py` + `scripts/eval/`)

R-2 mitigation. Goal: "the 5 descriptors visually distinguish themselves and recognizably match their primary."

1. **Eval set** — 5 descriptors (subset of the 10 from Phase 1's spike-4 eval set, chosen for visual contrast):
   - rugged alpine valley with creek
   - rolling hills (no hydrology)
   - desert mesa
   - boreal lowland with meandering river
   - canyon with dry wash
2. **Eval harness** `scripts/eval/render_eval_set.py`:
   - For each descriptor, runs the full Phase-3 generate flow at 256² (fast; 1024² runs only on explicit request to keep iteration tight).
   - Emits a contact sheet under `docs/eval/phase3/<timestamp>/contact_sheet.png` (PNG of the 5 PNG previews tiled 5×1) plus a JSON summary of the analyses.
   - Not run in CI; documented `make eval` target.
3. **Iteration loop**: tweak a `TERRAIN_PROFILES` entry → rerun harness → eyeball contact sheet → iterate. The lookup-table architecture means each cycle is seconds, not minutes.
4. **Acceptance** for Phase 3 (recorded in `docs/eval/phase3/README.md`): all 5 outputs visually distinguishable when viewed side-by-side; primary terrain type recognizable to Mehdi. No agent involved — that's Phase 5.
5. **Automated structural assertions** (in `tests/descriptor/test_eval_set.py`):
   - For each descriptor, the resulting `SpecRecord.summary` falls in plausible elevation/slope ranges (e.g., `desert_mesa` → mean slope < `alpine_valley` mean slope; `marsh` → std elevation < `alpine_peaks` std elevation). Encoded as a small ordering-rules table; catches regressions in `TERRAIN_PROFILES` without requiring image comparison.

## Stage H — Tests + golden files

Mirror source tree under `tests/`. Coverage gate stays at 90% branch.

1. `tests/descriptor/test_map_to_spec.py` — every `TerrainPrimary` profile covered; ruggedness modulation; elevation_band override; hydrology presence/absence; spec ID stability under identical inputs; spec ID changes when any input changes. Golden `SpecRecord` fixtures under `tests/fixtures/golden/specs/`.
2. `tests/generate/test_deterministic.py` — `make_rng` returns deterministic `Generator`; same seed+pass → same first 10 floats; different pass names → different streams.
3. `tests/generate/test_noise.py`, `test_erosion.py`, `test_stream.py` — small-grid (32²) determinism + invariants (e.g., erosion does not increase total mass without rain; thermal erosion does not exceed talus angle).
4. `tests/generate/test_terrain.py` — full pipeline determinism on a 64² grid; re-running twice produces byte-identical `Heightmap.data` (`np.array_equal`).
5. `tests/analyze/test_terrain_analysis.py` — analytical truth on a synthetic plane (slope 0), tilted plane (known slope), single-spike (known max), with stream/without stream.
6. `tests/server/test_generation_tools.py` — in-process tool tests:
   - `generate_region` happy path on a tiny region (32² for speed); persisted spec/heightmap/png exist; image content returned.
   - `generate_region` without descriptor → structured error.
   - `reroll_seed` updates region seed and persists new spec.
   - `analyze_region` requires a prior generation.
   - `inspect_spec` returns the persisted spec.
7. `tests/server/test_schema_export.py` (already from Phase 2) catches the spec-schema upgrade automatically.
8. **Performance benchmark** `tests/perf/test_terrain_perf.py` — *marked `@pytest.mark.slow`*, not in the default CI run. Prints elapsed seconds for 1024² (≈2 km² @ 2 m/px proxy); a `make perf` target runs it locally. Phase-3 acceptance: ≤30 s on dev machine for 512² (proportional check); 1024² recorded for Phase-4 budget visibility.
9. **Coverage**: defensive branches around scipy/Pillow error handling tagged `# pragma: no cover  # exhaustiveness` per `.github/instructions.md` §3 only when truly unreachable.

## Stage I — Documentation updates

1. `docs/generation.md` (new) — pipeline walkthrough (descriptor → spec → noise → erosion → stream → analysis), determinism contract, RNG pass-name registry, eval-set workflow.
2. `docs/eval/phase3/README.md` (new) — eval acceptance record + sample contact sheet committed.
3. `README.md` — "Generation" subsection with `forge.generate_region` example.
4. `AGENT/dev_phases/phase3.md` — committed plan.
5. `AGENT/ROADMAP.md` — mark Phase 3 complete only in the closing PR.
6. `AGENT/ARCHITECTURE.md` — append a short "Phase 3 measurements" note (perf number, RNG pass-name registry, profile table reference).

## Step ordering and dependencies
- Stage A (deps + spec schema upgrade) blocks everything else. Land first; existing Phase-2 golden fixtures regenerate.
- Stages B (deterministic RNG) and C (descriptor → spec mapping) can land in parallel; both block D.
- Stage D (`generate/`) depends on A + B; subdivides into D.1 (heightmap I/O), D.2 (noise), D.3 (erosion), D.4 (stream), D.5 (terrain orchestrator). D.1 first; D.2/D.3 parallel; D.4 last; D.5 ties them together.
- Stage E (analysis) depends on D.1.
- Stage F (MCP tools) depends on C + D + E.
- Stage G (eval) depends on F.
- Stage H (tests) interleaves throughout.
- Stage I (docs) closes the phase.

## Branches (one PR per concern, descriptive names per Phase 1/2 convention)
1. `spec-schema-typed-body` — Stage A (deps, SpecRecord upgrade, golden fixtures, schema export drift fix)
2. `deterministic-rng-and-pass-registry` — Stage B
3. `descriptor-to-spec-mapping` — Stage C (depends on 1 + 2)
4. `terrain-noise-and-erosion` — Stage D.1–D.3 (depends on 2)
5. `stream-injector` — Stage D.4 (depends on 4)
6. `terrain-orchestrator-and-analysis` — Stage D.5 + Stage E (depends on 4, 5)
7. `generation-mcp-tools` — Stage F + Stage H integration tests + Stage I docs (depends on 3, 6)
8. `phase3-eval-set` — Stage G (last; depends on 7)

## Relevant files (final Phase 3 tree additions)
```
forge_mcp/
├── _stubs/
│   ├── scipy/                        # if published stubs insufficient
│   └── PIL/                          # types-Pillow may suffice; otherwise local
├── analyze/
│   ├── __init__.py
│   └── terrain_analysis.py
├── descriptor/
│   └── map_to_spec.py
├── generate/
│   ├── __init__.py
│   ├── deterministic.py
│   ├── heightmap.py
│   ├── noise.py
│   ├── erosion.py
│   ├── stream.py
│   └── terrain.py
├── project/schemas.py                # extended: typed SpecRecord body
└── server/tools/
    ├── generation.py                 # NEW: generate_region, reroll_seed, analyze_region, inspect_spec
    └── inspection.py                 # extended: inspect_spec moved/registered
schemas/
└── spec.schema.json                  # regenerated (full Architecture §3.4 shape)
scripts/
└── eval/
    └── render_eval_set.py            # local-only; not in CI
docs/
├── generation.md
└── eval/phase3/
    ├── README.md
    └── contact_sheet.png             # committed acceptance artifact
tests/
├── descriptor/test_map_to_spec.py, test_eval_set.py
├── generate/test_deterministic.py, test_noise.py, test_erosion.py, test_stream.py, test_terrain.py
├── analyze/test_terrain_analysis.py
├── server/test_generation_tools.py
├── perf/test_terrain_perf.py         # @pytest.mark.slow
└── fixtures/golden/specs/...         # updated
```

## Verification (Phase 3 gate)
1. All branches merged, CI green on `main`.
2. `pytest --cov=forge_mcp --cov-fail-under=90 --cov-branch` exits 0; coverage stays in the 90–95% band.
3. `forge-schema-export --check` exits 0 — committed `schemas/spec.schema.json` matches the Phase-3 Pydantic shape.
4. `mypy` exits 0 strict; zero new ignore entries; zero `# type: ignore` without code; zero `Any` outside Pydantic-stub leak ignores.
5. Determinism: `tests/generate/test_terrain.py` asserts byte-identical heightmap on re-run; `tests/server/test_generation_tools.py` asserts byte-identical persisted PNG.
6. **MCP smoke (manual, via Claude Code):**
   - Open project → `forge.create_region` with descriptor `{terrain: {primary: alpine_valley, ruggedness: 0.8}, hydrology: {has_stream: true, stream_character: alpine_creek}}` → `forge.generate_region(region_id)` → receive PNG preview + analysis JSON in chat.
   - `forge.reroll_seed` → new spec_id, recognizably different heightmap, same descriptor.
   - `forge.analyze_region` (no regen) returns the cached analysis quickly.
   - `forge.inspect_spec` returns the typed spec body.
7. **Eval acceptance (manual, recorded in `docs/eval/phase3/README.md`):** the 5-descriptor contact sheet shows visually distinct outputs; primary terrain type recognizable.
8. **Performance (local, not CI):** 512² generation under 8 s on dev machine (proportional to NF-1.2's 30 s/1024²).
9. **Filesystem hygiene:** `realizations/heightmap/*.npy,*.png` are gitignored; no binary heightmaps slip into the diff.
10. **Strictness audit:** zero new `ignore` entries, zero `# type: ignore` without code, zero `ignore_missing_imports`, no `np.random.default_rng()` outside `make_rng`.

## Decisions baked in
- **No numba in Phase 3.** Pure numpy/scipy; revisit only if benchmark misses NF-1.2 by >1.5×.
- **Heightmap source of truth = `.npy`** (lossless). 16-bit PNG is for Blender ingestion + agent preview only.
- **Spec content addressing is real.** `spec_id` derives from canonical-JSON BLAKE2b of the spec body; identical descriptor+seed across regions ⇒ identical spec ID (intentional dedup property).
- **RNG pass-name registry is locked.** Adding/renaming a pass bumps `generator_version`; CI test asserts the constant set.
- **Eval set is 5 descriptors** (per ROADMAP), drawn from Phase-1's 10-descriptor set for continuity.
- **Generation never touches Blender in Phase 3.** `realization` block in tool response is `null`; Phase 4 fills it.
- **Locks ignored in Phase 3** (Phase 7 wires them); `reroll_seed` returns a structured warning making this explicit.
- **`render_view` deliberately not registered** until Phase 4 — same "no premature scaffolding" discipline as Phase 2.

## Confirmed decisions (2026-04-30)
1. **scipy stubs**: local stubs under `forge_mcp/_stubs/scipy/` covering only `scipy.ndimage.gaussian_filter` and `scipy.ndimage.sobel`. No `ignore_missing_imports`.
2. **Eval artifact**: commit the contact-sheet PNG to `docs/eval/phase3/<timestamp>/contact_sheet.png`. Acceptable repo bloat; valuable as a visual diff in future PRs.
3. **Perf gate**: local-only `make perf` target; no CI enforcement. NF-1.2 budget tracked in `docs/eval/phase3/README.md` and re-checked at Phase 4 boundary when Blender realization adds its own budget.

## Open questions to confirm with user
1. **scipy stub source**: official `scipy-stubs` (community-maintained, sometimes lags), `types-scipy` (legacy), or hand-rolled local stub for the 2-3 functions we use. Recommend local stub — minimal surface, no upstream churn risk.
2. **Eval-set image format**: contact-sheet PNG committed under `docs/eval/phase3/`. Acceptable repo-bloat? (Image will be ~50–200 KB.) Alternative: commit only the JSON analyses; regenerate the contact sheet on demand via `make eval`.
3. **Perf gate**: enforce 30 s @ 1024² in CI from a dedicated runner (slow + flaky), or keep perf as a local-only `make perf` target with no gate. Recommend local-only — perf gates are runner-dependent and prone to noise.
