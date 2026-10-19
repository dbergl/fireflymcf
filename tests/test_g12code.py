"""Recursive-descent walk of the G12 code blob (section table `0x67`).

A linear sweep reaches ~20% of the blob and then dies on the first literal pool. These tests pin
the three things that took it to >90%: the blob is Thumb-1, the header carries a third entry
point, and literal pools must be claimed as data rather than decoded.
"""
import pytest

from fireflymcf import g12code

BUILDS = ("mcf_1123", "mcf_136", "mcf_147")


@pytest.fixture(params=BUILDS)
def build(request):
    return request.getfixturevalue(request.param)


def test_every_build_carries_a_code_blob(build):
    body = g12code.blob(build)
    assert len(body) > 2000, f"table 0x67 is only {len(body)} bytes"
    assert int.from_bytes(body[0:2], "little") == 0xE19B, (
        "the blob should open with the same entry branch on every build")


# Thumb-2-ONLY mnemonics. `bl` is deliberately absent: it is a 32-bit encoding that exists in
# Thumb-1 as a halfword pair, so instruction WIDTH does not distinguish the architectures. An
# earlier version of this test asserted "no 32-bit instruction at all" and failed on all three
# builds for exactly that reason -- the 101 wide instructions are every one of them `bl`.
THUMB2_ONLY = {"movw", "movt", "rbit", "clz", "sdiv", "udiv", "tbb", "tbh",
               "it", "ite", "itt", "itte", "bfi", "bfc", "ubfx", "sbfx", "mls",
               "pld", "ldrd", "strd", "rrx"}


def test_the_blob_is_thumb1_not_thumb2(build):
    """No Thumb-2-only encoding anywhere in the recursively-walked code.

    `g12_config` used to call this a "Thumb-2 code blob". Across the full walk the only 32-bit
    instructions are `bl`, and no Thumb-2-exclusive mnemonic appears.

    Do NOT read a core out of that. The G12 advertises `McuDevID 0x04351001` on the bus --
    DEV_ID 0x435, an STM32L4 / Cortex-M4 (`BUS-BASELINE.md` PC.19). The Thumb-1-only encoding
    is a build choice, not a limit of the silicon; this docstring used to say "Cortex-M0-class
    core".
    """
    import capstone

    body = g12code.blob(build)
    a = g12code.analyse(build)
    assert a.starts, "nothing decoded"
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    found = {}
    for addr in sorted(a.starts):
        ins = next(iter(md.disasm(body[addr:addr + 4], addr, count=1)), None)
        if ins and ins.mnemonic.split(".")[0] in THUMB2_ONLY:
            found[ins.mnemonic] = addr
    assert not found, f"Thumb-2-only encoding(s) {found} -- the M0-class reading no longer holds"

    wide = {}
    for addr in sorted(a.wide):
        ins = next(iter(md.disasm(body[addr:addr + 4], addr, count=1)), None)
        if ins:
            wide.setdefault(ins.mnemonic, 0)
            wide[ins.mnemonic] += 1
    assert set(wide) <= {"bl"}, f"unexpected 32-bit encodings besides bl: {wide}"


def test_header_word_8_is_a_thumb_function_pointer(build):
    """`+0x08` is an odd word pointing inside the blob -- a third entry point, not data."""
    import struct
    body = g12code.blob(build)
    ptr = struct.unpack_from("<I", body, 8)[0]
    assert ptr & 1, f"+0x08 = {ptr:#x} has no Thumb bit"
    assert 0 < (ptr & ~1) < len(body), f"+0x08 target {ptr & ~1:#x} is outside the blob"
    assert (ptr & ~1) in g12code.entry_points(body)


def test_recursive_descent_reaches_most_of_the_blob(build):
    """>90%, against ~20% for a linear sweep."""
    a = g12code.analyse(build)
    assert a.coverage > 0.90, (
        f"coverage {a.coverage:.1%} -- recursive descent regressed toward a linear sweep")


def test_the_header_pointer_is_what_unlocks_the_coverage(mcf_136):
    """Dropping the third seed must collapse coverage.

    Without this, `test_recursive_descent_reaches_most_of_the_blob` could pass for the wrong
    reason and the header-pointer finding would be decoration rather than load-bearing.
    """
    body = g12code.blob(mcf_136)
    without = [s for s in g12code.entry_points(body) if s in (0x0C, 0x33A)]
    poor = g12code.analyse(mcf_136, seeds=without)
    rich = g12code.analyse(mcf_136)
    assert poor.coverage < 0.5 < rich.coverage, (
        f"two-seed walk {poor.coverage:.1%}, three-seed {rich.coverage:.1%}")


def test_literal_pools_are_claimed_as_data_not_code(mcf_136):
    """The sentinel word must be marked as a literal, never decoded as an instruction."""
    a = g12code.analyse(mcf_136)
    body = g12code.blob(mcf_136)
    import struct
    sentinels = [off for off in sorted(a.literals)
                 if off % 4 == 0 and off + 4 <= len(body)
                 and struct.unpack_from("<I", body, off)[0] == 0x0000FFFF]
    assert sentinels, "the 0xFFFF 'unset' sentinel was not claimed as a literal"
    for off in sentinels:
        assert off not in a.starts, f"literal at {off:#x} was also decoded as an instruction"


def test_the_data_tail_is_not_decoded_as_code(mcf_136):
    """The RV-C templates and 'G8 Test' at the end must stay unclaimed by the code walk."""
    body = g12code.blob(mcf_136)
    tail = body.index(b"G8 Test")
    a = g12code.analyse(mcf_136)
    assert tail not in a.code, "the 'G8 Test' string was decoded as an instruction"


def test_analysis_reports_a_call_graph(mcf_136):
    a = g12code.analyse(mcf_136)
    assert len(a.functions) > 20, f"only {len(a.functions)} functions found"
    assert a.calls, "no call sites recorded"
    assert a.returns, "no return instructions found"


# --- the frame table -------------------------------------------------------
#
# `docs/G12-CODE.md`: the blob's data tail is a table of 12-byte RV-C transmit records, and the
# awning ignition interlock at `+0x10A8` sends three of them. The strongest available check is
# not a self-consistency one -- it is that the records reproduce frames the coach actually put
# on the wire, so these tests read the captures rather than an expectation typed from the file.

import pathlib

CAPTURES = pathlib.Path(__file__).resolve().parent.parent / "captures"

# Captures in which WE actively drove the bus, rather than observed it. The `D3` read-back
# forces a one-shot `195FCE9C` report for ANY selector (`BUS-BASELINE.md` PC.49), and PC.48/PC.50
# injected indicator frames and set a gate. So a selector appearing in one of these says only
# "we asked", never "the G12 volunteers it" -- which is exactly what the invisibility negatives
# below claim. Any test whose meaning is "the coach never puts X on the wire" must exclude them.
PROBE_CAPTURES = frozenset({
    "pc48-awning-lock-injection-2026-09-03.log",     # injected 1FBDA FB/AA, drove cell 0x28
    "pc49-config-array-readback-2026-09-03.log",     # read-back of all 256 config cells
    "pc50-charger-relay-gate-2026-09-03.log",        # set gate 0x94, nudged BA/BC/BE
})


def observational_logs(caps=CAPTURES):
    """Every capture that records the bus behaving on its own.

    Guarded, because an exclusion list is exactly the kind of change that can silently hollow
    out a negative: if this ever returned nothing (or almost nothing) the "never on the bus"
    assertions below would pass vacuously.
    """
    logs = [p for p in sorted(caps.glob("*.log")) if p.name not in PROBE_CAPTURES]
    if logs:
        assert len(logs) >= 20, (
            f"only {len(logs)} observational captures -- the probe exclusion is over-broad and "
            "the invisibility negatives would pass vacuously")
    return logs


def _capture(name):
    path = CAPTURES / name
    if not path.exists():                       # pragma: no cover - corpus guard
        pytest.skip(f"{name} not present")
    return path.read_text(errors="replace")


def test_every_build_has_a_frame_table(build):
    frames = g12code.frame_table(build)
    assert frames, "no frame table found"
    assert all(f.id_high == 0x19 for f in frames)
    assert all(len(f.payload) == 8 for f in frames)


def test_136_frame_table_is_fourteen_records_at_0x1a0c(mcf_136):
    frames = g12code.frame_table(mcf_136)
    assert [f.offset for f in frames][:1] == [0x1A0C]
    assert len(frames) == 14
    assert [f.dgn for f in frames[:5]] == [0x1FBDA, 0x1FBDA, 0x1FBDA, 0x1FEDB, 0x1FED9]


def test_the_awning_interlock_frames_are_the_ones_the_coach_sent(mcf_136):
    """Records 11/12/13 are PC.27's OFF / LOCK / UNLOCK, byte for byte.

    This is the whole point of the decode: `GUICODE.md` Finding 52 proved no rule record can
    emit command 0x21/0x22, so these frames had to be code-generated, and here is the code's
    copy of them.
    """
    log = _capture("pc27-awning-interlock-2026-08-24.log")
    frames = g12code.frame_table(mcf_136)
    for index, command in ((11, 0x03), (12, 0x21), (13, 0x22)):
        rec = frames[index]
        assert rec.dgn == 0x1FEDB
        assert rec.payload[0] == 0x03, "channel 3, the awning extend"
        assert rec.payload[3] == command, "the command byte is payload[3], not payload[2]"
        assert rec.frame() in log, f"record {index} ({rec.frame()}) is not in PC.27"


def test_the_0xbf_mystery_frames_come_from_this_table(mcf_136):
    """PC.24's undecoded `0xBF` frames are records 0 and 2 -- and record 1 has never been seen.

    The unobserved record is the point: a table that only reproduced frames already in a capture
    would be unfalsifiable. This one predicts a third state nobody has triggered.
    """
    log = _capture("pc24-input-pins-2026-08-24.log")
    frames = g12code.frame_table(mcf_136)
    assert [f.payload[1] for f in frames[:3]] == [1, 2, 0]
    assert all(f.payload[2:] == b"\x00" * 6 for f in frames[:3]), (
        "the all-zero tail is what distinguishes these from the table-0x20 input templates")
    assert frames[0].frame() in log
    assert frames[2].frame() in log
    # Scoped to the G12's own frames on purpose. `BUS-BASELINE.md` PC.37: the PANEL also sources
    # instance 0xBF, including BF 02, so "state 2 has never been observed" is true only of the
    # G12. An earlier version of this comment claimed it corpus-wide, from a grep that had the
    # source address baked in -- a negative scoped to one source address is not a negative.
    assert frames[1].frame() not in log, "the G12 has not been seen sending BF 02"


def test_the_frame_table_is_the_blobs_only_pc_relative_data(mcf_136):
    """All 19 `add rX, pc` sites resolve into the table (or the `G8 Test` string just past it).

    This is why the earlier absolute-pointer and `ADR` scans came back empty, and it is worth a
    test: if a build ever addresses some other blob-internal table this way, that is a new
    structure and it should show up as a failure here rather than be missed.
    """
    body = g12code.blob(mcf_136)
    sites = g12code.add_pc_sites(body)
    assert len(sites) == 19
    start = g12code.frame_table_offset(body)
    assert start == 0x1A0C
    for off, target in sites:
        assert start <= target <= 0x1AB4, (
            f"add-pc at +{off:04x} points to +{target:04x}, outside the frame table")


def test_payloads_are_wire_order_not_the_panels_byte_order(mcf_136):
    """The panel stores payloads `[7,6,5,4,11,10,9,8]`; this table does not.

    Applying the panel's order to record 12 gives a frame the coach never sent, which is the
    cheap way to keep the two conventions from being conflated again.
    """
    log = _capture("pc27-awning-interlock-2026-08-24.log")
    rec = g12code.frame_table(mcf_136)[12]
    reordered = bytes(rec.payload[i] for i in (3, 2, 1, 0, 7, 6, 5, 4))
    assert rec.frame() in log
    assert reordered != rec.payload
    assert f"{rec.can_id():08X}#{reordered.hex().upper()}" not in log


# --- the config-defaults routine, and the selector<->cell mapping ----------

def test_defaults_reproduce_the_documented_table(mcf_136):
    """The fifteen `(offset, default)` pairs `docs/G12-CODE.md` tabulates, from a decoder that
    simulates registers rather than matching fixed instruction sequences."""
    got = {d.offset: d.value for d in g12code.config_defaults(mcf_136)}
    documented = {0x004: 720, 0x00A: 768, 0x00E: 30, 0x010: 30, 0x012: 255,
                  0x016: 1000, 0x018: 60, 0x01A: 1080, 0x01C: 1800, 0x01E: 1800,
                  0x028: 2048, 0x02C: 180, 0x0A0: 255, 0x0E0: 80, 0x0E2: 200}
    for off, val in documented.items():
        assert got.get(off) == val, f"+{off:#05x}: expected {val}, got {got.get(off)}"


def test_every_build_yields_defaults(build):
    d = g12code.config_defaults(build)
    assert len(d) > 40, f"only {len(d)} defaults recovered"
    assert all(0 <= x.value <= 0xFFFF for x in d)
    assert all(x.source in ("movs", "movs+lsls", "literal") for x in d)


def test_literal_sourced_defaults_come_from_claimed_pool_words(mcf_136):
    """A literal default is dropped unless the walk claimed its pool address as data.

    That guard is the fix for the old extraction's implausible entries: they came from a
    pc-relative address that landed in code, and a wrong address yields a number that still
    looks like a value.
    """
    analysis = g12code.analyse(mcf_136)
    for d in g12code.config_defaults(mcf_136, analysis):
        if d.source == "literal":
            assert d.literal is not None
            assert d.value == d.literal & 0xFFFF


def _live_selectors():
    """The G12's own `0x15FCE` sweep from PC.34 -- selector -> value, little-endian."""
    import pathlib, re
    log = pathlib.Path(__file__).resolve().parent.parent / "captures" / "pc34-all-options-2026-08-24.log"
    if not log.exists():                        # pragma: no cover - corpus guard
        pytest.skip("PC.34 capture not present")
    live = {}
    for sel, rest in re.findall(r"195FCE9C#([0-9A-F]{2})00([0-9A-F]{4})", log.read_text()):
        b = bytes.fromhex(rest)
        live.setdefault(int(sel, 16), b[0] | (b[1] << 8))
    return live


def _agreement(defaults, live, offset_of):
    """How many selectors' live values equal the default at `offset_of(selector)`.

    Counted twice: all matches, and matches on values other than 0/1 -- a struct full of small
    flags makes trivial agreement cheap, so the non-trivial count is the one that discriminates.
    """
    both = [(s, offset_of(s)) for s in live if offset_of(s) in defaults]
    exact = [s for s, o in both if defaults[o] == live[s]]
    return len(both), len(exact), len([s for s in exact if live[s] not in (0, 1)])


def test_the_g12_addresses_a_config_value_by_twice_its_selector(mcf_136):
    """`cell offset == 2 * selector`, scored against the G12's own sweep and two controls.

    The claim is not "some values agree" -- a config struct is full of 0s and 1s and will agree
    with almost any mapping. It is that the candidate beats every neighbouring alignment on
    values that are NOT 0 or 1, which no off-by-one mapping does.
    """
    defaults = {d.offset: d.value for d in g12code.config_defaults(mcf_136)}
    live = _live_selectors()

    overlap, exact, nontrivial = _agreement(defaults, live, g12code.config_cell_offset)
    assert overlap >= 15, f"only {overlap} selectors testable"
    assert nontrivial >= 4, f"only {nontrivial} non-trivial agreements"

    for shift in (-6, -4, -2, 2, 4, 6):
        _, _, control = _agreement(defaults, live, lambda s, k=shift: 2 * s + k)
        assert control < nontrivial, (
            f"shift {shift:+d} scores {control} non-trivial matches against the "
            f"candidate's {nontrivial}: the mapping is not distinguishable from noise")


def test_the_aes_start_voltage_cell_holds_the_value_the_panel_shows(mcf_136):
    """Selector 0x31 is AES "Start At"; `GUICODE.md` Finding 43 has the panel's copy at 1020
    (51.0 V at 0.05 V/unit). The G12's cell at 2*0x31 reads the same number on the coach.

    This one is worth its own test because it is a cross-device agreement: a panel-side
    template constant and a G12-side config cell, derived by routes that share no code.
    """
    live = _live_selectors()
    assert g12code.config_cell_offset(0x31) == 0x62
    assert live[0x31] == 1020


def test_option_selectors_are_seeded_as_placeholders(mcf_136):
    """The defaults routine DOES seed the option selectors -- as generic placeholders.

    An earlier reading had them "above the range, so no default" (Findings 19/42). That was
    corrected on the bytes (G12-CODE.md, 2026-09-02): the routine spells those cells with a
    register index (`movs #sel; lsls #1; strh [rB,rM]`), which the old immediate-offset-only
    extractor could not see. The seeds are 0xFFFF-only placeholders the panel's floorplan/mode
    bursts overwrite -- so they are present here, with generic values, not the coach's.
    """
    by_sel = g12code.config_defaults_by_selector(mcf_136)
    assert len(by_sel) == 128, f"13.6 seeds 128 selectors, got {len(by_sel)}"
    # present, with the placeholder values the register-indexed run writes
    for selector, seed in {0xE3: 0, 0xE6: 1, 0xE9: 1, 0xEC: 1, 0xEF: 0,
                           0xF5: 0, 0xF7: 60, 0xD7: 1, 0xD8: 2}.items():
        assert by_sel.get(selector) == seed, (
            f"selector {selector:#04x} seed {by_sel.get(selector)} != {seed}")


def test_the_pc_relative_literal_calculation_agrees_with_the_walk(build):
    """Every `ldr rX,[pc,#N]` in the blob resolves to an address the walk claimed as data.

    This is the property the extractor's literal guard exists to enforce, tested directly
    because **the guard itself never fires**: across all three builds, 0 of 36/84/151
    pc-relative loads land outside the claimed pool. So the guard is defensive dead code on
    this corpus and no mutation of it can be caught -- said here rather than left implied by a
    test that appears to cover it. (An earlier version of this test blinded the walk's
    `literals` set to force the branch; that was confounded, because blinding also zeroes the
    0xFFFF sentinel register, after which nothing arms and no defaults are produced at all.)

    What it does catch is a broken `_pc_literal`: Align(PC,4) + imm with PC = addr + 4.
    """
    import capstone
    body = g12code.blob(build)
    analysis = g12code.analyse(build)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    checked = 0
    for addr in sorted(analysis.starts):
        ins = next(iter(md.disasm(body[addr:addr + 4], addr)), None)
        if ins is None or ins.mnemonic != "ldr":
            continue
        ops = ins.op_str.split(", ")
        if len(ops) < 2 or not ops[1].startswith("[pc"):
            continue
        text = ", ".join(ops[1:])
        imm = int(text.strip("[]").split("#")[-1], 0) if "#" in text else 0
        assert g12code._pc_literal(addr, imm) in analysis.literals, (
            f"+{addr:04x}: pc-relative target is not a claimed pool word")
        checked += 1
    assert checked > 30, f"only {checked} literal loads seen"


def test_the_g12_derives_selector_15_high_byte_from_selector_f7(mcf_136):
    """`[cell 0x15] = ((config[0xF7] + 30) << 8) | (old & 0xFF)`, and the coach agrees.

    The blob at `+0x0DEC` forces `config[0xF7]` to 60, then `+0x0E02` writes selector 0x15's
    cell as `(that + 30) << 8` with the previous low byte preserved. 60 + 30 = 90 = 0x5A, and
    the G12 reports 0x5A00/0x5A01 in every capture -- while the panel only ever *writes*
    0x2801. Two writers, and the code says which one wins.
    """
    import pathlib, re
    caps = pathlib.Path(__file__).resolve().parent.parent / "captures"
    logs = sorted(caps.glob("*.log"))
    if not logs:                                # pragma: no cover - corpus guard
        pytest.skip("no captures present")

    assert g12code.config_cell_offset(0x15) == 0x2A
    assert g12code.config_cell_offset(0xF7) == 494

    panel, g12 = set(), set()
    for log in logs:
        text = log.read_text(errors="replace")
        for v in re.findall(r"19FED99F#FF96150F([0-9A-F]{4})D1EA", text):
            b = bytes.fromhex(v)
            panel.add(b[0] | (b[1] << 8))
        for v in re.findall(r"195FCE9C#1500([0-9A-F]{4})", text):
            b = bytes.fromhex(v)
            g12.add(b[0] | (b[1] << 8))

    assert panel == {0x2801}, f"the panel writes only 0x2801; saw {panel}"
    derived = ((60 + 30) << 8)
    assert {v & 0xFF00 for v in g12} <= {derived, 0x2800}, (
        f"every G12 report should carry the derived high byte 0x5A (or the panel's transient "
        f"0x28); saw {sorted(hex(v) for v in g12)}")
    assert derived in {v & 0xFF00 for v in g12}, "the derived value never appears"


def test_most_of_the_watchdog_regions_cells_are_invisible_on_the_bus(mcf_136):
    """A defensible negative: naming a cell by selector does not make it observable.

    Seven of the eleven cells the `+0x0CAA` region touches are **never reported** in the G12's
    `0x15FCE` sweep, across every capture in the corpus. The sweep is a subset of the config
    array, not a dump of it, so the selector mapping buys traceability for some cells and
    nothing at all for others.
    """
    import re
    logs = observational_logs()
    if not logs:                                # pragma: no cover - corpus guard
        pytest.skip("no captures present")
    reported = set()
    for log in logs:
        for sel, _ in re.findall(r"195FCE9C#([0-9A-F]{2})00([0-9A-F]{4})",
                                 log.read_text(errors="replace")):
            reported.add(int(sel, 16))

    region = {0x50: 0x28, 0x9E: 0x4F, 0xA6: 0x53, 0xE8: 0x74, 0xEC: 0x76, 0xEE: 0x77, 0xF0: 0x78}
    for offset, selector in region.items():
        assert g12code.config_cell_offset(selector) == offset
        assert selector not in reported, (
            f"selector {selector:#04x} (cell +{offset:#05x}) is now on the bus -- this negative "
            f"has expired and the cell can be traced")
    assert 0x75 in reported, "cell +0xEA (block A's status) should be reported"


def test_the_light_dispatch_picks_its_channel_by_FLOORPLAN(mcf_136):
    """`+0x17C0` selects instance 35 or 25 from `config[0xF5]`, the floorplan code.

    The predicate is read out of the blob rather than typed here, so a build that changes the
    floorplan set fails instead of being silently described by a stale constant.
    """
    import capstone
    body = g12code.blob(mcf_136)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    text = {i.address: (i.mnemonic, i.op_str) for i in md.disasm(body[0x17AE:0x17E0], 0x17AE)}

    # r1 = config[0xF5]: movs r3,#0xf5 ; lsls r3,r3,#1 ; ldrh r1,[r0,r3]
    assert text[0x17AE] == ("movs", "r3, #0xf5")
    assert text[0x17B2] == ("lsls", "r3, r3, #1")
    assert g12code.config_cell_offset(0xF5) == 0xF5 * 2 == 0x1EA

    # floorplan in {4,5,6} (unsigned subs+bls) or == 10 -> the instance-35 arm
    assert text[0x17C0] == ("subs", "r2, r1, #4")
    assert text[0x17C4 - 2] == ("uxth", "r2, r2")
    assert text[0x17CE] == ("cmp", "r2, #2")
    assert text[0x17D2] == ("cmp", "r1, #0xa")

    frames = g12code.frame_table(mcf_136)
    assert frames[5].payload[0] == 0x23 and frames[6].payload[0] == 0x19
    assert frames[5].payload[3] == frames[6].payload[3] == 4          # stop
    assert frames[7].payload[0] == 0x23 and frames[8].payload[0] == 0x19
    assert frames[7].payload[3] == frames[8].payload[3] == 5          # toggle
    assert frames[9].payload[0] == 0x23 and frames[10].payload[0] == 0x19
    assert frames[9].payload[3] == frames[10].payload[3] == 0x15      # ramp


def test_selector_f5_is_the_floorplan_code(mcf_136):
    """`0xF5` reads 8 (WD) on this coach, and every value it ever takes is a floorplan code.

    `GUICODE.md` Finding 17: 7=WA, 8=WD, 9=WT. So the dispatch resolves to instance **25** here --
    floorplan 8 is neither in {4,5,6} nor 10.

    Asserted as range-and-mode rather than "exactly two captures vary", which is what an earlier
    version said. That was a census of the corpus at one moment, not a claim about the firmware,
    and it broke on the next capture for two innocent reasons: the owner cycled floorplans again,
    and `0xF5` reads **0 for ~15 s while the panel boots** before the floorplan is pushed.
    """
    import pathlib, re, collections
    caps = pathlib.Path(__file__).resolve().parent.parent / "captures"
    logs = sorted(caps.glob("*.log"))
    if not logs:                                # pragma: no cover - corpus guard
        pytest.skip("no captures present")

    modal_is_wd = 0
    every = collections.Counter()
    for log in logs:
        vals = collections.Counter()
        for sel, rest in re.findall(r"195FCE9C#([0-9A-F]{2})00([0-9A-F]{4})",
                                    log.read_text(errors="replace")):
            if int(sel, 16) == 0xF5:
                b = bytes.fromhex(rest)
                vals[b[0] | (b[1] << 8)] += 1
        if not vals:
            continue
        every.update(vals)
        if vals.most_common(1)[0][0] == 8:
            modal_is_wd += 1

    assert set(every) <= set(range(0, 11)), (
        f"0xF5 should only ever hold a floorplan code (or 0 while booting); saw {sorted(every)}")
    assert modal_is_wd >= 20, f"0xF5 should sit at 8 in most captures, got {modal_is_wd}"
    assert every.most_common(1)[0][0] == 8, "8 (WD) should dominate corpus-wide"

    floorplan = 8
    assert not (4 <= floorplan <= 6 or floorplan == 10), "WD takes the instance-25 arm"


def test_the_g12_derives_selector_15_high_byte_from_selector_f7(mcf_136):
    """`[cell 0x15] = ((config[0xF7] + 30) << 8) | (old & 0xFF)`, and the coach agrees.

    The blob at `+0x0DEC` forces `config[0xF7]` to 60, then `+0x0E02` writes selector 0x15's
    cell as `(that + 30) << 8` with the previous low byte preserved. 60 + 30 = 90 = 0x5A, and
    the G12 reports 0x5A00/0x5A01 in every capture -- while the panel only ever *writes*
    0x2801. Two writers, and the code says which one wins.
    """
    import pathlib, re
    caps = pathlib.Path(__file__).resolve().parent.parent / "captures"
    logs = sorted(caps.glob("*.log"))
    if not logs:                                # pragma: no cover - corpus guard
        pytest.skip("no captures present")

    assert g12code.config_cell_offset(0x15) == 0x2A
    assert g12code.config_cell_offset(0xF7) == 494

    panel, g12 = set(), set()
    for log in logs:
        text = log.read_text(errors="replace")
        for v in re.findall(r"19FED99F#FF96150F([0-9A-F]{4})D1EA", text):
            b = bytes.fromhex(v)
            panel.add(b[0] | (b[1] << 8))
        for v in re.findall(r"195FCE9C#1500([0-9A-F]{4})", text):
            b = bytes.fromhex(v)
            g12.add(b[0] | (b[1] << 8))

    assert panel == {0x2801}, f"the panel writes only 0x2801; saw {panel}"
    derived = ((60 + 30) << 8)
    assert {v & 0xFF00 for v in g12} <= {derived, 0x2800}, (
        f"every G12 report should carry the derived high byte 0x5A (or the panel's transient "
        f"0x28); saw {sorted(hex(v) for v in g12)}")
    assert derived in {v & 0xFF00 for v in g12}, "the derived value never appears"


def test_most_of_the_watchdog_regions_cells_are_invisible_on_the_bus(mcf_136):
    """A defensible negative: naming a cell by selector does not make it observable.

    Seven of the eleven cells the `+0x0CAA` region touches are **never reported** in the G12's
    `0x15FCE` sweep, across every capture in the corpus. The sweep is a subset of the config
    array, not a dump of it, so the selector mapping buys traceability for some cells and
    nothing at all for others.
    """
    import pathlib, re
    logs = observational_logs()
    if not logs:                                # pragma: no cover - corpus guard
        pytest.skip("no captures present")
    reported = set()
    for log in logs:
        for sel, _ in re.findall(r"195FCE9C#([0-9A-F]{2})00([0-9A-F]{4})",
                                 log.read_text(errors="replace")):
            reported.add(int(sel, 16))

    region = {0x50: 0x28, 0x9E: 0x4F, 0xA6: 0x53, 0xE8: 0x74, 0xEC: 0x76, 0xEE: 0x77, 0xF0: 0x78}
    for offset, selector in region.items():
        assert g12code.config_cell_offset(selector) == offset
        assert selector not in reported, (
            f"selector {selector:#04x} (cell +{offset:#05x}) is now on the bus -- this negative "
            f"has expired and the cell can be traced")
    assert 0x75 in reported, "cell +0xEA (block A's status) should be reported"


def test_the_light_dispatch_picks_its_channel_by_FLOORPLAN(mcf_136):
    """`+0x17C0` selects instance 35 or 25 from `config[0xF5]`, the floorplan code.

    The predicate is read out of the blob rather than typed here, so a build that changes the
    floorplan set fails instead of being silently described by a stale constant.
    """
    import capstone
    body = g12code.blob(mcf_136)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    text = {i.address: (i.mnemonic, i.op_str) for i in md.disasm(body[0x17AE:0x17E0], 0x17AE)}

    # r1 = config[0xF5]: movs r3,#0xf5 ; lsls r3,r3,#1 ; ldrh r1,[r0,r3]
    assert text[0x17AE] == ("movs", "r3, #0xf5")
    assert text[0x17B2] == ("lsls", "r3, r3, #1")
    assert g12code.config_cell_offset(0xF5) == 0xF5 * 2 == 0x1EA

    # floorplan in {4,5,6} (unsigned subs+bls) or == 10 -> the instance-35 arm
    assert text[0x17C0] == ("subs", "r2, r1, #4")
    assert text[0x17C4 - 2] == ("uxth", "r2, r2")
    assert text[0x17CE] == ("cmp", "r2, #2")
    assert text[0x17D2] == ("cmp", "r1, #0xa")

    frames = g12code.frame_table(mcf_136)
    assert frames[5].payload[0] == 0x23 and frames[6].payload[0] == 0x19
    assert frames[5].payload[3] == frames[6].payload[3] == 4          # stop
    assert frames[7].payload[0] == 0x23 and frames[8].payload[0] == 0x19
    assert frames[7].payload[3] == frames[8].payload[3] == 5          # toggle
    assert frames[9].payload[0] == 0x23 and frames[10].payload[0] == 0x19
    assert frames[9].payload[3] == frames[10].payload[3] == 0x15      # ramp


def test_the_defaults_of_cells_0x69_and_0x6a_satisfy_the_code_that_relates_them(mcf_136):
    """`+0x0FAA` computes `[cell 0x69] = config[0x6A] - 54`, and the defaults are 54 apart.

    Two independent extractions agreeing -- the disassembly's constant and the defaults
    routine's literals -- on values that were once written off as an extraction error.
    """
    d = {x.offset: x.value for x in g12code.config_defaults(mcf_136)}
    assert d[g12code.config_cell_offset(0x6A)] - 54 == d[g12code.config_cell_offset(0x69)]


def test_the_floorplan_light_dispatch_is_gated_off_at_the_config_default(mcf_136):
    """Its only caller gates on `config[0x68]`'s second nibble being 1; the default is 0.

    Worth a test because the floorplan finding reads as unconditional until you look at the
    caller: the channel selection resolves to 25 for floorplan 8 *whenever the dispatch runs*,
    and at the shipped default it does not run at all.
    """
    import capstone
    body = g12code.blob(mcf_136)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    text = {i.address: (i.mnemonic, i.op_str) for i in md.disasm(body[0x0FB4:0x0FE0], 0x0FB4)}
    assert text[0x0FB6] == ("adds", "r2, #0xd0")          # config[0x68]
    assert text[0x0FD4] == ("cmp", "r1, #1")              # nibble1 == 1
    assert text[0x0FD8] == ("cmp", "r2, #0xc8")           # high byte != 200

    default = {x.offset: x.value for x in g12code.config_defaults(mcf_136)}[0xD0]
    assert (default >> 4) & 0xF != 1, "at the default the dispatch is gated OFF"


def test_the_sweep_covers_only_part_of_the_config_array(mcf_136):
    """75 of the 99 defaulted selectors are never reported by the G12, corpus-wide.

    The bound matters: naming a cell by selector is a decoding aid, not a way to observe it.
    """
    import pathlib, re
    logs = observational_logs()
    if not logs:                                # pragma: no cover - corpus guard
        pytest.skip("no captures present")
    reported = set()
    for log in logs:
        for sel, _ in re.findall(r"195FCE9C#([0-9A-F]{2})00([0-9A-F]{4})",
                                 log.read_text(errors="replace")):
            reported.add(int(sel, 16))
    assert 0x2F in reported and 0x31 in reported, (
        "the scan found no routinely-swept selector -- it is broken, so the negative below "
        "would be vacuous")
    defaulted = set(g12code.config_defaults_by_selector(mcf_136))
    assert len(defaulted - reported) > len(defaulted & reported), (
        "most defaulted selectors should be invisible on the bus")
    for cell_selector in (0x5E, 0x5F, 0x60, 0x68, 0x69, 0x6A, 0x6B):
        assert cell_selector not in reported


def test_the_bf_frames_are_a_start_and_a_timed_end(mcf_136):
    """Records 0/1/2 are `BF 01` / `BF 02` / `BF 00`, and PC.24 shows one start and one end.

    The frames are triggered from config cell 0x7D and ended by a timer expiry, so `BF 02`
    never having been seen is a statement about the trigger value, not about capture coverage.
    """
    import pathlib
    frames = g12code.frame_table(mcf_136)
    assert [f.dgn for f in frames[:3]] == [0x1FBDA] * 3
    assert [f.payload[0] for f in frames[:3]] == [0xBF] * 3
    assert [f.payload[1] for f in frames[:3]] == [1, 2, 0]

    log = pathlib.Path(__file__).resolve().parent.parent / "captures" / "pc24-input-pins-2026-08-24.log"
    if not log.exists():                        # pragma: no cover - corpus guard
        pytest.skip("PC.24 not present")
    text = log.read_text(errors="replace")
    assert text.count(frames[0].frame()) == 1, "exactly one start"
    assert text.count(frames[2].frame()) == 1, "exactly one end"
    assert frames[1].frame() not in text, "BF 02 has never been observed"


def test_the_bf_trigger_cell_is_never_written_non_zero_in_the_blob(mcf_136):
    """Only three sites touch cell 0x7D: the defaults routine, the read, and the clear.

    The consequence is the ceiling this whole dig keeps hitting -- the trigger is set by the G12
    application at 0x08006000, which the MCF does not contain.
    """
    import capstone
    body = g12code.blob(mcf_136)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    assert g12code.config_cell_offset(0x7D) == 0xFA
    sites = []
    for off in range(0, len(body) - 1, 2):
        hw = int.from_bytes(body[off:off + 2], "little")
        if (hw & 0xF800) == 0x3000 and (hw & 0xFF) == 0xFA:   # adds rX, #0xfa
            sites.append(off)
    assert sites == [0x0446, 0x0AF0, 0x0B28], f"unexpected access sites: {[hex(s) for s in sites]}"


def test_the_unreached_block_reads_three_user_adjustable_settings(mcf_136):
    """`+0x1858`/`+0x1878`/`+0x1948` read config 0xCC/0xCD/0xCE, and the panel can change them.

    Which is why "dead code the generator emitted" is a poor reading of a block the walk cannot
    reach: GuiCode carries +1000 / read-back / -1000 / restore-to-default templates for each,
    and the owner exercised all three on the bus in PC.8.
    """
    import capstone, pathlib, re
    body = g12code.blob(mcf_136)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    for addr, selector in ((0x1858, 0xCC), (0x1878, 0xCD), (0x1948, 0xCE)):
        ins = list(md.disasm(body[addr:addr + 6], addr))
        assert (ins[0].mnemonic, ins[0].op_str) == ("movs", f"r0, #{selector:#x}")
        assert (ins[1].mnemonic, ins[1].op_str) == ("lsls", "r0, r0, #1")
        assert g12code.config_cell_offset(selector) == selector * 2

    # the panel's own templates: +1000, read-back, -1000, and a restore-to-default
    defaults = {0xCC: 54000, 0xCD: 40000, 0xCE: 25000}
    for selector, default in defaults.items():
        head = bytes([0xD9, 0xFE, 0x19, 0xFF, 0x96, selector, 0x0F])
        assert mcf_136.find(head + b"\xe8\x03\xd2\xea") != -1, "no +1000 template"
        assert mcf_136.find(head + b"\x18\xfc\xd2\xea") != -1, "no -1000 template"
        assert mcf_136.find(head + default.to_bytes(2, "little") + b"\xd1\xea") != -1, (
            f"no restore-to-default template carrying {default}")

    log = pathlib.Path(__file__).resolve().parent.parent / "captures" / "pc8-lighting-2026-08-12.log"
    if not log.exists():                        # pragma: no cover - corpus guard
        pytest.skip("PC.8 not present")
    text = log.read_text(errors="replace")
    for selector, default in defaults.items():
        seen = {int.from_bytes(bytes.fromhex(v), "little")
                for v in re.findall(rf"195FCE9C#{selector:02X}00([0-9A-F]{{4}})", text)}
        assert seen == {default, default - 1000}, (
            f"selector {selector:#04x} should show the default and one step down; saw {seen}")


# `BUS-BASELINE.md` PC.35: 0xCC/0xCD/0xCE are the tank level thresholds the panel's
# PROBE TANK DIAGNOSTICS screen edits -- raw 16-bit ADC counts, higher = emptier.
TANK_THRESHOLDS = {0xCC: (54000, "0%/33%"), 0xCD: (40000, "33%/67%"), 0xCE: (25000, "67%/100%")}


def test_the_tank_thresholds_descend_and_the_block_reads_them_in_order(mcf_136):
    """The ladder is monotonically decreasing, which is what "higher ADC = emptier" requires.

    A ladder that did not descend would bucket every reading into one band, so this is the
    cheapest structural check that the three selectors were matched to the right boundaries.
    """
    values = [TANK_THRESHOLDS[s][0] for s in (0xCC, 0xCD, 0xCE)]
    assert values == sorted(values, reverse=True)
    assert [g12code.config_cell_offset(s) for s in (0xCC, 0xCD, 0xCE)] == [0x198, 0x19A, 0x19C]


def test_the_panel_carries_a_reset_to_default_for_each_threshold(mcf_136):
    """`Reset Default Thresholds` is three absolute `D1 EA` sets carrying the PC.35 defaults."""
    for selector, (default, _) in TANK_THRESHOLDS.items():
        head = bytes([0xD9, 0xFE, 0x19, 0xFF, 0x96, selector, 0x0F])
        assert mcf_136.find(head + default.to_bytes(2, "little") + b"\xd1\xea") != -1


def test_the_thresholds_predict_the_gauges_on_the_coach(mcf_136):
    """PC.35's photo: four raw ADC values, all above the top threshold, all four gauges at 0%.

    This is the end-to-end check -- raw reading, bucketing ladder, displayed percentage -- and
    it is what turned three anonymous numbers into an identification.
    """
    raw = {"FRESH": 65520, "GREY": 65515, "GREY2": 57412, "BLACK": 65520}

    def bucket(adc):
        if adc > TANK_THRESHOLDS[0xCC][0]:
            return 0
        if adc > TANK_THRESHOLDS[0xCD][0]:
            return 33
        if adc > TANK_THRESHOLDS[0xCE][0]:
            return 67
        return 100

    assert {name: bucket(v) for name, v in raw.items()} == dict.fromkeys(raw, 0)
    # and the ladder is not degenerate: a reading in each band buckets differently
    assert [bucket(a) for a in (60000, 50000, 30000, 10000)] == [0, 33, 67, 100]


def test_an_adjust_control_emits_no_config_buffer_frame(mcf_136):
    """PC.35: the six threshold presses produced `0x1FED9` writes and **no** `0x1EE00`.

    PC.34's offset<->selector pairing is specific to OPTION rows. Pinned so nobody repeats that
    technique on an adjust control and reads the absence as the mechanism having changed.
    """
    import pathlib, re
    log = (pathlib.Path(__file__).resolve().parent.parent / "captures"
           / "pc35-tank-thresholds-2026-08-24.log")
    if not log.exists():                        # pragma: no cover - corpus guard
        pytest.skip("PC.35 not present")
    text = log.read_text(errors="replace")
    adjusts = re.findall(r"19FED99F#FF96CC0F([0-9A-F]{4})D2EA", text)
    assert adjusts.count("E803") == 3 and adjusts.count("18FC") == 3, adjusts
    assert "19EE009F" not in text, "an adjust control should emit no config-buffer frame"

    seen = [int.from_bytes(bytes.fromhex(v), "little")
            for v in re.findall(r"195FCE9C#CC00([0-9A-F]{4})", text)]
    assert max(seen) == 57000 and seen[0] == seen[-1] == 54000, (
        "the run should walk up 3000 and return to the default")


def test_service_mode_writes_an_offset_with_no_selector(mcf_136):
    """PC.36: Service Mode is the third config-write shape -- offset only, no selector.

    Three classes now: an OPTIONS row writes both, a +/- adjust control writes the selector
    only, Service Mode writes the offset only. The negative is the load-bearing part -- offset
    0x12 has no selector, so `config.selectors_by_offset` omitting it is a complete answer
    rather than a gap in the extractor.
    """
    import pathlib, re
    from fireflymcf import config
    log = (pathlib.Path(__file__).resolve().parent.parent / "captures"
           / "pc36-service-mode-2026-08-24.log")
    if not log.exists():                        # pragma: no cover - corpus guard
        pytest.skip("PC.36 not present")
    text = log.read_text(errors="replace")

    offsets = [(float(t), int(v, 16)) for t, v in
               re.findall(r"\((\d+\.\d+)\) can1 19EE009F#1200(0[0-9A-F])", text)]
    assert [v for _, v in offsets] == [1, 0, 1, 0], "two enable/disable cycles"

    # Scoped to the toggles on purpose: the capture runs on and picks up unrelated 1FED9 traffic
    # a minute later. A first version of this asserted the whole file was free of 1FED9 and
    # failed, which is how the same overreach got caught in the write-up.
    selector_writes = [float(t) for t in
                       re.findall(r"\((\d+\.\d+)\) can1 19FED99F#", text)]
    for when, _ in offsets:
        assert not [t for t in selector_writes if abs(t - when) < 2.0], (
            "no selector write should accompany a Service Mode toggle")
    assert 0x12 not in config.selectors_by_offset(mcf_136), (
        "the extractor should have no selector for offset 0x12 -- that is the correct answer")


def test_service_mode_broadcasts_a_panel_sourced_indicator(mcf_136):
    """`1FBDA` instance 0xB0, panel-sourced, 1 = enabled -- an instance new to the corpus.

    Also the reason Finding 54's "instance 0xA0+N means input pin N" needs its source qualified:
    that holds for the G12 (SA 0x9C); the panel sources its own family on the same DGN.
    """
    import pathlib, re
    caps = pathlib.Path(__file__).resolve().parent.parent / "captures"
    log = caps / "pc36-service-mode-2026-08-24.log"
    if not log.exists():                        # pragma: no cover - corpus guard
        pytest.skip("PC.36 not present")
    text = log.read_text(errors="replace")
    states = re.findall(r"19FBDA9F#B0(0[01])", text)
    assert [int(s, 16) for s in states] == [1, 0, 1, 0]

    # Captures where Service Mode is DOCUMENTED as toggled carry the indicator too:
    # PC.40's session log holds the 2026-08-30 disable pair (`19FBDA9F#B000...` at
    # 11:21:24) after 11.23 left the flag set. "New to the corpus" was a claim about
    # the 2026-08-24 corpus; it stays pinned for every capture NOT documented to
    # contain a toggle.
    documented_toggles = {log.name, "pc-1123-session-2026-08-30.log"}
    for other in sorted(caps.glob("*.log")):
        if other.name in documented_toggles:
            continue
        assert "19FBDA9F#B0" not in other.read_text(errors="replace"), (
            f"instance 0xB0 was believed new to the corpus but appears in {other.name}")


def test_the_electrical_reset_to_defaults_burst_is_a_stored_template_run(mcf_136):
    """PC.36: the ELECTRICAL page's `Reset to Defaults` emits eight absolute sets, all stored.

    The stride is the tell -- 24 bytes, a 12-byte `D1 EA` set followed by its 12-byte `D3 FF`
    read-back, which is why every set on the wire is followed by a read-back. That makes the
    eight values the profile's factory defaults rather than whatever the panel happened to hold.
    """
    burst = [(0x2B, 5632), (0x2C, 2048), (0x31, 1020), (0x0C, 60),
             (0x0D, 1060), (0x0E, 1800), (0x0F, 1800), (0x16, 115)]
    at = []
    for selector, value in burst:
        template = (bytes([0xD9, 0xFE, 0x19, 0xFF, 0x96, selector, 0x0F])
                    + value.to_bytes(2, "little") + b"\xd1\xea")
        found = mcf_136.find(template)
        assert found != -1, f"no stored template for {selector:#04x} := {value}"
        at.append(found)
    assert at == sorted(at), "the run should be in emission order"
    assert {b - a for a, b in zip(at, at[1:])} == {24}, "a regular 24-byte stride"

    # GuiCode file base 0xF880 maps to address 0x240000 (checked against CONTROL-BEHAVIOR's
    # cited `ldrb r1,[r3,#0x12]` @0x00247C72). Finding 43 cites the 0x31 template at 0x2599D0,
    # one byte before the DGN, where the record's leading flags byte sits.
    address = 0x240000 + (at[2] - 1 - 0xF880)
    assert address == 0x002599D0, f"the 0x31 template should be Finding 43's, got {address:#x}"


def test_the_ags_limits_are_the_constants_the_blob_compares_against(mcf_136):
    """`RVC-GAPS.md` §3's missing limits, read back out of the code that enforces them.

    Each pair is built two ways in the blob -- the high bound as a `movs`+`lsls` immediate, the
    low bound as either a small immediate or a literal-pool word -- so this checks the numbers
    against the bytes rather than restating a table.
    """
    import capstone
    body = g12code.blob(mcf_136)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    text = {i.address: (i.mnemonic, i.op_str)
            for i in md.disasm(body[0x0A00:0x0AC0], 0x0A00)}

    # start-at: movs r1,#0x87 ; lsls r1,r1,#3  -> 1080, against cell +0x62
    assert text[0x0A2A] == ("movs", "r1, #0x87")
    assert text[0x0A2E] == ("lsls", "r1, r1, #3")
    assert 0x87 << 3 == g12code.AGS_LIMITS[0x31][1] == 1080
    assert text[0x0A30] == ("adds", "r3, #0x62")
    assert g12code.config_cell_offset(0x31) == 0x62

    # stop-at: movs r1,#0x89 ; lsls r1,r1,#3 -> 1096, against cell +0x1A
    assert text[0x0A46] == ("movs", "r1, #0x89")
    assert 0x89 << 3 == g12code.AGS_LIMITS[0x0D][1] == 1096

    # time@stop: movs r1,#0xe1 ; lsls r1,r1,#4 -> 3600
    assert text[0x0A5E] == ("movs", "r1, #0xe1")
    assert text[0x0A62] == ("lsls", "r1, r1, #4")
    assert 0xE1 << 4 == g12code.AGS_LIMITS[0x0E][1] == 3600

    # max run: cmp r2,#0x73 -> 115
    assert text[0x0AAE] == ("cmp", "r2, #0x73")
    assert 0x73 == g12code.AGS_LIMITS[0x16][1] == 115

    # the low bounds come from the pool, as `<= n` then store n+1
    for site, imm, selector in ((0x1266, 0x140, 0x31), (0x1278, 0x130, 0x0D), (0x128C, 0x120, 0x0E)):
        at = ((site + 4) & ~3) + imm
        word = int.from_bytes(body[at:at + 4], "little")
        assert word + 1 == g12code.AGS_LIMITS[selector][0], (
            f"selector {selector:#04x}: pool says <= {word}, table says min "
            f"{g12code.AGS_LIMITS[selector][0]}")


def test_the_g12_requires_separation_between_the_two_setpoints(mcf_136):
    """`+0x0A76`: `stop_at >= start_at + 8`, else `stop_at = start_at + 10`.

    A relational rule, which is why no table of bounds could have expressed it -- and why
    searching for a min/max *structure* was never going to find these.
    """
    import capstone
    body = g12code.blob(mcf_136)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    text = {i.address: (i.mnemonic, i.op_str)
            for i in md.disasm(body[0x0A76:0x0AA0], 0x0A76)}
    assert text[0x0A7A] == ("adds", "r0, #0x62")      # start-at cell
    assert text[0x0A7E] == ("ldrh", "r2, [r1, #0x1a]")  # stop-at cell
    assert text[0x0A82] == ("adds", "r5, #8")
    assert g12code.AGS_SETPOINT_GAP == 8
    assert text[0x0A84] == ("cmp", "r5, r2")
    assert text[0x0A9A] == ("adds", "r3, #0xa")       # forced stop = start + 10
    assert text[0x0A9C] == ("strh", "r3, [r1, #0x1a]")


def test_the_clamp_helper_matches_the_coachs_accepted_values(mcf_136):
    """Values the owner actually set and the G12 kept must survive the clamp unchanged.

    A clamp model that rejected observed-accepted values would be wrong, and this is the cheap
    check against restating the bounds backwards.
    """
    for selector, accepted in ((0x31, 1054), (0x31, 1066), (0x31, 1020),
                               (0x0D, 1060), (0x0D, 1076), (0x0C, 30), (0x0C, 300)):
        assert g12code.clamp_ags_parameter(selector, accepted) == accepted
    assert g12code.clamp_ags_parameter(0x31, 2000) == 1080
    assert g12code.clamp_ags_parameter(0x0C, 5) == 10


# --- what arms the automatic engine start ----------------------------------


def test_the_start_arming_block_decodes_to_its_selectors(mcf_136):
    """`+0x18FE`: the tail of entry `+0x0918`, decoded from operands rather than assumed.

    Two stores into cells nothing in this blob reads, so their consumer is the G12 application:

        config[0x0A] = config[0xEF] in {2,3} ? config[0x55] : config[0x56]
        config[0x0B] = config[0x2F] ? config[0x31] : 0
    """
    arming = g12code.start_arming(mcf_136)
    assert arming is not None
    assert arming.offset == 0x18FE
    assert arming.mode_selector == 0xEF              # the AGS/AES mode register
    assert (arming.mode_low, arming.mode_span) == (2, 1)   # AES and AES Single Output
    assert arming.aes_source == 0x55
    assert arming.measured == 0x0A                   # the live house-battery voltage
    assert arming.arming == 0x2F
    assert arming.threshold_source == 0x31           # AES "Start At"
    assert arming.threshold == 0x0B


def test_the_start_arming_block_matches_its_own_bytes(mcf_136):
    """A byte-level cross-check of the decode, independent of capstone's operand strings."""
    body = g12code.blob(mcf_136)
    assert body[0x18FE:0x192A].hex() == (
        "ef22236852009a5a023a012a51d81a00aa3212889a821a005e32128800"
        "2a02d01a0062321288da8209b0f0bd")


def test_only_136_carries_the_mode_gated_arming_block(mcf_1123, mcf_147):
    """A defensible negative: 11.23 and 14.7 reach the same two cells by other routes.

    Reporting the 13.6 shape for them would be a guess. 11.23 predates AES entirely, and its
    equivalent (`+0x08F0`) is not gated on the mode at all.
    """
    assert g12code.start_arming(mcf_1123) is None
    assert g12code.start_arming(mcf_147) is None


def test_the_low_volts_gate_is_in_every_build(build):
    """`config[0x0B] = config[0x2F] ? config[0x31] : 0` -- the defect-0c gate -- is the AGS
    watchdog, not AES: it is present with the SAME selectors in all three builds (11.23 predates
    AES). This is the invariant `low_volts_gate` decodes, where `start_arming` (the 13.6-only AES
    mode-gated re-point) does not generalise."""
    gate = g12code.low_volts_gate(build)
    assert gate is not None
    assert (gate.arming, gate.threshold_source, gate.threshold) == (0x2F, 0x31, 0x0B)


def test_the_low_volts_gate_offsets_are_the_decoded_sites(mcf_1123, mcf_136, mcf_147):
    """Offsets pin the exact store per build (the block moved as the blob grew)."""
    assert g12code.low_volts_gate(mcf_1123).offset == 0x08FE
    assert g12code.low_volts_gate(mcf_136).offset == 0x1924
    assert g12code.low_volts_gate(mcf_147).offset == 0x1D96


def test_only_136_re_points_the_measured_voltage_to_the_aes_source(mcf_1123, mcf_136, mcf_147):
    """The AES-NEW part is the `measured <- config[0x55]` re-point, not the low-volts gate. Only
    13.6 carries it (via `start_arming`); 11.23 never had it and 14.7 dropped it for a per-channel
    loop -- so the low-volts gate generalises but the measured re-point does not."""
    assert g12code.start_arming(mcf_136).aes_source == 0x55
    assert g12code.start_arming(mcf_1123) is None
    assert g12code.start_arming(mcf_147) is None


PC39 = "captures/pc39-low-volts-trigger-2026-08-25.log"


def test_the_arming_selector_was_zero_until_it_was_deliberately_set():
    """Hardware evidence, and the before/after that closed defect 0c.

    `0x2F` read 0 in all 28 captures up to 2026-08-24 while its own sentinel default in the blob
    is 1 -- so 0 was a stored value, not an unset one. PC.39 set it to 1 and the auto-start that
    had never fired, fired. The "always zero" form of this test was correct when written and is
    kept as the historical half: a non-zero sample in any earlier capture would still refute it.
    """
    import glob
    import re

    def sweep(path):
        out = []
        with open(path, errors="ignore") as handle:
            for line in handle:
                match = re.search(r"195FCE[0-9A-F]{2}#([0-9A-F]{16})", line)
                if match:
                    payload = bytes.fromhex(match.group(1))
                    if payload[0] == 0x2F:
                        out.append(payload[2] | (payload[3] << 8))
        return out

    # "prior" means prior in TIME, not merely "not PC39": captures taken after PC.39
    # legitimately show 2F = 1 (the owner leaves Low Volts armed -- e.g. the 2026-08-30
    # PC.40 logs), so scope the always-zero claim to captures dated before PC.39's
    # 2026-08-25, which is the claim the docstring actually makes.
    def capture_date(path):
        match = re.search(r"(2026-\d\d-\d\d)", path)
        return match.group(1) if match else "9999-99-99"

    prior = [v for path in sorted(glob.glob("captures/*.log"))
             if path != PC39 and capture_date(path) < "2026-08-25"
             for v in sweep(path)]
    assert len(prior) > 1000, f"only {len(prior)} samples before PC.39"
    assert set(prior) == {0}, f"selector 0x2F was not always zero before PC.39: {sorted(set(prior))}"
    assert set(sweep(PC39)) == {0, 1}, "PC.39 should show the flag being turned on"


def test_setting_the_arming_flag_produced_a_g12_sourced_start():
    """PC.39: the auto-start command comes from the G12 (`9C`), not the panel (`9F`).

    Every channel-18 ON in this corpus before PC.39 was `19FEDB9F` -- the `Start` button's
    GuiData binding. Source address is the whole distinction, so it is what this asserts.
    """
    import re

    start = None
    with open(PC39, errors="ignore") as handle:
        for line in handle:
            match = re.search(r"\((\d+\.\d+)\) can1 19FEDB(9[CF])#12FF([0-9A-F]{2})01", line)
            if match:
                start = match.groups()
                break
    assert start is not None, "no channel-18 ON in PC.39"
    when, source, level = start
    assert source == "9C", f"the start came from {source}, not the G12"
    assert int(level, 16) == 200


def test_the_blob_defaults_the_arming_selector_to_one(mcf_136):
    """`+0x0182`: the 0xFFFF-sentinel routine writes 1, which is what makes 0 a stored value."""
    import capstone
    body = g12code.blob(mcf_136)
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    text = {i.address: (i.mnemonic, i.op_str)
            for i in md.disasm(body[0x0180:0x0190], 0x0180)}
    assert text[0x0182] == ("adds", "r1, #0x5e")     # 2 * 0x2F
    assert text[0x018C] == ("movs", "r0, #1")
    assert text[0x018E] == ("strh", "r0, [r1]")


# --- Finding 58: what actually resets the coach ----------------------------


def test_clear_eeprom_reseeds_placeholders_not_the_coach_floorplan(mcf_136):
    """Why CLEAR EEPROM loses the floorplan and options but restores everything else.

    Firefly's own caption on object 22 says "you may need to set the floorplan and options back to
    previous settings". The blob agrees, but not because it has no default for those selectors --
    it does (the register-indexed seeds). It is because the seed is a *generic placeholder*, not
    the coach's value: the routine reseeds floorplan `0xF5 := 0` and mode `0xEF := 0`, whereas this
    WD/AES coach runs `0xF5 == 8` and `0xEF == 3`, which arrive from the panel. So after a clear
    the coach-specific floorplan and options must be re-set, while the numeric parameters (which
    seed to their real defaults) come back on their own.
    """
    defaults = g12code.config_defaults_by_selector(mcf_136)
    assert defaults[0xF5] == 0, "floorplan seed is a placeholder, not the coach's WD (8)"
    assert defaults[0xEF] == 0, "mode seed is a placeholder, not the coach's AES (3)"
    assert defaults[0x2F] == 1, "the AES arming flag must default ON, or PC.39 has no fix"
    # a numeric parameter, by contrast, seeds to a usable value that survives a clear
    assert defaults[0x0C] == 60, "time-at-start-volts should seed to its real default"


def test_clear_eeprom_is_the_only_control_using_a_non_standard_config_function(build):
    """`0xDE` with the magic 0x5D28 appears once per build and nowhere else.

    Set/adjust/read are `0xD1`/`0xD2`/`0xD3`. Anything else is a command rather than a value, and a
    survey of every 0x1FED9 template and every GuiData binding finds exactly one reachable one.
    """
    import struct
    from fireflymcf import container, guidata

    section = next(s for s in container.parse_container(build).sections if "GuiCode" in s.name)
    code = build[section.offset:section.offset + section.length]
    unusual = []
    for off in range(0, len(code) - 12, 4):
        ident = struct.unpack_from("<I", code, off)[0]
        if (ident & 0xFF) == 0 and ((ident >> 8) & 0x3FFFF) == 0x1FED9:
            payload = code[off + 4:off + 12]
            if payload[6] not in (0xD1, 0xD2, 0xD3):
                unusual.append(payload)
    assert len(unusual) == 1, f"expected one special command, got {[p.hex() for p in unusual]}"
    assert unusual[0] == bytes.fromhex("ff96abff285ddeff")
    assert unusual[0][2] == 0xAB and unusual[0][6] == 0xDE
    assert unusual[0][4] | (unusual[0][5] << 8) == 0x5D28

    # the 0xCF twin of G12 rule record 59 exists as a binding but no object carries its group
    groups = guidata.group_table(build)
    bindings = guidata.binding_table(build)
    for group in groups:
        for index in range(group.start, group.start + group.count):
            raw = bindings[index].raw
            wire = bytes(raw[k] for k in (7, 6, 5, 4, 11, 10, 9, 8))
            if wire[:2] == b"\xff\x96" and wire[6] == 0xDE:
                raise AssertionError(f"a binding also sends 0xDE: group {group.index}")


def test_a_stock_reflash_did_not_reset_the_config():
    """PC.36 -> stock flash -> PC.37, same day: nothing moved.

    This is what rules out "flash the G12 section" as a reset. `0xF5` reads 0 for the first ~15 s
    of a panel boot, so the assertion is that 8 is present, not that it is alone.
    """
    import re

    def sweep(path):
        out = {}
        with open(path, errors="ignore") as handle:
            for line in handle:
                match = re.search(r"195FCE9C#([0-9A-F]{16})", line)
                if match:
                    payload = bytes.fromhex(match.group(1))
                    out.setdefault(payload[0], set()).add(payload[2] | (payload[3] << 8))
        return out

    before = sweep("captures/pc36-service-mode-2026-08-24.log")
    after = sweep("captures/pc37-aes-trigger-attempt-2026-08-24.log")
    assert before[0x2F] == {0} and after[0x2F] == {0}, "the arming flag moved across the flash"
    assert before[0x0C] == {60} and 60 in after[0x0C]
    assert before[0xF5] == {8} and 8 in after[0xF5], "the floorplan moved across the flash"


def test_no_reset_button_restores_the_aes_arming_flag(mcf_136):
    """Every `D1 EA` run in the pool, and none of them writes 0x2F.

    So the panel offers no route back to the setting PC.39 identified -- which is why the answer
    to "how do I reset this" is CLEAR EEPROM rather than any of the Set-Defaults buttons.
    """
    import struct
    from fireflymcf import container

    section = next(s for s in container.parse_container(mcf_136).sections
                   if "GuiCode" in s.name)
    code = mcf_136[section.offset:section.offset + section.length]
    written = set()
    for off in range(0, len(code) - 12, 4):
        ident = struct.unpack_from("<I", code, off)[0]
        if (ident & 0xFF) == 0 and ((ident >> 8) & 0x3FFFF) == 0x1FED9:
            payload = code[off + 4:off + 12]
            if payload[6] == 0xD1 and payload[7] == 0xEA:
                written.add(payload[2])
    assert 0x31 in written, "the AGS/AES setpoint run should be here -- the scan is broken"
    assert 0xCC in written and 0x92 in written
    assert 0x2F not in written, "a reset run now writes 0x2F; Finding 58 needs revisiting"


# --- The ~1 Hz input refresh is NOT in this blob (G12-CODE.md "Next steps" item 6) ------------
#
# PC.24 measured every held input pin re-broadcasting its 0x1FBDA frame at 1.000 s. The blob doc
# long listed the mechanism as open, guessing it "drives table 0x20's templates from somewhere
# else in this blob". It does not: the table-0x67 blob has exactly two transmit paths -- the
# block-copy frame table (+0x1A0C) and table-0x62 rule activation ([r4,#8]) -- and neither emits
# an input-pin broadcast. The frame table's only 0x1FBDA is instance 0xBF (the code-generated
# three-state status), and table 0x62 carries no 0x1FBDA record at all, so no activated rule can
# emit one either. The repeat marker lives in the table-0x20 record (byte-2 bit 0x04, the "kind
# 0x04" census); the code that acts on it is the G12 application at 0x08006000, outside the MCF.

from fireflymcf import g12rules  # noqa: E402


def test_the_blob_emits_no_input_pin_broadcast(build):
    """The blob's two transmit paths never carry a 0x1FBDA input instance (0xA1-0xAA / 0xFB).

    Both facts are per-build and jointly establish the negative:
      1. the block-copy frame table emits 0x1FBDA only as instance 0xBF (or not at all), and
      2. table 0x62 -- the only table the blob activates ([r4,#8], indices 0..93) -- holds no
         0x1FBDA rule.
    """
    frame_insts = {f.payload[0] for f in g12code.frame_table(build) if f.dgn == 0x1FBDA}
    assert frame_insts <= {0xBF}, (
        "the blob block-copies a 0x1FBDA frame for an instance other than 0xBF: %s"
        % sorted(hex(i) for i in frame_insts))

    t62_1fbda = [r for r in g12rules.all_rules(build)
                 if r.table == 0x62 and r.dgn == 0x1FBDA]
    assert not t62_1fbda, (
        "table 0x62 now has a 0x1FBDA rule; the blob could emit an input broadcast by activation")


def test_the_held_input_marker_is_a_rule_record_bit_not_blob_code(mcf_136):
    """byte-2 bit 0x04 on the table-0x20 template is the 1 Hz repeat-while-held marker.

    Every ON/active input template sets it and its OFF counterpart clears it, so the "refresh
    while held" behaviour PC.24 saw is a property of the record the app engine reads -- not of
    the table-0x67 code. Pinned on 13.6, whose reference coach has the WD input map; 14.7
    (Terrain) has no 0x1FBDA input templates at all, which is why this is not parametrised.
    """
    t20 = [r for r in g12rules.all_rules(mcf_136) if r.table == 0x20 and r.dgn == 0x1FBDA]
    held = {r.raw[7] for r in t20 if r.raw[2] & 0x04}       # instance byte = 7 on these records
    off = {r.raw[7] for r in t20 if not r.raw[2] & 0x04}
    assert held, "no held input template found; the scan or the corpus changed"
    assert 0xAA in held, "pin 10 (Ignition, 0xAA) should be a held/refreshed input"
    assert held == off, (
        "every held ACTIVE template must have an OFF counterpart for the same instance; "
        "held=%s off=%s" % (sorted(hex(i) for i in held), sorted(hex(i) for i in off)))


# --- The per-instance tank bucketer and its lr-relative jump table ----------------------------
#
# The blob buckets four tank instances (0xA8-0xAB) into 0/33/67/100% by comparing each tank's raw
# ADC reading against the CC/CD/CE thresholds with hysteresis. The per-tank state is dispatched
# through an lr-relative jump thunk whose target the recursive flow walk cannot follow (it ends in
# `bx lr`), which is why +0x1858/+0x1878/+0x1948/+0x197E were long listed as "unreached". Teaching
# the walk to follow the thunk (`jump_table_arms`) reaches them.


def test_the_lr_relative_jump_thunk_is_present_on_every_build(build):
    """Every build carries the dispatch thunk, in a u8 (11.23/14.7) or u16 (13.6) table form."""
    found = g12code._find_jump_thunk(g12code.blob(build))
    assert found is not None, "the tank-bucketer dispatch thunk was not located"
    _entry, width = found
    assert width in (1, 2)


def test_the_jump_table_arms_are_the_tank_bucketer(mcf_136):
    """On 13.6 the four arms are the CC/CD/CE threshold blocks, and the flow-only walk misses them.

    Each arm opens `movs r0,#<selector>` for a tank-threshold selector (0xCC/0xCD/0xCE), which is
    what makes this the bucketer rather than an arbitrary computed jump.
    """
    body = g12code.blob(mcf_136)
    flow = g12code.analyse(mcf_136, follow_jump_tables=False)
    arms = g12code.jump_table_arms(body, flow)
    assert arms == {0x1858, 0x1878, 0x1948, 0x197E}

    for arm in arms:
        assert body[arm + 1] == 0x20, f"arm +{arm:#x} does not open with movs r0,#imm8"
        assert body[arm] in (0xCC, 0xCD, 0xCE), (
            f"arm +{arm:#x} reads selector {body[arm]:#x}, not a tank threshold")

    # the flow-only walk really does leave the arms dark; following the jump is load-bearing.
    flow_gaps = {start for start, _ in flow.gaps(8)}
    assert 0x1858 in flow_gaps and 0x1948 in flow_gaps, (
        "the arms were already covered without the jump follower -- the test proves nothing")


def test_following_the_jump_table_reaches_the_arms(build):
    """The follower is a strict improvement: every arm is covered with it and some without."""
    body = g12code.blob(build)
    flow = g12code.analyse(build, follow_jump_tables=False)
    full = g12code.analyse(build)
    arms = g12code.jump_table_arms(body, flow)
    assert arms, "no jump-table arms found on this build"
    assert full.coverage >= flow.coverage
    assert all(a in full.code for a in arms), "an arm stayed dark after following the jump table"
    assert any(a not in flow.code for a in arms), (
        "flow-only already reached every arm; the follower is not being exercised")
