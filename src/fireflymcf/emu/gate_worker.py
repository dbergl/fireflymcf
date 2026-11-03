"""Subprocess half of the emulator gate: `fireflymcf.emu.gate_worker IMAGE SCENARIO TIMEOUT_S
OUT_DIR KEY [SHA256]`, started by `gate.worker_command` (which keeps the caller's working
directory off `sys.path`). Loads the scenario file, runs its `scenario(s)`, saves a final
screenshot and the frames log, and prints one JSON line (the result) on the real stdout. Anything
the scenario prints goes to stderr. A hang or a unicorn crash here cannot take the CLI down.

The scenario runs from the bytes read here, compiled directly (never from `__pycache__`, as
`userop` loads `op.py`, R7), and, given SHA256, only if those bytes still hash to it."""
from __future__ import annotations

import contextlib
import hashlib
import json
import sys
import traceback
import types
from pathlib import Path


def _describe(exc: BaseException, scenario_file: str) -> str:
    """One line for a failure; a bare `assert` still names its line (the last frame in the
    scenario file, else the last frame)."""
    from .scenario import ScenarioError

    if isinstance(exc, ScenarioError):
        return str(exc)
    frames = traceback.extract_tb(exc.__traceback__)
    mine = [f for f in frames if f.filename == scenario_file] or frames[-1:]
    where = ""
    if mine:
        f = mine[-1]
        where = f" at {Path(f.filename).name}:{f.lineno}" + (f": {f.line}" if f.line else "")
    if isinstance(exc, AssertionError):
        msg = str(exc)
        return f"assertion failed{where}" + (f": {msg}" if msg else "")
    return f"{type(exc).__name__}: {exc}{where}"


def _load(scenario_path: str, key: str, sha256: str | None) -> types.ModuleType:
    """The scenario module, compiled from the file's bytes as read now; no bytecode cache."""
    source = Path(scenario_path).read_bytes()
    if sha256 and hashlib.sha256(source).hexdigest() != sha256:
        raise RuntimeError(f"{Path(scenario_path).name} changed since it was read (sha256 "
                           f"{sha256[:12]}...); run the command again")
    mod = types.ModuleType(f"emu_test_{key}")
    mod.__file__ = scenario_path
    sys.modules[mod.__name__] = mod
    exec(compile(source, scenario_path, "exec", dont_inherit=True), mod.__dict__)
    return mod


def main(argv: list[str]) -> int:
    image_path, scenario_path, timeout_s, out_dir, key, *rest = argv
    sha256 = rest[0] if rest else None
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = {"status": "passed", "reason": "", "screenshots": [], "frames_log": ""}
    s = None
    with contextlib.redirect_stdout(sys.stderr):
        try:
            from .scenario import Scenario

            mod = _load(scenario_path, key, sha256)
            s = Scenario(Path(image_path).read_bytes(), timeout_s=float(timeout_s))
            mod.scenario(s)
        except BaseException as e:  # noqa: BLE001 - every failure is a result
            result["status"] = "failed"
            result["reason"] = _describe(e, str(Path(scenario_path)))
        if s is not None:
            try:
                png = out / f"{key}-final.png"
                s.screenshot(png)
                result["screenshots"].append(str(png))
            except Exception:
                pass
            try:
                log = out / f"{key}-frames.txt"
                log.write_text("".join(
                    f"{f.t:9.3f} {f.can_id:08X} dgn={f.dgn:05X} sa={f.sa:02X} {f.data.hex()}\n"
                    for f in s.frames(sa=None)), encoding="utf-8")
                result["frames_log"] = str(log)
            except Exception:
                pass
    sys.stdout.write("\n" + json.dumps(result) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
