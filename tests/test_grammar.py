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
