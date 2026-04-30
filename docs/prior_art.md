# Prior art

A short audit of adjacent tools and what Forge does differently. The
goal is not exhaustive coverage — it is to lock in a defensible
differentiator framing for Phase 2+ and the eventual public narrative
(Show HN, README, talks).

## Comparison table

| System                              | Mechanism                                 | Scope                              | Agent-readiness                                | Format openness                          | Multi-tool reach                |
| ----------------------------------- | ----------------------------------------- | ---------------------------------- | ---------------------------------------------- | ---------------------------------------- | ------------------------------- |
| **BlenderMCP** (community)          | MCP server wrapping a fixed Blender API   | Single tool (Blender)              | Yes (MCP), but tool surface is hand-curated    | `.blend` (closed binary)                 | Single-tool                     |
| **Houdini** (SideFX)                | Procedural node graph (HDA / VEX / Python)| 3D / VFX / sim, broad              | Manual (HOM Python); no agent protocol         | `.hip` (proprietary)                     | Plugins per pipeline            |
| **Gaea**                            | Node graph terrain authoring              | Terrain only                       | None (GUI-first)                               | `.tor` + heightmap exports               | Export pipeline only            |
| **Wonderdraft**                     | Raster map painter                        | 2D fantasy maps                    | None                                           | Proprietary `.wonderdraft_map`           | None                            |
| **Azgaar's Fantasy Map Generator**  | In-browser procedural generator (JS)      | 2D world maps + lore stubs         | None (JS UI; some JSON export)                 | JSON + SVG (open)                        | Limited                         |
| **World Machine**                   | Node graph terrain                        | Terrain only                       | Limited (CLI batch)                            | Proprietary scene; standard heightmaps   | Export-only                     |

## What each gets right (and what it constrains)

- **BlenderMCP.** Proves the MCP-over-Blender ergonomic. But it is a
  thin RPC wrapper: the agent has to know which `bpy` ops to call and
  what they mean. There is no project memory, no descriptor IR, no
  cross-tool ambition.
- **Houdini.** The gold standard for procedural authoring. The
  node-graph-as-memory idea Forge inherits is essentially Houdini's.
  But it is GUI-first, single-tool, proprietary, and has no protocol an
  agent can reach without bespoke per-customer Python.
- **Gaea / World Machine.** Excellent at terrain; zero ambition beyond
  it. They cannot be a worldbuilding spine because they cannot see
  beyond a heightmap.
- **Wonderdraft / Azgaar.** Beautiful artifact generators. Both are
  GUI-shaped and operate on a single-frame mental model — there is no
  state machine an agent can navigate or extend.

## Forge differentiators

Five bullets that should appear in every external pitch:

1. **Hypergraph as project memory.** A typed graph of operators,
   side-effects, and `bpy.data` paths is the durable substrate. Ops
   compose; descriptors plan; the graph remembers — across sessions
   and across tools. No competitor exposes this.
2. **Zero-LLM determinism in the realize loop.** The descriptor IR is
   the contract between the agent and the engine. Realization is
   pure code: same descriptor in → same scene out. LLMs propose
   descriptors; they never touch geometry directly.
3. **MCP-native from day 1.** Tools are first-class MCP tools with
   typed JSON schemas that any compliant host (Claude Desktop /
   Code / Cursor / future agents) consumes natively — no per-host
   plugin work. BlenderMCP is the only other entrant here, and Forge
   subsumes its surface as a special case.
4. **Typed `bpy` command vocabulary.** Phase 1 already curates the
   2,441 raw ops down to 24 v1 macros with side-effect annotations.
   Agents reason over a small typed vocabulary instead of guessing
   into the full surface — a workable contract for both LLMs and
   downstream symbolic planners.
5. **Cross-tool roadmap.** Blender is the v1 host; Houdini, terrain
   tools, and 2D map generators are explicit Phase 4+ targets through
   the same descriptor IR + hypergraph. The architecture does not
   collapse if a second engine is added — that is the whole point.

## Reading list (cross-references)

- BlenderMCP — <https://github.com/ahujasid/blender-mcp>
- Model Context Protocol spec — <https://modelcontextprotocol.io>
- Houdini Object Model (HOM) — <https://www.sidefx.com/docs/houdini/hom/index.html>
- Gaea — <https://quadspinner.com/gaea>
- Azgaar's Fantasy Map Generator — <https://azgaar.github.io/Fantasy-Map-Generator/>
