"""Port of src/utils/Shell.ts to asyncio.

``exec()`` builds the command via the shell provider, spawns it (file mode by
default: both fds to one append-opened file; pipe mode when ``on_stdout`` is
given), wraps it in a ShellCommand, and tracks cwd via the ``pwd -P`` temp file.
"""

from __future__ import annotations

import asyncio
import os
import random
import shutil
import subprocess
import sys
import unicodedata
from asyncio.subprocess import DEVNULL, PIPE
from typing import Callable

from ..providers.bash_provider import create_bash_shell_provider
from ..providers.base import ShellProvider, ShellType
from ..state.cwd_state import AbortContext, EngineState
from ..state.subprocess_env import subprocess_env
from ..util.platform import get_platform, posix_path_to_windows_path
from .command import (
    ExecResult,
    ShellCommand,
    create_aborted_command,
    create_failed_command,
    generate_task_id,
    wrap_spawn,
)
from ..output.task_output import TaskOutput

DEFAULT_EXEC_TIMEOUT_MS = 30 * 60 * 1000  # 30 minutes (Shell.ts default)

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


# --- shell discovery ----------------------------------------------------------

def _is_executable(path: str) -> bool:
    try:
        return os.access(path, os.X_OK)
    except OSError:
        return False


_suitable_shell: str | None = None


def find_suitable_shell() -> str:
    """Port of findSuitableShell. Honors CLAUDE_CODE_SHELL, then SHELL, then
    discovery of zsh/bash with a preference matching $SHELL."""
    global _suitable_shell
    if _suitable_shell is not None:
        return _suitable_shell

    override = os.environ.get("CLAUDE_CODE_SHELL")
    if override and ("bash" in override or "zsh" in override) and _is_executable(override):
        _suitable_shell = override
        return override

    env_shell = os.environ.get("SHELL")
    env_shell_supported = bool(env_shell and ("bash" in env_shell or "zsh" in env_shell))
    prefer_bash = bool(env_shell and "bash" in env_shell)

    zsh_path = shutil.which("zsh")
    bash_path = shutil.which("bash")

    base_paths = ["/bin", "/usr/bin", "/usr/local/bin", "/opt/homebrew/bin"]
    order = ["bash", "zsh"] if prefer_bash else ["zsh", "bash"]
    candidates = [f"{p}/{shell}" for shell in order for p in base_paths]

    if prefer_bash:
        if bash_path:
            candidates.insert(0, bash_path)
        if zsh_path:
            candidates.append(zsh_path)
    else:
        if zsh_path:
            candidates.insert(0, zsh_path)
        if bash_path:
            candidates.append(bash_path)

    if env_shell_supported and _is_executable(env_shell):
        candidates.insert(0, env_shell)

    for shell in candidates:
        if shell and _is_executable(shell):
            _suitable_shell = shell
            return shell

    raise RuntimeError(
        "No suitable shell found. claude_bash requires a POSIX shell (bash/zsh)."
    )


def _reset_shell_cache_for_test() -> None:
    global _suitable_shell
    _suitable_shell = None


async def _resolve_provider(
    shell_type: ShellType, state: EngineState, skip_snapshot: bool = False
) -> ShellProvider:
    if shell_type == "bash":
        shell_path = find_suitable_shell()
        return await create_bash_shell_provider(
            shell_path,
            session_env_vars=state.session_env_vars,
            skip_snapshot=skip_snapshot,
        )
    if shell_type == "powershell":
        from ..providers.powershell_provider import create_powershell_provider
        from ..providers.powershell_detection import get_cached_powershell_path

        ps_path = await get_cached_powershell_path()
        if not ps_path:
            raise RuntimeError("PowerShell is not available")
        return create_powershell_provider(ps_path, session_env_vars=state.session_env_vars)
    raise ValueError(f"Unknown shell type: {shell_type}")


def _session_kwargs(detached: bool) -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True} if detached else {}


def set_cwd(state: EngineState, path: str) -> None:
    """Public helper mirroring Shell.ts setCwd — updates the engine's cwd."""
    state.set_cwd(path)


async def exec(
    command: str,
    abort_ctx: AbortContext,
    shell_type: ShellType,
    state: EngineState,
    *,
    timeout: float | None = None,
    on_progress: Callable[[str, str, int, int, bool], None] | None = None,
    prevent_cwd_changes: bool = False,
    should_auto_background: bool = False,
    on_stdout: Callable[[str], None] | None = None,
    skip_snapshot: bool = False,
) -> ShellCommand:
    timeout_ms = timeout if timeout is not None else DEFAULT_EXEC_TIMEOUT_MS

    provider = await _resolve_provider(shell_type, state, skip_snapshot=skip_snapshot)
    cmd_id = format(random.getrandbits(16), "04x")

    built = await provider.build_exec_command(command, id=cmd_id, use_sandbox=False)
    command_string = built["command_string"]
    cwd_file_path = built["cwd_file_path"]

    cwd = state.cwd
    # Recover if the cwd was deleted out from under us.
    if not os.path.isdir(cwd):
        fallback = state.original_cwd
        if os.path.isdir(fallback):
            state.set_cwd(fallback)
            cwd = state.cwd
        else:
            to = TaskOutput(generate_task_id(), None, state.task_output_dir)
            return create_failed_command(
                to,
                f'Working directory "{cwd}" no longer exists. '
                "Please restart from an existing directory.",
            )

    if abort_ctx.is_aborted:
        to = TaskOutput(generate_task_id(), None, state.task_output_dir)
        return create_aborted_command(to)

    use_pipe_mode = on_stdout is not None
    task_id = generate_task_id("local_bash")
    task_output = TaskOutput(
        task_id, on_progress, state.task_output_dir, stdout_to_file=not use_pipe_mode
    )
    os.makedirs(state.task_output_dir, exist_ok=True)

    bin_shell = provider.shell_path
    spawn_args = provider.get_spawn_args(command_string)
    env_overrides = await provider.get_environment_overrides(command)

    env = subprocess_env()
    if shell_type == "bash":
        env["SHELL"] = bin_shell
    env["GIT_EDITOR"] = "true"
    env["CLAUDECODE"] = "1"
    env.update(env_overrides)

    output_fd: int | None = None
    if not use_pipe_mode:
        if sys.platform == "win32":
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        else:
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | _O_NOFOLLOW
        output_fd = os.open(task_output.path, flags, 0o600)

    try:
        process = await asyncio.create_subprocess_exec(
            bin_shell,
            *spawn_args,
            cwd=cwd,
            env=env,
            stdin=DEVNULL,
            stdout=(PIPE if use_pipe_mode else output_fd),
            stderr=(PIPE if use_pipe_mode else output_fd),
            **_session_kwargs(provider.detached),
        )
    except Exception as error:
        if output_fd is not None:
            os.close(output_fd)
        to = TaskOutput(generate_task_id(), None, state.task_output_dir)
        return create_aborted_command(to, code=126, stderr=str(error))

    shell_command = wrap_spawn(
        process,
        abort_ctx,
        timeout_ms,
        task_output,
        should_auto_background,
        on_stdout=on_stdout,
    )

    if output_fd is not None:
        os.close(output_fd)  # child has its own dup

    _attach_cwd_tracking(shell_command, state, cwd_file_path, prevent_cwd_changes)
    return shell_command


def _attach_cwd_tracking(
    shell_command: ShellCommand,
    state: EngineState,
    cwd_file_path: str,
    prevent_cwd_changes: bool,
) -> None:
    def _on_done(fut: "asyncio.Future[ExecResult]") -> None:
        if fut.cancelled():
            return
        try:
            result = fut.result()
        except Exception:
            return
        native_path = (
            posix_path_to_windows_path(cwd_file_path)
            if get_platform() == "windows"
            else cwd_file_path
        )
        if result and not prevent_cwd_changes and not result.background_task_id:
            try:
                with open(native_path, encoding="utf-8") as f:
                    new_cwd = f.read().strip()
                if get_platform() == "windows":
                    new_cwd = posix_path_to_windows_path(new_cwd)
                if unicodedata.normalize("NFC", new_cwd) != state.cwd:
                    state.set_cwd(new_cwd)
            except OSError:
                pass
        try:
            os.unlink(native_path)
        except OSError:
            pass

    shell_command.result.add_done_callback(_on_done)
