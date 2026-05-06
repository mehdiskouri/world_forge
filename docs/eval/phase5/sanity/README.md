# Sanity walkthrough artifacts

This directory is reserved for the recorded outputs of the Phase 5
R-9 sanity walkthrough described in
[`docs/p5_sanity_walkthrough.md`](../../p5_sanity_walkthrough.md).

## Status

> **Pending operator run.** This walkthrough requires a real
> Claude Code session against a Blender 5.0.0 binary and cannot be
> performed inside the unit-test CI agent that produced the
> close-out PR.

The four artifacts below land here after an operator runs the
walkthrough on a development workstation. Each MUST be committed
before [`AGENT/ROADMAP.md`](../../../AGENT/ROADMAP.md) marks Phase 5
complete:

| Artifact          | Source                                                                  | Constraint              |
| ----------------- | ----------------------------------------------------------------------- | ----------------------- |
| `transcript.md`   | The Claude Code session transcript                                      | UTF-8 markdown          |
| `descriptor.json` | The descriptor the agent extracted from "rugged alpine valley with creek" | matches the canonical `alpine_valley_with_creek` row of `eval_set.json` |
| `preview.png`     | The PNG returned by `forge.generate_region`                             | < 100 KB at 512×384     |
| `audit.json`      | The `AuditVerdict` recorded under `audits/<region_id>/<audit_id>.json` | validates against `audit_verdict_json_schema()` |

Optionally — but strongly recommended — also commit
`extractions.json` (the full 12-descriptor extraction set fed to
`scripts/eval/skill_plan_eval.py`) plus the resulting
`docs/eval/phase5/<UTC-timestamp>/report.md` so the plan-skill score
is reproducible from git history alone.

The acceptance checklist for the run lives in
[`docs/p5_sanity_walkthrough.md`](../../p5_sanity_walkthrough.md) §5.
