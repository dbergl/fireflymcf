"""readout: on-screen register readouts."""
from __future__ import annotations

import pytest

from fireflymcf import followchannel, readout, sramreach, statusbind, status_map, theme

STOCK = "firefly-13.6/mcf.bin"


@pytest.fixture(scope="module")
def stock() -> bytes:
    with open(STOCK, "rb") as handle:
        return handle.read()


def _claimed_image(stock: bytes) -> bytes:
    """Stock with every halfword-safe spare cell claimed by a fake pool word, so tier 1 fails
    the way it does on v15 (211/213/215/217 all referenced by appended routines)."""
    import struct
    from fireflymcf import theme, patch
    cells = status_map.cell_table(stock)
    sec = theme.guicode_section(stock)
    buf = bytearray(stock)
    # write the four addresses as words into the section's last 16 content bytes -- a
    # literal pool nothing executes; the raw-word scan is what makes them "referenced"
    end = sec.offset + sec.length - 4
    for k, idx in enumerate((211, 213, 215, 217)):
        struct.pack_into("<I", buf, end - 16 + 4 * k, cells[idx])
    sealed, _ = patch.reseal(bytes(buf))
    return sealed


def test_tier_1_types_the_cell_to_a_width_2_readout(stock):
    """`FUN_00257BFC` copies the +0x4492C type byte into render state and `FUN_00251E38` uses
    it as the READ WIDTH (docs/GUICODE.md, sramreach._cell_widths), so a width-2 readout on
    stock's proven cell 211 -- which is typed 1 (a byte reader) -- must retype it or the panel
    would draw only the low byte. Confirm the premise first: 211 starts out typed 1."""
    from fireflymcf import guidata
    gs, _ = guidata.guidata_extent(stock)
    assert stock[gs + 0x4492C + 211] == 1
    out, cell, edits = readout.allocate_display_cell(stock, 2)
    assert (cell.slot, cell.addr, cell.width, cell.tier, cell.fresh) == (211, 0x20039F68, 2, 1, False)
    assert edits
    assert out[gs + 0x4492C + 211] == statusbind.VARIABLE_TYPE_U16
    # out differs from stock only where the returned edits say it does (the type byte plus
    # whatever checksum bytes the reseal touched) -- never anywhere else.
    diffs = {i for i in range(len(stock)) if out[i] != stock[i]}
    touched = set()
    for e in edits:
        touched.update(range(e.file_offset, e.file_offset + len(e.new)))
    assert diffs and diffs == touched


def test_tier_1_leaves_stock_untouched_for_a_width_1_readout(stock):
    """211 is already typed 1 -- the width-1 target -- so a width-1 readout changes nothing."""
    out, cell, edits = readout.allocate_display_cell(stock, 1)
    assert (cell.slot, cell.addr, cell.width, cell.tier, cell.fresh) == (211, 0x20039F68, 1, 1, False)
    assert out == stock and edits == []


def test_tier_2_gives_a_byte_readout_an_odd_spare_cell_when_no_halfword_is_left(stock):
    img = _claimed_image(stock)
    with pytest.raises(followchannel.FollowChannelError):
        followchannel.scan_display_cell(img)
    out, cell, edits = readout.allocate_display_cell(img, 1)
    assert cell.tier == 2 and cell.width == 1 and cell.addr % 2 == 1 and not cell.fresh
    assert cell.slot in statusbind.spare_cell_indices(img)
    forms = sramreach.reaching_forms(img, cell.addr, 1)
    assert forms["raw_word"] == [] and forms["base_offset"] == [] and not forms["state_struct"]
    assert forms["indexed_table"] == []
    # the chosen cell is already typed 1 (the width-1 target) on this image, so this tier
    # changes nothing -- if that ever stops being true, this assertion catches it and the
    # `edits == []` clause below must be dropped.
    from fireflymcf import guidata
    gs, _ = guidata.guidata_extent(out)
    assert out[gs + 0x4492C + cell.slot] == 1
    assert out == img and edits == []


def test_tier_3_points_a_spare_index_at_free_sram_and_types_it(stock):
    img = _claimed_image(stock)
    out, cell, edits = readout.allocate_display_cell(img, 2)
    assert cell.tier == 3 and cell.fresh and cell.addr % 2 == 0
    assert any(s <= cell.addr < s + n for s, n in sramreach.free_runs(img, 8))
    assert status_map.cell_table(out)[cell.slot] == cell.addr
    from fireflymcf import guidata
    gs, _ = guidata.guidata_extent(out)
    assert out[gs + 0x4492C + cell.slot] == statusbind.VARIABLE_TYPE_U16
    assert len(edits) >= 2 and out != img


def test_tier_3_refuses_clearly_when_no_spare_index_is_left(stock, monkeypatch):
    """Fix round item 7: the old tier-3 loop (`for idx in spare: ... return`) returned
    unconditionally on the first iteration, so an EMPTY `spare` fell through silently -- it
    kept rescanning every remaining free run for nothing before finally raising the generic
    message at the very end. With a viable free run but zero spare indices, the refusal must
    happen right there, naming the actual reason."""
    img = _claimed_image(stock)
    monkeypatch.setattr(statusbind, "spare_cell_indices", lambda data: [])
    with pytest.raises(readout.ReadoutError) as e:
        readout.allocate_display_cell(img, 2)
    assert "NO_DISPLAY_CELL" in str(e.value)
    assert "spare index" in str(e.value)


def test_allocation_refuses_when_nothing_fits(stock, monkeypatch):
    img = _claimed_image(stock)
    monkeypatch.setattr(sramreach, "free_runs", lambda data, min_bytes=8, **kw: [])
    with pytest.raises(readout.ReadoutError) as e:
        readout.allocate_display_cell(img, 2)
    assert "NO_DISPLAY_CELL" in str(e.value)


def test_allocation_rejects_a_bad_width(stock):
    with pytest.raises(readout.ReadoutError):
        readout.allocate_display_cell(stock, 4)


from tests.thumbsim import Sim
from fireflymcf import register, thumb

SIM_LOAD = 0x00300000


def _plan(stock, *, width=2, mask=0xFFFF, add=0, shift=0, sub=0):
    reg = register.resolve_register(stock, dgn="DGN_CONFIG", selector=0xEE)
    _img, cell, _e = readout.allocate_display_cell(stock, width)
    return readout.resolve(stock, register=reg, cell=cell, mask=mask, add=add, shift=shift,
                           sub=sub)


def _run(plan, blob, disasm, word):
    s = Sim(blob, SIM_LOAD, disasm)
    s.wr(plan.slot_addr, word, 2)
    s.wr(plan.cell.addr, 0xA5A5 if plan.cell.width == 2 else 0xA5, plan.cell.width)
    s.run()
    return s.rd(plan.cell.addr, plan.cell.width)


def test_build_round_trips_every_instruction_and_chains_on_the_hook(stock):
    plan = _plan(stock)
    blob, disasm = readout.build(plan, SIM_LOAD)
    assert disasm and len(blob) % 4 == 0
    text = [t for _a, t in disasm]
    assert text[0] == "push {r0, r1, r2, r3, lr}"
    assert text[-1] == "b.w #%#x" % plan.tail_target
    assert not any(r in t for t in text for r in ("r4", "r5", "r6", "r7"))
    hook_at, old, new = readout.detour_edit(plan, SIM_LOAD)
    assert hook_at == followchannel.HOOK_OFF and old == plan.hook_old
    assert thumb.disassemble(new, theme.GUICODE_BASE_DEFAULT + hook_at)[0][1] == "bl #%#x" % SIM_LOAD


def test_detour_edit_refuses_a_hook_that_is_neither_stock_nor_a_chained_bl(stock):
    import dataclasses
    plan = _plan(stock)
    bad = dataclasses.replace(plan, hook_old=b"\x00\xbf\x00\xbf")  # two nops
    with pytest.raises(followchannel.FollowChannelError):
        readout.detour_edit(bad, SIM_LOAD)


@pytest.mark.parametrize("word,expect", [(0x011E, 0x011E), (0x0005, 5), (0xFFFF, 0xFFFF)])
def test_the_plain_copy_moves_the_word_and_passes_the_sentinel_through(stock, word, expect):
    plan = _plan(stock)
    blob, disasm = readout.build(plan, SIM_LOAD)
    assert _run(plan, blob, disasm, word) == expect


def test_mask_add_shift_compute_the_stock_voltage_idiom(stock):
    # 0.05 V/LSB -> tenths, the (raw + 1) >> 1 the panel itself uses (PC.55)
    plan = _plan(stock, mask=0xFFFF, add=1, shift=1)
    blob, disasm = readout.build(plan, SIM_LOAD)
    assert _run(plan, blob, disasm, 1020) == 510        # 51.0 V
    assert _run(plan, blob, disasm, 0xFFFF) == 0xFFFF   # sentinel untouched


def test_a_byte_readout_stores_the_low_byte_and_the_sentinel_as_ff(stock):
    # Fix round, item 3: mask alone (0x00FF, no add/shift) made this test pass whether or not
    # the sentinel branch even existed, because 0xFFFF & 0x00FF == 0xFF either way -- deleting
    # the `beq` would still land on 0xFF via the arithmetic path. Adding an add/shift the
    # sentinel path must SKIP makes the two paths disagree (0x0F if the arithmetic runs on the
    # sentinel word, 0xFF only if it is bypassed), so the assertion below is falsifiable; proved
    # by temporarily flipping the emitted `beq` to `bne` (readout.build), which makes this
    # test's sentinel assertion fail (0x0F instead of 0xFF) -- reverted, not committed.
    plan = _plan(stock, width=1, mask=0x00FF, add=1, shift=1)
    blob, disasm = readout.build(plan, SIM_LOAD)
    assert _run(plan, blob, disasm, 0x011E) == 0x0F
    assert _run(plan, blob, disasm, 0xFFFF) == 0xFF        # sentinel bypasses mask/add/shift


def test_resolve_refuses_out_of_range_arithmetic(stock):
    for kw in (dict(mask=0), dict(mask=0x10000), dict(add=256), dict(shift=16)):
        with pytest.raises(readout.ReadoutError):
            _plan(stock, **kw)


from fireflymcf import guidata, insert, layout, records


def test_directive_bytes_encode_mode_0_with_the_stock_flags():
    assert readout.directive_bytes(4, 1) == bytes([0, 0x18, 4, 1])
    assert readout.directive_bytes(2, 0, zero_pad=True) == bytes([0, 0x1A, 2, 0])
    assert readout.directive_bytes(3, 0, justify="centre") == bytes([0, 0x14, 3, 0])
    assert readout.directive_bytes(3, 0, justify="left") == bytes([0, 0x10, 3, 0])
    with pytest.raises(readout.ReadoutError):
        readout.directive_bytes(3, 0, justify="middle")
    for bad in ((10, 0), (0, 4), (-1, 0)):
        with pytest.raises(readout.ReadoutError):
            readout.directive_bytes(*bad)


def test_author_readout_object_is_a_directive_then_an_absolute_readout(stock):
    obj = readout.author_readout_object(stock, x=400, y=560, display_slot=211,
                                        directive=readout.directive_bytes(3, 1))
    assert obj[0] == 2 and len(obj) % 4 == 0
    recs = records.object_records(obj, 0)
    assert [r.wtype for r in recs] == [9, 8]
    assert recs[0].fmt == (0, 0x18, 3, 1)
    assert recs[1].value_slot == 211
    assert recs[1].geom[0] == 400 and recs[1].geom[1] == 560
    assert recs[1].base_modes[:2] == [0, 0], "x/y absolute, not pen-relative like the donor"
    assert recs[1].font == 15 and recs[1].align == 3     # the donor's own font and justification


def test_inserted_readout_is_walked_under_its_own_directive_on_home(stock):
    obj = readout.author_readout_object(stock, x=400, y=560, display_slot=211,
                                        directive=readout.directive_bytes(2, 0))
    img, _edits = insert.insert_object(stock, obj, parent_index=4)
    new_index = len(guidata.object_tables(img)[1]) - 1
    events = readout.directive_in_force(img, 4)                        # HOME root is object 4
    ours = [e for e in events if e[0] == "readout" and e[1] == new_index]
    assert ours == [("readout", new_index, 211, (0, 0x18, 2, 0))]
    # obj_index alone is not enough: the walk emits a Placed entry for every non-container
    # record, including the wtype-9 directive (x0=y0=0, no geometry of its own) -- select the
    # wtype-8 readout specifically to check where IT lands.
    placed = [p for p in layout.Screen(img).walk(4)
              if p.obj_index == new_index and p.wtype == 8]
    assert placed and placed[0].x0 == 400 and placed[0].y0 == 560
    # the stock voltages still inherit object 441's directive, unchanged
    volts = [e for e in events if e[0] == "readout" and e[2] in (205, 209)]
    assert all(e[3] == (0, 0x18, 4, 1) for e in volts)


def test_directive_in_force_reapplies_a_revisited_directive_like_the_firmware_does(
        stock, monkeypatch):
    """The firmware overwrites the persistent directive state on EVERY wtype-9 decode
    (GUICODE Finding 65), including a re-visit of an object already seen. Hand-build a walk
    over stock objects 441 (directive (0,0x18,4,1)) -> 93 (a different directive,
    (0,0x18,5,1)) -> 441 again -> 443's wtype-8 readout (record 5, value_slot 209), and check
    the readout carries 441's fmt -- the one actually in force at that point -- not 93's."""

    gs, _ = guidata.guidata_extent(stock)
    tbl = guidata.object_tables(stock)[1]

    def off_of(obj, rec_idx):
        return records.object_records(stock, gs + tbl[obj])[rec_idx].offset

    def placed(wtype, obj_index, record_offset):
        return layout.Placed(wtype=wtype, label=None, x0=0, y0=0, x1=0, y1=0, depth=0,
                             obj_index=obj_index, record_offset=record_offset)

    fake_walk = [
        placed(9, 441, off_of(441, 0)),
        placed(9, 93, off_of(93, 0)),
        placed(9, 441, off_of(441, 0)),
        placed(8, 443, off_of(443, 5)),
    ]
    monkeypatch.setattr(layout.Screen, "walk", lambda self, root, **kw: fake_walk)
    events = readout.directive_in_force(stock, 4)
    # the re-visit of 441 emits no SECOND directive event (the Finding-65 count elsewhere
    # depends on that), but it still updates the fmt in force for what follows.
    assert [e[1] for e in events if e[0] == "directive"] == [441, 93]
    assert [e for e in events if e[0] == "readout"] == [
        ("readout", 443, 209, (0, 0x18, 4, 1))]


def test_the_revisit_fix_changes_one_real_stock_result_on_screen_28(stock):
    """The natural counterexample the hand-built walk above models: on real stock 13.6, screen
    28 (root object 31), object 791's readout on slot 277 is reached twice under
    `directive_in_force`, and one of those two visits is a genuine re-visit case. Under the OLD
    skip-on-revisit walk it decoded as `(0, 0x18, 4, 1)`; the fix (Task 4) makes it decode as
    `(0, 0x18, 3, 0)` instead, on BOTH visits -- confirmed against the old code by a scratch
    diff before writing this test, not asserted from the fix's own description."""
    events = readout.directive_in_force(stock, 31)
    mine = [e for e in events if e[0] == "readout" and e[1] == 791 and e[2] == 277]
    assert mine == [("readout", 791, 277, (0, 0x18, 3, 0)),
                    ("readout", 791, 277, (0, 0x18, 3, 0))], mine


def test_sub_clamps_and_scales_rvc_current_to_tenths(stock):
    # RV-C AC current: raw = 20 * A + 32000; (raw - 32000) >> 1 = tenths of an amp
    plan = _plan(stock, sub=32000, shift=1)
    blob, disasm = readout.build(plan, SIM_LOAD)
    assert _run(plan, blob, disasm, 32000 + 20 * 30) == 300      # 30.0 A
    assert _run(plan, blob, disasm, 32000 + 236) == 118          # 11.8 A
    assert _run(plan, blob, disasm, 32000) == 0
    assert _run(plan, blob, disasm, 31000) == 0                  # below the offset: clamp
    assert _run(plan, blob, disasm, 0) == 0
    assert _run(plan, blob, disasm, 0xFFFF) == 0xFFFF            # no data stays dashes


def test_sub_zero_emits_the_same_routine_as_before(stock):
    a, _ = readout.build(_plan(stock, shift=1), SIM_LOAD)
    b, _ = readout.build(_plan(stock, shift=1, sub=0), SIM_LOAD)
    assert a == b


def test_resolve_refuses_an_out_of_range_sub(stock):
    with pytest.raises(readout.ReadoutError):
        _plan(stock, sub=0x10000)
