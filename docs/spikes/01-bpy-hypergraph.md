# Spike 1 — bpy Hypergraph Ingestion

**Branch:** `bpy-hypergraph-ingestion`
**Time-box:** 3 days. **Actual:** within budget.
**Verdict:** ✅ **GO**

## What was built

- `scripts/blender/introspect.py` — runs inside Blender 5.0.0 via
  ``blender --background --python``; walks every `bpy.ops.*` operator
  and a curated list of `bpy.types`, dumps a single JSON file. Stdio
  discipline: progress on **stderr**, payload to ``--out`` only.
  Excluded from ruff/mypy strict matrix (target interpreter is
  Blender's, not host CPython). Verified end-to-end: produced 2 441
  operators and 11 types from a real Blender 5.0.0 binary.

- `scripts/host/build_hypergraph.py` — host-side curator. Filters the
  raw introspection down to a 24-operator v1 allow-list (drawn from
  ARCHITECTURE.md §5.4), joins hand-curated effect annotations
  (preconditions / postconditions / mutation set) and `bpy.data`
  alternative paths, and writes four artifacts under
  `forge_mcp/bpy_hypergraph/data/`. Tagged `blender-5.0.0-v1`.
  Failure mode: if any v1 operator is missing from the raw dump (e.g.,
  a future Blender release renames or removes an op), the build fails
  with a precise diagnostic — this is the drift signal.

- `forge_mcp/bpy_hypergraph/{__init__.py,query.py}` — runtime API:
  `load_hypergraph()` returns a frozen `BpyHypergraph`. Lookups are
  O(1) (`get_operator`, `get_type`, `get_effect`, `get_alternative`).
  Cross-artifact integrity check: every annotated operator must exist
  in the v1 set; schema tags must match across all four files.

- `forge_mcp/bpy_hypergraph/data/{operators,types,effects,alternative_paths}.json`
  — the committed, drift-checked v1 hypergraph.

- `tests/bpy_hypergraph/test_hypergraph.py` — 28 tests covering the
  happy path (artifact loads, canonical operators present, parameters
  introspectable), every validation branch, and three end-to-end
  rejection scenarios. **100 % line+branch coverage** of the
  `forge_mcp/bpy_hypergraph` subpackage.

## v1 operator set (24 ops, ~30–50 target band)

Per ARCHITECTURE §5.4 / §5.5 (the v1 macros):

| group        | operators                                               |
|--------------|---------------------------------------------------------|
| primitives   | plane, cube, grid, uv_sphere, ico_sphere, cylinder      |
| mesh edit    | subdivide, shade_smooth, shade_flat                     |
| modifiers    | modifier_add, modifier_apply, modifier_remove           |
| object mgmt  | select_all, delete, transform_apply, origin_set, parent_set |
| image        | image.open                                              |
| render/save  | render.render, save_as_mainfile, save_mainfile,         |
|              | open_mainfile, read_factory_settings, quit_blender      |

## Bpy.data alternative-path table (5.0 payoff)

ARCHITECTURE §5.4 calls out that 5.0 lets us prefer `bpy.data` calls
over `bpy.ops` for several common operations. The concrete table:

| operator                          | preferred | data path                                               |
|-----------------------------------|-----------|---------------------------------------------------------|
| `mesh.primitive_plane_add`        | ops       | `bpy.data.meshes.new` + bmesh (advanced topology only)  |
| `object.modifier_add`             | **data**  | `obj.modifiers.new(name, type)`                         |
| `object.modifier_apply`           | ops       | depsgraph + `mesh.from_existing` (deferred)             |
| `image.open`                      | **data**  | `bpy.data.images.load(filepath, check_existing=True)`   |
| `object.delete`                   | **data**  | `bpy.data.objects.remove(obj, do_unlink=True)`          |
| `render.render`                   | ops       | (no data path — context-bound)                          |
| `wm.save_as_mainfile`             | ops       | (no data path — window-manager-bound)                   |

This table feeds the realizer (Phase 4, ARCHITECTURE §5.7): it picks
the preferred path and falls back to the other if context conditions
fail.

## Strictness notes

- The Blender-internal script is excluded from ruff and mypy via
  `extend-exclude` / `exclude` in `pyproject.toml` because its target
  interpreter is Blender's (5.0 ships a curated CPython with `bpy`
  pre-imported). The host-side curator and runtime module **are**
  inside the strict matrix and pass `ruff ALL` + `mypy --strict +
  disallow_any_explicit + warn_unreachable + extra_checks`.
- 100 % line and branch coverage on the runtime module — defensive
  validation paths exercised by monkeypatching the JSON loader.

## Drift policy

- The 5.0 `bpy.ops` surface is enormous (2 441 operators); curating a
  v1 allow-list is the only way to keep the artifact reviewable.
- When Blender bumps a minor/patch version, re-run
  `scripts/blender/introspect.py` against the new binary, then
  `scripts/host/build_hypergraph.py`. The `schema_tag` changes from
  `blender-5.0.0-v1` to (e.g.) `blender-5.0.1-v1`; the runtime asserts
  all four artifacts agree.
- A future CI step (Phase 2) should run the build offline (no Blender
  required) against a checked-in raw dump and `git diff --exit-code`
  the artifacts.

## Go/no-go

GO. Blender 5.0.0 introspection works first-shot, the v1 surface fits
in the ~30–50 op band, the 5.0 `bpy.data` alternative paths exist for
the operations the realizer cares most about, and the runtime API is
small, fast, and 100 %-covered.
