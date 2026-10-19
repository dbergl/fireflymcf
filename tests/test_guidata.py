"""GuiData screen and widget records, anchored on the subject coach's OPTIONS screen.

Everything asserted here is inferred from the bytes of `firefly-13.6/mcf.bin`, not
proven from parsing code: Task 6 established that cli.bin only transports the GuiData
payload, so no located code reads these structures. What makes the inference worth
trusting is the anchor -- `answer_key.OPTIONS` was read off the physical coach, and the
parse has to reproduce it exactly, in order, from a record walk that never looks at the
label text.

Two of the brief's premises are contradicted by the data and are replaced below:

  * "(CH. 25) is a continuation line bound to the entry above it" -- it is not a record
    at all. Labels are length-prefixed and may contain newlines; "(CH. 25)" is the
    second line of the "Cargo / Bath Light" label. See test_ch25_is_part_of_the_label.
  * "records are fixed-size, at a ~0x49 stride" -- they are not. The stride varies
    (0x48..0x5f on this screen) and the variation is exactly the label length. See
    test_label_spacing_varies_only_by_the_label_length.

A third premise -- that a title identifies a screen -- is also wrong on this corpus:
the Main Options and Electrical Options tabs render two different screens under the
same "   OPTIONS" header. The tests below pin both.
"""
import struct

import pytest

from tests import answer_key
from fireflymcf import guidata, layout, records


@pytest.fixture
def options_screen(mcf_136):
    """The coach's OPTIONS screen: the second of the two screens headed "OPTIONS"."""
    return guidata.find_screens_by_title(mcf_136, "OPTIONS")[1]


@pytest.fixture
def electrical_screen(mcf_136):
    """The Electrical Options screen: the first of the two headed "OPTIONS"."""
    return guidata.find_screens_by_title(mcf_136, "OPTIONS")[0]


def test_finds_the_options_screen(mcf_136):
    """Two screens carry the "OPTIONS" header, so the title alone does not identify a
    screen. Both are pinned by title offset: the Electrical Options tab first, then the
    coach's OPTIONS screen at the answer key's offset."""
    screens = guidata.find_screens_by_title(mcf_136, "OPTIONS")
    assert [s.offset for s in screens] == [0x099C85, 0x099F3D]
    assert screens[1].offset == answer_key.OPTIONS_SCREEN_136[0][0]
    assert screens[1].title == "   OPTIONS"


def test_options_screen_reproduces_the_coach_exactly(options_screen):
    """The strongest semantic check available: this list was read off the physical
    coach. Order matters, and it is produced by walking records -- the parser never
    matches on label text.

    The answer key names the entry; one label carries a second line ("(CH. 25)") that
    the coach renders under it, so the comparison is against each label's first line.
    """
    entries = [w.label.splitlines()[0].strip() for w in options_screen.widgets]
    assert entries == [name for name, _ in answer_key.OPTIONS]


def test_ch25_is_part_of_the_cargo_bath_light_label(options_screen):
    """The brief's premise was wrong. "(CH. 25)" is not a separate record with a
    continuation flag: labels are length-prefixed and may contain newlines, and this
    label is one 34-byte string holding both lines.

    The widget is selected by position in the coach's list, not by matching its text.
    """
    index = [name for name, _ in answer_key.OPTIONS].index("Cargo / Bath Light")
    widget = options_screen.widgets[index]

    assert widget.label == "   Cargo / Bath Light\n    (CH. 25)"
    assert widget.label.splitlines() == ["   Cargo / Bath Light", "    (CH. 25)"]
    assert widget.length_prefix == len(widget.label) == 34

    # The answer key's "(CH. 25)" offset is a position inside this one label, not the
    # start of anything.
    ch25_offset, ch25_text = answer_key.OPTIONS_SCREEN_136[5]
    assert ch25_text == "    (CH. 25)"
    assert widget.offset < ch25_offset < widget.offset + len(widget.label)
    assert not any(w.offset == ch25_offset for w in options_screen.widgets)
    assert not any("(CH. 25)" == w.label.strip() for w in options_screen.widgets)

    # And no widget on this screen is a continuation: the format has no such thing.
    assert [w.is_continuation for w in options_screen.widgets] == [False] * 6


def test_every_label_is_length_prefixed(options_screen, electrical_screen):
    """The byte at offset-1 is the label length, excluding the NUL terminator. This is
    what makes a newline inside a label unambiguous."""
    for screen, expected in ((options_screen, 6), (electrical_screen, 4)):
        assert screen.title_length_prefix == len(screen.title)
        assert len(screen.widgets) == expected
        for widget in screen.widgets:
            assert widget.length_prefix == len(widget.label), widget


def test_label_spacing_varies_only_by_the_label_length(options_screen):
    """The brief's "fixed-size record at a ~0x49 stride" is wrong: the gaps between
    consecutive labels on this screen are 0x49, 0x4b, 0x48, 0x5f, 0x4a. What is
    constant is gap minus label length -- the records are variable-length and the
    variation is entirely the string."""
    offsets = [w.offset for w in options_screen.widgets]
    gaps = [b - a for a, b in zip(offsets, offsets[1:])]
    assert gaps == [0x49, 0x4B, 0x48, 0x5F, 0x4A]

    fixed = {gap - len(w.label) for gap, w in zip(gaps, options_screen.widgets)}
    assert fixed == {61}


def test_known_label_offsets_are_all_accounted_for(options_screen, mcf_136):
    """Every offset the answer key records for this screen is explained by the parse:
    as the title, as a widget label, or as a position inside one."""
    spans = [(options_screen.offset, options_screen.title)]
    spans += [(w.offset, w.label) for w in options_screen.widgets]

    for offset, text in answer_key.OPTIONS_SCREEN_136:
        assert mcf_136[offset:offset + len(text)].decode("ascii") == text
        covered = any(start <= offset < start + len(label) for start, label in spans)
        assert covered, f"answer key offset {offset:#x} ({text!r}) is not in the parse"


def test_electrical_options_screen_also_parses(electrical_screen):
    """One working screen could be a coincidence; two is a format. The Electrical
    Options screen sits immediately above OPTIONS and uses the same record types."""
    labels = [w.label for w in electrical_screen.widgets]
    assert labels == answer_key.ELECTRICAL_OPTIONS_136


def test_the_two_options_screens_are_adjacent_and_distinct(options_screen,
                                                           electrical_screen):
    """They are consecutive objects in the display list -- the Electrical Options
    screen's records end exactly where the OPTIONS screen's record count begins."""
    assert electrical_screen.end == options_screen.start
    assert electrical_screen.record_count == 31
    assert options_screen.record_count == 36
    electrical = {w.label for w in electrical_screen.widgets}
    options = {w.label for w in options_screen.widgets}
    assert len(electrical) == 4 and len(options) == 6
    assert not electrical & options


def test_a_screen_walks_to_a_four_byte_boundary(options_screen, mcf_136):
    """The screen container: a u8 record count, then exactly that many records, then
    zero padding to the next 4-byte boundary. A wrong record size shows up here as
    non-zero padding or a misaligned end."""
    assert mcf_136[options_screen.start] == options_screen.record_count
    assert options_screen.end % 4 == 0
    assert set(mcf_136[options_screen.records_end:options_screen.end]) <= {0}
    assert 0 <= options_screen.end - options_screen.records_end < 4


def test_parse_widgets_is_driven_by_the_record_count(mcf_136, options_screen):
    """parse_widgets walks records, not text. Truncating the count truncates the list
    at a record boundary rather than mangling it."""
    start = options_screen.start + 1
    assert guidata.parse_widgets(mcf_136, start, options_screen.record_count) == \
        options_screen.widgets
    # Records 0..8 cover the two dividers, the title, three list-chrome records, and
    # the first row triple: exactly one widget.
    assert [w.label for w in guidata.parse_widgets(mcf_136, start, 9)] == \
        ["   Heat Pump"]
    assert guidata.parse_widgets(mcf_136, start, 0) == []


def test_parse_widgets_raises_on_an_unknown_record(mcf_136, options_screen):
    """The opcode table is incomplete -- only the record types seen on decodable
    screens are known. Walking into an unknown one must fail loudly rather than
    silently resynchronise on whatever bytes follow."""
    with pytest.raises(guidata.UnknownRecord):
        guidata.parse_widgets(mcf_136, options_screen.start, 4)


def test_returns_none_for_an_absent_title(mcf_136):
    assert guidata.find_screen_by_title(mcf_136, "NO SUCH SCREEN") is None
    assert guidata.find_screens_by_title(mcf_136, "NO SUCH SCREEN") == []


def test_object_tables_finds_all_four_runs(corpus):
    """There are four object-pointer tables per file, not two.

    This module's docstring said two until Task 10; that was an artefact of
    MIN_OBJECT_TABLE being 64, which hid the 8-entry table belonging to the image
    sub-container and the 30-entry table nested inside another table's last object.
    """
    for version, expected in [("11.23", [8, 1201, 30, 2687]),
                              ("13.6", [8, 1422, 30, 3295]),
                              ("14.7", [9, 1077, 29, 3171])]:
        tables = guidata.object_tables(corpus[version]["mcf"])
        assert [len(t) for t in tables] == expected, version


def test_lowering_the_object_table_minimum_changes_no_answer(corpus, monkeypatch):
    """MIN_OBJECT_TABLE went from 64 to 4. Nothing that reads object_tables may move.

    The floorplan table is the only answer derived from these tables, and it is
    unchanged on all three files because the two newly visible tables carry no code
    objects, so _floorplan_table skips them.
    """
    before = {}
    monkeypatch.setattr(guidata, "MIN_OBJECT_TABLE", 64)
    for version in ("11.23", "13.6", "14.7"):
        before[version] = guidata._floorplan_table(corpus[version]["mcf"])
        assert before[version], "a vacuous comparison would prove nothing"

    monkeypatch.setattr(guidata, "MIN_OBJECT_TABLE", 4)
    for version in ("11.23", "13.6", "14.7"):
        assert guidata._floorplan_table(corpus[version]["mcf"]) == before[version]


# The number of screens that walk cleanly, and the number of controls that resolve to a
# group, are whole-corpus consequences of TAILS. Everything else that pins TAILS pins it
# only through the two 13.6 OPTIONS screens, so an opcode that appears nowhere on those
# two screens was otherwise unprotected: TAILS[0x0F] 10 -> 9 and TAILS[0x61] 5 -> 6 each
# left the rest of the suite green while changing the decode of every build. These counts
# are the guard. They are measured, not designed -- if a TAILS entry is ever corrected,
# the right response is to re-measure and say so here, not to loosen the assertion.
_SCREEN_COUNTS = {"11.23": 226, "13.6": 283, "14.7": 216}
_CONTROL_GROUP_COUNTS = {"11.23": 11, "13.6": 15, "14.7": 17}


def test_screen_and_control_group_counts_pin_the_tail_table(corpus):
    """Every walk-affecting TAILS entry, pinned at once, on all three builds.

    A wrong tail length does not usually break the walk -- it shifts where records end,
    so some screens stop landing on their 4-byte boundary and others start. What moves is
    the count. 283 for 13.6 is the number the module docstring publishes; 226 and 216 are
    its 11.23 and 14.7 equivalents. Asserting whole dicts so a failure shows all three
    builds at once rather than only the first to break.
    """
    screens = {v: len(list(guidata.iter_screens(corpus[v]["mcf"])))
               for v in _SCREEN_COUNTS}
    assert screens == _SCREEN_COUNTS

    groups = {v: len(guidata.find_control_groups(corpus[v]["mcf"]))
              for v in _CONTROL_GROUP_COUNTS}
    assert groups == _CONTROL_GROUP_COUNTS


# ---------------------------------------------------------------------------
# Task 1: structural locators for the match-value and variable register tables
# ---------------------------------------------------------------------------
#
# The ruling for this task is to locate by STRUCTURE first, then check the located
# offset against the known 13.6 constant -- not to search for the constant. The tests
# below reflect that order: each computes `guidata.find_...` with no knowledge of
# `layout.MATCH_TABLE_136` / `VAR_DPTR_TABLE_136` / `VAR_TYPE_TABLE_136` baked into the
# locator itself (see `guidata.py`), and only then compares to those constants.

def test_find_match_table_locates_the_13_6_offset(mcf_136):
    assert guidata.find_match_table(mcf_136) == layout.MATCH_TABLE_136


def test_find_variable_tables_locates_the_13_6_offsets(mcf_136):
    assert guidata.find_variable_tables(mcf_136) == (
        layout.VAR_DPTR_TABLE_136, layout.VAR_TYPE_TABLE_136)


def test_find_match_table_is_self_consistent_on_all_builds(corpus):
    """On every build: in range, parallel to the object table, and shaped like the
    known 13.6 table -- every non-0xFFFF entry small, with ascending-from-zero runs
    (the shape a type-6 switch's candidate group takes)."""
    for version in ("11.23", "13.6", "14.7"):
        data = corpus[version]["mcf"]
        gs, ge = guidata.guidata_extent(data)
        n = len(guidata.object_tables(data)[1])
        off = guidata.find_match_table(data)
        assert 0 <= off < ge - gs, version
        assert off + 2 * n <= ge - gs, version

        vals = struct.unpack_from(f"<{n}H", data, gs + off)
        nonffff = [v for v in vals if v != 0xFFFF]
        assert nonffff, f"{version}: table is all 0xFFFF, not a real match table"
        assert all(v < guidata.MATCH_TABLE_VALUE_BOUND for v in nonffff), version

        # at least one ascending run 0, 1, 2 (a switch group's own base plus its next
        # two candidates) -- the structural anchor the locator scores on.
        found_ascending_run = any(
            vals[i] == 0 and vals[i + 1] == 1 and vals[i + 2] == 2
            for i in range(n - 2))
        assert found_ascending_run, f"{version}: no ascending-from-zero run found"


def test_find_match_table_resolves_the_known_switch_group_on_13_6(mcf_136):
    """The located offset reproduces the exact object-247 switch group check that
    `test_match_value_of_the_option_group` (test_layout.py) pins against the constant
    -- i.e. the located table is not merely shaped right, it is the SAME table."""
    off = guidata.find_match_table(mcf_136)
    scr = layout.Screen(mcf_136, match_table_off=off)
    assert (scr._match_value(246), scr._match_value(247), scr._match_value(248)) == (0, 1, 2)


def test_find_variable_tables_resolve_to_sram_pointers_on_all_builds(corpus):
    """On every build: in range, and every low-index slot (well below the shortest
    corpus table, 516 on 14.7) resolves through the located pair to either 0 (unset) or
    a genuine SRAM address, with a plausible (small) type byte."""
    for version in ("11.23", "13.6", "14.7"):
        data = corpus[version]["mcf"]
        gs, ge = guidata.guidata_extent(data)
        dptr_off, type_off = guidata.find_variable_tables(data)
        assert 0 <= type_off < dptr_off < ge - gs, version

        resolved_any_sram = False
        for slot in range(400):
            addr, vtype = layout.variable_register(data, slot,
                                                    dptr_table_off=dptr_off,
                                                    type_table_off=type_off)
            assert addr == 0 or guidata.SRAM_LOW <= addr < guidata.SRAM_HIGH, \
                f"{version} slot {slot}: {addr:#x} is not 0 or an SRAM address"
            assert vtype <= guidata.VARIABLE_TYPE_MAX, f"{version} slot {slot}"
            resolved_any_sram = resolved_any_sram or addr != 0
        assert resolved_any_sram, f"{version}: no slot resolved to a real SRAM address"


def test_find_variable_tables_resolves_the_known_registers_on_13_6(mcf_136):
    """The located pair reproduces the exact registers `test_layout.py` pins against
    the `VAR_DPTR_TABLE_136`/`VAR_TYPE_TABLE_136` constants -- the located table is the
    same table, not merely a same-shaped one."""
    dptr_off, type_off = guidata.find_variable_tables(mcf_136)
    known = {109: (0x20039B49, 0), 37: (0x2003A38C, 1), 112: (0x2003A4B4, 0),
             5: (0x2003A77A, 1)}
    for slot, expected in known.items():
        assert layout.variable_register(mcf_136, slot, dptr_table_off=dptr_off,
                                        type_table_off=type_off) == expected


def test_find_match_table_raises_when_no_window_fits(mcf_136, monkeypatch):
    """A locator that always returns something, even on data with no plausible table,
    would be worse than useless -- confirm the not-found path is real and reachable."""
    monkeypatch.setattr(guidata, "MATCH_TABLE_VALUE_BOUND", 0)  # nothing can be "small"
    with pytest.raises(guidata.MatchTableNotFound):
        guidata.find_match_table(mcf_136)


def test_find_variable_tables_raises_when_no_run_fits(mcf_136, monkeypatch):
    monkeypatch.setattr(guidata, "VARIABLE_TABLE_MIN_RUN", 10_000_000)
    with pytest.raises(guidata.VariableTableNotFound):
        guidata.find_variable_tables(mcf_136)


# ---------------------------------------------------------------------------------------
# The type-6 value-match switch's candidate-child list (MCF-FORMAT §10 item 5).
#
# `switch_candidates` expands the structure a single record's bytes cannot carry -- the list
# of (match value, child object) a type-6 switch chooses between -- by reading the parallel
# match-value table. `reachable_objects` follows those candidates, which is what reaches a page
# *body*: the residual was that a base-only walk from root 0 reaches the CLIMATE tab in the
# chrome but never object 7, the CLIMATE page itself.
# ---------------------------------------------------------------------------------------

def _screen_switch(data):
    gs, _ge = guidata.guidata_extent(data)
    off = guidata.find_match_table(data)
    return layout._screen_switch_record(data, off, gs), off


@pytest.mark.parametrize("fixture", ["mcf_1123", "mcf_136", "mcf_147"])
def test_switch_candidates_expands_the_screen_switch(fixture, request):
    """The expanded candidate list is the screen map, and it is `count` entries wide.

    Ties the general expander to the independently-derived `screen_object_map`: the map is
    exactly the candidate list minus the 0xFFFF no-match default. If the two ever disagree the
    expander is reading the wrong table or range.
    """
    data = request.getfixturevalue(fixture)
    sw, off = _screen_switch(data)
    cands = guidata.switch_candidates(data, sw, off)

    assert len(cands) == sw.count                       # one entry per scanned candidate
    assert [obj for _v, obj in cands] == list(range(sw.child_index, sw.child_index + sw.count))
    assert {v: o for v, o in cands if v != layout.MATCH_DEFAULT} == \
        layout.screen_object_map(data)


def test_climate_is_a_switch_candidate_not_a_base_child(mcf_136):
    """On 13.6 CLIMATE is screen 3 -> object 7, and it is candidate *value 3* of the top
    screen switch, not the switch's base -- which is exactly why a base-only walk misses it."""
    sw, off = _screen_switch(mcf_136)
    cands = dict((v, o) for o, v in ((o, v) for v, o in guidata.switch_candidates(mcf_136, sw, off)))
    climate = layout.screen_object_map(mcf_136)[3]
    assert climate == 7
    assert cands[3] == climate
    assert sw.child_index != climate                    # the base is the default screen, not CLIMATE


def test_following_switch_candidates_reaches_the_climate_body(mcf_136):
    """The item-5 residual, pinned: a base-only walk from root 0 never reaches the CLIMATE
    page; following the switch candidates does, and pulls in the whole page subtree."""
    climate = layout.screen_object_map(mcf_136)[3]          # object 7

    base_only = guidata.reachable_objects(mcf_136, 0, follow_switch_candidates=False)
    with_cands = guidata.reachable_objects(mcf_136, 0)

    assert climate not in base_only                        # the residual: the body is unreachable
    assert climate in with_cands                           # ...until candidates are followed
    assert base_only < with_cands                          # strict superset

    # The whole CLIMATE page subtree comes in with it, and a real slice of it (the body the
    # residual named) is reachable only via the candidate expansion.
    climate_subtree = guidata.reachable_objects(mcf_136, climate)
    assert climate_subtree <= with_cands
    assert climate_subtree - base_only                     # non-empty: body objects a base walk drops


def test_switch_candidates_is_empty_for_a_non_switch_record(mcf_136):
    """A plain container (type 5) or a leaf has no candidate list; the expander says so rather
    than reading the match table at a meaningless index."""
    gs, _ge = guidata.guidata_extent(mcf_136)
    tab = guidata.object_tables(mcf_136)[1]
    # object 2 on 13.6 is a type-5 plain container (single child_index, no count).
    recs = records.object_records(mcf_136, gs + tab[2])
    assert any(r.wtype == 5 for r in recs)
    for r in recs:
        assert guidata.switch_candidates(mcf_136, r) == []
