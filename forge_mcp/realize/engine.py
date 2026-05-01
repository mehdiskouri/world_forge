"""RealizerEngine — walks curated v1 macro sequences against a Blender RPC.

Architecture section 5.7. Inputs:

* a :class:`~forge_mcp.bpy_hypergraph.CuratedSequenceBundle` — the
  hand-authored v1 macro library, locked by content hash;
* a :class:`~forge_mcp.bpy_hypergraph.BpyHypergraph` — the operator /
  type / effect / alternative-path catalogue, used to assert at
  construct-time that every sequence's calls reference known operators;
* a connected :class:`~forge_mcp.realize.RpcClient` — the host-side
  view of the Blender 5.0 adapter subprocess.

For each step the engine substitutes ``${name}`` placeholders, snapshots
``scene.diff`` before-and-after when the step declares a postcondition,
invokes the RPC method, validates the postcondition, and appends a
:class:`RealizationTraceStep`. ``seq:<name>`` step calls recurse into a
sub-sequence (depth-1 in v1; the bundle integrity check forbids deeper
nesting). A construction-time version mismatch between the running
Blender and the hypergraph's ``blender_version`` raises
:class:`BlenderVersionMismatchError`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from forge_mcp.bpy_hypergraph.query import load_hypergraph
from forge_mcp.bpy_hypergraph.sequences import (
    CuratedSequenceBundle,
    SequenceStep,
    load_curated_sequences,
)
from forge_mcp.realize.rpc import RpcError, RpcMethods

if TYPE_CHECKING:
    from collections.abc import Mapping

    from forge_mcp.bpy_hypergraph.query import BpyHypergraph
    from forge_mcp.bpy_hypergraph.sequences import CuratedSequence
    from forge_mcp.realize.rpc import JsonObject, JsonValue, RpcClient

_SEQ_PREFIX: Final[str] = "seq:"
_PNG_BUDGET_KEY: Final[str] = "png_max_bytes"
_SCENE_DIFF_KEY: Final[str] = "scene_diff"
_FILE_SIZE_KEY: Final[str] = "file_size_bytes"
REASON_PNG_OVERSIZE: Final[str] = "png_oversize"


class BlenderVersionMismatchError(RuntimeError):
    """Running Blender's version does not match the hypergraph contract."""


class RealizerError(RuntimeError):
    """Base class for engine failures."""


class RealizerStepError(RealizerError):
    """A single step failed (RPC error, postcondition mismatch, ...)."""

    def __init__(  # noqa: PLR0913 - error captures full step coordinates
        self,
        message: str,
        *,
        sequence_name: str,
        step_index: int,
        trace: tuple[RealizationTraceStep, ...],
        cause: Exception | None = None,
        reason_code: str | None = None,
    ) -> None:
        """Carry the partial trace plus the originating step coordinates."""
        super().__init__(message)
        self.sequence_name = sequence_name
        self.step_index = step_index
        self.trace = trace
        self.reason_code = reason_code
        self.__cause__ = cause


@dataclass(frozen=True, slots=True)
class RealizationTraceStep:
    """One executed step in a realization trace."""

    sequence_name: str
    step_index: int
    call: str
    duration_ms: float
    scene_diff_before: Mapping[str, int] | None
    scene_diff_after: Mapping[str, int] | None
    result: JsonValue


@dataclass(frozen=True, slots=True)
class RealizationResult:
    """Trace + final RPC result of an :py:meth:`RealizerEngine.execute_macro` call."""

    macro: str
    trace: tuple[RealizationTraceStep, ...]
    final_result: JsonValue
    total_duration_ms: float
    sequence_id: str


@dataclass(slots=True)
class _ExecState:
    """Mutable per-execution accumulator — internal to one execute_macro call."""

    trace: list[RealizationTraceStep] = field(default_factory=list)
    last_diff: Mapping[str, int] | None = None


@dataclass(frozen=True, slots=True)
class _StepCtx:
    """Internal per-step coordinates passed to validation helpers."""

    sequence_name: str
    index: int
    step: SequenceStep


def _substitute(value: JsonValue, inputs: Mapping[str, JsonValue]) -> JsonValue:
    """Resolve ``${name}`` placeholders in a JSON value tree.

    A bare ``"${name}"`` string is replaced wholesale (preserving the
    referenced value's type). Strings that merely *contain* a placeholder
    are not interpolated — the v1 surface only needs whole-value
    substitution. Dicts and lists are recursed.
    """
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            name = value[2:-1]
            if name not in inputs:
                msg = f"unbound placeholder ${{{name}}}"
                raise KeyError(msg)
            return inputs[name]
        return value
    if isinstance(value, list):
        return [_substitute(v, inputs) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, inputs) for k, v in value.items()}
    return value


def _bind_params(params: JsonObject, inputs: Mapping[str, JsonValue]) -> JsonObject:
    out = _substitute(dict(params), inputs)
    if not isinstance(out, dict):
        msg = "params must remain a dict after substitution"
        raise TypeError(msg)
    return out


def _check_eq(collection: str, target: JsonValue, actual_after: int) -> str | None:
    if not isinstance(target, int) or actual_after != target:
        return f"{collection}: expected eq {target!r}, got {actual_after}"
    return None


def _check_delta(
    collection: str,
    target: JsonValue,
    before: Mapping[str, int] | None,
    actual_after: int,
) -> str | None:
    if not isinstance(target, int):
        return f"{collection}: delta predicate must be int, got {target!r}"
    if before is None:
        return f"{collection}: delta predicate requires a before-snapshot"
    actual_before = before.get(collection, 0)
    if actual_after - actual_before != target:
        return (
            f"{collection}: expected delta {target!r},"
            f" got {actual_after - actual_before}"
            f" (before={actual_before}, after={actual_after})"
        )
    return None


def _check_one_collection(
    collection: str,
    predicate: JsonValue,
    before: Mapping[str, int] | None,
    after: Mapping[str, int],
) -> str | None:
    if not isinstance(predicate, dict):
        return f"scene_diff predicate for {collection!r} must be an object"
    actual_after = after.get(collection)
    if actual_after is None:
        return f"scene.diff did not report {collection!r}"
    if "eq" in predicate:
        reason = _check_eq(collection, predicate["eq"], actual_after)
        if reason is not None:
            return reason
    if "delta" in predicate:
        reason = _check_delta(collection, predicate["delta"], before, actual_after)
        if reason is not None:
            return reason
    return None


def _check_scene_diff(
    expected: Mapping[str, JsonValue],
    before: Mapping[str, int] | None,
    after: Mapping[str, int],
) -> str | None:
    """Return ``None`` on success, else a human-readable mismatch reason."""
    for collection, predicate in expected.items():
        reason = _check_one_collection(collection, predicate, before, after)
        if reason is not None:
            return reason
    return None


def _coerce_scene_diff(raw: JsonValue) -> Mapping[str, int]:
    if not isinstance(raw, dict):
        msg = f"scene.diff result must be an object, got {type(raw).__name__}"
        raise TypeError(msg)
    out: dict[str, int] = {}
    for key, val in raw.items():
        if not isinstance(val, int):
            msg = f"scene.diff[{key!r}] must be int, got {type(val).__name__}"
            raise TypeError(msg)
        out[key] = val
    return out


@dataclass(frozen=True, slots=True)
class _StepObs:
    """Observation accumulated during one step's execution."""

    before: Mapping[str, int] | None
    after: Mapping[str, int] | None
    result: JsonValue
    duration_ms: float


class RealizerEngine:
    """Walks curated v1 macro sequences against a connected Blender RPC."""

    def __init__(
        self,
        client: RpcClient,
        *,
        bundle: CuratedSequenceBundle | None = None,
        hypergraph: BpyHypergraph | None = None,
        skip_version_check: bool = False,
    ) -> None:
        """Validate the version contract then store the loaded artifacts."""
        self._client = client
        self._hypergraph = hypergraph if hypergraph is not None else load_hypergraph()
        self._bundle = (
            bundle if bundle is not None else load_curated_sequences(hypergraph=self._hypergraph)
        )
        if not skip_version_check:
            self._assert_blender_version()

    @property
    def bundle(self) -> CuratedSequenceBundle:
        """The curated-sequence bundle this engine drives."""
        return self._bundle

    def _assert_blender_version(self) -> None:
        result = self._client.call(RpcMethods.PING)
        if not isinstance(result, dict):
            msg = f"ping returned {type(result).__name__}, expected object"
            raise BlenderVersionMismatchError(msg)
        running = result.get("blender")
        expected = self._hypergraph.blender_version
        if running != expected:
            msg = (
                f"running Blender {running!r} does not match curated hypergraph target {expected!r}"
            )
            raise BlenderVersionMismatchError(msg)

    def execute_macro(self, name: str, inputs: Mapping[str, JsonValue]) -> RealizationResult:
        """Execute a curated macro and return the gathered trace."""
        sequence = self._bundle.get(name)
        state = _ExecState()
        wall_start = time.monotonic()
        final = self._execute_sequence(sequence, inputs, state)
        total_ms = (time.monotonic() - wall_start) * 1000.0
        return RealizationResult(
            macro=name,
            trace=tuple(state.trace),
            final_result=final,
            total_duration_ms=total_ms,
            sequence_id=sequence.sequence_id(),
        )

    def _execute_sequence(
        self,
        sequence: CuratedSequence,
        inputs: Mapping[str, JsonValue],
        state: _ExecState,
    ) -> JsonValue:
        last: JsonValue = None
        for index, step in enumerate(sequence.steps):
            ctx = _StepCtx(sequence_name=sequence.name, index=index, step=step)
            last = self._execute_step(ctx, inputs, state)
        return last

    def _execute_step(
        self,
        ctx: _StepCtx,
        inputs: Mapping[str, JsonValue],
        state: _ExecState,
    ) -> JsonValue:
        if ctx.step.call.startswith(_SEQ_PREFIX):
            return self._dispatch_sub_sequence(ctx, inputs, state)
        params = self._bind_or_fail(ctx, inputs, state)

        expects = ctx.step.expects
        wants_diff = expects is not None and _SCENE_DIFF_KEY in expects
        before = state.last_diff if wants_diff else None
        if wants_diff and before is None:
            before = self._snapshot_scene_diff()

        wall_start = time.monotonic()
        result = self._invoke(ctx, params, state)
        duration_ms = (time.monotonic() - wall_start) * 1000.0

        after: Mapping[str, int] | None = None
        if wants_diff:
            after = self._snapshot_scene_diff()
            state.last_diff = after

        obs = _StepObs(before=before, after=after, result=result, duration_ms=duration_ms)
        if expects is not None:
            self._validate_expects(ctx, expects, obs, state)

        state.trace.append(
            RealizationTraceStep(
                sequence_name=ctx.sequence_name,
                step_index=ctx.index,
                call=ctx.step.call,
                duration_ms=duration_ms,
                scene_diff_before=before,
                scene_diff_after=after,
                result=result,
            ),
        )
        return result

    def _dispatch_sub_sequence(
        self,
        ctx: _StepCtx,
        inputs: Mapping[str, JsonValue],
        state: _ExecState,
    ) -> JsonValue:
        sub_name = ctx.step.call.removeprefix(_SEQ_PREFIX)
        try:
            sub_seq = self._bundle.get(sub_name)
        except KeyError as exc:
            msg = f"unknown sub-sequence reference {ctx.step.call!r}"
            raise RealizerStepError(
                msg,
                sequence_name=ctx.sequence_name,
                step_index=ctx.index,
                trace=tuple(state.trace),
                cause=exc,
            ) from exc
        return self._execute_sequence(sub_seq, inputs, state)

    def _bind_or_fail(
        self,
        ctx: _StepCtx,
        inputs: Mapping[str, JsonValue],
        state: _ExecState,
    ) -> JsonObject:
        try:
            return _bind_params(ctx.step.params, inputs)
        except KeyError as exc:
            msg = f"placeholder substitution failed: {exc}"
            raise RealizerStepError(
                msg,
                sequence_name=ctx.sequence_name,
                step_index=ctx.index,
                trace=tuple(state.trace),
                cause=exc,
            ) from exc

    def _invoke(self, ctx: _StepCtx, params: JsonObject, state: _ExecState) -> JsonValue:
        try:
            return self._client.call(ctx.step.call, params)
        except RpcError as exc:
            msg = f"{ctx.step.call} failed: {exc}"
            raise RealizerStepError(
                msg,
                sequence_name=ctx.sequence_name,
                step_index=ctx.index,
                trace=tuple(state.trace),
                cause=exc,
            ) from exc

    def _validate_expects(
        self,
        ctx: _StepCtx,
        expects: JsonObject,
        obs: _StepObs,
        state: _ExecState,
    ) -> None:
        scene_diff_expected = expects.get(_SCENE_DIFF_KEY)
        if isinstance(scene_diff_expected, dict) and obs.after is not None:
            reason = _check_scene_diff(scene_diff_expected, obs.before, obs.after)
            if reason is not None:
                self._record_failure(
                    ctx,
                    obs,
                    state,
                    f"scene-diff postcondition failed: {reason}",
                )
        png_budget = expects.get(_PNG_BUDGET_KEY)
        if isinstance(png_budget, int):
            self._validate_png_budget(ctx, png_budget, obs, state)

    def _validate_png_budget(
        self,
        ctx: _StepCtx,
        budget: int,
        obs: _StepObs,
        state: _ExecState,
    ) -> None:
        if not isinstance(obs.result, dict):
            msg = "png budget enforced but render result is not an object"
            raise TypeError(msg)
        size = obs.result.get(_FILE_SIZE_KEY)
        if not isinstance(size, int):
            msg = "render result missing file_size_bytes"
            raise TypeError(msg)
        if size > budget:
            self._record_failure(
                ctx,
                obs,
                state,
                f"render exceeded png budget: {size} > {budget} bytes",
                reason_code=REASON_PNG_OVERSIZE,
            )

    def _record_failure(
        self,
        ctx: _StepCtx,
        obs: _StepObs,
        state: _ExecState,
        message: str,
        *,
        reason_code: str | None = None,
    ) -> None:
        state.trace.append(
            RealizationTraceStep(
                sequence_name=ctx.sequence_name,
                step_index=ctx.index,
                call=ctx.step.call,
                duration_ms=obs.duration_ms,
                scene_diff_before=obs.before,
                scene_diff_after=obs.after,
                result=obs.result,
            ),
        )
        raise RealizerStepError(
            message,
            sequence_name=ctx.sequence_name,
            step_index=ctx.index,
            trace=tuple(state.trace),
            reason_code=reason_code,
        )

    def _snapshot_scene_diff(self) -> Mapping[str, int]:
        return _coerce_scene_diff(self._client.call(RpcMethods.SCENE_DIFF))
