"""Port of src/utils/shell/bashProvider.ts.

Assembles the executed command:
    source <snapshot> 2>/dev/null || true && <disable-extglob> && eval '<cmd>' && pwd -P >| <cwdfile>
and the spawn args (``-c`` plus ``-l`` only when there is no snapshot).
"""

from __future__ import annotations

import os
import tempfile

from .._shellquote import quote
from ..quoting.pipe_command import rearrange_pipe_command
from ..quoting.shell_quoting import (
    quote_shell_command,
    rewrite_windows_null_redirect,
    should_add_stdin_redirect,
)
from ..util.platform import (
    get_platform,
    posix_path_to_windows_path,
    windows_path_to_posix_path,
)
from .base import BuildExecCommandResult


def _get_disable_extglob_command(shell_path: str) -> str | None:
    """Disable extended globs (security: malicious filenames that expand after
    validation)."""
    if "bash" in shell_path:
        return "shopt -u extglob 2>/dev/null || true"
    if "zsh" in shell_path:
        return "setopt NO_EXTENDED_GLOB 2>/dev/null || true"
    return None


class BashShellProvider:
    type = "bash"
    detached = True

    def __init__(
        self,
        shell_path: str,
        snapshot_path: str | None = None,
        session_env_vars: dict[str, str] | None = None,
    ):
        self.shell_path = shell_path
        self._snapshot_path = snapshot_path
        self._session_env_vars = session_env_vars if session_env_vars is not None else {}
        self._last_snapshot_file_path: str | None = None

    async def build_exec_command(
        self,
        command: str,
        *,
        id: str,
        sandbox_tmp_dir: str | None = None,
        use_sandbox: bool = False,
    ) -> BuildExecCommandResult:
        snapshot_file_path = self._snapshot_path
        if snapshot_file_path and not os.path.exists(snapshot_file_path):
            # Snapshot vanished mid-session — fall back to login shell.
            snapshot_file_path = None
        self._last_snapshot_file_path = snapshot_file_path

        tmpdir = tempfile.gettempdir()
        is_windows = get_platform() == "windows"
        shell_tmpdir = windows_path_to_posix_path(tmpdir) if is_windows else tmpdir

        # POSIX path used inside the shell command; native path used by Python.
        shell_cwd_file_path = _posix_join(shell_tmpdir, f"claude-{id}-cwd")
        cwd_file_path = os.path.join(tmpdir, f"claude-{id}-cwd")

        normalized_command = rewrite_windows_null_redirect(command)
        add_stdin_redirect = should_add_stdin_redirect(normalized_command)
        quoted_command = quote_shell_command(normalized_command, add_stdin_redirect)

        # Pipes: move the stdin redirect after the first command (see
        # bashPipeCommand.ts) so it applies to the first command, not eval.
        if "|" in normalized_command and add_stdin_redirect:
            quoted_command = rearrange_pipe_command(normalized_command)

        parts: list[str] = []
        if snapshot_file_path:
            final_path = (
                windows_path_to_posix_path(snapshot_file_path)
                if is_windows
                else snapshot_file_path
            )
            parts.append(f"source {quote([final_path])} 2>/dev/null || true")

        disable_extglob = _get_disable_extglob_command(self.shell_path)
        if disable_extglob:
            parts.append(disable_extglob)

        # eval re-parses after sourcing so aliases from the snapshot expand.
        parts.append(f"eval {quoted_command}")
        # `pwd -P >| <path>` records the physical cwd (consistent with realpath).
        parts.append(f"pwd -P >| {quote([shell_cwd_file_path])}")
        command_string = " && ".join(parts)

        return BuildExecCommandResult(
            command_string=command_string, cwd_file_path=cwd_file_path
        )

    def get_spawn_args(self, command_string: str) -> list[str]:
        skip_login_shell = self._last_snapshot_file_path is not None
        login = [] if skip_login_shell else ["-l"]
        return ["-c", *login, command_string]

    async def get_environment_overrides(self, command: str) -> dict[str, str]:
        # tmux isolation + sandbox tmpdir are out of scope; apply /env overrides.
        env: dict[str, str] = {}
        for key, value in self._session_env_vars.items():
            env[key] = value
        return env


def _posix_join(*parts: str) -> str:
    return "/".join(p.rstrip("/") for p in parts)


async def create_bash_shell_provider(
    shell_path: str,
    *,
    session_env_vars: dict[str, str] | None = None,
    skip_snapshot: bool = False,
) -> BashShellProvider:
    snapshot_path: str | None = None
    if not skip_snapshot:
        from .snapshot import get_cached_snapshot

        snapshot_path = await get_cached_snapshot(shell_path)
    return BashShellProvider(shell_path, snapshot_path, session_env_vars)
