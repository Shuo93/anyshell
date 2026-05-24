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
