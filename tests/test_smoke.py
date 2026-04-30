"""Phase 0 smoke test: package importable and exposes a version string."""

from __future__ import annotations

import forge_mcp


def test_version_is_nonempty_string() -> None:
    assert isinstance(forge_mcp.__version__, str)
    assert forge_mcp.__version__ == "0.0.0"


def test_package_exports_only_version() -> None:
    assert forge_mcp.__all__ == ["__version__"]
