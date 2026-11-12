"""tempreadout: an RV-C temperature drawn in the panel's Temp Units (cell 9), as CLIMATE does."""
import pytest

from fireflymcf import readout, register, tempreadout, thumb
from tests.thumbsim import Sim

NEEDS_RESEARCH_DATA = "reads the 13.6 corpus image"
SIM_LOAD = 0x00300000


@pytest.fixture(scope="module")
def stock() -> bytes:
    with open("firefly-13.6/mcf.bin", "rb") as handle:
        return handle.read()


def _kelvin32(celsius):
    return round((celsius + 273) * 32)


def _plan(stock):
    reg = register.resolve_register(stock, dgn="DGN_CONFIG", selector=0xEE)
    _img, cells, _e = readout.allocate_fresh_cells(stock, [(2, tempreadout.VALUE_TYPE)])
    return tempreadout.resolve(stock, slot_addr=reg.slot_addr, cell=cells[0])


def _run(plan, word, flag):
    blob, disasm = tempreadout.build(plan, SIM_LOAD)
    s = Sim(blob, SIM_LOAD, disasm)
    s.wr(plan.slot_addr, word, 2)
    s.wr(plan.flag_addr, flag, 1)
    s.wr(plan.cell.addr, 0xA5A5, 2)
    s.run()
    v = s.rd(plan.cell.addr, 2)
    return v - 0x10000 if v & 0x8000 else v          # the panel reads the cell with ldrsh


def test_the_units_switch_is_the_climate_pages_and_the_flag_is_cell_9(stock):
    sw = tempreadout.units_switch(stock)
    assert sw.flag_addr == 0x2003A2A3                              # GUICODE Finding 27
    assert sw.arms == {0: (5, 1), 1: (3, 0)}                       # objects 93 / 94
    assert sw.record == bytes.fromhex("0655000000000000ff090002005d00")   # 872[6]


def test_the_units_switch_refuses_an_image_without_one(stock, monkeypatch):
    monkeypatch.setattr(tempreadout, "UNITS_CELL", 263)            # the 12/24h flag's switches
    with pytest.raises(tempreadout.TempReadoutError):
        tempreadout.units_switch(stock)


@pytest.mark.parametrize("c,tenths,f", [(20, 200, 68), (0, 0, 32), (5, 50, 41), (-10, -100, 14),
                                        (-40, -400, -40), (37.5, 375, 100), (60, 600, 140)])
def test_each_arm_converts_like_the_panel(stock, c, tenths, f):
    plan = _plan(stock)
    raw = _kelvin32(c)
    assert _run(plan, raw, tempreadout.CELSIUS) == tenths
    assert _run(plan, raw, tempreadout.FAHRENHEIT) == f
    assert _run(plan, raw, 7) == f                     # any nonzero flag is Fahrenheit (cbz)


def test_every_reading_rounds_to_the_nearest_step(stock):
    plan = _plan(stock)
    for raw in range(0, tempreadout.RVC_MAX + 1, 1231):
        c = raw / 32 - 273
        assert abs(_run(plan, raw, 0) / 10 - c) <= 0.05 + 1e-9, raw
        assert abs(_run(plan, raw, 1) - (c * 9 / 5 + 32)) <= 0.5 + 1e-9, raw
        assert _run(plan, raw, 0) == tempreadout.celsius_tenths(raw)
        assert _run(plan, raw, 1) == tempreadout.fahrenheit(raw)


@pytest.mark.parametrize("raw", [0xFB00, 0xFFFE, 0xFFFF])
def test_no_reading_stores_a_value_too_wide_for_either_field(stock, raw):
    plan = _plan(stock)
    for flag in (0, 1):
        assert _run(plan, raw, flag) == tempreadout.INVALID
    assert len("%.1f" % (tempreadout.INVALID / 10)) > 5 and len(str(tempreadout.INVALID)) > 3


def test_the_routine_uses_only_scratch_registers_and_chains_on_the_hook(stock):
    plan = _plan(stock)
    blob, disasm = tempreadout.build(plan, SIM_LOAD)
    text = [t for _a, t in disasm]
    assert text[0] == "push {r0, r1, r2, r3, lr}" and text[-1] == "b.w #%#x" % plan.tail_target
    assert "udiv r1, r1, r2" in text
    assert not any(r in t for t in text for r in ("r4", "r5", "r6", "r7"))
    at, old, new = tempreadout.detour_edit(plan, SIM_LOAD)
    assert old == plan.hook_old and len(new) == 4


def test_udiv_encodes_as_capstone_reads_it():
    assert thumb.disassemble(thumb.udiv(1, 1, 2), 0x1000)[0][1] == "udiv r1, r1, r2"
    assert thumb.disassemble(thumb.udiv(3, 5, 9), 0x1000)[0][1] == "udiv r3, r5, sb"


def test_the_object_copies_the_directive_switch_then_draws_the_cell(stock):
    out, base = tempreadout.append_unit_arms(stock, (120, 125, 18, 20), 14)
    obj = tempreadout.build_object(out, x=118, y=141, display_slot=700, font=15,
                                   unit_arms_base=base)
    from fireflymcf import records
    recs = records.object_records(obj, 0)
    assert [r.wtype for r in recs] == [6, 8, 6]
    assert recs[0].child_index == 93 and recs[2].child_index == base
