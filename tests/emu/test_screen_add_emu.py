"""`add_screen` in the emulator: every stock screen draws pixel for pixel as on stock after the
band rebuild, an unknown id still shows the default, the rail and a light still work, and the
new screen draws its empty page (GUICODE Finding 93)."""
import time

import numpy as np
import pytest

pytest.importorskip("unicorn")

from fireflymcf import layout, screenadd  # noqa: E402
from fireflymcf.emu.scenario import Scenario  # noqa: E402

DC_DIMMER_COMMAND_2 = 0x1FEDB
UNKNOWN_ID = 200
# The demo network's clock follows the host clock (`peers.Clock`), drawn on HOME's header and
# SETTINGS' clock; freeze it so the two boots draw the same minute.
FROZEN = time.localtime(1_800_000_000)


def _visit(image, ids):
    s = Scenario(image, timeout_s=900)
    s.boot()
    shots = {}
    for sid in ids:
        s.show_screen(sid)
        assert s.screen == sid
        shots[sid] = s.screenshot()
    return s, shots


@pytest.fixture(scope="module")
def pair(mcf_136):
    from fireflymcf.emu import peers

    added, sid, _root = screenadd.add_screen(mcf_136, screenadd.blank_root(mcf_136, "BATTERY SETUP"))
    ids = sorted(layout.screen_object_map(mcf_136)) + [UNKNOWN_ID]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(peers.time, "localtime", lambda *_a: FROZEN)
        _s0, stock_shots = _visit(mcf_136, ids)
        s1, added_shots = _visit(added, ids + [sid])
    return stock_shots, added_shots, s1, sid


def test_every_screen_draws_as_stock(pair):
    stock_shots, added_shots, _s, _sid = pair
    differ = [sid for sid in stock_shots
              if not np.array_equal(stock_shots[sid], added_shots[sid])]
    assert differ == []


def test_new_screen_is_blank_page(pair):
    stock_shots, added_shots, s, sid = pair
    img = added_shots[sid]
    assert not np.array_equal(img, stock_shots[UNKNOWN_ID])          # not the default page
    # the page area (inside the LEVEL page's page fill) is one colour: nothing drawn on it
    page = img[60:420, 100:760].reshape(-1, 3)
    assert len(np.unique(page, axis=0)) == 1


def test_rail_and_light_still_work(mcf_136, pair):
    _st, _ad, s, _sid = pair
    for page in ("HOME", "ELECTRICAL", "LIGHTS", "CLIMATE CONTROL", "SETTINGS"):
        s.goto(page)
        assert s.page == page or page in s.page
    s.goto("HOME")
    t0 = s.now
    s.tap("Main Ceiling")
    assert s.frames(dgn=DC_DIMMER_COMMAND_2, since=t0)
