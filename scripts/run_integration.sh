#!/usr/bin/env bash
# Run the Blender-host integration suite. Refuses to start without
# FORGE_BLENDER_BIN pointing at a real Blender 5.0.0 binary so it
# fails loudly instead of silently skipping every test.
set -euo pipefail
: "${FORGE_BLENDER_BIN:?must point at a Blender 5.0.0 binary}"
exec uv run pytest tests/integration -m "not slow" "$@"
