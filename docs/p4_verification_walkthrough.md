# Verifying the Phase 4 realizer (manual + integration walkthrough)

This walkthrough is the manual companion to the Phase 4 verification gate
in [`AGENT/dev_phases/phase4.md`](../AGENT/dev_phases/phase4.md). It
covers the three checks that a CI run cannot perform on its own:

* **§6** — manual MCP smoke from Claude Code: `forge.generate_region`
  returns a `.blend` plus previews; `forge.render_view` reuses the
  saved scene without regenerating.
* **§7** — version refusal: a hypergraph pinned to a Blender version
  that does not match the running binary is rejected at engine
  construction.
* **§10** — determinism: the same descriptor + seed produces the same
  pixel payload across two fresh project trees.

Wire-checks for §1–§5 / §8–§9 (lint, mypy, schema, coverage, bench
artefacts, no-new-ignores audit) are mechanical — they run from
`make integration && make qa` against the committed bench output in
[`docs/eval/phase4/`](eval/phase4/).

The flow is intentionally end-to-end: install → register MCP server →
exercise a region round-trip → inspect the on-disk `.blend` and PNG
files → exercise the failure modes that automated CI cannot reach.

---

## 0. Prerequisites

| Requirement                | Why                                                                      |
| -------------------------- | ------------------------------------------------------------------------ |
| Linux (or macOS)           | dev target; Windows not supported in v1                                  |
| Python 3.13                | enforced by `pyproject.toml`                                             |
| `uv` ≥ 0.9                 | sole supported package manager                                           |
| `git`                      | every project tree is meant to be diffable                               |
| **Blender 5.0.0** binary   | mandatory for Phase 4; pin per Architecture §15                          |
| `FORGE_BLENDER_BIN` env    | absolute path to the Blender 5.0.0 binary; everything keys off this var |
| Claude Code (CLI)          | the v1 reference agent host; any MCP host with stdio works               |

Install Blender 5.0.0 per [`docs/blender_setup.md`](blender_setup.md)
and confirm:

```bash
# 1. Find the binary (adjust to wherever your install lives).
which blender                 # e.g. /usr/bin/blender
blender --version | head -1   # → "Blender 5.0.0"

# 2. Export the env var the realizer keys off — every later command
#    in this walkthrough assumes it's set in the current shell.
export FORGE_BLENDER_BIN="$(command -v blender)"
echo "$FORGE_BLENDER_BIN"                 # must print a non-empty path
"$FORGE_BLENDER_BIN" --version | head -1  # → "Blender 5.0.0"
```

If `$FORGE_BLENDER_BIN` is empty the next command expands to a bare
`"" --version` and your shell prints "Command '' not found" — that
means you skipped the `export` step.

If `--version` prints anything other than `Blender 5.0.0`, **stop
here** — the realizer is designed to refuse to operate (see §7
below) and every demo will fail with `BlenderVersionMismatchError`.

---

## 1. Install Forge locally

```bash
git clone https://github.com/mehdiskouri/world_forge.git
cd world_forge
uv sync                       # creates .venv, resolves deps from uv.lock
uv run pre-commit install     # one-time hook setup (recommended)
```

Sanity-check the install gates the same way CI does:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q --cov=forge_mcp --cov-branch --cov-fail-under=90
uv run forge-schema-export --check
```

All five must be green before you proceed. Then run the local
integration suite (gated on `FORGE_BLENDER_BIN`):

```bash
make integration               # uv run pytest tests/integration -m "not slow"
```

Expect 6 tests to pass: two from `test_generate_region.py`, two from
`test_render_view.py`, plus `test_version_refusal.py` and
`test_determinism.py`. These are the automated counterparts of §6,
§7, §10 below; the manual smoke confirms the MCP wire layer behaves
the same way under a real agent.

---

## 2. Confirm the `forge-mcp` entry point

The MCP server is installed as a console script by `pyproject.toml`:

```toml
[project.scripts]
forge-mcp = "forge_mcp.server.mcp:main"
```

```bash
uv run which forge-mcp
uv run forge-mcp --help
```

The absolute path printed by `uv run which forge-mcp` is what you give
to Claude Code in the next step. Forge needs `FORGE_BLENDER_BIN` set
in its environment too — either export it in the shell before
launching the host, or pass `--env` to `claude mcp add` (next step).

---

## 3. Register the server with Claude Code

```bash
claude mcp add forge \
  --scope user \
  --transport stdio \
  --env FORGE_BLENDER_BIN="$FORGE_BLENDER_BIN" \
  -- /workspace/world_forge/.venv/bin/forge-mcp
```

Anything after the bare `--` is the command Claude Code will spawn.
The `--env` is essential: at startup `forge-mcp` reads
`$FORGE_BLENDER_BIN` and installs the default Blender realizer
factory (`forge_mcp.server.mcp._install_default_realizer_factory`).
Without it the factory is left unset and every realization-aware tool
returns a structured `realizer_not_configured` (`generate_region`,
`render_view`) or `realizer_unavailable` (version mismatch) envelope —
that is the symptom to look for if the agent reports
"realizer_not_configured: no realizer factory installed".

Common ways the env var goes missing in the spawned `forge-mcp`:

* You forgot the `--env` flag on `claude mcp add`. The host process
  has the variable but the child does not inherit it.
* You re-registered the server with `claude mcp set forge` and
  dropped the `--env` arg.
* `$FORGE_BLENDER_BIN` was empty in the shell that ran
  `claude mcp add`, so the registered command line literally contains
  `--env FORGE_BLENDER_BIN=`. Re-export the variable per §0 and
  re-register.

Restart Claude Code and confirm Forge appears in the MCP server list
(`/mcp` slash command). The handshake should print the 19 tools from
Phases 2–4 plus `forge.generate_region` (extended) and
`forge.render_view` (new in Phase 4).

---

## 4. §6 — End-to-end region realization from Claude Code

Open a new Claude Code session in a clean directory. Drive the
following sequence; the agent should call exactly these tools.

**Step 4.1 — open a project.** Ask:

> "Create a Forge project named `Phase4Demo` rooted at
> `/tmp/phase4_demo`, world bounds `[[0, 0], [10, 10]]`."

Expected: `forge.create_project` returns `ok` with the project root.

**Step 4.2 — create a region.** Ask:

> "Create a region named `Alpha`, polygon `[[0,0],[1,0],[1,1],[0,1]]`,
> structured descriptor `{terrain: {primary: rolling_hills}}`, seed 7."

Expected: `forge.create_region` returns `ok` with a `node_id` like
`region_alpha`.

**Step 4.3 — generate the region.** Ask:

> "Generate region `region_alpha` and show me the ortho preview."

Expected: `forge.generate_region` returns under ~60 s with:

```jsonc
{
  "ok": true,
  "result": {
    "region_id": "region_alpha",
    "spec_id": "…",
    "blend_path": "/tmp/phase4_demo/realizations/blender/region_alpha.blend",
    "previews": {
      "ortho_top":      { "preview_path": "…/region_alpha.ortho_top.default.png", … },
      "perspective_se": { "preview_path": "…/region_alpha.perspective_se.default.png", … }
    },
    "realization": { "macro": "realize_region", "render_engine": "BLENDER_EEVEE", … },
    "analysis":     { … TerrainAnalysis … }
  }
}
```

Manually check on disk:

```bash
ls /tmp/phase4_demo/realizations/blender/
# region_alpha.blend
# region_alpha.ortho_top.default.png
# region_alpha.perspective_se.default.png
# region_alpha.ortho_top.default.realization.json
# region_alpha.perspective_se.default.realization.json
file /tmp/phase4_demo/realizations/blender/region_alpha.blend
# … Blender3D, version 5.00 …
"$FORGE_BLENDER_BIN" --background \
  --python-expr "import bpy; bpy.ops.wm.open_mainfile(filepath='/tmp/phase4_demo/realizations/blender/region_alpha.blend'); o=bpy.data.objects['terrain_region_alpha']; print(o['forge_node_id'], o['forge_spec_id'], o['forge_kind'])"
# → region_alpha <spec_id> terrain_mesh
```

Acceptance:

* `.blend` ≥ 1 KiB (typically ~500 KiB for the v1 macro).
* Ortho preview ≤ 280 KB at 1024×768 (per the Phase 4 NF-1.5
  measurement note in [`eval/phase4/README.md`](eval/phase4/README.md)).
* IDProperties (`forge_node_id`, `forge_spec_id`, `forge_kind`)
  survive the round trip — Phase 1's verdict honoured.

**Step 4.4 — render a different view without regenerating.** Ask:

> "Render the SE perspective of `region_alpha` at full resolution."

Expected: `forge.render_view(rid, "perspective_se", "full")` returns
under ~10 s with a 2048×1536 PNG path. Inspect:

```bash
ls /tmp/phase4_demo/realizations/blender/region_alpha.perspective_se.full.png
identify /tmp/phase4_demo/realizations/blender/region_alpha.perspective_se.full.png
# → … 2048x1536 …
```

Crucially: confirm `region_alpha.blend`'s mtime did **not** change.
`render_view` re-uses the saved scene; it does not re-run the
terrain generator, the realizer, or the analysis pass.

If any step in §4 fails, the agent surface the structured error
(`stage`, `step`, `reason_code`); attach that envelope to your bug
report — it is the trace the engine produced.

---

## 5. §7 — Version-refusal demo

Architecture §15 invariant: a curated bpy hypergraph pinned to one
Blender patch version refuses to drive any other binary.

**Automated check** (already run by `make integration`):

```bash
FORGE_BLENDER_BIN=/usr/bin/blender uv run pytest \
  tests/integration/test_version_refusal.py -v
# → test_realizer_refuses_mismatched_blender_version PASSED
```

The test loads the real hypergraph and bundle, swaps their
`blender_version` to `"999.0.0"` via `dataclasses.replace` /
`model_copy`, then constructs `RealizerEngine` against a real Blender
5.0 process. Expected outcome: `BlenderVersionMismatchError` from
`_assert_blender_version`, before any macro runs.

**Manual demo.** From a Claude Code session pointed at a
deliberately-mis-versioned Blender (e.g. unset the env var to simulate
a 4.x install):

```bash
unset FORGE_BLENDER_BIN
# restart Forge under a different binary, e.g. Blender 4.2:
claude mcp set forge --env FORGE_BLENDER_BIN=/path/to/blender-4.2
```

Ask: *"Generate region `region_alpha`."* Expected envelope:

```json
{
  "ok": false,
  "error": {
    "reason_code": "realizer_unavailable",
    "message": "running Blender '4.2.0' does not match curated hypergraph target '5.0.0'"
  }
}
```

The engine never enters the macro loop. No `.blend` is written. No
partial state is left behind.

---

## 6. §10 — Determinism check

The PRD's deterministic-given-seed guarantee bottoms out at the
realizer's render output. The integration test
[`test_determinism.py`](../tests/integration/test_determinism.py)
provisions two fresh project trees, runs `forge.generate_region` with
the same descriptor + seed, then hashes the **PNG IDAT chunks** of
the ortho preview from each run.

PNG IDAT carries the deflated pixel payload. We deliberately exclude
the `tEXt` and `tIME` ancillary chunks because libpng injects a
wall-clock timestamp into them — that timestamp is not part of the
determinism contract.

**Run it:**

```bash
make integration -- tests/integration/test_determinism.py -v
# or directly:
FORGE_BLENDER_BIN=/usr/bin/blender uv run pytest \
  tests/integration/test_determinism.py -v
# → test_generate_region_is_byte_deterministic_across_runs PASSED
```

**Manual sanity check.** From two terminals (or sequential runs):

```bash
rm -rf /tmp/det_a /tmp/det_b
# repeat the §4 sequence once into /tmp/det_a, once into /tmp/det_b
python3 - <<'PY'
import hashlib, struct, sys
def idat_digest(p):
    data = open(p, 'rb').read()
    h, pos = hashlib.blake2b(digest_size=16), 8
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos+4])[0]
        t = data[pos+4:pos+8]
        if t == b"IDAT":
            h.update(data[pos+8:pos+8+n])
        pos += 8 + n + 4
    return h.hexdigest()
for p in sys.argv[1:]:
    print(idat_digest(p), p)
PY \
  /tmp/det_a/realizations/blender/region_alpha.ortho_top.default.png \
  /tmp/det_b/realizations/blender/region_alpha.ortho_top.default.png
# → both lines must show the same hex digest.
```

If the digests diverge:

* Confirm both runs used the same Blender binary
  (`md5sum "$FORGE_BLENDER_BIN"`).
* Confirm `bpy_hypergraph` data and `curated_sequences.json` are
  identical (no uncommitted edits).
* Re-run with `FORGE_LOG_LEVEL=DEBUG` and inspect the per-step trace
  written to `…ortho_top.default.realization.json` — divergence will
  show up as differing `params` payloads or differing scene-diff
  counts.

---

## 7. Bench acceptance (§8 cross-reference)

The 5-descriptor bench lives at
[`scripts/eval/bench_phase4.py`](../scripts/eval/bench_phase4.py).
Reproduce with:

```bash
FORGE_BLENDER_BIN=/usr/bin/blender uv run python scripts/eval/bench_phase4.py
# writes docs/eval/phase4/<UTC-timestamp>/{<label>.blend,<label>.heightmap.png,
#                                          <label>.preview.png,contact_sheet.png,
#                                          manifest.json}
```

Acceptance: the contact sheet shows five visually-distinct terrain
types; `manifest.json` records `realize_wall_ms` + `render_wall_ms`
per entry; total wall-clock per region ≤ 60 s on the reference dev
box. See [`docs/eval/phase4/README.md`](eval/phase4/README.md) for
the full acceptance protocol and the NF-1.5 measurement note.

---

## 8. Tear down

```bash
claude mcp remove forge
rm -rf /tmp/phase4_demo /tmp/det_a /tmp/det_b
```

The Forge MCP server has no global state outside the project tree
itself; removing the project directory and unregistering the server
is enough.

---

## Appendix — known v1 measurements

| Item                                                   | PRD/spec     | Phase 4 measured                                   |
| ------------------------------------------------------ | ------------ | -------------------------------------------------- |
| End-to-end `generate_region` (rolling_hills, dev box)  | NF-1.3 ≤60 s | ~6 s (1× realize + 2× preview render @ 1024×768)   |
| Preview PNG (default 1024×768) for textured terrain    | NF-1.5 ≤200 KB | ~213 KB at zlib level 9 — see eval/phase4/README |
| Render engine                                          | EEVEE Next   | `BLENDER_EEVEE` (Blender 5.0 enum identifier)      |
| IDProperty path                                        | real or fallback | real `obj["forge_*"]` (Phase 1 verdict)        |
| Curated sequences shipped                              | n/a          | 9 macros (8 leaf + 1 composite `realize_region`)   |

The PNG ceiling deviation is the single intentional measured
divergence from the PRD; the structured-error path (`reason_code =
png_oversize`) still fires when a render genuinely exceeds the
per-resolution ceiling configured in `_PNG_MAX_BYTES`.
