

## Plan: `sub_region` node type with predicate-based scoping

### TL;DR
Add `sub_region` as a first-class node living between `region` and `asset` in `spatial_containment`. Each sub_region carries a typed `SubRegionPredicate` (height_band / slope / aspect, closed discriminated union) fixed at creation. The predicate is **evaluated lazily at realize time** against the parent region's persisted heightmap — never cached at create — so re-rolling the parent transparently updates coverage. Material applications targeting sub_regions get folded into the parent region's existing `CompositeMaterialPlan` as additional layers whose mix mask is the predicate wrapped in a new `PredicateMask` MaskSpec variant. The Blender adapter learns one new mask-factor branch that turns predicates into shader-node 0/1 step functions. Zero new RPC methods, zero changes to the recipe registry.

### Phases (sequential, each independently committable)
1. **Phase A — Schema + node type.** `SubRegionId`, `SubRegionPredicate` union (`HeightBandPredicate`, `SlopePredicate`, `AspectPredicate`), `SubRegionNode` (no `spatial_bounds` — predicate IS the extent), `PredicateMask` MaskSpec variant, three new `HistoryEventKind` values. Regenerate published schemas.
2. **Phase B — Service CRUD + persistence.** New `<project>/sub_regions/<id>.json` shard. `create_sub_region`/`update_sub_region`/`delete_sub_region` mirroring archetype CRUD; auto-manage `region → sub_region` spatial_containment edge and parent `RegionNode.children`. Reject delete when a `material_application` edge targets the sub_region (`SubRegionInUseError`).
3. **Phase C — Resolver awareness.** Add `_collect_sub_region_applications` in resolver.py; sub_region apps already outrank region apps via the existing `MaterialScope.SUB_REGION` precedence (rank 3). Wrap the sub_region's predicate into `PredicateMask` and stamp onto `ResolvedLayer.mask`. *Depends on A+B*.
4. **Phase D — Predicate evaluation utility.** New pure-numpy module `forge_mcp/realize/material/predicate.py` with `evaluate_predicate(...)`; expose `compute_predicate_grids(heightmap)` from terrain_analysis.py. *Parallel with C*.
5. **Phase E — MCP tools.** Six new envelope-shaped tools in `forge_mcp/server/tools/sub_regions.py`: `create_sub_region`, `update_sub_region`, `delete_sub_region`, `list_sub_regions`, `get_sub_region`, `preview_sub_region_coverage` (read-only, runs `evaluate_predicate` against the persisted heightmap — no Blender). Tighten `apply_material` so `scope` matches the target node kind. Update `EXPECTED_TOOLS`. *Depends on B+D*.
6. **Phase F — Blender adapter PredicateMask handler.** Extend `_build_mask_factor` in adapter.py with a `kind == "predicate"` branch dispatching to per-predicate-kind shader-node graphs (Geometry/SeparateXYZ/Math). No new RPC, no recipe-registry change. *Depends on A*.
7. **Phase G — Integration test + docs.** New `tests/integration/test_sub_region_material_resolution.py` (peaks + flats, three archetypes, deterministic plan_id, single material slot, coverage non-zero). Unit tests for predicate truth tables and resolver fan-out. Update realization.md "Composite materials" with a Sub-regions subsection. Full canonical suite green at ≥90% coverage.

### Relevant files
- schemas.py — node + predicate + PredicateMask + HistoryEventKind (~L909).
- service.py — CRUD + spatial_containment edge + child-tuple management.
- forge_mcp/project/paths.py — `sub_regions_dir`, `sub_region_path`.
- schema_export.py — extend `iter_published_schemas`.
- resolver.py — `_collect_sub_region_applications`, predicate→mask wrapping.
- `forge_mcp/realize/material/predicate.py` — new module.
- terrain_analysis.py — `compute_predicate_grids`.
- `forge_mcp/server/tools/sub_regions.py` — new file, six tools.
- materials.py — scope/target cross-check in `apply_material`.
- mcp.py — register six tools.
- adapter.py — `_build_mask_factor` predicate branch.

### Verification
1. `uv run forge-schema-export --check` clean after every regeneration.
2. `uv run pytest tests/project tests/realize tests/server tests/bpy_hypergraph` green.
3. `uv run pytest -m blender_integration` green for the new sub_region test.
4. `uv run pytest --cov=forge_mcp --cov-report=term-missing` ≥ 90%.
5. **Determinism gate:** two `forge.resolve_material` calls on the same sub_region-bearing region produce identical `plan_id`; updating one predicate produces a deterministically *different* `plan_id`.
6. **Coverage-preview gate:** `forge.preview_sub_region_coverage` returns non-zero for an in-band predicate, exactly zero for an out-of-band predicate; both succeed without Blender.
7. **Backwards-compat gate:** regions with no sub_regions produce identical `plan_id` before and after migration.

### Decisions & assumptions
- **Predicate evaluated at realize time, never cached.** Re-rolling the parent updates coverage on next `generate_region`.
- **No `spatial_bounds` on sub_regions.** Predicate IS the extent.
- **No nesting in v1.** sub_region → sub_region containment rejected at create.
- **`PredicateMask` is a *new* MaskSpec variant**, not a reuse of `HeightRampMask`/`SlopeMask`. Predicates are boolean selectors; masks are continuous mix factors. One new variant in the discriminated union, one new branch in the adapter.
- **`apply_material` cross-checks `scope` against target node kind** (v1 is pre-public; the tighter contract is free).
- **No new RPC method.** Renders through existing `material.build_composite`.
- **Coverage preview is a separate read-only tool**, not a side-effect of create.

### Further considerations
1. **Predicate vocabulary scope for v1.** Recommend `height_band`, `slope`, `aspect`. include `distance_to_stream` now (extra plumbing — needs the persisted stream geometry as a predicate input).
2. **Resolver mask combination when both an application's own `attrs.mask` AND the sub_region's predicate are present.**  predicate gates the layer's region of effect, application mask modulates within that region; document precedence.
3. **Parent-region edit propagation.**  leave sub_regions intact when the parent region's seed/descriptor changes (they re-evaluate against the new heightmap;preview_sub_region_coverage is the escape hatch)
4.  dedicated `docs/p6c_subregions_walkthrough.md`that includes materials walkthrough steps into a single integrated flow that generates a complete region with proper materials reflective of the title for each subregion to demonstrate e2e success.
