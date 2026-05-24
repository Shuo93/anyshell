"""Faithful Python port of the npm ``shell-quote`` library (v1.8.1) plus the
safe-wrapper helpers from Claude Code's ``src/utils/bash/shellQuote.ts``.

This is load-bearing for 1:1 reliability: ``quote()`` and ``parse()`` must
reproduce shell-quote's exact behaviour (including its quirks), because the
pipe-rearrangement and quoting layers depend on them — and on the bug
*detectors* (``has_shell_quote_single_quote_bug``, ``has_malformed_tokens``)
that exist precisely to bail out when shell-quote would misparse.

``ParseEntry`` mirrors the TS type ``string | {op} | {op:'glob', pattern}``:
- a plain ``str`` for a word,
- ``{"op": "<operator>"}`` for control operators (``|``, ``&&``, ``>``, ...),
- ``{"op": "glob", "pattern": "<pat>"}`` for glob words,
- ``{"comment": "<text>"}`` for trailing comments.
"""

from __future__ import annotations

import json
import random
import re
from typing import Callable, Union

ParseEntry = Union[str, dict]

# --- npm shell-quote parse.js constants (ported verbatim) ---------------------

_CONTROL = "(?:" + "|".join([
    r"\|\|",
    r"\&\&",
    ";;",
    r"\|\&",
    r"\<\(",
    r"\<\<\<",
    ">>",
    r">\&",
    r"<\&",
    r"[&;()|<>]",
]) + ")"
_control_re = re.compile("^" + _CONTROL + "$")

# NOTE: '\\t' here is a literal backslash+t (matching the JS source). Once
# concatenated into the regex source and compiled, the '\t' becomes a tab
# escape inside the character class.
_META = "|&;()<> \\t"
_SINGLE_QUOTE = r'"((\\"|[^"])*?)"'
_DOUBLE_QUOTE = r"'((\\'|[^'])*?)'"
_hash = re.compile("^#$")

_SQ = "'"
_DQ = '"'
_DS = "$"

# Random token used only when env is a callable (object-valued substitution).
_TOKEN = "".join(format(random.getrandbits(32), "x") for _ in range(4))
_starts_with_token = re.compile("^" + re.escape(_TOKEN))


def _get_var(env, pre: str, key: str) -> str:
    r = env(key) if callable(env) else env.get(key)
    if r is None and key != "":
        r = ""
    elif r is None:
        r = "$"
    if isinstance(r, (dict, list)):
        return pre + _TOKEN + json.dumps(r) + _TOKEN
    return pre + str(r)


def _parse_internal(string: str, env, opts: dict | None) -> list[ParseEntry]:
    opts = opts or {}
    bs = opts.get("escape") or "\\"
    bareword = "(\\" + bs + "['\"" + _META + "]|[^\\s'\"" + _META + "])+"

    chunker = re.compile(
        "(" + _CONTROL + ")|(" + bareword + "|" + _SINGLE_QUOTE + "|" + _DOUBLE_QUOTE + ")+"
    )

    matches = list(_match_all(string, chunker))
    if not matches:
        return []
    if not env:
        env = {}

    commented = False
    result: list[ParseEntry] = []

    for m in matches:
        s = m.group(0)
        match_index = m.start()
        if not s or commented:
            continue
        if _control_re.match(s):
            result.append({"op": s})
            continue

        # Hand-written scanner for Bash quoting rules (ported from parse.js).
        quote: str | bool = False
        esc = False
        out = ""
        is_glob = False
        i = 0

        def parse_env_var() -> str:
            nonlocal i
            i += 1
            char = s[i] if i < len(s) else ""
            if char == "{":
                i += 1
                if (s[i] if i < len(s) else "") == "}":
                    raise ValueError("Bad substitution: " + s[i - 2:i + 1])
                varend = s.find("}", i)
                if varend < 0:
                    raise ValueError("Bad substitution: " + s[i:])
                varname = s[i:varend]
                i = varend
            elif re.match(r"[*@#?$!_-]", char):
                varname = char
                i += 1
            else:
                sliced = s[i:]
                vm = re.search(r"[^\w\d_]", sliced)
                if not vm:
                    varname = sliced
                    i = len(s)
                else:
                    varname = sliced[:vm.start()]
                    i += vm.start() - 1
            return _get_var(env, "", varname)

        commented_here = False
        early: list[ParseEntry] | dict | None = None
        op_short_circuit: dict | None = None

        while i < len(s):
            c = s[i]
            is_glob = is_glob or (not quote and (c == "*" or c == "?"))
            if esc:
                out += c
                esc = False
            elif quote:
                if c == quote:
                    quote = False
                elif quote == _SQ:
                    out += c
                else:  # double quote
                    if c == bs:
                        i += 1
                        c = s[i] if i < len(s) else ""
                        if c == _DQ or c == bs or c == _DS:
                            out += c
                        else:
                            out += bs + c
                    elif c == _DS:
                        out += parse_env_var()
                    else:
                        out += c
            elif c == _DQ or c == _SQ:
                quote = c
            elif _control_re.match(c):
                op_short_circuit = {"op": s}
                break
            elif _hash.match(c):
                commented = True
                commented_here = True
                comment_obj = {"comment": string[match_index + i + 1:]}
                if out:
                    early = [out, comment_obj]
                else:
                    early = [comment_obj]
                break
            elif c == bs:
                esc = True
            elif c == _DS:
                out += parse_env_var()
            else:
                out += c
            i += 1

        if op_short_circuit is not None:
            result.append(op_short_circuit)
            continue
        if early is not None:
            for e in early:
                result.append(e)
            continue
        if commented_here:
            continue

        if is_glob:
            result.append({"op": "glob", "pattern": out})
        else:
            result.append(out)

    return result


def _match_all(s: str, r: re.Pattern):
    """Mirror npm shell-quote's matchAll: iterate all matches, advancing past
    zero-width matches by one to avoid infinite loops."""
    pos = 0
    while pos <= len(s):
        m = r.search(s, pos)
        if m is None:
            break
        yield m
        if m.end() == m.start():
            pos = m.end() + 1
        else:
            pos = m.end()


def parse(s: str, env=None, opts: dict | None = None) -> list[ParseEntry]:
    """Port of shell-quote ``parse``. ``env`` may be a dict, a callable, or
    None. The callable path performs object-substitution token expansion."""
    mapped = _parse_internal(s, env, opts)
    if not callable(env):
        return mapped
    acc: list[ParseEntry] = []
    split_re = re.compile("(" + re.escape(_TOKEN) + ".*?" + re.escape(_TOKEN) + ")")
    for item in mapped:
        if isinstance(item, dict):
            acc.append(item)
            continue
        xs = split_re.split(item)
        xs = [x for x in xs if x]
        if len(xs) == 1:
            acc.append(xs[0])
            continue
        for x in xs:
            if _starts_with_token.match(x):
                acc.append(json.loads(x.split(_TOKEN)[1]))
            else:
                acc.append(x)
    return acc


# --- quote (npm shell-quote quote.js, ported verbatim) ------------------------

_FINAL_RE = re.compile(r"""([A-Za-z]:)?([#!"$&'()*,:;<=>?@[\\\]^`{|}])""")


def _quote_one(s: str) -> str:
    if re.search(r'["\s]', s) and "'" not in s:
        return "'" + re.sub(r"(['\\])", r"\\\1", s) + "'"
    if re.search(r"[\"'\s]", s):
        return '"' + re.sub(r"([\"\\$`!])", r"\\\1", s) + '"'
    return _FINAL_RE.sub(r"\1\\\2", s)


def quote(args) -> str:
    """Port of shell-quote ``quote`` + Claude Code's ``quote()`` wrapper.

    In practice this is only ever called with lists of strings; we also handle
    operator dicts and scalars for completeness."""
    out: list[str] = []
    for s in args:
        if isinstance(s, dict) and "op" in s:
            out.append(re.sub(r"(.)", r"\\\1", s["op"]))
            continue
        if s is None:
            s = "null"
        elif isinstance(s, bool):
            s = "true" if s else "false"
        elif not isinstance(s, str):
            s = str(s)
        out.append(_quote_one(s))
    return " ".join(out)


# --- safe wrappers (shellQuote.ts) -------------------------------------------


class ShellParseResult:
    __slots__ = ("success", "tokens", "error")

    def __init__(self, success: bool, tokens: list[ParseEntry] | None = None, error: str | None = None):
        self.success = success
        self.tokens = tokens if tokens is not None else []
        self.error = error


def try_parse_shell_command(cmd: str, env=None) -> ShellParseResult:
    try:
        return ShellParseResult(True, parse(cmd, env))
    except Exception as e:  # noqa: BLE001 — mirror TS catch-all
        return ShellParseResult(False, error=str(e) or "Unknown parse error")


def has_malformed_tokens(command: str, parsed: list[ParseEntry]) -> bool:
    """Port of ``hasMalformedTokens``. Detects shell-quote misparses
    (unbalanced delimiters / unterminated quotes) that could turn a bash
    syntax error into an injection when tokens are rebuilt."""
    in_single = False
    in_double = False
    double_count = 0
    single_count = 0
    i = 0
    while i < len(command):
        c = command[i]
        if c == "\\" and not in_single:
            i += 2
            continue
        if c == '"' and not in_single:
            double_count += 1
            in_double = not in_double
        elif c == "'" and not in_double:
            single_count += 1
            in_single = not in_single
        i += 1
    if double_count % 2 != 0 or single_count % 2 != 0:
        return True

    for entry in parsed:
        if not isinstance(entry, str):
            continue
        if entry.count("{") != entry.count("}"):
            return True
        if entry.count("(") != entry.count(")"):
            return True
        if entry.count("[") != entry.count("]"):
            return True
        if len(re.findall(r'(?<!\\)"', entry)) % 2 != 0:
            return True
        if len(re.findall(r"(?<!\\)'", entry)) % 2 != 0:
            return True
    return False


def has_shell_quote_single_quote_bug(command: str) -> bool:
    """Port of ``hasShellQuoteSingleQuoteBug``. Detects ``'\\'`` patterns that
    exploit shell-quote treating ``\\`` as an escape inside single quotes
    (bash treats it literally)."""
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        char = command[i]
        if char == "\\" and not in_single:
            i += 2
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            if not in_single:
                backslash_count = 0
                j = i - 1
                while j >= 0 and command[j] == "\\":
                    backslash_count += 1
                    j -= 1
                if backslash_count > 0 and backslash_count % 2 == 1:
                    return True
                if (
                    backslash_count > 0
                    and backslash_count % 2 == 0
                    and command.find("'", i + 1) != -1
                ):
                    return True
            i += 1
            continue
        i += 1
    return False
