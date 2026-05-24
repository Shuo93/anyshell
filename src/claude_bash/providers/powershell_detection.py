"""Port of src/utils/shell/powershellDetection.ts.

Finds pwsh (PowerShell 7+) preferring it over powershell (5.1), with the Linux
snap-launcher workaround (snap can hang in subprocesses; prefer the direct
binary). Edition is inferred from the binary name without spawning.
"""

from __future__ import annotations

import os
import shutil
from typing import Literal

from ..util.platform import get_platform

PowerShellEdition = Literal["core", "desktop"]


def _probe_path(p: str) -> str | None:
    return p if os.path.isfile(p) else None


def find_powershell() -> str | None:
    pwsh = shutil.which("pwsh")
    if pwsh:
        if get_platform() == "linux":
            try:
                resolved = os.path.realpath(pwsh)
            except OSError:
                resolved = pwsh
            if pwsh.startswith("/snap/") or resolved.startswith("/snap/"):
                direct = _probe_path("/opt/microsoft/powershell/7/pwsh") or _probe_path("/usr/bin/pwsh")
                if direct:
                    try:
                        direct_resolved = os.path.realpath(direct)
                    except OSError:
                        direct_resolved = direct
                    if not direct.startswith("/snap/") and not direct_resolved.startswith("/snap/"):
                        return direct
        return pwsh

    powershell = shutil.which("powershell")
    if powershell:
        return powershell
    return None


_cache_set = False
_cached_path: str | None = None


async def get_cached_powershell_path() -> str | None:
    global _cache_set, _cached_path
    if not _cache_set:
        _cached_path = find_powershell()
        _cache_set = True
    return _cached_path


def reset_powershell_cache() -> None:
    global _cache_set, _cached_path
    _cache_set = False
    _cached_path = None


async def get_powershell_edition() -> PowerShellEdition | None:
    p = await get_cached_powershell_path()
    if not p:
        return None
    base = os.path.basename(p).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return "core" if base == "pwsh" else "desktop"
