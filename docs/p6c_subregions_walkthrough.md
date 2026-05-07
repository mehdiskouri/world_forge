# Phase 6-c walkthrough — sub-region predicate nodes end-to-end

This walkthrough traces a single material wiring all the way from a
fresh project to a Blender-rendered preview, focusing on the new
sub-region surface introduced in Phase 6-c. It composes the
materials walkthrough (Phase 6-bis) and the realization walkthroughs
(Phases 4–5) into one continuous flow.

The narrative scenario: a single rolling-hills region called
**Alpha** that should render with grass everywhere by default, snow
above an elevation line, and gravel along the steep faces. Each of
those three "looks" maps to a typed sub_region whose name reflects
its predicate.

All commands assume:

```bash
cd world_forge
uv sync
export FORGE_PROJECT_ROOT="$(mktemp -d)"
export FORGE_BLENDER_BIN=/usr/bin/blender   # any real Blender 5.0
```

Whenever a step says *"call ``forge.<tool>``"* you can run the tool
either through the MCP transport from Claude Code, or directly from
Python (the integration test
[`tests/integration/test_sub_region_material_resolution.py`](../tests/integration/test_sub_region_material_resolution.py)
is the reference Python translation).

---

## 0. Prerequisites

* CI gates pass locally: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q --cov=forge_mcp --cov-report=term`.
* `forge.list_tools` reports the six new sub_region tools alongside
  the eleven materials tools (53 total in Phase 6-c).
* `FORGE_BLENDER_BIN` points at a real Blender 5.0 binary (the
  realization, walkthrough, and sub-region integration tests all
  skip when this is missing).

---

## 1. Bootstrap the project and the parent region

```text
forge.create_project   path=$FORGE_PROJECT_ROOT name=AlpineDemo
                       bounds={"min":[0,0],"max":[10,10]}
forge.create_region    name=Alpha polygon=[[0,0],[10,0],[10,10],[0,10]]
                       seed=7
                       structured_descriptor={"terrain":{"primary":"rolling_hills"}}
```

Capture the returned ``region_id`` (call it ``$RID``); every
subsequent tool reuses it.

---

## 2. Define the three material archetypes

```text
forge.create_material_archetype name=valley_grass kind=flat_color
    parameters={"color":[0.30,0.55,0.20,1.0]}
forge.create_material_archetype name=alpine_snow  kind=flat_color
    parameters={"color":[0.96,0.97,0.99,1.0]}
forge.create_material_archetype name=cliff_gravel kind=triplanar_rock
    parameters={"base_color":[0.42,0.40,0.38,1.0],
                "roughness":0.85,
                "scale_meters":0.8}
```

The first call returns ``$GRASS_ID``, the second ``$SNOW_ID``, the
third ``$GRAVEL_ID``.

---

## 3. Apply the base coat to the whole region

```text
forge.apply_material material=$GRASS_ID target=$RID
                    attrs={"scope":"region","priority":0}
```

This is the floor: every vertex without a higher-priority match will
shade with ``valley_grass``.

---

## 4. Carve the highlands sub-region (height_band predicate)

```text
forge.create_sub_region parent_region=$RID name=Highlands
    predicate={"kind":"height_band","low_m":120.0,"high_m":10000.0}
```

The returned ``$HIGHLANDS_ID`` is a typed
``sub_region`` node hanging off ``$RID`` via a
``LAYER_SPATIAL_CONTAINMENT`` edge. Confirm coverage *before*
generating any heightmap by running the cheap, Blender-free preview:

```text
forge.preview_sub_region_coverage sub_region_id=$HIGHLANDS_ID
```

The envelope reports ``vertex_count``, ``coverage_fraction`` (0–1),
and ``bbox_uv`` — useful for tuning ``low_m`` interactively without
launching Blender.

Apply the snow material with sub_region scope:

```text
forge.apply_material material=$SNOW_ID target=$HIGHLANDS_ID
                    attrs={"scope":"sub_region","priority":5}
```

The higher priority makes this layer outrank the region-scoped grass
wherever the predicate selects.

---

## 5. Carve the cliffs sub-region (slope predicate)

```text
forge.create_sub_region parent_region=$RID name=Cliffs
    predicate={"kind":"slope","min_deg":35.0,"max_deg":90.0}
forge.apply_material material=$GRAVEL_ID target=$CLIFFS_ID
                    attrs={"scope":"sub_region","priority":10}
```

Slope predicates are evaluated in degrees from horizontal (0 = flat,
90 = vertical). Priority 10 puts gravel above snow on overhangs that
sit in both bands.

---

## 6. Resolve the composite plan (deterministic, Blender-free)

```text
forge.resolve_material region_id=$RID mesh_name=terrain_$RID
    elevation_min=-300.0 elevation_max=300.0
```

The envelope returns a :class:`CompositeMaterialPlan`:

* ``plan_id`` — content-addressed ``mplan_<20 hex>``;
* ``layers`` — three :class:`ResolvedLayer` entries, in priority
  order, each carrying its archetype parameters, ``MaskSpec``, and
  the new ``predicate_mask`` field (``None`` for the grass base,
  ``height_band`` for snow, ``slope`` for gravel).

Re-running the same call produces the byte-identical envelope —
that is the *determinism gate* the integration test asserts.

---

## 7. Generate + render

```text
forge.generate_region region_id=$RID
```

This runs the heightmap pipeline (Phase 3), opens Blender, executes
the ``realize_region`` macro, then ``apply_terrain_material`` with
the resolved plan, and finally ``render_preview``. The realization
summary in the envelope echoes the same ``plan_id`` from §6 plus
the ``elevation_band`` actually used.

The on-disk artefacts (in ``$FORGE_PROJECT_ROOT/realizations/``):

```
heightmap/$RID.npy            # lossless terrain
heightmap/$RID.png            # 16-bit preview
blender/$RID.blend            # the rendered scene
blender/$RID.preview.png      # the camera preview
blender/$RID.trace.json       # per-step trace
```

Open the ``.blend`` and confirm the terrain object's material slot
is named ``forge.material.<plan_id>`` — exactly the ``plan_id`` from
§6. That is the *content-address gate*.

---

## 8. Tweak a predicate and observe the determinism handshake

Narrow the highlands band:

```text
forge.update_sub_region sub_region_id=$HIGHLANDS_ID
    predicate={"kind":"height_band","low_m":180.0,"high_m":10000.0}
```

Re-run ``forge.resolve_material`` from §6. The new ``plan_id`` is
deterministically *different* (the canonical layer JSON changed)
but everything else — archetype parameters, layer order, mask
shapes — is unchanged. Re-running ``forge.generate_region`` produces
a new ``forge.material.<new_plan_id>`` data-block; the prior
``$RID.blend`` keeps its old name because the file already exists.

---

## 9. Delete a sub-region

```text
forge.delete_sub_region sub_region_id=$HIGHLANDS_ID
```

The service refuses if any ``material_application`` edge still
points at the sub_region (``SubRegionInUseError``). Unapply first:

```text
forge.unapply_material edge_id=<snow_application_edge>
forge.delete_sub_region sub_region_id=$HIGHLANDS_ID
```

The parent region's heightmap and other sub_regions are untouched —
predicate evaluation is lazy, so removing a sub_region is just an
edge + node delete.

---

## 10. Verification gates

The Phase 6-c plan in
[`AGENT/dev_phases/phase6_c_subregion.md`](../AGENT/dev_phases/phase6_c_subregion.md)
calls out six gates. The combined verification command:

```bash
uv run forge-schema-export --check
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest -q --cov=forge_mcp --cov-report=term
FORGE_BLENDER_BIN=/usr/bin/blender uv run pytest -q -m blender_integration \
    tests/integration/test_sub_region_material_resolution.py
```

The Blender-gated test asserts:

1. determinism (two ``forge.resolve_material`` calls produce
   identical envelopes);
2. the resolved plan carries a non-``None`` ``predicate_mask`` on
   the sub_region layer;
3. ``forge.preview_sub_region_coverage`` returns a non-zero
   ``coverage_fraction`` for an inclusive band;
4. updating the predicate produces a deterministically different
   ``plan_id``;
5. the rendered ``.blend`` exposes a single material slot whose
   name matches the deterministic ``plan_id``.

When all five fire green against a real Blender 5.0 host, Phase 6-c
is verified end-to-end.
