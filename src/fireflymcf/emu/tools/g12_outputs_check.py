#!/usr/bin/env python3
"""Check the emulated G12 output driver against a coach capture.

Feeds every frame in the capture that the G12 did not send itself (its
DC_DIMMER_COMMAND_2s and everything its receive rules copy into cells, e.g. the
WS500's DM_RV that keeps the AES watchdog disarmed) to an emulated G12 (no panel
firmware), at the recorded times, together with
the real G12's input-pin edges (so its own interlock runs) and its swept config
cells; outputs that were on at the start are seeded from their first status.  Then compares, per
command, what the real and the emulated G12 sent in the next 50 ms
(1FEDA / 16F84 / 16C84 / ACK), and the periodic traffic per output.

usage: python -m fireflymcf.emu.tools.g12_outputs_check <mcf> <capture.log> [-v]
"""
import collections
import sys

from fireflymcf.emu import rvc
from fireflymcf.emu.g12 import G12
from fireflymcf.emu.machine import Machine
from fireflymcf.emu.periph.bxcan import Frame
from fireflymcf.emu.replay import parse_candump

G12_SA = 0x9C
LEVEL16 = 0x16F84
OUT_DGNS = {0x1FEDA, 0x16F84, 0x16C84, 0x0E8FF}
WINDOW = 0.050


def boot_phase(real):
    """When to boot the emulated G12 (0-5 s) and how far its output driver's 1 s tick
    lags the rest (0-2 s): the sweep (15FCE every 5 s) gives the G12's own phase, which
    the blob's 1 s timer bank shares; the periodic 16F84 (byte 6 = 05) and the 2 s
    statuses (0.5 s after a tick) give the output driver's."""
    sweeps = collections.Counter(round(t % 5, 2) for t, dgn, d in real
                                 if dgn == 0x15FCE and d[0] == 0x01)
    boot = sweeps.most_common(1)[0][0] - 0.005 if sweeps else None
    ticks = sorted(t % 1 for t, dgn, d in real if dgn == LEVEL16 and d[6] == 0x05)
    if not ticks:
        return (boot or 0.0), 0.0
    phase = ticks[len(ticks) // 2]
    near = lambda b: sum(abs((t - b - 0.5 + 0.02) % 2) < 0.04  # noqa: E731
                         for t, dgn, d in real if dgn == 0x1FEDA)
    phase = max((phase, phase + 1), key=near)
    if boot is None:
        return phase, 0.0
    return boot, (phase - boot) % 2


def main(argv):
    mcf, capture = argv[1], argv[2]
    verbose = "-v" in argv
    frames = [(t, f) for t, f in parse_candump(capture) if f.extended]
    real = [(t, rvc.split_id(f.can_id)[1], bytes(f.data)) for t, f in frames
            if rvc.split_id(f.can_id)[2] == G12_SA and rvc.split_id(f.can_id)[1] in OUT_DGNS]
    cmds = [(t, f) for t, f in frames if rvc.split_id(f.can_id)[1] == rvc.DC_DIMMER_COMMAND_2
            and rvc.split_id(f.can_id)[2] != G12_SA]
    m = Machine()
    hz = m.cpu_hz
    emu = []
    from fidelity import g12_seed
    cells, inputs = g12_seed(frames)
    pins = inputs.pop("pins")
    from fidelity import g12_clock
    g = G12(open(mcf, "rb").read(), cells=cells, inputs={**inputs, "pins": pins},
            clock=g12_clock(frames, m), cold_start=False)
    g.send = lambda dgn, data, prio=6: emu.append((m.cycles / hz, dgn, bytes(data)))
    sweeps = [(t, 0x15FCE, bytes(f.data)) for t, f in frames
              if rvc.split_id(f.can_id)[1:] == (0x15FCE, G12_SA)]
    boot, g.output_phase = boot_phase(real + sweeps)
    m.cycles = int(boot * hz)                  # periodic traffic in phase with the capture
    m.canbus.add_peer(g)
    from fidelity import seed_outputs
    seed_outputs(g, frames)
    emu.clear()

    def advance(until):
        while True:
            nxt = g.pump(m.cycles)
            if nxt is None or m.cycles + nxt > until:
                break
            m.cycles += nxt
        m.cycles = max(m.cycles, until)

    edges = [(t, f) for t, f in frames if rvc.split_id(f.can_id)[1:] == (0x1FBDA, G12_SA)
             and 0xA1 <= f.data[0] <= 0xAA]
    others = [(t, f) for t, f in frames if rvc.split_id(f.can_id)[2] != G12_SA
              and rvc.split_id(f.can_id)[1] != rvc.DC_DIMMER_COMMAND_2]
    for t, f in sorted(cmds + edges + others, key=lambda e: e[0]):
        advance(int(t * hz))
        if rvc.split_id(f.can_id)[1] == 0x1FBDA:
            g.set_input(f.data[0] - 0xA0, f.data[1] == 1)
        else:
            g.on_frame(f)
    end = frames[-1][0]
    advance(int(end * hz))

    def response(events, t0, inst):
        """The commanded output's first 1FEDA and 16F84 within the window."""
        status = level = None
        for t, dgn, d in events:
            if t0 - 0.0005 <= t < t0 + WINDOW:
                if dgn == 0x1FEDA and d[0] == inst and status is None:
                    status = d.hex()
                elif dgn == 0x16F84 and d[1] == inst and level is None:
                    level = d.hex()
        return status, level

    match = undercurrent = 0
    for t, f in cmds:
        inst = f.data[0]
        r, e = response(real, t, inst), response(emu, t, inst)
        same = r == e
        match += same
        if verbose or not same:
            print(f"{t:8.3f} cmd {f.data.hex()}  {'ok' if same else 'DIFF'}  real {r}  emu {e}")
    print(f"\n{match}/{len(cmds)} commands: commanded output's 1FEDA + 16F84 identical")
    periodic = lambda ev: collections.Counter(  # noqa: E731
        d.hex() for _, dgn, d in ev if dgn in (0x1FEDA, 0x16F84))
    rp, ep = periodic(real), periodic(emu)
    common = sum((rp & ep).values())
    print(f"status/level frames with identical payloads: {common} of {sum(rp.values())} real"
          f" ({sum(ep.values())} emulated)")

    commanded = {f.data[0] for t, f in cmds}

    def per_output(events):
        c = collections.Counter()
        for t, dgn, d in events:
            if dgn in OUT_DGNS and dgn != 0x0E8FF:
                c[(dgn, d[0] if dgn == 0x1FEDA else d[1])] += 1
        return c
    rc, ec = per_output(real), per_output(emu)
    print("\nframes per output over the capture (real / emulated); outputs never commanded on")
    print("the bus are driven by the G12's own rules (A/C program, AES) and need their inputs:")
    acks = sum(dgn == 0x0E8FF for _, dgn, _ in real), sum(dgn == 0x0E8FF for _, dgn, _ in emu)
    print(f"  ACK 0E8FF: {acks[0]} / {acks[1]}")
    for key in sorted(set(rc) | set(ec)):
        if key[1] not in commanded:
            continue
        flag = "" if abs(rc[key] - ec[key]) <= max(1, rc[key] // 10) else "   <--"
        print(f"  {key[0]:05X} inst {key[1]:3}: {rc[key]:4} / {ec[key]:4}{flag}")


if __name__ == "__main__":
    main(sys.argv)
