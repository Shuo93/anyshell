"""M4 — TaskOutput file-mode poller + pipe-mode buffering/spill."""

from __future__ import annotations

import asyncio
import os

import pytest

from claude_bash.output.task_output import TaskOutput


# --- pipe mode ----------------------------------------------------------------

async def test_pipe_buffer_and_get_stdout(tmp_path):
    to = TaskOutput("t1", None, str(tmp_path), stdout_to_file=False)
    to.write_stdout("hello ")
    to.write_stdout("world")
    assert await to.get_stdout() == "hello world"
    assert to.get_stderr() == ""


async def test_pipe_progress_line_counting(tmp_path):
    calls = []
    to = TaskOutput("t2", lambda *a: calls.append(a), str(tmp_path), stdout_to_file=False)
    to.write_stdout("a\nb\nc\n")
    assert to.total_lines == 3
    assert calls, "on_progress should fire"
    last_lines, all_lines, total_lines, total_bytes, incomplete = calls[-1]
    assert total_lines == 3
    assert "c" in last_lines


async def test_pipe_overflow_spills_to_disk(tmp_path):
    to = TaskOutput("t3", None, str(tmp_path), stdout_to_file=False, max_memory=50)
    to.write_stdout("a\nb\nc\n" * 20)  # > 50 chars => spill
    assert to.is_overflowed is True
    out = await to.get_stdout()
    assert "Full output saved to" in out
    assert to.get_stderr() == ""


async def test_pipe_stderr_prefixed_on_disk(tmp_path):
    to = TaskOutput("t4", None, str(tmp_path), stdout_to_file=False, max_memory=10)
    to.write_stdout("x\n" * 10)   # force spill
    to.write_stderr("boom\n")
    await to.flush()
    content = open(to.path).read()
    assert "[stderr] boom" in content


# --- file mode ----------------------------------------------------------------

async def test_file_mode_get_stdout_small_redundant(tmp_path):
    to = TaskOutput("f1", None, str(tmp_path), stdout_to_file=True)
    os.makedirs(os.path.dirname(to.path), exist_ok=True)
    with open(to.path, "w") as f:
        f.write("short output\n")
    out = await to.get_stdout()
    assert out == "short output\n"
    assert to.output_file_redundant is True


async def test_file_mode_large_not_redundant(tmp_path, monkeypatch):
    from claude_bash.tool import output_limits

    monkeypatch.setattr(output_limits, "BASH_MAX_OUTPUT_DEFAULT", 50)
    to = TaskOutput("f2", None, str(tmp_path), stdout_to_file=True)
    os.makedirs(os.path.dirname(to.path), exist_ok=True)
    with open(to.path, "w") as f:
        f.write("Z" * 500)
    out = await to.get_stdout()
    assert len(out) == 50
    assert to.output_file_redundant is False
    assert to.output_file_size == 500


async def test_file_mode_poller_reports_progress(tmp_path):
    calls = []
    to = TaskOutput("f3", lambda *a: calls.append(a), str(tmp_path), stdout_to_file=True)
    os.makedirs(os.path.dirname(to.path), exist_ok=True)
    with open(to.path, "w") as f:
        f.write("l1\nl2\nl3\nl4\nl5\nl6\n")
    TaskOutput.start_polling("f3")
    try:
        # Wait for at least one poll tick (1s interval).
        await asyncio.sleep(1.3)
    finally:
        TaskOutput.stop_polling("f3")
    assert calls, "poller should have reported progress"
    last_lines, all_lines, total_lines, total_bytes, incomplete = calls[-1]
    # The TS tick loop does an extra increment on the final no-match iteration,
    # so a 6-newline file reports 7 (verified against the real shell-quote /
    # TaskOutput logic in Node). We match that behavior exactly.
    assert total_lines == 7
    assert total_bytes == os.path.getsize(to.path)
    assert incomplete is False
    to.clear()
