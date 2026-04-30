# Blender-internal scripts

These Python files run **inside Blender 5.0.0's own embedded interpreter**,
spawned as subprocesses by `forge_mcp/`. They are *not* importable from the
host Python environment (they would `ImportError` on `import bpy`), and
they are excluded from the main `uv run mypy` strict check (see
`pyproject.toml [tool.mypy] exclude`).

## Files

- `adapter.py` — long-lived stdio JSON-RPC server. Spawned by
  `forge_mcp.realize.blender_proc.BlenderProcess`. Speaks the small RPC
  surface defined in [`docs/spikes/02-blender-rpc.md`](../../docs/spikes/02-blender-rpc.md).
- `introspect.py` — single-shot ingestion helper. Walks `bpy.ops` and
  `bpy.types`, emits the raw JSON consumed by
  `forge_mcp.bpy_hypergraph.ingest`. Spawned by Spike 1's ingestion
  pipeline.

## Type-checking

Both files are checked separately against `fake-bpy-module-5.0` stubs:

```bash
uv run mypy --no-incremental scripts/blender
```

CI runs this as a soft (`continue-on-error: true`) step in Phase 1; it is
hardened (failing) from Phase 4 onward.

## Manual invocation

Set `FORGE_BLENDER_BIN` to your Blender 5.0.0 executable or rely on
`/usr/bin/blender` being a 5.0.0 binary.

```bash
# Smoke test: introspect bpy and dump JSON to /tmp/raw.json
"$FORGE_BLENDER_BIN" --background --python scripts/blender/introspect.py -- --out /tmp/raw.json

# Smoke test: launch the adapter, send a single ping
echo '{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}' | \
    "$FORGE_BLENDER_BIN" --background --python scripts/blender/adapter.py
```

## Stdio discipline

`adapter.py` reserves **stdout** for JSON-RPC frames only. Blender's own
log output (Python warnings, render progress, etc.) is redirected to
**stderr** during startup. Any production change that risks polluting
stdout will desynchronize the host-side RPC client and crash the realizer
on the next call.
