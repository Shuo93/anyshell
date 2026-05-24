# claude-bash

A Python (asyncio) 1:1 port of Claude Code's Bash + PowerShell tool execution
engine — the reliability layer beneath the LLM, ported faithfully from the
TypeScript source.

It reproduces, byte-for-byte where it matters:

- **Shell snapshot** — captures the user's aliases / functions / shell options /
  PATH so commands behave like the interactive shell without paying login-shell
  cost on every call.
- **Quoting + eval-wrapping** — a port of npm `shell-quote`, heredoc/multiline
  handling, pipe-aware `< /dev/null` redirection, and the bug detectors that
  bail out when shell-quote would misparse.
- **File-mode + pipe-mode output** — both fds streamed to a temp file with
  tail-polling progress, in-memory→disk overflow, and a 5 GB disk cap.
- **Process lifecycle** — timeout→SIGTERM, process-group tree-kill, abort vs
  "interrupt", exact exit-code mapping, cwd tracking via `pwd -P`.
- **Background tasks** — `run_in_background`, incremental output polling, kill,
  size watchdog, and a timeout→auto-background hook (no UI coupling).

Permission/security classification, sandboxing, the LLM, and the agent/REPL UI
are intentionally **out of scope** — this is just the execution engine.

## Status

In development. See `tests/` for the mock-based scenario matrix.

## LLM-facing output (tool_result)

`BashTool.run()` returns a `RunResult` carrying the rich `ExecResult` plus the
exact `tool_result` block Claude Code sends to the model — a single-string
`content` and an `is_error` flag — aligned with `BashTool.tsx`:

```python
rr = await tool.run("ls /nope", abort_ctx)
rr.content          # e.g. "Exit code 2\nls: /nope: No such file or directory"
rr.is_error         # True on a semantic failure; False for grep-exit-1, etc.
rr.to_tool_result_block("toolu_123").to_dict()
#   {"type": "tool_result", "tool_use_id": "toolu_123",
#    "content": "...", "is_error": True}
```

Faithful details: success output is leading-blank-stripped + right-trimmed and
wrapped in `<persisted-output>` when large; semantic errors are prefixed with
`Exit code N` (grep=1/diff=1/etc. are *not* errors); an interrupt-abort takes
the data path with the `<error>Command was aborted before completion</error>`
marker; background runs return a "running in background with ID …" message. As
in Claude Code, the synthetic "Command timed out after …" text stays on
`ExecResult.stderr` (for raw `exec()` users) and is **not** folded into the
model-facing content — a timeout simply shows `Exit code 143`.

## Layout

```
src/claude_bash/
  _shellquote.py     # port of npm shell-quote
  quoting/           # shellQuoting.ts + bashPipeCommand.ts
  providers/         # bash/powershell providers, detection, snapshot
  output/            # TaskOutput + DiskTaskOutput + shared poller
  engine/            # exec() + ShellCommand lifecycle
  state/             # per-engine cwd + abort + subprocess env
  tool/              # BashTool high-level API + output limits + semantics
  util/              # fs read helpers, format, platform
```

## Requirements

Python 3.11+. Single runtime dependency: `psutil` (Windows tree-kill + POSIX
fallback).
