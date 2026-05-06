# Verifying Phase 5 (skills + audit subagent)

This walkthrough is the wire-level companion to the manual sanity
walkthrough in [`docs/p5_sanity_walkthrough.md`](p5_sanity_walkthrough.md).
It enumerates the 12 gates listed in
[`AGENT/dev_phases/phase5.md`](../AGENT/dev_phases/phase5.md)
§"Verification" and shows the exact commands an operator runs to
confirm each.

The flow mirrors `p4_verification_walkthrough.md`: install gates →
schema-export drift → skill packaging → MCP tool round-trip →
sanity → docs.

---

## 0. Prerequisites

Same as [`docs/p5_sanity_walkthrough.md`](p5_sanity_walkthrough.md) §0.
Most of this checklist runs without Blender or Claude Code
(automated CI parity); the sanity walkthrough is the gate that
requires both.

---

## 1. Install + automated gates (CI parity)

```bash
git clone https://github.com/mehdiskouri/world_forge.git
cd world_forge
uv sync
uv run pre-commit install
```

Run the same commands CI runs:

```bash
uv run ruff check .                                                          # gate 4
uv run ruff format --check .                                                 # gate 4
uv run mypy                                                                  # gate 4
uv run pytest -q --cov=forge_mcp --cov-branch --cov-fail-under=90            # gates 1, 2, 5, 6, 8
uv run forge-schema-export --check                                           # gate 3
```

Expected outcomes:

- `ruff` clean (no widened ignores in `pyproject.toml`).
- `mypy` strict, 0 issues across all source files.
- `pytest` passes; coverage in the 90–95 % band; the audit /
  skills tests exercise the byte-identity assertions (gate 5).
- `forge-schema-export --check` confirms `schemas/audit_verdict.schema.json`
  is in sync with `audit_verdict_json_schema()` (gate 3).

---

## 2. Skill packaging (gate 6)

The shipped wheel must contain all five `SKILL.md` files plus the
plan-skill eval fixture. Confirm against a built wheel:

```bash
uv build                                                                     # writes dist/forge_mcp-*.whl
unzip -l dist/forge_mcp-*.whl | grep -E "SKILL\\.md|eval_set\\.json"
# expect 5x SKILL.md + 1x eval_set.json
```

The same assertion runs in CI via the unit tests under
[`tests/skills/`](../tests/skills/).

---

## 3. CLI smoke (gate 7)

```bash
uv run forge-skills list
# alpha-sorted listing of the 5 skills with version + asset count

uv run forge-skills install --dest /tmp/forge-skills-test
ls /tmp/forge-skills-test                  # → 5 directories

uv run forge-skills install --dest /tmp/forge-skills-test            # idempotent: skips up-to-date
uv run forge-skills install --dest /tmp/forge-skills-test --force    # overwrites
```

Each `install` is atomic per skill (staging dir → `os.replace`),
backed by `forge_mcp._io.atomic`.

---

## 4. MCP tool round-trip (gate 8)

The automated test
[`tests/server/test_audit_tools.py`](../tests/server/test_audit_tools.py)
exercises this end-to-end against a tmp project, but the same
sequence is reachable from any MCP-capable host. From a Claude Code
session connected to `forge-mcp`:

```text
forge.create_project(path="/tmp/p5_sanity", name="audit roundtrip")
forge.open_project(path="/tmp/p5_sanity")
forge.create_region(region_id="rgn_x", polygon=[[-1,-1],[1,-1],[1,1],[-1,1]])

# Read the schema the subagent must conform to
forge.get_audit_schema()
  -> {"schema_version": "1.0", "schema": {... AuditVerdict JSON Schema ...}}

# Record a verdict (subagent does this in production)
forge.record_audit(region_id="rgn_x", verdict={... see docs/audit.md §6 ...})
  -> {"audit_id": "audit_<hex>", "stored_path": "audits/rgn_x/audit_<hex>.json"}

# List + retrieve
forge.list_audits(region_id="rgn_x")
forge.get_audit(region_id="rgn_x", audit_id="audit_<hex>")
```

Malformed verdicts surface as a structured `AuditValidationError`
envelope; missing verdicts as `AuditNotFoundError`. Both are
covered by the unit tests.

---

## 5. Plan-skill eval (gate 9, manual recording)

Follow [`docs/p5_sanity_walkthrough.md`](p5_sanity_walkthrough.md) §4.
Pass threshold: `exact_match_count >= 8`. Result is committed under
`docs/eval/phase5/<UTC-timestamp>/{report.md,diffs.json}`.

The harness itself is exercised in CI by
[`tests/eval/test_skill_plan_eval.py`](../tests/eval/test_skill_plan_eval.py)
(deterministic Python only; no LLM calls).

---

## 6. R-9 sanity walkthrough (gate 10, manual)

Follow [`docs/p5_sanity_walkthrough.md`](p5_sanity_walkthrough.md)
§§ 2-5 end-to-end. Commit the resulting transcript and artifacts
under [`docs/eval/phase5/sanity/`](eval/phase5/sanity/).

---

## 7. Strictness audit (gate 11)

```bash
# No ignored ruff codes without explicit reasons.
git grep -nE "noqa(:)? *$" -- "*.py" || echo "OK: every noqa carries a code"
git grep -nE "type: ignore *$" -- "*.py" || echo "OK: every type:ignore carries a code"

# No outbound LLM-client imports anywhere in forge_mcp/
git grep -nE "openai|anthropic|httpx.*completions" -- "forge_mcp/"
# expect: nothing
```

The Architecture §15 invariant ("Forge never calls an LLM") is also
asserted at unit-test scope in
[`tests/test_smoke.py`](../tests/test_smoke.py).

---

## 8. No-LLM invariant (gate 12)

```bash
git grep -nE "subprocess|requests|httpx" -- forge_mcp/audit/ forge_mcp/skills/
# expect: nothing that calls outbound to a model provider
```

The audit and skills packages are pure data-shaping: no network, no
subprocess, no model dispatch. The realizer (`forge_mcp/realize/`)
spawns Blender via `subprocess`, but Blender itself does no model
inference.

---

## 9. Phase 5 close-out

When every gate above is green and the sanity transcript is
committed, mark Phase 5 complete in
[`AGENT/ROADMAP.md`](../AGENT/ROADMAP.md) and record the
measurements in
[`AGENT/ARCHITECTURE.md`](../AGENT/ARCHITECTURE.md) §"Phase 5
measurements" — the closing PR for `phase5-sanity-walkthrough-and-docs`.
