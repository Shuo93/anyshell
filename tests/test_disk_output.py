"""M4 — DiskTaskOutput drain queue, 5 GB cap, flush, and read helpers."""

from __future__ import annotations

import os

import pytest

from claude_bash.output import disk_output
from claude_bash.output.disk_output import (
    DiskTaskOutput,
    get_task_output,
    get_task_output_delta,
    get_task_output_size,
    init_task_output,
)


async def test_append_and_flush(tmp_path):
    path = str(tmp_path / "a.output")
    d = DiskTaskOutput(path)
    d.append("hello ")
    d.append("world\n")
    await d.flush()
    assert open(path).read() == "hello world\n"


async def test_cap_truncates(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_output, "MAX_TASK_OUTPUT_BYTES", 20)
    path = str(tmp_path / "capped.output")
    d = DiskTaskOutput(path)
    d.append("x" * 30)   # exceeds cap immediately
    d.append("DROPPED")  # must be ignored once capped
    await d.flush()
    content = open(path).read()
    assert "disk cap" in content
    assert "DROPPED" not in content


async def test_delta_incremental(tmp_path):
    path = str(tmp_path / "d.output")
    d = DiskTaskOutput(path)
    d.append("first\n")
    await d.flush()
    content, offset = await get_task_output_delta(path, 0)
    assert content == "first\n"
    assert offset == 6
    d.append("second\n")
    await d.flush()
    content2, offset2 = await get_task_output_delta(path, offset)
    assert content2 == "second\n"
    assert offset2 == 13


async def test_get_task_output_omission_notice(tmp_path):
    path = str(tmp_path / "big.output")
    with open(path, "w") as f:
        f.write("A" * 100 + "\n" + "B" * 100)
    out = await get_task_output(path, max_bytes=50)
    assert out.startswith("[")
    assert "earlier output omitted" in out


async def test_get_task_output_size(tmp_path):
    path = str(tmp_path / "s.output")
    assert await get_task_output_size(path) == 0
    with open(path, "w") as f:
        f.write("12345")
    assert await get_task_output_size(path) == 5


async def test_init_task_output_exclusive(tmp_path):
    path = str(tmp_path / "sub" / "init.output")
    await init_task_output(path)
    assert os.path.exists(path)
    with pytest.raises(FileExistsError):
        await init_task_output(path)  # O_EXCL => second create fails
