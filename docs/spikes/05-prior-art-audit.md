# Spike 05 — Prior-art audit (Phase 1)

**Branch:** `prior-art-audit` · **Verdict:** GO ✅

## Goal

Lock in the differentiator framing for Forge before Phase 2 begins, so
the public narrative (README, eventual Show HN, talks) and the
internal scope decisions both rest on the same rationale. Per
`AGENT/dev_phases/phase1.md` Stage F.

## Deliverables

- [docs/prior_art.md](../prior_art.md) — comparison table over six
  systems (BlenderMCP, Houdini, Gaea, Wonderdraft, Azgaar, World
  Machine) on five axes: mechanism, scope, agent-readiness, format
  openness, multi-tool reach. One paragraph of editorial per system.
- Five differentiator bullets explicitly called out:
  1. Hypergraph as project memory.
  2. Zero-LLM determinism in the realize loop.
  3. MCP-native from day 1.
  4. Typed `bpy` command vocabulary.
  5. Cross-tool roadmap.
- README updated to cross-link `docs/prior_art.md`.

## Findings (summary)

- **Closest competitor:** BlenderMCP. Same protocol surface, but no
  project memory, no descriptor IR, no cross-tool ambition. Forge
  subsumes it as a special case.
- **Closest concept:** Houdini's procedural node graph. Forge inherits
  the "graph as memory" idea but exposes it through MCP rather than a
  GUI, and aims explicitly at multi-tool reach instead of single-tool
  depth.
- **No identified competitor** combines MCP-native delivery + typed
  command vocabulary + persistent hypergraph + cross-tool ambition.
  The differentiator framing holds.

## Verdict

**GO.** Differentiator narrative is stable and concrete enough to
guide Phase 2 scope decisions and external messaging. No PRD or
Architecture revisions triggered.
