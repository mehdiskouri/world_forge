"""Integration check for the realizer's version-pin contract.

Phase 4 verification §7: a curated hypergraph whose ``blender_version``
does not match the running Blender must be refused immediately, before
any macro runs, with :class:`BlenderVersionMismatchError`.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from forge_mcp.bpy_hypergraph import load_hypergraph
from forge_mcp.bpy_hypergraph.sequences import load_curated_sequences
from forge_mcp.realize import BlenderProcess
from forge_mcp.realize.engine import BlenderVersionMismatchError, RealizerEngine


@pytest.mark.blender_integration
def test_realizer_refuses_mismatched_blender_version() -> None:
    real_hg = load_hypergraph()
    real_bundle = load_curated_sequences(hypergraph=real_hg)
    mismatched_hg = replace(real_hg, blender_version="999.0.0")
    mismatched_bundle = real_bundle.model_copy(update={"blender_version": "999.0.0"})
    with BlenderProcess() as proc, pytest.raises(BlenderVersionMismatchError):
        RealizerEngine(proc.client, hypergraph=mismatched_hg, bundle=mismatched_bundle)
