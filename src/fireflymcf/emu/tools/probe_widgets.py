#!/usr/bin/env python3
"""Find which GUI data points drive screen regions.

Boots (accepting the terms page if shown), performs the given taps, then for
each data-point index writes a test value straight into the GUI state block
(0x20007fc8 + 2*i, expiry 0x200087d0 + 4*i), lets the GUI redraw, and reports
which named regions changed.

usage: python -m fireflymcf.emu.tools.probe_widgets <mcf> --region name=x0,y0,x1,y1 [--region ...]
                        [--tap x,y ...] [--value N] [--only i,j,...]
"""
import argparse
import logging

import numpy as np

from fireflymcf.emu.machine import Machine

STATE, STATE_HI, EXPIRY = 0x20007FC8, 0x20008000, 0x200087D0


def tap(m, x, y):
    m.touch.touch([(x, y)])
    m.run(m.cpu_hz // 6)
    m.touch.touch([])
    m.run(m.cpu_hz)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mcf")
    ap.add_argument("--region", action="append", required=True)
    ap.add_argument("--tap", action="append", default=[])
    ap.add_argument("--value", type=lambda v: int(v, 0), default=260)
    ap.add_argument("--only", help="comma-separated data point indexes")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    regions = {}
    for r in args.region:
        name, box = r.split("=")
        regions[name] = tuple(map(int, box.split(",")))
    m = Machine()
    m.load_mcf(args.mcf)
    m.boot()
    hz = m.cpu_hz
    m.run(3 * hz)
    if m.eeprom.mem[0x7F] != 0:
        tap(m, 400, 425)  # I ACCEPT
    for t in args.tap:
        tap(m, *map(int, t.split(",")))
    count = int.from_bytes(m.uc.mem_read(0x20007FC4, 2), "little")
    indexes = [int(i) for i in args.only.split(",")] if args.only else range(count)

    def grab():
        img = m.periph["LTDC"].render()
        return {n: img[y0:y1, x0:x1].copy() for n, (x0, y0, x1, y1) in regions.items()}

    base = grab()
    for i in indexes:
        addr = STATE + 2 * i if i < 0x100 else STATE_HI + 2 * i
        old = bytes(m.uc.mem_read(addr, 2))
        m.uc.mem_write(addr, args.value.to_bytes(2, "little"))
        now_ms = m.cycles // (hz // 1000)
        m.uc.mem_write(EXPIRY + 4 * i, (now_ms + 600000).to_bytes(4, "little"))
        m.run(hz // 8)
        cur = grab()
        hits = [n for n in regions if not np.array_equal(cur[n], base[n])]
        if hits:
            print(f"data point {i}: {', '.join(hits)}", flush=True)
        m.uc.mem_write(addr, old)
        m.run(hz // 8)
        base = grab()


if __name__ == "__main__":
    main()
