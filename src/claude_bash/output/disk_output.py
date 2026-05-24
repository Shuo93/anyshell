"""Port of src/utils/task/diskOutput.ts.

Async disk-write queue with a single drain loop (GC-friendly: each batch is
freed as soon as it's written). 5 GB cap. Plus the read helpers used for
incremental background-task polling (the BashOutput equivalent).

Divergence: paths are passed in explicitly (``<dir>/<task_id>.output``) rather
than resolved from a global session, so multiple engines can coexist.
"""

from __future__ import annotations

import asyncio
import math
import os
import sys

from ..util.fs import read_file_range, tail_file

# Disk cap shared by the file-mode size watchdog and the pipe-mode disk queue.
MAX_TASK_OUTPUT_BYTES = 5 * 1024 * 1024 * 1024
MAX_TASK_OUTPUT_BYTES_DISPLAY = "5GB"

DEFAULT_MAX_READ_BYTES = 8 * 1024 * 1024  # 8 MB

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)  # absent on Windows


def task_output_path(output_dir: str, task_id: str) -> str:
    return os.path.join(output_dir, f"{task_id}.output")


def _append_flags() -> int:
    base = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    return base if sys.platform == "win32" else base | _O_NOFOLLOW


class DiskTaskOutput:
    """Buffers writes to a single task's output file via an async drain loop."""

    def __init__(self, path: str):
        self._path = path
        self._queue: list[bytes] = []
        self._bytes_written = 0
        self._capped = False
        self._drain_task: asyncio.Task | None = None
        self._flush_event = asyncio.Event()
        self._flush_event.set()

    def append(self, content: str) -> None:
        if self._capped:
            return
        encoded = content.encode("utf-8")
        self._bytes_written += len(encoded)
        if self._bytes_written > MAX_TASK_OUTPUT_BYTES:
            self._capped = True
            self._queue.append(
                f"\n[output truncated: exceeded {MAX_TASK_OUTPUT_BYTES_DISPLAY} disk cap]\n".encode()
            )
        else:
            self._queue.append(encoded)
        if self._drain_task is None or self._drain_task.done():
            self._flush_event.clear()
            self._drain_task = asyncio.get_event_loop().create_task(self._drain())

    async def flush(self) -> None:
        await self._flush_event.wait()

    def cancel(self) -> None:
        self._queue.clear()

    async def _drain(self) -> None:
        try:
            await self._drain_all_chunks()
        except Exception:
            if self._queue:
                try:
                    await self._drain_all_chunks()
                except Exception:
                    pass
        finally:
            self._flush_event.set()

    async def _drain_all_chunks(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        while True:
            fd = await asyncio.to_thread(os.open, self._path, _append_flags(), 0o600)
            try:
                while self._queue:
                    batch = b"".join(self._queue)
                    self._queue.clear()
                    await asyncio.to_thread(os.write, fd, batch)
            finally:
                await asyncio.to_thread(os.close, fd)
            if self._queue:
                continue
            break


async def init_task_output(path: str) -> str:
    """Create an empty output file (O_EXCL + O_NOFOLLOW)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if sys.platform != "win32":
        flags |= _O_NOFOLLOW
    fd = await asyncio.to_thread(os.open, path, flags, 0o600)
    await asyncio.to_thread(os.close, fd)
    return path


async def get_task_output_delta(
    path: str, from_offset: int, max_bytes: int = DEFAULT_MAX_READ_BYTES
) -> tuple[str, int]:
    """Read new content since ``from_offset`` (incremental poll). Returns
    ``(content, new_offset)``."""
    result = await read_file_range(path, from_offset, max_bytes)
    if not result:
        return "", from_offset
    return result.content, from_offset + result.bytes_read


async def get_task_output(path: str, max_bytes: int = DEFAULT_MAX_READ_BYTES) -> str:
    """Read the tail of a task's output, prefixing a notice if truncated."""
    result = await tail_file(path, max_bytes)
    if result.bytes_total > result.bytes_read:
        omitted_kb = math.floor((result.bytes_total - result.bytes_read) / 1024)
        return f"[{omitted_kb}KB of earlier output omitted]\n{result.content}"
    return result.content


async def get_task_output_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


async def cleanup_task_output(path: str) -> None:
    try:
        await asyncio.to_thread(os.unlink, path)
    except OSError:
        pass
