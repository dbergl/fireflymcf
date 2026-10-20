"""The LEVEL page (screen 29, object 367): layout, preview body, live body and its switch arms."""

from fireflymcf import levelpage


def test_tiles_match_the_mockup_math():
    cx, top, sc = 300, 84, 0.70
    X = lambda v: round(cx + (v - 260) * sc)
    Y = lambda v: round(top + (v - 40) * sc)
    t = levelpage.LAYOUT["tiles"]
    assert t["fl"] == (X(161) - 22 - 130, Y(130) - 37)
    assert t["fr"] == (X(359) + 22, Y(130) - 37)
    assert t["rl"] == (X(161) - 22 - 130, Y(465) - 37)
    assert t["rr"] == (X(359) + 22, Y(465) - 37)
    assert (t["w"], t["h"]) == (130, 74)
    v = levelpage.LAYOUT["van"]
    assert v["body"][:4] == (X(175), Y(40), X(345) - X(175), Y(560) - Y(40))


def test_the_example_values_match_the_mockup_solver():
    import math
    WB, TR, BH, TOL = 159.0, 68.5, 1.0, 0.25
    P, R, carried = -1.0, 0.4, 5
    a = WB / 2 * math.tan(math.radians(P))
    b = TR / 2 * math.tan(math.radians(R))
    h = {"fl": a + b, "fr": a - b, "rl": -a + b, "rr": -a - b}
    m = max(h.values())
    lay = {k: 0 if (m - v) <= TOL else math.ceil((m - v - TOL) / BH) for k, v in h.items()}
    lift = {k: f"{round((m - v) * 10) / 10:.1f}" for k, v in h.items()}
    E = levelpage.EXAMPLE
    for k in h:
        assert E["tiles"][k] == (None if lay[k] == 0 else (lay[k], lift[k])), k
    assert E["total"] == str(sum(lay.values())) and E["carried"] == str(carried)
    assert E["short"] == int(sum(lay.values()) > carried)


# ---- Task 8: the add_level_page op, the arrow glyph and render states ---------------------------


from fireflymcf import render


def test_recipe_ops_order_and_ids():
    ops = levelpage.recipe_ops(preview=False)
    assert [(o["id"], o["type"]) for o in ops] == [
        ("level-title", "rename_label"), ("level-arrow", "install_glyph"),
        ("level", "add_level_page")]
    assert ops[2]["params"]["arrow"] == {"slot": levelpage.ARROW[0], "char": levelpage.ARROW[1]}
    assert levelpage.recipe_ops(preview=True)[2]["params"]["preview"] is True


def test_arrow_rows_are_a_centred_up_triangle():
    rows = levelpage.arrow_rows()
    assert len(rows) == 14 and {len(r) for r in rows} == {20}
    counts = [r.count("f") for r in rows]
    assert counts == sorted(counts) and counts[0] >= 1 and counts[-1] == 20
    for r in rows:
        assert set(r) <= {"0", "f"}
        n, left = r.count("f"), r.index("f")
        assert r[left:left + n] == "f" * n and abs(left - (20 - n - left)) <= 1


# ---- mutation checks: a desynced VARS or a hollow routine must not compile silently -----------


# ---- render states --------------------------------------------------------------------------

# Every render state is the cell set the LEVEL routine itself writes for a 0x0FFB0 frame set:
# `test_levelfeed._run` runs `levelfeed.build`'s code in the Thumb emulator over the frames.
# Record 1: pitch u16 @1, roll u16 @3, flags @5 (bits 0-1 level, 2-3 short, 4-5 fault), dirs @6
# (bits 0-1 nose up, 2-3 nose down, 4-5 driver high, 6-7 passenger high), total @7. Record 2:
# layers FL/FR/RL/RR @1-4. Record 3: lift (0.1 in) @1-4. Record 4: carried @6.
from tests.test_levelfeed import _run as _emulate

NA = 0xFFFF
# The mockup's example: pitch 1.0 nose down, roll 0.4 driver side high, short, not level.
EXAMPLE_FRAMES = {1: "01 0A 00 04 00 04 14 08", 2: "02 03 04 00 01 FF FF FF",
                  3: "03 1C 21 00 05 FF FF FF", 4: "04 36 06 AD 02 14 05 19"}
EXAMPLE_STATE = _emulate(EXAMPLE_FRAMES)


def test_example_frames_reproduce_the_mockup_state():
    assert EXAMPLE_STATE == dict(
        pitch=10, roll=4, lay_fl=3, lay_fr=4, lay_rl=0, lay_rr=1,
        lift_fl=28, lift_fr=33, lift_rl=0, lift_rr=5, wheel_fl=1, wheel_fr=1, wheel_rl=0,
        wheel_rr=1, banner=0, short=1, total=8, carried=5, pitch_word=2, roll_word=1)


def test_format_cell_value_models_the_panel_formatter():
    f = render.format_cell_value
    assert f(10, (0, 0x18, 5, 1)) == "1.0" and f(4, (0, 0x18, 5, 1)) == "0.4"
    assert f(28, (0, 0x18, 4, 1)) == "2.8" and f(5, (0, 0x18, 4, 1)) == "0.5"
    assert f(3, (0, 0x18, 2, 0)) == "3" and f(44, (0, 0x18, 2, 0)) == "44"
    assert f(0xFFFF, (0, 0x18, 5, 1)) == "-----" and f(0xFFFE, (0, 0x18, 5, 1)) == "-----"
    assert f(0xFFFF, (0, 0x18, 3, 0)) == "---" and f(0xFFFF, (0, 0x18, 2, 0)) == "--"
    assert f(120, (0, 0x08, 5, 2)) == "1.2" and f(120, (0, 0x18, 5, 2)) == "1.20"


# ---- whole-image render: only screen 29 changes -----------------------------------------------


# ---- orientation (rear-facing, owner 2026-09-26) -------------------------------------------------


def test_every_tile_sits_beside_its_rotated_wheel():
    """Rear-facing (owner 2026-09-26): front at the bottom, driver on the right. Each wheel's
    tile is the one whose position is nearest that wheel's rotated tyre."""
    cx, top, sc = 300, 84, 0.70
    X = lambda v: round(cx + (v - 260) * sc)
    Y = lambda v: round(top + (v - 40) * sc)
    tyres = {"fl": (X(161), Y(95)), "fr": (X(331), Y(95)), "rl": (X(161), Y(430)),
             "rr": (X(331), Y(430))}
    body_top, body_bot = Y(40), Y(560)
    t = levelpage.LAYOUT["tiles"]
    for orientation in levelpage.ORIENTATIONS:
        dy = levelpage.SHIFT_Y[orientation]              # the van group's lift, tiles included
        tiles = {k: (t[k][0], t[k][1] + dy) for k in ("fl", "fr", "rl", "rr")}
        for wheel, (tx, ty) in tyres.items():
            if orientation != "front":                    # 180 degrees about the van centre
                tx, ty = 2 * cx - tx, body_top + body_bot - ty + dy
            pos = levelpage.tile_pos(wheel, orientation)
            assert pos == tiles[levelpage.tile_slot(wheel, orientation)]
            nearest = min(tiles.items(),
                          key=lambda kv: (kv[1][0] + t["w"] // 2 - tx) ** 2
                          + (kv[1][1] + t["h"] // 2 - ty) ** 2)
            assert nearest[1] == pos, (orientation, wheel)


def test_tiles_follow_the_drawn_van():
    """The drawn tyres (levelpage.van) are the mockup's rotated 180 degrees about the body centre,
    and each wheel's tile is the one nearest its DRAWN tyre: rotating the van without moving the
    tiles (or the reverse) fails this."""
    t = levelpage.LAYOUT["tiles"]
    front, rear = levelpage.van("front"), levelpage.van("rear-low")
    bx, by, bw, bh, _r = front["body"]
    assert rear["body"] == front["body"]                     # rotated in place: same box
    for a, b in zip(front["tyres"] + front["mirrors"] + [front["windshield"]] + front["dashes"],
                    rear["tyres"] + rear["mirrors"] + [rear["windshield"]] + rear["dashes"]):
        x, y, w, h = a
        assert b == (2 * bx + bw - x - w, 2 * by + bh - y - h, w, h), (a, b)
    assert rear["windshield"][1] > by + bh // 2 > front["windshield"][1]
    assert rear["front"][1] == by + bh + 4                    # FRONT below the van, y 452
    # "rear" is "rear-low" lifted by SHIFT_Y, every box and FRONT alike (owner 2026-09-27)
    dy, raised = levelpage.SHIFT_Y["rear"], levelpage.van("rear")
    lift = lambda b: (b[0], b[1] + dy) + tuple(b[2:])
    assert raised["body"] == lift(rear["body"]) and raised["front"] == lift(rear["front"])
    for k in ("tyres", "mirrors", "dashes"):
        assert raised[k] == [lift(b) for b in rear[k]], k
    for orientation in levelpage.ORIENTATIONS:
        tiles = {k: levelpage.tile_pos(k2, orientation) for k2 in ("fl", "fr", "rl", "rr")
                 for k in [levelpage.tile_slot(k2, orientation)]}
        tyres = levelpage.van(orientation)["tyres"]
        for wheel, (x, y, w, h) in zip(("fl", "fr", "rl", "rr"), tyres):
            cx, cy = x + w / 2, y + h / 2
            nearest = min(tiles, key=lambda k: (tiles[k][0] + t["w"] / 2 - cx) ** 2
                          + (tiles[k][1] + t["h"] / 2 - cy) ** 2)
            assert nearest == levelpage.tile_slot(wheel, orientation), (orientation, wheel)


def test_rear_tile_map_is_the_owner_mapping():
    assert levelpage.ORIENTATIONS == ("front", "rear", "rear-low")
    for o in ("rear", "rear-low"):
        assert {k: levelpage.tile_slot(k, o) for k in ("fl", "fr", "rl", "rr")} == \
            {"fl": "rr", "fr": "rl", "rl": "fr", "rr": "fl"}
    assert {k: levelpage.tile_slot(k, "front") for k in ("fl", "fr", "rl", "rr")} == \
        {"fl": "fl", "fr": "fr", "rl": "rl", "rr": "rr"}


# ---- controls: the Blocks-carried stepper and Zero Level (spec 2026-09-26 section 2) -----------


def test_controls_layout_fits_and_clears_the_tiles():
    x, y, w, h, _r = levelpage.LAYOUT["blocks"]["box"]
    for name in ("minus", "plus"):
        bx, by, bw, bh = levelpage.CONTROLS[name]
        assert x < bx and bx + bw < x + w and y < by and by + bh < y + h, name
    for o in levelpage.ORIENTATIONS:
        zx, zy, zw, zh = levelpage.controls_rects(o)["zero"]
        for key in ("fl", "fr", "rl", "rr"):
            tx, ty = levelpage.tile_pos(key, o)
            assert zy >= ty + 74 + 4 or zy + zh + 4 <= ty or zx >= tx + 130 + 4 \
                or zx + zw + 4 <= tx, (o, key)


# ---- controls wired into add_level_page (spec 2026-09-26 sections 3-4) -------------------------


def test_recipe_ops_carries_controls():
    ops = levelpage.recipe_ops(preview=False, controls=True)
    assert ops[2]["params"]["controls"] is True
    assert "controls" not in levelpage.recipe_ops(preview=False)[2]["params"]


# ---- the raised rear layout (owner 2026-09-27) ---------------------------------------------------


# ---- RV-C custom DGNs (owner 2026-09-28) -------------------------------------------------------


# ---- readout fills and the nav icon (v38, owner 2026-09-28) ------------------------------------


# ---- the LEVEL SETUP popup (v42, owner 2026-09-29) -----------------------------------------------


def test_setup_touches_do_not_overlap_and_sit_in_the_dialog():
    from fireflymcf import levelsetup
    rr = levelsetup.rects()
    names = list(rr)
    hit = lambda a, b: a[0] <= b[0] + b[2] and b[0] <= a[0] + a[2] and \
        a[1] <= b[1] + b[3] and b[1] <= a[1] + a[3]
    assert not [(a, b) for i, a in enumerate(names) for b in names[i + 1:] if hit(rr[a], rr[b])]
    x, y, w, h, _r = levelsetup.DIALOG
    assert all(x <= r[0] and r[0] + r[2] <= x + w and y <= r[1] and r[1] + r[3] <= y + h
               for r in rr.values())


# ---- repaint-on-change and the popup background (v43) --------------------------------------------
