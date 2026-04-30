# Spike 4 — Structured Descriptor Schema

**Branch:** `descriptor-schema`
**Time-box:** 0.5 day. **Actual:** within budget.
**Verdict:** ✅ **GO**

## What was built

- `forge_mcp/descriptor/schema.py` — Pydantic v2 models matching
  ARCHITECTURE.md §3.3 verbatim (`TerrainPrimary`, `StreamCharacter`,
  `Terrain`, `Hydrology`, `StructuredDescriptor`).
- `forge_mcp/descriptor/validate.py` — `validate(payload) ->
  StructuredDescriptor | ValidationFailure` returning a flat tuple of
  `(path, message, code)` issues. Cross-field invariants
  (inverted `elevation_band`, `has_stream` ↔ `stream_character` consistency)
  reported alongside Pydantic-level errors.
- `forge_mcp/descriptor/schema.json` — committed JSON Schema artifact;
  CI fails on drift (`test_committed_schema_json_matches_models`).
- `tests/descriptor/eval_descriptors.py` — 10 free-text → ground-truth
  pairs, manual extraction, covers ≥9 of the 12 terrain primaries plus
  all four hydrology shapes. Phase 5's `forge.plan` skill is evaluated
  against this set.
- `tests/descriptor/test_descriptor.py` — 24 tests: round-trip, all 10
  eval pairs validate, 6 rejection cases each surfacing the expected
  structured error code, drift check.

## Eval coverage matrix

| terrain.primary  | has_stream | stream_character     |
|------------------|------------|----------------------|
| alpine_valley    | true       | alpine_creek         |
| rolling_hills    | false      | none                 |
| desert_mesa      | false      | (omitted)            |
| boreal_lowland   | true       | meandering_river     |
| volcanic_cone    | (omitted)  | —                    |
| coastal_cliffs   | (omitted)  | —                    |
| canyon           | true       | dry_wash             |
| plains           | (omitted)  | —                    |
| alpine_peaks     | (omitted)  | —                    |
| marsh            | true       | meandering_river     |

Untested primaries (deferred to Phase 3 mapping iteration):
`alpine_peaks` is in the set; remaining unused primaries
(`desert_dunes`, `river_valley`) do not yet have eval pairs — flag for
Phase 3 to add.

## Strictness notes

- `disallow_any_explicit = true` collides with Pydantic 2 stubs — every
  `class X(BaseModel)` line carries `# type: ignore[explicit-any]
  # pydantic stubs leak Any`. This is **the documented escape pattern**
  for the entire codebase. `field_validator` does **not** trigger it
  (verified empirically; ignores would be flagged as unused).
- `JsonValue` recursive type alias defined in `validate.py` per the
  Phase 1 plan — no `Any` at the validation boundary.
- Coverage on the spike's surface: **98 %** (one defensive raise in
  `_check_notes_length` is the only miss; can be exercised in Phase 3).

## What was not done

- Sphinx-doc / external schema cross-check (deferred to spike 1's
  ingestion pipeline).
- A `forge-schema-export` CLI script (instructions §5 calls for one);
  the test asserts byte-equality against the committed file, and a
  one-liner is documented in the test docstring. Promote to a CLI in
  Phase 2 alongside additional schemas.

## Go/no-go

GO. Schema validates 10/10 eval pairs; rejection cases produce
structured errors with stable codes the agent can self-correct from;
JSON Schema artifact committed and drift-checked.
