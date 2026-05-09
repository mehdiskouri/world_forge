# Plan: Phase 6-f Environment Layer

Add an environment hypergraph layer (sun, sky, fog, ambient, time/season) plumbed end-to-end through descriptor → project state → resolver → curated sequence → adapter → realized Blender world. Mirror the Phase 6-e material pattern (recipe enum + validator registry + adapter builder dispatch + curated-sequence step + RPC method allowlist). Ship one PR per stage with `make integration` between stages.

## Locked decisions
1. **Scope**: world default + optional per-region override (mirrors material `apply` semantics).
2. **Parameterization**: `EnvironmentRecipe` enum (`clear`, `overcast`, `sunset`, `night`, `procedural_sky`). HDRI deferred.
3. **Sun**: derived from `latitude`/`longitude` + ISO `datetime` (UTC) via NOAA solar position algorithm. No explicit az/el knobs; the *recipe* picks intensity/color, not direction.
4. **HDRI**: out of scope for v1. Reserve enum slot in docstring only; do not add the value.
5. **Doc**: `AGENT/dev_phases/phase6f_environment.md` (full phase doc, not a follow-up note).

## Stage status

| Stage | Title | Status | PR |
| --- | --- | --- | --- |
| A | Schemas, project state, schema export | shipped | #70 |
| B | Solar position helper | shipped | #71 |
| C | Resolved environment plan + resolver | shipped | #72 |
| D | RPC surface, curated sequence, fixed-method allowlist | shipped | #73 |
| E | Adapter builders (Blender world + sun lamp) | shipped | #74 |
| F | MCP tool surface + generation pipeline integration | shipped | #75 |
| G | Phase doc, integration test, gates, ship | shipped | this PR |

## Stages

### Stage A — Schemas, project state, schema export
- Add `EnvironmentRecipe` enum + `EnvironmentParameters` model (sun_color RGBA, sun_intensity_w_m2, sky_zenith RGBA, sky_horizon RGBA, ambient_color RGBA, ambient_strength, fog_color RGBA, fog_density, fog_height_falloff, season `Literal["spring","summer","autumn","winter"]`, datetime_utc `datetime`, latitude/longitude floats with bounds [-90,90]/[-180,180], plan_id `str`) in `forge_mcp/project/schemas.py`.
- Add `EnvironmentNode` (`kind: Literal["environment"]`, id, recipe, parameters, region_id `RegionId | None`). Convention: `region_id is None` ⇒ world default; non-null ⇒ region override.
- Add `EnvironmentNodeId` NewType.
- Extend `WorldRootNode` with `environment_id: EnvironmentNodeId | None = None`.
- Extend `RegionNode` with `environment_id: EnvironmentNodeId | None = None`.
- Add `environments: dict[EnvironmentNodeId, EnvironmentNode]` to `ProjectState` + `_load_environments` + project file path.
- Add `create_environment` / `update_environment` / `delete_environment` / `list_environments` / `get_environment` mutators.
- Add `bind_environment(scope_id)` and `unbind_environment(scope_id)` mutators that update WorldRootNode.environment_id or RegionNode.environment_id (writes history events).
- Extend hypergraph `NodeRecord` union in `forge_mcp/hypergraph/core.py` to include `EnvironmentNode` (so traversal sees it).
- Add `EnvironmentNode` to `forge_mcp/project/schema_export.py` registry → emits `schemas/environment.schema.json`.
- `uv run forge-schema-export --write`.
- Tests: state mutator round-trips, hypergraph union narrows, schema regen drift-free.

### Stage B — Solar position helper
- New module `forge_mcp/environment/__init__.py` + `forge_mcp/environment/sun.py` implementing NOAA SPA (deterministic, pure stdlib `math` + `datetime`).
- Function: `compute_sun_direction(latitude_deg, longitude_deg, when_utc) -> SunDirection` returning azimuth_deg, elevation_deg, world-space unit vector `(x, y, z)` with Z=up, X=East, Y=North.
- Validate latitude/longitude bounds. Reject naïve datetimes (must carry tzinfo=UTC).
- Tests in `tests/environment/test_sun.py`: known reference points (e.g., London noon summer solstice ≈ 62° elevation; Quito equinox noon ≈ 90°) within 0.5° tolerance; determinism; tz-naïve rejection.

### Stage C — Resolved environment plan + resolver
- New module `forge_mcp/realize/environment/` mirroring `realize/material/`:
  - `plan.py`: `ResolvedEnvironment` dataclass (recipe, parameters dict, sun_direction tuple, sun_az_deg, sun_el_deg, scope_label, plan_id, plan_index).
  - `defaults.py`: `_VALIDATORS: dict[EnvironmentRecipe, Callable]` covering each recipe (clear/overcast/sunset/night/procedural_sky). Validates RGBA shapes, fog_density >= 0, ambient_strength in [0,1], etc.
  - `resolver.py`: `resolve_environment(service, scope_id) -> ResolvedEnvironment` — looks up region's `environment_id` first, falls back to world root's, then to a hard-coded `_DEFAULT_ENVIRONMENT` (clear daylight, equator, noon UTC). Computes sun via Stage B.
- Tests: each recipe validator (positive + negative cases), resolver fallback chain (region → world → default), validator-registry exhaustiveness (frozen-set check covers every enum value).

### Stage D — RPC surface, curated sequence, fixed-method allowlist
- Add `RpcMethods.WORLD_BUILD_ENVIRONMENT = "world.build_environment"` in `forge_mcp/realize/rpc.py`.
- Add to `_FIXED_ADAPTER_METHODS` in `forge_mcp/bpy_hypergraph/sequences.py`.
- New curated sequence `apply_environment` in `forge_mcp/bpy_hypergraph/data/curated_sequences.json` with one step: `world.build_environment` taking `recipe`, `parameters`, `sun_direction`, `sun_az_deg`, `sun_el_deg`, `plan_id`.
- Add `realize_environment` macro in `forge_mcp/realize/macros.py` that calls the curated sequence.
- Tests: sequence parses, allowlist contains the new method, macro façade types validate.

### Stage E — Adapter builders (Blender world + sun lamp)
- In `scripts/blender/adapter.py`:
  - `_handle_world_build_environment(payload)` — entrypoint dispatched in `_HANDLERS`.
  - `_ENVIRONMENT_BUILDERS: dict[str, Callable]` keyed by recipe value.
  - Per-recipe builders construct a world with cached name `forge.world.<plan_id>`:
    - Clear/overcast/sunset/night → `ShaderNodeBackground` + `ShaderNodeMixRGB` for zenith/horizon gradient via `ShaderNodeTexCoord`+`ShaderNodeSeparateXYZ`+`ShaderNodeMapRange`.
    - `procedural_sky` → `ShaderNodeTexSky` (Nishita) with sun direction wired from payload.
  - Volume scatter for fog: `ShaderNodeVolumeScatter` + `ShaderNodeVolumeAbsorption` mixed by density; height falloff via geometry-position.
  - Ambient: world background strength + a hidden `WORLD` color contribution.
  - Sun lamp: cached `forge.sun.<plan_id>` SUN light object; orient by `sun_direction` (build rotation matrix from `(az, el)` or use `Vector.to_track_quat`). Set energy from `sun_intensity_w_m2`. Idempotent: replace prior `forge.sun.*` and `forge.world.*` for that plan_id.
  - Bind world to scene via existing `scene.assign_world` after build (or fold inline).
- Extend `tests/realize/material/_bpy_fake.py` (or create `tests/realize/environment/_bpy_fake.py`) with `FakeWorldDB`, `FakeLightDB`, `FakeWorld` carrying `node_tree`. Reuse existing fake-node primitives.
- Tests: adapter handler creates world+sun deterministically; cache hits on rebuild; each recipe wires expected node types; sun direction matches input vector within float epsilon.

### Stage F — MCP tool surface + generation pipeline integration
- Register tools in `forge_mcp/server/mcp.py`:
  - `forge.create_environment`, `forge.update_environment`, `forge.delete_environment`, `forge.list_environments`, `forge.get_environment`
  - `forge.bind_environment` (params: scope_id), `forge.unbind_environment`
  - `forge.resolve_environment` (returns ResolvedEnvironment as JSON)
- Add to `forge_mcp/server/tools/environments.py` (new file) following `materials.py` pattern.
- Wire into `forge_mcp/server/tools/generation.py`: `_run_realizer` invokes `realize_environment` macro for the region's effective scope after material realization. Carry `EnvironmentResolveError` raised by validators as a structured tool error.
- Tests: server tool round-trip; generation pipeline calls environment macro; non-binding region falls back to world default.

### Stage G — Phase doc, integration test, gates, ship
- Create `AGENT/dev_phases/phase6f_environment.md` with PRD pointer, decisions, and stage status table.
- Add `tests/integration/test_environment.py`: bootstrap project, create world environment + region override, run `forge.generate_region`, reopen blend with `BlenderProcess`, assert `bpy.data.worlds["forge.world.<plan_id>"]` exists, sun lamp present with expected rotation, scene.world bound correctly, override beats default when present.
- Run full gates: `uv run pre-commit run --all-files`, `uv run mypy`, `uv run pytest --cov=forge_mcp --cov-report=term-missing` (≥90%), `make integration` (must reach 14/14 with new test).
- Open PR, poll checks, squash-merge, sync main.

## Step dependency graph
- A is the foundation; everything depends on it.
- B is parallel with A (pure stdlib, no schemas).
- C depends on A + B.
- D depends on C.
- E depends on D (RPC method must exist) but Blender code is parallel to C.
- F depends on A (state CRUD) and D (macro). Generation hook depends on E being callable.
- G depends on all prior.

Recommended order: A → B (parallel) → C → D → E → F → G, with B kicked off alongside A.

## Relevant files
- `forge_mcp/project/schemas.py` — add EnvironmentRecipe, EnvironmentParameters, EnvironmentNode, EnvironmentNodeId; extend WorldRootNode + RegionNode.
- `forge_mcp/project/service.py` — `ProjectState.environments`, `_load_environments`, environment mutators, `bind_environment` history events.
- `forge_mcp/project/schema_export.py` — register `EnvironmentNode` (and `EnvironmentParameters` if shared).
- `forge_mcp/hypergraph/core.py` — extend `NodeRecord` union.
- `forge_mcp/environment/sun.py` — NOAA SPA implementation.
- `forge_mcp/realize/environment/{plan,defaults,resolver}.py` — mirror `realize/material/` layout.
- `forge_mcp/realize/rpc.py` — `RpcMethods.WORLD_BUILD_ENVIRONMENT`.
- `forge_mcp/realize/macros.py` — `realize_environment` façade.
- `forge_mcp/bpy_hypergraph/sequences.py` — extend `_FIXED_ADAPTER_METHODS`.
- `forge_mcp/bpy_hypergraph/data/curated_sequences.json` — add `apply_environment` sequence.
- `scripts/blender/adapter.py` — `_handle_world_build_environment`, `_ENVIRONMENT_BUILDERS`, sun-lamp + world-shader helpers.
- `forge_mcp/server/tools/environments.py` (new) + `forge_mcp/server/mcp.py` registrations.
- `forge_mcp/server/tools/generation.py` — call `realize_environment` macro.
- `tests/environment/`, `tests/realize/environment/`, `tests/server/test_environments.py`, `tests/integration/test_environment.py`.
- `schemas/environment.schema.json` (regenerated).
- `AGENT/dev_phases/phase6f_environment.md`.

## Verification
1. `uv run pytest tests/environment/test_sun.py` — solar position reference cases.
2. Validator-registry exhaustiveness test mirrors `test_recipe_registry.py`.
3. `uv run forge-schema-export --check` — schema drift gate.
4. `uv run mypy` strict, zero errors.
5. `uv run pre-commit run --all-files`.
6. `uv run pytest --cov=forge_mcp` — ≥90% coverage maintained.
7. `make integration` — 14/14 (new env integration test included).
8. Manual: open generated `.blend`, confirm scene world is `forge.world.<plan_id>`, sun lamp orientation matches expected solar position for fixture lat/lon/datetime.

## Scope boundaries
- **In**: world default + region override; 5 procedural recipes; derived sun; volumetric fog via world volume; ambient via background strength; per-recipe validators; one curated sequence + RPC method; full state CRUD + MCP tools; integration test.
- **Out**: HDRI/EXR loading; multiple sun lamps; weather animation over time; per-frame timeline; volumetric clouds; eevee-vs-cycles divergence (assume Cycles defaults); season-driven foliage tints (just metadata for v1); locking semantics specific to environment.

## Risks / further considerations
1. **NOAA SPA precision**: full SPA is ~50 lines; a simpler NREL "low-precision" formula is ~15 lines and accurate to ~0.1°. Recommend the low-precision form unless a test demands sub-arcminute accuracy.
2. **Sun rotation convention**: Blender Sun lamps emit along -Z by default. Need an explicit helper (e.g., `Vector((-x,-y,-z)).to_track_quat('-Z','Y')`) to map our (E,N,Up) vector to lamp rotation. Will add a unit test using fake mathutils.
3. **Volumetric fog cost**: world-volume scatter is expensive in Cycles. Default `fog_density=0.0` so existing renders aren't slowed; document cost in phase doc.
4. **Hypergraph NodeRecord union currently omits SubRegionNode**. Extending it for EnvironmentNode is a small behavior change — verify no test asserts the old narrow union explicitly.
