"""Port of src/utils/bash/ShellSnapshot.ts.

Captures the user's shell aliases / functions / options / PATH into a temp
script that subsequent commands ``source``, so they behave like the user's
interactive shell without paying login-shell cost on every call. On failure the
caller falls back to a login shell (``-l``).

Divergence from the TS original: the ant-only embedded ripgrep/bfs/ugrep
(argv0-dispatch) integration is omitted — the Python port has no bundled binary.
User-defined rg aliases are still captured normally.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import random
import subprocess
import time

from ..state.paths import get_claude_config_home_dir
from ..state.subprocess_env import subprocess_env

SNAPSHOT_CREATION_TIMEOUT_S = 10.0


def _get_config_file(shell_path: str) -> str:
    if "zsh" in shell_path:
        name = ".zshrc"
    elif "bash" in shell_path:
        name = ".bashrc"
    else:
        name = ".profile"
    return os.path.join(os.path.expanduser("~"), name)


# User functions/options/aliases dump. Kept byte-faithful to the TS heredocs.
_ZSH_USER_CONTENT = r'''
      echo "# Functions" >> "$SNAPSHOT_FILE"
      typeset -f > /dev/null 2>&1
      typeset +f | grep -vE '^_[^_]' | while read func; do
        typeset -f "$func" >> "$SNAPSHOT_FILE"
      done
      echo "# Shell Options" >> "$SNAPSHOT_FILE"
      setopt | sed 's/^/setopt /' | head -n 1000 >> "$SNAPSHOT_FILE"
'''

_BASH_USER_CONTENT = r'''
      echo "# Functions" >> "$SNAPSHOT_FILE"
      declare -f > /dev/null 2>&1
      declare -F | cut -d' ' -f3 | grep -vE '^_[^_]' | while read func; do
        encoded_func=$(declare -f "$func" | base64 )
        echo "eval \"\$(echo '$encoded_func' | base64 -d)\" > /dev/null 2>&1" >> "$SNAPSHOT_FILE"
      done
      echo "# Shell Options" >> "$SNAPSHOT_FILE"
      shopt -p | head -n 1000 >> "$SNAPSHOT_FILE"
      set -o | grep "on" | awk '{print "set -o " $1}' | head -n 1000 >> "$SNAPSHOT_FILE"
      echo "shopt -s expand_aliases" >> "$SNAPSHOT_FILE"
'''

_ALIAS_CONTENT = r'''
      echo "# Aliases" >> "$SNAPSHOT_FILE"
      if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
        alias | grep -v "='winpty " | sed 's/^alias //g' | sed 's/^/alias -- /' | head -n 1000 >> "$SNAPSHOT_FILE"
      else
        alias | sed 's/^alias //g' | sed 's/^/alias -- /' | head -n 1000 >> "$SNAPSHOT_FILE"
      fi
'''


def _get_user_snapshot_content(config_file: str) -> str:
    is_zsh = config_file.endswith(".zshrc")
    content = _ZSH_USER_CONTENT if is_zsh else _BASH_USER_CONTENT
    return content + _ALIAS_CONTENT


def _shell_single_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _get_claude_code_snapshot_content() -> str:
    # PATH export only. (No bundled rg; user rg aliases are captured above.)
    path_value = os.environ.get("PATH", "")
    return f'''
      echo "export PATH={_shell_single_quote(path_value)}" >> "$SNAPSHOT_FILE"
'''


def _get_snapshot_script(shell_path: str, snapshot_path: str, config_exists: bool) -> str:
    config_file = _get_config_file(shell_path)
    is_zsh = config_file.endswith(".zshrc")

    if config_exists:
        user_content = _get_user_snapshot_content(config_file)
    elif not is_zsh:
        # Bash without a config: still force alias expansion.
        user_content = 'echo "shopt -s expand_aliases" >> "$SNAPSHOT_FILE"'
    else:
        user_content = ""

    claude_content = _get_claude_code_snapshot_content()
    source_line = (
        f'source "{config_file}" < /dev/null'
        if config_exists
        else "# No user config file to source"
    )

    return f'''SNAPSHOT_FILE={_shell_single_quote(snapshot_path)}
      {source_line}

      echo "# Snapshot file" >| "$SNAPSHOT_FILE"

      echo "# Unset all aliases to avoid conflicts with functions" >> "$SNAPSHOT_FILE"
      echo "unalias -a 2>/dev/null || true" >> "$SNAPSHOT_FILE"

      {user_content}

      {claude_content}

      if [ ! -f "$SNAPSHOT_FILE" ]; then
        echo "Error: Snapshot file was not created at $SNAPSHOT_FILE" >&2
        exit 1
      fi
    '''


def _make_snapshot_path(bin_shell: str) -> str:
    if "zsh" in bin_shell:
        shell_type = "zsh"
    elif "bash" in bin_shell:
        shell_type = "bash"
    else:
        shell_type = "sh"
    timestamp = int(time.time() * 1000)
    random_id = format(random.getrandbits(28), "x")[:6]
    snapshots_dir = os.path.join(get_claude_config_home_dir(), "shell-snapshots")
    return os.path.join(snapshots_dir, f"snapshot-{shell_type}-{timestamp}-{random_id}.sh")


def _cleanup_snapshot(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


async def create_and_save_snapshot(bin_shell: str) -> str | None:
    """Create the shell snapshot. Returns the snapshot path, or None on
    failure (timeout / shell error / missing output)."""
    try:
        config_file = _get_config_file(bin_shell)
        config_exists = os.path.exists(config_file)
        snapshot_path = _make_snapshot_path(bin_shell)
        os.makedirs(os.path.dirname(snapshot_path), exist_ok=True)
        script = _get_snapshot_script(bin_shell, snapshot_path, config_exists)

        env = subprocess_env()
        env.update({"SHELL": bin_shell, "GIT_EDITOR": "true", "CLAUDECODE": "1"})

        def _run() -> subprocess.CompletedProcess:
            return subprocess.run(
                [bin_shell, "-c", "-l", script],
                env=env,
                capture_output=True,
                timeout=SNAPSHOT_CREATION_TIMEOUT_S,
            )

        try:
            result = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired:
            return None

        if result.returncode != 0 or not os.path.exists(snapshot_path):
            return None

        atexit.register(_cleanup_snapshot, snapshot_path)
        return snapshot_path
    except Exception:  # noqa: BLE001 — snapshot is best-effort; fall back to -l
        return None


# Memoize snapshot creation per shell path (snapshot content is user-global,
# not engine-specific).
_snapshot_cache: dict[str, asyncio.Task] = {}


async def get_cached_snapshot(bin_shell: str) -> str | None:
    task = _snapshot_cache.get(bin_shell)
    if task is None:
        task = asyncio.ensure_future(create_and_save_snapshot(bin_shell))
        _snapshot_cache[bin_shell] = task
    return await task


def _reset_snapshot_cache_for_test() -> None:
    _snapshot_cache.clear()
