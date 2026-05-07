# Plan: Material Application & Composition Layers + Composite Shader Resolver

End-to-end addition of two new hypergraph layers (`material_application`,
`material_composition`), one new node type (`material_archetype`), a deterministic
material resolver, MCP tool surface, and a replacement of the realizer's monolithic
`material.build_terrain` step with a composite shader-graph builder driven by a
resolved plan. Default archetypes preserve current rendered output for projects
that declare no materials. All schemas regenerated through `forge-schema-export`.

This work is **beyond the documented scope** of Phase 4 (single material) and
Phase 6 (boundary contracts/canvas). Proceeding under user direction; recommend
amending `AGENT/dev_phases/` with a new phase plan capturing this contract before
merge.

---

## Phase A — Schema, persistence, hypergraph foundation

A1. **New node type `MaterialArchetypeNode`** in `forge_mcp/project/schemas.py`:
    - Fields: `id`, `kind="material_archetype"`, `name`, `recipe` (str — selects
      adapter shader-recipe template, e.g. `principled_height_ramp`,
      `triplanar_rock`, `flat_color`), `parameters` (typed dict per recipe),
      `tags`, `notes`. Content-addressed `id` via canonical JSON + blake2b
      (mirror `descriptor/map_to_spec.py` `_content_hash` pattern).
A2. Add `MaterialArchetypeNode` to `NodeRecord` union in
    `forge_mcp/hypergraph/core.py` and update `LayerView`/Hypergraph node lookups.
A3. **Typed edge attribute models** (NOT new top-level Edge models — keep loose
    `Edge.attrs` map but introduce strict Pydantic validator models that the
    service uses on read/write):
    - `MaterialApplicationAttrs` (`scope: world|region_group|region|sub_region|asset`,
      `priority: int`, `parameter_overrides: dict`, `mask: MaskSpec | None`).
    - `MaterialCompositionAttrs` (`mode: extends|composes`,
      `mask: MaskSpec | None`, `weight: float | None`).
    - `MaskSpec` discriminated union: `height_ramp` (stops), `slope` (low/high
      threshold + softness), `constant` (value). Extensible.
A4. **Layer registration**: extend the default layer tuple in
    `forge_mcp/project/service.py` (`_default_layers`) and the metadata default
    in `forge_mcp/project/schemas.py` (line ~748) with `material_application`,
    `material_composition`. Layer names exposed as constants in
    `forge_mcp/hypergraph/core.py` to remove magic strings.
A5. **Edge endpoint validation**: add a per-layer endpoint policy check in
    service write paths.
    - `material_application`: exactly one `material_archetype` endpoint and one
      or more spatial endpoints (`region`/`world_root` in v1).
    - `material_composition`: endpoints must all be `material_archetype`; reject
      cycles via `forge_mcp/hypergraph/traversal.py` DAG check.
A6. **Persistence**:
    - Reuse existing `edges/<layer>.json` mechanism (no new file format) for the
      two new layers.
    - Archetypes stored as nodes inside the existing nodes file (extend project
      load/save in `service.py` ~L578) — keeps a single node store and avoids a
      parallel content path.
A7. **Schema export**: regenerate `schemas/*.json` via
    `forge_mcp/project/schema_export.py` (extend export list ~L64). Verify CI
    drift check passes (`forge-schema-export --check`).
A8. **History event kinds** in the history schema (Pydantic source) and helpers
    in `service.py`:
    - `MATERIAL_ARCHETYPE_CREATED|UPDATED|DELETED`,
    - `MATERIAL_APPLIED|UNAPPLIED`,
    - `MATERIAL_COMPOSED|UNCOMPOSED`.
A9. Tests:
    - `tests/project/test_schemas.py` round-trip new node + edge attrs.
    - `tests/hypergraph/` — layer registration, traversal of new layers, cycle
      rejection in composition.
    - `tests/project/test_schema_export.py` — drift check sees new schemas.

## Phase B — Deterministic material resolver

B1. New module `forge_mcp/realize/material/` with:
    - `plan.py` — Pydantic `CompositeMaterialPlan` IR
      (`layers: list[ResolvedLayer]`, `mesh_id`, `plan_id` content hash).
      A `ResolvedLayer` carries: archetype snapshot (recipe + flattened params),
      mask spec (or `None` for base), and composition order.
    - `resolver.py` — pure-Python resolver:
      1. Walk `spatial_containment` upward from target region to enumerate
         applicable applications (world-scoped applications cascade down).
      2. Sort applications by `(scope_specificity, priority, edge_id)` —
         deterministic tie-break by canonical id.
      3. For each application, resolve composition DAG of the referenced
         archetype (`extends` flattens parameter overrides bottom-up, `composes`
         emits multiple stacked layers with their masks/weights).
      4. Apply per-application `parameter_overrides` last.
      5. Compute `plan_id = blake2b(canonical_json(plan))` analogous to spec id.
B2. **Built-in default archetype catalog** in `forge_mcp/realize/material/defaults.py`:
    - Ship `forge.terrain.height_ramp` archetype reproducing the current
      `_DEFAULT_COLOR_RAMP_STOPS` / `_DEFAULT_SLOPE_THRESHOLD` exactly.
    - When the resolver finds zero applications touching a region, it emits a
      single-layer plan from this default. This is the **backwards-compat
      contract** that keeps existing integration tests stable.
B3. **No RNG in resolver** (composition is fully deterministic from inputs); do
    not extend the `deterministic.py` purpose registry.
B4. Tests in `tests/realize/material/`:
    - Scope precedence (world < region_group < region < sub_region < asset).
    - Composition `extends` flattens params correctly across multiple levels.
    - Composition `composes` preserves mask spec and weight.
    - `plan_id` is stable across reorderings of inputs that should canonicalize
      identically; differs when params differ.
    - Cycle in composition rejected with explicit error.
    - Default-archetype fallback path produces a plan equivalent to today's
      hardcoded color ramp.

## Phase C — MCP tool surface

C1. New module `forge_mcp/server/tools/materials.py` with tools:
    - `forge.create_material_archetype`, `update_material_archetype`,
      `delete_material_archetype`, `list_material_archetypes`,
      `get_material_archetype`.
    - `forge.apply_material(archetype_id, target_node_ids, scope, priority,
      overrides, mask)` and `forge.unapply_material(edge_id)`.
    - `forge.compose_material(parent_id, child_ids, mode, mask, weight)` and
      `forge.uncompose_material(edge_id)`.
    - `forge.list_material_applications(region_id?)` (filtered traversal).
    - `forge.resolve_material(region_id)` — runs the resolver and returns the
      `CompositeMaterialPlan` for inspection (does not realize).
C2. Register all of the above in `forge_mcp/server/mcp.py` `build_server`.
C3. Each mutation tool emits the corresponding history event from Phase A.
C4. Tests in `tests/server/tools/test_materials.py`: tool behavior, validation
    errors (wrong endpoint kinds, cycles, unknown archetype), history events.

## Phase D — Realizer integration (replacement, not parallel path)

D1. **New RPC method** in `forge_mcp/realize/rpc.py`:
    `MATERIAL_BUILD_COMPOSITE = "material.build_composite"`. Drop
    `material.build_terrain` from `RpcMethods`. Keep one path so there is no
    legacy dead branch.
D2. **Curated sequences** in
    `forge_mcp/bpy_hypergraph/data/curated_sequences.json`:
    - Replace the body of `seq:apply_terrain_material` so its single step calls
      `material.build_composite` with a `{plan, mesh_name}` payload bound from
      placeholders. Sequence id is content-hashed; new hash will be produced
      automatically — update fixed-methods list and any pinned hashes.
D3. **Macro facade** in `forge_mcp/realize/macros.py`: typed
    `apply_terrain_material(plan: CompositeMaterialPlan, mesh_name: str)`
    facade. Postcondition `expects` checks: material slot count == 1,
    material name == `forge.material.<plan_id>`.
D4. **Generation tool wiring** in
    `forge_mcp/server/tools/generation.py` `_build_realize_inputs` (~L265):
    - Remove `_DEFAULT_COLOR_RAMP_STOPS` and `_DEFAULT_SLOPE_THRESHOLD`
      constants.
    - Call resolver with `region_id` to obtain the `CompositeMaterialPlan`.
    - Pass plan + mesh name into the macro inputs.
    - Add the `plan_id` to the realization trace metadata via
      `forge_mcp/realize/realization.py` for reproducibility audit.
D5. **Blender adapter** in `scripts/blender/adapter.py`:
    - New `material.build_composite(plan, mesh_name)` dispatcher entry.
    - **Recipe registry**: dictionary keyed by `recipe` string mapping to a
      builder that constructs a single Blender node group from a parameter
      dict. v1 recipes:
      1. `principled_height_ramp` — current behavior generalized
         (parameterized stops + roughness + base BSDF).
      2. `triplanar_rock` — basic triplanar projection driving a Principled
         BSDF (parameterized base color, roughness, scale).
      3. `flat_color` — minimal solid color fallback for sub-region/asset use.
    - **Layer assembly**: walk the plan layer-by-layer; instantiate each
      archetype's node group; combine consecutive layers via `MixShader` driven
      by the mask spec (height ramp / slope from geometry node `Normal`+`Z` /
      constant). Final shader → Material Output. One material slot, named
      `forge.material.<plan_id>`.
    - Remove old `material.build_terrain` builder.
D6. **bpy_hypergraph operator/effect catalog** updates for new shader operations
    that the recipe builders use (ShaderNodeGroup, ShaderNodeMixShader,
    ShaderNodeTexCoord, ShaderNodeMath/Combine for slope) — add to
    `forge_mcp/bpy_hypergraph/data/operators.json` and
    `effects.json` so the curated catalog stays accurate.
D7. Tests:
    - `tests/realize/test_rpc.py` — assert `material.build_composite` present;
      `material.build_terrain` removed.
    - `tests/realize/test_macros.py` — updated facade expectations.
    - `tests/bpy_hypergraph/test_sequences.py` — sequence still present, fixed
      methods list updated.
    - `tests/realize/material/test_adapter_recipes.py` — unit-test recipe
      builders against a stub bpy module (existing pattern in tests/realize).
    - **Blender-gated integration** under `tests/integration/` covering
      `material.build_composite` end-to-end (mirror existing
      `test_blender_proc.py` ~L229 pattern).

## Phase E — End-to-end acceptance

E1. `tests/integration/test_generate_region.py`: existing region with no
    material applications still renders, plan == default archetype, hashes
    stable (the backwards-compat regression gate).
E2. New `tests/integration/test_material_composition.py`:
    - Project with two archetypes (alpine_granite, alpine_snow) and a
      `composes` edge masked by slope.
    - One world-scoped application using the composed material.
    - Generate + render; assert deterministic plan_id, single material slot
      named after plan_id, mesh assignment correct.
    - Re-run with the same inputs → identical plan_id and material name.
E3. Walkthrough docs: add a short `docs/p7_material_layers_walkthrough.md`
    only if user requests; otherwise update `docs/realization.md` with the new
    composite path.
E4. Run full canonical command suite (`uv run ruff check .`,
    `uv run ruff format --check .`, `uv run mypy`,
    `uv run pytest --cov=forge_mcp --cov-report=term-missing`) and ensure
    coverage stays ≥90%.

---

## Relevant files
- `forge_mcp/hypergraph/core.py` — `NodeRecord`, layer registration, layer name constants.
- `forge_mcp/hypergraph/traversal.py` — DAG cycle check helper for composition.
- `forge_mcp/project/schemas.py` — node + edge attr models, default layers (~L748).
- `forge_mcp/project/service.py` — load/save (~L578), default layers (~L95), edge file paths (~L182), CRUD + history (`_append_history` ~L561).
- `forge_mcp/project/schema_export.py` — extend export list (~L64).
- `forge_mcp/realize/material/{plan,resolver,defaults}.py` — new package.
- `forge_mcp/realize/rpc.py` — `RpcMethods` (~L145), drop `build_terrain` add `build_composite`.
- `forge_mcp/realize/macros.py` — `apply_terrain_material` facade.
- `forge_mcp/realize/engine.py` — postcondition handling unchanged; verify.
- `forge_mcp/realize/realization.py` — record `plan_id` in trace.
- `forge_mcp/bpy_hypergraph/data/curated_sequences.json` — replace step body.
- `forge_mcp/bpy_hypergraph/data/operators.json`, `effects.json` — new shader nodes.
- `forge_mcp/server/tools/materials.py` — new MCP tools.
- `forge_mcp/server/tools/generation.py` — drop `_DEFAULT_COLOR_RAMP_STOPS` etc., wire resolver (~L105, L265).
- `forge_mcp/server/mcp.py` `build_server` — register new tools.
- `scripts/blender/adapter.py` — recipe registry + composite builder, drop `build_terrain` (~L312).
- `schemas/*.json` — regenerated, never hand-edited.

## Verification
1. `uv run forge-schema-export --check` passes after regeneration.
2. `uv run pytest tests/hypergraph tests/project tests/realize tests/server tests/bpy_hypergraph` green.
3. `uv run pytest -m integration` (Blender-gated) green for both default and composed materials.
4. `uv run pytest --cov=forge_mcp --cov-report=term-missing` stays ≥90%.
5. Determinism gate: invoke `forge.resolve_material` twice on the same project and assert identical `plan_id`.
6. Backwards-compat gate: rendered scene from the existing `test_generate_region.py` produces the same scene diff hash before/after migration.
7. Manual MCP-tool smoke through `scripts/host/` to create archetype → apply → resolve → realize.

## Decisions & assumptions
- **Archetypes are nodes** (not a parallel resource collection) so that
  `material_application` and `material_composition` edges have node-only
  endpoints, matching the user's specification.
- **Single replacement RPC method** (`material.build_composite`); the legacy
  `material.build_terrain` is removed, not kept as a parallel path. v1 is
  pre-public so no migration shim is owed.
- **Default archetype shipped in code** preserves current visual output for
  projects with no material configuration; this is the regression gate that
  lets us land the change without breaking integration tests.
- **Scope ordering** is fixed:
  `world < region_group < region < sub_region < asset`. `region_group`,
  `sub_region`, `asset` are accepted in the schema but only `world` and
  `region` resolve to real targets in v1 (no node types yet for the others).
  This leaves a forward-compatible enum without shipping unused node types.
- **No RNG**: resolver is deterministic from inputs. `plan_id` is content-hash.
- **Phase discipline**: this work is outside Phase 4/6 contracts; user has
  authorized. Recommend adding `AGENT/dev_phases/phase7-materials.md` capturing
  the new contract before merge.

## Further considerations
1. **Sub-region node type** — defer until a real use case lands; the resolver
   already accepts the scope key, so adding the node later is additive.
2. **Asset-level application** — same: schema accepts the scope, but no node
   kind ships in this slice.
3. **Texture/image assets** — recipe parameters intentionally don't reference
   external image paths in v1 (procedural only); textures can be added as a
   later recipe-only change without touching the layer/resolver design.
