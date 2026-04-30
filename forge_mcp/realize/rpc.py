"""JSON-RPC 2.0 framing and client (line-delimited over duplex streams)."""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from threading import Lock
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

# Recursive JSON value type. Defined locally to keep the realize module
# self-contained; will eventually share with descriptor.JsonValue.
type JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]


class RpcProtocolError(RuntimeError):
    """Raised when the peer violates the JSON-RPC framing contract."""


@dataclass(frozen=True, slots=True)
class RpcError(Exception):
    """JSON-RPC 2.0 error payload returned by the peer."""

    code: int
    message: str
    data: JsonValue | None = None

    def __str__(self) -> str:
        """Render the error as ``RpcError(<code>): <message>``."""
        return f"RpcError({self.code}): {self.message}"


@dataclass(frozen=True, slots=True)
class RpcRequest:
    """A JSON-RPC 2.0 request envelope."""

    method: str
    params: JsonObject
    id: int

    def to_json(self) -> str:
        """Serialize to a single-line JSON string (no trailing newline)."""
        return json.dumps(
            {"jsonrpc": "2.0", "id": self.id, "method": self.method, "params": self.params},
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class RpcResponse:
    """A JSON-RPC 2.0 response envelope."""

    id: int | None
    result: JsonValue | None
    error: RpcError | None

    @classmethod
    def from_json(cls, line: str) -> RpcResponse:
        """Parse one line of JSON. Raises :class:`RpcProtocolError` on bad framing."""
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            msg = f"non-JSON RPC frame: {line!r}"
            raise RpcProtocolError(msg) from exc
        if not isinstance(payload, dict):
            msg = f"RPC frame is not an object: {payload!r}"
            raise RpcProtocolError(msg)
        if payload.get("jsonrpc") != "2.0":
            msg = f"bad jsonrpc version: {payload.get('jsonrpc')!r}"
            raise RpcProtocolError(msg)
        rid_raw = payload.get("id")
        rid = rid_raw if isinstance(rid_raw, int) else None
        err_raw = payload.get("error")
        if err_raw is not None:
            if not isinstance(err_raw, dict):
                msg = f"RPC error not an object: {err_raw!r}"
                raise RpcProtocolError(msg)
            return cls(
                id=rid,
                result=None,
                error=RpcError(
                    code=int(err_raw.get("code", 0)),
                    message=str(err_raw.get("message", "")),
                    data=err_raw.get("data"),
                ),
            )
        return cls(id=rid, result=payload.get("result"), error=None)


class RpcClient:
    """Thread-safe synchronous JSON-RPC 2.0 client over duplex line streams.

    The client is intentionally tiny: it does not do batching, notifications,
    or out-of-order replies. The Blender adapter responds in-order, and v1
    callers are synchronous.
    """

    def __init__(self, stdin: IO[str], stdout: IO[str]) -> None:
        """Create a client bound to a writable stdin and a readable stdout."""
        self._stdin = stdin
        self._stdout = stdout
        self._lock = Lock()
        self._ids: Iterator[int] = itertools.count(1)

    def call(self, method: str, params: JsonObject | None = None) -> JsonValue:
        """Send a request and block on the response. Raises on RPC error."""
        with self._lock:
            req = RpcRequest(method=method, params=params or {}, id=next(self._ids))
            self._stdin.write(req.to_json() + "\n")
            self._stdin.flush()
            line = self._stdout.readline()
            if not line:
                msg = "RPC peer closed stdout before responding"
                raise RpcProtocolError(msg)
            resp = RpcResponse.from_json(line)
            if resp.error is not None:
                raise resp.error
            if resp.id is not None and resp.id != req.id:
                msg = f"RPC id mismatch: sent {req.id}, got {resp.id}"
                raise RpcProtocolError(msg)
            return resp.result
