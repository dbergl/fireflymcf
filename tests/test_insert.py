"""Task 2: clone an object and insert it as a new child of a page.

Every test insists on more than "it ran": the clone is proven byte-identical to its donor and
to decode into the same records; the inserted build is put through the decoder **boundary
oracle** (every object, including the brand-new top-index one, decodes to its boundary with
zero derailments), full **deep coverage** (no unclaimed byte, no overlap), **graph
equivalence** (the decoded object graph equals the input's plus exactly one node, reachable
from the intended parent, and nothing else), and a **container re-parse** (the CRCs verify).

The guard tests prove the checks are load-bearing rather than vacuous: `_verify_growth_shifts`
is shown to reject a hand-corrupted table (so it is not passing on anything); the
graph-equivalence comparison is shown to distinguish the before and after graphs (so its
equality assertion is not trivially true); a misaligned object and an out-of-range index are
shown to raise rather than corrupt.

Building an insertion touches the whole section and runs `relocate`'s multi-second self-check,
so each build is produced once per version by a module-scoped fixture and shared across the
assertions.
"""
from __future__ import annotations

import struct

import pytest

from fireflymcf import container, coverage, guidata, insert, records, relocate

VERSIONS = ["11.23", "13.6", "14.7"]


def _find_parent_and_donor(data: bytes) -> tuple[int, int]:
    """The first object-table-1 object that carries a wtype-5 container child record, and one
    of its existing children (a real, decodable object) to clone. This is exactly the home-page
    shape (`progress.md` preflight: children are individual wtype-5 records, not a range)."""
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX]
    for index, start in enumerate(table):
        try:
            recs = records.object_records(data, gs + start)
        except Exception:  # noqa: BLE001
            continue
        kids = [r.child_index for r in recs if r.wtype == 5 and r.child_index is not None]
        if kids:
            return index, kids[0]
    raise AssertionError("no container-bearing object found")


def _leaf_object(data: bytes) -> int:
    """An object-table-1 object that decodes but holds no wtype-5 container child record --
    an unsuitable attachment parent, for the negative test."""
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX]
    for index, start in enumerate(table):
        try:
            recs = records.object_records(data, gs + start)
        except Exception:  # noqa: BLE001
            continue
        if not any(r.wtype == 5 and r.child_index is not None for r in recs):
            return index
    raise AssertionError("every object has a container record?")


@pytest.fixture(scope="module", params=VERSIONS)
def built(request, corpus):
    """`(version, data, parent, donor, new_index, out, edits)` for one version -- the insertion
    performed once and reused. `corpus` is the session fixture from `conftest.py`."""
    version = request.param
    data = corpus[version]["mcf"]
    parent, donor = _find_parent_and_donor(data)
    object_bytes = insert.clone_object(data, donor)
    out, edits = insert.insert_object(data, object_bytes, parent)
    new_index = len(guidata.object_tables(out)[insert.OBJECT_TABLE_INDEX]) - 1
    return {
        "version": version, "data": data, "parent": parent, "donor": donor,
        "object_bytes": object_bytes, "new_index": new_index, "out": out, "edits": edits,
    }


# --------------------------------------------------------------------------------------
# clone
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("version", VERSIONS)
def test_clone_is_the_exact_span_and_decodes_like_the_donor(version, corpus):
    data = corpus[version]["mcf"]
    _, donor = _find_parent_and_donor(data)
    gs, _ = guidata.guidata_extent(data)
    start, end = insert._object_span(data, donor)

    clone = insert.clone_object(data, donor)
    assert clone == data[gs + start:gs + end]
    assert len(clone) % 4 == 0  # object spans are 4-aligned; the clone inherits that

    donor_recs = records.object_records(data, gs + start)
    clone_recs = records.object_records(clone, 0)
    assert [(r.wtype, r.geom, r.child_index, r.label) for r in donor_recs] \
        == [(r.wtype, r.geom, r.child_index, r.label) for r in clone_recs]


@pytest.mark.parametrize("version", VERSIONS)
def test_clone_out_of_range_raises(version, corpus):
    data = corpus[version]["mcf"]
    n = len(guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX])
    with pytest.raises(insert.InsertError):
        insert.clone_object(data, -1)
    with pytest.raises(insert.InsertError):
        insert.clone_object(data, n)
    with pytest.raises(insert.InsertError):
        insert.clone_object(data, n + 1000)


# --------------------------------------------------------------------------------------
# insert -- the correctness gates
# --------------------------------------------------------------------------------------
def test_insert_appends_exactly_one_object_at_the_top_index(built):
    before = guidata.object_tables(built["data"])[insert.OBJECT_TABLE_INDEX]
    after = guidata.object_tables(built["out"])[insert.OBJECT_TABLE_INDEX]
    assert len(after) == len(before) + 1
    assert built["new_index"] == len(before)  # the appended index is the old length

    # object-table 1 grew by exactly one; EVERY other table (0, 2, 3) kept its length
    before_lens = [len(t) for t in guidata.object_tables(built["data"])]
    after_lens = [len(t) for t in guidata.object_tables(built["out"])]
    assert len(after_lens) == len(before_lens)
    for i, (b, a) in enumerate(zip(before_lens, after_lens)):
        expected = b + 1 if i == insert.OBJECT_TABLE_INDEX else b
        assert a == expected, f"table {i}: {b} -> {a}, expected {expected}"


def test_insert_passes_the_boundary_oracle(built):
    ok, reason = relocate._boundary_oracle(built["out"])
    assert ok, reason
    # and the NEW top-index object specifically decodes to its boundary
    gs, _ = guidata.guidata_extent(built["out"])
    table = guidata.object_tables(built["out"])[insert.OBJECT_TABLE_INDEX]
    start, end = insert._object_span(built["out"], built["new_index"])
    recs = records.object_records(built["out"], gs + start)
    assert recs, "the new object decoded to zero records"
    assert 0 <= (gs + end) - recs[-1].end < 4  # lands within padding of its boundary


def test_insert_has_full_zero_unclaimed_coverage(built):
    report = coverage.analyse(built["out"], deep=True)
    assert report.unclaimed == []
    assert report.claimed == report.total


def test_insert_reparses_as_a_container_with_valid_crcs(built):
    # parse_container itself checks the extent chain; reseal would have raised on a bad CRC.
    c = container.parse_container(built["out"])
    assert any(guidata.GUIDATA_SECTION_MARKER in s.name for s in c.sections)
    # file grew by the object (+ its pointer) plus the padded attachment record
    grew = len(built["out"]) - len(built["data"])
    assert grew == len(built["object_bytes"]) + 4 + _attach_growth(built)


def _attach_growth(built) -> int:
    """The byte count the attachment step added: the net insertion after the parent's own
    trailing pad is absorbed -- `round4(C + rl) - round4(C)`, C = the parent's content length,
    rl = the cloned container record length (see `insert.insert_object`'s padding note). This
    is a multiple of 4 and reduces to `round4(rl)` only when the parent content was already
    4-aligned."""
    data = built["data"]
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX]
    parent_off = gs + table[built["parent"]]
    recs = records.object_records(data, parent_off)
    template = next(r for r in recs if r.wtype == 5 and r.child_index is not None)
    c_old = recs[-1].end - parent_off
    rl = template.size

    def round4(n: int) -> int:
        return (n + 3) & ~3

    return round4(c_old + rl) - round4(c_old)


def test_new_object_is_byte_identical_to_the_donor(built):
    gs, _ = guidata.guidata_extent(built["out"])
    table = guidata.object_tables(built["out"])[insert.OBJECT_TABLE_INDEX]
    start, end = insert._object_span(built["out"], built["new_index"])
    assert built["out"][gs + start:gs + end] == built["object_bytes"]


def test_graph_equals_input_plus_one_node_under_the_parent(built):
    before = insert.object_child_graph(built["data"])
    after = insert.object_child_graph(built["out"])
    parent, donor, new = built["parent"], built["donor"], built["new_index"]

    # the equality below is only meaningful if the two graphs actually differ -- prove it
    assert after != before, "graph did not change; the equivalence check would be vacuous"

    expected = dict(before)
    expected[parent] = sorted(before[parent] + [new])   # parent gained the new child
    expected[new] = before[donor]                        # clone carries the donor's children
    assert after == expected


# --------------------------------------------------------------------------------------
# positioned attach -- place the child at an absolute (x, y)
# --------------------------------------------------------------------------------------
def _positioned_parent_and_donor(data: bytes) -> tuple[int, int]:
    """The first object carrying a wtype-5 container record that ALSO bears x/y geometry
    (geom[0] and geom[1] present) -- the template a positioned attach needs -- and one of its
    children to clone. HOME is such an object (its child-907 template geom `[70, 49]`)."""
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX]
    for index, start in enumerate(table):
        try:
            recs = records.object_records(data, gs + start)
        except Exception:  # noqa: BLE001
            continue
        t = next((r for r in recs if r.wtype == 5 and r.child_index is not None
                  and r.geom[0] is not None and r.geom[1] is not None), None)
        if t is not None:
            return index, t.child_index
    raise AssertionError("no object with a geometry-bearing container template found")


@pytest.mark.parametrize("version", VERSIONS)
def test_positioned_insert_places_child_at_xy_and_passes_gates(version, corpus):
    data = corpus[version]["mcf"]
    parent, donor = _positioned_parent_and_donor(data)
    obj = insert.clone_object(data, donor)

    # A distinctive (x, y) unlikely to equal any template default, so the geom assertion below
    # cannot pass vacuously on an unmodified template.
    X, Y = 321, 234
    out, edits = insert.insert_object(data, obj, parent, position=(X, Y))
    new_index = len(guidata.object_tables(out)[insert.OBJECT_TABLE_INDEX]) - 1

    # gates still hold
    ok, reason = relocate._boundary_oracle(out)
    assert ok, reason
    assert coverage.analyse(out, deep=True).unclaimed == []

    # the parent's new attach record points at the child AND carries the requested (x, y)
    gs, _ = guidata.guidata_extent(out)
    table = guidata.object_tables(out)[insert.OBJECT_TABLE_INDEX]
    precs = records.object_records(out, gs + table[parent])
    att = next(r for r in precs if r.wtype == 5 and r.child_index == new_index)
    assert att.geom[0] == X and att.geom[1] == Y, (
        f"attach record geom is {att.geom[:2]}, expected [{X}, {Y}]")


@pytest.mark.parametrize("version", VERSIONS)
def test_positioned_insert_geom_assertion_is_not_vacuous(version, corpus):
    """Prove-can-fail companion: the same insert with a DIFFERENT position lands that other
    (x, y), so the equality above is really reading the record, not passing trivially."""
    data = corpus[version]["mcf"]
    parent, donor = _positioned_parent_and_donor(data)
    obj = insert.clone_object(data, donor)
    out, _ = insert.insert_object(data, obj, parent, position=(111, 222))
    new_index = len(guidata.object_tables(out)[insert.OBJECT_TABLE_INDEX]) - 1
    gs, _ = guidata.guidata_extent(out)
    table = guidata.object_tables(out)[insert.OBJECT_TABLE_INDEX]
    att = next(r for r in records.object_records(out, gs + table[parent])
               if r.wtype == 5 and r.child_index == new_index)
    assert (att.geom[0], att.geom[1]) == (111, 222)
    assert (att.geom[0], att.geom[1]) != (321, 234)   # would have caught a stuck value


@pytest.mark.parametrize("version", VERSIONS)
def test_positioned_insert_into_a_geomless_parent_raises(version, corpus):
    """A parent whose only wtype-5 container records lack x/y geometry cannot satisfy a
    positioned attach -- it is a clean refusal, not a mis-placed child at (0, 0)."""
    data = corpus[version]["mcf"]
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX]
    geomless = None
    for index, start in enumerate(table):
        try:
            recs = records.object_records(data, gs + start)
        except Exception:  # noqa: BLE001
            continue
        kids = [r for r in recs if r.wtype == 5 and r.child_index is not None]
        if kids and all(r.geom[0] is None or r.geom[1] is None for r in kids):
            geomless = index
            break
    if geomless is None:
        pytest.skip("no container-bearing object without any geometry template on this build")
    _, donor = _positioned_parent_and_donor(data)
    obj = insert.clone_object(data, donor)
    with pytest.raises(insert.InsertError):
        insert.insert_object(data, obj, geomless, position=(50, 60))


# --------------------------------------------------------------------------------------
# refusals (out-of-range / unsuitable / misaligned) -- raise, never corrupt
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("version", VERSIONS)
def test_insert_out_of_range_parent_raises(version, corpus):
    data = corpus[version]["mcf"]
    _, donor = _find_parent_and_donor(data)
    obj = insert.clone_object(data, donor)
    n = len(guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX])
    with pytest.raises(insert.InsertError):
        insert.insert_object(data, obj, -1)
    with pytest.raises(insert.InsertError):
        insert.insert_object(data, obj, n)


@pytest.mark.parametrize("version", VERSIONS)
def test_insert_into_a_leaf_parent_raises(version, corpus):
    data = corpus[version]["mcf"]
    _, donor = _find_parent_and_donor(data)
    obj = insert.clone_object(data, donor)
    leaf = _leaf_object(data)
    with pytest.raises(insert.InsertError):
        insert.insert_object(data, obj, leaf)


@pytest.mark.parametrize("version", VERSIONS)
def test_a_misaligned_object_is_refused(version, corpus):
    data = corpus[version]["mcf"]
    parent, donor = _find_parent_and_donor(data)
    obj = insert.clone_object(data, donor)
    with pytest.raises(insert.InsertError):
        insert.insert_object(data, obj + b"\x00", parent)   # length no longer a multiple of 4


# --------------------------------------------------------------------------------------
# the growth-shift gate is load-bearing (stub -> confirm break)
# --------------------------------------------------------------------------------------
def test_verify_growth_shifts_accepts_the_real_append_and_rejects_a_corruption(mcf_136):
    """Isolate the append step (`_append_object`) and prove its growth-aware shift gate both
    accepts the real result and rejects a one-entry corruption -- so a passing build is passing
    on merit, not vacuously."""
    data = mcf_136
    parent, donor = _find_parent_and_donor(data)
    obj = insert.clone_object(data, donor)
    tbl_end = min(guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX])
    delta = 4 + len(obj)
    new_entry_rel = tbl_end + 4

    g1, _edits, new_index = insert._append_object(data, obj)

    # the real append passes its own gate re-derived independently here
    insert._verify_growth_shifts(data, g1, tbl_end, delta, new_entry_rel)

    # corrupt the appended (top-index) entry's stored value by +4 and confirm the gate fires
    gs, _ = guidata.guidata_extent(g1)
    table_start_abs = relocate._verified_pointer_tables(g1)[insert.OBJECT_TABLE_INDEX][0]
    n = len(guidata.object_tables(g1)[insert.OBJECT_TABLE_INDEX])
    pos = table_start_abs + 4 * (n - 1)
    corrupt = bytearray(g1)
    val = struct.unpack_from("<I", corrupt, pos)[0]
    struct.pack_into("<I", corrupt, pos, val + 4)
    with pytest.raises(insert.InsertError):
        insert._verify_growth_shifts(data, bytes(corrupt), tbl_end, delta, new_entry_rel)

    # and corrupting a NON-trailing (ordinary) entry is caught too
    corrupt2 = bytearray(g1)
    struct.pack_into("<I", corrupt2, table_start_abs,
                     struct.unpack_from("<I", corrupt2, table_start_abs)[0] + 4)
    with pytest.raises(insert.InsertError):
        insert._verify_growth_shifts(data, bytes(corrupt2), tbl_end, delta, new_entry_rel)
