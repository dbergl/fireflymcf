"""Task 7 / rev 2: the live Low Volts trigger on the compact AES ELECTRICAL page (Mechanism B).

Stock freezes the Low Volts recolor cell (cell 45) in AES -- a native GuiCode gate at
0x0024A172 skips the cell-45 decode whenever the mode is AES, the only mode object 829 is
ever on screen for. Rev 2 reuses the un-frozen 0x2F receive slot (var 42, slot 0x2000801C)
with the hardware-proven v9 follower toolchain to drive an AGS-STYLE Low Volts control that
works in AES -- with NO gate surgery. The appended bridge MIRRORS the stock cell-45 block
un-gated: each tick it updates the group-129 variant counter through the firmware's OWN
setVariant (a `blx` through the fn-ptr at 0x20039908, r0=0x81, r1=!armed -- rev 3, replacing
the earlier direct 0x20007686 byte-write, fixing the one-way toggle, FIX 2), writes the
armed-flag byte 0x20039A79 (stock 0x24a182), and recolors cell 45 (0x2003A814 = armed ? ON
master 0x2003A1A0 : OFF master 0x20039A58, FIX 3), then tail-calls the displaced paint fn.

Everything here is HARDWARE-AFFECTING and coach-gated: the renderer is NOT an oracle for the
bridge/cell wiring, so these unit-prove the joins (register, authored bytes, control shape,
both ops compile with the golden AGS net still green) and leave the recolor/toggle to the coach.
"""
import json
import struct

from fireflymcf import (aescompact, followchannel, guidata, records, recipe, register,
                              statusbind, theme, thumb)

STOCK = "firefly-13.6/mcf.bin"

# The two AES ops in isolation. Was the `aes-compact` recipe; inlined here (and in
# test_aes_electrical_build.py) when that fixture file was removed, so this test still exercises
# `swap_aes_electrical` + `add_aes_low_volts_trigger` as a standalone 2-op build against stock.
AES_COMPACT_RECIPE = json.dumps({
    "schema": 1,
    "base_sha256": "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382",
    "build_name": "aes-compact",
    "ops": [
        {"id": "aesswap", "type": "swap_aes_electrical", "params": {}, "enabled": True},
        {"id": "aeslowvolts", "type": "add_aes_low_volts_trigger", "params": {}, "enabled": True},
    ],
})


def _stock():
    return open(STOCK, "rb").read()


def _build():
    stock = _stock()
    proj = recipe.Project.from_json(AES_COMPACT_RECIPE)
    res = recipe.compile_project(proj, stock)
    assert res.ok, res.errors
    return stock, res


# --- Step 1: the register resolves to the un-frozen 0x2F receive slot -------------------

def test_low_volts_register_resolves_to_unfrozen_slot():
    reg = register.resolve_register(_stock(), dgn="DGN_CONFIG", selector=0x2F)
    assert reg.var_index == 42 and reg.slot_addr == 0x2000801C


# --- The str.w word-store encoder the recolor uses is byte-exact against 13.6 -----------

def test_str_imm_matches_stock_cell45_store():
    """The recolor writes a colour WORD to cell 45; `thumb.str_imm(2,3,0)` is `str r2,[r3]` =
    0x601a, byte-identical to the stock cell-45 recolor store at 0x0024A1A6 in 13.6."""
    assert thumb.str_imm(2, 3, 0) == bytes.fromhex("1a60")
    thumb.assert_disassembles_to(thumb.str_imm(1, 2, 0), 0x259000, ["str r1, [r2]"])


# --- Step 2 (bridge): the authored bridge mirrors the stock block and round-trips -------

def test_display_bridge_calls_setvariant_and_writes_cell():
    """REV 3: the bridge reads the 0x2F slot 0x2000801C, updates the group-129 counter via the
    FIRMWARE'S OWN setVariant (a `blx` through the fn-ptr at 0x20039908, r0=0x81, r1=!armed) --
    NOT a direct 0x20007686 byte-write -- writes the armed-flag byte 0x20039A79, and recolors
    cell 45 (0x2003A814) from the ON/OFF master colours. Exactly the stock cell-45 block un-gated.
    It round-trips through the disassembler (build_display_bridge runs assert_disassembles_to over
    every instruction internally)."""
    reg = register.resolve_register(_stock(), dgn="DGN_CONFIG", selector=0x2F)
    plan = followchannel.resolve_display_bridge(_stock(), gate_addr=reg.slot_addr)
    code_load_addr = 0x00259BB4
    blob, disasm = followchannel.build_display_bridge(plan, code_load_addr)
    assert len(blob) % 4 == 0
    texts = [t for _a, t in disasm]

    # Contract: push/pop {r0-r3,lr} + tail-call to the displaced paint fn.
    assert texts[0] == "push {r0, r1, r2, r3, lr}"
    assert texts[-1] == "b.w #0x247bf8"
    assert texts[-2] == "pop.w {r0, r1, r2, r3, lr}"

    # It reads the gate slot as a halfword and compares it to the armed value (1).
    assert "ldrh r0, [r0]" in texts
    assert "cmp r0, #1" in texts

    # The counter is updated via setVariant, NOT a direct write: the fn-ptr is loaded from the
    # setter address, dereferenced, and CALLED (`blx r3`) with r0=0x81 (group 129) and r1=!armed
    # (0 in the armed arm, 1 in the disarmed arm) -- in BOTH arms.
    assert texts.count("ldr r3, [r3]") == 2, "setter fn-ptr not dereferenced in both arms"
    assert texts.count("blx r3") == 2, "setVariant not called (blx to the fn-ptr) in both arms"
    assert texts.count("movs r0, #0x81") == 2, "group id 0x81 not passed to setVariant in both arms"
    assert texts.count("movs r1, #0") == 2 and texts.count("movs r1, #1") == 2
    # And it does NOT poke the counter/twin directly any more.
    assert "strb.w r1, [r3]" not in texts
    assert "strb.w r1, [r3, #1]" not in texts

    # The armed-flag byte is written (stock 0x24a182), once, before the branch.
    assert texts.count("strb.w r1, [r2]") == 1

    # It stores a colour WORD into cell 45 via the SHARED rev-2 recolor tail (each arm selects
    # the ON/OFF master pointer, then one `ldr r1,[r2]; ldr r2,[=cell]; str r1,[r2]` copies it).
    # Sharing the store keeps `theme._copy_store_masters` recording only the fall-through OFF-master
    # copy (== stock's own cell-45 master), so the golden AGS render stays byte-identical.
    assert texts.count("str r1, [r2]") == 1
    assert texts.count("ldr r1, [r2]") == 1

    # The pool carries the six stock addresses, in order: gate, setter, armflag, ON, OFF, cell 45.
    pool = struct.unpack("<6I", blob[-24:])
    assert pool == (0x2000801C, 0x20039908, 0x20039A79, 0x2003A1A0, 0x20039A58, 0x2003A814)
    assert pool[1] == followchannel.VARIANT_SETTER
    assert pool[2] == followchannel.ARM_FLAG_ADDR
    assert pool[5] == followchannel.RECOLOR_CELL_ADDR

    # Independently re-round-trip the exact code region.
    code = blob[:disasm[-1][0] - code_load_addr + 4]
    thumb.assert_disassembles_to(code, code_load_addr, texts)


def test_bridge_addresses_match_the_stock_cell45_block():
    """The pinned addresses are the stock block's own (proven-from-code): the group-129 counter
    is 0x20006E6C + 129*16 + 0xA, and cell 45 is 0x2003A814."""
    assert followchannel.VARIANT_COUNTER_ADDR == 0x20006E6C + 129 * 16 + 0xA == 0x20007686
    assert followchannel.VARIANT_COUNTER_TWIN == followchannel.VARIANT_COUNTER_ADDR + 1
    assert followchannel.ON_MASTER_COLOR_ADDR == 0x2003A1A0
    assert followchannel.OFF_MASTER_COLOR_ADDR == 0x20039A58
    assert followchannel.RECOLOR_CELL_ADDR == 0x2003A814
    # REV 3: the setter fn-ptr (stock 0x24a18c/0x24a4e8 load) and the armed-flag byte (0x24a182).
    assert followchannel.VARIANT_SETTER == 0x20039908
    assert followchannel.ARM_FLAG_ADDR == 0x20039A79
    assert followchannel.VARIANT_GROUP_LOW_VOLTS == 0x81


# --- Step 3 (full build): both ops coach-verified 2026-09-01, golden AGS net still green ----

def test_full_build_compiles_with_both_ops_verified():
    """Both ops were flashed and confirmed on the coach 2026-09-01, so the
    compile must NOT list them as unverified -- the flag is what the Studio UI surfaces."""
    _stock_bytes, res = _build()
    assert res.ok, res.errors
    assert res.unverified == []


def test_ags_style_low_volts_box_present_in_cluster():
    """rev4: the AGS-style wtype-5 recolor container on value_slot 45 (the same cell the bridge
    drives) lives in the cloned cluster the compact frame recurses into -- NOT a wtype-6 checkbox
    on a spare cell, and NOT spliced into object 829's own record stream. The gear nav id and the
    group-129 Low Volts touch are both in that cluster too."""
    _stock_bytes, res = _build()
    data = res.data
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    frame = records.object_records(data, gs + tab[aescompact.ELECTRICAL_AES_BODY])

    # The compact frame is the faithful 7-record mirror; record 5 is the cluster container.
    assert [r.wtype for r in frame] == [33, 11, 5, 11, 5, 5, 6]
    cluster_index = frame[5].child_index
    recs = records.object_records(data, gs + tab[cluster_index])

    # A wtype-5 recolor container reading slot 45 exists in the cluster.
    cont = next((r for r in recs if r.wtype == 5
                 and r.value_slot == aescompact.RECOLOR_SLOT), None)
    assert cont is not None, "no wtype-5 Low Volts container reading slot 45 in the cloned cluster"
    assert aescompact.RECOLOR_SLOT == 45

    # Slot 45 resolves to the cell the bridge writes (0x2003A814).
    cell_addr = struct.unpack_from(
        "<I", data, gs + statusbind.CELL_TABLE_OFF + cont.value_slot * 4)[0]
    assert cell_addr == 0x2003A814 == followchannel.RECOLOR_CELL_ADDR

    # The group-129 Low Volts toggle and the gear nav id both live in the cluster.
    ids = [data[r.end - 2] | (data[r.end - 1] << 8) for r in recs if r.wtype == 12]
    assert aescompact.TRIGGER_TOUCH_ID in ids
    assert aescompact.SETTINGS_NAV_TOUCH_ID in ids


def test_bridge_detour_applied_in_the_full_build():
    _stock_bytes, res = _build()
    data = res.data
    from fireflymcf import container
    gcs = next(s for s in container.parse_container(data).sections if "GuiCode" in s.name)
    detour = data[gcs.offset + followchannel.HOOK_OFF:][:4]
    assert detour != followchannel.HOOK_STOCK
    text = thumb.disassemble(detour, theme.GUICODE_BASE_DEFAULT + followchannel.HOOK_OFF)[0][1]
    assert text == "bl #0x259bb4"
