# Blender Setup

The Phase-4 realizer talks to a real Blender 5.0 binary over JSON-RPC
(see [docs/realization.md](realization.md)). Tests, the bench, and the
server tools all locate the binary the same way: through the
``FORGE_BLENDER_BIN`` environment variable.

## Install Blender 5.0

Any 5.0.x install is fine. On Debian / Ubuntu:

```bash
sudo apt-get install blender    # if 5.0 is in your distro
# or download the tarball from https://www.blender.org/download/
# and add the `blender` binary to PATH.
```

Verify:

```bash
blender --version  # should print "Blender 5.0.x"
```

## Wire it into the realizer

Set ``FORGE_BLENDER_BIN`` to the absolute path of the binary:

```bash
export FORGE_BLENDER_BIN=/usr/bin/blender
```

The host-side helper resolves it like this:

* ``forge_mcp.realize.blender_binary()`` reads the env var and raises
  ``BlenderNotConfiguredError`` if it is missing or does not point at
  an executable file.
* ``BlenderProcess()`` uses the same helper to launch the subprocess
  with ``--background --python scripts/blender/adapter.py`` and the
  ``--`` separator that hands control over to the adapter.

Test integration markers (``@pytest.mark.blender_integration``) and
``scripts/eval/bench_phase4.py`` both skip gracefully with a clear
message when the env var is unset, so you can develop without a local
Blender install.

## Render-engine notes

The default render engine string used by the realizer is currently
``BLENDER_EEVEE_NEXT`` (the Blender 5.0 engine identifier). If your
local 5.0 install reports a different identifier, override it on the
``render_preview`` macro inputs (the ``engine`` field in
``RenderPreviewInputs``). Cycles is also supported but is markedly
slower; the preview path stays on EEVEE so the NF-1.5 200 KB ceiling
is comfortably met.

## Why a subprocess?

``bpy`` is only available inside Blender's own embedded Python
interpreter, so we never import it from the host. Instead the host
spawns Blender with ``scripts/blender/adapter.py``, which exposes a
small JSON-RPC surface (``RpcMethods`` in
``forge_mcp/realize/rpc.py``). The adapter is type-checked separately
against the ``fake-bpy-module-5.0`` stubs (see the
``mypy-blender-scripts`` CI step) so the host-side strict matrix stays
unaffected.
