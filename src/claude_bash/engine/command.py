"""Port of src/utils/ShellCommand.ts to asyncio.

ShellCommandImpl wraps an ``asyncio.subprocess.Process`` and reproduces the
Node lifecycle exactly:

- ``result`` resolves on shell **exit** (``process.wait()``) — never waits on
  grandchild-inherited fds (the 'exit' vs 'close' distinction).
- timeout fires ``_handle_timeout`` → auto-background callback or SIGTERM-kill.
- abort with ``reason='interrupt'`` does NOT kill (lets the caller background).
- ``kill()`` tree-kills the whole process group.
- ``background()`` cancels the timeout and (file mode) starts the 5 GB watchdog.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Protocol

from ..output.disk_output import MAX_TASK_OUTPUT_BYTES, MAX_TASK_OUTPUT_BYTES_DISPLAY
from ..output.task_output import TaskOutput
from ..state.cwd_state import AbortContext
from ..util.format import format_duration

SIGKILL_CODE = 137
SIGTERM_CODE = 143
SIZE_WATCHDOG_INTERVAL_S = 5.0

Status = Literal["running", "backgrounded", "completed", "killed"]


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    code: int
    interrupted: bool
    background_task_id: str | None = None
    backgrounded_by_user: bool | None = None
    assistant_auto_backgrounded: bool | None = None
    output_file_path: str | None = None
    output_file_size: int | None = None
    output_task_id: str | None = None
    pre_spawn_error: str | None = None


class ShellCommand(Protocol):
    status: Status
    result: "asyncio.Future[ExecResult]"
    task_output: TaskOutput

    def background(self, task_id: str) -> bool: ...
    def kill(self) -> None: ...
    def cleanup(self) -> None: ...
    def set_on_timeout(self, callback: Callable[[Callable[[str], bool]], None]) -> None: ...


def generate_task_id(prefix: str = "local_bash") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _prepend_stderr(prefix: str, stderr: str) -> str:
    return f"{prefix} {stderr}" if stderr else prefix


def _tree_kill(process: asyncio.subprocess.Process) -> None:
    """Kill the process and all descendants (port of tree-kill).

    POSIX: the child is a session leader (start_new_session) so ``killpg``
    reaches the whole group. Windows / fallback: psutil recursive kill."""
    if process.returncode is not None:
        return
    pid = process.pid
    if sys.platform == "win32":
        _psutil_kill_tree(pid)
        return
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def _psutil_kill_tree(pid: int) -> None:
    try:
        import psutil
    except ImportError:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM if sys.platform == "win32" else signal.SIGKILL)
        return
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    for p in [*parent.children(recursive=True), parent]:
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            p.kill()


class ShellCommandImpl:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        abort_ctx: AbortContext,
        timeout_ms: float,
        task_output: TaskOutput,
        should_auto_background: bool = False,
        max_output_bytes: int = MAX_TASK_OUTPUT_BYTES,
        on_stdout: Callable[[str], None] | None = None,
    ):
        self._loop = asyncio.get_event_loop()
        self._process = process
        self._abort_ctx = abort_ctx
        self._timeout_ms = timeout_ms
        self._should_auto_background = should_auto_background
        self._max_output_bytes = max_output_bytes
        self._on_stdout = on_stdout
        self.task_output = task_output

        self._status: Status = "running"
        self._background_task_id: str | None = None
        self._killed_for_size = False
        self._on_timeout_callback: Callable[[Callable[[str], bool]], None] | None = None

        self._exit_code_future: asyncio.Future[int] = self._loop.create_future()
        self.result: asyncio.Future[ExecResult] = self._loop.create_future()

        self._timeout_handle: asyncio.TimerHandle | None = None
        self._abort_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._wait_task: asyncio.Task | None = None
        self._reader_tasks: list[asyncio.Task] = []

        self._start()

    # --- setup ---------------------------------------------------------------

    def _start(self) -> None:
        # Pipe mode (hooks / on_stdout): read streams into TaskOutput.
        if self._process.stdout is not None:
            self._reader_tasks.append(
                self._loop.create_task(self._read_stream(self._process.stdout, False))
            )
        if self._process.stderr is not None:
            self._reader_tasks.append(
                self._loop.create_task(self._read_stream(self._process.stderr, True))
            )

        self._wait_task = self._loop.create_task(self._wait_for_exit())
        self._timeout_handle = self._loop.call_later(
            self._timeout_ms / 1000, self._handle_timeout
        )
        self._abort_task = self._loop.create_task(self._watch_abort())

    async def _read_stream(self, stream: asyncio.StreamReader, is_stderr: bool) -> None:
        try:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                if is_stderr:
                    self.task_output.write_stderr(text)
                else:
                    self.task_output.write_stdout(text)
                    if self._on_stdout:
                        self._on_stdout(text)
        except (asyncio.CancelledError, ValueError, OSError):
            pass

    # --- exit / timeout / abort ----------------------------------------------

    async def _wait_for_exit(self) -> None:
        try:
            await self._process.wait()
        except asyncio.CancelledError:
            return
        except Exception:
            self._resolve_exit_code(1)
            return
        rc = self._process.returncode
        if rc is None:
            code = 1
        elif rc < 0:
            code = 144 if -rc == int(signal.SIGTERM) else 1
        else:
            code = rc
        self._resolve_exit_code(code)

    def _resolve_exit_code(self, code: int) -> None:
        if not self._exit_code_future.done():
            self._exit_code_future.set_result(code)
            self._loop.create_task(self._handle_exit(code))

    async def _watch_abort(self) -> None:
        try:
            await self._abort_ctx.wait()
        except asyncio.CancelledError:
            return
        # On 'interrupt' (a new message was submitted) don't kill — let the
        # caller background so the model can see partial output.
        if self._abort_ctx.reason == "interrupt":
            return
        self.kill()

    def _handle_timeout(self) -> None:
        if self._should_auto_background and self._on_timeout_callback:
            self._on_timeout_callback(self.background)
        else:
            self._do_kill(SIGTERM_CODE)

    async def _handle_exit(self, code: int) -> None:
        self._cleanup_listeners()
        if self._status in ("running", "backgrounded"):
            self._status = "completed"

        stdout = await self.task_output.get_stdout()
        result = ExecResult(
            code=code,
            stdout=stdout,
            stderr=self.task_output.get_stderr(),
            interrupted=(code == SIGKILL_CODE),
            background_task_id=self._background_task_id,
        )

        if self.task_output.stdout_to_file and not self._background_task_id:
            if self.task_output.output_file_redundant:
                asyncio.ensure_future(self.task_output.delete_output_file())
            else:
                result.output_file_path = self.task_output.path
                result.output_file_size = self.task_output.output_file_size
                result.output_task_id = self.task_output.task_id

        if self._killed_for_size:
            result.stderr = _prepend_stderr(
                f"Background command killed: output file exceeded {MAX_TASK_OUTPUT_BYTES_DISPLAY}",
                result.stderr,
            )
        elif code == SIGTERM_CODE:
            result.stderr = _prepend_stderr(
                f"Command timed out after {format_duration(self._timeout_ms)}",
                result.stderr,
            )

        if not self.result.done():
            self.result.set_result(result)

    # --- kill / background ---------------------------------------------------

    def _do_kill(self, code: int = SIGKILL_CODE) -> None:
        self._status = "killed"
        _tree_kill(self._process)
        self._resolve_exit_code(code)

    def kill(self) -> None:
        self._do_kill()

    def background(self, task_id: str) -> bool:
        if self._status == "running":
            self._background_task_id = task_id
            self._status = "backgrounded"
            self._cleanup_listeners()
            if self.task_output.stdout_to_file:
                self._start_size_watchdog()
            else:
                self.task_output.spill_to_disk()
            return True
        return False

    def set_on_timeout(self, callback: Callable[[Callable[[str], bool]], None]) -> None:
        if self._should_auto_background:
            self._on_timeout_callback = callback

    @property
    def status(self) -> Status:
        return self._status

    # --- watchdog / cleanup --------------------------------------------------

    def _start_size_watchdog(self) -> None:
        self._watchdog_task = self._loop.create_task(self._size_watchdog_loop())

    async def _size_watchdog_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(SIZE_WATCHDOG_INTERVAL_S)
                if self._status != "backgrounded":
                    return
                try:
                    size = await asyncio.to_thread(os.path.getsize, self.task_output.path)
                except OSError:
                    continue
                if size > self._max_output_bytes and self._status == "backgrounded":
                    self._killed_for_size = True
                    self._stop_size_watchdog()
                    self._do_kill(SIGKILL_CODE)
                    return
        except asyncio.CancelledError:
            return

    def _stop_size_watchdog(self) -> None:
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        self._watchdog_task = None

    def _cleanup_listeners(self) -> None:
        if self._timeout_handle is not None:
            self._timeout_handle.cancel()
            self._timeout_handle = None
        if self._abort_task is not None and not self._abort_task.done():
            self._abort_task.cancel()
        self._abort_task = None
        self._stop_size_watchdog()

    def cleanup(self) -> None:
        for t in self._reader_tasks:
            if not t.done():
                t.cancel()
        self._reader_tasks = []
        self.task_output.clear()
        self._cleanup_listeners()
        # Intentionally do NOT cancel _wait_task: letting process.wait() complete
        # reaps the child and closes its transport cleanly (avoids zombies and the
        # "Loop is closed" finalizer warning). For a killed process it returns
        # almost immediately; for a completed one it's already done.


def wrap_spawn(
    process: asyncio.subprocess.Process,
    abort_ctx: AbortContext,
    timeout_ms: float,
    task_output: TaskOutput,
    should_auto_background: bool = False,
    max_output_bytes: int = MAX_TASK_OUTPUT_BYTES,
    on_stdout: Callable[[str], None] | None = None,
) -> ShellCommandImpl:
    return ShellCommandImpl(
        process,
        abort_ctx,
        timeout_ms,
        task_output,
        should_auto_background,
        max_output_bytes,
        on_stdout,
    )


class _StaticShellCommand:
    """A pre-resolved ShellCommand for aborted / pre-spawn-failed cases."""

    def __init__(self, status: Status, result: ExecResult, task_output: TaskOutput):
        self.status = status
        self.task_output = task_output
        self.result = asyncio.get_event_loop().create_future()
        self.result.set_result(result)

    def background(self, task_id: str) -> bool:
        return False

    def kill(self) -> None:
        pass

    def cleanup(self) -> None:
        pass

    def set_on_timeout(self, callback) -> None:
        pass


def create_aborted_command(
    task_output: TaskOutput,
    *,
    background_task_id: str | None = None,
    stderr: str | None = None,
    code: int | None = None,
) -> _StaticShellCommand:
    result = ExecResult(
        code=code if code is not None else 145,
        stdout="",
        stderr=stderr if stderr is not None else "Command aborted before execution",
        interrupted=True,
        background_task_id=background_task_id,
    )
    return _StaticShellCommand("killed", result, task_output)


def create_failed_command(task_output: TaskOutput, pre_spawn_error: str) -> _StaticShellCommand:
    result = ExecResult(
        code=1,
        stdout="",
        stderr=pre_spawn_error,
        interrupted=False,
        pre_spawn_error=pre_spawn_error,
    )
    return _StaticShellCommand("completed", result, task_output)
