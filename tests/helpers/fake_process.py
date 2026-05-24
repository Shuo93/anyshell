"""A minimal stand-in for asyncio.subprocess.Process for deterministic tests
that must not depend on real process timing."""

from __future__ import annotations

import asyncio


class FakeStream:
    def __init__(self, chunks: list[bytes] | None = None):
        self._chunks = list(chunks or [])

    async def read(self, n: int = -1) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class FakeProcess:
    """Mimics the bits of asyncio.subprocess.Process the engine touches."""

    def __init__(
        self,
        returncode: int = 0,
        exit_delay: float = 0.0,
        pid: int = 4242,
        stdout: FakeStream | None = None,
        stderr: FakeStream | None = None,
    ):
        self.returncode: int | None = None
        self.pid = pid
        self.stdout = stdout
        self.stderr = stderr
        self._exit_delay = exit_delay
        self._exit_code = returncode
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        if not self._exited.is_set():
            try:
                await asyncio.wait_for(self._exited.wait(), timeout=self._exit_delay or None)
            except asyncio.TimeoutError:
                pass
        if self.returncode is None:
            self.returncode = self._exit_code
            self._exited.set()
        return self.returncode

    def kill(self) -> None:
        if self.returncode is None:
            self.returncode = -9
            self._exited.set()

    def terminate(self) -> None:
        self.kill()
