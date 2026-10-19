"""Fonts and glyph bitmaps.

The lookup half of this (descriptor -> base index -> glyph table) is `proven-from-code` in
`docs/GUICODE.md` Finding 3. The bitmap half is `inferred-from-data`, so these tests pin
it against things that are checkable without trusting the decoder:

- a **mirror-symmetric glyph** must decode with zero left/right asymmetry. 13.6's slot-24
  `'>'` is a filled ellipse; it reaches exactly 0.0, and only at one (start, width) pair.
- the **font descriptors must partition the glyph table** with nothing left over -- a
  structural property of the table, independent of any bitmap claim.
- the **slot arithmetic** is checked against the two widget bytes whose effect was
  observed on the coach: 13.6's Climate button icon (`0x18`) and caption (`0x0f`).

Every literal here is a measurement. If the decoder drifts, these fail.
"""
from __future__ import annotations

import struct

import pytest

from fireflymcf import glyphs

from .answer_key import SECTION_NAMES


BUILDS = {
    "11.23": "firefly-11.23/mcf.bin",
    "13.6": "firefly-13.6/mcf.bin",
    "14.7": "firefly-14.7 (Terrain)/mcf.bin",
}

# Measured, per build: (glyph table file offset, entry count, font descriptor count).
GLYPH_TABLES = {
    "11.23": (0x090438, 2687, 29),
    "13.6": (0x09E474, 3295, 29),
    "14.7": (0x08E138, 3171, 28),
}

# 13.6 font slots the patched Starlink button uses. `(first, last, base, fallback)`.
SLOT_24_ICONS = (0x3E, 0x5E, 3140, 3173)      # the icon font the Climate button ships with
SLOT_23_ICONS = (0x30, 0x44, 3118, 3139)      # the one holding the signal arcs at '@'
SLOT_15_TEXT = (0x20, 0xFF, 2209, 2433)       # the caption's text font

# Characters in 13.6 slot 24 whose glyph is the shared placeholder -- the filled block the
# panel drew when '[' was put on the button in the v5 probe.
SLOT_24_PLACEHOLDERS = ("?", "@", "A", "K", "Y", "Z", "[", "]")


def load(build: str) -> bytes:
    with open(BUILDS[build], "rb") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def image_136() -> bytes:
    return load("13.6")


@pytest.mark.parametrize("build", sorted(BUILDS))
def test_glyph_table_is_found_by_scanning(build: str) -> None:
    """Located, not hardcoded -- the three builds put it at three different offsets."""
    offset, count = glyphs.find_glyph_table(load(build))
    expected_offset, expected_count, _ = GLYPH_TABLES[build]
    assert (offset, count) == (expected_offset, expected_count)


@pytest.mark.parametrize("build", sorted(BUILDS))
def test_font_count(build: str) -> None:
    assert len(glyphs.fonts(load(build))) == GLYPH_TABLES[build][2]


@pytest.mark.parametrize("build", sorted(BUILDS))
def test_descriptors_partition_the_glyph_table(build: str) -> None:
    """Each font's characters occupy `[base, base+count)`, then one fallback, then the
    next font. Contiguous, no overlap, nothing orphaned -- `GUICODE.md` Finding 3.
    """
    data = load(build)
    fonts = sorted(glyphs.fonts(data), key=lambda font: font.base)
    for lower, upper in zip(fonts, fonts[1:]):
        assert lower.fallback == lower.base + lower.count, (
            f"{build} slot {lower.slot}: fallback is not the entry after its range")
        assert lower.fallback + 1 == upper.base, (
            f"{build} slot {lower.slot} -> {upper.slot}: gap or overlap in the table")


def test_slot_arithmetic_matches_the_bytes_the_panel_honoured(image_136: bytes) -> None:
    """Slots 24 and 15 are what 13.6's Climate button widget names.

    `0x97220` holds `0x18` and `0x97238` holds `0x0f` in stock 13.6. Editing each produced
    the predicted result on the coach, so this arithmetic is load-bearing, not cosmetic.
    """
    icon = glyphs.font_for_slot(image_136, 24)
    caption = glyphs.font_for_slot(image_136, 15)
    assert (icon.first, icon.last, icon.base, icon.fallback) == SLOT_24_ICONS
    assert (caption.first, caption.last, caption.base, caption.fallback) == SLOT_15_TEXT
    # The caption resolving to a full Latin range is the cross-check that the numbering is
    # not off by one: a caption cannot be drawn from a 33-glyph icon font.
    assert caption.last - caption.first + 1 == 224


def test_signal_arc_font_is_slot_23(image_136: bytes) -> None:
    font = glyphs.font_for_slot(image_136, 23)
    assert (font.first, font.last, font.base, font.fallback) == SLOT_23_ICONS
    assert font.covers(ord("@"))


def test_out_of_range_character_returns_the_fallback(image_136: bytes) -> None:
    """The behaviour that produced the filled square in the v5 hardware probe.

    Slot 24 stops at `0x5E`, and `'['` (0x5B) is inside it but maps to the placeholder;
    `0x60` is outside and takes the fallback. Neither fails -- both draw something.
    """
    font = glyphs.font_for_slot(image_136, 24)
    assert not font.covers(0x60)
    assert font.glyph_index(0x60) == font.fallback
    assert font.covers(ord("["))


def test_placeholder_glyphs_share_one_pointer(image_136: bytes) -> None:
    """Eight slot-24 characters resolve to the same tiny record."""
    font = glyphs.font_for_slot(image_136, 24)
    found = [chr(char) for char in range(font.first, font.last + 1)
             if glyphs.glyph_extent(image_136, font.glyph_index(char))[1] < 64]
    assert tuple(found) == SLOT_24_PLACEHOLDERS
    offsets = {glyphs.glyph_extent(image_136, font.glyph_index(ord(char)))[0]
               for char in found}
    assert len(offsets) == 1, "the placeholders should be one shared record"


def test_symmetric_glyph_decodes_with_zero_asymmetry(image_136: bytes) -> None:
    """The measurement that settled the bitmap format.

    Slot 24 `'>'` is a filled ellipse. It reaches exact left/right symmetry only when the
    start and the width are both right -- rendering `stride*2` columns instead of `width`
    moves the mirror axis half a pixel and this stops being 0.

    Height is 48 here, the glyph's declared `header[0xd]`. It was 37 under the old flat reader,
    which counted stored rows off the byte extent; this ellipse *compresses* under the per-row
    RLE (37 stored rows expand to 48 output rows, `GUICODE.md` Finding 22), so 48 is the correct
    full height and 37 was the flat reader truncating. `start` is still 20 = `0xe + ceil(48/8)`.
    """
    font = glyphs.font_for_slot(image_136, 24)
    ellipse = glyphs.decode_glyph(image_136, font.glyph_index(ord(">")))
    assert ellipse.asymmetry() == 0.0
    assert (ellipse.width, ellipse.height, ellipse.start) == (48, 48, 20)


def test_the_start_offset_really_does_vary(image_136: bytes) -> None:
    """19 for the fan, 20 for the ellipse -- same font, one byte apart.

    Recorded because it is the surprising part of the format and the thing that made two
    earlier renderings wrong. No header field identified so far predicts which applies.
    """
    font = glyphs.font_for_slot(image_136, 24)
    fan = glyphs.decode_glyph(image_136, font.glyph_index(ord("E")))
    ellipse = glyphs.decode_glyph(image_136, font.glyph_index(ord(">")))
    assert fan.start == 19
    assert ellipse.start == 20
    assert fan.start != ellipse.start


def test_fan_glyph_is_stable(image_136: bytes) -> None:
    """The Climate button's stock icon, identified by eye against the panel photograph."""
    font = glyphs.font_for_slot(image_136, 24)
    fan = glyphs.decode_glyph(image_136, font.glyph_index(ord("E")))
    assert (fan.index, fan.width, fan.height) == (3147, 38, 38)
    assert fan.ink() == 9623
    assert all(len(row) == fan.width for row in fan.rows)


def test_rows_are_exactly_width_wide(image_136: bytes) -> None:
    """No padding column. Emitting `stride*2` was the bug behind the visible edge stripe."""
    for glyph in glyphs.decode_font(image_136, 24).values():
        assert all(len(row) == glyph.width for row in glyph.rows)
        assert glyph.width % 2 == 1 or glyph.width % 2 == 0     # both occur; stride rounds


def test_decode_font_skips_placeholders(image_136: bytes) -> None:
    font = glyphs.font_for_slot(image_136, 24)
    decoded = glyphs.decode_font(image_136, 24)
    assert len(decoded) == font.count - len(SLOT_24_PLACEHOLDERS)
    assert not set(decoded) & set(SLOT_24_PLACEHOLDERS)


def test_explicit_start_overrides_the_heuristic(image_136: bytes) -> None:
    """The start choice is a heuristic and is documented as one; it must be overridable."""
    font = glyphs.font_for_slot(image_136, 24)
    index = font.glyph_index(ord(">"))
    assert glyphs.decode_glyph(image_136, index, start=19).start == 19
    assert glyphs.decode_glyph(image_136, index, start=19).asymmetry() > 0.0


def test_slot23_icons_start_below_the_old_window(image_136: bytes) -> None:
    """Regression: slot-23 icons start at 17/18, which the old (19, 20) window could not
    reach -- forcing them to a wrong start that rolled every row sideways. The symmetric
    chevrons must now decode at 17 with near-zero asymmetry, and forcing 19 must be worse.
    """
    font = glyphs.font_for_slot(image_136, 23)
    up = glyphs.decode_glyph(image_136, font.glyph_index(ord("5")))     # up chevron
    assert up.start == 17
    assert up.asymmetry() < 1.0
    # the value the old window would have been forced to is visibly wrong (rolled)
    assert glyphs.decode_glyph(image_136, font.glyph_index(ord("5")), start=19
                               ).asymmetry() > 5.0
    snowflake = glyphs.decode_glyph(image_136, font.glyph_index(ord("3")))
    assert snowflake.start == 18                                        # even width -> 18


def test_every_build_has_a_guidata_section() -> None:
    """Guards the assumption `find_glyph_table` rests on."""
    for build in BUILDS:
        assert any("GuiData" in name for name in SECTION_NAMES[build])


# --------------------------------------------------------------------------------------
# Finding 22: the bitmap is 4bpp/1bpp pixel rows under a per-row vertical-repeat RLE
# (blitter FUN_002510C0). The three gates below pin the RLE decoder faithfully -- no fitting,
# structural facts only.
# --------------------------------------------------------------------------------------


def _run_count(data: bytes, off: int, height: int) -> int:
    """Independent replay of the RLE run walk: how many stored pixel rows a glyph has.

    Row r reads stream bit r+1 (bit 0 of byte +0xe skipped); a run of set bits ends on a clear
    bit or the last row. This recomputes the byte budget without calling the decoder, so the
    over-read assertion below has an oracle the decoder cannot satisfy vacuously.
    """
    runs = 0
    r = 0
    while r < height:
        while True:
            bit = (data[off + glyphs.OFF_RLE + ((r + 1) >> 3)] >> ((r + 1) & 7)) & 1
            r += 1
            if bit == 0 or r >= height:
                break
        runs += 1
    return runs


@pytest.mark.parametrize("build", sorted(BUILDS))
def test_no_glyph_over_reads_its_record_and_row_count_is_exact(build: str) -> None:
    """Decode every glyph in every font of every build; two things must hold for each:

    - it yields **exactly** `header[0xd]` output rows (the RLE always sums to `height`), and
    - its pixel footprint (`start + runs * bytes_per_row`) stays **within** its `glyph_extent`.

    The footprint check is the one that catches a wrong bpp: slot 9 forced to 4bpp reads its
    record roughly twice over. Non-vacuous -- both sides are measured from the bytes, and the
    row-count side reads the produced `Glyph.rows`, so a decoder that dropped or padded rows
    (the old flat reader's whole failure) fails immediately.
    """
    data = load(build)
    table = glyphs.resolve_glyph_table(data)
    checked = 0
    for font in glyphs.fonts(data, table=table):
        bpp = glyphs.font_bpp(data, font)
        for char in range(font.first, font.last + 1):
            index = font.glyph_index(char)
            off, extent = glyphs.glyph_extent(data, index, table=table)
            width = data[off + glyphs.OFF_WIDTH]
            height = data[off + glyphs.OFF_HEIGHT]
            if width < 1 or height < 1:
                continue
            g = glyphs.decode_glyph(data, index, table=table, bpp=bpp)
            assert g.height == height, f"{build} slot {font.slot} {char:#x}: {g.height} != {height}"
            assert all(len(row) == width for row in g.rows)
            row_bytes = (width + 1) >> 1 if bpp == 4 else (width + 7) >> 3
            consumed = g.start + _run_count(data, off, height) * row_bytes
            assert consumed <= extent, (
                f"{build} slot {font.slot} {char:#x} bpp{bpp}: reads {consumed} > extent {extent}")
            checked += 1
    assert checked > 2000, "the scan must actually reach thousands of glyphs, not short-circuit"


def test_font13_proof_glyphs_decode_to_correct_shapes(image_136: bytes) -> None:
    """Font 13 (4bpp) `I H T A E x o 0 8 %` -- the dig's validated set -- decode to their real
    shapes. Structural facts, not hardcoded bitmaps: a full-height stem, a hollow ring, a
    zero-mismatch symmetric bar.
    """
    font = glyphs.font_for_slot(image_136, 13)
    assert glyphs.font_bpp(image_136, font) == 4
    g = {ch: glyphs.decode_glyph(image_136, font.glyph_index(ord(ch)))
         for ch in "IHTAExo08%"}

    # all ten decode to their declared height, rows exactly `width` wide
    for ch, gl in g.items():
        assert gl.height == image_136[gl.offset + glyphs.OFF_HEIGHT], ch
        assert all(len(row) == gl.width for row in gl.rows), ch

    # 'I' is a full-height filled stem: a thin glyph, no empty row, every row solidly inked
    stem = g["I"]
    assert stem.width <= 4
    assert all(sum(row) > 0 for row in stem.rows)
    assert min(max(row) for row in stem.rows) >= 0x8

    # 'H' is mirror-symmetric -> exactly zero left/right mismatch (the format's settling test)
    assert g["H"].asymmetry() == 0.0

    # 'o' and '0' are rings: a vertically-central row is inked at both edges, hollow at centre,
    # and the whole glyph is near mirror-symmetric
    for ring in ("o", "0"):
        gl = g[ring]
        mid = gl.rows[gl.height // 2]
        assert mid[0] > 0 and mid[-1] > 0, ring          # both walls present
        assert mid[gl.width // 2] == 0, ring             # hollow interior
        assert gl.asymmetry() < 1.0, ring


def test_value_fonts_decode_including_the_1bpp_tall_font(image_136: bytes) -> None:
    """No-regression: value fonts 9/15/17 decode. Slot 9 (height 47, `font+8 == 1`) is the 1bpp
    tall font whose multi-byte RLE the naive prototype over-segmented; it must now decode its
    digits to full-height hollow rings, no over-read (that is asserted globally above)."""
    for slot, expect_bpp in ((9, 1), (15, 4), (17, 4)):
        font = glyphs.font_for_slot(image_136, slot)
        assert glyphs.font_bpp(image_136, font) == expect_bpp, slot
        decoded = glyphs.decode_font(image_136, slot)
        assert decoded, slot
        for digit in "08":
            gl = decoded[digit]
            assert gl.height == image_136[gl.offset + glyphs.OFF_HEIGHT], (slot, digit)
            assert gl.asymmetry() < 1.0, (slot, digit)          # digits are near-symmetric
        # '0' is a hollow ring at its centre; '8' is pinched (its waist is inked), so only '0'
        zero = decoded["0"]
        assert zero.rows[zero.height // 2][zero.width // 2] == 0, slot


# --------------------------------------------------------------------------------------
# Regression: an earlier id(data)-keyed cache lived in `find_glyph_table`/`glyph_extent` to
# avoid rescanning per character (render.py's proportional-text path). It survived GC id
# reuse (a strong reference to `data` in the cache entry), but NOT in-place mutation of the
# *same* object -- `data[i] = ...` on a bytearray leaves `id(data)` unchanged, so a second
# call would keep returning the stale table. That is exactly the shape of trap CLAUDE.md's
# stale-bytecode-after-same-length-edit note warns about. The fix removed the cross-call
# cache entirely: `find_glyph_table` always re-scans, and a caller that wants to avoid
# rescanning resolves a `GlyphTable` once (`resolve_glyph_table`) and threads it through
# explicitly for one render pass -- there is no hidden identity-based cache to go stale.
# --------------------------------------------------------------------------------------

def test_find_glyph_table_is_not_stale_after_mutating_the_same_object(image_136: bytes) -> None:
    """Reproduces the reviewer's repro directly: locate the table, corrupt it in place on the
    SAME object (`id(data)` unchanged -- a bytearray mutation, not a new object), and confirm
    the next call reflects that, not a cached, stale result.

    A hidden id(data)-keyed cache (the bug this pins) would return the pre-corruption
    `(off, count)` unchanged here, since nothing about `data`'s identity or the cache key
    changed -- only its bytes did. The corpus turns out to have more than one long run of
    QSPI-range pointers, so erasing the biggest one doesn't raise (a smaller, real run
    elsewhere becomes the new "longest") -- which is actually a stronger demonstration than a
    raise would be: the result provably comes from the CURRENT bytes, not anything remembered.
    """
    data = bytearray(image_136)
    off, count = glyphs.find_glyph_table(data)              # locate it once, on THIS object
    for i in range(off, off + count * 4):                  # corrupt the whole run, same object
        data[i] = 0
    new_off, new_count = glyphs.find_glyph_table(data)      # must re-scan, not return (off, count)
    assert (new_off, new_count) != (off, count)


def test_find_glyph_table_finds_a_run_written_after_the_original_is_erased(
        image_136: bytes) -> None:
    """Complements the test above with a positive, controlled case: erase the original table
    (same object) and write a deliberately huge new run of QSPI pointers elsewhere; the huge
    run must win and be reported at its new location -- proving the scan genuinely re-reads
    `data`'s current contents rather than serving anything computed on an earlier call."""
    data = bytearray(image_136)
    off, count = glyphs.find_glyph_table(data)               # locate it once, on THIS object
    for i in range(off, off + count * 4):                   # erase the original run
        data[i] = 0
    # a run bigger than anything that can occur naturally in this corpus (GLYPH_TABLES' widest
    # entry is under 3300) guarantees it becomes the new longest run, wherever it's placed.
    new_off = off + count * 4 + 4096
    new_count = 8000
    for i in range(new_count):
        struct.pack_into("<I", data, new_off + i * 4, glyphs.QSPI_BASE + (i % 4) * 4)
    found_off, found_count = glyphs.find_glyph_table(data, min_run=200)
    assert (found_off, found_count) == (new_off, new_count)


def test_resolve_glyph_table_matches_the_uncached_per_call_lookup(image_136: bytes) -> None:
    """`GlyphTable` (used to avoid rescanning across many characters in one render pass) must
    be equivalent to, not just faster than, the plain per-call path: same table location,
    same sorted address list, and passing it through `glyph_extent`/`font_for_slot`/
    `decode_glyph` must produce byte-identical results to leaving `table=None`."""
    table = glyphs.resolve_glyph_table(image_136)
    off, count = glyphs.find_glyph_table(image_136)
    assert (table.offset, table.entries) == (off, count)

    font_cached = glyphs.font_for_slot(image_136, 24, table=table)
    font_fresh = glyphs.font_for_slot(image_136, 24)
    assert font_cached == font_fresh

    idx = font_cached.glyph_index(ord("E"))
    assert (glyphs.glyph_extent(image_136, idx, table=table)
            == glyphs.glyph_extent(image_136, idx))
    assert (glyphs.decode_glyph(image_136, idx, table=table)
            == glyphs.decode_glyph(image_136, idx))
