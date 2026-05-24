"""Path helpers ported from envUtils.ts + permissions/filesystem.ts.

These compute the Claude config home and the per-session task-output directory
(``/tmp/claude-<uid>/<sanitized-cwd>/<session-id>/tasks``)."""

from __future__ import annotations

import functools
import hashlib
import os
import re
import tempfile
import unicodedata

from ..util.platform import get_platform

_MAX_SANITIZED_LENGTH = 50


@functools.lru_cache(maxsize=1)
def get_claude_config_home_dir() -> str:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude"
    )
    return unicodedata.normalize("NFC", base)


def get_claude_temp_dir_name() -> str:
    if get_platform() == "windows":
        return "claude"
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return f"claude-{uid}"


def get_claude_temp_dir() -> str:
    base = os.environ.get("CLAUDE_CODE_TMPDIR") or (
        tempfile.gettempdir() if get_platform() == "windows" else "/tmp"
    )
    try:
        base = os.path.realpath(base)
    except OSError:
        pass
    return os.path.join(base, get_claude_temp_dir_name())


def _simple_hash(name: str) -> str:
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]


def sanitize_path(name: str) -> str:
    """Port of sanitizePath: non-alnum -> '-', hashed suffix if too long."""
    sanitized = re.sub(r"[^a-zA-Z0-9]", "-", name)
    if len(sanitized) <= _MAX_SANITIZED_LENGTH:
        return sanitized
    return f"{sanitized[:_MAX_SANITIZED_LENGTH]}-{_simple_hash(name)}"


def project_temp_dir(original_cwd: str) -> str:
    return os.path.join(get_claude_temp_dir(), sanitize_path(original_cwd))


def task_output_dir(original_cwd: str, session_id: str) -> str:
    return os.path.join(project_temp_dir(original_cwd), session_id, "tasks")
