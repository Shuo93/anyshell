"""M5 — background tasks, incremental output polling, size watchdog."""

from __future__ import annotations

import asyncio

import pytest

from claude_bash.engine import command as command_mod
from claude_bash.engine.shell import exec as shell_exec
from claude_bash.output.disk_output import get_task_output_delta

pytestmark = pytest.mark.usefixtures("bash_path")


async def test_background_then_incremental_poll(state, abort):
    sc = await shell_exec(
        "for i in $(seq 1 30); do echo line$i; sleep 0.02; done",
        abort, "bash", state, skip_snapshot=True,
    )
    await asyncio.sleep(0.1)
    assert sc.background("task-1") is True
    assert sc.status == "backgrounded"

    # Poll incrementally until we see output.
    collected = ""
    offset = 0
    for _ in range(40):
        chunk, offset = await get_task_output_delta(sc.task_output.path, offset)
        collected += chunk
        if "line" in collected:
            break
        await asyncio.sleep(0.05)
    assert "line" in collected

    result = await sc.result
    assert result.background_task_id == "task-1"
    assert result.code == 0


async def test_background_then_kill(state, abort):
    sc = await shell_exec("sleep 10", abort, "bash", state, skip_snapshot=True)
    await asyncio.sleep(0.1)
    assert sc.background("task-2") is True
    sc.kill()
    result = await sc.result
    assert result.code == 137
    assert result.interrupted is True
    assert result.background_task_id == "task-2"


async def test_background_returns_false_when_not_running(state, abort):
    sc = await shell_exec("echo done", abort, "bash", state, skip_snapshot=True)
    await sc.result  # completes
    assert sc.background("late") is False


async def test_size_watchdog_kills_on_overflow(state, abort, monkeypatch):
    monkeypatch.setattr(command_mod, "SIZE_WATCHDOG_INTERVAL_S", 0.1)
    sc = await shell_exec("sleep 10", abort, "bash", state, skip_snapshot=True)
    await asyncio.sleep(0.1)
    # Inject a tiny cap and grow the output file past it.
    sc._max_output_bytes = 100
    with open(sc.task_output.path, "a") as f:
        f.write("Z" * 500)
    assert sc.background("task-3") is True
    result = await asyncio.wait_for(sc.result, timeout=3)
    assert result.code == 137
    assert "Background command killed: output file exceeded 5GB" in result.stderr


async def test_pipe_mode_background_spills(state, abort):
    chunks = []
    sc = await shell_exec(
        "sleep 10", abort, "bash", state,
        on_stdout=chunks.append, skip_snapshot=True,
    )
    await asyncio.sleep(0.1)
    assert sc.task_output.stdout_to_file is False
    assert sc.background("task-4") is True
    assert sc.task_output.is_overflowed is True  # spilled to disk on background
    sc.kill()
    await sc.result
