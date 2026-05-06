"""Smoke + unit tests for ``scripts/eval/skill_plan_eval``.

The harness is pure deterministic Python: it diffs pre-recorded
``{free_text: descriptor}`` extractions against the canonical
``forge.plan/eval_set.json`` fixture and writes a report. These tests
exercise the diff/score/report path against synthetic extractions —
they never invoke an LLM.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

if TYPE_CHECKING:
    from types import ModuleType


class _CanonicalExampleLike(Protocol):
    """Structural view of ``skill_plan_eval.CanonicalExample``."""

    example_id: str
    free_text: str
    descriptor: dict[str, object]


# ----------------------------------------------------------------------
# Module loader (scripts/eval/* is not a Python package; it is exposed via
# pyproject's per-file ignore for INP001). Load it by file path so the
# tests do not depend on sys.path tricks.
# ----------------------------------------------------------------------


def _load_harness() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "eval" / "skill_plan_eval.py"
    spec = importlib.util.spec_from_file_location("skill_plan_eval", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["skill_plan_eval"] = module
    spec.loader.exec_module(module)
    return module


HARNESS = _load_harness()


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _canonical_examples() -> list[_CanonicalExampleLike]:
    return list(HARNESS.load_canonical_examples())


def _all_correct_extractions() -> dict[str, dict[str, object]]:
    return {ex.free_text: ex.descriptor for ex in _canonical_examples()}


# ----------------------------------------------------------------------
# Loader tests
# ----------------------------------------------------------------------


def test_load_canonical_examples_has_at_least_ten_entries() -> None:
    # Phase 5 plan calls for >= 10 canonical descriptors; fixture may grow.
    examples = _canonical_examples()
    assert len(examples) >= 10  # noqa: PLR2004 - canonical fixture floor


def test_load_canonical_examples_unique_ids() -> None:
    examples = _canonical_examples()
    ids = [ex.example_id for ex in examples]
    assert len(set(ids)) == len(ids)


def test_load_extractions_rejects_non_object(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(TypeError, match="must be a JSON object"):
        HARNESS.load_extractions(bad)


def test_load_extractions_rejects_missing_key(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"items": {}}), encoding="utf-8")
    with pytest.raises(TypeError, match="missing object 'extractions'"):
        HARNESS.load_extractions(bad)


def test_load_extractions_rejects_non_object_value(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"extractions": {"x": "not-an-object"}}),
        encoding="utf-8",
    )
    with pytest.raises(TypeError, match="must be a JSON object"):
        HARNESS.load_extractions(bad)


# ----------------------------------------------------------------------
# Scoring tests
# ----------------------------------------------------------------------


def test_score_all_correct_passes() -> None:
    canonical = _canonical_examples()
    report = HARNESS.score(canonical, _all_correct_extractions())
    assert report.exact_match_count == len(canonical)
    assert report.passed
    assert report.field_match_rate == pytest.approx(1.0)
    assert all(r.exact_match for r in report.results)


def test_score_all_missing_fails() -> None:
    canonical = _canonical_examples()
    report = HARNESS.score(canonical, {})
    assert report.exact_match_count == 0
    assert not report.passed
    assert all(r.missing for r in report.results)


def test_score_partial_under_threshold() -> None:
    canonical = _canonical_examples()
    extractions = _all_correct_extractions()
    # Corrupt enough entries that exact_match_count falls below the
    # PASS_THRESHOLD (8). Drop everything beyond the first
    # ``PASS_THRESHOLD - 1`` correct extractions and corrupt one of the
    # survivors.
    threshold = HARNESS.PASS_THRESHOLD
    free_texts = list(extractions)
    for key in free_texts[threshold - 1 :]:
        del extractions[key]
    extractions[free_texts[0]] = {"terrain": {"primary": "wrong"}}
    report = HARNESS.score(canonical, extractions)
    # threshold - 1 correct entries left, minus 1 corrupted = below threshold.
    assert report.exact_match_count == threshold - 2
    assert not report.passed
    corrupted = next(r for r in report.results if r.free_text == free_texts[0])
    assert "terrain.primary" in corrupted.mismatched_fields


def test_score_threshold_exactly_passes() -> None:
    canonical = _canonical_examples()
    extractions = _all_correct_extractions()
    threshold = HARNESS.PASS_THRESHOLD
    free_texts = list(extractions)
    # Keep exactly ``threshold`` correct extractions.
    for key in free_texts[threshold:]:
        del extractions[key]
    report = HARNESS.score(canonical, extractions)
    assert report.exact_match_count == threshold
    assert report.passed


def test_diff_descriptor_handles_missing() -> None:
    matched, total, mismatched = HARNESS.diff_descriptor(
        {"a": {"b": 1, "c": 2}},
        None,
    )
    assert matched == 0
    assert total == 2  # noqa: PLR2004 - two leaves in expected
    assert mismatched == ["a.b", "a.c"]


def test_diff_descriptor_extra_field_counts_as_mismatch() -> None:
    matched, total, mismatched = HARNESS.diff_descriptor(
        {"a": {"b": 1}},
        {"a": {"b": 1, "c": 2}},
    )
    assert matched == 1  # a.b
    assert total == 2  # noqa: PLR2004 - a.b + a.c
    assert mismatched == ["a.c"]


# ----------------------------------------------------------------------
# Reporting tests
# ----------------------------------------------------------------------


def test_render_report_markdown_marks_pass() -> None:
    report = HARNESS.score(_canonical_examples(), _all_correct_extractions())
    md = HARNESS.render_report_markdown(report)
    assert md.startswith("# Plan-skill eval report")
    assert "Verdict: **PASS**" in md
    n = report.total
    assert f"Exact-match count: **{n} / {n}**" in md
    assert md.endswith("\n")


def test_render_report_markdown_marks_fail_with_missing() -> None:
    report = HARNESS.score(_canonical_examples(), {})
    md = HARNESS.render_report_markdown(report)
    assert "Verdict: **FAIL**" in md
    assert "missing" in md


def test_render_diffs_json_round_trips() -> None:
    canonical = _canonical_examples()
    report = HARNESS.score(canonical, _all_correct_extractions())
    payload = HARNESS.render_diffs_json(report)
    assert payload["passed"] is True
    assert payload["exact_match_count"] == len(canonical)
    assert payload["pass_threshold"] == HARNESS.PASS_THRESHOLD
    assert len(payload["results"]) == len(canonical)
    # Round-trips through JSON without loss.
    assert json.loads(json.dumps(payload)) == payload


def test_write_report_creates_files(tmp_path: Path) -> None:
    report = HARNESS.score(_canonical_examples(), _all_correct_extractions())
    out_dir = tmp_path / "phase5"
    md_path, json_path = HARNESS.write_report(report, out_dir)
    assert md_path.exists()
    assert json_path.exists()
    assert md_path.read_text(encoding="utf-8").startswith("# Plan-skill eval report")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["passed"] is True


# ----------------------------------------------------------------------
# CLI tests
# ----------------------------------------------------------------------


def _write_extractions(path: Path, data: dict[str, dict[str, object]]) -> None:
    path.write_text(json.dumps({"extractions": data}), encoding="utf-8")


def test_main_returns_zero_on_pass(tmp_path: Path) -> None:
    extractions_path = tmp_path / "extractions.json"
    _write_extractions(extractions_path, _all_correct_extractions())
    out_dir = tmp_path / "out"
    rc = HARNESS.main(
        [
            "--extractions",
            str(extractions_path),
            "--out",
            str(out_dir),
        ],
    )
    assert rc == 0
    assert (out_dir / "report.md").exists()
    assert (out_dir / "diffs.json").exists()


def test_main_returns_one_on_fail(tmp_path: Path) -> None:
    extractions_path = tmp_path / "extractions.json"
    _write_extractions(extractions_path, {})
    out_dir = tmp_path / "out"
    rc = HARNESS.main(
        [
            "--extractions",
            str(extractions_path),
            "--out",
            str(out_dir),
        ],
    )
    assert rc == 1
