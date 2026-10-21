"""enumlabel -- a value shown as text from a table: the arms, the switch and its placement.

`add_enum_label` draws one word per value. `statusfold` maps the received value to an index
(0..n-1 for the table's keys, n for stale or anything else) in a display cell every tick; this
module draws that cell as ONE wtype-6 switch with n+2 arms: the `0xFFFF` default at the band's
base (`blank_arm`, which draws no text), then the n+1 text arms matched on 0..n, the last drawing
the default text. Every value the routine writes is matched. A value it never writes (the cell
before the routine's first tick, inference: SRAM above `0x2000AD98` is not initialised,
`raminit.py`) meets no text arm, and `FUN_00256C54` then renders the base only because its match
is `0xFFFF` (`layout.Screen._switch_child`, GUICODE Finding 16): the blank arm, which wipes the
box when a `background` is given. The default-at-the-base shape is `cornermark`'s, the stock one.

**An arm** (`build_arm`) is flow-relative to the switch: every record has base modes `[1,1,1,1]`
(header byte 1 `0x55`) and geometry `(0, 0, w, h)`, so it is placed at the flow origin the
switch's own absolute pen commits (`FUN_00257BFC`: wtype 6 is in the flow gate) and its box is
`(x, y)-(x+w, y+h)` inclusive. It holds:

- optionally a wtype-33 fill (`pagedraw.capsule`'s donor, radius 1: stock draws wtype-33 with
  radius 1..10 and never 0) in `bg_index`, so a short word does not leave a longer word's pixels
  (the panel repaints only the selected arm);
- one wtype-11 label (`pagedraw.label`) with a declared `w` x `h` box, which the panel clips the
  text to (`GUICODE.md` Findings 69-70), ink `colour_index` and a transparent background.

Colours are mode-7 palette indices (`theme.resolve_fill_index`, resolved by the caller), written
over `pagedraw`'s mode-3 inline colour with `pagedraw._set_mode`; the ON pair (fillA2) equals the
OFF pair, as `pagedraw` writes every record.

**The blank arm** (`blank_arm`) is the optional fill and one empty flow-relative wtype-11
(`cornermark.build_empty_record()`, the empty arm stock's mark switches use): it sets no font and
no align, so the walk leaves it in the state it entered with.

**Placement** (`place`): the arms are appended as one band (`switchbuild.append_arms`), the
switch is authored absolute at `(x, y)` (`switchbuild.author_switch(at=...)`, which keeps the
donor's live-redraw enroll bit), and a one-record object holding it is attached under `parent`
by `insert.insert_object` (a byte clone of the parent's first wtype-5 attach, as `move_control`
pastes). The attach draws nothing; the switch is absolute, so the attach's own pen does not
place it.

**The band is laid out in descending match order** (`0xFFFF` at the base, then `n, n-1, ..., 0`). The renderer scans the
whole band for the value (`FUN_00256C54` at `0x00257B3E`), so order inside it does not change
what draws. It matters to `guidata.find_match_table`, which locates the table by counting
literal `0, 1, 2` runs: while `relocate.grow_match_table` has grown the table past the object
count, an ascending `0, 1, 2` among the new entries makes a window shifted past the table's start
outscore the real one, and the grow refuses itself ("the match table moved while being grown").
A descending band carries no such run.

**Persistent state.** The layout engine shares one state across a container's recursion
(`FUN_00258330` with the save flag clear; `layout.Screen.walk`): colour slots are restored after
a child, but the R38 font and the align byte are not. The arm's label writes both, so a record
the walk reaches after the label object, one that inherits its font, would draw in the label's
font. `entering_state` measures the `(font, align)` the walk carries to the attach point, and
`place(restore=...)` ends the object with an empty label (`restore_record`) that writes them back.
The flow origin and frame the object leaves are not restored; `foreign_deltas` is the whole-page
check that nothing after the label depends on them.

**Static text** (`add_text_label`) is the same label without the switch: `label_records(at=...)`
builds the fill and label absolute at `(x, y)` (pagedraw's own form), and `place_static`
attaches them by the same paste and ends them with the same restore record.
"""
from __future__ import annotations

import struct

from . import cornermark, guidata, insert, pagedraw as pd, pagewalk, records, switchbuild

ALIGNS = {"left": 1, "centre": 2, "right": 3}     # R49 h_align (`pagedraw.label`)
FILL_RADIUS = 1
_FLOW = 0x55                                       # header byte 1: base modes [1,1,1,1]
_PLACEHOLDER = (0xFF, 0xFF, 0xFF)                  # overwritten by the mode-7 index


class EnumLabelError(ValueError):
    """An arm, the switch or its placement could not be built."""


def _flow_relative(frag: bytes) -> bytes:
    out = bytearray(frag)
    out[1] = _FLOW
    return bytes(out)


def _index_colour(frag: bytes, roles: tuple, index: int) -> bytes:
    for role in roles:
        frag = pd._set_mode(frag, role, 7, struct.pack("<H", index))
    return frag


def _check(frag: bytes, wtype: int, w: int, h: int, colour: tuple,
           at: "tuple[int, int] | None" = None) -> records.WRecord:
    r = records.decode_record(frag, 0)
    modes, xy = ([1, 1, 1, 1], [0, 0]) if at is None else ([0, 0, 1, 1], list(at))
    if not (r.wtype == wtype and r.size == len(frag) and r.base_modes[:4] == modes
            and r.geom[:4] == xy + [w, h] and r.in_regs == [0] * 6 and r.out_regs == [0] * 6
            and colour in r.colors):
        raise EnumLabelError(f"authored wtype-{wtype} arm record failed its self-decode: "
                             f"{frag.hex()}")
    return r


def label_records(data: bytes, *, text: str, font: int, w: int, h: int, align: int,
                  colour_index: int, bg_index: int | None,
                  at: "tuple[int, int] | None" = None, multiline: bool = False) -> list[bytes]:
    """The records of one boxed label: an optional wtype-33 fill of `w` x `h` in `bg_index`,
    then a wtype-11 label `text` in a declared `w` x `h` box, R49 `align` (1 left, 2 centre,
    3 right) and vertically centred, ink `colour_index`. `at` None makes them flow-relative at
    `(0, 0)` (an arm, `build_arm`); `at=(x, y)` keeps `pagedraw`'s absolute form, the box
    `(x, y)-(x+w, y+h)` (`add_text_label`). `multiline` lets a `'\n'` through
    (`pagedraw.label`). Raises `EnumLabelError` when the text does not fit the box or a record
    fails its self-decode."""
    x, y = (0, 0) if at is None else at
    place = _flow_relative if at is None else bytes
    try:
        parts = []
        if bg_index is not None:
            fill = pd.capsule(data, x, y, w, h, FILL_RADIUS, _PLACEHOLDER)
            fill = _index_colour(place(fill), ("fillA", "fillA2"), bg_index)
            _check(fill, 33, w, h, ("fillA", 7, bg_index), at)
            parts.append(fill)
        lab = pd.label(data, text, x, y, w, h, font=font, rgb=_PLACEHOLDER, h_align=align,
                       v_align=2, multiline=multiline)
        lab = _index_colour(place(lab), ("fillA", "fillA2"), colour_index)
        r = _check(lab, 11, w, h, ("fillA", 7, colour_index), at)
        if not (r.label == text and r.font == font and r.h_align == align):
            raise EnumLabelError(f"authored label {text!r} failed its self-decode: {lab.hex()}")
        parts.append(lab)
        return parts
    except pd.PageDrawError as e:
        raise EnumLabelError(str(e)) from e


def build_arm(data: bytes, *, text: str, font: int, w: int, h: int, align: int,
              colour_index: int, bg_index: int | None) -> bytes:
    """One arm object (`<count>` + records, padded to 4): `label_records`' flow-relative fill
    and label. Raises `EnumLabelError` when the text does not fit the box or a record fails its
    self-decode."""
    parts = label_records(data, text=text, font=font, w=w, h=h, align=align,
                          colour_index=colour_index, bg_index=bg_index)
    try:
        return cornermark._object(parts)
    except cornermark.CornerMarkError as e:
        raise EnumLabelError(str(e)) from e


def blank_arm(data: bytes, *, w: int, h: int, bg_index: int | None) -> bytes:
    """The `0xFFFF` default arm (`<count>` + records, padded to 4): the `w` x `h` fill in
    `bg_index` when one is given, then an empty flow-relative wtype-11
    (`cornermark.build_empty_record()`), which draws nothing and sets no font or align."""
    try:
        parts = []
        if bg_index is not None:
            fill = pd.capsule(data, 0, 0, w, h, FILL_RADIUS, _PLACEHOLDER)
            fill = _index_colour(_flow_relative(fill), ("fillA", "fillA2"), bg_index)
            _check(fill, 33, w, h, ("fillA", 7, bg_index))
            parts.append(fill)
        parts.append(cornermark.build_empty_record())
        return cornermark._object(parts)
    except (pd.PageDrawError, cornermark.CornerMarkError) as e:
        raise EnumLabelError(str(e)) from e


def restore_record(font: int | None, align: int) -> bytes:
    """An empty wtype-11 label (`cornermark.build_empty_record(0, 0)`: draws nothing, leaves the
    flow origin where the arm left it) that writes the persistent font `font` (None: leave it)
    and header align `align` (0: leave it; the decoder never stores a 0 align, `FUN_00252CB0`
    0x2531FE)."""
    frag = cornermark.build_empty_record(0, 0)
    if font is not None:
        rec = records.decode_record(frag, 0)
        buf = bytearray(frag)
        buf[records.font_offset(frag, rec)] = pd._check_font(font)
        frag = bytes(buf)
    if align:
        frag = pd._set_align(frag, align)
    r = records.decode_record(frag, 0)
    if not (r.wtype == 11 and not r.label and r.size == len(frag) and r.font == font
            and r.align == align and r.base_modes[:4] == [1, 1, 1, 1]):
        raise EnumLabelError(f"authored restore record failed its self-decode: {frag.hex()}")
    return frag


def entering_state(data: bytes, parent: int, attach_index: int) -> set:
    """The `(font, align)` persistent state each walk carries INTO record `attach_index` of
    `parent` -- what the walk would carry past that point had nothing been attached there.

    Walks every page root that reaches `parent` (`pagewalk.reaching_roots`) at its base arms;
    only when no base walk passes the record are the switch-arm sets
    (`pagewalk.switch_arm_sets`) walked. The state is the one after the record walked just
    before it (`layout.Screen.walk`'s trace carries the state after each record). Empty when no
    root reaches `parent`."""
    roots = pagewalk.reaching_roots(data, parent)
    found: set = set()
    for sets in ([{}], pagewalk.switch_arm_sets(data, roots)):
        for root in roots:
            for values in sets:
                walk = pagewalk.walk_records(data, root, switch_values=values or None)
                for i, r in enumerate(walk):
                    if r.obj == parent and r.index == attach_index and i:
                        found.add((walk[i - 1].font, walk[i - 1].align))
        if found:
            break
    return found


def place(data: bytes, *, parent: int, x: int, y: int, arms: list[bytes], default: bytes,
          value_slot: int, restore: "tuple[int | None, int] | None" = None
          ) -> tuple[bytes, list, int]:
    """`(image, edits, object_index)`: `default` (`blank_arm`) appended at the band's base
    matched on `0xFFFF`, then `arms[i]` matched on `i` (in descending match order), a
    `len(arms) + 1`-arm switch on display cell `value_slot` at absolute `(x, y)`, held in a new
    object attached under `parent`. `edits` are `insert.insert_object`'s (attach step).
    `restore=(font, align)` follows the switch with `restore_record(font, align)`, so the arm's
    label does not hand its font and align to whatever the walk reaches after the object."""
    if not arms:
        raise EnumLabelError("an enum label needs at least one arm")
    count = len(arms) + 1
    try:
        # The 0xFFFF default first, at the base (`switchbuild.append_arms`); then descending
        # (see the module docstring): arm `i` is matched on `i`, wherever it sits.
        out, base = switchbuild.append_arms(data, [default] + arms[::-1],
                                            [0xFFFF] + list(range(len(arms)))[::-1])
        switch = switchbuild.author_switch(out, value_slot=value_slot, arms_base=base,
                                           count=count, at=(x, y))
        parts = [switch] + ([] if restore is None else [restore_record(*restore)])
    except (ValueError, cornermark.CornerMarkError) as e:   # RelocationError too
        raise EnumLabelError(str(e)) from e
    out, edits, index, recs = _attach(out, parent, parts)
    if not (len(recs) == len(parts) and recs[0].wtype == 6 and recs[0].child_index == base
            and recs[0].count == count and recs[0].value_slot == value_slot):
        raise EnumLabelError(f"the label object {index} does not hold the authored switch")
    return out, edits, index


def _attach(data: bytes, parent: int, parts: list[bytes]) -> tuple[bytes, list, int, list]:
    """`(image, edits, object_index, records)`: `parts` as one new object attached under
    `parent` (`insert.insert_object`, a byte clone of its first wtype-5 attach). Not resealed."""
    try:
        out, edits = insert.insert_object(data, cornermark._object(parts), parent)
    except (ValueError, cornermark.CornerMarkError) as e:   # InsertError, RelocationError too
        raise EnumLabelError(str(e)) from e
    gs, _ = guidata.guidata_extent(out)
    tab = guidata.object_tables(out)[1]
    index = len(tab) - 1
    return out, edits, index, records.object_records(out, gs + tab[index])


def place_static(data: bytes, *, parent: int, parts: list[bytes],
                 leaving: "tuple[int | None, int]"
                 ) -> "tuple[bytes, list, int, tuple[int | None, int] | None]":
    """`(image, edits, object_index, restore)`: `parts` (absolute records, no switch:
    `label_records(at=...)`) in a new object attached under `parent`, ended by
    `restore_record(*restore)` when the `(font, align)` the records leave (`leaving`) differs
    from what the walk carries in (`restore_state`; `restore` None when it does not). Raises
    `EnumLabelError` as `restore_state` does, or when the object does not hold the records."""
    out, edits, index, recs = _attach(data, parent, parts)
    restore = restore_state(out, parent, leaving)
    if restore is not None:
        out, edits, index, recs = _attach(data, parent, parts + [restore_record(*restore)])
    if len(recs) != len(parts) + (restore is not None):
        raise EnumLabelError(f"the label object {index} does not hold the authored records")
    return out, edits, index, restore


def leaving_state(data: bytes, parent: int, attach_index: int) -> set:
    """The `(font, align)` each walk of a page reaching `parent` carries out of record
    `attach_index`'s subtree (the state after its last record). `rvcbutton.place` and
    `cardreadouts.attach` measure what their object leaves with it, for `restore_state`.

    Base arms first; only when no base walk passes the record are the switch-arm sets
    (`pagewalk.switch_arm_sets`) walked, as `entering_state` does (a parent that only a switch
    arm reaches, e.g. HOME's 902)."""
    step = (parent, attach_index)
    roots = pagewalk.reaching_roots(data, parent)
    found: set = set()
    for sets in ([{}], pagewalk.switch_arm_sets(data, roots)):
        for root in roots:
            for values in sets:
                walk = pagewalk.walk_records(data, root, switch_values=values or None)
                for i, r in enumerate(walk):
                    inside = step in r.key
                    if inside and (i + 1 == len(walk) or step not in walk[i + 1].key):
                        found.add((r.font, r.align))
        if found:
            break
    return found


def restore_state(placed: bytes, parent: int, leaving: "tuple[int | None, int]"
                  ) -> "tuple[int | None, int] | None":
    """The `(font, align)` a restore record (`restore_record`) must write after an object just
    attached as `parent`'s last record, or None when the object already leaves `leaving` equal
    to the state the walk carried in (`entering_state`). Shared by `add_enum_label` (which
    leaves its own font and `ALIGN_NO_SHIFT`) and `rvcbutton.place` (which measures it).
    Raises `EnumLabelError` when the entering state cannot be measured or written back: no page
    reaches `parent`, pages disagree, no font is set yet (0xFF cannot be restored) or the align
    is 0 (the decoder never stores a 0 align)."""
    gs, _ = guidata.guidata_extent(placed)
    attach = len(records.object_records(placed, gs + guidata.object_tables(placed)[1][parent])) - 1
    states = entering_state(placed, parent, attach)
    if len(states) > 1:
        raise EnumLabelError(
            "the pages reaching object %d enter it with different font/align states %s; "
            "one object cannot restore them all" % (parent, sorted(states, key=str)))
    if not states:
        raise EnumLabelError(
            "no page root reaches object %d, so the font/align the object must hand back "
            "cannot be measured" % parent)
    ((font, align),) = states
    if font is None or align == 0:
        raise EnumLabelError(
            "the walk enters object %d with font %r / align %d; the placed object's font %r / "
            "align %d cannot be written back to that" % (parent, font, align, *leaving))
    font = None if font == leaving[0] else font
    align = 0 if align == leaving[1] else align
    return None if font is None and not align else (font, align)


def foreign_deltas(before: bytes, after: bytes, parent: int, label_object: int) -> list:
    """Every `pagewalk.diff` delta, over each page root reaching `parent` and each of its
    switch-arm sets (`pagewalk.switch_arm_sets` of the image before), that is NOT one of the
    placed object's own records appearing: the attach (the parent's last record), the object's
    records and the arms of every wtype-6 switch in it. Empty means nothing else on any page
    moved or changed state, the check `move_control` and `assert_layout` make. Shared by
    `add_enum_label` (one switch) and `add_rvc_button` (no switch).

    The placed object's own switches are walked too, one set per non-base arm: the image before
    has no such switch, so its arm sets would only ever walk their base arm, and under
    `add_enum_label` that is the blank default, which sets no font. A text arm's leaked font
    is caught only by walking that arm."""
    gs, _ = guidata.guidata_extent(after)
    tab = guidata.object_tables(after)[1]
    attach = len(records.object_records(after, gs + tab[parent])) - 1
    ours = {label_object}
    own_sets = []
    match_off = guidata.find_match_table(after)
    for sw in records.object_records(after, gs + tab[label_object]):
        if sw.wtype == 6:
            ours.update(range(sw.child_index, sw.child_index + sw.count))
            own_sets += [{sw.child_index: v}
                         for v, o in guidata.switch_candidates(after, sw, match_off)
                         if o != sw.child_index]
    roots = pagewalk.reaching_roots(after, parent)
    bad = []
    for values in pagewalk.switch_arm_sets(before, roots) + own_sets:
        for root in roots:
            for d in pagewalk.diff(pagewalk.walk_records(before, root, switch_values=values),
                                   pagewalk.walk_records(after, root, switch_values=values)):
                if d.kind == "appeared" and (d.after.obj in ours or
                                             (d.after.obj, d.after.index) == (parent, attach)):
                    continue
                bad.append(d)
    return bad
