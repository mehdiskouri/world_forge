.PHONY: eval perf integration

# Render the Phase-3 acceptance contact sheet under
# docs/eval/phase3/<UTC-timestamp>/. Local-only; not part of CI.
eval:
	uv run python scripts/eval/render_eval_set.py

# Run the Blender-host integration suite (skips slow perf tests).
# Requires FORGE_BLENDER_BIN to point at a real Blender 5.0.0 binary.
integration:
	./scripts/run_integration.sh

# Run the slow Blender-host budget smokes (NF-1 generate/render budgets).
# Requires FORGE_BLENDER_BIN to point at a real Blender 5.0.0 binary.
perf:
	./scripts/run_perf.sh
