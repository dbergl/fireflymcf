"""The G12 config-selector -> RV-C command rules (`GUICODE.md` Finding 52, `BUS-BASELINE.md` PC.23).

The oracle here is the **bus**, not the file: four frames the coach actually transmitted on
2026-08-23 are hardcoded below, and the decoder has to reproduce every non-substituted byte of
each. That is the whole point -- a 12-byte-record decode is easy to fit and hard to falsify, so
it is held to frames captured before the table was read.
"""
import pytest

from fireflymcf import g12rules, patch

IMAGE = "firefly-13.6/mcf.bin"


@pytest.fixture(scope="module")
def data():
    with open(IMAGE, "rb") as handle:
        return handle.read()


# PC.23: what the G12 put on the wire, keyed by the DGN it broadcast. The bytes the rule
# SUBSTITUTES are the config values at the time (they varied across the +/- pair); every other
# byte is fixed by the record and must reproduce.
OBSERVED = {
    0x1FFD0: "01FFFFFFFFD800FF",     # Low Batt. Cutout, selector 0x82, u16 at payload[5]
    0x1FF95: "01C8FFFFFFFFFFFF",     # Max Chrge Rate,   selector 0x92, payload[1]
    0x1FF87: "0155FFFFFFFFFFFF",     # VAC Dropout,      selector 0x86, payload[1]
    0x1FFC4: "01FFFFFFFFFF01FF",     # Battery Type,     selector 0x88, payload[6]
}
CONFIRMED_SELECTOR = {0x1FFD0: 0x82, 0x1FF95: 0x92, 0x1FF87: 0x86, 0x1FFC4: 0x88}


def _confirmed_rules(data):
    """The four rules PC.23 exercised, keyed by DGN -- identified by their selector, not index."""
    out = {}
    for rule in g12rules.command_rules(data):
        want = CONFIRMED_SELECTOR.get(rule.dgn)
        if want is not None and want in rule.selectors.values():
            out.setdefault(rule.dgn, rule)
    return out


def test_all_four_confirmed_rules_are_found(data):
    assert set(_confirmed_rules(data)) == set(OBSERVED)


@pytest.mark.parametrize("dgn", sorted(OBSERVED))
def test_record_reproduces_the_captured_frame(data, dgn):
    """Every byte the rule does NOT substitute must equal what the coach transmitted."""
    rule = _confirmed_rules(data)[dgn]
    wire = OBSERVED[dgn]
    substituted = set(rule.selectors)
    for i in range(8):
        if i in substituted:
            continue
        assert "%02X" % rule.payload[i] == wire[2 * i:2 * i + 2], (
            "payload[%d]: record says %02X, coach sent %s"
            % (i, rule.payload[i], wire[2 * i:2 * i + 2]))


@pytest.mark.parametrize("dgn", sorted(OBSERVED))
def test_substituted_slots_are_where_the_value_actually_landed(data, dgn):
    """The mask must mark exactly the bytes that differed from the FF template on the wire."""
    rule = _confirmed_rules(data)[dgn]
    wire = OBSERVED[dgn]
    on_wire_value = {i for i in range(8)
                     if wire[2 * i:2 * i + 2] != "%02X" % rule.payload[i]}
    assert on_wire_value <= set(rule.selectors), (
        "the coach put a value at %s but the record marks %s"
        % (sorted(on_wire_value), sorted(rule.selectors)))


def test_shore_breaker_size_drives_no_rule(data):
    """PC.23's control: 0x99 stored in the G12 (0 -> 5 -> 0) and relayed NOTHING.

    A decode that found a rule for it would be wrong, so this asserts the negative. It is the
    one prediction the table made that the bus could have falsified.
    """
    assert g12rules.rule_for_selector(data, 0x99) == []


def test_every_confirmed_selector_drives_exactly_one_rule(data):
    for dgn, selector in CONFIRMED_SELECTOR.items():
        rules = g12rules.rule_for_selector(data, selector)
        assert len(rules) == 1, "selector %#04x drives %d rules" % (selector, len(rules))
        assert rules[0].dgn == dgn


def test_every_charger_rule_commands_instance_1(data):
    """The premise behind `set_rule_instance` being the useful edit."""
    for rule in _confirmed_rules(data).values():
        assert rule.instance == 0x01


def test_payload_order_matches_the_panels_binding_layout():
    """The G12 and the panel store their 8 CAN bytes the same way. Not a coincidence to re-derive."""
    from fireflymcf import newbutton
    assert tuple(g12rules.PAYLOAD_ORDER) == tuple(newbutton._PAYLOAD_ORDER)


class TestEdits:
    def test_instance_edit_touches_one_byte_and_one_checksum(self, data):
        rule = _confirmed_rules(data)[0x1FF95]
        out, edits = g12rules.set_rule_instance(data, rule, 0x02)
        assert len(out) == len(data)
        diff = [i for i in range(len(data)) if data[i] != out[i]]
        # the instance byte, plus the G12 section's 2-byte checksum -- nothing else
        assert len(diff) == 3, diff
        assert diff[0] == rule.payload_offset(0)
        assert len(edits) == 1

    def test_instance_edit_round_trips_through_the_decoder(self, data):
        rule = _confirmed_rules(data)[0x1FF95]
        out, _ = g12rules.set_rule_instance(data, rule, 0x07)
        again = next(r for r in g12rules.command_rules(out) if r.index == rule.index)
        assert again.instance == 0x07
        assert again.dgn == rule.dgn
        assert again.selectors == rule.selectors

    def test_instance_ff_is_refused(self, data):
        rule = _confirmed_rules(data)[0x1FF95]
        with pytest.raises(g12rules.G12RuleError, match="RV-C null"):
            g12rules.set_rule_instance(data, rule, 0xFF)

    def test_selector_edit_refuses_an_unmasked_slot(self, data):
        """payload[0] is the instance literal; pointing a selector at it must be refused."""
        rule = _confirmed_rules(data)[0x1FF95]
        assert not (rule.mask >> 0) & 1
        with pytest.raises(g12rules.G12RuleError, match="LITERAL"):
            g12rules.set_rule_selector(data, rule, 0, 0x92)

    def test_selector_edit_moves_the_right_byte(self, data):
        rule = _confirmed_rules(data)[0x1FF87]
        (index,) = rule.selectors
        out, _ = g12rules.set_rule_selector(data, rule, index, 0x92)
        again = next(r for r in g12rules.command_rules(out) if r.index == rule.index)
        assert again.selectors == {index: 0x92}

    def test_dgn_edit_refuses_a_different_high_field(self, data):
        rule = _confirmed_rules(data)[0x1FF95]
        with pytest.raises(g12rules.G12RuleError, match="bits 16-17"):
            g12rules.set_rule_dgn(data, rule, 0x0FF95)

    def test_dgn_edit_is_same_size_and_reseals(self, data):
        rule = _confirmed_rules(data)[0x1FF95]
        out, edits = g12rules.set_rule_dgn(data, rule, 0x1FF97)
        assert len(out) == len(data)
        again = next(r for r in g12rules.command_rules(out) if r.index == rule.index)
        assert again.dgn == 0x1FF97
        assert len(edits) == 1

    def test_a_wrong_premise_is_refused(self, data):
        rule = _confirmed_rules(data)[0x1FF95]
        bogus = g12rules.CommandRule(index=rule.index, offset=rule.offset + 4,
                                     dgn=rule.dgn, mask=rule.mask, raw=rule.raw)
        with pytest.raises(g12rules.G12RuleError):
            g12rules.set_rule_instance(data, bogus, 0x02)

    def test_edited_image_still_passes_reseal_cleanly(self, data):
        rule = _confirmed_rules(data)[0x1FFC4]
        out, _ = g12rules.set_rule_instance(data, rule, 0x03)
        _again, changed = patch.reseal(out)
        assert changed == [], "a resealed image must have no pending checksum"


def test_corpus_images_decode_without_raising():
    """11.23 and 14.7 must not derail the decoder, even where they have no such rules."""
    for path in ("firefly-11.23/mcf.bin", "firefly-14.7 (Terrain)/mcf.bin"):
        with open(path, "rb") as handle:
            blob = handle.read()
        rules = g12rules.command_rules(blob)
        assert isinstance(rules, list)
        for rule in rules:
            assert 0 <= rule.mask <= 0xFF
            assert len(rule.payload) == 8


# --- Is screen 14 reachable at all? ----------------------------------------------------------
#
# The owner asked the right question on 2026-08-24: object 17 may be a leftover. It is. These
# pin the two halves of that, because both are things a future build could silently change --
# attaching object 1052 would give the page a route, and it should be a deliberate act.

def _reachable_objects(blob):
    """Every object in the display graph, walking containers and ALL switch candidates.

    Deliberately permissive: a type-6 candidate is only *drawn* when its switch's value slot
    selects it, so membership here means "in the graph", never "on screen". The claim this
    supports is the negative one -- an object OUTSIDE this set can never be displayed.
    """
    import collections
    from fireflymcf import guidata, records, layout
    gs, _ = guidata.guidata_extent(blob)
    main = guidata.object_tables(blob)[1]
    seen = set()
    stack = list(set(layout.screen_object_map(blob).values()))
    while stack:
        obj = stack.pop()
        if obj in seen or obj >= len(main):
            continue
        seen.add(obj)
        try:
            recs = records.object_records(blob, gs + main[obj])
        except Exception:
            continue
        for r in recs:
            if r.child_index is None:
                continue
            if r.wtype == 6 and r.count:
                stack.extend(range(r.child_index, r.child_index + r.count))
            elif r.wtype in (5, 0x2E):
                stack.append(r.child_index)
    return seen


def _registrars(blob, action_id):
    import struct
    from fireflymcf import guidata, records
    gs, _ = guidata.guidata_extent(blob)
    main = guidata.object_tables(blob)[1]
    out = []
    for obj in range(len(main)):
        try:
            recs = records.object_records(blob, gs + main[obj])
        except Exception:
            continue
        for r in recs:
            if r.wtype == 12:
                raw = blob[r.offset:r.offset + r.size]
                if struct.unpack_from("<H", raw, r.size - 2)[0] == action_id:
                    out.append(obj)
    return out


NAV_TO_SCREEN_14 = 0x7DE


def test_screen_14_is_orphaned_in_stock(data):
    """Nothing that can be displayed registers the id that opens INVERTER / CHARGER SETTINGS.

    GuiCode has exactly ONE store of screen 14 to `state+0x20A` (`0x0024B96C`) and it is guarded
    by action id 0x7DE. Only objects 1052 and 1053 carry that id, and neither is in the display
    graph -- so in stock 13.6 the page cannot be opened by touch. That is what makes v8's hold
    its first route, and why nothing about its runtime behaviour is confirmed.
    """
    registrars = _registrars(data, NAV_TO_SCREEN_14)
    assert registrars == [1052, 1053], registrars
    reachable = _reachable_objects(data)
    assert not (set(registrars) & reachable), (
        "an object registering %#05x became reachable; screen 14 is no longer orphaned "
        "and the v8 write-up needs revisiting" % NAV_TO_SCREEN_14)


def test_three_page_controls_write_selectors_the_g12_never_relays(data):
    """Object 17 wires the NON-relaying twin of three controls; the relaying ones are unattached.

    0x99 (Shore Brkr Size, obj 929), 0xBA (Absorb) and 0xBC (Float) drive no G12 rule -- pressing
    them stores a value the G12 forwards to nobody. Their relaying counterparts, selectors 0x90
    (obj 930) and 0x8E (obj 925), sit in the file unattached to any page.
    """
    for selector in (0x99, 0xBA, 0xBC):
        assert g12rules.rule_for_selector(data, selector) == [], (
            "selector %#04x now drives a rule" % selector)
    for selector in (0x90, 0x8E):
        assert g12rules.rule_for_selector(data, selector), (
            "selector %#04x should drive a rule" % selector)
    reachable = _reachable_objects(data)
    assert 925 not in reachable and 930 not in reachable, (
        "the relaying twins became reachable")
    assert 929 in reachable, "obj 929 (the non-relaying Shore Brkr Size) is on the page"


# --- The registry is not a second mechanism (2026-08-24) --------------------------------------
#
# Chasing table 0x62 record 59 turned up something bigger: the `ce 5f c1` "registry" records are
# ordinary transmit templates. Each is the template for the G12's periodic 0x15FCE broadcast of
# one selector. The oracle is the capture in the repo, so this predicts the wire from the file.

CAPTURE = "captures/pc23-baseline-2026-08-23.log"


def _swept_frames():
    """{selector byte: {8-byte payload hex, ...}} from the G12's own 0x15FCE broadcasts."""
    import re
    import collections
    out = collections.defaultdict(set)
    with open(CAPTURE) as handle:
        for line in handle:
            m = re.search(r"195FCE9C#([0-9A-F]{16})", line)
            if m:
                out[int(m.group(1)[0:2], 16)].add(m.group(1))
    return out


def test_registry_templates_predict_the_sweep(data):
    """Every unmasked byte of a registry template must equal what the coach transmitted."""
    swept = _swept_frames()
    assert len(swept) >= 40, "capture has too few swept selectors to be a useful oracle"
    checked = 0
    for rule in g12rules.sweep_templates(data):
        selector = rule.raw[4]
        frames = swept.get(selector)
        if not frames:
            continue
        payload = rule.payload
        assert any(
            all(int(f[2 * i:2 * i + 2], 16) == payload[i]
                for i in range(8) if not (rule.mask >> i) & 1)
            for f in frames), (
            "selector %#04x: template %s matches none of %s"
            % (selector, payload, sorted(frames)))
        checked += 1
    assert checked >= 40, "only %d registry templates were exercised" % checked


def test_every_substitution_in_a_sweep_template_is_a_u16_pair(data):
    """Registry templates substitute only adjacent same-selector pairs -- never lone bytes."""
    for rule in g12rules.sweep_templates(data):
        assert rule.value_fields, "sweep template %d substitutes nothing" % rule.index
        for _index, width, _sel in rule.value_fields:
            assert width == 2, (
                "sweep template %d has a 1-byte substitution %s"
                % (rule.index, rule.value_fields))


def test_a_sweep_frame_carries_each_named_selectors_live_value(data):
    """The cross-record prediction, and it is the strong one.

    Most templates name only their own selector, which alone would be circular. Registry record
    10 does not: it broadcasts selector 0x01 and packs a SECOND u16 at payload[6..7] naming
    selector **0x03**. So the value in 0x01's frame at [6..7] must equal what 0x03's own frame
    reports at [2..3] -- two different frames, predicted from one record.
    """
    swept = _swept_frames()

    def live(selector):
        frames = swept.get(selector)
        if not frames:
            return None
        vals = {int(f[6:8], 16) << 8 | int(f[4:6], 16) for f in frames}
        return vals.pop() if len(vals) == 1 else None

    cross = checked = 0
    for rule in g12rules.sweep_templates(data):
        own = rule.raw[4]
        frames = swept.get(own)
        if not frames:
            continue
        for index, _width, selector in rule.value_fields:
            expected = live(selector)
            if expected is None:
                continue
            for frame in frames:
                got = (int(frame[2 * index + 2:2 * index + 4], 16) << 8
                       | int(frame[2 * index:2 * index + 2], 16))
                assert got == expected, (
                    "selector %#04x frame payload[%d..%d] = %#06x, but selector %#04x reports "
                    "%#06x" % (own, index, index + 1, got, selector, expected))
            checked += 1
            if selector != own:
                cross += 1
    assert checked >= 40, "only %d fields exercised" % checked
    assert cross >= 1, "the cross-record case (record 10 naming selector 0x03) was not exercised"


def test_max_charge_rate_sweep_value_is_the_one_the_panel_would_show(data):
    """The end-to-end number: sweep u16 200 -> panel transform >>1 -> 100 %."""
    swept = _swept_frames()
    frames = swept[0x92]
    values = {int(f[6:8], 16) << 8 | int(f[4:6], 16) for f in frames}
    assert values == {200}, values
    assert 200 >> 1 == 100


def test_record_59_is_the_only_cf_function_and_has_never_been_seen(data):
    """The record that started the chase: decoded as a template, unexplained as a behaviour.

    It is a 1FED9 group-0x96 template whose function byte is 0xCF, taking the live values of
    option selectors 0xE3 and 0xE4. Function 0xCF occurs in no other record; the G12 has never
    been observed sourcing 1FED9 at all. So the template is decoded and the SEMANTICS ARE NOT --
    this test exists to keep that pair of facts honest, not to claim the rule does anything.
    """
    import glob
    import re
    from fireflymcf import g12_config
    cf = []
    for r in g12_config.rule_records(data):
        if r.raw[:3] == g12rules.REGISTRY_SIG:
            continue
        payload = [r.raw[i] for i in g12rules.PAYLOAD_ORDER]
        if payload[6] == 0xCF:
            cf.append((r.table, r.index, tuple(payload)))
    assert len(cf) == 1, cf
    table, index, payload = cf[0]
    assert (table, index) == (98, 59)
    assert payload == (0xFF, 0x96, 0xE3, 0xFF, 0xE4, 0x00, 0xCF, 0x00)

    # The G12 sourced 1FED9 for the FIRST time in PC.39 (2026-08-25), during an AES auto-start.
    # This assertion used to be `"9C" not in sources`, and it fired correctly. What it caught was
    # not record 59: the two frames are plain `D1` sets of `0x79` and `0x7D`, and function `0xCF`
    # is still absent from every capture. So the record stays unfired and the guard narrows from
    # "the G12 never sources this DGN" to "these two writes are all it has ever sent".
    seen = set()
    for path in glob.glob("captures/*.log"):
        with open(path) as handle:
            for line in handle:
                m = re.search(r"19FED99C#([0-9A-F]{16})", line)
                if m:
                    seen.add(bytes.fromhex(m.group(1)))
    assert seen == {bytes.fromhex("FF96790F0100D1FF"), bytes.fromhex("FF967D0F0200D1FF")}, (
        f"the G12 has sourced a 1FED9 frame this test does not know about: {sorted(seen)}")
    assert not any(frame[6] == 0xCF for frame in seen), (
        "function 0xCF observed -- record 59 has fired; revisit GUICODE Finding 52")


# --- The G12 INPUT pins (`GUICODE.md` Finding 54) --------------------------------------------
#
# Three tables have to agree, and they were read independently: the G12's transmit rules
# (table 0x20), the panel's receive-filter entries, and the DEVICES/FAULTS screen records.
# Fitting any one of them to the others would break the tests below, because each is checked
# against a source the others do not touch.

# GuiCode `FUN_00247BF8` @ 0x24b382-0x24b420: ten identical blocks
#     ldrsh.w r3,[r4,#2*var] / cmp r3,#1 / ite ne / mov.ne 0 / mov.eq 1 / ldr r2,[pool] / strb
# The pool words, in the order the ten `ldr`s reference them. This is the register-level join,
# hardcoded from the disassembly so the pure-GuiData walk in `input_rows` has to reproduce it.
GUICODE_INPUT_REGISTERS = {
    1: 0x2003A14C, 2: 0x2003A0E5, 3: 0x2003A0D5, 4: 0x2003A145, 5: 0x2003A139,
    6: 0x2003A136, 7: 0x2003A13A, 8: 0x2003A12C, 9: 0x2003A129, 10: 0x2003A4E5,
}
# Pins the G12 actually reports on 13.6 -- 5 and 8 are absent, and that is the negative.
BROADCAST_PINS = {1, 2, 3, 4, 6, 7, 9, 10}


def test_every_broadcast_input_has_both_an_on_and_an_off_rule():
    with open(IMAGE, "rb") as handle:
        blob = handle.read()
    rules = g12rules.input_rules(blob)
    assert set(rules) == BROADCAST_PINS
    for pin, pair in rules.items():
        assert set(pair) == {"on", "off"}, f"pin {pin} has only {sorted(pair)}"
        assert pair["on"].payload[1] == 1
        assert pair["off"].payload[1] == 0


def test_input_instance_is_a0_plus_pin(data):
    for st in g12rules.input_status(data):
        assert st.instance == 0xA0 + st.pin
        for rule in (st.on_rule, st.off_rule):
            if rule is not None:
                assert rule.instance == st.instance
                assert rule.dgn == g12rules.INDICATOR_DGN


def test_pins_5_and_8_are_watched_by_the_panel_but_broadcast_by_nobody(data):
    """The falsifiable negative, and it is decisive.

    A decode that merely fitted the screen to the rule table would make these two disappear.
    They do not: the panel allocates receive-filter vars 196 and 199 for instances 0xA5/0xA8,
    the G12 section contains no rule for either, and the screen shows no row for either. Three
    sources, one answer, and the answer is "nothing reports these pins".
    """
    for st in g12rules.input_status(data):
        if st.pin in (5, 8):
            assert st.watched, f"pin {st.pin} should still have a receive-filter var"
            assert not st.broadcast, f"pin {st.pin} unexpectedly has a G12 rule"
            assert st.label is None, f"pin {st.pin} unexpectedly has a screen row"
        else:
            assert st.broadcast and st.watched


def test_the_screen_rows_are_exactly_the_broadcast_pins(data):
    """GuiData and the G12 section agree on which pins exist, having never been compared."""
    rows = set(g12rules.input_rows(data))
    assert rows == BROADCAST_PINS == set(g12rules.input_rules(data))


def test_receive_filter_vars_are_contiguous_and_match_2x_the_guicode_offset(data):
    """var 192..201 for pins 1..10, so `2*var` is the 0x180..0x192 run the write block reads."""
    for st in g12rules.input_status(data):
        assert st.var == 191 + st.pin
        assert 2 * st.var == 0x180 + 2 * (st.pin - 1)


def test_screen_slots_reproduce_the_guicode_register_join(data):
    """The GuiData walk must land on the same registers the GuiCode run stores into.

    This is the test that makes the join evidence. `input_rows` reads captions and value slots
    out of the screen records and never looks at GuiCode; the expected registers come from the
    disassembled pool above. They agree on all eight rows -- including the two nobody would
    guess, pin 2 at slot 242 and pin 10 at slot 132, which break the otherwise tidy 300..307 run.
    """
    from fireflymcf import layout
    for st in g12rules.input_status(data):
        if st.slot is None:
            continue
        register, _type = layout.variable_register(data, st.slot)
        assert register == GUICODE_INPUT_REGISTERS[st.pin], (
            f"pin {st.pin}: slot {st.slot} resolves to {register:#010x}, "
            f"expected {GUICODE_INPUT_REGISTERS[st.pin]:#010x}")


def test_pin_2_and_pin_10_are_off_the_contiguous_run(data):
    """Guards the guess this decode could most easily have been: slot == 299 + pin."""
    slots = {st.pin: st.slot for st in g12rules.input_status(data)}
    assert slots[2] == 242 and slots[10] == 132
    assert slots[1] == 300 and slots[9] == 307


def test_ignition_is_pin_10_and_its_frames_are_predicted(data):
    """The actionable one: this is the frame to watch while turning the key."""
    ignition = next(st for st in g12rules.input_status(data) if st.pin == 10)
    assert "Ignition" in (ignition.label or "")
    active, inactive = ignition.frames()
    assert active == "19FBDA9C#AA01FFFFFFFFFFFF"
    assert inactive == "19FBDA9C#AA00FFFFFFFFFFFF"


def test_table_census_covers_all_four_rule_tables(data):
    """The regression guard for the bug that hid this finding for a day.

    `command_rules` reads table 0x62 only -- correctly, that is where the selector rules are --
    but for a day it was also the ONLY thing any entry point read, so tables 0x20/0x30/0x61 were
    invisible to `cli g12-rules`. If a future refactor narrows the census back down, this fails.
    """
    tags = {tag for tag, _count, _dgns in g12rules.table_census(data)}
    assert tags == set(g12rules.RULE_TABLE_TAGS) == {0x20, 0x30, 0x61, 0x62}
    census = {tag: dgns for tag, _count, dgns in g12rules.table_census(data)}
    assert census[0x20][g12rules.INDICATOR_DGN] == 18      # 9 instances x on/off
    assert g12rules.INDICATOR_DGN not in census[0x62]      # and none in the table 0x62 view


def test_all_rules_is_a_superset_of_the_filtered_views(data):
    every = {(r.table, r.index) for r in g12rules.all_rules(data)}
    for view in (g12rules.command_rules(data), g12rules.sweep_templates(data),
                 g12rules.indicator_rules(data)):
        assert {(r.table, r.index) for r in view} <= every
    assert len(every) == 151


def test_input_status_is_not_silently_empty(data):
    """`all([])` is True and an empty dict passes most assertions -- so check the shape."""
    status = g12rules.input_status(data)
    assert len(status) == 10
    assert sum(1 for st in status if st.broadcast) == 8
    assert sum(1 for st in status if st.label) == 8


def test_indicator_rules_decode_across_the_corpus():
    """11.23 and 14.7 must decode without raising, whatever they happen to contain."""
    for path in ("firefly-11.23/mcf.bin", "firefly-14.7 (Terrain)/mcf.bin"):
        with open(path, "rb") as handle:
            blob = handle.read()
        for rule in g12rules.indicator_rules(blob):
            assert rule.dgn == g12rules.INDICATOR_DGN
            assert len(rule.payload) == 8
        assert isinstance(g12rules.input_status(blob), list)


# --- Finding 55: the ELECTRICAL page's `Engine Running` text ---------------------------------
#
# Same 0x1FBDA namespace as the inputs above, which is how Finding 54's unidentified ninth
# instance (0xFB) got a name. These pin both halves of the owner's question: what drives the
# text, and why it is stale until you leave the screen.

ENGINE_STATE_REGISTER = 0x2003A50C          # display slot 169 -> here, via the dptr table
ENGINE_INDICATOR_VAR = 56                   # 0x1FBDA instance 0xFB, mirrored to 0x20039FD9
BRANCH_VAR = 41                             # 0x15FCE selector 0x01; == 1 picks the generator path


def _guicode_bytes(blob):
    from fireflymcf import theme
    section = theme.guicode_section(blob)
    return blob[section.offset:section.offset + section.length], theme.guicode_base(section)


def test_engine_switch_has_four_candidates_not_two(data):
    """`Starting` and `Stopping` exist too -- they are just unreachable on an AES coach."""
    import struct
    from fireflymcf import guidata, layout, records
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    switch = next(r for r in records.object_records(data, gs + table[353])
                  if r.wtype == 6 and r.value_slot == 169)
    assert switch.child_index == 526 and switch.count == 4
    seen = {}
    for k in range(switch.count):
        obj = switch.child_index + k
        match = struct.unpack_from("<H", data, gs + layout.MATCH_TABLE_136 + 2 * obj)[0]
        label = records.object_records(data, gs + table[obj])[0].label
        seen[match] = label
    assert seen == {0xFFFF: "Not\nRunning", 2: "Starting", 3: "Running", 4: "Stopping"}


def test_engine_state_slot_resolves_to_the_register_the_state_machine_writes(data):
    from fireflymcf import layout
    register, _type = layout.variable_register(data, 169)
    assert register == ENGINE_STATE_REGISTER


def test_nothing_in_guicode_reads_the_engine_state_byte(data):
    """The load-bearing half of the staleness diagnosis, and it is a complete scan.

    One pool literal, and every instruction that loads it goes on to STORE. So the byte's only
    consumer is the GuiData layout walk at compose time -- which is why the text cannot repaint
    in place. If a future build ever adds a reader, this fails and the diagnosis is void.
    """
    import struct
    code, base = _guicode_bytes(data)
    target = struct.pack("<I", ENGINE_STATE_REGISTER)
    pools = [i for i in range(0, len(code) - 3, 4) if code[i:i + 4] == target]
    assert len(pools) == 1, f"expected one pool literal, found {len(pools)}"
    # Every `ldr rN,[pc,#..]` reaching that pool word is followed within 8 bytes by a `strb`
    # (Thumb-2 `strb rX,[rY,#0]` encodes as 0x70.. / 0xF88.. ); checked by disassembly in the
    # finding. Here we assert the weaker, stable property: the literal is referenced, and the
    # register is never the source of a load-and-compare anywhere else in the section.
    assert pools[0] + base == 0x0024A5C8


def test_the_engine_indicator_is_1fbda_instance_fb(data):
    """Closes Finding 54's open item: the ninth table-0x20 pair is not a tenth input pin."""
    import struct
    from fireflymcf import guidata
    entry = guidata.variable_table(data)[ENGINE_INDICATOR_VAR]
    assert (struct.unpack_from("<I", entry.raw, 0)[0] & 0x3FFFF) == g12rules.INDICATOR_DGN
    assert entry.raw[4] == 0xFB
    assert 2 * ENGINE_INDICATOR_VAR == 0x70          # the ldrsh.w at 0x24b422
    # and the G12 has an ON/OFF pair for it, in the same table as the input pins
    pair = [r for r in g12rules.indicator_rules(data) if r.instance == 0xFB]
    assert len(pair) == 2 and {r.payload[1] for r in pair} == {0, 1}
    assert {r.table for r in pair} == {0x20}


def test_the_branch_selector_is_15fce_selector_01(data):
    import struct
    from fireflymcf import guidata
    entry = guidata.variable_table(data)[BRANCH_VAR]
    assert (struct.unpack_from("<I", entry.raw, 0)[0] & 0x3FFFF) == g12rules.CONFIG_STATUS_DGN
    assert entry.raw[4] == 0x01          # matches payload[0] == selector 0x01
    assert (entry.raw[3] & 7) == 2       # extracts payload[2]


def test_no_live_indicator_is_possible_for_the_engine_signal(data):
    """The defensible negative behind "this cannot be fixed by an edit".

    A widget repaints in place only when a GuiCode bridge block writes the colour cell it names,
    and those blocks are straight-line code over a fixed var set -- vars 18-43 and 66-85 on 13.6,
    the pin-status vars. The engine indicator's var is not among them, so there is no colour cell
    to bind either. If that set ever changes, this fails and the negative deserves re-checking.
    """
    from fireflymcf import statusbind
    # Positive control FIRST. A negative from `state_loads` means nothing if the function has
    # quietly started returning empty for everything -- which is exactly the failure mode
    # CLAUDE.md's "check your tests can fail" rule exists for.
    assert _bridged(statusbind, data, 32), "var 32 (a pin-status var) must be bridged"
    assert _bridged(statusbind, data, 70), "var 70 (a pin-status var) must be bridged"
    # The negatives that matter.
    assert not _bridged(statusbind, data, ENGINE_INDICATOR_VAR)
    assert not _bridged(statusbind, data, BRANCH_VAR)
    assert not _bridged(statusbind, data, 169)


def _bridged(statusbind, blob, var):
    try:
        return bool(statusbind.state_loads(blob, var))
    except Exception:                                       # noqa: BLE001
        return False


# --- Finding 54a: table 0x10, the input dispatch table ---------------------------------------
#
# This is what turns "the rules next to an indicator pair probably belong to that pin" from an
# inference into a fact, and it answers "what sets 0xFB".

# pin -> (first rule index, rule count, sense) on 13.6.
DISPATCH_136 = {
    1: (0, 5, "ground"), 2: (5, 2, "ground"), 3: (7, 3, "ground"), 4: (10, 5, "ground"),
    5: (15, 0, "unconfigured"), 6: (15, 5, "12V"), 7: (20, 5, "12V"), 8: (25, 0, "12V"),
    9: (25, 3, "12V"), 10: (28, 4, "12V"),
}


def test_input_dispatch_table_decodes(data):
    got = {e.pin: (e.first_rule, e.rule_count, e.sense) for e in g12rules.input_dispatch(data)}
    assert got == DISPATCH_136


def test_pin_10_fires_both_its_own_instance_and_0xfb(data):
    """The answer to "what sets 0xFB": the ignition sense input, pin 10.

    Its group is FOUR rules -- the 0xAA pair and the 0xFB pair -- and the two pairs are
    byte-identical apart from the instance byte, so they can never disagree.
    """
    group = g12rules.input_group_rules(data, 10)
    assert [r.instance for r in group] == [0xFB, 0xFB, 0xAA, 0xAA]
    assert [r.payload[1] for r in group] == [1, 0, 1, 0]
    for a, b in ((group[0], group[2]), (group[1], group[3])):
        assert [i for i in range(12) if a.raw[i] != b.raw[i]] == [7], "only the instance may differ"
    assert g12rules.instance_source_pin(data, 0xFB) == 10
    assert g12rules.instance_source_pin(data, 0xAA) == 10


def test_every_pin_group_ends_with_its_own_indicator_pair(data):
    """Validates the adjacency grouping Finding 54 originally had to assume."""
    for entry in g12rules.input_dispatch(data):
        group = g12rules.input_group_rules(data, entry.pin)
        if not group:
            continue
        own = g12rules.INPUT_INSTANCE_BASE + entry.pin
        tail = [r for r in group if r.dgn == g12rules.INDICATOR_DGN and r.instance == own]
        assert len(tail) == 2, f"pin {entry.pin} should broadcast exactly one ON/OFF pair"
        assert group[-1].instance == own and group[-2].instance == own


def test_pins_with_no_rules_are_exactly_the_ones_that_broadcast_nothing(data):
    """The negative, now structural: count 0 in the dispatch table, not merely an absence."""
    silent = {e.pin for e in g12rules.input_dispatch(data) if e.rule_count == 0}
    assert silent == {5, 8}
    assert silent == {st.pin for st in g12rules.input_status(data) if not st.broadcast}


def test_sense_bit_agrees_with_the_screen_captions(data):
    """Two images, no shared source: the G12 config byte vs GuiData's authored '(12V)'/'(GND)'."""
    rows = g12rules.input_rows(data)
    checked = 0
    for entry in g12rules.input_dispatch(data):
        label = rows.get(entry.pin, (None, None))[0]
        if not label or "(" not in label:
            continue
        says = "12V" if "(12V)" in label else "ground"
        assert entry.sense == says, f"pin {entry.pin}: table says {entry.sense}, caption {label!r}"
        checked += 1
    assert checked == 6, "expected six captions to carry a sense suffix"


def test_byte3_is_not_read_as_a_substitution_mask_outside_table_0x62(data):
    """Guards the bug this decode nearly shipped: `mask 05` -> a bogus `p[0]<-0xfb`."""
    for rule in g12rules.all_rules(data, tags=(0x20,)):
        assert rule.selectors == {}
        assert rule.phase_mask is not None
    light = g12rules.input_group_rules(data, 1)          # pin 1 is a light switch
    assert [r.phase_mask for r in light[:3]] == [
        ("tap-release",), ("held",), ("release-after-hold",)]
    assert [r.payload[3] for r in light[:3]] == [5, 21, 4]   # toggle, ramp, stop
    for rule in g12rules.command_rules(data):
        assert rule.phase_mask is None


def test_dispatch_bounds_stay_inside_the_rule_table_across_the_corpus():
    for path in (IMAGE, "firefly-11.23/mcf.bin", "firefly-14.7 (Terrain)/mcf.bin"):
        with open(path, "rb") as handle:
            blob = handle.read()
        count = len(g12rules.all_rules(blob, tags=(0x20,)))
        for entry in g12rules.input_dispatch(blob):
            assert entry.first_rule + entry.rule_count <= count, (
                f"{path} pin {entry.pin} runs past table 0x20")


def test_selectors_00_and_04_drive_no_rule(data):
    """Pins the 2026-08-24 retraction in `RVC-GAPS.md` §1.

    Both were briefly published as having a decoded role. The role was an artifact of reading
    table 0x62's substitution mask in tables 0x20/0x30. Counting only proven tables, and
    excluding each selector's own 0x15FCE sweep template, they drive nothing -- and if a future
    change re-widens the mask reading, this fails before the claim can be republished.
    """
    sweep = {r.index for r in g12rules.sweep_templates(data)}
    for selector in (0x00, 0x04):
        real = [r for r in g12rules.all_rules(data, tags=(g12rules.RULE_TABLE_TAG,))
                if selector in set(r.selectors.values()) and r.index not in sweep]
        assert real == [], f"selector {selector:#04x} should drive no rule, got {real}"
    # Positive control: the selectors that DO have rules still do.
    for selector, dgn in ((0x92, 0x1FF95), (0xF1, 0x1FACF), (0xF2, 0x1FACF)):
        real = [r for r in g12rules.all_rules(data, tags=(g12rules.RULE_TABLE_TAG,))
                if selector in set(r.selectors.values()) and r.index not in sweep]
        assert [r.dgn for r in real] == [dgn], f"selector {selector:#04x} lost its rule"


# --- the kind field, table 0x20's held bit, and table 0x61 (2026-09-02) -----------------
CORPUS = {"13.6": IMAGE, "11.23": "firefly-11.23/mcf.bin", "14.7": "firefly-14.7 (Terrain)/mcf.bin"}


def _blob(path):
    with open(path, "rb") as handle:
        return handle.read()


def test_kind_census_is_pinned_to_the_file(data):
    """The raw (table, kind) distribution on 13.6 -- 151 records, nothing inferred.

    Pinned so a change to how `kind` is extracted (or to which tables `all_rules` reads) shows
    up as a diff here before it can reach `KIND_OBSERVED`.
    """
    census = g12rules.kind_census(data)
    assert sum(census.values()) == 151
    assert {k for t, k in census if t == 0x62} == {0x00, 0x08, 0x20, 0x24, 0x28, 0x40, 0x80, 0xC0, 0xCC}
    assert {k for t, k in census if t == 0x61} == {0x14, 0x18, 0x1C, 0x24, 0x28, 0x2C, 0x5C}
    assert {k for t, k in census if t == 0x20} == {0x00, 0x04}
    assert {k for t, k in census if t == 0x30} == {0x00}
    assert census[(0x62, 0xC0)] == 54            # the sweep + 1FACF + record 59
    assert census[(0x62, 0x80)] == 5             # the TANK_STATUS family
    assert census[(0x62, 0x08)] == 4             # the AES-start group
    # every observed kind has an entry, and no entry is a kind the file lacks
    assert {key for key in census} == set(g12rules.KIND_OBSERVED) - {(0x61, 0x48)}


def test_table_0x20_kind_bit2_is_exactly_the_held_phase():
    """Kind `0x04` <=> phase mask carries `held`, on every input rule of 13.6 AND 11.23.

    That is the file-side half of `repeats_while_held`; PC.24's 1 Hz ON-only refresh is the
    wire-side half. A record where the two disagree would break the reading, so this checks all
    57 of them, not the convenient ones.
    """
    checked = 0
    for name in ("13.6", "11.23"):
        blob = _blob(CORPUS[name])
        for rule in g12rules.all_rules(blob, tags=(0x20,)):
            held = "held" in rule.phase_mask
            assert rule.repeats_while_held is held, (name, rule.index, rule.raw.hex())
            checked += 1
    assert checked == 32 + 25
    # and the reading it licenses: ON indicators repeat, OFF indicators do not
    blob = _blob(CORPUS["13.6"])
    for pin, pair in g12rules.input_rules(blob).items():
        assert pair["on"].repeats_while_held is True, pin
        assert pair["off"].repeats_while_held is False, pin
    for rule in g12rules.command_rules(blob):
        assert rule.repeats_while_held is None


# The two frames the coach transmits for the LPG level, captured 2026-08-30
# (`pc-1123-session`) with the same value on both DGNs: 657 of 681 consecutive pairs agree,
# the rest are the 1 Hz phase skew across an 80<->81 flip.
LPG_PRIVATE = "19AAB79C#035064FFFFFFFFFF"       # table 0x61 #16, cell 0xC2 at byte 1
LPG_STANDARD = "19FFB79C#035064FFFFFFFFFF"      # table 0x62 #93, selector 0xC2 at payload[1]


def test_table_0x61_cell_joins_a_proven_0x62_selector(data):
    """The one cross-table prediction that makes `StatusRule.cell` evidence, not a fit.

    Table 0x62's masked-selector mechanism is proven (PC.23). Its record #93 names selector
    `0xC2` at payload[1] of a `1FFB7` instance-3 frame with resolution 0x64. Table 0x61's
    record #16 -- read with the layout this test is about -- names cell `0xC2` at byte 1 of a
    `1AAB7` frame. If `+6` were not the cell, the two would have no reason to carry the same
    number at the same byte, and on the wire they do.
    """
    status = g12rules.status_rules(data)
    assert len(status) == 17
    lpg = status[16]
    assert (lpg.dgn, lpg.cell, lpg.payload_index, lpg.direction) == (0x1AAB7, 0xC2, 1, "transmit")
    private = LPG_PRIVATE.split("#")[1]
    assert private[2:4] == "50"                      # the cell value sits at byte 1
    relay = [r for r in g12rules.all_rules(data, tags=(0x62,))
             if r.dgn == 0x1FFB7 and r.instance == 3]
    assert len(relay) == 1 and relay[0].kind == 0x80
    assert relay[0].selectors == {1: 0xC2}
    standard = LPG_STANDARD.split("#")[1]
    for i in range(8):
        if i == 1:
            assert standard[2:4] == private[2:4]     # same value, both DGNs
            continue
        assert "%02X" % relay[0].payload[i] == standard[2 * i:2 * i + 2]


def test_status_rule_direction_matches_what_the_g12_sources(data):
    """Kind 0x2x records are the 1 Hz broadcasts the coach sends; 0x1x records are not.

    Observed from source 0x9C across 34 captures: `1FFFF`, `1AAFD`, `1FF9C`, `0BFC1`, `1AADC`
    and `1AAB7` every 1.000 s (`1FFFD` never -- gated). Of the 0x1x records' DGNs, `1FBDA`
    instances `0xFE`, `1FF96` instance 1, `1FFFE` and `1FFAB` have never been seen from ANY
    node, and `1FBDA` `0xFB`/`0xA2` are table-0x20's output, not these records'.
    """
    by_dir = {}
    for r in g12rules.status_rules(data):
        by_dir.setdefault(r.direction, set()).add(r.dgn)
    assert by_dir["transmit"] == {0x1FFFF, 0x1AAFD, 0x1FF9C, 0x0BFC1, 0x1AADC, 0x1AAB7, 0x1FFFD}
    assert by_dir["receive"] == {0x1FBDA, 0x1FF96, 0x1FFFE, 0x1FFAB, 0x1AADC, 0x1FECA}
    assert by_dir[None] == {0x1FEDA}                 # kind 0x5C, unobserved


def test_the_ignition_sense_cell_is_fed_from_the_bus_on_every_build():
    """Cell 0x28 = struct +0x50, the awning-interlock edge cell with no writer in the blob.

    13.6 and 11.23 feed it from the G12's own `1FBDA` instance `0xFB` (pin 10 / engine
    running); 14.7 -- a Terrain with ZERO table-0x20 input rules -- feeds the same cell from
    `1FFB8` DIGITAL_INPUT_STATUS instance `0x0A` instead. A build that lost its inputs changed
    exactly the record that sources the cell, and nothing else about it.
    """
    for name, want in (("13.6", (0x1FBDA, 0xFB)), ("11.23", (0x1FBDA, 0xFB)),
                       ("14.7", (0x1FFB8, 0x0A))):
        blob = _blob(CORPUS[name])
        first = g12rules.status_rules(blob)[0]
        assert (first.dgn, first.instance_range[0]) == want, name
        assert first.cell == 0x28 and first.kind == 0x1C and first.direction == "receive", name
        assert first.raw[7:] == bytes.fromhex("ff00000101"), name
    assert g12rules.all_rules(_blob(CORPUS["14.7"]), tags=(0x20,)) == []


def test_status_rules_decode_across_the_corpus():
    for name, count in (("13.6", 17), ("11.23", 14), ("14.7", 14)):
        rules = g12rules.status_rules(_blob(CORPUS[name]))
        assert len(rules) == count, name
        for r in rules:
            assert r.kind == r.raw[2] & 0xFC
            str(r)
