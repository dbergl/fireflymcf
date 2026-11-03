#!/usr/bin/env python3
"""Script the panel: boot, then a sequence of taps/holds, saving screenshots.

usage: python -m fireflymcf.emu.tools.drive <mcf> [--demo | --replay CANDUMP] [--eeprom FILE] STEP...
  STEP = name:x,y[,hold_seconds]     tap (or hold) at x,y then wait 1 s
         wait:seconds                let time pass
Screenshots go to build/shots/drive_<n>_<name>.png; CAN tx is logged.
"""
import logging
import os
import sys

from PIL import Image

from fireflymcf.emu.machine import Machine
from fireflymcf.emu.peers import demo_network

QUIET = ("19FECA9F", "19FFFD9F", "18EA9FFE", "18EEFF9F", "18EAFF9F")


class Quiet(logging.Filter):
    def filter(self, rec):
        return not any(q in rec.getMessage() for q in QUIET)


def main(argv):
    args = argv[1:]
    mcf = args.pop(0)
    demo = "--demo" in args
    if demo:
        args.remove("--demo")
    replay = None
    if "--replay" in args:
        i = args.index("--replay")
        replay = args[i + 1]
        del args[i:i + 2]
    eeprom = None
    if "--eeprom" in args:
        i = args.index("--eeprom")
        eeprom = args[i + 1]
        del args[i:i + 2]
    logging.basicConfig(level=logging.INFO, format="%(name)-6s %(message)s")
    logging.getLogger("periph").setLevel(logging.WARNING)
    logging.getLogger("machine").setLevel(logging.WARNING)
    logging.getLogger("can").addFilter(Quiet())
    log = logging.getLogger("drive")
    m = Machine()
    if eeprom:
        m.eeprom.attach_file(eeprom)
    m.load_mcf(mcf)
    m.boot()
    if demo:
        demo_network(m.canbus, m.guidata, g12_path=os.environ.get("G12_STATE"))
    if replay:
        from fireflymcf.emu.replay import CandumpReplay

        m.canbus.add_peer(CandumpReplay(replay, loop=True))
    hz = m.cpu_hz
    m.run(3 * hz)
    for n, step in enumerate(args):
        name, _, spec = step.partition(":")
        if name == "wait":
            m.run(int(float(spec) * hz))
            continue
        x, y, *hold = (float(v) for v in spec.split(","))
        log.info("--- %s (%d,%d)%s", name, x, y, f" hold {hold[0]}s" if hold else "")
        m.touch.touch([(x, y)])
        m.run(int((hold[0] if hold else 1 / 6) * hz))
        m.touch.touch([])
        m.run(hz)
        path = f"build/shots/drive_{n:02}_{name}.png"
        Image.fromarray(m.periph["LTDC"].render()).save(path)
        log.info("    -> %s", path)


if __name__ == "__main__":
    main(sys.argv)
