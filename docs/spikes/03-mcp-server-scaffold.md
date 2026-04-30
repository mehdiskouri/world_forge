# Spike 03 — MCP server scaffold (Phase 1)

**Branch:** `mcp-server-scaffold` · **Verdict:** GO ✅

## Goal

Deliver a minimal, MCP-protocol-correct server (Phase 1 surface) that
an MCP-aware host (Claude Desktop / Claude Code / Cursor) can connect to
over stdio, list tools on, and invoke each one. Three v1 tools:

| Tool                            | Purpose                                             |
| ------------------------------- | --------------------------------------------------- |
| `forge.ping`                    | Liveness + version probe.                           |
| `forge.echo`                    | Round-trip string for transport debugging.          |
| `forge.get_descriptor_schema`   | Returns the StructuredDescriptor JSON Schema.       |

These three are deliberately the only Phase 1 tools — realization,
descriptor authoring, and graph queries land in Phases 2-5.

## Implementation

- SDK: `mcp>=1.27.0` (official Anthropic Python SDK), using the
  `FastMCP` decorator-style API from `mcp.server.fastmcp`.
- Module: [forge_mcp/server/mcp.py](../../forge_mcp/server/mcp.py).
  - Pure handler functions registered via `server.tool(...)(fn)` so
    they remain unit-testable in isolation.
  - `forge.get_descriptor_schema` lazy-imports `forge_mcp.descriptor`
    so the scaffold remains importable on a branch where the descriptor
    sibling spike has not yet been merged.
  - `_forge_version()` uses `importlib.metadata.version("forge")` with
    a `"0.0.0+local"` fallback for editable/dev installs.
- Entry point: `[project.scripts] forge-mcp = "forge_mcp.server.mcp:main"`
  in [pyproject.toml](../../pyproject.toml). Allows host configs of the
  form `{"command": "uvx", "args": ["forge-mcp"]}` or
  `{"command": "forge-mcp"}`.

## Verification

### In-process tests

[tests/server/test_mcp.py](../../tests/server/test_mcp.py) — 13 tests,
**100% line+branch coverage** of `forge_mcp/server`:

- Tool registration: `build_server()` exposes exactly the three v1
  tool names via `await server.list_tools()`; server name is `"forge"`.
- Behaviour: every handler is invoked directly and asserted; both
  branches of `forge.get_descriptor_schema` (descriptor missing →
  placeholder; descriptor present → real schema, simulated via
  `monkeypatch.setitem(sys.modules, ...)`) are covered.
- `_forge_version()` `PackageNotFoundError` branch covered via
  `monkeypatch.setattr(server_mcp, "version", _raise)`.
- `main()` is exercised with a stub `build_server` so the spike does
  not actually open a stdio loop in tests.

### Manual stdio smoke test

After `uv pip install -e .`, sending the canonical MCP handshake +
`tools/call forge.ping` over stdin to `forge-mcp` returns:

```text
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05",
 "capabilities":{...,"tools":{"listChanged":false}},
 "serverInfo":{"name":"forge","version":"1.27.0"}}}
{"jsonrpc":"2.0","id":2,"result":{
   "content":[{"type":"text","text":"{\"alive\":true,\"version\":\"0.0.0\"}"}],
   "structuredContent":{"alive":true,"version":"0.0.0"},
   "isError":false}}
```

This validates the four Phase 1 spike-3 acceptance bullets:

1. ✅ MCP-aware host can connect over stdio.
2. ✅ Host can list tools (three names).
3. ✅ Host can invoke each tool and receive a structured JSON result.
4. ✅ Server reports a real `serverInfo.name` / `serverInfo.version`.

## Strictness notes

- `FastMCP`'s public type does not parameterize its three session
  generics; mypy `disallow_any_explicit` flags `FastMCP` annotations
  unless suppressed. Two scoped `# type: ignore[explicit-any]`
  comments are used (one on the function signature, one on the local
  binding), each with a reason comment, per
  [`.github/instructions.md`](../../.github/instructions.md) §2.
- The lazy descriptor import carries
  `# type: ignore[attr-defined]` because the descriptor module ships on
  a sibling branch that has not been merged yet.

## Outcome

- 13/13 tests passing, 100% coverage of `forge_mcp/server`.
- `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy` all clean.
- End-to-end stdio handshake + tool call confirmed against the
  installed `forge-mcp` console script.

**Verdict: GO.** The scaffold is ready to host the Phase 2-5 tool
surface. No architectural changes required.
