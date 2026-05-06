r"""Plan-skill extraction eval harness (Phase 5 Stage F item 2).

Deterministic Python scorer for the ``forge.plan`` skill. Compares
*pre-recorded* free-text → descriptor extractions (pasted by a human
operator from a real agent session) against the canonical
``forge_mcp/skills/forge.plan/eval_set.json`` fixture.

The harness itself does **not** call any LLM. It only diffs JSON.

Usage::

    uv run python scripts/eval/skill_plan_eval.py \\
        --extractions path/to/extractions.json \\
        [--out docs/eval/phase5/<UTC-timestamp>/]

``extractions.json`` shape::

    {
      "extractions": {
        "<free_text_string>": { ...descriptor JSON... },
        ...
      }
    }

Outputs ``report.md`` (human-readable summary) and ``diffs.json``
(structured per-descriptor diff) under the output directory.

Pass threshold (PRD R-2 mitigation): ``exact_match_count >= 8`` out of
the 10 canonical descriptors. The harness exits with code ``0`` on
pass, ``1`` on fail.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forge_mcp._io.atomic import atomic_write_text, dump_json
from forge_mcp.skills import skill_root

if TYPE_CHECKING:
    from collections.abc import Iterable

EVAL_SET_RELPATH = "forge.plan/eval_set.json"
PASS_THRESHOLD = 8


# ----------------------------------------------------------------------
# Data types
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalExample:
    """One row from ``eval_set.json``."""

    example_id: str
    free_text: str
    descriptor: dict[str, Any]


@dataclass(frozen=True)
class ExampleResult:
    """Per-example scoring outcome."""

    example_id: str
    free_text: str
    expected: dict[str, Any]
    actual: dict[str, Any] | None
    exact_match: bool
    matched_fields: int
    total_fields: int
    mismatched_fields: list[str]
    missing: bool


@dataclass(frozen=True)
class EvalReport:
    """Aggregate report across all canonical examples."""

    total: int
    exact_match_count: int
    matched_field_total: int
    field_total: int
    results: tuple[ExampleResult, ...]

    @property
    def field_match_rate(self) -> float:
        """Return the cross-example field-level match rate in ``[0, 1]``."""
        if self.field_total == 0:
            return 1.0
        return self.matched_field_total / self.field_total

    @property
    def passed(self) -> bool:
        """Return ``True`` when ``exact_match_count >= PASS_THRESHOLD``."""
        return self.exact_match_count >= PASS_THRESHOLD


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------


def load_canonical_examples(eval_set_path: Path | None = None) -> list[CanonicalExample]:
    """Load the 10 canonical free-text → descriptor pairs.

    Args:
        eval_set_path: Optional explicit path to ``eval_set.json``. When
            ``None``, loads the packaged fixture from
            ``forge_mcp/skills/forge.plan/eval_set.json``.

    Returns:
        A list of :class:`CanonicalExample` in fixture order.

    Raises:
        ValueError: If the fixture is malformed.
    """
    path = eval_set_path or (skill_root() / EVAL_SET_RELPATH)
    raw = json.loads(path.read_text(encoding="utf-8"))
    examples_raw = raw.get("examples")
    if not isinstance(examples_raw, list):
        msg = f"eval_set at {path} missing list 'examples'"
        raise TypeError(msg)
    out: list[CanonicalExample] = []
    for entry in examples_raw:
        if not isinstance(entry, dict):
            msg = f"eval_set entry is not an object: {entry!r}"
            raise TypeError(msg)
        try:
            example_id = str(entry["id"])
            free_text = str(entry["free_text"])
            descriptor = entry["descriptor"]
        except KeyError as exc:
            msg = f"eval_set entry missing key {exc.args[0]!r}: {entry!r}"
            raise ValueError(msg) from exc
        if not isinstance(descriptor, dict):
            msg = f"eval_set descriptor must be object for id={example_id!r}"
            raise TypeError(msg)
        out.append(
            CanonicalExample(
                example_id=example_id,
                free_text=free_text,
                descriptor=descriptor,
            ),
        )
    return out


def load_extractions(path: Path) -> dict[str, dict[str, Any]]:
    """Load a recorded ``{free_text: descriptor}`` extractions file.

    Args:
        path: JSON file written by the operator after pasting agent
            outputs. Top-level key ``"extractions"`` is required.

    Returns:
        Mapping from free-text string to extracted descriptor JSON.

    Raises:
        ValueError: If the file shape is wrong.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"extractions file {path} must be a JSON object"
        raise TypeError(msg)
    extractions = raw.get("extractions")
    if not isinstance(extractions, dict):
        msg = f"extractions file {path} missing object 'extractions'"
        raise TypeError(msg)
    out: dict[str, dict[str, Any]] = {}
    for key, value in extractions.items():
        if not isinstance(value, dict):
            msg = f"extraction for {key!r} must be a JSON object"
            raise TypeError(msg)
        out[str(key)] = value
    return out


# ----------------------------------------------------------------------
# Diff + scoring
# ----------------------------------------------------------------------


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:  # noqa: ANN401 - JSON values
    """Flatten a nested JSON object to ``{"a.b.c": leaf}`` form.

    Lists are treated as opaque leaves: equality is structural. Only
    object keys produce nested dotted paths.
    """
    if isinstance(obj, dict):
        flat: dict[str, Any] = {}
        for key, value in obj.items():
            sub_prefix = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                flat.update(_flatten(value, sub_prefix))
            else:
                flat[sub_prefix] = value
        return flat
    return {prefix: obj}


def diff_descriptor(
    expected: dict[str, Any],
    actual: dict[str, Any] | None,
) -> tuple[int, int, list[str]]:
    """Compute per-field match counts and list mismatched dotted keys.

    Args:
        expected: Canonical descriptor.
        actual: Extracted descriptor (or ``None`` if missing entirely).

    Returns:
        Tuple of ``(matched_fields, total_fields, mismatched_dotted_keys)``.
        When ``actual`` is ``None`` every expected field counts as a
        mismatch.
    """
    expected_flat = _flatten(expected)
    if actual is None:
        return (0, len(expected_flat), sorted(expected_flat))
    actual_flat = _flatten(actual)
    all_keys = set(expected_flat) | set(actual_flat)
    matched = 0
    mismatched: list[str] = []
    for key in sorted(all_keys):
        if expected_flat.get(key, _SENTINEL) == actual_flat.get(key, _SENTINEL):
            matched += 1
        else:
            mismatched.append(key)
    return (matched, len(all_keys), mismatched)


_SENTINEL = object()


def score(
    canonical: Iterable[CanonicalExample],
    extractions: dict[str, dict[str, Any]],
) -> EvalReport:
    """Score a set of extractions against the canonical fixture.

    Args:
        canonical: Iterable of canonical examples.
        extractions: ``{free_text: descriptor}`` from the operator.

    Returns:
        Aggregate :class:`EvalReport`.
    """
    results: list[ExampleResult] = []
    matched_total = 0
    field_total = 0
    exact = 0
    for example in canonical:
        actual = extractions.get(example.free_text)
        missing = actual is None
        matched, total, mismatched = diff_descriptor(example.descriptor, actual)
        is_exact = (not missing) and not mismatched and matched == total
        if is_exact:
            exact += 1
        matched_total += matched
        field_total += total
        results.append(
            ExampleResult(
                example_id=example.example_id,
                free_text=example.free_text,
                expected=example.descriptor,
                actual=actual,
                exact_match=is_exact,
                matched_fields=matched,
                total_fields=total,
                mismatched_fields=mismatched,
                missing=missing,
            ),
        )
    return EvalReport(
        total=len(results),
        exact_match_count=exact,
        matched_field_total=matched_total,
        field_total=field_total,
        results=tuple(results),
    )


# ----------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------


def render_report_markdown(report: EvalReport) -> str:
    """Render a human-readable Markdown summary of ``report``."""
    lines: list[str] = [
        "# Plan-skill eval report",
        "",
        f"- Total examples: **{report.total}**",
        f"- Exact-match count: **{report.exact_match_count} / {report.total}**",
        f"- Pass threshold: **{PASS_THRESHOLD} / {report.total}**",
        f"- Verdict: **{'PASS' if report.passed else 'FAIL'}**",
        f"- Field-level match rate: **{report.field_match_rate:.3f}**",
        "",
        "## Per-example",
        "",
    ]
    for result in report.results:
        status = "exact" if result.exact_match else ("missing" if result.missing else "diff")
        lines.append(f"### `{result.example_id}` — {status}")
        lines.append("")
        lines.append(f"- Free text: {result.free_text!r}")
        lines.append(
            f"- Field match: {result.matched_fields} / {result.total_fields}",
        )
        if result.mismatched_fields:
            lines.append("- Mismatched fields:")
            lines.extend(f"  - `{key}`" for key in result.mismatched_fields)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_diffs_json(report: EvalReport) -> dict[str, Any]:
    """Render the structured ``diffs.json`` payload."""
    return {
        "schema_version": "1.0",
        "total": report.total,
        "exact_match_count": report.exact_match_count,
        "pass_threshold": PASS_THRESHOLD,
        "passed": report.passed,
        "field_match_rate": report.field_match_rate,
        "results": [
            {
                "id": r.example_id,
                "free_text": r.free_text,
                "exact_match": r.exact_match,
                "missing": r.missing,
                "matched_fields": r.matched_fields,
                "total_fields": r.total_fields,
                "mismatched_fields": list(r.mismatched_fields),
                "expected": r.expected,
                "actual": r.actual,
            }
            for r in report.results
        ],
    }


def write_report(report: EvalReport, out_dir: Path) -> tuple[Path, Path]:
    """Write ``report.md`` and ``diffs.json`` under ``out_dir`` atomically.

    Returns:
        Tuple ``(report_md_path, diffs_json_path)``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "report.md"
    diffs_path = out_dir / "diffs.json"
    atomic_write_text(report_path, render_report_markdown(report))
    diffs_path.write_text(dump_json(render_diffs_json(report)), encoding="utf-8")
    return (report_path, diffs_path)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _default_out_dir() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("docs/eval/phase5") / stamp


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="skill_plan_eval",
        description="Score recorded plan-skill extractions against the canonical fixture.",
    )
    parser.add_argument(
        "--extractions",
        type=Path,
        required=True,
        help="Path to JSON file: {'extractions': {free_text: descriptor}}.",
    )
    parser.add_argument(
        "--eval-set",
        type=Path,
        default=None,
        help="Optional override for the canonical eval_set.json.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory (default docs/eval/phase5/<UTC-timestamp>/).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns process exit code."""
    args = _parse_args(argv)
    canonical = load_canonical_examples(args.eval_set)
    extractions = load_extractions(args.extractions)
    report = score(canonical, extractions)
    out_dir = args.out or _default_out_dir()
    report_path, diffs_path = write_report(report, out_dir)
    sys.stdout.write(
        f"Plan-skill eval: {report.exact_match_count}/{report.total} exact match"
        f" (threshold {PASS_THRESHOLD})\n"
        f"Wrote {report_path}\nWrote {diffs_path}\n",
    )
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover - CLI shim
    raise SystemExit(main())
