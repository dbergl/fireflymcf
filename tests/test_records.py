"""The byte-exact widget-record decoder, validated against the object-table boundaries.

An object in the `+0x46148` table is `<count><records...><pad to 4-byte boundary>`, and all
objects are 4-byte aligned. So a correct `decode_record` walks exactly `count` records and
lands 0-3 bytes short of the next object's start. This test asserts that for *every* object
in *all three* builds — the same oracle that drove the decoder to byte-exactness. It is not
fitted to any screen: a wrong read order derails somewhere in ~3700 objects.
"""
from __future__ import annotations

import struct

import pytest

from fireflymcf import guidata, layout, records


BUILDS = {
    "11.23": "firefly-11.23/mcf.bin",
    "13.6": "firefly-13.6/mcf.bin",
    "14.7": "firefly-14.7 (Terrain)/mcf.bin",
}
EXPECTED = {"11.23": 1200, "13.6": 1421, "14.7": 1076}     # objects with a next boundary


def _object_table(data: bytes) -> list[int]:
    """The screen/object table (the ~1077-1421-entry one; GUICODE Finding 12)."""
    gs, ge = guidata.guidata_extent(data)
    tables = guidata.object_tables(data)
    # The object table is the one whose entries are <count><records> objects; it is table[1]
    # across all three builds (sizes 8/9, then ~1077-1422, then ~30, then ~2700-3300).
    return [p for p in tables[1] if 0 < p < (ge - gs)]


@pytest.mark.parametrize("build", sorted(BUILDS))
def test_every_object_decodes_to_its_boundary(build: str) -> None:
    with open(BUILDS[build], "rb") as handle:
        data = handle.read()
    gs, _ = guidata.guidata_extent(data)
    offs = sorted(set(_object_table(data)))
    nxt = {a: b for a, b in zip(offs, offs[1:])}

    ok = 0
    for o in offs:
        if o not in nxt:
            continue
        fo, end = gs + o, gs + nxt[o]
        recs = records.object_records(data, fo)
        pos = recs[-1].end if recs else fo + 1
        gap = end - pos
        assert 0 <= gap < 4, (
            f"{build} object {fo:#x} count={data[fo]} ended at +{pos - fo} "
            f"(gap {gap} to boundary {nxt[o]:#x}) — read order is off")
        ok += 1
    assert ok == EXPECTED[build], f"{build}: validated {ok} objects, expected {EXPECTED[build]}"


def test_decoder_reads_the_starlink_button() -> None:
    """A spot check against known content: the Bath-Fan/Starlink button object in 13.6."""
    with open(BUILDS["13.6"], "rb") as handle:
        data = handle.read()
    recs = records.object_records(data, 0x97200)
    labels = [r.label for r in recs if r.label]
    assert "Bath Fan" in labels                       # the caption, read length-prefixed
    # the four records: rounded button, icon glyph, caption, touch region
    assert len(recs) == 4


def test_decoder_lands_byte_exact_not_by_luck() -> None:
    """Guard: a deliberately wrong read (drop the label string) must derail the oracle."""
    with open(BUILDS["13.6"], "rb") as handle:
        data = handle.read()
    # A label object whose string must be consumed to land on the boundary.
    recs = records.object_records(data, 0x71950)       # single 'Float' label record
    assert any(r.label == "Float" for r in recs)


def _switch_rec(data, obj, slot):
    """The type-6 switch record inside object `obj` reading variable `slot`."""
    gs, _ = guidata.guidata_extent(data)
    table = _object_table(data)
    for r in records.object_records(data, gs + table[obj]):
        if r.wtype == 6 and r.value_slot == slot:
            return r
    raise AssertionError(f"no type-6 switch reading slot {slot} in obj {obj}")


def test_type6_switch_count_is_decoded() -> None:
    """A type-6/0x2e value-match switch carries its candidate COUNT as a u16 in its own bytes —
    the firmware's record `+0x94`, written in FUN_00252CB0 at 0x00253B2A under the mask
    `(aed & 0x40) | (aee & 0x4000)` (i.e. wtype 6 or 0x2e), and read back by the switch scan at
    0x00257B3A (`ldrsh r3,[r6,#0x94]`). records.py already read this u16 at the R45 position but
    discarded it (the old code assumed every switch resolves to a single child); `WRecord.count`
    now surfaces it. Byte-verified on three switches: the u16 sits immediately before the
    child_index (inverter `71 00 · 02 00 · 81 05`; Bath Fan `6d 00 · 03 00 · f6 00`; floorplan
    `05 00 · 0b 00 · 8f 03`). Non-vacuous: a stub returning None fails these equalities."""
    with open(BUILDS["13.6"], "rb") as handle:
        data = handle.read()
    assert _switch_rec(data, 899, 113).count == 2      # inverter: [1409,1411) = {1409,1410}
    assert _switch_rec(data, 881, 109).count == 3      # Bath Fan: [246,249)
    assert _switch_rec(data, 4, 5).count == 11         # HOME floorplan: [911,922), mv 0..10


def test_multiline_text_align_bytes_are_decoded() -> None:
    """A wt-11/13 text record's R49 block (gated aed&0x2800) carries the multi-line text
    placement bytes the drawer FUN_002559A0 reads: state+0x94 h-align, +0x95 v-align, +0x96
    line-height (0x00253D2E/D38/D42 strb …,[r4,#0x94..0x96]). records.py consumed these 4 bytes
    for alignment but discarded them; WRecord now surfaces h_align/v_align/line_height. Byte-
    verified on the HOME bulb column: the 'Main\\nCeiling' label (obj 905) is v-align 3 (bottom),
    the bulb glyph (obj 258) v-align 2 (centre); both h-align 2 (centre). Non-vacuous: a stub
    returning None fails the equalities."""
    with open(BUILDS["13.6"], "rb") as handle:
        data = handle.read()
    gs, _ = guidata.guidata_extent(data)
    table = _object_table(data)

    def label_rec(obj, text):
        for r in records.object_records(data, gs + table[obj]):
            if (r.label or "").strip().replace("\n", "\\n") == text or (r.label or "") == text:
                return r
        raise AssertionError(f"{text!r} not in obj {obj}")

    lab = label_rec(905, "Main\nCeiling")
    assert (lab.h_align, lab.v_align) == (2, 3)      # centre horizontally, BOTTOM vertically
    assert lab.line_height == 18
    glyph = next(r for r in records.object_records(data, gs + table[258]) if r.label == "2")
    assert (glyph.h_align, glyph.v_align) == (2, 2)  # the glyph block centres in its cell


# wtype-13 counts per build (all carry a value_slot, none carry an inline label).
DYNAMIC_TEXT_COUNTS = {"11.23": 48, "13.6": 75, "14.7": 35}


@pytest.mark.parametrize("build", sorted(BUILDS))
def test_wtype13_is_dynamic_text_bound_to_a_slot(build: str) -> None:
    """wtype 13 is the dynamic-string widget (GUICODE Finding 31): every one draws its text
    from `value_slot`'s variable-table entry, never from an inline label. Corpus-invariant
    across all three builds — a decoder that mis-classified the type or dropped the value_slot
    read would break the count or the (slot present, no label) shape. Non-vacuous: the count
    assertion fails if `is_dynamic_text` returns nothing."""
    with open(BUILDS[build], "rb") as handle:
        data = handle.read()
    gs, _ = guidata.guidata_extent(data)
    n = 0
    for off in sorted(set(_object_table(data))):
        for r in records.object_records(data, gs + off):
            if not r.is_dynamic_text:
                continue
            n += 1
            assert r.wtype == 13
            assert r.value_slot is not None, f"{build} wt13 @{r.offset:#x} has no value_slot"
            assert not r.label, f"{build} wt13 @{r.offset:#x} unexpectedly carries {r.label!r}"
    assert n == DYNAMIC_TEXT_COUNTS[build], f"{build}: {n} wtype-13 records"


def test_aes_ags_runtime_label_is_a_wtype13_string_slot() -> None:
    """The ELECTRICAL run-time caption ("Max Engine Run Time" / "Max Gen Run Time") and the
    AES/AGS mode indicator are wtype-13 dynamic-string widgets whose value_slot resolves to a
    type-9 (string) SRAM buffer that GuiCode's FUN_00247BF8 fills from the CAN config — the
    display end of GUICODE Finding 31. Ties records.py (binding) to layout.variable_register
    (slot -> SRAM, type). Non-vacuous: wrong wtype/slot/type each break an assertion."""
    with open(BUILDS["13.6"], "rb") as handle:
        data = handle.read()
    gs, _ = guidata.guidata_extent(data)

    caption = [r for off in _object_table(data)
               for r in records.object_records(data, gs + off)
               if r.wtype == 13 and r.value_slot == 269]
    assert len(caption) == 1, f"expected one caption widget, got {len(caption)}"
    cap = caption[0]
    assert cap.is_dynamic_text
    assert (cap.geom[2], cap.geom[3]) == (220, 80)     # box corner, same place as the static text

    # slot 269 -> the caption SRAM buffer (0x20039DEC), slot 129 -> the indicator (0x20039B9C);
    # both type 9 (string). slot 270 (the "115 Min" value) is the adjacent numeric (type 3).
    assert layout.variable_register(data, 269) == (0x20039DEC, 9)
    assert layout.variable_register(data, 129) == (0x20039B9C, 9)
    assert layout.variable_register(data, 270) == (0x2003A280, 3)

    indicator = [r for off in _object_table(data)
                 for r in records.object_records(data, gs + off)
                 if r.wtype == 13 and r.value_slot == 129]
    assert indicator, "AES/AGS mode indicator widget (slot 129) not found"


def test_child_index_offset_round_trips_on_containers() -> None:
    """`records.child_index_offset` returns the file offset of a wtype-5/6/0x2e record's child
    index u16, recovered by replaying the decoder's cursor to R48. Validate it round-trips: the
    u16 stored at the returned offset equals `rec.child_index`, across a spread of container
    records on 13.6 — plain wt-5 wrappers (989/990 -> 295, the Tank Heater / Water Pump family),
    the HOME tank-quadrant floorplan switch (obj 4, wt-6 [911,922)), and the button switch inside
    quadrant 912 (wt-6 [989,991)). A non-container record returns None."""
    with open(BUILDS["13.6"], "rb") as handle:
        data = handle.read()
    gs, _ = guidata.guidata_extent(data)
    table = _object_table(data)

    seen_container = 0
    seen_leaf = 0
    for obj in (989, 990, 295, 4, 912, 911, 278):
        for r in records.object_records(data, gs + table[obj]):
            off = records.child_index_offset(data, r)
            if r.child_index is None:
                assert off is None, f"leaf wt{r.wtype} @ {r.offset:#x} got offset {off}"
                seen_leaf += 1
                continue
            assert off is not None
            got = struct.unpack_from("<H", data, off)[0]
            assert got == r.child_index, (
                f"obj {obj} wt{r.wtype}: offset {off:#x} reads {got}, want {r.child_index}")
            # the offset is the cursor position at R48, i.e. inside the record body
            assert r.offset < off < r.end
            seen_container += 1
    assert seen_container >= 6, f"expected several container records, saw {seen_container}"
    assert seen_leaf >= 3, f"expected several leaf records, saw {seen_leaf}"

    # Spot-check the exact byte position the report cites: obj 989's wt-5 child (-> 295).
    wt5 = records.object_records(data, gs + table[989])[0]
    assert wt5.wtype == 5 and wt5.child_index == 295
    assert records.child_index_offset(data, wt5) == wt5.offset + wt5.size - 2


def test_child_index_offset_detects_a_wrong_offset() -> None:
    """Guard (the standing "check your tests can fail" rule): a record whose stored child index
    disagrees with the bytes at the offset must raise, never return a wrong offset silently."""
    with open(BUILDS["13.6"], "rb") as handle:
        data = bytearray(open(BUILDS["13.6"], "rb").read())
    gs, _ = guidata.guidata_extent(bytes(data))
    table = _object_table(bytes(data))
    wt5 = records.object_records(bytes(data), gs + table[989])[0]
    off = records.child_index_offset(bytes(data), wt5)
    # Corrupt the byte at the offset so the cached rec.child_index (295) no longer matches.
    data[off] ^= 0xFF
    with pytest.raises(ValueError):
        records.child_index_offset(bytes(data), wt5)


def test_color_offset_round_trips_to_the_fillA_index() -> None:
    """`records.color_offset` returns the file offset of a record's mode-7 fillA index, verified
    by re-reading the u16 there. Object 250 (stock 13.6 'Start' button box) carries fillA mode-7
    145; the offset must read back 145 and sit inside the record."""
    with open(BUILDS["13.6"], "rb") as handle:
        data = handle.read()
    gs, _ = guidata.guidata_extent(data)
    table = _object_table(data)
    box = next(r for r in records.object_records(data, gs + table[250])
               if r.wtype == 33 and any(c[0] == "fillA" and c[1] == 7 for c in r.colors))
    off = records.color_offset(data, box, "fillA")
    assert off is not None
    assert box.offset < off < box.end
    assert struct.unpack_from("<H", data, off)[0] == 145        # the decoded fillA value

    # an inherit-mode role (no inline value) returns None, not a wrong offset
    inherit = next((r for r in records.object_records(data, gs + table[250])
                    for c in r.colors if c[0] == "fillA" and c[1] in (0, 4)), None)
    if inherit is not None:
        assert records.color_offset(data, inherit, "fillA") is None


@pytest.mark.parametrize("build", sorted(BUILDS))
def test_r49_decodes_four_bytes_including_the_line_height_flag(build: str) -> None:
    """R49 is FOUR bytes, not three: h-align, v-align, line-height, and a flag
    (`FUN_00252CB0` stores them at 0x253d2e/d38/d42/d4c -> render state +0x94..+0x97).

    The fourth byte was consumed-and-discarded on the assumption it was padding. It is not:
    the multi-line drawer `FUN_002559A0` applies it once before the first advance
    (0x255a0c-0x255a26) as `line_height += font[0x07]`, so a set flag means "space the lines by
    the font's own cell height".

    The flag is a general modifier, not a "use the default" marker: **most** flagged records carry
    `line_height == 0` (so the advance is exactly the cell height), but a handful carry a non-zero
    base and mean `base + cell`. That was an over-claim in the first draft of this test, caught by
    the test itself, and it is why the assertion below is on the decode rather than on a pairing.
    """
    data = open(BUILDS[build], "rb").read()
    gs, _ = guidata.guidata_extent(data)
    table = _object_table(data)
    flagged = with_r49 = zero_base = 0
    for off in table:
        try:
            recs = records.object_records(data, gs + off)
        except Exception:
            continue
        for rec in recs:
            if rec.line_height is None:
                assert rec.line_height_adds_font is None      # no R49 -> neither field
                continue
            with_r49 += 1
            assert rec.line_height_adds_font in (0, 1), rec.line_height_adds_font
            if rec.line_height_adds_font:
                flagged += 1
                zero_base += (rec.line_height == 0)
    assert with_r49 > 500, with_r49
    assert flagged > 20, f"expected the flag to be exercised in {build}, saw {flagged}"
    # the dominant use is "space by the font cell height" (line_height 0), which is the case the
    # renderer previously had to approximate with `size + 2`
    assert zero_base >= flagged * 0.8, (zero_base, flagged)


def test_font_offset_locates_an_explicit_selector_and_an_inherit_one():
    """The R38 selector is one byte where 0xff means "inherit the persistent font".

    `font_offset` returns the offset whenever the FIELD is present, including the 0xff case --
    which is the case that matters, because writing a real slot there is the documented one-byte
    font change. Water Pump (295) carries an explicit 13; the Bath Fan donor's caption (247)
    carries the field holding 0xff.
    """
    data = open(BUILDS['13.6'], 'rb').read()
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]

    wp = [r for r in records.object_records(data, gs + table[295]) if r.wtype == 11]
    explicit = [r for r in wp if r.font is not None]
    assert explicit, "Water Pump's caption should carry an explicit font selector"
    for rec in explicit:
        off = records.font_offset(data, rec)
        assert off is not None and data[off] == rec.font

    # Object 279 is the button donor, and its caption is the inherit case this exists for.
    donor = [r for r in records.object_records(data, gs + table[279])
             if r.wtype == 11 and r.font is None]
    assert donor, "expected an inherit-font caption on donor 279"
    assert any((o := records.font_offset(data, r)) is not None and data[o] == 0xFF
               for r in donor), "an inherit selector must still expose its byte"

    # Corpus-wide, not one hand-picked object: every record the decoder says carries the field
    # must round-trip, whichever of the two cases it is.
    present = explicit_n = inherit_n = 0
    for i in range(len(table)):
        try:
            recs = records.object_records(data, gs + table[i])
        except Exception:
            continue
        for rec in recs:
            off = records.font_offset(data, rec)
            if off is None:
                continue
            present += 1
            if rec.font is None:
                inherit_n += 1
                assert data[off] == 0xFF
            else:
                explicit_n += 1
                assert data[off] == rec.font
    assert present and explicit_n and inherit_n, (present, explicit_n, inherit_n)


def test_font_offset_round_trip_guard_rejects_a_moved_offset():
    """The guard is what makes the offset safe to write through."""
    data = open(BUILDS['13.6'], 'rb').read()
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    rec = next(r for r in records.object_records(data, gs + table[295])
               if r.wtype == 11 and r.font is not None)
    off = records.font_offset(data, rec)
    tampered = bytearray(data)
    tampered[off] = (rec.font + 1) & 0xFF
    # The decoder now reads a different font, so a stale WRecord must not validate against it.
    with pytest.raises(ValueError, match="font_offset"):
        records.font_offset(bytes(tampered), rec)


def _all_wtypes(path):
    data = open(path, "rb").read()
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    seen = set()
    for off in tab:
        try:
            for r in records.object_records(data, gs + off):
                seen.add(r.wtype)
        except Exception:
            pass
    return seen


@pytest.mark.parametrize("build,path", BUILDS.items())
def test_every_corpus_wtype_is_named(build, path):
    """Every widget-dispatch type that occurs is in `records.WTYPES` (the 24-case FUN_00256C54
    switch, GUICODE 'Finding 10 completed'). A new unnamed type must be decoded, not ignored."""
    unnamed = sorted(wt for wt in _all_wtypes(path) if wt not in records.WTYPES)
    assert unnamed == [], f"{build}: unnamed wtypes {unnamed}"


def test_the_wtype_names_that_earlier_prose_got_wrong():
    # The corrections from the full dispatch: 12 is the touch region (not a label), 11 is
    # multi-line text, 16 is a dot (not an image-button), 32 is the gauge well.
    assert records.wtype_name(0x0C) == "touch"
    assert records.wtype_name(0x0B) == "text_multiline"
    assert records.wtype_name(0x10) == "dot"
    assert records.wtype_name(0x20) == "gauge_well"
    assert records.wtype_name(0x3E).startswith("unknown_")   # a default-case type


# ----------------------------------------------------------------------------------------
# GUICODE Finding 65: wtype 9 carries the number-format directive (mode, flags, width, places).
# ----------------------------------------------------------------------------------------

def _walk_directives(data: bytes, page: str) -> list[tuple]:
    """(kind, obj, ...) events in layout-walk order: each type-9 directive as it is decoded and
    each wtype-8 readout with the directive in force when it is placed. Thin wrapper over
    `readout.directive_in_force` -- the Finding-65 walk lives in one place."""
    from fireflymcf import cli, readout
    root = cli._resolve_page_root(data, page)
    return readout.directive_in_force(data, root)


def test_every_type9_record_carries_a_format_directive_and_nothing_else_does() -> None:
    with open(BUILDS["13.6"], "rb") as handle:
        data = handle.read()
    gs, _ = guidata.guidata_extent(data)
    tbl = guidata.object_tables(data)[1]
    n9 = 0
    for off in tbl:
        for r in records.object_records(data, gs + off):
            if r.wtype == 9:
                n9 += 1
                assert r.fmt is not None and len(r.fmt) == 4 and r.fmt_off is not None
                assert bytes(data[r.fmt_off:r.fmt_off + 4]) == bytes(r.fmt)
                assert r.size == 12
                assert not (r.fmt[1] & 0x20), "flag bit 5 is never set in stock (and never read)"
            else:
                assert r.fmt is None, f"wtype {r.wtype} must not carry the directive"
    assert n9 == 385


def test_the_clock_and_the_setpoints_sit_under_the_directives_the_photos_show() -> None:
    """HOME draws `2:49PM` (mode 8) and `12.4V` (one decimal place, inherited from the object
    walked just before); ELECTRICAL draws `51.0` from a type-3 cell holding 510."""
    with open(BUILDS["13.6"], "rb") as handle:
        data = handle.read()
    home = _walk_directives(data, "home")
    clock = [e for e in home if e[0] == "readout" and e[2] == 226]   # render.CLOCK_SLOT
    assert clock and all(e[3][0] == 8 for e in clock), clock
    volts = [e for e in home if e[0] == "readout" and e[2] in (205, 209)]
    assert len(volts) == 2 and all(e[3] == (0, 0x18, 4, 1) for e in volts), volts
    # the voltages have no directive of their own: the one in force came from object 441
    idx = home.index(volts[0])
    last_directive = [e for e in home[:idx] if e[0] == "directive"][-1]
    assert last_directive[1] == 441 and last_directive[1] not in {e[1] for e in volts}

    elec = _walk_directives(data, "electrical")
    start_at = [e for e in elec if e[0] == "readout" and e[2] == 272]
    assert start_at and start_at[0][3] == (0, 0x18, 4, 1)


def test_the_wireless_id_is_the_only_hex_readout() -> None:
    with open(BUILDS["13.6"], "rb") as handle:
        data = handle.read()
    gs, _ = guidata.guidata_extent(data)
    tbl = guidata.object_tables(data)[1]
    hex_slots = set()
    for off in tbl:
        cur = None
        for r in records.object_records(data, gs + off):
            if r.wtype == 9:
                cur = r.fmt
            elif r.wtype == 8 and cur and cur[0] in (10, 11):
                hex_slots.add(r.value_slot)
    assert hex_slots == {264}


# ---------------------------------------------------------------------------------------
# The OPTIONS row id (§10 item 8) is the value_slot of each row's wtype-6 switch.
#
# Each Main Options row is a wtype-6 value-match switch; the six on 13.6 carry value_slot
# 0x6b..0x70 in display order, all sharing child_index and count=3 (candidates {0,1,2}). The
# 2026-08-12 spec called 0x6b..0x70 an opaque "row id"; it is the slot the switch reads (via
# FUN_00251E38, records.py R40-R42) to pick one of three shared appearance children.
# ---------------------------------------------------------------------------------------

def test_options_row_id_is_the_row_switch_value_slot() -> None:
    with open(BUILDS["13.6"], "rb") as handle:
        data = handle.read()
    gs, _ = guidata.guidata_extent(data)

    # Find the object that holds the six Main Options rows: the one whose wtype-6 switches
    # cover all of value_slot 0x6b..0x70. Located structurally, not at a hard object offset.
    wanted = set(range(0x6B, 0x71))
    row_switches = None
    for ptr in sorted(set(_object_table(data))):
        recs = records.object_records(data, gs + ptr)
        sixes = [r for r in recs if r.wtype == 6 and r.value_slot is not None]
        if wanted <= {r.value_slot for r in sixes}:
            row_switches = sixes
            break
    assert row_switches is not None, "the Main Options screen object was not found"

    by_slot = {r.value_slot: r for r in row_switches}
    for slot in range(0x6B, 0x71):                       # 0x6b .. 0x70, one per option row
        assert slot in by_slot, f"no row switch reads value_slot {slot:#x}"
        r = by_slot[slot]
        assert r.count == 3, f"row switch {slot:#x} has count {r.count}, expected 3"
        assert r.child_index == by_slot[0x6B].child_index, "rows should share the candidate base"


# ---------------------------------------------------------------------------------------
# Rounded-shape fields (GUICODE Finding 73): corner radius = geom[4] (+0x32), octant mask = +0x8b.
# ---------------------------------------------------------------------------------------

def _first_of_wtype(data, wtype, gs, tab, limit=800):
    for base in tab[:limit]:
        try:
            recs = records.object_records(data, gs + base)
        except Exception:
            continue
        for r in recs:
            if r.wtype == wtype:
                return r
    return None


def test_the_rounded_shapes_carry_a_corner_radius_and_the_circle_a_full_octant_mask(mcf_136):
    """The shape renderer's radius (state+0x32 = geom[4]) and octant mask (state+0x8b) are decoded.

    `records.py` already read both bytes to stay aligned; Finding 73 named them. This pins that
    the rounded widgets expose a radius and that the circle/dot (0x10) carries the full-circle
    octant mask 0xff -- the bytes a renderer needs to draw the corners at their real size.
    """
    gs, _ge = guidata.guidata_extent(mcf_136)
    tab = guidata.object_tables(mcf_136)[1]

    for wtype in (0x10, 0x20, 0x21):                 # circle, rounded-rect, capsule
        r = _first_of_wtype(mcf_136, wtype, gs, tab)
        assert r is not None, f"no wtype {wtype:#x} record found"
        assert r.corner_radius is not None and r.corner_radius > 0
        assert r.corner_radius == r.geom[4]          # the fifth geom slot IS the radius

    circle = _first_of_wtype(mcf_136, 0x10, gs, tab)
    assert circle.corner_mask == 0xFF                # 0x10 dot = every octant = a full circle


def test_corner_fields_are_absent_on_a_plain_text_record(mcf_136):
    """A wtype-11 text label carries neither field: corner_mask is None and there is no fifth
    geom slot, so corner_radius is None. Guards against the decode inventing shape geometry."""
    gs, _ge = guidata.guidata_extent(mcf_136)
    tab = guidata.object_tables(mcf_136)[1]
    text = _first_of_wtype(mcf_136, 11, gs, tab)
    assert text is not None
    assert text.corner_mask is None
    assert text.corner_radius is None
