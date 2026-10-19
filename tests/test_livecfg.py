"""Decoding a coach's LIVE configuration from a candump capture.

The G12 broadcasts a selector-keyed config status on DGN 0x15FCE every ~5s. Every
selector the floorplan-selection burst writes appears there, plus the ones it does not
(0xD7 Go Power! count, 0xD8 battery count -- `BUS-BASELINE.md` PC.12a's "comes from
status, not from the transaction").

The fixture is real frames trimmed from a listen-only capture on the coach -- one full
selector cycle plus a battery-count toggle. It exists to exercise the DECODER; the
hardware facts it carries (what each selector means, the 00 = "1 battery" encoding, that
the floorplan burst never writes 0xD8) are documented in `BUS-BASELINE.md` PC.1/PC.12a and
mechanised by `tests/test_capture_defaults.py`. Nothing here re-derives them.
"""
import pytest

from fireflymcf import livecfg

CAPTURE = "tests/fixtures/livecfg-sample.log"
STOCK = "firefly-13.6/mcf.bin"


@pytest.fixture(scope="module")
def capture():
    with open(CAPTURE) as f:
        return f.read()


@pytest.fixture(scope="module")
def stock():
    with open(STOCK, "rb") as f:
        return f.read()


def test_decodes_the_selector_map(capture):
    live = livecfg.decode_capture(capture)
    assert live.source == 0x9C                      # the G12
    # 42 distinct selectors, and the fixture repeats D8 twice more for the toggle
    assert live.frames == 45 and len(live.selectors) == 42
    # the floorplan the coach is actually on
    assert live.selectors[0xF5] == 8                # WD
    # options, matching what the OPTIONS pages show
    assert live.selectors[0xE5] == 1                # Bunk Accent
    assert live.selectors[0xEB] == 0                # Heat Pump
    assert live.selectors[0xEF] == 3                # AES Single Output


def test_value_is_a_u16_little_endian_at_payload_bytes_2_3(capture):
    """0x31 is the AES "Start At" voltage: 0x03FC = 1020 x 0.05 V = 51.0 V.

    A byte-wide read would give 0xFC = 252, and a big-endian one 0xFC03 = 64515.
    Both are wrong; this pins the field."""
    live = livecfg.decode_capture(capture)
    assert live.selectors[0x31] == 1020
    assert live.selectors[0x31] * 0.05 == pytest.approx(51.0)


def test_last_value_wins_so_a_toggle_is_reflected(capture):
    """The owner set Batteries to 2 then back to 1 during this capture.

    0xD8 is the battery count and it is ZERO-BASED -- 0 shows "1". The decode must
    report the final state, not the first frame."""
    live = livecfg.decode_capture(capture)
    assert live.selectors[0xD8] == 0


def test_a_capture_with_no_status_frames_is_empty_not_an_error():
    live = livecfg.decode_capture("(1.0) can1 19FEDB9F#01FC00FFFFFFFFFF\n")
    assert live.selectors == {} and live.frames == 0 and live.source is None


def test_garbage_input_is_empty_not_an_error():
    assert livecfg.decode_capture("not a capture at all\n\n").frames == 0
    assert livecfg.decode_capture("").frames == 0


def test_to_preview_maps_selectors_onto_the_coach_config(stock, capture):
    """The decoded map becomes exactly the shape `project.preview` stores."""
    live = livecfg.decode_capture(capture)
    pv = livecfg.to_preview(stock, live)
    assert pv["floorplan"] == "WD"
    # every option the coach reports ON, by the name the picker uses
    assert set(pv["options"]) == {"Bunk Accent", "Cargo / Bath Light", "Black Tank",
                                  "AES Single Output"}
    # the counts ride in extra_slots, keyed by their VALUE SLOT not their selector: the
    # battery count (0xD8 -> 599) and the Go Power! controller count (0xD7 -> 39). The
    # coach reports 0 controllers, which is a WD's own floorplan default -- and one of
    # the three sources the nav rail is derived from.
    assert pv["extra_slots"] == {"599": 0, "39": 0}


def test_to_preview_picks_one_generator_mode_by_value(stock):
    """Selector 0xEF is a radio: 1 = AGS, 2 = AES, 3 = AES Single Output."""
    for value, name in ((1, "AGS"), (2, "AES"), (3, "AES Single Output")):
        live = livecfg.LiveConfig(source=0x9C, frames=1, selectors={0xF5: 8, 0xEF: value})
        pv = livecfg.to_preview(stock, live)
        assert pv["options"] == [name], value
    off = livecfg.LiveConfig(source=0x9C, frames=1, selectors={0xF5: 8, 0xEF: 0})
    assert livecfg.to_preview(stock, off)["options"] == []


def test_to_preview_omits_a_floorplan_it_cannot_name(stock):
    live = livecfg.LiveConfig(source=0x9C, frames=1, selectors={0xF5: 99})
    assert "floorplan" not in livecfg.to_preview(stock, live)
