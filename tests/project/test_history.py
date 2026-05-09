"""Tests for :mod:`forge_mcp.project.history`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from forge_mcp.project.history import (
    HistoryError,
    HistoryGapError,
    HistoryLog,
)
from forge_mcp.project.schemas import HistoryActor, HistoryEventKind

if TYPE_CHECKING:
    from pathlib import Path


_NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


def _log(tmp_path: Path) -> HistoryLog:
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    return HistoryLog(history_dir, count=0)


def test_append_writes_zero_padded_event_files(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append(HistoryEventKind.CREATE_PROJECT, at=_NOW, payload={"k": "v"})
    log.append(HistoryEventKind.SAVE_PROJECT, at=_NOW)
    files = sorted(p.name for p in (tmp_path / "history").glob("*.json"))
    assert files == ["0001_create_project.json", "0002_save_project.json"]
    assert log.count == 2  # noqa: PLR2004 - two appended events


def test_iter_events_is_monotonic_and_round_trips(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append(HistoryEventKind.CREATE_PROJECT, at=_NOW, actor=HistoryActor.AGENT)
    log.append(HistoryEventKind.OPEN_PROJECT, at=_NOW)
    log.append(HistoryEventKind.SAVE_PROJECT, at=_NOW)
    events = list(log.iter_events())
    assert [e.event_id for e in events] == ["0001", "0002", "0003"]
    assert [e.kind for e in events] == [
        HistoryEventKind.CREATE_PROJECT,
        HistoryEventKind.OPEN_PROJECT,
        HistoryEventKind.SAVE_PROJECT,
    ]


def test_iter_events_reverse_and_limit(tmp_path: Path) -> None:
    log = _log(tmp_path)
    for _ in range(3):
        log.append(HistoryEventKind.SAVE_PROJECT, at=_NOW)
    rev = list(log.iter_events(reverse=True, limit=2))
    assert [e.event_id for e in rev] == ["0003", "0002"]


def test_iter_events_detects_gap(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append(HistoryEventKind.CREATE_PROJECT, at=_NOW)
    log.append(HistoryEventKind.SAVE_PROJECT, at=_NOW)
    # Manually delete the first file to simulate a gap.
    (tmp_path / "history" / "0001_create_project.json").unlink()
    with pytest.raises(HistoryGapError):
        list(log.iter_events())


def test_iter_events_rejects_unknown_filename(tmp_path: Path) -> None:
    log = _log(tmp_path)
    (tmp_path / "history" / "garbage.json").write_text("{}", encoding="utf-8")
    with pytest.raises(HistoryError, match="unrecognised"):
        list(log.iter_events())


def test_iter_events_rejects_corrupt_payload(tmp_path: Path) -> None:
    log = _log(tmp_path)
    (tmp_path / "history" / "0001_create_project.json").write_text(
        "not valid json",
        encoding="utf-8",
    )
    with pytest.raises(HistoryError, match="failed to load"):
        list(log.iter_events())


def test_iter_events_on_missing_directory_yields_nothing(tmp_path: Path) -> None:
    log = HistoryLog(tmp_path / "does-not-exist", count=0)
    assert list(log.iter_events()) == []
