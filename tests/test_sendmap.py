"""`sendmap` -- every way the panel transmits, and what the panel calls each message
(`GUICODE.md` Finding 83).

Pinned on 13.6 (the coverage table, named examples), spot-checked on 11.23 and 14.7, and held to
independent evidence: the coach's own captures (every frame the panel sent, matched to an entry
or a GuiCode send), `answer_key.OPTIONS` / `INTERIOR_MASTER` / `EXTERIOR_MASTER` / `CHANNELS`,
the gestures decode, the receive side's option join (phase 2), and the LCD instructions the
transmit path is read from. The stub tests at the bottom show the pins can fail.
"""
from __future__ import annotations

import glob
import json
import os
import re
import struct
from pathlib import Path

import pytest

from fireflymcf import busmap, cli, container, gestures, sendmap, slottrace

from tests import answer_key

REPO = Path(__file__).resolve().parents[1]
B136 = "firefly-13.6/mcf.bin"
B1123 = "firefly-11.23/mcf.bin"
B147 = "firefly-14.7 (Terrain)/mcf.bin"


def image(name):
    return (REPO / name).read_bytes()


def _clear():
    for mod in (sendmap, busmap, slottrace):
        for v in vars(mod).values():
            if callable(getattr(v, "cache_clear", None)):
                v.cache_clear()


@pytest.fixture
def clean_caches():
    _clear()
    yield
    _clear()


def groups(data):
    return {g.group: g for g in sendmap.binding_groups(data)}


def handler_sends(data, touch_id):
    return [s for s in sendmap.guicode_sends(data) if any(i == touch_id for i, _e in s.touch_ids)]


# ------------------------------------------------------------------------------ the pins

COVERAGE_136 = {
    0x1FED9: {"bindings": 224, "reached": 129, "unattached": 26, "dead": 69},
    0x1FEDB: {"bindings": 337, "reached": 159, "unattached": 75, "dead": 103},
    0x1FEF9: {"bindings": 100, "reached": 24, "unattached": 61, "dead": 15},
    0x1FBDA: {"bindings": 24, "reached": 24},
    0x1FFEE: {"bindings": 22, "reached": 22},
}


def test_coverage_136():
    m = sendmap.send_map(image(B136))
    cov = sendmap.coverage(m)
    for dgn, want in COVERAGE_136.items():
        got = {k: cov[dgn][k] for k in ("bindings", *sendmap.GROUP_CLASSES) if cov[dgn][k]}
        assert got == want, hex(dgn)
    assert len(m.groups) == 233
    assert sum(1 for g in m.groups for e in g.entries if not e.stub) == 741
    assert {c: sum(1 for g in m.groups if g.cls == c) for c in sendmap.GROUP_CLASSES} == \
        {"reached": 101, "unattached": 65, "dead": 62, "stub": 5}
    assert {c: sum(1 for s in m.guicode if s.context == c) for c in sendmap.CONTEXTS} == \
        {"touch": 526, "touch-test": 9, "config-set": 1, "other": 165}
    assert len(m.config_calls) == 33
    assert sum(1 for s in m.guicode if s.frame.template) == 554


def test_the_dispatcher_136():
    """22 press handlers after the id-change gate at 0x24ACB2, and 108 release handlers in the
    0xFF handler's second search on the latched id (r2), the first at 0x24B024
    (`movw r3,#0x846 ; cmp r2,r3 ; beq.w 0x24CB4E`). No search path is left unattributed."""
    data = image(B136)
    handlers, gates, exits, tree, loose = sendmap.touch_dispatch(data)
    assert gates == (0x24ACB2,) and exits == frozenset({0x24B2A6}) and not loose
    edges = [e for ids in handlers.values() for _i, e in ids]
    assert (edges.count("press"), edges.count("release")) == (22, 108)
    assert (0x846, "release") in handlers[0x24CB4E]
    assert (0xFF, "press") in handlers[0x24B01A]
    assert (0x843, "release") in handlers[0x24BA80]      # the one-value range, no final compare
    _c, _b, ins, at = sendmap._gc(data)
    cmp = ins[at[0x24B028]]
    assert (cmp.mnemonic, cmp.op_str) == ("cmp", "r2, r3")
    latch = ins[at[0x24ACB8]]
    assert (latch.mnemonic, latch.op_str) == ("ldrh", "r2, [r2, #0xe]")


def test_water_pump_136():
    """HOME's Water Pump: touch id 44 = group 44, DC_DIMMER_COMMAND_2 to instance 44 -- toggle
    (variant 1) or off (variant 0), on tap or release after a hold; OUTPUTS page 'Water Pump'."""
    g = groups(image(B136))[44]
    assert g.cls == "reached" and g.caption == "Water Pump" and g.guicode_variant
    assert any(c.attached and "HOME" in c.pages for c in g.controls)
    live = [e for e in g.entries if not e.stub]
    assert [(e.dgn, e.instance, e.command, e.variant) for e in live] == \
        [(0x1FEDB, 44, 5, 1), (0x1FEDB, 44, 3, 0)]
    assert all(e.phases == ("tap-release", "release-after-hold") for e in live)
    assert ("Water Pump", "outputs-page") in {(n.text, n.source) for n in live[0].names}
    assert live[0].payload == bytes.fromhex("2cffc805ff00ffff")


@pytest.mark.parametrize("group,label,variant,want", [
    (1, "ON", 1, answer_key.INTERIOR_MASTER), (2, "OFF", 1, answer_key.INTERIOR_MASTER),
    (216, "ON", 0, answer_key.EXTERIOR_MASTER), (217, "OFF", 0, answer_key.EXTERIOR_MASTER),
])
def test_the_masters_136(group, label, variant, want):
    """The Light Master's and Exterior Master's ON/OFF groups carry channel 35 in one variant
    each, and GuiCode picks the variant by floorplan: `ldr r3,=0x2003A77A` / `cmp r3,#9` /
    `cmpne r3,#1` (0x24B352-0x24B35C) -- RT or WT: interior variant 0 (no 35), exterior 1
    (with 35, the Security Light), else (WD among them) interior 1, exterior 0
    (`setVariant` at 0x24E834-0x24E84C). The WD variant is exactly the answer key's (PC.8)."""
    data = image(B136)
    g = groups(data)[group]
    master = [c for c in g.controls if c.caption == label and c.attached]
    assert master and master[0].title.endswith("Master") and g.guicode_variant
    live = {e.instance for e in g.entries if not e.stub and e.variant == variant}
    other = {e.instance for e in g.entries if not e.stub and e.variant != variant}
    assert live == set(want) and live ^ other == {35}
    assert {e.command for e in g.entries if not e.stub} <= ({0, 1} if label == "ON" else {6})
    _c, _b, ins, at = sendmap._gc(data)
    sets = [(sendmap._resolve(data, "r0", k), sendmap._resolve(data, "r1", k))
            for k in range(at[0x24E834] - 2, at[0x24E84C] + 1) if ins[k].mnemonic == "blx"]
    assert [(r0[0], r1[0]) for r0, r1 in sets] == [(1, 1), (2, 1), (0xD8, 0), (0xD9, 0)]


def test_low_volts_136():
    """Finding 57's Low Volts: object 31, touch id 0x81 = group 129, 0x1FED9 selector 0x2F
    := 0 / := 1 (with read-backs) in two variants GuiCode selects (setVariant(129, v))."""
    g = groups(image(B136))[129]
    assert g.caption.startswith("Low Volts") and g.guicode_variant and g.cls == "reached"
    assert any(c.obj == 31 for c in g.controls)
    writes = {(e.variant, e.write_value) for e in g.entries if e.selector == 0x2F
              and e.write_value is not None}
    assert writes == {(0, 0), (1, 1)}


def test_tank_heater_is_a_guicode_handler_136():
    """13.6's HOME Tank Heater is not a binding group: touch id 0x851 is a GuiCode action
    (release edge), whose handler sends DC_DIMMER_COMMAND_2 off and on (cmd 3, cmd 1) to
    instances 5-8 -- the answer key's four TANK HEAT pins."""
    sends = handler_sends(image(B136), 0x851)
    assert sends and all(("Tank Heater", "control") == (s.names[0].text, s.names[0].source)
                         for s in sends)
    got = {(s.frame.instance, s.frame.data[3]) for s in sends}
    assert got == {(i, c) for i in (5, 6, 7, 8) for c in (1, 3)}
    for i in (5, 6, 7, 8):
        assert answer_key.CHANNELS[i].startswith("TANK HEAT")


def test_options_rows_send_their_selectors_136():
    """Every OPTIONS row in the answer key: a release handler of a touch region captioned with
    the row's name writes its selector (D1), and the payload is named by the row (option)."""
    data = image(B136)
    sends = sendmap.guicode_sends(data)
    for name, sel in answer_key.OPTIONS:
        hits = [s for s in sends if s.context == "touch" and s.frame.selector == sel
                and s.frame.data[6] == sendmap.CONFIG_OP_SET
                and any(c.attached and c.caption and c.caption.startswith(name)
                        for c in s.controls)]
        assert hits, name
        assert all(e == "release" for s in hits for _i, e in s.touch_ids)
        assert any(n.source == "option" and n.text.startswith(name) for s in hits for n in s.names)


def test_rebinding_option_sends_the_off_136():
    """Bath Light (selector 0xE4, id 0x857) selects channel 26 and turns 25 off; Cargo (0xE3,
    0x856) the reverse -- `answer_key.OPTION_CHANNEL_BINDING`, PC.33's companion OFF."""
    data = image(B136)
    for tid, off in ((0x857, answer_key.OPTION_CHANNEL_BINDING[0xE3]),
                     (0x856, answer_key.OPTION_CHANNEL_BINDING[0xE4])):
        offs = [s.frame.instance for s in handler_sends(data, tid)
                if s.frame.dgn == 0x1FEDB and s.frame.data[3] == 3]
        # the select arm turns off the abandoned channel first; the deselect arm (a tap on the
        # row already chosen) turns off the row's own
        assert offs[0] == off and set(offs) == set(answer_key.OPTION_CHANNEL_BINDING.values())


def test_config_set_calls_136():
    """The OPTIONS handlers call the config-set helper with their row's offset (Heat Pump cfg
    0x0A from id 0x843), and the receive side's compare block calls it for every selector."""
    calls = sendmap.config_set_calls(image(B136))
    hp = [c for c in calls if c.offset == 0x0A and c.context == "touch"]
    assert {c.value for c in hp} == {0, 1} and all(c.option == "Heat Pump" for c in hp)
    assert all(i == 0x843 for c in hp for i, _e in c.touch_ids)
    echo = [c for c in calls if 0x249E4E <= c.at <= 0x249EC6]
    assert len(echo) == 11 and all(c.context == "other" for c in echo)
    assert {c.option for c in echo} >= {"Heat Pump", "Bath Fan", "Black Tank", "floorplan"}


# ------------------------------------------------------------------------------ the oracles

LCD_INTERNAL = {0x1FECA, 0x1FFFD, 0x0EEFF, 0x0ECFF, 0x0EBFF}   # DM_RV, DC source, claim, TP
REMAP_BUILD = {(0x1FEDB, bytes.fromhex("26ffc805ff00ffff"))}   # PC.13's remapped control


def _captured_panel_frames():
    out = {}
    for f in sorted(glob.glob(str(REPO / "captures" / "*.log"))):
        if os.path.basename(f).startswith(("pc-1123", "pc-147")):
            continue                               # other builds
        for line in open(f, errors="ignore"):
            m = re.search(r"\b([0-9A-F]{8})#([0-9A-F]{16})\b", line)
            if m and int(m.group(1), 16) & 0xFF == 0x9F:
                cid = int(m.group(1), 16)
                out.setdefault(((cid >> 8) & 0x3FFFF, bytes.fromhex(m.group(2))), f)
    return out


def test_every_captured_panel_frame_is_in_the_map():
    """Every distinct frame the panel (SA 0x9F) put on the coach's bus, in every 13.6 capture,
    is a binding's payload or a GuiCode frame (a built frame's unknown bytes match anything),
    except the LCD firmware's own transmits and the one frame a remapped build sent."""
    m = sendmap.send_map(image(B136), None, False)
    pats = [(e.dgn, None, tuple(e.payload)) for g in m.groups for e in g.entries if not e.stub]
    pats += [(s.frame.dgn, s.frame.dgn_expr, s.frame.data) for s in m.guicode]

    def explained(dgn, data):
        for pd, expr, pdata in pats:
            if pd is None and not (expr.startswith("0xea00+") and dgn >> 8 == 0xEA):
                continue
            if pd is not None and pd != dgn:
                continue
            if all(not isinstance(b, int) or b == data[i] for i, b in enumerate(pdata)):
                return True
        return False

    frames = _captured_panel_frames()
    assert len(frames) >= 250
    missing = {k for k in frames if not explained(*k)}
    assert {d for d, _p in missing} - {d for d, _p in REMAP_BUILD} <= LCD_INTERNAL
    assert missing - {k for k in missing if k[0] in LCD_INTERNAL} == REMAP_BUILD
    assert len(frames) - len(missing) >= 240


def test_no_dead_group_payload_was_ever_sent():
    """The negative behind `dead` and `unattached`, held to the bus: of the payloads only such
    groups carry (no reached group and no exact GuiCode frame sends them), none appears from the
    panel in any 13.6 capture, but the one request a GuiCode parser also builds
    (`18EA429F#EBFE00..`, FUN_0024F104's `0EA00 + [..]` with `EB FE 00 00 00 00 00 00`)."""
    m = sendmap.send_map(image(B136), None, False)
    live, other = set(), set()
    for g in m.groups:
        for e in g.entries:
            if not e.stub:
                (live if g.cls == "reached" else other).add((e.dgn, bytes(e.payload)))
    exact = {(s.dgn, bytes(s.frame.data)) for s in m.guicode
             if s.dgn is not None and all(isinstance(b, int) for b in s.frame.data)}
    only = other - live - exact
    assert len(only) >= 200
    seen = only & set(_captured_panel_frames())
    assert seen == {(0x0EA42, bytes.fromhex("ebfe000000000000"))}
    built = [s for s in m.guicode if s.dgn is None and s.frame.dgn_expr.startswith("0xea00+")
             and s.frame.data == tuple(bytes.fromhex("ebfe000000000000"))]
    assert [s.function for s in built] == [0x24F104]


def test_bindings_agree_with_gestures():
    """Each entry's phases, variant and repeat flag are the gestures decode of the same bytes."""
    for name in (B136, B1123, B147):
        data = image(name)
        for g in sendmap.binding_groups(data, False):
            beh = gestures.describe_group(data, g.group)
            mine = [(e.index, e.phases, e.variant, e.repeats) for e in g.entries if not e.stub]
            theirs = [(e.binding_index, tuple(e.phase_names), e.variant, e.repeats)
                      for e in beh.emissions]
            assert mine == theirs, (name, g.group)


@pytest.mark.parametrize("name", [B136, B147])
def test_the_receive_option_join_meets_the_send_side(name):
    """Phase 2 names a 15FCE entry by the OPTIONS row its compare block's config-set call
    reaches; the send side's touch handlers write the same selector, named by the same row."""
    data = image(name)
    sends = sendmap.guicode_sends(data)
    n = 0
    for e in busmap.receive_map(data, frozenset({busmap.DGN_CONFIG})):
        if e.cls != "config" or e.filter_value is None or not e.name or e.name == "floorplan":
            continue
        rows = set(e.name.split(" / "))
        hits = [s for s in sends if s.context == "touch" and s.frame.selector == e.filter_value]
        assert hits, (name, hex(e.filter_value))
        assert any(x.source in ("option", "echo") and set(x.text.split(" / ")) & rows
                   for s in hits for x in s.names), (name, hex(e.filter_value))
        n += 1
    assert n >= 5


@pytest.mark.parametrize("name", [B136, B1123, B147])
def test_the_lcd_transmit_path(name):
    """The instructions the transmit path is read from, in each build's LCD section: the
    binding sender inserts the entry's 18 low bits at id bit 8 and priority 6, copies the payload
    in the 3,2,1,0,7,6,5,4 order, and calls the ring; the API table's +0x08 word is the ring."""
    data = image(name)
    lcd = sendmap.lcd_sender(data)
    assert (lcd.payload_copy, lcd.binding_send, lcd.transmit) == (0x21B890, 0x21B9DE, 0x21825C)
    sec = next(s for s in container.parse_container(data).sections if "LCD" in s.name)
    body = data[sec.offset:sec.offset + sec.length]

    import capstone
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

    def at(a):
        i = next(md.disasm(body[a - sendmap.LCD_BASE:a - sendmap.LCD_BASE + 4], a, count=1))
        return i.mnemonic, i.op_str
    assert at(0x21B9AA) == ("ldr", "r1, [r4]")
    assert at(0x21B9B2) == ("movs", "r3, #6")
    assert at(0x21B9B4) == ("bfi", "r2, r1, #8, #0x12")
    assert at(0x21B9C0) == ("bfi", "r2, r3, #2, #3")
    assert at(0x21B9C4) == ("ubfx", "r1, r1, #3, #1")
    assert at(0x21B9D4) == ("strb", "fp, [r2, #-0xc]!")
    assert at(0x21B9DE) == ("bl", "#0x21825c")
    assert at(0x218298) == ("ubfx", "r1, r2, #8, #0x12")
    assert lcd.pointers == (0x21C348,)
    assert struct.unpack_from("<I", body, 0x21C348 - sendmap.LCD_BASE)[0] == 0x21825D
    assert len(lcd.callers) == 8


@pytest.mark.parametrize("name,slot,store", [
    (B136, 0x2003986C, 0x247AA2), (B147, 0x20039558, 0x247B56), (B1123, 0x20039294, 0x24743A),
])
def test_the_sender_slot(name, slot, store):
    """GuiCode_entry0's store of the API table's +0x08 word (Finding 23's table for 13.6 and
    14.7), and every send goes through it."""
    data = image(name)
    assert sendmap.sender_slot(data) == (slot, store)
    assert sendmap.guicode_sends(data)


@pytest.mark.parametrize("name", [B136, B1123, B147])
def test_the_slot_never_escapes(name):
    """The argument from absence behind the GuiCode enumeration: the slot's address is one pool
    word, every literal load of it but the install is followed by a send through it, and at most
    two `blx` in the section have a target the walk cannot resolve."""
    data = image(name)
    slot, store = sendmap.sender_slot(data)
    _c, _b, ins, at = sendmap._gc(data)
    sends = {at[s.at] for s in sendmap.guicode_sends(data, False)}
    loads = slottrace.literal_loads(data)[slot]
    for k in loads:
        if ins[k + 1].address == store:
            continue
        assert any(j in sends for j in range(k + 1, k + 40)), hex(ins[k].address)
    unresolved = [k for k, cell in sendmap._indirect_calls(data) if cell is None]
    assert len(unresolved) <= 2


def test_the_binding_payload_is_the_wire_order():
    """The stored-to-wire reordering `newbutton.binding_payload` uses is the LCD's copy."""
    body = sendmap._PAYLOAD_COPY
    pairs = [(body[i] >> 3 & 7, body[i + 2] >> 3 & 7) for i in range(0, 32, 4)]
    src = [(body[i] >> 6) | ((body[i + 1] & 7) << 2) for i in range(0, 32, 4)]
    dst = [(body[i + 2] >> 6) | ((body[i + 3] & 7) << 2) for i in range(0, 32, 4)]
    assert all(a == 1 and b == 0 for a, b in pairs)            # ldrb r3,[r1,#i] / strb r3,[r0,#j]
    assert dict(zip(dst, src)) == {0: 3, 1: 2, 2: 1, 3: 0, 4: 7, 5: 6, 6: 5, 7: 4}


# ------------------------------------------------------------------------------ spot checks

def test_other_builds():
    for name, n_groups, n_bindings in ((B1123, 230, 732), (B147, 234, 743)):
        m = sendmap.send_map(image(name))
        assert len(m.groups) == n_groups
        assert sum(1 for g in m.groups for e in g.entries if not e.stub) == n_bindings
        assert sum(s.context == "touch" for s in m.guicode) >= 190
        wp = [g for g in m.groups if g.group == 44][0]
        assert wp.cls == "reached" and 44 in {e.instance for e in wp.entries if not e.stub}
    m147 = sendmap.send_map(image(B147))
    starlink = [s for s in m147.guicode if s.frame.selector == 0xE4 and s.context == "touch"]
    assert any(c.caption == "Starlink Power" for s in starlink for c in s.controls)


def test_spec_names_are_kept_apart():
    assert sendmap.spec_name(0x1FEDB) == "RV-C DC_DIMMER_COMMAND_2"
    assert sendmap.spec_name(0x0EA42) == "J1939 Request (destination 0x42)"
    assert sendmap.spec_name(0x1FACE) is None
    for g in sendmap.binding_groups(image(B136)):
        for e in g.entries:
            for n in e.names:
                assert n.source in ("outputs-page", "output-caption", "option", "echo")


# ------------------------------------------------------------------------------ the pins can fail

def test_without_the_dispatcher_no_send_is_a_touch(clean_caches, monkeypatch):
    monkeypatch.setattr(sendmap, "touch_dispatch", lambda data: ({}, (), frozenset(),
                                                                 frozenset(), frozenset()))
    sends = sendmap.guicode_sends(image(B136))
    assert not any(s.context == "touch" for s in sends)
    with pytest.raises(AssertionError):
        test_options_rows_send_their_selectors_136()


def test_without_touch_regions_every_group_is_dead(clean_caches, monkeypatch):
    monkeypatch.setattr(sendmap, "touch_regions", lambda data: {})
    gs = sendmap.binding_groups(image(B136))
    assert not any(g.cls in ("reached", "unattached") for g in gs)
    with pytest.raises(AssertionError):
        test_water_pump_136()


def test_without_the_sender_slot_guicode_sends_nothing(clean_caches, monkeypatch):
    monkeypatch.setattr(sendmap, "sender_slot", lambda data: (None, None))
    assert sendmap.guicode_sends(image(B136)) == ()
    with pytest.raises(AssertionError):
        test_every_captured_panel_frame_is_in_the_map()


def test_without_the_release_search_the_options_are_lost(clean_caches, monkeypatch):
    """The OPTIONS rows act on release: drop the second search and they are no longer touch."""
    real = sendmap.touch_dispatch

    def press_only(data):
        h, g, e, t, lo = real(data)
        return ({a: frozenset(x for x in ids if x[1] == "press") for a, ids in h.items()
                 if any(x[1] == "press" for x in ids)}, g, e, t, lo)
    monkeypatch.setattr(sendmap, "touch_dispatch", press_only)
    with pytest.raises(AssertionError):
        test_options_rows_send_their_selectors_136()


# ------------------------------------------------------------------------------ the command

def test_cli_bus_map_send(capsys):
    assert cli.main(["bus-map", str(REPO / B136), "--dgn", "1FEDB"]) == 0
    out = capsys.readouterr().out
    assert "SEND --" in out and "'Water Pump' (HOME)" in out and "RV-C DC_DIMMER_COMMAND_2" in out
    assert "RECEIVE --" in out
    assert cli.main(["bus-map", str(REPO / B136), "--dgn", "1fed9", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"receive", "coverage", "send"}
    assert payload["send"]["coverage"]["1FED9"]["bindings"] == 224
    assert payload["send"]["sender_slot"] == "0x2003986c"


def test_cli_doctor_bus(capsys):
    assert cli.main(["doctor", str(REPO / B136)]) == 0
    assert "doctor --bus" in capsys.readouterr().out
    assert cli.main(["doctor", str(REPO / B136), "--bus", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["bus"]["receive"]["drawn"] == 128 and info["bus"]["send"]["bindings"] == 741
