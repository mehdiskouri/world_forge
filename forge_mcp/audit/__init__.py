"""Audit subsystem (Phase 5 Stage D): verdict schema + persistence.

Forge contains zero LLM calls (Architecture §15 invariant). The audit
subagent is therefore invoked *by the agent client* (Claude Code's
Task subagent primarily, with an inline isolated-context fallback for
clients without subagent support). Forge ships:

* :class:`AuditVerdict` — the persisted record schema.
* :class:`AuditService` — atomic-write + indexed retrieval helpers.
* MCP tools wired in :mod:`forge_mcp.server.tools.audit`.

A failing audit records a verdict; it never auto-triggers a reroll or
mutates region state. The user/agent decides next steps.
"""

from __future__ import annotations

from forge_mcp.audit.paths import AuditPaths
from forge_mcp.audit.service import (
    AuditNotFoundError,
    AuditService,
    AuditValidationError,
)
from forge_mcp.audit.verdict import (
    AUDIT_DIMENSION_NAMES,
    AUDIT_SCHEMA_VERSION,
    AUDIT_VERDICT_VALUES,
    AuditDimension,
    AuditDimensionName,
    AuditId,
    AuditVerdict,
    AuditVerdictValue,
    SubagentContext,
    audit_verdict_json_schema,
    new_audit_id,
)

__all__ = [
    "AUDIT_DIMENSION_NAMES",
    "AUDIT_SCHEMA_VERSION",
    "AUDIT_VERDICT_VALUES",
    "AuditDimension",
    "AuditDimensionName",
    "AuditId",
    "AuditNotFoundError",
    "AuditPaths",
    "AuditService",
    "AuditValidationError",
    "AuditVerdict",
    "AuditVerdictValue",
    "SubagentContext",
    "audit_verdict_json_schema",
    "new_audit_id",
]
