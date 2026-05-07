# Phase 6-d sanity walkthrough — Cycles / OptiX / CUDA + render-knob expansion (manual)

This walkthrough is the **mandatory manual gate** for closing
Phase 6-d. It exercises the new `render_options` surface — engine
selection (EEVEE vs Cycles), GPU device probing
(`OPTIX`/`CUDA`/`HIP`/`METAL`), free-form resolution + PNG-budget
overrides, Cycles sample-pinning — through a real Claude Code session
and proves the resolver reaches Blender, the trace records what
actually rendered, and the failure modes the service guarantees fire
on cue.

It is the wire-level companion to the integration test in
[`tests/integration/test_render_engine_options.py`](../tests/integration/test_render_engine_options.py)
and is referenced from
[`AGENT/dev_phases/phase6_d_render_options.md`](../AGENT/dev_phases/phase6_d_render_options.md)
Phase D ("Tests + docs").

The flow follows the same shape as the Phase 6-c walkthrough: install
+ register MCP server → drive an agent through the new tools → render
the same region under three configurations → diff the trace envelopes
→ confirm the failure modes the service guarantees. If any step fails
to behave as described, **stop** and follow §7 ("Failure response") —
do not silently lower the bar.

---

## 0. Prerequisites

| Requirement                | Why                                                                |
| -------------------------- | ------------------------------------------------------------------ |
| Linux / macOS              | dev target; Windows not supported                                  |
| Python 3.13 + `uv` ≥ 0.9   | enforced by `pyproject.toml`                                       |
| **Blender 5.0.0** binary   | the realizer; pin per Architecture §15                             |
| `FORGE_BLENDER_BIN` env    | absolute path to the Blender 5.0.0 binary                          |
| **Claude Code** CLI        | the v1 reference agent host                                        |
| At least one Cycles GPU    | required for §3.4 / §3.5; §3.3 covers the no-GPU fallback path     |

Everything in
[`docs/p6c_subregions_walkthrough.md`](p6c_subregions_walkthrough.md)
must already pass on the same machine. Phase 6-d builds on the
realizer (Phase 4) and the materials surface (Phase 6-bis); both must
work end-to-end before the engine knobs are worth exercising.

> **No GPU on the host?** §3.4 ("Cycles + AUTO") still works — the
> resolver falls back to Cycles on CPU and tags the trace with the
> `cycles_cpu_fallback` note. §3.5 ("Cycles + OPTIX explicit") is the
> step that requires a real OptiX / CUDA / HIP / METAL device; on a
> CPU-only host it must return `device_unavailable` and you can
> capture *that* envelope as the gate artefact instead.

---

## 1. Install + automated gates (CI parity)

```bash
git clone https://github.com/mehdiskouri/world_forge.git
cd world_forge
uv sync
uv run pre-commit install
```

Run the same gates CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q --cov=forge_mcp --cov-branch --cov-fail-under=90
uv run forge-schema-export --check
```

All five must be green. The `forge-schema-export --check` gate
includes the new `schemas/render_options.schema.json` (19 schemas
total). Then run the local integration suite (gated on
`FORGE_BLENDER_BIN`):

```bash
make integration
```

Expect the new `tests/integration/test_render_engine_options.py` to
pass alongside the Phase 4 / 6-c tests. That test is the automated
counterpart of §3.3 + §3.4 below; the manual smoke confirms the MCP
wire layer behaves the same way under a real agent.

---

## 2. Register the MCP server with Claude Code

```bash
uv run which forge-mcp
# e.g. /workspace/world_forge/.venv/bin/forge-mcp

claude mcp add world-forge \
  --scope user \
  --transport stdio \
  --env FORGE_BLENDER_BIN="$FORGE_BLENDER_BIN" \
  -- "$(uv run which forge-mcp)"
```

Restart Claude Code. In a fresh session:

```
/mcp world-forge ping
```

— the server should respond. Then ask the agent to enumerate the new
tool:

> "List the Forge MCP tools whose name is `forge.list_render_devices`."

Expected: the tool surfaces. The Phase 6-d MCP envelope ships **54
tools** total (53 from Phase 6-c + `forge.list_render_devices`).

---

## 3. Exercise the render_options loop

> The exact agent prompts below are reference text. Their phrasing
> can vary; what matters is that the agent's tool calls and the
> server's responses match the expected payloads.

The narrative scenario: a single **alpine_peaks** region called
**RenderTarget** generated once, then re-rendered under three
distinct engine / device / resolution configurations to compare the
resolver behaviour and the trace fields.

### 3.1. New project + region

User → agent:

> "Create a new Forge project at `/tmp/p6d_sanity` named
> `RenderDemo`, world bounds `[[-2000,-2000],[2000,2000]]`. Then add
> a 4 km × 4 km region named `RenderTarget` centred at the origin,
> structured descriptor
> `{terrain: {primary: alpine_peaks, elevation_band: [1500, 3500]}}`,
> seed 11."

Expected agent calls:

```text
forge.create_project(path="/tmp/p6d_sanity", name="RenderDemo",
                     bounds={"min":[-2000,-2000],"max":[2000,2000]})
forge.open_project(path="/tmp/p6d_sanity")
forge.create_region(name="RenderTarget",
                    polygon=[[-2000,-2000],[2000,-2000],[2000,2000],[-2000,2000]],
                    structured_descriptor={
                        "terrain":{"primary":"alpine_peaks",
                                   "elevation_band":[1500.0,3500.0]}},
                    seed=11)
```

Capture the returned `region.node_id` (call it `$RID`); every
subsequent step reuses it. Same 4 km × 4 km caveat as the Phase 6-c
walkthrough §3.1 (the polygon-extent clamp).

### 3.2. Probe the host's render devices

User → agent:

> "Call `forge.list_render_devices` and report which devices are
> available."

Expected agent call:

```text
forge.list_render_devices()
```

Expected envelope shape:

```jsonc
{
  "ok": true,
  "result": {
    "available_device_types": ["OPTIX", "CUDA", "CPU"],   // host-dependent
    "default_device_type":   "OPTIX"                        // first GPU, else CPU
  }
}
```

The probe is captured during the realizer's version-check ping and
cached per process; it lists every device type Cycles can target on
this host. The `default_device_type` is the first GPU in the
preference order `OPTIX, CUDA, HIP, METAL` — or `"CPU"` when no GPU
is detected. Pass `force_refresh=true` to re-issue the ping
(typically only needed after plugging in a new device).

Capture the returned tuple — it determines which of §3.3–§3.5 below
is the *gate* run vs the fallback run on this host.

### 3.3. Configuration A — EEVEE / CPU at the default tier (legacy default)

User → agent:

> "Generate `$RID` with no render options at all, then show me the
> realization summary."

Expected agent call:

```text
forge.generate_region(region_id=$RID)
```

Expected realization summary fields:

```jsonc
{
  "render_engine":      "BLENDER_EEVEE",
  "render_device_type": "CPU",
  "render_cycles_samples": 64,         // resolver default; ignored by EEVEE
  "render_notes":       []
}
```

The legacy path. Calls that omit `render_options` entirely keep the
pre-Phase-6-d behaviour byte-for-byte: EEVEE on CPU at the tier
defaults (preview 512×384, default 1024×768, full 2048×1536). This
is the determinism baseline used by `make integration`.

Capture the rendered preview file size and PNG digest (e.g. via
`sha256sum /tmp/p6d_sanity/realizations/blender/${RID}.preview.png`)
— §3.6 below verifies it matches a second identical run.

### 3.4. Configuration B — Cycles / AUTO at 1920×1080

User → agent:

> "Re-render the same region's default view at 1920×1080 with
> `render_options = {engine: 'CYCLES', device: 'AUTO', width: 1920,
> height: 1080, cycles_samples: 64, png_max_bytes: 8388608}`."

Expected agent call:

```text
forge.render_view(region_id=$RID, view_kind="ortho_top",
                  resolution="default",
                  render_options={
                    "engine": "CYCLES",
                    "device": "AUTO",
                    "width":  1920,
                    "height": 1080,
                    "cycles_samples": 64,
                    "png_max_bytes": 8_388_608
                  })
```

Expected envelope fields (host-dependent device pick):

```jsonc
{
  "render_engine":         "CYCLES",
  "render_device_type":    "OPTIX",        // or first available GPU; "CPU" if none
  "render_cycles_samples": 64,
  "render_notes":          []              // or ["cycles_cpu_fallback"] on CPU-only hosts
}
```

The AUTO device picker walks `OPTIX → CUDA → HIP → METAL → CPU`. On
a host with any GPU, that GPU is selected and the explicit Cycles
request is honoured. On a CPU-only host, the resolver falls back to
Cycles on CPU and tags the trace with the `cycles_cpu_fallback` note
— the artefact still renders.

The 1920×1080 frame size comes straight from the `width`/`height`
override (the `default` tier preset is ignored when both dimensions
are supplied). The 8 MiB PNG ceiling lifts the NF-1.5 default to fit
the higher-resolution frame.

### 3.5. Configuration C — Cycles / OPTIX explicit at 4K with raised PNG budget

> Skip this step on a CPU-only host and capture the
> `device_unavailable` envelope from §3.7 instead.

User → agent:

> "Re-render the same view at 3840×2160 with
> `render_options = {engine: 'CYCLES', device: 'OPTIX', width: 3840,
> height: 2160, cycles_samples: 256, png_max_bytes: 33554432}`."

Expected agent call:

```text
forge.render_view(region_id=$RID, view_kind="ortho_top",
                  resolution="default",
                  render_options={
                    "engine": "CYCLES",
                    "device": "OPTIX",
                    "width":  3840,
                    "height": 2160,
                    "cycles_samples": 256,
                    "png_max_bytes": 33_554_432
                  })
```

Expected envelope fields:

```jsonc
{
  "render_engine":         "CYCLES",
  "render_device_type":    "OPTIX",
  "render_cycles_samples": 256,
  "render_notes":          []
}
```

This is the maxed-out path: explicit GPU device, 4K frame, 32 MiB
PNG ceiling, 256 Cycles samples (the resolver pins
`use_adaptive_sampling=False` so the digest is reproducible). The
3840×2160 frame is right at the 16 MP pixel-budget ceiling
(`MAX_PIXEL_BUDGET = 4096 × 4096`); anything larger raises
`invalid_render_options`.

### 3.6. Determinism handshake on Configuration A

User → agent:

> "Re-run the §3.3 generate_region call (no render_options) and
> diff the preview PNG digest against the digest from §3.3."

Expected agent calls:

```text
forge.generate_region(region_id=$RID)
```

The two PNG digests must be **byte-identical** — that is the
backwards-compat gate the integration test asserts. The legacy
`render_options=None` path resolves to EEVEE/CPU at the same tier
defaults and the same Cycles-sample value (ignored by EEVEE), so
the renders are bit-reproducible.

### 3.7. Failure mode — device that's not on the host

> Run this step on whichever host you have. Pick a device type that
> is **not** in the §3.2 `available_device_types` list (e.g. `METAL`
> on a Linux/Nvidia box, `CUDA` on a CPU-only Mac).

User → agent:

> "Re-render `$RID` with `render_options = {engine: 'CYCLES',
> device: 'METAL'}`."

Expected agent call:

```text
forge.generate_region(region_id=$RID,
                      render_options={"engine":"CYCLES","device":"METAL"})
```

Expected error envelope:

```jsonc
{
  "ok": false,
  "error": {
    "code": "device_unavailable",
    "message": "Cycles device 'METAL' is not available on this host.",
    "details": {
      "device": "METAL",
      "available": ["OPTIX", "CUDA", "CPU"]
    }
  }
}
```

The structured `available` list is what the agent uses to recover —
typically by retrying with `device: "AUTO"` or one of the listed
types. No partial render runs; nothing is written to disk.

### 3.8. Failure mode — invalid combination

User → agent:

> "Re-render `$RID` with `render_options = {engine: 'BLENDER_EEVEE',
> device: 'OPTIX'}`."

Expected error envelope:

```jsonc
{
  "ok": false,
  "error": {
    "code": "invalid_render_options",
    "message": "...EEVEE is CPU-only; remove device or set engine to CYCLES.",
    "details": { /* pydantic validation context */ }
  }
}
```

Same envelope for `{engine: 'BLENDER_EEVEE', cycles_samples: 128}`
(EEVEE doesn't path-trace) and for `{width: 1920}` without a paired
`height`. Validation runs before the realizer is even invoked.

---

## 4. On-disk artefacts

After §3.5 (or §3.4 if no GPU), the project tree contains:

```
/tmp/p6d_sanity/realizations/
├── heightmap/$RID.npy
├── heightmap/$RID.png
└── blender/
    ├── $RID.blend
    ├── $RID.preview.png                     # §3.3
    ├── $RID.ortho_top.default.png           # §3.5 last write wins
    ├── $RID.perspective_se.default.png
    └── $RID.realization.json                # trace; render_engine etc echoed
```

The trace sidecar records the `render_engine` / `render_device_type`
/ `render_cycles_samples` / `render_notes` of the *most recent*
render; previous configurations live only in the agent transcript.
For the close-out PR, capture transcripts from §3.3 + §3.4 + §3.5
plus the final `$RID.realization.json`.

---

## 5. Walkthrough close-out

| Item                                              | Status |
| ------------------------------------------------- | ------ |
| §1 — automated gates green                        |        |
| §2 — MCP server registered, 54 tools surface      |        |
| §3.2 — `forge.list_render_devices` returns probe  |        |
| §3.3 — EEVEE/CPU legacy default render            |        |
| §3.4 — Cycles/AUTO 1920×1080 render               |        |
| §3.5 — Cycles/OPTIX 4K render (or 3.7 substitute) |        |
| §3.6 — legacy default is bit-reproducible         |        |
| §3.7 — `device_unavailable` fires on cue          |        |
| §3.8 — `invalid_render_options` fires on cue      |        |

Only when every row above is checked is Phase 6-d "manually green".

---

## 6. Failure response

If any step misbehaves, **stop**:

1. Capture the failing envelope verbatim (agent transcript + server log).
2. File a follow-up under `AGENT/follow_ups/` describing the gap.
3. Do **not** lower the assertions in this walkthrough. Phase 6-d's
   sole purpose is to gate the new render-options surface against
   regressions; weakening the gate defeats the point.

The most common failure modes seen during development:

- **`forge.list_render_devices` returns an empty list** even though
  `nvidia-smi` shows a GPU — the Cycles addon failed to enumerate
  devices because the OptiX runtime is missing. Install the Blender
  build that ships with the matching OptiX SDK or fall back to CUDA.
- **§3.6 digest mismatch** — something in the legacy path drifted.
  This is a backwards-compat regression. Bisect against the
  Phase 6-c PNG digest.
- **`device_unavailable` fires for the device that §3.2 listed** —
  the per-process probe cache is stale. Pass `force_refresh=true`
  and re-run; if it persists, the adapter and resolver disagree on
  the device list, which is a bug.
