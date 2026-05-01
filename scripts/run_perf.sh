#!/usr/bin/env bash
# Run the slow Blender-host integration tests (NF-1 budget smokes).
set -euo pipefail
: "${FORGE_BLENDER_BIN:?must point at a Blender 5.0.0 binary}"
exec uv run pytest tests/integration -m "slow" "$@"
