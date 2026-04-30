# AGENTS.md — pointer for AI coding agents

You are working in the `world_forge` repository (project name: **Forge**).
Before making any change, read these documents in order:

1. [`.github/instructions.md`](.github/instructions.md) — **mandatory rules**
   on phase discipline, lint/type strictness, test coverage, docstrings,
   and schema maintenance. These rules are enforced by CI and reviewers.
2. [`AGENT/PRD.md`](AGENT/PRD.md) — product requirements (v1 thesis,
   architectural commitments, success criteria).
3. [`AGENT/ARCHITECTURE.md`](AGENT/ARCHITECTURE.md) — system architecture
   (project format, MCP server layout, bpy hypergraph, realizer).
4. [`AGENT/ROADMAP.md`](AGENT/ROADMAP.md) — phase roadmap.
5. [`AGENT/dev_phases/`](AGENT/dev_phases/) — the **current phase plan**
   is the contract for what you implement. Find the highest-numbered
   `phaseN.md` and follow it step by step.

## Strictness mandate (do not violate)

- `ruff` runs with `select = ["ALL"]` and a frozen `ignore` list. Do not
  widen it.
- `mypy` runs in `strict = true` mode plus extra checks. Do not relax it.
- No `# noqa` or `# type: ignore` without a rule code and a one-line reason.
- Test coverage floor is **90%** (target band 90–95%) from Phase 2 onward.
- Docstrings: Google style, concise, on every public class/function/method.
- JSON schemas under `schemas/` are generated from Pydantic models and
  verified in CI; never hand-edit.

## Canonical commands

```bash
uv sync                       # install / refresh deps
uv run pre-commit install     # one-time hook setup
uv run pre-commit run --all-files
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov=forge_mcp --cov-report=term-missing
```

CI runs the same commands. If they pass locally, they pass in CI.

## Phase boundaries

Do not start work that belongs to a later phase, even if it looks easy.
Do not leave a phase "mostly done". If a need arises that the current
phase plan does not cover, stop and ask before improvising.
