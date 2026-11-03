"""The emulator gate: run an op's scenario file against a built image and report the result.

The scenario runs in a subprocess (`gate_worker`), so a hang or an emulator crash cannot take the
CLI down; the parent kills it a little after `timeout_s`. A failing result never stops an image
being written (the caller records it in the sidecar). Only 13.6 is emulated: anything else is
`not emulated`, never a pass.
"""
from __future__ import annotations

import json
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
    """The sidecar key a scenario's result is stored under (the file stem; Task 10 switches this
    to the op type)."""
    return Path(path).stem


def run_scenario_file(image: bytes, path: Path, timeout_s: float = 300, *,
                      out_dir: Path | None = None, key: str | None = None) -> EmuResult:
    from .scenario import supported

    ok, reason = supported(image)
    if not ok:
        return EmuResult("not emulated", reason)
    path = Path(path)
    key = key or result_key(path)
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="fireflymcf-emu-"))
    out.mkdir(parents=True, exist_ok=True)
    img = out / f"{key}-image.bin"
    img.write_bytes(bytes(image))
    cmd = [sys.executable, "-m", "fireflymcf.emu.gate_worker", str(img), str(path.resolve()),
           str(timeout_s), str(out), key]
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


def run_scenarios(build: Path, scenarios: list[Path], timeout_s: float = 300) -> dict[str, EmuResult]:
    """Run each scenario file against the image at `build`; screenshots and frame logs go to
    `<build>.emu/`. Keyed by `result_key`."""
    image = Path(build).read_bytes()
    out = Path(str(build) + ".emu")
    return {result_key(p): run_scenario_file(image, p, timeout_s, out_dir=out)
            for p in scenarios}


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
