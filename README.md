# Forge

> Agent-native worldbuilding format and toolchain delivered as an MCP server.
> Pure-deterministic Python core, Blender 5.0 realizer, multi-layer hypergraph
> as project memory. See [`AGENT/PRD.md`](AGENT/PRD.md) for the v1 thesis and
> [`AGENT/ARCHITECTURE.md`](AGENT/ARCHITECTURE.md) for the system design.

**Status:** Phase 0 — repo bootstrap. No application code yet.

[![CI](https://github.com/mehdi/world_forge/actions/workflows/ci.yml/badge.svg)](https://github.com/mehdi/world_forge/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/release/python-3130/)

---

## Quickstart (development)

Forge targets **Python 3.13** and uses [`uv`](https://docs.astral.sh/uv/)
for environment + dependency management.

```bash
git clone https://github.com/mehdi/world_forge.git
cd world_forge

uv sync                       # create .venv, install dev deps
uv run pre-commit install     # wire up git hooks
uv run pytest                 # smoke tests pass
uv run ruff check .           # lint
uv run ruff format --check .  # formatting
uv run mypy                   # strict type-check
```

A fresh clone followed by `uv sync && uv run pytest` should be green
on the first try. If it isn't, that's a bug — open an issue.

## Repository layout

```
world_forge/
├── .github/
│   ├── instructions.md       # mandatory rules for AI agents + humans
│   └── workflows/ci.yml
├── AGENT/                    # PRD, Architecture, Roadmap, phase plans
├── forge_mcp/                # the package (placeholder in Phase 0)
├── tests/
├── pyproject.toml
└── ...
```

## For AI coding agents

Read [`.github/instructions.md`](.github/instructions.md) **before** making
any changes. It defines the non-negotiable rules around phase discipline,
lint/type-check strictness, test coverage, docstrings, and schema
maintenance.

Companion documents:

- [`AGENTS.md`](AGENTS.md) — short pointer for AI clients.
- [`AGENT/PRD.md`](AGENT/PRD.md) — product requirements.
- [`AGENT/ARCHITECTURE.md`](AGENT/ARCHITECTURE.md) — system design.
- [`AGENT/ROADMAP.md`](AGENT/ROADMAP.md) — phase roadmap.
- [`AGENT/dev_phases/`](AGENT/dev_phases/) — per-phase implementation plans.

## Contributing

- Work on feature branches; open PRs against `main`.
- CI must be green; pre-commit must be installed locally.
- Lint + type-check strictness will not be loosened (see
  [instructions §2](.github/instructions.md#2-lint-and-type-check-strictness--never-loosened)).
- Test coverage target is **90–95%** once real code lands (Phase 2+).

## License

[Apache License 2.0](LICENSE) © 2026 Mehdi.
