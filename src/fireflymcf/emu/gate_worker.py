"""Subprocess half of the emulator gate: `python -m fireflymcf.emu.gate_worker IMAGE SCENARIO
TIMEOUT_S OUT_DIR KEY`. Loads the scenario file, runs its `scenario(s)`, saves a final screenshot
and the frames log, and prints one JSON line (the result) on the real stdout. Anything the
scenario prints goes to stderr. A hang or a unicorn crash here cannot take the CLI down."""
from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
import traceback
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


def main(argv: list[str]) -> int:
    image_path, scenario_path, timeout_s, out_dir, key = argv
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = {"status": "passed", "reason": "", "screenshots": [], "frames_log": ""}
    s = None
    with contextlib.redirect_stdout(sys.stderr):
        try:
            from .scenario import Scenario

            spec = importlib.util.spec_from_file_location(f"emu_test_{key}", scenario_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
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
