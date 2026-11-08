#!/usr/bin/env python3
"""Regenerate `src/fireflymcf/data/grammar136.json`, the fingerprints `grammar.prove` searches
for, from 13.6 stock.

    python scripts/gen_grammar_fingerprints.py [firefly-13.6/mcf.bin]

**Extents come from Ghidra, not from a rule.** `GHIDRA_BODIES` holds each function's body
(`getFunctionAt(entry).getBody()`) in the research repository's `guicode136` project, queried
2026-11-08 with this read-only PyGhidra post-script:

    fn = getFunctionAt(currentProgram.getAddressFactory().getAddress(ENTRY))
    print(fn.getName(), [(str(r.getMinAddress()), str(r.getMaxAddress()))
                         for r in fn.getBody().getAddressRanges()])

The ranges' gaps are the data inside each function. A function's fingerprint covers its entry to
its body's last byte. The generator sweeps that span with `grammar.sweep`, the same sweep the
search uses, and refuses unless the sweep's code/data split equals Ghidra's:
- every instruction lies inside a body range;
- every pool word and table lies in a gap;
- the sweep ends exactly at the body's end.

Pads may fall either way, because Ghidra puts some inside its ranges and some outside.

**The renderer** is fingerprinted as a dispatch plus segments:
- the dispatch is its entry through `tbh [pc, r3, lsl #1]` and that tbh's table;
- the segments are the code after the table, then each switch arm, from its start in the table to
  the next arm start. The last arm runs to the body's end.

Each segment records its wtypes (the table entries that select it) and the indices of its
unconditional exits (for fall-through).

**What an item holds.** Each item keeps its concrete registers, for the search's register
bijection. Data items hold their values (pool words, compared under the search's address
mappings). Code targets hold their position, or a callee's 13.6 address. `guicode_region` is
13.6's GuiCode span, used to classify GuiCode pointers.

**Bounds.** `BOUNDS` names the `cmp.w R, #imm` sites whose immediate may differ: the decoder's
`mode7_index`, every `cmp.w R, #0x298` in it, 13 sites (`BOUND_SITES`).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fireflymcf import firmware, grammar  # noqa: E402

OUT = ROOT / "src" / "fireflymcf" / "data" / "grammar136.json"

# Ghidra `guicode136` function bodies, inclusive (min, max) ranges, queried 2026-11-08.
GHIDRA_BODIES = {
    "decoder": ("FUN_00252CB0", 0x252CB0, [
        (0x252CB0, 0x252FF7), (0x25300C, 0x25331B), (0x253328, 0x25335D), (0x25337C, 0x25341D),
        (0x25343C, 0x2534DD), (0x2534E6, 0x2535A1), (0x2535B8, 0x2535D7), (0x2535E0, 0x253849),
        (0x253860, 0x253A13), (0x253A2C, 0x253CFB), (0x253D18, 0x253E0D)]),
    "layout": ("FUN_00257BFC", 0x257BFC, [
        (0x257BFC, 0x257E63), (0x257E74, 0x25801B), (0x258028, 0x2580B5), (0x258116, 0x2582AF),
        (0x2582C8, 0x258329)]),
    "renderer": ("FUN_00256C54", 0x256C54, [
        (0x256C54, 0x256CD5), (0x256D36, 0x256EC5), (0x256ECC, 0x257113), (0x257120, 0x25740F),
        (0x257414, 0x2576E3), (0x2576E8, 0x25799D), (0x2579A4, 0x257BED)]),
}
BOUNDS = {"decoder": {"mode7_index": "cmp.w R, #0x298"}}
BOUND_SITES = {"mode7_index": 13}


def _fail(msg: str) -> None:
    raise SystemExit(f"gen_grammar_fingerprints: {msg}")


def _check_against_ghidra(name: str, items: list, ranges: list) -> None:
    def inside(a: int) -> bool:
        return any(lo <= a <= hi for lo, hi in ranges)
    for it in items:
        cover = [inside(a) for a in range(it.addr, it.addr + it.size)]
        if it.text == "<pad>":
            continue
        if it.text.startswith(("<data", "<table")):
            if any(cover):
                _fail(f"{name}: {it.text} at {it.addr:#x} lies in Ghidra's body")
        elif it.text == "<bad>" or not all(cover):
            _fail(f"{name}: `{it.raw}` at {it.addr:#x} lies outside Ghidra's body")
    end = max(hi for _lo, hi in ranges) + 1
    if items[-1].addr + items[-1].size != end:
        _fail(f"{name}: the sweep ends at {items[-1].addr + items[-1].size:#x}, "
              f"Ghidra's body at {end:#x}")


def _segment(start: int, wtypes: list, items: list, enc: list) -> dict:
    return {"start": start, "wtypes": wtypes,
            "exits": [i for i, it in enumerate(items) if it.exit], "items": enc}


def build(data: bytes) -> dict:
    if firmware.identify(data) != "13.6":
        _fail("the fingerprints derive from 13.6 stock; this image is "
              f"{firmware.identify(data) or firmware.describe(data)}")
    code, base = grammar.section(data)
    functions = {}
    for name, (ghidra, entry, ranges) in GHIDRA_BODIES.items():
        end = max(hi for _lo, hi in ranges) + 1
        data_map: dict[int, int] = {}
        if name != "renderer":
            items = list(grammar.sweep(code, base, entry, end=end, data=data_map))
            _check_against_ghidra(name, items, ranges)
            enc = grammar.encode([items], code, base)
            segments = [_segment(entry, [], items, enc[0])]
            swept = [items]
        else:
            disp = []
            for it in grammar.sweep(code, base, entry, end=end, data=data_map):
                disp.append(it)
                if it.entries:
                    break
            tbh = [it for it in disp if it.raw.startswith("tbh")]
            if len(tbh) != 1 or not disp[-1].entries:
                _fail("renderer: the dispatch does not end in one tbh table")
            table = disp[-1]
            by_start: dict[int, list] = {}
            for w, t in enumerate(table.entries):
                by_start.setdefault(t, []).append(w)
            starts = sorted({table.addr + table.size, *table.entries})
            if starts[0] != table.addr + table.size:
                _fail("renderer: an arm starts inside the dispatch")
            swept = [disp]
            for k, s in enumerate(starts):
                stop = starts[k + 1] if k + 1 < len(starts) else end
                seg = list(grammar.sweep(code, base, s, end=stop, data=data_map))
                if seg[-1].addr + seg[-1].size != stop:
                    _fail(f"renderer: the segment at {s:#x} runs past {stop:#x}")
                swept.append(seg)
            _check_against_ghidra(name, [it for seg in swept for it in seg], ranges)
            enc = grammar.encode(swept, code, base)
            segments = [_segment(entry, [], disp, enc[0])]
            segments += [_segment(s, sorted(by_start.get(s, [])), swept[k + 1], enc[k + 1])
                         for k, s in enumerate(starts)]
        fn = {"ghidra": ghidra, "entry": entry, "end": end, "segments": segments}
        if name in BOUNDS:
            fn["bounds"], fn["bound_values"] = {}, {}
            for bname, text in BOUNDS[name].items():
                sites = [[s, i] for s, seg in enumerate(segments)
                         for i, e in enumerate(seg["items"]) if grammar.abstract(grammar.text_part(e)) == text]
                if len(sites) != BOUND_SITES[bname]:
                    _fail(f"{name}: {len(sites)} `{text}` sites, expected {BOUND_SITES[bname]}")
                fn["bounds"][bname] = sites
                fn["bound_values"][bname] = int(text.rsplit("#", 1)[1], 0)
        functions[name] = fn
    return {"_generated_by": "scripts/gen_grammar_fingerprints.py from 13.6 stock; do not edit",
            "guicode_region": [base, base + len(code)], "functions": functions}


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else ROOT / "firefly-13.6" / "mcf.bin"
    fp = build(path.read_bytes())
    OUT.write_text(json.dumps(fp, indent=1) + "\n", encoding="utf-8")
    n = sum(len(s["items"]) for f in fp["functions"].values() for s in f["segments"])
    print(f"wrote {OUT.relative_to(ROOT)}: {n} items")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
