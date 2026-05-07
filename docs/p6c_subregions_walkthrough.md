# Phase 6-c sanity walkthrough — sub-region predicate nodes (manual)

This walkthrough is the **mandatory manual gate** for closing
Phase 6-c. It exercises the full sub-region surface — typed nodes,
predicate masks, scoped material applications, deterministic plan
resolution, end-to-end Blender render — through a real Claude Code
session and proves that the six new sub-region tools and the
PredicateMask shader-graph path actually shape what lands in
`bpy.data.materials`.

It is the wire-level companion to the integration test in
[`tests/integration/test_sub_region_material_resolution.py`](../tests/integration/test_sub_region_material_resolution.py)
and is referenced from
[`AGENT/dev_phases/phase6_c_subregion.md`](../AGENT/dev_phases/phase6_c_subregion.md)
Phase G ("Verification").

The flow follows the same shape as the Phase 4/5 walkthroughs:
install + register MCP server → drive an agent through the new
sub-region tools → inspect the on-disk `.blend` → exercise the
determinism handshake → confirm the failure modes the service
guarantees. If any step fails to behave as described, **stop** and
follow §7 ("Failure response") — do not silently lower the bar.

---

## 0. Prerequisites

| Requirement                | Why                                                                |
| -------------------------- | ------------------------------------------------------------------ |
| Linux / macOS              | dev target; Windows not supported                                  |
| Python 3.13 + `uv` ≥ 0.9   | enforced by `pyproject.toml`                                       |
| **Blender 5.0.0** binary   | the realizer; pin per Architecture §15                             |
| `FORGE_BLENDER_BIN` env    | absolute path to the Blender 5.0.0 binary                          |
| **Claude Code** CLI        | the v1 reference agent host                                        |
| `git` working tree         | every project tree the agent writes is meant to be diffable        |

Everything in [`docs/p5_sanity_walkthrough.md`](p5_sanity_walkthrough.md)
must already pass on the same machine. Phase 6-c builds on top of
the materials surface (Phase 6-bis) which itself builds on the
realizer (Phase 4) — both must work end-to-end before the predicate
loop is worth exercising.

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

All five must be green. Then run the local integration suite (gated
on `FORGE_BLENDER_BIN`):

```bash
make integration
```

Expect the new
`tests/integration/test_sub_region_material_resolution.py` to pass
alongside the Phase 4 / 6-bis tests. That single test is the
automated counterpart of §3.6 + §3.7 + §3.8 below; the manual smoke
confirms the MCP wire layer behaves the same way under a real agent.

---

## 2. Register the MCP server with Claude Code

```bash
uv run which forge-mcp
# e.g. /workspace/world_forge/.venv/bin/forge-mcp

claude mcp add world-forge \
  --scope user \
  --transport stdio \
  --env FORGE_BLENDER_BIN="$FORGE_BLENDER_BIN" \
  -- "$(uv run which forge-mcp)"
```

Restart Claude Code. In a fresh session:

```
/mcp world-forge ping
```

— the server should respond. Then ask the agent to enumerate tools:

> "List the Forge MCP tools whose name starts with `forge.create_sub_region`,
> `forge.update_sub_region`, `forge.delete_sub_region`,
> `forge.list_sub_regions`, `forge.get_sub_region`, or
> `forge.preview_sub_region_coverage`."

Expected: all six tools surface. The Phase 6-c MCP envelope ships
**53 tools** total (47 from Phase 6-bis + 6 new sub-region tools).

---

## 3. Exercise the sub-region loop

> The exact agent prompts below are reference text. Their phrasing
> can vary; what matters is that the agent's tool calls and the
> server's responses match the expected payloads.

The narrative scenario: a single **alpine_peaks** region called
**Alpha** that should render with grass everywhere by default, snow
on the high ridges, and gravel along the steep faces. Each of the
latter two "looks" maps to a typed sub_region whose name reflects
its predicate.

### 3.1. New project + region

User → agent:

> "Create a new Forge project at `/tmp/p6c_sanity` named `AlpineDemo`,
> world bounds `[[-2000,-2000],[2000,2000]]`. Then add a 4 km × 4 km
> region named `Alpha` centred at the origin, structured descriptor
> `{terrain: {primary: alpine_peaks, elevation_band: [1500, 3500]}}`,
> seed 7."

Expected agent calls:

```text
forge.create_project(path="/tmp/p6c_sanity", name="AlpineDemo",
                     bounds={"min":[-2000,-2000],"max":[2000,2000]})
forge.open_project(path="/tmp/p6c_sanity")
forge.create_region(name="Alpha",
                    polygon=[[-2000,-2000],[2000,-2000],[2000,2000],[-2000,2000]],
                    structured_descriptor={
                        "terrain":{"primary":"alpine_peaks",
                                   "elevation_band":[1500.0,3500.0]}},
                    seed=7)
```

Capture the returned `region.node_id` (call it `$RID`); every
subsequent step reuses it.

> **Why 4 km × 4 km, not the original 10 m × 10 m?** The Phase 6
> Stage A region-extent-aware elevation-band clamp resolves the
> archetype's default 2 km of relief against the polygon's
> bounding-box extent. With a 10 m polygon, `min_extent_m × tan(55°)`
> ≈ 14 m of relief is all the slope-plausibility ceiling allows;
> the spec's elevation band silently shrinks to that range and the
> rendered mesh reads as fine repetitive bumps over a flat slab
> instead of an alpine ridge. A 4 km extent gives `4000 × tan(55°)`
> ≈ 5.7 km of headroom, so the explicit `[1500, 3500]` band passes
> through unclamped and the noise has 2 km of vertical real estate
> to land on. Same caveat as in
> [`docs/p5_sanity_walkthrough.md`](p5_sanity_walkthrough.md#32-draw-a-region)
> §3.2 and the follow-up
> [`AGENT/follow_ups/phase5-elevation-band-scaling.md`](../AGENT/follow_ups/phase5-elevation-band-scaling.md).

### 3.2. Three material archetypes

User → agent:

> "Create three archetypes:
> `valley_grass` flat_color rgba `[0.30,0.55,0.20,1.0]`,
> `alpine_snow` flat_color rgba `[0.96,0.97,0.99,1.0]`,
> `cliff_gravel` triplanar_rock with base_color `[0.42,0.40,0.38,1.0]`,
> roughness 0.85, scale_meters 0.8."

Expected agent calls:

```text
forge.create_material_archetype(name="valley_grass", kind="flat_color",
    parameters={"color":[0.30,0.55,0.20,1.0]})
forge.create_material_archetype(name="alpine_snow",  kind="flat_color",
    parameters={"color":[0.96,0.97,0.99,1.0]})
forge.create_material_archetype(name="cliff_gravel", kind="triplanar_rock",
    parameters={"base_color":[0.42,0.40,0.38,1.0],
                "roughness":0.85,"scale_meters":0.8})
```

The three returned `node_id` values are `$GRASS_ID`, `$SNOW_ID`,
`$GRAVEL_ID`.

### 3.3. Apply the base coat to the whole region

User → agent:

> "Apply `valley_grass` to `$RID` at region scope, priority 0."

Expected agent call:

```text
forge.apply_material(material=$GRASS_ID, target=$RID,
                     attrs={"scope":"region","priority":0})
```

This is the floor: every vertex without a higher-priority match will
shade with `valley_grass`.

### 3.4. Carve the Highlands sub-region (height_band predicate)

User → agent:

> "Add a sub-region of `$RID` named `Highlands` covering elevations
> at or above 2800 m (use a very high upper bound). Then preview its
> coverage so I can see what fraction of the region it captures."

Expected agent calls:

```text
forge.create_sub_region(parent_region=$RID, name="Highlands",
    predicate={"kind":"height_band","low_m":2800.0,"high_m":10000.0})
forge.preview_sub_region_coverage(sub_region_id=$HIGHLANDS_ID)
```

The `create_sub_region` envelope returns `$HIGHLANDS_ID` — a typed
`sub_region` node hanging off `$RID` via a
`LAYER_SPATIAL_CONTAINMENT` edge. The preview envelope reports
`vertex_count`, `coverage_fraction` (0–1), and `bbox_uv` in the UV
square — useful for tuning `low_m` interactively without launching
Blender. (Coverage > 0 is the gate; the exact fraction depends on
the seed-7 heightmap; with the `[1500, 3500]` band a 2800 m cutoff
typically captures 8–18% of the surface.)

User → agent:

> "Apply `alpine_snow` to `$HIGHLANDS_ID` at sub_region scope,
> priority 5."

Expected agent call:

```text
forge.apply_material(material=$SNOW_ID, target=$HIGHLANDS_ID,
                     attrs={"scope":"sub_region","priority":5})
```

The higher priority makes this layer outrank the region-scoped grass
wherever the predicate selects.

### 3.5. Carve the Cliffs sub-region (slope predicate)

User → agent:

> "Add a sub-region of `$RID` named `Cliffs` covering surfaces
> sloped 35°–90° from horizontal, then apply `cliff_gravel` to it
> at sub_region scope, priority 10."

Expected agent calls:

```text
forge.create_sub_region(parent_region=$RID, name="Cliffs",
    predicate={"kind":"slope","min_deg":35.0,"max_deg":90.0})
forge.apply_material(material=$GRAVEL_ID, target=$CLIFFS_ID,
                     attrs={"scope":"sub_region","priority":10})
```

Slope predicates are evaluated in degrees from horizontal (0 = flat,
90 = vertical). Priority 10 puts gravel above snow on overhangs that
sit in both bands.

### 3.6. Resolve the composite plan (deterministic, Blender-free)

User → agent:

> "Resolve the material plan for `$RID` against mesh `terrain_$RID`
> with elevation band `[1500, 3500]` (the spec's actual band). Show
> me the `plan_id` and the `predicate_mask` of each layer. Then
> resolve it a second time with identical arguments and confirm the
> envelopes are byte-identical."

Expected agent calls (twice):

```text
forge.resolve_material(region_id=$RID, mesh_name="terrain_"+$RID,
                       elevation_min=1500.0, elevation_max=3500.0)
```

Expected envelope shape:

```jsonc
{
  "ok": true,
  "result": {
    "plan_id": "mplan_<20 hex>",
    "layers": [
      { "archetype_node_id": "$GRASS_ID",  "predicate_mask": null, ... },
      { "archetype_node_id": "$SNOW_ID",
        "predicate_mask": {"kind":"predicate","predicate":{"kind":"height_band", ...}}, ... },
      { "archetype_node_id": "$GRAVEL_ID",
        "predicate_mask": {"kind":"predicate","predicate":{"kind":"slope", ...}}, ... }
    ]
  }
}
```

The two envelopes must be byte-identical — that is the
*determinism gate* the integration test asserts. Capture both
envelopes for the close-out PR.

### 3.7. Generate + render

User → agent:

> "Generate region `$RID` and show me the preview."

Expected agent call:

```text
forge.generate_region(region_id=$RID)
```

This runs the heightmap pipeline (Phase 3), opens Blender, executes
the `realize_region` macro, then `apply_terrain_material` with the
resolved plan, and finally `render_preview`. The realization summary
in the envelope echoes the same `plan_id` from §3.6 plus the
`elevation_band` actually used.

Manually inspect on disk:

```bash
ls /tmp/p6c_sanity/realizations/
# heightmap/<rid>.npy            lossless terrain
# heightmap/<rid>.png            16-bit preview
# blender/<rid>.blend            the rendered scene
# blender/<rid>.ortho_top.default.png
# blender/<rid>.perspective_se.default.png
# blender/<rid>.*.realization.json

"$FORGE_BLENDER_BIN" --background --python-expr "
import bpy
bpy.ops.wm.open_mainfile(filepath='/tmp/p6c_sanity/realizations/blender/${RID}.blend')
obj = bpy.data.objects['terrain_${RID}']
print([s.material.name for s in obj.material_slots])
"
# → ['forge.material.<plan_id>'] — exactly the plan_id from §3.6
```

That single material slot whose name embeds the deterministic
`plan_id` is the *content-address gate*.

### 3.8. Tweak a predicate and observe the determinism handshake

User → agent:

> "Update `$HIGHLANDS_ID`'s predicate to a higher snow line — band
> `[3100, 10000]` instead of `[2800, 10000]` — and re-resolve the
> material plan."

Expected agent calls:

```text
forge.update_sub_region(sub_region_id=$HIGHLANDS_ID,
    predicate={"kind":"height_band","low_m":3100.0,"high_m":10000.0})
forge.resolve_material(region_id=$RID, mesh_name="terrain_"+$RID,
                       elevation_min=1500.0, elevation_max=3500.0)
```

The new `plan_id` is deterministically *different* (the canonical
layer JSON changed) but everything else — archetype parameters,
layer order, mask shapes — is unchanged. Re-running
`forge.generate_region` produces a new
`forge.material.<new_plan_id>` data-block in a fresh `.blend`.

### 3.9. Delete-in-use refusal

User → agent:

> "Delete `$HIGHLANDS_ID`."

Expected envelope:

```jsonc
{
  "ok": false,
  "error": {
    "reason_code": "sub_region_in_use",
    "message": "sub_region '<id>' is referenced by 1 material_application edge(s); unapply first"
  }
}
```

The service refuses while a `material_application` edge still points
at the sub_region. Have the agent unapply the snow edge first
(`forge.unapply_material edge_id=...`), then re-issue
`forge.delete_sub_region`. The second call succeeds; the parent
region's heightmap and the other sub_region (`Cliffs`) are
untouched — predicate evaluation is lazy, so removing a sub_region
is just an edge + node delete.

---

## 4. Predicate-evaluation isolation (no Blender)

The coverage preview tool is the cheap escape hatch: it must run
without a realizer factory installed and without writing anything to
the project tree. From a second Claude Code session pointed at a
Forge server registered **without** `FORGE_BLENDER_BIN`:

User → agent:

> "Open `/tmp/p6c_sanity` and run `forge.preview_sub_region_coverage`
> on the Cliffs sub_region. Do not generate or render anything."

Expected: the envelope returns `vertex_count`,
`coverage_fraction`, and `bbox_uv` without ever touching Blender.
If it raises `realizer_not_configured` or otherwise touches the
realizer, the isolation guarantee is broken — file as a Phase 6-c
regression before merging anything else.

---

## 5. Acceptance checklist

Tick all of these before opening the close-out PR:

- [ ] `uv run pytest -q --cov=forge_mcp --cov-branch --cov-fail-under=90` exits 0
- [ ] `uv run forge-schema-export --check` exits 0
- [ ] `make integration` passes the new
      `test_sub_region_material_resolution.py`
- [ ] Claude Code session creates a project, applies a base coat,
      attaches Highlands (height_band) + Cliffs (slope) sub_regions,
      and renders the preview inline (§3.1 – §3.7)
- [ ] Two `forge.resolve_material` calls produce byte-identical
      envelopes (§3.6 determinism gate)
- [ ] `forge.preview_sub_region_coverage` returns a non-zero
      `coverage_fraction` for `Highlands` (§3.4)
- [ ] Updating a predicate produces a deterministically *different*
      `plan_id` (§3.8)
- [ ] The rendered `.blend` exposes a single material slot whose
      name embeds the deterministic `plan_id` (§3.7 content-address
      gate)
- [ ] `forge.delete_sub_region` refuses while applications reference
      the sub_region (§3.9)
- [ ] Sanity transcript + both resolve envelopes + preview PNG
      committed under [`docs/eval/phase6c/sanity/`](eval/phase6c/sanity/)

---

## 6. Tear down

```bash
rm -rf /tmp/p6c_sanity
claude mcp remove world-forge
```

The Forge MCP server has no global state outside the project tree
itself; removing the project directory and unregistering the server
is enough. The sanity transcripts and harness output remain in git
history.

---

## 7. Failure response

If Claude Code does not reliably drive the new sub-region tools:

1. Iterate **only** the surfacing of the tools — sharpen the
   docstrings on `forge_mcp/server/tools/sub_regions.py`, but do
   **not** widen the predicate schema, relax the determinism gate,
   or split the predicate-mask × application-mask multiply.
2. Re-run §3. The five gates listed in the integration test are
   fixed by the phase plan.
3. After two iterations without success, surface the failure as a
   phase blocker in
   [`AGENT/dev_phases/phase6_c_subregion.md`](../AGENT/dev_phases/phase6_c_subregion.md)
   §"Verification" — escalate, do not silently lower the bar.

If `forge.generate_region` succeeds but the rendered `.blend` shows
the grass base everywhere (no snow / gravel discrimination), the
shader-graph predicate-factor builder in `scripts/blender/adapter.py`
is the suspect: the `_build_predicate_factor` dispatch should land
on `_build_height_band_factor` / `_build_slope_factor` and *not* on
the warning-emitting fallback. Check the realization trace
(`*.realization.json`) for the per-step `material.build_composite`
payload — it should carry a `predicate_mask` for the snow and gravel
layers.
