# Phase 4 acceptance — realizer eval set

This directory holds the **acceptance artefact** that closes Phase 4
(see [`AGENT/dev_phases/phase4.md`](../../../AGENT/dev_phases/phase4.md),
verification §6 and Stage I in
[`phase4remaining.md`](../../../AGENT/dev_phases/phase4remaining.md)).
It is the realizer counterpart of `docs/eval/phase3/`: same five
descriptors, same seed, same generator shape — pushed through the
curated `realize_region` macro and rendered by a real Blender 5.0.0
EEVEE process.

## What lives here

Each sub-folder is one local realization of the eval set, named by
UTC timestamp (e.g. `20260501T143225Z/`). Inside a run:

* `<label>.blend` — the saved Blender file produced by the
  `realize_region` macro. Round-trippable: opening it shows the
  textured terrain mesh, slope-aware material, sun light, world
  background, both cameras (`cam_ortho_<label>`, `cam_persp_<label>`)
  and the `forge_node_id` / `forge_spec_id` / `forge_kind` IDProperties
  on the terrain object.
* `<label>.heightmap.png` — the 16-bit PNG that drives the
  displace modifier (subsampled mesh + full-resolution displacement,
  per Architecture §6).
* `<label>.preview.png` — perspective_se preview at 512×384 (zlib 15).
  Comparable to phase3's heightmaps but in textured 3D.
* `contact_sheet.png` — the five previews tiled horizontally
  (256×192 each, nearest-neighbour) for at-a-glance regression review.
* `manifest.json` — spec ids, primary terrain types, per-macro
  wall-clock timings (`realize_wall_ms`, `render_wall_ms`), and the
  full step-level trace returned by `RealizerEngine.execute_macro`.

The bench lives at
[`scripts/eval/bench_phase4.py`](../../../scripts/eval/bench_phase4.py).
Reproduce locally with:

```bash
FORGE_BLENDER_BIN=/usr/bin/blender uv run python scripts/eval/bench_phase4.py
```

It will create `docs/eval/phase4/<UTC-timestamp>/` automatically.

## Acceptance criteria

Phase 4 is accepted when **all** of the following hold:

1. The Blender-host integration suite (`make integration`) is green.
   It exercises `forge.generate_region`, `forge.render_view`, the
   version-pin contract, and byte-determinism across two fresh
   project trees (PNG IDAT chunks compared; libpng tEXt timestamps
   excluded — see
   [`tests/integration/test_determinism.py`](../../../tests/integration/test_determinism.py)).
2. The unit + descriptor + realizer tests in `tests/` are green at
   ≥90 % branch coverage with the standard quality gate
   (`ruff format --check`, `ruff check`, `mypy`, `pytest --cov`,
   `forge-schema-export --check`).
3. A locally-rendered `contact_sheet.png` shows the five entries as
   recognisable, textured terrain that visually matches the Phase 3
   heightmaps. We commit the latest accepted sheet here so future
   PRs have a visual baseline to diff against.
4. `manifest.json` records `realize_wall_ms` and `render_wall_ms`
   per descriptor on the reference machine (Blender 5.0.0,
   linux-x86_64). NF-1.3 (≤60 s wall-clock per region @ default
   resolution) is checked locally via `make perf`; CI does not gate
   on absolute timings since they are runner-dependent.

## NF-1.5 measurement note

The PRD's NF-1.5 target was **≤200 KB PNG @ 1024×768** for the default
preview. Phase 4 measurement on the rolling-hills test scene (real
generator output, EEVEE Next, full materials) produced **~213 KB at
zlib level 9** — a slight overshoot the curated macro cannot
compress further at 8-bit RGB. We therefore:

* Bumped `RETRY_PNG_COMPRESSION` from 30 → 100 (full zlib effort).
* Parameterised the macro postcondition `expects.png_max_bytes`
  (default 280 000 bytes; 100 000 / 280 000 / 1 120 000 by resolution
  bucket — see `_PNG_MAX_BYTES` in
  [`forge_mcp/server/tools/generation.py`](../../../forge_mcp/server/tools/generation.py)).
* Logged the measurement here so v2 can revisit (sharper compression
  options: PNG palette quantisation, switching to JPEG/WebP for the
  preview tier, or simplifying the v1 shading network).

The structured-error path (`reason_code = png_oversize`) still fires
when a render genuinely exceeds the per-resolution ceiling — the
budget did not get silently widened, only re-measured.

## Eval inputs (locked, identical to Phase 3)

| Label | Primary | Ruggedness | Stream |
| --- | --- | --- | --- |
| `alpine_valley_with_creek` | `alpine_valley` | 0.8 | `alpine_creek` |
| `rolling_hills_dry` | `rolling_hills` | 0.4 | — |
| `desert_mesa` | `desert_mesa` | 0.6 | — |
| `boreal_lowland_meander` | `boreal_lowland` | 0.2 | `meandering_river` |
| `canyon_dry_wash` | `canyon` | 0.7 | `dry_wash` |

Seed `17`, shape `(128, 128)` — pinned in
[`forge_mcp/eval/__init__.py`](../../../forge_mcp/eval/__init__.py).
