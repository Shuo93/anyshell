"""Port of ``src/utils/bash/shellQuoting.ts``.

Quotes a shell command for ``eval`` while preserving heredocs and multiline
strings (which shell-quote would corrupt by escaping ``!`` -> ``\\!``), and
provides the stdin-redirect and Windows ``nul`` rewrite helpers.
"""

from __future__ import annotations

import re

from .._shellquote import quote

# Bit-shift / arithmetic guards that must NOT be treated as heredocs.
_BITSHIFT_DIGIT = re.compile(r"\d\s*<<\s*\d")
_BITSHIFT_TEST = re.compile(r"\[\[\s*\d+\s*<<\s*\d+\s*\]\]")
_BITSHIFT_ARITH = re.compile(r"\$\(\(.*<<.*\)\)")
_HEREDOC_RE = re.compile(r"""<<-?\s*(?:(['"]?)(\w+)\1|\\(\w+))""")

_SINGLE_QUOTE_MULTILINE = re.compile(r"'(?:[^'\\]|\\.)*\n(?:[^'\\]|\\.)*'")
_DOUBLE_QUOTE_MULTILINE = re.compile(r'"(?:[^"\\]|\\.)*\n(?:[^"\\]|\\.)*"')

_STDIN_REDIRECT_RE = re.compile(r"(?:^|[\s;&|])<(?![<(])\s*\S+")

# Windows CMD-style `>nul` redirects -> POSIX `/dev/null`. See claude-code#4928.
_NUL_REDIRECT_RE = re.compile(r"(\d?&?>+\s*)[Nn][Uu][Ll](?=\s|$|[|&;)\n])")


def contains_heredoc(command: str) -> bool:
    """Detect heredoc patterns (``<<EOF``, ``<<'EOF'``, ``<<-EOF``, ...) while
    excluding bit-shift / arithmetic uses of ``<<``."""
    if (
        _BITSHIFT_DIGIT.search(command)
        or _BITSHIFT_TEST.search(command)
        or _BITSHIFT_ARITH.search(command)
    ):
        return False
    return bool(_HEREDOC_RE.search(command))


def contains_multiline_string(command: str) -> bool:
    """Detect a quoted string spanning a real newline."""
    return bool(
        _SINGLE_QUOTE_MULTILINE.search(command)
        or _DOUBLE_QUOTE_MULTILINE.search(command)
    )


def quote_shell_command(command: str, add_stdin_redirect: bool = True) -> str:
    """Quote ``command`` for ``eval``. Heredocs/multiline strings are single-
    quoted (escaping only ``'``) to avoid shell-quote's ``!`` corruption;
    everything else goes through shell-quote ``quote()``."""
    if contains_heredoc(command) or contains_multiline_string(command):
        escaped = command.replace("'", "'\"'\"'")
        quoted = "'" + escaped + "'"
        if contains_heredoc(command):
            # Heredocs provide their own stdin; never add `< /dev/null`.
            return quoted
        return f"{quoted} < /dev/null" if add_stdin_redirect else quoted

    if add_stdin_redirect:
        return quote([command, "<", "/dev/null"])
    return quote([command])


def has_stdin_redirect(command: str) -> bool:
    """True if the command already has a ``< file`` redirect (not ``<<`` heredoc
    or ``<(`` process substitution)."""
    return bool(_STDIN_REDIRECT_RE.search(command))


def should_add_stdin_redirect(command: str) -> bool:
    """Whether ``< /dev/null`` can be safely appended."""
    if contains_heredoc(command):
        return False
    if has_stdin_redirect(command):
        return False
    return True


def rewrite_windows_null_redirect(command: str) -> str:
    """Rewrite ``2>nul`` / ``>NUL`` (hallucinated CMD syntax) to ``/dev/null``."""
    return _NUL_REDIRECT_RE.sub(r"\1/dev/null", command)
