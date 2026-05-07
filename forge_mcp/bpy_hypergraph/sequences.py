"""Curated v1 macro sequences for the realizer engine.

Architecture §5.7: the realizer walks hand-authored, content-addressed
step lists ("curated sequences") instead of running a general planner.
Each v1 macro (``reset_scene``, ``create_terrain_from_heightmap``,
``apply_terrain_material``, ``carve_stream``, ``set_camera_overview``,
``add_basic_lighting``, ``render_preview``, ``save_blend``, and the
composite ``realize_region``) is one entry in
``data/curated_sequences.json``.

This module owns the Pydantic schema, the loader, and the integrity
checks that lock the v1 surface:

* every ``call`` is one of the known adapter methods, a ``bpy.ops``
  idname present in ``operators.json``, a ``bpy.data.<coll>.{new,remove}``
  collection method, or a ``seq:<name>`` reference to another sequence
  in the same bundle;
* the ``sequence_id`` of each sequence is the ``blake2b`` of its
  canonical JSON form (10-byte digest, hex-encoded). Locking these in
  CI catches accidental edits to the v1 macro surface.
"""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field

from forge_mcp.realize.rpc import RpcMethods

if TYPE_CHECKING:
    from collections.abc import Mapping
    from importlib.resources.abc import Traversable

    from forge_mcp.bpy_hypergraph.query import BpyHypergraph

# Recursive JSON value type. Mirrors realize.rpc.JsonValue but kept
# local so the hypergraph package stays free of a hard import edge on
# the realize runtime.
type JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]

_SEQ_PREFIX: Final[str] = "seq:"
_BPY_OPS_PREFIX: Final[str] = "bpy.ops."
_BPY_DATA_PREFIX: Final[str] = "bpy.data."
_BPY_DATA_NEW_SUFFIX: Final[str] = ".new"
_BPY_DATA_REMOVE_SUFFIX: Final[str] = ".remove"
_SEQUENCE_ID_BYTES: Final[int] = 10
_FIXED_ADAPTER_METHODS: Final[frozenset[str]] = frozenset(
    {
        RpcMethods.PING,
        RpcMethods.SHUTDOWN,
        RpcMethods.SET_PROPERTY,
        RpcMethods.GET_PROPERTY,
        RpcMethods.SET_IDPROP,
        RpcMethods.GET_IDPROP,
        RpcMethods.MESH_FROM_PYDATA,
        RpcMethods.MESH_ADD_DISPLACE,
        RpcMethods.IMAGE_FROM_FILE,
        RpcMethods.RENDER_TO_FILE,
        RpcMethods.RENDER_SET_ENGINE_DEVICE,
        RpcMethods.MATERIAL_BUILD_COMPOSITE,
        RpcMethods.OBJECT_FROM_DATA,
        RpcMethods.SCENE_ASSIGN_WORLD,
        RpcMethods.SCENE_DIFF,
    },
)


class CuratedSequenceError(RuntimeError):
    """Raised when ``curated_sequences.json`` is structurally invalid."""


class SequenceStep(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """One step in a curated sequence.

    ``call`` is the JSON-RPC method to invoke (or a ``seq:<name>``
    reference). ``params`` may use ``${name}`` placeholders that the
    engine substitutes from the macro inputs at execution time.
    ``expects`` is an opaque postcondition payload the engine
    interprets (typically ``{"scene_diff": {...}}``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    call: str = Field(..., min_length=1)
    params: JsonObject = Field(default_factory=dict)
    expects: JsonObject | None = None


class CuratedSequence(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """One curated, hand-authored macro sequence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1)
    description: str = ""
    inputs_schema: dict[str, str] = Field(default_factory=dict)
    outputs_schema: dict[str, str] = Field(default_factory=dict)
    steps: tuple[SequenceStep, ...]

    def canonical_json(self) -> str:
        """Return the canonical JSON form used for ``sequence_id``."""
        payload = self.model_dump(mode="json")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def sequence_id(self) -> str:
        """Return the content hash of this sequence (hex, 20 chars)."""
        return hashlib.blake2b(
            self.canonical_json().encode("utf-8"),
            digest_size=_SEQUENCE_ID_BYTES,
        ).hexdigest()


class CuratedSequenceBundle(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """The top-level payload of ``data/curated_sequences.json``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_tag: str
    blender_version: str
    sequences: tuple[CuratedSequence, ...]

    def get(self, name: str) -> CuratedSequence:
        """Return the sequence with ``name`` or raise ``KeyError``."""
        for seq in self.sequences:
            if seq.name == name:
                return seq
        msg = f"no curated sequence named {name!r}"
        raise KeyError(msg)

    def names(self) -> tuple[str, ...]:
        """Return all sequence names in declaration order."""
        return tuple(seq.name for seq in self.sequences)


_DATA_PACKAGE: Final[str] = "forge_mcp.bpy_hypergraph.data"
_BUNDLE_FILENAME: Final[str] = "curated_sequences.json"


def _is_known_call(call: str, *, op_idnames: frozenset[str], seq_names: frozenset[str]) -> bool:
    if call in _FIXED_ADAPTER_METHODS:
        return True
    if call.startswith(_BPY_OPS_PREFIX):
        return call in op_idnames
    if call.startswith(_BPY_DATA_PREFIX) and call.endswith(
        (_BPY_DATA_NEW_SUFFIX, _BPY_DATA_REMOVE_SUFFIX),
    ):
        return True
    if call.startswith(_SEQ_PREFIX):
        return call.removeprefix(_SEQ_PREFIX) in seq_names
    return False


def _validate_against_hypergraph(bundle: CuratedSequenceBundle, hypergraph: BpyHypergraph) -> None:
    op_idnames = frozenset(hypergraph.list_operators())
    seq_names = frozenset(bundle.names())
    for seq in bundle.sequences:
        for index, step in enumerate(seq.steps):
            if not _is_known_call(step.call, op_idnames=op_idnames, seq_names=seq_names):
                msg = (
                    f"sequence {seq.name!r} step {index}: unknown call {step.call!r}"
                    f" — not an adapter method, bpy.ops idname, bpy.data collection op,"
                    f" or seq:<known> reference"
                )
                raise CuratedSequenceError(msg)
    if bundle.blender_version != hypergraph.blender_version:
        msg = (
            f"curated_sequences.json blender_version {bundle.blender_version!r} does not"
            f" match operators.json {hypergraph.blender_version!r}"
        )
        raise CuratedSequenceError(msg)


def _read_bundle_text() -> str:
    res: Traversable = resources.files(_DATA_PACKAGE).joinpath(_BUNDLE_FILENAME)
    return res.read_text(encoding="utf-8")


def load_curated_sequences(*, hypergraph: BpyHypergraph | None = None) -> CuratedSequenceBundle:
    """Load and validate the v1 curated-sequence bundle.

    If ``hypergraph`` is provided, every step's ``call`` is checked
    against the operator registry plus the known adapter methods and
    cross-sequence references.
    """
    text = _read_bundle_text()
    payload: Mapping[str, object] = json.loads(text)
    try:
        bundle = CuratedSequenceBundle.model_validate(payload)
    except Exception as exc:
        msg = f"curated_sequences.json: {exc}"
        raise CuratedSequenceError(msg) from exc
    if hypergraph is not None:
        _validate_against_hypergraph(bundle, hypergraph)
    return bundle
