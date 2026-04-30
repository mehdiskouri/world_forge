# Plan: Phase 0 — `world_forge` Repo Bootstrap

Stand up the public `world_forge` GitHub repo on Apache-2.0 with a green-from-day-1 Python 3.13 / `uv` scaffold, strict linting + typing baseline, and unit-only CI. End state: a fresh clone runs `uv sync && uv run pytest` cleanly and CI is green on the first PR. Zero application code beyond a placeholder package — Phase 1 spikes start on top of this.

> **STRICTNESS MANDATE (load-bearing for the project's future health):** ruff and mypy are configured at maximum strictness from the very first commit. No `# noqa`, no `# type: ignore`, no `mypy --no-strict-optional`, no per-file ignore lists, no `ruff: ignore` blanket excludes. Every rule that ruff and mypy ship strict-mode is **on**. Tech debt compounds; we pay zero principal at the start. Any future relaxation requires an explicit PR with justification.

## Phase outline

### Stage A — Remote + local repo creation
1. **Create remote repo.** `gh repo create world_forge --public --description "Agent-native worldbuilding MCP server (Forge v1)" --license apache-2.0 --gitignore Python --add-readme=false`. The `--license` and `--gitignore` flags seed Apache-2.0 + a Python `.gitignore` server-side; we will overwrite both locally to control exact content.
2. **Clone into the existing workspace.** Repo cloned alongside or replacing the current `world_forge` workspace folder; preserve the existing `AGENT/` directory (PRD/Architecture/ROADMAP) by adding it on the first commit.
3. **Verify default branch is `main`.** Set if needed: `gh repo edit --default-branch main`.
4. **Initial branch model.** Work on `main` directly only for this phase; subsequent work happens on feature branches via PR.

### Stage B — Project metadata + license
1. `LICENSE` — exact Apache-2.0 text (use the GitHub-seeded copy or `curl https://www.apache.org/licenses/LICENSE-2.0.txt`); add a `NOTICE` file with copyright line `Copyright 2026 Mehdi <…>`.
2. `pyproject.toml` (PEP 621):
   - `[project]` — name `forge`, version `0.0.0`, requires-python `>=3.13`, license `Apache-2.0`, authors, description, keywords, classifiers.
   - `[project.urls]` — Homepage, Repository, Issues.
   - `[build-system]` — `hatchling` (default for uv-managed packages).
   - `[tool.uv]` — dev dependencies: `ruff`, `mypy`, `pytest`, `pytest-cov`, `pre-commit`. No runtime deps yet (the `mcp` SDK, Pydantic, numpy etc. arrive in Phase 1+ as needed).
   - Strict tool sections (see Stage D).
3. `README.md` skeleton — vision one-liner from PRD §1, status badge for CI, install + dev quickstart (`uv sync`, `uv run pytest`, `pre-commit install`), pointer to `AGENT/PRD.md` and `AGENT/ARCHITECTURE.md`.
4. `AGENTS.md` (root) — short pointer document for AI coding agents: links to `AGENT/PRD.md`, `AGENT/ARCHITECTURE.md`, `AGENT/ROADMAP.md`; states the strictness mandate; lists the canonical commands (`uv sync`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest`).

### Stage C — Source layout + placeholder package
1. `forge_mcp/__init__.py` — single line: `__version__ = "0.0.0"` (typed; mypy strict will care).
2. `forge_mcp/py.typed` — empty marker file (PEP 561; required so downstream consumers see our type hints).
3. `tests/__init__.py` — empty.
4. `tests/test_smoke.py` — one trivial test importing `forge_mcp` and asserting `__version__`. Exists so CI's pytest step has something to run and proves the toolchain end-to-end.

### Stage D — Strict ruff + mypy configuration (do not soften)
1. **`[tool.ruff]`** in `pyproject.toml`:
   - `target-version = "py313"`, `line-length = 100`, `src = ["forge_mcp", "tests"]`.
   - `[tool.ruff.lint]` — `select = ["ALL"]`. Then a *minimal* `ignore` list covering only rules that are stylistically incompatible with chosen formatter behavior or tautological with other selected rules: `D203` (vs `D211`), `D213` (vs `D212`), `COM812` (conflicts with formatter), `ISC001` (conflicts with formatter). **Do not add anything else to `ignore`.** Rules like `ANN`, `D`, `S`, `BLE`, `TRY`, `PLR`, `C90` stay on.
   - `[tool.ruff.lint.per-file-ignores]` — `tests/**/*.py = ["S101", "D", "ANN"]` — assertions in tests, no docstring requirement, no annotations requirement on test functions. **This is the only per-file relaxation allowed.**
   - `[tool.ruff.lint.pydocstyle]` — `convention = "google"`.
   - `[tool.ruff.lint.flake8-tidy-imports]` — `ban-relative-imports = "all"`.
   - `[tool.ruff.format]` — defaults; will format the codebase.
2. **`[tool.mypy]`** in `pyproject.toml`:
   - `python_version = "3.13"`, `strict = true`. The `strict = true` flag turns on the full strict set (including `disallow_untyped_defs`, `disallow_any_generics`, `warn_return_any`, `no_implicit_optional`, `warn_unused_ignores`, etc.).
   - Additional tightening: `warn_unreachable = true`, `disallow_any_unimported = true`, `disallow_any_explicit = true`, `extra_checks = true`, `enable_error_code = ["ignore-without-code", "redundant-self", "truthy-bool", "possibly-undefined"]`.
   - `[[tool.mypy.overrides]]` — none. Third-party stubs added inline as deps grow; never silenced via `ignore_missing_imports`.
3. **`[tool.pytest.ini_options]`** — `addopts = "-ra --strict-markers --strict-config"`, `xfail_strict = true`, `testpaths = ["tests"]`.
4. **`[tool.coverage.run]`** — `branch = true`, `source = ["forge_mcp"]`. Coverage threshold left unenforced for Phase 0 (one-line package); enforce ≥85% from Phase 2 onward in the roadmap.

### Stage E — `.gitignore`
Single root `.gitignore` covering Python (`__pycache__/`, `*.py[cod]`, `*$py.class`, `.pyc`), packaging (`build/`, `dist/`, `*.egg-info/`, `wheels/`), uv (`.venv/`, `.uv/`), test/coverage (`.pytest_cache/`, `.coverage`, `htmlcov/`, `coverage.xml`, `.tox/`, `.nox/`), mypy (`.mypy_cache/`), ruff (`.ruff_cache/`), Forge-specific (`realizations/`, `*.blend`, `*.blend1`, `*.blend2`, `*.npy`, `*.png` *only inside* `realizations/` — keep PNG allowed elsewhere for canvas/docs assets), node modules (`node_modules/`, `dist-canvas/`), OS junk (`.DS_Store`, `Thumbs.db`), IDE (`.vscode/`, `.idea/`, `*.swp`).

### Stage F — Pre-commit hooks
1. `.pre-commit-config.yaml`:
   - `pre-commit-hooks` (v latest pinned): `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-toml`, `check-added-large-files` (5MB), `check-merge-conflict`, `mixed-line-ending` (`--fix=lf`).
   - `astral-sh/ruff-pre-commit` (pinned): `ruff` then `ruff-format`. Use `--exit-non-zero-on-fix` so pre-commit fails on any auto-fix (forcing the dev to stage the fix).
   - `pre-commit/mirrors-mypy` (pinned): runs `mypy` against `forge_mcp` with the same strict config.
2. README documents `pre-commit install` as part of the dev quickstart.
3. CI runs `pre-commit run --all-files` as the first step (single source of truth for lint/format).

### Stage G — GitHub Actions CI
1. `.github/workflows/ci.yml`:
   - Triggers: `push` to `main`, `pull_request` against `main`.
   - Single job `ci` on `ubuntu-latest`, Python 3.13 via `actions/setup-python@v5` (or `astral-sh/setup-uv@v3` which handles both uv and Python).
   - Steps: checkout → setup uv → `uv sync --all-extras --dev` → `pre-commit run --all-files` → `uv run mypy forge_mcp tests` → `uv run pytest --cov=forge_mcp --cov-report=term-missing`.
   - `concurrency` block to cancel in-progress runs on the same ref.
   - Cache: `astral-sh/setup-uv` provides built-in uv cache; enable it.
2. `.github/workflows/` housekeeping — no other workflows in Phase 0 (release + integration workflows arrive in Phases 4 and 8).
3. `.github/dependabot.yml` — weekly updates for `github-actions` and `pip` (uv-compatible) ecosystems. Optional; recommended.
4. Issue + PR templates omitted in Phase 0 (add when external contributors arrive).

### Stage H — Branch protection (advisory; documented even if solo)
1. After first green CI run, enable on `main`: require status check `ci` to pass, require linear history, restrict force-push, require PRs (no direct push). Configurable via `gh api`; document in `README.md#contributing` even if not enforced solo.

### Stage I — First commit + first PR proof
1. **Commit 1 on `main`:** initial scaffold (everything above), commit message `chore: bootstrap forge v1 repo (Apache-2.0)`.
2. Push to remote; verify CI green on `main`.
3. **Sanity PR:** branch `chore/verify-ci`, trivial change (e.g., README typo), open PR, confirm CI runs and gates merge. Merge and delete the branch.
4. Tag none yet — first tag waits until Phase 8 (`v0.1.0`).

## Step ordering and dependencies
Stages A → B run sequentially (need the cloned repo). Stages C, D, E, F can be authored in parallel inside the same commit since they are independent files. Stages G, H depend on B+D being correct (CI invokes ruff/mypy/pytest configured there). Stage I is the verification gate that closes Phase 0.

## Relevant files (final Phase 0 tree)
```
world_forge/
├── .github/
│   ├── workflows/ci.yml
│   └── dependabot.yml                 # optional
├── .gitignore
├── .pre-commit-config.yaml
├── AGENT/
│   ├── PRD.md                         # already present
│   ├── ARCHITECTURE.md                # already present
│   └── ROADMAP.md                     # already present
├── AGENTS.md
├── LICENSE                            # Apache-2.0
├── NOTICE
├── README.md
├── pyproject.toml
├── forge_mcp/
│   ├── __init__.py
│   └── py.typed
└── tests/
    ├── __init__.py
    └── test_smoke.py
```

## Verification (manual + automated)
1. Fresh clone in a clean directory: `gh repo clone <user>/world_forge && cd world_forge && uv sync && uv run pytest` — all green.
2. `uv run ruff check .` exits 0; `uv run ruff format --check .` exits 0.
3. `uv run mypy forge_mcp tests` exits 0 with strict mode active (verify by intentionally adding an untyped function locally and confirming it fails — then revert).
4. `pre-commit run --all-files` exits 0 against the committed tree.
5. CI on first push shows a green ✓ on `main`.
6. Sanity PR (Stage I.3) shows the CI status check blocking merge until green.
7. Repo landing page on github.com shows: Apache-2.0 badge, README rendered, `AGENT/` docs visible, no large binaries committed.

## Cross-cutting strictness rules (apply to every later phase)
- **Never widen ruff `ignore` or `per-file-ignores`** without an explicit PR justification block in the description. The `tests/**` exemption above is the entire allowed list at Phase 0.
- **Never add `# type: ignore` without an error code** (ruff `PGH003` enforces this; mypy `ignore-without-code` doubles it).
- **Never add `mypy.overrides` with `ignore_missing_imports = true`** without first attempting to install/write proper stubs.
- **Pre-commit must stay in sync with CI** — both run the same ruff and mypy versions (pin in both files to the same SHA/version).
- Coverage threshold added in Phase 2 once real code lands; until then, only smoke-test coverage exists.

## Out of scope for Phase 0
- Any application code (`server/`, `project/`, `descriptor/`, `realize/`, `bpy_hypergraph/`, etc.) — those land in Phases 1 and 2.
- Runtime dependencies (`mcp`, Pydantic v2, numpy, scipy, FastAPI). Added per phase as needed.
- Blender installation, integration test harness — Phases 1 and 4.
- Release workflow, changelog automation, PyPI publication — Phase 8.
- Issue/PR templates, CODE_OF_CONDUCT, CONTRIBUTING.md — defer until external contributors join.
