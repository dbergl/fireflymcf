"""Option -> gated widget: the `hides_control` and `channel` joins, recovered 2026-09-02.

**There is no visibility flag and no stored channel.** An OPTIONS row "hides its control"
because the row's value slot is the key of an ordinary type-6 value-match switch somewhere
in the object tree (GUICODE Finding 16, `layout.Screen._switch_child`), whose arm for the
option's ON value draws a control and whose arm for 0 draws nothing. The same switch is
what "rebinds" the Bath control between channels 25 and 26: there are TWO switches on the
LIGHTS page, one keyed on `Cargo / Bath Light` (0xE3, slot 110) revealing object 370 (touch
id 25 -> group 25 -> channel 25) and one keyed on `Bath Light` (0xE4, slot 111) revealing
object 374 (touch id 26 -> group 26 -> channel 26). Each option independently shows its own
control; the tap handlers' mutual exclusion (Finding 50, PC.33) is what makes exactly one of
the two visible at a time and so makes it *look* like one control being rebound. With both
off -- a reachable state, PC.33 -- neither switch draws, which is exactly "the Bath control
is bound to no channel at all".

Both joins are therefore the same mechanism, read from GuiData alone:

    OPTIONS row  -> value slot           options._iter_option_rows (the row's own switch)
    value slot   -> gating switch(es)    every wt-6/0x2e record anywhere whose value_slot
                                         matches; arms resolved by the firmware's own scan
                                         (`layout.Screen._switch_child`, proven-from-code)
    ON arm       -> control              a wt-12 touch record in the arm's subtree
    touch id     -> group -> channel     guidata.group_bindings (the proven control join)

13.6 (`tests/test_option_gates.py` pins these): the per-floorplan LIGHTS pages, objects
940-949 under the LIGHTS root (object 6, floorplan switch on slot 5), each carry three
switches -- slot 108 (Bunk Accent) base 375 {0xffff: 375 empty, 1: 376 'Bunk Accent' ->
group 34}, slot 110 base 369 {0: 369, 1: 370 'Bath' -> group 25} (943/944 use base 371 ->
372 'Cargo', same group 25), slot 111 base 373 {0: 373, 1: 374 'Bath' -> group 26} (943 uses
base 618, count 3 -> 619). Black Tank (0xEC, slot 112) gates the HOME tank cluster: objects
912-921 base 801 {0: 801 (a '.' at x=-20), 1: 802 'BLACK' + gauge child 719}. 14.7 has the
same shape on its own indices (LIGHTS pages 794-799; Cargo -> 328/330 -> channel 25,
Starlink Power -> 240 -> channel 17, Bunk Accent -> 334 -> group 34, Black Tank -> 738).

**What "empty" means here, so it is not a fit to the placeholder text.** An arm is empty
when its subtree holds no wt-12 touch region and no wt-5 container child. The firmware's
empty arms are a single wt-0 record at negative x (objects 369/373/375/618/620: x = -95 or
-14), a bare wt-33 box (246), or a wt-11 label '.' (314/351/1420); none of those carries a
control or a child, so the structural test needs no knowledge of the '.' convention.

**`channel` is filled only when it is unique.** The union of every DC_DIMMER_COMMAND_2
instance the revealed controls' groups command must be a single channel. Group 25 and
group 26 are single-channel, so 0xE3 -> 25 and 0xE4 -> 26. Bunk Accent's group 34 carries
variant-2 entries (byte-2 high nibble 2, `CONTROL-BEHAVIOR.md` section 5) on channel 23 beside
its channel-34 entries, so its channel set is {23, 34} and `channel` stays None -- the set is
exposed on the gate, the field is not guessed. Black Tank reveals a gauge, not a control:
`hides_control` is True (a wt-5 child appears) and `channel` is None.

**A second, independent derivation exists in GuiCode** for the two rows whose tap handlers
emit a companion OFF: `config.option_off_channel` reads the `0x1FEDB` command-3 template
beside the selector's `:= 0` write (GUICODE Finding 23, observed on the wire in PC.33).
`tests/test_option_gates.py` asserts the two routes agree wherever both exist. The GuiData
route is the one `guidata.find_option_bindings` ships, because it reaches every gated row
(Bunk Accent and Black Tank emit no OFF -- PC.34) and needs no disassembler.

**Reported honestly, not filtered to the answer key:** every boolean row on the Main OPTIONS
tab is gate-shaped except Heat Pump, whose arms (872/873/874) all carry touch regions and
differ only in which mode buttons they add -- the "reveal" direction PC.34 photographed.
`answer_key.OPTION_HIDES_CONTROL` names 0xE5 and 0xEC because those are the two the owner
toggled in PC.9; Bath Fan (0xE9 -> Climate object 247, Finding 16's worked example) and the
Bath/Cargo pair (0xE3/0xE4) are gate-shaped too, and this module says so.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import guidata, layout, memo, records


@dataclass(frozen=True)
class OptionGate:
    """One type-6 switch keyed on an OPTIONS row's value slot, with both arms resolved."""

    switch_obj: int             # object whose record list holds the switch
    record_offset: int          # file offset of the wt-6 record
    value_slot: int
    base: int                   # first candidate object index
    count: int                  # candidates scanned, [base, base+count)
    on_value: int               # the slot value that means "option selected"
    arm_off: int | None         # candidate drawn when the slot reads 0; None = nothing
    arm_on: int | None          # candidate drawn when the slot reads `on_value`
    off_touch: frozenset        # wt-12 touch ids in arm_off's subtree
    on_touch: frozenset         # wt-12 touch ids in arm_on's subtree
    off_children: frozenset     # wt-5 container children in arm_off's subtree
    on_children: frozenset      # wt-5 container children in arm_on's subtree
    channels: frozenset         # DC_DIMMER_COMMAND_2 instances the ON arm's groups command
                                # that the OFF arm's do not

    @property
    def reveals(self) -> bool:
        """The OFF arm draws no control and no container; the ON arm draws at least one.

        This is the shape `answer_key.OPTION_HIDES_CONTROL` describes: turning the option
        off makes the widget disappear, not change."""
        return (not self.off_touch and not self.off_children
                and bool(self.on_touch or self.on_children))


def _subtree(data: bytes, gs: int, ge: int, table: list,
             index: int | None) -> tuple[frozenset, frozenset]:
    """`(touch ids, wt-5 children)` reachable from object `index` -- through wt-5 children
    unconditionally and through EVERY candidate of nested switches (any of them may draw,
    depending on other slots). `(∅, ∅)` for None or an out-of-range index."""
    from . import options

    touch: set[int] = set()
    children: set[int] = set()
    seen: set[int] = set()
    stack = [] if index is None else [index]
    while stack:
        i = stack.pop()
        if i in seen or not (0 <= i < len(table)):
            continue
        seen.add(i)
        for rec in options._own_records(data, gs, ge, table, i):
            if rec.wtype == 12:
                touch.add(_touch_id(data, rec))
            elif rec.wtype == 5 and rec.child_index is not None:
                children.add(rec.child_index)
                stack.append(rec.child_index)
            elif rec.wtype in (6, 0x2E) and rec.child_index is not None:
                stack.extend(rec.child_index + k for k in range(rec.count or 0))
    return frozenset(touch), frozenset(children)


def _touch_id(data: bytes, rec: "records.WRecord") -> int:
    """The trailing u16 of a wt-12 record: the touch id the dispatcher matches, which for a
    control is a group index (`guidata.find_control_groups`, `GROUP_REFERENCE_TAIL`)."""
    import struct
    return struct.unpack_from("<H", data, rec.end - guidata.GROUP_REFERENCE_TAIL)[0]


def _group_channels(groups, bindings, touch_ids) -> frozenset:
    out: set[int] = set()
    for tid in touch_ids:
        if tid < len(groups):
            out |= {b.channel for b in guidata.group_bindings(groups, bindings, tid)
                    if b.channel is not None}
    return frozenset(out)


@memo.content_cached
def _all_gates(data: bytes) -> tuple:
    """Every wt-6/0x2e switch record in every object, as `(obj, offset, slot, base, count)`.
    One pass over the object table, shared by every slot query."""
    from . import options

    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    out = []
    for i in range(len(table)):
        for rec in options._own_records(data, gs, ge, table, i):
            if (rec.wtype in (6, 0x2E) and rec.value_slot is not None
                    and rec.child_index is not None and rec.count):
                out.append((i, rec.offset, rec.value_slot, rec.child_index, rec.count))
    return tuple(out)


def option_gates(data: bytes, value_slot: int, on_value: int = 1) -> list[OptionGate]:
    """Every switch keyed on `value_slot`, with the arms it resolves for 0 and `on_value`.

    Arm resolution is the firmware's own scan (`layout.Screen._switch_child`: exactly `count`
    candidates, first match wins, base only if it is an explicit 0xffff default, else
    nothing). Returns `[]` when no match table is located -- an annotation, never a guess."""
    scr = layout.Screen(data)
    if scr.match_table_off is None:
        return []
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    groups = guidata.group_table(data)
    bindings = guidata.binding_table(data)
    out = []
    for obj, offset, slot, base, count in _all_gates(data):
        if slot != value_slot:
            continue
        arm_off = scr._switch_child(base, 0, count)
        arm_on = scr._switch_child(base, on_value, count)
        off_touch, off_children = _subtree(data, gs, ge, table, arm_off)
        on_touch, on_children = _subtree(data, gs, ge, table, arm_on)
        channels = (_group_channels(groups, bindings, on_touch)
                    - _group_channels(groups, bindings, off_touch))
        out.append(OptionGate(switch_obj=obj, record_offset=offset, value_slot=slot,
                              base=base, count=count, on_value=on_value,
                              arm_off=arm_off, arm_on=arm_on,
                              off_touch=off_touch, on_touch=on_touch,
                              off_children=off_children, on_children=on_children,
                              channels=channels))
    return out


def hides_control(data: bytes, value_slot: int, on_value: int = 1) -> bool:
    """True when some switch keyed on the slot draws a control/container at `on_value` and
    nothing at 0 (`OptionGate.reveals`). See the module docstring for which rows qualify."""
    return any(g.reveals for g in option_gates(data, value_slot, on_value))


def gated_channels(data: bytes, value_slot: int, on_value: int = 1) -> frozenset:
    """Every DC_DIMMER_COMMAND_2 instance commanded by a control that appears only when the
    option is on, across all its gates."""
    out: set[int] = set()
    for g in option_gates(data, value_slot, on_value):
        if g.reveals:
            out |= g.channels
    return frozenset(out)


def gated_channel(data: bytes, value_slot: int, on_value: int = 1) -> int | None:
    """The one channel the option's revealed control commands, or None when there is none
    or more than one (Bunk Accent's {23, 34}) -- never a pick from an ambiguous set."""
    found = gated_channels(data, value_slot, on_value)
    return next(iter(found)) if len(found) == 1 else None
