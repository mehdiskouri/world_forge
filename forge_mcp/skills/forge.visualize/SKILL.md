---
name: "forge.visualize"
version: "0.2.0"
description: "Pick the cheapest tool that answers the user's question about a region: forge.analyze_region for numbers, forge.render_view for pictures. Choose view kind and resolution by intent and cost."
triggers: ["render a preview", "show me", "visualize this region", "what does it look like", "render a top-down", "render an oblique", "preview", "thumbnail"]
requires_tools: ["forge.analyze_region", "forge.render_view", "forge.inspect_spec", "forge.get_region"]
requires_subagent: false
---

# forge.visualize

Use this skill when the user wants to **see** or **measure** a region
that has already been generated. Forge offers two distinct surfaces;
this skill is about choosing between them and configuring the right
arguments.

## When to invoke

Invoke when the user asks to:

* render / preview / visualize a region,
* see a top-down or perspective view,
* check elevation range, hydrology stats, or other geometric numbers,
* compare two cameras or two resolutions of the same region.

Do **not** invoke when:

* the region has not been generated yet — use `forge.plan` +
  `forge.generate_region` first,
* the user is asking about *connectivity* between regions — use
  `forge.connect`,
* the user wants an audit / quality verdict — use `forge.audit`.

## Tool inventory

This skill drives only four MCP tools:

| Tool | Purpose | Cost |
|---|---|---|
| `forge.get_region` | One region's record (descriptor, polygon, spec_id). | Free, in-memory. |
| `forge.inspect_spec` | The frozen `SpecRecord` (params + seed). | One JSON read. |
| `forge.analyze_region` | Re-analyze the persisted heightmap → elevation stats, hydrology metrics. | One float32 NumPy read; ~tens of ms. |
| `forge.render_view` | Render a PNG via the Blender realizer. | Spawns a Blender process; seconds. |

Never call `forge.generate_region` from this skill — it mutates the
region's spec and blows away cached realizations. If the region is
not generated, surface that to the user and stop.

## Decision tree

1. **Did the user ask for numbers** (elevation range, max slope,
   stream length, …)? Call `forge.analyze_region(region_id)`. The
   returned `analysis` dict already covers every quantity the v1
   `analyze` module computes; do not run a render to derive what you
   can read.
2. **Did the user ask for a picture**? Pick a view kind and a
   resolution (next two sections), then call
   `forge.render_view(region_id, view_kind=..., resolution=...)`.
3. **Are both needed** (e.g. "render it and tell me how steep it
   is")? Call `forge.analyze_region` *first* — if it surfaces
   anything pathological (zero relief, NaNs, empty hydrology) tell
   the user before paying the render cost.

## View kind

| Value | Camera | Use for |
|---|---|---|
| `"ortho_top"` (default) | Orthographic top-down. | Layout, polygon boundaries, river paths. |
| `"perspective_se"` | Perspective from the SE corner, ~30° elevation. | Relief, mountain shapes, valley depth. |

If the user does not specify, prefer `"ortho_top"` for "show me" /
"what does it look like" requests, and `"perspective_se"` for "is it
mountainous?" / "how steep?" follow-ups.

## Resolution

| Value | Pixels | Wall-clock (typical hardware) | When to use |
|---|---|---|---|
| `"preview"` | 512x384 | ~1–2 s | Iteration, quick sanity. |
| `"default"` | 1024x768 | ~3–5 s | The normal answer. |
| `"full"` | 2048x1536 | ~10–20 s | Final hand-off, screenshot for a doc. |

Pick the smallest resolution that answers the question. The output
PNG is also size-bounded server-side, so a `"full"` render can fail
with `render_too_large` on extremely complex scenes — fall back to
`"default"` and tell the user.

## Idempotency and caching

`forge.render_view` writes its output to
`realizations/blender/<region>.<view_kind>.<resolution>.png` and a
sibling `.trace.json`. A subsequent identical call re-renders only if
the spec or scene changed; otherwise it returns the cached path.
Treat the response's `"path"` as authoritative — do not invent paths.

## Worked patterns

### Pattern: "show me alpine-bowl"

```
forge.get_region(region_id="alpine-bowl")
  → confirm spec_id is present (region has been generated)
forge.render_view(region_id="alpine-bowl",
                  view_kind="ortho_top",
                  resolution="default")
  → return the path to the user
```

### Pattern: "is it actually mountainous?"

```
forge.analyze_region(region_id="alpine-bowl")
  → read analysis.elevation.range_m
forge.render_view(region_id="alpine-bowl",
                  view_kind="perspective_se",
                  resolution="default")
```

### Pattern: "give me a thumbnail"

Always `resolution="preview"` — never pay full cost for a thumbnail.

### Pattern: "why does this look wrong?"

This is an *audit* request. Stop and route to `forge.audit` instead of
re-rendering at higher resolution.

## Common pitfalls

* **Calling `render_view` before `generate_region`** returns
  `not_generated`. Surface the error verbatim and tell the user to
  generate first; do not silently call `generate_region` from this
  skill.
* **Asking for `"full"` by default** wastes seconds per request. Only
  promote to `"full"` on explicit ask or for a final artefact.
* **Treating the analysis output as a render**. If the user wants to
  see the terrain, you must call `render_view`; numbers alone are not
  an answer to "show me".
* **Forgetting `view_kind`**: omitting it defaults to `ortho_top`,
  which is fine for layout questions but useless for relief.

## Failure recovery

| Error code | Meaning | Recovery |
|---|---|---|
| `no_open_project` | No project is loaded. | Tell the user to open a project. |
| `unknown_region` | `region_id` is not in the project. | Call `forge.list_regions` and ask the user. |
| `not_generated` | Region has no persisted heightmap. | Tell the user to call `forge.generate_region` first; do not call it yourself from this skill. |
| `realizer_not_configured` | `$FORGE_BLENDER_BIN` is unset. | Surface the error verbatim — the user must configure Blender before any render works. |
| `render_too_large` | PNG exceeded the per-resolution byte ceiling. | Drop one resolution tier and retry once; if still failing, report and stop. |
