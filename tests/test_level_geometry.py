"""v45: Wheelbase and Track rows on the LEVEL SETUP popup (`add_level_page` `geometry`).

- the four steppers send level-sensor ops 0Bh-0Eh on 0x1EFBF: one frame per tap, and after a
  1-s hold one every 250 ms (the stock arrows' shape), nothing on release;
- two more receive entries read record 04h bytes 1-2 and 3-4 as u16;
- the popup grows to five rows inside the page area, its touches last, its readouts on the new
  cells with one decimal place;
- v45 is v44's recipe with `geometry` on, and rebuilds to the image handed over.
"""

import pytest

from fireflymcf import levelcontrols, levelfeed, levelsetup

STOCK = "firefly-13.6/mcf.bin"


# ---- the controls ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, op", sorted(levelcontrols.GEOMETRY_OPCODES.items()))
def test_a_geometry_stepper_sends_its_op_once_per_tap_and_repeats_while_held(name, op):
    stock = open(STOCK, "rb").read()
    entry = levelcontrols.group_entry(stock, name, 0)
    assert entry[:4] == bytes.fromhex("10000400"), "1-s hold, 250 ms held repeat, no second loop"
    frame = (0x19EFBF9F, bytes([0x4C, 0x56, op]) + b"\xff" * 5)
    p = levelcontrols.presses(entry, levelcontrols.bindings(name, levelcontrols.STEPPER_DGN_RVC))
    assert p["tap"] == [frame]
    # held at 1000 ms, then 1250 / 1500 / 1750 before a 2-s release; the release sends nothing
    assert p["held_2s"] == [frame] * 4
    assert p["held_3s"] == [frame] * 8


def test_the_other_setup_controls_still_send_once():
    stock = open(STOCK, "rb").read()
    for name in levelcontrols.SETUP_CONTROLS:
        entry = levelcontrols.group_entry(stock, name, 0)
        p = levelcontrols.presses(entry, levelcontrols.bindings(name))
        assert all(len(v) == 1 for v in p.values()), name


# ---- the receive side -----------------------------------------------------------------------

def test_geometry_reads_record_04h_bytes_1_and_3_as_u16():
    assert levelfeed.GEOMETRY_VARS == (("wb", 4, 1, 2), ("track", 4, 3, 2))
    assert levelfeed.vars_for(True) == levelfeed.VARS + levelfeed.SETUP_VARS
    assert levelfeed.vars_for(True, True)[-2:] == levelfeed.GEOMETRY_VARS


# ---- the popup ------------------------------------------------------------------------------

def test_the_five_row_dialog_fits_the_page_and_no_touch_overlaps():
    x, y, w, h, _r = levelsetup.GEOMETRY_DIALOG
    assert y >= 37 and y + h <= 479, "the dialog must sit below the header, inside the page"
    rr = levelsetup.rects(True)
    names = list(rr)
    hit = lambda a, b: a[0] <= b[0] + b[2] and b[0] <= a[0] + a[2] and \
        a[1] <= b[1] + b[3] and b[1] <= a[1] + a[3]
    assert not [(a, b) for i, a in enumerate(names) for b in names[i + 1:] if hit(rr[a], rr[b])]
    assert all(x <= r[0] and r[0] + r[2] <= x + w and y <= r[1] and r[1] + r[3] <= y + h
               for r in rr.values())
    assert set(levelsetup.layout(True)["touch_names"]) == set(rr)


def test_the_three_row_layout_is_unchanged():
    lay = levelsetup.layout(False)
    assert lay["dialog"] == (150, 60, 580, 360, 10)
    assert lay["row_y"] == {"height": 118, "tol": 190, "carried": 262}
    assert lay["touch_names"] == levelsetup.TOUCH_NAMES
