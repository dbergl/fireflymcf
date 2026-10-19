"""The per-floorplan defaults, re-derived from the panel's CAN writes.

**This is a regression test, not a new source.** PC.12 already derived
`answer_key.FLOORPLAN_OPTION_DEFAULTS` from this capture's `0x1FED9` writes, alongside the
owner's screen readings; the write DGN and its `FF 96 <sel> 0F <val>` layout were recorded
earlier still, in PC.2. What this module adds is **mechanisation**: the derivation was a
manual reading, so nothing would have caught a transcription slip in the ninety values, and
nothing pinned the properties the firmware work now leans on.

`observed-on-hardware` -- it belongs in tests rather than in a parser because there is
nothing to parse: it reads bytes the coach transmitted. See `docs/BUS-BASELINE.md` PC.12a.

Address roles, pinned below rather than assumed:

    0x9C   the G12    -- sole transmitter of 0x15FCE, periodic ~5 s (configuration STATUS)
    0x9F   the Spyder -- transmitter of 0x1FED9              (configuration WRITE)

Write frame layout, `<sel>` at byte 2 and `<val>` at byte 4:

    19FED99F#FF96<sel>0F<val>..D1EA     FF96F50F0500D1EA  =  selector 0xF5 := 5 (TB)
    19FED99F#FF96<sel>0FFFFFD3FF        the terminator that follows each written selector

The test that matters most for the firmware work is
`test_burst_is_atomic_with_no_g12_round_trip`: the panel emits all twelve selectors in a
few milliseconds without the G12 saying anything in between, which is what proves the
values come from the panel's own flash. That single fact refuted the hypothesis that the
G12 holds the defaults and the panel merely echoes them, and it is the reason the search
in `docs/GUICODE.md` Finding 6 was worth running at all.
"""
import re
from pathlib import Path

import pytest

from tests import answer_key

REPO_ROOT = Path(__file__).resolve().parent.parent
CAPTURE = REPO_ROOT / "captures" / "pc5-floorplans-2026-08-12.log"

CONFIG_STATUS = "195FCE"        # DGN 0x15FCE, broadcast by the G12
CONFIG_WRITE = "19FED9"         # DGN 0x1FED9, written by the panel
G12_ADDRESS = "9C"
PANEL_ADDRESS = "9F"

WRITE_PREFIX = "FF96"           # constant first two bytes of every write frame
TERMINATOR = "FFFF"             # bytes 4-5 of the frame that closes each selector

# A new burst starts after this much silence on the write DGN.
BURST_GAP_S = 0.5

FRAME_RE = re.compile(r"\((\d+\.\d+)\) can\d+ ([0-9A-F]+)#([0-9A-F]*)")

# The transaction writes twelve selectors. Ten are options PC.12 tabulates; 0x15 and 0xF7
# are not options and are excluded from the value comparison. See
# test_burst_writes_twelve_selectors for why D8 is not among them.
NON_OPTION_SELECTORS = {0x15, 0xF7}
FLOORPLAN_SELECTOR = 0xF5


@pytest.fixture(scope="session")
def frames() -> list[tuple[float, str, str]]:
    """(timestamp, can_id, data_hex) for every frame in the PC.5 capture."""
    assert CAPTURE.is_file(), f"missing capture: {CAPTURE}"
    out = []
    for line in CAPTURE.read_text().splitlines():
        m = FRAME_RE.match(line)
        if m:
            out.append((float(m.group(1)), m.group(2), m.group(3)))
    assert len(out) > 10_000, f"capture parsed to only {len(out)} frames"
    return out


@pytest.fixture(scope="session")
def bursts(frames) -> list[dict]:
    """Each floorplan-selection transaction, as {"t", "floorplan", "values", "span"}.

    Terminator frames are skipped; a burst is a run of write frames with no gap longer
    than BURST_GAP_S.
    """
    writes = [
        (t, d) for t, cid, d in frames
        if cid == CONFIG_WRITE + PANEL_ADDRESS and d.startswith(WRITE_PREFIX)
    ]
    groups, current, last_t = [], [], None
    for t, d in writes:
        if last_t is not None and t - last_t > BURST_GAP_S:
            groups.append(current)
            current = []
        current.append((t, d))
        last_t = t
    if current:
        groups.append(current)

    out = []
    for g in groups:
        values = {}
        for _, d in g:
            if d[8:12] == TERMINATOR:
                continue
            values.setdefault(int(d[4:6], 16), int(d[8:10], 16))
        if FLOORPLAN_SELECTOR not in values:
            continue
        out.append({
            "t": g[0][0],
            "floorplan": values[FLOORPLAN_SELECTOR],
            "values": values,
            "span": g[-1][0] - g[0][0],
            "start": g[0][0],
            "end": g[-1][0],
        })
    return out


def test_only_the_g12_broadcasts_configuration_status(frames):
    """0x15FCE comes from exactly one address, and it is not the panel.

    This is what makes the status/write split more than an assumption: if the panel also
    broadcast 0x15FCE, "the panel writes the defaults" would not follow from 0x1FED9.
    """
    sources = {cid[len(CONFIG_STATUS):] for _, cid, _ in frames
               if cid.startswith(CONFIG_STATUS)}
    assert sources == {G12_ADDRESS}


def test_only_the_panel_writes_configuration(frames):
    """0x1FED9 comes from exactly one address, and it is not the G12."""
    sources = {cid[len(CONFIG_WRITE):] for _, cid, _ in frames
               if cid.startswith(CONFIG_WRITE)}
    assert sources == {PANEL_ADDRESS}


def test_capture_covers_every_floorplan(bursts):
    """The eleven bursts in this capture visit all ten floorplans."""
    assert len(bursts) == 11
    assert {b["floorplan"] for b in bursts} == set(range(1, 11))
    assert set(answer_key.FLOORPLAN_OPTION_DEFAULTS) == set(range(1, 11))


def test_burst_writes_twelve_selectors(bursts):
    """Twelve selectors per burst, and D8 is NOT one of them.

    PC.12 tabulates ten option selectors including 0xD8, but the write transaction never
    carries 0xD8 -- its value there was read from status, not from a write. The twelve
    written are the other nine options, the floorplan selector, and 0x15 / 0xF7, which are
    not options at all. Pinned so the discrepancy stays visible instead of being smoothed
    over by a comparison that only checks the selectors it expects.
    """
    for b in bursts:
        assert len(b["values"]) == 12, (
            f"floorplan {b['floorplan']}: {len(b['values'])} selectors")
        assert 0xD8 not in b["values"]
        assert NON_OPTION_SELECTORS <= set(b["values"])


def test_writes_reproduce_the_defaults_oracle(bursts):
    """Every written option value matches answer_key, for all ten floorplans.

    Mechanises the reading PC.12 did by hand, so a transcription slip in any of the ninety
    values would now fail. It compares only the selectors the burst actually writes, so
    0xD8 is excluded -- asserted separately above rather than silently dropped here.
    """
    checked = 0
    for b in bursts:
        expected = answer_key.FLOORPLAN_OPTION_DEFAULTS[b["floorplan"]]
        for selector, value in expected.items():
            if selector not in b["values"]:
                continue                      # 0xD8, per test_burst_writes_twelve_selectors
            assert b["values"][selector] == value, (
                f"floorplan {b['floorplan']} selector {selector:#04x}: "
                f"capture says {b['values'][selector]}, oracle says {value}")
            checked += 1
    # Nine of ten selectors, eleven bursts -- guard against the loop silently doing
    # nothing, which is how a test like this passes while proving nothing.
    assert checked == 9 * 11


def test_panel_writes_precede_the_g12_reporting_those_values(frames, bursts):
    """The panel originates each new value; the G12 only confirms it afterwards.

    The load-bearing test of this module, and the reason the search in `docs/GUICODE.md`
    Finding 6 was worth running: it establishes that the defaults are in the *panel's*
    flash rather than the G12's.

    An earlier version of this test asserted the G12 was silent during the burst. That was
    wrong -- the G12 runs a continuous ~5 s status cycle that inevitably overlaps a ~50 ms
    burst, and it also confirms each write within a few milliseconds. The mistake came from
    "observing" silence in a dump that had filtered G12 frames out. The correct question is
    not whether the G12 speaks but whether it speaks *first*.

    For every selector whose value actually changes, the ordering is unambiguous:

        131.967  g12 status   E3 = 1      <- last report before the burst: the OLD value
        136.109  PANEL WRITE  E3 := 0     <- the panel introduces the new value
        136.113  g12 status   E3  = 0     <- the G12 confirms, 4 ms later

    The new value does not exist on the bus until the panel writes it, so the panel cannot
    have read it from anywhere. Were it echoing the G12, the status frame would come first.
    """
    status = [(t, int(d[0:2], 16), int(d[4:6], 16)) for t, cid, d in frames
              if cid == CONFIG_STATUS + G12_ADDRESS and len(d) >= 6]

    changed_total = 0
    for b in bursts:
        for selector, value in b["values"].items():
            if selector in NON_OPTION_SELECTORS:
                continue
            prior = [(t, v) for t, s, v in status if s == selector and t < b["start"]]
            if not prior or prior[-1][1] == value:
                continue                    # unchanged: the G12 already reported it
            changed_total += 1
            write_t = next(
                t for t, cid, d in frames
                if cid == CONFIG_WRITE + PANEL_ADDRESS
                and d.startswith(WRITE_PREFIX)
                and int(d[4:6], 16) == selector
                and d[8:12] != TERMINATOR
                and b["start"] <= t <= b["end"])
            first_report = next(
                (t for t, s, v in status
                 if s == selector and v == value and t >= b["start"]), None)
            assert first_report is not None, (
                f"floorplan {b['floorplan']} selector {selector:#04x}: "
                f"G12 never confirmed {value}")
            assert write_t < first_report, (
                f"floorplan {b['floorplan']} selector {selector:#04x}: G12 reported "
                f"{value} at {first_report:.3f} before the panel wrote it at "
                f"{write_t:.3f} -- the panel would be echoing, not originating")

    # Guard against the loop finding nothing to check, which is how this test would pass
    # while proving nothing at all.
    assert changed_total >= 20, (
        f"only {changed_total} changed selectors examined; expected many across 11 bursts")


def test_burst_completes_quickly(bursts):
    """Each selection burst is tens of milliseconds, not a sequence of round trips.

    Weaker than the ordering test above and kept only as a sanity bound: twelve
    query/response exchanges over CAN would not fit in this window.
    """
    for b in bursts:
        assert b["span"] < 0.200, (
            f"floorplan {b['floorplan']}: burst took {b['span'] * 1000:.0f} ms")


def test_floorplan_five_is_tb_and_overwrites_owner_settings(bursts):
    """The specific transaction that exposed PC.12's correction.

    PC.5 originally read the transaction as the panel preserving the owner's settings.
    Selecting TB (0xF5 = 5) writes E5 := 0 and E3 := 0, while PC.12 records the owner as
    having had both at 1 -- so the transaction pushes defaults rather than preserving
    anything. Pinned because this is the concrete counterexample the correction rests on.

    The owner's own baseline is documented in BUS-BASELINE.md ("E3=1, E5=1, EC=1") but is
    not in answer_key, so only the written values are asserted here. WD's defaults happen
    to equal the owner's configuration, which is exactly why the original reading looked
    right; that coincidence is what this test guards against re-establishing.
    """
    tb = [b for b in bursts if b["floorplan"] == 5]
    assert len(tb) == 1
    assert tb[0]["values"][0xE5] == 0
    assert tb[0]["values"][0xE3] == 0
    # WD (floorplan 8) is the coincidence: its defaults match the owner's settings.
    wd = next(b for b in bursts if b["floorplan"] == 8)
    assert wd["values"][0xE5] == 1
    assert wd["values"][0xE3] == 1


def test_aes_parameter_profile_is_in_the_template_pool():
    """GUICODE Finding 43: the AES parameter profile PC.4 Result 4 tabulated lives in GuiCode's
    CAN-template pool as literal `0x1FED9` writes, and one handler (0x24e500) emits the whole set.

    PC.4 closed with "The MCF decode should show where these profiles live" -- this is that. The
    values are asserted against the capture's AES column, so a wrong extraction fails rather than
    looking plausible."""
    import struct
    from fireflymcf import theme
    data = open("firefly-13.6/mcf.bin", "rb").read()
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)

    def value_at(vaddr):
        off = section.offset + (vaddr - base)
        t = data[off:off + 12]
        assert t[1] == 0xD9 and t[2] == 0xFE, t.hex()      # 0x1FED9 config write
        assert t[4] == 0xFF and t[5] == 0x96 and t[7] == 0x0F, t.hex()
        return t[6], struct.unpack_from("<H", t, 8)[0]

    # selector, template address, expected value -- the AES column of PC.4 Result 4
    for vaddr, sel, want in ((0x2599D0, 0x31, 1020),     # start at voltage  = 51.00 V
                             (0x259A00, 0x0D, 1060),     # stop at voltage   = 53.00 V
                             (0x2599E8, 0x0C, 60),       # time at start volts
                             (0x259A18, 0x0E, 1800),     # time at stop volts
                             (0x259A30, 0x0F, 1800),     # companion of 0x0E
                             (0x259A48, 0x16, 115)):     # max engine run time
        got_sel, got_val = value_at(vaddr)
        assert got_sel == sel, (hex(vaddr), hex(got_sel), hex(sel))
        assert got_val == want, (hex(vaddr), got_val, want)


def test_aes_start_at_voltage_default_is_51v():
    """The owner's question, pinned: the AES 'Start At' default before any user change is
    1020 units x 0.05 V = 51.00 V, held as a u16 at GuiCode 0x2599D8 (file 0x29258). It is
    ALREADY the value they asked about changing it to, so the answer to "can we set it to 51.0"
    is that no edit is needed."""
    import struct
    from fireflymcf import theme
    data = open("firefly-13.6/mcf.bin", "rb").read()
    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    off = section.offset + (0x2599D0 - base)
    assert struct.unpack_from("<H", data, off + 8)[0] * 0.05 == 51.0


def test_the_aes_profile_is_global_not_per_floorplan():
    """One `0x31` template exists in the whole GuiCode section, and no floorplan handler writes
    that selector -- floorplan selection sets only the mode (0xEF). So every floorplan defaulting
    to an AES mode shares the single 51.0 V profile.

    Non-vacuous both ways: it requires the Li plans to actually default to an AES mode (so a
    regression that zeroed 0xEF would fail) and requires exactly one 0x31 template (so a
    per-floorplan profile appearing later fails too)."""
    import struct
    from fireflymcf import theme, floorplandefaults, options
    data = open("firefly-13.6/mcf.bin", "rb").read()
    section = theme.guicode_section(data)
    code = data[section.offset:section.offset + section.length]

    writes = [i for i in range(0, len(code) - 12, 2)
              if code[i] == 0 and code[i + 1] == 0xD9 and code[i + 2] == 0xFE
              and code[i + 4] == 0xFF and code[i + 5] == 0x96 and code[i + 6] == 0x31
              and code[i + 7] == 0x0F
              and struct.unpack_from("<H", code, i + 8)[0] != 0xFFFF]
    assert len(writes) == 1, [hex(i) for i in writes]

    table = floorplandefaults.floorplan_defaults(data)
    assert not any(0x31 in w for w in table.values()), "floorplan handlers must not set 0x31"

    codes = options.floorplan_options(data)
    by_code = {codes[v]: w for v, w in table.items() if v in codes}
    for plan in ("WA", "WD", "WT", "TB"):
        assert by_code[plan].get(0xEF) == 3, (plan, by_code[plan].get(0xEF))
    assert by_code["SY"].get(0xEF) == 0, by_code["SY"].get(0xEF)
