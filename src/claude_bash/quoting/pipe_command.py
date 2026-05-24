"""Port of ``src/utils/bash/bashPipeCommand.ts``.

Rearranges a piped command so that ``< /dev/null`` applies to the *first*
command in the pipeline rather than to ``eval`` itself. Bails out to a safe
single-quoted-for-eval form whenever shell-quote cannot be trusted to tokenize
the command the same way bash would.
"""

from __future__ import annotations

import re

from .._shellquote import (
    ParseEntry,
    has_malformed_tokens,
    has_shell_quote_single_quote_bug,
    quote,
    try_parse_shell_command,
)

_SHELL_VAR_RE = re.compile(r"\$[A-Za-z_{]")
_CONTROL_STRUCTURE_RE = re.compile(r"\b(for|while|until|if|case|select)\s")
_ENV_VAR_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_FD_RE = re.compile(r"^[012]$")
_CONTINUATION_RE = re.compile(r"\\+\n")


def rearrange_pipe_command(command: str) -> str:
    """Move ``< /dev/null`` after the first command in a pipeline.

    Falls back to ``single_quote_for_eval(cmd) + ' < /dev/null'`` on any
    construct shell-quote can't be trusted with (backticks, ``$()``, ``$VAR``,
    control structures, bare newlines, the single-quote bug, parse failures,
    or malformed tokens)."""
    if "`" in command:
        return _quote_with_eval_stdin_redirect(command)
    if "$(" in command:
        return _quote_with_eval_stdin_redirect(command)
    if _SHELL_VAR_RE.search(command):
        return _quote_with_eval_stdin_redirect(command)
    if _contains_control_structure(command):
        return _quote_with_eval_stdin_redirect(command)

    joined = _join_continuation_lines(command)

    if "\n" in joined:
        return _quote_with_eval_stdin_redirect(command)
    if has_shell_quote_single_quote_bug(joined):
        return _quote_with_eval_stdin_redirect(command)

    parse_result = try_parse_shell_command(joined)
    if not parse_result.success:
        return _quote_with_eval_stdin_redirect(command)

    parsed = parse_result.tokens
    if has_malformed_tokens(joined, parsed):
        return _quote_with_eval_stdin_redirect(command)

    first_pipe_index = _find_first_pipe_operator(parsed)
    if first_pipe_index <= 0:
        return _quote_with_eval_stdin_redirect(command)

    parts = [
        *_build_command_parts(parsed, 0, first_pipe_index),
        "< /dev/null",
        *_build_command_parts(parsed, first_pipe_index, len(parsed)),
    ]
    return _single_quote_for_eval(" ".join(parts))


def _find_first_pipe_operator(parsed: list[ParseEntry]) -> int:
    for i, entry in enumerate(parsed):
        if _is_operator(entry, "|"):
            return i
    return -1


def _build_command_parts(parsed: list[ParseEntry], start: int, end: int) -> list[str]:
    parts: list[str] = []
    seen_non_env_var = False

    i = start
    while i < end:
        entry = parsed[i]

        # File-descriptor redirections (2>&1, 2>/dev/null, 2> &1) kept intact.
        if (
            isinstance(entry, str)
            and _FD_RE.match(entry)
            and i + 2 < end
            and _is_operator(parsed[i + 1])
        ):
            op = parsed[i + 1]["op"]
            target = parsed[i + 2]
            if op == ">&" and isinstance(target, str) and _FD_RE.match(target):
                parts.append(f"{entry}>&{target}")
                i += 3
                continue
            if op == ">" and target == "/dev/null":
                parts.append(f"{entry}>/dev/null")
                i += 3
                continue
            if op == ">" and isinstance(target, str) and target.startswith("&"):
                fd = target[1:]
                if _FD_RE.match(fd):
                    parts.append(f"{entry}>&{fd}")
                    i += 3
                    continue

        if isinstance(entry, str):
            is_env_var = not seen_non_env_var and _is_environment_variable_assignment(entry)
            if is_env_var:
                eq_index = entry.index("=")
                name = entry[:eq_index]
                value = entry[eq_index + 1:]
                parts.append(f"{name}={quote([value])}")
            else:
                seen_non_env_var = True
                parts.append(quote([entry]))
        elif _is_operator(entry):
            if entry.get("op") == "glob" and "pattern" in entry:
                parts.append(entry["pattern"])
            else:
                parts.append(entry["op"])
                if _is_command_separator(entry["op"]):
                    seen_non_env_var = False
        i += 1

    return parts


def _is_environment_variable_assignment(s: str) -> bool:
    return bool(_ENV_VAR_ASSIGN_RE.match(s))


def _is_command_separator(op: str) -> bool:
    return op in ("&&", "||", ";")


def _is_operator(entry: object, op: str | None = None) -> bool:
    if not isinstance(entry, dict) or "op" not in entry:
        return False
    return entry["op"] == op if op is not None else True


def _contains_control_structure(command: str) -> bool:
    return bool(_CONTROL_STRUCTURE_RE.search(command))


def _quote_with_eval_stdin_redirect(command: str) -> str:
    return _single_quote_for_eval(command) + " < /dev/null"


def _single_quote_for_eval(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _join_continuation_lines(command: str) -> str:
    def repl(m: re.Match) -> str:
        match = m.group(0)
        backslash_count = len(match) - 1  # minus the newline
        if backslash_count % 2 == 1:
            return "\\" * (backslash_count - 1)
        return match

    return _CONTINUATION_RE.sub(repl, command)
