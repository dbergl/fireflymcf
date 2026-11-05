"""`add_rvc_button` cycle + `add_button_states` on the emulated panel, end to end.

A three-state cycle button on HOME (`tests/test_rvc_cycle.py`'s) reads the Water Pump's DC
dimmer status (var 86, `(slot & 0x0C) >> 2`); tapping Water Pump flips what the emulated G12
reports there (0x44 on, 0x40 off). One boot, every clause: nothing is sent without a tap; with
no report a tap sends the unknown state's payload (variant 0) and a second tap the same again;
with state k reported a tap sends payload k+1; the button's body is drawn in the reported
state's colour.
"""
import json

import pytest

pytest.importorskip("unicorn")

from fireflymcf import guidata, recipe, records  # noqa: E402
from fireflymcf.emu.scenario import Scenario  # noqa: E402

NEEDS_RESEARCH_DATA = "boots the 13.6 corpus image in the emulator"

DGN = 0x1FFF6
PAYLOADS = ["00ffffffffffffff", "01ffffffffffffff", "02ffffffffffffff"]
OFF, ON = (65, 81, 163), (0, 174, 239)


def _project(sha):
    match = lambda v: {"source": {"var": 86}, "mask": 0x0C, "shift": 2, "eq": v}  # noqa: E731
    return {"schema": 1, "base_sha256": sha, "build_name": "button-states emu", "ops": [
        {"id": "mode", "type": "add_rvc_button", "enabled": True,
         "params": {"parent": 4, "x": 210, "y": 332, "w": 80, "h": 44, "text": "Mode",
                    "dgn": DGN, "cycle": {"payloads": PAYLOADS}}},
        {"id": "st", "type": "add_button_states", "enabled": True,
         "params": {"buttons": [{"button": {"ref": "mode"}, "unknown": {"text": "?"},
                                 "states": [{"match": match(k), "text": t, "lit": k == 1}
                                            for k, t in enumerate(("Alpha", "Bravo",
                                                                   "Gamma"))]}]}}]}


def _button_object(data):
    """The last object carrying a touch region: the button (its state arms, appended after
    it, carry none)."""
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    return next(i for i in range(len(tab) - 1, 0, -1)
                if any(r.wtype == 12 for r in records.object_records(data, gs + tab[i])))


def test_cycle_sends_the_state_after_the_reported_one(mcf_136, tmp_path):
    import hashlib
    res = recipe.compile_project(
        recipe.Project.from_json(json.dumps(_project(hashlib.sha256(mcf_136).hexdigest()))),
        mcf_136)
    assert res.ok, res.errors
    button = _button_object(res.data)
    s = Scenario(res.data, timeout_s=300)
    s.boot()
    s.goto("HOME")
    s.run(3)

    def tap():
        t = s.now
        s.tap(button)
        s.run(2)
        return [bytes(f.data).hex() for f in s.frames(dgn=DGN, sa=0x9F, since=t)]

    def body():
        img = s.screenshot(tmp_path / "b.png")
        return tuple(int(c) for c in img[332 + 22, 210 + 8])   # in the capsule, left of the text

    t = s.now
    s.run(5)
    assert s.frames(dgn=DGN, sa=0x9F, since=t) == [], "sent without a tap"
    assert body() == OFF
    assert tap() == [PAYLOADS[0]], "no report: the unknown state's variant 0"
    assert tap() == [PAYLOADS[0]], "report unchanged: the same frame again"
    s.tap("Water Pump")
    s.run(3)                                    # 0x44: state 1 reported
    assert body() == ON
    assert tap() == [PAYLOADS[2]]
    s.tap("Water Pump")
    s.run(3)                                    # 0x40: state 0 reported
    assert body() == OFF
    assert tap() == [PAYLOADS[1]]
