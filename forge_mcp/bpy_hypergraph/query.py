"""Loaders and query helpers for the bpy hypergraph artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping
    from importlib.resources.abc import Traversable


class HypergraphLoadError(RuntimeError):
    """Raised when the on-disk hypergraph artifact is invalid or inconsistent."""


@dataclass(frozen=True, slots=True)
class OperatorParam:
    """One declared parameter on a Blender operator."""

    name: str
    type: str
    is_required: bool
    is_output: bool
    description: str


@dataclass(frozen=True, slots=True)
class OperatorEntry:
    """A curated v1 operator (one of ~30-50)."""

    idname: str
    label: str
    description: str
    bl_options: tuple[str, ...]
    params: tuple[OperatorParam, ...]


@dataclass(frozen=True, slots=True)
class TypeProperty:
    """A property declared on a curated bpy.type."""

    name: str
    type: str
    description: str
    is_readonly: bool


@dataclass(frozen=True, slots=True)
class TypeEntry:
    """A curated bpy.type (Mesh, Material, ...)."""

    name: str
    description: str
    properties: tuple[TypeProperty, ...]


@dataclass(frozen=True, slots=True)
class EffectAnnotation:
    """Hand-curated pre/post conditions and mutation set for one operator."""

    preconditions: tuple[str, ...]
    postconditions: tuple[str, ...]
    mutates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AlternativePath:
    """Mapping from a bpy.ops idname to its bpy.data equivalent (if any)."""

    data_path: str | None
    preferred: str  # 'ops' | 'data'
    notes: str


@dataclass(frozen=True, slots=True)
class BpyHypergraph:
    """In-memory view of the four hypergraph artifacts.

    All collections are immutable. Lookups by ``idname`` are O(1) via
    pre-built dicts; iteration order matches the on-disk artifact (sorted
    by ``idname`` / ``name``).
    """

    schema_tag: str
    blender_version: str
    operators: tuple[OperatorEntry, ...]
    types: tuple[TypeEntry, ...]
    _operators_by_idname: dict[str, OperatorEntry]
    _types_by_name: dict[str, TypeEntry]
    _effects: dict[str, EffectAnnotation]
    _alternatives: dict[str, AlternativePath]

    def get_operator(self, idname: str) -> OperatorEntry:
        """Return the operator with the given fully-qualified idname.

        Raises:
            KeyError: ``idname`` is not in the v1 allow-list.
        """
        return self._operators_by_idname[idname]

    def get_type(self, name: str) -> TypeEntry:
        """Return the curated bpy.type with the given short name."""
        return self._types_by_name[name]

    def get_effect(self, idname: str) -> EffectAnnotation | None:
        """Return effect annotations for an operator, or ``None`` if not annotated."""
        return self._effects.get(idname)

    def get_alternative(self, idname: str) -> AlternativePath | None:
        """Return the bpy.data alternative for an operator, or ``None``."""
        return self._alternatives.get(idname)

    def list_operators(self) -> tuple[str, ...]:
        """Return all v1 operator idnames in canonical order."""
        return tuple(op.idname for op in self.operators)


_DATA_PACKAGE: Final[str] = "forge_mcp.bpy_hypergraph.data"


def _read_json(name: str) -> Mapping[str, object]:
    res: Traversable = resources.files(_DATA_PACKAGE).joinpath(name)
    text = res.read_text(encoding="utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        msg = f"{name}: top-level must be a JSON object, got {type(payload).__name__}"
        raise HypergraphLoadError(msg)
    return payload


def _expect_str(payload: Mapping[str, object], key: str, source: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        msg = f"{source}: missing or non-string '{key}'"
        raise HypergraphLoadError(msg)
    return value


def _build_operator(raw: Mapping[str, object]) -> OperatorEntry:
    params_raw = raw.get("params", [])
    if not isinstance(params_raw, list):
        msg = f"operator {raw.get('idname')!r}: 'params' must be a list"
        raise HypergraphLoadError(msg)
    params: list[OperatorParam] = []
    for p in params_raw:
        if not isinstance(p, dict):
            msg = f"operator {raw.get('idname')!r}: each param must be an object"
            raise HypergraphLoadError(msg)
        params.append(
            OperatorParam(
                name=str(p["name"]),
                type=str(p["type"]),
                is_required=bool(p.get("is_required", False)),
                is_output=bool(p.get("is_output", False)),
                description=str(p.get("description", "")),
            ),
        )
    bl_opts_raw = raw.get("bl_options", [])
    if not isinstance(bl_opts_raw, list):
        msg = f"operator {raw.get('idname')!r}: 'bl_options' must be a list"
        raise HypergraphLoadError(msg)
    return OperatorEntry(
        idname=str(raw["idname"]),
        label=str(raw.get("label", "")),
        description=str(raw.get("description", "")),
        bl_options=tuple(str(o) for o in bl_opts_raw),
        params=tuple(params),
    )


def _build_type(raw: Mapping[str, object]) -> TypeEntry:
    props_raw = raw.get("properties", [])
    if not isinstance(props_raw, list):
        msg = f"type {raw.get('name')!r}: 'properties' must be a list"
        raise HypergraphLoadError(msg)
    props: list[TypeProperty] = []
    for p in props_raw:
        if not isinstance(p, dict):
            msg = f"type {raw.get('name')!r}: each property must be an object"
            raise HypergraphLoadError(msg)
        props.append(
            TypeProperty(
                name=str(p["name"]),
                type=str(p["type"]),
                description=str(p.get("description", "")),
                is_readonly=bool(p.get("is_readonly", False)),
            ),
        )
    return TypeEntry(
        name=str(raw["name"]),
        description=str(raw.get("description", "")),
        properties=tuple(props),
    )


def _build_effects(payload: Mapping[str, object]) -> dict[str, EffectAnnotation]:
    raw = payload.get("effects", {})
    if not isinstance(raw, dict):
        msg = "effects.json: 'effects' must be a JSON object"
        raise HypergraphLoadError(msg)
    out: dict[str, EffectAnnotation] = {}
    for idname, entry in raw.items():
        if not isinstance(entry, dict):
            msg = f"effects[{idname!r}]: must be an object"
            raise HypergraphLoadError(msg)
        out[str(idname)] = EffectAnnotation(
            preconditions=tuple(str(s) for s in entry.get("preconditions", []) or []),
            postconditions=tuple(str(s) for s in entry.get("postconditions", []) or []),
            mutates=tuple(str(s) for s in entry.get("mutates", []) or []),
        )
    return out


def _build_alternatives(payload: Mapping[str, object]) -> dict[str, AlternativePath]:
    raw = payload.get("alternative_paths", {})
    if not isinstance(raw, dict):
        msg = "alternative_paths.json: 'alternative_paths' must be a JSON object"
        raise HypergraphLoadError(msg)
    out: dict[str, AlternativePath] = {}
    for idname, entry in raw.items():
        if not isinstance(entry, dict):
            msg = f"alternative_paths[{idname!r}]: must be an object"
            raise HypergraphLoadError(msg)
        data_path_raw = entry.get("data_path")
        if data_path_raw is not None and not isinstance(data_path_raw, str):
            msg = f"alternative_paths[{idname!r}]: 'data_path' must be string or null"
            raise HypergraphLoadError(msg)
        preferred = str(entry.get("preferred", ""))
        if preferred not in {"ops", "data"}:
            msg = (
                f"alternative_paths[{idname!r}]: 'preferred' must be 'ops' or 'data',"
                f" got {preferred!r}"
            )
            raise HypergraphLoadError(msg)
        out[str(idname)] = AlternativePath(
            data_path=data_path_raw,
            preferred=preferred,
            notes=str(entry.get("notes", "")),
        )
    return out


def load_hypergraph() -> BpyHypergraph:
    """Load and validate the four committed hypergraph artifacts.

    Raises:
        HypergraphLoadError: any artifact is missing, structurally
            invalid, or carries a different ``schema_tag`` than the
            others.
    """
    ops_payload = _read_json("operators.json")
    types_payload = _read_json("types.json")
    effects_payload = _read_json("effects.json")
    alts_payload = _read_json("alternative_paths.json")

    schema_tag = _expect_str(ops_payload, "schema_tag", "operators.json")
    for name, payload in (
        ("types.json", types_payload),
        ("effects.json", effects_payload),
        ("alternative_paths.json", alts_payload),
    ):
        other = _expect_str(payload, "schema_tag", name)
        if other != schema_tag:
            msg = f"{name}: schema_tag mismatch ({other!r} != {schema_tag!r})"
            raise HypergraphLoadError(msg)

    blender_version = _expect_str(ops_payload, "blender_version", "operators.json")

    ops_raw = ops_payload.get("operators", [])
    if not isinstance(ops_raw, list):
        msg = "operators.json: 'operators' must be a list"
        raise HypergraphLoadError(msg)
    operators = tuple(_build_operator(op) for op in ops_raw if isinstance(op, dict))

    types_raw = types_payload.get("types", [])
    if not isinstance(types_raw, list):
        msg = "types.json: 'types' must be a list"
        raise HypergraphLoadError(msg)
    types = tuple(_build_type(t) for t in types_raw if isinstance(t, dict))

    effects = _build_effects(effects_payload)
    alternatives = _build_alternatives(alts_payload)

    # Cross-artifact integrity: every annotated operator must exist in v1
    # operator set; every alternative path likewise.
    op_idnames = {op.idname for op in operators}
    stray_effects = set(effects) - op_idnames
    if stray_effects:
        msg = f"effects.json references unknown operators: {sorted(stray_effects)}"
        raise HypergraphLoadError(msg)
    stray_alts = set(alternatives) - op_idnames
    if stray_alts:
        msg = f"alternative_paths.json references unknown operators: {sorted(stray_alts)}"
        raise HypergraphLoadError(msg)

    return BpyHypergraph(
        schema_tag=schema_tag,
        blender_version=blender_version,
        operators=operators,
        types=types,
        _operators_by_idname={op.idname: op for op in operators},
        _types_by_name={t.name: t for t in types},
        _effects=effects,
        _alternatives=alternatives,
    )
