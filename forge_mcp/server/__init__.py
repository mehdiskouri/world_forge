"""Forge MCP server (Phase 1 spike 3).

The server exposes Forge's capabilities to MCP-compatible AI agent
hosts (Claude Desktop / Claude Code / Cursor) over stdio. Phase 1 is a
scaffold proving the wire is up; richer tools land in later phases:

- Phase 2: project I/O (`forge.project.create`, `forge.project.open`)
- Phase 3-4: descriptor → realization tools (`forge.realize_region`)
- Phase 5: free-text → descriptor skill (`forge.plan`)

Phase 1 surface (proves transport, schema discovery, and round-trip):

* ``forge.ping``                  — `{ "alive": True, "version": "<str>" }`
* ``forge.echo(text: str)``       — `{ "echoed": text }` (round-trip)
* ``forge.get_descriptor_schema`` — JSON Schema for ``StructuredDescriptor``
"""

from forge_mcp.server.mcp import build_server, main

__all__ = ["build_server", "main"]
