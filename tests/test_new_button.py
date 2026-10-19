"""Task 3: a genuinely new, always-visible button on the 13.6 home page, verified end to end.

The build (clone obj 1409 -> insert under home page 4 -> rename to 'Starlink' + reposition ->
reseal) is produced once by a module-scoped fixture (it runs `relocate`'s multi-second
self-check) and shared. Every acceptance gate from the brief has a test:

- boundary oracle passes over every object, including the new top-index one;
- deep coverage is full with zero unclaimed and every CRC verifies;
- the decoded object graph equals stock + exactly one new node under the home page;
- generalised L3 accounts for every differing byte;
- the decoded home page shows the new label at its retargeted position and the donor's original
  control is still present.

The guard tests prove the checks are load-bearing, not vacuous: generalised L3 is shown to reject
a flipped output byte, a dropped edit, a mis-valued edit, and a retarget edit outside the clone
span; and the decoded-render assertion is shown to distinguish stock (no 'Starlink') from the
build (so it is not trivially true).
"""
from __future__ import annotations

import dataclasses

import pytest

from fireflymcf import (coverage, guidata, insert, layout, patch, records,
                              relocate, relocate_demo, verify)


@pytest.fixture(scope="module")
def build(mcf_136):
    """The whole pipeline run once against stock 13.6."""
    return relocate_demo.build_new_button(mcf_136)


# --------------------------------------------------------------------------------------
# the pipeline produced the expected structural change
# --------------------------------------------------------------------------------------
def test_image_grew_by_the_insertions_only(build, mcf_136):
    grew = len(build["final"]) - len(mcf_136)
    assert grew == build["l3"].grew_by == sum(l for _, l in build["l3"].inserted_spans)
    # two insertions: the appended object (+its pointer) and the attachment record
    assert len(build["l3"].inserted_spans) == 2


def test_exactly_one_object_appended_at_the_top_index(build, mcf_136):
    before = guidata.object_tables(mcf_136)
    after = guidata.object_tables(build["final"])
    assert len(after) == len(before)
    for i, (b, a) in enumerate(zip(before, after)):
        expected = len(b) + (1 if i == insert.OBJECT_TABLE_INDEX else 0)
        assert len(a) == expected, f"table {i}: {len(b)} -> {len(a)}"
    assert build["new_index"] == len(before[insert.OBJECT_TABLE_INDEX])


# --------------------------------------------------------------------------------------
# correctness gates
# --------------------------------------------------------------------------------------
def test_boundary_oracle_passes_including_new_object(build):
    ok, reason = relocate._boundary_oracle(build["final"])
    assert ok, reason
    # and the new top-index object specifically decodes to its boundary
    final, ni = build["final"], build["new_index"]
    gs, _ = guidata.guidata_extent(final)
    start, end = insert._object_span(final, ni)
    recs = records.object_records(final, gs + start)
    assert recs
    assert 0 <= (gs + end) - recs[-1].end < 4


def test_deep_coverage_is_full_and_zero_unclaimed(build):
    report = coverage.analyse(build["final"], deep=True)
    assert report.unclaimed == []
    assert report.claimed == report.total


def test_every_crc_verifies(build):
    # reseal changes nothing on a build whose checksums are already correct
    _, changed = patch.reseal(build["final"])
    assert changed == []


def test_graph_equals_stock_plus_one_node_under_home(build, mcf_136):
    before = insert.object_child_graph(mcf_136)
    after = insert.object_child_graph(build["final"])
    ni = build["new_index"]
    assert after != before, "graph did not change; equivalence check would be vacuous"
    expected = dict(before)
    expected[relocate_demo.HOME_PAGE_INDEX] = sorted(
        before[relocate_demo.HOME_PAGE_INDEX] + [ni])
    expected[ni] = before[relocate_demo.DONOR_INDEX]     # clone carries the donor's children
    assert after == expected


def test_generalized_l3_accounts_for_every_byte(build, mcf_136):
    report = verify.generalized_l3(mcf_136, build["final"], build["edits"])
    assert report.grew_by == 172
    assert report.counts["insertion"] == 2
    assert report.counts["retarget"] == 3            # set_label + two coords of set_position
    assert report.counts["record-count"] == 1
    assert report.counts["shift"] > 0 and report.counts["checksum"] > 0


# --------------------------------------------------------------------------------------
# the retarget hit the CLONE, not the donor
# --------------------------------------------------------------------------------------
def test_retarget_edits_all_land_inside_the_clone_span(build):
    final, ni = build["final"], build["new_index"]
    gs, _ = guidata.guidata_extent(final)
    start, end = insert._object_span(final, ni)
    retarget = [e for e in build["edits"]
                if e.structure not in (verify.SHIFT_STRUCTS | verify.INSERT_STRUCTS
                                       | {verify.CHECKSUM_STRUCT, verify.RECORD_COUNT_STRUCT,
                                          verify.RECORD_HEAD_STRUCT})]
    assert retarget, "no retarget edits found"
    for e in retarget:
        assert gs + start <= e.file_offset and e.file_offset + len(e.new) <= gs + end


def test_donor_original_object_still_reads_inverter(build, mcf_136):
    # object 1409's own bytes are unchanged: the retarget touched the clone, not the donor
    gs0, _ = guidata.guidata_extent(mcf_136)
    s0, e0 = insert._object_span(mcf_136, relocate_demo.DONOR_INDEX)
    gs1, _ = guidata.guidata_extent(build["final"])
    s1, e1 = insert._object_span(build["final"], relocate_demo.DONOR_INDEX)
    assert build["final"][gs1 + s1:gs1 + e1] == mcf_136[gs0 + s0:gs0 + e0]


# --------------------------------------------------------------------------------------
# the decoded render: new button present at its retargeted position, donor present
# --------------------------------------------------------------------------------------
def _home_labels(data):
    scr = layout.Screen(data)
    scr.walk(relocate_demo.HOME_PAGE_INDEX)
    return scr.placed


def test_decoded_home_shows_new_label_and_keeps_the_donor(build):
    placed = _home_labels(build["final"])
    by_label = {p.label: p for p in placed if p.label}
    assert relocate_demo.NEW_LABEL in by_label, "the new button's label is not on the home page"
    assert relocate_demo.DONOR_LABEL in by_label, "the donor's original control vanished"
    new = by_label[relocate_demo.NEW_LABEL]
    donor = by_label[relocate_demo.DONOR_LABEL]
    # the new button is its own control at a distinct location, not sitting on the donor
    assert new.obj_index == build["new_index"]
    assert donor.obj_index == relocate_demo.DONOR_INDEX
    assert (new.x0, new.y0, new.x1, new.y1) != (donor.x0, donor.y0, donor.x1, donor.y1)
    # set_position placed the new button's box far corner where we asked
    assert (new.x1, new.y1) == relocate_demo.NEW_POSITION


def test_stock_home_has_no_new_label(mcf_136):
    # positive control: the decoded-render assertion above is not trivially true
    labels = {p.label for p in _home_labels(mcf_136)}
    assert relocate_demo.NEW_LABEL not in labels
    assert relocate_demo.DONOR_LABEL in labels


# --------------------------------------------------------------------------------------
# generalised L3 is load-bearing: mutate -> confirm it rejects
# --------------------------------------------------------------------------------------
def test_l3_rejects_a_flipped_output_byte(build, mcf_136):
    # flip a byte inside the clone that no edit covers -> unexplained difference
    final, ni = build["final"], build["new_index"]
    gs, _ = guidata.guidata_extent(final)
    start, _end = insert._object_span(final, ni)
    pos = gs + start                          # first object byte, not a retarget target
    tampered = bytearray(final)
    tampered[pos] ^= 0xFF
    with pytest.raises(verify.VerifyError):
        verify.generalized_l3(mcf_136, bytes(tampered), build["edits"])


def test_l3_rejects_a_dropped_edit(build, mcf_136):
    # drop one shift edit -> reconstruction no longer matches final. Pick an edit at a position
    # carrying only ONE edit: a doubly-shifted position (append then attach) would have its
    # value restored by the surviving edit (last-write-wins), so dropping one is invisible there.
    from collections import Counter
    per_pos = Counter(e.file_offset for e in build["edits"])
    dropped = next(i for i, e in enumerate(build["edits"])
                   if e.structure == "object-table-entry" and per_pos[e.file_offset] == 1)
    edits = build["edits"][:dropped] + build["edits"][dropped + 1:]
    with pytest.raises(verify.VerifyError):
        verify.generalized_l3(mcf_136, build["final"], edits)


def test_l3_rejects_a_mis_valued_shift_edit(build, mcf_136):
    # corrupt a shift edit's value: it no longer reconstructs, and its delta is no longer an
    # insertion length -- either failure is a rejection
    i = next(i for i, e in enumerate(build["edits"])
             if e.structure == "object-table-entry")
    e = build["edits"][i]
    bad = dataclasses.replace(e, new=bytes([(e.new[0] + 1) & 0xFF]) + e.new[1:])
    edits = build["edits"][:i] + [bad] + build["edits"][i + 1:]
    with pytest.raises(verify.VerifyError):
        verify.generalized_l3(mcf_136, build["final"], edits)


def test_l3_reconstruction_proof_is_necessary(build, mcf_136):
    """Isolate proof 1 (reconstruct-and-compare). Corrupt a retarget edit's VALUE in place --
    same offset, same length, still inside the clone span -- keeping the real `final`. Proof 2
    (categorisation) still sees a legitimate retarget edit in the clone span, and proof 3 still
    finds the position covered, so NEITHER catches it; only reconstruct-and-compare does (the
    rebuilt image now diverges from `final` at that byte). This pins proof 1's necessity: neuter
    it and this case is silently accepted.
    """
    retarget = [(i, e) for i, e in enumerate(build["edits"])
                if e.structure not in (verify.SHIFT_STRUCTS | verify.INSERT_STRUCTS
                                       | {verify.CHECKSUM_STRUCT, verify.RECORD_COUNT_STRUCT,
                                          verify.RECORD_HEAD_STRUCT})]
    assert retarget, "no retarget edit to corrupt"
    i, e = retarget[0]
    bad_new = bytes([e.new[0] ^ 0xFF]) + e.new[1:]          # same offset, same length, wrong byte
    bad = dataclasses.replace(e, new=bad_new)
    edits = build["edits"][:i] + [bad] + build["edits"][i + 1:]
    with pytest.raises(verify.VerifyError):
        verify.generalized_l3(mcf_136, build["final"], edits)


def test_l3_rejects_a_retarget_edit_outside_the_clone(build, mcf_136):
    # a fabricated same-size edit that reconstructs correctly (old==new so `final` is unchanged)
    # but lands OUTSIDE the clone span must still be rejected as unexplained/illegitimate
    final = build["final"]
    outside = 0x100                                   # in the container header, far from the clone
    fake = patch.Edit(outside, final[outside:outside + 1], final[outside:outside + 1],
                      "container-header", "fabricated no-op outside the clone")
    with pytest.raises(verify.VerifyError):
        verify.generalized_l3(mcf_136, final, build["edits"] + [fake])


def test_l3_record_head_must_overwrite_only_pad(build, mcf_136):
    """The record-head classification is load-bearing: a record-head edit is only legitimate when
    it overwrites the parent's zero pad (`old` all-zero) abutting an insertion. Claim it overwrote
    a non-zero byte and generalised L3 must reject it -- so a real attach-head that clobbered live
    record bytes could never pass as a benign pad overwrite. This build carries exactly one
    record-head edit (parent 4's content was not 4-aligned), so the guard is actually exercised."""
    heads = [(i, e) for i, e in enumerate(build["edits"])
             if e.structure == verify.RECORD_HEAD_STRUCT]
    assert len(heads) == 1, "this build should emit exactly one record-head edit"
    i, e = heads[0]
    assert all(b == 0 for b in e.old)                      # the real one overwrites pad
    bad = dataclasses.replace(e, old=bytes([0x01]) + e.old[1:])   # pretend it overwrote a live byte
    edits = build["edits"][:i] + [bad] + build["edits"][i + 1:]
    with pytest.raises(verify.VerifyError):
        verify.generalized_l3(mcf_136, build["final"], edits)


# --------------------------------------------------------------------------------------
# the demo writer produces the artifacts
# --------------------------------------------------------------------------------------
def test_write_demo_emits_bin_and_png(tmp_path):
    result = relocate_demo.write_demo(out_dir=str(tmp_path))
    binp = tmp_path / "relocator-new-button-13.6.bin"
    pngp = tmp_path / "relocator-new-button-13.6.png"
    assert binp.exists() and pngp.exists()
    # the written MCF reloads, decodes, and still passes the boundary oracle
    reload = binp.read_bytes()
    assert reload == result["final"]
    ok, reason = relocate._boundary_oracle(reload)
    assert ok, reason
    from PIL import Image
    with Image.open(pngp) as im:
        assert im.size == (800, 480)


def test_build_leaves_no_stale_guicode_pointer(build, mcf_136):
    """Regression for the flashed boot loop: the relocator inserts the new object, which shifts
    GuiData structures GuiCode points at by absolute address. Every GuiCode->GuiData pointer in
    the finished build must be relocated to track its target (value bumped consistently, never
    left aiming at pre-shift content). Re-derive the pointer sets and check each one moved by the
    net growth if its target was above the insertion, unchanged otherwise -- so nothing GuiCode
    dereferences at boot points into shifted GuiData."""
    from fireflymcf import theme
    final = build["final"]
    before = dict(theme.guicode_guidata_pointers(mcf_136))
    after = dict(theme.guicode_guidata_pointers(final))
    assert set(before) == set(after) and before
    grew = len(final) - len(mcf_136)
    for pos, rel_b in before.items():
        # a target above the insertion moved by the total growth; below it, unchanged. The
        # insertion point is not exposed here, so assert the value is EITHER unchanged OR +grew,
        # and that the two high tables specifically moved.
        assert after[pos] in (rel_b, rel_b + grew), f"GuiCode @ {pos:#x} shifted oddly"
    # the two high pointer tables GuiCode references must have moved by the growth
    high = {rel for rel in before.values() if rel >= 0x74000}
    assert high, "expected GuiCode pointers into the high (shifted) region"
    for pos, rel_b in before.items():
        if rel_b >= 0x74000:
            assert after[pos] == rel_b + grew, f"high GuiCode ptr @ {pos:#x} not relocated"
