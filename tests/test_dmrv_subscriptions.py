"""GUICODE Finding 77: the panel's five DM_RV (`0x1FECA`) receive-filter variables are write-only,
and DM_RV reaches the screen only through three source-address-keyed frame parsers in GuiCode, one
of them a solar-controller branch inside `FUN_00240D20`.

Everything is read from the 13.6 image (and, for the slot-read negative, the running v32 image
too), so the finding stays anchored to bytes rather than to prose.
"""
import struct

import pytest
from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs

from fireflymcf import guidata, theme

STOCK = "firefly-13.6/mcf.bin"
DM_RV = 0x1FECA
SLOT_BASE = 0x20007FC8               # GuiCode_entry1's r0: struct 0x20007EBC + 0x10C
HIGH_SLOT_BASE = 0x20008000          # var index > 255 (`followchannel.status_slot_addr`)

# var -> (extraction mode, payload byte n, refresh class), from entry bytes +2 and +3 (§5.4a)
DM_RV_VARS = {162: (1, 2, 3), 163: (1, 3, 3), 164: (1, 4, 3), 235: (1, 1, 2), 277: (0, 0, 0)}


def _slot_offset(var: int) -> int:
    """The slot's offset from the base GuiCode_entry1 receives."""
    addr = (SLOT_BASE + var * 2) if var <= 255 else (HIGH_SLOT_BASE + var * 2)
    return addr - SLOT_BASE


def _guicode(path):
    d = open(path, "rb").read()
    sec = theme.guicode_section(d)
    return bytes(d[sec.offset:sec.offset + sec.length]), theme.guicode_base(sec)


def _wide_load_offsets(body: bytes, base: int) -> dict[int, list[int]]:
    """Every register-based immediate load GuiCode can use to read a slot at offset > 124, found
    by a byte scan at every halfword (so no disassembly desync can hide one), keyed on the byte
    offset it reads -> [sites]. PC-relative forms are excluded: they read literal pools.

    T1 narrow loads cannot reach any DM_RV slot (`ldr` imm5*4 <= 124, `ldrh` imm5*2 <= 62,
    `ldrb` imm5 <= 31), so the T2 imm12 forms and `ldrd` imm8*4 are the whole search space."""
    t2 = {0xF8D0, 0xF8B0, 0xF9B0, 0xF890, 0xF990}   # ldr.w ldrh.w ldrsh.w ldrb.w ldrsb.w
    out: dict[int, list[int]] = {}
    for off in range(0, len(body) - 4, 2):
        h1, h2 = struct.unpack_from("<HH", body, off)
        rn = h1 & 0xF
        if rn == 15:
            continue
        if (h1 & 0xFFF0) in t2:
            out.setdefault(h2 & 0xFFF, []).append(base + off)
        elif (h1 & 0xFE50) == 0xE850 and h1 & 0x0180 == 0x0180:     # ldrd, P=1 U=1
            out.setdefault((h2 & 0xFF) * 4, []).append(base + off)
    return out


def test_dm_rv_filter_entries_are_the_five_decoded():
    vt = guidata.variable_table(open(STOCK, "rb").read())
    got = {}
    for v in vt:
        if struct.unpack_from("<I", v.raw, 0)[0] & 0x3FFFF != DM_RV:
            continue
        assert v.raw[4] == 0xFF, "var %d has an instance filter" % v.index
        got[v.index] = (v.raw[2] >> 4, v.raw[3] & 7, (v.raw[2] >> 2) & 3)
    assert got == DM_RV_VARS


@pytest.mark.parametrize("path", [STOCK])
def test_no_guicode_load_reads_a_dm_rv_slot(path):
    """THE NEGATIVE. No GuiCode instruction reads any DM_RV slot, by base+offset or by absolute
    literal. The positive control is var 138, whose slot FUN_00247BF8 reads at 0x2484D2
    (`ldrsh.w r6,[r4,#0x114]`, `src/fireflymcf/data/slot_bridge_136.txt`): if the scan stops
    finding that, it has gone blind and the negative means nothing."""
    body, base = _guicode(path)
    loads = _wide_load_offsets(body, base)
    assert 0x2484D2 in loads.get(_slot_offset(138), []), "scan lost its positive control"
    for var in DM_RV_VARS:
        off = _slot_offset(var)
        assert not loads.get(off) and not loads.get(off + 1), (var, hex(off), loads.get(off))
    words = {w for (w,) in struct.iter_unpack("<I", body[:len(body) // 4 * 4])}
    for var in DM_RV_VARS:
        addr = SLOT_BASE + _slot_offset(var)
        assert not {addr, addr + 1} & words, "absolute literal for var %d's slot" % var


def _u32(body, base, vaddr):
    return struct.unpack_from("<I", body, vaddr - base)[0]


def test_solar_parser_dispatches_on_dm_rv():
    """FUN_00240D20 is a 16-DGN parser, not a 0x1FDFF-only one: its dispatch literals (compared
    against `canid & 0x03FFFF00`) run 0x241040..0x241080, and 0x24107C is DM_RV, compared at
    0x240E12 (`ldr r7,[pc]` ; `cmp r2,r7` ; `beq.w 0x241C8C`)."""
    body, base = _guicode(STOCK)
    dgns = [_u32(body, base, a) >> 8 for a in range(0x241040, 0x241084, 4)]
    assert dgns == [0x1FDFF, 0x1FEB3, 0x1FEB2, 0x1FEAF, 0x1FEAE, 0x1FE85, 0x1FE84, 0x1FE83,
                    0x1FE82, 0x1FE81, 0x1FE80, 0x1FDFE, 0x1FDFC, 0x1FDFA, 0x0EBFF, DM_RV,
                    0x1FED8]
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    ins = [(i.mnemonic, i.op_str) for i in md.disasm(body[0x240E12 - base:0x240E1A - base],
                                                      0x240E12)]
    assert ins == [("ldr", "r7, [pc, #0x268]"), ("cmp", "r2, r7"), ("beq.w", "#0x241c8c")]
    assert ((0x240E12 + 4) & ~3) + 0x268 == 0x24107C


def test_solar_dm_rv_branch_keys_on_adopted_addresses_reset_to_zero():
    """The branch matches the sender against five adopted solar-controller addresses; each is
    zeroed at reset (`movs r3,#0` at 0x2409BE, then `ldr r1,[pc]` ; `strb r3,[r1]` pairs at
    0x240A7E..0x240A90 with r3 never redefined) and is only written again by the solar DGN arms.
    So a node that sends no solar DGN cannot be matched."""
    body, base = _guicode(STOCK)
    table = [0x20039879, 0x20039870, 0x20039872, 0x20039871, 0x20039878]
    # the DM_RV branch's five address literals, one per controller
    assert [_u32(body, base, a) for a in (0x241D3C, 0x241D44, 0x241D48, 0x241D4C, 0x24216C)] \
        == table
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    run = list(md.disasm(body[0x2409BE - base:0x240A92 - base], 0x2409BE))
    assert (run[0].mnemonic, run[0].op_str) == ("movs", "r3, #0")
    for i in run[1:]:
        first = i.op_str.split(",")[0].strip()
        assert not (first == "r3" and not i.mnemonic.startswith(("str", "cmp", "tst"))), \
            "r3 redefined at %#x" % i.address
    zeroed = []
    for a, b in zip(run, run[1:]):
        if a.mnemonic == "ldr" and a.op_str.startswith("r1, [pc") and \
                (b.mnemonic, b.op_str) == ("strb", "r3, [r1]"):
            imm = int(a.op_str.split("#")[1].rstrip("]"), 16)
            zeroed.append(_u32(body, base, ((a.address + 4) & ~3) + imm))
    assert set(table) <= set(zeroed), [hex(z) for z in zeroed]


def test_frame_layout_anchor_payload0_at_plus4():
    """The frame struct GuiCode_entry4 hands its parsers holds payload[0] at +4: the PD parser's
    `payload[1] == 'B'` test, hardware-confirmed 2026-08-23 (`BUS-BASELINE.md` Result 7), is
    `ldrb r4,[r0,#5]` ; `cmp r4,#0x42` at 0x24F392. The solar branch's byte reads rest on it."""
    body, base = _guicode(STOCK)
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    ins = [(i.mnemonic, i.op_str) for i in md.disasm(body[0x24F392 - base:0x24F396 - base],
                                                      0x24F392)]
    assert ins == [("ldrb", "r4, [r0, #5]"), ("cmp", "r4, #0x42")]
