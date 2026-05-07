

## Plan: Phase 6-d — Cycles/OptiX/CUDA + render-knob expansion

Add optional GPU path-tracing (Cycles via OptiX/CUDA/HIP/METAL) and free-form resolution + PNG-budget overrides on top of the existing tiers, exposed as agent-facing kwargs on `forge.generate_region` and `forge.render_view`. **Cycles + best available GPU is the default when a GPU is detected**; EEVEE + CPU is the deterministic fallback when no GPU is available (or when the agent forces it).

The agent gets a single typed `RenderOptions` kwarg. Width/height override the named tier; `png_max_bytes` overrides the per-tier ceiling (capped at 32 MiB); `cycles_samples` controls fidelity for Cycles. Asking for a non-AUTO device that's not on the host returns a structured `device_unavailable` error.

### Phases

**A — device probe + RenderOptions schema**
1. Adapter PING / new `render.list_devices` step probes Cycles devices via `prefs.addons['cycles'].preferences.get_devices_for_type(...)`, cached on `RealizerEngine.available_devices`.
2. New `forge_mcp/realize/render_options.py` Pydantic model: `engine`, `device`, `width`+`height` (paired), `png_max_bytes`, `cycles_samples`. Validators: pixel cap 16 MP, byte cap 32 MiB, EEVEE+device/EEVEE+samples rejection. *(parallel with A1)*
3. `resolve_render_settings(view_kind, tier, options, available_devices)` helper encodes the engine-default rule and override precedence. *(parallel with A2)*

**B — adapter device-flip step + sequence wiring**
1. New `_handle_render_set_engine_device` flips `prefs.addons['cycles'].preferences.compute_device_type`, toggles per-device `use` bools, sets `scene.cycles.device`. Idempotent.
2. `_handle_ping` / `render.list_devices` exposes the probe payload.
3. curated_sequences.json: prepend `render.set_engine_device` step to `render_preview` macro; widen `inputs_schema` for new fields.
4. Extend `RenderPreviewInputs` dataclass + macro builder.

**C — MCP tool surface**
1. `generate_region` and `render_view` get optional `render_options: dict | None = None`. Validated via `RenderOptions.model_validate`. Surface `invalid_render_options`, `device_unavailable`, existing `png_oversize`.
2. Plumb `available_devices` from the realizer to the tool layer (lazy probe per process).
3. Update mcp.py tool description text.

**D — integration + docs + sanity**
1. `tests/integration/test_render_engine_options.py`: Cycles/CPU happy path + sample-pinned IDAT-digest determinism check.
2. realization.md "Engine semantics" gains a "GPU rendering" subsection documenting `RenderOptions`, the engine-default rule, and the new envelopes.
3. `docs/p6d_render_options_walkthrough.md` — new sanity walkthrough in the §3 prompt-by-prompt format from the Phase 6-c walkthrough; agent probes devices, renders the same region under three configs (EEVEE/CPU default-tier, Cycles/AUTO 1920×1080, Cycles/OPTIX 4K with raised PNG budget), asserts the trace.

**E — verification**
- `forge-schema-export --check` includes new `render_options.schema.json`.
- All canonical gates (ruff/format/mypy/pytest ≥90%).
- `make integration` includes the new test.
- Backwards-compat gate: tools called *without* `render_options` produce byte-identical artefacts to pre-Phase-6-d.

### Relevant files

- generation.py — `_RESOLUTIONS`, `_PNG_MAX_BYTES`, `_RENDER_ENGINE`; `_run_realizer` (L365-L384); `render_view` (L613-L617); `_validate_render_view_args` (L560-L572).
- `forge_mcp/realize/render_options.py` — **new** Pydantic model.
- macros.py — `RenderPreviewInputs` dataclass.
- engine.py — version check; add `_probe_devices()`.
- curated_sequences.json — `render_preview` macro.
- adapter.py — new `_handle_render_set_engine_device`, extend `_handle_ping`.
- mcp.py — tool descriptions.
- `tests/realize/test_render_options.py`, `tests/realize/test_resolve_render_settings.py`, `tests/integration/test_render_engine_options.py` — **new**.
- test_realization_tools.py, test_macros.py, test_engine.py, test_render_view.py — extended.
- realization.md, `docs/p6d_render_options_walkthrough.md`, `AGENT/dev_phases/phase6_d_render_options.md`.

### Decisions (locked)

1. EEVEE remains the deterministic CPU/raster baseline; **Cycles + best available GPU device is the default** when a GPU is detected, falls back to EEVEE on a CPU-only host.
2. Single `device` enum (`AUTO`/`CPU`/`OPTIX`/`CUDA`/`HIP`/`METAL`); non-AUTO unavailable → `device_unavailable`.
3. `width`+`height` are a paired override on top of the named tier (tier stays as the PNG-budget anchor). Cap: 16 MP.
4. `png_max_bytes` override capped at 32 MiB.
5. New knobs land on **both** `forge.generate_region` and `forge.render_view`.

### Further considerations

1. **Cycles determinism vs. seed.** Cycles with adaptive sampling is not bit-deterministic across hardware; the integration determinism test pins `cycles_samples` and runs CPU only. Should the resolver also force `scene.cycles.use_adaptive_sampling = False` when `cycles_samples` is set, to make the determinism guarantee explicit? yes
2. **Per-process device probe vs. per-call.** Probing once at engine construction is fast (~10 ms) but locks the device list to the first session. Should we re-probe on every `forge.list_devices` call?

 cache + add a `force_refresh: bool = False` kwarg on a separate `forge.list_render_devices` MCP tool for diagnostics.
3. **Surface `forge.list_render_devices` as a public tool?** Useful for the agent to introspect before picking a device, vs. just relying on `device_unavailable` to bounce. YES

Plan saved to `memories/session/plan.md`. Ready to proceed with implementation, or want to adjust any of the further-considerations items first?
