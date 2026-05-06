"""End-to-end tests for the Phase-5 Stage D audit MCP tool surface."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from forge_mcp.audit.verdict import AUDIT_DIMENSION_NAMES
from forge_mcp.project.schemas import HistoryEventKind
from forge_mcp.project.service import ProjectService
from forge_mcp.server.tools import get_service, set_service
from forge_mcp.server.tools.audit import (
    get_audit,
    get_audit_schema,
    list_audits,
    record_audit,
)
from forge_mcp.server.tools.projects import create_project

from tests.audit._factories import make_verdict

if TYPE_CHECKING:
    from pathlib import Path

_BOUNDS: dict[str, object] = {"min": [0.0, 0.0], "max": [10.0, 10.0]}


@pytest.fixture(autouse=True)
def _isolated_service() -> None:
    set_service(ProjectService())


def _ok(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    assert isinstance(result, dict)
    return result


def _err(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is False, envelope
    error = envelope["error"]
    assert isinstance(error, dict)
    return error


def test_record_audit_requires_open_project() -> None:
    """No open project -> ``no_open_project`` envelope."""
    err = _err(record_audit(make_verdict().model_dump(mode="json")))
    assert err["code"] == "no_open_project"


def test_record_audit_round_trips_through_disk(tmp_path: Path) -> None:
    """Record + get returns the same audit body and appends history."""
    _ok(create_project(str(tmp_path), "Demo", _BOUNDS))
    verdict = make_verdict()
    payload = _ok(record_audit(verdict.model_dump(mode="json")))
    assert payload["audit_id"] == verdict.audit_id
    assert payload["verdict"] == "pass"

    fetched = _ok(get_audit(verdict.audit_id))
    assert fetched["audit_id"] == verdict.audit_id
    assert isinstance(fetched["dimensions"], list)
    assert len(fetched["dimensions"]) == len(AUDIT_DIMENSION_NAMES)

    events = list(get_service().state.history.iter_events())
    kinds = [event.kind for event in events]
    assert HistoryEventKind.AUDIT_RECORDED in kinds


def test_record_audit_invalid_body_returns_structured_error(tmp_path: Path) -> None:
    """A body missing required fields fails with ``invalid_audit_verdict``."""
    _ok(create_project(str(tmp_path), "Demo", _BOUNDS))
    err = _err(record_audit({"verdict": "pass"}))
    assert err["code"] == "invalid_audit_verdict"


def test_list_audits_filters_by_region(tmp_path: Path) -> None:
    """Filter by ``region_id`` narrows the listing."""
    _ok(create_project(str(tmp_path), "Demo", _BOUNDS))
    a = make_verdict(region_id="alpine-bowl", spec_id="spec_aaaaaa")
    b = make_verdict(region_id="bog-1", spec_id="spec_bbbbbb")
    _ok(record_audit(a.model_dump(mode="json")))
    _ok(record_audit(b.model_dump(mode="json")))

    listed = _ok(list_audits(region_id="bog-1"))
    audits = listed["audits"]
    assert isinstance(audits, list)
    assert [a["region_id"] for a in audits] == ["bog-1"]


def test_get_audit_unknown_returns_error(tmp_path: Path) -> None:
    """Unknown audit_id -> ``audit_not_found``."""
    _ok(create_project(str(tmp_path), "Demo", _BOUNDS))
    err = _err(get_audit("audit_deadbeefcafe"))
    assert err["code"] == "audit_not_found"


def test_get_audit_schema_returns_published_schema() -> None:
    """``forge.get_audit_schema`` works without an open project."""
    payload = _ok(get_audit_schema())
    assert payload["title"] == "ForgeAuditVerdict"
