"""`routinekit`: the primitive surface user ops build on.

The emulator tests boot 13.6 builds in `Scenario` (a few seconds of wall time each); the rest are
static. Every routine here is installed through `userop.Editor`, so each test also proves the
call replays.
"""
import hashlib
import json
import struct

import pytest

from fireflymcf import (followchannel, pumpauto, recipe, register, routinekit, sramreach,
                        theme, thumb, userop)

NEEDS_RESEARCH_DATA = "installs routines on the 13.6 corpus image and boots it in the emulator"

CONFIG_DGN = 0x1FED9
PANEL_SA = 0x9F


# --- helpers -------------------------------------------------------------------------------

def _counter_build(cell: int):
    """A `build(code_load_addr, tail_target)` for a routine that adds 1 to the u32 at `cell`."""
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


def _install_counter(data: bytes):
    """`(editor, cell, TickInstall)`: the counter routine installed through the Editor."""
    ed = userop.Editor(data)
    cell = ed.call("fresh_sram", 4)
    inst = routinekit.install_tick(ed, _counter_build(cell), "counter")
    return ed, cell, inst


def _routine_build_ops():
    return [
        {"id": "link", "type": "add_option_follower", "enabled": True,
         "params": {"watch_channel": 25, "target_channel": 44, "gate_mask": 0xFF00,
                    "gate_on_value": 0x100}},
        {"id": "pump", "type": "add_pump_auto_off", "enabled": True,
         "params": {"allow_disable": True}},
        {"id": "f", "type": "append_filter", "enabled": True,
         "params": {"dgn": 0x1FFD7, "mode": 2, "payload_byte": 3, "instance": 0x41,
                    "compare_byte": 0}},
        {"id": "r", "type": "add_register_readout", "enabled": True,
         "params": {"register": {"ref": "f"}, "sub": 32000, "shift": 1, "draw": False}},
    ]


def _routine_build(stock):
    """Ruling R2a's stand-in for a reference build: option follower, pump auto off, one
    register readout."""
    ops = _routine_build_ops()
    p = {"schema": 1, "base_sha256": hashlib.sha256(stock).hexdigest(),
         "build_name": "routinekit-test", "ops": ops}
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(p)), stock)
    assert res.ok, res.errors
    return res.data


def _pumpauto_display_addr(data: bytes) -> int:
    """pumpauto's minutes readout byte, read out of its own routine: the routine's first literal
    load is the 0xEE config slot (pool word 0), and the display cell is pool word 2."""
    want = register.status_slot_addr(289)
    sec = theme.guicode_section(data)
    base = theme.GUICODE_BASE_DEFAULT
    for entry in followchannel.chain_from_hook(data)[:-1]:
        at = entry - base + sec.offset
        (_, text), = thumb.disassemble(data[at + 2:at + 4], entry + 2)
        if not text.startswith("ldr r0, [pc"):
            continue
        off = int(text.split("#")[1].rstrip("]"), 16) if "#" in text else 0
        lit = ((entry + 2 + 4) & ~3) + off
        lit_at = lit - base + sec.offset
        if struct.unpack_from("<I", data, lit_at)[0] == want:
            return struct.unpack_from("<I", data, lit_at + 8)[0]
    raise AssertionError("no pumpauto routine on the chain")


def _u32(s, addr):
    return int.from_bytes(s.read_mem(addr, 4), "little")


# --- static ---------------------------------------------------------------------------------

def test_slot_addr_index_split(mcf_136):
    assert routinekit.slot_addr(mcf_136, 255) == (mcf_136, 0x20007FC8 + 510)
    assert routinekit.slot_addr(mcf_136, 256) == (mcf_136, 0x20008000 + 512)
    assert routinekit.slot_addr(mcf_136, 289)[1] == register.status_slot_addr(289)
    n = len(__import__("fireflymcf").guidata.variable_table(mcf_136))
    with pytest.raises(routinekit.RoutineKitError, match="variable table"):
        routinekit.slot_addr(mcf_136, n)


def test_config_frame_matches_pumpauto(mcf_136):
    cfg = register.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=0xEE)
    pump = register.resolve_register(mcf_136, dgn="DC_DIMMER_STATUS_3", instance=44)
    plan = pumpauto.resolve(mcf_136, config=cfg, pump=pump)
    assert routinekit.config_frame(0xEE, 0, 0xD1) == plan.write_template
    assert routinekit.config_frame(0xEE, 0xFFFF, 0xD3) == plan.readback_template
    # PC.47's host write, hardware-observed: FF96EE0F0100D1EA
    assert routinekit.config_frame(0xEE, 0x0001, 0xD1)[4:] == bytes.fromhex("ff96ee0f0100d1ea")
    # 13.6's own D2 pool record (file 0x28cec): a +1000 delta on 0xCC
    assert routinekit.config_frame(0xCC, 1000, 0xD2) == bytes.fromhex("00d9fe19ff96cc0fe803d2ea")
    # pumpauto's private name still resolves to the promoted function
    assert pumpauto._config_frame is pumpauto.config_frame
    with pytest.raises(routinekit.RoutineKitError):
        routinekit.config_frame(0xEE, 0, 0xD4)


def _norm_ldr(text: str) -> str:
    return text.split(", [pc")[0] + ", =lit" if ", [pc" in text else text


def test_emit_send_frame_is_pumpautos_sequence():
    """The sender call `emit_send_frame` emits is pumpauto's own, instruction for instruction
    (literal offsets aside): rebuilt here from `pumpauto.build`'s output."""
    tpl = routinekit.config_frame(0x2F, 0xFFFF, 0xD3)
    r = thumb.Routine({}, error=routinekit.RoutineKitError)
    routinekit.emit_prologue(r)
    routinekit.emit_send_frame(r, 0x00259BB4, sender_addr=followchannel.SENDER_PTR_ADDR)
    routinekit.emit_tail(r, followchannel.TAIL_TARGET)
    _, dis = r.assemble(0x00259BB4 + len(tpl), 0)
    mine = [_norm_ldr(t) for _, t in dis][1:-2]

    plan = pumpauto.PumpAutoPlan(
        config_slot=0x20008242, pump_slot=0x20008000, display_addr=0x2003A000,
        tick_anchor_addr=0x2003A001, countdown_addr=0x2003A010, hook_at=followchannel.HOOK_OFF,
        hook_old=followchannel.HOOK_STOCK, tail_target=followchannel.TAIL_TARGET,
        sender_ptr=followchannel.SENDER_PTR_ADDR, off_template=bytes(12),
        default_word=30, write_template=tpl, readback_template=tpl, config_selector=0x2F,
        display_slot=211, countdown_slot=215)
    _, pdis = pumpauto.build(plan, 0x00259BB4)
    ptext = [_norm_ldr(t) for _, t in pdis]
    n = len(mine)
    assert any(ptext[i:i + n] == mine for i in range(len(ptext) - n + 1)), mine

    with_value = thumb.Routine({}, error=routinekit.RoutineKitError)
    routinekit.emit_send_frame(with_value, 0x00259BB4, value_in_r3=True,
                               sender_addr=followchannel.SENDER_PTR_ADDR)
    _, vdis = with_value.assemble(0x00259BC0, 0)
    vtext = [_norm_ldr(t) for _, t in vdis]
    assert "strh r3, [r0, #8]" in vtext
    assert any(ptext[i:i + len(vtext)] == vtext for i in range(len(ptext) - len(vtext) + 1))
    # the pool carries the sender pointer and the template address
    assert set(with_value.pool.values()) == {followchannel.SENDER_PTR_ADDR, 0x00259BB4}


def test_install_tick_routine_static(mcf_136):
    ed, cell, inst = _install_counter(mcf_136)
    assert inst.tail_target == followchannel.TAIL_TARGET
    assert inst.hook_old == followchannel.HOOK_STOCK
    assert inst.load_addr == followchannel.STOCK_APPEND_POINT
    assert followchannel.chain_from_hook(ed.data) == [inst.entry_addr, followchannel.TAIL_TARGET]
    assert userop.replay(mcf_136, ed.ledger) == ed.data
    # the counter cell is now reached by the routine's pool word: a second allocation avoids it
    assert sramreach.is_referenced(ed.data, cell, 4)
    assert ed.call("fresh_sram", 4) != cell
    # a stale plan is refused, and nothing is written
    plan = routinekit.tick_install_plan(mcf_136)[1]
    blob, entry = _counter_build(cell)(plan.code_load_addr, plan.tail_target)
    with pytest.raises(routinekit.RoutineKitError, match="STALE|plan"):
        routinekit.install_tick_routine(ed.data, blob, entry, "again",
                                        code_load_addr=plan.code_load_addr,
                                        tail_target=plan.tail_target)


def test_install_tick_routine_refuses_a_broken_link(mcf_136):
    plan = routinekit.tick_install_plan(mcf_136)[1]
    r = thumb.Routine({}, error=routinekit.RoutineKitError)
    routinekit.emit_prologue(r)
    r.emit(4, lambda h, s: (thumb.pop_w_reglist([0, 1, 2, 3, 14]), "pop.w {r0, r1, r2, r3, lr}"))
    r.emit(4, lambda h, s: (thumb.b_w(h, 0x00247000), "b.w #0x247000"))     # not the tail
    blob, entry = routinekit.assemble_tick(r, plan.code_load_addr)
    with pytest.raises(routinekit.RoutineKitError, match="tail"):
        routinekit.install_tick_routine(mcf_136, blob, entry, "bad",
                                        code_load_addr=plan.code_load_addr,
                                        tail_target=plan.tail_target)
    r = thumb.Routine({}, error=routinekit.RoutineKitError)
    r.emit(4, lambda h, s: (thumb.b_w(h, plan.tail_target), f"b.w #{plan.tail_target:#x}"))
    blob, entry = routinekit.assemble_tick(r, plan.code_load_addr)
    with pytest.raises(routinekit.RoutineKitError, match="push"):
        routinekit.install_tick_routine(mcf_136, blob, entry, "no prologue",
                                        code_load_addr=plan.code_load_addr,
                                        tail_target=plan.tail_target)


def test_every_library_link_passes_the_link_check(mcf_136):
    """The prologue/tail check `install_tick_routine` applies holds for every routine the
    library's own ops chain, so it describes the existing contract rather than a new one."""
    data = _routine_build(mcf_136)
    chain = followchannel.chain_from_hook(data)
    assert len(chain) >= 4
    for entry, nxt in zip(chain, chain[1:]):
        routinekit._check_link(data, entry, nxt)


def test_fresh_sram_and_registers(mcf_136):
    _, a = routinekit.fresh_sram(mcf_136, 4)
    assert a % 4 == 0 and not sramreach.is_referenced(mcf_136, a, 4)
    _, b = routinekit.fresh_sram(mcf_136, 4, taken=list(range(a, a + 4)))
    assert b != a and not (a <= b < a + 4)
    _, reg = routinekit.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=0xEE)
    assert reg.var_index == 289 and reg.slot_addr == 0x20008242
    _, reg = routinekit.resolve_register(mcf_136, dgn="DC_DIMMER_STATUS_3", instance=44)
    assert reg.slot_addr == register.status_slot_addr(reg.var_index)
    with pytest.raises(routinekit.RoutineKitError):
        routinekit.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=0x01FE)
    assert routinekit.find_config_toggle(mcf_136, 0xEE) == (mcf_136, None)
    link = recipe.compile_project(recipe.Project.from_json(json.dumps({
        "schema": 1, "base_sha256": hashlib.sha256(mcf_136).hexdigest(), "build_name": "t",
        "ops": [_routine_build_ops()[0]]})), mcf_136)
    assert link.ok, link.errors
    assert routinekit.find_config_toggle(link.data, 0xEE) == (link.data, 164)
    # pumpauto's +/-5 stepper groups qualify too: three candidates, so it refuses rather than pick
    with pytest.raises(routinekit.RoutineKitError, match="ambiguous") as e:
        routinekit.find_config_toggle(_routine_build(mcf_136), 0xEE)
    assert e.value.code == "AMBIGUOUS_TOGGLE"


def test_primitives_registered():
    assert set(routinekit.PRIMITIVES) == {"tick_install_plan", "install_tick_routine",
                                          "slot_addr", "resolve_register", "fresh_sram",
                                          "find_config_toggle", "own_colour_cell",
                                          "own_colour_cells", "repaint_on_cell",
                                          "own_repaint_trigger"}


# --- emulator ---------------------------------------------------------------------------------

def _scenario(data):
    pytest.importorskip("unicorn")
    from fireflymcf.emu.scenario import Scenario
    s = Scenario(data, timeout_s=300)
    s.boot()
    return s


def _run_to(s, t):
    s.run(max(0.0, t - s.now))


def test_tick_routine_runs_each_tick(mcf_136):
    ed, cell, _ = _install_counter(mcf_136)
    s = _scenario(ed.data)
    _run_to(s, 5.0)
    a = _u32(s, cell)
    _run_to(s, 10.0)
    b = _u32(s, cell)
    assert 0 < a < b, (a, b)


def test_tick_routine_chains_after_existing(mcf_136):
    from fireflymcf.emu.canbus import Peer

    base = _routine_build(mcf_136)
    before = followchannel.chain_from_hook(base)
    display = _pumpauto_display_addr(base)
    ed, cell, inst = _install_counter(base)
    assert followchannel.chain_from_hook(ed.data) == [inst.entry_addr] + before
    assert inst.tail_target == before[0]
    assert userop.replay(base, ed.ledger) == ed.data

    s = _scenario(ed.data)

    class Host(Peer):
        sa = 0x42

    host = s._m.canbus.add_peer(Host())
    _run_to(s, 6.0)
    c0 = _u32(s, cell)
    for word in (0x0014, 0x0123):               # 20 min link off, then 35 min link on
        host.send(CONFIG_DGN, routinekit.config_frame(0xEE, word, 0xD1)[4:])
        s.run(1.0)
        assert s.g12_selector(0xEE) == word
        assert s.read_mem(display, 1)[0] == word & 0xFF, (hex(word), s.read_mem(display, 2))
    assert _u32(s, cell) > c0


def test_emit_send_frame_transmits(mcf_136):
    tpl = routinekit.config_frame(0x2F, 0xFFFF, 0xD3)
    ed = userop.Editor(mcf_136)
    flag = ed.call("fresh_sram", 4)

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
        return routinekit.assemble_tick(r, code_load_addr, prefix=tpl)

    routinekit.install_tick(ed, build, "send once")
    s = _scenario(ed.data)
    _run_to(s, 5.0)
    assert s.read_mem(flag, 2) == b"\x5a\x00"
    mine = [f for f in s.frames(dgn=CONFIG_DGN, sa=PANEL_SA) if f.data == tpl[4:]]
    assert len(mine) == 1, mine
    assert mine[0].t <= 2.0, mine           # the first ticks run well inside 2 s of power-on
    # the G12 answers the read-back with its 0x2F report
    answers = [f for f in s.frames(sa=None, since=mine[0].t)
               if f.sa != PANEL_SA and len(f.data) > 2 and f.data[0] == 0x2F]
    assert answers and answers[0].t - mine[0].t <= 2.0, answers[:3]


def test_readers_find_a_tagged_routine(mcf_136):
    """`guicode`, `find_tag`, `tick_chain`: the stable readers a user op's check and emulator test
    use to find what it installed (the write-new-op example relies on them)."""
    g = routinekit.guicode(mcf_136)
    sec = theme.guicode_section(mcf_136)
    assert (g.load_addr, g.offset, len(g.body)) == (theme.GUICODE_BASE_DEFAULT, sec.offset,
                                                     sec.length)
    assert g.at(g.load_addr + followchannel.HOOK_OFF, 4) == followchannel.HOOK_STOCK
    assert routinekit.tick_chain(mcf_136) == followchannel.chain_from_hook(mcf_136)
    with pytest.raises(routinekit.RoutineKitError) as e:
        routinekit.find_tag(mcf_136, b"uTST")
    assert e.value.code == "NO_TAG"
    with pytest.raises(routinekit.RoutineKitError):
        g.at(g.load_addr + len(g.body) - 2, 4)

    ed = userop.Editor(mcf_136)
    cell = ed.call("fresh_sram", 4)
    tag = b"uTST" + struct.pack("<I", cell)

    def build(code_load_addr, tail_target):
        r = thumb.Routine({}, error=routinekit.RoutineKitError)
        routinekit.emit_prologue(r)
        routinekit.emit_tail(r, tail_target)
        return routinekit.assemble_tick(r, code_load_addr, prefix=tag)

    inst = routinekit.install_tick(ed, build, "tagged")
    at = routinekit.find_tag(ed.data, b"uTST")
    assert at == inst.load_addr and routinekit.guicode(ed.data).u32(at + 4) == cell
    assert routinekit.tick_chain(ed.data)[0] == inst.entry_addr == at + 8
    with pytest.raises(routinekit.RoutineKitError) as e:
        routinekit.find_tag(ed.data + b"", b"\x00\x00\x00\x00")
    assert e.value.code == "AMBIGUOUS_TAG"
