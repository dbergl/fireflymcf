"""The floorplan table, and the per-floorplan option defaults -- RECOVERED 2026-08-18.

The three defaults tests below were `xfail(strict)` for most of this project's life, guarding a
measured negative: `guidata.find_option_defaults` raised `NotImplementedError` because the 10x6
table of booleans the coach demonstrably has was not in any of the three mcf.bin files. Every
search that docstring recorded still stands -- they were all searches for **stored data**, and the
defaults turn out to be **emitted as code**: each floorplan's tap handler transmits a run of
static CAN-frame templates whose literal `<selector, value>` bytes are the defaults
(`GUICODE.md` Finding 42).

The marks are off, and `test_defaults_match_the_capture` below is the real check -- it asserts all
90 answer-key entries, which is what makes "return the shared option list" or "invent defaults"
fail rather than pass vacuously.
"""
import pytest

from tests import answer_key
from fireflymcf import guidata




def test_finds_all_ten_floorplans(mcf_136):
    table = guidata.find_floorplan_table(mcf_136)
    assert len(table) == 10


def test_floorplan_codes_and_values_match_the_coach(mcf_136):
    """Every value was observed by selecting that floorplan on the touchscreen."""
    table = guidata.find_floorplan_table(mcf_136)
    got = {fp.value: fp.code for fp in table}
    assert got == answer_key.FLOORPLANS


def test_floorplan_families_match_the_selection_screen(mcf_136):
    table = guidata.find_floorplan_table(mcf_136)
    by_family: dict[str, set[str]] = {}
    for fp in table:
        by_family.setdefault(fp.family, set()).add(fp.code)
    for family, codes in answer_key.FLOORPLAN_FAMILIES.items():
        assert by_family.get(family) == set(codes), f"{family} membership differs"


def test_defaults_exist_for_every_floorplan(mcf_136):
    """The option list is shared; the defaults are per-floorplan. Every floorplan must
    therefore have a defaults entry covering the same six options."""
    option_names = {name for name, _ in answer_key.OPTIONS}
    for value in answer_key.FLOORPLANS:
        defaults = guidata.find_option_defaults(mcf_136, value)
        # a superset: the selection burst also writes AGS/AES and the inverter options, which the
        # answer key does not tabulate because they are not on the coach's OPTIONS screen
        assert option_names <= set(defaults), f"floorplan {value}: missing {option_names - set(defaults)}"


def test_wd_defaults_are_booleans(mcf_136):
    defaults = guidata.find_option_defaults(mcf_136, 8)  # WD, the subject coach
    assert defaults, "an empty result would satisfy the all() below vacuously"
    assert all(isinstance(v, bool) for v in defaults.values())


def test_defaults_differ_between_floorplans(mcf_136):
    """The owner observed that defaults differ per floorplan. If every floorplan
    produced identical defaults, the per-floorplan lookup is not actually indexing."""
    seen = {tuple(sorted(guidata.find_option_defaults(mcf_136, v).items()))
            for v in answer_key.FLOORPLANS}
    assert len(seen) > 1, "all floorplans returned identical defaults"


# 14.7's own floorplan enum, read out of the file by the same derivation and then
# checked by hand against its FLOORPLAN SELECTION screen. It is NOT answer-key data --
# no coach was driven for this build -- but it is a different code set from 13.6's
# (note RE, and no RD/WD), so it is what proves the reader is not tuned to 13.6.
FLOORPLANS_147: dict[int, str] = {
    1: "RT", 2: "RA", 3: "SY", 4: "TB", 5: "CB",
    6: "WA", 7: "WT", 8: "RY", 9: "RE",
}
FLOORPLAN_FAMILIES_147: dict[str, set[str]] = {
    "generator": {"RT", "RA", "CB", "RY", "RE"},
    "li-aes": {"SY", "TB", "WA", "WT"},
}


def test_147_also_has_a_floorplan_table(mcf_147):
    """`len(table) >= 1` would pass on garbage, so assert the whole enum."""
    table = guidata.find_floorplan_table(mcf_147)
    assert {fp.value: fp.code for fp in table} == FLOORPLANS_147


def test_147_families_come_off_its_own_selection_screen(mcf_147):
    table = guidata.find_floorplan_table(mcf_147)
    by_family: dict[str, set[str]] = {}
    for fp in table:
        by_family.setdefault(fp.family, set()).add(fp.code)
    assert by_family == FLOORPLAN_FAMILIES_147


def test_a_table_read_off_a_selection_screen_is_marked_confirmed(mcf_136, mcf_147):
    for mcf in (mcf_136, mcf_147):
        table = guidata.find_floorplan_table(mcf)
        assert table, "expected a table to check the flag on"  # `all([])` is True
        assert all(fp.confirmed for fp in table)


def test_a_file_with_no_selection_screen_confirms_its_run_from_the_handlers(mcf_1123):
    """11.23 has the value anchor but no FLOORPLAN SELECTION screen and no type-6 name
    switch -- its codes live only as the anchor-indexed label run. The run's values are
    confirmed against an INDEPENDENT oracle: the tap handlers, which each self-identify by
    writing their own value to selector 0xF5 (`floorplandefaults`) -- and on 11.23 the run
    must be ALIGNED to that oracle, because its run counts from 0, not 13.6's 1.

    The expected map below is `observed-on-hardware` (2026-08-30, coach flashed to 11.23,
    `captures/pc-1123-session-2026-08-30.log`): stated-order picker taps put 2=3RA/4RA,
    4=3WA/4WA, 6=3CB/4CB, 7=3RD/4RD on the wire as `0xF5` writes, photographed highlights
    anchor 1=3RT/4RT, 3=3WT/4WT, 5=3WD/4WD, and Ship Mode=0 follows by elimination. The
    pre-fix 1-based run mis-named every floorplan by one position and called 3RD/4RD
    (value 8) unconfirmed; value 8 is in fact OUT OF RANGE on 11.23 (the panel shows
    'No floorplan selected' for it -- the 13.6 WD residue after a cross-flash).
    Family is always "" -- 11.23 predates the generator/li-AES split."""
    from fireflymcf import floorplandefaults
    table = guidata.find_floorplan_table(mcf_1123)
    assert table, "11.23 does carry the value anchor"
    assert all(fp.family == "" for fp in table)
    assert {fp.value: fp.code for fp in table} == {
        0: "Ship Mode", 1: "3RT / 4RT", 2: "3RA / 4RA", 3: "3WT / 4WT",
        4: "3WA / 4WA", 5: "3WD / 4WD", 6: "3CB / 4CB", 7: "3RD / 4RD"}

    handler_values = set(floorplandefaults.floorplan_defaults(mcf_1123))
    # every entry is corroborated by a self-identifying handler and vice versa -- the
    # aligned run covers the handler set exactly, which is what licenses the alignment.
    for fp in table:
        assert fp.confirmed == (fp.value in handler_values), fp
    assert all(fp.confirmed for fp in table)


def test_a_file_with_no_anchor_raises_rather_than_returning_empty(mcf_136):
    """An empty list would read as "this build has no floorplans"; it means "this
    reader did not recognise this build"."""
    without_anchor = mcf_136.replace(
        guidata.FLOORPLAN_ANCHOR.encode("ascii"), b"X" * len(guidata.FLOORPLAN_ANCHOR))
    assert without_anchor != mcf_136
    with pytest.raises(guidata.FloorplanTableNotFound):
        guidata.find_floorplan_table(without_anchor)


def test_defaults_match_the_capture(mcf_136):
    """The load-bearing test: every value in `answer_key.FLOORPLAN_OPTION_DEFAULTS` -- 90 entries,
    read off the bus in PC.12 -- must be reproduced from the file.

    The derivation never consults the answer key: a handler is found by loading the template pool
    and is attributed to a floorplan by the value it writes to selector 0xF5, i.e. it identifies
    itself. Selector 0xD8 is excluded because PC.12a showed the burst never writes it (its value in
    PC.12's table came from status), so its absence here is the expected result, not a gap."""
    from fireflymcf import floorplandefaults
    table = floorplandefaults.floorplan_defaults(mcf_136)
    checked = 0
    for value, expected in answer_key.FLOORPLAN_OPTION_DEFAULTS.items():
        for selector, want in expected.items():
            if selector == 0xD8:                      # never written by the transaction (PC.12a)
                assert selector not in table[value]
                continue
            assert table[value][selector] == want, (
                f"floorplan {value} selector 0x{selector:02x}: file says "
                f"{table[value][selector]}, the bus said {want}")
            checked += 1
    assert checked == 90, checked


def test_every_handler_writes_its_own_floorplan_value(mcf_136):
    """The self-check that makes the attribution trustworthy without an oracle: each handler's
    first 0xF5 write is its own floorplan value, so a mis-attributed handler is impossible."""
    from fireflymcf import floorplandefaults
    table = floorplandefaults.floorplan_defaults(mcf_136)
    assert set(table) >= set(answer_key.FLOORPLANS)
    for value, writes in table.items():
        assert writes.get(0xF5) == value, (value, writes.get(0xF5))


def test_floorplan_defaults_are_build_general(mcf_136, mcf_147, mcf_1123):
    """The per-floorplan default bursts are recovered on EVERY build, not just 13.6. The template
    pool address differs per build (13.6 ~0x2594xx, 14.7 ~0x25bdxx), so hardcoding the 13.6 window
    silently returned ZERO handlers on 14.7 -- the pool bounds are now derived structurally from the
    templates. Each build must yield several floorplans, each writing its own 0xF5 value plus more."""
    from fireflymcf import floorplandefaults
    for name, mcf in [("13.6", mcf_136), ("14.7", mcf_147), ("11.23", mcf_1123)]:
        defaults = floorplandefaults.floorplan_defaults(mcf)
        assert len(defaults) >= 8, f"{name}: only {len(defaults)} floorplans decoded"
        for fp_value, writes in defaults.items():
            assert writes.get(floorplandefaults.SEL_FLOORPLAN) == fp_value   # self-identifies
            assert len(writes) > 1                                            # more than just 0xF5
