"""`grammar.prove`: twins of 13.6's record decoder, layout engine and shape renderer, located in
any image's GuiCode by their normalised instruction lists (2026-11-08)."""
from __future__ import annotations

import importlib.util
import struct
from pathlib import Path

import pytest

from fireflymcf import grammar, theme

NEEDS_RESEARCH_DATA = "reads the three stock images"

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = theme.GUICODE_BASE_DEFAULT

# Expectations only: the module locates every one of these (Ghidra guicode136/guicode147).
TWINS = {"13.6": (0x252CB0, 0x257BFC, 0x256C54), "14.7": (0x255578, 0x25A4B8, 0x259514)}
# The renderer's wtype arms that differ on 14.7 (state offsets moved, 2026-11-08).
ARMS_DIFFERING_147 = {10, 14, 18}
ARMS_TEMPLATES = {2, 3, 4, 6, 8, 11, 12, 0x20, 0x21}
DEFAULT_ARM = frozenset({9, 15, *range(20, 32), *range(36, 46)})
# `cmp.w R, #0x298` sites over the decoder's exact Ghidra extent (the research's 11 was a window).
BOUND_SITES = 13


@pytest.fixture
def stocks(mcf_136, mcf_147, mcf_1123):
    return {"13.6": mcf_136, "14.7": mcf_147, "11.23": mcf_1123}


def _patch(data: bytes, addr: int, code: bytes) -> bytes:
    sec = theme.guicode_section(data)
    out = bytearray(data)
    at = sec.offset + addr - BASE
    out[at:at + len(code)] = code
    return bytes(out)


def _read(data: bytes, addr: int, n: int) -> bytes:
    sec = theme.guicode_section(data)
    return data[sec.offset + addr - BASE:sec.offset + addr - BASE + n]


def test_136_proves_itself(stocks):
    p = grammar.prove(stocks["13.6"])
    assert (p.decoder, p.layout, p.renderer) == TWINS["13.6"]
    assert p.bounds == {}
    assert p.renderer_wtypes == frozenset(range(48))
    assert p.renderer_default_wtypes == DEFAULT_ARM
    assert not any("not proven" in line for line in p.proof)


def test_147_twins_and_bound(stocks):
    p = grammar.prove(stocks["14.7"])
    assert (p.decoder, p.layout, p.renderer) == TWINS["14.7"]
    assert p.bounds == {"mode7_index": (0x298, 0x1F8)}
    assert ARMS_DIFFERING_147.isdisjoint(p.renderer_wtypes)
    assert ARMS_TEMPLATES <= p.renderer_wtypes
    assert p.renderer_wtypes == frozenset(range(48)) - ARMS_DIFFERING_147
    # wtypes whose table entry is the default arm (the dispatch's `bhi.w`) draw nothing
    assert p.renderer_default_wtypes == DEFAULT_ARM
    assert ARMS_TEMPLATES.isdisjoint(p.renderer_default_wtypes)
    text = "\n".join(p.proof)
    assert "0x255578" in text and "0x25a4b8" in text and "0x259514" in text
    # the arms that differ are named, with the first differing instruction
    assert "wt 10, 18" in text and "0x25a1f8" in text
    assert "wt 14" in text and "not proven" in text
    assert f"{BOUND_SITES} sites" in text and "0x255c82" in text


def test_bound_sites_counted_over_exact_extent():
    fp = grammar.fingerprints()
    sites = fp["functions"]["decoder"]["bounds"]["mode7_index"]
    assert len(sites) == BOUND_SITES


def test_1123_refuses(stocks):
    with pytest.raises(grammar.GrammarError) as e:
        grammar.prove(stocks["11.23"])
    assert e.value.code == "GRAMMAR"
    assert "decoder" in str(e.value)


def test_flipped_decoder_immediate_refuses_naming_it(stocks):
    data = stocks["14.7"]
    assert _read(data, 0x255C0E, 4) == bytes.fromhex("47f22f02")      # movw r2, #0x702f
    bad = _patch(data, 0x255C0E, bytes.fromhex("47f22e02"))            # movw r2, #0x702e
    with pytest.raises(grammar.GrammarError) as e:
        grammar.prove(bad)
    assert e.value.code == "GRAMMAR"
    assert "0x255c0e" in str(e.value) and "#0x702e" in str(e.value)


def test_retargeted_decoder_branch_refuses(stocks):
    """Same text, other target: branch targets are compared by position, not masked away."""
    data = stocks["14.7"]
    assert _read(data, 0x255660, 2) == bytes.fromhex("3bb9")           # cbnz r3, #0x255672
    with pytest.raises(grammar.GrammarError) as e:
        grammar.prove(_patch(data, 0x255660, bytes.fromhex("33b9")))   # cbnz r3, #0x255670
    assert "0x255660" in str(e.value)


def test_swapped_register_refuses(stocks):
    """Registers follow one bijection: `r2` -> `r3` at one site is not a renaming."""
    data = stocks["14.7"]
    assert _read(data, 0x255C0E, 4) == bytes.fromhex("47f22f02")      # movw r2, #0x702f
    with pytest.raises(grammar.GrammarError) as e:
        grammar.prove(_patch(data, 0x255C0E, bytes.fromhex("47f22f03")))   # movw r3, #0x702f
    assert e.value.code == "GRAMMAR"
    assert "0x255c0e" in str(e.value) and "register" in str(e.value)


def test_changed_pool_word_refuses(stocks):
    """Pool words are compared: an SRAM address maps one-to-one, so one changed word refuses."""
    data = stocks["14.7"]
    assert _read(data, 0x2558C0, 4) == struct.pack("<I", 0x200301B4)
    with pytest.raises(grammar.GrammarError) as e:
        grammar.prove(_patch(data, 0x2558C0, struct.pack("<I", 0x200301B4 ^ 0x10)))
    assert e.value.code == "GRAMMAR" and "SRAM" in str(e.value)


def test_retargeted_call_refuses(stocks):
    """Callees map one-to-one: one `bl` to another helper refuses."""
    data = stocks["14.7"]
    assert _dis(data, 0x25558C, 4) == [(0x25558C, "bl #0x2549e4")]
    bad = _patch(data, 0x25558C, bytes.fromhex("fff730fa"))
    assert _dis(bad, 0x25558C, 4) == [(0x25558C, "bl #0x2549f0")]
    with pytest.raises(grammar.GrammarError) as e:
        grammar.prove(bad)
    assert "0x25558c" in str(e.value) and "callee" in str(e.value)


def test_two_addresses_onto_one_refuses(stocks):
    """The mapping is injective: 13.6's once-used SRAM word 0x20036DB0 (14.7 pool `0x2562E4`)
    made equal to the image of another 13.6 address (0x20036D54 -> 0x20036D58) refuses, though
    no 13.6 value maps two ways."""
    data = stocks["14.7"]
    assert _read(data, 0x2562E4, 4) == struct.pack("<I", 0x20036DB4)
    with pytest.raises(grammar.GrammarError) as e:
        grammar.prove(_patch(data, 0x2562E4, struct.pack("<I", 0x20036D58)))
    assert "0x20036d58 is 13.6's 0x20036d54" in str(e.value)


def test_pool_shifts_are_reported(stocks):
    text = "\n".join(grammar.prove(stocks["14.7"]).proof)
    assert "decoder: SRAM pool values (5 distinct) shifted +0x0 x2, +0x4 x3" in text
    assert "distinct callees, mapped one-to-one" in text


def test_inconsistent_bound_refuses(stocks):
    data = stocks["14.7"]
    assert _read(data, 0x255C82, 4) == bytes.fromhex("b0f5fc7f")      # cmp.w r0, #0x1f8
    bad = _patch(data, 0x255C82, bytes.fromhex("b0f5007f"))            # cmp.w r0, #0x200
    with pytest.raises(grammar.GrammarError) as e:
        grammar.prove(bad)
    assert "0x255c82" in str(e.value)


def test_mutated_arm_drops_only_its_wtypes(stocks):
    """A change inside wt 0x20's arm (14.7 `0x2599E0`) un-proves wt 0x20 alone."""
    data = stocks["14.7"]
    p = grammar.prove(data)
    a = 0x2599F0
    assert _read(data, a, 4) == bytes.fromhex("b2f92a20")             # ldrsh.w r2, [r2, #0x2a]
    q = grammar.prove(_patch(data, a, bytes.fromhex("b2f92c20")))      # ldrsh.w r2, [r2, #0x2c]
    assert q.renderer_wtypes == p.renderer_wtypes - {0x20}
    assert any(f"{a:#x}" in line and "wt 32" in line for line in q.proof)


def test_change_reached_by_the_default_arm_unproves_every_wtype(stocks):
    """14.7 wt 12's arm (`0x25A42C`) holds code the wt 47 arm branches into, and the default arm
    reaches wt 47's: every wtype reaches the default, so none is proven."""
    data = stocks["14.7"]
    assert _read(data, 0x25A436, 2) == bytes.fromhex("042b")           # cmp r3, #4
    with pytest.raises(grammar.GrammarError) as e:
        grammar.prove(_patch(data, 0x25A436, bytes.fromhex("052b")))   # cmp r3, #5
    assert "no wtype arm" in str(e.value) and "0x25a436" in str(e.value)


def test_change_in_code_the_dispatch_reaches_unproves_every_wtype(stocks):
    """The code after the tbh table (14.7 `0x2595F6`) is reached from the dispatch itself."""
    data = stocks["14.7"]
    assert _read(data, 0x2595F8, 4) == bytes.fromhex("b2f95e30")       # ldrsh.w r3, [r2, #0x5e]
    with pytest.raises(grammar.GrammarError) as e:
        grammar.prove(_patch(data, 0x2595F8, bytes.fromhex("b2f96030")))
    assert "shared code after the table" in str(e.value) and "0x2595f8" in str(e.value)


def test_mutated_dispatch_refuses(stocks):
    data = stocks["14.7"]
    # cmp r3, #0x2f before the tbh: the table's size, and so the dispatch, changes
    tbh = next(a for a, t in _dis(data, 0x259514, 0x90) if t.startswith("tbh"))
    cmp_at = next(a for a, t in _dis(data, tbh - 8, 8) if t == "cmp r3, #0x2f")
    bad = _patch(data, cmp_at, bytes([0x2E, 0x2B]))                    # cmp r3, #0x2e
    with pytest.raises(grammar.GrammarError) as e:
        grammar.prove(bad)
    assert "renderer" in str(e.value)


def test_proof_is_memoised_and_copied(stocks):
    p = grammar.prove(stocks["14.7"])
    p.bounds["mode7_index"] = (0, 0)
    assert grammar.prove(stocks["14.7"]).bounds == {"mode7_index": (0x298, 0x1F8)}


def test_no_guicode_refuses():
    with pytest.raises(grammar.GrammarError) as e:
        grammar.prove(b"\x00" * 64)
    assert e.value.code == "GRAMMAR"


def test_generator_reproduces_shipped_fingerprints(stocks, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "gen_grammar_fingerprints", REPO_ROOT / "scripts" / "gen_grammar_fingerprints.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    built = gen.build(stocks["13.6"])
    assert built == grammar.fingerprints()
    with pytest.raises(SystemExit):
        gen.build(stocks["14.7"])                        # 13.6 stock only


def _dis(data: bytes, addr: int, n: int) -> list:
    from fireflymcf import thumb
    return thumb.disassemble(_read(data, addr, n), addr)


def test_text_drawers_twin_on_136(stocks):
    p = grammar.prove(stocks["13.6"])
    assert p.text_drawers_twin
    assert any(line.startswith("text11:") and "one twin, at 0x2559a0" in line for line in p.proof)
    assert any(line.startswith("text8:") and "one twin, at 0x2557c0" in line for line in p.proof)


def test_text_drawers_not_twins_on_147_without_refusing(stocks):
    p = grammar.prove(stocks["14.7"])          # still proves the decoder, layout and renderer
    assert not p.text_drawers_twin
    lines = [ln for ln in p.proof if ln.startswith(("text11:", "text8:"))]
    assert len(lines) == 2 and all("not a twin" in ln and "differs at" in ln for ln in lines)


def test_text_drawer_mutation_clears_the_flag_only(stocks):
    """One immediate changed in 13.6's wt-8 drawer: the flag drops, prove still succeeds."""
    data = stocks["13.6"]
    at = 0x255810                        # `ldrb r4, [r1, #0xb]` (Finding 84) in FUN_002557C0
    old = _read(data, at, 2)
    new = bytes([old[0] ^ 0x40, old[1]])  # imm5 0xb -> another offset
    p = grammar.prove(_patch(data, at, new))
    assert not p.text_drawers_twin and (p.decoder, p.layout, p.renderer) == TWINS["13.6"]


# --- the LCD firmware's group handling (`grammar.prove_lcd`, 2026-11-08) --------------------------

# Expectations only (Ghidra lcd136; research `groups.md` §3): every image carries them at
# 13.6's addresses, its LCD a relocation twin of 13.6's. FUN_0021A664 is two Ghidra ranges.
LCD_FUNCTIONS_136 = {"FUN_0021B420": 0x21B420, "FUN_0021B5E8": 0x21B5E8,
                     "FUN_0021B678": 0x21B678, "FUN_0021B8B4": 0x21B8B4,
                     "FUN_0021BAB4": 0x21BAB4, "FUN_0021AB88": 0x21AB88,
                     "FUN_0021A664": 0x21A664, "FUN_0021A664_tail": 0x21AEA4}


def _lcd_sec(data: bytes):
    from fireflymcf import container
    return next(s for s in container.parse_container(data).sections
                if "LCD Firmware" in (s.name or ""))


def _lcd_patch(data: bytes, addr: int, code: bytes) -> bytes:
    from fireflymcf.raminit import LCD_BASE
    out = bytearray(data)
    at = _lcd_sec(data).offset + addr - LCD_BASE
    out[at:at + len(code)] = code
    return bytes(out)


def _lcd_read(data: bytes, addr: int, n: int) -> bytes:
    from fireflymcf.raminit import LCD_BASE
    at = _lcd_sec(data).offset + addr - LCD_BASE
    return data[at:at + n]


def test_lcd_136_proves_itself(stocks):
    p = grammar.prove_lcd(stocks["13.6"])
    assert dict(p.functions) == LCD_FUNCTIONS_136
    assert set(p.pool_shifts) == {"SRAM", "GuiData", "LCD flash"}
    assert all(s == frozenset({0}) for s in p.pool_shifts.values())
    assert p.callee_shifts == frozenset({0})
    assert "30 callee bindings over the LCD functions, shifted +0x0" in "\n".join(p.proof)
    assert len(p.proof) >= len(LCD_FUNCTIONS_136)


@pytest.mark.parametrize("version", ["14.7", "11.23"])
def test_lcd_twins_with_relocations_reported(stocks, version):
    """11.23 and 14.7 share one LCD, a relocation twin of 13.6's (research `groups.md` §3: 17
    bytes differ in `0x21A000`-`0x21E124`, rodata pointers +4 and `bl` displacements +2). Inside
    the fingerprinted functions only the rodata shift shows: both LCD-flash pool words
    (`FUN_0021B420`'s deadline table at pool `0x21B5E4`, `FUN_0021BAB4`'s at `0x21BDD4`) are +4. The six `bl` sites whose
    displacement grew by 2 (`0x21B1DC`, `0x21B218`, `0x21B288`, `0x21C23C`, `0x21D290`,
    `0x21D3C0`) lie outside every one of their Ghidra bodies, so each of the 30 callee
    bindings is +0. FUN_0021A664's calls land on the located twins: `FUN_0021AB88` (`0x21A67A`),
    its own second range (tail call `0x21A6B2`), and the gated setter (`0x21B002`)."""
    p = grammar.prove_lcd(stocks[version])
    assert dict(p.functions) == LCD_FUNCTIONS_136
    assert p.pool_shifts["LCD flash"] == frozenset({4})
    assert p.pool_shifts["SRAM"] == p.pool_shifts["GuiData"] == frozenset({0})
    assert p.callee_shifts == frozenset({0})
    text = "\n".join(p.proof)
    assert "LCD flash pool values (1 distinct) shifted +0x4 x1" in text
    assert "30 callee bindings over the LCD functions, shifted +0x0" in text
    assert "reference to FUN_0021B5E8 at 0x21b002 lands on the located twin 0x21b5e8" in text
    assert "reference to FUN_0021A664_tail at 0x21a6b2 lands on the located twin 0x21aea4" in text
    assert "reference to FUN_0021B8B4 at 0x21bb9a lands on the located twin 0x21b8b4" in text
    for name, addr in LCD_FUNCTIONS_136.items():
        assert f"{name}" in text and f"{addr:#x}" in text


def test_lcd_flipped_immediate_refuses_naming_it(stocks):
    data = stocks["14.7"]
    assert _lcd_read(data, 0x21B686, 2) == bytes.fromhex("c172")        # strb r1, [r0, #0xb]
    bad = _lcd_patch(data, 0x21B686, bytes.fromhex("0173"))              # strb r1, [r0, #0xc]
    with pytest.raises(grammar.LcdGrammarError) as e:
        grammar.prove_lcd(bad)
    assert e.value.code == "LCD_GRAMMAR"
    assert "FUN_0021B678" in str(e.value)
    assert "0x21b686" in str(e.value) and "#0xc" in str(e.value)


def test_lcd_retargeted_gated_setter_call_refuses(stocks):
    """The record-field path: FUN_0021A664's second range calls the gated setter at `0x21B002`.
    Retargeted to another helper (`0x21B694`), the cross-check refuses naming that call."""
    data = stocks["14.7"]
    assert _lcd_read(data, 0x21B002, 4) == _bl(0x21B002, 0x21B5E8)
    with pytest.raises(grammar.LcdGrammarError) as e:
        grammar.prove_lcd(_lcd_patch(data, 0x21B002, _bl(0x21B002, 0x21B694)))
    assert "FUN_0021A664_tail" in str(e.value) and "0x21b002" in str(e.value)


def _bl(pc: int, target: int) -> bytes:
    """The Thumb-2 `bl` at `pc` to `target`."""
    off = target - (pc + 4)
    s, i1, i2 = (off >> 24) & 1, (off >> 23) & 1, (off >> 22) & 1
    h1 = 0xF000 | s << 10 | (off >> 12) & 0x3FF
    h2 = 0xD000 | ((~i1 ^ s) & 1) << 13 | ((~i2 ^ s) & 1) << 11 | (off >> 1) & 0x7FF
    return struct.pack("<HH", h1, h2)


def test_lcd_changed_rodata_word_refuses(stocks):
    """An LCD-flash pool word maps one-to-one: FUN_0021B420's deadline-table pointer (14.7 pool
    `0x21B5E4`, 13.6's `0x21F2A2` + 4) made another value refuses."""
    data = stocks["14.7"]
    assert _lcd_read(data, 0x21B5E4, 4) == struct.pack("<I", 0x21F2A6)
    with pytest.raises(grammar.LcdGrammarError) as e:
        grammar.prove_lcd(_lcd_patch(data, 0x21B5E4, struct.pack("<I", 0x40000000)))
    assert "FUN_0021B420" in str(e.value) and "LCD flash" in str(e.value)


def test_lcd_call_between_them_must_land_on_the_located_twin(stocks):
    """`FUN_0021BAB4` calls the sender `FUN_0021B8B4` once (`0x21BB9A`). Retargeted to another
    helper (`0x21B694`) the callee mapping alone is still one-to-one, so it is the cross-check
    that refuses."""
    data = stocks["14.7"]
    assert _lcd_read(data, 0x21BB9A, 4) == bytes.fromhex("fff78bfe")     # bl #0x21b8b4
    bad = _lcd_patch(data, 0x21BB9A, bytes.fromhex("fff77bfd"))          # bl #0x21b694
    with pytest.raises(grammar.LcdGrammarError) as e:
        grammar.prove_lcd(bad)
    assert "FUN_0021B8B4" in str(e.value) and "0x21bb9a" in str(e.value)
    assert "not the located twin 0x21b8b4" in str(e.value)


def test_lcd_a_call_onto_a_located_twin_must_be_that_function_in_13_6(stocks):
    """The reverse direction: `FUN_0021B5E8` calls the helper `0x21A06C` once (`0x21B63C`), a
    function outside the set. Retargeted onto the located twin of `FUN_0021B678`, the callee
    mapping is still one-to-one within the function and the forward check sees no listed
    callee, so it is the reverse check that refuses."""
    data = stocks["14.7"]
    assert _lcd_read(data, 0x21B63C, 4) == _bl(0x21B63C, 0x21A06C)
    with pytest.raises(grammar.LcdGrammarError) as e:
        grammar.prove_lcd(_lcd_patch(data, 0x21B63C, _bl(0x21B63C, 0x21B678)))
    assert e.value.code == "LCD_GRAMMAR"
    assert "FUN_0021B5E8" in str(e.value) and "0x21b63c" in str(e.value)
    assert "FUN_0021B678" in str(e.value)


def test_lcd_a_container_without_an_lcd_section_refuses_cleanly(stocks):
    data = stocks["14.7"]
    head = data[:_lcd_sec(data).offset]
    at = head.index(b"LCD Firmware\x00")
    renamed = bytearray(data)
    renamed[at:at + 3] = b"XYZ"
    with pytest.raises(grammar.LcdGrammarError, match="no LCD Firmware section") as e:
        grammar.prove_lcd(bytes(renamed))
    assert e.value.code == "LCD_GRAMMAR"
    with pytest.raises(grammar.LcdGrammarError):
        grammar.prove_lcd(b"\x00" * 64)


def test_lcd_proof_is_memoised_and_immutable(stocks):
    p = grammar.prove_lcd(stocks["14.7"])
    with pytest.raises(TypeError):
        p.functions["FUN_0021B420"] = 0                           # type: ignore[index]
    assert grammar.prove_lcd(stocks["14.7"]) is p


def test_group_prefix_gate_is_prove_lcd(stocks):
    """`donors.group_prefix` is gated on `prove_lcd` alone: it refuses LCD_GRAMMAR when the LCD's
    group handling is not 13.6's (even where the GuiCode grammar proves), and accepts 11.23,
    whose GuiCode grammar does not prove but whose LCD does."""
    from fireflymcf import donors
    pre, proof = donors.group_prefix(stocks["14.7"], "group.tap")
    assert pre == donors.GROUP_PREFIXES["group.tap"].prefix
    assert any("FUN_0021B420" in line for line in proof)
    pre, _ = donors.group_prefix(stocks["11.23"], "group.hold")
    assert pre == donors.GROUP_PREFIXES["group.hold"].prefix
    bad = _lcd_patch(stocks["14.7"], 0x21B686, bytes.fromhex("0173"))
    grammar.prove(bad)                                       # GuiCode untouched: still proves
    for name in donors.GROUP_PREFIXES:
        with pytest.raises(donors.DonorError) as e:
            donors.group_prefix(bad, name)
        assert e.value.code == "LCD_GRAMMAR" and "0x21b686" in str(e.value)


def test_lcd_generator_reproduces_shipped_fingerprints(stocks):
    spec = importlib.util.spec_from_file_location(
        "gen_grammar_fingerprints", REPO_ROOT / "scripts" / "gen_grammar_fingerprints.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert gen.build_lcd(stocks["13.6"]) == grammar.lcd_fingerprints()
    with pytest.raises(SystemExit):
        gen.build_lcd(stocks["14.7"])                    # 13.6 stock only
