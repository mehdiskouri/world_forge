"""End-to-end integration tests that drive a real Blender 5.0 process.

All tests in this package are tagged with the ``blender_integration``
pytest marker and gated on ``$FORGE_BLENDER_BIN`` pointing at a real
Blender 5.0.0 binary; without it they are skipped (so the default
``pytest`` invocation in CI stays headless / hermetic).
"""
