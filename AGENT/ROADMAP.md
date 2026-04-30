# Plan: Forge v1 — High-Level Phase Roadmap

Forge v1 is an agent-native worldbuilding MCP server: deterministic Python (zero LLM), Blender 5.0.x as the v1 realizer, descriptor extraction lives in the agent (plan skill is load-bearing), terrain-only single axis end-to-end. Target: 6–7 weeks. The roadmap below decomposes the PRD §10 timeline plus the Pre-week-1 spike checklist into discrete phases starting from Phase 0 (repo bootstrap). Each phase is independently verifiable and ends in a demonstrable state. Phases 1+ correspond roughly to PRD weeks; Phase 1 covers the pre-week-1 spikes that gate everything else.

## Phase 0 — Repo bootstrap (`world_forge`)
**Outcome:** Public GitHub repo `world_forge` exists, clone-able, with green CI on an empty scaffold.
**Steps**
1. Create remote repo `world_forge` (public) via `gh repo create`; set default branch `main`; push initial commit.
2. Add `.gitignore` covering: Python (`__pycache__`, `.venv`, `*.pyc`), uv (`.uv/`), build artifacts (`dist/`, `*.egg-info`), Blender outputs (`realizations/`, `*.blend`, `*.blend1`), node modules for canvas, OS junk, IDE files.
3. Bootstrap minimal Python project with `uv`: `pyproject.toml` (Python 3.13, project name `forge`), placeholder `forge_mcp/__init__.py`, empty `tests/`.
4. Add `LICENSE` (MIT or Apache-2.0 — TBD with user), `README.md` skeleton, `AGENTS.md`/`CLAUDE.md` placeholder pointing to `AGENT/` docs.
5. Add CI/CD: GitHub Actions workflow `ci.yml` running on push/PR — uv setup, `uv sync`, `ruff check`, `ruff format --check`, `mypy forge_mcp`, `pytest -q`. Use matrix on Ubuntu (Linux primary) with Python 3.13.
6. Add `pre-commit` config (ruff, ruff-format, end-of-file-fixer, trailing-whitespace) and document `pre-commit install`.
7. Add branch protection on `main` (require CI green, no direct push) — optional, document if not enforced.

**Verification:** repo reachable; `git clone && uv sync && pytest` succeeds locally; CI green on initial PR; pre-commit runs cleanly.

---

## Phase 1 — Pre-week-1 spikes (architectural de-risking)
**Outcome:** All four PRD pre-week-1 risks are validated or surfaced; architecture decisions locked.
**Steps** (largely parallel; some depend on Blender 5.0 install)
1. **Blender 5.0.x install + version pin.** Choose specific patch (e.g., 5.0.2); document install path; record in `docs/blender_setup.md`. *Blocks 2, 3.*
2. **bpy hypergraph ingestion spike** (PRD §11.1). Headless Blender script walks `bpy.ops` + `bpy.types`, parses Sphinx docs, hand-curates ~30–50 v1 operators with effects + alternative `bpy.data` paths, emits `forge_mcp/bpy_hypergraph/data/*.json` tagged `blender-5.0.2-v1`. *2–3 days.*
3. **Blender RPC + IDProperty spike** (PRD §11.2 + R-7). Headless Blender 5.0 with stdio JSON-RPC adapter; verify persistent state, sub-second roundtrip, crash-restart, custom IDProperty round-trip. Decide IDProperty vs scene-metadata-dict fallback. *0.5–1 day. Parallel with 2.*
4. **MCP server scaffold** (PRD §11.3). Minimal `forge_mcp/server/mcp.py` using official Python `mcp` SDK with 2–3 dummy tools (e.g., `ping`, `get_version`); verify loads in Claude Code, Claude Desktop, Cursor. *0.5 day. Parallel with 2, 3.*
5. **Structured descriptor schema draft** (PRD §11.4). Author Pydantic models matching Architecture §3.3; export JSON Schema; manually validate extraction against 10 free-text test descriptors. *0.5 day. Parallel with all.*
6. **Prior-art audit** (PRD §11.5). Short markdown comparing Forge to BlenderMCP, Houdini, Gaea, Wonderdraft, Azgaar, World Machine; capture differentiators. *0.5 day. Parallel.*

**Verification:** bpy hypergraph JSON exists and loads; RPC spike demo (start Blender, send 5 calls, kill, restart, repeat); MCP scaffold appears in Claude Code's tool list; IDProperty decision recorded; schema validates 10 test cases; prior-art doc reviewed. **Gate:** any serious blocker → revise PRD before Phase 2.

---

## Phase 2 — Project format + tool scaffolding (PRD week 1) — **complete**
**Outcome:** A user can hand-edit a Forge project on disk, open it via an agent, and list regions through MCP.
**Steps**
1. Pydantic schemas (`forge_mcp/project/schemas.py`) for: `project.json`, region nodes, edges, specs, boundaries, locks, history, audits — match Architecture §3.
2. `ProjectService` (`forge_mcp/project/service.py`): `create_project`, `open_project`, `save_project`, `close_project`, atomic write-temp-then-rename (NF-3.1). Filesystem layout per Architecture §3.
3. Hypergraph in-memory representation + JSON serialization (containment, adjacency, hydrology layers).
4. History append-only log + `undo` stub (full undo in Phase 7).
5. MCP tools: project tools, region CRUD (`create_region`, `update_region`, `delete_region`, `list_regions`, `get_region`), `get_descriptor_schema`, basic hypergraph queries. Polygon non-overlap validation (F-6.5).
6. Adjacency auto-detection on region create/update (F-6.6) — emits boundary stubs (contracts arrive Phase 6).
7. Unit tests: schema round-trip with golden files, project save/load, polygon validation, adjacency detection.

**Verification:** create+open+save project via MCP from Claude Code; agent lists regions; project directory diffable in git; `pytest` green; CI green.

---

## Phase 3 — Descriptor mapping + terrain generator (PRD week 2) — **complete**
**Outcome:** A structured descriptor + seed → heightmap PNG returned via MCP tool call. Determinism verified.
**Steps**
1. `forge_mcp/descriptor/`: `schema.py` (Pydantic, finalized), `validate.py` (structured error reporting), `map_to_spec.py` with `TERRAIN_PROFILES` lookup tables for every enum value (Architecture §4.2).
2. `forge_mcp/generate/terrain.py`: ridged multifractal noise + hydraulic erosion + thermal erosion, all with explicit RNG (Architecture §4.3). numpy + scipy; consider numba for erosion inner loops if perf needs it (NF-1.2 ≤30s for 1km² @ 2m/px).
3. `forge_mcp/generate/stream.py`: stream feature injector (anchors stubbed; full anchors in Phase 6).
4. `forge_mcp/analyze/terrain_analysis.py`: numerical analysis output (elevation stats, slope histogram) for `analyze_region`.
5. Heightmap storage: `.npy` internal, 16-bit PNG for Blender ingestion (Architecture §14.4).
6. Wire `analyze_region` and partial `generate_region` (no Blender yet) tools.
7. Build the 5-descriptor eval set (R-2 mitigation); iterate `TERRAIN_PROFILES` until visually distinct.
8. Determinism tests: same (descriptor, seed) → byte-identical heightmap.

**Verification:** invoke `generate_region` → receive heightmap PNG + analysis JSON; eval-set heightmaps visually distinct; perf budget met on dev machine; determinism test green.

**Landed in:** PRs #24 (typed spec body), #25 (deterministic RNG + pass registry), #26 (descriptor → spec mapping), #27 (terrain noise + erosion + heightmap I/O), #28 (deterministic stream injector), #29 (orchestrator + analysis), #30 (agent-facing generation tools), #31 (eval set + acceptance contact sheet). Acceptance artefact: [`docs/eval/phase3/`](../../docs/eval/phase3/README.md).

---

## Phase 4 — bpy 5.0 realizer (PRD week 3)
**Outcome:** Heightmap → `.blend` file with terrain mesh; preview PNG returned through MCP `generate_region`.
**Steps**
1. Blender adapter script (Architecture §7.2): stdio JSON-RPC, dispatches `bpy.ops.*`, `bpy.data.*`, `set_property:*`. Lives inside Blender process.
2. `forge_mcp/realize/blender_proc.py`: spawn/manage long-lived headless Blender 5.0; restartability with <5s recovery (NF-3.2).
3. `forge_mcp/realize/rpc.py`: stdio JSON-RPC client; timeouts; structured error surfacing.
4. `forge_mcp/realize/engine.py` `RealizerEngine`: executes curated sequences from bpy hypergraph; pre/post-condition checks; prefers `bpy.data` paths.
5. `forge_mcp/realize/macros.py`: implement v1 macros from Architecture §5.5 — `reset_scene`, `create_terrain_from_heightmap`, `apply_terrain_material`, `carve_stream`, `set_camera_overview`, `add_basic_lighting`, `render_preview`, `save_blend`, composite `realize_region`.
6. Custom IDProperties applied per Architecture §5.6 (or fallback chosen in Phase 1).
7. Wire full `generate_region`: descriptor → spec → heightmap → realize → preview + analysis returned via MCP image content (F-12.5).
8. Add `render_view` tool (preview/default/full resolutions, ortho_top + perspective_se).
9. Integration tests: full regenerate loop on CI with Blender 5.0 installed; IDProperty round-trip; perf <60s end-to-end (NF-1.3); image <200KB at 1024×768 (NF-1.5).

**Verification:** end-to-end `generate_region` call returns terrain `.blend` + preview PNG in chat; CI integration test green with Blender 5.0.

---

## Phase 5 — Skills + audit subagent (PRD week 4)
**Outcome:** Agent drives end-to-end region creation from free-text user prompts, with audit feedback.
**Steps**
1. Author `forge.plan/SKILL.md` with full structured descriptor schema embedded inline + 10+ worked examples + workflow + tool call patterns + common pitfalls (Architecture §6.1).
2. Author `forge.visualize/SKILL.md`, `forge.audit/SKILL.md`, `forge.cleanup/SKILL.md`, `forge.connect/SKILL.md`.
3. Implement audit subagent invocation (Architecture §14.6): client subagent mechanism preferred, inline isolated-context fallback. Verdict format JSON-schema'd; persisted under `audits/`.
4. Skill tests (Architecture §12.3): plan skill against Claude Code with 10 free-text descriptors; verify extracted descriptors match expected; iterate skill content until extraction is reliable.
5. Mid-week sanity check (R-9): end-to-end "user free text → terrain in Blender" against Claude Code.

**Verification:** clean session — user types "rugged alpine valley with stream", gets terrain; audit subagent flags one intentional descriptor mismatch; success criterion §8.3 (descriptor coherence) achievable.

---

## Phase 6 — Boundary contracts + popup canvas (PRD week 5)
**Outcome:** Two adjacent regions generated; their seam is verified plausible. Canvas page lets user draw polygons.
**Steps**
1. `forge_mcp/boundary/`: `adjacency.py` (already partially in Phase 2), `contract.py` (elevation continuity negotiation per Architecture §8), `apply.py` (inject boundary constraints into spec).
2. Stream-crossing anchors (F-9.4): both sides of a boundary honor anchor points.
3. Conflict surfacing (F-9.5): structured error when locks conflict with contract.
4. `forge_mcp/server/canvas_server.py`: FastAPI mini-app embedded in MCP process; HTTP serves `canvas_page/`; WebSocket pushes state.
5. `canvas_page/`: vanilla TS + Konva.js; polygon drawing tools; posts back as `create_region`/`update_region`. Two delivery modes (VSCode webview + standalone tab).
6. Connection-map view skeleton (full live updates in Phase 7).
7. Integration: drawing on canvas creates regions in the project; agent sees them via `list_regions`.
8. Seam test rigging (PRD §8.1).

**Verification:** PRD success §8.1 (seam test) passes manually; canvas reachable in Claude Code webview and standalone browser; WebSocket update latency <500ms (NF-1.4).

---

## Phase 7 — Locks, reroll, undo + live connection map (PRD week 6)
**Outcome:** Full v1 feature set working; locked features survive seed rerolls; map updates live throughout.
**Steps**
1. `forge_mcp/project/locks.py`: property locks (JSON path), feature locks (heightmap patch capture + blend-back during regeneration), wholesale region locks (skip regen). Architecture §9.
2. Lock tools: `lock_property`, `lock_feature`, `lock_region`, `list_locks`, `unlock`.
3. `reroll_seed` tool: new seed, regenerate honoring locks.
4. Full `undo` implementation: replay history minus last N events; v1 keeps last 50 (F-10.5).
5. Connection map live updates (canvas page): force-directed layout via d3-force; layer toggles; reflects state via WebSocket broadcasts on every Forge state change.
6. Cleanup skill exercised: orphaned specs, stale realizations, conflicting locks detection.
7. Acceptance test §8.2 (regeneration test) rigging.

**Verification:** PRD success §8.2 (lock survives 3 rerolls) passes; connection map test §8.4 passes.

---

## Phase 8 — Polish, edge cases, demo (PRD week 7)
**Outcome:** All four success criteria green on a clean install; 3–5 minute demo video shipped.
**Steps**
1. Edge-case sweep: malformed descriptors, polygon degenerate cases, Blender crash mid-render, IDProperty edge behavior, version-mismatch refusal.
2. Install docs: `README.md` quickstart for Claude Code + Cursor + Claude Desktop; MCP config snippets.
3. Skill installation docs: how to load Forge skills in each supported client.
4. Run all four PRD §8 acceptance tests on a clean machine with a friendly tester.
5. Record 3–5 minute demo against real agent client (Claude Code or Cursor).
6. Tag `v0.1.0`; cut release.

**Verification:** Clean-install run on a second machine completes all four tests; demo video published.

---

## Cross-cutting concerns (apply throughout every phase)
- **Determinism invariant:** every generator takes explicit RNG; no module-level state. Tests assert byte-identical outputs.
- **No LLM in Forge:** PR review check; CI grep for any `openai`, `anthropic`, `httpx` calls into model providers from `forge_mcp/` (allow-list audit subagent invocation indirection).
- **Atomic writes:** every project save uses write-temp-then-rename.
- **Version pinning:** Blender patch version in `project.json`; bpy hypergraph version checked at realizer startup; refuse to load on mismatch (Architecture §15).
- **Schema versioning:** `descriptor_schema_version` in `project.json` from day 1.
- **Git friendliness:** all JSON pretty-printed with stable key order; binaries under `realizations/` always gitignored.

## Relevant files (target structure mapped to phases)
- `pyproject.toml`, `.github/workflows/ci.yml`, `.gitignore`, `.pre-commit-config.yaml` — Phase 0
- `forge_mcp/server/mcp.py`, `forge_mcp/server/tools/*.py` — Phases 1, 2, 3, 4, 6, 7
- `forge_mcp/project/{service,schemas,history,locks}.py` — Phases 2, 7
- `forge_mcp/descriptor/{schema,validate,map_to_spec}.py` — Phases 1, 3
- `forge_mcp/generate/{terrain,stream,deterministic}.py` — Phase 3
- `forge_mcp/realize/{engine,blender_proc,rpc,macros}.py` + Blender adapter script — Phases 1, 4
- `forge_mcp/bpy_hypergraph/{ingest,query}.py` + `data/*.json` — Phase 1
- `forge_mcp/boundary/{adjacency,contract,apply}.py` — Phases 2, 6
- `forge_mcp/analyze/*.py` — Phase 3
- `forge_mcp/skills/forge.{plan,visualize,audit,cleanup,connect}/SKILL.md` — Phase 5
- `forge_mcp/canvas_page/{index.html,canvas.ts,connection_map.ts,styles.css}`, `forge_mcp/server/canvas_server.py` — Phases 6, 7
- `tests/` — every phase

## Decisions / assumptions baked in
- Python 3.13 + uv; Linux-first dev; CI on Ubuntu.
- Blender 5.0.x patch chosen in Phase 1; pinned thereafter.
- MCP transport: stdio for v1.
- Canvas: standalone browser tab first, webview as enhancement.
- IDProperty vs scene-metadata-dict decided in Phase 1 spike.
- License choice deferred to user (recommend Apache-2.0).
- Branch protection on `main`: recommended but not enforced if solo.

## Explicitly out of scope (per PRD §4)
Settlements, vegetation, biomes beyond terrain texturing, multi-tool realizers (Unity/Unreal/MuJoCo), generative 3D assets, marketplace, telemetry, Forge's own UI, any LLM call from inside Forge.

## Confirmed decisions (2026-04-30)
1. **License:** Apache-2.0 (`LICENSE` added in Phase 0).
2. **Visibility:** public from Phase 0.
3. **CI scope:** unit-only CI + lint/type-check; Blender 5.0 integration tests live in a separate `scripts/run_integration.sh` (or `nox -s integration`) executed locally and pre-release. Phase 4 verification updated accordingly: integration test suite must be runnable locally with one command and is gated on Blender 5.0.x install.
