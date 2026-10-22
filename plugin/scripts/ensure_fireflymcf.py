"""Keep the fireflymcf package installed at the plugin's own version, in the plugin's data folder.

Run by the plugin's SessionStart hook (`hooks/hooks.json`). Claude Code has no plugin-install or
plugin-update event, so every session start checks a marker: when `installed-version` in
`$CLAUDE_PLUGIN_DATA` matches the `version` in this plugin's `plugin.json` and the executable is
there, it only prints where fireflymcf is. Otherwise it creates (or reuses) a venv under
`$CLAUDE_PLUGIN_DATA/venv` and installs that release's wheel from GitHub (no git needed), then
writes the marker. `$CLAUDE_PLUGIN_DATA` survives plugin updates; `$CLAUDE_PLUGIN_ROOT` does not.

Standard library only, and the same on Linux, macOS and Windows (`bin` vs `Scripts`, `.exe`).
What it prints on stdout goes into Claude's context, so it prints one line: where the
executables are, or why they are not and the command to install by hand. It never fails the
session: every error ends in that one line and exit status 0. pip's own output goes to
`install.log` beside the venv.

Environment (set by Claude Code for plugin hooks): CLAUDE_PLUGIN_ROOT, CLAUDE_PLUGIN_DATA.
FIREFLYMCF_INSTALL_SPEC overrides the pip requirement (tests; a local wheel).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import venv
from pathlib import Path

MIN_PYTHON = (3, 10)            # pyproject.toml's requires-python
REPO = "https://github.com/dbergl/fireflymcf"
LOCK_STALE_S = 15 * 60          # a lock older than this is a crashed install, not a live one


def wheel_spec(version: str) -> str:
    """The pip requirement for `version`: the release's wheel, with the `full` extra."""
    return (f"fireflymcf[full] @ {REPO}/releases/download/v{version}/"
            f"fireflymcf-{version}-py3-none-any.whl")


def manual_command(version: str) -> str:
    return f'pip install "{wheel_spec(version)}"'


def scripts_dir(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts" if os.name == "nt" else "bin")


def executable(venv_dir: Path, name: str) -> Path:
    return scripts_dir(venv_dir) / (name + (".exe" if os.name == "nt" else ""))


def venv_python(venv_dir: Path) -> Path:
    return executable(venv_dir, "python")


def plugin_version(root: Path) -> str:
    return json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))[
        "version"]


def _locked(lock: Path) -> bool:
    """Take the install lock (another session may be installing). True when taken."""
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if time.time() - lock.stat().st_mtime < LOCK_STALE_S:
                return False
            lock.unlink()
        except OSError:
            return False
        return _locked(lock)
    os.close(fd)
    return True


def ensure(root: Path, data: Path, spec: str | None = None,
           python: tuple = sys.version_info[:2]) -> tuple[bool, str]:
    """`(ready, line)`: make fireflymcf `plugin_version(root)` available under `data`, and the
    one line to print. Never raises for an install failure."""
    version = plugin_version(root)
    venv_dir = data / "venv"
    marker = data / "installed-version"
    exe = executable(venv_dir, "fireflymcf")
    ready = (f"fireflymcf {version} is installed for this plugin: run {exe} "
             f"(fireflymcf-tankpatch and fireflymcf-g12flash are beside it in "
             f"{scripts_dir(venv_dir)}).")
    try:
        if marker.read_text(encoding="utf-8").strip() == version and exe.exists():
            return True, ready
    except OSError:
        pass
    manual = (f"Install it by hand with Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+: "
              f"{manual_command(version)}")
    if tuple(python) < MIN_PYTHON:
        return False, (f"fireflymcf {version} is not installed: the plugin hook found Python "
                       f"{python[0]}.{python[1]}, and fireflymcf needs "
                       f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}+. {manual}")
    data.mkdir(parents=True, exist_ok=True)
    lock = data / "install.lock"
    if not _locked(lock):
        return False, (f"fireflymcf {version} is being installed by another session; it will "
                       f"be at {exe} when that finishes. {manual}")
    log = data / "install.log"
    try:
        if not venv_python(venv_dir).exists():
            venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
        with log.open("w", encoding="utf-8") as out:
            done = subprocess.run(
                [str(venv_python(venv_dir)), "-m", "pip", "install", "--upgrade",
                 "--disable-pip-version-check", "--no-input", spec or wheel_spec(version)],
                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        if done.returncode != 0 or not exe.exists():
            return False, (f"fireflymcf {version} did not install (pip exit "
                           f"{done.returncode}; see {log}). {manual}")
        marker.write_text(version + "\n", encoding="utf-8")
        return True, ready
    except Exception as e:                        # venv or pip could not start at all
        return False, f"fireflymcf {version} did not install ({e}). {manual}"
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def main() -> int:
    try:
        root = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parents[1])
        data = os.environ.get("CLAUDE_PLUGIN_DATA")
        if not data:
            print("fireflymcf plugin hook: CLAUDE_PLUGIN_DATA is not set, so nothing was "
                  "installed. " + manual_command(plugin_version(root)))
            return 0
        _ok, line = ensure(root, Path(data), os.environ.get("FIREFLYMCF_INSTALL_SPEC"))
        print(line)
    except Exception as e:                        # a broken plugin.json, an unwritable folder
        print(f"fireflymcf plugin hook: could not check the install ({e}). Install it by hand: "
              f'pip install "fireflymcf[full] @ git+{REPO}.git"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
