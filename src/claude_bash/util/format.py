"""Port of the duration/size formatters from format.ts that the engine uses
for stderr messages (e.g. "Command timed out after 2m")."""

from __future__ import annotations

import math


def _strip_trailing_zero(s: str) -> str:
    return s[:-2] if s.endswith(".0") else s


def format_file_size(size_in_bytes: float) -> str:
    kb = size_in_bytes / 1024
    if kb < 1:
        return f"{size_in_bytes} bytes"
    if kb < 1024:
        return f"{_strip_trailing_zero(f'{kb:.1f}')}KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{_strip_trailing_zero(f'{mb:.1f}')}MB"
    gb = mb / 1024
    return f"{_strip_trailing_zero(f'{gb:.1f}')}GB"


def format_duration(
    ms: float,
    *,
    hide_trailing_zeros: bool = False,
    most_significant_only: bool = False,
) -> str:
    if ms < 60000:
        if ms == 0:
            return "0s"
        if ms < 1:
            return f"{ms / 1000:.1f}s"
        return f"{math.floor(ms / 1000)}s"

    days = math.floor(ms / 86400000)
    hours = math.floor((ms % 86400000) / 3600000)
    minutes = math.floor((ms % 3600000) / 60000)
    seconds = round((ms % 60000) / 1000)

    if seconds == 60:
        seconds = 0
        minutes += 1
    if minutes == 60:
        minutes = 0
        hours += 1
    if hours == 24:
        hours = 0
        days += 1

    hide = hide_trailing_zeros

    if most_significant_only:
        if days > 0:
            return f"{days}d"
        if hours > 0:
            return f"{hours}h"
        if minutes > 0:
            return f"{minutes}m"
        return f"{seconds}s"

    if days > 0:
        if hide and hours == 0 and minutes == 0:
            return f"{days}d"
        if hide and minutes == 0:
            return f"{days}d {hours}h"
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        if hide and minutes == 0 and seconds == 0:
            return f"{hours}h"
        if hide and seconds == 0:
            return f"{hours}h {minutes}m"
        return f"{hours}h {minutes}m {seconds}s"
    if minutes > 0:
        if hide and seconds == 0:
            return f"{minutes}m"
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"
