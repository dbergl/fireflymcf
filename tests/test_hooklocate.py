"""`hooklocate`: the per-tick hook, its chain, the stock callee, the append point and the host-API
cells, located structurally in every stock image (2026-11-07) and checked against every grown
13.6 reference build."""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from fireflymcf import followchannel, hooklocate, theme, thumb

NEEDS_RESEARCH_DATA = "reads the three stock images and the reference builds"

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = theme.GUICODE_BASE_DEFAULT

# Expectations only (the research table, 2026-11-07); the module locates every one of them.
STOCK = {
    "13.6": dict(entry1=0x24EE28, hook_off=0xEE3C, hook_bytes="f8f7dcfe", callee=0x247BF8,
                 append=0x259BB4, setter=0x20039908, sender=0x2003986C),
    "14.7": dict(entry1=0x24F274, hook_off=0xF288, hook_bytes="f8f710fd", callee=0x247CAC,
                 append=0x25C4CC, setter=0x200395FC, sender=0x20039558),
    "11.23": dict(entry1=0x24CE10, hook_off=0xCE24, hook_bytes="faf7b4fb", callee=0x247590,
                  append=0x257548, setter=0x20039304, sender=0x20039294),
}


@pytest.fixture
def stocks(mcf_136, mcf_147, mcf_1123):
    return {"13.6": mcf_136, "14.7": mcf_147, "11.23": mcf_1123}


def _patch(data: bytes, addr: int, code: bytes) -> bytes:
    """`data` with `code` written at GuiCode runtime address `addr`."""
    sec = theme.guicode_section(data)
    out = bytearray(data)
    at = sec.offset + addr - BASE
    out[at:at + len(code)] = code
    return bytes(out)


# ---------------------------------------------------------------------------------------------
# The oracle for built images: followchannel.chain_from_hook as it stood at 704f366, with the
# 13.6 pins it read copied in. Kept here, independent of hooklocate, because chain_from_hook is
# to be rewired onto hooklocate; comparing against it then would compare hooklocate to itself.
# ---------------------------------------------------------------------------------------------
_PIN_TAIL_TARGET = 0x00247BF8
_PIN_HOOK_OFF = 0xEE3C
_PIN_HOOK_STOCK = bytes.fromhex("f8f7dcfe")
_PIN_STOCK_APPEND_POINT = 0x00259BB4


def _pinned_hook_target(section: bytes) -> int:
    hook_old = bytes(section[_PIN_HOOK_OFF:_PIN_HOOK_OFF + 4])
    if hook_old == _PIN_HOOK_STOCK:
        return _PIN_TAIL_TARGET
    append_point = BASE + len(section) - 4
    insns = thumb.disassemble(hook_old, BASE + _PIN_HOOK_OFF)
    text = insns[0][1] if insns else ""
    if text.startswith("bl #"):
        target = int(text[4:], 16)
        if _PIN_STOCK_APPEND_POINT <= target < append_point and target % 2 == 0:
            return target
    raise AssertionError(f"pinned hook_state refuses {hook_old.hex()} ({text})")


def _pinned_chain_from_hook(data: bytes) -> list[int]:
    sec = theme.guicode_section(data)
    section = data[sec.offset:sec.offset + sec.length]
    append_point = BASE + sec.length - 4
    target = _pinned_hook_target(section)
    chain, seen = [], set()
    while target != _PIN_TAIL_TARGET:
        assert _PIN_STOCK_APPEND_POINT <= target < append_point, f"{target:#x} outside"
        assert target not in seen, f"loop at {target:#x}"
        seen.add(target)
        chain.append(target)
        code = section[target - BASE:append_point - BASE]
        tail = next((t for _, t in thumb.disassemble(code, target) if t.startswith("b.w #")), None)
        assert tail is not None, f"{target:#x} has no b.w"
        target = int(tail[5:], 16)
    chain.append(_PIN_TAIL_TARGET)
    return chain


def _built_136_images(mcf_136):
    """Every `builds/*.bin` grown from stock 13.6: same GuiCode header vectors, longer section."""
    stock_sec = theme.guicode_section(mcf_136)
    stock_head = mcf_136[stock_sec.offset:stock_sec.offset + 0x20]
    out = []
    for p in sorted((REPO_ROOT / "builds").glob("*.bin")):
        d = p.read_bytes()
        try:
            sec = theme.guicode_section(d)
        except ValueError:
            continue
        if d[sec.offset:sec.offset + 0x20] == stock_head and sec.length >= stock_sec.length:
            out.append((p.name, d))
    return out


# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["13.6", "14.7", "11.23"])
def test_stock_values(stocks, version):
    data, want = stocks[version], STOCK[version]
    h = hooklocate.locate_hook(data)
    assert h.entry1 == want["entry1"]
    assert h.hook_off == want["hook_off"]
    assert h.hook_addr == BASE + want["hook_off"]
    assert h.hook_bytes.hex() == want["hook_bytes"]
    assert h.stock_callee == want["callee"]
    assert h.links == ()
    assert hooklocate.stock_append_point(data) == want["append"]
    cells = hooklocate.host_api_cells(data)
    assert cells[4] == want["setter"]
    assert cells[8] == want["sender"]
    # Each proof line names an address and an instruction.
    assert any(f"{h.hook_addr:#x} bl #{h.stock_callee:#x}" in p for p in h.proof)
    assert any("#0x214]" in p for p in h.proof)
    assert any("bl-callers" in p and f"{h.hook_addr:#x}" in p for p in h.proof)
    # With its own stock as the bound, the same answer.
    assert hooklocate.locate_hook(data, stock=data) == h


def test_13_6_matches_followchannel_pins(mcf_136):
    """13.6 locates today's pins exactly (byte identity of every build rests on it)."""
    h = hooklocate.locate_hook(mcf_136)
    assert h.hook_off == followchannel.HOOK_OFF
    assert h.hook_bytes == followchannel.HOOK_STOCK
    assert h.stock_callee == followchannel.TAIL_TARGET
    assert hooklocate.stock_append_point(mcf_136) == followchannel.STOCK_APPEND_POINT
    cells = hooklocate.host_api_cells(mcf_136)
    assert cells[0x08] == followchannel.SENDER_PTR_ADDR
    assert cells[0x04] == followchannel.VARIANT_SETTER


def test_matches_chain_from_hook_on_built_images(mcf_136):
    images = _built_136_images(mcf_136)
    if not images:
        pytest.skip("no reference builds linked (scripts/link-research.sh)")
    chained = 0
    for name, img in images:
        want = _pinned_chain_from_hook(img)
        h = hooklocate.locate_hook(img, stock=mcf_136)
        assert list(h.links) + [h.stock_callee] == want, name
        assert hooklocate.locate_hook(img) == h, name
        assert h.hook_off == _PIN_HOOK_OFF, name
        chained += bool(h.links)
    assert chained, "no reference build has a chain: the walk was never exercised"


def test_append_point_refuses_a_built_image(mcf_136):
    h = hooklocate.locate_hook(mcf_136)
    at = _scratch(mcf_136)
    img = _patch(mcf_136, at, _fake_link(at, h.stock_callee))
    img = _patch(img, h.hook_addr, thumb.bl(h.hook_addr, at))
    built = [img] + [d for _, d in _built_136_images(mcf_136)][:3]
    for d in built:
        assert hooklocate.locate_hook(d).links
        with pytest.raises(hooklocate.HookNotFound, match="not a stock image"):
            hooklocate.stock_append_point(d)


def _fake_link(at: int, target: int) -> bytes:
    """`push {r0-r3,lr}; pop.w {r0-r3,lr}; b.w target` at `at` (10 bytes)."""
    return (thumb.push_reglist([0, 1, 2, 3, 14]) + thumb.pop_w_reglist([0, 1, 2, 3, 14])
            + thumb.b_w(at + 6, target))


def _scratch(data: bytes) -> int:
    """A stock-code address to overwrite for a mutation: the middle of the stock callee's body,
    far from entry1, the callee's prologue and its +0x214 load."""
    return hooklocate.locate_hook(data).stock_callee + 0x1000


def test_link_below_append_point_refused(mcf_136):
    h = hooklocate.locate_hook(mcf_136)
    at = _scratch(mcf_136)
    img = _patch(mcf_136, at, _fake_link(at, h.stock_callee))
    img = _patch(img, h.hook_addr, thumb.bl(h.hook_addr, at))
    # Without the stock bound the link contract alone accepts it ...
    assert hooklocate.locate_hook(img).links == (at,)
    # ... with it, a link in stock code is refused.
    with pytest.raises(hooklocate.HookNotFound, match="below the stock append point"):
        hooklocate.locate_hook(img, stock=mcf_136)


def test_link_without_tail_refused(mcf_147):
    h = hooklocate.locate_hook(mcf_147)
    at = _scratch(mcf_147)
    no_pop = thumb.push_reglist([0, 1, 2, 3, 14]) + thumb.NOP4 + thumb.b_w(at + 6, h.stock_callee)
    img = _patch(mcf_147, at, no_pop)
    img = _patch(img, h.hook_addr, thumb.bl(h.hook_addr, at))
    with pytest.raises(hooklocate.HookNotFound, match="no .*tail"):
        hooklocate.locate_hook(img)


def test_link_loop_refused(mcf_136):
    h = hooklocate.locate_hook(mcf_136)
    at = _scratch(mcf_136)
    img = _patch(mcf_136, at, _fake_link(at, at))
    img = _patch(img, h.hook_addr, thumb.bl(h.hook_addr, at))
    with pytest.raises(hooklocate.HookNotFound, match="loops"):
        hooklocate.locate_hook(img)


def test_callee_with_two_callers_refused(mcf_136):
    h = hooklocate.locate_hook(mcf_136)
    at = _scratch(mcf_136)
    img = _patch(mcf_136, at, thumb.bl(at, h.stock_callee))
    with pytest.raises(hooklocate.HookNotFound, match="bl-callers"):
        hooklocate.locate_hook(img)


def test_callee_without_config_load_refused(mcf_136):
    """The hook pointed at another stock function: entry0, which never loads +0x214."""
    h = hooklocate.locate_hook(mcf_136)
    sec = theme.guicode_section(mcf_136)
    entry0 = struct.unpack_from("<I", mcf_136, sec.offset + 0x0C)[0] & ~1
    img = _patch(mcf_136, h.hook_addr, thumb.bl(h.hook_addr, entry0))
    with pytest.raises(hooklocate.HookNotFound, match="0x214"):
        hooklocate.locate_hook(img)


@pytest.mark.parametrize("version", ["13.6", "11.23"])
def test_entry1_branch_before_bl_refused(stocks, version):
    data = stocks[version]
    e1 = hooklocate.locate_hook(data).entry1
    img = _patch(data, e1 + 2, thumb.b_n(e1 + 2, e1 + 6))
    with pytest.raises(hooklocate.HookNotFound, match="leaves"):
        hooklocate.locate_hook(img)


def test_entry1_r0_write_before_bl_refused(mcf_136):
    e1 = hooklocate.locate_hook(mcf_136).entry1
    img = _patch(mcf_136, e1 + 2, thumb.movs_imm(0, 0))
    with pytest.raises(hooklocate.HookNotFound, match="overwrites r0"):
        hooklocate.locate_hook(img)


def test_missing_thumb_vector_refused(mcf_136):
    sec = theme.guicode_section(mcf_136)
    w = struct.unpack_from("<I", mcf_136, sec.offset + 0x10)[0]
    img = bytearray(mcf_136)
    struct.pack_into("<I", img, sec.offset + 0x10, w & ~1)
    with pytest.raises(hooklocate.HookNotFound, match="Thumb"):
        hooklocate.locate_hook(bytes(img))


def test_entry0_offset_stored_twice_refused(mcf_136):
    """entry0's `ldr r2,[r4,#8]` rewritten to `ldr r2,[r4,#4]`: +0x04 stored twice."""
    sec = theme.guicode_section(mcf_136)
    e0 = struct.unpack_from("<I", mcf_136, sec.offset + 0x0C)[0] & ~1
    code = mcf_136[sec.offset + e0 - BASE:sec.offset + e0 - BASE + 0x30]
    at = next(a for a, t in thumb.disassemble(code, e0) if t == "ldr r2, [r4, #8]")
    img = _patch(mcf_136, at, thumb.ldr_imm(2, 4, 4))
    with pytest.raises(hooklocate.HookNotFound, match=r"\+0x4 stored twice"):
        hooklocate.host_api_cells(img)


def test_memo_keys_on_bytes(mcf_136, mcf_147):
    """The cache keys on content: a bytearray edited in place answers from its new bytes."""
    buf = bytearray(mcf_136)
    first = hooklocate.locate_hook(buf)
    assert hooklocate.locate_hook(bytes(mcf_136)) == first
    buf[:] = mcf_147
    assert hooklocate.locate_hook(buf).hook_off == STOCK["14.7"]["hook_off"]
    cells = hooklocate.host_api_cells(mcf_136)
    cells[4] = 0
    assert hooklocate.host_api_cells(mcf_136)[4] == STOCK["13.6"]["setter"]


def test_stock_callee_must_match(mcf_136, mcf_147):
    """With `stock`, the chain must end in stock's own callee: 14.7's hook (no links, so the
    append-point bound never applies) against 13.6 as its stock is refused."""
    assert hooklocate.locate_hook(mcf_147).links == ()
    with pytest.raises(hooklocate.HookNotFound, match="not the stock callee"):
        hooklocate.locate_hook(mcf_147, stock=mcf_136)


def test_memo_does_not_read_module_constants(mcf_136, monkeypatch):
    """The cache takes every constant as an argument: a monkeypatched window answers from the
    patched value, not from a result cached under the old one."""
    hooklocate.locate_hook(mcf_136)
    monkeypatch.setattr(hooklocate, "ENTRY1_WINDOW", 0x10)
    with pytest.raises(hooklocate.HookNotFound, match="no bl within 0x10"):
        hooklocate.locate_hook(mcf_136)
    monkeypatch.undo()
    assert hooklocate.locate_hook(mcf_136).hook_off == STOCK["13.6"]["hook_off"]
