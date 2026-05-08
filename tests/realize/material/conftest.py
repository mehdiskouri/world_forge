"""Pytest fixtures for adapter shader-builder unit tests.

The ``adapter`` fixture installs a fresh :class:`._bpy_fake.FakeBpy`
in ``sys.modules`` *before* importing
``scripts/blender/adapter.py``, returning ``(adapter_module, fake_bpy)``
so tests can call ``adapter._build_pbr_layered(...)`` directly and
inspect the recorded node graph.

Each test gets a fresh fake module (the ``adapter`` is also reloaded)
so node-counter state cannot leak between tests.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from tests.realize.material._bpy_fake import (
    FakeBpy,
    install_fake_bpy,
    load_adapter_module,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType


@pytest.fixture
def adapter() -> Iterator[tuple[ModuleType, FakeBpy]]:
    """Yield ``(adapter_module, fake_bpy)`` for one test.

    The fake ``bpy`` is installed before the adapter import so the
    adapter binds to the fake. Both are torn down after the test so a
    subsequent test starts with a fresh ``sys.modules['bpy']`` and a
    fresh adapter module.
    """
    fake = install_fake_bpy()
    module = load_adapter_module()
    try:
        yield module, fake
    finally:
        sys.modules.pop("forge_adapter_under_test", None)
        sys.modules.pop("bpy", None)
