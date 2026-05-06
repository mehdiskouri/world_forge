---
name: "forge.plan"
version: "0.1.0"
description: "Extract a structured terrain descriptor from free-text intent and drive the Forge generation pipeline. Stage A placeholder; full content lands in branch skill-plan-content."
triggers: ["create a region", "describe terrain", "make this a", "generate a region"]
requires_tools: ["forge.get_descriptor_schema", "forge.create_region", "forge.generate_region", "forge.analyze_region", "forge.inspect_spec", "forge.render_view"]
requires_subagent: false
---

# forge.plan (Phase 5 Stage A placeholder)

This SKILL.md is a placeholder shipped by branch
``skills-package-and-loader`` so the loader, CLI, and packaging can be
verified end-to-end. Branch ``skill-plan-content`` replaces this body
with the full embedded JSON Schema, the ten worked free-text →
descriptor examples, and the workflow / pitfalls / failure-recovery
sections specified in [phase5.md](../../../AGENT/dev_phases/phase5.md).
