#!/usr/bin/env python3
"""Fidelity check: emulated panel transmissions vs the real panel in a capture.

Replays a coach capture (candump -l) into the emulator, records what the
emulated firmware transmits, and compares it per CAN ID with what the real
panel (source 0x9F) sent in the same capture: rate, period and payloads.

With --g12 the recorded G12 (SA 0x9C) is dropped from the replay and the
emulated G12 runs the MCF's G12 logic instead (and drives outputs 1-44), seeded
with the settings it swept on 15FCE and the outputs it reported on, and fed, on the replay's timeline, the sensor readings and input-pin
edges the real one broadcast (tanks, voltages, temperature, LPG, ignition and
switches); its frames are then compared with the real G12's as well.

usage: python -m fireflymcf.emu.tools.fidelity <mcf> <capture.log> [--g12] [--seconds N] [--settle S] [--markdown OUT]
"""
import argparse
import collections
import logging
import os
import statistics

from fireflymcf.emu import rvc
from fireflymcf.emu.canbus import Peer
from fireflymcf.emu.machine import Machine
from fireflymcf.emu.replay import CandumpReplay, parse_candump

PANEL_SA = 0x9F
G12_SA = 0x9C
STATE_CELLS = {0x01, 0x05}   # change during a capture (AES enable, cell 05 state); 03 rides on 01


def g12_seed(frames):
    """Cells and inputs for the emulated G12 from what the real one broadcast."""
    cells, tanks, volts, ambient, lpg, pins, thermostat = {}, {}, {}, None, None, {}, None
    for t, f in frames:
        _, dgn, sa = rvc.split_id(f.can_id)
        d = f.data
        if sa != G12_SA:
            continue
        if dgn == 0x15FCE:
            if d[0] in STATE_CELLS and d[0] in cells:
                continue                             # application state: as it started
            cells[d[0]] = d[2] | d[3] << 8
            if d[0] == 0x01 and len(d) >= 8:         # record #10 also carries cell 03
                cells[0x03] = d[6] | d[7] << 8
        elif dgn == 0x0BFC1:
            tanks[d[0]] = d[1] | d[2] << 8
        elif dgn == 0x1AAFD:
            volts[d[0]] = (d[2] | d[3] << 8) * 0.05
        elif dgn == rvc.THERMOSTAT_AMBIENT_STATUS:
            ambient = (d[1] | d[2] << 8) * 0.03125 - 273
        elif dgn == 0x1AAB7:
            lpg = d[1]
        elif dgn == 0x1FFE2 and thermostat is None:
            thermostat = bytes(d)
        elif dgn == 0x1FBDA and 0xA1 <= d[0] <= 0xAA and d[0] - 0xA0 not in pins:
            # An active pin refreshes every second (PC.24) and OFF is sent once, so a
            # pin is on at the start only if its first frame is an ON within ~1 s.
            pins[d[0] - 0xA0] = d[1] == 1 and t < 1.2
    if pins.get(10):                            # already latched: interlock cells 28/29
        cells[0x28] = cells[0x29] = 1
    if 0x81 not in cells and 0x92 in cells:
        # The charge rate (0x81) soft-starts after power-up, +20 every 10 s up to
        # 0x92 (13.6 +0x0E48, rule #8 = 1FF95); a running coach has finished it
        # (live read-back 2026-10-21: 81 = 92 = 200). 0x81 is not swept.
        cells[0x81] = cells[0x92]
    return cells, {"tanks": tanks, "house": volts.get(1), "chassis": volts.get(2),
                   "ambient": ambient, "lpg": lpg, "pins": pins, "thermostat": thermostat}


def g12_clock(frames, machine):
    """A clock for the emulated G12 that runs from the real G12's first 1FFFF in the
    capture (quiet hours, cell 04, depend on it)."""
    import time
    for t, f in frames:
        _, dgn, sa = rvc.split_id(f.can_id)
        if sa == G12_SA and dgn == rvc.DATE_TIME_STATUS:
            d = f.data
            base = time.mktime((2000 + d[0], d[1], d[2], d[4], d[5], d[6], 0, 0, -1)) - t
            return lambda: time.localtime(base + machine.cycles / machine.cpu_hz)
    return time.localtime


class G12SensorReplay(Peer):
    """Feeds the emulated G12 the real one's sensor frames, looping with ``replay``."""

    DGNS = {0x0BFC1, 0x1AAFD, rvc.THERMOSTAT_AMBIENT_STATUS, 0x1AAB7, 0x1FBDA}

    def __init__(self, frames, g12, replay):
        self.events = [(t, rvc.split_id(f.can_id)[1], f.data) for t, f in frames
                       if f.extended and rvc.split_id(f.can_id)[2] == G12_SA
                       and rvc.split_id(f.can_id)[1] in self.DGNS]
        self.g12, self.replay = g12, replay

    def attach(self, bus):
        super().attach(bus)
        self.pos = 0
        self.pass_start = self.replay.start

    def pump(self, now):
        hz = self.bus.m.cpu_hz
        while self.events:
            if self.pos >= len(self.events):
                if self.replay.start == self.pass_start:  # wait for the replay to wrap
                    return hz // 100
                self.pos, self.pass_start = 0, self.replay.start
            t, dgn, d = self.events[self.pos]
            due = self.replay.start + int(t * hz)
            if due > now:
                return due - now
            self.apply(dgn, d)
            self.pos += 1
        return None

    def apply(self, dgn, d):
        inputs = self.g12.inputs
        if dgn == 0x0BFC1:
            inputs["tanks"][d[0]] = d[1] | d[2] << 8
        elif dgn == 0x1AAFD:
            inputs["house" if d[0] == 1 else "chassis"] = (d[2] | d[3] << 8) * 0.05
        elif dgn == rvc.THERMOSTAT_AMBIENT_STATUS:
            inputs["ambient"] = (d[1] | d[2] << 8) * 0.03125 - 273
        elif dgn == 0x1AAB7:
            inputs["lpg"] = d[1]
        elif dgn == 0x1FBDA and 0xA1 <= d[0] <= 0xAA:   # pin 10 also sends FB itself
            self.g12.set_input(d[0] - 0xA0, d[1] == 1)


def seed_outputs(g12, frames):
    """Output states at the capture start (first statuses), and every output's load
    current and lifetime counters as the real G12 reported them."""
    seen, counters = set(), {}
    # Full-output current: the most common reading at level 200, 1 s past a
    # switch-on (soft start); readings flicker by a count (heaters 2A-2C), so the
    # maximum overstates it.  Outputs never at full fall back to their maximum.
    full, peak, level, since = {}, {}, {}, {}
    for t, f in frames:
        _, dgn, sa = rvc.split_id(f.can_id)
        d = f.data
        if sa != G12_SA:
            continue
        if dgn == rvc.DC_DIMMER_STATUS_3:
            on = d[2] if d[6] & 0x0C else 0
            if on and not level.get(d[0]):
                since[d[0]] = t
            level[d[0]] = on
        elif dgn == 0x16F84 and d[6] == 0x05 and d[4] != 0xFF:
            peak[d[1]] = max(peak.get(d[1], 0), d[4])
            if level.get(d[1]) == 200 and t - since.get(d[1], -9) > 1.0:
                full.setdefault(d[1], collections.Counter())[d[4]] += 1
        elif dgn == 0x16C84 and d[1] not in counters:
            counters[d[1]] = (d[2] | d[3] << 8, d[4] | d[5] << 8)
    for t, f in frames:
        if t > 2.5:
            break
        _, dgn, sa = rvc.split_id(f.can_id)
        d = f.data
        if sa == G12_SA and dgn == rvc.DC_DIMMER_STATUS_3 and d[0] not in seen \
                and g12.outputs.owns(d[0]):
            seen.add(d[0])
            g12.outputs.seed(d[0], d[2] if d[6] & 0x0C else 0, locked=bool(d[3] & 1),
                             last_cmd=d[5], uc_latched=bool(d[6] & 0x40))
    loads = {i: (full[i].most_common(1)[0][0] if i in full else v) for i, v in peak.items()}
    for inst, load in loads.items():
        if g12.outputs.owns(inst):
            g12.outputs.out[inst].load = load
    for inst, (count, minutes) in counters.items():
        if g12.outputs.owns(inst):
            o = g12.outputs.out[inst]
            if o.on:    # first frame is a minute tick
                o.count, o.minutes = count, minutes - 1
            else:       # first frame is a switch-on, which counted itself
                o.count, o.minutes = count - 1, minutes


def summarize(events, duration):
    """events: list of (t, can_id, data) -> {can_id: stats}"""
    by_id = collections.defaultdict(list)
    for t, ident, data in events:
        by_id[ident].append((t, data))
    out = {}
    for ident, items in by_id.items():
        times = [t for t, _ in items]
        gaps = [b - a for a, b in zip(times, times[1:])]
        out[ident] = {
            "count": len(items),
            "per_min": 60.0 * len(items) / duration if duration else 0,
            "period": statistics.median(gaps) if gaps else None,
            "payloads": collections.Counter(d.hex().upper() for _, d in items),
        }
    return out


def fmt_period(p):
    return "-" if p is None else f"{p:.2f}s"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mcf")
    ap.add_argument("capture")
    ap.add_argument("--seconds", type=float, default=None,
                    help="measurement window (default: 2x capture length)")
    ap.add_argument("--settle", type=float, default=8.0, help="seconds after boot+accept to skip")
    ap.add_argument("--markdown", help="write the report here as well")
    ap.add_argument("--g12", action="store_true",
                    help="emulate the G12 from the MCF instead of replaying it")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    frames = parse_candump(args.capture)
    sources = (PANEL_SA, G12_SA) if args.g12 else (PANEL_SA,)
    recorded = [(t, f) for t, f in frames
                if f.extended and rvc.split_id(f.can_id)[2] in sources]
    cap_len = frames[-1][0]
    window = args.seconds or 2 * cap_len

    m = Machine()
    m.load_mcf(args.mcf)
    m.boot()
    replay = m.canbus.add_peer(CandumpReplay(args.capture, skip_sources=sources, loop=True))
    hz = m.cpu_hz
    tx = []
    orig_sink = m.periph["CAN1"].sink
    m.periph["CAN1"].sink = lambda f: (tx.append((m.cycles / hz, f.can_id, f.data)), orig_sink(f))
    if args.g12:
        from fireflymcf.emu.g12 import G12

        cells, inputs = g12_seed(frames)
        g12 = G12(m.mcf_data, cells=cells, inputs=inputs, clock=g12_clock(frames, m),
                  cold_start=False)
        orig_send = g12.send
        g12.send = lambda dgn, data, prio=6: (
            tx.append((m.cycles / hz, rvc.can_id(dgn, G12_SA, prio), bytes(data))),
            orig_send(dgn, data, prio))
        m.canbus.add_peer(g12)
        # The sweep set is history (rules the G12 has activated since it powered up,
        # e.g. #29 for 0x11 at its first clamp); take the capture's as the starting state.
        for n, rule in g12.rules.items():
            if rule.dgn == 0x15FCE and rule.payload[0] in cells and n not in g12.active:
                g12.active[n] = len(g12.active) + 1000
        m.canbus.add_peer(G12SensorReplay(frames, g12, replay))
        seed_outputs(g12, frames)
    m.run(3 * hz)
    if m.eeprom.mem[0x7F] != 0:                 # terms page: accept
        m.touch.touch([(400, 425)])
        m.run(hz // 6)
        m.touch.touch([])
    m.run(int(args.settle * hz))
    t0 = m.cycles / hz
    tx_all = list(tx)                           # everything since boot, for event timing
    tx.clear()
    m.run(int(window * hz))
    tx_all += tx
    emu_ev = [(t, i, d) for t, i, d in tx_all if t >= 0]
    recorded_ev = [(t, f.can_id, f.data) for t, f in recorded]
    emu = summarize([(t - t0, i, d) for t, i, d in tx], window)
    real = summarize(recorded_ev, cap_len)

    lines = [f"# Fidelity: {os.path.basename(args.capture)}", "",
             f"Real panel: {cap_len:.0f} s of capture. Emulated panel: {window:.0f} s "
             f"(replay looped, after {args.settle:.0f} s settle).", "",
             "| CAN ID | DGN | real /min | emu /min | real period | emu period | payloads match |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    verdicts = {}
    for ident in sorted(set(real) | set(emu)):
        r, e = real.get(ident), emu.get(ident)
        name = rvc.dgn_name(rvc.split_id(ident)[1])
        if r and e:
            same = set(r["payloads"]) == set(e["payloads"])
            sub = set(r["payloads"]) <= set(e["payloads"]) or set(e["payloads"]) <= set(r["payloads"])
            match = "yes" if same else ("partly" if sub or set(r["payloads"]) & set(e["payloads"])
                                        else "no")
        else:
            match = "real only" if r else "emu only"
        verdicts[ident] = match
        lines.append(f"| {ident:08X} | {name} | {r['per_min'] if r else 0:.1f} | "
                     f"{e['per_min'] if e else 0:.1f} | {fmt_period(r and r['period'])} | "
                     f"{fmt_period(e and e['period'])} | {match} |")
    rare = [i for i in sorted(set(real) | set(emu))
            if 0 < max(real.get(i, {}).get("count", 0), emu.get(i, {}).get("count", 0)) <= 6]
    if rare:
        lines += ["", "## Event timing (s; emulated times modulo the capture length)", ""]
        for ident in rare:
            rt = [f"{t:.3f}" for t, i, _ in recorded_ev if i == ident]
            et = [f"{t % (cap_len + 0.1):.3f}" for t, i, _ in emu_ev if i == ident]
            lines.append(f"* `{ident:08X}` real: {', '.join(rt) or '-'}; emu: {', '.join(et) or '-'}")
    lines += ["", "## Payload differences", ""]
    for ident, match in verdicts.items():
        if match == "yes":
            continue
        r = real.get(ident, {"payloads": {}})["payloads"]
        e = emu.get(ident, {"payloads": {}})["payloads"]
        lines.append(f"* `{ident:08X}` real: {', '.join(sorted(r)) or '-'}")
        lines.append(f"  emu: {', '.join(sorted(e)) or '-'}")
    report = "\n".join(lines)
    print(report)
    if args.markdown:
        open(args.markdown, "w", encoding="utf-8").write(report + "\n")


if __name__ == "__main__":
    main()
