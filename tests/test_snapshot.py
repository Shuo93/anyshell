"""Tests for providers/snapshot.py — script generation + create/run."""

from __future__ import annotations

import os
import re
import shutil
import subprocess

import pytest

from claude_bash.providers import snapshot


def test_config_file_selection():
    assert snapshot._get_config_file("/bin/zsh").endswith(".zshrc")
    assert snapshot._get_config_file("/usr/bin/bash").endswith(".bashrc")
    assert snapshot._get_config_file("/bin/sh").endswith(".profile")


def test_script_contains_core_sections_bash():
    script = snapshot._get_snapshot_script("/bin/bash", "/tmp/snap.sh", config_exists=True)
    assert "SNAPSHOT_FILE='/tmp/snap.sh'" in script
    assert 'source "' in script  # sources the config file
    assert "unalias -a 2>/dev/null || true" in script
    assert "# Functions" in script
    assert "shopt -s expand_aliases" in script
    assert "export PATH=" in script
    # Bash path uses base64 function encoding.
    assert "base64" in script


def test_script_zsh_uses_typeset_and_setopt():
    script = snapshot._get_snapshot_script("/bin/zsh", "/tmp/s.sh", config_exists=True)
    assert "typeset +f" in script
    assert "setopt | sed 's/^/setopt /'" in script


def test_script_no_config_bash_still_expands_aliases():
    script = snapshot._get_snapshot_script("/bin/bash", "/tmp/s.sh", config_exists=False)
    assert "# No user config file to source" in script
    assert "shopt -s expand_aliases" in script


async def test_create_snapshot_failure_returns_none(monkeypatch):
    snapshot._reset_snapshot_cache_for_test()

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode=1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(snapshot.subprocess, "run", fake_run)
    result = await snapshot.create_and_save_snapshot("/bin/bash")
    assert result is None


async def test_create_snapshot_success(monkeypatch):
    snapshot._reset_snapshot_cache_for_test()

    def fake_run(args, **kwargs):
        # Extract SNAPSHOT_FILE='...' from the script and create it, mimicking
        # the real shell writing the snapshot.
        script = args[3]
        m = re.search(r"SNAPSHOT_FILE='([^']+)'", script)
        assert m, "script must embed the snapshot path"
        path = m.group(1)
        with open(path, "w") as f:
            f.write("# Snapshot file\n")
        return subprocess.CompletedProcess(args, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(snapshot.subprocess, "run", fake_run)
    result = await snapshot.create_and_save_snapshot("/bin/bash")
    assert result is not None
    assert os.path.exists(result)
    os.unlink(result)


@pytest.mark.skipif(not shutil.which("bash"), reason="bash not available")
async def test_create_snapshot_real_bash():
    """Integration: run the real snapshot script under bash."""
    snapshot._reset_snapshot_cache_for_test()
    bash = shutil.which("bash")
    result = await snapshot.create_and_save_snapshot(bash)
    assert result is not None, "snapshot creation should succeed with real bash"
    content = open(result).read()
    assert "# Snapshot file" in content
    assert "export PATH=" in content
    os.unlink(result)
