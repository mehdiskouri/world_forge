# Plan: Phase 7 — Locks, reroll, undo + live connection map

Wire the v1 lock semantics (property/feature/region) end-to-end so locked features survive seed rerolls; ship a real bounded-window `undo`; replace the placeholder canvas frontend with a Vite-bundled live connection map; add the cleanup MCP tools the `forge.cleanup` skill already names but cannot call. Ship one PR per stage, `make integration` green between stages, mirroring the Phase 6-f cadence (A through G shipped sequentially).

## Locked decisions
1. **Lock model.** Three `LockKind`s already in `lock.schema.json`: `property`, `feature`, `region`. Phase 7 keeps the same record shape and adds typed payload validators per kind.
2. **Property lock payload.** `{ "json_path": "<dotted.path>", "expected_value": <JsonValue> }`. Enforced on every mutator that writes the targeted node. `expected_value` is whatever the value was at lock time; mismatch on re-read after a mutation triggers `LockViolationError`.
3. **Feature lock payload.** `{ "kind": "heightmap_patch", "bbox_world": [x0,y0,x1,y1], "captured_path": "locks/feature/<lock_id>.npy", "captured_seed": <int>, "captured_at": <iso8601> }`. Stored as a 16-bit `.npy` patch under `<project>/locks/feature/<lock_id>.npy` via the same atomic-write helper used by `realizations/`. On regenerate, the patch is blended back into the regenerated heightmap with a 4-pixel cosine feather.
4. **Region lock payload.** `{ "scope": "skip_regen" }`. Region locks short-circuit `generate_region` after spec compile: existing realization artefacts are kept untouched, history records `region_lock_skipped`.
5. **Undo strategy.** Snapshot-before-mutation. Bounded ring buffer of last 50 `ProjectState` JSON snapshots in memory + a parallel on-disk ring under `<project>/.undo/<n>.json` so undo survives close/open. Heightmap and realization artefacts are NOT snapshotted (too large); undo restores state but leaves on-disk realizations stale (caller can re-run `generate_region`).
6. **Reroll semantics.** `reroll_seed` already exists. Phase 7 makes the regeneration path that follows it lock-aware: feature locks blend back, region locks short-circuit, property locks raise `LockViolationError` if regeneration would alter their target value.
7. **Connection map frontend.** Vite + TypeScript + d3-force-3d. New `forge_mcp/canvas_page/` source tree, `npm run build` emits to `forge_mcp/canvas_page/dist/` (gitignored). CI gains a Node 22 LTS step that runs `npm ci && npm run build && npm run test` before pytest, and packages the built `dist/` into the wheel via `[tool.hatch.build.targets.wheel].include`. Konva canvas (Phase 6) and d3-force connection map share the same bundle entry point with route-based view switch.
8. **Cleanup tools.** Three new read-only-by-default MCP tools: `forge.find_orphans`, `forge.find_stale_realizations`, `forge.find_lock_conflicts`. A single mutating tool `forge.purge_orphans(dry_run=True)` defaults to dry-run; `dry_run=False` requires explicit pass.
9. **Phase doc.** New `AGENT/dev_phases/phase7.md` with PRD pointer, locked decisions, stage status table (mirrors `phase6f_environment.md` shape).
10. **Acceptance tests.** §8.2 "lock survives 3 rerolls" lands in Stage D as an integration test against `BlenderProcess`. §8.4 "connection map" lands in Stage F as a Playwright test against the built bundle.

## Stages (one PR per stage, gates green between)

## Status (post-Stage G)

| Stage | Title | Status | PR |
|---|---|---|---|
| A | Lock service CRUD + 4 mutation MCP tools | shipped | #77 |
| B | Property lock enforcement | shipped | #78 |
| C | Feature + region lock enforcement in regeneration | shipped | #79 |
| D | Lock-aware reroll + §8.2 acceptance integration test | shipped | #80 |
| E | Undo replay implementation (snapshot strategy) | shipped | #81 |
| F | Connection-map frontend (Vite bundle) | shipped | #82 |
| F follow-up | Playwright connection-map e2e | shipped | #83 |
| G | Cleanup MCP tools + phase doc + ship | shipped | #84 |



### Stage A — Lock service CRUD + 4 mutation MCP tools
- Add `LockKind`-specific Pydantic payload models in `forge_mcp/project/schemas.py`: `PropertyLockPayload`, `FeatureLockPayload`, `RegionLockPayload`. Existing `LockRecord.payload` becomes `PropertyLockPayload | FeatureLockPayload | RegionLockPayload` discriminated by `kind`.
- Extend `forge_mcp/project/locks.py` with `LockStore.find_by_target(node_id, json_path=None)`, `LockStore.find_overlapping_features(bbox)`, `LockStore.remove_by_id(lock_id)`. Generate `lock_id` with blake2b-10 over `(kind, region_id, payload, created_at)`.
- Add `ProjectService.create_lock(...)`, `ProjectService.remove_lock(lock_id)` with custom errors `UnknownLockError`, `LockTargetNotFoundError`, `OverlappingFeatureLockError`. Emit history events `lock_created`, `lock_removed`.
- New tool module `forge_mcp/server/tools/locks.py` with: `lock_property(region_id, json_path)`, `lock_feature(region_id, bbox_world)`, `lock_region(region_id)`, `unlock(lock_id)`. Reuse `ok()/fail()` envelope. `lock_feature` reads the current heightmap, slices the bbox, writes the `.npy` patch via `forge_mcp._io.atomic.atomic_write_bytes`.
- Register in `forge_mcp/server/mcp.py` next to `forge.list_locks`.
- Update `tests/server/test_mcp.py::EXPECTED_TOOLS` (4 new entries).
- Schema regen: `uv run forge-schema-export --write` updates `schemas/lock.schema.json` and `schemas/lock_store.schema.json`.
- Tests: payload union round-trip; `lock_feature` writes deterministic `.npy` content; `unlock` removes by id; overlap detection rejects double-locks on same bbox.

### Stage B — Property lock enforcement
- Introduce `forge_mcp/project/lock_enforcement.py` with `check_property_locks(state, node_id, before_doc, after_doc)` that walks `LockStore.find_by_target(node_id)` and raises `LockViolationError(lock_id, json_path, expected, actual)` on first mismatch.
- Wire the check into every existing mutator that updates a node: `update_region`, `update_sub_region`, `update_environment`, `update_material_archetype`, `bind_environment`, `unbind_environment`, `apply_material`, `unapply_material`, `compose_material`, `uncompose_material`. Pattern: snapshot `node.model_dump(mode="json")` before mutation, run mutation, call `check_property_locks(state, node_id, before, after)`, on violation rollback the in-memory state and re-raise.
- `LockViolationError` surfaces as `{"code": "lock_violation", ...}` envelope in every affected MCP tool.
- Tests: locking `terrain.primary` then `update_region` with a different primary fails; locking an unrelated path lets the update through.

### Stage C — Feature + region lock enforcement in regeneration
- Refactor `forge_mcp/generate/terrain.py` orchestrator to accept a `feature_locks: Sequence[FeatureLockPayload]` argument and a `_apply_feature_lock_patches(heightmap, locks)` helper that loads each `.npy`, computes pixel bbox in heightmap coordinates from `bbox_world` + region extent, blends the patch with a 4-pixel cosine feather.
- Refactor `forge_mcp/server/tools/generation.py::_run_generation` to: (a) early-return with `region_lock_skipped` history event if any `RegionLockPayload` matches the region; (b) load feature locks for the region, pass to terrain generator, blend after main heightmap pass and before stream injection; (c) re-check property locks against the regenerated `Region.analysis` and abort with `LockViolationError` if a locked stat would change.
- Add unit tests against `_apply_feature_lock_patches` covering: feather correctness (boundary pixels are weighted), missing patch file → `FeatureLockPatchMissingError`, bbox outside region → `FeatureLockOutOfBoundsError`.

### Stage D — Lock-aware reroll + §8.2 acceptance integration test
- `reroll_seed` already exists; this stage just exercises the Stage C path via reroll. Make `reroll_seed` invoke regeneration directly when `regenerate=True` (new optional kwarg, defaults to False to preserve current contract).
- Add `tests/integration/test_lock_survives_reroll.py`: bootstrap region, generate, capture three named features (one peak, one valley, one slope band) via `lock_feature`, reroll three times with new seeds, reopen blend each time with `BlenderProcess`, assert each captured heightmap patch reads back within 1e-3 elevation tolerance at the patch sample points. Also assert region lock case keeps blend file mtime stable across rerolls.
- This test brings `make integration` from 15/15 → 16/16.

### Stage E — Undo replay implementation (snapshot strategy)
- Add `forge_mcp/project/undo.py` with `UndoStack(maxlen=50)` exposing `push(state)`, `pop() -> ProjectState`. Snapshots are `state.model_dump(mode="json")`; `pop` rebuilds via `ProjectState.model_validate(...)`.
- Disk persistence: `<project>/.undo/<n>.json` written via `atomic_write_text` after every mutation; load on `open_project`. Cap at 50 (FIFO eviction).
- Hook into `ProjectService` via the existing `_notify_subscribers` site (`service.py:399-420`): wrap every public mutator with `with self._undo_capture(): ...` context manager that snapshots `_before_state` on enter, persists `_after_state` on successful exit. On exception inside the context, rollback to `_before_state` (also fixes a current bug where partially-applied mutations can leave state inconsistent).
- Replace `history.undo()` stub with a real implementation backed by `UndoStack`. Update `forge_mcp/server/tools/history.py::undo()` to drop the not_implemented path.
- Tests: undo after `create_region` removes the region; undo after `update_region` restores prior fields; undo across `close_project`/`open_project` works (disk persistence); 51st mutation evicts oldest; undo into pre-create state returns `cannot_undo` error.

### Stage F — Connection-map frontend (Vite bundle) + §8.4 acceptance test
- New `forge_mcp/canvas_page/` Vite + TS source tree:
  - `package.json` with `vite`, `typescript`, `d3-force`, `konva` (for the existing canvas view), `vitest`, `@playwright/test`.
  - `src/main.ts` — entry; route on `?view=canvas` vs `?view=connection-map`.
  - `src/canvas/draw.ts` — Konva polygon-drawing tools (port the placeholder behavior the server currently injects).
  - `src/connection_map/layout.ts` — d3-force layout with layer-toggle UI (containment / adjacency / hydrology layers from `forge.query_layer`).
  - `src/ws_client.ts` — WebSocket client consuming the existing `{type: "snapshot"}` and `{type: "patch"}` envelopes from `canvas_server.py`.
  - `vite.config.ts` outputs to `dist/` with hashed chunks.
- `forge_mcp/server/canvas_server.py`: replace the placeholder HTML branch with a static-mount that serves `dist/`. Falls back to placeholder only if `dist/` is missing (helpful error pointing at `npm run build`).
- CI: add `.github/workflows/ci.yml` step `setup-node@v4` (Node 22), `npm ci --prefix forge_mcp/canvas_page`, `npm run build --prefix forge_mcp/canvas_page`, `npm run test --prefix forge_mcp/canvas_page`. Build artefacts cached on `package-lock.json` hash.
- `pyproject.toml`: include `forge_mcp/canvas_page/dist/**` in the wheel via `[tool.hatch.build.targets.wheel.force-include]`. Ignore `dist/` and `node_modules/` in `.gitignore`.
- Add `tests/canvas/test_connection_map_e2e.py` (Playwright) that boots `canvas_server`, navigates to `?view=connection-map`, creates a region via MCP, asserts the new node appears in the d3-force layout within 500ms (covers PRD §8.4 + NF-1.4). Wire into `make integration`; brings 16/16 → 17/17.

### Stage G — Cleanup MCP tools + phase doc + ship
- New `forge_mcp/server/tools/cleanup.py` with: `find_orphans()` (specs without a region, applications pointing at deleted archetypes, environment bindings to deleted environment ids — all read-only), `find_stale_realizations()` (`<project>/realizations/blender/*.blend` whose `<region>.spec.json` mtime is newer), `find_lock_conflicts()` (locks whose target node no longer exists, or whose `expected_value` no longer matches without a recorded mutation), `purge_orphans(dry_run=True)` (only mutator).
- Update `forge_mcp/skills/forge.cleanup/SKILL.md` to reference the new tools (replace "missing tools" callouts).
- Register all four in `mcp.py`; extend `EXPECTED_TOOLS`.
- Tests: golden-file orphan detection over a synthetic project; `purge_orphans(dry_run=True)` does not write; `purge_orphans(dry_run=False)` removes flagged spec files atomically.
- Create `AGENT/dev_phases/phase7.md` with locked decisions + Stage A–G status table (all shipped).
- Mark Phase 7 complete in `AGENT/ROADMAP.md` (add **complete** suffix to the Phase 7 heading and a "Landed in" footer with PR numbers).
- Final gates + `make integration` 17/17 + ship.

## Step dependency graph
- A → B → C → D (lock semantics chain).
- E (undo) is parallel with B/C/D; can ship after A.
- F (canvas frontend) is parallel with everything from A onward; only depends on the existing canvas_server WS protocol.
- G depends on A (lock conflict detector reads LockStore) and E (cleanup uses snapshot rollback for `purge_orphans`'s atomicity guarantee, optional but cleaner).
- Recommended order: A → B → E (parallel) → C → D → F (parallel with C/D) → G.

## Relevant files
- `forge_mcp/project/schemas.py` — discriminated `LockRecord.payload`, three `*LockPayload` models.
- `forge_mcp/project/locks.py` — extend with target-lookup queries; existing add/remove/list stays.
- `forge_mcp/project/lock_enforcement.py` — new; the `check_property_locks` engine.
- `forge_mcp/project/undo.py` — new; `UndoStack` + disk persistence.
- `forge_mcp/project/service.py` — wrap every mutator with undo-capture context manager; raise `LockViolationError` after each mutation.
- `forge_mcp/project/history.py` — replace `undo()` stub with `UndoStack`-backed implementation (line 143 today).
- `forge_mcp/server/tools/locks.py` — new; 4 lock mutators.
- `forge_mcp/server/tools/cleanup.py` — new; 4 cleanup tools.
- `forge_mcp/server/tools/history.py` — drop the `not_implemented` envelope from `undo()` (line 25 today).
- `forge_mcp/server/tools/generation.py` — feature-lock blend-back, region-lock skip, post-regen property-lock check; `reroll_seed(..., regenerate=False)` kwarg.
- `forge_mcp/generate/terrain.py` — accept `feature_locks` and call `_apply_feature_lock_patches`.
- `forge_mcp/server/canvas_server.py` — replace placeholder HTML branch with static-mount of `forge_mcp/canvas_page/dist/`.
- `forge_mcp/canvas_page/` — new Vite + TS source tree (package.json, vite.config.ts, src/{main,canvas/draw,connection_map/layout,ws_client}.ts).
- `pyproject.toml` — `[tool.hatch.build.targets.wheel.force-include]` for `forge_mcp/canvas_page/dist/`.
- `.github/workflows/ci.yml` — add Node 22 setup + `npm ci` + `npm run build` + `npm run test` before pytest.
- `.gitignore` — `forge_mcp/canvas_page/dist/`, `forge_mcp/canvas_page/node_modules/`.
- `forge_mcp/skills/forge.cleanup/SKILL.md` — point at the four new tools.
- `tests/project/test_locks.py`, `tests/project/test_undo.py` (new), `tests/server/tools/test_locks.py` (new), `tests/server/tools/test_cleanup.py` (new), `tests/integration/test_lock_survives_reroll.py` (new), `tests/canvas/test_connection_map_e2e.py` (new), `tests/server/test_mcp.py` (extend `EXPECTED_TOOLS` with 8 new tool names: `forge.lock_property`, `forge.lock_feature`, `forge.lock_region`, `forge.unlock`, `forge.find_orphans`, `forge.find_stale_realizations`, `forge.find_lock_conflicts`, `forge.purge_orphans`).
- `AGENT/dev_phases/phase7.md` — new phase doc.
- `AGENT/ROADMAP.md` — mark Phase 7 complete in Stage G.

## Verification (at end of each stage)
1. `uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pre-commit run --all-files`.
2. `uv run pytest --cov=forge_mcp --cov-branch --cov-fail-under=90 -q`.
3. `make integration` passes (must stay green at the existing count after Stages A–C, then 16/16 after D, 17/17 after F).
4. Stages A and G also: `uv run forge-schema-export --check` (no JSON-schema drift).
5. Stage F also: `npm run build --prefix forge_mcp/canvas_page` + `npm run test --prefix forge_mcp/canvas_page`.
6. Per-stage: `git push`, open PR, poll `gh pr checks <n>` until pass, `gh pr merge <n> --squash --delete-branch`, `git checkout main && git pull --ff-only`.

## Decisions deferred / explicitly out of scope
- Multi-user locks (single-writer assumption holds for v1).
- Visual lock indicators on the connection map (icon overlay can wait for Phase 8 polish).
- Undo redo (one-way undo only; PRD §F-10.5 only requires undo).
- Cleanup of stale Blender artefacts is detection-only in Phase 7; deletion deferred to user-driven `purge_orphans`.
- HDRI environment locks (HDRI itself is out of scope per Phase 6-f decision 4).
- Lock TTL / auto-expiry (no roadmap requirement).

## Further considerations (raise during refinement if needed)
1. **`update_region` polygon edits vs feature locks**: if a feature lock's bbox falls outside the new polygon after an update, do we (a) auto-remove the lock with a `lock_invalidated` history event, or (b) reject the update with `LockViolationError`? Recommendation: (b) — same behaviour as property locks; user must `unlock` first.
2. **Snapshot size**: a project with 100 regions + materials can produce a multi-MB JSON. 50 snapshots = 100s of MB on disk. Recommendation: keep raw JSON for v1, revisit binary diffs in Phase 8 if tests show pain.
3. **CI Node version**: 22 LTS (Iron) — release line through Apr 2027. Alternative: 20 LTS (Hydrogen). Recommendation: 22.
