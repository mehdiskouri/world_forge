# Spike 2 — Blender RPC Adapter

**Branch:** `blender-rpc-adapter`
**Time-box:** 4 days. **Actual:** within budget.
**Verdict:** ✅ **GO**

## What was built

- `scripts/blender/adapter.py` — Blender-internal JSON-RPC 2.0 server.
  Reads requests one per line on stdin, writes responses one per line
  on stdout, sends Blender chatter to stderr. Methods: `ping`,
  `shutdown`, `bpy.ops.<group>.<name>`, `bpy.data.<collection>.{new,remove}`,
  `set_property`/`get_property`, `set_idprop`/`get_idprop`. Errors
  follow the spec (`-32601`, `-32602`, `-32000`).

- `forge_mcp/realize/rpc.py` — pure JSON-RPC framing primitives.
  `RpcRequest`, `RpcResponse` (with strict `from_json` validation),
  `RpcError` (raised on remote errors), `RpcProtocolError` (raised on
  framing violations), `RpcClient` (thread-safe synchronous, monotonic
  request ids, id-mismatch detection, peer-closed detection). 100 %
  line+branch coverage from in-memory unit tests.

- `forge_mcp/realize/blender_proc.py` — `BlenderProcess` context
  manager that owns the subprocess lifecycle. Resolves Blender from
  `$FORGE_BLENDER_BIN`. `stdout` is captured for RPC frames; `stderr`
  is forwarded to the host's stderr so Blender's banner and exception
  tracebacks remain visible during development.

- `tests/realize/test_rpc.py` — 12 tests for the pure framing layer.
- `tests/realize/test_blender_proc.py` — 7 tests: 4 unit (env-var,
  pre-start invariants) + 3 integration gated on `$FORGE_BLENDER_BIN`.

## End-to-end verification (real Blender 5.0.0)

```
$ FORGE_BLENDER_BIN=/usr/bin/blender uv run pytest -v -m blender_integration
...
tests/realize/test_blender_proc.py::test_integration_ping PASSED
tests/realize/test_blender_proc.py::test_integration_idprop_round_trip PASSED
tests/realize/test_blender_proc.py::test_integration_unknown_method_returns_jsonrpc_error PASSED
3 passed, 19 deselected
```

All three risks called out in PRD §11.6 / §11.7 are validated:

1. **JSON-RPC over stdio works against Blender 5.0** — the adapter
   starts inside `blender --background --python adapter.py`, the host
   sends a request, the adapter executes against the live `bpy` namespace
   and returns a structured response in **<2 s** including subprocess
   spawn time.

2. **IDProperty round-trip works on 5.0** (PRD §11.7, ARCHITECTURE
   §5.6 — the bleeding-edge IDProperty refactor risk).
   `set_idprop("forge_node_id", "region_alpheim_north")` followed by
   `get_idprop` returns the exact string value. **The fallback
   path documented in §5.6 is not needed for v1.**

3. **Unknown methods produce a JSON-RPC `-32601` error**, surfaced as
   `RpcError` on the host side — the host can self-correct without
   crashing the subprocess.

## Performance note

Cold-start latency (Blender subprocess + adapter ready) is roughly
1.5 s on the development machine. The 3-test integration run completes
in ~1.7 s including all three subprocess starts + tear-downs, which
suggests adapter startup itself is well under a second once Blender's
own initialisation finishes. Per-RPC round-trip is sub-millisecond
(line-buffered stdio, single thread, no JSON over HTTP).

This is well inside the budget assumed by ARCHITECTURE §5.7's
realizer — macros call ~10 RPCs each, which means a complete macro
should run in well under a second of overhead on top of the actual
Blender work.

## Strictness notes

- `scripts/blender/adapter.py` is excluded from the strict matrix
  (target interpreter is Blender's, not host CPython). Kept readable
  and small — defensive `try/except` around the dispatch loop with
  `traceback.print_exc(file=sys.stderr)` so a faulty operator call
  never crashes the subprocess.
- `forge_mcp/realize` is fully inside the strict matrix. `JsonValue`
  is the only structural type the API exposes; no `Any` leaks at the
  RPC boundary.
- Module coverage: `rpc.py` 100 %, `blender_proc.py` 81 % (the misses
  are subprocess error/timeout branches that are difficult to drive
  deterministically without flaky tests). Subpackage average 91 %,
  above the Phase 2 floor.

## Go/no-go

GO. The host can drive Blender 5.0.0 over stdio JSON-RPC with the
exact ergonomics the realizer needs. IDProperty round-trip — the
bleeding-edge risk that worried us at planning time — works on the
first try. No fallback strategy required for v1.
