"""statelights -- a button lit from a display cell: ONE wtype-6 switch over whole-button arms.

`add_button_status` (`recipe_ops.AddButtonStatus`) and the Batt Heat status ops
(`aescompact.add_batt_heat_state_switch`) light a button the same way. `wrap_button` takes the
button's static body out of its object's record stream and puts ONE two-or-more-arm switch, on
the button's cell, in its place:

  before:  box, anchor, CAPSULE, [strip], [caption], [glyph, cancel], touch, [restore]
  after:   box, anchor, SWITCH,                                      touch, [restore]
  arm k:   capsule in arm k's fill, [strip], [caption], [glyph, cancel], [contactor, cancel]

**Each arm draws the whole button** (the v31 lesson, `aescompact`'s state-arm comment): the panel
repaints only the arm it selects, so an arm that left out the caption would leave the previous
arm's capsule over it. The opaque capsule goes first and covers whatever the outgoing arm drew.

**The body moves into the arms byte for byte.** Everything between the capsule and the touch
region is copied verbatim into every arm, so the flow origin, font and align the arm commits are
the ones the stream records used to commit, and the touch region after the switch does not move
(`foreign_deltas` checks it on every page). Only the capsule is rebuilt: the Low Volts donor
285[0] at the inner size with its mode-7 fillA set to the arm's index, as the button builder
(`rvcbutton.button_records`) made it. A wtype-33 capsule is not in the layout flow gate.

**Finding the body.** With `touch_id`, the button is the one wtype-12 record carrying that id
(the touch id equals the button's group), and its capsule is the last wtype-33 before it; every
record in between must belong to a button body (empty labels, the caption, the glyph), so a
capsule another button's switch already replaced cannot be mistaken for this one. Without it,
the Batt Heat rule (`aescompact._batt_heat_body_records`: the fill-35 capsule directly before
the `text` caption) names the span, which is the path the coach-verified v32 heater status and
v49's Ignore Alarms status take, byte-identically.

**Arms** are `(match, lit, contactor)` triples: `match` the match-table value (the `0xFFFF`
default arm FIRST, `switchbuild.append_arms`), `lit` picks `on_index` over `off_index`, and
`contactor` adds the Batt Heat contactor glyph top-right (`aescompact.BATT_HEAT_STATE_ARMS`).
"""
from __future__ import annotations

import struct

from . import (aescompact, cornermark, guidata, insert, pagewalk, records, relocate,
               switchbuild)


class StateLightsError(ValueError):
    """A button body could not be found, or its arms or switch could not be built."""


def _touch_id(data: bytes, r: records.WRecord) -> int:
    return data[r.end - 2] | (data[r.end - 1] << 8)


def body_span(data: bytes, recs: list, *, object_index: int, w: int, h: int,
              text: str | None, glyph: tuple[int, str] | None = None, font: int | None = None,
              touch_id: int | None = None) -> tuple[int, int]:
    """`(capsule, end)`: the record indices `[capsule, end)` of the button's static body in
    object `object_index`'s stream `recs` (module docstring, "Finding the body")."""
    if touch_id is None:
        cap, lab = aescompact._batt_heat_body_records(recs, object_index, text)
        return cap, lab + 1
    touches = [i for i, r in enumerate(recs) if r.wtype == 12 and _touch_id(data, r) == touch_id]
    if len(touches) != 1:
        raise StateLightsError(f"expected one touch region with id {touch_id} in object "
                               f"{object_index}, found {touches}")
    t = touches[0]
    caps = [i for i in range(t) if recs[i].wtype == 33]
    if not caps:
        raise StateLightsError(f"no capsule (wtype 33) before touch {touch_id} in object "
                               f"{object_index}")
    cap = caps[-1]
    inset = aescompact.PUMP_OUTLINE_INSET
    c = recs[cap]
    if c.geom[2:4] != [w - 2 * inset, h - 2 * inset] or not c.colors \
            or c.colors[0][:2] != ("fillA", 7):
        raise StateLightsError(f"object {object_index} record {cap} is not a {w} x {h} button's "
                               f"static capsule (geom {c.geom[2:4]}, colours {c.colors})")
    body = recs[cap + 1:t]
    stray = [cap + 1 + k for k, r in enumerate(body) if r.wtype not in (0, 11)]
    if stray:
        raise StateLightsError(f"records {stray} between the capsule and touch {touch_id} in "
                               f"object {object_index} are not a button body's")
    captions = [r for r in body if r.wtype == 11 and r.label]
    if [r.label for r in captions] != ([] if text is None else [text]):
        raise StateLightsError(f"button body in object {object_index} has captions "
                               f"{[r.label for r in captions]}, expected {text!r}")
    if font is not None and any(r.font != font for r in captions):
        raise StateLightsError(f"the caption is not in font {font}")
    glyphs = [(r.font, r.label) for r in body if r.wtype == 0]
    if glyphs != ([] if glyph is None else [tuple(glyph)]):
        raise StateLightsError(f"button body in object {object_index} has glyphs {glyphs}, "
                               f"expected {glyph!r}")
    return cap, t


def capsule(donors: bytes, *, fill_index: int, w: int, h: int) -> bytes:
    """The button's capsule at the inner size, in static fill `fill_index` (donor 285[0])."""
    a = aescompact
    inset = a.PUMP_OUTLINE_INSET
    btn = a._clone_with_geom(donors, a.PUMP_BUTTON_BODY_DONOR, a.PUMP_BUTTON_BOX_REC,
                             {2: w - 2 * inset, 3: h - 2 * inset})
    box_rec = a._object_records(donors, a.PUMP_BUTTON_BODY_DONOR)[a.PUMP_BUTTON_BOX_REC]
    fill_off = box_rec.color_off[0]
    if fill_off is None:
        raise StateLightsError("Low Volts box donor 285[0] has no inline fillA colour to repoint")
    struct.pack_into("<H", btn, fill_off - box_rec.offset, fill_index)
    return bytes(btn)


def contactor_records(w: int) -> list[bytes]:
    """The Batt Heat contactor glyph, top-right of a `w`-wide button, and the empty record that
    cancels its offset."""
    a = aescompact
    dx = a.contactor_glyph_dx(w)
    return [a._red_glyph_record(dx, a.CONTACTOR_GLYPH_DY, a.CONTACTOR_GLYPH_W, a.CONTACTOR_GLYPH_H),
            cornermark.build_empty_record(-dx, -a.CONTACTOR_GLYPH_DY)]


def build_arm(donors: bytes, *, fill_index: int, body: list[bytes], w: int, h: int,
              text: str | None, contactor: bool = False) -> bytes:
    """One arm: the capsule in `fill_index`, then `body` (the stream records it replaces, byte
    for byte), then the contactor glyph when asked. `<count>` + records, padded to 4."""
    parts = [capsule(donors, fill_index=fill_index, w=w, h=h)] + list(body)
    if contactor:
        parts += contactor_records(w)
    payload = cornermark._object(parts)
    chk = records.object_records(payload, 0)
    inset = aescompact.PUMP_OUTLINE_INSET
    mark = aescompact.CONTACTOR_GLYPH_FONT_SLOT
    if not (len(chk) == len(parts) and chk[0].wtype == 33
            and chk[0].colors[0] == ("fillA", 7, fill_index)
            and chk[0].geom[2:4] == [w - 2 * inset, h - 2 * inset]
            and [r.label for r in chk[:len(parts) - 2 * contactor] if r.wtype == 11 and r.label]
            == ([] if text is None else [text])
            and any(r.font == mark and r.wtype == 11 for r in chk) == contactor):
        raise StateLightsError(f"button state arm (fill {fill_index}, contactor {contactor}) "
                               "failed its self-decode")
    return payload


def _stream(data: bytes, object_index: int) -> list:
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    return records.object_records(data, gs + tab[object_index])


def wrap_button(data: bytes, *, object_index: int, value_slot: int, donors: bytes,
                arms: tuple, off_index: int, on_index: int, w: int, h: int,
                text: str | None, glyph: tuple[int, str] | None = None,
                font: int | None = None, grow: bool = True,
                touch_id: int | None = None) -> tuple[bytes, dict]:
    """Replace the button's static body in object `object_index` with ONE enrolled wtype-6
    switch on cell `value_slot` over whole-button `arms` (module docstring). `grow` False appends
    the arms without growing the match table (a caller that grew it already). Returns the image
    (not resealed) and `{arms_base, arm_objects, switch_index, switch_offset, span, value_slot,
    off_index, on_index}`, `span` the body's `(capsule, end)` before the wrap."""
    recs = _stream(data, object_index)
    cap, end = body_span(data, recs, object_index=object_index, w=w, h=h, text=text,
                         glyph=glyph, font=font, touch_id=touch_id)
    body = [bytes(data[r.offset:r.offset + r.size]) for r in recs[cap + 1:end]]
    arm_objects = [build_arm(donors, fill_index=on_index if lit else off_index, body=body,
                             w=w, h=h, text=text, contactor=contactor)
                   for _match, lit, contactor in arms]
    out = data
    if grow:
        out, arms_base = switchbuild.append_arms(out, arm_objects, [m for m, _l, _c in arms])
    else:
        landed = []
        for arm in arm_objects:
            out, _e, i = insert._append_object(out, arm)
            landed.append(i)
        if landed != list(range(landed[0], landed[0] + len(arm_objects))):
            raise StateLightsError("button state arms did not land contiguously (%s)" % landed)
        arms_base = landed[0]
    switch = switchbuild.author_switch(out, value_slot=value_slot, arms_base=arms_base,
                                       count=len(arms))
    recs = _stream(out, object_index)
    if body_span(out, recs, object_index=object_index, w=w, h=h, text=text, glyph=glyph,
                 font=font, touch_id=touch_id) != (cap, end):
        raise StateLightsError("the button body moved while its arms were appended")
    stream = [out[r.offset:r.offset + r.size] for r in recs]
    stream[cap:end] = [switch]
    out, _ = relocate.replace_object_records(out, object_index, cornermark._object(stream))
    sw = _stream(out, object_index)[cap]
    return out, {"arms_base": arms_base,
                 "arm_objects": list(range(arms_base, arms_base + len(arm_objects))),
                 "switch_index": cap, "switch_offset": sw.offset, "span": (cap, end),
                 "value_slot": value_slot, "off_index": off_index, "on_index": on_index}


def _remap(key: tuple, obj: int, cap: int, end: int):
    """`key` after `[cap, end)` of `obj` became one record at `cap`; None inside the span."""
    out = []
    for o, i in key:
        if o == obj:
            if cap <= i < end:
                return None
            if i >= end:
                i -= end - cap - 1
        out.append((o, i))
    return tuple(out)


def _inert_colour_delta(d, object_index: int) -> bool:
    """A delta the wrap makes by construction and nothing can see: an EMPTY label of the
    button's own object (the `enumlabel.restore_record` after the touch region) inheriting
    different colours. The layout engine restores the colour slots after a container
    (`enumlabel` module docstring), so the colours the caption set no longer leave the switch;
    an empty label draws no pixels, and its font, align and box are compared as before."""
    b, a = d.before, d.after
    return (d.kind == "state" and b is not None and a is not None and b.obj == object_index
            and b.wtype == 11 and not b.label and not a.label
            and (b.font, b.align, b.value_slot) == (a.font, a.align, a.value_slot))


def foreign_deltas(before: bytes, after: bytes, object_index: int, span: tuple[int, int]) -> list:
    """Every `pagewalk.diff` delta, over each page root reaching `object_index` and each of its
    switch-arm sets, that is not the wrap itself: the body records `span` leave, the switch and
    its arm's records appear, and every other record keeps its box and state. Each walk also
    checks the selected arm draws the body where the stream drew it (the same wtype and box, in
    order; a contactor glyph after them aside); a mismatch is returned as the arm's first
    record. Colour-only deltas on the object's own empty labels are not foreign
    (`_inert_colour_delta`)."""
    cap, end = span
    roots = pagewalk.reaching_roots(after, object_index)
    bad = []
    for values in pagewalk.switch_arm_sets(before, roots):
        for root in roots:
            b = pagewalk.walk_records(before, root, switch_values=values)
            a = pagewalk.walk_records(after, root, switch_values=values)
            remap = {r.key: _remap(r.key, object_index, cap, end) for r in b
                     if any(o == object_index for o, _i in r.key)}
            for d in pagewalk.diff(b, a, remap=remap):
                if d.kind == "appeared" and (object_index, cap) in d.after.key:
                    continue
                if _inert_colour_delta(d, object_index):
                    continue
                bad.append(d)
            # the selected arm draws the body where the stream drew it (the body records are
            # leaves, so each sits one step below the switch's parent prefix)
            for sw in (r for r in a if r.key[-1] == (object_index, cap)):
                k = sw.key
                was = [(r.wtype, r.box) for r in b if r.key[:-1] == k[:-1]
                       and r.key[-1][0] == object_index and cap <= r.key[-1][1] < end]
                drawn = [r for r in a if len(r.key) == len(k) + 1 and r.key[:-1] == k]
                if [(r.wtype, r.box) for r in drawn][:len(was)] != was:
                    first = drawn[0] if drawn else sw
                    bad.append(pagewalk.Delta(first.key, first.wtype, first.label, "moved",
                                              None, first))
    return bad
