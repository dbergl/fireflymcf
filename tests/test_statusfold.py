"""`statusfold`: one appended per-tick routine that lights cells, maps enums and drives variants.

Data-free: every plan is built directly at a fixed load address with the stock hook state, and the
emitted routine is executed in `tests.thumbsim.Sim` with the slots and cells in Sim SRAM.
"""
import pytest

from fireflymcf import followchannel, statusfold, theme, thumb
from tests.thumbsim import Sim

LOAD = 0x00300000
SLOT = 0x20008100
CELL = 0x2003B000
STALE = followchannel.STALE_SENTINEL


def _light(kind="eq", value=1, mask=0x0F, shift=0, valid_max=None, cell=CELL, slot=SLOT,
           group=None):
    return statusfold.Light(slot_addr=slot, mask=mask, shift=shift, kind=kind, value=value,
                            valid_max=valid_max, cell_addr=cell, variant_group=group)


def _plan(*items):
    return statusfold.FoldPlan(items=tuple(items), hook_at=followchannel.HOOK_OFF,
                               hook_old=followchannel.HOOK_STOCK,
                               tail_target=followchannel.TAIL_TARGET)


def _run(plan, slots, regs=None):
    """Build `plan`, seed each `{slot_addr: u16}`, run one tick; return the Sim."""
    blob, dis = statusfold.build(plan, LOAD)
    sim = Sim(blob, LOAD, dis)
    for addr, val in slots.items():
        sim.wr(addr, val, 2)
    for item in plan.items:                    # a cell is not pre-zeroed by the routine's caller
        sim.wr(item.cell_addr, 0xAAAA, 2)
    for i, v in (regs or {}).items():
        sim.regs[i] = v
    sim.run()
    return sim


def _cells(plan, slot_values, slot=SLOT, cell=CELL):
    return [_run(plan, {slot: v}).rd(cell, 2) for v in slot_values]


def test_light_eq_over_values():
    plan = _plan(_light("eq", 1, mask=0x0F))
    assert _cells(plan, [0x00, 0x01, 0xF1, 0x03]) == [0, 1, 1, 0]


def test_light_stale_is_off_before_mask():
    """0xFFFF masked to 0x0F would equal 15: the sentinel test must come before the mask."""
    plan = _plan(_light("eq", 15, mask=0x0F))
    assert _cells(plan, [STALE, 0x0F]) == [0, 1]


def test_light_ge_valid_max():
    plan = _plan(_light("ge", 0x78, mask=0xFF, valid_max=0xFA))
    assert _cells(plan, [0x50, 0x78, 0xC8, 0xFA, 0xFF]) == [0, 1, 1, 1, 0]


def test_light_ne_and_bit():
    assert _cells(_plan(_light("ne", 0, mask=0xFF)), [0, 3, STALE]) == [0, 1, 0]
    assert _cells(_plan(_light("bit", 0x02, mask=0xFF)), [0, 1, 2, 3, 0xFD, STALE]) == \
        [0, 0, 1, 1, 0, 0]


def test_light_shift_and_wide_mask_and_value():
    """mask and value past imm8 go through `movw`; the field is shifted down before the test."""
    plan = _plan(_light("eq", 0x123, mask=0x3FF0, shift=4))
    assert _cells(plan, [0x1230, 0x1231, 0x1240, 0xD23F]) == [1, 1, 0, 1]
    plan = _plan(_light("ge", 0x300, mask=0xFFFF, valid_max=0x1000))
    assert _cells(plan, [0x2FF, 0x300, 0x1000, 0x1001]) == [0, 1, 1, 0]


def test_variant_calls():
    plan = _plan(_light("eq", 1, cell=CELL, slot=SLOT, group=162),
                 _light("eq", 1, cell=CELL + 2, slot=SLOT + 2, group=168))
    sim = _run(plan, {SLOT: 1, SLOT + 2: 0})
    assert sim.calls == [(162, 1), (168, 0)]
    sim = _run(plan, {SLOT: STALE, SLOT + 2: 1})
    assert sim.calls == [(162, 0), (168, 1)]
    assert sim.rd(CELL, 2) == 0 and sim.rd(CELL + 2, 2) == 1


def test_light_without_group_calls_nothing():
    assert _run(_plan(_light()), {SLOT: 1}).calls == []


def test_enum_index():
    plan = _plan(statusfold.Enum(slot_addr=SLOT, mask=0xFF, shift=0, keys=(0, 1, 3, 4, 6),
                                 cell_addr=CELL))
    assert _cells(plan, [0, 3, 6, 2, STALE]) == [0, 2, 4, 5, 5]


def test_enum_wide_keys_shift_and_sixteen():
    keys = tuple(range(0x100, 0x110))
    plan = _plan(statusfold.Enum(slot_addr=SLOT, mask=0xFFF0, shift=4, keys=keys,
                                 cell_addr=CELL))
    assert _cells(plan, [0x1000, 0x10F5, 0x1105, 0x0FF0]) == [0, 15, 16, 16]


def test_mixed_items_each_write_their_own_cell():
    plan = _plan(_light("eq", 1, slot=SLOT, cell=CELL, group=162),
                 statusfold.Enum(slot_addr=SLOT + 2, mask=0x0F, shift=0, keys=(2, 5),
                                 cell_addr=CELL + 2),
                 _light("bit", 0x80, mask=0xFF, slot=SLOT + 4, cell=CELL + 4))
    sim = _run(plan, {SLOT: 1, SLOT + 2: 5, SLOT + 4: 0x81})
    assert [sim.rd(CELL + i, 2) for i in (0, 2, 4)] == [1, 1, 1]
    assert sim.calls == [(162, 1)]


def test_tail_branch_and_registers():
    plan = _plan(_light(group=162), statusfold.Enum(SLOT + 2, 0xFF, 0, (1,), CELL + 2))
    regs = {i: 0x11110000 + i for i in range(16) if i not in (13, 15)}
    sim = _run(plan, {SLOT: 1, SLOT + 2: 1}, regs)
    assert sim.tail == plan.tail_target
    assert sim.sp_at_exit == Sim.STACK_TOP
    assert sim.regs[4] == Sim.STATE_BASE
    for i in list(range(0, 4)) + list(range(5, 12)) + [14]:
        assert sim.regs[i] == regs[i], f"r{i} not preserved"


def test_many_items_still_build_and_run():
    """24 Lights with variants run past `ldr` T1's 1020-byte literal reach: the routine falls back
    to wide literal loads and every item still lights its own cell and calls its own group."""
    items = [_light("eq", 1, mask=0x01, slot=SLOT + 2 * i, cell=CELL + 2 * i, group=100 + i)
             for i in range(24)]
    plan = _plan(*items)
    blob, dis = statusfold.build(plan, LOAD)
    assert any(t.startswith("ldr.w") for _a, t in dis)
    slots = {SLOT + 2 * i: i % 2 for i in range(24)}
    sim = _run(plan, slots)
    assert [sim.rd(CELL + 2 * i, 2) for i in range(24)] == [i % 2 for i in range(24)]
    assert sim.calls == [(100 + i, i % 2) for i in range(24)]
    assert sim.tail == plan.tail_target


def test_small_plans_use_narrow_literals():
    _blob, dis = statusfold.build(_plan(_light(group=1)), LOAD)
    assert not any(t.startswith("ldr.w") for _a, t in dis)


def test_setter_call_shape():
    """The setVariant call is `build_display_bridge`'s: pointer from the pool, dereferenced."""
    _blob, dis = statusfold.build(_plan(_light(group=0xA2)), LOAD)
    text = [t for _a, t in dis]
    i = text.index("blx r3")
    assert text[i - 3].startswith("ldr r3, [pc")
    assert text[i - 2:i] == ["ldr r3, [r3]", "movs r0, #0xa2"]
    assert text[0] == "push {r0, r1, r2, r3, lr}"
    assert text[-2:] == ["pop.w {r0, r1, r2, r3, lr}", f"b.w #{followchannel.TAIL_TARGET:#x}"]


def test_pool_carries_setter_once():
    import struct
    plan = _plan(_light(group=1), _light(slot=SLOT + 2, cell=CELL + 2, group=2))
    blob, _dis = statusfold.build(plan, LOAD)
    words = [struct.unpack_from("<I", blob, o)[0] for o in range(len(blob) - 20, len(blob), 4)]
    assert sorted(words) == sorted([SLOT, CELL, followchannel.VARIANT_SETTER, SLOT + 2, CELL + 2])


@pytest.mark.parametrize("items, needle", [
    ([], "at least one"),
    ([_light(slot=SLOT + 1)], "odd"),
    ([_light(cell=CELL + 1)], "odd"),
    ([_light(slot=0x10000000)], "SRAM"),
    ([_light(cell=0x20080000)], "SRAM"),
    ([_light(mask=0)], "mask"),
    ([_light(shift=16)], "shift"),
    ([_light(value=0x10000)], "value"),
    ([_light(group=0x100)], "variant_group"),
    ([_light(kind="lt")], "kind"),
    ([_light(), _light(slot=SLOT + 2)], "duplicate cell"),
    ([statusfold.Enum(SLOT, 0xFF, 0, tuple(range(17)), CELL)], "16"),
    ([statusfold.Enum(SLOT, 0xFF, 0, (1, 2, 1), CELL)], "duplicate key"),
])
def test_resolve_refusals(items, needle):
    """Validation runs before the hook is read, so no image is needed to reach a refusal."""
    with pytest.raises(statusfold.StatusFoldError, match=needle):
        statusfold.resolve(b"", items)


def test_disassembles():
    plan = _plan(_light(group=3), statusfold.Enum(SLOT + 2, 0xFF, 0, (1, 2), CELL + 2))
    blob, dis = statusfold.build(plan, LOAD)
    assert dis and dis[0] == (LOAD, "push {r0, r1, r2, r3, lr}")
    expected = [t for _a, t in dis]
    code_len = dis[-1][0] + 4 - LOAD                     # the last instruction is the b.w
    thumb.assert_disassembles_to(blob[:code_len], LOAD, expected)
    i = expected.index("movs r1, #0")
    off = dis[i][0] - LOAD
    bad = bytearray(blob[:code_len])
    bad[off] ^= 0x01                                     # movs r1, #0 -> movs r1, #1
    with pytest.raises(ValueError, match="does not disassemble"):
        thumb.assert_disassembles_to(bytes(bad), LOAD, expected)


def test_detour_edit_is_a_bl_to_the_entry():
    plan = _plan(_light())
    at, old, new = statusfold.detour_edit(plan, LOAD)
    assert (at, old) == (followchannel.HOOK_OFF, followchannel.HOOK_STOCK)
    assert new == thumb.bl(theme.GUICODE_BASE_DEFAULT + followchannel.HOOK_OFF, LOAD)
