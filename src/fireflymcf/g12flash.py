"""Read a Firefly G12's upload identity over RV-C. **This tool writes nothing.**

It listens on a CAN bus and reports what the G12 says about itself: which upload targets it
carries -- its application, its config, its bootloader -- and each one's version, load address
and identifiers. It cannot change the device. The write half of the protocol is not implemented
here, on purpose.

WHY YOU MIGHT WANT THIS
-----------------------
The `G12` section of an MCF is loaded into the device **over RV-C**, not over USB: the panel's
boot-menu `G12` row is an RV-C upload and the USB stick only carries the file. So the device
will tell you, over the bus, exactly which config it is holding -- `ConfigType`, `ConfigID`,
`ConfigRev` -- which is how you find out whether an MCF you have is the one it wants.

WHAT IT CANNOT DO, AND THIS IS NOT A LIMITATION OF THE TOOL
-----------------------------------------------------------
It cannot read the config back. The protocol has no verb for it: the loader's three commands all
push, and there is nowhere in the loader for received bulk data to land. So there is **no way to
back up a G12's current config before changing it**. If you ever intend to write to a G12, the
MCF you are running is your only backup -- keep it.

THE LADDER
----------
    fireflymcf-g12flash probe --interface can1               # listen only; transmits NOTHING
    fireflymcf-g12flash probe --interface can1 --request     # + one 8-byte REQUEST from SA 0xC0
    fireflymcf-g12flash probe --interface can1 --request --as-loader   # + the same from SA 0xF9

The passive form is enough to name every upload target and its firmware version -- but only
because the **panel** polls for that every 14 seconds and the G12 answers. It is somebody else's
question you are overhearing, so on a bus with no panel a passive listen hears nothing, and that
silence means no poller rather than no device. The request buys the parameter *values*, and a
request directed at one device draws far more than a broadcast one does.

`--as-loader` sends from `0xF9`, the address the panel's own config loader uses. It exists
because we do not yet know whether the G12 answers a requester that is not the loader; reach for
it only if the default address is ignored. It refuses to run if it hears `0xF9` during the
listen window, since two nodes on one address is a real fault, not a nuisance.

Exit codes: 0 heard at least one device, 1 heard nothing, 2 refused.
"""
from __future__ import annotations

import argparse
import sys

from fireflymcf import console, g12net, upload

REFUSED = 2

#: Reported in this order, so the config target a reader cares about is not buried.
PARAM_ORDER = [
    "ConfigType", "ConfigID", "ConfigRev", "FuncBank", "FuncInst",
    "ExtFwVer", "FwRlsLetter", "ExtFuncCode", "BtldrVer",
    "HwVer/LcdDescType", "HwOpt", "LdAddr",
    "McuDevID", "McuFlashSize", "McuRamSize",
]

UPLOAD_TYPES = {1: "application", 2: "config (an MCF's G12 section)", 0xFF: "device/bootloader"}


class Refused(Exception):
    """The run was declined before anything was transmitted."""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fireflymcf-g12flash",
        description="Read a G12's upload identity over RV-C. Writes nothing.",
        epilog=__doc__.split("THE LADDER")[1].strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="report what the device says about itself")
    probe.add_argument("-i", "--interface", default="can1", metavar="IFACE",
                       help="the socketcan interface (default: can1, the coach's RV-C bus)")
    probe.add_argument("-t", "--listen", type=float, default=20.0, metavar="SECONDS",
                       help="how long to listen. The unprompted status broadcast runs about "
                            "every 14 s, so the default covers one cycle (default: 20)")
    probe.add_argument("--request", action="store_true",
                       help="send ONE 8-byte REQUEST frame to draw the parameter values. "
                            "Without it this transmits nothing at all")
    probe.add_argument("--target", type=lambda s: int(s, 0), metavar="ADDR",
                       help="direct the request at one device (e.g. 0x9C, the G12). A directed "
                            "request draws far more than a broadcast one")
    probe.add_argument("--as-loader", action="store_true",
                       help=f"send from {upload.LOADER_SA:#04x}, the panel loader's address, "
                            f"instead of {upload.PROBE_SA:#04x}. Only if the default is ignored")
    return p


def _describe(found: g12net.Enumeration) -> str:
    lines = []
    for source, ident in sorted(found.identities.items()):
        maker = " (Firefly)" if ident.manufacturer == upload.MANUFACTURER_FIREFLY else ""
        lines.append(f"  SA {source:#04x}  config identity (1FED8, broadcast unprompted)")
        lines.append(f"    manufacturer {ident.manufacturer}{maker}"
                     f"   FuncBank {ident.func_bank:#04x}   FuncInst {ident.func_instance:#04x}")
        lines.append(f"    ConfigType {ident.config_type:#06x}   ConfigID {ident.config_id:#04x}"
                     f"   ConfigRev {ident.config_rev}")
        lines.append("")
    for device in sorted(found.devices, key=lambda d: d.key):
        instance, kind = device.key
        lines.append(
            f"  SA {device.source:#04x}  DeviceInst {instance}  UploadType {kind:#04x}"
            f"  -- {UPLOAD_TYPES.get(kind, 'unknown')}")
        # Parameters are only expected from the device we DIRECTED a request at. A passive listen
        # never has them (0FF02 answers a request and nothing else), and neither does a device we
        # did not address -- so calling either "incomplete" fires an alarm that means nothing and
        # trains the reader to ignore the one case that does. Both forms were shipped and both
        # were caught on hardware: PC.64 and PC.67.
        if not found.was_asked(device):
            short = f"   ({device.status.parameter_count} parameters, not requested)"
        elif device.complete:
            short = ""
        else:
            short = (f"   [INCOMPLETE: declared {device.status.parameter_count}, "
                     f"heard {len(device.parameters)}]")
        lines.append(f"    version {device.status.version:#06x}"
                     f"  release {device.status.release_letter:#04x}"
                     f"  bank {device.status.func_bank:#04x}{short}")
        named = {p.name: p.value for p in device.parameters.values()}
        for name in PARAM_ORDER:
            if name in named:
                lines.append(f"      {name:<20} {named[name]:#x}")
        for name, value in sorted(named.items()):
            if name not in PARAM_ORDER:
                lines.append(f"      {name:<20} {value:#x}")
        lines.append("")
    return "\n".join(lines).rstrip()


def main(argv=None) -> int:
    console.utf8()
    args = _parser().parse_args(argv)
    source = upload.LOADER_SA if args.as_loader else upload.PROBE_SA

    try:
        if args.as_loader and not args.request:
            raise Refused("--as-loader only changes the address a request is sent FROM, so it "
                          "does nothing without --request")

        try:
            bus = g12net.SocketCanBus(args.interface)
        except OSError as e:
            raise Refused(f"cannot open {args.interface}: {e}")
    except Refused as e:
        print(f"g12flash: {e}", file=sys.stderr)
        return REFUSED

    with bus:
        if args.request:
            # Listen before transmitting: an address collision is worth catching BEFORE we add a
            # third party to it, and this is the only chance to do so.
            print(f"listening {args.listen:.0f}s before sending, to check {source:#04x} is free...")
            quiet = g12net.enumerate_devices(bus, listen=args.listen, source=source)
            if source in quiet.heard:
                who = "the panel's loader is running" if source == upload.LOADER_SA else \
                      "something else already uses it"
                print(f"g12flash: refusing to transmit from {source:#04x} -- {who}. "
                      f"Nothing was sent.", file=sys.stderr)
                print(_describe(quiet))
                return REFUSED

        found = g12net.enumerate_devices(
            bus, listen=args.listen, send_request=args.request,
            target=args.target, source=source)

    if args.request and args.target is not None and not any(
            d.source == args.target for d in found.devices):
        print(f"g12flash: {args.target:#04x} did not answer the request. Anything listed below "
              f"is another device answering somebody else's poll, not ours.\n")

    if not found.devices:
        print(f"g12flash: heard nothing on {args.interface} in {args.listen:.0f}s. The status "
              f"broadcast is unprompted and periodic, so silence means the bus or the device, "
              f"not the request.", file=sys.stderr)
        return 1

    print(f"{len(found.devices)} upload target(s) on {args.interface}"
          f"{' (one REQUEST sent from ' + hex(source) + ')' if found.transmitted else ' (nothing transmitted)'}:")
    print()
    print(_describe(found))
    print()
    print("This tool wrote nothing to the device, and cannot. There is no read-back in this "
          "protocol, so a G12's config cannot be backed up -- keep the MCF you are running.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
