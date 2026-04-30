.PHONY: eval perf

# Render the Phase-3 acceptance contact sheet under
# docs/eval/phase3/<UTC-timestamp>/. Local-only; not part of CI.
eval:
	uv run python scripts/eval/render_eval_set.py

# Phase-3 perf gate is local-only by design (NF-1.2 is runner-sensitive).
# Today this is a placeholder; Phase 4 may grow a real benchmark harness.
perf:
	@echo "perf gate: see AGENT/dev_phases/phase3.md (Stage H step 8)."
