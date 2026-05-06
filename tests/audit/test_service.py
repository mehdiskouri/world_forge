"""Tests for :class:`forge_mcp.audit.service.AuditService`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from forge_mcp.audit.service import (
    AuditNotFoundError,
    AuditService,
    AuditValidationError,
)
from forge_mcp.audit.verdict import AuditId

from tests.audit._factories import make_verdict

if TYPE_CHECKING:
    from pathlib import Path


def test_record_persists_verdict_and_updates_index(tmp_path: Path) -> None:
    """Recording a verdict writes its body and indexes the summary."""
    service = AuditService(tmp_path)
    verdict = make_verdict()
    recorded = service.record(verdict)
    assert recorded.audit_id == verdict.audit_id

    on_disk = service.paths.verdict_path(verdict.region_id, verdict.audit_id)
    assert on_disk.is_file()

    listed = service.list_audits()
    assert len(listed) == 1
    assert listed[0].audit_id == verdict.audit_id


def test_record_invokes_history_appender(tmp_path: Path) -> None:
    """``history_appender`` fires once with the canonical args."""
    calls: list[tuple[str, str, str, str]] = []

    def appender(region_id: str, spec_id: str, audit_id: str, verdict: str) -> object:
        calls.append((str(region_id), str(spec_id), str(audit_id), verdict))
        return None

    service = AuditService(tmp_path, history_appender=appender)
    verdict = make_verdict()
    service.record(verdict)
    assert calls == [
        (verdict.region_id, verdict.spec_id, verdict.audit_id, verdict.verdict),
    ]


def test_record_is_idempotent_on_identical_body(tmp_path: Path) -> None:
    """Re-recording the same verdict collapses to one index row."""
    service = AuditService(tmp_path)
    verdict = make_verdict()
    service.record(verdict)
    service.record(verdict)
    summaries = service.list_audits()
    assert len(summaries) == 1


def test_list_audits_filters_by_region(tmp_path: Path) -> None:
    """Filter narrows the listing to one region."""
    service = AuditService(tmp_path)
    a = make_verdict(region_id="alpine-bowl", spec_id="spec_aaaaaa")
    b = make_verdict(region_id="bog-1", spec_id="spec_bbbbbb")
    service.record(a)
    service.record(b)
    listed = service.list_audits(region_id=a.region_id)
    assert [s.region_id for s in listed] == [a.region_id]


def test_get_returns_full_body(tmp_path: Path) -> None:
    """``get`` round-trips the on-disk verdict."""
    service = AuditService(tmp_path)
    verdict = make_verdict()
    service.record(verdict)
    fetched = service.get(verdict.audit_id)
    assert fetched.audit_id == verdict.audit_id
    assert fetched.summary == verdict.summary


def test_get_unknown_audit_raises(tmp_path: Path) -> None:
    """Unknown ids raise :class:`AuditNotFoundError`."""
    service = AuditService(tmp_path)
    with pytest.raises(AuditNotFoundError):
        service.get(AuditId("audit_deadbeefcafe"))


def test_corrupt_index_raises_validation_error(tmp_path: Path) -> None:
    """A malformed ``_index.json`` is reported as a structured error."""
    service = AuditService(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    service.paths.index_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(AuditValidationError):
        service.list_audits()


def test_listing_orders_by_created_at(tmp_path: Path) -> None:
    """Summaries returned by ``list_audits`` sort by ``created_at`` ascending."""
    service = AuditService(tmp_path)
    earlier = make_verdict(
        region_id="alpine-bowl",
        summary="first",
        created_at=datetime(2026, 5, 6, 9, 0, 0, tzinfo=UTC),
    )
    later = make_verdict(
        region_id="alpine-bowl",
        summary="second",
        created_at=datetime(2026, 5, 6, 11, 0, 0, tzinfo=UTC),
    )
    # Record in reverse-chronological order to prove the sort isn't accidental.
    service.record(later)
    service.record(earlier)
    listed = service.list_audits()
    assert [s.audit_id for s in listed] == [earlier.audit_id, later.audit_id]
