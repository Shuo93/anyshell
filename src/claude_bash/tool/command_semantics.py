"""Port of src/tools/BashTool/commandSemantics.ts.

Many commands use non-zero exit codes to convey information rather than failure
(grep=1 no match, diff=1 differs, ...). ``interpret_command_result`` returns
whether an exit code should be treated as an error plus an optional message.
"""

from __future__ import annotations

import re
from typing import Callable

# semantic(exit_code, stdout, stderr) -> {"is_error": bool, "message": str|None}
CommandSemantic = Callable[[int, str, str], dict]


def _default(exit_code: int, _stdout: str, _stderr: str) -> dict:
    return {
        "is_error": exit_code != 0,
        "message": f"Command failed with exit code {exit_code}" if exit_code != 0 else None,
    }


def _grep_like(exit_code: int, _stdout: str, _stderr: str) -> dict:
    return {"is_error": exit_code >= 2, "message": "No matches found" if exit_code == 1 else None}


def _find(exit_code: int, _stdout: str, _stderr: str) -> dict:
    return {
        "is_error": exit_code >= 2,
        "message": "Some directories were inaccessible" if exit_code == 1 else None,
    }


def _diff(exit_code: int, _stdout: str, _stderr: str) -> dict:
    return {"is_error": exit_code >= 2, "message": "Files differ" if exit_code == 1 else None}


def _test(exit_code: int, _stdout: str, _stderr: str) -> dict:
    return {"is_error": exit_code >= 2, "message": "Condition is false" if exit_code == 1 else None}


COMMAND_SEMANTICS: dict[str, CommandSemantic] = {
    "grep": _grep_like,
    "rg": _grep_like,
    "find": _find,
    "diff": _diff,
    "test": _test,
    "[": _test,
}

_SEGMENT_SPLIT = re.compile(r"\|\||&&|\||;")


def _extract_base_command(command: str) -> str:
    parts = command.strip().split()
    return parts[0] if parts else ""


def _heuristically_extract_base_command(command: str) -> str:
    # The last command in a pipeline/chain determines the exit code.
    segments = [s for s in _SEGMENT_SPLIT.split(command) if s.strip()]
    last = segments[-1] if segments else command
    return _extract_base_command(last)


def interpret_command_result(command: str, exit_code: int, stdout: str, stderr: str) -> dict:
    base = _heuristically_extract_base_command(command)
    semantic = COMMAND_SEMANTICS.get(base, _default)
    return semantic(exit_code, stdout, stderr)
