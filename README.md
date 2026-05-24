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

## Install

Not published yet — install from a checkout:

```bash
git clone https://github.com/Shuo93/anyshell.git
cd anyshell
uv sync                 # or: pip install -e .
uv run pytest           # 143 tests
```

## Usage

Everything is asyncio-native. An `EngineState` holds the tracked cwd and session
id (per-instance — never `os.chdir`), and an `AbortContext` replaces Node's
`AbortSignal`.

### Run a command

```python
import asyncio
import claude_bash as cb


async def main():
    state = cb.EngineState()                 # defaults to os.getcwd()
    tool = cb.BashTool(state)

    # timeout is in milliseconds (default 120_000, clamped to max 600_000)
    rr = await tool.run("echo hello && ls -1", cb.AbortContext(), timeout=30_000)

    print(rr.exec_result.code)               # 0
    print(rr.exec_result.stdout)             # captured output (merged stdout+stderr)
    print(rr.content, rr.is_error)           # LLM-facing string + error flag

    # cwd is tracked across calls (via `pwd -P`), without touching the process cwd
    await tool.run("cd /tmp", cb.AbortContext())
    print(state.cwd)                         # e.g. /private/tmp (symlink-resolved)


asyncio.run(main())
```

### Build the `tool_result` block for an LLM

```python
rr = await tool.run("grep -R TODO src", cb.AbortContext())
block = rr.to_tool_result_block("toolu_abc").to_dict()
# -> {"type": "tool_result", "tool_use_id": "toolu_abc",
#     "content": "...", "is_error": False}
```

### Stream progress

```python
def on_progress(p: cb.BashProgress) -> None:
    print(f"[{p.elapsed_seconds:.0f}s] {p.total_lines} lines so far\n{p.output}")

rr = await tool.run(
    "for i in $(seq 1 20); do echo line$i; sleep 0.2; done",
    cb.AbortContext(),
    on_progress=on_progress,
)
```

### Background a command, poll its output, kill it

The lower-level `exec()` hands you the `ShellCommand` directly:

```python
abort = cb.AbortContext()
sc = await cb.exec("python -m http.server 8000", abort, "bash", state)

sc.background("server-1")     # detach: cancels the timeout, arms the 5 GB watchdog

# read new output incrementally (the BashOutput equivalent)
content, offset = await cb.get_task_output_delta(sc.task_output.path, 0)
print(content)

sc.kill()                     # process-group tree-kill
result = await sc.result      # ExecResult(code=137, interrupted=True, ...)
```

`BashTool.run(..., run_in_background=True)` returns immediately instead; supply
`on_auto_background=async fn(command, shell_command) -> task_id` to register the
task (and to capture `shell_command` for later polling/kill). The same hook fires
when a foreground command exceeds its timeout.

### Abort vs interrupt

```python
abort = cb.AbortContext()
task = asyncio.create_task(tool.run("sleep 100", abort))
await asyncio.sleep(1)

abort.abort()                       # kill -> code 137, is_error True
# abort.abort(reason="interrupt")   # do NOT kill — lets you background for partial output
```

### PowerShell

```python
# requires pwsh / powershell on PATH; provider adds -NoProfile -NonInteractive
sc = await cb.exec("Get-ChildItem", cb.AbortContext(), "powershell", state)
result = await sc.result
```

A full end-to-end script lives in [`examples/demo.py`](examples/demo.py).

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
