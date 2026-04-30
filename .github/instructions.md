# Forge — AI Agent Working Instructions

These rules are **load-bearing** for every contribution made by an AI coding agent
(or a human collaborator) to the `world_forge` repository. They are not aspirational;
CI, pre-commit, and reviewers enforce them. Violating any rule below requires an
explicit, justified PR with the rule name in the description (e.g.
`RELAX: ruff per-file-ignores — justification: ...`).

Read this file before doing anything in the repo. Re-read it whenever you are
about to add a `# noqa`, a `# type: ignore`, an `__init__.py` shim, a "quick
fix", or anything that "just gets CI green".

---

## 1. Phase discipline

The project is sliced into phases under [AGENT/dev_phases/](../AGENT/dev_phases/)
and summarized in [AGENT/ROADMAP.md](../AGENT/ROADMAP.md). Every phase has an
explicit **Outcome**, **Steps**, **Verification**, and **Out of scope** section.

- **Stick to the current phase.** Do not start work that belongs to a later
  phase, even if it looks easy or "while you're in there". Earlier phases lock
  the architecture decisions later phases depend on; jumping ahead silently
  forks the design.
- **Complete the current phase before moving on.** Every step in the phase plan
  must land, every Verification bullet must pass, and the phase's
  out-of-scope list must remain untouched. A "mostly done" phase is a not-done
  phase.
- **No scope creep.** If a need arises that is not in the current phase's
  Steps, either (a) it is already covered by a later phase — defer it, or
  (b) it is a genuine gap — surface it explicitly and update the phase plan
  in the same PR before implementing.
- **No premature scaffolding.** Do not create empty modules, placeholder
  classes, or "we'll fill this in next phase" stubs that the current phase
  does not require. The repo's structure must always reflect what is *built*,
  not what is *planned*.
- **PRD / Architecture are the contract.** If a phase plan and the PRD or
  Architecture disagree, stop and ask. Do not silently pick one.

## 2. Lint and type-check strictness — never loosened

The repo runs `ruff` (with `select = ["ALL"]`) and `mypy --strict` plus
additional tightening (see `pyproject.toml`). These are not negotiable.

- **Never widen `tool.ruff.lint.ignore`.** The Phase 0 list (`D203`, `D213`,
  `COM812`, `ISC001`) is the entire allowed set. New ignores require a
  dedicated PR titled `RELAX: ruff ignore <RULE>` with a written justification
  reviewed by the project owner.
- **Never widen `tool.ruff.lint.per-file-ignores`.** The Phase 0 entry for
  `tests/**/*.py` (`S101`, `D`, `ANN`) is the entire allowed set.
- **Never add `# noqa` without a rule code.** `# noqa` (bare) is banned by
  ruff `PGH004`. Use `# noqa: <RULE>` only when the rule is genuinely wrong
  for the line; include a short inline reason: `# noqa: <RULE>  # reason`.
- **Never add `# type: ignore` without a rule code.** Mypy
  `enable_error_code = ["ignore-without-code"]` enforces this. Bare
  `# type: ignore` will fail CI. Prefer fixing the type; add stubs if a
  third-party library is the problem.
- **Never set `mypy.overrides.ignore_missing_imports = true`.** If a
  dependency lacks types, write a stub under `forge_mcp/_stubs/` and point
  `mypy_path` at it, or upstream a stub. Silencing missing imports hides
  real bugs.
- **Never disable `mypy --strict`** or any of the additional tightening flags
  (`warn_unreachable`, `disallow_any_unimported`, `disallow_any_explicit`,
  `extra_checks`, `enable_error_code`).
- **Pre-commit and CI must pin identical versions** of ruff and mypy. When
  bumping one, bump the other in the same commit.
- **Format and lint locally before committing.** `uv run ruff format` and
  `uv run ruff check --fix` should leave nothing for CI to find.

## 3. Test coverage — 90–95% target, enforced

Once the codebase has real modules (Phase 2 onward), branch coverage on
`forge_mcp/` must sit in the **90–95%** range and be enforced in CI.

- **Coverage floor is 90%.** `pytest --cov=forge_mcp --cov-fail-under=90`
  runs in CI. PRs that drop coverage below the floor are blocked.
- **Coverage ceiling is ~95%.** Going meaningfully above 95% usually means
  testing trivia or implementation details. Aim for genuine behavioral
  coverage, not line-counter satisfaction.
- **Branch coverage is on.** `tool.coverage.run.branch = true`. Untested
  branches count as misses.
- **No `# pragma: no cover` without justification.** Allowed only for
  defensive-only branches that are unreachable by construction (e.g. a
  `match` exhaustiveness fallback). Include an inline comment.
- **Every new public function/class ships with tests.** Unit tests for pure
  logic; integration tests (gated locally on Blender 5.0) for realizer paths
  per the Phase 4+ plan.
- **Determinism is part of coverage.** Generators that the PRD calls
  deterministic must have a "byte-identical on re-run" test. This is not
  optional.
- **No skipped tests in `main`.** `xfail_strict = true` — an unexpectedly
  passing xfail is a failure. Resolve the test or delete it.

## 4. Docstrings — clean, concise, on every relevant code block

Ruff is configured with `pydocstyle` `convention = "google"` and the `D`
rule family selected. Docstrings are required, but they must earn their
keep.

- **Every public module, class, function, and method** has a Google-style
  docstring. Private helpers (`_leading_underscore`) need a docstring only
  when their behavior is non-obvious.
- **One-line summary, imperative mood,** ending in a period. Example:
  `"""Map a structured descriptor to a deterministic terrain spec."""`.
  Not `"""This function maps..."""`.
- **Args / Returns / Raises** sections only when they add information
  beyond the type signature. Do not restate `seed: int` as
  `seed: An integer seed.`.
- **Document invariants and side effects,** not types. Types live in
  annotations; docstrings explain *why* and *what guarantees*.
- **Reference the PRD / Architecture by section** when a function exists
  to satisfy a specific requirement (e.g. `# See ARCHITECTURE.md §4.2`).
- **No commented-out code, no TODO without an issue link,** no "this is
  obvious from the code" comments.
- **Update docstrings in the same commit that changes behavior.** A stale
  docstring is worse than no docstring.

## 5. `schema.json` — kept up to date with the codebase

The structured descriptor schema and other Pydantic-derived JSON Schemas
are part of Forge's public surface (the agent reads them; the canvas page
consumes them; the plan skill embeds them). They must always reflect the
current Pydantic models.

- **Single source of truth: Pydantic models** in `forge_mcp/`. JSON Schema
  files under `schemas/` (or wherever the phase plan locates them) are
  *generated*, never hand-edited.
- **A `uv run forge-schema-export` (or equivalent `make schemas`) command
  regenerates every schema.** This command is added the first time a
  schema is introduced (Phase 2) and lives in `pyproject.toml` `[project.scripts]`.
- **CI verifies schemas are in sync.** A check runs the export and fails if
  the generated files differ from what is committed (`git diff --exit-code`).
  Forgetting to regenerate breaks CI.
- **Schema versioning is mandatory.** Every published schema has a
  `version` field. Breaking changes bump the major; additive changes bump
  the minor. The version embedded in the plan skill, the `project.json`
  metadata, and the generated `schema.json` must all match.
- **Schema changes are reviewed.** Any PR touching a Pydantic model that
  exports to JSON Schema must include the regenerated `schema.json` in the
  diff and call out the version bump in the PR description.

## 6. Operational rules

- **No direct push to `main`.** Work on feature branches, open PRs.
- **CI must be green before merge.** No "merge and fix forward".
- **Pre-commit must be installed locally** (`pre-commit install`).
- **Never commit secrets, API keys, or `.env` files.** `forge` makes zero
  network calls in v1; if you find yourself adding credentials, you are
  violating an architectural invariant (Architecture §15).
- **Never commit binary artifacts** that the project format calls
  *derived* (`.blend`, large `.png`, `.npy` heightmaps under
  `realizations/`). They are gitignored for a reason.
- **Atomic writes for any on-disk project state.**
  Write-to-temp-then-rename. No partial writes.

## 7. When in doubt

1. Re-read the current phase plan in [AGENT/dev_phases/](../AGENT/dev_phases/).
2. Re-read the relevant section of the PRD and Architecture.
3. Ask. Do not "make a judgment call" that silently changes architecture,
   widens lints, lowers coverage, or skips a phase step.

These rules exist because tech debt compounds. The cost of holding the
line in week 1 is a few minutes per PR; the cost of relaxing it is weeks
of cleanup later. Hold the line.
