"""Host-side realizer plumbing (Phase 1 spike 2).

Spawns a Blender 5.0 subprocess running ``scripts/blender/adapter.py``
and provides a tiny synchronous JSON-RPC client (:class:`RpcClient`) and
a context manager (:class:`BlenderProcess`) that owns the subprocess
lifecycle.

Phase 1 only validates the shape; the realizer engine itself
(ARCHITECTURE §5.7) lives in Phase 4.
"""

from forge_mcp.realize.blender_proc import (
    BLENDER_BIN_ENV,
    BlenderNotConfiguredError,
    BlenderProcess,
    blender_binary,
)
from forge_mcp.realize.rpc import (
    RpcClient,
    RpcError,
    RpcProtocolError,
    RpcRequest,
    RpcResponse,
)

__all__ = [
    "BLENDER_BIN_ENV",
    "BlenderNotConfiguredError",
    "BlenderProcess",
    "RpcClient",
    "RpcError",
    "RpcProtocolError",
    "RpcRequest",
    "RpcResponse",
    "blender_binary",
]
