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
import sys
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
    import pytest

EXPECTED_TOOL_COUNT = 3


def test_build_server_registers_v1_tool_surface() -> None:
    server = build_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "forge.ping",
        "forge.echo",
        "forge.get_descriptor_schema",
    }
    assert len(tools) == EXPECTED_TOOL_COUNT


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


def test_forge_get_descriptor_schema_returns_schema_or_placeholder() -> None:
    result = forge_get_descriptor_schema()
    # Either the real schema (descriptor branch merged) or the
    # placeholder ($schema + x-status) — both expose ``$schema``.
    assert "$schema" in result


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
    """Cover the success branch where the descriptor module is importable."""
    import types  # noqa: PLC0415

    fake = types.ModuleType("forge_mcp.descriptor")

    def _schema() -> dict[str, object]:
        return {"$schema": "https://example/test", "title": "FakeDescriptor"}

    fake.descriptor_json_schema = _schema  # type: ignore[attr-defined]  # injected for test
    monkeypatch.setitem(sys.modules, "forge_mcp.descriptor", fake)
    result = forge_get_descriptor_schema()
    assert result["title"] == "FakeDescriptor"


def test_main_invokes_server_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main`` should construct a server and call ``run``."""
    calls: list[str] = []

    class _Stub:
        def run(self) -> None:
            calls.append("run")

    def _build() -> _Stub:
        return _Stub()

    monkeypatch.setattr(server_mcp, "build_server", _build)
    main()
    assert calls == ["run"]
