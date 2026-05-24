"""Tests for tool/tool_result.py — the LLM-facing tool_result mapping
(content string + is_error), aligned with Claude Code's BashTool."""

from __future__ import annotations

import pytest

from claude_bash.tool.bash_tool import BashTool
from claude_bash.tool.tool_result import build_tool_result


# --- pure mapper --------------------------------------------------------------

def test_success_plain():
    content, is_error = build_tool_result(
        stdout="hello\n", code=0, interrupted=False, is_interrupt=False,
        interpretation_is_error=False,
    )
    assert content == "hello"
    assert is_error is False


def test_success_strips_leading_blank_lines_and_trailing():
    content, _ = build_tool_result(
        stdout="\n  \nreal output\n\n", code=0, interrupted=False,
        is_interrupt=False, interpretation_is_error=False,
    )
    assert content == "real output"


def test_semantic_error_prefixes_exit_code():
    content, is_error = build_tool_result(
        stdout="boom", code=2, interrupted=False, is_interrupt=False,
        interpretation_is_error=True,
    )
    assert content == "Exit code 2\nboom"
    assert is_error is True


def test_killed_includes_interrupt_message():
    # Killed (not an interrupt-abort): error path, interrupted=True adds the marker.
    content, is_error = build_tool_result(
        stdout="partial", code=137, interrupted=True, is_interrupt=False,
        interpretation_is_error=True,
    )
    assert content == "Exit code 137\n[Request interrupted by user for tool use]\npartial"
    assert is_error is True


def test_interrupt_abort_takes_data_path():
    # reason='interrupt' => is_interrupt True => data path, not the error path.
    content, is_error = build_tool_result(
        stdout="some out\n", code=137, interrupted=True, is_interrupt=True,
        interpretation_is_error=True,
    )
    assert "Exit code" not in content
    assert content.endswith("<error>Command was aborted before completion</error>")
    assert is_error is True


def test_background_default_message():
    content, is_error = build_tool_result(
        stdout="", code=0, interrupted=False, is_interrupt=False,
        interpretation_is_error=False,
        background_task_id="bg-1", background_output_path="/tmp/x.output",
    )
    assert content == "Command running in background with ID: bg-1. Output is being written to: /tmp/x.output"
    assert is_error is False


def test_background_assistant_mode_message():
    content, _ = build_tool_result(
        stdout="", code=0, interrupted=False, is_interrupt=False,
        interpretation_is_error=False,
        background_task_id="bg-2", background_output_path="/tmp/y.output",
        assistant_auto_backgrounded=True,
    )
    assert content.startswith("Command exceeded the assistant-mode blocking budget (15s)")
    assert "ID: bg-2" in content


def test_persisted_output_wrapped():
    big = "x" * 5000
    content, _ = build_tool_result(
        stdout=big, code=0, interrupted=False, is_interrupt=False,
        interpretation_is_error=False,
        persisted_output_path="/tmp/full.output", persisted_output_size=5000,
    )
    assert content.startswith("<persisted-output>")
    assert content.rstrip().endswith("</persisted-output>")
    assert "Full output saved to: /tmp/full.output" in content


# --- integration via BashTool.run --------------------------------------------

@pytest.mark.usefixtures("bash_path")
class TestBashToolMapping:
    async def test_success_block(self, state, abort):
        rr = await BashTool(state).run("echo hi", abort, skip_snapshot=True)
        assert rr.is_error is False
        assert "hi" in rr.content
        block = rr.to_tool_result_block("tu-1")
        assert block.to_dict() == {
            "type": "tool_result", "content": rr.content, "is_error": False, "tool_use_id": "tu-1",
        }

    async def test_failure_block_is_error(self, state, abort):
        rr = await BashTool(state).run("exit 7", abort, skip_snapshot=True)
        assert rr.is_error is True
        assert rr.content.startswith("Exit code 7")

    async def test_grep_no_match_not_error(self, state, abort):
        rr = await BashTool(state).run("echo hi | grep nomatch", abort, skip_snapshot=True)
        # grep exit 1 = no match => NOT an error in the tool_result.
        assert rr.is_error is False
        assert "Exit code" not in rr.content

    async def test_timeout_block(self, state, abort):
        rr = await BashTool(state).run("sleep 5", abort, timeout=300, skip_snapshot=True)
        assert rr.is_error is True
        assert rr.content == "Exit code 143"

    async def test_explicit_background_block(self, state, abort):
        captured = {}

        async def on_bg(command, sc):
            captured["sc"] = sc
            return "bg-x"

        rr = await BashTool(state, on_auto_background=on_bg).run(
            "sleep 0.3", abort, run_in_background=True, skip_snapshot=True
        )
        assert rr.is_error is False
        assert rr.content.startswith("Command running in background with ID: bg-x")
        captured["sc"].kill()
