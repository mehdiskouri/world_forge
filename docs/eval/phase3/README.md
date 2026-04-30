# Phase 3 acceptance — terrain generator eval set

This directory holds the **acceptance artefact** that closes Phase 3
(see [`AGENT/dev_phases/phase3.md`](../../../AGENT/dev_phases/phase3.md),
Stage G).

## What lives here

Each sub-folder is one local render of the canonical 5-descriptor
eval set (descriptors + seed + shape are pinned in
`forge_mcp/eval/__init__.py`):

* `contact_sheet.png` — the five heightmaps tiled horizontally
  (256×256 each, 8-bit gray, nearest-neighbour upscaled from the
  internal 128×128 generator grid). Pixels are intentionally crunchy
  so structural differences pop.
* `analyses.json` — the matching `TerrainAnalysis` payloads in the
  same order as the contact sheet.
* `manifest.json` — the spec ids, primary terrain types and ordered
  generator pass-names that produced the sheet.

The renderer lives at `scripts/eval/render_eval_set.py`. Reproduce
locally with `make eval` or:

```bash
uv run python scripts/eval/render_eval_set.py --out docs/eval/phase3/$(date -u +%Y%m%dT%H%M%SZ)
```

## Acceptance criteria

Phase 3 is accepted when:

1. The structural regression suite
   (`tests/descriptor/test_eval_set.py`) is green. It locks pairwise
   ordering rules (canyon p95-slope > rolling-hills p95-slope; alpine
   max-elevation > boreal max-elevation; etc.) so a `TERRAIN_PROFILES`
   regression cannot ship silently.
2. A locally-rendered `contact_sheet.png` shows the five entries as
   visually distinguishable terrain types. We commit the latest
   accepted sheet here (small PNG, < 100 KB) so future PRs have a
   visual baseline to diff against.

## Performance

NF-1.2 budget (≤30 s for 1 km² @ 2 m/px) is checked locally only via
`make perf` — generator perf is runner-dependent so we do not gate
CI on it. Phase 4 reopens the budget once Blender realisation joins
the pipeline.

## Eval inputs (locked)

| Label | Primary | Ruggedness | Stream |
| --- | --- | --- | --- |
| `alpine_valley_with_creek` | `alpine_valley` | 0.8 | `alpine_creek` |
| `rolling_hills_dry` | `rolling_hills` | 0.4 | — |
| `desert_mesa` | `desert_mesa` | 0.6 | — |
| `boreal_lowland_meander` | `boreal_lowland` | 0.2 | `meandering_river` |
| `canyon_dry_wash` | `canyon` | 0.7 | `dry_wash` |

Seed: `17`. Shape: `(128, 128)`. Resolution comes from the spec's
`resolution_meters_per_pixel` (currently the Phase-3 default).
