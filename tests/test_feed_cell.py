"""Feeding an EXISTING display cell from a register: the Absorb/Float rows on a 48 V coach.

`readout.existing_display_cell` / `silence_writers`, `register.resolve_register(var_index=)`,
and `add_register_readout`'s `cell_slot` mode -- the shape that makes the panel's own rows read
the `0x1FECC` value the stock decode fences to 12-18 V (`BUS-BASELINE.md` PC.55).
"""
from __future__ import annotations

import hashlib

import pytest

from fireflymcf import (followchannel, readout, recipe, register, slotsource, sramreach,
                              theme, thumb)

STOCK = "firefly-13.6/mcf.bin"
ABSORB_VAR, FLOAT_VAR = 206, 207              # 0x1FECC payload [3..4] / [5..6], 0.05 V
ABSORB_CELL, FLOAT_CELL = 162, 163            # object 303's wtype-8 value_slots, type 3
ABSORB_CONFIG_STORE, FLOAT_CONFIG_STORE = 0x247C34, 0x247C3A   # FUN_00247BF8's per-tick copy
ABSORB_FENCED_STORE, FLOAT_FENCED_STORE = 0x24A844, 0x24A852   # the 12-18 V fenced decode


@pytest.fixture(scope="module")
def data() -> bytes:
    with open(STOCK, "rb") as handle:
        return handle.read()


def _compile(data, *specs):
    p = recipe.Project(base_sha256=hashlib.sha256(data).hexdigest(), build_name=None,
                       ops=list(specs))
    return recipe.compile_project(p, data)


def absorb_params(**over):
    base = {"register": {"dgn": 0x1FECC, "index": ABSORB_VAR}, "add": 1, "shift": 1,
            "width_bytes": 2, "cell_slot": ABSORB_CELL, "silence": [ABSORB_CONFIG_STORE]}
    base.update(over)
    return base


# --- the register, by index ------------------------------------------------------------------

def test_the_two_1fecc_entries_cannot_be_told_apart_by_filter_but_can_by_index(data) -> None:
    with pytest.raises(register.RegisterError, match="matches 2"):
        register.resolve_register(data, dgn=0x1FECC)
    a = register.resolve_register(data, dgn=0x1FECC, var_index=ABSORB_VAR)
    f = register.resolve_register(data, dgn=0x1FECC, var_index=FLOAT_VAR)
    assert (a.slot_addr, f.slot_addr) == (0x20008164, 0x20008166)     # PC.55's [r4,#0x19c]/[0x19e]
    assert a.source_field == 3 and f.source_field == 5                  # payload byte offsets
    with pytest.raises(register.RegisterError):
        register.resolve_register(data, dgn="DGN_CONFIG", var_index=ABSORB_VAR)   # wrong DGN


# --- the cell and its writers ----------------------------------------------------------------

def test_existing_cell_is_typed_and_has_two_stock_writers(data) -> None:
    c = readout.existing_display_cell(data, ABSORB_CELL, 2)
    assert (c.addr, c.width, c.tier, c.fresh) == (0x20039F30, 2, 0, False)
    assert readout.stock_writers(data, c) == [ABSORB_CONFIG_STORE, ABSORB_FENCED_STORE]
    with pytest.raises(readout.ReadoutError, match="variable type"):
        readout.existing_display_cell(data, ABSORB_CELL, 1)


def test_silencing_replaces_one_store_with_a_nop_and_refuses_the_wrong_site(data) -> None:
    c = readout.existing_display_cell(data, ABSORB_CELL, 2)
    img, edits = readout.silence_writers(data, c, [ABSORB_CONFIG_STORE])
    assert len(edits) == 1 and edits[0][1] == b"\x11\x80" and edits[0][2] == b"\x00\xbf"
    assert len(img) == len(data)
    assert readout.stock_writers(img, c) == [ABSORB_FENCED_STORE]
    with pytest.raises(readout.ReadoutError, match="not a store to cell"):
        readout.silence_writers(data, c, [FLOAT_CONFIG_STORE])       # Float's writer, not Absorb's
    with pytest.raises(readout.ReadoutError, match="not a store to cell"):
        readout.silence_writers(data, c, [ABSORB_CONFIG_STORE - 2])  # the ldr before it


# --- the op --------------------------------------------------------------------------------------

def test_feeding_the_absorb_cell_compiles_and_reports_verified(data) -> None:
    r = _compile(data, recipe.OpSpec(id="absorb", type="add_register_readout",
                                     params=absorb_params()))
    assert r.ok, r.errors
    assert r.unverified == []          # v18 FLASHED AND VERIFIED 2026-09-05 (PC.60)
    img = r.data
    # the stock config-byte writer is gone, the fenced decode's is kept, ours is added
    writes = slotsource.slot_source(img, ABSORB_CELL).writes
    assert [w for w in writes if w < followchannel.STOCK_APPEND_POINT] == [ABSORB_FENCED_STORE]
    assert [w for w in writes if w >= followchannel.STOCK_APPEND_POINT]
    sec = theme.guicode_section(img)
    off = sec.offset + (ABSORB_CONFIG_STORE - theme.guicode_base(sec))
    thumb.assert_disassembles_to(bytes(img[off:off + 2]), ABSORB_CONFIG_STORE, ["nop"])
    # the routine is on the chain and reaches the cell from above the stock append point
    chain = followchannel.chain_from_hook(img)
    assert chain and chain[0] >= followchannel.STOCK_APPEND_POINT
    forms = sramreach.reaching_forms(img, 0x20039F30, 2)
    ours = [int(s, 16) for s in forms["base_offset"] if int(s, 16) >= followchannel.STOCK_APPEND_POINT]
    assert ours
    # the same number of objects as stock: nothing was authored
    from fireflymcf import guidata
    assert len(guidata.object_tables(img)[1]) == len(guidata.object_tables(data)[1])


def test_the_routine_rounds_like_the_panel(data) -> None:
    """`add 1, shift 1` is the stock decode's own `(raw + 1) >> 1`: 48.0 V = raw 960 -> 480
    tenths; the 0xFFFF sentinel passes through untouched so the row draws dashes."""
    import tests.thumbsim as ts
    r = _compile(data, recipe.OpSpec(id="absorb", type="add_register_readout",
                                     params=absorb_params()))
    assert r.ok, r.errors
    img = r.data
    entry = followchannel.chain_from_hook(img)[0]
    sec = theme.guicode_section(img)
    base = theme.guicode_base(sec)
    blob = bytes(img[sec.offset + (entry - base): sec.offset + sec.length - 4])
    dis = thumb.disassemble(blob, entry)
    for raw, want in ((960, 480), (1151, 576), (0xFFFF, 0xFFFF)):
        sim = ts.Sim(blob, entry, dis)
        sim.wr(0x20008164, raw, 2)
        sim.run()
        assert sim.rd(0x20039F30, 2) == want, (raw, sim.rd(0x20039F30, 2))


def test_both_rows_fed_in_one_recipe_chain_and_keep_each_others_writers_straight(data) -> None:
    r = _compile(data,
                 recipe.OpSpec(id="absorb", type="add_register_readout", params=absorb_params()),
                 recipe.OpSpec(id="float", type="add_register_readout", params=absorb_params(
                     register={"dgn": 0x1FECC, "index": FLOAT_VAR}, cell_slot=FLOAT_CELL,
                     silence=[FLOAT_CONFIG_STORE])))
    assert r.ok, r.errors
    assert r.unverified == []
    assert len(followchannel.chain_from_hook(r.data)) >= 2
    def stock_writes(slot):
        return [w for w in slotsource.slot_source(r.data, slot).writes
                if w < followchannel.STOCK_APPEND_POINT]
    assert stock_writes(ABSORB_CELL) == [ABSORB_FENCED_STORE]
    assert stock_writes(FLOAT_CELL) == [FLOAT_FENCED_STORE]


def test_the_op_refuses_mixed_modes_and_a_wrong_silence_site(data) -> None:
    for bad in (absorb_params(page=17), absorb_params(x=1), absorb_params(format={"width": 3}),
                absorb_params(silence=[FLOAT_CONFIG_STORE]), absorb_params(width_bytes=1)):
        r = _compile(data, recipe.OpSpec(id="absorb", type="add_register_readout", params=bad))
        assert not r.ok, bad
        assert r.errors[0]["code"] in ("BAD_PARAMS", "READOUT_REFUSED"), (bad, r.errors)
    # silence without cell_slot, and the drawing mode still needing page/x/y
    r = _compile(data, recipe.OpSpec(id="ro", type="add_register_readout", params={
        "register": {"dgn": "DGN_CONFIG", "selector": 0xEE}, "silence": [ABSORB_CONFIG_STORE]}))
    assert not r.ok and r.errors[0]["code"] == "BAD_PARAMS"
    r = _compile(data, recipe.OpSpec(id="ro", type="add_register_readout", params={
        "register": {"dgn": "DGN_CONFIG", "selector": 0xEE}, "page": 4}))
    assert not r.ok and r.errors[0]["code"] == "BAD_PARAMS"
