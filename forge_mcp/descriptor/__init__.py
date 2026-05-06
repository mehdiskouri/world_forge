"""Structured descriptor — Forge's typed handoff from agent to Forge.

The agent (using the ``forge.plan`` skill) extracts a :class:`StructuredDescriptor`
from user free text and passes it to Forge tools. Forge contains zero LLM
calls; this module is the boundary that enforces structure.

See :doc:`AGENT/ARCHITECTURE.md` §3.3 for the schema contract and Phase 1
spike 4 (:doc:`docs/spikes/04-descriptor-schema.md`) for the eval set.
"""

from __future__ import annotations

from forge_mcp.descriptor.region_extent import RegionExtent
from forge_mcp.descriptor.schema import (
    SCHEMA_VERSION,
    Hydrology,
    StreamCharacter,
    StructuredDescriptor,
    Terrain,
    TerrainPrimary,
    descriptor_json_schema,
)
from forge_mcp.descriptor.validate import (
    ValidationFailure,
    ValidationIssue,
    validate,
)

__all__ = [
    "SCHEMA_VERSION",
    "Hydrology",
    "RegionExtent",
    "StreamCharacter",
    "StructuredDescriptor",
    "Terrain",
    "TerrainPrimary",
    "ValidationFailure",
    "ValidationIssue",
    "descriptor_json_schema",
    "validate",
]
