"""Shared fixtures: an isolated EngineState (task output under a temp dir) and
an AbortContext factory."""

from __future__ import annotations

import asyncio
import shutil

import pytest

from claude_bash.state.cwd_state import AbortContext, EngineState


@pytest.fixture(autouse=True)
async def _reap_subprocess_tasks():
    """After each async test, let pending process.wait() tasks finish so child
    transports close cleanly (no zombies, no 'Loop is closed' finalizer noise)."""
    yield
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.wait(pending, timeout=2.0)


@pytest.fixture
def state(tmp_path, monkeypatch):
    """An EngineState whose task-output dir lives under a per-test temp dir."""
    # Route the per-session task output under the test's tmp dir.
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(tmp_path / "claude-tmp"))
    work = tmp_path / "work"
    work.mkdir()
    return EngineState(initial_cwd=str(work))


@pytest.fixture
def abort():
    return AbortContext()


@pytest.fixture
def bash_path():
    p = shutil.which("bash")
    if not p:
        pytest.skip("bash not available")
    return p
