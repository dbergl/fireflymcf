"""python -m fireflymcf.emu <package.mcf> [options]

Runs the LCD firmware from an MCF package.  With --gui the LTDC output is shown
in a window and the mouse drives the GT911 touch controller.
"""
import argparse
import collections
import contextlib
import json
import logging
import os
import sys
import time

# unicorn and the machine are imported inside the functions that run it, so `add_arguments`
# works (for `fireflymcf emulate --help`) without the `emu` extra installed.
CPU_HZ = 160_000_000   # replaced by periph.stm32f7.CPU_HZ when the emulator runs


def report(m, why, dt):
    from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_SP

    uc = m.uc
    print(f"stopped: {why or 'time limit'}")
    print(f"  pc=0x{uc.reg_read(UC_ARM_REG_PC):08x} lr=0x{uc.reg_read(UC_ARM_REG_LR):08x} "
          f"sp=0x{uc.reg_read(UC_ARM_REG_SP):08x} active exceptions {m.core.active}")
    sim = m.cycles / CPU_HZ
    print(f"  {sim:.2f}s simulated in {dt:.2f}s wall ({sim / dt:.2f}x real time), "
          f"{m.idle_cycles / max(1, m.cycles):.0%} idle-skipped")
    counts = getattr(m.canbus, "tx_counts", None)
    if counts is not None:
        print(f"  remote bus ({m.canbus.tx_mode}): {counts['sent']} frames sent, "
              f"{counts['held']} held; real-panel frames seen: {m.canbus.foreign_panel}")


def run_gui(m, scale):
    import pygame

    ltdc = m.periph["LTDC"]
    pygame.init()
    screen = pygame.display.set_mode((int(800 * scale), int(480 * scale)))
    pygame.display.set_caption("Firefly LCD emulator")
    clock = pygame.time.Clock()
    fps = 30
    frame_cycles = CPU_HZ // fps
    pressed = False
    why = None
    while why is None:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return "window closed"
            if ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEMOTION) and (
                    ev.type == pygame.MOUSEBUTTONDOWN or pressed):
                pressed = True
                x, y = ev.pos
                m.touch.touch([(x / scale, y / scale)])
            elif ev.type == pygame.MOUSEBUTTONUP:
                pressed = False
                m.touch.touch([])
        why = m.run(frame_cycles)
        img = ltdc.render()
        if img is not None:
            surf = pygame.surfarray.make_surface(img.swapaxes(0, 1))
            if scale != 1:
                surf = pygame.transform.smoothscale(surf, screen.get_size())
            screen.blit(surf, (0, 0))
        pygame.display.set_caption(f"Firefly LCD emulator - t={m.cycles / CPU_HZ:.1f}s")
        pygame.display.flip()
        clock.tick(fps)  # pace simulated time to wall time when running faster
    return why


def add_arguments(ap):
    ap.add_argument("mcf", help="MCF firmware package (e.g. firefly-13.6/mcf.bin)")
    ap.add_argument("--seconds", type=float, default=None, help="stop after N simulated seconds")
    ap.add_argument("--gui", action="store_true", help="show the display; mouse = touch")
    ap.add_argument("--scale", type=float, default=1.0, help="window scale for --gui")
    ap.add_argument("--screenshot", metavar="PNG", help="save the display when stopping")
    ap.add_argument("--eeprom", default="build/eeprom.bin",
                    help="file backing the board EEPROM (settings); 'none' = volatile")
    ap.add_argument("--network", choices=["none", "demo"], default="demo",
                    help="simulated RV-C devices on the bus (default: demo)")
    ap.add_argument("--ambient", type=float, default=None, metavar="DEG_C",
                    help="the demo G12's thermostat sensor (default 21); outside "
                         "-51..82 the panel lists 'The Front thermistor has an invalid reading.'")
    ap.add_argument("--remote-battery", type=int, default=2800, metavar="MV",
                    help="the wireless wall switch's battery (default 2800 mV); 0 = no remote. "
                         "Silent, 0 V or >= 3.6 V lists '... has a dead battery or is "
                         "disconnected.' from 10 minutes after boot")
    ap.add_argument("--replay", metavar="CANDUMP_LOG",
                    help="replay a recorded coach bus (candump -l); implies --network none")
    ap.add_argument("--replay-loop", action="store_true", help="loop the replay")
    ap.add_argument("--can", metavar="IFACE:CHANNEL",
                    help="bridge to a python-can bus, e.g. socketcan:vcan0, or listen (only) "
                         "to a remote bus over SSH, e.g. ssh:USER@HOST:CHANNEL")
    ap.add_argument("--can-tx", choices=["dry", "live"],
                    help="with --can ssh:...: join the remote bus as the panel (its real panel "
                         "must be disconnected). dry = log what would be sent, send nothing; "
                         "live = transmit (settings writes held unless --allow-config)")
    ap.add_argument("--allow-config", action="store_true",
                    help="with --can-tx live: also send settings writes (1FED9 D1/D2 to the "
                         "G12, 1EE00 option broadcasts)")
    ap.add_argument("--tx-log", default="build/live/panel-tx.log",
                    help="with --can-tx: every frame the panel sends, marked sent / held")
    ap.add_argument("--log-can", action="store_true", help="log frames delivered to the LCD")
    ap.add_argument("--stats", action="store_true", help="print the hottest MMIO accesses")
    ap.add_argument("-q", "--quiet", action="store_true", help="hide unmodelled-register notes")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--json", action="store_true",
                    help="print a JSON summary (headless only); the log goes to stderr")


def run(args, ap=None):
    """Run the emulator for parsed `args`; returns the process exit code."""
    if args.json and args.gui:
        print("error: --json does not combine with --gui", file=sys.stderr)
        return 2
    if args.json:
        with contextlib.redirect_stdout(sys.stderr):
            summary = _run(args, ap)
        if isinstance(summary, int):
            return summary
        print(json.dumps(summary))
        return 0
    result = _run(args, ap)
    return result if isinstance(result, int) else 0


def _run(args, ap=None):
    global CPU_HZ
    from .machine import Machine
    from .periph.stm32f7 import CPU_HZ as _HZ

    CPU_HZ = _HZ
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s.%(msecs)03d %(name)-7s %(message)s", datefmt="%H:%M:%S")
    if args.quiet:
        logging.getLogger("periph").setLevel(logging.WARNING)

    m = Machine()
    if args.eeprom != "none":
        os.makedirs(os.path.dirname(args.eeprom) or ".", exist_ok=True)
        m.eeprom.attach_file(args.eeprom)
    m.load_mcf(args.mcf)
    m.boot()
    m.rfremote.battery_mv = args.remote_battery or None
    if args.can_tx:
        if not (args.can or "").startswith("ssh:"):
            if ap is not None:
                ap.error("--can-tx needs --can ssh:HOST:CHANNEL")
            print("error: --can-tx needs --can ssh:HOST:CHANNEL", file=sys.stderr)
            return 2
        args.network = "none"              # only the real coach answers
        os.makedirs(os.path.dirname(args.tx_log) or ".", exist_ok=True)
    if args.replay:
        from .replay import CandumpReplay

        m.canbus.add_peer(CandumpReplay(args.replay, loop=args.replay_loop))
    elif args.network == "demo":
        from .peers import demo_network

        g12_path = None
        if args.eeprom != "none":
            g12_path = os.path.join(os.path.dirname(args.eeprom) or ".", "g12.json")
        demo_network(m.canbus, m.guidata, g12_path=g12_path, ambient=args.ambient)
    if args.can:
        m.canbus.connect(args.can, tx=args.can_tx, allow_config=args.allow_config,
                         tx_log=args.tx_log if args.can_tx else None)
    m.canbus.log_rx = args.log_can
    if args.stats:
        m.bus.stats = collections.Counter()
    t0 = time.time()
    try:
        if args.gui:
            why = run_gui(m, args.scale)
        else:
            why = m.run(None if args.seconds is None else int(args.seconds * CPU_HZ))
    except KeyboardInterrupt:
        why = "interrupted"
    dt = time.time() - t0
    report(m, why, dt)
    shot = None
    if args.screenshot:
        from PIL import Image

        img = m.periph["LTDC"].render()
        if img is None:
            print("  (display not configured; no screenshot)")
        else:
            Image.fromarray(img).save(args.screenshot)
            shot = args.screenshot
            print(f"  screenshot: {args.screenshot}")
    if args.stats:
        print("hottest MMIO accesses:")
        for (name, off, kind, pc), n in m.bus.stats.most_common(15):
            print(f"  {n:9,}  {name:8} +0x{off:03x} {kind}  pc=0x{pc:08x}")
    return {"ok": True, "stopped": why or "time limit", "seconds": round(m.cycles / CPU_HZ, 3),
            "wall_seconds": round(dt, 3), "screenshot": shot}


def main():
    ap = argparse.ArgumentParser(description="Spyder Controls / Firefly LCD emulator")
    add_arguments(ap)
    raise SystemExit(run(ap.parse_args(), ap))


if __name__ == "__main__":
    main()
