# Forge skills (v1)

This document is the operator-facing reference for the five skills
shipped with Forge in Phase 5. Skills are static markdown files
authored to shape an MCP-capable agent's behaviour against the Forge
tool surface; Forge itself never invokes an LLM (Architecture §15).

For the **why**, see PRD §6 ("Skill model"). For the **delivery
contract**, see Architecture §6.

---

## 0. The five skills

| Skill            | Purpose                                                                | Subagent | Asset(s)              |
| ---------------- | ---------------------------------------------------------------------- | -------- | --------------------- |
| `forge.plan`     | Free-text → structured `Descriptor` extraction                         | no       | `eval_set.json`       |
| `forge.visualize`| Choose `render_view` parameters; interpret returned previews           | no       | —                     |
| `forge.audit`    | Run a self-audit subagent and record an `AuditVerdict`                 | **yes**  | —                     |
| `forge.cleanup`  | Reroll seeds, delete regions, prune realizations                       | no       | —                     |
| `forge.connect`  | Inspect adjacency / lock state across regions                          | no       | —                     |

All five live under `forge_mcp/skills/<name>/SKILL.md` in the wheel and
are exposed through the MCP surface (`forge.list_skills`,
`forge.get_skill`) as well as the `forge-skills` CLI.

---

## 1. Frontmatter contract

Every `SKILL.md` starts with a YAML-like fenced frontmatter block,
parsed by [`forge_mcp/skills/_schema.py`](../forge_mcp/skills/_schema.py)
(`SkillFrontmatter`). Required keys:

- `name` — fully-qualified skill name (`forge.plan`, …).
- `version` — semantic version. The plan and audit skills track the
  version of their embedded JSON Schema (descriptor / verdict).
- `description` — one-sentence purpose statement; surfaced verbatim
  by `forge.list_skills`.
- `requires_tools` — list of MCP tool names the skill instructs the
  agent to call. CI asserts every name is registered in
  `forge_mcp.server.mcp.build_server()`.

Optional keys:

- `requires_subagent: true` — must be set on `forge.audit` (and only
  on `forge.audit`) to signal that the skill needs a clean-context
  child agent (Architecture §14.6).

CI rejects unknown keys. Hand-editing the embedded JSON Schema
fenced blocks in `forge.plan` or `forge.audit` is rejected by the
byte-identity tests
([`tests/skills/test_plan_skill.py`](../tests/skills/test_plan_skill.py),
[`tests/skills/test_audit_skill.py`](../tests/skills/test_audit_skill.py));
edit the Pydantic model and re-export instead.

---

## 2. How each agent client loads skills

| Client          | Loader                                                                            |
| --------------- | --------------------------------------------------------------------------------- |
| Claude Code     | drop `SKILL.md` files under `~/.claude/skills/<name>/`; the CLI auto-discovers    |
| Claude Desktop  | paste a skill body into the system prompt (manual; same content)                  |
| Cursor          | copy into `.cursor/rules/<name>.md`; `requires_tools` declared in the rule header |
| Inline fallback | any host: paste the skill body into the first message of the session              |

The shipped `forge-skills install --dest <path>` (default
`~/.claude/skills/`) writes one folder per skill atomically. Re-runs
without `--force` skip up-to-date folders; with `--force` they are
replaced via `_io.atomic.atomic_write_text`.

The MCP surface exposes the same content for clients that prefer to
fetch it at session start:

```text
forge.list_skills() -> [{"name": "forge.plan", "version": "1.0.0", "description": ...}, ...]
forge.get_skill("forge.plan") -> {"frontmatter": {...}, "body_markdown": "...", "embedded_assets": {...}}
```

---

## 3. Audit subagent invocation contract

Stage E of Phase 5 makes the audit loop concrete. The contract:

1. The user (or a higher-level skill) asks the agent to audit a region.
2. The agent loads `forge.audit` (preferring a Claude Code Task
   subagent; falls back to an inline isolated context on Cursor /
   Copilot — see `forge.audit/SKILL.md` §3).
3. The subagent calls **read-only** Forge tools only:
   `forge.get_region`, `forge.list_regions`, `forge.get_audit_schema`,
   `forge.list_audits`, `forge.get_audit`, `forge.render_view`.
4. It scores the region across the four fixed
   [`AuditDimension`](../forge_mcp/audit/verdict.py)s
   (descriptor_coherence, geometric_validity, render_quality,
   spec_alignment) on the three-valued `pass / warn / fail` scale.
5. It posts the verdict back via `forge.record_audit`, which validates
   against `AuditVerdict`, computes the deterministic
   `audit_id = "audit_" + blake2b(canonical_body, digest_size=6).hex()`,
   and persists under
   `<project>/audits/<region_id>/<audit_id>.json`.
6. The persistence step appends a `HistoryEventKind.AUDIT_RECORDED`
   event to the project history.

The subagent MUST NOT call mutating tools (`generate_region`,
`reroll_seed`, `update_region`, `delete_region`); the coherence test
in `tests/skills/test_audit_skill.py` asserts the absence of every
such name in `forge.audit/SKILL.md`'s `requires_tools`.

---

## 4. Plan-skill quality bar

The `forge.plan` skill is the load-bearing one — it is the bridge
between free user prose and the typed `Descriptor` Pydantic model. Its
quality bar is defined by the canonical fixture
[`forge_mcp/skills/forge.plan/eval_set.json`](../forge_mcp/skills/forge.plan/eval_set.json),
shared between the inline worked examples in `SKILL.md` and the
deterministic eval harness
[`scripts/eval/skill_plan_eval.py`](../scripts/eval/skill_plan_eval.py).

**Pass threshold: ≥ 8 / 10 exact descriptor matches.** This is the
PRD R-2 mitigation level. The harness reports exact-match count plus
field-level match rate and writes
`docs/eval/phase5/<UTC-timestamp>/{report.md, diffs.json}`. The
harness itself is pure Python; it does not call any LLM. The operator
records extractions from a real Claude Code (or other) session into a
JSON file and feeds it to the harness:

```bash
uv run python scripts/eval/skill_plan_eval.py \
    --extractions docs/eval/phase5/sanity/extractions.json \
    --out docs/eval/phase5/<UTC-timestamp>/
```

Below 8/10 the failure response is documented in
[`AGENT/dev_phases/phase5.md`](../AGENT/dev_phases/phase5.md) Stage G:
iterate the plan skill (more examples, sharper pitfalls), re-run, and
escalate as a phase blocker after two iterations.

---

## 5. CLI reference

```bash
# List shipped skills with version + asset count
uv run forge-skills list

# Install all five into Claude Code's skills directory
uv run forge-skills install                                   # default ~/.claude/skills/
uv run forge-skills install --dest /tmp/skills                # explicit destination
uv run forge-skills install --dest /tmp/skills --force        # overwrite existing
```

`install` is atomic per skill: the destination is staged as
`<name>.tmp`, then `os.replace`d into place only after every file
within it has been written.

---

## 6. Versioning policy

- `forge.plan/SKILL.md` version tracks the embedded
  `descriptor_json_schema()` major+minor.
- `forge.audit/SKILL.md` version tracks the embedded
  `audit_verdict_json_schema()` (currently `1.0`); the
  `requires_subagent: true` flag is permanent for v1.
- The three short skills (`visualize`, `cleanup`, `connect`) version
  independently from any schema; bump on body content changes.

CI enforces version coherence in
[`tests/skills/test_plan_skill.py`](../tests/skills/test_plan_skill.py)
and [`tests/skills/test_audit_skill.py`](../tests/skills/test_audit_skill.py).
