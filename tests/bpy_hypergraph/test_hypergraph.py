"""Tests for the runtime hypergraph query API and the on-disk artifacts."""

from __future__ import annotations

import json
from importlib import resources
from typing import TYPE_CHECKING

import pytest
from forge_mcp.bpy_hypergraph import (
    HypergraphLoadError,
    load_hypergraph,
)
from forge_mcp.bpy_hypergraph import query as q
from forge_mcp.bpy_hypergraph.query import (
    _build_alternatives,
    _build_effects,
    _build_operator,
    _build_type,
    _read_json,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

EXPECTED_BLENDER_VERSION = "5.0.0"
EXPECTED_SCHEMA_TAG = f"blender-{EXPECTED_BLENDER_VERSION}-v1"
MIN_V1_OPERATORS = 20  # ARCHITECTURE §5.4: ~30-50, v1 floor is 20
EXPECTED_TYPE_COUNT = 11  # see scripts/blender/introspect.py TYPE_ALLOW_LIST


def test_load_hypergraph_loads_committed_artifacts() -> None:
    hg = load_hypergraph()
    assert hg.schema_tag == EXPECTED_SCHEMA_TAG
    assert hg.blender_version == EXPECTED_BLENDER_VERSION
    assert len(hg.operators) >= MIN_V1_OPERATORS
    assert len(hg.types) == EXPECTED_TYPE_COUNT


def test_v1_includes_canonical_operators() -> None:
    hg = load_hypergraph()
    must_have = {
        "bpy.ops.mesh.primitive_plane_add",
        "bpy.ops.object.modifier_add",
        "bpy.ops.image.open",
        "bpy.ops.render.render",
        "bpy.ops.wm.save_as_mainfile",
    }
    assert must_have.issubset(set(hg.list_operators()))


def test_get_operator_returns_structured_entry() -> None:
    hg = load_hypergraph()
    op = hg.get_operator("bpy.ops.mesh.primitive_plane_add")
    assert op.idname == "bpy.ops.mesh.primitive_plane_add"
    # primitive_plane_add must declare a 'size' parameter in 5.0
    param_names = {p.name for p in op.params}
    assert "size" in param_names


def test_get_type_returns_structured_entry() -> None:
    hg = load_hypergraph()
    mesh = hg.get_type("Mesh")
    assert mesh.name == "Mesh"
    assert any(p.name == "vertices" for p in mesh.properties)


def test_effects_attached_to_known_operators() -> None:
    hg = load_hypergraph()
    eff = hg.get_effect("bpy.ops.object.modifier_add")
    assert eff is not None
    assert "active_object.modifiers" in eff.mutates


def test_alternative_paths_prefer_data_for_modifier_add() -> None:
    hg = load_hypergraph()
    alt = hg.get_alternative("bpy.ops.object.modifier_add")
    assert alt is not None
    assert alt.preferred == "data"
    assert alt.data_path is not None
    assert "modifiers.new" in alt.data_path


def test_alternative_paths_keep_render_as_ops() -> None:
    hg = load_hypergraph()
    alt = hg.get_alternative("bpy.ops.render.render")
    assert alt is not None
    assert alt.preferred == "ops"
    assert alt.data_path is None


def test_artifact_schema_tags_consistent() -> None:
    pkg = resources.files("forge_mcp.bpy_hypergraph.data")
    tags: list[str] = []
    for name in ("operators.json", "types.json", "effects.json", "alternative_paths.json"):
        payload = json.loads(pkg.joinpath(name).read_text(encoding="utf-8"))
        tags.append(payload["schema_tag"])
    assert tags == [EXPECTED_SCHEMA_TAG] * 4


def test_load_rejects_schema_tag_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    real = q._read_json  # noqa: SLF001  # monkeypatching internal loader

    def fake_read(name: str) -> Mapping[str, object]:
        payload = dict(real(name))
        if name == "types.json":
            payload["schema_tag"] = "blender-9.9.9-v1"
        return payload

    monkeypatch.setattr(q, "_read_json", fake_read)
    with pytest.raises(HypergraphLoadError, match="schema_tag mismatch"):
        load_hypergraph()


def test_load_rejects_stray_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    real = q._read_json  # noqa: SLF001  # monkeypatching internal loader

    def fake_read(name: str) -> Mapping[str, object]:
        payload = dict(real(name))
        if name == "effects.json":
            effects_obj = payload["effects"]
            assert isinstance(effects_obj, dict)
            effects = dict(effects_obj)
            effects["bpy.ops.does.not_exist"] = {
                "preconditions": [],
                "postconditions": [],
                "mutates": [],
            }
            payload["effects"] = effects
        return payload

    monkeypatch.setattr(q, "_read_json", fake_read)
    with pytest.raises(HypergraphLoadError, match=r"effects\.json references unknown"):
        load_hypergraph()


def test_load_rejects_invalid_alternative_preferred() -> None:
    payload: dict[str, object] = {
        "schema_tag": "x",
        "alternative_paths": {
            "bpy.ops.foo": {"preferred": "weird", "data_path": None, "notes": ""},
        },
    }
    with pytest.raises(HypergraphLoadError, match="must be 'ops' or 'data'"):
        _build_alternatives(payload)


def test_build_effects_rejects_non_object_entry() -> None:
    payload: dict[str, object] = {"effects": {"bpy.ops.foo": "not-an-object"}}
    with pytest.raises(HypergraphLoadError, match="must be an object"):
        _build_effects(payload)


def test_build_effects_rejects_non_object_root() -> None:
    with pytest.raises(HypergraphLoadError, match="must be a JSON object"):
        _build_effects({"effects": "not-a-dict"})


def test_build_alternatives_rejects_non_object_root() -> None:
    with pytest.raises(HypergraphLoadError, match="must be a JSON object"):
        _build_alternatives({"alternative_paths": ["not", "a", "dict"]})


def test_build_alternatives_rejects_non_object_entry() -> None:
    with pytest.raises(HypergraphLoadError, match="must be an object"):
        _build_alternatives({"alternative_paths": {"bpy.ops.foo": "scalar"}})


def test_build_alternatives_rejects_non_string_data_path() -> None:
    payload: dict[str, object] = {
        "alternative_paths": {
            "bpy.ops.foo": {"preferred": "ops", "data_path": 42, "notes": ""},
        },
    }
    with pytest.raises(HypergraphLoadError, match="data_path"):
        _build_alternatives(payload)


def test_build_operator_rejects_non_list_params() -> None:
    with pytest.raises(HypergraphLoadError, match=r"'params' must be a list"):
        _build_operator({"idname": "bpy.ops.x", "params": {}})


def test_build_operator_rejects_non_object_param() -> None:
    with pytest.raises(HypergraphLoadError, match="each param must be an object"):
        _build_operator({"idname": "bpy.ops.x", "params": ["scalar"]})


def test_build_operator_rejects_non_list_bl_options() -> None:
    with pytest.raises(HypergraphLoadError, match=r"'bl_options' must be a list"):
        _build_operator({"idname": "bpy.ops.x", "params": [], "bl_options": {}})


def test_build_type_rejects_non_list_properties() -> None:
    with pytest.raises(HypergraphLoadError, match=r"'properties' must be a list"):
        _build_type({"name": "X", "properties": {}})


def test_build_type_rejects_non_object_property() -> None:
    with pytest.raises(HypergraphLoadError, match="each property must be an object"):
        _build_type({"name": "X", "properties": ["scalar"]})


def test_expect_str_rejects_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    real = q._read_json  # noqa: SLF001  # capture before monkeypatch

    def fake_read(name: str) -> Mapping[str, object]:
        if name == "operators.json":
            return {}
        return real(name)

    monkeypatch.setattr(q, "_read_json", fake_read)
    with pytest.raises(HypergraphLoadError, match="schema_tag"):
        load_hypergraph()


def test_read_json_rejects_non_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "forge_mcp.bpy_hypergraph.query.json.loads",
        lambda _text: ["not", "a", "dict"],
    )
    with pytest.raises(HypergraphLoadError, match="top-level must be a JSON object"):
        _read_json("operators.json")


def test_load_rejects_non_object_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    real = q._read_json  # noqa: SLF001  # capture before monkeypatch

    def fake_read(name: str) -> Mapping[str, object]:
        if name == "operators.json":
            return {
                "schema_tag": EXPECTED_SCHEMA_TAG,
                "blender_version": "5.0.0",
                "operators": {},
            }
        return real(name)

    monkeypatch.setattr(q, "_read_json", fake_read)
    with pytest.raises(HypergraphLoadError, match=r"'operators' must be a list"):
        load_hypergraph()


def test_load_rejects_non_list_types(monkeypatch: pytest.MonkeyPatch) -> None:
    real = q._read_json  # noqa: SLF001  # capture before monkeypatch

    def fake_read(name: str) -> Mapping[str, object]:
        payload = dict(real(name))
        if name == "types.json":
            payload["types"] = {}
        return payload

    monkeypatch.setattr(q, "_read_json", fake_read)
    with pytest.raises(HypergraphLoadError, match=r"'types' must be a list"):
        load_hypergraph()


def test_load_rejects_stray_alternative(monkeypatch: pytest.MonkeyPatch) -> None:
    real = q._read_json  # noqa: SLF001  # capture before monkeypatch

    def fake_read(name: str) -> Mapping[str, object]:
        payload = dict(real(name))
        if name == "alternative_paths.json":
            alts_obj = payload["alternative_paths"]
            assert isinstance(alts_obj, dict)
            alts = dict(alts_obj)
            alts["bpy.ops.does.not_exist"] = {
                "preferred": "ops",
                "data_path": None,
                "notes": "",
            }
            payload["alternative_paths"] = alts
        return payload

    monkeypatch.setattr(q, "_read_json", fake_read)
    with pytest.raises(HypergraphLoadError, match="references unknown operators"):
        load_hypergraph()
