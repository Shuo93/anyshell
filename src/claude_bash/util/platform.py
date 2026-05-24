"""Platform detection + Windows/POSIX path conversion (port of platform.ts and
windowsPaths.ts)."""

from __future__ import annotations

import re
import sys
from typing import Literal

Platform = Literal["macos", "linux", "windows"]


def get_platform() -> Platform:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    return "linux"


def is_windows() -> bool:
    return sys.platform == "win32"


_WIN_DRIVE_RE = re.compile(r"^([A-Za-z]):[/\\]")
_CYGDRIVE_RE = re.compile(r"^/cygdrive/([A-Za-z])(/|$)")
_POSIX_DRIVE_RE = re.compile(r"^/([A-Za-z])(/|$)")


def windows_path_to_posix_path(windows_path: str) -> str:
    """C:\\Users\\foo -> /c/Users/foo (Git Bash convention)."""
    if windows_path.startswith("\\\\"):  # UNC
        return windows_path.replace("\\", "/")
    m = _WIN_DRIVE_RE.match(windows_path)
    if m:
        drive = m.group(1).lower()
        return "/" + drive + windows_path[2:].replace("\\", "/")
    return windows_path.replace("\\", "/")


def posix_path_to_windows_path(posix_path: str) -> str:
    """/c/Users/foo -> C:\\Users\\foo."""
    if posix_path.startswith("//"):  # UNC
        return posix_path.replace("/", "\\")
    cyg = _CYGDRIVE_RE.match(posix_path)
    if cyg:
        drive = cyg.group(1).upper()
        rest = posix_path[len("/cygdrive/" + cyg.group(1)):]
        return drive + ":" + (rest or "\\").replace("/", "\\")
    drive_m = _POSIX_DRIVE_RE.match(posix_path)
    if drive_m:
        drive = drive_m.group(1).upper()
        rest = posix_path[2:]
        return drive + ":" + (rest or "\\").replace("/", "\\")
    return posix_path.replace("/", "\\")
