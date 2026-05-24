"""Port of the string helpers from stringUtils.ts used by the engine:
``safe_join_lines`` and ``EndTruncatingAccumulator``."""

from __future__ import annotations

MAX_STRING_LENGTH = 2 ** 25  # 33,554,432


def safe_join_lines(lines: list[str], delimiter: str = ",", max_size: int = MAX_STRING_LENGTH) -> str:
    truncation_marker = "...[truncated]"
    result = ""
    for line in lines:
        delimiter_to_add = delimiter if result else ""
        full_addition = delimiter_to_add + line
        if len(result) + len(full_addition) <= max_size:
            result += full_addition
        else:
            remaining = max_size - len(result) - len(delimiter_to_add) - len(truncation_marker)
            if remaining > 0:
                result += delimiter_to_add + line[:remaining] + truncation_marker
            else:
                result += truncation_marker
            return result
    return result


class EndTruncatingAccumulator:
    """Accumulates text up to ``max_size`` chars; further input is dropped and a
    ``[output truncated - NKB removed]`` marker is appended by ``str()``."""

    def __init__(self, max_size: int = MAX_STRING_LENGTH):
        self._max_size = max_size
        self._content = ""
        self._is_truncated = False
        self._total_bytes_received = 0

    def append(self, data: str) -> None:
        self._total_bytes_received += len(data)
        if self._is_truncated and len(self._content) >= self._max_size:
            return
        if len(self._content) + len(data) > self._max_size:
            remaining = self._max_size - len(self._content)
            if remaining > 0:
                self._content += data[:remaining]
            self._is_truncated = True
        else:
            self._content += data

    def __str__(self) -> str:
        if not self._is_truncated:
            return self._content
        truncated_bytes = self._total_bytes_received - self._max_size
        truncated_kb = round(truncated_bytes / 1024)
        return self._content + f"\n... [output truncated - {truncated_kb}KB removed]"

    def clear(self) -> None:
        self._content = ""
        self._is_truncated = False
        self._total_bytes_received = 0

    @property
    def length(self) -> int:
        return len(self._content)

    @property
    def truncated(self) -> bool:
        return self._is_truncated

    @property
    def total_bytes(self) -> int:
        return self._total_bytes_received
