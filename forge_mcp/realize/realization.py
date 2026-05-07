"""Realization-trace sidecar models + persistence.

When ``forge.generate_region`` (or ``forge.render_view``) drives the
realizer, the resulting :class:`~forge_mcp.realize.engine.RealizationResult`
gets serialised to ``realizations/blender/<region_id>.trace.json`` so
later phases (audit subagent, debugging) can read what actually
happened without re-running the realizer.

The sidecar is content-addressed only by ``region_id`` + ``view_kind``
and lives outside the spec body — it is not part of the spec hash so
re-running the realizer never changes ``spec_id``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict

from forge_mcp._io.atomic import write_json
from forge_mcp.realize.rpc import (
    JsonValue,  # noqa: TC001 - needed at runtime for pydantic forward refs
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from forge_mcp.realize.engine import RealizationResult, RealizationTraceStep


class TraceStepRecord(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """One executed step, in canonical-JSON form."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    sequence_name: str
    step_index: int
    call: str
    duration_ms: float
    scene_diff_before: dict[str, int] | None
    scene_diff_after: dict[str, int] | None
    result: JsonValue


class RealizationTraceRecord(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Persisted realization trace for one region + view."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    region_id: str
    view_kind: str
    macro: str
    sequence_id: str
    total_duration_ms: float
    final_result: JsonValue
    steps: tuple[TraceStepRecord, ...]
    plan_id: str | None = None


def _diff_to_dict(diff: Mapping[str, int] | None) -> dict[str, int] | None:
    if diff is None:
        return None
    return dict(diff)


def _step_to_record(step: RealizationTraceStep) -> TraceStepRecord:
    return TraceStepRecord(
        sequence_name=step.sequence_name,
        step_index=step.step_index,
        call=step.call,
        duration_ms=step.duration_ms,
        scene_diff_before=_diff_to_dict(step.scene_diff_before),
        scene_diff_after=_diff_to_dict(step.scene_diff_after),
        result=step.result,
    )


def record_from_result(
    result: RealizationResult,
    *,
    region_id: str,
    view_kind: str,
    plan_id: str | None = None,
) -> RealizationTraceRecord:
    """Build a :class:`RealizationTraceRecord` from the engine's output.

    ``plan_id`` is the deterministic content hash of the
    :class:`~forge_mcp.realize.material.CompositeMaterialPlan` used
    during realization; recorded so a downstream auditor can link the
    rendered material slot back to the resolver inputs.
    """
    return RealizationTraceRecord(
        region_id=region_id,
        view_kind=view_kind,
        macro=result.macro,
        sequence_id=result.sequence_id,
        total_duration_ms=result.total_duration_ms,
        final_result=result.final_result,
        steps=tuple(_step_to_record(s) for s in result.trace),
        plan_id=plan_id,
    )


def write_trace_record(path: Path, record: RealizationTraceRecord) -> None:
    """Atomically write ``record`` as canonical JSON to ``path``."""
    write_json(path, record)


__all__ = [
    "RealizationTraceRecord",
    "TraceStepRecord",
    "record_from_result",
    "write_trace_record",
]
