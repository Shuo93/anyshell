"""ShellProvider protocol (port of shellProvider.ts)."""

from __future__ import annotations

from typing import Literal, Protocol, TypedDict, runtime_checkable

ShellType = Literal["bash", "powershell"]
SHELL_TYPES: tuple[ShellType, ...] = ("bash", "powershell")
DEFAULT_HOOK_SHELL: ShellType = "bash"


class BuildExecCommandResult(TypedDict):
    command_string: str
    cwd_file_path: str


@runtime_checkable
class ShellProvider(Protocol):
    type: ShellType
    shell_path: str
    detached: bool

    async def build_exec_command(
        self,
        command: str,
        *,
        id: str,
        sandbox_tmp_dir: str | None = None,
        use_sandbox: bool = False,
    ) -> BuildExecCommandResult:
        ...

    def get_spawn_args(self, command_string: str) -> list[str]:
        ...

    async def get_environment_overrides(self, command: str) -> dict[str, str]:
        ...
