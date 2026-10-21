"""The `relocate` primitive: insert bytes into GuiData, repair every downstream reference.

Every test below insists on more than "it didn't crash": the shift is checked entry by
entry against the rule the scope spec states (`target >= at` gains exactly `K`, everything
else is untouched), the resealed build's checksums are independently recomputed and
compared (not just re-read), and the guard tests
(`test_a_wrong_shift_trips_some_self_check_gate`,
`test_a_wrong_shift_on_a_trailing_entry_is_caught_only_by_verify_table_shifts`) prove the
self-check gates are load-bearing by injecting a real wrong shift and watching `relocate`
refuse to return the result -- following this project's standing rule that a guard nobody
has watched fail is not known to work. The second of those two exists because the first
round of this fix (`coverage.analyse(deep=True)`) turned out to have its own blind spot,
found only by trying to break it a specific way (a table's *trailing* entry) rather than by
inspection.
"""
from __future__ import annotations

import dataclasses
import struct

import pytest

from fireflymcf import container, coverage, guidata, patch, relocate

VERSIONS = ["11.23", "13.6", "14.7"]
K = 64  # an arbitrary, small, non-trivial payload length used across the positive tests

# A stand-in GuiCode descriptor for the gate tests that drive it with synthetic bytes rather than
# a whole MCF. Its only job is to be IDENTICAL for both images, which is what the gate checks.
_FAKE_SECTION = {"index": 0, "name": "GuiCode", "offset": 0, "length": 0x40, "raw_entry": b""}


def _insertion_point(data: bytes) -> int:
    """A safe boundary: the start of object-pointer table 1 (the widget/screen table).

    Nothing straddles this offset -- the table's own words begin exactly here, and every
    header-declared table ends at 0x3304-0x3440 across the corpus, well before it -- so an
    insertion here can only ever bump table entries and shift the container, never split a
    declared table body.
    """
    start, _ = relocate.object_table_bounds(data, 1)
    return start


# ---------------------------------------------------------------------------
# Positive path: insert a zero payload at a safe boundary, all three builds.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", VERSIONS)
def test_insertion_grows_the_file_by_exactly_k_and_reparses(corpus, version):
    data = corpus[version]["mcf"]
    at = _insertion_point(data)

    result, edits = relocate.relocate(data, at, b"\x00" * K)

    assert len(result) == len(data) + K
    assert edits, "relocate produced no Edit records for a real insertion"

    # "container re-parses cleanly": parse_container must not raise, GuiData's declared
    # length must have grown by K, and every section after it must have slid by K.
    before = container.parse_container(data)
    after = container.parse_container(result)
    assert after.section_count == before.section_count
    for b, a in zip(before.sections, after.sections):
        if guidata.GUIDATA_SECTION_MARKER in b.name:
            assert a.length == b.length + K
            assert a.offset == b.offset
        elif b.offset > next(s for s in before.sections
                             if guidata.GUIDATA_SECTION_MARKER in s.name).offset:
            assert a.offset == b.offset + K
        else:
            assert a.offset == b.offset  # sections before GuiData are untouched


@pytest.mark.parametrize("version", VERSIONS)
def test_insertion_at_the_very_end_of_guidata(corpus, version):
    """`at == len(guidata)`: append at the section's very end, not just at an internal
    table boundary. Task 2 appends new objects near the end of GuiData, so this edge has
    to work on its own, not merely be "probably fine because the middle case is": the
    section grows by K, the boundary oracle and coverage both still pass, and the one
    section that follows GuiData in the file (G12, in all three builds) slides by K."""
    data = corpus[version]["mcf"]
    gs, ge = guidata.guidata_extent(data)
    at = ge - gs

    result, edits = relocate.relocate(data, at, b"\x00" * K)

    assert len(result) == len(data) + K
    assert edits

    before = container.parse_container(data)
    after = container.parse_container(result)
    guidata_before = next(s for s in before.sections
                          if guidata.GUIDATA_SECTION_MARKER in s.name)
    guidata_after = next(s for s in after.sections
                        if guidata.GUIDATA_SECTION_MARKER in s.name)
    assert guidata_after.length == guidata_before.length + K
    assert guidata_after.offset == guidata_before.offset
    for b, a in zip(before.sections, after.sections):
        if b.offset > guidata_before.offset:
            assert a.offset == b.offset + K, f"section {b.name!r} did not slide by K"

    ok, reason = relocate._boundary_oracle(result)
    assert ok, reason
    report = coverage.analyse(result)
    assert report.claimed == report.total
    assert report.unclaimed == []


@pytest.mark.parametrize("version", VERSIONS)
def test_the_boundary_oracle_still_passes_with_zero_derailments(corpus, version):
    """The same falsifier `tests/test_records.py` runs over the untouched corpus: every
    object with a next boundary in table 1 must still decode to within [0, 4) bytes of it.
    `relocate` already asserts this internally (it would have raised otherwise); this test
    re-derives it independently so the guarantee is not taken only on relocate's word."""
    data = corpus[version]["mcf"]
    at = _insertion_point(data)
    result, _ = relocate.relocate(data, at, b"\x00" * K)

    ok, reason = relocate._boundary_oracle(result)
    assert ok, reason


@pytest.mark.parametrize("version", VERSIONS)
def test_object_table_targets_shift_by_exactly_k_or_not_at_all(corpus, version):
    """The core correctness claim: every table entry whose target was `>= at` gains
    exactly K; every entry whose target was `< at` is byte-for-byte unchanged."""
    data = corpus[version]["mcf"]
    at = _insertion_point(data)
    result, _ = relocate.relocate(data, at, b"\x00" * K)

    before_tables = guidata.object_tables(data)
    after_tables = guidata.object_tables(result)
    assert len(before_tables) == len(after_tables) == 4
    total_checked = 0
    for before, after in zip(before_tables, after_tables):
        assert len(before) == len(after)
        for old, new in zip(before, after):
            expected = old + K if old >= at else old
            assert new == expected, f"{old:#x} -> {new:#x}, expected {expected:#x}"
            total_checked += 1
    assert total_checked == sum(len(t) for t in before_tables)
    assert total_checked > 3000  # 11.23/13.6/14.7 all carry thousands of entries


@pytest.mark.parametrize("version", VERSIONS)
def test_resealed_build_passes_coverage(corpus, version):
    """All nine-per-section stored checksums verify -- `coverage.analyse` recomputes and
    compares every one, raising `CoverageError` on any mismatch, so a clean run here is a
    positive statement, not merely "no exception happened to occur"."""
    data = corpus[version]["mcf"]
    at = _insertion_point(data)
    result, _ = relocate.relocate(data, at, b"\x00" * K)

    report = coverage.analyse(result)
    assert report.claimed == report.total
    assert report.unclaimed == []

    # Cross-check independently of coverage.analyse's internal verification: recompute
    # every stored checksum field's CRC directly and compare.
    fields = patch.checksum_fields(result)
    assert fields, "no checksum fields found -- coverage geometry must be wrong"
    for f in fields:
        assert f.stored(result) == f.computed(result), f.label


def test_k_zero_is_the_identity(mcf_136):
    """`relocate(data, at, b"")` must return `data` byte-identical, with no edits."""
    at = _insertion_point(mcf_136)
    result, edits = relocate.relocate(mcf_136, at, b"")
    assert result == mcf_136
    assert edits == []


def test_deep_coverage_also_passes_at_a_table_boundary(mcf_136):
    """Bonus, not required by the brief: inserting an all-zero payload exactly at a table
    boundary creates a gap that GuiData's deep coverage pass classifies as `padding` (every
    byte 0x00), so the *internal* structure of GuiData -- not just the container -- tiles
    exactly, with no unclaimed span and no overlap."""
    at = _insertion_point(mcf_136)
    result, _ = relocate.relocate(mcf_136, at, b"\x00" * K)
    report = coverage.analyse(result, deep=True)
    assert report.unclaimed == []
    assert report.claimed == report.total


# ---------------------------------------------------------------------------
# Cross-checks against the authoritative source: two implementations of the same
# structure must not be able to silently diverge.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", VERSIONS)
def test_guidata_directory_matches_coverage_deep_pass(corpus, version):
    """`_guidata_directory` reimplements the header-directory walk `coverage._claim_guidata`
    already performs (coverage.py's `_claim_guidata`, ~ lines 261-299) -- same self-
    terminating technique, second implementation, no cross-check between them until now.
    That is exactly the drift risk `patch.checksum_fields` guards against by being tested
    against `coverage.analyse` (patch.py:74-75: "Duplicating the arithmetic rather than
    importing it would let the two drift"). This test is that same guard for
    `_guidata_directory`: every non-zero-size table it reports must appear, at the same
    (section-relative offset, size), among `coverage.analyse(deep=True)`'s own
    `guidata-table:<tag>` claims -- the authoritative walk, since a wrong claim there
    would make the deep coverage pass itself fail (tested independently in
    `tests/test_coverage.py`).
    """
    data = corpus[version]["mcf"]
    gs, _ = guidata.guidata_extent(data)

    mine = relocate._guidata_directory(data, gs)
    assert mine, "no directory entries found -- the walk must have broken"

    report = coverage.analyse(data, deep=True)
    claimed = {
        label: (start, length)
        for start, length, label in report.spans
        if label.startswith("guidata-table:")
    }
    assert claimed, "coverage's deep pass claimed no guidata-table spans"

    nonzero = [(entry_at, tag, offset, size) for entry_at, tag, offset, size in mine if size]
    assert len(nonzero) == len(claimed), (
        "the directory walk and coverage's deep pass disagree on how many tables have a "
        "non-zero declared size")
    for _entry_at, tag, offset, size in nonzero:
        label = f"guidata-table:{tag:#06x}"
        assert label in claimed, f"tag {tag:#04x} not claimed by coverage's deep pass"
        start, length = claimed[label]
        assert start == gs + offset, f"tag {tag:#04x}: offset {offset:#x} vs claim at {start - gs:#x}"
        assert length == size, f"tag {tag:#04x}: size {size:#x} vs claimed length {length:#x}"


# ---------------------------------------------------------------------------
# Guards: refuse rather than guess.
# ---------------------------------------------------------------------------


def test_out_of_range_offset_is_rejected(mcf_136):
    _, span = relocate.object_table_bounds(mcf_136, 1)  # any in-bounds reference point
    gs, ge = guidata.guidata_extent(mcf_136)
    with pytest.raises(relocate.RelocationError, match="outside GuiData"):
        relocate.relocate(mcf_136, (ge - gs) + 1, b"\x00" * K)


def test_splitting_a_header_table_body_is_refused(mcf_136):
    """Inserting inside the binding table's declared body (GuiData+0x778, size 0x22F8,
    `docs/MCF-FORMAT.md` S5.1) is not a table-boundary insertion, so relocate must refuse
    it rather than silently growing or ignoring the table's declared size."""
    with pytest.raises(relocate.RelocationError, match="falls inside header table"):
        relocate.relocate(mcf_136, 0x778 + 10, b"\x00" * 4)


def test_a_broken_pointer_table_invariant_is_caught_for_any_table(mcf_136, monkeypatch):
    """Finding 1 of the Task-1 quality review: the self-check must cover ALL FOUR
    object-pointer tables, not only table 1 (the one `_boundary_oracle` content-decodes).
    `_verified_pointer_tables` delegates the self-terminating check (a table's lowest
    pointer equals its own end) to `coverage._pointer_tables`, which validates every run
    it finds -- table 0 and table 2/3 included -- so a violation there must still stop
    `relocate` before it trusts a table's storage position. None of the three corpus
    builds actually has a broken table to exercise this on, so the failure is injected
    here by making the underlying check fail synthetically -- the same "stub and confirm
    the failure fires" technique the guard tests below use for the shift arithmetic.
    """
    at = _insertion_point(mcf_136)  # computed BEFORE the monkeypatch takes effect

    def broken(data, start, end):
        raise coverage.CoverageError(
            "synthetic: an object-pointer table's lowest pointer does not equal its "
            "own end")

    monkeypatch.setattr(coverage, "_pointer_tables", broken)
    with pytest.raises(relocate.RelocationError, match="does not hold"):
        relocate.relocate(mcf_136, at, b"\x00" * K)


def test_a_wrong_shift_trips_some_self_check_gate(mcf_136, monkeypatch):
    """Prove the self-check is load-bearing: inject a shift that is wrong by exactly one
    on a single, non-trailing object-table entry (an off-by-one on the target that
    `test_object_table_targets_shift_by_exactly_k_or_not_at_all` would otherwise catch),
    and confirm `relocate` raises `RelocationError` instead of returning that build.

    Deliberately does not pin down WHICH of the four self-check gates fires first --
    `relocate` runs several (`_verified_pointer_tables`, deep coverage,
    `_verify_table_shifts`, the boundary oracle), any non-trailing entry corruption is
    plausibly caught by more than one of them, and asserting a specific gate here would
    make this test brittle to their internal ordering. `_verify_table_shifts` proves the
    one gate that matters most precisely
    (`test_a_wrong_shift_on_a_trailing_entry_is_caught_only_by_verify_table_shifts`
    below); this test proves the ensemble refuses the build at all.

    Without `monkeypatch.setattr` below, `relocate` computes every shift correctly and
    this test would have nothing to assert -- so the injection is what makes the guard
    observably load-bearing rather than merely present in the source.
    """
    at = _insertion_point(mcf_136)

    calls = {"n": 0}
    real_new_target = relocate._new_target

    def off_by_one_on_second_call(value: int, k: int) -> int:
        calls["n"] += 1
        result = real_new_target(value, k)
        # The very first qualifying object-table entry at this boundary is table 1's
        # highest-offset (trailing) object -- the one entry none of the neighbour-based
        # checks can see a wrong shift on (see the trailing-entry test below); corrupting
        # it here would prove less. The second call is a non-trailing entry.
        if calls["n"] == 2:
            result += 1
        return result

    monkeypatch.setattr(relocate, "_new_target", off_by_one_on_second_call)

    with pytest.raises(relocate.RelocationError, match="relocate produced a build"):
        relocate.relocate(mcf_136, at, b"\x00" * K)

    assert calls["n"] >= 2, "the corrupted call path was never exercised"


def test_a_wrong_shift_on_a_trailing_entry_is_caught_only_by_verify_table_shifts(
        mcf_136, monkeypatch):
    """Round 2 of the Task-1 quality review, 2026-08-14. The re-reviewer proved
    empirically that `_verified_pointer_tables` only checks a table's MINIMUM-valued
    entry: corrupting object-table 0's minimum-target entry by +1 raised; corrupting its
    MAXIMUM-target entry by +1 (its *trailing* object -- the one entry with no next
    neighbour to be checked against) returned successfully with a silently broken table.

    Investigating the fix (`coverage.analyse(deep=True)`, as first recommended) surfaced
    a deeper problem: a table's trailing object is `coverage.py`'s own acknowledged
    "weakest claim in the module" -- its upper bound is a heuristic ("no bigger than any
    object this table already delimits") with tens of thousands of bytes of slack in this
    corpus, so a `K+1`-instead-of-`K` error on it does not trip deep coverage either. This
    test injects exactly that -- a REAL wrong shift via `relocate`'s own arithmetic, not a
    synthetic exception -- on table 0's and table 3's trailing (maximum-target) entry, and
    confirms `_verify_table_shifts` is what catches it (matching on its message
    specifically, not merely "some gate raised", to prove this particular gate is not
    redundant with the other three).
    """
    tables_before = guidata.object_tables(mcf_136)
    real_new_target = relocate._new_target

    for table_index in (0, 3):
        min_target = min(tables_before[table_index])
        max_target = max(tables_before[table_index])
        assert max_target != min_target
        # One byte past the table's own minimum target: the minimum entry does not
        # qualify for a shift (stays unchanged, so gate 1's invariant trivially still
        # holds), while every other entry -- including the trailing/maximum one -- does.
        at = min_target + 1

        def wrong_on_trailing_entry(value: int, k: int, _victim=max_target) -> int:
            result = real_new_target(value, k)
            return result + 1 if value == _victim else result

        monkeypatch.setattr(relocate, "_new_target", wrong_on_trailing_entry)
        with pytest.raises(relocate.RelocationError, match="wrong object-table shift"):
            relocate.relocate(mcf_136, at, b"\x00" * K)
        monkeypatch.undo()

        # Cross-check the finding itself, not just relocate()'s reaction to it: with the
        # SAME corruption, gates 1 and 2 alone (bypassing _verify_table_shifts and the
        # oracle) must NOT catch it -- otherwise this is no longer the blind spot the
        # test claims to cover, and it would be testing nothing new. Reaching this line
        # at all (relocate returning instead of raising) already proves gates 1 and 2
        # let it through; `_verified_pointer_tables` not raising and `_deep_coverage_ok`
        # returning ok make that explicit rather than merely implied.
        monkeypatch.setattr(relocate, "_new_target", wrong_on_trailing_entry)
        monkeypatch.setattr(relocate, "_verify_table_shifts", lambda *a, **kw: (True, ""))
        monkeypatch.setattr(relocate, "_boundary_oracle", lambda *a, **kw: (True, ""))
        result, _ = relocate.relocate(mcf_136, at, b"\x00" * K)
        relocate._verified_pointer_tables(result)  # must not raise -- gate 1 misses it
        ok2, reason2 = relocate._deep_coverage_ok(result)
        assert ok2, (
            f"table {table_index}: deep coverage unexpectedly caught the trailing-entry "
            f"corruption ({reason2!r}) -- the blind spot this test targets no longer "
            f"exists, so pick a different repro")
        monkeypatch.undo()


def test_the_guard_can_also_be_tripped_by_direct_corruption(mcf_136):
    """A second, independent route to the same guard: take an already-correct relocated
    build and hand-corrupt one *checked* object-table entry's stored value by +1 (the
    "mutate one entry by K+-1" the task brief asks for), then confirm
    `relocate._boundary_oracle` -- the exact function `relocate` calls internally --
    reports failure on it."""
    at = _insertion_point(mcf_136)
    result, edits = relocate.relocate(mcf_136, at, b"\x00" * K)

    table_edits = [e for e in edits if e.structure == "object-table-entry"]
    assert len(table_edits) > 1
    # Skip the first: it is the trailing object with no next boundary (see above), so
    # corrupting it would not be caught by construction, not by the guard's absence.
    victim = table_edits[1]

    corrupted = bytearray(result)
    value = struct.unpack_from("<I", corrupted, victim.file_offset)[0]
    struct.pack_into("<I", corrupted, victim.file_offset, value + 1)

    ok, reason = relocate._boundary_oracle(bytes(corrupted))
    assert not ok, "corrupting a checked object-table entry by +1 went undetected"
    assert reason


# ---------------------------------------------------------------------------
# GuiCode -> GuiData pointer relocation (the boot-loop fix)
# ---------------------------------------------------------------------------
def test_guicode_pointers_into_shifted_guidata_are_relocated(mcf_136):
    """GuiCode holds absolute pointers into GuiData (the four pointer tables + a few object-data
    addresses). Inserting at the object-table start shifts every target >= there, so each such
    GuiCode literal must gain K -- otherwise the panel dereferences shifted content at boot (the
    boot loop this fix addresses). Assert every GuiCode->GuiData pointer is bumped by exactly K
    when its target moved, unchanged otherwise; and that at least one really moved (non-vacuous)."""
    from fireflymcf import theme
    at = _insertion_point(mcf_136)
    result, edits = relocate.relocate(mcf_136, at, b"\x00" * K)
    before = dict(theme.guicode_guidata_pointers(mcf_136))
    after = dict(theme.guicode_guidata_pointers(result))
    assert set(before) == set(after) and before                  # same literals, non-empty
    moved = 0
    for pos, rel_b in before.items():
        expected = rel_b + K if rel_b >= at else rel_b
        assert after[pos] == expected, f"GuiCode literal @ {pos:#x} not relocated"
        moved += rel_b >= at
    assert moved > 0, "no GuiCode pointer was above the insertion -- test is vacuous"
    # and the relocator emitted guicode-pointer edits for them
    assert any(e.structure == "guicode-pointer" for e in edits)


def test_guicode_pointer_gate_rejects_a_stale_pointer(mcf_136):
    """The gate is load-bearing: hand-forge a build where one GuiCode literal was NOT relocated
    (write its old value back) and `_verify_guicode_pointers` must reject it. Without the gate a
    stale GuiCode pointer -- exactly the flashed boot loop -- would pass every other check."""
    from fireflymcf import theme
    at = _insertion_point(mcf_136)
    result, _ = relocate.relocate(mcf_136, at, b"\x00" * K)
    ok, _ = relocate._verify_guicode_pointers(mcf_136, result, at, K)
    assert ok, "the real build must pass the gate"
    # revert one relocated GuiCode literal to its pre-shift value -> stale
    before = dict(theme.guicode_guidata_pointers(mcf_136))
    pos = next(p for p, rel in before.items() if rel >= at)
    tampered = bytearray(result)
    struct.pack_into("<I", tampered, pos, 0x90000000 + before[pos])   # old (stale) value
    ok, reason = relocate._verify_guicode_pointers(mcf_136, bytes(tampered), at, K)
    assert not ok, "gate must reject a stale GuiCode pointer"


def test_lcd_stranded_pointer_guard_is_a_defensible_negative(mcf_136):
    """LCD Firmware (flashed separately) references GuiData only at its base (rel 0), which never
    shifts, so an insertion is allowed. The guard exists for a future build where that changes;
    here it must pass. Non-vacuous: assert LCD really only points at rel 0."""
    from fireflymcf import theme
    lcd_targets = {rel for _pos, rel in theme.section_guidata_pointers(mcf_136, "LCD")}
    assert lcd_targets == {0}, f"LCD GuiData targets changed: {sorted(lcd_targets):#x}"
    ok, _ = relocate._verify_no_stranded_lcd_pointers(mcf_136, _insertion_point(mcf_136))
    assert ok


def test_grow_match_table_gives_the_next_appended_objects_their_match_values():
    import struct
    from fireflymcf import guidata, insert, relocate
    data = open("firefly-13.6/mcf.bin", "rb").read()
    n = len(guidata.object_tables(data)[1])
    grown, edits = relocate.grow_match_table(data, [0, 1])
    gs, _ = guidata.guidata_extent(grown)
    mt = guidata.find_match_table(grown)
    assert mt == guidata.find_match_table(data), "the table's own offset must not move"
    assert len(guidata.object_tables(grown)[1]) == n, "no object is created by growing the table"
    got = [struct.unpack_from("<H", grown, gs + mt + i * 2)[0] for i in (n - 1, n, n + 1)]
    assert got[1:] == [0, 1]
    assert got[0] == struct.unpack_from("<H", data, gs + mt + (n - 1) * 2)[0]
    # and an object appended afterwards reads the new entry, not an object-table word
    with_obj, _e, idx = insert._append_object(grown, insert.clone_object(grown, 173))
    gs2, _ = guidata.guidata_extent(with_obj)
    assert idx == n
    assert struct.unpack_from("<H", with_obj, gs2 + guidata.find_match_table(with_obj) + idx * 2)[0] == 0
    # `Edit` has no `.section` attribute (only `.structure`); the insertion `relocate()` makes
    # is tagged "guidata-insertion" -- confirm the growth actually landed in GuiData.
    assert any(e.structure == "guidata-insertion" for e in edits)


def test_grow_match_table_refuses_an_odd_count():
    import pytest
    from fireflymcf import relocate
    data = open("firefly-13.6/mcf.bin", "rb").read()
    with pytest.raises(relocate.RelocationError):
        relocate.grow_match_table(data, [0])


@pytest.mark.parametrize("value", [256, 0x1234, 0xFFFE])
def test_grow_match_table_refuses_a_value_the_locator_would_reject(value):
    """`find_match_table`'s hard filter takes every entry as 0xFFFF or below 256; a grown value
    in 256..0xFFFE would stop its anchored walk inside the table, and every switch would then
    read shifted match values."""
    from fireflymcf import relocate
    data = open("firefly-13.6/mcf.bin", "rb").read()
    with pytest.raises(relocate.RelocationError, match="below 256"):
        relocate.grow_match_table(data, [0, value])


@pytest.mark.parametrize("value", [0, 255, 0xFFFF])
def test_grow_match_table_takes_the_values_the_locator_accepts(value):
    from fireflymcf import guidata, relocate
    data = open("firefly-13.6/mcf.bin", "rb").read()
    grown, _ = relocate.grow_match_table(data, [0, value])
    assert guidata.find_match_table(grown) == guidata.find_match_table(data)


def test_grow_match_table_after_a_prior_append_still_lands_on_the_right_object_indices():
    """v16 (aescompact.swap_electrical_to_compact) calls `grow_match_table` AFTER other objects
    have already been appended (aes_body/engine/cluster clones) -- objects `insert.py` never
    gives a match-table entry (`find_match_table`'s own docstring: appending grows object-table
    1 but not this table), so the table's own physical width can already lag behind the object
    count. An earlier revision of this function used `n = len(object_tables(data)[1])` -- the
    NEXT object index -- as the insertion point too, landing the insert `(n - w)` u16s INSIDE
    object-table 1's own pointer words instead of at the match table's real end (`w`), corrupting
    it (caught downstream, as `SWAP_REFUSED: wrong object-table shift`, not here). This pins the
    fix: two prior appends (an even gap, so no RelocationError from the alignment check either),
    then growing the table must still give the NEXT two objects (index n, n+1) the values asked
    for -- not objects w, w+1 -- and must not disturb the two already-appended, uncovered objects'
    own table-1 entries."""
    import struct
    from fireflymcf import guidata, insert, relocate
    data = open("firefly-13.6/mcf.bin", "rb").read()
    w = len(guidata.object_tables(data)[1])          # match table's stock width
    out = data
    for _ in range(2):                                # two un-tracked appends -> even gap
        out, _e, _idx = insert._append_object(out, insert.clone_object(data, 173))
    n = len(guidata.object_tables(out)[1])
    assert n == w + 2, "test setup: expected exactly two prior appends"

    grown, edits = relocate.grow_match_table(out, [0, 1])
    gs, _ = guidata.guidata_extent(grown)
    mt = guidata.find_match_table(grown)
    assert mt == guidata.find_match_table(out), "the table's own offset must not move"
    assert len(guidata.object_tables(grown)[1]) == n, "no object is created by growing the table"
    got = [struct.unpack_from("<H", grown, gs + mt + i * 2)[0] for i in (n - 1, n, n + 1)]
    assert got[1:] == [0, 1], "the NEW values must land on the NEXT object indices (n, n+1)"
    assert got[0] == 0xFFFF, "the last already-appended, uncovered object gets the filler sentinel"
    # an object appended afterwards reads the new entry, not an object-table word
    with_obj, _e, idx = insert._append_object(grown, insert.clone_object(grown, 173))
    gs2, _ = guidata.guidata_extent(with_obj)
    assert idx == n
    assert struct.unpack_from("<H", with_obj, gs2 + guidata.find_match_table(with_obj) + idx * 2)[0] == 0


def test_grow_match_table_pads_an_odd_gap_with_one_trailing_sentinel():
    """One prior un-tracked append (an odd gap) plus an even `values` count needs an ODD total
    insert length to land `values` exactly at `n`/`n+1` -- 2-byte, not 4-byte, aligned, which
    would misalign object-table 1. A gap's parity is a property of the whole preceding op chain,
    not something a caller of `grow_match_table` controls (fix round 1, finding 1) -- so this is
    a LEGAL situation, not refused: one extra trailing `0xFFFF` restores 4-alignment, pre-covering
    index `n+2` (harmless -- the firmware's own default-arm marker) rather than raising."""
    import struct
    from fireflymcf import guidata, insert, relocate
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, _e, _idx = insert._append_object(data, insert.clone_object(data, 173))
    n = len(guidata.object_tables(out)[1])
    mt_before = guidata.find_match_table(out)

    grown, edits = relocate.grow_match_table(out, [0, 1])
    gs, _ = guidata.guidata_extent(grown)
    mt = guidata.find_match_table(grown)
    assert mt == mt_before, "the table's own offset must not move"
    assert len(guidata.object_tables(grown)[1]) == n, "no object is created by growing the table"
    got = [struct.unpack_from("<H", grown, gs + mt + i * 2)[0] for i in (n, n + 1, n + 2)]
    assert got == [0, 1, 0xFFFF], \
        "values at n/n+1, and one trailing sentinel pre-covering n+2"
    # a subsequently appended object reads the trailing sentinel, not the real values or an
    # object-table word
    with_obj, _e, idx = insert._append_object(grown, insert.clone_object(grown, 173))
    gs2, _ = guidata.guidata_extent(with_obj)
    assert idx == n
    with_obj2, _e2, idx2 = insert._append_object(with_obj, insert.clone_object(with_obj, 173))
    gs3, _ = guidata.guidata_extent(with_obj2)
    assert idx2 == n + 1
    assert struct.unpack_from(
        "<H", with_obj2, gs3 + guidata.find_match_table(with_obj2) + (n + 2) * 2)[0] == 0xFFFF


def test_grow_match_table_overwrites_a_pre_covering_trailer_on_the_next_grow():
    """The trailer a grow adds after an odd gap leaves the table ONE entry wider than the object
    count once its own objects are appended -- a legal state (v32's Batt Heat switch left it, and
    the next grow in the same build was refused, 2026-09-24). The next grow must overwrite that
    pre-covering 0xFFFF in place and insert only the rest, so its values still land on the NEXT
    appended objects."""
    import struct
    from fireflymcf import guidata, insert, relocate
    data = open("firefly-13.6/mcf.bin", "rb").read()
    out, _e, _idx = insert._append_object(data, insert.clone_object(data, 173))   # odd gap
    out, _ = relocate.grow_match_table(out, [0, 1])                              # adds a trailer
    for _ in range(2):
        out, _e, _idx = insert._append_object(out, insert.clone_object(out, 173))
    n = len(guidata.object_tables(out)[1])
    grown, _edits = relocate.grow_match_table(out, [5, 6, 7, 8])
    gs, _ = guidata.guidata_extent(grown)
    mt = guidata.find_match_table(grown)
    got = [struct.unpack_from("<H", grown, gs + mt + i * 2)[0] for i in range(n, n + 4)]
    assert got == [5, 6, 7, 8], got
    for k in range(4):
        grown, _e, idx = insert._append_object(grown, insert.clone_object(grown, 173))
        assert idx == n + k


def test_grow_match_table_refuses_a_match_table_wider_than_the_object_count():
    """Fix round 1, finding 2: a located match table wider than the object count whose extra
    entries are NOT pre-covering 0xFFFF sentinels means `find_match_table` found the wrong
    window -- refuse rather than silently place `values` at `w` instead of `n`. (A table one
    trailer wider IS legal; see the test above.) Exercised by patching `object_table_bounds`,
    the one call `grow_match_table` derives `w` from, so the extra entries are object-table
    words."""
    import pytest
    from fireflymcf import relocate
    data = open("firefly-13.6/mcf.bin", "rb").read()
    real_bounds = relocate.object_table_bounds

    def fake_bounds(d, index):
        start, end = real_bounds(d, index)
        return start + 100, end     # push table 1's start artificially later -> w > n

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(relocate, "object_table_bounds", fake_bounds)
        with pytest.raises(relocate.RelocationError, match="exceeds the object count"):
            relocate.grow_match_table(data, [0, 1])


# ---------------------------------------------------------------------------------------------
# The fifth gate's scan-artifact rule (2026-09-06, building v21)
# ---------------------------------------------------------------------------------------------
# `_verify_guicode_pointers` used to require the two literal SETS to be equal. They are produced
# by a linear disassembly sweep, and this project's standing rule is that instruction bytes
# collide with the `0x9xxxxxxx` range, so the sets can legitimately differ by a phantom. The gate
# now allows a position seen by only one scan when BOTH of the following hold -- and refuses when
# either does not, which is what these three tests pin.

def _pointer_gate(before, after, at, delta):
    from fireflymcf import relocate
    return relocate._verify_guicode_pointers(before, after, at, delta)


def _fake_scan(monkeypatch, before_map, after_map):
    """Force the two scans to return exactly these {position: rel} maps.

    These tests drive the gate with synthetic byte strings rather than an MCF, so the
    GuiCode-identity assertion is stubbed too -- it is exercised for real by
    `test_the_pointer_gate_refuses_a_moved_guicode_section`.
    """
    from fireflymcf import container, theme
    calls = {"n": 0}

    def scan(data):
        calls["n"] += 1
        return sorted((before_map if calls["n"] == 1 else after_map).items())

    monkeypatch.setattr(theme, "guicode_guidata_pointers", scan)
    monkeypatch.setattr(theme, "guicode_section",
                        lambda data: container.Section(**_FAKE_SECTION))


def test_the_pointer_gate_allows_a_phantom_below_the_insertion_point(monkeypatch):
    """The v21 case. A position only one scan sees, whose word did not change and whose target is
    below `at`, cannot be a missed relocation: content below `at` does not move, so a pointer to
    it must NOT be bumped and is correct however the sweep classified it."""
    data = bytes(0x40)
    _fake_scan(monkeypatch, {}, {0x10: 0x100})      # phantom at 0x10, target below at
    ok, msg = _pointer_gate(data, data, at=0x1000, delta=20)
    assert ok, msg


def test_the_pointer_gate_still_refuses_a_phantom_AT_OR_ABOVE_the_insertion_point(monkeypatch):
    """The dangerous case the set-equality check existed for: a real pointer into shifted content
    that the forward pass never bumped would look exactly like this."""
    data = bytes(0x40)
    _fake_scan(monkeypatch, {}, {0x10: 0x2000})     # target above at -- should have been bumped
    ok, msg = _pointer_gate(data, data, at=0x1000, delta=20)
    assert not ok and "insertion point" in msg


def test_the_pointer_gate_still_refuses_a_phantom_whose_word_changed(monkeypatch):
    """A word that CHANGED across the edit was touched by the forward pass, so its classification
    is not an artifact and the mismatch is real."""
    before = bytes(0x40)
    after = bytes(0x10) + b"\x11\x22\x33\x44" + bytes(0x2C)
    _fake_scan(monkeypatch, {}, {0x10: 0x100})
    ok, msg = _pointer_gate(before, after, at=0x1000, delta=20)
    assert not ok and "word changed" in msg


# ---------------------------------------------------------------------------------------------
# The same gate against REAL bytes, at an insertion point where literals actually need bumping
# ---------------------------------------------------------------------------------------------
# The three tests above drive the gate with a fake scan, which pins the branch logic and nothing
# else. These drive it with 13.6's own 27 GuiCode literals and an `after` built by applying only
# the pointer bump -- so a bug in the gate's arithmetic, its coordinate space, or its handling of
# the symmetric difference shows up against real values. Written after the 2026-09-06 relaxation,
# because the reasoning for that change is only as good as the protection that survives it.
#
# NOTE the insertion point. At the ordinary APPEND point (`span - 4`) not one of 13.6's literals
# targets anything above it, so every "missed bump" mutation is vacuous and passes -- which is
# exactly what an early version of this audit concluded before noticing. 0x40000 is mid-GuiData,
# where 19 of the 27 do need bumping.

_MID_AT, _MID_DELTA = 0x40000, 20


def _bumped(before, at, delta):
    """`before` with every GuiCode literal at/above `at` bumped -- a correct forward pass, and
    nothing else touched (the gate reads no other bytes)."""
    from fireflymcf import guidata, theme
    out = bytearray(before)
    for pos, rel in theme.guicode_guidata_pointers(before):
        if rel >= at:
            struct.pack_into("<I", out, pos, guidata.OBJECT_POINTER_BASE + rel + delta)
    return bytes(out)


@pytest.fixture(scope="module")
def _mid_case(mcf_136):
    from fireflymcf import theme
    before = mcf_136
    ptrs = dict(theme.guicode_guidata_pointers(before))
    movers = sorted((p, r) for p, r in ptrs.items() if r >= _MID_AT)
    stayers = sorted((p, r) for p, r in ptrs.items() if r < _MID_AT)
    assert movers and stayers, "this insertion point must have literals on both sides of it"
    return before, _bumped(before, _MID_AT, _MID_DELTA), movers, stayers


def test_the_pointer_gate_passes_a_correct_bump_of_real_literals(_mid_case):
    before, after, movers, _ = _mid_case
    ok, msg = _pointer_gate(before, after, _MID_AT, _MID_DELTA)
    assert ok, msg


def test_the_pointer_gate_refuses_every_forward_pass_failure_on_real_literals(_mid_case):
    """The four ways the forward pass can be wrong, each applied to a real literal.

    This is the test that says the 2026-09-06 relaxation did not open a hole: the relaxation
    accepts a symmetric-difference position ONLY when its word is unchanged AND its target is
    below `at`, and each mutation below violates one of those or lands in the intersection.
    """
    from fireflymcf import guidata, theme
    before, good, movers, stayers = _mid_case
    (pos, rel), (spos, srel) = movers[0], stayers[0]

    def refuse(mutate, hide_from_after=None, expect=None):
        broken = bytearray(good)
        mutate(broken)
        real = theme.guicode_guidata_pointers
        if hide_from_after is not None:
            calls = {"n": 0}

            def scan(data):
                calls["n"] += 1
                out = real(data)
                return ([(p, r) for p, r in out if p != hide_from_after]
                        if calls["n"] == 2 else out)
            theme.guicode_guidata_pointers = scan
        try:
            ok, msg = _pointer_gate(before, bytes(broken), _MID_AT, _MID_DELTA)
        finally:
            theme.guicode_guidata_pointers = real
        assert not ok, "the gate accepted a build with a mis-relocated GuiCode pointer"
        if expect:
            assert expect in msg, msg
        return msg

    # a pointer that needed bumping was left stale, and both scans still see it
    refuse(lambda b: struct.pack_into("<I", b, pos, guidata.OBJECT_POINTER_BASE + rel),
           expect="expected")
    # ...bumped, but by the wrong amount
    refuse(lambda b: struct.pack_into("<I", b, pos,
                                      guidata.OBJECT_POINTER_BASE + rel + _MID_DELTA + 4))
    # a pointer BELOW `at` was moved when it should have been left alone
    refuse(lambda b: struct.pack_into("<I", b, spos,
                                      guidata.OBJECT_POINTER_BASE + srel + _MID_DELTA))
    # stale AND invisible to the `after` scan -- the symmetric-difference arm must catch it
    refuse(lambda b: struct.pack_into("<I", b, pos, guidata.OBJECT_POINTER_BASE + rel),
           hide_from_after=pos, expect="insertion point")
    # correctly bumped but invisible to the `after` scan -- the word changed, so still refused
    refuse(lambda b: None, hide_from_after=pos, expect="word changed")


def test_the_pointer_gate_refuses_a_moved_guicode_section(_mid_case, monkeypatch):
    """The gate reads each literal's word out of BOTH images at the same file offset, so the two
    must agree on where GuiCode is. Asserted rather than assumed: if it were false the comparison
    would silently read unrelated bytes instead of mismatching."""
    from fireflymcf import theme
    before, after, _, _ = _mid_case
    real = theme.guicode_section
    calls = {"n": 0}

    def moved(data):
        calls["n"] += 1
        sec = real(data)
        if calls["n"] == 2:
            sec = dataclasses.replace(sec, offset=sec.offset + 4)   # Section is frozen
        return sec

    monkeypatch.setattr(theme, "guicode_section", moved)
    ok, msg = _pointer_gate(before, after, _MID_AT, _MID_DELTA)
    assert not ok and "GuiCode moved or resized" in msg


def test_find_match_table_survives_more_appends_than_the_width_scan_tries(monkeypatch):
    """A 58-op build appended more objects than `MATCH_TABLE_MAX_APPENDED` without growing the
    match table, and the width scan stopped finding it (INSERT_REFUSED on the 15th button). The
    anchored walk (the table ends where object table 1 begins) still returns the stock offset,
    and a grow afterwards lands its values on the next object indices."""
    import struct
    from fireflymcf import guidata, insert, relocate
    data = open("firefly-13.6/mcf.bin", "rb").read()
    stock_mt = guidata.find_match_table(data)
    extra = guidata.MATCH_TABLE_MAX_APPENDED + 4
    out = data
    for _ in range(extra):
        out, _e, _idx = insert._append_object(out, insert.clone_object(data, 173))
    n = len(guidata.object_tables(out)[1])
    assert n == len(guidata.object_tables(data)[1]) + extra
    assert guidata.find_match_table(out) == stock_mt

    grown, _ = relocate.grow_match_table(out, [0, 1])
    gs, _ = guidata.guidata_extent(grown)
    assert guidata.find_match_table(grown) == stock_mt
    assert [struct.unpack_from("<H", grown, gs + stock_mt + i * 2)[0] for i in (n, n + 1)] \
        == [0, 1]

    # without the anchor, the width scan alone gives up on this image
    def unanchored(*a, **k):
        raise ValueError("anchor disabled")
    monkeypatch.setattr(guidata, "object_table_offset", unanchored)
    with pytest.raises(guidata.MatchTableNotFound):
        guidata.find_match_table(out)


def test_find_match_table_anchors_a_table_two_wider_than_the_object_count(monkeypatch):
    """A grow lands its values on the next object indices before those objects are appended,
    so a table can be wider than the object count: eight committed 13.6 builds carry width
    n + 2. The anchored walk must take them, not fall back to the width scan (disabled here)."""
    from fireflymcf import guidata, relocate
    data = open("firefly-13.6/mcf.bin", "rb").read()
    stock_mt = guidata.find_match_table(data)
    grown, _ = relocate.grow_match_table(data, [0, 1])
    n = len(guidata.object_tables(grown)[1])
    start, _end = relocate.object_table_bounds(grown, relocate.OBJECT_TABLE_INDEX)
    assert (start - stock_mt) // 2 == n + 2
    monkeypatch.setattr(guidata, "MATCH_TABLE_MAX_APPENDED", -1)   # the width scan tries nothing
    assert guidata.find_match_table(grown) == stock_mt
