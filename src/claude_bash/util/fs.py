"""Port of readFileRange / tailFile from fsOperations.ts.

Both run the blocking I/O in a thread (asyncio.to_thread) so they never block
the event loop — matching the non-blocking semantics of Node's fs promises.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass


@dataclass
class FileRangeResult:
    content: str
    bytes_read: int
    bytes_total: int


async def read_file_range(path: str, offset: int, max_bytes: int) -> FileRangeResult | None:
    """Read up to ``max_bytes`` from ``path`` starting at ``offset``.

    Returns ``None`` when the file is smaller than ``offset`` (nothing to read)
    or does not exist (mirrors readFileRange returning null)."""

    def _read() -> FileRangeResult | None:
        try:
            with open(path, "rb") as f:
                total = os.fstat(f.fileno()).st_size
                if total <= offset:
                    return None
                to_read = min(total - offset, max_bytes)
                f.seek(offset)
                data = f.read(to_read)
                return FileRangeResult(
                    content=data.decode("utf-8", errors="replace"),
                    bytes_read=len(data),
                    bytes_total=total,
                )
        except FileNotFoundError:
            return None

    return await asyncio.to_thread(_read)


async def tail_file(path: str, max_bytes: int) -> FileRangeResult:
    """Read the last ``max_bytes`` of ``path``. Empty result if it doesn't
    exist or is empty (mirrors tailFile)."""

    def _tail() -> FileRangeResult:
        try:
            with open(path, "rb") as f:
                total = os.fstat(f.fileno()).st_size
                if total == 0:
                    return FileRangeResult("", 0, 0)
                offset = max(0, total - max_bytes)
                to_read = total - offset
                f.seek(offset)
                data = f.read(to_read)
                return FileRangeResult(
                    content=data.decode("utf-8", errors="replace"),
                    bytes_read=len(data),
                    bytes_total=total,
                )
        except FileNotFoundError:
            return FileRangeResult("", 0, 0)

    return await asyncio.to_thread(_tail)
