"""`themeswap` / the `set_theme` op: replacing the panel's chrome colours.

The oracle is the theme-init block itself: after a swap, `theme.mode7_palette` (the independent
extractor, which emulates the block) must read the requested colours back out of the patched
instructions, and no GuiData field may still carry a stock theme colour.
"""
from __future__ import annotations

import struct

import pytest

from fireflymcf import patch, recipe, theme, themeswap

THEME_SIX = {"bg": (33, 22, 17), "panel": (61, 40, 29), "button": (166, 86, 38),
           "header": (176, 58, 28), "rail": (112, 40, 22), "active": (200, 118, 22)}


def _apply(data, colours, wells=None):
    p = themeswap.plan(data, colours, wells)
    pt = patch.McfPatcher(data)
    for off, old, new, note in p.edits:
        pt.set_bytes(off, old, new, note)
    return pt.build()[0], p


def test_encoders_reproduce_the_stock_instructions(mcf_136):
    sec = theme.guicode_section(mcf_136)
    base = theme.guicode_base(sec)

    def at(addr, n):
        o = sec.offset + addr - base
        return mcf_136[o:o + n]
    assert at(0x24831E, 8) == themeswap.enc_dp_imm(False, 2, 2, 0xA90000) + themeswap.enc_addw(2, 2, 0x5F3)
    assert at(0x248340, 8) == themeswap.enc_dp_imm(False, 3, 3, 0x3AC000) + themeswap.enc_addw(3, 3, 0x37A)
    assert at(0x248336, 4) == themeswap.enc_movw(1, 0xAEEF)


def test_stock_theme_is_what_the_extractor_reads(mcf_136):
    pal = theme.mode7_palette(mcf_136)
    for role, idx in (("bg", 3), ("header", 4), ("panel", 8), ("rail", 10)):
        assert pal[idx] == themeswap.int_rgb(themeswap.STOCK[role])


def test_swap_reads_back_through_the_independent_extractor(mcf_136):
    out, p = _apply(mcf_136, THEME_SIX)
    pal = theme.mode7_palette(out)
    assert pal[3] == THEME_SIX["bg"] and pal[4] == THEME_SIX["header"]
    assert pal[8] == THEME_SIX["panel"] and pal[10] == THEME_SIX["rail"]
    # header and rail are exact (movw/movt), whatever bg and button are
    assert p.applied["header"] == themeswap.rgb_int(THEME_SIX["header"])
    assert p.applied["rail"] == themeswap.rgb_int(THEME_SIX["rail"])


def test_active_lands_on_the_nearest_reachable_colour_and_says_so(mcf_136):
    out, p = _apply(mcf_136, THEME_SIX)
    got = themeswap.int_rgb(p.applied["active"])
    assert max(abs(a - b) for a, b in zip(got, THEME_SIX["active"])) <= 16
    # the extractor emulates add.w r1,r3,#imm from the button literal: idx 35 is the lit colour
    assert theme.mode7_palette(out)[35] == got


def test_no_guidata_field_keeps_a_stock_theme_colour(mcf_136):
    out, p = _apply(mcf_136, THEME_SIX)
    stock = set(themeswap.STOCK.values())
    assert p.inline > 0
    assert not [o for o, v, _ in themeswap.inline_fields(out) if v in stock]
    gs, _ = __import__("fireflymcf").guidata.guidata_extent(out)
    decode = theme.colour_decode(out)
    pal = [struct.unpack_from("<I", out, gs + decode.palette_offset + 4 * i)[0]
           for i in range(decode.palette_len)]
    assert not stock & set(pal)


def test_only_the_traced_block_changes_in_guicode(mcf_136):
    out, _ = _apply(mcf_136, THEME_SIX)
    sec = theme.guicode_section(mcf_136)
    base = theme.guicode_base(sec)
    for off, n in patch.diff_ranges(mcf_136, out):
        if sec.offset <= off < sec.offset + sec.length:
            addr = base + off - sec.offset
            in_block = 0x248316 <= addr < 0x248390 or 0x2485CC <= addr < 0x2485E0
            at_checksum = off + n >= sec.offset + sec.length - 4
            assert in_block or at_checksum, hex(addr)


def test_tank_wells_repoint_only_wtype32_index_161(mcf_136):
    out, p = _apply(mcf_136, THEME_SIX, wells="bg")
    assert p.wells >= 4
    out2, p2 = _apply(mcf_136, THEME_SIX)
    assert p2.wells == 0


def test_a_second_swap_refuses(mcf_136):
    out, _ = _apply(mcf_136, THEME_SIX)
    with pytest.raises(themeswap.ThemeSwapError, match="already"):
        themeswap.plan(out, THEME_SIX)


def test_other_builds_refuse(mcf_1123):
    """11.23 has no theme-init in either skeleton's shape (`plan` on 14.7 is
    `test_colour_locate.test_plan_on_147_patches_the_located_sites`)."""
    with pytest.raises(themeswap.ThemeSwapError, match="theme-init"):
        themeswap.plan(mcf_1123, THEME_SIX)


def test_unknown_role_refuses(mcf_136):
    with pytest.raises(themeswap.ThemeSwapError, match="unknown"):
        themeswap.plan(mcf_136, {"chrome": (1, 2, 3)})


def test_op_compiles_unverified_and_verifies(mcf_136):
    import hashlib
    import json
    doc = {"schema": 1, "base_sha256": hashlib.sha256(mcf_136).hexdigest(),
           "build_name": "theme test", "ops": [
               {"id": "t", "type": "set_theme", "params": {
                   "colours": {k: list(v) for k, v in THEME_SIX.items()}, "tank_wells": "bg"}}]}
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(doc)), mcf_136)
    assert res.ok, res.errors
    assert res.unverified == []                # verified on the coach 2026-11-02
    assert any("set_theme" in line for line in res.manifest)


def test_op_rejects_bad_colour():
    import json
    doc = {"schema": 1, "base_sha256": "0" * 64, "build_name": "x", "ops": [
        {"id": "t", "type": "set_theme", "params": {"colours": {"bg": [1, 2]}}}]}
    with pytest.raises(recipe.RecipeError):
        recipe.Project.from_json(json.dumps(doc)).ops[0]
        recipe.compile_project(recipe.Project.from_json(json.dumps(doc)), b"")


STOCK_SLATE = (32, 50, 64)          # a stock inline-only blue (objects 720-727), no theme role


def test_extra_remaps_a_standalone_inline_colour(mcf_136):
    v = themeswap.rgb_int(STOCK_SLATE)
    assert [o for o, val, _ in themeswap.inline_fields(mcf_136) if val == v]
    out, p = _apply_extra(mcf_136, [(STOCK_SLATE, (61, 40, 29))])
    assert not [o for o, val, _ in themeswap.inline_fields(out) if val == v]
    assert [o for o, val, _ in themeswap.inline_fields(out) if val == 0x3D281D]


def test_extra_reaches_the_mode6_palette(mcf_136):
    blue = (27, 117, 188)           # mode-6 entry 5
    out, _ = _apply_extra(mcf_136, [(blue, (184, 104, 44))])
    gs, _ = __import__("fireflymcf").guidata.guidata_extent(out)
    assert struct.unpack_from("<I", out, gs + theme.colour_decode(out).palette_offset + 20)[0] == 0xB8682C


def test_extra_refuses_a_role_colour(mcf_136):
    with pytest.raises(themeswap.ThemeSwapError, match="role"):
        themeswap.plan(mcf_136, THEME_SIX, extra=[((39, 40, 98), (1, 2, 3))])


def _apply_extra(data, extra):
    p = themeswap.plan(data, THEME_SIX, None, extra)
    pt = patch.McfPatcher(data)
    for off, old, new, note in p.edits:
        pt.set_bytes(off, old, new, note)
    return pt.build()[0], p


def test_stored_colour_literals_are_the_theme_init_stores_not_returned_constants(mcf_136):
    lits = themeswap.stored_colour_literals(mcf_136)
    assert lits[0x2485FC] == 0x2BB673          # theme-init battery green -> idx 224's cell
    assert lits[0x248614] == 0x141414          # theme-init black -> idx 130's cell
    # the stock percent->colour helper at 0x2591ac returns its colours with `bx lr`
    assert 0x2591CC not in lits and 0x2591D0 not in lits


def test_extra_rewrites_a_stored_literal(mcf_136):
    out, p = _apply_extra(mcf_136, [((43, 182, 115), (112, 150, 58))])
    assert p.literals >= 1
    assert theme.mode7_palette(out)[224] == (112, 150, 58)


def test_tank_fills_change_tanks_only(mcf_136):
    grey = 0x808285
    before = sum(v == grey for _, v, _ in themeswap.inline_fields(mcf_136))
    p = themeswap.plan(mcf_136, THEME_SIX, None, None, [((128, 130, 133), (150, 134, 118))])
    assert p.tank_fills == 2                   # GREY and GREY2
    pt = patch.McfPatcher(mcf_136)
    for e in p.edits:
        pt.set_bytes(*e)
    out = pt.build()[0]
    after = sum(v == grey for _, v, _ in themeswap.inline_fields(out))
    assert after == before - 2 and after > 0   # every other UI grey is untouched
