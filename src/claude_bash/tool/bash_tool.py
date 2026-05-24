"""Port of the runShellCommand generator from src/tools/BashTool/BashTool.tsx.

High-level API over ``exec()``: timeout clamping, progress reporting, the
PROGRESS_THRESHOLD initial wait, background handling (explicit + timeout-driven
auto-background via a library callback), the completed-after-backgrounding
race-fix, large-output persistence, and exit-code interpretation.

Python note: async generators cannot ``return`` a value, so the TS generator's
"yield progress, return ExecResult" becomes ``run(...) -> RunResult`` with an
``on_progress`` callback.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, replace
from typing import Awaitable, Callable

from ..engine.command import ExecResult, ShellCommand, generate_task_id
from ..engine.shell import exec as shell_exec
from ..output.task_output import TaskOutput
from ..state.cwd_state import AbortContext, EngineState
from .command_semantics import interpret_command_result
from .output_limits import DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS

PROGRESS_THRESHOLD_MS = 2_000
MAX_PERSISTED_SIZE = 64 * 1024 * 1024  # 64 MB
DISALLOWED_AUTO_BACKGROUND_COMMANDS = ("sleep",)

OnAutoBackground = Callable[[str, ShellCommand], "Awaitable[str]"]


@dataclass
class BashProgress:
    output: str          # most recent ~5 lines
    full_output: str     # most recent ~100 lines
    elapsed_seconds: float
    total_lines: int
    total_bytes: int
    task_id: str | None
    timeout_ms: int | None


@dataclass
class RunResult:
    exec_result: ExecResult
    return_code_interpretation: str | None = None
    persisted_output_path: str | None = None
    persisted_output_size: int | None = None


def _is_autobackgrounding_allowed(command: str) -> bool:
    parts = command.strip().split()
    base = parts[0] if parts else ""
    return base not in DISALLOWED_AUTO_BACKGROUND_COMMANDS


class BashTool:
    def __init__(
        self,
        state: EngineState,
        *,
        default_timeout_ms: int = DEFAULT_TIMEOUT_MS,
        max_timeout_ms: int = MAX_TIMEOUT_MS,
        progress_threshold_ms: int = PROGRESS_THRESHOLD_MS,
        on_auto_background: OnAutoBackground | None = None,
        disable_background_tasks: bool = False,
    ):
        self._state = state
        self._default_timeout_ms = default_timeout_ms
        self._max_timeout_ms = max_timeout_ms
        self._progress_threshold_ms = progress_threshold_ms
        self._on_auto_background = on_auto_background
        self._disable_background_tasks = disable_background_tasks

    async def run(
        self,
        command: str,
        abort_ctx: AbortContext,
        *,
        timeout: int | None = None,
        description: str | None = None,
        run_in_background: bool = False,
        prevent_cwd_changes: bool = False,
        on_progress: Callable[[BashProgress], None] | None = None,
        skip_snapshot: bool = False,
    ) -> RunResult:
        timeout_ms = timeout if timeout else self._default_timeout_ms
        if timeout_ms > self._max_timeout_ms:
            timeout_ms = self._max_timeout_ms

        loop = asyncio.get_event_loop()
        start = loop.time()
        background_shell_id: str | None = None
        progress_event = asyncio.Event()
        latest = {"output": "", "full": "", "total_lines": 0, "total_bytes": 0}

        def _progress_cb(last_lines, all_lines, total_lines, total_bytes, incomplete):
            latest["output"] = last_lines
            latest["full"] = all_lines
            latest["total_lines"] = total_lines
            latest["total_bytes"] = total_bytes if incomplete else 0
            progress_event.set()

        should_auto_bg = (
            not self._disable_background_tasks and _is_autobackgrounding_allowed(command)
        )

        sc = await shell_exec(
            command,
            abort_ctx,
            "bash",
            self._state,
            timeout=timeout_ms,
            on_progress=_progress_cb,
            prevent_cwd_changes=prevent_cwd_changes,
            should_auto_background=should_auto_bg,
            skip_snapshot=skip_snapshot,
        )

        async def _mint_task_id() -> str:
            if self._on_auto_background:
                return await self._on_auto_background(command, sc)
            return generate_task_id("local_bash")

        # Explicit background request — return immediately.
        if run_in_background and not self._disable_background_tasks:
            tid = await _mint_task_id()
            sc.background(tid)
            return _background_run_result(tid)

        # Timeout-driven auto-background hook.
        if should_auto_bg:
            def _timeout_cb(background_fn: Callable[[str], bool]) -> None:
                async def _go() -> None:
                    nonlocal background_shell_id
                    tid = await _mint_task_id()
                    background_fn(tid)
                    background_shell_id = tid
                    progress_event.set()

                loop.create_task(_go())

            sc.set_on_timeout(_timeout_cb)

        # Phase 1: wait up to the progress threshold for a fast command.
        try:
            result = await asyncio.wait_for(
                asyncio.shield(sc.result), timeout=self._progress_threshold_ms / 1000
            )
            sc.cleanup()
            return await self._build_run_result(command, result, sc)
        except asyncio.TimeoutError:
            pass

        if background_shell_id:
            return _background_run_result(background_shell_id, assistant_auto=True)

        # Phase 2: progress loop driven by the shared poller.
        TaskOutput.start_polling(sc.task_output.task_id)
        try:
            while True:
                progress_event.clear()
                if not sc.result.done():
                    waiter = asyncio.ensure_future(progress_event.wait())
                    _, pending = await asyncio.wait(
                        {sc.result, waiter}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if waiter in pending:
                        waiter.cancel()

                if sc.result.done():
                    result = sc.result.result()
                    # Race: backgrounding fired but the command finished before
                    # the next tick — strip the id and reconstruct the file path.
                    if result.background_task_id is not None:
                        fixed = replace(result, background_task_id=None)
                        to = sc.task_output
                        if to.stdout_to_file and not to.output_file_redundant:
                            fixed.output_file_path = to.path
                            fixed.output_file_size = to.output_file_size
                            fixed.output_task_id = to.task_id
                        sc.cleanup()
                        return await self._build_run_result(command, fixed, sc)
                    sc.cleanup()
                    return await self._build_run_result(command, result, sc)

                if background_shell_id:
                    return _background_run_result(background_shell_id, assistant_auto=True)

                elapsed = loop.time() - start
                if on_progress:
                    on_progress(
                        BashProgress(
                            output=latest["output"],
                            full_output=latest["full"],
                            elapsed_seconds=elapsed,
                            total_lines=latest["total_lines"],
                            total_bytes=latest["total_bytes"],
                            task_id=sc.task_output.task_id,
                            timeout_ms=int(timeout_ms),
                        )
                    )
        finally:
            TaskOutput.stop_polling(sc.task_output.task_id)

    async def _build_run_result(
        self, command: str, result: ExecResult, sc: ShellCommand
    ) -> RunResult:
        interp = interpret_command_result(command, result.code, result.stdout, result.stderr)
        persisted_path: str | None = None
        persisted_size: int | None = None
        if result.output_file_path and result.output_task_id:
            try:
                size = os.path.getsize(result.output_file_path)
                persisted_size = size
                if size > MAX_PERSISTED_SIZE:
                    await asyncio.to_thread(os.truncate, result.output_file_path, MAX_PERSISTED_SIZE)
                persisted_path = result.output_file_path
            except OSError:
                pass
        return RunResult(
            exec_result=result,
            return_code_interpretation=interp["message"],
            persisted_output_path=persisted_path,
            persisted_output_size=persisted_size,
        )


def _background_run_result(task_id: str, assistant_auto: bool = False) -> RunResult:
    return RunResult(
        exec_result=ExecResult(
            stdout="",
            stderr="",
            code=0,
            interrupted=False,
            background_task_id=task_id,
            assistant_auto_backgrounded=assistant_auto or None,
        )
    )
