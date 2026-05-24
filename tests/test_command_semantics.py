"""Tests for tool/command_semantics.py."""

from __future__ import annotations

from claude_bash.tool.command_semantics import interpret_command_result


def test_grep_no_match_not_error():
    r = interpret_command_result("grep foo file", 1, "", "")
    assert r["is_error"] is False
    assert r["message"] == "No matches found"


def test_grep_real_error():
    r = interpret_command_result("grep foo file", 2, "", "")
    assert r["is_error"] is True


def test_diff_differs_not_error():
    r = interpret_command_result("diff a b", 1, "", "")
    assert r["is_error"] is False
    assert r["message"] == "Files differ"


def test_default_nonzero_is_error():
    r = interpret_command_result("ls /nope", 2, "", "")
    assert r["is_error"] is True
    assert "exit code 2" in r["message"]


def test_zero_is_success():
    r = interpret_command_result("echo hi", 0, "hi\n", "")
    assert r["is_error"] is False
    assert r["message"] is None


def test_last_command_in_pipe_determines_semantics():
    # `cat x | grep y` — grep determines the exit code semantics.
    r = interpret_command_result("cat x | grep y", 1, "", "")
    assert r["is_error"] is False
    assert r["message"] == "No matches found"
