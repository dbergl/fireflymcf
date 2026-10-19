"""Two appended per-tick routines must CHAIN on the single hook, not conflict.

Both `add_option_follower` (the light->pump follower) and `add_aes_low_volts_trigger` (the
cell-45 display bridge) detour the same `bl` at GuiCode+0xEE3C -- the single caller of the
per-tick paint fn FUN_00247BF8 -- and both used to pin the append point to stock's
0x00259BB4, so whichever ran second refused with UNEXPECTED_APPEND_POINT.

The fix: a routine appended onto an already-detoured image tail-branches to the routine the
hook CURRENTLY reaches (the previous routine's entry) instead of FUN_00247BF8, and the hook is
retargeted to the new routine. Each routine saves/restores r0-r3+lr and never touches r4-r7,
so the chain is transparent to the displaced paint fn. Nothing else about either routine
changes, so the coach-proven code stays proven apart from one branch target.
"""
import hashlib
import json

from fireflymcf import container, followchannel, recipe, register, theme, thumb

STOCK = "firefly-13.6/mcf.bin"
TICK_FN = 0x00247BF8
STOCK_APPEND = 0x00259BB4
FOLLOWER_ENTRY = STOCK_APPEND + followchannel.ENTRY_OFF          # 0x259BCC

PUMPLINK = {"id": "pumplink", "type": "add_option_follower",
            "params": {"watch_channel": 25, "target_channel": 44}}
AESSWAP = {"id": "aesswap", "type": "swap_aes_electrical", "params": {}}
AESLV = {"id": "aeslowvolts", "type": "add_aes_low_volts_trigger", "params": {}}


def _stock():
    return open(STOCK, "rb").read()


def _compile(ops):
    stock = _stock()
    proj = recipe.Project.from_json(json.dumps({
        "schema": 1, "base_sha256": hashlib.sha256(stock).hexdigest(),
        "build_name": "chain test", "ops": ops, "preview": {}}))
    res = recipe.compile_project(proj, stock)
    assert res.ok, res.errors
    return res.data


def _guicode(img):
    sec = next(s for s in container.parse_container(img).sections if "GuiCode" in s.name)
    return img[sec.offset:sec.offset + sec.length]


def _hook_target(section):
    text = thumb.disassemble(section[followchannel.HOOK_OFF:followchannel.HOOK_OFF + 4],
                             theme.GUICODE_BASE_DEFAULT + followchannel.HOOK_OFF)[0][1]
    assert text.startswith("bl #"), text
    return int(text[4:], 16)


def _bw_targets(section, lo, hi):
    """Targets of every `b.w` in runtime range [lo, hi) of the section (the appended area)."""
    base = theme.GUICODE_BASE_DEFAULT
    out = []
    for at in range(lo, hi, 2):
        insns = thumb.disassemble(section[at - base:at - base + 4], at)
        if insns and insns[0][1].startswith("b.w #"):
            out.append((at, int(insns[0][1][5:], 16)))
    return out


def _append_point(section):
    return theme.GUICODE_BASE_DEFAULT + len(section) - 4


def test_resolve_on_a_detoured_image_targets_the_previous_routine():
    """followchannel level: after the follower is in, a display-bridge plan resolved on that
    image carries the CURRENT hook bytes and a tail target of the follower's entry."""
    img = _compile([PUMPLINK])
    reg = register.resolve_register(img, dgn="DGN_CONFIG", selector=0x2F)
    plan = followchannel.resolve_display_bridge(img, gate_addr=reg.slot_addr)
    section = _guicode(img)
    assert plan.hook_old == section[followchannel.HOOK_OFF:followchannel.HOOK_OFF + 4]
    assert plan.hook_old != followchannel.HOOK_STOCK
    assert plan.tail_target == FOLLOWER_ENTRY
    blob, disasm = followchannel.build_display_bridge(plan, _append_point(section))
    assert f"b.w #{FOLLOWER_ENTRY:#x}" in [d[1] for d in disasm]
    # and on STOCK the tail is still the paint fn -- the proven form is unchanged
    plan0 = followchannel.resolve_display_bridge(_stock(), gate_addr=reg.slot_addr)
    assert plan0.tail_target == TICK_FN and plan0.hook_old == followchannel.HOOK_STOCK


def test_follower_then_low_volts_chains_bridge_to_follower_to_tick():
    img = _compile([PUMPLINK, AESSWAP, AESLV])
    section = _guicode(img)
    second = _append_point(_guicode(_compile([PUMPLINK])))     # where the bridge landed
    assert second > STOCK_APPEND
    # hook -> bridge (entry at its blob start) -> follower entry -> paint fn
    assert _hook_target(section) == second + followchannel.DISPLAY_BRIDGE_ENTRY_OFF
    bws = dict(_bw_targets(section, STOCK_APPEND, _append_point(section)))
    assert FOLLOWER_ENTRY in bws.values(), bws
    assert TICK_FN in bws.values(), bws
    bridge_tail = [t for at, t in bws.items() if at >= second]
    follower_tail = [t for at, t in bws.items() if at < second]
    assert bridge_tail == [FOLLOWER_ENTRY], bridge_tail
    assert follower_tail == [TICK_FN], follower_tail


def test_low_volts_then_follower_chains_the_other_way():
    img = _compile([AESSWAP, AESLV, PUMPLINK])
    section = _guicode(img)
    second = _append_point(_guicode(_compile([AESSWAP, AESLV])))
    bridge_entry = STOCK_APPEND + followchannel.DISPLAY_BRIDGE_ENTRY_OFF
    assert _hook_target(section) == second + followchannel.ENTRY_OFF
    bws = dict(_bw_targets(section, STOCK_APPEND, _append_point(section)))
    assert [t for at, t in bws.items() if at >= second] == [bridge_entry]
    assert [t for at, t in bws.items() if at < second] == [TICK_FN]

