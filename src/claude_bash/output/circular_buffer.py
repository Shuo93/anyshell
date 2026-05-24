"""Port of CircularBuffer.ts — fixed-size ring buffer keeping a rolling window
of the most recent items (used for pipe-mode progress)."""

from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar("T")


class CircularBuffer(Generic[T]):
    def __init__(self, capacity: int):
        self._capacity = capacity
        self._buffer: list = [None] * capacity
        self._head = 0
        self._size = 0

    def add(self, item: T) -> None:
        self._buffer[self._head] = item
        self._head = (self._head + 1) % self._capacity
        if self._size < self._capacity:
            self._size += 1

    def add_all(self, items: list[T]) -> None:
        for item in items:
            self.add(item)

    def get_recent(self, count: int) -> list[T]:
        result: list[T] = []
        start = 0 if self._size < self._capacity else self._head
        available = min(count, self._size)
        for i in range(available):
            index = (start + self._size - available + i) % self._capacity
            result.append(self._buffer[index])
        return result

    def to_array(self) -> list[T]:
        if self._size == 0:
            return []
        result: list[T] = []
        start = 0 if self._size < self._capacity else self._head
        for i in range(self._size):
            index = (start + i) % self._capacity
            result.append(self._buffer[index])
        return result

    def clear(self) -> None:
        self._buffer = [None] * self._capacity
        self._head = 0
        self._size = 0

    def length(self) -> int:
        return self._size
