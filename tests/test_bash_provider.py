"""Tests for providers/bash_provider.py — command assembly + spawn args."""

from __future__ import annotations

import os

import pytest

from claude_bash.providers.bash_provider import (
    BashShellProvider,
    create_bash_shell_provider,
)


async def test_no_snapshot_adds_login_shell():
    provider = BashShellProvider("/bin/bash", snapshot_path=None)
    built = await provider.build_exec_command("echo hi", id="abcd")
    assert "source " not in built["command_string"]
    assert "shopt -u extglob 2>/dev/null || true" in built["command_string"]
    assert "eval 'echo hi'" in built["command_string"]
    assert built["command_string"].rstrip().endswith(
        f"pwd -P >| {built['cwd_file_path']}"
    ) or "pwd -P >|" in built["command_string"]
    # No snapshot => login shell (-l) is added.
    assert provider.get_spawn_args(built["command_string"])[:2] == ["-c", "-l"]


async def test_with_snapshot_sources_and_skips_login(tmp_path):
    snap = tmp_path / "snapshot.sh"
    snap.write_text("# snapshot\n")
    provider = BashShellProvider("/bin/bash", snapshot_path=str(snap))
    built = await provider.build_exec_command("echo hi", id="ef01")
    assert f"source {snap} 2>/dev/null || true" in built["command_string"]
    args = provider.get_spawn_args(built["command_string"])
    assert args[0] == "-c"
    assert "-l" not in args  # snapshot present => no login shell


async def test_missing_snapshot_falls_back_to_login(tmp_path):
    # Snapshot path set but file does not exist => behave as no snapshot.
    provider = BashShellProvider("/bin/bash", snapshot_path=str(tmp_path / "gone.sh"))
    built = await provider.build_exec_command("echo hi", id="2222")
    assert "source " not in built["command_string"]
    assert provider.get_spawn_args(built["command_string"])[:2] == ["-c", "-l"]


async def test_zsh_uses_setopt_extglob():
    provider = BashShellProvider("/bin/zsh", snapshot_path=None)
    built = await provider.build_exec_command("echo hi", id="3333")
    assert "setopt NO_EXTENDED_GLOB 2>/dev/null || true" in built["command_string"]


async def test_heredoc_not_given_stdin_redirect():
    provider = BashShellProvider("/bin/bash", snapshot_path=None)
    cmd = "cat <<EOF\nhi\nEOF"
    built = await provider.build_exec_command(cmd, id="4444")
    # heredoc is single-quoted and gets no `< /dev/null`.
    assert "eval 'cat <<EOF\nhi\nEOF'" in built["command_string"]


async def test_pipe_command_rearranged():
    provider = BashShellProvider("/bin/bash", snapshot_path=None)
    built = await provider.build_exec_command("rg foo | wc -l", id="5555")
    assert "eval 'rg foo < /dev/null | wc -l'" in built["command_string"]


async def test_cwd_file_path_in_tmpdir():
    provider = BashShellProvider("/bin/bash", snapshot_path=None)
    built = await provider.build_exec_command("pwd", id="dead")
    assert os.path.basename(built["cwd_file_path"]) == "claude-dead-cwd"


async def test_session_env_overrides():
    provider = BashShellProvider(
        "/bin/bash", snapshot_path=None, session_env_vars={"FOO": "bar"}
    )
    env = await provider.get_environment_overrides("echo hi")
    assert env == {"FOO": "bar"}


async def test_create_provider_skip_snapshot():
    provider = await create_bash_shell_provider("/bin/bash", skip_snapshot=True)
    assert provider.type == "bash"
    assert provider.detached is True
    assert provider._last_snapshot_file_path is None
