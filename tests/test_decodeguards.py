"""Readouts the panel silently refuses to update.

The case these pin is `Absorb`/`Float`, where a two-sided fence on one field gates a second field
too, and a live injection at a plausible voltage was discarded with no indication on screen
(`BUS-BASELINE.md` PC.55).
"""
from __future__ import annotations

import pytest

from fireflymcf import decodeguards, tanknames, theme, thumb

pytest.importorskip("capstone")

ABSORB_CELL = 0x20039F30


def test_the_absorb_float_fence_is_found_with_both_sides(mcf_136):
    guard = decodeguards.guard_for_cell(mcf_136, ABSORB_CELL)
    assert guard is not None, "the fence that cost a coach session is not being found"
    assert guard.two_sided
    assert {f[1] for f in guard.fences} == {239, 362}
    assert set(guard.variables) == {206, 207}


def test_the_fence_is_inherited_by_a_second_field(mcf_136):
    """`Float` (var 207) carries the test; `Absorb` (var 206) is discarded with it.

    This is what makes the failure confusing on hardware: a good value for one field is dropped
    because a different field is out of range.
    """
    guard = decodeguards.guard_for_cell(mcf_136, ABSORB_CELL)
    assert guard.variables.index(207) < guard.variables.index(206)


def test_the_scan_finds_the_other_fenced_readouts_too(mcf_136):
    guards = decodeguards.range_guards(mcf_136)
    cells = {g.cell for g in guards}
    assert ABSORB_CELL in cells
    assert len(guards) >= 5, "the scan collapsed -- it should find several fenced readouts"
    assert all(g.fences for g in guards)


def test_enum_tests_are_not_reported_as_ranges(mcf_136):
    """A `cmp #1` state test is not a range fence; only magnitudes count."""
    for guard in decodeguards.range_guards(mcf_136):
        assert all(f[1] > decodeguards.MIN_RANGE_IMMEDIATE for f in guard.fences)


# Expectations only (research, 2026-11-08); `decoder_range` locates every one of them.
RANGE_136 = (0x247BF8, 0x24EDB0)        # `push {r4-r7,lr}` .. `pop {r4-r7,pc}`
RANGE_147 = (0x247CAC, 0x24F1FC)
RANGE_1123 = (0x247590, 0x24CDFA)       # `pop.w {r4-r11,pc}`
EPILOGUE_136 = 0x24EDB0
TAIL_136 = (0x24EDB2, 0x24EDB8)         # flag clear, then `b 0x24eba4` back into the body


def _patch(data: bytes, addr: int, code: bytes) -> bytes:
    sec = theme.guicode_section(data)
    out = bytearray(data)
    at = sec.offset + addr - theme.guicode_base(sec)
    out[at:at + len(code)] = code
    return bytes(out)


def test_the_range_is_the_callee_to_its_one_epilogue(mcf_136, mcf_147, mcf_1123):
    assert decodeguards.decoder_range(mcf_136) == RANGE_136
    assert decodeguards.decoder_range(mcf_147) == RANGE_147
    assert decodeguards.decoder_range(mcf_1123) == RANGE_1123


def test_the_proof_names_the_epilogue_and_the_tails(mcf_136):
    proof = decodeguards.decoder_range_proof(mcf_136)
    assert "0x24edb0" in proof and "pop {r4, r5, r6, r7, pc}" in proof
    assert "5 out-of-line tail blocks" in proof and "0x24edb2" in proof


def test_147_finds_the_seven_guards_and_the_absorb_float_fence(mcf_147):
    """14.7's Absorb/Float fence sits at 0x24ABE0, past where 13.6's old window ended."""
    guards = decodeguards.range_guards(mcf_147)
    assert len(guards) == 7
    absorb = [g for g in guards if set(g.variables) == {206, 207}]
    assert len(absorb) == 1 and absorb[0].two_sided
    assert absorb[0].fences[0][:2] == (0x24ABE0, 239)
    assert {f[1] for f in absorb[0].fences} == {239, 362}
    assert absorb[0].variables.index(207) < absorb[0].variables.index(206)


def test_1123_locates(mcf_1123):
    """11.23 locates; its guard variable lists leak (research 2026-11-08), which is why
    `cell_slot` is not enabled there on the strength of the range alone."""
    assert decodeguards.range_guards(mcf_1123)
    assert isinstance(decodeguards.range_guards(mcf_1123, 0x247590, 0x247600), list)


def _refused(img, match):
    with pytest.raises(decodeguards.DecoderRangeUnknown, match=match) as ei:
        decodeguards.decoder_range(img)
    assert ei.value.code == "DECODER_RANGE_UNKNOWN"
    with pytest.raises(decodeguards.DecoderRangeUnknown):
        decodeguards.range_guards(img)


def test_a_nopped_epilogue_is_refused(mcf_136):
    """The region then runs on into the next function, whose `push` is no epilogue."""
    img = _patch(mcf_136, EPILOGUE_136, bytes.fromhex("00bf"))
    _refused(img, "not the callee's epilogue")


def test_a_second_return_is_refused(mcf_136):
    img = _patch(mcf_136, 0x24A000, bytes.fromhex("7047"))          # `blx r3` -> `bx lr`
    _refused(img, "another return")


def test_a_computed_jump_is_refused(mcf_136):
    """A `tbb` with no `cmp #N; bhi` bound in front of it is no switch this can follow."""
    img = _patch(mcf_136, 0x24A00A, bytes.fromhex("dfe803f0"))      # `tbb [pc, r3]`
    _refused(img, "computed jump")


def test_a_branch_out_that_is_no_tail_is_refused(mcf_136):
    img = _patch(mcf_136, 0x24A012, thumb.b_w(0x24A012, 0x247000))  # before the entry
    _refused(img, "leaves the callee")


def test_a_tail_that_does_not_rejoin_is_refused(mcf_136):
    img = _patch(mcf_136, TAIL_136[1], thumb.b_n(TAIL_136[1], 0x24EE28))
    _refused(img, "does not rejoin")


@pytest.mark.parametrize("load", [thumb.ldrh_imm(2, 4, 0x10), bytes.fromhex("04cc")],
                         ids=["ldrh [r4,#0x10]", "ldm r4!, {r2}"])
def test_a_tail_that_loads_a_slot_is_refused(mcf_136, load):
    img = _patch(mcf_136, TAIL_136[0] + 2, load)
    _refused(img, "loads a receive slot")


@pytest.mark.parametrize("regions", [(), (RANGE_136, RANGE_136)])
def test_zero_or_two_regions_at_the_callee_are_refused(mcf_136, monkeypatch, regions):
    monkeypatch.setattr(tanknames, "decoder_ranges", lambda data: regions)
    _refused(mcf_136, "regions start at the callee")


def test_no_region_at_all_is_refused(mcf_136, monkeypatch):
    def none(data):
        raise tanknames.TankNameError("no region")
    monkeypatch.setattr(tanknames, "decoder_ranges", none)
    _refused(mcf_136, "no region")


def test_a_bounded_switch_is_followed_and_an_unbounded_one_refused(mcf_1123):
    """11.23's decoder holds `cmp r3, #4; bhi; tbb [pc, r3]` at 0x24C412 (research 2026-11-08):
    every arm lands inside the callee, so it stays in range. Without the `bhi` bound the table
    could send any index anywhere, and the locator refuses."""
    assert "1 bounded switch(es) (0x24c412)" in decodeguards.decoder_range_proof(mcf_1123)
    img = _patch(mcf_1123, 0x24C410, bytes.fromhex("00bf"))          # `bhi` -> `nop`
    _refused(img, "computed jump")


# 11.23's switch (research 2026-11-08): `0x24C40E cmp r3, #4; bhi; 0x24C412 tbb [pc, r3]`, a
# five-byte table at 0x24C416 padded to 0x24C41C, whose arms are 0x24C41C, 0x24C426, 0x24C430...
SWITCH_1123 = (0x24C40E, 0x24C412, 0x24C416)


def _as_tbh(data, entries):
    """11.23's switch rewritten as `cmp r3, #len-1; bhi; tbh [pc, r3, lsl #1]` over `entries`."""
    cmp_at, tb_at, table = SWITCH_1123
    img = _patch(data, cmp_at, bytes([len(entries) - 1, 0x2B]))
    img = _patch(img, tb_at, bytes.fromhex("dfe813f0"))
    return _patch(img, table, b"".join(e.to_bytes(2, "little") for e in entries))


def test_a_bounded_tbh_is_followed(mcf_1123):
    """Three halfword entries (the stock arms 0x24C41C, 0x24C426, 0x24C430) end the table at
    0x24C41C, an instruction boundary."""
    img = _as_tbh(mcf_1123, [3, 8, 13])
    assert decodeguards.decoder_range(img) == RANGE_1123
    assert "1 bounded switch(es) (0x24c412)" in decodeguards.decoder_range_proof(img)


def test_a_switch_arm_outside_the_callee_is_refused(mcf_1123):
    img = _as_tbh(mcf_1123, [3, 0x800, 13])                         # case 1 -> 0x24D416
    _refused(img, "sends case 1 to 0x24d416")


def test_a_switch_arm_into_its_own_table_is_refused(mcf_1123):
    img = _patch(mcf_1123, SWITCH_1123[2], b"\x00")                  # case 0 -> the table
    _refused(img, "sends case 0 to 0x24c416")


def test_a_switch_table_not_ending_on_an_instruction_is_refused(mcf_1123):
    """`cmp r3, #6`: seven entries end the table at 0x24C41E, inside `ldrb.w` at 0x24C41C."""
    img = _patch(mcf_1123, SWITCH_1123[0], bytes([6, 0x2B]))
    _refused(img, "does not end on an instruction")


def test_an_in_range_branch_off_an_instruction_boundary_is_refused(mcf_136):
    """`0x24EDA4 beq #0x24edd6` retargeted into the middle of `0x24ED96 bl` (4 bytes)."""
    img = _patch(mcf_136, 0x24EDA4, thumb.bcc_n(0x24EDA4, 0x24ED98, "eq"))
    _refused(img, "not on an instruction boundary")


def test_a_conditional_epilogue_is_not_the_epilogue(mcf_136):
    """An IT-predicated `popeq` is no epilogue. Capstone names it `popeq` and sets its cc, so the
    mnemonic test and the cc test each refuse it (this test cannot tell which fired)."""
    from fireflymcf import tanknames as tn
    _c, _b, ins, at = tn._guicode(mcf_136)
    ep = ins[at[EPILOGUE_136]]
    assert decodeguards._is_pop_r4_pc(ep)
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    md.detail = True
    it_pop = list(md.disasm(bytes.fromhex("08bff0bd"), EPILOGUE_136 - 2))   # it eq; popeq
    assert it_pop[1].mnemonic.startswith("pop") and not decodeguards._is_pop_r4_pc(it_pop[1])
