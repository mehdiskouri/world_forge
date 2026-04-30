"""``forge.get_descriptor_schema`` — Phase-2 rewire to the real schema source."""

from __future__ import annotations

from forge_mcp.descriptor import descriptor_json_schema
from forge_mcp.server.tools._responses import ok


def get_descriptor_schema() -> dict[str, object]:
    """Return the StructuredDescriptor JSON Schema."""
    return ok(dict(descriptor_json_schema()))


__all__ = ["get_descriptor_schema"]
