"""LLM-facing tool_result mapping — port of BashTool.tsx's
``mapToolResultToToolResultBlockParam`` plus the thrown-ShellError path
(``getErrorParts`` in toolErrors.ts).

Produces the Anthropic ``tool_result`` block the model sees: a single string
``content`` and an ``is_error`` flag.

Two paths, exactly as Claude Code:
- **Error path** (semantic error AND not an interrupt-abort): mirrors
  ``throw new ShellError('', mergedOutput, code, interrupted)`` →
  ``["Exit code N", interrupt_msg, mergedOutput, ""].filter(Boolean).join('\\n')``,
  ``is_error = True``.
- **Data path** (success / non-error exit codes / interrupt): processed stdout
  (leading blank lines stripped, right-trimmed; wrapped in <persisted-output>
  if large) + abort marker + background info, joined by '\\n';
  ``is_error = interrupted``.

Note (faithful to Claude Code): in file mode the synthetic "Command timed out
after ..." / size-kill text lives on ExecResult.stderr but is NOT included here
— BashTool only ever surfaces result.stdout (the merged fd) to the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..util.format import format_file_size

EOL = "\n"
INTERRUPT_MESSAGE_FOR_TOOL_USE = "[Request interrupted by user for tool use]"
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
PREVIEW_SIZE_BYTES = 2000
ASSISTANT_BLOCKING_BUDGET_MS = 15_000

_LEADING_BLANK_LINES = re.compile(r"^(\s*\n)+")


def generate_preview(content: str, max_bytes: int) -> tuple[str, bool]:
    """Port of generatePreview: returns (preview, has_more)."""
    if len(content) <= max_bytes:
        return content, False
    truncated = content[:max_bytes]
    last_newline = truncated.rfind("\n")
    cut = last_newline if last_newline > max_bytes * 0.5 else max_bytes
    return content[:cut], True


def build_large_tool_result_message(
    filepath: str, original_size: int, preview: str, has_more: bool
) -> str:
    """Port of buildLargeToolResultMessage (<persisted-output> wrapper)."""
    msg = f"{PERSISTED_OUTPUT_TAG}\n"
    msg += f"Output too large ({format_file_size(original_size)}). Full output saved to: {filepath}\n\n"
    msg += f"Preview (first {format_file_size(PREVIEW_SIZE_BYTES)}):\n"
    msg += preview
    msg += "\n...\n" if has_more else "\n"
    msg += PERSISTED_OUTPUT_CLOSING_TAG
    return msg


@dataclass
class ToolResultBlock:
    """The Anthropic tool_result block sent back to the model."""

    content: str
    is_error: bool
    tool_use_id: str | None = None

    def to_dict(self) -> dict:
        d: dict = {"type": "tool_result", "content": self.content, "is_error": self.is_error}
        if self.tool_use_id is not None:
            d["tool_use_id"] = self.tool_use_id
        return d


def build_tool_result(
    *,
    stdout: str,
    code: int,
    interrupted: bool,
    is_interrupt: bool,
    interpretation_is_error: bool,
    shell_reset_stderr: str = "",
    background_task_id: str | None = None,
    backgrounded_by_user: bool = False,
    assistant_auto_backgrounded: bool = False,
    background_output_path: str = "",
    persisted_output_path: str | None = None,
    persisted_output_size: int | None = None,
) -> tuple[str, bool]:
    """Build (content, is_error) for the model-facing tool_result.

    ``stdout`` is the merged fd output (file mode). ``shell_reset_stderr`` is
    the rare cwd-reset notice (Out.stderr); normally empty.
    """
    # --- Error path: semantic error and not an interrupt-abort ---
    if interpretation_is_error and not is_interrupt:
        parts = [
            f"Exit code {code}",
            INTERRUPT_MESSAGE_FOR_TOOL_USE if interrupted else "",
            stdout,
            "",
        ]
        return EOL.join(p for p in parts if p), True

    # --- Data path ---
    processed = stdout
    if processed:
        processed = _LEADING_BLANK_LINES.sub("", processed).rstrip()

    if persisted_output_path:
        preview, has_more = generate_preview(processed, PREVIEW_SIZE_BYTES)
        processed = build_large_tool_result_message(
            persisted_output_path, persisted_output_size or 0, preview, has_more
        )

    error_message = shell_reset_stderr.strip()
    if interrupted:
        if shell_reset_stderr:
            error_message += EOL
        error_message += "<error>Command was aborted before completion</error>"

    background_info = ""
    if background_task_id:
        if assistant_auto_backgrounded:
            background_info = (
                f"Command exceeded the assistant-mode blocking budget "
                f"({ASSISTANT_BLOCKING_BUDGET_MS // 1000}s) and was moved to the background "
                f"with ID: {background_task_id}. It is still running — you will be notified "
                f"when it completes. Output is being written to: {background_output_path}. "
                f"In assistant mode, delegate long-running work to a subagent or use "
                f"run_in_background to keep this conversation responsive."
            )
        elif backgrounded_by_user:
            background_info = (
                f"Command was manually backgrounded by user with ID: {background_task_id}. "
                f"Output is being written to: {background_output_path}"
            )
        else:
            background_info = (
                f"Command running in background with ID: {background_task_id}. "
                f"Output is being written to: {background_output_path}"
            )

    content = EOL.join(p for p in [processed, error_message, background_info] if p)
    return content, interrupted
