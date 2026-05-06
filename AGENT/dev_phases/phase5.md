# Plan: Phase 5 — Skills + Audit Subagent

End state: a clean session with a skill-capable agent client (Claude Code primary) where the user types free text ("rugged alpine valley with stream") and gets terrain in Blender plus an audit verdict; plan skill carries the full structured descriptor schema and >=80% extraction success on the 10-descriptor eval set; audit subagent runs in an isolated context and records JSON-schema'd verdicts under `<project>/audits/`. PRD success §8.3 (descriptor coherence) becomes achievable. Five SKILL.md files shipped, packaged, and individually validated.

> **Strictness rules persist.** ruff ALL + mypy strict + 90% branch coverage on `forge_mcp/`. New code: skill loader + verdict schema + audit tools + skill-eval harness — all stay under the same bar. Skill markdown isn't typechecked, but its frontmatter + embedded JSON Schema *are* validated by a CI-run unit test.

## Hard architectural constraint (load-bearing)

**Forge contains zero LLM calls (Architecture §15 invariant).** The audit subagent is therefore invoked *by the agent client*, not by Forge. Forge ships:
- The audit `SKILL.md` (telling the agent client to spawn a subagent with a restricted tool set + verdict prompt).
- The `AuditVerdict` Pydantic schema, exported via `forge.get_audit_schema`.
- A `forge.record_audit(region_id, verdict)` tool that validates against the schema, persists under `audits/<region_id>/audit_NNNN.json` (atomic write), and appends a history event.
- A `forge.list_audits(region_id?)` / `forge.get_audit(audit_id)` retrieval pair.

If a client lacks subagent support (Cursor, Copilot today), the skill instructs the main agent to perform an "inline isolated-context audit" (a documented soft convention). Forge cannot enforce isolation; it can only validate the verdict's structure. This is explicit in the skill body and in `docs/skills.md`.

## Scope summary
- `forge_mcp/skills/` directory with five SKILL.md files: `forge.plan`, `forge.visualize`, `forge.audit`, `forge.cleanup`, `forge.connect`.
- Plan skill embeds the canonical structured descriptor JSON Schema + >=10 worked free-text -> structured-descriptor examples + workflow + tool patterns + pitfalls.
- `forge_mcp/audit/`: `verdict.py` (Pydantic model), `service.py` (record/list/get + persistence), `paths.py` (audits dir layout).
- Three new MCP tools: `forge.record_audit`, `forge.list_audits`, `forge.get_audit`. Existing `forge.get_descriptor_schema` stays; **new** `forge.get_audit_schema`.
- Skills delivery surface: skill files distributed inside the wheel under `forge_mcp/skills/`; a `forge-skills` CLI (`[project.scripts]` entry) copies them into `~/.claude/skills/forge.<name>/` (or `--dest` override) for clients that scan a skill directory; `forge.get_skill(name)` MCP tool returns the raw markdown for clients that paste skill content into a system prompt.
- Skill-validation unit test: every shipped SKILL.md has valid frontmatter (name, description, triggers, version, schema fields) and — for `forge.plan` — the embedded JSON Schema is byte-identical to `descriptor_json_schema()` output (CI lock).
- Plan-skill **extraction eval harness** (`scripts/eval/skill_plan_eval.py`): scores agent extractions against the 10-descriptor fixture; recordable to `docs/eval/phase5/<timestamp>/`. The harness itself is deterministic Python (consumes pre-recorded extractions; does not call any LLM).
- R-9 mid-phase sanity walkthrough doc (`docs/p5_sanity_walkthrough.md`): the "free text -> terrain in Blender" loop on a clean Claude Code session.

## Out of scope for Phase 5 (do not scaffold)
- Subagent orchestration beyond audit (PRD F-4.2 explicit). No worker, planner, or critic agents.
- LLM-side skill execution simulation. We do not run Claude inside CI; the eval harness scores **recorded** extractions only.
- Boundary-contract material in skills (Phase 6). `forge.plan` covers single-region intents; multi-region adjacency wording added in Phase 6.
- Lock/reroll/undo content in skills (Phase 7). The plan skill mentions locks exist; full lock playbook lives in a Phase-7 amendment to `forge.plan` + new content in `forge.cleanup`.
- Live canvas / connection-map skill content (Phase 6+). `forge.connect` Phase-5 version covers hypergraph traversal only, not canvas.
- Auto-invoking the audit subagent from Forge. Forge never spawns subagents.
- Per-client skill loaders beyond Claude. We document Cursor/Copilot manual paste; we don't ship installers for them.
- Telemetry on skill effectiveness (NF-4 forbids).

## Stage A — Skills package layout, frontmatter schema, distribution

1. **Directory**: `forge_mcp/skills/forge.<name>/SKILL.md` — one folder per skill, matches Anthropic skill convention. Each folder may include sibling files (examples, schemas) referenced from the SKILL.md.
2. **Frontmatter schema**: a Pydantic model `SkillFrontmatter(name, version, description, triggers: tuple[str,...], requires_tools: tuple[str,...], requires_subagent: bool)`. Persisted alongside skills as `forge_mcp/skills/_schema.py`. CI test parses every shipped `SKILL.md`'s YAML frontmatter and validates.
3. **Skill loader** (`forge_mcp/skills/loader.py`): `iter_skills() -> Iterator[SkillRecord]`, `load_skill(name) -> SkillRecord` where `SkillRecord(frontmatter, body_markdown, embedded_assets: Mapping[str, str])`. No I/O outside `forge_mcp/skills/`. Uses `importlib.resources` so it works from a wheel.
4. **MCP surface**:
   - `forge.list_skills()` -> tuple of `{name, version, description, triggers}`.
   - `forge.get_skill(name)` -> `{frontmatter, body_markdown}` for paste-into-prompt clients.
   - `forge.get_audit_schema()` -> `AuditVerdict` JSON Schema (parallel to existing `get_descriptor_schema`).
5. **CLI installer** (`[project.scripts] forge-skills = "forge_mcp.skills.cli:main"`):
   - `forge-skills install [--client claude] [--dest PATH] [--force]` — copies skill folders into `~/.claude/skills/forge.<name>/` (or `--dest`).
   - `forge-skills list` — lists shipped skills.
   - `forge-skills export --out PATH` — writes each skill body markdown to a single bundle file for manual paste.
   - Default `--client claude`. Other clients explicitly print "manual paste required, see docs/skills.md".
   - All atomic: write-temp-then-rename via existing `_io/atomic.py`.
6. **Packaging**: extend `pyproject.toml` `[tool.hatch.build.targets.wheel]` to include `forge_mcp/skills/**/*.md` and `forge_mcp/skills/**/*.json`. CI test asserts the wheel ships the files (`unzip -l dist/*.whl | grep SKILL.md` count == 5).

## Stage B — `forge.plan` SKILL.md (load-bearing)

This is the highest-stakes file in Phase 5. Architecture §6.1 + PRD F-3.1.

1. **Frontmatter** declares `triggers` covering region creation, descriptor changes, generation requests; `requires_tools` lists every tool the skill calls; `requires_subagent: false`.
2. **Body sections** (in order):
   1. *When this skill applies* — trigger phrasing.
   2. *Structured descriptor schema* — the **entire** JSON Schema embedded inline as a fenced ` ```json` block. Generated at build time from `descriptor_json_schema()` and verified byte-identical by CI (skill is the source of truth for users; Pydantic model is the source of truth for code; the test guarantees they match).
   3. *Worked examples* — at least **10** free-text -> structured-descriptor pairs, one per `TerrainPrimary` enum value plus 1-2 stream-character variants. Each example shows the original text, the extracted JSON, and a one-line note on the trickiest bit.
   4. *Workflow* — the 9-step sequence from Architecture §6.1.
   5. *Tool call patterns* — concrete invocations: `create_region`, `generate_region`, `analyze_region`, `inspect_spec`, `render_view`, `get_descriptor_schema`, `lock_*` (mention only, full content in Phase 7), `record_audit` (only triggered after audit subagent returns).
   6. *Common pitfalls* — forgetting `hydrology` for stream descriptors; out-of-range ruggedness; conflating elevation_band low-end with sea level; misclassifying mesa vs canyon; reroll vs regenerate confusion.
   7. *Failure recovery* — what to do when `create_region` returns a polygon-overlap error, when `generate_region` exceeds NF-1.3, when descriptor validation fails.
3. **Eval set fixture**: `forge_mcp/skills/forge.plan/eval_set.json` — the 10 free-text descriptors with their canonical structured outputs. Used by the eval harness in Stage E. **Same fixture** used by the inline worked examples (single source of truth; CI asserts).
4. **Schema versioning**: skill version follows the descriptor schema version; CI fails if `descriptor.SCHEMA_VERSION` changes without the skill version bumping.

## Stage C — `forge.visualize`, `forge.cleanup`, `forge.connect`

Each one short (~150-300 lines markdown). Architecture §6.2 says these are largely unchanged from v2.0 - keep them tight, no aspirational content.

- **`forge.visualize`** — defines `view_kind` ∈ {ortho_top, perspective_se}, `resolution` ∈ {preview, default, full}, when to call `analyze_region` vs `render_view` (analysis-first, renders for verification only); cost guidance ("renders are expensive; prefer analysis").
- **`forge.cleanup`** — detection patterns for orphaned specs (no region references), stale realizations (`.blend` newer than spec referenced), conflicting locks (Phase 7 will deepen). Tool patterns: `inspect_spec`, `list_regions`, file inspection through MCP project tools. v1 calls out manual cleanup steps; auto-clean is v2.
- **`forge.connect`** — hypergraph traversal patterns: `query_layer`, `list_boundaries`, `inspect_boundary`. v1 covers containment + adjacency layers. **No canvas content** (Phase 6).

CI: same frontmatter validator as the plan skill.

## Stage D — Audit verdict schema + persistence + tools

Architecture §3 directory layout already reserves `<project>/audits/`. Phase 5 makes it real.

1. **`forge_mcp/audit/verdict.py`**:
   ```
   AuditVerdict (frozen, extra=forbid):
     audit_id: AuditId  # "audit_<blake2b6hex(canonical_json(body))>"
     schema_version: Literal["1.0"]
     region_id: RegionId
     spec_id: SpecId
     verdict: Literal["pass", "fail", "warn"]
     dimensions: tuple[AuditDimension, ...]  # one per axis below
     summary: str  # 1-3 sentences, max 500 chars
     created_at: datetime
     subagent_context: SubagentContext  # client_name, isolated, tool_calls_observed
   AuditDimension:
     name: Literal[
       "descriptor_coherence",  # extracted descriptor matches user intent
       "geometric_validity",    # mesh, polygon, scale plausible
       "render_quality",        # preview not broken/black/clipped
       "spec_alignment",        # realizer outputs honor spec params
     ]
     verdict: Literal["pass", "fail", "warn"]
     confidence: float  # 0..1
     evidence: tuple[str, ...]  # references to tool calls + observations
   SubagentContext:
     client_name: str  # "claude_code", "claude_desktop", "cursor", "inline_fallback", ...
     isolated: bool
     tool_calls_observed: tuple[str, ...]  # the audit subagent reports which tools it called
   ```
2. **`forge_mcp/audit/service.py`**: `AuditService(project_root)` with `record(verdict)` (atomic write to `audits/<region_id>/<audit_id>.json`), `list(region_id=None)`, `get(audit_id)`. Index file `audits/_index.json` holds `{audit_id: {region_id, spec_id, created_at, verdict}}` for cheap listing — rebuilt on `record`.
3. **History integration**: `record_audit` appends `HistoryEvent(kind="audit_recorded", refs={region_id, spec_id, audit_id})`.
4. **Errors**: `AuditValidationError(field, reason)` on schema mismatch; `AuditNotFoundError(audit_id)`; `RegionNotFoundError` reused.
5. **Tools** (added to `forge_mcp/server/tools/inspection.py` or new `forge_mcp/server/tools/audit.py` — pick the latter for clarity):
   - `forge.record_audit(region_id, verdict_json) -> {audit_id, path, history_event_id}`.
   - `forge.list_audits(region_id?) -> tuple[AuditSummary, ...]`.
   - `forge.get_audit(audit_id) -> AuditVerdict`.
   - `forge.get_audit_schema() -> JSON Schema` (parallel to `get_descriptor_schema`).
6. **Schema export**: `forge-schema-export` adds the audit schema to `schemas/audit_verdict.schema.json`. CI drift check covers it.

## Stage E — `forge.audit` SKILL.md

1. **Frontmatter**: `requires_subagent: true`. `triggers` includes "after generation", "after reroll", "verify region matches descriptor".
2. **Body**:
   1. *Subagent contract* — the audit subagent **must** be spawned in an isolated context with **only** these tools available: `get_region`, `inspect_spec`, `analyze_region`, `render_view`, `get_descriptor_schema`, `record_audit`, `get_audit_schema`. The skill instructs the main agent to constrain the subagent (Claude Code Task tool supports this).
   2. *Workflow* — fetch spec + analysis, render `ortho_top` preview, score four dimensions, write verdict via `record_audit`, return summary to main agent.
   3. *Verdict rubric* — concrete scoring guidance per dimension (e.g., descriptor_coherence: pass if extracted enum matches user's primary noun; warn if related; fail if unrelated).
   4. *Inline isolated-context fallback* — for clients without subagents: the main agent should "open a fresh internal scratchpad, set aside prior context, perform the audit, then return". Acknowledged as a soft convention.
   5. *Tool restrictions* — exhaustive list. The audit subagent **never** calls `generate_region`, `reroll_seed`, `create_region`, `update_region`, or any lock tool.
3. **Schema lift**: the audit `SKILL.md` embeds the `AuditVerdict` JSON Schema inline (same source-of-truth pattern as the plan skill); CI byte-identity test.

## Stage F — Skill validation + plan-skill eval harness

1. **Frontmatter + embedding tests** (`tests/skills/test_skill_files.py`):
   - Every `forge_mcp/skills/forge.*/SKILL.md` parses with valid frontmatter.
   - `forge.plan`'s embedded JSON Schema fenced block matches `descriptor_json_schema()` byte-for-byte.
   - `forge.audit`'s embedded JSON Schema matches `audit_verdict_json_schema()`.
   - Examples in `forge.plan/SKILL.md` parse as JSON and validate against the descriptor schema.
   - Tools listed in `requires_tools` exist in the registered MCP tool surface.
   - Skill version >= descriptor schema version where applicable.
2. **Plan-skill eval harness** (`scripts/eval/skill_plan_eval.py`):
   - Inputs: `forge_mcp/skills/forge.plan/eval_set.json` (canonical) + `--extractions PATH` (a JSON file the human pastes from a real agent session: `{descriptor_text: extracted_json}`).
   - Output: per-descriptor diff + aggregate score (`exact_match_count / 10`, `field_match_rate`, list of mismatched fields). Writes `docs/eval/phase5/<timestamp>/{report.md, diffs.json}`.
   - Pass threshold: 8/10 exact match (PRD R-2 mitigation; documented as the plan-skill quality bar).
   - Pure deterministic Python; no LLM calls; CI-runnable on a hand-crafted "extractions" fixture for smoke testing.
3. **CLI smoke** (`tests/skills/test_cli.py`): `forge-skills list` prints all five skills; `forge-skills install --dest <tmp>` writes 5 SKILL.md files atomically.
4. **MCP smoke** (`tests/server/test_skill_tools.py`): `list_skills`, `get_skill("forge.plan")`, `get_audit_schema`, and the round-trip `record_audit -> list_audits -> get_audit` against a tmp project.
5. **Coverage**: 90% branch on `forge_mcp/audit/`, `forge_mcp/skills/`, `forge_mcp/server/tools/audit.py` via fakes (no real subagent).

## Stage G — R-9 mid-phase sanity check (manual, doc-anchored)

PRD §10 + ROADMAP step 5 require a mid-phase sanity walkthrough against Claude Code: the user types free text, gets terrain in Blender. This is the gate that proves the skills actually shape agent behavior.

1. **Walkthrough doc** (`docs/p5_sanity_walkthrough.md`):
   - Pre-req: Claude Code installed, Blender 5.0.0 path set, `forge-skills install` run.
   - Steps: open `world_forge` MCP; new project; "draw" a region via `create_region` polygon (canvas is Phase 6); say "make this a rugged alpine valley with a small creek"; observe agent extracts descriptor + calls `generate_region`; observe `.blend` + preview returned; ask "audit this region"; observe audit subagent spawned + verdict recorded; inspect `<project>/audits/`.
   - Expected outputs at each step (recorded JSON snippets / preview thumbnail).
2. **Recording artifact**: `docs/eval/phase5/sanity/{transcript.md, descriptor.json, preview.png, audit.json}` committed under git-LFS-free convention (PNG <100 KB at 512x384).
3. **Acceptance**: walkthrough completes end-to-end on at least one machine before Phase 5 closes.
4. **Failure response**: if Claude Code doesn't reliably extract the descriptor, iterate **only the plan skill** (more examples, sharper pitfall guidance) and re-run. If still failing after 2 iterations, surface as a phase blocker — do not silently lower the bar.

## Stage H — Docs + ROADMAP close-out

1. `docs/skills.md` (new): how Forge ships skills, how each agent client loads them, manual paste fallback, audit subagent invocation contract.
2. `docs/audit.md` (new): verdict schema, dimensions, persistence layout, retrieval tools.
3. `docs/p5_verification_walkthrough.md` (parallel to existing `p4_verification_walkthrough.md`): the gate checklist for Phase 5.
4. `docs/eval/phase5/README.md`: eval-harness usage + accepted run output.
5. `AGENT/dev_phases/phase5.md` committed.
6. `AGENT/ROADMAP.md` Phase 5 marked complete in closing PR.
7. `AGENT/ARCHITECTURE.md`: append "Phase 5 measurements" — confirmed audit subagent path used (Claude Code Task vs inline fallback), eval-harness score on the canonical 10-descriptor set, list of shipped skill files + versions.

## Step ordering and dependencies
- A (skills scaffold + loader + CLI + tools shell) is the prerequisite for everything else.
- D (audit verdict + service + tools) can land in parallel with A — only `forge.get_audit_schema` couples them.
- B (forge.plan) depends on A (frontmatter validator) + descriptor schema (Phase 1+3).
- E (forge.audit) depends on A + D (audit schema embed).
- C (visualize/cleanup/connect) depends on A only; smallest scope, can interleave.
- F (validation tests + eval harness) depends on B/C/D/E being merged.
- G (sanity walkthrough) depends on F + Phase 4 realizer being callable end-to-end.
- H (docs + close-out) is last.

## Branches (one PR per concern; descriptive)
1. `skills-package-and-loader` — Stage A: directory, frontmatter schema, loader, CLI, list_skills/get_skill tools, packaging.
2. `audit-verdict-schema-and-tools` — Stage D: verdict model, AuditService, record/list/get + get_audit_schema, history integration, schema export drift.
3. `skill-plan-content` — Stage B: load-bearing forge.plan SKILL.md + eval_set.json + descriptor-schema byte-identity test.
4. `skills-visualize-cleanup-connect` — Stage C: three short skills bundled.
5. `skill-audit-content` — Stage E: forge.audit SKILL.md + audit-schema byte-identity test.
6. `plan-skill-eval-harness` — Stage F item 2: deterministic eval harness + smoke fixture + CI test.
7. `phase5-sanity-walkthrough-and-docs` — Stage G + H: manual walkthrough doc, eval artifacts, docs/skills.md, docs/audit.md, ROADMAP/Architecture updates.

## Relevant files (Phase 5 tree additions)
```
forge_mcp/
├── audit/
│   ├── __init__.py
│   ├── verdict.py        # AuditVerdict, AuditDimension, SubagentContext
│   ├── service.py        # AuditService.record/list/get + index
│   └── paths.py          # audits dir layout helpers
├── skills/
│   ├── __init__.py
│   ├── _schema.py        # SkillFrontmatter Pydantic model
│   ├── loader.py         # iter_skills, load_skill via importlib.resources
│   ├── cli.py            # forge-skills entry point
│   ├── forge.plan/
│   │   ├── SKILL.md
│   │   └── eval_set.json
│   ├── forge.visualize/SKILL.md
│   ├── forge.audit/SKILL.md
│   ├── forge.cleanup/SKILL.md
│   └── forge.connect/SKILL.md
└── server/tools/
    ├── audit.py          # NEW: record/list/get/get_audit_schema
    └── skills.py         # NEW: list_skills, get_skill
scripts/eval/
└── skill_plan_eval.py    # NEW: deterministic extraction scorer
schemas/
└── audit_verdict.schema.json  # NEW (generated)
tests/
├── audit/
│   ├── test_verdict.py
│   ├── test_service.py
│   └── test_history.py
├── skills/
│   ├── test_skill_files.py    # frontmatter + schema byte-identity
│   ├── test_loader.py
│   └── test_cli.py
├── server/
│   ├── test_skill_tools.py
│   └── test_audit_tools.py
└── eval/
    └── test_skill_plan_eval.py
docs/
├── skills.md
├── audit.md
├── p5_sanity_walkthrough.md
├── p5_verification_walkthrough.md
└── eval/phase5/...
```

## Verification (Phase 5 gate)
1. All seven branches merged; CI green on `main`.
2. `pytest --cov=forge_mcp --cov-fail-under=90 --cov-branch` exits 0; band 90-95%.
3. `forge-schema-export --check` exits 0 (audit verdict schema published; descriptor schema unchanged).
4. `mypy` strict exits 0 on host code (no new ignores).
5. **Skill byte-identity**: `forge.plan`'s embedded descriptor schema matches `descriptor_json_schema()`; `forge.audit`'s embedded audit schema matches `audit_verdict_json_schema()`. Asserted in CI.
6. **Skill packaging**: built wheel contains all 5 SKILL.md files (CI test).
7. **CLI smoke**: `forge-skills install --dest <tmp>` writes 5 skill folders atomically; idempotent on re-run with `--force`.
8. **MCP tool round-trip**: `record_audit` -> `list_audits` -> `get_audit` returns identical verdict on a tmp project; `record_audit` rejects malformed JSON with `AuditValidationError`.
9. **Plan-skill eval (manual)**: human runs Claude Code session against the 10-descriptor fixture; pastes extractions into the eval harness; harness reports >=8/10 exact match. Result committed under `docs/eval/phase5/<timestamp>/`.
10. **R-9 sanity walkthrough (manual)**: `docs/p5_sanity_walkthrough.md` completed end-to-end on Claude Code with terrain rendered, audit subagent spawned, verdict persisted under `audits/`. Transcript + artifacts committed.
11. **Strictness audit**: zero new `# type: ignore` without code; zero `# noqa` without code+reason; no LLM client imports anywhere in `forge_mcp/` (CI grep for `openai|anthropic|httpx.*completions`).
12. **No-LLM invariant** (Architecture §15) preserved: `grep -rn 'subprocess\|requests\|httpx' forge_mcp/audit/ forge_mcp/skills/` returns nothing that calls outbound to a model provider; CI test asserts.

## Decisions baked in
- **Audit subagent runs in the agent client, never in Forge.** Forge ships the skill + verdict schema + record tools. No exception.
- **Plan-skill schema embedding is generated from the Pydantic model at build time** (build hook on the wheel, plus CI byte-identity test). Hand-editing the embedded block fails CI.
- **Five skills shipped in v1**, exactly as PRD §6.3 names them. No extras.
- **Audit verdict has four fixed dimensions**: descriptor_coherence, geometric_validity, render_quality, spec_alignment. Not extensible in v1.
- **Audit verdict schema version 1.0**; schema-version evolution policy documented.
- **`forge-skills install` defaults to Claude Code path** (`~/.claude/skills/`). Other clients require manual paste; documented.
- **Eval harness is deterministic Python**; the LLM is *not* in CI. The harness scores recorded extractions only.
- **R-9 sanity walkthrough is a hard gate**, not a soft suggestion. If Claude Code can't reliably drive the loop, the plan skill iterates before the phase closes.
- **Skill markdown is **not** mypy-checked**, but its frontmatter and embedded JSON are validated by unit tests.
- **Audits live under `<project>/audits/<region_id>/audit_NNNN.json`** with a per-project `_index.json` cache; layout matches Architecture §3.
- **Audit invalidates nothing.** A failing audit recorded a verdict; it doesn't auto-trigger reroll. The user/agent decides next steps.

## Confirmed decisions (2026-05-06)
1. **Audit subagent targeting**: Claude Code's Task subagent is the primary path; inline isolated-context fallback is documented in `forge.audit/SKILL.md` for Cursor/Copilot. No client-specific installers beyond Claude.
2. **Plan-skill eval pass threshold**: 8/10 exact match on the canonical 10-descriptor `eval_set.json`. Documented in `docs/skills.md` as the plan-skill quality bar.
3. **Skill installer**: ship `forge-skills` CLI in Phase 5 (Stage A), defaulting `install` to `~/.claude/skills/`. Atomic writes via `_io/atomic.py`; `--force` for idempotent re-install.

## Open questions to confirm with user
1. **Audit subagent scope**: ship targeting Claude Code's Task subagent as primary (recommended; the only client today with a clean isolated-context primitive) and document inline-fallback for Cursor/Copilot, vs. attempt parity across all three from day 1 (more skill content, harder to verify).
2. **Plan-skill eval pass threshold**: 8/10 exact match (recommended; matches PRD R-2 mitigation language) vs. 9/10 (tighter; risks gating phase on agent variance) vs. field-match-rate >= 0.9 (softer; allows minor enum near-misses).
3. **Skill installer scope**: ship `forge-skills` CLI now (recommended; one-command setup for Claude Code) vs. docs-only with copy-paste instructions (smaller surface, but adds friction to every new install).
