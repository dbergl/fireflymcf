"""`celltables.locate`: the display-cell table and its parallel type table, found from GuiCode's
bound compares on any image (stock 11.23/13.6/14.7, and a grown image used as stock).

The expected values are test expectations only (measured 2026-11-07); none is a locator input.
"""
from __future__ import annotations

import dataclasses
import struct
from pathlib import Path

import pytest

from fireflymcf import celltables, guidata, recipe, theme

REPO_ROOT = Path(__file__).resolve().parent.parent
GD = 0x90000000
_GROWN_BUILDS = sorted((REPO_ROOT / "builds").glob("*.bin"))


def test_locate_136(mcf_136):
    ct = celltables.locate(mcf_136)
    assert (ct.cell_off, ct.type_off, ct.rows) == (0x44BC8, 0x4492C, 665)
    assert (ct.cell_base, ct.type_base) == (0x90044BC8, 0x9004492C)
    assert ct.bound_form == "cmp.w"
    assert len(ct.gate_sites) == 8 and 0x2533BA in ct.gate_sites
    assert ct.clamp_sites == (0x25383C, 0x253870, 0x25388E, 0x2538AC, 0x2538C6, 0x2538E4)
    assert ct.bound_sites == tuple(sorted(ct.gate_sites + ct.clamp_sites))
    # each clamp names its store and the read that indexes the cell table with what it stored
    assert ("clamp 0x25383c (cmp.w): strh at 0x25383a stores the index to 0x20036da8; ldrh at "
            "0x258208 reads it back and indexes the cell table at 0x25820c") in ct.proof
    assert ct.cell_refs == (0x2535B4, 0x25385C, 0x253D04, 0x25689C, 0x257E68, 0x258020,
                            0x2582C0)
    assert ct.type_refs == (0x253D08, 0x257E6C, 0x258024, 0x2582C4)
    assert any("align4(665)" in p for p in ct.proof)


def test_locate_147(mcf_147):
    ct = celltables.locate(mcf_147)
    assert (ct.type_off, ct.rows) == (0x42978, 505)
    assert ct.cell_off == ct.type_off + 0x1FC          # align4(505): the cell table as found
    assert ct.bound_form == "cmp.w"
    assert 0x255C82 in ct.gate_sites and len(ct.gate_sites) == 8
    assert ct.clamp_sites == (0x256104, 0x256138, 0x256156, 0x256174, 0x25618E, 0x2561AC)


def test_locate_1123_reads_the_movw_bound(mcf_1123):
    ct = celltables.locate(mcf_1123)
    assert (ct.cell_off, ct.type_off, ct.rows) == (0x44B60, 0x4492C, 562)
    assert ct.bound_form == "movw+cmp"
    assert 0x250CEA in ct.gate_sites
    assert len(ct.clamp_sites) == 6 and 0x251548 in ct.clamp_sites


def test_accessors_follow_locate(corpus):
    for version in ("11.23", "13.6", "14.7"):
        data = corpus[version]["mcf"]
        ct = celltables.locate(data)
        assert celltables.cell_table_off(data) == ct.cell_off, version
        assert celltables.type_table_off(data) == ct.type_off, version
        assert celltables.row_count(data) == ct.rows, version


def test_find_variable_tables_delegates(mcf_147, monkeypatch):
    assert guidata.find_variable_tables(mcf_147) == (0x42B74, 0x42978)

    def refuse(_data):
        raise celltables.CellTableError("CELL_TABLE_NO_BOUND", "stubbed")
    monkeypatch.setattr(celltables, "locate", refuse)
    with pytest.raises(guidata.VariableTableNotFound):
        guidata.find_variable_tables(mcf_147)


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_agrees_with_variable_type_table(corpus, version):
    data = corpus[version]["mcf"]
    assert celltables.locate(data).type_off == guidata.variable_type_table(data)


@pytest.mark.skipif(not _GROWN_BUILDS, reason="research builds/ not linked")
@pytest.mark.parametrize("path", _GROWN_BUILDS, ids=lambda p: p.name)
def test_locate_on_grown_builds(path):
    """A grown image used as stock: the live tables are the relocated pair GuiCode loads, never
    the stale stock copy left in GuiData (which no GuiCode literal names any more)."""
    from fireflymcf import tablegrow
    data = path.read_bytes()
    ct = celltables.locate(data)
    assert ct.type_off == guidata.variable_type_table(data)
    assert guidata.find_variable_tables(data) == (ct.cell_off, ct.type_off)
    if ct.rows > 665:                       # grown: the relocated pair, the stock copy unread
        assert ct.cell_off != 0x44BC8 and ct.type_off != 0x4492C
        assert tablegrow.find_base_refs(data, 0x90044BC8) == []
    else:                                   # an ungrown 13.6 build: the stock pair, as stock
        assert (ct.cell_off, ct.type_off, ct.rows) == (0x44BC8, 0x4492C, 665)
    gs, _ge = guidata.guidata_extent(data)
    words = struct.unpack_from(f"<{ct.rows}I", data, gs + ct.cell_off)
    assert all(w == 0 or guidata.SRAM_LOW <= w < guidata.SRAM_HIGH for w in words)


def _second_bound(data: bytes) -> bytes:
    """13.6 with one of the cell table's eight `cmp.w r,#0x298` bounds re-encoded to #0x290."""
    from fireflymcf import tablegrow
    sec = theme.guicode_section(data)
    base = theme.guicode_base(sec)
    off = sec.offset + 0x2533BA - base
    out = bytearray(data)
    out[off:off + 4] = tablegrow._reencode_cmpw(bytes(data[off:off + 4]), 0x290)
    return bytes(out)


def test_a_second_bound_refuses(mcf_136):
    with pytest.raises(celltables.CellTableError) as e:
        celltables.locate(_second_bound(mcf_136))
    assert e.value.code == "CELL_TABLE_BOUND_AMBIGUOUS"
    assert "0x290" in str(e.value) and "0x298" in str(e.value)


def test_no_bound_refuses(mcf_136, monkeypatch):
    """The locator fails when the bound scan finds nothing (the test can fail)."""
    monkeypatch.setattr(celltables, "_gated", lambda *a: {})
    celltables._locate.cache_clear()
    try:
        with pytest.raises(celltables.CellTableError) as e:
            celltables.locate(mcf_136)
        assert e.value.code == "CELL_TABLE_NO_BOUND"
    finally:
        celltables._locate.cache_clear()


def test_disagreement_with_the_literal_pair_refuses(mcf_136, monkeypatch):
    real = guidata.variable_table_literals(mcf_136)
    monkeypatch.setattr(guidata, "variable_table_literals",
                        lambda d: dataclasses.replace(real, type_off=real.type_off - 8))
    with pytest.raises(celltables.CellTableError) as e:
        celltables.locate(mcf_136)
    assert e.value.code == "TYPE_TABLE_DISAGREES"


def test_adjacency_refuses(mcf_136, monkeypatch):
    """A lone type candidate that does not end where the cell table starts is refused."""
    real = celltables._gated

    def shifted(code, base, lits):
        out = dict(real(code, base, lits))
        out[0x90044920] = out.pop(0x9004492C)       # a type table 12 bytes early
        return out
    monkeypatch.setattr(celltables, "_gated", shifted)
    celltables._locate.cache_clear()
    try:
        with pytest.raises(celltables.CellTableError) as e:
            celltables.locate(mcf_136)
        assert e.value.code == "CELL_TABLE_ADJACENCY"
    finally:
        celltables._locate.cache_clear()


def test_error_is_coded_for_recipes():
    assert issubclass(celltables.CellTableError, guidata.VariableTableNotFound)
    assert celltables.CellTableError in recipe._CODED


# --- Task 2: the render state, the cell area, spare cells (measured 2026-11-07/08) ----------
#
# Every number below is a test expectation, never a locator input: `locate_render_state` finds
# the one SRAM base GuiCode addresses through `movw` offsets, and `cell_area` runs from its end
# to the first register-indexed base above it.

from fireflymcf import readout, routinekit, sramreach, statusbind, status_map  # noqa: E402

_FREE_RUNS4_136 = "c537e60ecca5fe7ab81c127dd6b729b9caceec1fac3afb73c5238cd4791827ac"
_FREE_RUNS8_136 = "1ce9b3786f0da57a3110325f3c07abaf861f16d0709992766efd7ca889638ffa"
_FREE_WORDS_136 = "f6a7ecb8785dccd61150cd75f0f1e0b1609798b94c7b4173f7ae94fdddd2d8e8"
_FRESH_CELL_136 = "af03339157ec6ab857107c862e79d3cdab3664fb990b48479b4799da4e4685b9"


def _sha(obj) -> str:
    import hashlib
    return hashlib.sha256(repr(obj).encode()).hexdigest()


def test_render_state_136(mcf_136):
    rs = sramreach.locate_render_state(mcf_136)
    assert (rs.base, rs.end) == (0x200301B0, 0x20039868)        # max movw offset 0x96B4
    assert len(rs.movw_sites) == 591
    assert rs.indexed_fields == (0x200301B0, 0x200371D1)        # the digit buffer is a field
    assert sramreach.cell_area(mcf_136) == (0x20039868, 0x2003A8B8)


def test_render_state_147(mcf_147):
    rs = sramreach.locate_render_state(mcf_147)
    assert (rs.base, rs.end) == (0x200301B4, 0x20039548)
    assert len(rs.movw_sites) == 597
    assert rs.indexed_fields == (0x200301B4, 0x200371D5)
    lo, hi = sramreach.cell_area(mcf_147)
    assert (lo, hi) == (0x20039548, 0x2003A3D0)
    cells = [c for c in status_map.cell_table(mcf_147) if c]
    assert cells and all(lo <= c < hi for c in cells)


def test_render_state_1123(mcf_1123):
    rs = sramreach.locate_render_state(mcf_1123)
    assert rs.base == 0x20030160
    assert sramreach.cell_area(mcf_1123) == (0x20039284, 0x20039ACC)


def test_free_runs_136_unchanged(mcf_136):
    """The located window and the struct-bounded field rule reproduce the old pinned answer."""
    runs = sramreach.free_runs(mcf_136, 4)
    assert len(runs) == 225 and sum(n for _, n in runs) == 1986
    assert runs[0] == (0x2003A858, 44)
    assert _sha(runs) == _FREE_RUNS4_136
    assert _sha(sramreach.free_runs(mcf_136)) == _FREE_RUNS8_136
    assert _sha(sramreach.free_words(mcf_136)) == _FREE_WORDS_136


def test_allocate_fresh_cells_136_unchanged(mcf_136):
    import hashlib
    out, cells, edits = readout.allocate_fresh_cells(mcf_136, [(2, 3)])
    assert (cells[0].slot, cells[0].addr) == (665, 0x2003A858)
    assert len(edits) == 31
    assert hashlib.sha256(out).hexdigest() == _FRESH_CELL_136


def test_free_runs_147_lie_in_the_area(mcf_147):
    lo, hi = sramreach.cell_area(mcf_147)
    runs = sramreach.free_runs(mcf_147, 4)
    assert len(runs) == 200 and runs[0] == (0x20039F20, 64)
    assert all(lo <= s and s + n <= hi for s, n in runs)
    assert all(lo <= a and a + 4 <= hi for a in sramreach.free_words(mcf_147))


def test_fresh_sram_works_on_147(mcf_147):
    """Plan 2 left this refusing NO_SRAM: the 13.6 render-state pin poisoned all of 14.7's
    SRAM. The located render state bounds it, and the longest run opens at 0x20039F20."""
    lo, hi = sramreach.cell_area(mcf_147)
    _, addr = routinekit.fresh_sram(mcf_147, 4)
    assert lo <= addr and addr + 4 <= hi
    assert addr == 0x20039F20
    assert sramreach.reaching_forms(mcf_147, addr, 4) == {
        "raw_word": [], "base_offset": [], "state_struct": False, "indexed_table": [],
        "cell_table": []}


def test_struct_fields_are_bounded_by_the_struct(mcf_136, mcf_147):
    """A register-indexed base inside the render state is a field: its reach ends at the
    struct's end, never at SRAM_HIGH. On 13.6 that is inside the state span the struct already
    claims, so `referenced` is unchanged (test_free_runs_136_unchanged)."""
    for data in (mcf_136, mcf_147):
        rs = sramreach.locate_render_state(data)
        r = sramreach.reach(data)
        assert r.state_span == (rs.base, rs.end)
        for b in rs.indexed_fields:
            assert (b, sramreach.SRAM_HIGH) not in r.poisoned
        lo, hi = sramreach.cell_area(data)
        assert all(plo >= hi or phi <= lo for plo, phi in r.poisoned), r.poisoned


def test_render_state_refuses_zero_or_several_bases(mcf_136, monkeypatch):
    real = sramreach._trace

    def none(*a):
        t = real(*a)
        return dataclasses.replace(t, movw={})

    def two(*a):
        t = real(*a)
        movw = dict(t.movw)
        movw[0x20031000] = ((0x250000, 4),)
        return dataclasses.replace(t, movw=movw)
    for stub in (none, two):
        monkeypatch.setattr(sramreach, "_trace", stub)
        with pytest.raises(sramreach.SramReachError) as e:
            sramreach.locate_render_state(mcf_136)
        assert e.value.code == "RENDER_STATE_BASES"


def test_cell_area_refuses_no_indexed_base_above(mcf_136, monkeypatch):
    real = sramreach._trace

    def below_only(*a):
        t = real(*a)
        return dataclasses.replace(t, indexed={b: s for b, s in t.indexed.items()
                                               if b < 0x20039868})
    monkeypatch.setattr(sramreach, "_trace", below_only)
    with pytest.raises(sramreach.SramReachError) as e:
        sramreach.cell_area(mcf_136)
    assert e.value.code == "CELL_AREA_NO_CEILING"


def test_cell_area_refuses_a_cell_outside(mcf_136):
    """A table row naming SRAM above the first indexed table is outside the area: refuse."""
    gs, _ = guidata.guidata_extent(mcf_136)
    out = bytearray(mcf_136)
    struct.pack_into("<I", out, gs + celltables.cell_table_off(mcf_136) + 203 * 4, 0x2003B000)
    with pytest.raises(sramreach.SramReachError) as e:
        sramreach.cell_area(bytes(out))
    assert e.value.code == "CELL_OUTSIDE_AREA"
    assert "0x2003b000" in str(e.value).lower()


def test_sramreach_error_is_coded_for_recipes():
    assert sramreach.SramReachError in recipe._CODED


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_no_spare_beyond_rows(corpus, version):
    data = corpus[version]["mcf"]
    rows = celltables.locate(data).rows
    spare = statusbind.spare_cell_indices(data)
    assert spare and all(0 <= i < rows for i in spare), [i for i in spare if i >= rows]


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_spare_not_read_elsewhere(corpus, version):
    """A "spare" another object table reads (14.7: 187, 193, 224, 230; 13.6: the 254-260
    background-bar cells) is not spare."""
    data = corpus[version]["mcf"]
    spare = statusbind.spare_cell_indices(data)
    assert status_map.foreign_table_refs(data, set(spare)) == {}
    known = {"13.6": {252, 254, 256, 258, 260}, "14.7": {187, 193, 224, 230},
             "11.23": {151, 152}}[version]
    assert not known & set(spare)


@pytest.mark.parametrize("version", ["11.23", "13.6", "14.7"])
def test_no_stale_pair_on_stock(corpus, version):
    assert celltables.stale_pair(corpus[version]["mcf"]) is None


@pytest.mark.skipif(not _GROWN_BUILDS, reason="research builds/ not linked")
@pytest.mark.parametrize("path", _GROWN_BUILDS, ids=lambda p: p.name)
def test_grown_build_spares_come_from_the_stock(path, mcf_136):
    """Every reference build grew from 13.6, whose stale pair stays at +0x44BC8. As its own
    stock it refuses spare reuse; inside a recipe whose stock is 13.6 stock, no grown row is
    offered."""
    from fireflymcf import stockscope
    data = path.read_bytes()
    assert celltables.stale_pair(data) == 0x44BC8
    with pytest.raises(statusbind.SpareRowsUnknown) as e:
        statusbind.spare_cell_indices(data)
    assert e.value.code == "SPARE_ROWS_UNKNOWN"
    with stockscope.stock_scope(mcf_136):
        spare = statusbind.spare_cell_indices(data)
    assert spare and max(spare) < 665


# the first grown reference build (the review reproduced the case on one, 2026-11-08)
_GROWN = _GROWN_BUILDS[0] if _GROWN_BUILDS else None


def _grown_with_a_repointed_spare() -> bytes:
    """The review's case: one stock spare row (173) repointed after the grow, as `index_cell`
    does. A prefix match against the stale copy stops at row 173 and loses the stock count."""
    data = bytearray(_GROWN.read_bytes())
    gs, _ = guidata.guidata_extent(bytes(data))
    struct.pack_into("<I", data, gs + celltables.cell_table_off(bytes(data)) + 173 * 4,
                     0x2003A000)
    return bytes(data)


@pytest.mark.skipif(_GROWN is None, reason="research builds/ not linked")
def test_repointed_spare_on_grown_image_offers_no_grown_row(mcf_136):
    from fireflymcf import stockscope
    data = _grown_with_a_repointed_spare()
    with stockscope.stock_scope(mcf_136):
        spare = statusbind.spare_cell_indices(data)
    assert spare and max(spare) < 665


@pytest.mark.skipif(_GROWN is None, reason="research builds/ not linked")
def test_grown_stock_refuses_spare_reuse():
    for data in (_GROWN.read_bytes(), _grown_with_a_repointed_spare()):
        with pytest.raises(statusbind.SpareRowsUnknown) as e:
            statusbind.spare_cell_indices(data)
        assert e.value.code == "SPARE_ROWS_UNKNOWN"


def test_spare_rows_unknown_is_coded_for_recipes():
    assert statusbind.SpareRowsUnknown in recipe._CODED
    assert issubclass(statusbind.SpareRowsUnknown, statusbind.StatusBindError)


@pytest.mark.skipif(_GROWN is None, reason="research builds/ not linked")
def test_stale_pair_needs_an_unread_copy(monkeypatch):
    """The test can fail: a copy GuiCode still loads is not stale, so the same image with every
    base reported as read has no stale pair."""
    from fireflymcf import tablegrow
    data = _GROWN.read_bytes()
    monkeypatch.setattr(tablegrow, "find_base_refs", lambda d, base: [0x240000])
    celltables._stale_pair.cache_clear()
    try:
        assert celltables._stale_pair(data, guidata.SRAM_LOW, guidata.SRAM_HIGH) is None
    finally:
        celltables._stale_pair.cache_clear()


def _halfword_moves_reusing(monkeypatch, before: tuple, after: tuple) -> tuple:
    """`(reused, fresh, walks_reused, walks_fresh)`: `_halfword_moves` on `after` evaluated
    right after `before` (so it may reuse `before`'s untouched starts), and from scratch, both
    through `__wrapped__` (the content cache would answer a repeat without evaluating), with
    the `_walk_half` runs each evaluation made."""
    calls = []
    real = celltables._walk_half

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(celltables, "_walk_half", counting)
    try:
        celltables._RECENT_HALF.clear()
        celltables._halfword_moves.__wrapped__(*before)
        calls.clear()
        reused = celltables._halfword_moves.__wrapped__(*after)
        walks_reused = len(calls)
        celltables._RECENT_HALF.clear()
        calls.clear()
        fresh = celltables._halfword_moves.__wrapped__(*after)
        walks_fresh = len(calls)
    finally:
        celltables._RECENT_HALF.clear()
    return reused, fresh, walks_reused, walks_fresh


def test_halfword_moves_reuse_equals_a_fresh_evaluation(mcf_136, monkeypatch):
    """`_halfword_moves` reuses a recent evaluation's untouched starts; after a grow (GuiCode
    literals repointed, bounds rebumped) the reused answer equals a from-scratch one, and the
    reuse happened (fewer `_walk_half` runs than the fresh evaluation, so the equality is not
    two fresh evaluations agreeing)."""
    from fireflymcf import readout, tablegrow
    grown, _cells, _edits = readout.allocate_fresh_cells(mcf_136, [(2, 3)])
    args = []
    for img in (mcf_136, grown):
        code, base = tablegrow._guicode_slice(img)
        args.append((bytes(code), base, guidata.SRAM_LOW, guidata.SRAM_HIGH))
    assert args[0][0] != args[1][0] and len(args[0][0]) == len(args[1][0])
    reused, fresh, walks_reused, walks_fresh = _halfword_moves_reusing(monkeypatch, *args)
    assert reused == fresh
    assert 0 < walks_reused < walks_fresh


def test_halfword_moves_reuse_survives_a_guicode_append(mcf_136, monkeypatch):
    """A length-changing edit (a routine appended to GuiCode): the appended start is walked,
    its store is found, and the reused answer equals a fresh one."""
    from fireflymcf import tablegrow
    code, base = tablegrow._guicode_slice(mcf_136)
    code = bytes(code) + bytes(-len(code) % 4)
    a = len(code)
    # ldr r0,[pc,#4] ; strh r1,[r0] ; bx lr ; nop ; .word 0x2003A000
    tail = struct.pack("<4HI", 0x4801, 0x8001, 0x4770, 0xBF00, 0x2003A000)
    before = (bytes(tablegrow._guicode_slice(mcf_136)[0]), base, guidata.SRAM_LOW,
              guidata.SRAM_HIGH)
    after = (code + tail, base, guidata.SRAM_LOW, guidata.SRAM_HIGH)
    reused, fresh, walks_reused, walks_fresh = _halfword_moves_reusing(monkeypatch, before, after)
    assert reused == fresh
    assert (1, 0x2003A000) in fresh[0][base + a + 2]
    assert 0 < walks_reused < walks_fresh


def test_area_comes_from_the_stock_not_a_user_pointed_cell(mcf_136):
    """A row an earlier op pointed outside the area is the working image's, not the stock's:
    inside the recipe's stock scope the area is the stock's and allocation goes on, while the
    working image's reach still counts the row's cell as used."""
    from fireflymcf import stockscope
    gs, _ = guidata.guidata_extent(mcf_136)
    out = bytearray(mcf_136)
    struct.pack_into("<I", out, gs + celltables.cell_table_off(mcf_136) + 203 * 4, 0x2003B000)
    out = bytes(out)
    with stockscope.stock_scope(mcf_136):
        assert sramreach.cell_area(out) == sramreach.cell_area(mcf_136)
        assert sramreach.free_runs(out)
        assert sramreach.reaching_forms(out, 0x2003B000, 1)["cell_table"] == ["0x2003b000"]
    with pytest.raises(sramreach.SramReachError):
        sramreach.cell_area(out)                      # outside a scope, the image is its own stock


def test_render_state_comes_from_the_stock_not_an_authored_base(mcf_136, monkeypatch):
    """An authored routine that addresses its own literal SRAM base through `movw` offsets is a
    second such base in the working image only. Inside the stock scope the render state is the
    stock's, so an unrelated later op does not refuse RENDER_STATE_BASES; the routine's accesses
    stay in the working image's reach."""
    from fireflymcf import readout, stockscope
    grown, _cells, _edits = readout.allocate_fresh_cells(mcf_136, [(2, 3)])
    sec = theme.guicode_section(mcf_136)
    stock_code = bytes(mcf_136[sec.offset:sec.offset + sec.length])
    real = sramreach._trace.__wrapped__

    def authored(code, *a):
        t = real(code, *a)
        if code == stock_code:
            return t
        movw = dict(t.movw)
        movw[0x2003A100] = ((0x260000, 0x10),)
        bo = dict(t.base_offset)
        bo[0x2003A110] = frozenset({(0x260000, 4)})
        return dataclasses.replace(t, movw=movw, base_offset=bo)
    monkeypatch.setattr(sramreach, "_trace", authored)
    sramreach._reach.cache_clear()
    try:
        with pytest.raises(sramreach.SramReachError) as e:
            sramreach.locate_render_state(grown)
        assert e.value.code == "RENDER_STATE_BASES"
        with stockscope.stock_scope(mcf_136):
            assert sramreach.locate_render_state(grown) == sramreach.locate_render_state(mcf_136)
            assert sramreach.cell_area(grown) == sramreach.cell_area(mcf_136)
            assert sramreach.reaching_forms(grown, 0x2003A110, 4)["base_offset"] == ["0x260000"]
            assert sramreach.free_runs(grown)
    finally:
        sramreach._reach.cache_clear()


def test_stale_pair_matches_a_prefix_with_a_nonzero_row():
    """A prefix of zero rows would match any zero run below the table; the matched prefix
    runs at least four rows and on to the first non-zero row, and a table of zero rows has
    nothing to match."""
    rows = struct.pack("<6I", 0, 0, 0, 0, 0, 0x20039900) + b"\xAA" * 8
    assert celltables._match_prefix(rows, 0, 7) == rows[:24]
    first = struct.pack("<5I", 0x20039900, 0, 0, 0, 0)
    assert celltables._match_prefix(first, 0, 5) == first[:16]
    assert celltables._match_prefix(bytes(20), 0, 5) is None


def test_stale_pair_prefix_on_stock_is_four_rows(mcf_136):
    ct = celltables.locate(mcf_136)
    gs, _ = guidata.guidata_extent(mcf_136)
    assert len(celltables._match_prefix(mcf_136, gs + ct.cell_off, ct.rows)) == 16
