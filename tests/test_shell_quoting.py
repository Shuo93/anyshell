"""Tests for quoting/shell_quoting.py (port of shellQuoting.ts)."""

from __future__ import annotations

from claude_bash.quoting.shell_quoting import (
    contains_heredoc,
    contains_multiline_string,
    has_stdin_redirect,
    quote_shell_command,
    rewrite_windows_null_redirect,
    should_add_stdin_redirect,
)


def test_contains_heredoc_true():
    assert contains_heredoc("cat <<EOF\nhi\nEOF") is True
    assert contains_heredoc("cat <<'EOF'\nhi\nEOF") is True
    assert contains_heredoc("cat <<-EOF\nhi\nEOF") is True


def test_contains_heredoc_excludes_bitshift():
    assert contains_heredoc("echo $((1 << 2))") is False
    assert contains_heredoc("x=$((a << b))") is False


def test_contains_multiline_string():
    assert contains_multiline_string("echo 'line1\nline2'") is True
    assert contains_multiline_string('echo "line1\nline2"') is True
    assert contains_multiline_string("echo single line") is False


def test_quote_shell_command_regular():
    # `<` is escaped to `\<` by shell-quote so it becomes an eval argument.
    assert quote_shell_command("echo hello") == "'echo hello' \\< /dev/null"
    assert quote_shell_command("echo hello", add_stdin_redirect=False) == "'echo hello'"


def test_quote_shell_command_heredoc_no_stdin_redirect():
    cmd = "cat <<EOF\nhi\nEOF"
    out = quote_shell_command(cmd)
    assert "< /dev/null" not in out
    assert out.startswith("'") and out.endswith("'")


def test_quote_shell_command_multiline_preserves_bang():
    # jq filter with ! must NOT become \! (heredoc/multiline single-quote path).
    cmd = "jq 'select(.a\n!= .b)'"
    out = quote_shell_command(cmd)
    assert "\\!" not in out


def test_has_stdin_redirect():
    assert has_stdin_redirect("cat < file") is True
    assert has_stdin_redirect("cat <<EOF") is False        # heredoc
    assert has_stdin_redirect("diff <(a) <(b)") is False   # process subst
    assert has_stdin_redirect("echo hi") is False


def test_should_add_stdin_redirect():
    assert should_add_stdin_redirect("echo hi") is True
    assert should_add_stdin_redirect("cat < file") is False
    assert should_add_stdin_redirect("cat <<EOF\nx\nEOF") is False


def test_rewrite_windows_null_redirect():
    assert rewrite_windows_null_redirect("ls 2>nul") == "ls 2>/dev/null"
    assert rewrite_windows_null_redirect("ls > NUL") == "ls > /dev/null"
    assert rewrite_windows_null_redirect("cmd &>nul") == "cmd &>/dev/null"
    # Must NOT touch these:
    assert rewrite_windows_null_redirect("echo >null") == "echo >null"
    assert rewrite_windows_null_redirect("cat nul.txt") == "cat nul.txt"
    assert rewrite_windows_null_redirect("echo >nul.txt") == "echo >nul.txt"
