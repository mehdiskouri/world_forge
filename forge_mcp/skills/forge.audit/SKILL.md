---
name: "forge.audit"
version: "0.2.0"
description: "Spawn an isolated audit subagent that scores a generated region against its descriptor and persists a structured AuditVerdict. Read-only with respect to region state; failing audits are recorded but never auto-rerolled."
triggers: ["audit this region", "verify the result", "after generation", "after reroll", "is this generation correct", "check quality"]
requires_tools: ["forge.get_region", "forge.inspect_spec", "forge.analyze_region", "forge.render_view", "forge.get_descriptor_schema", "forge.get_audit_schema", "forge.record_audit"]
requires_subagent: true
---

# forge.audit

Use this skill **after** a region has been generated (or rerolled) to
produce a structured quality verdict. The audit runs in an **isolated
subagent** so the auditor cannot be biased by the planner's prior
context, and so its tool calls are easy to spot-check.

A failing verdict is **recorded** — never silently rerolled. Recovery
(reroll, reword, escalate) is the user's policy, not this skill's.

## When to invoke

Invoke when the user asks to:

* "audit / verify / sanity-check" a region,
* compare a generated result against the descriptor,
* run quality control after a `forge.reroll_seed`,
* score a region before persisting it as a final artefact.

Do **not** invoke when:

* the region has not been generated yet — call `forge.plan` +
  `forge.generate_region` first,
* the user wants a picture, not a verdict — use `forge.visualize`,
* the user wants connectivity diagnosis — use `forge.connect`.

## Subagent contract (mandatory)

This skill **must** run in a fresh, isolated subagent context. The
v1 supported clients are:

| Client | Mechanism | `subagent_context.isolated` |
|---|---|---|
| Claude Code | The built-in `Task` subagent. | `true` |
| Claude Desktop | The built-in subagent primitive. | `true` |
| Cursor | Subagent surface (when enabled). | `true` |
| Anything else | Inline isolated-context fallback (see below). | `false` |

The auditor subagent receives:

* the region's id,
* the descriptor (raw + schema),
* the spec (raw),
* the analysis (numbers from `forge.analyze_region`),
* one rendered preview path per camera (`ortho_top`, `perspective_se`),
* the `AuditVerdict` JSON Schema.

It returns a single MCP tool call: `forge.record_audit(verdict=...)`.
No mutation tools (`forge.generate_region`, `forge.reroll_seed`,
`forge.create_region`, `forge.update_region`, `forge.delete_region`)
may be invoked from inside the subagent. The `tool_calls_observed`
field on the verdict is the audit trail.

### Inline isolated-context fallback

For clients without a subagent primitive, drop into a clearly-marked
"audit context" turn that:

1. lists exactly the read-only tools available
   (`requires_tools` above),
2. forbids any other tool call,
3. produces the verdict in one go,
4. records `subagent_context.isolated = false` so the verdict's
   provenance is honest.

## Workflow (8 steps)

1. **Acquire the region's identity.** Call `forge.get_region(region_id)`.
   If `spec_id` is `None`, return without calling `record_audit` — the
   region is not generated, there is nothing to audit. Tell the user.
2. **Pull the spec.** `forge.inspect_spec(spec_id=...)` (or
   `region_id=...`). Confirm it loads.
3. **Pull the analysis.** `forge.analyze_region(region_id=...)`. If
   this errors with `not_generated`, surface and stop.
4. **Render two pictures, cheaply.**
   `forge.render_view(region_id=..., view_kind="ortho_top",
   resolution="preview")` and the same with
   `view_kind="perspective_se"`. Always use `"preview"` —
   higher resolutions are wasted on a verdict.
5. **Fetch the descriptor schema.** `forge.get_descriptor_schema()`
   so the auditor can ground "descriptor coherence" claims in the
   actual schema.
6. **Fetch the audit schema.** `forge.get_audit_schema()` so the
   auditor knows the exact shape it must produce.
7. **Score each dimension.** Apply the rubric below. Each dimension
   gets a verdict (`pass` / `fail` / `warn`), a confidence in
   `[0.0, 1.0]`, and one or more terse evidence strings.
8. **Record.** `forge.record_audit(verdict=<full body>)`. The server
   computes the content-addressed `audit_id` and appends an
   `audit_recorded` history event. Surface the returned `audit_id` to
   the user.

## Dimension rubric

The verdict body must contain **exactly four** dimensions, in any
order. Each is scored independently.

### `descriptor_coherence`

Did the realized region match the descriptor's intent?

| Verdict | Signal |
|---|---|
| `pass` | Primary terrain matches; modifiers/hydrology present where required. |
| `warn` | Match is imperfect (e.g. modifiers attenuated, hydrology weaker than `intensity` would suggest). |
| `fail` | Wrong primary terrain, or required hydrology missing entirely. |

Evidence: cite the specific descriptor field and the corresponding
analysis number or render observation.

### `geometric_validity`

Is the geometry physically plausible?

| Verdict | Signal |
|---|---|
| `pass` | Elevation range non-zero, no NaNs, slopes within reasonable bounds for terrain primary. |
| `warn` | Edge-of-tolerance values, e.g. very low relief on `mountain_peaks`. |
| `fail` | NaNs, zero relief, slopes > 80°, polygon self-intersection. |

Evidence: numeric values from `analyze_region`.

### `render_quality`

Are the previews informative?

| Verdict | Signal |
|---|---|
| `pass` | Both previews show the terrain clearly; no clipping or full-black/full-white frames. |
| `warn` | One camera is informative, the other is mostly empty/clipped. |
| `fail` | Both previews are unusable (black, clipped, or otherwise broken). |

Evidence: which preview, what is wrong.

### `spec_alignment`

Did the realizer honour the spec?

| Verdict | Signal |
|---|---|
| `pass` | Spec params are reflected in the analysis (e.g. `roughness` scales relief, `seed` is stable). |
| `warn` | Minor drift (e.g. relief is within tolerance but on the low side). |
| `fail` | Spec param had no observable effect. |

Evidence: name the spec field and the observed mismatch.

## Top-level verdict

Reduce the four dimension verdicts to one:

| Top-level | When |
|---|---|
| `pass` | All four dimensions are `pass`. |
| `warn` | Any dimension is `warn` *and* none is `fail`. |
| `fail` | Any dimension is `fail`. |

The `summary` field is **1–3 sentences, ≤ 500 characters**. State the
verdict and the single most important reason; details belong in
dimension `evidence`.

## Embedded `AuditVerdict` JSON Schema

This block is **byte-identical** to the schema returned by
`forge.get_audit_schema()` and to `schemas/audit_verdict.schema.json`
in the repo. CI enforces the equivalence via
`tests/skills/test_audit_skill.py`.

```json
{
  "$defs": {
    "AuditDimension": {
      "additionalProperties": false,
      "description": "One scored axis of the verdict.",
      "properties": {
        "confidence": {
          "maximum": 1.0,
          "minimum": 0.0,
          "title": "Confidence",
          "type": "number"
        },
        "evidence": {
          "default": [],
          "items": {
            "type": "string"
          },
          "title": "Evidence",
          "type": "array"
        },
        "name": {
          "enum": [
            "descriptor_coherence",
            "geometric_validity",
            "render_quality",
            "spec_alignment"
          ],
          "title": "Name",
          "type": "string"
        },
        "verdict": {
          "enum": [
            "pass",
            "fail",
            "warn"
          ],
          "title": "Verdict",
          "type": "string"
        }
      },
      "required": [
        "name",
        "verdict",
        "confidence"
      ],
      "title": "AuditDimension",
      "type": "object"
    },
    "SubagentContext": {
      "additionalProperties": false,
      "description": "Provenance metadata recorded with every verdict.\n\nForge cannot enforce isolation (the subagent runs in the agent\nclient, not in Forge); ``isolated`` is a best-effort claim\nreported by the client. ``tool_calls_observed`` lists the tool\nnames the subagent actually called so a reviewer can spot-check\nthat no mutation tools (``forge.generate_region``,\n``forge.reroll_seed``, locks, etc.) leaked into the audit context.",
      "properties": {
        "client_name": {
          "maxLength": 64,
          "minLength": 1,
          "title": "Client Name",
          "type": "string"
        },
        "isolated": {
          "title": "Isolated",
          "type": "boolean"
        },
        "tool_calls_observed": {
          "default": [],
          "items": {
            "type": "string"
          },
          "title": "Tool Calls Observed",
          "type": "array"
        }
      },
      "required": [
        "client_name",
        "isolated"
      ],
      "title": "SubagentContext",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "description": "Persisted audit-verdict record (frozen, ``extra=\"forbid\"``).",
  "properties": {
    "audit_id": {
      "title": "Audit Id",
      "type": "string"
    },
    "created_at": {
      "format": "date-time",
      "title": "Created At",
      "type": "string"
    },
    "dimensions": {
      "items": {
        "$ref": "#/$defs/AuditDimension"
      },
      "title": "Dimensions",
      "type": "array"
    },
    "region_id": {
      "title": "Region Id",
      "type": "string"
    },
    "schema_version": {
      "const": "1.0",
      "default": "1.0",
      "title": "Schema Version",
      "type": "string"
    },
    "spec_id": {
      "title": "Spec Id",
      "type": "string"
    },
    "subagent_context": {
      "$ref": "#/$defs/SubagentContext"
    },
    "summary": {
      "maxLength": 500,
      "minLength": 1,
      "title": "Summary",
      "type": "string"
    },
    "verdict": {
      "enum": [
        "pass",
        "fail",
        "warn"
      ],
      "title": "Verdict",
      "type": "string"
    }
  },
  "required": [
    "audit_id",
    "region_id",
    "spec_id",
    "verdict",
    "dimensions",
    "summary",
    "created_at",
    "subagent_context"
  ],
  "title": "ForgeAuditVerdict",
  "type": "object"
}
```

## Worked verdict (illustrative)

Region descriptor asks for `mountain_peaks`, `intensity=0.8`,
hydrology with one `seasonal` stream. After generation +
`analyze_region`, elevation range is 1320 m, max slope 47°, one
stream with non-zero length, both previews are clean.

The verdict body the subagent should produce:

```json
{
  "schema_version": "1.0",
  "region_id": "alpine-bowl",
  "spec_id": "spec_aabbcc",
  "verdict": "pass",
  "dimensions": [
    {"name": "descriptor_coherence", "verdict": "pass", "confidence": 0.95,
     "evidence": ["primary=mountain_peaks matches relief; one seasonal stream present"]},
    {"name": "geometric_validity", "verdict": "pass", "confidence": 0.9,
     "evidence": ["elevation_range_m=1320, max_slope_deg=47, no NaNs"]},
    {"name": "render_quality", "verdict": "pass", "confidence": 0.9,
     "evidence": ["ortho_top and perspective_se previews are clean"]},
    {"name": "spec_alignment", "verdict": "pass", "confidence": 0.85,
     "evidence": ["intensity=0.8 reflected in 1320 m relief"]}
  ],
  "summary": "Pass: descriptor honoured, geometry plausible, previews clean.",
  "created_at": "2026-05-06T12:00:00+00:00",
  "subagent_context": {
    "client_name": "claude_code",
    "isolated": true,
    "tool_calls_observed": [
      "forge.get_region", "forge.inspect_spec", "forge.analyze_region",
      "forge.render_view", "forge.get_descriptor_schema", "forge.get_audit_schema"
    ]
  }
}
```

The server fills in `audit_id` (content-addressed). On success it
returns `{audit_id, region_id, verdict}`.

## Common pitfalls

* **Calling a mutation tool from the subagent.** Forge cannot block
  it, but the verdict's `tool_calls_observed` will reveal it and a
  reviewer can reject the audit. Stick to the read-only tools.
* **Skipping a dimension.** Submitting fewer than four dimensions, or
  duplicating a name, fails server-side validation
  (`invalid_audit_verdict`).
* **Re-rendering at `"default"` or `"full"`.** A verdict only needs
  `"preview"`-quality images. Higher resolutions waste seconds and
  add no signal.
* **Auto-rerolling on `fail`.** Never call `forge.reroll_seed` from
  this skill, even on a unanimous fail. Surface the verdict and stop.
* **Lying about `isolated`.** If you fall back to inline context, set
  `isolated=false`. The provenance is the only safety check Forge can
  enforce.

## Failure recovery

| Error code | Meaning | Recovery |
|---|---|---|
| `no_open_project` | No project is loaded. | Tell the user to open one. |
| `unknown_region` | Region not in the project. | List regions; ask the user. |
| `not_generated` | No persisted heightmap. | Tell the user to generate first; do not audit. |
| `invalid_audit_verdict` | Body failed Pydantic validation. | Re-read the embedded schema; the structured `errors` list pinpoints the offending field. |
| `realizer_not_configured` | Cannot render previews. | Surface; if the user permits, score `render_quality` as `warn` with evidence "previews unavailable" and proceed. |
| `audit_not_found` | A subsequent `forge.get_audit` lookup failed. | Echo to the user; the verdict was not persisted. |
