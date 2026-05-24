"""Port of outputLimits.ts + the bash timeout constants from timeouts.ts."""

from __future__ import annotations

import os

BASH_MAX_OUTPUT_UPPER_LIMIT = 150_000
BASH_MAX_OUTPUT_DEFAULT = 30_000

DEFAULT_TIMEOUT_MS = 120_000  # 2 minutes
MAX_TIMEOUT_MS = 600_000      # 10 minutes


def _validate_bounded_int(value: str | None, default: int, upper: int) -> int:
    if not value:
        return default
    try:
        parsed = int(value, 10)
    except ValueError:
        return default
    if parsed <= 0:
        return default
    if parsed > upper:
        return upper
    return parsed


def get_max_output_length() -> int:
    return _validate_bounded_int(
        os.environ.get("BASH_MAX_OUTPUT_LENGTH"),
        BASH_MAX_OUTPUT_DEFAULT,
        BASH_MAX_OUTPUT_UPPER_LIMIT,
    )


def get_default_bash_timeout_ms(env: dict[str, str] | None = None) -> int:
    env = env if env is not None else os.environ
    raw = env.get("BASH_DEFAULT_TIMEOUT_MS")
    if raw:
        try:
            parsed = int(raw, 10)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return DEFAULT_TIMEOUT_MS


def get_max_bash_timeout_ms(env: dict[str, str] | None = None) -> int:
    env = env if env is not None else os.environ
    raw = env.get("BASH_MAX_TIMEOUT_MS")
    if raw:
        try:
            parsed = int(raw, 10)
            if parsed > 0:
                return max(parsed, get_default_bash_timeout_ms(env))
        except ValueError:
            pass
    return max(MAX_TIMEOUT_MS, get_default_bash_timeout_ms(env))
