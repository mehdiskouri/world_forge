# Carry-over: region-extent ↔ elevation-band scaling

Surfaced during the Phase 5 R-9 sanity walkthrough
([`docs/p5_sanity_walkthrough.md`](../docs/p5_sanity_walkthrough.md))
on 2026-05-06. The audit subagent flagged
`geometric_validity = warn` on the demo `alpine_valley` region; the
finding was correct but the proposed root cause (smoothing sigma) was
wrong. This note is the durable record so Phase 6 planning picks it
up rather than rediscovering it from chat history.

**Severity:** content / mapping bug. Not a Phase 5 blocker — the
audit loop worked as designed (surfaced the warn, persisted the
verdict, did not auto-reroll).

**Owner on file:** Phase 6 (boundary contracts is when region-size
discipline naturally surfaces).

---

## 1. Symptom

Audit verdict for the alpine-valley demo region (200 m × 200 m
polygon, descriptor `{terrain.primary: alpine_valley, ruggedness:
0.75, hydrology.has_stream: true, hydrology.stream_character:
alpine_creek}`):

| Dimension              | Verdict | Finding                                                                 |
| ---------------------- | ------- | ----------------------------------------------------------------------- |
| `descriptor_coherence` | pass    | All fields valid; `has_stream` co-constraint satisfied                  |
| `geometric_validity`   | **warn**| Mean slope 74°, p95 slope 85° — near-vertical across most of the terrain |
| `render_quality`       | pass    | Both ortho_top and perspective_se previews clean                         |
| `spec_alignment`       | pass    | Generators faithfully encode the descriptor                              |

Subagent's hypothesis: smoothing sigma too low (0.22 px); recommend
reroll. **This is wrong.** Cranking the sigma back up softens
high-frequency noise on top of the macro shape; it cannot bring an
83° mean slope down to anything plausible.

The rendered preview shows four further symptoms beyond the slope
number that the audit reported, all of which are downstream
consequences of the same upstream bug:

- **Single-axis vertical stretch** — the geometry reads as a
  displacement spike, not a valley.
- **"Crystalline shards" / mesh tearing on the silhouette** — the
  triangulated mesh emits visibly degenerate quads on the steepest
  faces.
- **Crushed lighting** — most of the surface renders near-black with
  only a pale-green sliver picking up the sun.
- **Apparent loss of shader elevation banding** — the green / brown /
  white colour ramp the macro feeds to ``apply_terrain_material``
  does not visibly band the terrain.

Section 2 below walks each one back to the same root cause; section
3 then describes the single fix that resolves all of them.

---

## 2. Actual root cause

The descriptor → spec mapping in
[`forge_mcp/descriptor/map_to_spec.py`](../forge_mcp/descriptor/map_to_spec.py)
applies an **archetype-fixed** elevation band irrespective of the
region's horizontal extent.

For `alpine_valley`:

```python
TerrainPrimary.ALPINE_VALLEY: TerrainProfile(
    ...
    smooth_sigma_pixels_base=0.4,
    default_elevation_band=(800.0, 2400.0),   # 1600 m relief, fixed
    ...
)
```

`_resolve_elevation_band` then takes that band verbatim unless the
descriptor overrides it:

```python
elevation_band = descriptor.terrain.elevation_band or profile.default_elevation_band
```

The pipeline normalises the noise field to `[0, 1]` and stretches it
to the full elevation band
([`forge_mcp/generate/terrain.py`](../forge_mcp/generate/terrain.py)
`_apply_elevation_band`). For a 200 m × 200 m polygon at the default
`_DEFAULT_RESOLUTION_M_PER_PX = 2.0`, that produces a 100 × 100 px
heightmap covering ~1600 m of vertical relief.

`_slope_and_aspect` in
[`forge_mcp/analyze/terrain_analysis.py`](../forge_mcp/analyze/terrain_analysis.py)
takes Sobel gradients in real units (m / m). The mean slope
collapses to `arctan(~1600 / 200) ≈ 83°`, matching the audit's
"mean 74°, p95 85°" almost exactly.

In one line: the archetype assumes a km-scale region; the demo
hands it a hectometre-scale polygon; the mapper happily fits 1.6 km
of relief into 200 m of horizontal extent.

### 2.1 — How the four secondary symptoms cascade from the same bug

All four extra symptoms above resolve to the same upstream cause and
require **no separate fix**.

| Visible symptom                  | Code site                                                                                                                                                                                                                                       | Causal link                                                                                                                                                                                                                                                                                                |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Vertically-stretched spike       | `_apply_elevation_band` in [`forge_mcp/generate/terrain.py`](../forge_mcp/generate/terrain.py)                                                                                                                                                  | Direct effect: noise is normalised to `[0, 1]` then linearly stretched into the resolved band. There is no XY normalisation, only a Z multiply, so a too-tall band reads as pure vertical stretch.                                                                                                         |
| "Crystalline shards" / tearing   | `mesh_from_heightmap` in [`forge_mcp/realize/heightmap_mesh.py`](../forge_mcp/realize/heightmap_mesh.py) (256² vertex cap) combined with `displace_strength = 0.0` set in [`forge_mcp/server/tools/generation.py`](../forge_mcp/server/tools/generation.py) so the mesh carries `z = data_meters` directly | With ~16 m horizontal spacing per quad but up to 1600 m of vertical step between adjacent verts, quads degenerate into wedges and Eevee renders the silhouette as faceted shards. Same mesh on a 4 km extent has 16× wider quads with the same Z spread → smooth surface.                                  |
| Crushed lighting / near-black    | `add_basic_lighting` in [`forge_mcp/realize/macros.py`](../forge_mcp/realize/macros.py) using a single sun lamp at 45° pitch / -45° yaw                                                                                                          | Mean face normal sits ~83° off horizontal, so the overwhelming majority of faces are back-lit. Only the NW-facing slivers catch the sun. There is no lighting bug; the rig is correct for plausible terrain. Bring the slope distribution into a sane band and the same sun renders the same scene cleanly. |
| Apparent loss of elevation bands | `_DEFAULT_COLOR_RAMP_STOPS` (green → brown → white) keyed by `elevation_min/elevation_max` in [`forge_mcp/server/tools/generation.py`](../forge_mcp/server/tools/generation.py) and consumed by the `apply_terrain_material` macro              | The colour ramp **is** active, but the green band (z ≈ 800–1400 m) covers ~37% of the elevation range and projects almost entirely onto the near-vertical cliff faces, which the sun never lights. There is no shader bug; the geometry is forcing the green stop into shadow.                              |

The single primary culprit is therefore unambiguously
``_resolve_elevation_band`` ignoring the region's horizontal
extent. Everything else is a downstream rendering artefact of the
implausible (relief / extent) ratio. The fix in section 3.1 closes
all four symptoms with one change.

---

## 3. The two clean fixes

### 3.1 — Region-size-aware elevation band (preferred)

Add a slope-plausibility ceiling to `_resolve_elevation_band`.
Concretely:

1. Plumb the region polygon's bounding-box extent through to
   `map_to_spec(...)` (Phase 6 already needs this for boundary
   contracts; the data is in `regions/<id>/region.json`).
2. Compute `max_band_meters = extent_m * tan(MAX_MEAN_SLOPE_DEG)`
   with a per-archetype ceiling table:
   - cliff-tolerant archetypes (`alpine_peaks`, `canyon`,
     `coastal_cliffs`, `volcanic_cone`) → ~50–60°
   - everything else → ~25–35°
3. Clamp `default_elevation_band[1] - default_elevation_band[0]` to
   `max_band_meters`, scaling around the band midpoint.
4. Reject explicit descriptor overrides that violate the ceiling
   with a structured error pointing at the offending field — this is
   the kind of violation `forge.plan` should never emit, so failing
   loud is correct.

Tests to add alongside:

- Unit test in `tests/descriptor/test_map_to_spec.py` covering each
  archetype × {small, medium, km-scale} polygon, asserting the
  resulting band stays within the ceiling.
- Property test that `_slope_stats(...)` over the resulting
  heightmap reports a mean slope below the per-archetype ceiling
  (acceptable noise band: ±10°).
- Update the relevant `forge.plan/eval_set.json` entries to
  document expected behaviour for tiny-extent polygons.

### 3.2 — Walkthrough-only patch (immediate)

Bump the demo region in
[`docs/p5_sanity_walkthrough.md`](../docs/p5_sanity_walkthrough.md)
§3.2 from 200 m × 200 m to ~4 km × 4 km
(`polygon=[[-2000,-2000],[2000,-2000],[2000,2000],[-2000,2000]]`).
With a 4000 m extent the implied mean slope drops to
`arctan(1600 / 4000) ≈ 22°`, well within plausibility.

This is a one-line doc change that does not touch code; it papers
over the symptom for a single demo but leaves the underlying
mapping issue in place. Worth doing for the sanity gate while the
real fix is queued for Phase 6.

---

## 4. What this exposed about the audit subagent

The audit caught the right symptom and persisted the right verdict.
The hypothesis it offered ("smoothing sigma too low") was off by one
abstraction layer (micro-roughness vs. macro relief). Two follow-ups
worth considering for the audit skill content:

1. Add a worked example to
   [`forge_mcp/skills/forge.audit/SKILL.md`](../forge_mcp/skills/forge.audit/SKILL.md)
   that explicitly distinguishes:
   - **Macro-relief implausibility** (region extent vs. elevation
     band) — the spec is wrong for the region size.
   - **Micro-roughness implausibility** (smoothing sigma, octave
     count) — the noise stack is wrong for the archetype.
2. Cross-link `geometric_validity` warns to the
   `_slope_and_aspect` documentation so the subagent can quote the
   gradient computation in evidence — discourages shape-of-the-noise
   guesses when the math actually points at the macro-shape mapper.

These are low-priority polish, not blockers; the v1 contract
("audit invalidates nothing; it records") held.

---

## 5. Suggested Phase 6 issue title + scope

> **Phase 6 follow-up: region-extent-aware elevation band scaling in `map_to_spec`**
>
> Plumb polygon extent through `map_to_spec(...)`, add a per-archetype
> slope-plausibility ceiling table, clamp/reject elevation bands that
> would imply implausible mean slopes for the region size. Update
> `tests/descriptor/test_map_to_spec.py` with archetype × extent
> coverage. Refresh the alpine-valley sanity walkthrough demo region
> once the clamp lands so the slope finding becomes a `pass`.
>
> Linked symptom: [`AGENT/follow_ups/phase5-elevation-band-scaling.md`](follow_ups/phase5-elevation-band-scaling.md).

The fix lands cleanly alongside Phase 6 boundary contracts because
both depend on the same plumbing (region polygon extent → spec
inputs).
