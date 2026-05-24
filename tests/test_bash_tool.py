"""M6 — BashTool.run() scenarios against real bash."""

from __future__ import annotations

import asyncio

import pytest

from claude_bash.tool.bash_tool import BashTool

pytestmark = pytest.mark.usefixtures("bash_path")


def _tool(state, **kw):
    return BashTool(state, **kw)


async def test_fast_command_returns_result(state, abort):
    tool = _tool(state)
    rr = await tool.run("echo hi", abort, skip_snapshot=True)
    assert rr.exec_result.code == 0
    assert "hi" in rr.exec_result.stdout
    assert rr.exec_result.background_task_id is None


async def test_timeout_clamped_to_max(state, abort):
    tool = _tool(state, max_timeout_ms=500)
    # Request 10 minutes but max is 0.5s; sleep 5 should time out at ~0.5s.
    rr = await tool.run("sleep 5", abort, timeout=600_000, skip_snapshot=True)
    assert rr.exec_result.code == 143
    assert "timed out" in rr.exec_result.stderr.lower()


async def test_return_code_interpretation(state, abort):
    tool = _tool(state)
    rr = await tool.run("echo hello | grep nomatch", abort, skip_snapshot=True)
    # grep exit 1 = no match, not an error.
    assert rr.exec_result.code == 1
    assert rr.return_code_interpretation == "No matches found"


async def test_explicit_background_returns_immediately(state, abort):
    captured = {}

    async def on_bg(command, sc):
        captured["command"] = command
        captured["sc"] = sc
        return "bg-123"

    tool = _tool(state, on_auto_background=on_bg)
    rr = await tool.run("sleep 5", abort, run_in_background=True, skip_snapshot=True)
    assert rr.exec_result.background_task_id == "bg-123"
    assert rr.exec_result.code == 0
    assert captured["command"] == "sleep 5"
    captured["sc"].kill()  # reap the backgrounded process


async def test_explicit_background_default_task_id(state, abort):
    # Short-lived command so it self-reaps; no callback -> default minted id.
    tool = _tool(state)
    rr = await tool.run("sleep 0.3", abort, run_in_background=True, skip_snapshot=True)
    assert rr.exec_result.background_task_id is not None
    assert rr.exec_result.background_task_id.startswith("local_bash_")


async def test_progress_callback_fires_for_slow_command(state, abort):
    progresses = []
    tool = _tool(state, progress_threshold_ms=200)
    # Emits a line every ~0.1s for ~1.5s; should outlive the threshold and
    # trigger at least one progress callback.
    rr = await tool.run(
        "for i in $(seq 1 15); do echo p$i; sleep 0.1; done",
        abort,
        on_progress=progresses.append,
        skip_snapshot=True,
    )
    assert rr.exec_result.code == 0
    assert progresses, "expected at least one progress event"
    assert progresses[-1].task_id is not None
    assert progresses[-1].timeout_ms == 120_000


async def test_timeout_auto_background(state, abort):
    # A long blocking command (not 'sleep', which is disallowed) that exceeds
    # the short timeout should auto-background via the on_auto_background hook.
    captured = {}

    async def on_bg(command, sc):
        captured["sc"] = sc
        return "auto-bg-1"

    tool = _tool(state, progress_threshold_ms=100, on_auto_background=on_bg)
    rr = await tool.run(
        "while true; do echo x; sleep 0.1; done",
        abort,
        timeout=400,
        skip_snapshot=True,
    )
    assert rr.exec_result.background_task_id == "auto-bg-1"
    captured["sc"].kill()  # reap the infinite-loop process


async def test_disable_background_runs_foreground(state, abort):
    tool = _tool(state, disable_background_tasks=True, progress_threshold_ms=100)
    # With background disabled, a timeout kills (SIGTERM) instead of backgrounding.
    rr = await tool.run("sleep 5", abort, timeout=300, skip_snapshot=True)
    assert rr.exec_result.code == 143
