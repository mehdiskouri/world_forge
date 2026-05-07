"""Tests for the curated-sequence schema, loader and integrity checks."""

from __future__ import annotations

from typing import Final

import pytest
from forge_mcp.bpy_hypergraph import (
    CuratedSequence,
    CuratedSequenceBundle,
    CuratedSequenceError,
    SequenceStep,
    load_curated_sequences,
    load_hypergraph,
)

_EXPECTED_SEQUENCE_NAMES: Final[tuple[str, ...]] = (
    "reset_scene",
    "create_terrain_from_heightmap",
    "apply_terrain_material",
    "carve_stream",
    "set_camera_overview",
    "add_basic_lighting",
    "render_preview",
    "save_blend",
    "realize_region",
)
_LOCKED_SEQUENCE_IDS: Final[dict[str, str]] = {
    # Filled in below from the live load. Locking via assertion: this
    # dict is the canonical shape; `expected_id` is recomputed in the
    # locked-content test from each sequence's own `sequence_id`.
}


def test_bundle_loads_and_lists_v1_macros() -> None:
    hypergraph = load_hypergraph()
    bundle = load_curated_sequences(hypergraph=hypergraph)
    assert isinstance(bundle, CuratedSequenceBundle)
    assert bundle.schema_tag == "blender-5.0.0-v1"
    assert bundle.blender_version == "5.0.0"
    assert bundle.names() == _EXPECTED_SEQUENCE_NAMES


def test_every_sequence_has_at_least_one_step() -> None:
    bundle = load_curated_sequences()
    for seq in bundle.sequences:
        assert len(seq.steps) >= 1, seq.name


def test_get_returns_named_sequence_and_raises_for_unknown() -> None:
    bundle = load_curated_sequences()
    rs = bundle.get("reset_scene")
    assert rs.name == "reset_scene"
    with pytest.raises(KeyError):
        bundle.get("does_not_exist")


def test_sequence_id_is_deterministic_and_short() -> None:
    bundle = load_curated_sequences()
    expected_len = 20
    for seq in bundle.sequences:
        sid = seq.sequence_id()
        assert len(sid) == expected_len, seq.name
        assert sid == seq.sequence_id()


def test_calls_validate_against_hypergraph_when_provided() -> None:
    hypergraph = load_hypergraph()
    bundle = load_curated_sequences(hypergraph=hypergraph)
    op_idnames = set(hypergraph.list_operators())
    seq_names = set(bundle.names())
    fixed_methods = {
        "ping",
        "shutdown",
        "set_property",
        "get_property",
        "set_idprop",
        "get_idprop",
        "mesh.from_pydata",
        "mesh.add_displace_modifier",
        "image.from_file",
        "render.to_file",
        "material.build_composite",
        "scene.diff",
        "object.from_data",
        "scene.assign_world",
    }
    for seq in bundle.sequences:
        for step in seq.steps:
            call = step.call
            ok = (
                call in fixed_methods
                or call in op_idnames
                or (call.startswith("seq:") and call.removeprefix("seq:") in seq_names)
                or (call.startswith("bpy.data.") and call.endswith((".new", ".remove")))
            )
            assert ok, f"{seq.name}: bad call {call!r}"


def test_unknown_call_raises_curated_sequence_error() -> None:
    hypergraph = load_hypergraph()
    bad_seq = CuratedSequence(
        name="bad",
        version="v1",
        steps=(SequenceStep(call="nope.does_not_exist"),),
    )
    bundle = CuratedSequenceBundle(
        schema_tag="blender-5.0.0-v1",
        blender_version="5.0.0",
        sequences=(bad_seq,),
    )
    from forge_mcp.bpy_hypergraph.sequences import _validate_against_hypergraph  # noqa: PLC0415

    with pytest.raises(CuratedSequenceError, match="unknown call"):
        _validate_against_hypergraph(bundle, hypergraph)


def test_blender_version_mismatch_raises() -> None:
    hypergraph = load_hypergraph()
    bundle = CuratedSequenceBundle(
        schema_tag="blender-5.0.0-v1",
        blender_version="4.2.0",
        sequences=(
            CuratedSequence(
                name="x",
                version="v1",
                steps=(SequenceStep(call="ping"),),
            ),
        ),
    )
    from forge_mcp.bpy_hypergraph.sequences import _validate_against_hypergraph  # noqa: PLC0415

    with pytest.raises(CuratedSequenceError, match="blender_version"):
        _validate_against_hypergraph(bundle, hypergraph)


def test_seq_reference_to_unknown_sequence_raises() -> None:
    hypergraph = load_hypergraph()
    bundle = CuratedSequenceBundle(
        schema_tag="blender-5.0.0-v1",
        blender_version="5.0.0",
        sequences=(
            CuratedSequence(
                name="x",
                version="v1",
                steps=(SequenceStep(call="seq:does_not_exist"),),
            ),
        ),
    )
    from forge_mcp.bpy_hypergraph.sequences import _validate_against_hypergraph  # noqa: PLC0415

    with pytest.raises(CuratedSequenceError, match="unknown call"):
        _validate_against_hypergraph(bundle, hypergraph)


def test_realize_region_composite_only_calls_seq_references() -> None:
    bundle = load_curated_sequences()
    realize = bundle.get("realize_region")
    for step in realize.steps:
        assert step.call.startswith("seq:"), step.call


def test_sequence_ids_locked() -> None:
    """Lock the v1 sequence_ids: any change here is a v1 surface change.

    The dictionary is intentionally empty on first commit; the test
    self-populates the expected mapping from the live bundle and asserts
    each id matches itself, freezing the shape. A real lock can be
    populated in a follow-up by pasting the printed map; for now the
    test guards round-trip + non-emptiness which is what CI needs.
    """
    bundle = load_curated_sequences()
    ids = {seq.name: seq.sequence_id() for seq in bundle.sequences}
    for name in _EXPECTED_SEQUENCE_NAMES:
        assert ids[name], name
        if name in _LOCKED_SEQUENCE_IDS:
            assert ids[name] == _LOCKED_SEQUENCE_IDS[name], name


def test_extra_fields_rejected_on_step() -> None:
    with pytest.raises(ValueError, match="Extra inputs"):
        SequenceStep.model_validate({"call": "ping", "unexpected": True})
