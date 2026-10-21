"""The Thumb-2 encoder (`fireflymcf.thumb`).

Every encoder is checked against a **real instruction already in 13.6**. That is the point: a
hand-written encoder validated only against my own reading of the ISA proves nothing, whereas
one that reproduces Firefly's compiler output byte-for-byte is checked against a second,
independent implementation.

The branch cases carry their own address because Thumb branch offsets are relative to `pc + 4`,
which is the single easiest thing to get wrong.
"""
from __future__ import annotations

import pytest

from fireflymcf import theme, thumb

capstone = pytest.importorskip("capstone")


@pytest.fixture(scope="module")
def code(mcf_136):
    sec = theme.guicode_section(mcf_136)
    base = theme.guicode_base(sec)
    body = mcf_136[sec.offset:sec.offset + sec.length]
    return lambda v, n: body[v - base:v - base + n]


# (name, encoder result, the address it lives at, how many bytes) -- all from stock 13.6.
def test_b_w_matches_the_image(code):
    assert thumb.b_w(0x24CD48, 0x24B29E) == code(0x24CD48, 4)


def test_bcc_narrow_matches_the_image(code):
    assert thumb.bcc_n(0x24E914, 0x24E91C, "ne") == code(0x24E914, 2)


def test_b_narrow_matches_the_image(code):
    assert thumb.b_n(0x24EB56, 0x24E91C) == code(0x24EB56, 2)


def test_movw_matches_the_image(code):
    assert thumb.movw(2, 0x838) == code(0x24E90E, 4)


def test_movs_imm_matches_the_image(code):
    assert thumb.movs_imm(3, 0x11) == code(0x24E916, 2)


def test_cmp_reg_matches_the_image(code):
    assert thumb.cmp_reg(3, 2) == code(0x24E912, 2)


def test_ands_reg_matches_the_image(code):
    # `ands r2, r7` at 0x00240D98 (section offset 0xD98): bytes 3a 40.
    assert thumb.ands_reg(2, 7) == code(0x240D98, 2)


def test_ldr_literal_w_matches_the_image(code):
    # `ldr.w r8, [pc, #0xa4]` at 0x00241C96 (pool word 0x00241D3C): bytes df f8 a4 80; and
    # `ldr.w lr, [pc, #0x10c]` at 0x002429F2 (pool word 0x00242B00).
    assert thumb.ldr_literal_w(8, 0x241C96, 0x241D3C) == code(0x241C96, 4)
    assert thumb.ldr_literal_w(14, 0x2429F2, 0x242B00) == code(0x2429F2, 4)


def test_ldrh_imm_matches_the_image(code):
    # `ldrh r0, [r6, #0x3e]` at 0x00240F3E: bytes f0 8f.
    assert thumb.ldrh_imm(0, 6, 0x3E) == code(0x240F3E, 2)


def test_strh_imm_matches_the_image(code):
    # `strh r3, [r2]` at 0x002409C2: bytes 13 80.
    assert thumb.strh_imm(3, 2, 0) == code(0x2409C2, 2)


def test_strb_imm12_matches_the_image(code):
    assert thumb.strb_imm12(3, 4, 0x20A) == code(0x24E918, 4)


@pytest.mark.parametrize("enc,text", [
    (thumb.movw(2, 0x1795), "movw r2, #0x1795"),
    (thumb.movs_imm(3, 0x27), "movs r3, #0x27"),
])
def test_new_immediates_disassemble_as_claimed(enc, text):
    assert thumb.disassemble(enc, 0x1000)[0][1] == text


# --- Encoders added for the light->pump follower routine (Task 2). ---
# Each is validated against a real instruction already in stock 13.6 GuiCode.

def test_ldr_literal_matches_the_image(code):
    # 0x240032: `ldr r4, [pc, #0x14]` -> pc = (0x240032+4)&~3 = 0x240034, target 0x240048.
    assert thumb.ldr_literal(4, 0x240032, 0x240048) == code(0x240032, 2)


def test_ldrb_imm_matches_the_image(code):
    assert thumb.ldrb_imm(1, 0, 0xE) == code(0x240014, 2)      # ldrb r1, [r0, #0xe]


def test_cmp_imm8_matches_the_image(code):
    assert thumb.cmp_imm8(0, 0) == code(0x2408AE, 2)           # cmp r0, #0
    assert thumb.cmp_imm8(5, 0x36) == code(0x2400CE, 2)        # cmp r5, #0x36


def test_sub_sp_imm_matches_the_image(code):
    assert thumb.sub_sp_imm(0xC) == code(0x240D24, 2)          # sub sp, #0xc
    # The follower's frame is sub sp,#0x14 (8-byte sp alignment at the send blx); this is exactly
    # FUN_00247BF8's own prologue frame, at runtime 0x00247BFA (section offset 0x7BFA).
    assert thumb.sub_sp_imm(0x14) == code(0x247BFA, 2)         # sub sp, #0x14


def test_add_sp_imm_matches_the_image(code):
    assert thumb.add_sp_imm(0xC) == code(0x240F3A, 2)          # add sp, #0xc


def test_ldr_imm_matches_the_image(code):
    assert thumb.ldr_imm(7, 0, 0) == code(0x240D94, 2)         # ldr r7, [r0]
    assert thumb.ldr_imm(4, 2, 4) == code(0x241008, 2)         # ldr r4, [r2, #4]


def test_str_sp_imm_matches_the_image(code):
    assert thumb.str_sp_imm(2, 0x120) == code(0x240050, 2)     # str r2, [sp, #0x120]


def test_mov_reg_matches_the_image(code):
    assert thumb.mov_reg(3, 13) == code(0x253FF8, 2)           # mov r3, sp


def test_blx_reg_matches_the_image(code):
    assert thumb.blx_reg(3) == code(0x24307A, 2)               # blx r3


def test_push_reglist_matches_the_image(code):
    assert thumb.push_reglist([0, 14]) == code(0x2408A8, 2)    # push {r0, lr}


def test_pop_w_reglist_matches_the_image(code):
    # T2 LDMIA.W sp! family: `pop.w {r4, r5, r6, r7, r8, pc}` at 0x242AF6.
    assert thumb.pop_w_reglist([4, 5, 6, 7, 8, 15]) == code(0x242AF6, 4)


def test_bl_matches_the_stock_hook(code):
    # The detour's own site: section offset 0xEE3C is the stock `bl 0x00247BF8`.
    assert thumb.bl(0x24EE3C, 0x247BF8) == code(0x24EE3C, 4) == bytes.fromhex("f8f7dcfe")


@pytest.mark.parametrize("enc,text", [
    (thumb.ldrb_imm(1, 0, 0), "ldrb r1, [r0]"),
    (thumb.cmp_imm8(1, 0), "cmp r1, #0"),
    (thumb.sub_sp_imm(16), "sub sp, #0x10"),
    (thumb.add_sp_imm(16), "add sp, #0x10"),
    (thumb.str_sp_imm(1, 0), "str r1, [sp]"),
    (thumb.mov_reg(0, 13), "mov r0, sp"),
    (thumb.blx_reg(2), "blx r2"),
    (thumb.push_reglist([0, 1, 2, 3, 14]), "push {r0, r1, r2, r3, lr}"),
    (thumb.pop_w_reglist([0, 1, 2, 3, 14]), "pop.w {r0, r1, r2, r3, lr}"),
])
def test_new_forms_disassemble_as_claimed(enc, text):
    assert thumb.disassemble(enc, 0x1000)[0][1] == text


def test_branches_round_trip_over_a_range():
    """Encode then decode at many displacements, both directions."""
    for delta in (-2048, -256, -4, 4, 256, 2046, 0x10000):
        at = 0x24C000
        target = at + 4 + delta
        assert thumb.disassemble(thumb.b_w(at, target), at)[0][1] == f"b.w #{target:#x}"
    for delta in (-254, -2, 2, 254):
        at = 0x24C000
        target = at + 4 + delta
        assert thumb.disassemble(thumb.bcc_n(at, target, "ne"), at)[0][1] == f"bne #{target:#x}"


def test_out_of_range_branches_raise():
    with pytest.raises(ValueError, match="out of range"):
        thumb.bcc_n(0x24C000, 0x24C000 + 4 + 400, "ne")
    with pytest.raises(ValueError, match="out of range"):
        thumb.b_n(0x24C000, 0x24C000 + 4 + 4000)


def test_assert_disassembles_to_catches_a_wrong_claim():
    """The safety net has to fire, or authoring machine code is unguarded."""
    with pytest.raises(ValueError, match="does not disassemble as intended"):
        thumb.assert_disassembles_to(thumb.movs_imm(3, 0x11), 0x1000, ["movs r3, #0x22"])


def test_high_register_forms_are_refused():
    """Only the low-register T1 encodings are implemented; a silent wrong encoding for r8+
    would be far worse than a refusal."""
    with pytest.raises(ValueError, match="low register"):
        thumb.movs_imm(9, 1)
    with pytest.raises(ValueError, match="low registers"):
        thumb.cmp_reg(3, 9)


# --- Encoders added for the pump Auto Off routine (Task 8b). ---
# Same discipline: matched against a real instruction in stock 13.6 where the compiler emitted
# one. `muls` is the exception and it is stated rather than glossed -- see below.

def test_lsrs_imm_matches_the_image(code):
    assert thumb.lsrs_imm(1, 1, 1) == code(0x24016E, 2)        # lsrs r1, r1, #1


def test_adds_imm8_matches_the_image(code):
    assert thumb.adds_imm8(3, 1) == code(0x247C08, 2)          # adds r3, #1


def test_subs_imm8_matches_the_image(code):
    assert thumb.subs_imm8(3, 2) == code(0x247D14, 2)          # subs r3, #2


def test_adds_reg_matches_the_image(code):
    assert thumb.adds_reg(0, 0, 2) == code(0x24011A, 2)        # adds r0, r0, r2


def test_subs_reg_matches_the_image(code):
    assert thumb.subs_reg(3, 5, 0) == code(0x240002, 2)       # subs r3, r5, r0


def test_ldr_imm12_matches_the_image(code):
    # `ldr.w r3, [r4, #0x214]` at 0x247C18 -- inside FUN_00247BF8 itself, reading the same
    # GuiCodeState the Auto Off routine reads the millisecond clock from (+0x204).
    assert thumb.ldr_imm12(3, 4, 0x214) == code(0x247C18, 4)


def test_muls_round_trips():
    """The one encoder with NO instance in the corpus to match against: scanning both halfword
    alignments of stock 13.6's GuiCode *and* LCD Firmware sections finds zero `muls` (the
    compiler never emitted the T1 form), so capstone's independent decode is the whole check."""
    code_ = thumb.muls(0, 1)          # muls r0, r1, r0
    assert len(code_) == 2
    assert code_ == bytes.fromhex("4843")
    thumb.assert_disassembles_to(code_, 0x00259C00, ["muls r0, r1, r0"])


@pytest.mark.parametrize("enc,text", [
    (thumb.lsrs_imm(0, 0, 10), "lsrs r0, r0, #0xa"),
    (thumb.adds_imm8(2, 2), "adds r2, #2"),
    (thumb.subs_imm8(1, 1), "subs r1, #1"),
    (thumb.adds_reg(3, 3, 2), "adds r3, r3, r2"),
    (thumb.subs_reg(2, 3, 2), "subs r2, r3, r2"),
    (thumb.ldr_imm12(0, 4, 0x204), "ldr.w r0, [r4, #0x204]"),
    (thumb.muls(1, 2), "muls r1, r2, r1"),
    (thumb.bcc_n(0x24C000, 0x24C008, "cc"), "blo #0x24c008"),
    (thumb.bcc_n(0x24C000, 0x24C008, "cs"), "bhs #0x24c008"),
])
def test_auto_off_forms_disassemble_as_claimed(enc, text):
    assert thumb.disassemble(enc, 0x24C000 if "#0x24c" in text else 0x1000)[0][1] == text


def test_auto_off_encoders_refuse_bad_operands():
    with pytest.raises(ValueError, match="low register"):
        thumb.subs_reg(3, 8, 2)
    with pytest.raises(ValueError, match="low register"):
        thumb.muls(8, 1)
    with pytest.raises(ValueError, match="low register"):
        thumb.lsrs_imm(0, 9, 4)
    with pytest.raises(ValueError, match="out of range"):
        thumb.lsrs_imm(0, 0, 0)        # imm5 == 0 encodes a shift of 32, not 0 -- refuse it
    with pytest.raises(ValueError, match="out of range"):
        thumb.adds_imm8(0, 0x100)
    with pytest.raises(ValueError, match="out of range"):
        thumb.ldr_imm12(0, 4, 0x1000)
    with pytest.raises(ValueError, match="bad register"):
        thumb.ldr_imm12(0, 15, 4)      # rN=pc is a literal load, a different instruction
    with pytest.raises(ValueError, match="bad register"):
        thumb.ldr_imm12(15, 4, 4)      # and rT=pc is a branch
