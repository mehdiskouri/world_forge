---
name: "forge.audit"
version: "0.1.0"
description: "Spawn an isolated audit subagent that scores a generated region against its descriptor and persists a structured AuditVerdict. Stage A placeholder."
triggers: ["audit this region", "verify the result", "after generation", "after reroll"]
requires_tools: ["forge.get_region", "forge.inspect_spec", "forge.analyze_region", "forge.render_view", "forge.get_descriptor_schema", "forge.get_audit_schema", "forge.record_audit"]
requires_subagent: true
---

# forge.audit (Phase 5 Stage A placeholder)

Full body — including the embedded ``AuditVerdict`` JSON Schema, the
subagent contract, the dimension rubric, and the inline isolated-context
fallback — lands in branch ``skill-audit-content`` after branch
``audit-verdict-schema-and-tools`` ships the schema.
