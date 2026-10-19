"""Give a control a HOLD action beside its tap, by splitting its hit region.

`docs/CONTROL-BEHAVIOR.md` §1 (the id-change edge) and §7 (protected touch areas).

**Why a split and not a second action on the same region.** The GuiCode action dispatcher is
edge-triggered on the touch id (`cmp`/`beq` @`0x0024ACBA`), so a region's tap handler runs the
instant the finger lands — before any hold deadline can expire. A tap action and a hold action
can therefore coexist on one region only when the tap is *harmless to perform on the way to the
hold*; both shipped examples respect that (id `0x838` navigates then navigates, id `0x7E8` has no
tap handler at all). A tap that toggles a load does not qualify: you would switch the load every
time you reached for the hold. Swapping them does not help either — a navigating tap changes what
sits under the finger, the id changes, and the hold consumer's "id still matches" test fails, so
a navigating tap cancels its own hold.

The way out is two *non-overlapping* regions: the control keeps its tap, and a strip beside it
carries the hold. Overlapping them would be useless — the hit test returns the **lowest** matching
id (`0x00258BB2`-`0x00258C26`) and every class-1 id is below every class-2 id.

## The two halves

`split_touch_region` is the GuiData half. It shrinks the control's existing `wtype 12` record and
inserts a second one beside it. Three things had to be right, and the third is the one that bites:

* a touch record is `<8-byte header><u16 w><u16 h><u16 id>`, and header byte 2 is the geometry
  presence mask — bit 0 adds a leading `u16 x`, so the inserted record is 16 bytes (conveniently
  already 4-aligned, which `relocate` requires);
* positions are **flow-relative**, not absolute: the record carries only a pen offset, so where it
  lands is a property of the layout replay, not of the bytes alone. `split_touch_region` therefore
  *verifies* the result by re-walking the finished image rather than trusting the arithmetic;
* **an inserted record advances the pen by its own x, shifting everything after it.** The first
  working version placed both hit regions perfectly and moved the block's label and boxes 148 px
  to the right, off the panel -- and every check passed, because they only looked at the hit
  regions. The fix is to subtract the same offset from the *next* record's x (one same-size u16),
  which restores the flow exactly; the guard is `_drawn_placements`, which asserts every
  non-touch element of the object lands where it did before. Verifying only the thing you set is
  how that defect survived: check what you did **not** intend to change;
* **an object's first byte is its RECORD COUNT** (`records.object_records`: `count = data[obj]`).
  Insert a record without bumping it and the decoder stops one record early, which is exactly what
  the boundary oracle reported the first time this was attempted. That byte is the whole reason
  the first attempt failed, and it is a one-byte edit.

`wire_hold_to_screen` is the GuiCode half, three same-size edits:

* the arm's delay (`0x0024B7E6`), rewritten from the firmware's own encoding of the same
  instruction rather than an assembled one — `addw r3,r3,#0xbb8` is lifted byte-for-byte from the
  CLEAR EEPROM timer at `0x0024EC54`;
* the expired-deadline consumer's id compare (`0x0024E90E`) and its screen constant
  (`0x0024E916`).

**The consumer chain has exactly two branches and no room for a third**, so wiring a new id means
*repurposing* one. `0x838`'s is the cheap one to take: it is the SETTINGS icon's 5-second shortcut
to AUTO ENGINE START ENABLE, and that screen also opens from an ordinary button (id `0x7E1`,
registered by objects 92/690/826), so only the shortcut is lost, not the screen.

**Service Mode bypasses the result.** With `0x2003A528 == 1` the protected-area delay is removed
and the hold becomes a plain tap (`0x0024C7E8`). That is the firmware's documented behaviour, not
a defect of this edit — object 20's own caption says so.

STATUS: the GuiCode edits are `proven-from-code` and same-size; the region split is byte-verified
(boundary oracle + a layout re-walk asserting the final boxes) and **CONFIRMED ON HARDWARE
2026-08-23** (`BUS-BASELINE.md` PC.22) — tap and all three holds behave as designed. That round
trip is what promotes two new capabilities from "gated" to "works": inserting a record into an
object's record stream, and authoring Thumb-2 into dead space (`thumb.py`).
"""
from __future__ import annotations

import re
import struct

from . import guidata, layout, records, relocate, theme, thumb

# --- the GuiData half ---------------------------------------------------------------------

# Header byte 2 is the geometry presence mask; bits 0..3 are pen coords x, y, w, h
# (`records._PEN_BITS`). A touch record ships with `0x0c` (w+h); adding bit 0 gives it an x.
GEOM_BIT_X = 0x01

# --- the GuiCode half ---------------------------------------------------------------------

# The dormant protected-touch arm (`CONTROL-BEHAVIOR.md` §7's "third arm"): it stores
# `now + delay` into the shared deadline cell 0x200398A8 for touch id 0x1795, an id no object
# registers. Reached as an ordinary leaf of the dispatch tree for any id > 0xFA1
# (0x24ACD8 bhi -> 0x24B164 bhi -> 0x24B1C0), so registering a region with that id is enough --
# nothing has to be repointed.
DORMANT_HOLD_ID = 0x1795
ARM_DELAY_SITE = 0x0024B7E6          # add.w r3,r3,#0x3e8   (1000 ms)
ARM_DELAY_OLD = bytes.fromhex("03f57a73")
# `addw r3,r3,#0xbb8` (3000 ms), lifted verbatim from the CLEAR EEPROM timer at 0x0024EC54
# rather than assembled here -- same instruction, same registers, known-good encoding.
ARM_DELAY_3000 = bytes.fromhex("03f6b833")
ARM_DELAY_SOURCE = 0x0024EC54

# The expired-deadline consumer (0x0024E8EC): a two-branch chain on the current touch id.
CONSUMER_ID_SITE = 0x0024E90E        # movw r2,#0x838
CONSUMER_ID_OLD = bytes.fromhex("40f63802")
CONSUMER_SCREEN_SITE = 0x0024E916    # movs r3,#0x11   (screen 17)
CONSUMER_SCREEN_OLD = bytes.fromhex("1123")


def encode_movw(rd: int, imm16: int) -> bytes:
    """Thumb-2 `movw rD,#imm16` (T3), 4 bytes.

    Encoded here rather than copied because the immediate is the thing being changed. Callers
    should round-trip the result through capstone; `wire_hold_to_screen` does.
    """
    if not 0 <= imm16 <= 0xFFFF:
        raise ValueError(f"imm16 out of range: {imm16:#x}")
    if not 0 <= rd <= 15:
        raise ValueError(f"bad register r{rd}")
    imm4 = (imm16 >> 12) & 0xF
    i = (imm16 >> 11) & 1
    imm3 = (imm16 >> 8) & 7
    imm8 = imm16 & 0xFF
    hw1 = 0xF240 | (i << 10) | imm4
    hw2 = (imm3 << 12) | (rd << 8) | imm8
    return struct.pack("<HH", hw1, hw2)


def encode_movs(rd: int, imm8: int) -> bytes:
    """Thumb `movs rD,#imm8` (T1), 2 bytes. `rd` must be a low register."""
    if not 0 <= imm8 <= 0xFF:
        raise ValueError(f"imm8 out of range: {imm8:#x}")
    if not 0 <= rd <= 7:
        raise ValueError(f"movs T1 needs a low register, got r{rd}")
    return struct.pack("<H", 0x2000 | (rd << 8) | imm8)


def touch_record(data: bytes, obj_index: int) -> records.WRecord:
    """The object's sole `wtype 12` record, or raise.

    Sole, deliberately: this module's whole job is to turn one region into two, so an object
    that already has several is not the shape it was written for and is refused rather than
    guessed at.
    """
    gs, _ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[relocate.OBJECT_TABLE_INDEX]
    base = gs + table[obj_index]
    found = [r for r in records.object_records(data, base) if r.wtype == 12]
    if len(found) != 1:
        raise ValueError(
            f"object {obj_index} has {len(found)} touch records, expected exactly 1")
    return found[0]


def region_boxes(data: bytes, obj_index: int, root: int = 4,
                 slot_values: dict | None = None) -> list[tuple]:
    """Absolute `(x0,y0,x1,y1)` of every touch region `obj_index` places on `root`.

    This is the verification oracle for the split: hit-region geometry is flow-relative, so the
    only trustworthy statement about where a region lands is one made by replaying the layout
    engine over the finished bytes.
    """
    screen = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136)
    placed = screen.walk(root, slot_values=slot_values or {})
    return [(q.x0, q.y0, q.x1, q.y1)
            for q in placed if q.obj_index == obj_index and q.wtype == 12]


def split_touch_region(data: bytes, obj_index: int, *, tap_width: int, hold_x: int,
                       hold_width: int, hold_id: int = DORMANT_HOLD_ID,
                       expect: tuple | None = None, root: int = 4,
                       slot_values: dict | None = None) -> tuple[bytes, list]:
    """Shrink `obj_index`'s hit region to `tap_width` and insert a second one beside it.

    The new record is a copy of the existing one's header with the x-presence bit set, followed
    by `<x><w><h><id>`; its height is the original's, so the two strips always align.

    `expect`, when given, is the `((tap box), (hold box))` the caller requires and is asserted
    against a **re-walk of the finished image**. Pass it: the arithmetic that produces `hold_x`
    is a guess about the layout pen, and this is the only thing that turns it into a fact.
    """
    gs, _ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[relocate.OBJECT_TABLE_INDEX]
    base = gs + table[obj_index]
    rec = touch_record(data, obj_index)

    if rec.geom[2] is None or rec.geom[3] is None:
        raise ValueError(f"object {obj_index}'s touch record carries no width/height")
    if not 0 < tap_width < rec.geom[2]:
        raise ValueError(
            f"tap_width {tap_width} must be between 1 and the current {rec.geom[2]}")

    all_recs = records.object_records(data, base)
    idx = all_recs.index(rec)
    if idx + 1 >= len(all_recs):
        raise ValueError(
            f"object {obj_index}'s touch record is last; there is no following record to absorb "
            "the pen advance")
    nxt = all_recs[idx + 1]
    if nxt.geom[0] is None:
        raise ValueError(
            f"object {obj_index}'s record after the hit region carries no x, so the pen advance "
            "cannot be compensated")

    before = _drawn_placements(data, obj_index, root, slot_values)
    out = bytearray(data)
    edits: list[tuple[int, bytes, bytes, str]] = []

    # 1. shrink the existing region
    off = rec.geom_off[2]
    old = bytes(out[off:off + 2])
    struct.pack_into("<H", out, off, tap_width)
    edits.append((off, old, bytes(out[off:off + 2]),
                  f"obj {obj_index} tap region width {rec.geom[2]} -> {tap_width}"))

    # 2. compensate the pen. The inserted record advances the flow by its own x, so without
    #    this every later record in the object moves right by `hold_x` -- the label and boxes
    #    slide off the panel while both hit regions still look perfect.
    noff = nxt.geom_off[0]
    nold = bytes(out[noff:noff + 2])
    struct.pack_into("<H", out, noff, (nxt.geom[0] - hold_x) & 0xFFFF)
    edits.append((noff, nold, bytes(out[noff:noff + 2]),
                  f"obj {obj_index} pen compensation: next record x {nxt.geom[0]} -> "
                  f"{nxt.geom[0] - hold_x}"))

    # 3. bump the object's record count -- the byte that made the first attempt fail
    count = out[base]
    if count != len([r for r in records.object_records(data, base)]):
        raise ValueError(f"object {obj_index}'s count byte {count} disagrees with its records")
    out[base] = count + 1
    edits.append((base, bytes([count]), bytes([count + 1]),
                  f"obj {obj_index} record count {count} -> {count + 1}"))

    # 4. build and insert the new region
    header = bytearray(data[rec.offset:rec.offset + 8])
    header[2] |= GEOM_BIT_X
    payload = bytes(header) + struct.pack(
        "<HHHH", hold_x & 0xFFFF, hold_width, rec.geom[3], hold_id)
    if len(payload) % 4:
        raise ValueError(f"payload must be 4-aligned, got {len(payload)}")
    check = records.decode_record(payload + b"\x00" * 32, 0)
    if check.wtype != 12 or check.size != len(payload):
        raise ValueError(
            f"synthesised record does not decode as a {len(payload)}-byte touch record: "
            f"wtype {check.wtype}, size {check.size}")

    at = (rec.offset + rec.size) - gs
    data, reloc_edits = relocate.relocate(bytes(out), at, payload)
    edits.append((rec.offset + rec.size, b"", payload,
                  f"obj {obj_index} new hit region id {hold_id:#06x} "
                  f"(x={hold_x}, w={hold_width})"))

    # 5. verify from the FINISHED bytes, not from the arithmetic above
    boxes = region_boxes(data, obj_index, root=root, slot_values=slot_values)
    if len(boxes) != 2:
        raise ValueError(f"expected 2 touch regions after the split, walked {len(boxes)}")
    if expect is not None and tuple(boxes) != tuple(expect):
        raise ValueError(f"split landed at {boxes}, expected {list(expect)}")
    after = _drawn_placements(data, obj_index, root, slot_values)
    if after != before:
        raise ValueError(
            "the split moved the object's drawn elements; the pen compensation is wrong.\n"
            f"  before: {before}\n  after:  {after}")
    return data, edits


def _drawn_placements(data: bytes, obj_index: int, root: int, slot_values: dict | None):
    """Every NON-touch element the object places, as `(wtype, box, label)`.

    The invariant a hit-region insertion must preserve: splitting a region is supposed to change
    what is *touchable*, never what is *drawn*.
    """
    screen = layout.Screen(data, match_table_off=layout.MATCH_TABLE_136)
    placed = screen.walk(root, slot_values=slot_values or {})
    return [(q.wtype, q.x0, q.y0, q.x1, q.y1, q.label)
            for q in placed if q.obj_index == obj_index and q.wtype != 12]


def set_hold_delay(data: bytes, delay_ms: int = 3000) -> tuple[bytes, list]:
    """Change the protected-touch arm's threshold. Only 3000 ms is offered.

    The replacement is the firmware's own `addw r3,r3,#0xbb8`, read back out of the CLEAR EEPROM
    timer at `ARM_DELAY_SOURCE` before use, rather than an assembled immediate -- so a build
    whose own 3-second timer differs fails loudly instead of being handed a guess.
    """
    if delay_ms != 3000:
        raise ValueError(
            "only 3000 ms is supported: the replacement is the firmware's own "
            f"`addw r3,r3,#0xbb8` lifted from {ARM_DELAY_SOURCE:#x}, not an assembled immediate")
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    out = bytearray(data)
    lifted = bytes(out[section.offset + (ARM_DELAY_SOURCE - base):][:4])
    if lifted != ARM_DELAY_3000:
        raise ValueError(
            f"the 3000 ms encoding at {ARM_DELAY_SOURCE:#x} is {lifted.hex()}, "
            f"expected {ARM_DELAY_3000.hex()}")
    off = section.offset + (ARM_DELAY_SITE - base)
    got = bytes(out[off:off + 4])
    if got != ARM_DELAY_OLD:
        raise ValueError(
            f"arm delay site: expected {ARM_DELAY_OLD.hex()} at {ARM_DELAY_SITE:#x}, "
            f"found {got.hex()}")
    out[off:off + 4] = ARM_DELAY_3000
    return bytes(out), [(off, ARM_DELAY_OLD, ARM_DELAY_3000,
                         f"protected-touch arm delay 1000 -> {delay_ms} ms")]


def wire_hold_to_object(data: bytes, *, touch_id: int, obj_index: int,
                        delay_ms: int = 3000) -> tuple[bytes, list]:
    """Point the protected-touch consumer at `touch_id` -> the screen showing `obj_index`.

    **Take the object, not the screen id.** `state+0x20A` ids are a match-table lookup, not
    `object - 4`; that arithmetic put SERVICE MODE one screen off and aimed a build at GO POWER!
    CONFIGURATION when it meant the PD Inverter page (owner caught it on the coach, 2026-08-23).
    An object index is checkable -- you can render it -- so it is the safe thing to pass, and
    `layout.screen_for_object` does the lookup.
    """
    screen = layout.screen_for_object(data, obj_index)
    return wire_hold_to_screen(data, touch_id=touch_id, screen=screen, delay_ms=delay_ms)


def wire_hold_to_screen(data: bytes, *, touch_id: int, screen: int,
                        delay_ms: int = 3000) -> tuple[bytes, list]:
    """Point the protected-touch consumer at `touch_id` -> `screen`, at `delay_ms`.

    Prefer `wire_hold_to_object`: a raw screen id is a table lookup that cannot be checked by
    inspection.

    Repurposes the `0x838` branch (see the module docstring for why that one). Same-size, and
    every site asserts its expected old bytes first, so this refuses on a build whose GuiCode
    already differs rather than corrupting it.
    """
    from . import theme                       # local: keeps the import graph shallow

    if delay_ms != 3000:
        raise ValueError(
            "only 3000 ms is supported: the replacement is the firmware's own "
            f"`addw r3,r3,#0xbb8` lifted from {ARM_DELAY_SOURCE:#x}, not an assembled immediate")
    if not 0 <= screen <= 0xFF:
        raise ValueError(f"screen id {screen} does not fit the byte store at state+0x20a")

    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    out = bytearray(data)
    edits: list[tuple[int, bytes, bytes, str]] = []

    def poke(vaddr: int, old: bytes, new: bytes, why: str) -> None:
        if len(old) != len(new):
            raise ValueError(f"not same-size: {why}")
        off = section.offset + (vaddr - base)
        got = bytes(out[off:off + len(old)])
        if got != old:
            raise ValueError(f"{why}: expected {old.hex()} at {vaddr:#x}, found {got.hex()}")
        out[off:off + len(new)] = new
        edits.append((off, old, new, why))

    # The 3000 ms encoding is not trusted from this module -- it is read back out of the image
    # at the site it was lifted from, so a build whose CLEAR EEPROM timer differs fails loudly.
    lifted = bytes(out[section.offset + (ARM_DELAY_SOURCE - base):][:4])
    if lifted != ARM_DELAY_3000:
        raise ValueError(
            f"the 3000 ms encoding at {ARM_DELAY_SOURCE:#x} is {lifted.hex()}, "
            f"expected {ARM_DELAY_3000.hex()}")

    poke(ARM_DELAY_SITE, ARM_DELAY_OLD, ARM_DELAY_3000,
         f"protected-touch arm delay 1000 -> {delay_ms} ms")
    poke(CONSUMER_ID_SITE, CONSUMER_ID_OLD, encode_movw(2, touch_id),
         f"deadline consumer id 0x838 -> {touch_id:#06x} "
         "(costs the SETTINGS 5 s shortcut to AUTO ENGINE START ENABLE)")
    poke(CONSUMER_SCREEN_SITE, CONSUMER_SCREEN_OLD, encode_movs(3, screen),
         f"deadline consumer screen 17 -> {screen} (object {screen + 4})")
    return bytes(out), edits


# ===========================================================================================
# The dispatch stub: a THIRD hold destination, without giving one up
# ===========================================================================================
#
# The expired-deadline consumer is a two-branch chain with no room for a third, and GuiCode
# cannot grow (no instruction relocator -- every branch offset and literal-pool reference is
# position-dependent). Both facts are real; the conclusion "so one shortcut must be sacrificed"
# was not. GuiCode cannot grow, but it does not have to: removing the option interlocks left
# four 36-byte runs of NOPs, and a dispatch stub fits in one.
#
#     0x24CD24  b.w 0x24CD48        step over the stub -- the Progressive Inverter handler
#                                   still falls straight through to its own tail, unchanged
#     0x24CD28  <stub>              entered only by the branch installed at 0x24E90E
#
# The stub replaces the consumer's `0x838` arm (14 bytes: movw/cmp/bne/movs/strb.w) with a
# `b.w`, and re-implements that arm plus as many more as asked for. The `0x7E8` arm above it is
# never touched.
#
# This is the project's first authored machine code: `thumb.py` encodes it and every
# instruction is disassembled back and compared against what it claims to be.

CONSUMER_ARM_SITE = 0x0024E90E       # the 0x838 arm: movw/cmp/bne/movs/strb.w
CONSUMER_ARM_LEN = 14
CONSUMER_REJOIN = 0x0024E91C         # where the chain continues after the arms
SCREEN_FIELD = 0x20A                 # GuiCodeState+0x20A, the screen id (a BYTE)

# The interlock NOP runs, as (start, length). Only usable on a build that removed the
# interlocks; `install_hold_dispatch` asserts the run really is all NOPs before using it.
SCRATCH_RUNS = ((0x0024CD24, 36), (0x0024D062, 36), (0x0024D15A, 36), (0x0024BFCC, 36))


def _cs_imm(v: int) -> str:
    """Format an immediate the way capstone prints it: bare decimal below 10, else hex.

    Only needed because `assert_disassembles_to` compares rendered text, which is the whole
    point -- it forces the expected listing to be written the way an independent disassembler
    would render it, not the way the encoder happens to think about it.
    """
    return str(v) if v < 10 else f"{v:#x}"


def stub_size(n_entries: int) -> int:
    """Bytes a dispatch stub with `n_entries` will occupy."""
    if n_entries < 1:
        raise ValueError("no entries")
    return 12 * (n_entries - 1) + 10 + 8


def build_dispatch_stub(at: int, entries, rejoin: int) -> tuple[bytes, list]:
    """`(code, listing)` for `if id == e0: screen = s0; elif ... ; <rejoin>`.

    Assumes the register state that holds at `CONSUMER_ARM_SITE`: `r3` is the current touch id
    and `r4` is GuiCodeState. Both are live there -- `r3` from the `ldrh.w r3,[r4,#0x208]` four
    instructions earlier, `r4` throughout the dispatcher -- which is why the stub can be entered
    from that site and nowhere else.
    """
    if not entries:
        raise ValueError("no entries")
    for tid, screen in entries:
        if not 0 <= screen <= 0xFF:
            raise ValueError(f"screen {screen} does not fit the byte at state+{SCREEN_FIELD:#x}")

    # Pass 1: sizes are fixed per entry, so the labels are arithmetic, not a fixpoint.
    per = [12] * (len(entries) - 1) + [10]
    starts, pos = [], at
    for n in per:
        starts.append(pos)
        pos += n
    write_at, out_at = pos, pos + 4

    code, listing = bytearray(), []
    for i, (tid, screen) in enumerate(entries):
        here = starts[i]
        nxt = starts[i + 1] if i + 1 < len(entries) else out_at
        code += thumb.movw(2, tid);                 listing.append(f"movw r2, #{_cs_imm(tid)}")
        code += thumb.cmp_reg(3, 2);                listing.append("cmp r3, r2")
        code += thumb.bcc_n(here + 6, nxt, "ne");   listing.append(f"bne #{_cs_imm(nxt)}")
        code += thumb.movs_imm(3, screen);          listing.append(f"movs r3, #{_cs_imm(screen)}")
        if i + 1 < len(entries):
            code += thumb.b_n(here + 10, write_at); listing.append(f"b #{_cs_imm(write_at)}")
    code += thumb.strb_imm12(3, 4, SCREEN_FIELD)
    listing.append(f"strb.w r3, [r4, #{_cs_imm(SCREEN_FIELD)}]")
    code += thumb.b_w(out_at, rejoin);              listing.append(f"b.w #{_cs_imm(rejoin)}")
    thumb.assert_disassembles_to(bytes(code), at, listing)
    return bytes(code), listing


def install_hold_dispatch(data: bytes, entries, *, scratch=None) -> tuple[bytes, list]:
    """Put a multi-way hold dispatch in dead space and point the consumer at it.

    `entries` is `[(touch_id, screen), ...]`. Include the arm being replaced -- `(0x838, 17)`
    on 13.6 -- or its shortcut really is lost; nothing here re-adds it for you.

    Refuses unless the scratch run is entirely NOPs of the expected length, so it can only ever
    be used on a build that actually freed that space.
    """
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    out = bytearray(data)
    edits: list[tuple[int, bytes, bytes, str]] = []

    def off(vaddr: int) -> int:
        return section.offset + (vaddr - base)

    runs = SCRATCH_RUNS if scratch is None else (scratch,)
    filler = (thumb.NOP4 * 3 + thumb.NOP2 * 3) * 2
    run_at = run_len = None
    for start, length in runs:
        if bytes(out[off(start):off(start) + length]) == filler[:length]:
            run_at, run_len = start, length
            break
    if run_at is None:
        raise ValueError(
            "no scratch run of NOPs found; this needs a build with the option interlocks removed")

    stub_at = run_at + 4
    need = stub_size(len(entries))
    if 4 + need > run_len:
        raise ValueError(
            f"stub needs {need} bytes, only {run_len - 4} available at {run_at:#x}")
    stub, listing = build_dispatch_stub(stub_at, entries, CONSUMER_REJOIN)

    # 1. step over the stub so the host handler's own flow is unchanged
    skip = thumb.b_w(run_at, run_at + run_len)
    thumb.assert_disassembles_to(skip, run_at, [f"b.w #{_cs_imm(run_at + run_len)}"])
    payload = bytearray(skip + stub)
    payload += thumb.NOP2 * ((run_len - len(payload)) // 2)
    old = bytes(out[off(run_at):off(run_at) + run_len])
    out[off(run_at):off(run_at) + run_len] = payload
    edits.append((off(run_at), old, bytes(payload),
                  f"hold dispatch stub in dead space at {stub_at:#x} "
                  f"({', '.join('%#x->screen %d' % e for e in entries)})"))

    # 2. repoint the consumer's arm at it
    call = thumb.b_w(CONSUMER_ARM_SITE, stub_at)
    thumb.assert_disassembles_to(call, CONSUMER_ARM_SITE, [f"b.w #{_cs_imm(stub_at)}"])
    arm = bytearray(call) + thumb.NOP2 * ((CONSUMER_ARM_LEN - len(call)) // 2)
    old = bytes(out[off(CONSUMER_ARM_SITE):off(CONSUMER_ARM_SITE) + CONSUMER_ARM_LEN])
    if old[:4] != CONSUMER_ID_OLD:
        raise ValueError(f"consumer arm at {CONSUMER_ARM_SITE:#x} is not stock: {old.hex()}")
    out[off(CONSUMER_ARM_SITE):off(CONSUMER_ARM_SITE) + CONSUMER_ARM_LEN] = arm
    edits.append((off(CONSUMER_ARM_SITE), old, bytes(arm),
                  f"deadline consumer arm -> b.w {stub_at:#x} (the dispatch stub)"))
    return bytes(out), edits


def hold_dispatch_map(data: bytes) -> dict:
    """`{touch id: screen}` the expired-deadline path resolves, read back from the image.

    Walks the consumer's own `0x7E8` arm and then follows the stub, so it reports what the
    finished bytes do rather than what the build meant to write.
    """
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    code = bytes(data[section.offset:section.offset + section.length])
    out: dict[int, int] = {}

    # the untouched first arm: cmp.w r3,#0x7e8 / beq.w <target: movs r3,#N ; strb>
    head = thumb.disassemble(code[0x24E906 - base:0x24E90E - base], 0x24E906)
    m = re.match(r"cmp\.w r3, #(0x[0-9a-f]+)", head[0][1])
    if m and head[1][1].startswith("beq"):
        tgt = int(head[1][1].split("#")[1], 0)
        arm = thumb.disassemble(code[tgt - base:tgt - base + 6], tgt)
        s = re.match(r"movs r3, #(0x[0-9a-f]+)", arm[0][1])
        if s:
            out[int(m.group(1), 0)] = int(s.group(1), 0)

    # the second arm: either a stock inline arm or a b.w into the stub
    second = thumb.disassemble(code[CONSUMER_ARM_SITE - base:CONSUMER_ARM_SITE - base + 14],
                               CONSUMER_ARM_SITE)
    if second[0][1].startswith("b.w"):
        at = int(second[0][1].split("#")[1], 0)
        body = thumb.disassemble(code[at - base:at - base + 64], at)
        tid = None
        for _a, text in body:
            mm = re.match(r"movw r2, #(0x[0-9a-f]+)", text)
            ms = re.match(r"movs r3, #(0x[0-9a-f]+)", text)
            if mm:
                tid = int(mm.group(1), 0)
            elif ms and tid is not None:
                out[tid] = int(ms.group(1), 0)
                tid = None
    else:
        mm = re.match(r"movw r2, #(0x[0-9a-f]+)", second[0][1])
        ms = next((t for _a, t in second if t.startswith("movs r3")), None)
        if mm and ms:
            out[int(mm.group(1), 0)] = int(ms.split("#")[1], 0)
    return out
