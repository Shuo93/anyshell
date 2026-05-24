"""M3 lifecycle tests — exec() against real shells (strongest fidelity)."""

from __future__ import annotations

import asyncio
import os

import pytest

from claude_bash.engine.shell import exec as shell_exec
from claude_bash.state.cwd_state import AbortContext

pytestmark = pytest.mark.usefixtures("bash_path")


async def _run(command, abort, state, **kw):
    kw.setdefault("skip_snapshot", True)
    sc = await shell_exec(command, abort, "bash", state, **kw)
    return await sc.result, sc


async def test_success(state, abort):
    result, sc = await _run("echo hello", abort, state)
    assert result.code == 0
    assert "hello" in result.stdout
    assert result.interrupted is False


async def test_nonzero_exit(state, abort):
    result, _ = await _run("exit 3", abort, state)
    assert result.code == 3
    assert result.interrupted is False


async def test_stderr_merged_into_stdout(state, abort):
    # File mode merges both fds into the output file.
    result, _ = await _run("echo oops >&2", abort, state)
    assert "oops" in result.stdout
    assert result.code == 0


async def test_cwd_tracking(state, abort):
    target = os.path.realpath("/tmp")
    result, _ = await _run(f"cd {target} && pwd", abort, state)
    assert result.code == 0
    assert state.cwd == os.path.realpath(target)


async def test_cwd_not_changed_without_cd(state, abort):
    before = state.cwd
    await _run("echo hi", abort, state)
    assert state.cwd == before


async def test_small_output_file_redundant(state, abort):
    result, _ = await _run("echo small", abort, state)
    # Small output fits inline; no persisted file path.
    assert result.output_file_path is None


async def test_large_output_persisted(state, abort):
    # Produce > 30 KB so the output file is not redundant.
    result, _ = await _run("for i in $(seq 1 5000); do echo line$i; done", abort, state)
    assert result.code == 0
    assert result.output_file_path is not None
    assert result.output_file_size and result.output_file_size > 30_000


async def test_heredoc(state, abort):
    result, _ = await _run("cat <<EOF\nhi-heredoc\nEOF", abort, state)
    assert "hi-heredoc" in result.stdout


async def test_pipe(state, abort):
    result, _ = await _run("printf 'a\\nb\\nc\\n' | wc -l", abort, state)
    assert result.code == 0
    assert result.stdout.strip() == "3"


async def test_timeout_sigterm(state, abort):
    result, _ = await _run("sleep 5", abort, state, timeout=300)
    assert result.code == 143
    assert "Command timed out after" in result.stderr


async def test_abort_kills(state):
    abort = AbortContext()
    sc = await shell_exec("sleep 5", abort, "bash", state, skip_snapshot=True)
    await asyncio.sleep(0.2)
    abort.abort()  # no reason => kill
    result = await sc.result
    assert result.code == 137
    assert result.interrupted is True


async def test_abort_interrupt_does_not_kill(state):
    abort = AbortContext()
    sc = await shell_exec("sleep 5", abort, "bash", state, skip_snapshot=True)
    await asyncio.sleep(0.2)
    abort.abort(reason="interrupt")  # interrupt => do NOT kill
    await asyncio.sleep(0.2)
    assert sc.status == "running"
    sc.kill()  # cleanup
    result = await sc.result
    assert result.code == 137


async def test_aborted_before_spawn(state):
    abort = AbortContext()
    abort.abort()
    sc = await shell_exec("echo hi", abort, "bash", state, skip_snapshot=True)
    result = await sc.result
    assert result.interrupted is True
    assert result.code == 145


async def test_failed_when_cwd_missing(tmp_path, monkeypatch):
    from claude_bash.state.cwd_state import EngineState

    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(tmp_path / "ct"))
    gone = tmp_path / "gone"
    gone.mkdir()
    st = EngineState(initial_cwd=str(gone))
    # Remove both cwd and original_cwd so recovery fails.
    os.rmdir(str(gone))
    abort = AbortContext()
    sc = await shell_exec("echo hi", abort, "bash", st, skip_snapshot=True)
    result = await sc.result
    assert result.pre_spawn_error is not None
    assert result.code == 1
