"""The emulator gate: run an op's scenario file against a built image and report the result.

The scenario runs in a subprocess (`gate_worker`), so a hang or an emulator crash cannot take the
CLI down; the parent kills it a little after `timeout_s`. A failing result never stops an image
being written (the caller records it in the sidecar). Only 13.6 and 14.7 are emulated
(`scenario.supported`): anything else is `not emulated`, never a pass.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

KILL_GRACE_S = 30.0       # the parent's hard kill is this long after the scenario's own timeout


@dataclass
class EmuResult:
    status: str                   # "passed" | "failed" | "not emulated"
    reason: str = ""
    screenshots: list[str] = field(default_factory=list)
    frames_log: str = ""          # path of the text log of every frame seen on the bus

    def to_dict(self) -> dict:
        return asdict(self)


def result_key(path: Path) -> str:
    """The sidecar key an explicitly named scenario file's result is stored under: its stem. A
    user op's `emu_test.py` found through the project is keyed by the op's type instead
    (`run_scenarios` with a mapping), so two ops' `emu_test.py` never collide."""
    return Path(path).stem


def _file_key(key: str) -> str:
    """`key` made safe for a file name and a module name (an op type carries `:`)."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key)


# `python -c` puts '' (the working directory) first on sys.path, as `-m` does; drop it before
# anything is imported, so a stray module where the caller stands cannot shadow one the worker
# or the scenario imports. (`-P` does the same, from Python 3.11 only.)
_WORKER_BOOT = ("import sys; sys.path.pop(0); import runpy; "
                "runpy.run_module('fireflymcf.emu.gate_worker', run_name='__main__')")


def worker_command(*args: str) -> list[str]:
    """The argv that starts `gate_worker` with `args`, the caller's directory off `sys.path`."""
    return [sys.executable, "-c", _WORKER_BOOT, *args]


def run_scenario_file(image: bytes, path: Path, timeout_s: float = 300, *,
                      out_dir: Path | None = None, key: str | None = None,
                      sha256: str | None = None) -> EmuResult:
    """Run one scenario file on `image` in a worker subprocess. With `sha256`, the worker runs
    the file only if its bytes still hash to it (a user op's `emu_test.py`, read at discovery)."""
    from .scenario import supported

    ok, reason = supported(image)
    if not ok:
        return EmuResult("not emulated", reason)
    path = Path(path)
    key = _file_key(key or result_key(path))
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="fireflymcf-emu-"))
    out.mkdir(parents=True, exist_ok=True)
    img = out / f"{key}-image.bin"
    img.write_bytes(bytes(image))
    cmd = worker_command(str(img), str(path.resolve()), str(timeout_s), str(out), key,
                         *([sha256] if sha256 else []))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + KILL_GRACE_S)
    except subprocess.TimeoutExpired:
        return EmuResult("failed", f"timeout: worker did not finish within {timeout_s:g} s "
                                   f"(+{KILL_GRACE_S:g} s grace) and was killed")
    finally:
        img.unlink(missing_ok=True)
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
                return EmuResult(d["status"], d.get("reason", ""), d.get("screenshots", []),
                                 d.get("frames_log", ""))
            except (ValueError, KeyError):
                break
    tail = " | ".join((proc.stderr or "").strip().splitlines()[-3:])
    return EmuResult("failed", f"worker exited {proc.returncode} without a result: {tail}")


def run_scenarios(build: Path, scenarios, timeout_s: float = 300,
                  digests: dict | None = None) -> dict[str, EmuResult]:
    """Run each scenario file against the image at `build`; screenshots and frame logs go to
    `<build>.emu/`. `scenarios` is a list of files (keyed by `result_key`) or a {key: file}
    mapping (the CLI keys a user op's `emu_test.py` by its type). `digests` maps a key to the
    sha256 its file must still have (`run_scenario_file`)."""
    if not isinstance(scenarios, dict):
        scenarios = {result_key(p): p for p in scenarios}
    digests = digests or {}
    image = Path(build).read_bytes()
    out = Path(str(build) + ".emu")
    return {k: run_scenario_file(image, p, timeout_s, out_dir=out, key=k, sha256=digests.get(k))
            for k, p in scenarios.items()}


def record(build: Path, results: dict[str, EmuResult]) -> Path:
    """Add or replace `"emulation": {key: result}` in the build's sidecar (`<build>.json`),
    creating a minimal one when `compile` did not write it."""
    import hashlib

    side = Path(str(build) + ".json")
    try:
        data = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"sha256": hashlib.sha256(Path(build).read_bytes()).hexdigest()}
    data.setdefault("emulation", {}).update({k: r.to_dict() for k, r in results.items()})
    side.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return side


def summary_lines(results: dict[str, EmuResult]) -> list[str]:
    """What to print: `emulated, not flashed` only when every result passed, else each reason."""
    if results and all(r.status == "passed" for r in results.values()):
        return ["emulated, not flashed"]
    lines = []
    for k, r in results.items():
        if r.status != "passed":
            lines.append(f"{k}: {r.status}: {r.reason}")
    return lines
