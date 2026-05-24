"""Per-engine state: cwd tracking + abort context + session env vars.

Replaces Claude Code's global bootstrap/state.js (getCwd/setCwdState/
getOriginalCwd/getSessionId). Crucially, cwd is tracked **per EngineState
instance** — never via ``os.chdir`` — so multiple engines can coexist without
process-wide side effects.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import unicodedata
import uuid

from . import paths


def _normalize(path: str) -> str:
    try:
        resolved = str(pathlib.Path(path).resolve())
    except OSError:
        resolved = os.path.abspath(path)
    return unicodedata.normalize("NFC", resolved)


class EngineState:
    """Holds cwd / original_cwd / session_id and per-session env overrides."""

    def __init__(self, initial_cwd: str | None = None, session_id: str | None = None):
        cwd = initial_cwd or os.getcwd()
        self._cwd = _normalize(cwd)
        self._original_cwd = self._cwd
        # Captured once and never regenerated (mirrors the memoized session id;
        # keeps background-task output paths stable across a /clear-equivalent).
        self.session_id = session_id or uuid.uuid4().hex
        # Vars set via a /env-equivalent; applied to children, not this process.
        self.session_env_vars: dict[str, str] = {}

    @property
    def cwd(self) -> str:
        return self._cwd

    @property
    def original_cwd(self) -> str:
        return self._original_cwd

    def set_cwd(self, new_cwd: str) -> None:
        self._cwd = _normalize(new_cwd)

    @property
    def task_output_dir(self) -> str:
        return paths.task_output_dir(self._original_cwd, self.session_id)

    def task_output_path(self, task_id: str) -> str:
        return os.path.join(self.task_output_dir, f"{task_id}.output")


class AbortContext:
    """Replaces Node's AbortSignal. ``abort(reason='interrupt')`` signals that
    a running command should be backgrounded rather than killed."""

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self._reason: str | None = None

    def abort(self, reason: str | None = None) -> None:
        self._reason = reason
        self.event.set()

    @property
    def is_aborted(self) -> bool:
        return self.event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    async def wait(self) -> None:
        await self.event.wait()
