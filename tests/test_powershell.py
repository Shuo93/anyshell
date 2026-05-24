"""M7 — PowerShell provider command building + detection (mock-based)."""

from __future__ import annotations

import pytest

from claude_bash.providers import powershell_detection as detect
from claude_bash.providers.powershell_provider import (
    build_powershell_args,
    create_powershell_provider,
    _encode_powershell_command,
)


def test_build_powershell_args():
    assert build_powershell_args("Get-Date") == [
        "-NoProfile", "-NonInteractive", "-Command", "Get-Date",
    ]


def test_encode_roundtrip():
    encoded = _encode_powershell_command("Write-Host hi")
    import base64
    assert base64.b64decode(encoded).decode("utf-16-le") == "Write-Host hi"
    # base64 alphabet only — survives any quoting layer.
    assert all(c.isalnum() or c in "+/=" for c in encoded)


async def test_build_exec_command_non_sandbox():
    provider = create_powershell_provider("/usr/bin/pwsh")
    built = await provider.build_exec_command("Get-ChildItem", id="ab12")
    cs = built["command_string"]
    assert cs.startswith("Get-ChildItem")
    assert "$LASTEXITCODE" in cs
    assert "Out-File -FilePath" in cs
    assert cs.rstrip().endswith("exit $_ec")
    assert built["cwd_file_path"].endswith("claude-pwd-ps-ab12")
    assert provider.detached is False


async def test_get_spawn_args_wraps_command():
    provider = create_powershell_provider("/usr/bin/pwsh")
    built = await provider.build_exec_command("echo hi", id="cd34")
    args = provider.get_spawn_args(built["command_string"])
    assert args[:3] == ["-NoProfile", "-NonInteractive", "-Command"]


async def test_sandbox_uses_encoded_command():
    provider = create_powershell_provider("/usr/bin/pwsh")
    built = await provider.build_exec_command(
        "Get-Date", id="ef56", sandbox_tmp_dir="/tmp/sbx", use_sandbox=True
    )
    cs = built["command_string"]
    assert "-EncodedCommand" in cs
    assert "-NoProfile" in cs
    assert built["cwd_file_path"] == "/tmp/sbx/claude-pwd-ps-ef56"


async def test_session_env_overrides():
    provider = create_powershell_provider("/usr/bin/pwsh", session_env_vars={"X": "1"})
    env = await provider.get_environment_overrides("Get-Date")
    assert env == {"X": "1"}


async def test_edition_detection_pwsh(monkeypatch):
    async def fake_path():
        return "/opt/microsoft/powershell/7/pwsh"
    monkeypatch.setattr(detect, "get_cached_powershell_path", fake_path)
    assert await detect.get_powershell_edition() == "core"


async def test_edition_detection_desktop(monkeypatch):
    async def fake_path():
        return r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    monkeypatch.setattr(detect, "get_cached_powershell_path", fake_path)
    assert await detect.get_powershell_edition() == "desktop"


async def test_edition_none_when_absent(monkeypatch):
    async def fake_path():
        return None
    monkeypatch.setattr(detect, "get_cached_powershell_path", fake_path)
    assert await detect.get_powershell_edition() is None
