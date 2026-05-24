"""claude_bash — Python (asyncio) 1:1 port of Claude Code's bash/powershell
execution engine."""

from __future__ import annotations

from .engine.command import (
    ExecResult,
    ShellCommand,
    create_aborted_command,
    create_failed_command,
    generate_task_id,
)
from .engine.shell import exec, find_suitable_shell, set_cwd
from .output.disk_output import (
    get_task_output,
    get_task_output_delta,
    get_task_output_size,
)
from .providers.base import ShellType
from .state.cwd_state import AbortContext, EngineState
from .tool.bash_tool import BashProgress, BashTool, RunResult
from .tool.command_semantics import interpret_command_result
from .tool.tool_result import ToolResultBlock, build_tool_result

__all__ = [
    "exec",
    "find_suitable_shell",
    "set_cwd",
    "ExecResult",
    "ShellCommand",
    "create_aborted_command",
    "create_failed_command",
    "generate_task_id",
    "EngineState",
    "AbortContext",
    "ShellType",
    "BashTool",
    "BashProgress",
    "RunResult",
    "ToolResultBlock",
    "build_tool_result",
    "interpret_command_result",
    "get_task_output",
    "get_task_output_delta",
    "get_task_output_size",
]
