# Phase 5 evaluation artifacts

This directory holds the recorded outputs of the Phase 5 evaluation
harness ([`scripts/eval/skill_plan_eval.py`](../../../scripts/eval/skill_plan_eval.py))
and the manual R-9 sanity walkthrough.

## Layout

```
docs/eval/phase5/
├── README.md                      # this file
├── sanity/                        # the manual walkthrough artifacts
│   ├── README.md                  # status of the manual gate
│   ├── transcript.md              # session transcript (committed by operator)
│   ├── descriptor.json            # extracted descriptor for the demo region
│   ├── preview.png                # < 100 KB at 512×384
│   └── audit.json                 # the recorded AuditVerdict
└── <UTC-timestamp>/               # one directory per harness run
    ├── report.md                  # Markdown summary
    └── diffs.json                 # structured per-descriptor diff
```

## Running the harness

The harness is **deterministic Python**. It compares pre-recorded
free-text → descriptor extractions (pasted by the operator from a
real Claude Code session) against the canonical fixture
[`forge_mcp/skills/forge.plan/eval_set.json`](../../../forge_mcp/skills/forge.plan/eval_set.json).
It does not call any LLM.

```bash
uv run python scripts/eval/skill_plan_eval.py \
    --extractions docs/eval/phase5/sanity/extractions.json \
    --out docs/eval/phase5/$(date -u +%Y%m%dT%H%M%SZ)/
```

`extractions.json` shape:

```json
{
  "extractions": {
    "<free_text_string_from_eval_set>": { ...descriptor JSON... }
  }
}
```

## Pass threshold

`exact_match_count >= 8` out of the canonical 10+-descriptor fixture
(PRD R-2 mitigation). The harness exits `0` on pass, `1` on fail.
The threshold lives as `PASS_THRESHOLD` in the harness module and is
referenced from [`docs/skills.md`](../../skills.md) §4.

## Accepted run output

A "pass" run produces:

- `report.md` — human-readable summary with `Verdict: **PASS**` and
  per-example field-level match details.
- `diffs.json` — structured payload (`{passed: true, exact_match_count,
  pass_threshold, results: [...]}`) that downstream tooling can diff
  across runs.

The schema of `diffs.json` is implicit (see
`render_diffs_json` in
[`scripts/eval/skill_plan_eval.py`](../../../scripts/eval/skill_plan_eval.py));
the smoke tests in
[`tests/eval/test_skill_plan_eval.py`](../../../tests/eval/test_skill_plan_eval.py)
exercise the round-trip.
