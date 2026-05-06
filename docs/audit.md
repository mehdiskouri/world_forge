# Forge audit verdicts (v1)

Companion to [`docs/skills.md`](skills.md). This document specifies
how the Phase 5 audit subagent records its findings, where they
live on disk, and how to retrieve them through the MCP surface.

The shape of the verdict is fixed: four dimensions, three-valued
verdicts, JSON-schema'd at version `1.0` and never silently extended
in v1.

---

## 0. Schema source of truth

`AuditVerdict` is defined in
[`forge_mcp/audit/verdict.py`](../forge_mcp/audit/verdict.py) as a
frozen Pydantic model with `extra="forbid"`. It is published as
`schemas/audit_verdict.schema.json` by
`forge-schema-export --check` (CI byte-identity gate) and embedded
verbatim inside `forge_mcp/skills/forge.audit/SKILL.md` (skill
byte-identity gate).

The runtime schema is reachable two ways:

```python
from forge_mcp.audit import audit_verdict_json_schema
schema = audit_verdict_json_schema()
```

```text
forge.get_audit_schema() -> {"schema_version": "1.0", "schema": {...}}
```

`AUDIT_SCHEMA_VERSION` (`forge_mcp.audit.AUDIT_SCHEMA_VERSION`) is the
single string version pinned at `"1.0"`. The plan-skill version
tracks the descriptor schema; the audit-skill version tracks this
constant.

---

## 1. The four fixed dimensions

```python
class AuditDimension(StrEnum):
    DESCRIPTOR_COHERENCE = "descriptor_coherence"
    GEOMETRIC_VALIDITY   = "geometric_validity"
    RENDER_QUALITY       = "render_quality"
    SPEC_ALIGNMENT       = "spec_alignment"
```

Every verdict MUST score all four dimensions exactly once;
`AuditVerdict.model_validator` rejects partial sets and duplicates.
The rubric for each dimension lives in
[`forge_mcp/skills/forge.audit/SKILL.md`](../forge_mcp/skills/forge.audit/SKILL.md)
§4 — that is the file the subagent reads.

The per-dimension verdict is the three-valued enum
`AuditVerdictValue ∈ {"pass", "warn", "fail"}`. The top-level
`AuditVerdict.verdict` field is the reduction:

| Reduction rule                                               | Top-level verdict |
| ------------------------------------------------------------ | ----------------- |
| every dimension `pass`                                        | `pass`            |
| at least one `fail`                                           | `fail`            |
| no `fail` and at least one `warn`                             | `warn`            |

The reduction is recomputed by the `AuditVerdict.model_validator`
against the supplied dimensions; mismatches are rejected.

---

## 2. Deterministic identity

The `audit_id` field is **derived**, not supplied:

```python
audit_id = "audit_" + blake2b(canonical_body, digest_size=6).hex()
```

`canonical_body` is the verdict JSON-encoded with `sort_keys=True`
and `ensure_ascii=False`, with the `audit_id` and `created_at` fields
excluded. The model validator recomputes the id and rejects any value
that does not match — so two different agents recording the same
verdict produce the same id, and the on-disk filename is fully
content-addressed.

`created_at` is captured at `AuditService.record(...)` call time using
`datetime.now(tz=UTC)`; it is written into the on-disk record but does
not feed the id.

---

## 3. Subagent context envelope

The optional `subagent_context: SubagentContext | None` field records
the provenance of the verdict:

```python
class SubagentContext(BaseModel):
    client: str            # "claude_code" | "cursor" | "inline_fallback" | …
    transport: str         # "task_tool" | "isolated_inline" | …
    model: str | None      # caller-supplied; not validated by Forge
    prompt_hash: str | None  # blake2b hex of the audit prompt body, if known
```

Forge does not introspect or rate-limit by these fields; they exist
to make recorded verdicts reproducible and auditable.

---

## 4. Persistence layout

The Phase 4 project tree gains an `audits/` subdirectory in Phase 5:

```
<project>/
├── audits/
│   ├── _index.json                         # cache: list of (region_id, audit_id, verdict, created_at)
│   └── <region_id>/
│       └── <audit_id>.json                 # one verdict per file (content-addressed)
└── … (other Phase 1-4 dirs)
```

`AuditService` (`forge_mcp/audit/service.py`) owns this layout:

- `record(region_id, verdict)` — validates, computes id+timestamp,
  writes the verdict JSON atomically, refreshes `_index.json`,
  appends a `HistoryEventKind.AUDIT_RECORDED` history event.
- `list_audits(region_id=None)` — reads `_index.json`; cheap.
- `get(region_id, audit_id)` — reads the underlying JSON file.

Recording the same verdict twice is a no-op: the second `record` call
hits the same content-addressed filename and exits without rewriting.

---

## 5. MCP tool surface

Four tools, all registered in
[`forge_mcp/server/tools/audit.py`](../forge_mcp/server/tools/audit.py):

| Tool                           | Mutates? | Purpose                                                         |
| ------------------------------ | -------- | --------------------------------------------------------------- |
| `forge.get_audit_schema`       | no       | Returns `{schema_version, schema}` for the embedded JSON Schema |
| `forge.record_audit`           | yes      | Validates a verdict and persists it under `audits/`             |
| `forge.list_audits`            | no       | Lists summaries (region scoped or whole project)                |
| `forge.get_audit`              | no       | Returns one full verdict by `region_id` + `audit_id`            |

`record_audit` is the **only** mutating audit tool. Failed validation
surfaces as a structured `AuditValidationError` envelope (`fail(code,
message, details)` per `forge_mcp/server/tools/_responses.py`); the
caller receives field-level error pointers from Pydantic. A missing
verdict on `get_audit` surfaces as `AuditNotFoundError`.

---

## 6. Worked example

A minimal `pass` verdict for a small alpine-valley region:

```json
{
  "audit_id": "audit_<computed>",
  "schema_version": "1.0",
  "region_id": "rgn_alpine_001",
  "spec_id": "spec_2c4a17f9",
  "created_at": "2026-05-12T10:11:12Z",
  "verdict": "pass",
  "dimensions": [
    {"name": "descriptor_coherence", "verdict": "pass", "evidence": "alpine_valley + creek match free text 'rugged alpine valley'"},
    {"name": "geometric_validity",   "verdict": "pass", "evidence": "no inverted faces; mesh manifold; height range [0,1] preserved"},
    {"name": "render_quality",       "verdict": "pass", "evidence": "preview within 200KB; ortho_top covers region bounds; no missing-texture pinks"},
    {"name": "spec_alignment",       "verdict": "pass", "evidence": "stream anchor positions inside region polygon"}
  ],
  "subagent_context": {
    "client": "claude_code",
    "transport": "task_tool",
    "model": null,
    "prompt_hash": null
  }
}
```

Record it:

```text
forge.record_audit(region_id="rgn_alpine_001", verdict={...the above without audit_id...})
  -> {"audit_id": "audit_<hex>", "stored_path": "audits/rgn_alpine_001/audit_<hex>.json"}
```

Retrieve it:

```text
forge.list_audits(region_id="rgn_alpine_001")
  -> [{"audit_id": "audit_<hex>", "verdict": "pass", "created_at": "..."}]
forge.get_audit(region_id="rgn_alpine_001", audit_id="audit_<hex>")
  -> {full AuditVerdict}
```

---

## 7. What audits do *not* do (v1)

- **Audits do not auto-trigger reroll.** A `fail` verdict is a record;
  the user (or a higher-level skill) decides what to do about it.
- **Forge never spawns the audit subagent itself.** It only ships the
  skill, the schema, and the persistence tools; spawning lives in the
  agent client (Architecture §15: no LLM calls inside Forge).
- **Dimensions are not extensible in v1.** Future schema versions may
  add fields, but each version is a separate published schema with a
  separate `AUDIT_SCHEMA_VERSION` constant.
