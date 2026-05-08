# Plan: Phase 6-e — Procedural material expansion + slope-mask hotfix

Bring the material surface up from "3 flat-ish recipes + broken slope mask" to a
generalized procedural PBR family that can express grass, powdery snow, sand,
water, dirt — without ever loading an image. Fix the silent slope-mask
fall-through in the adapter as the foundation step so the new recipes can
*rely* on slope masking working.

**Out of scope (firm):** image textures, HDRI environments, biome
auto-assignment, image-based displacement on the material side, hair-physics,
animated grass wind sim. All of those land in later phases.

**Pattern:** Originally planned as a single squashed PR per Phase 6-d
convention, but mid-Stage-A discovery that **no `bpy` test stub exists** in
the repo (all adapter coverage is via real Blender integration tests) makes
the byte-identical IDAT backwards-compat gate in Stages B and E
unverifiable from a sandboxed dev session. Revised cadence: **one PR per
stage**, each landed after `make integration` validates the change against
real Blender. Branch `feat/procedural_materials` carries Stage A only;
Stages B–G open as fresh branches off main as they ship.

**Status:**
- Stage A — **shipped** as PR #62 (slope-mask hotfix; merged 2026-05-08).
- Stage B — **shipped** as PR #63 (additive `pbr_layered` recipe + bpy
  test stub infrastructure). Re-scoped to additive (no legacy wrapper
  retrofit) since the byte-identical IDAT gate is unnecessary risk for
  a recipe that doesn't replace anything. Legacy `flat_color` /
  `triplanar_rock` builders remain unchanged.

---

## Stage A — Slope-mask hotfix (foundation)

The bug: `forge_mcp/project/schemas.py::SlopeMask` is a real Pydantic model
that the resolver passes through to `material.build_composite`, but
`scripts/blender/adapter.py::_build_base_mask_factor` only branches on
`constant` and `height_ramp`; `slope` falls through to `const = weight_value`.
Result: every Phase 6-c "Cliffs" sub-region with a slope predicate composites
at full strength as if its mask were always-on. The Phase 6-c walkthrough
hides this because it uses *predicates* (which work) on a sub-region whose
*application mask* is omitted (so weight=1 anyway).

**Implement:**
1. Add `_build_slope_mask_factor(nodes, links, low, high, softness)` to
   adapter. Domain: cosine-of-normal `|normal.z|` (1=flat, 0=cliff). Build
   `Geometry.Normal → SeparateXYZ.Z → Absolute → smoothstep(low, high)`,
   then optional softness expansion (`low - softness`, `high + softness`)
   with a Math `SMOOTH_STEP` operation. Multiply by `weight_value`.
2. Wire it into `_build_base_mask_factor`: replace the `# Unknown / slope
   mask: fall back` branch with `if kind == "slope": return
   _build_slope_mask_factor(...)` before the catch-all.
3. Keep the catch-all (now genuinely "unknown") writing a stderr warning so
   future schema additions can't silently no-op again.
4. Backwards-compat: existing `constant` + `height_ramp` masks must produce
   byte-identical shaders (regression-tested in
   `tests/realize/test_material_resolver.py` against current goldens).

**Tests (Phase D1 of overall stage list, but lives with Stage A):**
- `tests/realize/material/test_adapter_mask_factor.py` (new) — 3 unit
  fixtures that import the adapter module via the `bpy` stub layer and
  build the slope-mask sub-graph for `(low=0, high=1)`,
  `(low=0.5, high=0.8, softness=0.1)`, `(low=0, high=0)` rejected at
  schema time. Assert node-tree shape (operations, link endpoints, default
  values). Pattern: copy
  `tests/realize/material/test_adapter_predicate_factor.py`.
- Integration assertion in `tests/integration/test_sub_region_material_resolution.py`:
  add a 3rd application carrying a `SlopeMask` and assert the rendered
  IDAT digest changes vs the same plan with the mask removed.

---

## Stage B — Generalized PBR base (`pbr_layered`)

A new general-purpose recipe that subsumes `flat_color`/`triplanar_rock`
without breaking them. Single Principled BSDF with **all channels driven by
optional procedural noise**:

| Parameter (all optional except `base_color`) | Default | Wires into |
| --- | --- | --- |
| `base_color: RGBA`                           | required| BSDF Base Color |
| `base_color_variation: {scale_m, amount}`     | none    | Voronoi(scale) → MixRGB(amount) |
| `roughness: float [0,1]`                       | 0.7     | BSDF Roughness |
| `roughness_variation: {scale_m, amount}`      | none    | Noise(scale) → mix into Roughness |
| `metallic: float`                              | 0.0     | BSDF Metallic |
| `normal_detail: {scale_m, strength}`           | none    | Noise → Bump → BSDF Normal |
| `clearcoat: float`                             | 0.0     | BSDF Coat Weight |
| `triplanar_scale_m: float`                     | 1.0     | Geometry.Position → Vector Scale (shared input vector for all noise nodes above) |

**Builder:** `_build_pbr_layered` in adapter; central helper
`_build_world_position_vector(scale)` reused by all variation paths so they
share the same coordinate frame. Existing `triplanar_rock` and `flat_color`
become *thin wrappers* over `pbr_layered` defaults — single
`_RECIPE_BUILDERS` table maps both old enum values to the same builder with
parameter-shape adaptation, so determinism on legacy plans is preserved
(advisory: re-render-and-diff regression test in §G).

**Validator** (`forge_mcp/realize/material/defaults.py`):
- `_validate_pbr_layered` requires only `base_color`; every other field is
  optional with a typed shape check (the `*_variation` dicts must carry
  both `scale_m > 0` and `amount in [0, 1]`).

---

## Stage C — Biome-targeted procedural recipes (no image textures)

Add three new recipes: `procedural_snow`, `procedural_sand`,
`procedural_water`. Each is a *small* shader-graph builder layered on top
of `pbr_layered`'s helpers. All ship as **shader-only** (no geometry
nodes) so they fit the existing `material.build_composite` MixShader model
unchanged.

### C.1 `procedural_snow` (powdery / packed)
- Principled BSDF: white base, low roughness (0.15), high
  `subsurface_weight` (~0.4), white subsurface color, `subsurface_radius
  ≈ (1.5, 1.5, 1.5) m` for the powdery look.
- **Sparkle**: high-frequency Voronoi (scale ~ 200/m), step-thresholded,
  driving Specular tint up; controlled by `sparkle_density ∈ [0, 1]`
  parameter.
- **Drift bump**: low-frequency Noise → Bump for wind-shaped surface.
- **Optional Volume Scatter** for "powdery depth" — needs Material
  Output's Volume socket. Currently `material.build_composite` only mixes
  Surface shaders via `MixShader`; **Stage E adds a parallel Volume
  composite path** so `procedural_snow` and `procedural_water` can
  contribute volume.
- Parameters: `sparkle_density` (default 0.6), `drift_scale_m` (default
  3.0), `subsurface_strength` (default 0.4), `volumetric_depth`
  (optional float; activates volume socket).

### C.2 `procedural_sand` (dunes / beach)
- Principled BSDF: warm tan base color, roughness ~0.85, low metallic.
- **Grain micro-bump**: 2-octave Voronoi at scale ~50/m → Bump (strength
  ~0.3). Gives the granular shimmer.
- **Wind ripples**: Wave node (bands mode) with parametric `ripple_freq`
  and `ripple_strength`, mixed into normal via Bump. Optional `ripple_aspect_deg`
  rotates the ripple direction.
- **Wet edge darkening**: world-space Z-driven (or
  external-Value-input-driven) darkening curve so beaches darken near
  water level. Parameter `wet_band: {z_low, z_high, darken_amount}` —
  optional; when absent the channel is a no-op.
- Parameters: `base_color`, `grain_scale_m`, `ripple_freq`,
  `ripple_strength`, `ripple_aspect_deg`, `wet_band`.

### C.3 `procedural_water` (lakes / shallow ocean)
- Principled BSDF: low roughness (0.05), `transmission ≈ 1.0`, IOR 1.33,
  base color tint = parametric `water_tint`.
- **Animated wave normals**: two Voronoi noises at differing scales
  added → Bump → Normal. `wave_scale_m`, `wave_strength` parameters.
  No time dimension in v1 (renders are stills).
- **Volume absorption** for depth tint via Volume Absorption node +
  parametric `absorption_density`. Plumbs through Stage E volume
  socket.

### C.4 Validators + enum entries
All three recipes append to `MaterialRecipe` enum, get
`_validate_procedural_*` entries in `_VALIDATORS`, and registered builders
in `_RECIPE_BUILDERS`. Existing `validate_recipe_parameters` stays the
sole entry point.

---

## Stage D — Geometry-Nodes grass (`procedural_grass`)

The big one. Real instanced blades on the terrain mesh, scoped by mask +
predicate just like surface materials, deterministic from the plan
seed. **This is a new realisation channel**, not a MixShader layer — the
composite material remains the surface; the grass adds a Geometry-Nodes
modifier on top.

### D.1 Architectural shape
- New plan-layer field `instancer: ProceduralInstancer | None` on
  `MaterialPlanLayer` (Pydantic, `forge_mcp/realize/material/plan.py`).
  Carries `kind: Literal["geometry_nodes"]`, `recipe`,
  `parameters`, `density_per_m2`, `seed`. When set, the layer's `recipe`
  is interpreted as a geometry-nodes recipe and the layer's `mask` /
  `predicate_mask` modulate the instance density inside the geo-nodes
  graph (via attribute domains), not the surface shader.
- New RPC method `material.attach_instancer` (added to
  `forge_mcp/realize/rpc.py::RpcMethods`) called *after*
  `material.build_composite`, taking `(target_object, plan_id,
  instancer_layers)`. Idempotent: re-running with the same content-hash
  removes prior `forge.instancer.<plan_id>.<index>` modifiers and
  re-creates them.
- `forge_mcp/bpy_hypergraph/data/curated_sequences.json::apply_terrain_material`
  appends an `material.attach_instancer` step *conditional on the plan
  having any instancer layers*. (The `if-step` machinery already exists
  for `realize_region`'s stream branch; reuse the same pattern, or
  fall back to "always call; no-op when empty".)

### D.2 `procedural_grass` recipe
- **Builder** in adapter: `_attach_procedural_grass_modifier(obj, params)`.
  Creates:
  1. A bladed mesh data-block (`forge.grass_blade.<seed>`) — 3-vert
     triangle, height = `blade_height_m`. Re-uses across plans with
     same seed/height. Generated procedurally; no asset import.
  2. A Geometry Nodes node group (`forge.geom.grass.<plan_id>.<idx>`)
     containing:
     - Distribute Points on Faces (Poisson, `density = density_per_m2`,
       `seed = seed`).
     - Selection inputs: cosine-of-normal threshold for slope masking;
       Z-band cutoff for height masking; Voronoi-based density jitter
       for organic clumping.
     - Instance on Points using the blade mesh.
     - Per-instance Random Rotation + Scale jitter (parametric
       `rotation_jitter_deg`, `scale_jitter`).
     - Realize Instances → output to modifier.
  3. A separate `forge.material.grass.<plan_id>.<idx>` material assigned
     to the blade mesh: green-spectrum `pbr_layered` with translucency
     channel (translucent BSDF mixed at parametric
     `translucency` weight).
- Parameters: `density_per_m2` (default 200), `blade_height_m` (0.15),
  `blade_color: RGBA` (default green), `slope_max_cos` (0.8 — only on
  near-flat ground), `height_band: {z_low, z_high}` (optional
  altitude clamp), `rotation_jitter_deg` (180), `scale_jitter` (0.3),
  `translucency` (0.4), `seed` (taken from plan layer seed if omitted).
- Determinism: Distribute Points seed is `seed`; output bytecount of
  the modifier inputs only changes when parameters change → plan_id
  is a sufficient cache key.

### D.3 Predicate gating
The geo-nodes graph reads world-space position + surface normal at
distribution time, so `height_band`, `slope`, and `aspect` predicates
that are already in the schema can be *re-implemented inside the
geometry-nodes graph* using the same threshold math as the shader-side
predicate factors (Stage A unifies the slope code path; reuse the same
constants). `distance_to_stream` predicate stays unrealised in v1
(consistent with the existing shader behaviour) — emits the same
stderr warning.

### D.4 Performance ceiling
Hard-cap `density_per_m2 × surface_area_m2 ≤ 5_000_000` blades in the
resolver to prevent runaway memory; raise structured
`grass_density_too_high` error (new error code in
`forge_mcp/server/tools/generation.py` error registry). Under EEVEE
this typically renders in <10 s for a 4 km² region at the cap.

---

## Stage E — Composite material: parallel Volume socket

Currently `_handle_material_build_composite` mixes layer surface sockets
through `MixShader` and wires the result to
`Material Output.Surface`. Volume scattering recipes
(`procedural_snow`, `procedural_water`) need a parallel mix path
ending at `Material Output.Volume`.

**Implement:**
1. Builders return a richer struct: `BuilderResult(surface: Socket,
   volume: Socket | None)` instead of a bare surface socket. Existing
   builders return `volume=None` and their output behaviour is
   unchanged.
2. The composite loop maintains two running sockets
   (`composite_surface`, `composite_volume`); when a layer contributes a
   volume, it's mixed into the running volume via `MixShader` using the
   *same* `fac_socket` (mask × predicate) the surface uses, so volume
   contributions are scoped to the same region as surface contributions.
3. `composite_volume` connects to Material Output Volume only when
   non-None (avoids forcing volume rendering on plans with no volume
   layers).
4. Backwards-compat: plans whose layers all return `volume=None` produce
   a Material Output with no Volume link — byte-identical to today.
   Verified by golden-IDAT regression.

---

## Stage F — Resolver, schemas, validators

1. **`forge_mcp/realize/material/plan.py::MaterialPlanLayer`** — add
   optional `instancer: ProceduralInstancer | None = None` field;
   continues to be content-hashed into `plan_id` so two regions with
   identical instancer setups share both the surface material AND the
   geo-nodes modifier.
2. **`forge_mcp/realize/material/resolver.py`** — when an archetype's
   recipe is in `_INSTANCER_RECIPES = {MaterialRecipe.PROCEDURAL_GRASS}`,
   the resolver emits the layer with `instancer=ProceduralInstancer(...)`
   instead of as a surface layer. (A single archetype application is
   either surface XOR instancer; mixed plans split into multiple
   layers, one per role.)
3. **Recipe-set CI test** in `tests/realize/material/test_recipe_registry.py`
   asserts that `MaterialRecipe` enum, `_VALIDATORS`, `_RECIPE_BUILDERS`
   (queried from a stubbed `bpy` import) and the JSON schema all stay
   exhaustive — no recipe can land without all four touch-points.
4. **Schema regen**: `uv run forge-schema-export --check` must include
   the new optional fields (no new top-level schemas — `plan.py` flows
   through `material_plan.schema.json`).

---

## Stage G — Tests + walkthrough + final gates

1. **Unit tests** (Python, no Blender):
   - Resolver: per-recipe parameter-merge tests (Phase 6-bis pattern),
     ProceduralInstancer round-trip, slope-mask plumbing, validator
     rejections for each new recipe.
   - Adapter sub-graph builders: for each new recipe, assert node-tree
     topology against a golden (using the existing `bpy` test stub
     pattern from `tests/realize/material/test_adapter_*.py`).
2. **Integration tests** (Blender-gated, `make integration`):
   - Extend `tests/integration/test_material_composition.py`: one new
     test per recipe (snow, sand, water, grass) renders a region with a
     single application of that recipe at full strength, asserts non-zero
     PNG, IDAT digest stable across 2 runs (determinism gate).
   - New `tests/integration/test_geometry_nodes_grass.py`: applies
     `procedural_grass` to a sub-region with a slope predicate, opens
     the resulting `.blend`, asserts a `forge.geom.grass.*` modifier
     exists on the terrain object and that
     `obj.evaluated_get(deps).data.vertices` count > unmodified count
     (proves the modifier evaluates instances).
   - **Slope-mask backwards-compat gate**: re-render the Phase 6-c
     walkthrough's region under both pre-fix code (saved IDAT digest in
     `docs/eval/phase6c/*.png` if extant; otherwise capture in this PR)
     and post-fix; assert the digest changes for plans whose
     applications carry slope masks AND stays identical for plans with
     only constant/height-ramp masks.
3. **Walkthrough** `docs/p6e_procedural_materials_walkthrough.md` —
   §3 prompt-by-prompt format from Phase 6-c/d. Single 4 km region;
   agent applies (a) `procedural_snow` to a height-band-predicated
   "Highlands" sub-region, (b) `procedural_sand` to a slope-mask-gated
   beach (forces the slope-mask fix to fire), (c) `procedural_grass` to
   a slope-predicated meadow, (d) `procedural_water` to a low-Z mask;
   inspects the rendered `.blend`. Includes a §4 "failure response"
   with the new error codes (`grass_density_too_high`, the existing
   `recipe_parameter_error`).
4. **`docs/realization.md`** — new "Procedural recipes" subsection
   listing the 7 recipes (3 legacy + 4 new) with their parameter
   tables; cross-reference the walkthrough.
5. **Final gates** (CI parity):
   - `uv run pre-commit run --all-files`
   - `uv run ruff check . && uv run ruff format --check .`
   - `uv run mypy`
   - `uv run pytest -q --cov=forge_mcp --cov-branch --cov-fail-under=90`
   - `uv run forge-schema-export --check`
   - `make integration` — all existing tests + the new ones green.
6. **Backwards-compat gate**: any region whose archetypes use only the
   3 legacy recipes (`flat_color`, `triplanar_rock`,
   `principled_height_ramp`) and only `constant`/`height_ramp` masks
   renders byte-identical to pre-Phase-6-e. Tested by the golden-IDAT
   regressions in step 2 above.

---

## Step ordering and dependencies

- **A** (slope-mask hotfix) lands first; `git`-mergeable as its own
  half-PR if reviewers prefer, or as the first commit on the feature
  branch. Everything else depends on it because Stages C/D rely on
  slope masking actually working.
- **B** (`pbr_layered`) is parallelisable with A; pure refactor +
  superset of existing recipes.
- **E** (volume socket) sequenced before C, because `procedural_snow`
  / `procedural_water` validators reference volume parameters whose
  shapes E defines.
- **C** (snow/sand/water) depends on B + E.
- **D** (grass + instancer plumbing) depends on A + (the `pbr_layered`
  helpers from B for the blade material). Schema work in F must be
  partially landed before D's tests run.
- **F** (resolver/plan/schema) interleaves with B–D.
- **G** (tests/walkthrough/docs) is last.

---

## Relevant files (additions + edits)

```
forge_mcp/
├── project/
│   └── schemas.py                          # add ProceduralInstancer; no
│                                           #  changes to SlopeMask itself
├── realize/
│   ├── material/
│   │   ├── defaults.py                      # +4 validators, +pbr_layered defaults
│   │   ├── plan.py                          # MaterialPlanLayer.instancer field
│   │   └── resolver.py                      # split surface XOR instancer roles
│   └── rpc.py                               # +RpcMethods.MATERIAL_ATTACH_INSTANCER
├── bpy_hypergraph/
│   └── data/
│       └── curated_sequences.json           # apply_terrain_material gains
│                                           # conditional attach_instancer step
└── server/tools/generation.py               # +grass_density_too_high error code

scripts/blender/
└── adapter.py                               # _build_slope_mask_factor;
                                             # _build_pbr_layered;
                                             # _build_procedural_{snow,sand,water};
                                             # _attach_procedural_grass_modifier;
                                             # BuilderResult struct;
                                             # composite Volume socket plumbing

schemas/                                     # regen via forge-schema-export
├── material_plan.schema.json                # +instancer
└── ...

tests/
├── realize/material/
│   ├── test_adapter_mask_factor.py          # NEW — slope-mask sub-graph unit
│   ├── test_recipe_registry.py              # NEW — exhaustiveness gate
│   └── test_resolver.py                     # +procedural_* coverage
└── integration/
    ├── test_material_composition.py         # +1 test per new recipe
    └── test_geometry_nodes_grass.py         # NEW

docs/
├── realization.md                           # +Procedural recipes subsection
└── p6e_procedural_materials_walkthrough.md  # NEW

AGENT/dev_phases/
└── phase6_e_procedural_materials.md         # NEW — phase plan doc
```

---

## Verification (per-stage gates, copy into PR description)

| Stage | Gate                                                                |
| ----- | ------------------------------------------------------------------- |
| A     | new mask-factor unit test green; slope-mask integration test changes IDAT vs no-mask baseline; legacy-mask renders byte-identical |
| B     | `pbr_layered` builder unit test green; `flat_color` + `triplanar_rock` legacy IDAT digests unchanged |
| C     | per-recipe integration test green; volume socket attached only on snow/water plans |
| D     | grass integration test asserts modifier present + evaluated mesh has >100k extra vertices on a small fixture; 5M-blade cap raises `grass_density_too_high` |
| E     | composite legacy IDAT digests unchanged; volume-only plans still render |
| F     | `forge-schema-export --check` clean; recipe-registry exhaustiveness test green |
| G     | full `make integration` green; coverage ≥ 90 %; walkthrough's 4 sub-region renders captured under `docs/eval/phase6e/` |

---

## Open questions to resolve before/during implementation

1. **Volume rendering + EEVEE**: EEVEE legacy renders volume scatter only
   inside meshes flagged as "volumetric"; the terrain mesh is a surface,
   so a volume socket on it is a no-op. Two options:
   (a) ship volume support but document it as Cycles-only — agents must
       pass `render_options={"engine": "CYCLES"}` to see powdery-snow
       depth or absorptive water tint;
   (b) defer volume entirely to a later phase and ship snow/water as
       surface-only in 6-e.
   **Recommendation: (a).** The Phase 6-d render-options surface exists
   precisely so agents can opt in to Cycles; documenting the dependency
   is honest and ships the feature today. The volume socket then gates
   itself out on EEVEE renders cleanly.
2. **`pbr_layered` as superset**: should `flat_color` and
   `triplanar_rock` be deprecated, or kept as "easy-mode" aliases? Plan
   above keeps them as thin wrappers (zero parameter migration cost).
   **Recommendation: keep as wrappers.** Removing enum values breaks
   on-disk projects; deprecation can land in a later phase if at all.
3. **Per-blade material vs vertex colour**: should grass blades carry a
   full `pbr_layered` material or just a vertex-colour-driven simple
   shader? Plan above attaches a per-plan `pbr_layered` material so
   designers can tune blade appearance.
   **Recommendation: full material.** Cost is one extra material data-
   block per grass plan (cheap); benefit is the blades respect the same
   PBR controls as everything else.
