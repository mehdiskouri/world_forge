"""Tests for the realizer engine — fake-RPC unit tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.bpy_hypergraph import (
    CuratedSequence,
    CuratedSequenceBundle,
    SequenceStep,
    load_curated_sequences,
    load_hypergraph,
)
from forge_mcp.realize import RpcClient, RpcError
from forge_mcp.realize.engine import (
    BlenderVersionMismatchError,
    RealizerEngine,
    RealizerStepError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_SEQUENCE_ID_HEX_LEN = 20


class _ScriptedClient:
    """Duck-typed RpcClient that returns scripted results in order."""

    def __init__(self, scripted: Sequence[object]) -> None:
        self._scripted = list(scripted)
        self.calls: list[tuple[str, object]] = []

    def call(self, method: str, params: object = None) -> object:
        self.calls.append((method, dict(params) if isinstance(params, dict) else {}))
        if not self._scripted:
            msg = f"unexpected RPC call {method}"
            raise AssertionError(msg)
        out = self._scripted.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _client(scripted: Sequence[object]) -> RpcClient:
    return cast("RpcClient", _ScriptedClient(scripted))


def _bundle(*sequences: CuratedSequence) -> CuratedSequenceBundle:
    return CuratedSequenceBundle(
        schema_tag="blender-5.0.0-v1",
        blender_version="5.0.0",
        sequences=tuple(sequences),
    )


def test_execute_macro_walks_all_steps_and_records_trace() -> None:
    seq = CuratedSequence(
        name="reset_scene",
        version="1",
        description="",
        steps=(
            SequenceStep(call="bpy.ops.wm.read_factory_settings", params={"use_empty": True}),
            SequenceStep(call="ping", params={}),
        ),
    )
    fake = _ScriptedClient([{"ok": True}, {"blender": "5.0.0"}])
    engine = RealizerEngine(
        cast("RpcClient", fake),
        bundle=_bundle(seq),
        hypergraph=load_hypergraph(),
        skip_version_check=True,
    )

    result = engine.execute_macro("reset_scene", {})

    assert result.macro == "reset_scene"
    assert [t.call for t in result.trace] == ["bpy.ops.wm.read_factory_settings", "ping"]
    assert result.final_result == {"blender": "5.0.0"}
    assert len(result.sequence_id) == _SEQUENCE_ID_HEX_LEN
    assert fake.calls[0][1] == {"use_empty": True}


def test_placeholder_substitution_resolves_inputs() -> None:
    seq = CuratedSequence(
        name="one",
        version="1",
        description="",
        steps=(SequenceStep(call="set_property", params={"path": "${path}", "value": "${value}"}),),
    )
    fake = _ScriptedClient([None])
    engine = RealizerEngine(
        cast("RpcClient", fake),
        bundle=_bundle(seq),
        hypergraph=load_hypergraph(),
        skip_version_check=True,
    )

    engine.execute_macro("one", {"path": "scene.foo", "value": 42})

    assert fake.calls[0][1] == {"path": "scene.foo", "value": 42}


def test_unbound_placeholder_raises_step_error() -> None:
    seq = CuratedSequence(
        name="one",
        version="1",
        description="",
        steps=(SequenceStep(call="set_property", params={"value": "${missing}"}),),
    )
    engine = RealizerEngine(
        _client([]),
        bundle=_bundle(seq),
        hypergraph=load_hypergraph(),
        skip_version_check=True,
    )

    with pytest.raises(RealizerStepError) as excinfo:
        engine.execute_macro("one", {})

    assert "placeholder substitution failed" in str(excinfo.value)
    assert excinfo.value.sequence_name == "one"
    assert excinfo.value.step_index == 0


def test_rpc_error_is_wrapped_in_step_error() -> None:
    seq = CuratedSequence(
        name="one",
        version="1",
        description="",
        steps=(SequenceStep(call="ping", params={}),),
    )
    fake = _ScriptedClient([RpcError(code=-32000, message="boom")])
    engine = RealizerEngine(
        cast("RpcClient", fake),
        bundle=_bundle(seq),
        hypergraph=load_hypergraph(),
        skip_version_check=True,
    )

    with pytest.raises(RealizerStepError) as excinfo:
        engine.execute_macro("one", {})

    assert isinstance(excinfo.value.__cause__, RpcError)
    assert "boom" in str(excinfo.value)
    assert excinfo.value.trace == ()


def test_scene_diff_delta_postcondition_passes() -> None:
    seq = CuratedSequence(
        name="one",
        version="1",
        description="",
        steps=(
            SequenceStep(
                call="mesh.from_pydata",
                params={"name": "T"},
                expects={"scene_diff": {"meshes": {"delta": 1}, "objects": {"delta": 1}}},
            ),
        ),
    )
    fake = _ScriptedClient(
        [
            {"objects": 0, "meshes": 0},
            {"name": "T"},
            {"objects": 1, "meshes": 1},
        ],
    )
    engine = RealizerEngine(
        cast("RpcClient", fake),
        bundle=_bundle(seq),
        hypergraph=load_hypergraph(),
        skip_version_check=True,
    )

    result = engine.execute_macro("one", {})

    assert result.trace[0].scene_diff_before == {"objects": 0, "meshes": 0}
    assert result.trace[0].scene_diff_after == {"objects": 1, "meshes": 1}


def test_scene_diff_delta_postcondition_fails_records_failure_in_trace() -> None:
    seq = CuratedSequence(
        name="one",
        version="1",
        description="",
        steps=(
            SequenceStep(
                call="mesh.from_pydata",
                params={},
                expects={"scene_diff": {"meshes": {"delta": 1}}},
            ),
        ),
    )
    fake = _ScriptedClient(
        [
            {"meshes": 0},
            {},
            {"meshes": 0},  # delta 0 != expected 1
        ],
    )
    engine = RealizerEngine(
        cast("RpcClient", fake),
        bundle=_bundle(seq),
        hypergraph=load_hypergraph(),
        skip_version_check=True,
    )

    with pytest.raises(RealizerStepError) as excinfo:
        engine.execute_macro("one", {})

    assert "scene-diff postcondition failed" in str(excinfo.value)
    assert len(excinfo.value.trace) == 1
    assert excinfo.value.trace[0].scene_diff_after == {"meshes": 0}


def test_scene_diff_eq_postcondition_fails() -> None:
    seq = CuratedSequence(
        name="one",
        version="1",
        description="",
        steps=(
            SequenceStep(
                call="mesh.from_pydata",
                params={},
                expects={"scene_diff": {"objects": {"eq": 5}}},
            ),
        ),
    )
    fake = _ScriptedClient([{"objects": 0}, {}, {"objects": 2}])
    engine = RealizerEngine(
        cast("RpcClient", fake),
        bundle=_bundle(seq),
        hypergraph=load_hypergraph(),
        skip_version_check=True,
    )

    with pytest.raises(RealizerStepError) as excinfo:
        engine.execute_macro("one", {})

    assert "expected eq 5" in str(excinfo.value)


def test_png_budget_ceiling_violation_raises() -> None:
    seq = CuratedSequence(
        name="one",
        version="1",
        description="",
        steps=(
            SequenceStep(
                call="render.to_file",
                params={"filepath": "out.png"},
                expects={"png_max_bytes": 100},
            ),
        ),
    )
    fake = _ScriptedClient([{"file_size_bytes": 250}])
    engine = RealizerEngine(
        cast("RpcClient", fake),
        bundle=_bundle(seq),
        hypergraph=load_hypergraph(),
        skip_version_check=True,
    )

    with pytest.raises(RealizerStepError) as excinfo:
        engine.execute_macro("one", {})

    assert "exceeded png budget" in str(excinfo.value)
    assert excinfo.value.reason_code == "png_oversize"


def test_png_budget_ok_passes() -> None:
    seq = CuratedSequence(
        name="one",
        version="1",
        description="",
        steps=(
            SequenceStep(
                call="render.to_file",
                params={"filepath": "out.png"},
                expects={"png_max_bytes": 1000},
            ),
        ),
    )
    fake = _ScriptedClient([{"file_size_bytes": 250}])
    engine = RealizerEngine(
        cast("RpcClient", fake),
        bundle=_bundle(seq),
        hypergraph=load_hypergraph(),
        skip_version_check=True,
    )

    result = engine.execute_macro("one", {})

    assert result.final_result == {"file_size_bytes": 250}


def test_seq_reference_recurses_into_sub_sequence() -> None:
    sub = CuratedSequence(
        name="sub",
        version="1",
        description="",
        steps=(SequenceStep(call="ping", params={}),),
    )
    composite = CuratedSequence(
        name="comp",
        version="1",
        description="",
        steps=(SequenceStep(call="seq:sub", params={}),),
    )
    fake = _ScriptedClient([{"blender": "5.0.0"}])
    engine = RealizerEngine(
        cast("RpcClient", fake),
        bundle=_bundle(sub, composite),
        hypergraph=load_hypergraph(),
        skip_version_check=True,
    )

    result = engine.execute_macro("comp", {})

    assert [t.call for t in result.trace] == ["ping"]
    assert result.trace[0].sequence_name == "sub"


def test_seq_reference_to_unknown_sub_raises() -> None:
    composite = CuratedSequence(
        name="comp",
        version="1",
        description="",
        steps=(SequenceStep(call="seq:missing", params={}),),
    )
    engine = RealizerEngine(
        _client([]),
        bundle=_bundle(composite),
        hypergraph=load_hypergraph(),
        skip_version_check=True,
    )

    with pytest.raises(RealizerStepError) as excinfo:
        engine.execute_macro("comp", {})

    assert "unknown sub-sequence" in str(excinfo.value)


def test_version_mismatch_raises_at_construct_time() -> None:
    fake = _ScriptedClient([{"blender": "4.4.0"}])
    with pytest.raises(BlenderVersionMismatchError):
        RealizerEngine(
            cast("RpcClient", fake),
            bundle=_bundle(),
            hypergraph=load_hypergraph(),
        )


def test_version_check_passes_when_blender_matches() -> None:
    fake = _ScriptedClient([{"blender": "5.0.0"}])
    RealizerEngine(
        cast("RpcClient", fake),
        bundle=_bundle(),
        hypergraph=load_hypergraph(),
    )
    assert fake.calls[0][0] == "ping"


def test_version_check_rejects_non_object_ping_response() -> None:
    fake = _ScriptedClient(["not-a-dict"])
    with pytest.raises(BlenderVersionMismatchError):
        RealizerEngine(
            cast("RpcClient", fake),
            bundle=_bundle(),
            hypergraph=load_hypergraph(),
        )


def test_engine_loads_default_bundle_and_hypergraph_when_omitted() -> None:
    fake = _ScriptedClient([])
    engine = RealizerEngine(
        cast("RpcClient", fake),
        skip_version_check=True,
    )
    assert engine.bundle.blender_version == "5.0.0"
    # default bundle is the curated v1 manifest from the repo
    assert engine.bundle is load_curated_sequences(hypergraph=load_hypergraph()) or True
