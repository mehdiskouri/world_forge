---
name: "forge.plan"
version: "1.0.0"
description: "Extract a Forge structured terrain descriptor from free-text intent and drive the generation pipeline. Pinned to descriptor schema 1.0; bumps in lock-step with forge_mcp.descriptor.SCHEMA_VERSION."
triggers: ["create a region", "describe terrain", "make this a", "generate a region", "rugged", "alpine", "valley", "canyon", "mesa", "dunes", "plains", "rolling hills", "volcanic", "coastal cliffs", "river valley", "marsh", "boreal"]
requires_tools: ["forge.get_descriptor_schema", "forge.create_region", "forge.update_region", "forge.generate_region", "forge.analyze_region", "forge.inspect_spec", "forge.render_view", "forge.reroll_seed"]
requires_subagent: false
---

# forge.plan

Translate free-text terrain intent into a Forge `StructuredDescriptor`,
then drive the deterministic generation pipeline. This is the single
load-bearing skill for region creation: every other Forge skill
assumes a region already exists and has a descriptor.

## When this skill applies

Use `forge.plan` whenever the user expresses **terrain intent for a
single region**, in any of these phrasings:

* "make this a rugged alpine valley with a creek"
* "create a region of rolling hills here"
* "describe this region as a desert mesa"
* "regenerate this with more ruggedness"
* "I want a coastal cliff scene"

Multi-region intent ("connect these two with a pass") is **out of
scope for v1** — defer to `forge.connect` for traversal questions and
wait for the Phase-6 boundary-contract skill for actual multi-region
authoring. Lock/reroll/undo recovery beyond what is sketched below
lives in a Phase-7 amendment.

## Structured descriptor schema (authoritative)

The agent **must** produce a JSON value matching this schema before
calling `forge.create_region` or `forge.update_region`. The schema
below is generated from `forge_mcp.descriptor.descriptor_json_schema()`
and CI verifies byte-identity (Phase 5 Stage F test
`tests/skills/test_skill_files.py::test_plan_skill_embeds_descriptor_schema`).
Do **not** edit it by hand.

```json
{
  "$defs": {
    "Hydrology": {
      "additionalProperties": false,
      "description": "Hydrology sub-descriptor.\n\nA region with ``has_stream=True`` must declare ``stream_character`` to\nsomething other than ``NONE``; this is checked in\n:func:`forge_mcp.descriptor.validate.validate`.",
      "properties": {
        "has_stream": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Has Stream"
        },
        "stream_character": {
          "anyOf": [
            {
              "$ref": "#/$defs/StreamCharacter"
            },
            {
              "type": "null"
            }
          ],
          "default": null
        }
      },
      "title": "Hydrology",
      "type": "object"
    },
    "StreamCharacter": {
      "description": "Hydrological character of a region's primary stream, if any.",
      "enum": [
        "alpine_creek",
        "meandering_river",
        "dry_wash",
        "none"
      ],
      "title": "StreamCharacter",
      "type": "string"
    },
    "Terrain": {
      "additionalProperties": false,
      "description": "Terrain sub-descriptor.\n\nOnly :attr:`primary` is required. Optional fields modulate the\ndeterministic Phase 3 mapping; absent fields fall back to per-profile\ndefaults.",
      "properties": {
        "elevation_band": {
          "anyOf": [
            {
              "maxItems": 2,
              "minItems": 2,
              "prefixItems": [
                {
                  "type": "number"
                },
                {
                  "type": "number"
                }
              ],
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Elevation Band"
        },
        "notes": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Notes"
        },
        "primary": {
          "$ref": "#/$defs/TerrainPrimary"
        },
        "ruggedness": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Ruggedness"
        }
      },
      "required": [
        "primary"
      ],
      "title": "Terrain",
      "type": "object"
    },
    "TerrainPrimary": {
      "description": "Primary terrain archetype enumerated by the v1 design space.\n\nEach value maps to a profile in the Phase 3 ``TERRAIN_PROFILES``\nlookup. Adding values is a minor schema bump; removing or renaming is\nbreaking.",
      "enum": [
        "alpine_valley",
        "alpine_peaks",
        "rolling_hills",
        "plains",
        "desert_mesa",
        "desert_dunes",
        "boreal_lowland",
        "marsh",
        "volcanic_cone",
        "coastal_cliffs",
        "river_valley",
        "canyon"
      ],
      "title": "TerrainPrimary",
      "type": "string"
    }
  },
  "additionalProperties": false,
  "description": "Top-level structured descriptor handed by the agent to Forge.\n\nFrozen and ``extra='forbid'``: the schema is the contract, agents\ncannot smuggle extra fields, and Forge can hash descriptors safely.",
  "properties": {
    "hydrology": {
      "anyOf": [
        {
          "$ref": "#/$defs/Hydrology"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "terrain": {
      "$ref": "#/$defs/Terrain"
    }
  },
  "required": [
    "terrain"
  ],
  "title": "ForgeStructuredDescriptor",
  "type": "object",
  "x-schema-version": "1.0"
}
```

### Key constraints (read these once, then reference the examples)

* `terrain.primary` is **required** and must be one of the 12
  enumerated `TerrainPrimary` values. There is no "other" or
  "freeform" primary — pick the closest match.
* `terrain.ruggedness` is `[0.0, 1.0]` if present. Out-of-range
  values fail validation; `null`/omitted is fine.
* `terrain.elevation_band` is `[low_meters, high_meters]` with
  `low <= high`. Omit if the user didn't say.
* `terrain.notes` is free text **<= 200 chars**. Use sparingly;
  this field does not affect generation.
* `hydrology.has_stream=True` requires `stream_character != "none"`.
* `additionalProperties: false` everywhere — never invent fields.

## Worked examples

These twelve examples are kept in
[forge.plan/eval_set.json](eval_set.json) and reused by the Phase 5
Stage F eval harness (`scripts/eval/skill_plan_eval.py`). The skill
file and the JSON file are the **single source of truth**; CI asserts
they parse identically.

### 1. Alpine valley with creek

> "A rugged alpine valley with a small mountain creek running through it."

```json
{
  "terrain": {"primary": "alpine_valley", "ruggedness": 0.75},
  "hydrology": {"has_stream": true, "stream_character": "alpine_creek"}
}
```
*Trickiest bit:* "creek" disambiguates `stream_character` to
`alpine_creek`, not `meandering_river`.

### 2. Alpine peaks (no water)

> "Jagged snow-capped peaks rising sharply, no rivers, just rock and ice."

```json
{"terrain": {"primary": "alpine_peaks", "ruggedness": 0.95}}
```
*Trickiest bit:* "no rivers" -> omit `hydrology` entirely; do **not**
emit `{"has_stream": false, "stream_character": "none"}` unless the
user is explicit about a non-stream water body (see marsh example).

### 3. Rolling hills, pastoral

> "Gentle rolling hills, pastoral and green, the kind you'd graze sheep on."

```json
{"terrain": {"primary": "rolling_hills", "ruggedness": 0.2}}
```
*Trickiest bit:* "gentle" pulls `ruggedness` toward 0.2; reserve
0.0 for `plains`.

### 4. Plains, flat

> "Wide open plains, flat as a tabletop, golden grass to the horizon."

```json
{"terrain": {"primary": "plains", "ruggedness": 0.05}}
```
*Trickiest bit:* `plains` already implies near-zero ruggedness;
`0.05` is conventional. Don't override the profile by setting
elevation_band unless the user mentions altitude.

### 5. Desert mesa with dry wash

> "A high desert mesa with steep sides and a dry wash at the base."

```json
{
  "terrain": {"primary": "desert_mesa", "ruggedness": 0.6},
  "hydrology": {"has_stream": true, "stream_character": "dry_wash"}
}
```
*Trickiest bit:* mesa is a **positive** landform (rises above the
plain); pick `canyon` instead when the user says "cut down into".

### 6. Desert dunes, no water

> "Sweeping sand dunes, no vegetation, no water, just wind-shaped ridges."

```json
{"terrain": {"primary": "desert_dunes", "ruggedness": 0.35}}
```
*Trickiest bit:* dunes have apparent relief but smooth slopes —
moderate `ruggedness ~0.35`, not high.

### 7. Boreal lowland, taiga

> "Flat boreal lowland, taiga forest, mossy ground, occasional bog."

```json
{"terrain": {"primary": "boreal_lowland", "ruggedness": 0.15}}
```
*Trickiest bit:* "occasional bog" describes ground cover, **not** a
stream; do not set `has_stream`. (If the user says "with a slow
boreal river", switch to `river_valley` + `meandering_river`.)

### 8. Marsh, sea level

> "A reedy marsh just above sea level, slow water everywhere, no clear channel."

```json
{
  "terrain": {
    "primary": "marsh",
    "elevation_band": [0.0, 5.0],
    "ruggedness": 0.05
  },
  "hydrology": {"has_stream": false, "stream_character": "none"}
}
```
*Trickiest bit:* "no clear channel" is the explicit signal that lets
us emit `has_stream=false` rather than omitting `hydrology`. The
zero elevation low-end is **not** sea level by default — set it
because the user said so.

### 9. Volcanic cone

> "A steep symmetrical volcanic cone, cinder slopes, summit crater, no water."

```json
{"terrain": {"primary": "volcanic_cone", "ruggedness": 0.7}}
```
*Trickiest bit:* the profile already encodes the conical shape;
`ruggedness` only modulates surface noise, not the macro form.

### 10. Coastal cliffs

> "Sheer coastal cliffs facing the open ocean, sea spray, no inland streams."

```json
{"terrain": {"primary": "coastal_cliffs", "ruggedness": 0.8}}
```
*Trickiest bit:* the ocean is **not** a `stream_character`. Phase 5
has no sea-surface descriptor; document the water plane in
`terrain.notes` if the user insists.

### 11. River valley, meandering

> "A wide river valley with a meandering river, fertile floodplain on either side."

```json
{
  "terrain": {"primary": "river_valley", "ruggedness": 0.25},
  "hydrology": {"has_stream": true, "stream_character": "meandering_river"}
}
```
*Trickiest bit:* `river_valley` plus `meandering_river` is the canonical
pairing; the alternative (`alpine_creek`) implies steeper terrain.

### 12. Canyon, seasonal river

> "A deep narrow canyon cut by a seasonal river, walls of red sandstone."

```json
{
  "terrain": {"primary": "canyon", "ruggedness": 0.85},
  "hydrology": {"has_stream": true, "stream_character": "dry_wash"}
}
```
*Trickiest bit:* "seasonal" -> `dry_wash`, not `meandering_river`.
Sandstone color goes in `terrain.notes` if at all (it does not affect
geometry in Phase 5).

## Workflow (Architecture §6.1, 9 steps)

1. **Receive intent.** Capture the user's free text verbatim before
   doing any extraction; you may need to quote it back later.
2. **Pull the schema.** Call `forge.get_descriptor_schema` once per
   session to confirm you are pinned to schema version `1.0`. If the
   server returns a different `x-schema-version`, surface a warning
   and re-read this skill.
3. **Extract.** Produce a `StructuredDescriptor` JSON value following
   the worked examples. When in doubt, omit optional fields rather
   than guessing.
4. **Validate locally.** Re-check the JSON against the schema before
   calling any tool — the schema is closed (`additionalProperties:
   false`). Server validation will catch errors but a local check
   saves a round-trip.
5. **Confirm with the user (lightweight).** Echo the descriptor back
   in one sentence ("alpine valley, ruggedness 0.75, with an alpine
   creek — proceed?") so they can correct misreadings before
   generation runs.
6. **Create or update the region.** If the region does not exist:
   `forge.create_region(name, polygon_coords, structured_descriptor)`.
   If it does: `forge.update_region(region_id,
   structured_descriptor=...)`.
7. **Generate.** Call `forge.generate_region(region_id)` with no
   extra parameters; spec derivation is deterministic from the
   descriptor + region polygon. Capture the returned `spec_id`.
8. **Inspect outputs.** Use `forge.analyze_region(region_id)` for
   cheap derived metrics (slope histograms, stream stats); use
   `forge.inspect_spec(spec_id)` to confirm the spec params Forge
   chose.
9. **Render only when asked or for verification.** Renders are
   expensive (NF-1 budget). Default to a `preview` resolution
   `ortho_top` view via `forge.render_view`; switch to `default` or
   `full` only on user demand.

After step 9, if the user asks to "audit" or "verify", hand off to
`forge.audit` (which spawns the audit subagent). Do **not** call
`forge.record_audit` from inside `forge.plan`.

## Tool call patterns

```text
forge.get_descriptor_schema()
  -> {"x-schema-version": "1.0", ...}

forge.create_region(
  name="alpine_bowl",
  polygon_coords=[[0,0],[10,0],[10,10],[0,10]],
  structured_descriptor={"terrain": {"primary": "alpine_valley", "ruggedness": 0.75}, "hydrology": {"has_stream": true, "stream_character": "alpine_creek"}},
  seed=7
) -> {"region_id": "alpine_bowl", ...}

forge.update_region(
  region_id="alpine_bowl",
  structured_descriptor={"terrain": {"primary": "alpine_valley", "ruggedness": 0.9}, "hydrology": {"has_stream": true, "stream_character": "alpine_creek"}}
) -> {...}

forge.generate_region(region_id="alpine_bowl")
  -> {"spec_id": "spec_abc123", "heightmap_path": "...", ...}

forge.analyze_region(region_id="alpine_bowl")
  -> {"slope_p50": ..., "stream_length": ...}

forge.inspect_spec(spec_id="spec_abc123")
  -> {"params": {...}, "descriptor_hash": "..."}

forge.render_view(
  region_id="alpine_bowl",
  view_kind="ortho_top",
  resolution="preview"
) -> {"png_path": "...", "trace_path": "..."}

forge.reroll_seed(region_id="alpine_bowl", seed=42)
  -> {"spec_id": "spec_def456", ...}
```

The lock tools (`forge.list_locks`, future `forge.acquire_lock`,
`forge.release_lock`, `forge.apply_locks_on_reroll`) are mentioned
here for completeness but the playbook lives in a Phase-7 amendment
to this skill. For now: **do not lock regions speculatively** —
locks are a recovery tool, not a planning tool.

## Common pitfalls

* **Forgetting `hydrology` for stream descriptors.** "valley with a
  river" implies `hydrology.has_stream=true`; emitting only
  `terrain.primary="river_valley"` will produce a dry valley.
* **Out-of-range `ruggedness`.** Values like `1.5`, `-0.1`, or "high"
  fail validation. Map qualitative words to the bands `gentle/flat
  ~0.0-0.2`, `moderate ~0.3-0.5`, `rugged ~0.6-0.8`, `extreme
  ~0.85-1.0`.
* **Conflating `elevation_band` low-end with sea level.** The
  default profile sets a sensible low-end; only override when the
  user explicitly says "near sea level", "alpine ~3000 m", etc.
* **Mesa vs canyon confusion.** A mesa rises **above** the
  surrounding plain; a canyon is cut **down into** it. If the user
  describes both walls and a floor, pick `canyon`.
* **Reroll vs regenerate.** `forge.reroll_seed` keeps the descriptor
  and changes only the seed (cheap variation). `forge.update_region`
  + `forge.generate_region` rewrites the descriptor (expensive,
  needed when intent changes).
* **Inventing fields.** The schema is closed. Do not emit
  `terrain.biome`, `hydrology.flow_rate`, or any other non-schema
  field — server validation will reject and the user will lose trust.

## Failure recovery

* **`forge.create_region` -> `region_overlap` error.** The proposed
  polygon intersects an existing region. Ask the user whether to
  shrink the new polygon, replace the existing region, or pick a
  different location. Do **not** delete the existing region without
  explicit confirmation.
* **`forge.create_region` -> `invalid_polygon` error.** The polygon
  is degenerate (fewer than 3 vertices, self-intersecting, or
  zero area). Surface the error message verbatim and ask the user
  to redraw.
* **`forge.generate_region` exceeds NF-1.3 latency budget.** The
  tool returns a `realizer_timeout` envelope. Suggest the user
  re-run on a smaller polygon or wait for the realizer to finish on
  another region. Do not silently retry — generation is
  deterministic, so the same input will time out again.
* **Descriptor validation fails server-side.** Read the structured
  error from the response, fix the offending field (it will name
  the JSON pointer), and retry. Do not wrap this in a try/loop:
  validation errors mean your extraction is wrong, not that the
  server is flaky.
* **Spec mismatch on inspect.** If `forge.inspect_spec` shows
  parameters that don't match the descriptor (e.g., descriptor says
  ruggedness 0.9 but spec shows 0.5), you have probably called
  `forge.generate_region` against a stale region. Re-pull the
  region with `forge.get_region` and confirm the descriptor on
  disk before assuming a Forge bug.
