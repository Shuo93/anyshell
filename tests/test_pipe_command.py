"""Tests for quoting/pipe_command.py (port of bashPipeCommand.ts)."""

from __future__ import annotations

from claude_bash.quoting.pipe_command import rearrange_pipe_command


def test_simple_pipe_rearrange():
    # `< /dev/null` moves after the first command, whole thing single-quoted.
    assert rearrange_pipe_command("rg foo | wc -l") == "'rg foo < /dev/null | wc -l'"


def test_env_var_assignment_preserved():
    assert (
        rearrange_pipe_command("FOO=bar cmd | wc")
        == "'FOO=bar cmd < /dev/null | wc'"
    )


def test_fd_redirect_preserved():
    assert (
        rearrange_pipe_command("echo a 2>&1 | tee log")
        == "'echo a 2>&1 < /dev/null | tee log'"
    )


def test_fd_redirect_devnull_preserved():
    assert (
        rearrange_pipe_command("echo a 2>/dev/null | cat")
        == "'echo a 2>/dev/null < /dev/null | cat'"
    )


def test_glob_not_quoted():
    assert rearrange_pipe_command("ls *.py | head") == "'ls *.py < /dev/null | head'"


def test_no_pipe_bails_to_eval_form():
    # No pipe operator => fall back to single-quote-for-eval + redirect.
    assert rearrange_pipe_command("echo hi") == "'echo hi' < /dev/null"


def test_command_substitution_bails():
    assert (
        rearrange_pipe_command("echo $(date) | cat")
        == "'echo $(date) | cat' < /dev/null"
    )


def test_backtick_bails():
    assert (
        rearrange_pipe_command("echo `date` | cat")
        == "'echo `date` | cat' < /dev/null"
    )


def test_shell_var_bails():
    assert (
        rearrange_pipe_command("echo $HOME | cat")
        == "'echo $HOME | cat' < /dev/null"
    )


def test_control_structure_bails():
    cmd = "for i in 1 2; do echo $i; done | cat"
    assert rearrange_pipe_command(cmd) == _single_quote(cmd) + " < /dev/null"


def test_single_quote_bug_bails():
    cmd = r"echo '\' '--evil' | cat"
    out = rearrange_pipe_command(cmd)
    assert out == _single_quote(cmd) + " < /dev/null"


def test_bang_not_corrupted_in_bail():
    # The eval-bail path uses single quotes, so ! is preserved (no \!).
    cmd = "jq 'select(.a != .b)' | cat"
    out = rearrange_pipe_command(cmd)
    # `!` inside a $VAR/`$(`-free simple pipe is actually rearranged; ensure
    # no backslash-bang corruption regardless of path.
    assert "\\!" not in out


def _single_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"
