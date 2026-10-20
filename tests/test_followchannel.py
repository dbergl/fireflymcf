"""The light->pump follower: `followchannel.resolve` / `build` / `detour_edit`.

Task 2 authors NEW ARM Thumb machine code into dead space, so the discipline is the same as
`thumb.py`'s: every emitted instruction is disassembled back by capstone (`build` does this
internally via `thumb.assert_disassembles_to` and returns the verified disassembly), and every
derived address is checked against a value independently recovered from stock 13.6.

The blob shape (revised ruling 3): templates first (so the routine reaches them by absolute
address held in a literal pool), then the routine, then a 4-aligned literal pool of the u32
absolute addresses the routine's `ldr rT,[pc,#imm]` loads reference.
"""
from __future__ import annotations

import pytest

from fireflymcf import container, followchannel, thumb

capstone = pytest.importorskip("capstone")

# Blob layout constants (revised ruling 3 / task brief).
CODE_ADDR = 0x00259BB4
ENTRY = CODE_ADDR + 0x18
TAIL = 0x00247BF8

# The final blob bytes, frozen after the Task 3 ABI pin (sp 8-byte alignment at the blx). Any
# later refactor that drifts a single byte of hand-authored machine code that will be FLASHED
# fails here loudly. See test_blob_is_byte_stable.
EXPECTED_BLOB_HEX = (
    "00dbfe192cffc801ff00ffff00dbfe192cff0003ff00ffff"          # on + off templates (24 B)
    "0fb519480188194a1388994228d011804ff6ff708142"              # raw-slot latch; current-0xFFFF guard
    "23d01c200140042901d1012100e000210340042b01d1"              # firmware field decode: (slot&0x1C)==4
    "012300e00023994214d00e480078002810d0002901d00d4800e0"      # (0xFFFF-last decodes to OFF -> first
    "0d4885b00168426883680091019202936846064a1268904705b0"      #  toggle fires)
    "bde80f40edf7e2bf"                                          # ... transmit + tail-call
    "4e800020689f032096a203206c980320b49b2500c09b2500"          # pool (24 B): watch/latch/gate/sender/on/off
)


def test_send_primitive_shape_is_present_in_136(mcf_136):
    """Document the hardware oracle: the ldm/stm/blx send idiom the routine mimics really is at
    section-offset 0xA14E. If this ever fails, the send-site address moved — re-locate it before
    trusting the routine's shape; do not adjust the routine to a guessed address."""
    c = container.parse_container(mcf_136)
    gcs = next(s for s in c.sections if "GuiCode" in s.name)
    gc = mcf_136[gcs.offset:gcs.offset + gcs.length]
    window = gc[0xA14E:0xA14E + 0x14]
    texts = [t for _a, t in thumb.disassemble(window, 0x0024A14E)]
    joined = " | ".join(texts).lower()
    assert "ldm" in joined and "blx" in joined     # block-copy the frame, then indirect-call
    # And the sender takes the frame pointer in r0 (`mov r0, r3` feeds the `blx`): the routine's
    # `mov r0, sp` matches this.
    assert any("mov r0," in t for t in texts)


def test_blob_is_byte_stable(mcf_136):
    """Freeze the FINAL blob bytes (post ABI pin). This is hand-authored ARM Thumb that will be
    FLASHED; a single silent byte drift here is a correctness bug, so pin it."""
    plan = followchannel.resolve(mcf_136, 25, 44, 200, "Heat Pump")
    blob, _ = followchannel.build(plan, CODE_ADDR)
    assert blob.hex() == EXPECTED_BLOB_HEX
    assert len(blob) == len(bytes.fromhex(EXPECTED_BLOB_HEX)) == 152
    assert len(blob) % 4 == 0


def test_sp_is_8_byte_aligned_at_the_blx(mcf_136):
    """AAPCS requires sp 8-byte-aligned at a call. Decode the routine and confirm the total sp
    decrement from entry to the `blx` (push reg-count*4 + sub_sp immediates, less any add_sp) is
    a multiple of 8. This pins the Task 3 fix so a future edit cannot silently re-misalign the
    frame the CAN sender is called on."""
    plan = followchannel.resolve(mcf_136, 25, 44, 200, "Heat Pump")
    _blob, disasm = followchannel.build(plan, CODE_ADDR)

    decrement = 0
    reached_blx = False
    for _addr, text in disasm:
        if text.startswith("push"):
            regs = text[text.index("{") + 1:text.index("}")].split(",")
            decrement += 4 * len([r for r in regs if r.strip()])
        elif text.startswith("sub sp"):
            decrement += int(text.rsplit("#", 1)[1], 0)
        elif text.startswith("add sp"):
            decrement -= int(text.rsplit("#", 1)[1], 0)
        elif text.startswith("blx"):
            reached_blx = True
            break
    assert reached_blx, "routine has no blx — the send site is gone"
    assert decrement == 40, f"sp decrement at blx changed: {decrement}"
    assert decrement % 8 == 0, f"sp is {decrement % 8}-mod-8 (misaligned) at the blx"


def test_resolve_derives_known_136_addresses(mcf_136):
    plan = followchannel.resolve(mcf_136, watch_channel=25, target_channel=44,
                                 on_level=200, gate_option="Heat Pump")
    # Receive-filter slot for status var 67 (ch25): STATUS_SLOT_BASE 0x20007FC8 + 67*2 (§5.4c).
    # NOT the variable-pointer cell 0x20039E4A -- the v1 bug the coach exposed (never written by
    # the receive filter, so the follower never saw the light change).
    assert plan.watch_addr == 0x2000804E
    # Firmware-faithful on-decode: the light is on iff (slot & on_mask) == on_value. ch25's own
    # indicator block (FUN_00247BF8 @0x248afc) does ubfx #2,#3 then cmp #1, i.e. operating-status
    # field (bits 2-4, mask 0x1C) == 1, which is 0x04 in the raw byte. NOT a bare `& 0x04` bit test.
    assert plan.on_mask == 0x1C
    assert plan.on_value == 0x04
    # The latch stores the raw slot halfword, so its cell must be 2-aligned.
    assert plan.latch_addr == 0x20039F68
    assert plan.latch_addr % 2 == 0
    assert plan.gate_addr == 0x2003A296
    assert plan.hook_at == 0xEE3C            # offset within the GuiCode section
    assert plan.hook_old == bytes.fromhex("f8f7dcfe")
    assert plan.on_template == bytes.fromhex("00dbfe192cffc801ff00ffff")
    assert plan.off_template == bytes.fromhex("00dbfe192cff0003ff00ffff")


def test_resolve_skips_the_guicode_live_spare_cell(mcf_136):
    """The latch must NOT be a spare cell that GuiCode already uses as working memory.

    Cell 203's SRAM address 0x2003A2F8 is *widget*-spare (unused by any record) yet firmware-live:
    stock FUN_00247BF8 — the per-tick function we hook — loads it at 0x24a600. `resolve` must skip
    it and pick a spare that GuiCode references nowhere. This encodes the safety defect the latch
    fix closes: pick by CODE-unreferenced, not merely WIDGET-unreferenced."""
    from fireflymcf import guidata, statusbind, theme

    plan = followchannel.resolve(mcf_136, 25, 44, 200, "Heat Pump")
    assert plan.latch_addr != 0x2003A2F8, "latch collides with GuiCode-live cell 203"

    sec = theme.guicode_section(mcf_136)
    gc = mcf_136[sec.offset:sec.offset + sec.length]
    base = theme.guicode_base(sec)
    # 0x2003A2F8 really is referenced (so skipping it is load-bearing, not cosmetic)...
    assert followchannel._guicode_references(gc, base, 0x2003A2F8)
    # ...and the cell resolve actually chose is referenced nowhere in GuiCode.
    assert not followchannel._guicode_references(gc, base, plan.latch_addr)

    # And the chosen address is a genuine spare +0x44BC8 cell in the table.
    gs, _ = guidata.guidata_extent(mcf_136)
    import struct
    spare_addrs = {
        struct.unpack_from("<I", mcf_136, gs + statusbind.CELL_TABLE_OFF + i * 4)[0]
        for i in statusbind.spare_cell_indices(mcf_136)
    }
    assert plan.latch_addr in spare_addrs


def test_gate_addr_is_a_plausible_sram_register(mcf_136):
    plan = followchannel.resolve(mcf_136, 25, 44, 200, "Heat Pump")
    assert 0x20030000 <= plan.gate_addr < 0x20040000


def test_resolve_refuses_a_corrupted_hook(mcf_136):
    """A cross-coach safety rail: if the byte we detour is not the stock `bl`, refuse."""
    from fireflymcf import theme
    sec = theme.guicode_section(mcf_136)
    tampered = bytearray(mcf_136)
    tampered[sec.offset + 0xEE3C] ^= 0xFF     # break the stock hook
    with pytest.raises(followchannel.FollowChannelError):
        followchannel.resolve(bytes(tampered), 25, 44, 200, "Heat Pump")


def test_resolve_refuses_an_unknown_option(mcf_136):
    with pytest.raises(followchannel.FollowChannelError):
        followchannel.resolve(mcf_136, 25, 44, 200, "No Such Option")


def test_resolve_gate_slot_selector_uses_receive_slot(mcf_136):
    plan = followchannel.resolve(
        mcf_136, watch_channel=25, target_channel=44, on_level=200,
        gate_option=None, gate_slot_selector=0xEE)
    assert plan.gate_is_slot is True
    # 0xEE is variable-table entry 289 (> 255), so the receive filter stores it in the HIGH
    # slot array: 0x20008000 + 289*2 = 0x20008242 (index-split base, FUN_0021C760). The old flat
    # 0x20007FC8 + 289*2 = 0x2000820A was the PC.44 bug -- 56 bytes into unwritten space -- which
    # this test wrongly encoded until the create-a-register spike found the split.
    assert plan.gate_addr == 0x20008242
    assert plan.gate_on_value == 1


def test_status_slot_addr_is_index_split():
    # low index: 0x20007FC8 + idx*2; high index (> 0xFF): 0x20008000 + idx*2
    assert followchannel.status_slot_addr(67) == 0x2000804E    # watched light, low
    assert followchannel.status_slot_addr(255) == 0x200081C6   # last low
    assert followchannel.status_slot_addr(256) == 0x20008200   # first high
    assert followchannel.status_slot_addr(289) == 0x20008242   # 0xEE gate, high


def test_resolve_rejects_both_gate_sources(mcf_136):
    with pytest.raises(followchannel.FollowChannelError):
        followchannel.resolve(mcf_136, 25, 44, 200,
                              gate_option="Heat Pump", gate_slot_selector=0xEE)


def test_resolve_gate_slot_unknown_selector_refuses(mcf_136):
    with pytest.raises(followchannel.FollowChannelError):
        followchannel.resolve(mcf_136, 25, 44, 200,
                              gate_option=None, gate_slot_selector=0x77)  # no DGN_CONFIG var


def test_resolve_allocates_display_and_carries_group(mcf_136):
    plan = followchannel.resolve(mcf_136, 25, 44, 200, gate_option=None,
                                 gate_slot_selector=0xEE, variant_group=233,
                                 alloc_display=True)
    assert plan.display_addr and plan.display_slot is not None
    assert plan.display_addr % 2 == 0 and plan.display_addr != plan.latch_addr
    assert plan.variant_group == 233


def test_build_bridge_block_sets_variant_and_display(mcf_136):
    plan = followchannel.resolve(mcf_136, 25, 44, 200, gate_option=None,
                                 gate_slot_selector=0xEE, variant_group=233,
                                 alloc_display=True)
    blob, disasm = followchannel.build(plan, 0x00259BB4)
    texts = [t for _, t in disasm]
    # writes the display cell as a byte, loads the setter, calls it with r0=#233
    assert any(t.startswith("strb") for t in texts)
    assert "movs r0, #0xe9" in texts        # 233 == 0xE9, the new group id
    assert texts.count("blx r2") >= 2         # setVariant + CAN sender


def test_build_gate_is_slot_reads_halfword(mcf_136):
    plan = followchannel.resolve(mcf_136, 25, 44, 200,
                                 gate_option=None, gate_slot_selector=0xEE)
    blob, disasm = followchannel.build(plan, CODE_ADDR)
    texts = [t for _, t in disasm]
    # the gate loads the slot as a halfword and tests == 1
    assert any(t.startswith("ldrh r0, [r0]") for t in texts)
    assert "cmp r0, #1" in texts


def test_build_disassembles_as_authored(mcf_136):
    plan = followchannel.resolve(mcf_136, 25, 44, 200, "Heat Pump")
    blob, disasm = followchannel.build(plan, CODE_ADDR)
    assert len(blob) % 4 == 0
    # templates live at the front, in wire order, reachable by absolute address.
    assert blob[0x00:0x0C] == plan.on_template
    assert blob[0x0C:0x18] == plan.off_template
    # build() runs thumb.assert_disassembles_to on the routine; a non-empty disassembly
    # proves it did not silently skip an instruction.
    assert disasm, "build must return its verified disassembly"
    texts = [t for _a, t in disasm]
    # the tail-call contract (ruling 4): restore r0-r3 and lr, then branch to the per-tick fn.
    assert any(t == "push {r0, r1, r2, r3, lr}" for t in texts)
    assert any(t == "pop.w {r0, r1, r2, r3, lr}" for t in texts)
    assert any("0x247bf8" in t.replace(" ", "").lower() for t in texts)
    # the routine must not `pop {pc}` (ruling 4).
    assert not any("pc" in t and t.startswith("pop") for t in texts)


def test_build_routine_starts_at_entry_offset(mcf_136):
    plan = followchannel.resolve(mcf_136, 25, 44, 200, "Heat Pump")
    _blob, disasm = followchannel.build(plan, CODE_ADDR)
    assert disasm[0][0] == ENTRY            # first instruction sits at code_load_addr + 0x18


def test_detour_retargets_the_single_caller(mcf_136):
    plan = followchannel.resolve(mcf_136, 25, 44, 200, "Heat Pump")
    off, old, new = followchannel.detour_edit(plan, CODE_ADDR)
    assert off == 0xEE3C
    assert old == bytes.fromhex("f8f7dcfe")
    # new bytes decode to a bl to the routine entry (code_addr + 0x18).
    (_, text), = thumb.disassemble(new, 0x0024EE3C)
    assert "bl" in text and hex(ENTRY)[2:] in text.replace(" ", "").lower()


def scan_sram_refs(section: bytes, base: int, addr: int) -> list:
    """Every reference to `addr` in a GuiCode `section` mapped at `base`.

    Two forms, per the standing scan rule (13.6 has no movw/movt, so a 32-bit constant can only
    reach code as a literal-pool word):
      * a raw little-endian u32 occurrence of `addr` (a pool word or a bare table pointer), and
      * a T1 `ldr rX,[pc,#imm8]` (halfword 0x4800..0x4FFF) whose PC-relative target word == `addr`.
    Both 2-byte alignments are scanned. Returns section-relative offsets of each hit."""
    import struct as _struct

    want = _struct.pack("<I", addr)
    hits = []
    start = 0
    while True:
        i = section.find(want, start)
        if i == -1:
            break
        hits.append(("u32", i))
        start = i + 1
    n = len(section)
    for pos in range(0, n - 1):
        instr = section[pos] | (section[pos + 1] << 8)
        if 0x4800 <= instr <= 0x4FFF:
            imm8 = instr & 0xFF
            target = ((base + pos + 4) & ~3) + imm8 * 4
            off = target - base
            if 0 <= off + 4 <= n and _struct.unpack_from("<I", section, off)[0] == addr:
                hits.append(("ldr", pos))
    return hits


def test_plan_from_registers_matches_the_channel_and_selector_slots(mcf_136):
    from fireflymcf import register
    watch = followchannel.Condition(
        register.resolve_register(mcf_136, dgn="DC_DIMMER_STATUS_3", instance=25),
        mask=0x1C, value=0x04)
    gate = followchannel.Condition(
        register.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=0xEE), value=1)
    plan = followchannel.plan_from_registers(
        mcf_136, watch=watch, target_channel=44, on_level=200, gates=[gate])
    assert plan.watch_addr == 0x2000804E
    assert plan.on_mask == 0x1C and plan.on_value == 0x04
    assert [g.register.slot_addr for g in plan.gates] == [0x20008242]
    assert plan.display_addr is None and plan.variant_group is None   # no display path
    assert plan.on_template == followchannel._template(44, 200, 1)


def test_plan_from_registers_refuses_a_wide_watch_mask(mcf_136):
    """on_mask is a byte field in the routine (`movs r0,#imm8`); a >0xFF watch mask must be
    refused, not silently truncated to its low byte (fix round 1: was corrupt-not-refuse)."""
    from fireflymcf import register
    watch = followchannel.Condition(
        register.resolve_register(mcf_136, dgn="DC_DIMMER_STATUS_3", instance=25),
        mask=0x11C, value=0x04)
    with pytest.raises(followchannel.FollowChannelError, match="byte"):
        followchannel.plan_from_registers(
            mcf_136, watch=watch, target_channel=44, on_level=200, gates=[])


def test_plan_from_registers_refuses_a_corrupted_hook(mcf_136):
    from fireflymcf import register, theme
    tampered = bytearray(mcf_136)
    sec = theme.guicode_section(mcf_136)
    tampered[sec.offset + followchannel.HOOK_OFF] ^= 0xFF
    watch = followchannel.Condition(
        register.resolve_register(bytes(tampered), dgn="DC_DIMMER_STATUS_3", instance=25),
        mask=0x1C, value=0x04)
    with pytest.raises(followchannel.FollowChannelError, match="hook"):
        followchannel.plan_from_registers(bytes(tampered), watch=watch, target_channel=44,
                                          on_level=200, gates=[])


def test_latch_cell_is_referenced_by_nothing_but_our_routine(mcf_136):
    """The spare cell we latch into must not be read/written by any stock GuiCode.

    Scan stock GuiCode for the CHOSEN latch address (pool literals + T1 ldr-literals; 13.6 has no
    movw/movt so no other direct form exists). Stock must have ZERO references. As a documenting
    guard that the selection is load-bearing (not cosmetic), also assert that cell 203's address
    0x2003A2F8 — which `resolve` deliberately skips — IS referenced by stock GuiCode."""
    from fireflymcf import statusbind, theme

    sec = theme.guicode_section(mcf_136)
    gc = mcf_136[sec.offset:sec.offset + sec.length]
    base = theme.guicode_base(sec)

    latch = followchannel.resolve(mcf_136, 25, 44, 200, "Heat Pump").latch_addr
    hits = scan_sram_refs(gc, base, latch)
    assert hits == [], f"chosen latch {latch:#x} already used by stock GuiCode at {hits}"

    # WHY resolve must skip cell 203: its address is referenced by stock GuiCode.
    import struct
    from fireflymcf import guidata
    gs = guidata.guidata_extent(mcf_136)[0]
    addr_203 = struct.unpack_from("<I", mcf_136, gs + statusbind.CELL_TABLE_OFF + 203 * 4)[0]
    assert addr_203 == 0x2003A2F8
    assert scan_sram_refs(gc, base, addr_203), "expected cell 203 (0x2003A2F8) to be GuiCode-live"


def test_build_empty_gates_is_byte_identical_to_single_gate(mcf_136):
    # The proven single-gate slot path and an equivalent one-gate `gates` plan should each
    # round-trip; the point of the empty-gates branch is that the OLD code path is untouched.
    plan = followchannel.resolve(mcf_136, 25, 44, 200, gate_slot_selector=0xEE)
    blob_old, _ = followchannel.build(plan, CODE_ADDR)          # gates == [] -> old branch
    assert plan.gates == []
    assert isinstance(blob_old, bytes) and len(blob_old) % 4 == 0


def test_build_multi_gate_emits_a_test_per_condition(mcf_136):
    from fireflymcf import register
    watch = followchannel.Condition(
        register.resolve_register(mcf_136, dgn="DC_DIMMER_STATUS_3", instance=25),
        mask=0x1C, value=0x04)
    g1 = followchannel.Condition(
        register.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=0xEE), value=1)
    g2 = followchannel.Condition(
        register.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=0xE6), value=1)
    plan = followchannel.plan_from_registers(
        mcf_136, watch=watch, target_channel=44, gates=[g1, g2])
    blob, disasm = followchannel.build(plan, CODE_ADDR)         # asserts round-trip internally
    text = "\n".join(t for _a, t in disasm)
    # one ldrh + cmp per gate condition
    assert text.count("ldrh r0, [r0]") >= 2


def test_build_multi_gate_never_clobbers_r1_the_live_state(mcf_136):
    """The gate section runs AFTER r1 has been set to the new on/off state that the
    template-selection code (`cmp r1, #0`) reads. A masked gate (mask != 0xFFFF) or a wide-value
    gate (value > 0xFF) must materialise its mask/value into a DEAD scratch (r2), never r1 --
    otherwise the routine transmits the wrong frame. assert_disassembles_to is blind to register
    liveness, so this asserts the register discipline directly on the gate section."""
    from fireflymcf import register
    watch = followchannel.Condition(
        register.resolve_register(mcf_136, dgn="DC_DIMMER_STATUS_3", instance=25),
        mask=0x1C, value=0x04)
    masked = followchannel.Condition(                       # exercises the ands (mask) branch
        register.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=0xEE),
        mask=0x1C, value=0x04)
    wide = followchannel.Condition(                         # exercises the movw+cmp_reg branch
        register.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=0xE6),
        mask=0xFFFF, value=0x0123)
    plan = followchannel.plan_from_registers(
        mcf_136, watch=watch, target_channel=44, gates=[masked, wide])
    _blob, disasm = followchannel.build(plan, CODE_ADDR)    # asserts round-trip internally

    # Delimit the gate section: it starts at the first `ldrh r0, [r0]` (each gate's slot load) and
    # ends just before the template-selection `cmp r1, #0`.
    texts = [t for _a, t in disasm]
    start = next(i for i, t in enumerate(texts) if t == "ldrh r0, [r0]")
    end = next(i for i, t in enumerate(texts) if t == "cmp r1, #0")
    gate_section = texts[start:end]
    assert gate_section, "gate section is empty -- delimiters wrong"

    # The masked and wide-value branches must be present (guard against a future refactor that
    # silently stops exercising them and makes the r1 assertion vacuous).
    assert any(t.startswith("movs r2,") or t.startswith("movw r2,") for t in gate_section)
    assert any(t == "ands r0, r2" for t in gate_section)
    assert any(t == "cmp r0, r2" for t in gate_section)

    # No instruction in the gate section may WRITE r1. Every write form the loop could emit names
    # its destination first (movs/movw/ands/ldr...), so a leading "<op> r1," is the signature.
    import re
    offenders = [t for t in gate_section if re.match(r"\w+(\.\w+)?\s+r1\b", t)]
    assert offenders == [], f"gate section writes live r1: {offenders}"


def test_build_register_path_empty_gates_emits_no_gate_deref(mcf_136):
    """FINDING 1: a `plan_from_registers(..., gates=[])` is a legal pure edge-follower (no gate).
    It must route through the multi-gate section (which over an empty list emits NO gate test --
    "always transmit on edge"), NOT the legacy single-gate branch, which would author a read of
    `plan.gate_addr` (== 0 on the register path) -- a runtime dereference of address 0x0.

    So: the built routine must contain NO gate load of any form (`ldrb r0, [r0]` / `ldrh r0, [r0]`),
    and the region between the on-state decode and the template-select `cmp r1, #0` must hold only
    the one `beq` transition guard -- no gate emission at all -- then proceed to transmit."""
    from fireflymcf import register
    watch = followchannel.Condition(
        register.resolve_register(mcf_136, dgn="DC_DIMMER_STATUS_3", instance=25),
        mask=0x1C, value=0x04)
    plan = followchannel.plan_from_registers(
        mcf_136, watch=watch, target_channel=44, on_level=200, gates=[])
    assert plan.gates == [] and plan.use_gate_list is True
    blob, disasm = followchannel.build(plan, CODE_ADDR)     # asserts round-trip internally
    assert isinstance(blob, bytes) and len(blob) % 4 == 0
    texts = [t for _a, t in disasm]

    # No gate dereference at all -- the legacy branch would read the gate slot with ldrb/ldrh r0.
    assert "ldrb r0, [r0]" not in texts, texts
    assert "ldrh r0, [r0]" not in texts, texts

    # The region between the on-state decode (ends at the on(cur) vs on(last) `cmp r1, r3`) and the
    # template-select `cmp r1, #0` must hold ONLY the transition-guard `beq done` -- no gate test.
    tsel = texts.index("cmp r1, #0")
    on_decode_end = max(i for i, t in enumerate(texts) if t == "cmp r1, r3")
    between = texts[on_decode_end + 1: tsel]
    assert len(between) == 1 and between[0].startswith("beq"), between

    # And it proceeds to the template-select / transmit / tail-call path.
    assert "blx r2" in texts and any(t.startswith("b.w") for t in texts)


def test_build_multi_gate_wide_mask_movw_round_trips(mcf_136):
    """FINDING 3: a gate whose mask > 0xFF (and != 0xFFFF) materialises the mask with
    `movw r2, #mask`. No committed build exercised that branch, so assert_disassembles_to never
    round-tripped it. A gate with mask=0x1FF (value small) does."""
    from fireflymcf import register
    watch = followchannel.Condition(
        register.resolve_register(mcf_136, dgn="DC_DIMMER_STATUS_3", instance=25),
        mask=0x1C, value=0x04)
    wide_mask = followchannel.Condition(                    # exercises the movw r2,#mask branch
        register.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=0xEE),
        mask=0x1FF, value=1)
    plan = followchannel.plan_from_registers(
        mcf_136, watch=watch, target_channel=44, gates=[wide_mask])
    _blob, disasm = followchannel.build(plan, CODE_ADDR)    # asserts round-trip internally
    texts = [t for _a, t in disasm]
    assert "movw r2, #0x1ff" in texts, texts
    assert "ands r0, r2" in texts, texts


# --- Task 13: the MASKED gate -- `(slot & mask) == value` on a shared selector word ----------
#
# The 0xEE selector now carries TWO fields in one 16-bit word (auto-off minutes in the low byte,
# the Pump Link flag in the high byte -- 2026-09-03 pump-auto-off design §2.2/§3.2). The follower
# gate and the display bridge used to test the whole halfword `== 1`, which the widened word can
# never be, so the follower went dead and the button stopped highlighting. The fix is the masked
# form `(slot & 0xFF00) == 0x0100`, opt-in via `gate_mask`, with the unmasked default emitting
# byte-identical code (`tests/test_registers.py` pins builds that are on a physical vehicle).

# Frozen BEFORE the masked-gate change (captured from the pre-Task-13 tree). These are the three
# unmasked emissions the mask must not disturb by one byte.
UNMASKED_BRIDGE_BLOB_HEX = (
    "00dbfe192cffc801ff00ffff00dbfe192cff0003ff00ffff"
    "0fb524480088244a012804d1012182f80010002103e0002182f8001001211f4a1268e920904719480188194a13"
    "88994228d011804ff6ff70814223d01c200140042901d1012100e000210340042b01d1012300e00023994214d0"
    "0f480078002810d0002901d00f4800e00f4885b00168426883680091019202936846084a1268904705b0bde80f"
    "40edf7d0bf"
    "4e800020689f032042820020609f0320089903206c980320b49b2500c09b2500"
)
UNMASKED_SLOT_GATE_BLOB_HEX = (
    "00dbfe192cffc801ff00ffff00dbfe192cff0003ff00ffff"
    "0fb519480188194a1388994228d011804ff6ff70814223d01c200140042901d1012100e000210340042b01d101"
    "2300e00023994214d00e480088012810d1002901d00d4800e00d4885b00168426883680091019202936846064a"
    "1268904705b0bde80f40edf7e2bf"
    "4e800020689f0320428200206c980320b49b2500c09b2500"
)
UNMASKED_DISPLAY_BRIDGE_BLOB_HEX = (
    "0fb510480088012801d0002100e001210e4a82f80010002906d00b4b1b680021812098470a4a05e0074b1b6801"
    "2181209847084a1168084a1160bde80f40eef701b8"
    "00001c80002008990320799a0320a0a10320589a032014a80320"
)

FLAG_MASK = 0xFF00          # the Pump Link flag lives in the high byte of selector 0xEE
FLAG_ON = 0x0100            # ... and "linked" is that byte == 1


def _bridge_plan(mcf, **kw):
    return followchannel.resolve(mcf, 25, 44, 200, gate_slot_selector=0xEE,
                                 variant_group=233, alloc_display=True, **kw)


def test_unmasked_follower_bridge_blob_is_byte_identical(mcf_136):
    """The default (unmasked) bridge path must emit the pre-Task-13 bytes EXACTLY -- these are on
    the vehicle (test_registers.py pins the builds that contain them)."""
    blob, _ = followchannel.build(_bridge_plan(mcf_136), CODE_ADDR)
    assert blob.hex() == UNMASKED_BRIDGE_BLOB_HEX
    plan = _bridge_plan(mcf_136)
    assert plan.gate_mask == 0xFFFF, "the mask must default to the full halfword (today's test)"


def test_unmasked_slot_gate_blob_is_byte_identical(mcf_136):
    """Same, for the slot-gated follower with no display bridge."""
    plan = followchannel.resolve(mcf_136, 25, 44, 200, gate_slot_selector=0xEE)
    blob, _ = followchannel.build(plan, CODE_ADDR)
    assert blob.hex() == UNMASKED_SLOT_GATE_BLOB_HEX


def test_unmasked_display_bridge_blob_is_byte_identical(mcf_136):
    """Same, for the standalone Low Volts display bridge (selector 0x2F, hardware-verified)."""
    from fireflymcf import register
    reg = register.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=0x2F)
    plan = followchannel.resolve_display_bridge(mcf_136, gate_addr=reg.slot_addr)
    assert plan.gate_mask == 0xFFFF
    blob, _ = followchannel.build_display_bridge(plan, CODE_ADDR)
    assert blob.hex() == UNMASKED_DISPLAY_BRIDGE_BLOB_HEX


def test_masked_gate_bridge_tests_the_flag_byte(mcf_136):
    """`gate_mask=0xFF00, gate_on_value=0x0100` makes the follower's per-tick bridge test
    `(slot & 0xFF00) == 0x0100` instead of `slot == 1`. Neither literal fits the 8-bit cmp
    immediate, so the mask is materialised with `movw` (the encoding the multi-gate branch
    already uses) -- and `build` round-trips every byte of it through capstone."""
    plan = _bridge_plan(mcf_136, gate_mask=FLAG_MASK, gate_on_value=FLAG_ON)
    assert plan.gate_mask == FLAG_MASK and plan.gate_on_value == FLAG_ON
    _blob, disasm = followchannel.build(plan, CODE_ADDR)     # asserts round-trip internally
    texts = [t for _a, t in disasm]
    i = texts.index("ldrh r0, [r0]")                         # the bridge's gate-slot load
    window = texts[i:i + 8]
    assert "movw r1, #0xff00" in window, window
    assert "ands r0, r1" in window, window
    assert "movw r1, #0x100" in window, window
    assert "cmp r0, r1" in window, window
    # ... and the old whole-halfword compare is gone (it is the defect: the widened word is
    # never 1, so the button would never highlight).
    assert "cmp r0, #1" not in texts, texts


def test_masked_gate_bridge_keeps_the_display_pointer_in_r2(mcf_136):
    """Register discipline: the bridge's mask/value scratch is r1 (dead until the arms set the
    flag), NEVER r2 -- r2 holds the display cell pointer that both arms `strb.w` through, and
    r4-r7 are untouchable. assert_disassembles_to is blind to liveness, so assert it directly."""
    import re
    plan = _bridge_plan(mcf_136, gate_mask=FLAG_MASK, gate_on_value=FLAG_ON)
    _blob, disasm = followchannel.build(plan, CODE_ADDR)
    texts = [t for _a, t in disasm]
    start = texts.index("ldrh r0, [r0]")
    end = texts.index("strb.w r1, [r2]")                     # the first arm's display write
    section = texts[start:end]
    assert not any(re.match(r"\w+(\.\w+)?\s+r2\b", t) for t in section[1:]
                   if not t.startswith("ldr r2, [pc")), section
    assert not any(re.search(r"\br[4-7]\b", t) for t in texts), texts


def test_masked_slot_gate_without_bridge_uses_r2_and_spares_live_r1(mcf_136):
    """The no-bridge slot gate runs after r1 holds the new on/off state the template select
    reads, so ITS mask/value scratch must be r2 (the multi-gate branch's discipline)."""
    import re
    plan = followchannel.resolve(mcf_136, 25, 44, 200, gate_slot_selector=0xEE,
                                 gate_mask=FLAG_MASK, gate_on_value=FLAG_ON)
    _blob, disasm = followchannel.build(plan, CODE_ADDR)     # asserts round-trip internally
    texts = [t for _a, t in disasm]
    start = texts.index("ldrh r0, [r0]")
    end = texts.index("cmp r1, #0")                          # template selection
    section = texts[start:end]
    assert "movw r2, #0xff00" in section and "ands r0, r2" in section, section
    assert "movw r2, #0x100" in section and "cmp r0, r2" in section, section
    offenders = [t for t in section if re.match(r"\w+(\.\w+)?\s+r1\b", t)]
    assert offenders == [], f"masked gate writes live r1: {offenders}"


def test_masked_display_bridge_tests_the_flag_byte(mcf_136):
    """The standalone display bridge takes the same masked form (spec §3.2: BOTH the follower
    gate and the display bridge become masked tests)."""
    import re
    from fireflymcf import register
    reg = register.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=0xEE)
    plan = followchannel.resolve_display_bridge(mcf_136, gate_addr=reg.slot_addr,
                                                gate_mask=FLAG_MASK, gate_on_value=FLAG_ON)
    _blob, disasm = followchannel.build_display_bridge(plan, CODE_ADDR)   # round-trips internally
    texts = [t for _a, t in disasm]
    start = texts.index("ldrh r0, [r0]")
    stop = next(i for i, t in enumerate(texts[start:], start) if t.startswith("beq"))
    section = texts[start:stop]
    assert "movw r2, #0xff00" in section and "ands r0, r2" in section, section
    assert "movw r2, #0x100" in section and "cmp r0, r2" in section, section
    assert "cmp r0, #1" not in texts, texts
    # the flag register r1 is written only by the two arms, after the compare
    assert not any(re.match(r"\w+(\.\w+)?\s+r1\b", t) for t in section), section
    assert not any(re.search(r"\br[4-7]\b", t) for t in texts), texts


def test_masked_gate_refuses_a_value_outside_its_mask(mcf_136):
    """`(slot & 0xFF00) == 0x0001` can never hold: refuse rather than author dead code."""
    with pytest.raises(followchannel.FollowChannelError):
        _bridge_plan(mcf_136, gate_mask=FLAG_MASK, gate_on_value=1)
    with pytest.raises(followchannel.FollowChannelError):
        followchannel.resolve_display_bridge(mcf_136, gate_addr=0x20008242,
                                             gate_mask=FLAG_MASK, gate_on_value=1)


def test_masked_gate_refuses_a_test_the_unset_sentinel_satisfies(mcf_136):
    """A receive slot reads 0xFFFF until the G12 first sweeps it. A mask/value pair the sentinel
    satisfies (e.g. `(slot & 0xFF00) == 0xFF00`) would read "on" at boot -- the same trap the
    unmasked path refuses with `gate_on_value must not be 0xFFFF`."""
    with pytest.raises(followchannel.FollowChannelError):
        _bridge_plan(mcf_136, gate_mask=FLAG_MASK, gate_on_value=0xFF00)
    with pytest.raises(followchannel.FollowChannelError):
        followchannel.resolve_display_bridge(mcf_136, gate_addr=0x20008242,
                                             gate_mask=FLAG_MASK, gate_on_value=0xFF00)


def test_masked_gate_refuses_an_empty_mask(mcf_136):
    with pytest.raises(followchannel.FollowChannelError):
        _bridge_plan(mcf_136, gate_mask=0, gate_on_value=0)


def test_gate_mask_is_refused_on_the_register_path(mcf_136):
    """The register path reads a config REGISTER as a byte and tests it nonzero -- there is no
    halfword to mask, so a `gate_mask` there would be silently meaningless. Refuse it."""
    with pytest.raises(followchannel.FollowChannelError):
        followchannel.resolve(mcf_136, 25, 44, 200, "Heat Pump", gate_mask=FLAG_MASK)


# --- Task 13, fix round 1 ---------------------------------------------------------------------

# Frozen from the PRE-Task-13 tree (`git show HEAD~:src/fireflymcf/followchannel.py`), for a
# plan whose three conditions cover the three emitter branches the sha pins do NOT reach:
# a movs-mask (0x1C), a movw-mask (0x1FF) and a movw + cmp_reg wide value (0x123). Finding 2:
# without this, two thirds of what the shared emitter refactor touched had only disassembly-text
# assertions behind it. Every byte of this blob predates the refactor.
UNMASKED_MULTI_GATE_BLOB_HEX = (
    "00dbfe192cffc801ff00ffff00dbfe192cff0003ff00ffff0fb521480188214a1388994237d011804ff6ff7081"
    "4232d01c200140042901d1012100e000210340042b01d1012300e00023994223d0164800881c22104004281dd1"
    "1448008840f2ff121040012816d11148008840f22312904210d1002901d00f4800e00f4885b001684268836800"
    "91019202936846094a1268904705b0bde80f40edf7d3bf00004e800020689f032042820020348200201c800020"
    "6c980320b49b2500c09b2500"
)


def _three_branch_gates(mcf):
    from fireflymcf import register
    watch = followchannel.Condition(
        register.resolve_register(mcf, dgn="DC_DIMMER_STATUS_3", instance=25),
        mask=0x1C, value=0x04)
    narrow = followchannel.Condition(                    # movs-mask branch
        register.resolve_register(mcf, dgn="DGN_CONFIG", selector=0xEE), mask=0x1C, value=0x04)
    wide = followchannel.Condition(                      # movw-mask branch
        register.resolve_register(mcf, dgn="DGN_CONFIG", selector=0xE6), mask=0x1FF, value=1)
    val = followchannel.Condition(                       # movw + cmp_reg wide-value branch
        register.resolve_register(mcf, dgn="DGN_CONFIG", selector=0x2F), mask=0xFFFF, value=0x0123)
    return watch, [narrow, wide, val]


def test_unmasked_multi_gate_blob_is_byte_identical(mcf_136):
    """FINDING 2: the sha256 pins in tests/test_registers.py only ever exercise ONE of the four
    branches of the shared emitter (`mask == 0xFFFF, value <= 0xFF` -> a single `cmp r0,#imm8`),
    because the reference coach's `golden-v22-anchor` recipe carries no `follow_register` op. Pin the other three
    against bytes captured from the pre-Task-13 tree, so the refactor's whole surface has durable
    byte cover and not just disassembly-text assertions."""
    watch, gates = _three_branch_gates(mcf_136)
    plan = followchannel.plan_from_registers(
        mcf_136, watch=watch, target_channel=44, gates=gates)
    blob, disasm = followchannel.build(plan, CODE_ADDR)   # round-trips every byte internally
    assert blob.hex() == UNMASKED_MULTI_GATE_BLOB_HEX
    texts = [t for _a, t in disasm]
    # ... and all four emitter branches really are in there (a pin over code that stopped
    # exercising a branch would be vacuous).
    assert "movs r2, #0x1c" in texts and "cmp r0, #4" in texts        # movs-mask + imm8 value
    assert "movw r2, #0x1ff" in texts                                 # movw-mask
    assert "movw r2, #0x123" in texts and "cmp r0, r2" in texts       # movw + cmp_reg value
    assert texts.count("ands r0, r2") == 2


def test_condition_gates_refuse_a_permanently_dead_test(mcf_136):
    """FINDING 1: `Condition(mask=0xFF00, value=1)` can never hold -- the same defect class this
    task exists to eliminate -- and the Condition path used to accept it with only u16-range
    guards. It must now be refused at PLAN time, not compiled green."""
    from fireflymcf import register
    watch, _ = _three_branch_gates(mcf_136)
    dead = followchannel.Condition(
        register.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=0xEE),
        mask=0xFF00, value=1)
    with pytest.raises(followchannel.FollowChannelError):
        followchannel.plan_from_registers(mcf_136, watch=watch, target_channel=44, gates=[dead])


def test_condition_gates_refuse_a_zero_mask(mcf_136):
    """FINDING 1: `(slot & 0) == 0` is always true -- an un-gate, not a gate."""
    from fireflymcf import register
    watch, _ = _three_branch_gates(mcf_136)
    always = followchannel.Condition(
        register.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=0xEE), mask=0, value=0)
    with pytest.raises(followchannel.FollowChannelError):
        followchannel.plan_from_registers(mcf_136, watch=watch, target_channel=44, gates=[always])


def test_condition_gates_refuse_a_test_the_unset_sentinel_satisfies(mcf_136):
    """FINDING 1, the serious one: `(slot & 0xFF00) == 0xFF00` is satisfied by the 0xFFFF a
    receive slot reads before the G12's first sweep, so the follower would TRANSMIT on the bus at
    boot -- a live write, not a dead feature. The single slot gate already refused this; the
    Condition path three lines away did not."""
    from fireflymcf import register
    watch, _ = _three_branch_gates(mcf_136)
    boot_on = followchannel.Condition(
        register.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=0xEE),
        mask=0xFF00, value=0xFF00)
    with pytest.raises(followchannel.FollowChannelError):
        followchannel.plan_from_registers(mcf_136, watch=watch, target_channel=44, gates=[boot_on])
    # and `build` refuses it too, for a plan constructed directly (defence in depth)
    plan = followchannel.plan_from_registers(mcf_136, watch=watch, target_channel=44, gates=[])
    plan.gates = [boot_on]
    with pytest.raises(followchannel.FollowChannelError):
        followchannel.build(plan, CODE_ADDR)


def test_condition_gates_still_allow_a_wide_value_on_the_full_mask(mcf_136):
    """The one asymmetry between the two gate shapes, and it is deliberate: `mask=0xFFFF,
    value>0xFF` stays legal on the Condition path (it is `follow_register`'s movw+cmp_reg branch,
    a shipped capability), while the single slot gate refuses it so its byte-pinned
    `cmp r0,#imm8` shape cannot be silently widened without the caller naming a `gate_mask`."""
    watch, gates = _three_branch_gates(mcf_136)
    plan = followchannel.plan_from_registers(
        mcf_136, watch=watch, target_channel=44, gates=gates[2:])     # the 0x123 wide value
    _blob, disasm = followchannel.build(plan, CODE_ADDR)
    assert "movw r2, #0x123" in [t for _a, t in disasm]
    with pytest.raises(followchannel.FollowChannelError):             # ... but not as a slot gate
        followchannel.resolve(mcf_136, 25, 44, 200, gate_slot_selector=0xEE, gate_on_value=0x123)
