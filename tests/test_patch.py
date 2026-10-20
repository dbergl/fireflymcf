"""The MCF writer: same-size edits, resealing, and the verification ladder.

These tests are written to fail if the writer stops guaranteeing what it claims, not merely
to exercise it. The invariant tests (I1-I3) are as load-bearing as the happy path -- a
patcher whose refusals stop working is more dangerous than one that does nothing, because
its output goes onto hardware.

`"Heat Pump"` is a 13.6 string, so tests naming it are 13.6-only. Everything that is a
property of the *format* rather than of one build is parameterised over all three.
"""
from __future__ import annotations

import pytest

from fireflymcf import coverage, guidata, patch, theme

ALL_BUILDS = ("11.23", "13.6", "14.7")

# The OPTIONS-screen occurrence in 13.6 -- the one the panel shows on the screen the owner
# reaches, established in test_options_screen_site_is_unique below rather than assumed here.
HEAT_PUMP_OPTIONS_SITE = 0x099FA1
HEAT_PUMP_SITE_COUNT_136 = 7


# --------------------------------------------------------------------------- checksums


@pytest.mark.parametrize("version", ALL_BUILDS)
def test_checksum_fields_match_the_coverage_pass(corpus, version):
    """`patch.checksum_fields` duplicates `coverage.analyse`'s arithmetic; pin them together.

    The duplication is deliberate (the writer must enumerate fields without running a full
    coverage pass) and this is the test that stops the two drifting.
    """
    data = corpus[version]["mcf"]
    report = coverage.analyse(data)
    claimed = {(start, label) for start, length, label in report.spans
               if "checksum" in label}
    ours = {(f.position, f.label) for f in patch.checksum_fields(data)}
    assert ours == claimed


@pytest.mark.parametrize("version", ALL_BUILDS)
def test_every_stored_checksum_already_verifies(corpus, version):
    """The corpus is self-consistent, so a reseal of an untouched file is a no-op."""
    data = corpus[version]["mcf"]
    fields = patch.checksum_fields(data)
    assert len(fields) == 9, "1 header + 4 descriptor + 4 payload"
    for f in fields:
        assert f.stored(data) == f.computed(data), f.label


@pytest.mark.parametrize("version", ALL_BUILDS)
def test_reseal_of_an_untouched_image_changes_nothing(corpus, version):
    data = corpus[version]["mcf"]
    out, changed = patch.reseal(data)
    assert changed == []
    assert out == data


@pytest.mark.parametrize("version", ALL_BUILDS)
def test_reseal_repairs_a_deliberately_broken_checksum(corpus, version):
    """Prove reseal actually computes rather than copying the stored word through."""
    data = bytearray(corpus[version]["mcf"])
    field = next(f for f in patch.checksum_fields(bytes(data))
                 if f.label.startswith("section-checksum"))
    data[field.position] ^= 0xFF
    out, changed = patch.reseal(bytes(data))
    assert field.label in changed
    assert out == corpus[version]["mcf"]


# ------------------------------------------------------------------------- label sites


def test_find_label_sites_finds_every_heat_pump_record(mcf_136):
    sites = patch.find_label_sites(mcf_136, "Heat Pump")
    assert len(sites) == HEAT_PUMP_SITE_COUNT_136
    assert [s.text for s in sites] == [
        "    Heat Pump", "    Heat Pump", "Remove\nHeat Pump", "Add\nHeat Pump",
        "Heat Pump", "Heat Pump", "   Heat Pump"]


@pytest.mark.parametrize("version", ALL_BUILDS)
@pytest.mark.parametrize("text", ["OPTIONS", "Water Pump"])
def test_located_sites_are_well_formed_label_records(corpus, version, text):
    """A format property, so it holds for any build and any text that occurs at all.

    Each site must be NUL-terminated at exactly `length`, and its field must reproduce
    the bytes the record describes.
    """
    data = corpus[version]["mcf"]
    sites = patch.find_label_sites(data, text)
    for s in sites:
        assert data[s.length_offset] == s.length
        assert data[s.text_offset:s.text_offset + s.length].decode("ascii") == s.text
        assert data[s.text_offset + s.length] == 0x00
        assert text in s.text


@pytest.mark.parametrize("version", ALL_BUILDS)
def test_find_label_sites_is_confined_to_guidata(corpus, version):
    data = corpus[version]["mcf"]
    start, end = guidata.guidata_extent(data)
    for s in patch.find_label_sites(data, "OPTIONS"):
        assert start <= s.length_offset and s.end <= end


def test_absent_text_yields_no_sites(mcf_136):
    assert patch.find_label_sites(mcf_136, "Nuclear Reactor") == []


def test_options_screen_site_is_unique(mcf_136):
    """Exactly one 'Heat Pump' record sits on a decodable screen, and it is an OPTIONS row.

    This is what makes `--on-screen OPTIONS` a safe way to name the site the owner sees,
    and it is the fact the dry-run patch depends on.
    """
    sites = patch.find_label_sites(mcf_136, "Heat Pump")
    screens = list(guidata.iter_screens(mcf_136))
    on_screen = [(s, scr) for s in sites for scr in screens
                 if scr.start <= s.text_offset < scr.end]
    assert len(on_screen) == 1
    site, screen = on_screen[0]
    assert site.text_offset == HEAT_PUMP_OPTIONS_SITE
    assert screen.title.strip() == "OPTIONS"
    assert [w.label for w in screen.widgets if w.offset == site.text_offset] \
        == ["   Heat Pump"]


# ------------------------------------------------------------------------------ repad


@pytest.mark.parametrize("field,new,length,expected", [
    ("   Heat Pump", "12v aux", 12, "   12v aux  "),
    ("    Heat Pump", "12v aux", 13, "    12v aux  "),
    ("Heat Pump", "12v aux", 9, "12v aux  "),
    ("Add\nHeat Pump", "12v aux", 13, "Add\n12v aux  "),
    ("Remove\nHeat Pump", "12v aux", 16, "Remove\n12v aux  "),
    ("Heat Pump", "Heat Pumps", 9, None),          # one byte too long, no indent to give
    ("   Heat Pump", "Heat Pump", 12, "   Heat Pump"),   # identity
])
def test_repad(field, new, length, expected):
    if expected is None:
        with pytest.raises(patch.PatchError, match="more"):
            patch.repad(field, "Heat Pump", new, length)
        return
    out = patch.repad(field, "Heat Pump", new, length)
    assert out == expected
    assert len(out) == length


def test_repad_preserves_the_indent_the_panel_draws(mcf_136):
    """The dry run showed the renderer reproduces leading spaces literally, so a rename
    must not change them -- left-padding moved the OPTIONS row two characters right."""
    out = patch.repad("   Heat Pump", "Heat Pump", "12v aux", 12)
    assert out.startswith("   12v aux")
    assert len(out) - len(out.rstrip(" ")) == 2, "slack goes on the right"


def test_repad_left_alignment_is_still_available():
    assert patch.repad("   Heat Pump", "Heat Pump", "12v aux", 12, "left") == "     12v aux"


def test_repad_rejects_an_unknown_alignment():
    # "centre" used to be the example of an invalid value here; it is a real mode now, so
    # this needs a value that is genuinely not one.
    with pytest.raises(patch.PatchError, match="align"):
        patch.repad("   Heat Pump", "Heat Pump", "12v aux", 12, "middle")


def test_repad_centre_splits_the_pad_around_the_last_line():
    """A centre-aligned record centres each line by the width of the string it was GIVEN.

    So trailing pad is not invisible there the way it is on a left-aligned label: it drags the
    visible text left by half the pad. Owner-observed on the coach 2026-08-21 -- the caption
    'Overflow\nValve   ' (padded to the 17-byte donor field) drew its second line 9 px left of
    centre, beside a 'Water\nPump' that looked right because it needs no padding at all.

    Splitting the pad puts the visible word back within half a space of centre: measured against
    `metrics.text_width`, '  Valve ' lands the word at 38 px against a true centre of 37.
    """
    out = patch.repad("Fresh Tank Heater", "Fresh Tank Heater", "Overflow\nValve", 17, "center")
    assert out == "Overflow\n  Valve "
    assert len(out) == 17


def test_repad_replaces_text_that_spans_lines():
    """`old` may cross a newline: 'Water\nPump' -> 'Pump' is the agent guide's own example, and
    the line-by-line search raised StopIteration on it. The lines `old` spans are one unit."""
    assert patch.repad("Water\nPump", "Water\nPump", "Pump", 10, "center") == "   Pump   "
    assert patch.repad("Water\nPump", "Water\nPump", "Pump", 10) == "Pump      "
    assert patch.repad("A\nWater\nPump", "Water\nPump", "Pump", 12) == "A\nPump      "
    assert patch.repad("Water\nPump", "r\nP", "r P", 10) == "Water Pump"


def test_repad_centre_pads_a_single_line_on_both_sides():
    out = patch.repad("Fresh Tank Heater", "Fresh Tank Heater", "Valve", 17, "center")
    assert out == "      Valve      "
    assert len(out) == 17


def test_repad_centre_puts_the_odd_space_on_the_left():
    """An odd pad cannot split evenly, and which side takes the extra is not arbitrary: the
    leading run is measured with the string's own start kerning, so leading-heavy lands closer.
    For 'Valve' in font 13 the two options are 38 px and 32 px against a centre of 37."""
    out = patch.repad("Fresh Tank Heater", "Fresh Tank Heater", "Valve", 12, "center")
    assert out == "    Valve   ", out          # 7 spaces -> 4 leading, 3 trailing


def test_repad_accepts_both_spellings_of_centre():
    a = patch.repad("Fresh Tank Heater", "Fresh Tank Heater", "Valve", 17, "center")
    b = patch.repad("Fresh Tank Heater", "Fresh Tank Heater", "Valve", 17, "centre")
    assert a == b


def test_repad_pads_only_the_line_that_changed():
    """The other line's alignment must survive, or a two-line label shifts on screen."""
    field = "  Add\n   Heat Pump"
    out = patch.repad(field, "Heat Pump", "aux", len(field))
    assert out.split("\n")[0] == "  Add"
    assert out.split("\n")[1] == "   aux      "
    assert len(out) == len(field)


@pytest.mark.parametrize("new,expected", [
    ("Heat Pumps", "  Heat Pumps"),        # eats one space of the three
    ("Aux Load 12v", "Aux Load 12v"),      # eats all three, zero indent left
])
def test_repad_consumes_indent_for_a_longer_name(new, expected):
    """A longer name may eat leading spaces -- the field length is what must not change."""
    out = patch.repad("   Heat Pump", "Heat Pump", new, 12)
    assert out == expected and len(out) == 12


def test_repad_refuses_to_eat_more_indent_than_exists():
    with pytest.raises(patch.PatchError, match="indent"):
        patch.repad("   Heat Pump", "Heat Pump", "Heat Pump Long", 12)


def test_repad_rejects_text_that_is_not_there():
    with pytest.raises(patch.PatchError, match="does not occur"):
        patch.repad("   Bath Fan", "Heat Pump", "12v aux", 12)


# ---------------------------------------------------------------------------- the patch


@pytest.fixture
def options_rename(mcf_136):
    """The dry-run patch: rename the OPTIONS-screen 'Heat Pump' and reseal."""
    site = next(s for s in patch.find_label_sites(mcf_136, "Heat Pump")
                if s.text_offset == HEAT_PUMP_OPTIONS_SITE)
    p = patch.McfPatcher(mcf_136)
    p.set_label(site, "Heat Pump", "12v aux")
    out, changed = p.build()
    return p, out, changed


def test_patch_does_not_mutate_its_input(mcf_136, options_rename):
    _, out, _ = options_rename
    assert out is not mcf_136
    assert mcf_136[HEAT_PUMP_OPTIONS_SITE:HEAT_PUMP_OPTIONS_SITE + 12] == b"   Heat Pump"


def test_patch_resesals_exactly_the_guidata_payload(options_rename):
    """A GuiData edit must touch one checksum. If it touched more, the geometry is wrong;
    if fewer, nothing was written."""
    _, _, changed = options_rename
    assert changed == ["section-checksum:Lyra 13v6 GuiData"]


def test_patch_is_visible_to_the_ordinary_reader(options_rename):
    _, out, _ = options_rename
    labels = [w.label for w in guidata.find_screens_by_title(out, "OPTIONS")[1].widgets]
    assert labels[0] == "   12v aux  "
    assert "   Heat Pump" not in labels


def test_patch_leaves_the_other_six_sites_alone(mcf_136, options_rename):
    """--on-screen names one site; the rest of the build must be untouched."""
    _, out, _ = options_rename
    assert len(patch.find_label_sites(out, "Heat Pump")) == HEAT_PUMP_SITE_COUNT_136 - 1


def test_patched_image_still_covers_every_byte(options_rename):
    _, out, _ = options_rename
    report = coverage.analyse(out, deep=True)
    assert report.claimed == report.total
    assert report.unclaimed == []


def test_the_full_ladder_passes(mcf_136, options_rename):
    p, out, changed = options_rename
    lines = patch.verify(mcf_136, out, p.edits(), changed)
    assert [line[:2] for line in lines] == ["L1", "L2", "L3", "L4", "L5"]


def test_round_trip_restores_the_original(mcf_136, options_rename):
    """L4 on its own, because it is the strongest cheap invariant the writer has.

    Deliberately routed through the public `find_label_sites` / `set_label` path rather
    than the raw byte replacement `verify` uses, so it also proves a rename is invertible
    through the ordinary interface.
    """
    _, out, _ = options_rename
    site = next(s for s in patch.find_label_sites(out, "12v aux")
                if s.text_offset == HEAT_PUMP_OPTIONS_SITE)
    back = patch.McfPatcher(out)
    back.set_label(site, "12v aux", "Heat Pump")
    restored, changed = back.build()
    assert changed == ["section-checksum:Lyra 13v6 GuiData"]
    assert restored == mcf_136


def test_edit_records_the_structure_it_lands_in(options_rename):
    p, _, _ = options_rename
    assert p.edits()[0].structure.startswith("object:")


# ------------------------------------------------------------- invariants, which must bite


def test_i1_a_length_change_is_refused(mcf_136):
    p = patch.McfPatcher(mcf_136)
    with pytest.raises(patch.PatchError, match="same-size"):
        p._add(HEAT_PUMP_OPTIONS_SITE, b"   Heat Pump", b"short", "shrink")


def test_i1_reached_through_set_label(mcf_136):
    """A replacement longer than the field must be refused, not truncated."""
    site = next(s for s in patch.find_label_sites(mcf_136, "Heat Pump")
                if s.text_offset == HEAT_PUMP_OPTIONS_SITE)
    p = patch.McfPatcher(mcf_136)
    with pytest.raises(patch.PatchError, match="more"):
        p.set_label(site, "Heat Pump", "Extremely Long Replacement Name")


def test_i2_overlapping_edits_are_refused(mcf_136):
    p = patch.McfPatcher(mcf_136)
    p._add(HEAT_PUMP_OPTIONS_SITE, b"   Heat Pump", b"     12v aux", "first")
    with pytest.raises(patch.PatchError, match="overlaps"):
        p._add(HEAT_PUMP_OPTIONS_SITE + 4, b"eat P", b"XXXXX", "second")


def test_i3_an_edit_into_a_checksum_field_is_refused(mcf_136):
    field = next(f for f in patch.checksum_fields(mcf_136)
                 if f.label.startswith("section-checksum"))
    p = patch.McfPatcher(mcf_136)
    old = mcf_136[field.position:field.position + 2]
    with pytest.raises(patch.PatchError, match="checksum"):
        p._add(field.position, old, bytes(2), "into a checksum")


def test_i3_an_edit_into_padding_is_refused(mcf_136):
    report = coverage.analyse(mcf_136, deep=True)
    start, length, _ = next(s for s in report.spans if s[2] == "padding")
    p = patch.McfPatcher(mcf_136)
    with pytest.raises(patch.PatchError, match="padding"):
        p._add(start, mcf_136[start:start + 1], b"\x01", "into padding")


def test_a_stale_expectation_is_refused(mcf_136):
    """If the image does not hold what the caller thinks, the offset is wrong."""
    p = patch.McfPatcher(mcf_136)
    with pytest.raises(patch.PatchError, match="expected"):
        p._add(HEAT_PUMP_OPTIONS_SITE, b"   Bath Fan!", b"     12v aux", "wrong base")


def test_building_with_no_edits_is_refused(mcf_136):
    with pytest.raises(patch.PatchError, match="no edits"):
        patch.McfPatcher(mcf_136).build()


def test_set_channel_is_implemented(mcf_136):
    """This test used to assert the opposite, and its job was to fail when Phase 2 landed.

    It did. Kept, inverted, as the marker that the guard was removed on purpose rather
    than lost -- the behaviour itself is covered by `tests/test_set_channel.py`.
    """
    from fireflymcf import guidata
    control = [c for c in guidata.find_control_groups(mcf_136) if c.group == 40][0]
    p = patch.McfPatcher(mcf_136)
    p.set_channel(control, 38, move_status=False)
    assert len(p.edits()) == 2


# ------------------------------------------------------------------ the ladder can fail


def test_l3_catches_a_byte_changed_outside_the_edits(mcf_136, options_rename):
    """The rung that matters most: corrupt an unrelated byte, reseal, and it must fail.

    L1 would pass this happily -- the checksums are all consistent -- which is exactly
    why the ladder does not stop at L1.
    """
    p, out, _ = options_rename
    tampered = bytearray(out)
    victim = HEAT_PUMP_OPTIONS_SITE + 0x400
    tampered[victim] ^= 0x20
    resealed, changed = patch.reseal(bytes(tampered))
    assert coverage.analyse(resealed, deep=True).unclaimed == []   # L1 is satisfied
    with pytest.raises(patch.PatchError, match="L3"):
        patch.verify(mcf_136, resealed, p.edits(), changed)


def test_l5_catches_an_unresealed_checksum(mcf_136, options_rename):
    p, out, changed = options_rename
    broken = bytearray(out)
    field = next(f for f in patch.checksum_fields(out) if f.label == "header-checksum")
    broken[field.position] ^= 0xFF
    with pytest.raises(patch.PatchError):
        patch.verify(mcf_136, bytes(broken), p.edits(), changed)


def test_diff_ranges_reports_maximal_runs():
    assert patch.diff_ranges(b"abcdef", b"abcdef") == []
    assert patch.diff_ranges(b"abcdef", b"aXcdef") == [(1, 1)]
    assert patch.diff_ranges(b"abcdef", b"aXYdef") == [(1, 2)]
    assert patch.diff_ranges(b"abcdef", b"aXcYef") == [(1, 1), (3, 1)]
    assert patch.diff_ranges(b"abcdef", b"abcdeX") == [(5, 1)]


def test_diff_ranges_rejects_a_length_change():
    with pytest.raises(patch.PatchError, match="length"):
        patch.diff_ranges(b"abc", b"abcd")


# --------------------------------------------------------------- recolour a button (set_fill_color)

def test_find_button_box_locates_the_wt33_fill_and_rejects_bad_index(mcf_136):
    """`find_button_box` returns the record whose fillA is a mode-7 index -- object 250's wt-33
    'Start' box (fillA 145) in stock 13.6 -- and refuses an out-of-range object index."""
    box = patch.find_button_box(mcf_136, 250, "fillA")
    assert box.wtype == 33
    assert any(c[0] == "fillA" and c[1] == 7 and c[2] == 145 for c in box.colors)
    with pytest.raises(patch.PatchError):
        patch.find_button_box(mcf_136, 10_000_000, "fillA")


def test_set_fill_color_recolours_and_passes_the_ladder(mcf_136):
    """Recolouring object 250's box to the button blue (65,81,163) is a single same-size u16 edit
    that reseals exactly the GuiData section checksum and passes the full L1-L5 verification
    ladder. This is the whole recolour capability, proven end to end."""
    from fireflymcf import theme, records
    box = patch.find_button_box(mcf_136, 250, "fillA")
    blue = theme.resolve_fill_index(mcf_136, color=(65, 81, 163))   # a static blue index

    p = patch.McfPatcher(mcf_136)
    p.set_fill_color(box, color=(65, 81, 163))
    edits = p.edits()
    assert len(edits) == 1 and len(edits[0].old) == len(edits[0].new) == 2   # same-size u16
    out, changed = p.build()
    assert len(out) == len(mcf_136)
    assert changed == ["section-checksum:Lyra 13v6 GuiData"]     # exactly the one section

    # the fillA index is now the resolved blue, read back through the decoder
    box2 = patch.find_button_box(out, 250, "fillA")
    off = records.color_offset(out, box2, "fillA")
    assert out[off] | (out[off + 1] << 8) == blue

    # the writer's own ladder accepts it (L1 coverage .. L5 updater checks)
    lines = patch.verify(mcf_136, out, edits, changed)
    assert any(line.startswith("L1") for line in lines)


def test_set_fill_color_refuses_an_off_palette_colour(mcf_136):
    """The firmware rule: a colour the palette cannot name is refused, so no undefined fill is
    ever written. `_add` never runs, so no edit is recorded."""
    box = patch.find_button_box(mcf_136, 250, "fillA")
    p = patch.McfPatcher(mcf_136)
    with pytest.raises(theme.PaletteError):
        p.set_fill_color(box, color=(1, 2, 3))
    assert p.edits() == []


# ------------------------------------------------------------------ build name (flasher menu)

def test_build_name_reads_and_sets_and_reseals_only_the_header(mcf_136):
    """The flasher build name round-trips through set_build_name, changing exactly the header
    checksum (the field lives in the header structure) and no file length."""
    assert patch.build_name(mcf_136) == "Jayco B-Van V13"
    out, changed = patch.set_build_name(mcf_136, "Test Build 42")
    assert patch.build_name(out) == "Test Build 42"
    assert len(out) == len(mcf_136)                       # length-invariant
    assert changed == ["header-checksum"]                 # only the header checksum moved
    # the stored checksums all verify on the renamed image (non-vacuity: it is a valid MCF)
    for f in patch.checksum_fields(out):
        pass
    coverage.analyse(out, deep=True)


def test_mark_custom_is_idempotent(mcf_136):
    """mark_custom appends '(Custom)' once; re-marking an already-marked build is a no-op (returns
    the same object), so re-patching never stacks the suffix."""
    marked = patch.mark_custom(mcf_136)
    assert patch.build_name(marked) == "Jayco B-Van V13 (Custom)"
    again = patch.mark_custom(marked)
    assert again is marked                                # unchanged: no double-append
    assert patch.build_name(again) == "Jayco B-Van V13 (Custom)"


def test_set_build_name_rejects_an_oversized_name(mcf_136):
    """A name that will not fit the fixed field is refused, never truncated onto the section
    directory that follows it."""
    with pytest.raises(patch.PatchError):
        patch.set_build_name(mcf_136, "X" * patch.BUILD_NAME_CAPACITY)


@pytest.mark.parametrize("version", ALL_BUILDS)
def test_same_size_rename_and_full_ladder_are_build_general(corpus, version):
    """A same-size rename acts on a *found* label, not a fixed index, so it works on any build.
    Prove it end to end -- find 'HELP' (a page title present on every corpus build), rename it to
    the same-length 'INFO', and require the full five-rung verification ladder to pass -- on 11.23,
    13.6 and 14.7 alike. This is the evidence behind GETTING-STARTED's 'same-size edits are
    build-general' claim."""
    data = corpus[version]["mcf"]
    sites = patch.find_label_sites(data, "HELP")
    assert sites, f"'HELP' not found on {version}"
    p = patch.McfPatcher(data)
    for s in sites:
        p.set_label(s, "HELP", "INFO")
    out, changed = p.build()
    assert len(out) == len(data)                         # same-size: nothing moved
    lines = patch.verify(data, out, p.edits(), changed)
    assert [line[:2] for line in lines] == ["L1", "L2", "L3", "L4", "L5"]
    assert patch.find_label_sites(out, "INFO")           # the new label is present and re-readable


def test_label_sites_break_a_nested_tie_with_the_record_decoder(mcf_136):
    """'12 - PORCH' (13.6, 0x8c8d5) has two well-formed length bytes ending at one NUL: the
    real one (0x0a, 10) and the byte before it (0x0b, 11), which also qualifies because 0x0a
    is a newline, a label byte. The record that holds it decodes its length at 0x8c8cf, so
    that wins; a tie no decoded record settles still raises."""
    sites = patch.find_label_sites(mcf_136, "PORCH")
    assert [(s.length_offset, s.text) for s in sites] == [
        (0x8c8cf, "12 - PORCH"), (0x8d34e, "12 - PORCH"), (0x8dd81, "12 - PORCH")]
