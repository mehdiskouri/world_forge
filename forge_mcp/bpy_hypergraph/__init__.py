"""Runtime API for the curated bpy hypergraph artifact.

The hypergraph is a set of four JSON files (operators, types, effects,
alternative paths) generated offline by ``scripts/host/build_hypergraph.py``
from a Blender 5.0.0 introspection dump. This module loads them on
first access and offers small, read-only query helpers to the realizer
(Phase 4) and the MCP server (Phase 6).

The on-disk schema is versioned via the ``schema_tag`` field
(``blender-<version>-v1``); :func:`load_hypergraph` raises if the four
files disagree.
"""

from forge_mcp.bpy_hypergraph.query import (
    AlternativePath,
    BpyHypergraph,
    EffectAnnotation,
    HypergraphLoadError,
    OperatorEntry,
    TypeEntry,
    load_hypergraph,
)

__all__ = [
    "AlternativePath",
    "BpyHypergraph",
    "EffectAnnotation",
    "HypergraphLoadError",
    "OperatorEntry",
    "TypeEntry",
    "load_hypergraph",
]
