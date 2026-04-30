"""Tests for the typed macro facade + no-bpy guard."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.bpy_hypergraph.sequences import (
    CuratedSequence,
    CuratedSequenceBundle,
    SequenceStep,
    load_curated_sequences,
)
from forge_mcp.realize import macros
from forge_mcp.realize.engine import RealizerEngine

if TYPE_CHECKING:
    from collections.abc import Sequence

    from forge_mcp.realize.rpc import RpcClient


class _ScriptedClient:
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


def _engine(bundle: CuratedSequenceBundle, scripted: Sequence[object]) -> RealizerEngine:
    return RealizerEngine(
        cast("RpcClient", _ScriptedClient(scripted)),
        bundle=bundle,
        skip_version_check=True,
    )


def test_to_inputs_rejects_non_dataclass() -> None:
    with pytest.raises(TypeError, match="dataclass instance"):
        macros._to_inputs("not a dataclass")  # noqa: SLF001


def test_to_inputs_rejects_dataclass_class_object() -> None:
    with pytest.raises(TypeError, match="dataclass instance"):
        macros._to_inputs(macros.CarveStreamInputs)  # noqa: SLF001


def test_to_inputs_extracts_fields() -> None:
    payload = macros.CarveStreamInputs(curve_name="c", region_id="r")
    out = macros._to_inputs(payload)  # noqa: SLF001
    assert out == {"curve_name": "c", "region_id": "r"}


def test_macro_names_match_curated_bundle() -> None:
    bundle = load_curated_sequences()
    expected = {
        macros.MACRO_RESET_SCENE,
        macros.MACRO_CREATE_TERRAIN,
        macros.MACRO_APPLY_TERRAIN_MATERIAL,
        macros.MACRO_CARVE_STREAM,
        macros.MACRO_SET_CAMERA_OVERVIEW,
        macros.MACRO_ADD_BASIC_LIGHTING,
        macros.MACRO_RENDER_PREVIEW,
        macros.MACRO_SAVE_BLEND,
        macros.MACRO_REALIZE_REGION,
    }
    assert expected.issubset(set(bundle.names()))


def _trivial_bundle(name: str, *steps: SequenceStep) -> CuratedSequenceBundle:
    return CuratedSequenceBundle(
        schema_tag="blender-5.0.0-v1",
        blender_version="5.0.0",
        sequences=(
            CuratedSequence(
                name=name,
                version="1",
                description="",
                steps=steps,
            ),
        ),
    )


def test_reset_scene_facade_invokes_engine() -> None:
    bundle = _trivial_bundle(
        macros.MACRO_RESET_SCENE,
        SequenceStep(call="ping", params={}),
    )
    engine = _engine(bundle, [{"alive": True}])
    result = macros.reset_scene(engine)
    assert result.macro == macros.MACRO_RESET_SCENE
    assert result.final_result == {"alive": True}


def test_carve_stream_facade_passes_inputs_through() -> None:
    bundle = _trivial_bundle(
        macros.MACRO_CARVE_STREAM,
        SequenceStep(call="set_idprop", params={"name": "${curve_name}"}),
    )
    engine = _engine(bundle, [None])
    macros.carve_stream(
        engine,
        macros.CarveStreamInputs(curve_name="stream-7", region_id="r-1"),
    )
    fake = cast("_ScriptedClient", engine._client)  # noqa: SLF001
    assert fake.calls[0][1] == {"name": "stream-7"}


def test_render_preview_facade_threads_render_inputs() -> None:
    bundle = _trivial_bundle(
        macros.MACRO_RENDER_PREVIEW,
        SequenceStep(
            call="render.to_file",
            params={"filepath": "${filepath}", "engine": "${engine}"},
            expects={"png_max_bytes": 1000},
        ),
    )
    engine = _engine(bundle, [{"file_size_bytes": 500}])
    result = macros.render_preview(
        engine,
        macros.RenderPreviewInputs(
            filepath="out.png",
            resolution_x=512,
            resolution_y=384,
            camera_name="cam",
            engine="CYCLES",
        ),
    )
    assert result.final_result == {"file_size_bytes": 500}


def test_no_bpy_imports_in_macros_module() -> None:
    src = Path(macros.__file__).read_text(encoding="utf-8")
    forbidden = [ln for ln in src.splitlines() if "import bpy" in ln or "from bpy" in ln]
    assert not forbidden, f"forge_mcp/realize/macros.py must not import bpy: {forbidden!r}"
