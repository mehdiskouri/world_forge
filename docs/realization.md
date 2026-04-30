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
| ``apply_terrain_material`` | ``material.build_terrain`` with elevation-driven color ramp. |
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
