# Plan: Phase 1 — Pre-Week-1 Architectural Spikes

De-risk every architectural assumption that downstream phases depend on, *before* writing any production-shaped code. Five parallel-ish spikes (per ROADMAP Phase 1 / PRD §11) produce concrete artifacts plus a written go/no-go verdict per risk. End state: bpy hypergraph JSON exists for the locally-installed Blender 5.0.0; stdio JSON-RPC + IDProperty round-trip proven; minimal MCP server loads in Claude Code; descriptor schema validates 10 hand-extracted test cases; prior-art differentiator framing committed. Any "no-go" → revise PRD before Phase 2.

> **Strictness mandate still applies.** Spike code is not exempt from `ruff select=ALL` + `mypy strict`. Spike velocity comes from narrow scope, not from `# noqa`. Code that runs *inside* Blender's own Python interpreter (the adapter script) is special-cased via mypy `exclude` because it imports `bpy` natively at runtime; it is type-checked separately using `fake-bpy-module-5.0` as a dev typing dependency.

## Stage A — Phase 1 prep (one-time, blocks all spikes)

1. **Pin Blender 5.0.0** as the v1 target (user has it installed locally). Update Architecture references that say "5.0.2" to "5.0.0" via a note in the spike outputs (full doc edit deferred — spec says "patch pinned per build" so 5.0.0 is conformant). Record the absolute path to the Blender executable in a developer-local untracked file `scripts/blender/.blender_path` (gitignored), and document the env var `FORGE_BLENDER_BIN` that all scripts honor.
2. **Branch model.** One feature branch per spike: `phase1/spike-{1..5}`, each merged via PR after CI green. This keeps spikes parallelizable and reviewable, and proves the Phase 0 PR workflow under realistic load. Order of merge does not matter except spike 1 (hypergraph) and spike 2 (RPC) ideally land before any production-shaped phase begins.
3. **Add runtime + dev dependencies via `uv add`** (one PR or rolled into spike PRs):
   - Runtime: `mcp` (official Anthropic SDK), `pydantic>=2.7`.
   - Dev (typing only): `fake-bpy-module-latest` — provides type stubs for `bpy`. (`fake-bpy-module-5.0` may not exist yet; use `latest` and accept a small mismatch for typing purposes only. Runtime always uses real bpy from Blender 5.0.0.)
   - Dev: `types-jsonschema` if we lean on jsonschema directly; otherwise rely on Pydantic-generated schemas.
4. **Spike directory layout** (gets committed under feature branches):
   ```
   forge_mcp/
   ├── bpy_hypergraph/
   │   ├── __init__.py
   │   ├── ingest.py            # spike 1 (orchestration; runs in host Python, invokes Blender)
   │   ├── data/                # spike 1 outputs (committed JSON)
   │   │   ├── operators.json
   │   │   ├── types.json
   │   │   ├── effects.json            # hand-curated
   │   │   └── alternative_paths.json  # 5.0 bpy.data alternatives
   │   └── query.py             # minimal load+query API used by tests
   ├── descriptor/
   │   ├── __init__.py
   │   ├── schema.py            # spike 4 — Pydantic models, schema export
   │   └── validate.py          # spike 4
   ├── realize/
   │   ├── __init__.py
   │   ├── blender_proc.py      # spike 2 — host-side process manager
   │   └── rpc.py               # spike 2 — JSON-RPC client
   └── server/
       ├── __init__.py
       └── mcp.py               # spike 3 — minimal MCP server with dummy tools
   scripts/
   └── blender/
       ├── adapter.py           # spike 2 — runs INSIDE Blender; stdio JSON-RPC server
       ├── introspect.py        # spike 1 — runs INSIDE Blender; emits raw introspection JSON
       └── README.md            # how to run scripts manually
   docs/
   ├── spikes/                  # spike write-ups (one .md per spike, 1-2 pages each)
   │   ├── 01-bpy-hypergraph.md
   │   ├── 02-blender-rpc.md
   │   ├── 03-mcp-scaffold.md
   │   ├── 04-descriptor-schema.md
   │   └── 05-prior-art.md
   ├── blender_setup.md         # how to install + point Forge at Blender 5.0.0
   └── prior_art.md             # spike 5 deliverable, surfaced from docs/spikes/05
   tests/
   ├── bpy_hypergraph/
   ├── descriptor/
   └── realize/                 # unit tests only; full RPC test gated on FORGE_BLENDER_BIN
   ```
5. **mypy carve-out for Blender-internal scripts.** Add to `[tool.mypy]`: `exclude = ["scripts/blender/.*"]`. Then add a *second*, smaller mypy invocation — `uv run mypy --config-file pyproject.toml --no-incremental scripts/blender` — that uses `fake-bpy-module-latest` stubs and is run as a separate CI step (allowed to be `continue-on-error: true` for Phase 1, hardened later). This is the only acceptable strictness exception in Phase 1: it isolates `bpy` typing to a separately-checked surface without weakening the main package's strict mode.

## Stage B — Spike 1: bpy hypergraph ingestion (PRD §11.1)

**Risk:** R-3, R-6 — incomplete operator coverage, slower than 4-day budget against 5.0's evolving API.
**Branch:** `phase1/spike-1-bpy-hypergraph`. **Time-box: 3 days.**

**Steps**
1. `scripts/blender/introspect.py` — runs as `blender --background --python scripts/blender/introspect.py -- --out raw.json`. Walks `bpy.ops` and `bpy.types` programmatically. For every operator: name, idname, signature parameters with types + defaults, `poll` source (best-effort), `bl_options`, docstring. For every type in a curated allow-list (Mesh, Material, Modifier, Object, Image, Curve, Light, Camera, World, Scene, Collection): properties with types + defaults + descriptions. Emits raw JSON to a path passed by `--out`.
2. `forge_mcp/bpy_hypergraph/ingest.py` — host-side orchestrator:
   - Locates Blender via `FORGE_BLENDER_BIN` env var.
   - Subprocess-runs introspect.py, captures `raw.json`.
   - Parses Sphinx HTML docs *if available locally* (URL-fetch deferred to keep no-network NF-4.1 valid). If docs absent, proceeds with introspection-only; record gap in spike write-up.
   - Hand-curates the v1 operator allow-list (~30–50 ops, list seeded from Architecture §5.4: mesh/modifier/material/curve/light/camera/world/render/save).
   - Derives `alternative_paths` for ops that have a `bpy.data` equivalent in 5.0 (manual mapping table in code; not auto-derived in v1).
   - Loads hand-curated `effects.json` (start with empty + the ~30–50 entries needed for v1 macros).
   - Emits four JSON files under `forge_mcp/bpy_hypergraph/data/` tagged at the top with `"target_version": "5.0.0"` and a `"hypergraph_version": "blender-5.0.0-v1"` field.
3. `forge_mcp/bpy_hypergraph/query.py` — minimal load + query API: `load_hypergraph(path)` returns a frozen Pydantic model; `get_operator(idname)`, `get_type(name)`, `preferred_path(idname)`. Strictly typed.
4. **IDProperty round-trip validation** — actually validated in Spike 2 (more natural with the running adapter). Spike 1 just records that the hypergraph encodes IDProperty access paths needed by `forge_node_id`/`forge_spec_id`/`forge_kind`.
5. **Tests** under `tests/bpy_hypergraph/`:
   - Loading + schema validation of the four committed JSON files (no Blender needed).
   - Coverage assertion: every op listed in Architecture §5.4 appears in `operators.json`.
   - `preferred_path` returns the `bpy.data` form for at least N ops where 5.0 provides one (smoke threshold; refine in Phase 4).
6. **Write-up** `docs/spikes/01-bpy-hypergraph.md` — actual op count, doc-parse coverage, gaps, time spent, go/no-go verdict.

**Go criteria**: ≥30 v1 ops with full param typing; alternative_paths populated for ≥10; tests green; ingestion reproducible via `uv run python -m forge_mcp.bpy_hypergraph.ingest --regenerate`.

## Stage C — Spike 2: Blender RPC + IDProperty round-trip (PRD §11.2, R-4, R-7)

**Branch:** `phase1/spike-2-rpc`. **Time-box: 1 day.**

**Steps**
1. `scripts/blender/adapter.py` — runs inside Blender as `blender --background --python scripts/blender/adapter.py`. Reads JSON-RPC requests from stdin, writes responses to stdout. Methods supported in Phase 1:
   - `ping` → `{ok: true, blender_version: "5.0.0"}`
   - `bpy.ops.<...>` → invokes operator with given params, returns `{ok, result, scene_state_diff}` (diff is a stub: just a delta count of `bpy.data.objects` for now).
   - `bpy.data.<...>` → resolves data API constructor/method.
   - `set_property:<path>` → sets property at JSON-pathed location.
   - `get_property:<path>` → reads property.
   - `set_idprop` / `get_idprop` → IDProperty round-trip on a named object.
   - `shutdown` → exits cleanly.
   Stdout is reserved for JSON-RPC; everything else (Blender's own logs) goes to stderr (`sys.stdout = sys.stderr` immediately after stdio handshake, then a fresh stdout via `os.fdopen(1, "wb")`).
2. `forge_mcp/realize/blender_proc.py` — spawns + manages the long-lived process: `start()`, `stop(timeout=2.0)`, `restart()`, health probe via `ping`. Crash detection on stdout EOF triggers automatic restart; surfaces structured errors. Restart target: <5s end-to-end (NF-3.2).
3. `forge_mcp/realize/rpc.py` — JSON-RPC client over the subprocess: `call(method, params, timeout=10.0)`, request ID counter, structured error mapping. Strictly typed (params/result are `dict[str, Any]` only at the boundary; we live with the one explicit `Any` here — note this is an exception that mypy `disallow_any_explicit` flags; we'll use a `JsonValue` recursive type alias instead and keep zero `Any`).
4. **IDProperty validation script** — a pytest under `tests/realize/test_idproperty_roundtrip.py` that, gated on `FORGE_BLENDER_BIN` env var presence, spawns Blender, creates a cube, sets `obj["forge_node_id"] = "region_test"` + `obj["forge_spec_id"] = "spec_abc"` + `obj["forge_kind"] = "terrain_mesh"`, saves the .blend to a tmp path, restarts Blender, reopens the .blend, asserts all three IDProperties survived round-trip with correct types. **Go/no-go decision recorded in spike write-up:** if unstable, switch to scene-level metadata dict keyed by object name (Architecture §5.6 fallback) before Phase 2.
5. **Tests**: unit tests for rpc.py with a fake subprocess (stdin/stdout pipes piped through an in-process echo); integration test gated on `FORGE_BLENDER_BIN`. Performance assertion: 100 sequential `ping` calls average <50ms each (proxy for "sub-second roundtrip" PRD criterion).
6. **Write-up** `docs/spikes/02-blender-rpc.md` — measured roundtrip latency, restart time, IDProperty verdict, fallback decision if any.

**Go criteria**: 100 pings under 5s total; cold restart <5s; IDProperty round-trip green OR fallback chosen and documented.

## Stage D — Spike 3: MCP server scaffold (PRD §11.3)

**Branch:** `phase1/spike-3-mcp-scaffold`. **Time-box: 0.5 day. Parallel with B, C.**

**Steps**
1. `forge_mcp/server/mcp.py` — minimal server using the official `mcp` Python SDK. Registers three dummy tools that exercise distinct return shapes:
   - `ping()` → `{"ok": True, "version": forge_mcp.__version__}` (sanity).
   - `get_descriptor_schema()` → returns the JSON Schema produced by Pydantic `model_json_schema()` from spike 4 (forces an early integration touchpoint between spikes 3 and 4 — confirms the schema actually survives MCP serialization).
   - `echo(message: str)` → returns the message wrapped in MCP text content (proves typed-input validation works end-to-end).
   Stdio transport by Architecture §14.1.
2. **Manual verification** — load the server in three clients and confirm the tools appear:
   - Claude Code (VSCode) — by far the primary target.
   - Claude Desktop.
   - Cursor (best-effort; document if it diverges).
   Capture screenshots into `docs/spikes/03-mcp-scaffold.md`.
3. `pyproject.toml` `[project.scripts]` — add `forge-mcp = "forge_mcp.server.mcp:main"` so `uv run forge-mcp` is the documented invocation.
4. **Tests** under `tests/server/test_scaffold.py` — in-process: instantiate the MCP server, list registered tools, invoke each with mocked transport, assert response shape. Schema-tool test asserts the JSON Schema returned matches the schema produced by Pydantic directly.
5. **Write-up** `docs/spikes/03-mcp-scaffold.md` — install steps for each client, screenshot of tools listed, any client-specific gotchas.

**Go criteria**: server loads and lists tools in Claude Code; in-process tests green; one round trip per dummy tool succeeds.

## Stage E — Spike 4: Structured descriptor schema draft (PRD §11.4)

**Branch:** `phase1/spike-4-descriptor-schema`. **Time-box: 0.5 day. Parallel with B, C, D.**

**Steps**
1. `forge_mcp/descriptor/schema.py` — Pydantic v2 models that match Architecture §3.3 verbatim:
   - `TerrainPrimary` `StrEnum` with the 12 enum values listed.
   - `StreamCharacter` `StrEnum` (`alpine_creek`, `meandering_river`, `dry_wash`, `none`).
   - `Terrain` model with `primary: TerrainPrimary`, `elevation_band: tuple[float, float] | None`, `ruggedness: float | None` (constrained 0–1), `notes: str | None` (max 200).
   - `Hydrology` model with `has_stream: bool | None`, `stream_character: StreamCharacter | None`.
   - `StructuredDescriptor` model with `terrain: Terrain` (required), `hydrology: Hydrology | None`. `model_config = ConfigDict(extra="forbid", frozen=True)`.
   - `SCHEMA_VERSION = "1.0"`.
2. `forge_mcp/descriptor/validate.py` — `validate(payload: JsonValue) -> StructuredDescriptor | ValidationFailure` returning structured errors (Pydantic `ValidationError` mapped to a flat list of `{path, message, code}` dicts the agent can self-correct from).
3. **Eval set** — `tests/descriptor/eval_descriptors.py` containing 10 free-text → expected structured-descriptor pairs covering the v1 design space (rugged alpine valley + creek, rolling foothills, desert mesa, boreal lowland with river, volcanic cone, coastal cliffs, canyon with dry wash, plains, alpine peaks, marsh). For Phase 1 these are *manually extracted* expected outputs — they form the ground truth for the Phase 5 plan-skill agent test.
4. **Tests** — schema round-trip (model → JSON Schema → re-validate sample), structural assertions on each eval pair, rejection cases (out-of-range ruggedness, unknown enum value, malformed elevation_band, extra field with `extra="forbid"`).
5. **JSON Schema artifact** — emit `forge_mcp/descriptor/schema.json` (committed) via `model_json_schema()`. Add a test asserting it matches the live model output (so accidental drift fails CI).
6. **Write-up** `docs/spikes/04-descriptor-schema.md` — schema, eval pair coverage matrix, anything that felt awkward to express (will inform Phase 3 `TERRAIN_PROFILES` design).

**Go criteria**: schema validates 10 eval pairs; 4+ rejection cases produce structured errors; schema JSON committed and CI-checked for drift.

## Stage F — Spike 5: Prior-art audit (PRD §11.5)

**Branch:** `phase1/spike-5-prior-art`. **Time-box: 0.5 day. Parallel with all others.**

**Steps**
1. `docs/prior_art.md` — short comparison table covering BlenderMCP, Houdini (procedural baseline), Gaea, Wonderdraft, Azgaar, World Machine. Columns: mechanism, scope, agent-readiness, format openness, multi-tool reach. One paragraph per system.
2. Pull out 3–5 differentiator bullets for Forge: (a) hypergraph as project memory, (b) zero-LLM determinism, (c) MCP-native tool surface, (d) typed bpy command vocabulary, (e) cross-tool roadmap.
3. Cross-link from `README.md`.

**Go criteria**: doc exists, reviewed, differentiator framing locked in for the eventual Show HN narrative.

## Stage G — Phase 1 synthesis + go/no-go gate

**Branch:** `phase1/synthesis` (or just a commit on `main` after all spikes merged).

**Steps**
1. Aggregate spike write-ups; confirm every PRD §11 risk has a verdict.
2. If any spike is "no-go," **stop**: revise PRD/Architecture before opening any Phase 2 PR. Specific revision triggers:
   - bpy ingestion <30 ops or alternative_paths sparse → renegotiate v1 macro list.
   - RPC roundtrip slow / restart unstable → reconsider stdio vs ZMQ for v1 (Architecture §2 commits to stdio for v1; revisit if proven wrong).
   - IDProperty refactor breaks round-trip → switch to scene-metadata-dict fallback in `forge_mcp/realize/idmeta.py`; update Architecture §5.6.
   - MCP scaffold fails to load in Claude Code → halt; this is the primary client.
3. Update `AGENT/ARCHITECTURE.md` with any concrete decisions made (Blender pin: 5.0.0; IDProperty verdict; RPC measured latency; ingested op count).
4. Tag — none. Phase 1 is internal de-risking; no version bump.

## Step ordering and dependencies
- Stage A is sequential prerequisite for all spikes.
- Spikes 1, 2, 3, 4, 5 are all parallelizable. Spike 3 has a *soft* dependency on Spike 4 (the `get_descriptor_schema` dummy tool ideally returns the spike-4 schema); if spikes are done in different orders, spike 3 returns a placeholder schema until spike 4 lands and is then patched.
- Spike 2 is a soft dependency for Spike 1 only for the IDProperty validation step; otherwise independent.
- Stage G runs only after all spikes merge.

## Relevant files (Phase 1 deliverables)
- `forge_mcp/bpy_hypergraph/{ingest,query}.py` + `data/{operators,types,effects,alternative_paths}.json` — Spike 1
- `scripts/blender/{introspect,adapter}.py` — Spikes 1 & 2 (Blender-internal Python)
- `forge_mcp/realize/{blender_proc,rpc}.py` — Spike 2
- `forge_mcp/server/mcp.py` — Spike 3
- `forge_mcp/descriptor/{schema,validate,schema.json}` — Spike 4
- `docs/spikes/0{1..5}-*.md`, `docs/blender_setup.md`, `docs/prior_art.md`
- `pyproject.toml` deps + `[project.scripts]` + mypy `exclude` for `scripts/blender/`
- `.github/workflows/ci.yml` — add separate mypy step for `scripts/blender/` using fake-bpy stubs

## Verification (Phase 1 gate)
1. All five spike branches merged; CI green on each.
2. `uv run python -m forge_mcp.bpy_hypergraph.ingest --regenerate` reproduces committed JSON byte-for-byte (determinism smoke test).
3. `FORGE_BLENDER_BIN=/path/to/blender uv run pytest -m blender_integration` runs the IDProperty + RPC tests against real Blender 5.0.0; all green.
4. `uv run forge-mcp` launches; loadable in Claude Code; `ping`/`echo`/`get_descriptor_schema` callable.
5. `tests/descriptor/eval_descriptors.py` 10 pairs all validate; rejection cases produce structured errors.
6. `docs/spikes/0{1..5}-*.md` exist, each with a clear go/no-go verdict.
7. `docs/prior_art.md` exists and is linked from README.
8. Strictness check: zero new entries in ruff `ignore` or `per-file-ignores`; zero `# type: ignore` without code; zero `mypy.overrides` with `ignore_missing_imports`. `JsonValue` recursive alias used at RPC boundary (no explicit `Any`).
9. Architecture §3, §5, §7 updated with concrete numbers (Blender 5.0.0 pin, op count, latency, IDProperty verdict).

## Decisions baked in
- **Blender pin: 5.0.0** (locally installed). All hypergraph + project metadata fields use `blender-5.0.0-v1` tag. PRD/Architecture mentions of 5.0.2 are illustrative; this commits the actual pin.
- **bpy typing strategy:** `fake-bpy-module-latest` as a *dev-only* type-stub dependency. Blender-internal scripts (`scripts/blender/*.py`) excluded from main mypy run; checked separately with stubs (Phase 1: `continue-on-error`; tightened from Phase 4).
- **`JsonValue` over `Any` at RPC boundary** — preserves `disallow_any_explicit`. Recursive type alias defined once in `forge_mcp/realize/rpc.py`.
- **Branch-per-spike PR model** — exercises Phase 0's PR workflow under realistic load and keeps spike rollback cheap.
- **No network** — bpy ingestion does *not* fetch Sphinx docs from blender.org during Phase 1 (NF-4.1 zero-network commitment). Use locally-cached docs if present; otherwise introspection-only and record the gap.
- **Eval-set ground truth is manual** — Phase 1 manually extracts the 10 expected descriptors. The plan skill (Phase 5) is what gets evaluated against them later.

## Out of scope for Phase 1
- Production project format / `ProjectService` (Phase 2).
- Terrain generator, descriptor → spec mapping (Phase 3).
- Realizer engine, macros, full `generate_region` (Phase 4).
- Skills, audit subagent (Phase 5).
- Boundary contracts, canvas (Phase 6).
- Locks, undo, connection map (Phase 7).
- General `bpy` planner — only curated sequences in v1 (Architecture §5.7).
- Coverage threshold (still deferred to Phase 2).

## Confirmed decisions (2026-04-30)
1. **bpy stubs:** pin a specific **5.0-aligned `fake-bpy-module` build** (e.g., `fake-bpy-module-5.0` if published; otherwise the closest tagged release matching 5.0.x). Spike 1 includes a "find-and-pin" step: query PyPI for available variants, pick the closest match to 5.0.0, pin exact version in `[dependency-groups] dev`. If no 5.0 build exists, fall back to `fake-bpy-module-latest` *but* open a tracking issue and revisit before Phase 4.
2. **Branch model:** one PR per spike, **descriptive names — no "phase" or "spike" prefix**. Concretely:
   - Spike 1 → `bpy-hypergraph-ingestion`
   - Spike 2 → `blender-rpc-adapter` (IDProperty round-trip rolled in)
   - Spike 3 → `mcp-server-scaffold`
   - Spike 4 → `descriptor-schema`
   - Spike 5 → `prior-art-audit`
   Stage A prep can ride on the first spike branch or a tiny `dev-deps-and-layout` branch — author's choice.
3. **Sphinx-doc enrichment:** commit a snapshot of the relevant Blender 5.0.0 Sphinx docs into `vendor/blender_docs/` (or a Git submodule pointing at a frozen tag if size becomes an issue). Spike 1 ingestion parses *local* docs only — preserves NF-4.1 zero-network. Add `vendor/blender_docs/` to `.gitignore` for binaries (PNG/screenshots) but keep HTML/JSON/RST tracked. Document the snapshot version + provenance in `docs/blender_setup.md`.
