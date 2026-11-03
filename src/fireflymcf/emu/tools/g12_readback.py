#!/usr/bin/env python3
"""Read every G12 config cell from a real coach and compare with the emulated G12.

The read-back is PC.49's primitive (fireflymcf docs/BUS-BASELINE.md): the
query ``FF 96 <sel> 0F FF FF D3 FF`` on 1FED9 from the free probe address 0xC0
makes the G12 answer with one 15FCE report and triggers nothing else (verified
again on 2026-10-21: no CAN id outside the live baseline but our own).  Only
queries (D3) are ever sent -- never writes.

  python -m fireflymcf.emu.tools.g12_readback <mcf> --host USER@HOST --send [--out capture.log]
      runs candump on the host, sends the 256 queries at 4 Hz, fetches the log
  python -m fireflymcf.emu.tools.g12_readback <mcf> --capture capture.log [--baseline live.log]
      analyses an existing read-back capture

The comparison seeds the emulated G12 only with what the real one broadcasts by
itself (its 15FCE sweep, sensors, inputs; from --baseline, default the read-back
capture) and lets the MCF's G12 code run, so matching cells that are never
broadcast test the code's defaults and logic.
"""
import argparse
import subprocess
import sys

from fireflymcf.emu import rvc
from fireflymcf.emu.g12 import G12
from fireflymcf.emu.machine import Machine
from fireflymcf.emu.replay import parse_candump
from fireflymcf.emu.tools.fidelity import g12_seed

QUERY_ID = 0x19FED9C0   # 1FED9 from SA 0xC0, priority 6
REPORT_ID = 0x195FCE9C  # 15FCE from the G12

REMOTE = """set -e
timeout 85 candump -L {ch} > /tmp/g12-readback.log &
sleep 1
for i in $(seq 0 255); do h=$(printf %02X $i); cansend {ch} 19FED9C0#FF96${{h}}0FFFFFD3FF; sleep 0.25; done
sleep 3
kill %1 2>/dev/null || true
wait 2>/dev/null || true"""


def capture(host, channel, out):
    subprocess.run(["ssh", "-o", "BatchMode=yes", host, REMOTE.format(ch=channel)], check=True)
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", f"{host}:/tmp/g12-readback.log", out],
                   check=True)


def read_cells(frames):
    cells, queried, answered = {}, set(), set()
    pending = {}
    for t, f in frames:
        if f.can_id == QUERY_ID and len(f.data) >= 7 and f.data[6] == 0xD3:
            queried.add(f.data[2])
            pending[f.data[2]] = t
        elif f.can_id == REPORT_ID:
            sel = f.data[0]
            cells[sel] = f.data[2] | f.data[3] << 8
            if sel in pending and t - pending[sel] < 0.1:
                answered.add(sel)
    return cells, queried, answered


def emulate(mcf, baseline):
    swept, inputs = g12_seed(baseline)
    m = Machine()
    g = G12(open(mcf, "rb").read(), cells=swept, inputs=inputs, cold_start=False)
    g.send = lambda *a, **k: None
    m.canbus.add_peer(g)
    for _, f in baseline:                       # what the G12 receives from others
        if f.extended and rvc.split_id(f.can_id)[2] not in (0x9C, 0x9F, 0xC0):
            g.on_frame(f)
    for _ in range(10):
        m.cycles += m.cpu_hz
        g.pump(m.cycles)
    return {s: g.cell(s) for s in range(256)}, swept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mcf")
    ap.add_argument("--capture", help="analyse this read-back capture")
    ap.add_argument("--host", help="SSH host with can-utils on the coach bus")
    ap.add_argument("--channel", default="can1")
    ap.add_argument("--send", action="store_true", help="really transmit the 256 queries")
    ap.add_argument("--out", default="build/live/g12-readback.log")
    ap.add_argument("--baseline", help="capture to seed the emulated G12 from")
    args = ap.parse_args()
    if args.host:
        if not args.send:
            sys.exit("refusing to transmit without --send (256 read-only 1FED9 queries from 0xC0)")
        capture(args.host, args.channel, args.out)
        args.capture = args.out
    if not args.capture:
        sys.exit("need --capture, or --host with --send")
    frames = parse_candump(args.capture)
    real, queried, answered = read_cells(frames)
    print(f"{len(answered)}/{len(queried)} queries answered within 100 ms; "
          f"{sum(v != 0xFFFF for v in real.values())} cells set, "
          f"{sum(v == 0xFFFF for v in real.values())} unset")
    emu, swept = emulate(args.mcf, parse_candump(args.baseline) if args.baseline else frames)
    diff = [s for s in range(256) if s in real and emu[s] != real[s]]
    never = [s for s in range(256) if s in real and s not in swept]
    print(f"emulated G12 identical in {len(real) - len(diff)}/{len(real)} cells "
          f"({len([s for s in never if s not in diff])}/{len(never)} of those never broadcast)")
    for s in diff:
        print(f"  {s:02X}: real {real[s]:5}  emulated {emu[s]:5}")


if __name__ == "__main__":
    main()
