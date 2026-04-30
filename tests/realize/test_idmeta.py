"""Tests for the thin idmeta wrapper around set_idprop / get_idprop RPCs."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from forge_mcp.realize import idmeta

if TYPE_CHECKING:
    from forge_mcp.realize.rpc import RpcClient


class _ScriptedClient:
    def __init__(self, scripted: list[object]) -> None:
        self._scripted = scripted
        self.calls: list[tuple[str, object]] = []

    def call(self, method: str, params: object = None) -> object:
        self.calls.append((method, params))
        return self._scripted.pop(0)


def test_set_idprop_forwards_payload_unmodified() -> None:
    fake = _ScriptedClient([None])
    idmeta.set_idprop(
        cast("RpcClient", fake),
        collection="objects",
        name="terrain",
        key="forge_kind",
        value="terrain_mesh",
    )
    assert fake.calls == [
        (
            "set_idprop",
            {
                "collection": "objects",
                "name": "terrain",
                "key": "forge_kind",
                "value": "terrain_mesh",
            },
        ),
    ]


def test_get_idprop_returns_underlying_rpc_value() -> None:
    sentinel = 42
    fake = _ScriptedClient([sentinel])
    out = idmeta.get_idprop(
        cast("RpcClient", fake),
        collection="objects",
        name="terrain",
        key="forge_node_id",
    )
    assert out == sentinel
    assert fake.calls == [
        (
            "get_idprop",
            {"collection": "objects", "name": "terrain", "key": "forge_node_id"},
        ),
    ]
