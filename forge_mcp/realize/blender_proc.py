"""Subprocess-based Blender process owning the adapter lifecycle."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Self

from forge_mcp.realize.rpc import RpcClient

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import TracebackType

#: Environment variable that points at a Blender 5.0 binary.
BLENDER_BIN_ENV = "FORGE_BLENDER_BIN"

#: Path to ``scripts/blender/adapter.py`` relative to the repo root.
_ADAPTER_RELPATH = Path("scripts/blender/adapter.py")


class BlenderNotConfiguredError(RuntimeError):
    """Raised when ``$FORGE_BLENDER_BIN`` is unset or the binary is missing."""


def blender_binary() -> Path:
    """Return the configured Blender binary path or raise.

    Raises:
        BlenderNotConfiguredError: ``$FORGE_BLENDER_BIN`` is unset or the
            referenced path does not exist.
    """
    raw = os.environ.get(BLENDER_BIN_ENV)
    if not raw:
        msg = f"{BLENDER_BIN_ENV} is not set"
        raise BlenderNotConfiguredError(msg)
    path = Path(raw)
    if not path.exists():
        msg = f"{BLENDER_BIN_ENV}={raw} does not exist"
        raise BlenderNotConfiguredError(msg)
    return path


def _adapter_script() -> Path:
    here = Path(__file__).resolve()
    # forge_mcp/realize/blender_proc.py -> repo_root
    repo_root = here.parent.parent.parent
    return repo_root / _ADAPTER_RELPATH


class BlenderProcess:
    """Owns a Blender subprocess running the JSON-RPC adapter.

    Use as a context manager::

        with BlenderProcess() as proc:
            proc.client.call("ping")
    """

    def __init__(self, blender: Path | None = None, adapter: Path | None = None) -> None:
        """Configure the process. The subprocess starts on :py:meth:`start`."""
        self._blender = blender or blender_binary()
        self._adapter = adapter or _adapter_script()
        self._proc: subprocess.Popen[str] | None = None
        self._client: RpcClient | None = None

    @property
    def client(self) -> RpcClient:
        """Return the RPC client. Raises if the process is not running."""
        if self._client is None:
            msg = "BlenderProcess not started; use as a context manager"
            raise RuntimeError(msg)
        return self._client

    def start(self) -> None:
        """Spawn the Blender subprocess and wire up the RPC client."""
        if self._proc is not None:
            msg = "BlenderProcess already started"
            raise RuntimeError(msg)
        cmd = [
            str(self._blender),
            "--background",
            "--python",
            str(self._adapter),
        ]
        # Capture stderr to a separate stream so Blender chatter doesn't
        # contaminate the JSON-RPC frames on stdout.
        self._proc = subprocess.Popen(  # noqa: S603  # cmd is a list of vetted args
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True,
            bufsize=1,  # line-buffered
        )
        if self._proc.stdin is None or self._proc.stdout is None:
            msg = "subprocess.Popen failed to wire stdio"
            raise RuntimeError(msg)
        self._client = RpcClient(self._proc.stdin, self._proc.stdout)

    def stop(self, timeout: float = 10.0) -> int:
        """Send shutdown, then wait. Returns the process exit code."""
        if self._proc is None:
            return 0
        try:
            if self._client is not None:
                self._client.call("shutdown")
        except Exception:  # noqa: BLE001  # best-effort shutdown; we'll terminate next
            sys.stderr.write("forge: shutdown RPC failed; terminating subprocess\n")
        try:
            return self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.terminate()
            return self._proc.wait(timeout=timeout)
        finally:
            self._proc = None
            self._client = None

    def __enter__(self) -> Self:
        """Start the subprocess and return ``self``."""
        self.start()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        """Stop the subprocess; suppresses no exceptions."""
        self.stop()


@contextmanager
def blender_process() -> Iterator[BlenderProcess]:
    """Convenience context manager wrapping :class:`BlenderProcess`."""
    proc = BlenderProcess()
    proc.start()
    try:
        yield proc
    finally:
        proc.stop()
