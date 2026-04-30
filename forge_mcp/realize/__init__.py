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
    RpcMethods,
    RpcProtocolError,
    RpcRequest,
    RpcResponse,
)

# Note: ``forge_mcp.realize.engine`` is intentionally not re-exported here
# to avoid an import cycle: ``engine`` imports
# ``forge_mcp.bpy_hypergraph.sequences``, which imports ``RpcMethods`` from
# this package. Callers should ``from forge_mcp.realize.engine import ...``.

__all__ = [
    "BLENDER_BIN_ENV",
    "BlenderNotConfiguredError",
    "BlenderProcess",
    "RpcClient",
    "RpcError",
    "RpcMethods",
    "RpcProtocolError",
    "RpcRequest",
    "RpcResponse",
    "blender_binary",
]
