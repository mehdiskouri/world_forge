"""Thin wrapper around the adapter's ``set_idprop`` / ``get_idprop`` RPCs.

Phase 1 spike 2 verdict was that real Blender IDProperties are
expressive enough to carry the forge metadata we need
(forge_node_id / forge_spec_id / forge_kind), so this module is a
deliberately thin facade over the existing
:py:attr:`forge_mcp.realize.RpcMethods.SET_IDPROP` and
:py:attr:`forge_mcp.realize.RpcMethods.GET_IDPROP` calls — no
serialisation, no schema layering, no fallback.

The realizer macros use it directly (rather than each macro re-typing
the param dict) so any future change to the IDProperty wire shape is
contained to one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_mcp.realize.rpc import RpcMethods

if TYPE_CHECKING:
    from forge_mcp.realize.rpc import JsonValue, RpcClient


def set_idprop(
    client: RpcClient,
    *,
    collection: str,
    name: str,
    key: str,
    value: JsonValue,
) -> None:
    """Set a single IDProperty on a Blender datablock via the adapter RPC."""
    client.call(
        RpcMethods.SET_IDPROP,
        {"collection": collection, "name": name, "key": key, "value": value},
    )


def get_idprop(
    client: RpcClient,
    *,
    collection: str,
    name: str,
    key: str,
) -> JsonValue:
    """Read a single IDProperty from a Blender datablock via the adapter RPC."""
    return client.call(
        RpcMethods.GET_IDPROP,
        {"collection": collection, "name": name, "key": key},
    )
