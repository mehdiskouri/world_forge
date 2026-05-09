"""MCP server scaffold using FastMCP from the official Anthropic SDK.

Phase 1 only validated transport. Phase 2 wires the v1 tool surface
documented in ``AGENT/dev_phases/phase2.md`` Stage G: project lifecycle,
region CRUD, schema export, hypergraph + boundary inspection, history,
and lock listing. Generation / realization / audit tools land in
Phases 3-5 and are intentionally not registered here yet.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from forge_mcp.realize import (
    BLENDER_BIN_ENV,
    BlenderNotConfiguredError,
    BlenderProcess,
    blender_binary,
)
from forge_mcp.server.tools import audit as audit_tools
from forge_mcp.server.tools import canvas as canvas_tools
from forge_mcp.server.tools import cleanup as cleanup_tools
from forge_mcp.server.tools import environments as environment_tools
from forge_mcp.server.tools import generation as generation_tools
from forge_mcp.server.tools import history as history_tools
from forge_mcp.server.tools import hypergraph as hypergraph_tools
from forge_mcp.server.tools import inspection as inspection_tools
from forge_mcp.server.tools import locks as lock_tools
from forge_mcp.server.tools import materials as material_tools
from forge_mcp.server.tools import projects as project_tools
from forge_mcp.server.tools import regions as region_tools
from forge_mcp.server.tools import schema as schema_tools
from forge_mcp.server.tools import set_realizer_factory
from forge_mcp.server.tools import skills as skills_tools
from forge_mcp.server.tools import sub_regions as sub_region_tools

if TYPE_CHECKING:
    from collections.abc import Iterator

    from forge_mcp.realize.engine import RealizerEngine

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


def build_server() -> FastMCP:  # type: ignore[explicit-any]  # FastMCP's session generics default to Any  # noqa: PLR0915 - one server() registration per tool
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
        description=(
            "Pop the most recent snapshot off the bounded undo ring "
            "(50 deep, mirrored under `<project>/.undo`) and restore "
            "the prior in-memory + on-disk state. Heightmaps and "
            "Blender realizations are not snapshotted; rerun "
            "`forge.generate_region` if a generation needs to be "
            "reproduced. Returns `cannot_undo` when the ring is at "
            "its baseline floor."
        ),
    )(history_tools.undo)

    # --- Locks (read-only in Phase 2) -------------------------------------
    server.tool(
        name="forge.list_locks",
        title="List locks",
        description="Return locks, optionally filtered by `region_id`.",
    )(inspection_tools.list_locks)

    # --- Lock mutators (Phase 7 Stage A) ----------------------------------
    server.tool(
        name="forge.lock_property",
        title="Pin a region property",
        description=(
            "Create a property lock that pins the value at `json_path` "
            "(dotted path on the region JSON) to its current value. "
            "Phase 7 Stage B will refuse mutations that would change it."
        ),
    )(lock_tools.lock_property)
    server.tool(
        name="forge.lock_feature",
        title="Capture a heightmap feature",
        description=(
            "Capture the current heightmap patch covering `bbox_world` "
            "(`[x0, y0, x1, y1]` in metres). Phase 7 Stage C blends the "
            "patch back during regeneration so the feature survives "
            "seed rerolls."
        ),
    )(lock_tools.lock_feature)
    server.tool(
        name="forge.lock_region",
        title="Lock a whole region",
        description=(
            "Skip regeneration entirely for this region. `generate_region` "
            "will short-circuit after spec compile and emit a "
            "`region_lock_skipped` history event."
        ),
    )(lock_tools.lock_region)
    server.tool(
        name="forge.unlock",
        title="Remove a lock",
        description="Remove the lock identified by `lock_id`.",
    )(lock_tools.unlock)

    # --- Generation (Phase 3) ---------------------------------------------
    server.tool(
        name="forge.generate_region",
        title="Generate a region",
        description=(
            "Compile the region's descriptor into a spec, run the terrain "
            "pipeline, persist the heightmap + analysis, and return both. "
            "Optional `render_options` overrides the per-tier render "
            "defaults (engine, device, width/height, cycles_samples, "
            "png_max_bytes); see `forge.list_render_devices` for the "
            "host's device menu."
        ),
    )(generation_tools.generate_region)
    server.tool(
        name="forge.reroll_seed",
        title="Reroll a region seed",
        description=(
            "Replace `region.seed` with the supplied value, or with a "
            "deterministic blake2b derivation when omitted. Pass "
            "`regenerate=True` to immediately re-run `forge.generate_region` "
            "with the new seed; the Stage C lock-aware path is reused so "
            "feature locks blend back, region locks short-circuit, and "
            "property locks raise `lock_violation` on drift."
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
            "`realizations/blender/`. Optional `render_options` accepts the "
            "same knobs as `forge.generate_region`."
        ),
    )(generation_tools.render_view)
    server.tool(
        name="forge.list_render_devices",
        title="List Cycles render devices",
        description=(
            "Return the host's available Cycles compute backends "
            "(OPTIX/CUDA/HIP/METAL/CPU) and the auto-pick default. "
            "Pass `force_refresh=True` to re-issue the device probe."
        ),
    )(generation_tools.list_render_devices)

    # --- Skills surface (Phase 5 Stage A) ---------------------------------
    server.tool(
        name="forge.list_skills",
        title="List shipped Forge skills",
        description=(
            "Return a summary of every SKILL.md shipped under "
            "forge_mcp/skills/. Used by clients that don't scan a local "
            "skill directory (Cursor, Copilot)."
        ),
    )(skills_tools.list_skills)
    server.tool(
        name="forge.get_skill",
        title="Get one shipped Forge skill",
        description="Return the parsed frontmatter and raw body markdown for one skill.",
    )(skills_tools.get_skill)

    # --- Audit surface (Phase 5 Stage D) ----------------------------------
    server.tool(
        name="forge.record_audit",
        title="Record an audit verdict",
        description=(
            "Validate, persist, and history-log one AuditVerdict produced "
            "by the audit subagent. Verdict body must match the schema "
            "returned by `forge.get_audit_schema`."
        ),
    )(audit_tools.record_audit)
    server.tool(
        name="forge.list_audits",
        title="List recorded audits",
        description="Return audit summaries, optionally filtered by `region_id`.",
    )(audit_tools.list_audits)
    server.tool(
        name="forge.get_audit",
        title="Get one audit verdict",
        description="Return the full AuditVerdict body for `audit_id`.",
    )(audit_tools.get_audit)
    server.tool(
        name="forge.get_audit_schema",
        title="Get the AuditVerdict JSON Schema",
        description=(
            "Return the JSON Schema for AuditVerdict. The audit subagent "
            "uses it to construct a valid verdict before calling "
            "`forge.record_audit`."
        ),
    )(audit_tools.get_audit_schema)

    # --- Materials (Phase 6-bis Phase C) ----------------------------------
    server.tool(
        name="forge.create_material_archetype",
        title="Create a material archetype",
        description=(
            "Persist a new material archetype node (recipe + parameters). "
            "Returns a structured error on invalid recipe / parameters."
        ),
    )(material_tools.create_material_archetype)
    server.tool(
        name="forge.update_material_archetype",
        title="Update a material archetype",
        description="Apply a partial update to an existing archetype.",
    )(material_tools.update_material_archetype)
    server.tool(
        name="forge.delete_material_archetype",
        title="Delete a material archetype",
        description="Refuses if any material edge still references the archetype.",
    )(material_tools.delete_material_archetype)
    server.tool(
        name="forge.list_material_archetypes",
        title="List material archetypes",
        description="Return a deterministic summary of every archetype.",
    )(material_tools.list_material_archetypes)
    server.tool(
        name="forge.get_material_archetype",
        title="Get one material archetype",
        description="Return the full archetype record for `archetype_id`.",
    )(material_tools.get_material_archetype)
    server.tool(
        name="forge.apply_material",
        title="Apply a material to a target",
        description=(
            "Add a `material_application` edge from `archetype_id` to "
            "`target_node_id` (region or world root). `attrs` selects "
            "scope/priority/mask/parameter_overrides."
        ),
    )(material_tools.apply_material)
    server.tool(
        name="forge.unapply_material",
        title="Remove a material application",
        description="Delete the named `material_application` edge.",
    )(material_tools.unapply_material)
    server.tool(
        name="forge.list_material_applications",
        title="List material applications",
        description="Return every `material_application` edge in deterministic order.",
    )(material_tools.list_material_applications)
    server.tool(
        name="forge.compose_material",
        title="Compose two material archetypes",
        description=(
            "Add a directed `material_composition` edge "
            "`parent_archetype_id -> child_archetype_id` (extends/composes). "
            "Refuses cycles."
        ),
    )(material_tools.compose_material)
    server.tool(
        name="forge.uncompose_material",
        title="Remove a material composition",
        description="Delete the named `material_composition` edge.",
    )(material_tools.uncompose_material)
    server.tool(
        name="forge.resolve_material",
        title="Resolve the composite material plan for a region",
        description=(
            "Walk spatial-containment ancestors, expand archetypes through "
            "EXTENDS/COMPOSES, and return the deterministic CompositeMaterialPlan "
            "(falls back to the default terrain archetype when no application "
            "targets the region)."
        ),
    )(material_tools.resolve_material)

    # --- Environments (Phase 6-f Stage F) ---------------------------------
    server.tool(
        name="forge.create_environment",
        title="Create an environment archetype",
        description=(
            "Persist a new environment node (recipe + parameters: sun, "
            "sky, fog, ambient, time-of-day, season, latitude/longitude). "
            "The node is unbound on creation; call `forge.bind_environment` "
            "to attach it to the world root or a region."
        ),
    )(environment_tools.create_environment)
    server.tool(
        name="forge.update_environment",
        title="Update an environment",
        description="Apply a partial update to an existing environment node.",
    )(environment_tools.update_environment)
    server.tool(
        name="forge.delete_environment",
        title="Delete an environment",
        description="Refuses if any scope (world root or region) still binds the environment.",
    )(environment_tools.delete_environment)
    server.tool(
        name="forge.list_environments",
        title="List environments",
        description="Return a deterministic summary of every environment node.",
    )(environment_tools.list_environments)
    server.tool(
        name="forge.get_environment",
        title="Get one environment",
        description="Return the full environment record for `environment_id`.",
    )(environment_tools.get_environment)
    server.tool(
        name="forge.bind_environment",
        title="Bind an environment to a scope",
        description=(
            "Bind `environment_id` to `scope_node_id` (world root or a "
            "region). Replaces any existing binding on the scope."
        ),
    )(environment_tools.bind_environment)
    server.tool(
        name="forge.unbind_environment",
        title="Clear an environment binding",
        description="No-op when the scope already has no binding.",
    )(environment_tools.unbind_environment)
    server.tool(
        name="forge.resolve_environment",
        title="Resolve the environment plan for a region",
        description=(
            "Walk region -> world root -> hard-coded default and return "
            "the flat ResolvedEnvironment payload (with computed sun "
            "position) that the adapter consumes."
        ),
    )(environment_tools.resolve_environment_tool)

    # --- Sub-regions (Phase 6-c Phase E) ----------------------------------
    server.tool(
        name="forge.create_sub_region",
        title="Create a predicate-typed sub-region",
        description=(
            "Create a `sub_region` node under an existing region. The "
            "predicate (height_band / slope / aspect / distance_to_stream) "
            "is the sub-region's extent — it is evaluated lazily at "
            "realize time against the parent's persisted heightmap, so "
            "re-rolling the parent updates coverage on next "
            "`forge.generate_region`."
        ),
    )(sub_region_tools.create_sub_region)
    server.tool(
        name="forge.update_sub_region",
        title="Update a sub-region",
        description="Apply a partial update to an existing sub-region.",
    )(sub_region_tools.update_sub_region)
    server.tool(
        name="forge.delete_sub_region",
        title="Delete a sub-region",
        description=(
            "Refuses if any `material_application` edge still targets "
            "the sub-region; remove those applications first."
        ),
    )(sub_region_tools.delete_sub_region)
    server.tool(
        name="forge.list_sub_regions",
        title="List sub-regions",
        description=(
            "Return a deterministic list of sub-region summaries; filterable by `parent_region_id`."
        ),
    )(sub_region_tools.list_sub_regions)
    server.tool(
        name="forge.get_sub_region",
        title="Get one sub-region",
        description="Return the full sub-region record for `sub_region_id`.",
    )(sub_region_tools.get_sub_region)
    server.tool(
        name="forge.preview_sub_region_coverage",
        title="Preview sub-region predicate coverage",
        description=(
            "Evaluate the sub-region's predicate against the parent "
            "region's persisted heightmap and return vertex_count, "
            "total_vertices, coverage_fraction, and bbox_uv. Read-only; "
            "no Blender required."
        ),
    )(sub_region_tools.preview_sub_region_coverage)

    # --- Canvas (Phase 6 Stage D) -----------------------------------------
    server.tool(
        name="forge.canvas_url",
        title="Get the popup-canvas URL",
        description=(
            "Lazily start the embedded canvas HTTP/WebSocket server on "
            "127.0.0.1 and return its URL. Subsequent calls reuse the "
            "same server. Requires an open project."
        ),
    )(canvas_tools.canvas_url)
    server.tool(
        name="forge.canvas_status",
        title="Get the popup-canvas status",
        description=(
            "Return whether the canvas server is running, its URL, and "
            "the count of connected WebSocket clients."
        ),
    )(canvas_tools.canvas_status)

    # --- Cleanup (Phase 7 Stage G) ----------------------------------------
    server.tool(
        name="forge.find_orphans",
        title="Find orphan artefacts",
        description=(
            "Return orphan spec files (no region references them), "
            "material_application edges with missing archetype "
            "endpoints, and regions whose `environment_id` no longer "
            "resolves. Read-only."
        ),
    )(cleanup_tools.find_orphans)
    server.tool(
        name="forge.find_stale_realizations",
        title="Find stale .blend files",
        description=(
            "Return `realizations/blender/<region>.blend` files whose "
            "source spec JSON has a newer mtime, plus blends whose "
            "region or spec has been deleted. Read-only."
        ),
    )(cleanup_tools.find_stale_realizations)
    server.tool(
        name="forge.find_lock_conflicts",
        title="Find lock conflicts",
        description=(
            "Return locks whose target region was deleted, plus "
            "property locks whose `expected_value` no longer matches "
            "the live region JSON. Read-only."
        ),
    )(cleanup_tools.find_lock_conflicts)
    server.tool(
        name="forge.purge_orphans",
        title="Delete orphan spec files",
        description=(
            "Delete the orphan spec files reported by "
            "`forge.find_orphans`. Defaults to `dry_run=True`; pass "
            "`dry_run=False` explicitly to actually delete."
        ),
    )(cleanup_tools.purge_orphans)

    return server


def _install_default_realizer_factory() -> bool:
    """Wire the Blender realizer factory if ``$FORGE_BLENDER_BIN`` is valid.

    Returns ``True`` when a factory was installed, ``False`` when the
    environment is not configured for a real Blender binary (in which
    case the realization-aware tools return a structured
    ``realizer_not_configured`` envelope instead of crashing the
    server).

    The factory itself is lazy: each ``with factory()`` call spawns a
    fresh ``BlenderProcess`` and yields a ``RealizerEngine`` bound to
    its RPC client. We import :class:`RealizerEngine` inside the
    closure to keep startup cheap and to avoid an import cycle through
    ``forge_mcp.realize``.
    """
    try:
        binary = blender_binary()
    except BlenderNotConfiguredError as exc:
        sys.stderr.write(
            f"forge: realizer disabled — {exc}; "
            f"set ${BLENDER_BIN_ENV} to a Blender 5.0 binary to enable "
            f"forge.generate_region / forge.render_view\n",
        )
        return False

    @contextmanager
    def factory() -> Iterator[RealizerEngine]:
        from forge_mcp.realize.engine import RealizerEngine  # noqa: PLC0415 - break import cycle

        with BlenderProcess(blender=binary) as proc:
            yield RealizerEngine(proc.client)

    set_realizer_factory(factory)
    return True


def main() -> None:
    """Entry point for ``forge-mcp`` (declared in ``[project.scripts]``).

    Runs the server on the stdio transport. The MCP SDK's stdio runner
    coordinates the asyncio loop; we just delegate. Before handing off
    we attempt to install the default Blender realizer factory so that
    Phase 4 tools (``forge.generate_region``, ``forge.render_view``)
    can actually drive Blender when ``$FORGE_BLENDER_BIN`` is set.
    """
    _install_default_realizer_factory()
    build_server().run()
