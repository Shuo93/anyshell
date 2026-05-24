"""Port of src/utils/task/TaskOutput.ts.

Single source of truth for a command's output.

- **File mode** (bash commands): the child writes both fds straight to a file;
  progress is extracted by a shared 1-second poller reading the 4 KB tail.
- **Pipe mode** (real-time callbacks): data flows through ``write_stdout`` /
  ``write_stderr``, buffered in memory (with a CircularBuffer of recent lines),
  spilling to ``DiskTaskOutput`` past 8 MB.
"""

from __future__ import annotations

import asyncio
import math
import os

from ..tool.output_limits import get_max_output_length
from ..util.fs import read_file_range, tail_file
from ..util.stringutils import safe_join_lines
from .circular_buffer import CircularBuffer
from .disk_output import DiskTaskOutput, task_output_path

DEFAULT_MAX_MEMORY = 8 * 1024 * 1024  # 8 MB
POLL_INTERVAL_S = 1.0
PROGRESS_TAIL_BYTES = 4096

# ProgressCallback(last_lines, all_lines, total_lines, total_bytes, is_incomplete)
ProgressCallback = "Callable[[str, str, int, int, bool], None]"


def _js_round(x: float) -> int:
    return math.floor(x + 0.5)


class TaskOutput:
    # --- shared poller state (module-level via class attributes) ---
    _registry: dict[str, "TaskOutput"] = {}
    _active_polling: dict[str, "TaskOutput"] = {}
    _poll_task: asyncio.Task | None = None

    def __init__(self, task_id, on_progress, output_dir, stdout_to_file=False, max_memory=DEFAULT_MAX_MEMORY):
        self.task_id = task_id
        self.output_dir = output_dir
        self.path = task_output_path(output_dir, task_id)
        self.stdout_to_file = stdout_to_file
        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._disk: DiskTaskOutput | None = None
        self._recent_lines: CircularBuffer = CircularBuffer(1000)
        self._total_lines = 0
        self._total_bytes = 0
        self._max_memory = max_memory
        self._on_progress = on_progress
        self._output_file_redundant = False
        self._output_file_size = 0
        if stdout_to_file and on_progress:
            TaskOutput._registry[task_id] = self

    # --- shared poller --------------------------------------------------------

    @classmethod
    def start_polling(cls, task_id: str) -> None:
        instance = cls._registry.get(task_id)
        if not instance or not instance._on_progress:
            return
        cls._active_polling[task_id] = instance
        if cls._poll_task is None or cls._poll_task.done():
            cls._poll_task = asyncio.get_event_loop().create_task(cls._poll_loop())

    @classmethod
    def stop_polling(cls, task_id: str) -> None:
        cls._active_polling.pop(task_id, None)
        if not cls._active_polling and cls._poll_task is not None:
            cls._poll_task.cancel()
            cls._poll_task = None

    @classmethod
    async def _poll_loop(cls) -> None:
        try:
            while cls._active_polling:
                await asyncio.sleep(POLL_INTERVAL_S)
                for entry in list(cls._active_polling.values()):
                    if entry._on_progress:
                        await entry._poll_once()
        except asyncio.CancelledError:
            pass

    async def _poll_once(self) -> None:
        res = await tail_file(self.path, PROGRESS_TAIL_BYTES)
        if not self._on_progress:
            return
        content, bytes_read, bytes_total = res.content, res.bytes_read, res.bytes_total
        if not content:
            self._on_progress("", "", self._total_lines, bytes_total, False)
            return
        pos = len(content)
        n5 = 0
        n100 = 0
        line_count = 0
        while pos > 0:
            pos = content.rfind("\n", 0, pos)  # == JS lastIndexOf('\n', pos-1)
            line_count += 1
            if line_count == 5:
                n5 = 0 if pos <= 0 else pos + 1
            if line_count == 100:
                n100 = 0 if pos <= 0 else pos + 1
        if bytes_read >= bytes_total:
            total_lines = line_count
        else:
            total_lines = max(self._total_lines, _js_round((bytes_total / bytes_read) * line_count))
        self._total_lines = total_lines
        self._total_bytes = bytes_total
        self._on_progress(
            content[n5:], content[n100:], total_lines, bytes_total, bytes_read < bytes_total
        )

    # --- pipe-mode writes -----------------------------------------------------

    def write_stdout(self, data: str) -> None:
        self._write_buffered(data, False)

    def write_stderr(self, data: str) -> None:
        self._write_buffered(data, True)

    def _write_buffered(self, data: str, is_stderr: bool) -> None:
        self._total_bytes += len(data)
        self._update_progress(data)
        if self._disk:
            self._disk.append(f"[stderr] {data}" if is_stderr else data)
            return
        total_mem = len(self._stdout_buffer) + len(self._stderr_buffer) + len(data)
        if total_mem > self._max_memory:
            self._spill_to_disk(data if is_stderr else None, None if is_stderr else data)
            return
        if is_stderr:
            self._stderr_buffer += data
        else:
            self._stdout_buffer += data

    def _update_progress(self, data: str) -> None:
        MAX_PROGRESS_BYTES = 4096
        MAX_PROGRESS_LINES = 100
        line_count = 0
        lines: list[str] = []
        extracted_bytes = 0
        pos = len(data)
        while pos > 0:
            prev = data.rfind("\n", 0, pos)  # == JS lastIndexOf('\n', pos-1)
            if prev == -1:
                break
            line_count += 1
            if len(lines) < MAX_PROGRESS_LINES and extracted_bytes < MAX_PROGRESS_BYTES:
                line_len = pos - prev - 1
                if 0 < line_len <= MAX_PROGRESS_BYTES - extracted_bytes:
                    line = data[prev + 1:pos]
                    if line.strip():
                        lines.append(line)
                        extracted_bytes += line_len
            pos = prev
        self._total_lines += line_count
        for i in range(len(lines) - 1, -1, -1):
            self._recent_lines.add(lines[i])
        if self._on_progress and lines:
            recent = self._recent_lines.get_recent(5)
            self._on_progress(
                safe_join_lines(recent, "\n"),
                safe_join_lines(self._recent_lines.get_recent(100), "\n"),
                self._total_lines,
                self._total_bytes,
                self._disk is not None,
            )

    def _spill_to_disk(self, stderr_chunk: str | None, stdout_chunk: str | None) -> None:
        self._disk = DiskTaskOutput(self.path)
        if self._stdout_buffer:
            self._disk.append(self._stdout_buffer)
            self._stdout_buffer = ""
        if self._stderr_buffer:
            self._disk.append(f"[stderr] {self._stderr_buffer}")
            self._stderr_buffer = ""
        if stdout_chunk:
            self._disk.append(stdout_chunk)
        if stderr_chunk:
            self._disk.append(f"[stderr] {stderr_chunk}")

    # --- result reads ---------------------------------------------------------

    async def get_stdout(self) -> str:
        if self.stdout_to_file:
            return await self._read_stdout_from_file()
        if self._disk:
            recent = self._recent_lines.get_recent(5)
            tail = safe_join_lines(recent, "\n")
            size_kb = round(self._total_bytes / 1024)
            notice = f"\nOutput truncated ({size_kb}KB total). Full output saved to: {self.path}"
            return tail + notice if tail else notice.lstrip()
        return self._stdout_buffer

    async def _read_stdout_from_file(self) -> str:
        max_bytes = get_max_output_length()
        try:
            result = await read_file_range(self.path, 0, max_bytes)
            if not result:
                self._output_file_redundant = True
                return ""
            self._output_file_size = result.bytes_total
            self._output_file_redundant = result.bytes_total <= result.bytes_read
            return result.content
        except OSError as err:
            return (
                f"<bash output unavailable: output file {self.path} could not be "
                f"read ({err}).>"
            )

    def get_stderr(self) -> str:
        if self._disk:
            return ""
        return self._stderr_buffer

    @property
    def is_overflowed(self) -> bool:
        return self._disk is not None

    @property
    def total_lines(self) -> int:
        return self._total_lines

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    @property
    def output_file_redundant(self) -> bool:
        return self._output_file_redundant

    @property
    def output_file_size(self) -> int:
        return self._output_file_size

    def spill_to_disk(self) -> None:
        if not self._disk:
            self._spill_to_disk(None, None)

    async def flush(self) -> None:
        if self._disk:
            await self._disk.flush()

    async def delete_output_file(self) -> None:
        try:
            await asyncio.to_thread(os.unlink, self.path)
        except OSError:
            pass

    def clear(self) -> None:
        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._recent_lines.clear()
        self._on_progress = None
        if self._disk:
            self._disk.cancel()
        TaskOutput.stop_polling(self.task_id)
        TaskOutput._registry.pop(self.task_id, None)
