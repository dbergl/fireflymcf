"""Reveal the nav rail's SIXTH icon, and point it somewhere useful -- 2026-09-03.

Firefly authored a six-icon left nav rail and then left it unreachable. `options`' nav-rail note
(`GUICODE.md` Finding 47) records the switch: object **974** carries a wt-6 on slot **453** with
`child_base` 385 and `count` 4, candidates matching

    385  0xFFFF (default)  rail + P D Q R L     five icons
    386  0                 rail + P   Q R L     four icons, no Electrical
    387  2  }              a further wt-6 on slot 185 -> 389 / 390
    388  4  }
      389  0               rail + P   Q R S L   five icons, no Electrical, PLUS 'S'
      390  1               rail + P D Q R S L   SIX icons

and the panel only ever writes 453 = 0 or 1 (two stores, the complete writer set,
`proven-from-code`). 1 matches no candidate, so it falls through to the 385 default and the `S`
icon has never been drawn on this coach.

**The edit is therefore two bytes and no new content**: repoint the switch's `child_base` from
385 to **389**. The scan then reads 389 (mv 0) and 390 (mv 1) first, so

    453 = 0  (nothing electrical fitted) -> 389, five icons + S
    453 = 1  (AGS / solar / inverter)    -> 390, SIX icons + S

which mirrors stock's own no-Electrical/Electrical pair exactly, adding the sixth icon to both.
`count` is left at 4: the scan is first-match-wins and 389/390 are scanned first, so candidates
391 (`0xFFFF`) and 392 (`1`) are unreachable for any value 453 can hold. Nothing is lost -- 390 is
a strict superset of 385 -- and this depends on **no** never-established slot: 453 has two writers
and slot 185 is never consulted on the new path.

**Where the icon goes.** Its region carries ACTION id `0x7DC`, whose dispatcher leaf sets screen
**12** -- `SLIDES/AWNINGS`, the awning/slide operation page (`EXT`/`RET`, `UP`/`DWN`,
`OPEN`/`CLOSE`, `Raise Bed`, `Ignition Lock-Out`). There is a *second* page also titled
`SLIDES/AWNINGS`, screen **29**, which is the auto-levelling page (`Auto Level`, `All Retract`,
`Cancel`, `EXCESSIVE SLOPE`, `ENGAGE PARK BRAKE`, `FRONT`/`LEFT`/`REAR`/`Right`) and carries the
slot 174/175 gates `bootlottery` flags. Screen 29's only dispatcher leaf is id `0x7ED`, which
**no wt-12 region in the image carries**, so it is unreachable by construction -- which is what
the owner reports. `retarget_nav_icon` repoints the `0x7DC` leaf, the same one-instruction shape
as `aescompact.retarget_inverter_gear`.

Both edits are same-size and guarded on the stock bytes; neither reseals (the caller does).
"""
from __future__ import annotations

import struct

from . import guidata, options, records, theme, thumb

NAV_RAIL_SWITCH_OBJECT = 974
NAV_RAIL_SLOT = 453
NAV_RAIL_STOCK_BASE = 385          # the five-icon default arm
NAV_RAIL_STOCK_COUNT = 4
NAV_RAIL_SIX_ICON_BASE = 389       # 389 = five + S, 390 = SIX (Electrical + S)

SLIDES_TOUCH_ID = 0x7DC            # the sixth icon's ACTION id
SLIDES_LEAF_SITE = 0x0024C702      # `movs r3,#0xc` in the 0x7DC action body
SLIDES_LEAF_STOCK = bytes.fromhex("0c23")     # movs r3,#0xc -> screen 12
SLIDES_OPERATION_SCREEN = 12       # awning / slide controls
SLIDES_LEVELLING_SCREEN = 29       # Auto Level / All Retract -- bootlottery slots 174/175


def _nav_switch_record(data: bytes):
    """Object 974's nav-rail switch record, located by slot rather than by position."""
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    found = [r for r in options._own_records(data, gs, ge, table, NAV_RAIL_SWITCH_OBJECT)
             if r.wtype in (6, 0x2E) and r.value_slot == NAV_RAIL_SLOT]
    if len(found) != 1:
        raise ValueError(
            f"object {NAV_RAIL_SWITCH_OBJECT} carries {len(found)} switches on slot "
            f"{NAV_RAIL_SLOT}, expected exactly 1; this build is not the one the route was "
            "derived against")
    return found[0]


def show_extra_nav_icon(data: bytes, base: int = NAV_RAIL_SIX_ICON_BASE) -> tuple:
    """Repoint the nav-rail switch at the arms that include the sixth icon. Same-size, 2 bytes."""
    rec = _nav_switch_record(data)
    if rec.child_index != NAV_RAIL_STOCK_BASE or rec.count != NAV_RAIL_STOCK_COUNT:
        raise ValueError(
            f"nav rail switch is child_base {rec.child_index} count {rec.count}, expected stock "
            f"{NAV_RAIL_STOCK_BASE}/{NAV_RAIL_STOCK_COUNT} (already applied, or a drifted build)")
    off = records.child_index_offset(data, rec)      # round-trip guarded by records.py
    got = bytes(data[off:off + 2])
    new = struct.pack("<H", base)
    out = bytearray(data)
    out[off:off + 2] = new
    edits = [(off, got, bytes(new),
              f"nav rail switch (object {NAV_RAIL_SWITCH_OBJECT}, slot {NAV_RAIL_SLOT}) child_base "
              f"{NAV_RAIL_STOCK_BASE} -> {base}: 453=0 draws {base} (five icons + S), 453=1 draws "
              f"{base + 1} (six icons). Adds the 'S' icon; loses nothing")]
    return bytes(out), edits


def retarget_nav_icon(data: bytes, screen_id: int = SLIDES_LEVELLING_SCREEN) -> tuple:
    """Repoint the `0x7DC` action leaf from screen 12 to `screen_id`. Same-size, guarded, 2 bytes."""
    if not (0 <= screen_id <= 0xFF):
        raise ValueError(f"screen id {screen_id} must fit `movs r3,#imm8` (0..255)")
    section = theme.guicode_section(data)
    off = section.offset + (SLIDES_LEAF_SITE - theme.guicode_base(section))
    got = bytes(data[off:off + 2])
    if got != SLIDES_LEAF_STOCK:
        raise ValueError(
            f"0x7dc action leaf screen constant: expected {SLIDES_LEAF_STOCK.hex()} "
            f"(movs r3,#0xc, screen {SLIDES_OPERATION_SCREEN}) at {SLIDES_LEAF_SITE:#x}, "
            f"found {got.hex()}")
    new = thumb.movs_imm(3, screen_id)
    thumb.assert_disassembles_to(new, SLIDES_LEAF_SITE, [f"movs r3, #{screen_id:#x}"])
    out = bytearray(data)
    out[off:off + 2] = new
    edits = [(off, got, bytes(new),
              f"0x7dc action leaf (nav rail 'S' icon) screen {SLIDES_OPERATION_SCREEN} "
              f"(slide/awning controls) -> {screen_id}")]
    return bytes(out), edits


def retarget_action_leaf(data: bytes, touch_id: int, from_screen: int, to_screen: int) -> tuple:
    """Repoint ACTION leaf `touch_id`'s screen store from `from_screen` to `to_screen`.

    The leaf's `movs r3,#imm` is located through `navmap.routes` (the dispatcher's own compare
    tree), not a pinned address, and must hold exactly `movs r3,#from_screen`; same-size, 2
    bytes. `retarget_nav_icon` is this edit for `0x7DC` at its stock site; the LEVEL SETUP
    popup uses it for `0x7ED` (29 -> 12), a leaf no stock region carries."""
    from . import navmap
    if not (0 <= to_screen <= 0xFF):
        raise ValueError(f"screen id {to_screen} must fit `movs r3,#imm8` (0..255)")
    hits = [r for r in navmap.routes(data) if r.touch_id == touch_id]
    if len(hits) != 1:
        raise ValueError(f"ACTION id {touch_id:#x} has {len(hits)} leaves, expected exactly 1")
    leaf = hits[0]
    sharers = [hex(r.touch_id) for r in navmap.routes(data)
               if r.store_at == leaf.store_at and r.touch_id != touch_id]
    if sharers:
        raise ValueError(f"ACTION id {touch_id:#x}'s store at {leaf.store_at:#x} is shared with "
                         f"{sharers}; retargeting it would move them too")
    if leaf.screen != from_screen:
        raise ValueError(f"ACTION id {touch_id:#x} goes to screen {leaf.screen}, "
                         f"expected {from_screen}")
    section = theme.guicode_section(data)
    off = section.offset + (leaf.store_at - theme.guicode_base(section))
    want = thumb.movs_imm(3, from_screen)
    got = bytes(data[off:off + 2])
    if got != want:
        raise ValueError(f"ACTION id {touch_id:#x}'s store at {leaf.store_at:#x} is {got.hex()}, "
                         f"not movs r3,#{from_screen:#x} ({want.hex()})")
    new = thumb.movs_imm(3, to_screen)
    thumb.assert_disassembles_to(new, leaf.store_at, [f"movs r3, #{to_screen:#x}"])
    out = bytearray(data)
    out[off:off + 2] = new
    return bytes(out), [(off, got, bytes(new),
                         f"ACTION {touch_id:#x} leaf screen {from_screen} -> {to_screen}")]
