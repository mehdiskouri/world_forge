# Realization (Phase 4)

This document covers the Phase-4 realization layer: how a generated
heightmap becomes a Blender ``.blend`` plus a preview PNG via the
curated v1 macro library.

## Layers at a glance

```
    forge.generate_region / forge.render_view   (server/tools/generation.py)
                    │
                    ▼
    realize / macros.py    typed Python facade per macro
                    │                       │
                    ▼                       ▼
    realize / engine.py             realize / heightmap_mesh.py
        ─ walks SequenceSteps           ─ heightmap → vertex/face arrays
        ─ scene.diff postconditions
        ─ depth-1 seq:<name> recursion
                    │
                    ▼
    realize / rpc.py            realize / blender_proc.py
        ─ JSON-RPC client         ─ owns the blender subprocess
                    │
                    ▼
    scripts / blender / adapter.py   (runs INSIDE Blender)
```

The ``forge_mcp.realize`` package never imports ``bpy``. Every Blender
side-effect is one JSON-RPC call away. ``scripts/blender/adapter.py``
runs in Blender's own Python and is type-checked separately against
``fake-bpy-module-5.0`` stubs.

## Curated v1 macros

The macro library is a single content-addressed JSON file at
``forge_mcp/bpy_hypergraph/data/curated_sequences.json``. It carries
nine macros:

| Macro | Purpose |
| --- | --- |
| ``reset_scene`` | Wipe to ``read_factory_settings(use_empty=True)``. |
| ``create_terrain_from_heightmap`` | ``mesh.from_pydata`` + IDProperty tags (``forge_node_id``, ``forge_spec_id``, ``forge_kind``). |
| ``apply_terrain_material`` | ``material.build_composite`` from a resolved :class:`CompositeMaterialPlan` (see "Composite materials" below). |
| ``carve_stream`` | Curve datablock + IDProperty tag for the optional stream geometry. |
| ``set_camera_overview`` | Ortho-top + perspective cameras framing world bounds. |
| ``add_basic_lighting`` | Sun lamp + procedural sky world. |
| ``render_preview`` | Render through the chosen camera at the requested resolution; engine enforces the NF-1.5 200 KB ceiling via ``expects.png_max_bytes``. |
| ``save_blend`` | ``bpy.ops.wm.save_as_mainfile`` (atomic ``.tmp`` + ``os.replace`` is host-side). |
| ``realize_region`` | Composite: reset → terrain → material → stream → cameras → lighting → save. |

Each ``CuratedSequence`` carries a ``version`` string and is hashed with
BLAKE2b (``digest_size=10``) to a 20-character hex ``sequence_id`` so
the on-disk realization trace can pin which exact macro version ran.

## Engine semantics

``RealizerEngine.execute_macro(name, inputs)`` walks the macro's
``steps``:

* ``${name}`` placeholders inside ``params`` are resolved whole-value
  (no string-interpolation surprises) from the ``inputs`` mapping;
* ``seq:<other_macro>`` calls recurse into a sub-sequence (depth 1; the
  bundle integrity check forbids nesting deeper than that);
* ``expects.scene_diff`` postconditions snapshot
  ``scene.diff`` before/after and verify per-collection ``eq``/``delta``
  predicates;
* ``expects.png_max_bytes`` postconditions enforce the NF-1.5 ceiling on
  the rendered file size;
* every step appends a ``RealizationTraceStep`` carrying the call name,
  duration in milliseconds, and the before/after diffs;
* on construction the engine pings the adapter and refuses to run if
  the running Blender's ``blender_version`` does not match the
  hypergraph contract (``BlenderVersionMismatchError``).

Failures raise ``RealizerStepError`` carrying the partial trace so
callers (the generation tools, the bench, the eventual audit
subagent) always have a step-by-step record of what happened.

## Server tools

``forge.generate_region`` is the canonical entry point. When a realizer
factory is installed (via
``forge_mcp.server.tools.set_realizer_factory``), it:

1. compiles the descriptor into a spec and runs the Phase-3 terrain
   pipeline;
2. tessellates the resulting heightmap into vertex/face arrays via
   ``forge_mcp.realize.heightmap_mesh.mesh_from_heightmap``;
3. invokes ``realize_region`` against Blender, writing the ``.blend``
   to ``<project>/realizations/blender/<region_id>.blend.tmp``;
4. invokes ``render_preview`` at 512x384 (the default ``preview``
   resolution), writing
   ``<project>/realizations/blender/<region_id>.preview.png.tmp``;
5. ``os.replace``s both into place atomically only after both writes
   succeed;
6. writes the realization-trace sidecar to
   ``<project>/realizations/blender/<region_id>.trace.json``.

When no factory is installed the tool falls through gracefully —
``blend_path`` / ``preview_path`` / ``realization_trace_path`` come back
``None`` and the rest of the spec / heightmap pipeline still runs.

``forge.render_view(region_id, view_kind)`` re-runs the realize +
render path at one of three preset resolutions:

| ``view_kind`` | Resolution |
| --- | --- |
| ``preview`` | 512 x 384 |
| ``default`` | 1024 x 768 |
| ``full`` | 2048 x 1536 |

The realizer rebuilds the scene from the persisted heightmap on every
call so the on-disk ``.blend`` stays honest about what the most recent
render actually drew. A missing factory returns the
``realizer_not_configured`` error envelope so the agent can fall back
to the heightmap PNG.

## GPU rendering and render_options

Both ``forge.generate_region`` and ``forge.render_view`` accept an
optional ``render_options`` object that swaps the underlying engine and
device, and overrides resolution / png-budget / Cycles sample count for
that one call. The schema (``schemas/render_options.schema.json``) is
generated from
``forge_mcp.realize.render_options.RenderOptions``.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| ``engine`` | ``"BLENDER_EEVEE"`` \| ``"CYCLES"`` | auto | EEVEE = deterministic raster baseline; Cycles = path tracer. |
| ``device`` | ``"AUTO" \| "CPU" \| "OPTIX" \| "CUDA" \| "HIP" \| "METAL"`` | ``"AUTO"`` | Non-AUTO devices that are not present return ``device_unavailable``. |
| ``width`` / ``height`` | int (1..4096) | tier preset | Must be paired; ``width * height`` capped at 16 MP (4096*4096). |
| ``png_max_bytes`` | int (1..32 MiB) | tier preset | Lifts the NF-1.5 ceiling for that one render. |
| ``cycles_samples`` | int (1..4096) | 64 | Cycles only; forces ``use_adaptive_sampling=False`` so digests stay stable. |

**Engine baselines.** EEVEE always runs on CPU and is the determinism
baseline used by the integration suite (byte-for-byte digest equality
on identical descriptors). Cycles is the path-traced engine; when a GPU
backend is detected it is preferred over CPU. EEVEE + a GPU device, or
EEVEE + ``cycles_samples``, is rejected at validation time.

**Auto-pick rules.** When ``render_options`` is supplied and ``engine``
is unset, the realizer picks Cycles + the first available GPU device
(probe order ``OPTIX, CUDA, HIP, METAL``); if no GPU is present it falls
back to EEVEE on CPU. An explicit ``engine="CYCLES"`` with ``device``
unset prefers GPU but falls back to CPU and adds the
``cycles_cpu_fallback`` note to the trace. Resolved settings (engine,
device, width, height, png cap, samples, notes) are surfaced on every
``generate_region`` / ``render_view`` response under
``render_engine`` / ``render_device_type`` / ``render_cycles_samples``
/ ``render_notes``.

**Backwards compatibility.** Calls that omit ``render_options``
entirely continue to use EEVEE on CPU at the tier defaults — the
``legacy_default`` resolver flag preserves byte-for-byte parity with
pre-Phase-6-d artifacts. Pass ``render_options={}`` (an empty object)
to opt in to the new auto-pick behaviour without specifying any
overrides.

**Device discovery.** ``forge.list_render_devices`` returns the
adapter's most recent probe (``available_device_types``,
``default_device_type``). The probe is captured during the version-check
ping and cached per process; pass ``force_refresh=true`` to re-issue
the ping. The same probe powers the resolver's GPU detection inside
``generate_region`` / ``render_view`` so device availability stays
consistent across calls.

## Composite materials

The terrain material a region renders with is no longer hard-coded; it
is *resolved* from the project hypergraph at the start of every
``forge.generate_region`` call. The pipeline is:

1. ``forge_mcp.realize.material.resolve_plan(state, region_id, ...)``
   walks the ancestor chain of ``region_id``
   (``region``-scoped applications win over ``world`` scope; ties break
   on ``priority`` then ``edge_id``), expands every
   ``material_application`` edge against its archetype's
   ``material_composition`` chain (``extends`` flattens parameters,
   ``composes`` stacks an extra layer with a ``MaskSpec``), and emits
   a ``CompositeMaterialPlan`` carrying a tuple of ``ResolvedLayer`` s.
2. The plan is content-addressed: ``plan_id =
   blake2b(canonical_json({region_id, mesh_name, layers}),
   digest_size=10).hexdigest()`` (prefixed ``mplan_``). Two regions
   that resolve to the same layers share the same ``plan_id`` — and
   therefore the same ``forge.material.<plan_id>`` data-block in the
   rendered ``.blend``.
3. When no ``material_application`` is in scope, the resolver falls
   back to the synthesised default archetype produced by
   ``forge_mcp.realize.material.defaults.default_terrain_archetype``
   (the green/brown/white height ramp the prior hard-coded path used).
   This is the regression gate for projects with no material wiring.
4. The plan is JSON-serialised and threaded through the
   ``apply_terrain_material`` macro to ``material.build_composite``,
   which in the Blender adapter dispatches each layer through a recipe
   registry (``principled_height_ramp``, ``triplanar_rock``,
   ``flat_color``) and mixes consecutive layers via
   ``ShaderNodeMixShader`` driven by the layer's ``MaskSpec``
   (``height_ramp`` / ``slope`` / ``constant``).

The MCP surface for material wiring lives in
``forge_mcp/server/tools/materials.py``: ``forge.create_material_archetype``,
``forge.update_material_archetype``, ``forge.delete_material_archetype``,
``forge.list_material_archetypes``, ``forge.get_material_archetype``,
``forge.apply_material``, ``forge.unapply_material``,
``forge.list_material_applications``, ``forge.compose_material``,
``forge.uncompose_material``, and the read-only
``forge.resolve_material`` (which returns the same
``CompositeMaterialPlan`` ``forge.generate_region`` will use).

The realization summary in the ``forge.generate_region`` envelope
exposes ``plan_id`` and ``elevation_band`` so callers can correlate
trace records, on-disk material names, and resolver previews without
re-deriving them.

### Sub-regions

A region is rarely materially homogeneous: a single "alpine valley"
descriptor may want grass in the basin, scree on the steep faces,
and snow above an elevation line. Phase 6-c models this by adding a
typed ``sub_region`` node that hangs off a parent region via a
``LAYER_SPATIAL_CONTAINMENT`` edge and carries a
:class:`SubRegionPredicate` describing *which* surface points it
covers. Predicates are evaluated lazily — the sub_region itself is
just a query.

Four predicate kinds ship in v1:

* ``height_band`` — half-open ``[low_m, high_m)`` on absolute
  elevation;
* ``slope`` — half-open ``[min_deg, max_deg)`` on the surface slope
  (degrees from horizontal);
* ``aspect`` — half-open ``[min_deg, max_deg)`` on the compass bearing
  of the downhill direction (north = 0°), with wrap-through-north
  ``min > max`` interpreted as ``[min, 360) ∪ [0, max)``;
* ``distance_to_stream`` — ``<= max_m``, evaluated against the
  rasterised stream centerline.

Sub_regions slot into the resolver as additional targets in the
ancestor walk: an ``apply_material`` edge whose
``scope = "sub_region"`` lands on the sub_region node, the resolver
materialises the resulting ``CompositeMaterialPlan`` layer with a
:class:`PredicateMask` attached, and the Blender adapter combines
that predicate factor with the application's own ``MaskSpec`` via
a clamped ``ShaderNodeMath`` multiply (predicate gates the *region of
effect*, the application mask modulates *within* it). The plan is
still content-addressed over the same canonical layer tuple, so two
identical sub_region wirings collapse to the same ``plan_id`` and
share the same on-disk material data-block.

The MCP surface for sub_regions lives in
``forge_mcp/server/tools/sub_regions.py``:
``forge.create_sub_region``, ``forge.update_sub_region``,
``forge.delete_sub_region``, ``forge.list_sub_regions``,
``forge.get_sub_region``, and the read-only
``forge.preview_sub_region_coverage`` (which evaluates the predicate
against the parent region's heightmap and returns the selected vertex
count, coverage fraction, and UV-space AABB without launching
Blender). End-to-end behaviour is exercised in
``tests/integration/test_sub_region_material_resolution.py`` and
walked through in
[``docs/p6c_subregions_walkthrough.md``](p6c_subregions_walkthrough.md).

## On-disk layout

```
<project>/
└── realizations/
    ├── heightmap/
    │   ├── <region_id>.npy           (Phase 3, lossless)
    │   ├── <region_id>.npy.meta.json (Phase 3 sidecar)
    │   ├── <region_id>.png           (Phase 3, 16-bit preview)
    │   └── <region_id>.stream.json   (Phase 3, optional)
    └── blender/                       (Phase 4)
        ├── <region_id>.blend          (atomic write)
        ├── <region_id>.preview.png    (atomic write)
        └── <region_id>.trace.json     (canonical JSON)
```

The trace sidecar carries:

```json
{
  "region_id": "...",
  "view_kind": "preview",
  "macro": "realize_region",
  "sequence_id": "20-hex-chars",
  "total_duration_ms": 1234.5,
  "final_result": { ... },
  "steps": [
    { "sequence_name": "...", "step_index": 0, "call": "...",
      "duration_ms": 1.2,
      "scene_diff_before": { ... } | null,
      "scene_diff_after": { ... } | null,
      "result": ... }
  ]
}
```

## Running the bench

The realization bench is local-only (it requires a real Blender 5.0
binary). To run it:

```bash
FORGE_BLENDER_BIN=/usr/bin/blender uv run python scripts/eval/bench_phase4.py
```

Outputs land in ``docs/eval/phase4/<UTC-timestamp>/``:

* ``manifest.json`` — per-descriptor wall-clock for ``realize_region``
  and ``render_preview``, plus the per-step trace summary;
* ``<label>.blend`` and ``<label>.preview.png`` for each entry;
* ``contact_sheet.png`` — the previews tiled horizontally.

The bench shares ``forge_mcp.eval.EVAL_DESCRIPTORS`` with the Phase-3
contact-sheet renderer so descriptors and seeds stay locked between
the heightmap-only path and the realized path.
