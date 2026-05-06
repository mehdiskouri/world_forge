"""Helpers shared across the Phase-5 Stage D audit tests."""

from __future__ import annotations

from datetime import UTC, datetime

from forge_mcp.audit.verdict import (
    AUDIT_DIMENSION_NAMES,
    AuditDimension,
    AuditVerdict,
    AuditVerdictValue,
    SubagentContext,
    new_audit_id,
)
from forge_mcp.project.schemas import RegionId, SpecId

_FIXED_TIMESTAMP = datetime(2026, 5, 6, 12, 0, 0, tzinfo=UTC)


def make_dimensions(verdict: AuditVerdictValue = "pass") -> tuple[AuditDimension, ...]:
    """Build one canonical ``AuditDimension`` per fixed axis."""
    return tuple(
        AuditDimension(
            name=name,
            verdict=verdict,
            confidence=0.9,
            evidence=("looks reasonable",),
        )
        for name in AUDIT_DIMENSION_NAMES
    )


def make_verdict(  # noqa: PLR0913 - test factory exposes every verdict knob
    *,
    region_id: str = "alpine-bowl",
    spec_id: str = "spec_aabbcc",
    verdict: AuditVerdictValue = "pass",
    summary: str = "ok",
    created_at: datetime | None = None,
    client_name: str = "claude_code",
    isolated: bool = True,
) -> AuditVerdict:
    """Construct a valid :class:`AuditVerdict` for tests.

    Computes the content-addressed ``audit_id`` from the canonical
    body so the model's id-self-check passes.
    """
    body: dict[str, object] = {
        "schema_version": "1.0",
        "region_id": region_id,
        "spec_id": spec_id,
        "verdict": verdict,
        "dimensions": [dim.model_dump(mode="json") for dim in make_dimensions(verdict)],
        "summary": summary,
        "subagent_context": {
            "client_name": client_name,
            "isolated": isolated,
            "tool_calls_observed": [],
        },
    }
    audit_id = new_audit_id(body)
    return AuditVerdict(
        audit_id=audit_id,
        region_id=RegionId(region_id),
        spec_id=SpecId(spec_id),
        verdict=verdict,
        dimensions=make_dimensions(verdict),
        summary=summary,
        created_at=created_at or _FIXED_TIMESTAMP,
        subagent_context=SubagentContext(
            client_name=client_name,
            isolated=isolated,
            tool_calls_observed=(),
        ),
    )


__all__ = ["make_dimensions", "make_verdict"]
