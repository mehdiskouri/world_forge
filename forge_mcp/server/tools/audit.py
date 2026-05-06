"""``forge.record_audit`` / ``list_audits`` / ``get_audit`` / ``get_audit_schema``.

Phase 5 Stage D MCP tools. The audit subagent (running inside the
agent client, not inside Forge) calls ``record_audit`` with a verdict
JSON body it has assembled; Forge validates, persists, and appends a
history event.

A failing verdict is *recorded* but never auto-rerolls a region or
mutates region state. Recovery is the agent's policy.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError

from forge_mcp.audit import (
    AuditNotFoundError,
    AuditService,
    AuditValidationError,
    AuditVerdict,
    audit_verdict_json_schema,
)
from forge_mcp.audit.verdict import AuditId, AuditVerdictValue
from forge_mcp.project.schemas import HistoryEventKind, RegionId, SpecId
from forge_mcp.project.service import NoOpenProjectError
from forge_mcp.server.tools import get_service
from forge_mcp.server.tools._responses import fail, ok


def _build_service() -> AuditService:
    """Bind a fresh :class:`AuditService` to the currently open project."""
    project = get_service()
    state = project.state  # raises NoOpenProjectError; caller must catch
    audits_root = state.paths.audits_dir

    def _append_history_event(
        region_id: RegionId,
        spec_id: SpecId,
        audit_id: AuditId,
        verdict: AuditVerdictValue,
    ) -> object:
        return state.history.append(
            HistoryEventKind.AUDIT_RECORDED,
            at=datetime.now(tz=UTC),
            payload={
                "region_id": region_id,
                "spec_id": spec_id,
                "audit_id": audit_id,
                "verdict": verdict,
            },
        )

    return AuditService(audits_root, history_appender=_append_history_event)


def record_audit(verdict: object) -> dict[str, object]:
    """Validate, persist, and history-log one audit verdict.

    ``verdict`` must be a JSON-shaped dict matching the
    :class:`AuditVerdict` schema (see ``forge.get_audit_schema``).
    """
    try:
        service = _build_service()
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    try:
        parsed = AuditVerdict.model_validate(verdict)
    except ValidationError as exc:
        return fail("invalid_audit_verdict", str(exc), details={"errors": exc.errors()})
    try:
        recorded = service.record(parsed)
    except AuditValidationError as exc:
        return fail(
            "invalid_audit_verdict",
            exc.reason,
            details={"field": exc.field} if exc.field else None,
        )
    return ok(
        {
            "audit_id": str(recorded.audit_id),
            "region_id": str(recorded.region_id),
            "verdict": recorded.verdict,
        },
    )


def list_audits(region_id: str | None = None) -> dict[str, object]:
    """Return audit summaries, optionally filtered by ``region_id``."""
    try:
        service = _build_service()
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    typed_region = RegionId(region_id) if region_id is not None else None
    summaries = service.list_audits(region_id=typed_region)
    return ok(
        {
            "audits": [summary.model_dump(mode="json") for summary in summaries],
        },
    )


def get_audit(audit_id: str) -> dict[str, object]:
    """Return one full audit verdict by id."""
    try:
        service = _build_service()
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    try:
        verdict = service.get(AuditId(audit_id))
    except AuditNotFoundError as exc:
        return fail("audit_not_found", str(exc), details={"audit_id": audit_id})
    return ok(verdict.model_dump(mode="json"))


def get_audit_schema() -> dict[str, object]:
    """Return the published JSON Schema for :class:`AuditVerdict`."""
    return ok(audit_verdict_json_schema())


__all__ = [
    "get_audit",
    "get_audit_schema",
    "list_audits",
    "record_audit",
]
