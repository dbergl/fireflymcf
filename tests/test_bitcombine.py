"""`bitcombine`: the per-tick routine that folds single bits from several receive slots into one
display cell, so ONE value-match switch can distinguish every state a widget draws.

The routine is hand-authored Thumb, so these tests read it back the way the firmware would: the
emitted blob is disassembled and compared instruction for instruction, and the literal pool is
decoded to confirm the routine addresses the slots and the cell the plan named. The refusals
matter as much as the code -- an ambiguous weight would make a combined value name two states.
"""
import struct

import pytest

from fireflymcf import bitcombine, followchannel, readout, thumb

STOCK = "firefly-13.6/mcf.bin"
LOAD = 0x00259BB4                    # stock 13.6's GuiCode append point, 4-aligned

HEATER_SLOT = 0x20008254
CONTACTOR_SLOT = 0x20008256
CELL_ADDR = 0x2003B000


def _stock():
    return open(STOCK, "rb").read()


def _cell(width=2, addr=CELL_ADDR, slot=669):
    return readout.DisplayCell(slot=slot, addr=addr, width=width, tier="fresh", fresh=True)


def _plan(data=None, sources=None, cell=None):
    return bitcombine.resolve(
        data if data is not None else _stock(),
        sources=sources if sources is not None else [
            bitcombine.BitSource(HEATER_SLOT, 1, 1),
            bitcombine.BitSource(CONTACTOR_SLOT, 1, 2)],
        cell=cell or _cell())


def test_routine_reads_each_slot_and_writes_the_cell():
    """The literal pool is `[slot0, slot1, ..., cell]` in source order -- the contract
    `tests/test_batt_heat_status.py` uses to pair a routine to its variables."""
    blob, _dis = bitcombine.build(_plan(), LOAD)
    pool = blob[-12:]
    assert struct.unpack("<III", pool) == (HEATER_SLOT, CONTACTOR_SLOT, CELL_ADDR)


def test_routine_disassembles_to_the_fold_it_claims():
    """`build` already asserts this internally; re-reading it here pins the SHAPE, so a change to
    the emitted sequence has to be made deliberately rather than slipping through."""
    _blob, dis = bitcombine.build(_plan(), LOAD)
    text = [t for _addr, t in dis]
    assert text[0] == "push {r0, r1, r2, r3, lr}"
    assert text[1] == "movs r1, #0", "the accumulator must start at zero, not at whatever r1 held"
    assert text[-2] == "pop.w {r0, r1, r2, r3, lr}"
    assert text[-1] == "b.w #0x247bf8", "the routine must tail-call the per-tick paint it displaced"
    # One weight add per source, in source order.
    assert [t for t in text if t.startswith("adds r1")] == ["adds r1, #1", "adds r1, #2"]
    # Each source is sentinel-guarded before its bit is tested.
    assert text.count("movw r2, #0xffff") == 2
    assert text.count("ands r3, r2") == 2
    assert text.count("strh r1, [r0]") == 1


def test_a_stale_slot_contributes_nothing():
    """Until the first frame arrives the receive slot holds STALE_SENTINEL. Both branches out of
    a source jump PAST its `adds`, so a stale or clear slot leaves the accumulator alone and the
    cell reads a genuine 'off' rather than a pattern of set bits."""
    _blob, dis = bitcombine.build(_plan(), LOAD)
    text = {addr: t for addr, t in dis}
    adds = [addr for addr, t in dis if t.startswith("adds r1")]
    branches = [(addr, t) for addr, t in dis if t.startswith("beq")]
    assert len(branches) == 2 * len(adds), "each source needs a stale guard and a bit test"
    for addr, t in branches:
        target = int(t.split("#")[1], 16)
        assert target in text, "branch to %#x is not an instruction in this routine" % target
        assert target > addr, "the guards must branch FORWARD, past this source's adds"


def test_refuses_an_ambiguous_weight():
    """Two sources sharing a weight, or a weight that is not a single bit, make the combined value
    name more than one state -- the switch arms could not then be written."""
    with pytest.raises(bitcombine.BitCombineError, match="power of two"):
        _plan(sources=[bitcombine.BitSource(HEATER_SLOT, 1, 3)])
    with pytest.raises(bitcombine.BitCombineError, match="two sources"):
        _plan(sources=[bitcombine.BitSource(HEATER_SLOT, 1, 1),
                       bitcombine.BitSource(CONTACTOR_SLOT, 1, 1)])


def test_refuses_a_byte_cell_and_an_empty_source_list():
    with pytest.raises(bitcombine.BitCombineError, match="u16"):
        _plan(cell=_cell(width=1))
    with pytest.raises(bitcombine.BitCombineError, match="at least one source"):
        _plan(sources=[])


def test_detour_replaces_the_stock_hook_bl():
    plan = _plan()
    at, old, new = bitcombine.detour_edit(plan, LOAD)
    assert at == followchannel.HOOK_OFF
    assert old == plan.hook_old
    assert len(new) == 4 and new != old
    ins = thumb.disassemble(new, followchannel.theme.GUICODE_BASE_DEFAULT + at)
    assert ins[0][1] == "bl #%#x" % LOAD
