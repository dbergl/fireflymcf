"""The plugin's SessionStart hook keeps fireflymcf installed at the plugin's version.

`plugin/scripts/ensure_fireflymcf.py` runs on Linux, macOS and Windows (this module runs in the
public CI matrix). The install path is exercised for real -- a venv, pip, a console script --
against a tiny local wheel named `fireflymcf`, so nothing reaches the network.
"""
import base64
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
SCRIPT = PLUGIN / "scripts" / "ensure_fireflymcf.py"


def _load():
    spec = importlib.util.spec_from_file_location("ensure_fireflymcf", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hook = _load()


def _plugin(tmp_path, version):
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "fireflymcf", "version": version}), encoding="utf-8")
    return root


def _wheel(tmp_path, version):
    """A minimal pure wheel `fireflymcf==version` whose `fireflymcf` script prints it."""
    files = {
        "fakeff.py": f"def main():\n    print('fake fireflymcf {version}')\n",
        f"fireflymcf-{version}.dist-info/METADATA":
            f"Metadata-Version: 2.1\nName: fireflymcf\nVersion: {version}\n",
        f"fireflymcf-{version}.dist-info/WHEEL":
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"fireflymcf-{version}.dist-info/entry_points.txt":
            "[console_scripts]\nfireflymcf = fakeff:main\n",
    }

    def digest(body):
        h = hashlib.sha256(body.encode()).digest()
        return "sha256=" + base64.urlsafe_b64encode(h).rstrip(b"=").decode()

    record = f"fireflymcf-{version}.dist-info/RECORD"
    files[record] = "".join(f"{p},{digest(b)},{len(b.encode())}\n" for p, b in files.items()) \
        + f"{record},,\n"
    path = tmp_path / f"fireflymcf-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as z:
        for p, b in files.items():
            z.writestr(p, b)
    return path


def _spec(wheel):
    return f"fireflymcf @ {wheel.resolve().as_uri()}"


def test_the_wheel_spec_is_the_releases_asset():
    assert hook.wheel_spec("0.3.3") == (
        "fireflymcf[full] @ https://github.com/dbergl/fireflymcf/releases/download/v0.3.3/"
        "fireflymcf-0.3.3-py3-none-any.whl")


def test_the_executable_follows_the_platform(tmp_path):
    exe = hook.executable(tmp_path, "fireflymcf")
    if os.name == "nt":
        assert exe == tmp_path / "Scripts" / "fireflymcf.exe"
    else:
        assert exe == tmp_path / "bin" / "fireflymcf"


def test_the_plugin_manifest_and_hook_agree():
    """The hook reads the version the release bump writes; hooks.json runs the wrapper on every
    SessionStart (no matcher: after a compaction or /clear the line goes back into context),
    and the wrapper runs this script."""
    assert hook.plugin_version(PLUGIN) == json.loads(
        (PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    (entry,) = hooks["hooks"]["SessionStart"]
    assert "matcher" not in entry
    assert entry["hooks"] == [{"type": "command", "command":
                               'bash "${CLAUDE_PLUGIN_ROOT}/scripts/session-start.sh"'}]
    assert "ensure_fireflymcf.py" in WRAPPER.read_text(encoding="utf-8")


def test_install_then_noop_then_upgrade(tmp_path):
    data = tmp_path / "data"
    ok, line = hook.ensure(_plugin(tmp_path, "1.0.0"), data, _spec(_wheel(tmp_path, "1.0.0")))
    assert ok, line
    exe = hook.executable(data / "venv", "fireflymcf")
    assert str(exe) in line and "1.0.0" in line
    assert subprocess.run([str(exe)], capture_output=True, text=True).stdout.strip() == \
        "fake fireflymcf 1.0.0"
    assert (data / "installed-version").read_text(encoding="utf-8").strip() == "1.0.0"
    # same version: no pip at all (a spec that cannot install proves it)
    ok, line = hook.ensure(_plugin(tmp_path, "1.0.0"), data, "fireflymcf @ file:///nowhere.whl")
    assert ok and "1.0.0" in line
    # the plugin updated: the next session installs the new release into the same venv
    ok, line = hook.ensure(_plugin(tmp_path, "1.1.0"), data, _spec(_wheel(tmp_path, "1.1.0")))
    assert ok, line
    assert subprocess.run([str(exe)], capture_output=True, text=True).stdout.strip() == \
        "fake fireflymcf 1.1.0"
    assert (data / "installed-version").read_text(encoding="utf-8").strip() == "1.1.0"
    assert not (data / "install.lock").exists()


def test_a_failed_install_says_how_to_install_by_hand(tmp_path):
    data = tmp_path / "data"
    ok, line = hook.ensure(_plugin(tmp_path, "2.0.0"), data,
                           "fireflymcf @ " + (tmp_path / "missing.whl").resolve().as_uri())
    assert not ok
    assert "did not install" in line and hook.manual_command("2.0.0") in line
    assert "install.log" in line and not (data / "installed-version").exists()
    assert not (data / "install.lock").exists()


def test_an_old_python_is_refused_before_anything_is_written(tmp_path):
    data = tmp_path / "data"
    ok, line = hook.ensure(_plugin(tmp_path, "2.0.0"), data, python=(3, 9))
    assert not ok and "Python 3.9" in line and "3.10+" in line
    assert not data.exists()


def test_a_live_lock_is_another_session_installing(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "install.lock").write_text("", encoding="utf-8")
    ok, line = hook.ensure(_plugin(tmp_path, "2.0.0"), data, "unused")
    assert not ok and "another session" in line
    assert (data / "install.lock").exists()          # not ours to remove


def test_a_stale_lock_is_taken_over(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    lock = data / "install.lock"
    lock.write_text("", encoding="utf-8")
    old = lock.stat().st_mtime - hook.LOCK_STALE_S - 1
    os.utime(lock, (old, old))
    assert hook._locked(lock) and lock.exists()


def test_main_prints_one_line_and_never_fails(tmp_path):
    root = _plugin(tmp_path, "3.0.0")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_PLUGIN")}
    env.update(CLAUDE_PLUGIN_ROOT=str(root), CLAUDE_PLUGIN_DATA=str(tmp_path / "data"),
               FIREFLYMCF_INSTALL_SPEC="fireflymcf @ " + (tmp_path / "x.whl").resolve().as_uri())
    done = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True,
                          text=True)
    assert done.returncode == 0
    assert len(done.stdout.strip().splitlines()) == 1 and "did not install" in done.stdout
    del env["CLAUDE_PLUGIN_DATA"]
    done = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True,
                          text=True)
    assert done.returncode == 0 and "CLAUDE_PLUGIN_DATA is not set" in done.stdout


def test_a_broken_manifest_still_exits_zero(tmp_path):
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text("{", encoding="utf-8")
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(root),
               CLAUDE_PLUGIN_DATA=str(tmp_path / "data"))
    done = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True,
                          text=True)
    assert done.returncode == 0 and "fireflymcf plugin hook" in done.stdout


WRAPPER = PLUGIN / "scripts" / "session-start.sh"
# Claude Code runs the hook's command in bash (Git Bash on Windows). `shutil.which` searches PATH
# in order, so on Windows it finds Git Bash, not System32's WSL `bash.exe` that CreateProcess
# would pick first.
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="no bash on PATH")


def _hook_env(tmp_path, root, spec, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_PLUGIN")}
    env.update(CLAUDE_PLUGIN_ROOT=str(root), CLAUDE_PLUGIN_DATA=str(tmp_path / "data"),
               FIREFLYMCF_INSTALL_SPEC=spec, **extra)
    return env


@needs_bash
def test_the_hook_command_installs_through_the_wrapper(tmp_path):
    """What hooks.json runs: bash, the wrapper, whichever Python 3.10+ it finds first."""
    root = _plugin(tmp_path, "4.0.0")
    done = subprocess.run([BASH, str(WRAPPER)], capture_output=True, text=True,
                          env=_hook_env(tmp_path, root, _spec(_wheel(tmp_path, "4.0.0"))))
    assert done.returncode == 0, done.stderr
    assert "fireflymcf 4.0.0 is installed" in done.stdout, done.stdout + done.stderr
    exe = hook.executable(tmp_path / "data" / "venv", "fireflymcf")
    assert subprocess.run([str(exe)], capture_output=True, text=True).stdout.strip() == \
        "fake fireflymcf 4.0.0"


@needs_bash
@pytest.mark.skipif(os.name == "nt", reason="PATH isolation by symlink is POSIX")
def test_the_wrapper_without_a_python_says_so_and_exits_zero(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("bash", "dirname"):
        (bindir / tool).symlink_to(shutil.which(tool))
    env = _hook_env(tmp_path, _plugin(tmp_path, "4.0.0"), "unused", PATH=str(bindir))
    done = subprocess.run([str(bindir / "bash"), str(WRAPPER)], capture_output=True, text=True,
                          env=env)
    assert done.returncode == 0
    assert "no Python 3.10+ found" in done.stdout and "pip install" in done.stdout
