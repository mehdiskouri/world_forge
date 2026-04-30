"""MCP server scaffold using FastMCP from the official Anthropic SDK.

Phase 1 only validates that an MCP-aware host can connect, list tools,
and invoke each one over stdio. The real Forge tools land in Phases 2-5.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from mcp.server.fastmcp import FastMCP

_SERVER_NAME = "forge"


def _forge_version() -> str:
    """Return the installed Forge package version (or ``"0.0.0+local"``).

    The MCP server reports this in ``forge.ping`` so the host can detect
    server upgrades or downgrades across reconnects.
    """
    try:
        return version("forge")
    except PackageNotFoundError:
        return "0.0.0+local"


def forge_ping() -> dict[str, object]:
    """Return liveness information for the Forge server."""
    return {"alive": True, "version": _forge_version()}


def forge_echo(text: str) -> dict[str, object]:
    """Echo ``text`` unchanged. Validates JSON-RPC frame round-trip."""
    return {"echoed": text}


def forge_get_descriptor_schema() -> dict[str, object]:
    """Return the descriptor JSON Schema as a dict.

    Imported lazily so this module stays importable on workers that
    don't depend on the descriptor subpackage (currently a sibling
    branch — the import will succeed once the descriptor branch is
    merged).
    """
    try:
        from forge_mcp.descriptor import descriptor_json_schema  # type: ignore[attr-defined,import-not-found,unused-ignore]  # noqa: I001, PLC0415  # sibling branch may or may not be merged
    except ImportError:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "x-status": "descriptor module not yet available on this branch",
        }
    return dict(descriptor_json_schema())


def build_server() -> FastMCP:  # type: ignore[explicit-any]  # FastMCP's session generics default to Any
    """Construct the MCP server with the v1 tool surface registered.

    Kept as a separate function so tests can introspect the server
    without spinning up a stdio transport.
    """
    server: FastMCP = FastMCP(_SERVER_NAME)  # type: ignore[explicit-any]  # see build_server

    server.tool(
        name="forge.ping",
        title="Ping the Forge server",
        description="Liveness check. Returns alive=True and the server version.",
    )(forge_ping)
    server.tool(
        name="forge.echo",
        title="Echo a string",
        description="Round-trip a string for transport debugging.",
    )(forge_echo)
    server.tool(
        name="forge.get_descriptor_schema",
        title="Get the StructuredDescriptor JSON schema",
        description=(
            "Return the JSON Schema for the Forge structured descriptor. "
            "Agents use this to know exactly what a valid descriptor looks "
            "like before calling later realization tools."
        ),
    )(forge_get_descriptor_schema)

    return server


def main() -> None:
    """Entry point for ``forge-mcp`` (declared in ``[project.scripts]``).

    Runs the server on the stdio transport. The MCP SDK's stdio runner
    coordinates the asyncio loop; we just delegate.
    """
    build_server().run()
