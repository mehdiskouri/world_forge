"""In-process tests for the FastMCP server.

The Phase 1 spike validates two surfaces:

1. **Tool registration** — ``build_server()`` returns a configured
   :class:`FastMCP` instance that exposes exactly the v1 tool surface.
   We assert this by listing tools through the public async API.
2. **Tool behaviour** — the underlying handler functions are pure and
   are invoked directly. Driving them through ``FastMCP.call_tool`` is
   exercised separately by the manual stdio smoke test documented in
   ``docs/spikes/03-mcp-server-scaffold.md``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import forge_mcp.server.mcp as server_mcp
from forge_mcp.server import build_server, main
from forge_mcp.server.mcp import (
    _forge_version,
    forge_echo,
    forge_get_descriptor_schema,
    forge_ping,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

EXPECTED_TOOLS: frozenset[str] = frozenset(
    {
        # Transport / introspection
        "forge.ping",
        "forge.echo",
        "forge.get_descriptor_schema",
        # Project lifecycle
        "forge.create_project",
        "forge.open_project",
        "forge.save_project",
        "forge.close_project",
        # Region CRUD
        "forge.create_region",
        "forge.update_region",
        "forge.delete_region",
        "forge.list_regions",
        "forge.get_region",
        # Hypergraph + boundaries
        "forge.query_layer",
        "forge.list_boundaries",
        "forge.inspect_boundary",
        # History
        "forge.history",
        "forge.undo",
        # Locks (read-only in Phase 2)
        "forge.list_locks",
        # Lock mutators (Phase 7 Stage A)
        "forge.lock_property",
        "forge.lock_feature",
        "forge.lock_region",
        "forge.unlock",
        # Generation (Phase 3)
        "forge.generate_region",
        "forge.reroll_seed",
        "forge.analyze_region",
        "forge.inspect_spec",
        # Realization views (Phase 4)
        "forge.render_view",
        # Render device probe (Phase 6-d)
        "forge.list_render_devices",
        # Skills surface (Phase 5)
        "forge.list_skills",
        "forge.get_skill",
        # Audit surface (Phase 5 Stage D)
        "forge.record_audit",
        "forge.list_audits",
        "forge.get_audit",
        "forge.get_audit_schema",
        # Canvas surface (Phase 6 Stage D)
        "forge.canvas_url",
        "forge.canvas_status",
        # Materials surface (Phase 6-bis Phase C)
        "forge.create_material_archetype",
        "forge.update_material_archetype",
        "forge.delete_material_archetype",
        "forge.list_material_archetypes",
        "forge.get_material_archetype",
        "forge.apply_material",
        "forge.unapply_material",
        "forge.list_material_applications",
        "forge.compose_material",
        "forge.uncompose_material",
        "forge.resolve_material",
        # Sub-regions surface (Phase 6-c Phase E)
        "forge.create_sub_region",
        "forge.update_sub_region",
        "forge.delete_sub_region",
        "forge.list_sub_regions",
        "forge.get_sub_region",
        "forge.preview_sub_region_coverage",
        # Environments surface (Phase 6-f Stage F)
        "forge.create_environment",
        "forge.update_environment",
        "forge.delete_environment",
        "forge.list_environments",
        "forge.get_environment",
        "forge.bind_environment",
        "forge.unbind_environment",
        "forge.resolve_environment",
        # Cleanup surface (Phase 7 Stage G)
        "forge.find_orphans",
        "forge.find_stale_realizations",
        "forge.find_lock_conflicts",
        "forge.purge_orphans",
    },
)


def test_build_server_registers_v1_tool_surface() -> None:
    server = build_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS
    assert len(tools) == len(EXPECTED_TOOLS)


def test_build_server_uses_forge_name() -> None:
    server = build_server()
    assert server.name == "forge"


def test_forge_ping_returns_alive_and_version() -> None:
    result = forge_ping()
    assert result["alive"] is True
    version_field = result["version"]
    assert isinstance(version_field, str)
    assert version_field == _forge_version()


def test_forge_echo_round_trips_string() -> None:
    payload = "hello forge — descriptor v1"
    assert forge_echo(payload) == {"echoed": payload}


def test_forge_echo_round_trips_empty_string() -> None:
    assert forge_echo("") == {"echoed": ""}


def test_forge_get_descriptor_schema_returns_real_schema() -> None:
    result = forge_get_descriptor_schema()
    # Phase 2 rewires this tool to call descriptor_json_schema directly,
    # so the response is always the real envelope: {"ok": True, "result": <schema>}.
    assert result["ok"] is True
    schema = result["result"]
    assert isinstance(schema, dict)
    assert "properties" in schema or "$schema" in schema


def test_forge_version_is_non_empty_string() -> None:
    v = _forge_version()
    assert isinstance(v, str)
    assert v


def test_main_entry_point_is_callable() -> None:
    """``forge-mcp`` console script entry point must be importable."""
    assert callable(main)


def test_forge_version_falls_back_when_metadata_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the PackageNotFoundError branch of ``_forge_version``."""
    from importlib.metadata import PackageNotFoundError  # noqa: PLC0415

    def _raise(_: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(server_mcp, "version", _raise)
    assert _forge_version() == "0.0.0+local"


def test_forge_get_descriptor_schema_returns_real_schema_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 2 rewire: the tool delegates to ``schema_tools.get_descriptor_schema``.

    We monkey-patch *that* helper to confirm the wiring without touching
    the real descriptor module.
    """
    from forge_mcp.server.tools import schema as schema_tools  # noqa: PLC0415

    def _fake() -> dict[str, object]:
        return {"ok": True, "result": {"$schema": "https://example/test"}}

    monkeypatch.setattr(schema_tools, "get_descriptor_schema", _fake)
    result = forge_get_descriptor_schema()
    assert result == {"ok": True, "result": {"$schema": "https://example/test"}}


def test_main_invokes_server_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main`` should construct a server and call ``run``."""
    calls: list[str] = []

    class _Stub:
        def run(self) -> None:
            calls.append("run")

    def _build() -> _Stub:
        return _Stub()

    monkeypatch.setattr(server_mcp, "build_server", _build)
    monkeypatch.setattr(server_mcp, "_install_default_realizer_factory", lambda: False)
    main()
    assert calls == ["run"]


def test_install_default_realizer_factory_skips_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``$FORGE_BLENDER_BIN`` is unset, no factory should be installed."""
    from forge_mcp.realize import BLENDER_BIN_ENV  # noqa: PLC0415
    from forge_mcp.server.tools import (  # noqa: PLC0415
        get_realizer_factory,
        set_realizer_factory,
    )

    monkeypatch.delenv(BLENDER_BIN_ENV, raising=False)
    set_realizer_factory(None)
    try:
        installed = server_mcp._install_default_realizer_factory()  # noqa: SLF001
        assert installed is False
        assert get_realizer_factory() is None
    finally:
        set_realizer_factory(None)


def test_install_default_realizer_factory_wires_factory_when_env_valid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A valid ``$FORGE_BLENDER_BIN`` installs a factory; the closure is lazy."""
    from forge_mcp.realize import BLENDER_BIN_ENV  # noqa: PLC0415
    from forge_mcp.server.tools import (  # noqa: PLC0415
        get_realizer_factory,
        set_realizer_factory,
    )

    fake_binary = tmp_path / "blender"
    fake_binary.write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setenv(BLENDER_BIN_ENV, str(fake_binary))
    set_realizer_factory(None)
    try:
        installed = server_mcp._install_default_realizer_factory()  # noqa: SLF001
        assert installed is True
        factory = get_realizer_factory()
        assert factory is not None
        # Sanity-check that the factory itself is a callable; we do *not*
        # call it because that would actually spawn Blender.
        assert callable(factory)
    finally:
        set_realizer_factory(None)


def test_main_installs_realizer_factory_before_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main`` wires the realizer factory before handing off to ``run``."""
    order: list[str] = []

    def _install() -> bool:
        order.append("install")
        return False

    class _Stub:
        def run(self) -> None:
            order.append("run")

    def _build() -> _Stub:
        return _Stub()

    monkeypatch.setattr(server_mcp, "_install_default_realizer_factory", _install)
    monkeypatch.setattr(server_mcp, "build_server", _build)
    main()
    assert order == ["install", "run"]
