"""Phase-1 relocator spike: how completely can GuiData's absolute self-pointers be
localized structurally?

A GuiData relocator (inserting new records) must bump every `0x900xxxxx` self-pointer
whose target is after the insertion point. That is only safe if *every* such pointer is
known by position — a false positive corrupts data, a miss leaves a dangling pointer.
This module classifies every aligned self-pointer as (a) an object-table entry,
(b) inside a decoded record body, or (c) unclassified, and reports the counts. The
unclassified/non-table total is the go/no-go number for the relocator scoped in
its design note (not published).

Run:  python3 -m fireflymcf.relocator_scan [mcf.bin ...]
      (defaults to the three corpus builds)
"""
from __future__ import annotations

import struct
import sys

from . import guidata, records

BASE = 0x90000000
DEFAULT_BUILDS = [
    ("11.23", "firefly-11.23/mcf.bin"),
    ("13.6", "firefly-13.6/mcf.bin"),
    ("14.7", "firefly-14.7 (Terrain)/mcf.bin"),
]


def _aligned_self_pointers(seg: bytes) -> dict[int, int]:
    """{byte position: target section-offset} for aligned words pointing inside GuiData."""
    n = len(seg)
    hi = BASE + n
    out = {}
    for off in range(0, n - 3, 4):
        v = struct.unpack_from("<I", seg, off)[0]
        if BASE <= v < hi:
            out[off] = v - BASE
    return out


def _object_table_positions(sp: dict[int, int]) -> set[int]:
    """Byte positions of the object-pointer tables: contiguous aligned runs of >=8."""
    runs = []
    start = prev = None
    for off in sorted(sp):
        if start is None:
            start = prev = off
        elif off == prev + 4:
            prev = off
        else:
            runs.append((start, prev))
            start = prev = off
    if start is not None:
        runs.append((start, prev))
    pos = set()
    for a, b in runs:
        if (b - a) // 4 + 1 >= 8:
            for o in range(a, b + 4, 4):
                pos.add(o)
    return pos


def scan(path: str) -> dict:
    data = open(path, "rb").read()
    gs, ge = guidata.guidata_extent(data)
    seg = data[gs:ge]
    n = len(seg)
    hi = BASE + n

    sp = _aligned_self_pointers(seg)
    table_pos = _object_table_positions(sp)

    # decode every object; collect record byte spans + valid pointer destinations
    # (object starts and record starts) — the only offsets a live pointer targets.
    spans = []
    obj_starts = set()
    rec_starts = set()
    for tbl in guidata.object_tables(data):
        for objstart in tbl:  # object_tables returns section-relative offsets
            obj_starts.add(objstart)
            if not (0 <= objstart < n):
                continue
            try:
                recs = records.object_records(data, gs + objstart)
            except Exception:
                continue
            for r in recs:
                rs = r.offset - gs
                spans.append((rs, rs + r.size))
                rec_starts.add(rs)
    spans.sort()
    valid_targets = obj_starts | rec_starts

    non_table = [(o, t) for o, t in sp.items() if o not in table_pos]
    real_target = sum(1 for _, t in non_table if t != 0)
    at_base = len(non_table) - real_target
    unaligned = sum(
        1 for o in range(0, n - 3)
        if o % 4 and BASE <= struct.unpack_from("<I", seg, o)[0] < hi
    )

    # The relocator question: how many self-pointer-shaped words OUTSIDE the object
    # tables actually target a valid object/record boundary? Those are the only ones
    # that could be live embedded pointers; everything else is data or a sentinel.
    live_non_table = 0
    for o in range(0, n - 3):
        if o in table_pos:
            continue
        v = struct.unpack_from("<I", seg, o)[0]
        if not (BASE <= v < hi):
            continue
        t = v - BASE
        if t != 0 and t in valid_targets:
            live_non_table += 1

    return {
        "bytes": n,
        "self_pointers": len(sp),
        "in_tables": len(table_pos),
        "non_table": len(non_table),
        "real_target": real_target,
        "at_base": at_base,
        "unaligned": unaligned,
        "live_non_table": live_non_table,
    }


def main(argv: list[str]) -> int:
    builds = [(p, p) for p in argv[1:]] or DEFAULT_BUILDS
    for tag, path in builds:
        try:
            r = scan(path)
        except Exception as exc:  # noqa: BLE001 - a scan is a diagnostic, keep going
            print(f"{tag}: ERROR {exc!r}")
            continue
        pct = 100.0 * r["in_tables"] / r["self_pointers"]
        print(f"=== {tag}  GuiData={r['bytes']} bytes ===")
        print(f"  aligned self-pointers : {r['self_pointers']}")
        print(f"  in object tables      : {r['in_tables']}  ({pct:.1f}%) - live, localized")
        print(f"  non-table aligned     : {r['non_table']}  "
              f"(real-target {r['real_target']} / ==base {r['at_base']})")
        print(f"  unaligned candidates  : {r['unaligned']}")
        print(f"  >> non-table words targeting an object/record boundary: "
              f"{r['live_non_table']}  (chance-level; NOT live pointers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
