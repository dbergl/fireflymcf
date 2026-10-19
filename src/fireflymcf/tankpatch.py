"""Stop a Firefly G12 broadcasting RV-C TANK_STATUS, so another sender can own the tank.

WHY YOU MIGHT WANT THIS
-----------------------
With no probe wired to a tank input the G12's ADC floats to rail, and the bucketing ladder
turns that into a perfectly well-formed `1FFB7` TANK_STATUS frame reading **0%**, once a
second, forever. No RV-C receiver can tell that from a real empty tank. Worse, the Firefly
panel's own receive filter is *source-blind* -- it matches the 18-bit DGN and nothing about
who sent the frame -- so fitting a real sender on the same instance does not win the slot, it
alternates with the G12 at 1 Hz and the gauge flickers between the two.

Silencing the G12 is therefore a precondition for fitting a different RV-C tank sender, not a
tidy-up. This tool does that, and nothing else.

WHAT IT CHANGES
---------------
Two bytes per tank. The G12 section of the MCF carries a rule engine; each broadcast is one
12-byte rule record that some instruction switches on at start-up. This NOPs that instruction.
The record itself is left intact, so the image still *describes* the broadcast and the change
is one byte-pair per tank to revert. Nothing moves, nothing grows, and the edit is confined to
the G12 section -- which the bootloader can flash on its own.

Verified on the author's coach (Jayco B-Van, Firefly G12 + Spyder 7" panel, firmware 13.6): a
12-second capture after flashing carried **zero** `19FFB7` frames where every earlier capture
carried about 48, while the proprietary `18BFC19C` raw-ADC and `19AAB79C` LPG-mirror broadcasts
kept running untouched. A foreign RV-C sender then drove the panel's own stock gauge -- and
tanks nobody claimed read the `--%` no-data sentinel rather than a stale 0%.

WHAT IT DOES NOT CHANGE
-----------------------
`0BFC1` (the raw ADC the PROBE TANK DIAGNOSTICS page reads) and `1AAB7` (Firefly's private
TANK_STATUS mirror) are proprietary DGNs that collide with nothing standard, so they are left
alone. The LPG tank (instance 3) usually has a real sender and is never silenced by default.

BUILDS
------
Validated on 11.23, 13.6 and 14.7, which are structurally different from each other -- 11.23
has no LPG rule at all and gates nothing; 13.6 gates two tanks; 14.7 gates all five. Nothing
here is a hardcoded offset: the tanks are read out of the rule table by DGN, and the
instruction that switches each one on is found by scanning for its idiom. On a build this tool
has not seen, it runs an audit first and refuses rather than guessing. If it refuses on your
image, the report it prints is what to send to the maintainer.

    fireflymcf-tankpatch mcf.bin                                  # report only, writes nothing
    fireflymcf-tankpatch mcf.bin --all-water -o mcf-notank.bin    # the usual case
    fireflymcf-tankpatch mcf.bin --fresh --grey -o mcf-notank.bin # pick tanks individually

Exit codes: 0 success, 2 refused (nothing written), 3 the written image failed its own
verification (nothing written).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fireflymcf import container, patch, tankbroadcast

# `--flag` -> RV-C TANK_STATUS instance. The names are RV-C's, not Firefly's.
TANK_FLAGS = {
    "fresh": 0,
    "black": 1,
    "grey": 2,
    "lpg": 3,
    "grey2": 18,
}
# What `--all-water` means: every water and waste tank, never the LPG.
ALL_WATER = tankbroadcast.DEFAULT_INSTANCES

# Appended to the header build name unless `--name` or `--no-rename` says otherwise. The
# bootloader's file menu lists images by that string, NOT by filename, so two builds sharing a
# name are indistinguishable at the one moment picking the wrong one is expensive.
NAME_SUFFIX = " No Tank Bcast"

REFUSED = 2
UNVERIFIED = 3


class Refused(Exception):
    """Something is wrong with the request or the image. Nothing has been written."""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fireflymcf-tankpatch",
        description=__doc__.split("WHY YOU MIGHT WANT THIS")[0].strip(),
        epilog="Run with no tank flags to see what your image broadcasts before changing it.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mcf", type=Path, help="the MCF file to read (the whole mcf.bin)")
    p.add_argument("-o", "--output", type=Path, metavar="FILE",
                   help="write the patched image here. Without it, nothing is written")
    p.add_argument("--extract-g12", type=Path, metavar="FILE",
                   help="also write out the G12 section on its own -- stock if no tank was "
                        "named, patched if one was. That section is exactly what the device "
                        "receives over RV-C, so it is the useful unit to hold; "
                        "fireflymcf-g12flash reads the device that consumes it")

    tanks = p.add_argument_group(
        "tanks to silence", "None of these means report-only. RV-C TANK_STATUS instances.")
    tanks.add_argument("--all-water", action="store_true",
                       help=f"fresh, black, grey and second grey (instances "
                            f"{', '.join(str(i) for i in ALL_WATER)}) -- the usual choice")
    for name, instance in TANK_FLAGS.items():
        tanks.add_argument(f"--{name}", action="store_true",
                           help=f"instance {instance} ({tankbroadcast.INSTANCE_NAMES[instance]})"
                                + (" -- never included in --all-water" if instance == 3 else ""))
    tanks.add_argument("--instance", type=int, action="append", default=[], metavar="N",
                       help="any other RV-C instance, by number; repeatable")

    naming = p.add_argument_group("the name the bootloader menu shows")
    naming.add_argument("--name", metavar="TEXT",
                        help="set the build name outright (max 127 characters)")
    naming.add_argument("--no-rename", action="store_true",
                        help=f"leave the build name alone (default: append '{NAME_SUFFIX}')")

    overrides = p.add_argument_group("overrides")
    overrides.add_argument("--force", action="store_true",
                           help="patch even though the build audit failed. The audit exists "
                                "because the instruction this tool edits is found by pattern, "
                                "not by a fixed address; read the report before using this")
    overrides.add_argument("--any-filename", action="store_true",
                           help="allow an output name that does not start with 'mcf-'. The "
                                "bootloader lists no other file, so the image would have to be "
                                "renamed before it could be flashed")
    return p


def _g12_section(data: bytes) -> bytes:
    """The `G12` section's bytes -- exactly what the device is sent over RV-C.

    Not a flashable image: the bootloader lists whole MCFs named `mcf-*.bin` and would never
    offer this. It is the unit the *device* consumes, which is a different thing.
    """
    for section in container.parse_container(data).sections:
        if section.name.startswith("G12"):
            return data[section.offset:section.offset + section.length]
    raise Refused("this image has no G12 section, so there is nothing to extract")


def _wanted(args) -> list:
    """The instances the flags name, in a stable order, deduplicated."""
    picked = list(ALL_WATER) if args.all_water else []
    for name, instance in TANK_FLAGS.items():
        if getattr(args, name):
            picked.append(instance)
    picked.extend(args.instance)
    for i in picked:
        if not 0 <= i <= 255:
            raise Refused(f"instance {i} is not an RV-C instance byte (0-255)")
    return sorted(dict.fromkeys(picked))


def _panel_names(data: bytes) -> dict:
    """`{instance: caption}` from the panel's own gauges (`tanknames`); empty if not traced."""
    from fireflymcf import tanknames
    try:
        return {i: n for i, (n, _src) in tanknames.tank_names(data).items() if n}
    except tanknames.TankNameError:
        return {}


def _inventory(report, data: bytes) -> str:
    panel = _panel_names(data)
    lines = ["What this image broadcasts on 1FFB7 TANK_STATUS:", "",
             f"  {'instance':<12} {'panel name':<14} {'RV-C name':<18} {'flag':<15} state"]
    for b in report.broadcasts:
        flag = next((f"--{n}" for n, i in TANK_FLAGS.items() if i == b.instance),
                    f"--instance {b.instance}")
        state = ("SILENT -- nothing switches this rule on" if b.activate_at is None else
                 "broadcasting once a second, behind a config gate the G12 re-checks"
                 if b.guarded else "broadcasting once a second")
        lines.append(f"  instance {b.instance:<3} {panel.get(b.instance, '-'):<14} "
                     f"{b.tank:<18} {flag:<15} {state}")
    lines += ["", "  The instance numbers are read from this file's G12 rules. The panel name is the",
              "  caption the panel draws on the gauge that instance feeds, read from this file",
              "  (`-`: no gauge traced). The RV-C name is the standard's for the number. Where they",
              "  disagree, or the panel name is missing, check the panel before silencing a tank.",
              "  Some gauges draw only under some floorplans or options."]
    return "\n".join(lines)


def _renamed(data: bytes, args) -> bytes:
    if args.name:
        name = args.name
    elif args.no_rename:
        return data
    else:
        current = patch.build_name(data)
        if current.endswith(NAME_SUFFIX):
            return data
        name = current + NAME_SUFFIX
    if len(name.encode()) >= patch.BUILD_NAME_CAPACITY:
        raise Refused(f"build name is {len(name.encode())} bytes; the header field holds "
                      f"{patch.BUILD_NAME_CAPACITY - 1}")
    out, _ = patch.set_build_name(data, name)
    return out


def _moved_checksums(before: bytes, after: bytes) -> list:
    """Which stored checksums the patch moved. Read off the images, not tallied per step."""
    return [f.label for f in patch.checksum_fields(before) if f.stored(before) != f.stored(after)]


def _verify(before: bytes, after: bytes, instances, renamed: bool) -> None:
    """Re-derive from the OUTPUT that the edit landed, and that nothing else moved.

    Deriving from the output rather than trusting the edit list is the point: a patch that
    reported success while changing nothing would pass the second check and fail the first.
    """
    quiet = set(tankbroadcast.suppressed_instances(after))
    missed = [i for i in instances if i not in quiet]
    if missed:
        raise Refused(f"the patched image still switches on instance(s) {missed} -- "
                      f"refusing to write an image that does not do what was asked",)

    g12 = next(s for s in container.parse_container(before).sections
               if s.name.startswith("G12"))
    allowed = [(g12.offset, g12.offset + g12.length)]
    if renamed:
        # Exactly the name field and the header checksum word -- NOT "everything before the
        # G12 section", which would wave through an edit anywhere in GuiCode or GuiData.
        allowed.append((patch.BUILD_NAME_OFFSET,
                        patch.BUILD_NAME_OFFSET + patch.BUILD_NAME_CAPACITY))
        header = next(f for f in patch.checksum_fields(before)
                      if f.label == "header-checksum")
        allowed.append((header.position, header.position + 2))
    for start, length in patch.diff_ranges(before, after):
        end = start + length
        if not any(lo <= start and end <= hi for lo, hi in allowed):
            raise Refused(f"the patch changed bytes at {start:#x}-{end:#x}, outside the "
                          f"sections it is allowed to touch")

    stale = patch.reseal(after)[1]
    if stale:
        raise Refused("the patched image's checksums are stale: " + ", ".join(stale))


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        try:
            data = args.mcf.read_bytes()
        except OSError as e:
            raise Refused(str(e))

        try:
            report = tankbroadcast.audit(data)
        except tankbroadcast.TankBroadcastError as e:
            raise Refused(str(e))
        print(_inventory(report, data))
        print()
        print(report.report())
        print()

        wanted = _wanted(args)

        # Checked before any work, so a run that cannot land its output fails before it starts.
        if args.extract_g12 is not None and args.extract_g12.exists():
            raise Refused(f"{args.extract_g12} already exists; refusing to overwrite it")

        if not wanted:
            if args.extract_g12 is None:
                print("No tank named, so nothing was written. Add --all-water to silence the "
                      "four water and waste tanks, or name them one at a time; -o FILE to "
                      "write. --extract-g12 FILE writes the stock G12 section on its own.")
                return 0
            section = _g12_section(data)
            args.extract_g12.write_bytes(section)
            print(f"  G12 section : {args.extract_g12}  ({len(section)} bytes, unpatched)")
            print()
            print("No tank was named, so this is the stock section. It is what the device "
                  "receives over RV-C; fireflymcf-g12flash reads the device that consumes it.")
            return 0

        if not report.ok and not args.force:
            raise Refused(
                "this image does not match the shape this tool reads, so it will not be "
                "patched. The audit above says which check failed. If you believe the image "
                "is sound, --force patches anyway; otherwise send that report to the "
                "maintainer, because a wrong edit here is a wrong instruction in the G12.")

        if args.output is None and args.extract_g12 is None:
            raise Refused("tanks were named but neither -o FILE nor --extract-g12 FILE was "
                          "given, so there is nowhere to write the result")
        if args.output is not None:
            if args.output.exists():
                raise Refused(f"{args.output} already exists; refusing to overwrite it")
            if not args.output.name.startswith("mcf-") and not args.any_filename:
                raise Refused(
                    f"the bootloader lists only files named 'mcf-*.bin', so "
                    f"'{args.output.name}' would never appear in its menu. Rename it, or "
                    f"pass --any-filename if you know you will rename it later.")

        try:
            out, edits = tankbroadcast.suppress(data, wanted)
        except tankbroadcast.TankBroadcastError as e:
            raise Refused(str(e))
        named = _renamed(out, args)
        renamed = named != out
        out, _ = patch.reseal(named)
        # Derived from the two images rather than accumulated from each step: `set_build_name`
        # reseals on its way out, so a step-by-step tally under-reports.
        resealed = _moved_checksums(data, out)
    except Refused as e:
        print(f"tankpatch: {e}", file=sys.stderr)
        return REFUSED

    try:
        _verify(data, out, wanted, renamed=renamed)
    except Refused as e:
        print(f"tankpatch: {e}", file=sys.stderr)
        return UNVERIFIED

    if args.output is not None:
        args.output.write_bytes(out)
    if args.extract_g12 is not None:
        args.extract_g12.write_bytes(_g12_section(out))
    for offset, old, new, why in edits:
        print(f"  {offset:#08x}  {old.hex()} -> {new.hex()}  {why}")
    print()
    print(f"  build name : {patch.build_name(out)}")
    print(f"  resealed   : {', '.join(resealed) or 'nothing'}")
    if args.output is not None:
        print(f"  written    : {args.output}  ({len(out)} bytes)")
    if args.extract_g12 is not None:
        section = _g12_section(out)
        print(f"  G12 section: {args.extract_g12}  ({len(section)} bytes, patched)")
    print()
    if args.output is not None:
        print("Flash it with the USB stick and a power cycle, the same way as a vendor image. "
              "To revert, flash the image you started from -- keep it.")
    else:
        print("Only the G12 section was written, which the bootloader cannot flash: it lists "
              "whole images named 'mcf-*.bin'. Add -o FILE for something you can flash. To "
              "revert any change, flash the image you started from -- keep it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
