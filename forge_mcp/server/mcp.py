"""MCP server scaffold using FastMCP from the official Anthropic SDK.

Phase 1 only validated transport. Phase 2 wires the v1 tool surface
documented in ``AGENT/dev_phases/phase2.md`` Stage G: project lifecycle,
region CRUD, schema export, hypergraph + boundary inspection, history,
and lock listing. Generation / realization / audit tools land in
Phases 3-5 and are intentionally not registered here yet.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from mcp.server.fastmcp import FastMCP

from forge_mcp.server.tools import generation as generation_tools
from forge_mcp.server.tools import history as history_tools
from forge_mcp.server.tools import hypergraph as hypergraph_tools
from forge_mcp.server.tools import inspection as inspection_tools
from forge_mcp.server.tools import projects as project_tools
from forge_mcp.server.tools import regions as region_tools
from forge_mcp.server.tools import schema as schema_tools

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
    """Return the descriptor JSON Schema as a tool envelope.

    Phase-2 rewire: calls :func:`forge_mcp.descriptor.descriptor_json_schema`
    directly. The Phase-1 try/except fallback only existed because the
    descriptor module lived on a sibling branch at the time.
    """
    return schema_tools.get_descriptor_schema()


def build_server() -> FastMCP:  # type: ignore[explicit-any]  # FastMCP's session generics default to Any
    """Construct the MCP server with the v1 tool surface registered.

    Kept as a separate function so tests can introspect the server
    without spinning up a stdio transport.
    """
    server: FastMCP = FastMCP(_SERVER_NAME)  # type: ignore[explicit-any]  # see build_server

    # --- Transport / introspection (Phase 1) ------------------------------
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

    # --- Project lifecycle (Phase 2 Stage G) ------------------------------
    server.tool(
        name="forge.create_project",
        title="Create a new Forge project",
        description="Materialize a fresh project tree at `path` and load it.",
    )(project_tools.create_project)
    server.tool(
        name="forge.open_project",
        title="Open an existing Forge project",
        description="Load the project at `path` into memory.",
    )(project_tools.open_project)
    server.tool(
        name="forge.save_project",
        title="Save the open project",
        description="Flush all in-memory state to disk.",
    )(project_tools.save_project)
    server.tool(
        name="forge.close_project",
        title="Close the open project",
        description="Flush and drop the open project from memory.",
    )(project_tools.close_project)

    # --- Region CRUD ------------------------------------------------------
    server.tool(
        name="forge.create_region",
        title="Create a region",
        description=(
            "Validate, persist, and adjacency-stub a new region. "
            "Returns a structured error on overlap / invalid polygon."
        ),
    )(region_tools.create_region)
    server.tool(
        name="forge.update_region",
        title="Update a region",
        description="Apply a partial update; re-runs adjacency on polygon change.",
    )(region_tools.update_region)
    server.tool(
        name="forge.delete_region",
        title="Delete a region",
        description="Remove a region and any boundaries it participates in.",
    )(region_tools.delete_region)
    server.tool(
        name="forge.list_regions",
        title="List regions",
        description="Return a deterministic summary list of every region.",
    )(region_tools.list_regions)
    server.tool(
        name="forge.get_region",
        title="Get one region",
        description="Return the full record for `region_id`.",
    )(region_tools.get_region)

    # --- Hypergraph / boundaries ------------------------------------------
    server.tool(
        name="forge.query_layer",
        title="Query a hypergraph layer",
        description="BFS over `layer` from `root_node` (optional) up to `depth`.",
    )(hypergraph_tools.query_layer)
    server.tool(
        name="forge.list_boundaries",
        title="List boundaries",
        description="Return every boundary record in deterministic order.",
    )(hypergraph_tools.list_boundaries)
    server.tool(
        name="forge.inspect_boundary",
        title="Inspect a boundary",
        description="Return one boundary record by id.",
    )(hypergraph_tools.inspect_boundary)

    # --- History ----------------------------------------------------------
    server.tool(
        name="forge.history",
        title="Read the history log",
        description="Return events oldest-first, optionally capped by `limit`.",
    )(history_tools.history)
    server.tool(
        name="forge.undo",
        title="Undo the last event",
        description="Phase-7 surface; in Phase 2 returns a `not_implemented` error.",
    )(history_tools.undo)

    # --- Locks (read-only in Phase 2) -------------------------------------
    server.tool(
        name="forge.list_locks",
        title="List locks",
        description="Return locks, optionally filtered by `region_id`.",
    )(inspection_tools.list_locks)

    # --- Generation (Phase 3) ---------------------------------------------
    server.tool(
        name="forge.generate_region",
        title="Generate a region",
        description=(
            "Compile the region's descriptor into a spec, run the terrain "
            "pipeline, persist the heightmap + analysis, and return both."
        ),
    )(generation_tools.generate_region)
    server.tool(
        name="forge.reroll_seed",
        title="Reroll a region seed",
        description=(
            "Replace `region.seed` with the supplied value, or with a "
            "deterministic blake2b derivation when omitted."
        ),
    )(generation_tools.reroll_seed)
    server.tool(
        name="forge.analyze_region",
        title="Re-analyze a generated region",
        description="Recompute the structured analysis from the persisted heightmap.",
    )(generation_tools.analyze_region)
    server.tool(
        name="forge.inspect_spec",
        title="Inspect a persisted spec",
        description="Return one SpecRecord by `spec_id` or by `region_id` indirection.",
    )(generation_tools.inspect_spec)
    server.tool(
        name="forge.render_view",
        title="Render a region view",
        description=(
            "Re-run the v1 realize_region macro on a previously-generated region "
            "and render a `preview` (512x384), `default` (1024x768), or `full` "
            "(2048x1536) PNG. Persists `.blend`, preview, and trace under "
            "`realizations/blender/`."
        ),
    )(generation_tools.render_view)

    return server


def main() -> None:
    """Entry point for ``forge-mcp`` (declared in ``[project.scripts]``).

    Runs the server on the stdio transport. The MCP SDK's stdio runner
    coordinates the asyncio loop; we just delegate.
    """
    build_server().run()
