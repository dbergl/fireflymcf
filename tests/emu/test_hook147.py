"""Authored per-tick routines on 14.7, proved in the emulator (2026-11-07).

`routinekit.install_tick` finds the hook, the chain's end and the host CAN sender in the image
(`hooklocate`): nothing below hands it a 14.7 address. The numbers this module names are test
expectations read from 14.7's own GuiCode, never locators:

* the stock callee F opens `0x247CAC push {r4-r7, lr}` and, at `0x247CB8 ldr r2, [pc, #0x1dc]`
  (literal `0x247E98` = `0x20039D00`), `ldrh; adds #1; strh` a halfword it wraps above `0x77`.
  That halfword advancing is the stock tick still running behind an appended link.
* host API `+0x08`'s cell is `0x20039558` (entry0's copies). That the LCD firmware's `+0x08` is
  the CAN-frame sender on 14.7 was inferred from entry0's shape; `test_sender_cell_sends`
  settles it in the emulator, which runs 14.7's own LCD firmware.

* `routinekit.fresh_sram` refuses on 14.7 (NO_SRAM, open item): `sramreach` names 13.6's
  render-state base `0x200301B0` and digit buffer `0x200371D1`; 14.7 loads `0x200301B4`
  (`0x2540CA ldr r4, [pc, #0x100]`, then `ldr.w r2, [r4, r0, lsl #2]`) and `0x200371D5`, so both
  read as unbounded indexed tables and poison all SRAM above them. The counter cells below are
  therefore test expectations: with those two bases bounded as 13.6's are (state span to
  `0x20039548`, applied here by inference, as 13.6's span is), `0x20039F20` opens 14.7's longest
  free run (64 bytes; the run bound is inferred on the same footing), and no other form
  reaches it (`test_cells_are_reached_only_through_the_unbounded_bases`). The emulator then
  shows nothing else writes them: each counter advances exactly as the stock tick does.

Each test fails on the stock image unchanged: no counter, no link, no frame.
"""
import hashlib
import shutil
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("unicorn")

from fireflymcf import (followchannel, hooklocate, recipe, routinekit, thumb,  # noqa: E402
                        userop)
from fireflymcf.emu import g12, peers  # noqa: E402
from fireflymcf.emu.scenario import PANEL_SA, Scenario  # noqa: E402

NEEDS_RESEARCH_DATA = "installs tick routines on the 14.7 corpus image and boots it in the emulator"

USEROPS = Path(__file__).resolve().parents[1] / "fixtures" / "userops"
SETTLE_S = 12.0
CONFIG_DGN = 0x1FED9
STOCK_TICK_LITERAL = 0x247E98        # F's `0x247CB8 ldr r2, [pc, #0x1dc]` (14.7 expectation)
STOCK_TICK_HALFWORD = 0x20039D00     # what that literal holds (14.7 expectation)
SENDER_CELL = 0x20039558             # host API +0x08's cell on 14.7 (14.7 expectation)
RAM_INIT_END = 0x2000AD98            # the C runtime initialises SRAM only below this (raminit)
CELLS = (0x20039F20, 0x20039F24, 0x20039F28)   # free on 14.7 (module docstring)
UNBOUNDED_147 = (0x200301B4, 0x200371D5)       # 14.7's render state and digit buffer
TICK_PERIOD = 0x78                   # F wraps its halfword above 0x77 (`0x247CC2 cmp r3, #0x77`)
CLOCK = time.struct_time((2026, 11, 7, 11, 0, 0, 5, 311, 0))


class _PinnedTime:
    """`peers`' view of the `time` module: a fixed local time (as `test_ops147`)."""

    @staticmethod
    def localtime(*_):
        return CLOCK


@pytest.fixture(scope="module", autouse=True)
def _pinned_clock():
    built = g12.from_mcf

    def from_mcf(mcf_data, **kwargs):
        return built(mcf_data, **{**kwargs, "clock": _PinnedTime.localtime})

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(g12, "from_mcf", from_mcf)
        mp.setattr(peers, "time", _PinnedTime)
        yield


def _counter_build(cell: int):
    """`build(code_load_addr, tail_target)` for a link that adds 1 to the u32 at `cell`."""
    def build(code_load_addr, tail_target):
        r = thumb.Routine({"cell": cell}, error=routinekit.RoutineKitError)
        routinekit.emit_prologue(r)
        r.ldr_lit(0, "cell")
        r.emit(2, lambda h, s: (thumb.ldr_imm(1, 0, 0), "ldr r1, [r0]"))
        r.emit(2, lambda h, s: (thumb.adds_imm8(1, 1), "adds r1, #1"))
        r.emit(2, lambda h, s: (thumb.str_imm(1, 0, 0), "str r1, [r0]"))
        routinekit.emit_tail(r, tail_target)
        return routinekit.assemble_tick(r, code_load_addr)
    return build


def _install_counter(ed: userop.Editor, cell: int):
    assert cell > RAM_INIT_END, hex(cell)
    return cell, routinekit.install_tick(ed, _counter_build(cell), "counter")


def _boot(image: bytes) -> Scenario:
    s = Scenario(image, timeout_s=300)
    s.boot()
    return s


def _run_to(s: Scenario, t: float) -> None:
    s.run(max(0.0, t - s.now))


def _u32(s: Scenario, addr: int) -> int:
    return int.from_bytes(s.read_mem(addr, 4), "little")


def _u16(s: Scenario, addr: int) -> int:
    return int.from_bytes(s.read_mem(addr, 2), "little")


def _stock_tick_samples(s: Scenario, n: int = 5, dt: float = 0.13) -> list[int]:
    out = []
    for _ in range(n):
        out.append(_u16(s, STOCK_TICK_HALFWORD))
        s.run(dt)
    return out


@pytest.fixture(scope="module")
def stock_home(mcf_147, tmp_path_factory):
    s = _boot(mcf_147)
    _run_to(s, SETTLE_S)
    return s.screenshot(tmp_path_factory.mktemp("hook147") / "stock.png").astype(int)


def test_expectations_read_from_the_image(mcf_147):
    """The module's 14.7 numbers are what 14.7's GuiCode holds; the locators found the same."""
    h = hooklocate.locate_hook(mcf_147)
    sec_off = routinekit.guicode(mcf_147).offset
    base = routinekit.guicode(mcf_147).load_addr
    at = sec_off + STOCK_TICK_LITERAL - base
    assert int.from_bytes(mcf_147[at:at + 4], "little") == STOCK_TICK_HALFWORD
    assert h.stock_callee <= 0x247CB8 < h.stock_callee + 0x10
    assert hooklocate.host_api_cells(mcf_147)[0x08] == SENDER_CELL


def test_cells_are_reached_only_through_the_unbounded_bases(mcf_147):
    from fireflymcf import sramreach
    for cell in CELLS:
        forms = sramreach.reach(mcf_147).forms(cell, 4)
        assert forms and all(f == "indexed_table" and d[0] in UNBOUNDED_147
                             for f, d in forms), (hex(cell), forms)


def test_fresh_sram_refuses_on_147(mcf_147):
    """Open item: a defensible refusal, not a wrong cell (module docstring)."""
    with pytest.raises(routinekit.RoutineKitError) as e:
        userop.Editor(mcf_147).call("fresh_sram", 4)
    assert e.value.code == "NO_SRAM"


def _in_step_with_stock_tick(s: Scenario, cell: int, run_s: float) -> tuple[int, int]:
    """Run `run_s` and return (counter delta, stock tick delta): the link and F each run once
    per tick, so the deltas agree modulo F's wrap unless something else writes the cell."""
    c0, t0 = _u32(s, cell), _u16(s, STOCK_TICK_HALFWORD)
    s.run(run_s)
    c1, t1 = _u32(s, cell), _u16(s, STOCK_TICK_HALFWORD)
    dc = (c1 - c0) & 0xFFFFFFFF
    assert 0 < dc < 10_000, (c0, c1)
    assert dc % TICK_PERIOD == (t1 - t0) % TICK_PERIOD, (dc, t0, t1)
    return dc, t1 - t0


def test_hook_fires_each_tick(mcf_147, stock_home, tmp_path):
    ed = userop.Editor(mcf_147)
    cell, inst = _install_counter(ed, CELLS[0])
    assert followchannel.chain_from_hook(ed.data) == [
        inst.entry_addr, hooklocate.locate_hook(mcf_147).stock_callee]
    s = _boot(ed.data)
    _run_to(s, SETTLE_S - 1.0)
    _in_step_with_stock_tick(s, cell, 1.0)
    home = s.screenshot(tmp_path / "home.png").astype(int)
    assert np.array_equal(home, stock_home), "the link changed HOME's pixels"
    ticks = _stock_tick_samples(s)
    assert len(set(ticks)) > 1, ("the stock callee's tick halfword is frozen", ticks)


def test_chain_of_two(mcf_147):
    ed = userop.Editor(mcf_147)
    first_cell, first = _install_counter(ed, CELLS[0])
    second_cell, second = _install_counter(ed, CELLS[1])
    callee = hooklocate.locate_hook(mcf_147).stock_callee
    assert followchannel.chain_from_hook(ed.data) == [second.entry_addr, first.entry_addr, callee]
    assert second.tail_target == first.entry_addr and first.tail_target == callee
    assert userop.replay(mcf_147, ed.ledger) == ed.data
    s = _boot(ed.data)
    _run_to(s, 5.0)
    a = (_u32(s, first_cell), _u32(s, second_cell))
    _in_step_with_stock_tick(s, first_cell, 1.0)
    _in_step_with_stock_tick(s, second_cell, 1.0)
    b = (_u32(s, first_cell), _u32(s, second_cell))
    assert (b[0] - a[0]) & 0xFFFFFFFF == (b[1] - a[1]) & 0xFFFFFFFF, \
        ("both links run once per tick", a, b)


def test_sender_cell_sends(mcf_147):
    """A latched link sends one fixed frame through host API +0x08's cell: the panel transmits
    it once, with the template's id intact. This is what settles +0x08 = the sender on 14.7."""
    tpl = routinekit.config_frame(0x2F, 0xFFFF, 0xD3)
    ed = userop.Editor(mcf_147)
    flag = CELLS[2]
    plan = ed.call("tick_install_plan")
    assert plan.sender_addr == hooklocate.host_api_cells(mcf_147)[0x08] == SENDER_CELL
    pools = []

    def build(code_load_addr, tail_target):
        r = thumb.Routine({"flag": flag}, error=routinekit.RoutineKitError)
        routinekit.emit_prologue(r)
        r.ldr_lit(0, "flag")
        r.emit(2, lambda h, s: (thumb.ldrh_imm(1, 0, 0), "ldrh r1, [r0]"))
        r.emit(2, lambda h, s: (thumb.cmp_imm8(1, 0x5A), "cmp r1, #0x5a"))
        r.emit(2, lambda h, s: (thumb.bcc_n(h, s("done"), "eq"), f"beq #{s('done'):#x}"))
        r.emit(2, lambda h, s: (thumb.movs_imm(1, 0x5A), "movs r1, #0x5a"))
        r.emit(2, lambda h, s: (thumb.strh_imm(1, 0, 0), "strh r1, [r0]"))
        routinekit.emit_send_frame(r, code_load_addr)         # the template is the prefix
        r.label("done")
        routinekit.emit_tail(r, tail_target)
        pools.append(dict(r.pool))
        return routinekit.assemble_tick(r, code_load_addr, prefix=tpl)

    routinekit.install_tick(ed, build, "send once")
    assert pools[0]["_sender"] == SENDER_CELL
    s = _boot(ed.data)
    _run_to(s, 5.0)
    assert s.read_mem(flag, 2) == b"\x5a\x00"
    mine = [f for f in s.frames(dgn=CONFIG_DGN, sa=None) if f.data == tpl[4:]]
    assert len(mine) == 1, mine
    assert mine[0].sa == PANEL_SA, hex(mine[0].sa)


def test_stock_sends_no_such_frame(mcf_147):
    """The control for `test_sender_cell_sends`: stock 14.7 never sends that read-back."""
    tpl = routinekit.config_frame(0x2F, 0xFFFF, 0xD3)
    s = _boot(mcf_147)
    _run_to(s, 5.0)
    assert not [f for f in s.frames(dgn=CONFIG_DGN, sa=None) if f.data == tpl[4:]]


def test_user_op_tick_on_147(mcf_147, tmp_path):
    """The `send_frame` user-op fixture (a tick routine sending a D1 write and a D3 read-back of
    selector 0xEE every tick) compiles on 14.7 through `compile_project`, and the panel sends
    both frames, from its own address, each tick."""
    shutil.copytree(USEROPS / "send_frame", tmp_path / "ops" / "send_frame")
    pf = tmp_path / "p.ffproj.json"
    pf.write_text("{}")
    proj = recipe.Project(base_sha256=hashlib.sha256(mcf_147).hexdigest(),
                          build_name=None,
                          ops=[recipe.OpSpec(id="u", type="user:send-frame", params={})])
    with userop.registered(userop.load_user_ops(pf, proj)):
        res = recipe.compile_project(proj, mcf_147)
    assert res.ok, res.errors
    assert res.firmware == "14.7" and res.unverified == ["u"]
    chain = followchannel.chain_from_hook(res.data)
    assert len(chain) == 2 and chain[1] == hooklocate.locate_hook(mcf_147).stock_callee

    write = routinekit.config_frame(0xEE, 0, 0xD1)[4:]
    readback = routinekit.config_frame(0xEE, 0xFFFF, 0xD3)[4:]
    s = _boot(res.data)
    _run_to(s, 5.0)
    t = s.now
    s.run(1.0)
    sent = s.frames(dgn=CONFIG_DGN, sa=None, since=t)
    w = [f for f in sent if f.data == write]
    rb = [f for f in sent if f.data == readback]
    assert w and rb, sent[:5]
    assert {f.sa for f in w + rb} == {PANEL_SA}
    assert abs(len(w) - len(rb)) <= 1, (len(w), len(rb))
