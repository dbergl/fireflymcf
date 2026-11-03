#!/usr/bin/env python3
"""Emulation speed benchmark: simulated vs wall time per phase.

usage: python -m fireflymcf.emu.tools.bench <mcf> [--profile OUT.prof] [--replay CANDUMP] [--seconds N]

Phases: boot (0-3 s, fresh EEPROM -> terms page), accept + settle on HOME with
the demo network (or a replay), then N seconds idle on HOME.
"""
import argparse
import cProfile
import logging
import time

from fireflymcf.emu.machine import Machine
from fireflymcf.emu.peers import demo_network


def phase(m, name, seconds):
    c0, w0, i0 = m.cycles, time.perf_counter(), m.idle_cycles
    m.run(int(seconds * m.cpu_hz))
    sim = (m.cycles - c0) / m.cpu_hz
    wall = time.perf_counter() - w0
    idle = (m.idle_cycles - i0) / max(1, m.cycles - c0)
    print(f"  {name:8} {sim:6.2f}s sim  {wall:6.2f}s wall  {sim / wall:5.2f}x  {idle:4.0%} idle")
    return sim, wall


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mcf")
    ap.add_argument("--profile", help="write cProfile stats here")
    ap.add_argument("--replay", help="feed this candump log instead of the demo network")
    ap.add_argument("--seconds", type=float, default=10.0, help="HOME idle phase length")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    m = Machine()
    m.load_mcf(args.mcf)
    m.boot()
    if args.replay:
        from fireflymcf.emu.replay import CandumpReplay

        m.canbus.add_peer(CandumpReplay(args.replay, skip_sources=(0x9F,), loop=True))
    else:
        demo_network(m.canbus, m.guidata)
    prof = cProfile.Profile() if args.profile else None
    if prof:
        prof.enable()
    total = [phase(m, "boot", 3.0)]
    m.touch.touch([(400, 425)])                 # I ACCEPT
    m.run(m.cpu_hz // 6)
    m.touch.touch([])
    total.append(phase(m, "settle", 3.0))
    total.append(phase(m, "home", args.seconds))
    if prof:
        prof.disable()
        prof.dump_stats(args.profile)
    sim, wall = (sum(x) for x in zip(*total))
    print(f"  {'total':8} {sim:6.2f}s sim  {wall:6.2f}s wall  {sim / wall:5.2f}x")


if __name__ == "__main__":
    main()
