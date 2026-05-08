# Phase 6-e walkthrough — procedural materials + GN grass (manual)

This walkthrough is the **mandatory manual gate** for closing
Phase 6-e. It exercises the four new surface recipes
(`pbr_layered`, `procedural_snow`, `procedural_sand`,
`procedural_water`) and the new geometry-nodes instancer recipe
(`procedural_grass`) end-to-end through a real Claude Code session,
and proves the new failure modes — `recipe_parameter_error`,
`grass_density_too_high` — fire on cue.

It is the wire-level companion to the integration tests in
[`tests/integration/test_pbr_layered_recipe.py`](../tests/integration/test_pbr_layered_recipe.py),
[`tests/integration/test_procedural_water_recipe.py`](../tests/integration/test_procedural_water_recipe.py),
[`tests/integration/test_procedural_grass_recipe.py`](../tests/integration/test_procedural_grass_recipe.py),
and [`tests/integration/test_volume_socket_composite.py`](../tests/integration/test_volume_socket_composite.py),
and is referenced from
[`AGENT/dev_phases/phase6e_PBR.md`](../AGENT/dev_phases/phase6e_PBR.md)
Stage G ("Tests + walkthrough + final gates").

The flow follows the same shape as the Phase 6-c / 6-d walkthroughs:
install + register MCP server → drive an agent through the new
tools → resolve + render the same region under four overlapping
material applications → inspect the rendered `.blend` → confirm the
failure modes the service guarantees. If any step fails to behave
as described, **stop** and follow §6 ("Failure response") — do not
silently lower the bar.

---

## 0. Prerequisites

| Requirement                | Why                                                                |
| -------------------------- | ------------------------------------------------------------------ |
| Linux / macOS              | dev target; Windows not supported                                  |
| Python 3.13 + `uv` >= 0.9  | enforced by `pyproject.toml`                                       |
| **Blender 5.0.0** binary   | the realizer; pin per Architecture §15                             |
| `FORGE_BLENDER_BIN` env    | absolute path to the Blender 5.0.0 binary                          |
| **Claude Code** CLI        | the v1 reference agent host                                        |

Everything in
[`docs/p6d_render_options_walkthrough.md`](p6d_render_options_walkthrough.md)
must already pass on the same machine. Phase 6-e builds on the
materials surface (Phase 6-bis), the sub_region surface (Phase 6-c),
and the realizer (Phase 4); all three must work end-to-end before
the procedural recipes are worth exercising.

---

## 1. Install + automated gates (CI parity)

```bash
git clone https://github.com/mehdiskouri/world_forge.git
cd world_forge
uv sync
uv run pre-commit install
```

Run the same gates CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q --cov=forge_mcp --cov-branch --cov-fail-under=90
uv run forge-schema-export --check
```

All five must be green. The `forge-schema-export --check` gate
includes the Phase 6-e additions to
`schemas/material_archetype.schema.json` (the new `MaterialRecipe`
enum values: `pbr_layered`, `procedural_snow`, `procedural_sand`,
`procedural_water`, `procedural_grass`). Then run the local
integration suite (gated on `FORGE_BLENDER_BIN`):

```bash
make integration
```

Expect **14 passed, 2 deselected**. The four new Phase 6-e
integration tests are:

- `test_pbr_layered_recipe.py` (Stage B)
- `test_procedural_water_recipe.py` (Stage C, with volume socket)
- `test_volume_socket_composite.py` (Stage E, parallel volume mix)
- `test_procedural_grass_recipe.py` (Stage D, GN modifier)

If any of these fail, fix them before proceeding — the walkthrough
exercises the same code paths and will trip on the same bugs.

---

## 2. Register the MCP server with Claude Code

Identical to the Phase 6-d walkthrough §2; no Phase 6-e changes to
the registration surface. The server name is `forge`, transport is
stdio, command is `uv run forge-mcp` from the repo root. Verify with
`claude mcp list` that `forge` is registered and reachable.

---

## 3. Prompt-by-prompt session

Open a fresh Claude Code session in a scratch directory. Use the
prompts below verbatim. After each prompt, copy the agent's
response (or the resulting tool-call envelope) into a scratch file
named `phase6e_walkthrough.md` for later review.

### 3.1 Bootstrap a 4 km region

> **Prompt 1**:
> Create a new Forge project at `~/forge-walkthroughs/p6e/world.forge`,
> open it, and add a 4 km × 4 km region named `valley_floor`
> centred on the origin with seed `0xC011AB`. Use the standard
> alpine-valley descriptor (peak elevation 1600 m, valley floor
> 200 m). Generate the heightmap so we have terrain to texture.

The agent should call `forge.open_project`,
`forge.create_region`, `forge.set_region_descriptor`,
`forge.generate_region`. Expect the envelope's `realization`
block to include `plan_id` (defaulted to the legacy
`principled_height_ramp`) and `elevation_band`. Capture the
`plan_id` — you will use it as a baseline.

### 3.2 Apply `pbr_layered` to the whole region

> **Prompt 2**:
> Create a new material archetype named `bedrock_pbr` using the
> `pbr_layered` recipe with these parameters: base_color
> `[0.42, 0.40, 0.38, 1.0]`, roughness 0.78, specular 0.5,
> metallic 0.0, normal_strength 0.6. Apply it region-wide
> (priority 0). Then re-run `forge.generate_region` and report the
> new `plan_id`.

Expect the `plan_id` to change (different recipe + parameters
hash differently), the rendered `.blend` to gain a
`forge.material.<plan_id>` data-block, and the resolver
preview's single layer to read `recipe: "pbr_layered"`. The
old principled-height-ramp baseline is gone.

### 3.3 Add a snow sub_region with volume scattering

> **Prompt 3**:
> Create a sub_region of `valley_floor` named `peaks_snow` with a
> `height_band` predicate covering `[1200.0, 9999.0]` m. Create a
> `procedural_snow` archetype named `summit_snow` with parameters
> base_color `[0.95, 0.96, 0.98, 1.0]`, sparkle_strength 0.4,
> sparkle_scale 24.0, volume_scatter_density 0.05,
> volume_absorption_density 0.005. Apply `summit_snow` to the
> sub_region with priority 10 (so it wins over the bedrock).
> Re-generate.

Expect the new envelope to expose **two** layers in the
resolver preview: the bedrock at z-low and the snow above
1200 m. The rendered `.blend` should gain a `Material Output`
with both `Surface` and `Volume` sockets linked (Stage E
parallel volume mix). The `plan_id` changes again.

### 3.4 Slope-gated sand on the steep faces

> **Prompt 4**:
> Add a `slope` sub_region of `valley_floor` named
> `eroded_slopes` with a slope predicate `slope_min_deg=35.0,
> slope_max_deg=80.0`. Create a `procedural_sand` archetype
> `talus_sand` with parameters base_color
> `[0.78, 0.66, 0.42, 1.0]`, noise_scale 6.0,
> ripple_strength 0.25. Apply at priority 20. Re-generate.

The slope-mask **must actually mask** in the composite shader
graph — this is the Stage A regression gate. Inspect the
resulting `.blend`'s `forge.material.<plan_id>` node tree and
confirm the sand layer's `MixShader` factor is driven by a
`Geometry > Normal -> Z` chain, not a constant.

### 3.5 Procedural grass on the meadow band

> **Prompt 5**:
> Add a `height_band` sub_region named `meadow` covering
> `[210.0, 800.0]` m. Create a `procedural_grass` archetype named
> `valley_meadow` with parameters: density_per_m2 8.0,
> blade_height_m 0.20, blade_color `[0.18, 0.55, 0.18, 1.0]`,
> slope_max_cos 0.7, rotation_jitter_deg 180.0, scale_jitter 0.3,
> translucency 0.4, seed 12345. Apply at priority 5. Re-generate.

Expect the resolver preview's `meadow`-bound layer to carry an
`instancer` block (`kind: "geometry_nodes"`, `density_per_m2: 8.0`,
`seed: 12345`). The composite material handler **does not** add
that layer to the surface MixShader chain; instead, after
`material.build_composite`, the `material.attach_instancer` step
creates a `forge.instancer.<plan_id>.<index>` `NODES` modifier on
the `terrain_<region_id>` object whose `node_group` is
`forge.geom.grass.<plan_id>.<index>`. Confirm via
`bpy.data.node_groups` in the rendered `.blend` (the agent can
script Blender headless via `forge.run_blender_query` if your
host wires that, or you can open the `.blend` manually).

### 3.6 Procedural water in the basin (volume socket)

> **Prompt 6**:
> Add a `height_band` sub_region named `riverbed` covering
> `[200.0, 215.0]` m. Create a `procedural_water` archetype
> `creek_water` with parameters: base_color
> `[0.04, 0.18, 0.32, 1.0]`, ior 1.33, roughness 0.05,
> volume_scatter_density 0.4, volume_absorption_density 0.6,
> volume_absorption_color `[0.10, 0.30, 0.45]`. Apply at priority
> 30. Re-generate.

Two volume-bearing layers (`procedural_snow` from §3.3,
`procedural_water` here) now contribute to the parallel volume
mix; the realizer should still produce a single
`Material Output.Volume` link. Capture the final `plan_id` —
this is the canonical Phase 6-e composite.

---

## 4. Inspect the rendered `.blend`

Open the final `.blend` in Blender 5.0 (the path is in the
`generate_region` envelope's `blend_path`). Verify:

1. **Object** `terrain_<region_id>` exists and carries:
   - One material slot pointing at `forge.material.<plan_id>`.
   - One `NODES` modifier named `forge.instancer.<plan_id>.0`
     (the grass) whose `node_group` is
     `forge.geom.grass.<plan_id>.0`.
2. **Material** `forge.material.<plan_id>` node tree shows:
   - A linear `MixShader` chain mixing four surface layers
     (bedrock, snow, sand, water).
   - A parallel `MixShader` chain feeding `Material Output.Volume`
     with two contributors (snow + water).
   - The sand layer's `MixShader.Fac` is driven by a slope chain
     (`Geometry > Normal` -> `SeparateXYZ.Z` -> `Compare`), **not**
     a constant.
3. **Geometry-Nodes group** `forge.geom.grass.<plan_id>.0` contains:
   - `GeometryNodeDistributePointsOnFaces` (POISSON, density 8.0).
   - `FunctionNodeCompare` on slope (`Normal.Z >= 0.7`).
   - `GeometryNodeInstanceOnPoints` sourcing the blade via
     `GeometryNodeObjectInfo` -> `forge.grass_blade_obj.*`.
   - `GeometryNodeRealizeInstances` upstream of `Group Output`.
4. **Data-blocks** `bpy.data.meshes["forge.grass_blade.12345.0.2000"]`
   and `bpy.data.materials["forge.material.grass.<plan_id>.0"]`
   exist (the cached blade mesh and per-blade material).

If any of these are missing, the corresponding stage's adapter
builder is broken — check the **last** envelope's
`realization.trace` for the failing RPC.

---

## 5. Trip the new failure modes

### 5.1 `recipe_parameter_error`

> **Prompt 7**:
> Create a `procedural_grass` archetype named `bad_grass` with
> parameters `{"density_per_m2": -1.0}` (negative density).

Expect the envelope to be `{"ok": false, "error": {"code":
"recipe_parameter_error", "message": "density_per_m2 must be
positive ..."}}`. The validator (`_validate_procedural_grass`)
fires before the archetype reaches the resolver.

### 5.2 `grass_density_too_high`

> **Prompt 8**:
> Update `valley_meadow` to `density_per_m2=10000.0` (10 k blades
> per m^2). Re-generate.

For a 4 km x 4 km region the requested primitives are
`10000 * 16e6 = 1.6e11` — well above the 5,000,000 cap.
Expect:

```json
{
  "ok": false,
  "error": {
    "code": "grass_density_too_high",
    "message": "procedural_grass density * area = 160000000000 exceeds cap 5000000 for region ...",
    "details": {
      "region_id": "...",
      "requested": 1.6e11,
      "cap": 5000000.0,
      "area_m2": 16000000.0,
      "density_per_m2": 10000.0
    }
  }
}
```

The cap is enforced by `_check_instancer_density` *before* any
Blender RPC fires, so the failure is fast and cheap.

---

## 6. Failure response

If any §3 prompt produces a different envelope than described, or
§4 finds a missing data-block / modifier / link, **stop** and:

1. Capture the offending envelope verbatim into the scratch
   `phase6e_walkthrough.md`.
2. Re-run `make integration` — if the corresponding integration
   test now also fails, you have a real regression: bisect against
   `main` and open an issue. If the integration test still passes
   but the walkthrough does not, the divergence is in the agent
   layer (Claude tool-call shape, MCP transport, CLI version) —
   capture the wire-level RPC trace from the server logs and
   compare it to the integration test's `proc.client.call` shape.
3. Do **not** "patch around" by lowering the assertion. The
   walkthrough is the contract.

---

## 7. Closeout

Once §3-§5 all pass:

- Commit the captured `phase6e_walkthrough.md` under
  `docs/eval/phase6e/<date>/` for the audit trail.
- Mark Stage G complete in
  [`AGENT/dev_phases/phase6e_PBR.md`](../AGENT/dev_phases/phase6e_PBR.md).
- Phase 6-e is shippable.
