"""Port of src/utils/shell/powershellProvider.ts.

Builds the PowerShell command with exit-code capture ($LASTEXITCODE else $?)
and cwd tracking via Out-File, plus the -NoProfile -NonInteractive flags. The
base64 UTF-16LE -EncodedCommand form is only used on the (out-of-scope) sandbox
path, where an outer shell-quoting layer would otherwise corrupt ``!``.
"""

from __future__ import annotations

import base64
import os
import tempfile

from .base import BuildExecCommandResult, ShellProvider


def build_powershell_args(cmd: str) -> list[str]:
    return ["-NoProfile", "-NonInteractive", "-Command", cmd]


def _encode_powershell_command(ps_command: str) -> str:
    """Base64-encode as UTF-16LE (survives any shell-quoting layer)."""
    return base64.b64encode(ps_command.encode("utf-16-le")).decode("ascii")


class PowerShellProvider:
    type = "powershell"
    detached = False

    def __init__(self, shell_path: str, session_env_vars: dict[str, str] | None = None):
        self.shell_path = shell_path
        self._session_env_vars = session_env_vars if session_env_vars is not None else {}
        self._sandbox_tmp_dir: str | None = None

    async def build_exec_command(
        self,
        command: str,
        *,
        id: str,
        sandbox_tmp_dir: str | None = None,
        use_sandbox: bool = False,
    ) -> BuildExecCommandResult:
        self._sandbox_tmp_dir = sandbox_tmp_dir if use_sandbox else None

        if use_sandbox and sandbox_tmp_dir:
            cwd_file_path = f"{sandbox_tmp_dir}/claude-pwd-ps-{id}"
        else:
            cwd_file_path = os.path.join(tempfile.gettempdir(), f"claude-pwd-ps-{id}")
        escaped = cwd_file_path.replace("'", "''")

        # Prefer $LASTEXITCODE (native exe) over $? (which PS 5.1 sets false when
        # a native command writes to a redirected stderr even on exit 0).
        cwd_tracking = (
            "\n; $_ec = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } elseif ($?) { 0 } else { 1 }"
            f"\n; (Get-Location).Path | Out-File -FilePath '{escaped}' -Encoding utf8 -NoNewline"
            "\n; exit $_ec"
        )
        ps_command = command + cwd_tracking

        if use_sandbox:
            command_string = " ".join([
                "'" + self.shell_path.replace("'", "'\\''") + "'",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                _encode_powershell_command(ps_command),
            ])
        else:
            command_string = ps_command

        return BuildExecCommandResult(command_string=command_string, cwd_file_path=cwd_file_path)

    def get_spawn_args(self, command_string: str) -> list[str]:
        return build_powershell_args(command_string)

    async def get_environment_overrides(self, command: str) -> dict[str, str]:
        env: dict[str, str] = {}
        # Session vars first so sandbox TMPDIR can't be overridden by /env.
        for key, value in self._session_env_vars.items():
            env[key] = value
        if self._sandbox_tmp_dir:
            env["TMPDIR"] = self._sandbox_tmp_dir
            env["CLAUDE_CODE_TMPDIR"] = self._sandbox_tmp_dir
        return env


def create_powershell_provider(
    shell_path: str, *, session_env_vars: dict[str, str] | None = None
) -> ShellProvider:
    return PowerShellProvider(shell_path, session_env_vars)
