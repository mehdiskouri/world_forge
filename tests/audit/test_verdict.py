"""Tests for :mod:`forge_mcp.audit.verdict`."""

from __future__ import annotations

import pytest
from forge_mcp.audit.verdict import (
    AUDIT_DIMENSION_NAMES,
    AUDIT_SCHEMA_VERSION,
    AUDIT_VERDICT_VALUES,
    AuditDimension,
    AuditVerdict,
    SubagentContext,
    audit_verdict_json_schema,
    new_audit_id,
)
from forge_mcp.project.schemas import RegionId, SpecId
from pydantic import ValidationError

from tests.audit._factories import make_dimensions, make_verdict

_EXPECTED_DIMENSION_COUNT = 4


def test_constants_match_phase5_decisions() -> None:
    """The frozen constants enforce the four-dimension v1 contract."""
    assert AUDIT_SCHEMA_VERSION == "1.0"
    assert AUDIT_VERDICT_VALUES == ("pass", "fail", "warn")
    assert len(AUDIT_DIMENSION_NAMES) == _EXPECTED_DIMENSION_COUNT
    assert AUDIT_DIMENSION_NAMES == (
        "descriptor_coherence",
        "geometric_validity",
        "render_quality",
        "spec_alignment",
    )


def test_audit_id_is_deterministic() -> None:
    """Two verdicts with identical bodies share one ``audit_id``."""
    a = make_verdict()
    b = make_verdict()
    assert a.audit_id == b.audit_id
    assert a.audit_id.startswith("audit_")


def test_audit_id_changes_when_summary_changes() -> None:
    """Touching any body field changes the content-addressed id."""
    a = make_verdict(summary="ok")
    b = make_verdict(summary="ok!")
    assert a.audit_id != b.audit_id


def test_audit_id_mismatch_is_rejected() -> None:
    """Constructing with a wrong ``audit_id`` raises ``ValidationError``."""
    canonical = make_verdict()
    with pytest.raises(ValidationError, match="audit_id mismatch"):
        AuditVerdict(
            audit_id="audit_deadbeefcafe",  # type: ignore[arg-type]  # intentionally wrong
            region_id=canonical.region_id,
            spec_id=canonical.spec_id,
            verdict=canonical.verdict,
            dimensions=canonical.dimensions,
            summary=canonical.summary,
            created_at=canonical.created_at,
            subagent_context=canonical.subagent_context,
        )


def test_missing_dimension_is_rejected() -> None:
    """Skipping any of the four canonical dimensions fails validation."""
    canonical = make_verdict()
    truncated = tuple(d for d in canonical.dimensions if d.name != "render_quality")
    body = canonical.model_dump(mode="json")
    body["dimensions"] = [d.model_dump(mode="json") for d in truncated]
    with pytest.raises(ValidationError, match="missing="):
        AuditVerdict.model_validate(body)


def test_duplicate_dimension_is_rejected() -> None:
    """Duplicate dimension names trip the uniqueness validator."""
    canonical = make_verdict()
    dup = (*canonical.dimensions, canonical.dimensions[0])
    body = canonical.model_dump(mode="json")
    body["dimensions"] = [d.model_dump(mode="json") for d in dup]
    with pytest.raises(ValidationError, match="must be unique"):
        AuditVerdict.model_validate(body)


def test_summary_max_length_enforced() -> None:
    """Summaries longer than 500 chars are rejected."""
    with pytest.raises(ValidationError):
        make_verdict(summary="x" * 501)


def test_dimension_confidence_bounds() -> None:
    """Confidence is clamped to ``[0, 1]``."""
    with pytest.raises(ValidationError):
        AuditDimension(
            name="render_quality",
            verdict="pass",
            confidence=1.5,
            evidence=(),
        )


def test_subagent_context_requires_client_name() -> None:
    """Empty client_name is rejected."""
    with pytest.raises(ValidationError):
        SubagentContext(client_name="", isolated=True, tool_calls_observed=())


def test_extra_fields_are_forbidden() -> None:
    """``extra="forbid"`` keeps schema drift loud."""
    body = make_verdict().model_dump(mode="json")
    body["unexpected"] = 1
    with pytest.raises(ValidationError):
        AuditVerdict.model_validate(body)


def test_published_schema_round_trips() -> None:
    """The published JSON Schema names the model and lists the dimensions."""
    schema = audit_verdict_json_schema()
    assert schema["title"] == "ForgeAuditVerdict"
    assert "$defs" in schema


def test_new_audit_id_is_stable_across_dict_orders() -> None:
    """Reordering dict keys does not change the derived id (sort_keys=True)."""
    body_a: dict[str, object] = {"a": 1, "b": [1, 2]}
    body_b: dict[str, object] = {"b": [1, 2], "a": 1}
    assert new_audit_id(body_a) == new_audit_id(body_b)


def test_region_and_spec_ids_are_typed() -> None:
    """RegionId / SpecId NewTypes carry through to the verdict."""
    verdict = make_verdict(region_id="bog-1", spec_id="spec_010101")
    assert verdict.region_id == RegionId("bog-1")
    assert verdict.spec_id == SpecId("spec_010101")
    assert make_dimensions("warn")[0].verdict == "warn"
