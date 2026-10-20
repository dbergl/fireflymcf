"""Task 4: the 48V battery per-tick routine (color + level + value).

Pins the emitted instruction stream of `voltagebattery.build_routine` (the standalone builder)
and, at compile level, that `AddVoltageBattery` grows GuiCode and detours the per-tick hook into
the appended routine. The builder itself calls `thumb.assert_disassembles_to` before returning,
so these tests are a second, independent decode of the same bytes.
"""
import hashlib
import json
import struct
from pathlib import Path

import pytest

from fireflymcf import (voltagebattery, thumb, followchannel, theme, container,
                             recipe, guidata, insert)

ROOT = Path(__file__).resolve().parents[1]
STOCK = (ROOT / "firefly-13.6" / "mcf.bin").read_bytes()


SLOT = 0x2000809A
VALUE_CELL = 0x20038100
COLOR_CELL = 0x20038104
LEVEL_CELL = 0x20038108
TAIL = 0x00247BF8


def _text(code, entry):
    return [t for _a, t in thumb.disassemble(code, entry)]


def test_entry_off_is_zero():
    assert voltagebattery.ENTRY_OFF == 0


def test_routine_reads_slot_thresholds_and_levels():
    entry = 0x00260000
    code = voltagebattery.build_routine(
        slot_addr=SLOT, value_cell=VALUE_CELL, color_cell=COLOR_CELL,
        level_cell=LEVEL_CELL, entry_addr=entry, tail_target=TAIL)
    assert isinstance(code, bytes) and len(code) % 4 == 0
    txt = _text(code, entry)
    joined = " ; ".join(txt)

    # prologue / epilogue frame (mirrors readout.build)
    assert txt[0] == "push {r0, r1, r2, r3, lr}"
    assert "pop.w {r0, r1, r2, r3, lr}" in txt
    # reads the slot: a literal load then a halfword read
    assert "ldrh r1, [r0]" in txt
    # the 1008 threshold and the 960/1096 clamp bounds, as movw immediates
    assert "movw r2, #0x3f0" in joined        # 1008
    assert "movw r3, #0x3c0" in joined        # 960  (lo)
    assert "movw r3, #0x448" in joined        # 1096 (hi)
    # threshold branch (unsigned)
    assert "cmp r1, r2" in txt
    assert any(b.startswith("bhs ") for b in txt)
    # clamp branches: unsigned lo-skip (bhs) and hi-skip (bls)
    assert any(b.startswith("bls ") for b in txt)
    # value: raw>>1 then a halfword store
    assert "lsrs r1, r1, #1" in txt
    assert "strh r1, [r0]" in txt
    # level: (hi-raw) via subs, then rescaled onto the stock 0..40 empty-offset span by
    # (delta * 77) >> 8 (was a bare >>2, which maxed at 34 and under-emptied the gauge), byte store
    assert "subs r2, r3, r2" in txt
    assert "movs r3, #0x4d" in txt              # 77, the rescale multiplier
    assert "muls r2, r3, r2" in txt
    assert "lsrs r2, r2, #8" in txt
    assert "strb.w r2, [r0]" in txt
    # color: full-word store of the master colour literal
    assert "str r3, [r0]" in txt
    # tail-chain to the previous chain head: the last real instruction before the pool. The
    # blob carries the literal pool after the code, so the trailing disassembly decodes pool
    # words -- take the instruction right after the pop.w frame restore.
    assert "b.w #0x247bf8" in txt
    assert txt[txt.index("pop.w {r0, r1, r2, r3, lr}") + 1] == "b.w #0x247bf8"


def test_literal_pool_carries_all_operands():
    entry = 0x00260000
    code = voltagebattery.build_routine(
        slot_addr=SLOT, value_cell=VALUE_CELL, color_cell=COLOR_CELL,
        level_cell=LEVEL_CELL, entry_addr=entry, tail_target=TAIL)
    words = set(struct.unpack("<%dI" % (len(code) // 4), code))
    for expected in (SLOT, 0x2BB673, 0xCC2127, COLOR_CELL, LEVEL_CELL, VALUE_CELL):
        assert expected in words, "%#x missing from routine bytes" % expected


def test_custom_colors_and_thresholds_land_in_the_pool_and_immediates():
    entry = 0x00260000
    code = voltagebattery.build_routine(
        slot_addr=SLOT, value_cell=VALUE_CELL, color_cell=COLOR_CELL,
        level_cell=LEVEL_CELL, entry_addr=entry, tail_target=TAIL,
        green=0x111111, red=0x222222, thresh=1000, lo=900, hi=1100)
    words = set(struct.unpack("<%dI" % (len(code) // 4), code))
    assert 0x111111 in words and 0x222222 in words
    joined = " ; ".join(_text(code, entry))
    assert "movw r2, #0x3e8" in joined        # 1000
    assert "movw r3, #0x384" in joined        # 900
    assert "movw r3, #0x44c" in joined        # 1100


def test_unaligned_load_addr_refused():
    with pytest.raises(voltagebattery.VoltageBatteryError):
        voltagebattery.build_routine(
            slot_addr=SLOT, value_cell=VALUE_CELL, color_cell=COLOR_CELL,
            level_cell=LEVEL_CELL, entry_addr=0x00260002, tail_target=TAIL)


def test_tail_chains_to_an_appended_target():
    # when the previous chain head is itself an appended routine, we tail-branch to it
    entry = 0x00260000
    prev = 0x0025F000
    code = voltagebattery.build_routine(
        slot_addr=SLOT, value_cell=VALUE_CELL, color_cell=COLOR_CELL,
        level_cell=LEVEL_CELL, entry_addr=entry, tail_target=prev)
    txt = _text(code, entry)
    assert txt[txt.index("pop.w {r0, r1, r2, r3, lr}") + 1] == "b.w #0x25f000"


# --- compile-level: the op grows GuiCode and detours the per-tick hook into it ---

def _compile(params):
    proj = {"schema": 1, "base_sha256": hashlib.sha256(STOCK).hexdigest(),
            "build_name": "t48v",
            "ops": [{"id": "batt", "type": "add_voltage_battery", "params": params}]}
    return recipe.compile_project(recipe.Project.from_json(json.dumps(proj)), STOCK)


def _guicode_len(data):
    return next(s for s in container.parse_container(data).sections
               if "GuiCode" in s.name).length


def test_op_compiles_grows_guicode_and_detours_hook():
    res = _compile({"donor": 443, "parent": 902, "x": 690, "y": 334})
    assert res.ok, res.errors

    # GuiCode grew by the appended routine
    assert _guicode_len(res.data) > _guicode_len(STOCK)

    # the per-tick hook now points into the appended region (hook -> our routine); the chain
    # walks it and terminates at the stock paint fn (chain_from_hook raises if it does not)
    _, entry = followchannel.hook_state(res.data)
    chain = followchannel.chain_from_hook(res.data)
    # our routine is the only appended link; the chain ends at the displaced stock paint fn
    assert chain == [entry, followchannel.TAIL_TARGET], chain
    assert entry >= followchannel.STOCK_APPEND_POINT, \
        "hook does not reach the appended region: %#x" % entry

    # the routine at that entry reads the 48V slot, stores to the two CUSTOM cells (colour word +
    # level byte; the value reads the live stock cell 209 directly, so no value store), tail-chains
    # to the previous head (the stock paint fn 0x247bf8 on a from-stock build)
    sec = theme.guicode_section(res.data)
    base = theme.GUICODE_BASE_DEFAULT
    off = sec.offset + (entry - base)
    code = res.data[off:off + 0x60]
    txt = [t for _a, t in thumb.disassemble(code, entry)]
    joined = " ; ".join(txt)
    assert "ldrh r1, [r0]" in txt
    assert "movw r2, #0x3f0" in joined          # 1008 threshold
    assert "str r3, [r0]" in txt                # colour word
    assert "strb.w r2, [r0]" in txt             # level byte
    assert "strh r1, [r0]" not in txt           # NO value store -- the value reads live cell 209
    assert txt[txt.index("pop.w {r0, r1, r2, r3, lr}") + 1] == "b.w #0x247bf8"


def test_op_reports_verified_after_v25():
    # coach-verified 2026-09-12 (v25 flashed; number/fill/colour update live on HOME and
    # ELECTRICAL), so add_voltage_battery is hardware_verified=True and no longer reported
    # unverified -- even though it grows fresh tier-3 cells.
    res = _compile({"donor": 443, "parent": 902, "x": 690, "y": 334})
    assert res.ok, res.errors
    assert not any("batt" in u for u in res.unverified), res.unverified
