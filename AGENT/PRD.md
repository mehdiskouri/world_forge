# Forge v1 — Product Requirements Document

**Status:** Draft (Revision 3)
**Owner:** Mehdi
**Target ship:** 6–7 weeks from kickoff
**Document version:** 3.0

> *Working name: Forge. Replace when branding lands.*

---

## 1. Vision

Forge is an agent-native worldbuilding format and toolchain delivered as an MCP server. Game projects today are tangled webs of cross-references — GUIDs, prefab dependencies, material slots, scene hierarchies — stored as opaque binaries that resist version control, agent reasoning, and multi-tool workflows. Forge replaces the binary-as-source-of-truth model with a structured, multi-layer hypergraph project format that holds the world's combinatorial structure as inspectable JSON, while geometry and binary outputs are derived, regeneratable artifacts.

The product is consumed through the user's existing MCP-capable agent client — Claude Code, Claude Desktop, Cursor, GitHub Copilot, or any other client supporting MCP. Forge does not ship its own conversational UI; the agent provides that. Forge ships a structured tool surface, a skill library that teaches the agent how to use it well, and a popup connection-map visualization that gives the user and the agent a shared spatial reference for the project structure.

The long-term product is a cross-tool orchestration layer that drives Blender, Unity, Unreal, and physics simulators (MuJoCo, Newton, Warp) from a single agent-readable project format. The long-term differentiator is the multi-layer hypergraph as project memory: an agent or human collaborator always has structured access to inter-asset relationships, cross-cutting features, and persistent design intent — across sessions, across tools, and across scales.

V1 proves the foundational architecture on a single tool (Blender 5.0, owned via Forge's own bpy realizer) and a single axis (terrain). It is a deliberately minimal end-to-end slice. Everything that doesn't validate the architectural thesis is deferred.

## 2. V1 thesis

A user, working through their MCP-capable agent client (Claude Code, Cursor, etc.):

1. Draws region polygons on the popup canvas page
2. Asks the agent to compile descriptors and generate the regions
3. Receives generated terrain previews directly in the agent's chat surface
4. Iterates by asking the agent to lock features, reroll seeds, or revise descriptors
5. Adds adjacent regions whose seams join smoothly
6. Sees a stream flow continuously across a region boundary
7. Watches the popup connection map update live as the project structure evolves

If this works, the project format, descriptor schema, boundary contract solver, editing semantics, typed command realization via the bpy hypergraph, and skill-based agent interaction are all proven. Everything else (biomes, settlements, culture, multi-tool, additional realizers) is execution risk on top of validated foundations.

## 3. Architectural commitments

Two commitments distinguish v3.0 from prior revisions and shape every requirement that follows:

### 3.1 Forge is purely deterministic Python

Forge contains zero LLM calls. All semantic interpretation of natural-language input happens in the calling agent's context, governed by the plan skill which defines the structured descriptor schema. The agent receives free text from the user, extracts a structured descriptor matching the schema, and passes it to Forge tools. Forge's tools accept only structured input and produce deterministic output.

This makes Forge:
- Cheaper to run (no API costs)
- Faster (no network roundtrip in the inner loop)
- Easier to test (deterministic given inputs)
- Privacy-preserving (no data leaves the user's machine except via the agent client they chose)
- Independent of any model provider

Trade-off: Forge requires a skill-capable agent client, or the user must provide structured input directly. This is acceptable since the target user already runs an MCP-capable agent.

### 3.2 Blender 5.0 as the v1 target

Forge pins Blender 5.0.x as the v1 target (rather than 4.4 LTS) for three reasons specific to agent-driven workflows:

- **API harmonization** — 5.0 unified naming conventions across render engines, eliminating "dialect" differences that previously caused agents to produce inconsistent scripts.
- **Context simplification** — 5.0 expanded data-block-centric access via `bpy.data`, reducing reliance on `bpy.ops` and its strict context requirements (the source of most "poll failed" errors in agent-generated scripts).
- **First-class type hinting** — 5.0's improved PEP 484 compliance makes API introspection significantly more reliable, which directly improves the bpy hypergraph ingestion quality.

Trade-off: 5.0 is not LTS; patch versions could introduce semantic changes mid-build. Mitigation: pin a specific 5.0.x patch in the bpy hypergraph version field; document migration steps for future updates. The IDProperty refactor in 5.0 is a known area of bleeding-edge behavior — Forge uses custom IDProperties to link Blender objects back to project node IDs, so this is monitored as a known risk.

## 4. Out of scope (explicit)

The following are deferred to v2 or later. They will not be built, scaffolded, or partially implemented in v1:

**Worldbuilding scope:**
- Settlements, architecture, buildings
- Biomes beyond terrain texturing
- Vegetation scatter
- Multiple style sheets, cultural overlays, era and weathering
- Climate as a separate layer (only used as terrain hints in v1)
- Lore, history, faction, kinship layers
- Anchors for hand-authored content (only locks in v1)
- Deltas and prototype inheritance
- Global archetypes

**Asset and execution scope:**
- Generative 3D asset creation (Trellis, Hunyuan3D, Meshy, Rodin)
- External asset library integration (Quixel, Polyhaven, Sketchfab)
- User-uploaded asset ingestion
- Unity, Unreal, MuJoCo, Newton, Warp realizers
- Physics validation, navmesh generation, navigation rollouts

**Agent scope:**
- Subagent orchestration beyond the audit skill
- Macro mining, automated command discovery, proposer-solver-verifier systems
- LLM-driven creative agents proposing additions autonomously (autopilot mode)
- Cross-session agent memory beyond what skills provide
- Any LLM call originating from inside Forge

**Product scope:**
- Multiplayer or shared editing
- Marketplace, billing, SaaS infrastructure
- Telemetry, usage analytics
- Forge's own conversational UI (the agent client provides it)

The discipline of holding this line is the most important non-technical requirement of v1.

## 5. Users and workflows

### 5.1 Target user (v1)

Solo or small-team game developers and 3D artists who already use an MCP-capable agent client (Claude Code, Cursor, Claude Desktop, or GitHub Copilot with MCP), are familiar with procedural workflows, and are comfortable with descriptor-driven generation. v1 is a proof-of-architecture demo, not a public product, so the user is primarily Mehdi plus a small set of friendly testers (3–5 people, ideally including one game developer and one technical artist already using agent-based workflows).

### 5.2 Primary workflow

The user installs Forge by adding it to their MCP client config and ensures the plan skill is loaded by their client. They open a project directory with their agent. They open the popup canvas page in a browser tab (or VSCode webview, if using Claude Code in VSCode). The interaction loop:

```
User opens canvas → Draws region polygon → Names region →
User asks agent (free text): "generate this region as a rugged alpine valley with a stream" →
Agent (using plan skill) extracts structured descriptor matching Forge's schema →
Agent invokes create_region / generate_region with structured input →
Forge runs deterministic generation → returns analysis + preview image →
Agent presents to user →
User asks: "lock that hill on the south ridge and reroll the seed" →
Agent calls lock_feature, then reroll_seed →
Forge regenerates deterministically with locks honored →
User draws adjacent region → Asks agent to generate as rolling foothills →
Agent extracts structured descriptor → Forge generates with boundary contract →
Connection map (popup) updates throughout
```

### 5.3 Session shape

A v1 session is 5–30 minutes. The user creates 1–4 regions, iterates a few times each, saves. No long-running work, no agent autopilot, no cross-session continuity beyond project save/load and skill-mediated agent memory.

### 5.4 The three-plane interaction model

V1 explicitly separates three concerns:

- **Forge plane** — owns project state, hypergraph, deterministic generators, contracts, the bpy realizer, and the popup connection map. Pure Python, no LLM.
- **Agent plane** — the user's MCP client (Claude Code, Cursor, etc.). Owns conversation, intent interpretation, descriptor extraction (using the schema from the plan skill), tool invocation, and presentation of Forge's outputs.
- **User plane** — the human, interacting through the agent client and the popup canvas page.

This separation is the architectural commitment that lets Forge ride the wave of agent capability instead of building parallel UX, and lets it remain free of any model-provider dependency.

## 6. Functional requirements

### 6.1 MCP server and tool surface

- **F-1.1** Forge runs as an MCP server installable via standard MCP client configuration.
- **F-1.2** Forge exposes the tool set defined in §6.2. The tool surface is the agent's API.
- **F-1.3** Tool results return structured data (JSON) and image content (rendered previews) using MCP's native content types.
- **F-1.4** Forge ships with a skill library (§6.3) that compatible agent clients load to learn Forge's interaction patterns and the structured descriptor schema.
- **F-1.5** All tool inputs accept only structured data conforming to documented schemas. No tool accepts free text for semantic interpretation.

### 6.2 Required tools (v1 surface)

**Project tools:**
- `create_project(name, world_bounds)`, `open_project(path)`, `save_project()`, `close_project()`

**Region tools:**
- `create_region(name, polygon_coords, structured_descriptor?, seed?)`
- `update_region(region_id, fields)`
- `delete_region(region_id)`
- `list_regions()`, `get_region(region_id)`

**Generation tools:**
- `generate_region(region_id, options?)` — full plan/realize, returns preview image and analysis
- `reroll_seed(region_id)` — new seed, regenerate with locks honored

**Lock tools:**
- `lock_property(region_id, property_path, value?)`
- `lock_feature(region_id, feature_descriptor)`
- `lock_region(region_id)` — wholesale lock
- `list_locks(region_id?)`, `unlock(lock_id)`

**Inspection tools:**
- `inspect_spec(spec_id | region_id)`
- `analyze_region(region_id)` — structured numerical analysis (no render)
- `render_view(region_id, view_kind, resolution?)` — explicit render request, returns image

**Schema tools:**
- `get_descriptor_schema()` — returns the structured descriptor JSON schema for the agent to validate against

**Hypergraph tools:**
- `query_layer(layer, root_node?, depth?, filter?)`
- `list_boundaries()`, `inspect_boundary(boundary_id)`

**History tools:**
- `undo()`, `history(limit?)`

**Canvas synchronization tools:**
- `get_canvas_state()`

Note the absence of `compile_descriptor`. The descriptor extraction step lives in the agent's context, not in Forge.

### 6.3 Skills

Forge ships five SKILL.md files. The plan skill is now load-bearing for v1 functionality since it carries the descriptor schema.

- **F-3.1 Plan skill (`forge.plan`).** Triggers on intent requiring structural change. Carries the structured descriptor schema (terrain primary type, elevation band, ruggedness, hydrology) and teaches the agent to: extract structured descriptors from user free text, validate against the schema, propose plans, surface to user, execute via Forge tools, update connection map. **Required for full v1 functionality.**

- **F-3.2 Visualize skill (`forge.visualize`).** Teaches structured-data-primary, snapshots-for-verification perception. Defines view kinds and resolution choices.

- **F-3.3 Audit skill (`forge.audit`).** Triggers after generation. Audit subagent compares structured descriptor (intent) against analysis + views (realization), produces verdict. v1's only subagent.

- **F-3.4 Cleanup skill (`forge.cleanup`).** Detects and proposes safe cleanup of orphaned specs, stale realizations, conflicting locks.

- **F-3.5 Connect skill (`forge.connect`).** Hypergraph traversal and cross-cutting hyperedge creation.

### 6.4 Subagent policy (v1)

- **F-4.1** v1 supports exactly one subagent type: the audit subagent invoked by the audit skill. Returns structured verdict to main agent.
- **F-4.2** No other subagent orchestration in v1.

### 6.5 Project format and storage

- **F-5.1** A project is a directory on disk with JSON files in a defined folder structure (Architecture §3).
- **F-5.2** Projects can be created empty, opened from disk, saved to disk via tool calls.
- **F-5.3** All project state persisted as JSON. Binary outputs (.blend, PNG) are derived artifacts.
- **F-5.4** Projects are git-friendly: JSON formatted for diffability; binaries gitignored.

### 6.6 Region definition

- **F-6.1** User draws region polygons on the popup canvas via mouse.
- **F-6.2** Agent can create regions via `create_region` with explicit polygon coordinates.
- **F-6.3** Each region has unique ID, user-supplied name, polygon footprint, integer seed.
- **F-6.4** Regions can be moved, resized, deleted via agent or canvas.
- **F-6.5** Regions cannot overlap; structured error returned on attempts.
- **F-6.6** Adjacent regions auto-detected; boundary objects auto-created.

### 6.7 Structured descriptors

- **F-7.1** Forge defines a JSON schema for structured descriptors. Schema available via `get_descriptor_schema` tool and embedded in the plan skill.
- **F-7.2** v1 schema covers: terrain primary type (alpine_valley, rolling_hills, desert_mesa, boreal_lowland, volcanic_cone, etc.), elevation band, ruggedness scalar, hydrology presence and character.
- **F-7.3** `create_region` and `update_region` accept structured descriptors; reject malformed input with structured errors.
- **F-7.4** Forge maps structured descriptors to terrain spec parameters via deterministic Python (lookup tables and interpolation). No LLM involved.

### 6.8 Terrain generation

- **F-8.1** Given (terrain spec, seed, contracts), generation is deterministic. Same inputs = byte-identical output.
- **F-8.2** Pipeline: ridged multifractal noise → hydraulic erosion → thermal erosion → optional stream carving.
- **F-8.3** Generation under 60s for 1km × 1km at 2m/pixel on target dev machine.

### 6.9 Boundary contracts

- **F-9.1** Adjacency creation triggers boundary object with contract.
- **F-9.2** Contract negotiates elevation continuity along shared edge.
- **F-9.3** Generators consume contracts as boundary conditions.
- **F-9.4** Stream crossings carry stream anchors; both sides honor them.
- **F-9.5** Conflicting locks → conflict state surfaced via tool results.

### 6.10 Editing semantics

- **F-10.1** Property locks pin spec values by JSON path.
- **F-10.2** Feature locks capture heightmap patches, constrain regeneration.
- **F-10.3** Wholesale region locks skip regeneration entirely.
- **F-10.4** Seed reroll generates new seed, regenerates with locks honored.
- **F-10.5** Undo replays history minus last N events; v1 keeps last 50 live.

### 6.11 Realization (Blender 5.0 via the bpy hypergraph)

- **F-11.1** Forge owns Blender execution via its own bpy realizer. No dependency on third-party Blender MCPs.
- **F-11.2** Realizer uses a *bpy knowledge hypergraph* — structured representation of Blender 5.0's Python API — as typed command vocabulary.
- **F-11.3** Long-lived headless Blender 5.0 process; communication via stdio JSON-RPC.
- **F-11.4** Realization plans constructed by the realizer engine; executes curated operator sequences, validates pre/postconditions, leverages 5.0's improved data-block-centric API to minimize `bpy.ops` reliance.
- **F-11.5** v1 ships ~30–50 curated bpy operators in the hypergraph plus 5–8 macros. General planner is v2.
- **F-11.6** Each generation produces .blend file plus default-resolution preview images returned via MCP.
- **F-11.7** Custom IDProperties on Blender objects link realized geometry back to project node IDs. v1 monitors 5.0's IDProperty refactor for stability.

### 6.12 Agent perception

- **F-12.1** Perception cost scales with intent uncertainty, not project size. Most reasoning over structured payloads; renders for verification.
- **F-12.2** `analyze_region` returns numerical properties without rendering.
- **F-12.3** `render_view` returns images at preview/default/full resolutions, ortho_top and perspective_se views.
- **F-12.4** `generate_region` automatically returns analysis + default-resolution preview.
- **F-12.5** Images returned via MCP image content; no separate transport.

### 6.13 The popup connection map

- **F-13.1** Forge serves a popup web page accessible as VSCode webview or standalone browser tab.
- **F-13.2** Page renders project worldbuilding hypergraph as live force-directed connection map with layer toggles.
- **F-13.3** Receives state updates from Forge over WebSocket; user actions post back as Forge tool invocations.
- **F-13.4** Only direct Forge UI; everything else agent-mediated.

## 7. Non-functional requirements

### 7.1 Performance

- **NF-1.1** Plan construction: under 1 second.
- **NF-1.2** Heightmap generation (1km², 2m/px): under 30 seconds.
- **NF-1.3** Blender realization (single region): under 60 seconds end-to-end.
- **NF-1.4** Connection map update latency: under 500ms after Forge state change.
- **NF-1.5** Tool result image return: 1024×768 PNG under 200KB encoded.
- **NF-1.6** Structured descriptor validation: under 100ms.

### 7.2 Determinism

- **NF-2.1** Given identical (spec, seed, generator version, contracts, bpy hypergraph version), all outputs bitwise reproducible. *Forge has no non-deterministic stages in v1; LLM removal eliminates the previous caveat.*

### 7.3 Reliability

- **NF-3.1** Crashes during generation must not corrupt project. Atomic writes (write-temp-then-rename).
- **NF-3.2** Blender process failures recoverable; restart in under 5 seconds.
- **NF-3.3** Failed generations leave project in last-known-good state.

### 7.4 Privacy and data

- **NF-4.1** All Forge operations local. **Forge makes zero network calls in v1.** Network traffic originates only from the agent client the user has chosen.
- **NF-4.2** No API keys required by Forge.

### 7.5 Compatibility

- **NF-5.1** Forge MCP server compatible with any MCP client supporting standard stdio or HTTP transports.
- **NF-5.2** Skills shipped as standard SKILL.md files; compatible with any client supporting the Anthropic skill format.
- **NF-5.3** **Plan skill is required for full v1 functionality** because it carries the descriptor schema. Clients without skill loading require the user to supply structured descriptors directly, or to copy the schema into the system prompt.
- **NF-5.4** Popup canvas works as VSCode webview (Claude Code in VSCode) and as standalone browser tab (other clients).
- **NF-5.5** Blender 5.0.x required. Specific patch version pinned per build; documented in `project.json`.

## 8. Success criteria

V1 is successful if and only if the following four tests pass on a clean install with a friendly tester running a skill-capable agent client:

### 8.1 Seam test

Tester draws two adjacent regions with contrasting descriptors ("rugged alpine valley", "rolling foothills") on the canvas. Asks the agent to generate both. Agent extracts structured descriptors via plan skill, calls Forge tools. Tester rotates around the seam in the resulting Blender scene; reports as visually plausible — no cliff, no gap, no z-fighting.

### 8.2 Regeneration test

Tester draws one region, asks agent to generate. Tester identifies a feature ("lock that hill on the south ridge"). Agent calls `lock_feature`. Tester asks for three seed rerolls. After all three, the locked feature is recognizably preserved while surroundings vary meaningfully.

### 8.3 Descriptor coherence test

Tester gives agent five descriptors covering the v1 design space. Agent extracts structured descriptors and generates each. Tester reports outputs as visually distinct and recognizably matching their descriptors.

### 8.4 Connection map test

Throughout 8.1–8.3, the popup connection map updates live: regions appear as nodes, boundaries as edges, status reflects state. Tester reports map as informative and accurate.

### 8.5 Demo recording

Clean 3–5 minute video walkthrough of the four tests above, recorded against a real agent client (Claude Code or Cursor). Suitable for Show HN, Twitter, portfolio.

## 9. Risks

### 9.1 Architectural risks

- **R-1** Boundary contract math produces visible artifacts at seams. *Mitigation:* concentrated week 5 testing with extreme cases.
- **R-2** Structured descriptors don't visually differentiate the way agents expect. *Mitigation:* 5-descriptor eval set built week 2; iterated against before week 4. Schema documented in plan skill with examples.
- **R-3** bpy hypergraph effect curation incomplete; realizer fails on edge cases. *Mitigation:* hand-curate v1 operator set; aggressive pre-week-1 ingestion spike.
- **R-4** Blender 5.0 process reliability poor in long sessions. *Mitigation:* restartability designed in from day 1; pre-week-1 RPC spike validates against 5.0 specifically.
- **R-5** Agent quality at descriptor extraction varies across clients. *Mitigation:* plan skill provides explicit schema and examples; `get_descriptor_schema` tool lets agents self-correct; structured input validation surfaces specific errors.

### 9.2 Schedule risks

- **R-6** bpy hypergraph ingestion takes longer than 3 days against 5.0's evolving API. *Mitigation:* time-box pre-week-1 to 4 days; ship thinner curated set if needed.
- **R-7** 5.0's IDProperty refactor causes runtime issues with custom metadata. *Mitigation:* validate IDProperty round-trip during pre-week-1 spike; fall back to scene-level metadata dict if refactor proves unstable.
- **R-8** Popup canvas integration with VSCode webview finicky. *Mitigation:* fallback to standalone browser tab; webview is enhancement.
- **R-9** Skills don't shape agent behavior as expected. *Mitigation:* test plan skill specifically with Claude Code mid-week 3; iterate before further building.

### 9.3 External risks

- **R-10** Blender 5.0.x patch update breaks bpy hypergraph mid-build. *Mitigation:* pin specific patch; lock version in project.json; document migration steps.

### 9.4 Personal risks

- **R-11** Job applications and other research projects compete for time. *Mitigation:* explicit 6–7 week box; reassess week 4; ship lite version rather than slip.

## 10. Timeline

| Week | Deliverable | Demo state |
|------|-------------|------------|
| Pre  | bpy hypergraph ingestion (Blender 5.0), RPC spike, MCP scaffold, prior-art audit, IDProperty validation | Architecture decisions locked; bpy 5.0 ops queryable |
| 1    | Project format schemas, structured descriptor schema, MCP tool scaffolding | Hand-edit a project; agent lists regions via MCP |
| 2    | Structured-descriptor → terrain spec mapping (deterministic Python); terrain generator | Structured descriptor → heightmap PNG via tool call |
| 3    | bpy 5.0 realizer: curated operator execution, the 5–8 v1 macros | Heightmap → .blend with terrain mesh; preview returned via MCP |
| 4    | Skills authored (plan with schema embedded, visualize, audit, cleanup, connect); audit subagent | Agent end-to-end region creation from free-text prompts |
| 5    | Boundary contracts; popup canvas page (drawing, hypergraph rendering) | Two adjacent regions; verified seam |
| 6    | Locks, reroll, undo; popup connection map live updates | Full v1 feature set working |
| 7    | Polish, edge cases, demo recording, README, install docs | Four success-criteria tests pass; demo video shipped |

## 11. Pre-week-1 checklist

These must complete before week 1, in 4–5 days of focused spike work:

1. **bpy hypergraph ingestion against Blender 5.0.** Walk `bpy.ops` and `bpy.types` programmatically in headless Blender 5.0. Extract operators, parameters, types, poll functions. Match against 5.0's Sphinx docs. Hand-curate effects for the v1 operator set (~30–50 ops). Produce v1 bpy hypergraph as JSON. Validate the data-block-centric paths (5.0's selling point) work for terrain creation. *2–3 days.*
2. **Blender 5.0 RPC spike + IDProperty validation.** Headless Blender 5.0 with stdio JSON-RPC server. Confirm: persistent state, sub-second roundtrip, recoverable from crashes. Validate IDProperty round-trip on custom metadata (the known bleeding-edge area). *0.5–1 day.*
3. **MCP server scaffold.** Minimal Python MCP server using official SDK with 2–3 dummy tools. Verify loads in Claude Code, Claude Desktop, Cursor. *0.5 day.*
4. **Structured descriptor schema draft.** Write the v1 schema (terrain types, elevation band, ruggedness, hydrology). Validate against 10 free-text test descriptors with manual extraction. Verify the schema captures meaningful variation. *0.5 day.*
5. **Prior-art audit.** Map BlenderMCP, Houdini, Gaea, Wonderdraft, Azgaar, World Machine. Document differentiator framing. *0.5 day.*

If any flag a serious issue, the PRD is revised before week 1 starts.

## 12. Post-v1 (informational, not committed)

Natural v2 priorities, in order, each estimated at 2–4 weeks:

1. Second axis: biome with vegetation scatter (procedural)
2. Anchors for hand-authored content; full editing semantics
3. Asset-building subagents (settlements, vegetation specialists)
4. External asset library integration (Polyhaven, Quixel)
5. Generative 3D for unique assets (Trellis/Hunyuan3D/Rodin)
6. Second realizer: Unity scene assembly via its own typed-command hypergraph
7. General bpy planner (synthesizing novel sequences)
8. Blender LTS migration path (when 5.x reaches LTS, currently scheduled for 5.4 LTS)

## 13. Approvals

| Field | Value |
|-------|-------|
| PRD version | 3.0 |
| Approved | 2026-04-29 |
| Next review | End of week 4 |
| Major changes from v2.0 | Forge is purely deterministic (no internal LLM); plan skill becomes load-bearing for v1; Blender pinned to 5.0.x for AI-friendly API improvements; descriptor compilation moved to agent context; pre-week-1 adds IDProperty validation |
