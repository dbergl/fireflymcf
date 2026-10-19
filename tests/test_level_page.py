"""The LEVEL page (screen 29, object 367): layout, preview body, live body and its switch arms."""
import pytest

from fireflymcf import guidata, levelfeed, levelpage, records

V33 = "builds/mcf-13.6-BattHeatGlyph-v33.bin"
ARROW = levelpage.ARROW       # the real glyph char; on bare v33 it is still the placeholder
PATH1 = (2, 4, 11, 0x20, 0x21)


@pytest.fixture(scope="module")
def v33():
    return open(V33, "rb").read()


@pytest.fixture(scope="module")
def live(v33):
    cells = {name: 700 + i for i, (name, _w) in enumerate(levelfeed.CELLS)}
    out, info = levelpage.build_body(v33, cells=cells, arrow=ARROW)
    return cells, out, info


def _explicit(r):
    """Every colour field mode 3, except a wt-11 text background pair, which may be mode 5
    (explicit transparency, not an inherit: pagedraw's module docstring)."""
    for role, m, _v in r.colors:
        if r.wtype == 11 and role in ("fillB", "fillB2"):
            assert m in (3, 5), (r.wtype, role, m)
        else:
            assert m == 3, (r.wtype, role, m)


def _check_record(r):
    assert r.header[1] == 0x50, (r.wtype, r.header.hex())
    # Exempt: the wt-9 format directive (draws nothing; pagedraw's donor 793[4]) and the wt-6
    # switch (switchbuild's stock enable-pill donor 9[9], align 0, draws only its arms).
    if r.wtype not in (6, 9):
        assert r.align != 0, (r.wtype, r.header.hex())
    if r.wtype in PATH1 + (8,):
        assert r.colors, r.wtype
        _explicit(r)
    if r.wtype in (8, 11):
        assert r.font is not None and r.font != 0xFF


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


def test_preview_body_has_no_switch_and_no_inherited_style(v33):
    out, info = levelpage.build_body(v33, cells=None, arrow=ARROW)
    assert out == v33
    recs = records.object_records(info["body"], 0)
    assert len(recs) == info["records"]
    assert not any(r.wtype == 6 for r in recs)
    assert not any(r.wtype in (8, 9) for r in recs)
    for r in recs:
        _check_record(r)
    texts = [r.label for r in recs if r.wtype == 11]
    for want in ("FRONT", "Attitude", "Blocks", "PITCH", "ROLL", "Needed", "Carried",
                 "1.0°", "0.4°", "nose down", "driver side high", "NOT LEVEL",
                 "SHORT ON BLOCKS", "LEVEL", "8", "5"):
        assert want in texts, want
    assert "block" not in texts and texts.count("blocks") == 3


def test_live_body_switches_and_arms(live):
    cells, out, info = live
    sw = [r for r in records.object_records(info["body"], 0) if r.wtype == 6]
    by_slot = {r.value_slot: r.count for r in sw}
    for k in ("fl", "fr", "rl", "rr"):
        assert by_slot[cells[f"wheel_{k}"]] == 3
    assert by_slot[cells["banner"]] == 4 and by_slot[cells["short"]] == 2
    assert by_slot[cells["pitch_word"]] == 3 and by_slot[cells["roll_word"]] == 3
    assert len(sw) == 8
    assert all(r.header[4] & 0x20 for r in sw)


def test_live_body_readouts_are_enrolled_on_their_cells(live):
    cells, _out, info = live
    recs = records.object_records(info["body"], 0)
    for r in recs:
        _check_record(r)
    ro = {r.value_slot: r for r in recs if r.wtype == 8}
    assert set(ro) == {cells[n] for n in ("pitch", "roll", "total", "carried")}
    assert all(r.header[4] & 0x20 for r in ro.values())


def _arm_records(out, idx):
    gs, _ = guidata.guidata_extent(out)
    return records.object_records(out, gs + guidata.object_tables(out)[1][idx])


def _match(out, idx):
    gs, _ = guidata.guidata_extent(out)
    mt = guidata.find_match_table(out)
    return int.from_bytes(out[gs + mt + 2 * idx:gs + mt + 2 * idx + 2], "little")


def test_every_band_puts_the_default_arm_first_in_the_match_table(live):
    _cells, out, info = live
    for name, s in info["switches"].items():
        got = [_match(out, s["arms_base"] + k) for k in range(s["count"])]
        assert got == s["match"], name
        assert got[0] == 0xFFFF, name
        assert 0xFFFF not in got[1:], name


def _box(r):
    x, y, w, h = r.geom[:4]
    return x, y, x + w, y + h


def test_every_arm_paints_its_whole_area_first(live):
    """Each arm's FIRST record is an opaque shape (capsule or fill) covering the bounding box of
    every record in the arm, and every arm of a switch covers the same area (spec 5.2)."""
    _cells, out, info = live
    for name, s in info["switches"].items():
        areas = set()
        for k in range(s["count"]):
            recs = _arm_records(out, s["arms_base"] + k)
            for r in recs:
                _check_record(r)
            first = recs[0]
            is_word = name.endswith("_word")
            if is_word:     # a word arm is one opaque label, or the blank fill of its box
                assert first.wtype in (4, 11), (name, k, first.wtype)
                if first.wtype == 11:
                    assert all(m == 3 for _r, m, _v in first.colors), (name, k)
            else:
                assert first.wtype in (4, 0x21), (name, k, first.wtype)
            x0, y0, x1, y1 = _box(first)
            areas.add((x0, y0, x1, y1))
            for r in recs[1:]:
                if r.wtype == 9:
                    continue
                px, py = r.geom[0], r.geom[1]
                assert x0 <= px <= x1 and y0 <= py <= y1, (name, k, r.wtype, r.geom)
        assert len(areas) == 1, (name, areas)


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

import json
import struct

from fireflymcf import (followchannel, glyphs, metrics, newbutton, readout, recipe, register,
                              render, status_map, statusbind, theme, thumb, validate)

STOCK = "firefly-13.6/mcf.bin"
V33_RECIPE = "builds/mcf-13.6-batt-heat-glyph.ffproj.json"
LEVEL_ROOT = 32
SHOTS = None          # set to a directory to keep the state renders (the report's PNGs)


def _project(preview: bool) -> recipe.Project:
    p = json.load(open(V33_RECIPE))
    p["build_name"] = "level-test"
    p["ops"] += levelpage.recipe_ops(preview=preview)   # rename_label + install_glyph + add_level_page
    return recipe.Project.from_json(json.dumps(p))


def _compile(compile_with_handles, preview: bool):
    """`(result, level handles)`, through the suite's shared compile cache (conftest)."""
    res, handles = compile_with_handles(_project(preview), open(STOCK, "rb").read())
    assert res.ok, res.errors
    return res, handles["level"]


@pytest.fixture(scope="module", params=[True, False], ids=["preview", "live"])
def built(request, compile_with_handles):
    res, h = _compile(compile_with_handles, request.param)
    return request.param, res, h


@pytest.fixture(scope="module")
def live_build(compile_with_handles):
    return _compile(compile_with_handles, False)


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


def test_compiles_and_is_unverified(built):
    _preview, res, _h = built
    assert "level" in res.unverified


def test_title_renamed_and_root_records_kept(built):
    _preview, res, h = built
    gs, _ = guidata.guidata_extent(res.data)
    tab = guidata.object_tables(res.data)[1]
    recs = records.object_records(res.data, gs + tab[LEVEL_ROOT])
    assert [res.data[r.offset:r.end].hex() for r in recs[:6]] == h["root_records"]
    assert recs[3].label.strip() == "LEVEL"
    assert recs[6].wtype in (5, 6, 0x2E)


def test_arrow_glyph_installed_pixel_for_pixel(built):
    _preview, res, _h = built
    slot, char = levelpage.ARROW
    font = glyphs.font_for_slot(res.data, slot)
    g = glyphs.decode_glyph(res.data, font.glyph_index(ord(char)))
    want = [tuple(int(c, 16) for c in row) for row in levelpage.arrow_rows()]
    assert (g.width, g.height) == (20, 14)
    assert [tuple(r) for r in g.rows] == want
    assert g.y == (metrics.font_metrics(res.data, slot).height - 14) // 2


def test_arrow_clears_every_two_digit_layer_count(built):
    """The layers readout is right-aligned to x0+63 (FUN_002557C0: x = pen - (w - 1)); the arrow
    box ends at x0+27. Every value 10..99 must leave >= ARROW_CLEARANCE empty columns between."""
    _preview, res, _h = built
    ax, _ay, aw, _ah = levelpage.TILE["arrow"]
    arrow_last = ax + aw                                  # inclusive box, relative to x0
    slot, char = levelpage.ARROW
    assert metrics.text_width(res.data, slot, char) <= aw + 1
    widest = max(metrics.text_width(res.data, levelpage.F_TILE_LAYERS, str(v))
                 for v in range(10, 100))
    first = levelpage.TILE["layers_right"] - (widest - 1)
    assert first - arrow_last - 1 >= levelpage.ARROW_CLEARANCE, (first, arrow_last)


def test_blocks_word_clears_every_two_digit_layer_count(built):
    """The layers readout's last column is x0 + layers_right (FUN_002557C0 right-aligns to the
    pen); "blocks" starts at x0 + TILE["blocks"][0]. Every value 10..99 leaves >= BLOCKS_GAP
    empty columns before "blocks" (the owner read "44blocks" at a 2 px gap), and "blocks" stays
    inside the 130 px tile."""
    _preview, res, _h = built
    widest = max(metrics.text_width(res.data, levelpage.F_TILE_LAYERS, str(v))
                 for v in range(10, 100))
    layers_last = levelpage.TILE["layers_right"]
    bx, _by = levelpage.TILE["blocks"]
    first = levelpage.TILE["layers_right"] - (widest - 1)
    assert first <= layers_last
    assert bx - layers_last - 1 >= levelpage.BLOCKS_GAP, (bx, layers_last)
    blocks_w = metrics.text_width(res.data, levelpage.F_TILE_BLOCKS, "blocks")
    assert bx + blocks_w + 2 <= levelpage.LAYOUT["tiles"]["w"], (bx, blocks_w)


def test_live_entries(built):
    preview, res, h = built
    vs = [(i, v) for i, v in enumerate(guidata.variable_table(res.data)) if v.dgn_low == 0xFFB0]
    if preview:
        assert vs == []
        return
    assert len(vs) == 14 and all(v.raw[5] == 0 and v.raw[4] in (1, 2, 3, 4) for _i, v in vs)
    assert [i for i, _v in vs] == h["vars"]
    for (_i, v), (_name, rec, byte, mode) in zip(vs, levelfeed.VARS):
        assert bytes(v.raw) == bytes([0xB0, 0xFF, 0x00 | (1 << 2) | (mode << 4), byte, rec, 0, 0, 0])
    assert len(statusbind.registered_dgns(res.data)) <= statusbind.REGISTRY_CAP


def test_live_cells_routine_and_switches(live_build):
    res, h = live_build
    data = res.data
    table = status_map.cell_table(data)
    assert sorted(h["cells"]) == sorted(n for n, _w in levelfeed.CELLS)
    for name, idx in h["cells"].items():
        assert table[idx] == h["cell_addrs"][name], name
    gc = theme.guicode_section(data)
    code = data[gc.offset:gc.offset + gc.length]
    for a in list(h["cell_addrs"].values()) + [register.status_slot_addr(v) for v in h["vars"]]:
        assert struct.pack("<I", a) in code, hex(a)
    chain = followchannel.chain_from_hook(data)
    assert h["routine_addr"] in chain
    gs, _ = guidata.guidata_extent(data)
    body = records.object_records(data, gs + guidata.object_tables(data)[1][367])
    sw = {r.value_slot: r for r in body if r.wtype == 6}
    assert len(sw) == 8 and all(r.header[4] & 0x20 for r in sw.values())
    for name, s in h["arms"].items():
        assert sw[h["cells"][name]].count == s["count"], name


# ---- mutation checks: a desynced VARS or a hollow routine must not compile silently -----------

def _cache_seed_before_level(compile_prefix_seed, proj):
    """A fresh cache dict holding just the prefix through `level-arrow` (every op before `level`
    itself), so only the `level` op re-applies under the test's monkeypatch; a cache holding the
    whole project would replay its already-cached, unmutated `level` result and never re-run
    apply()."""
    enabled = [s for s in proj.ops if s.enabled]
    return compile_prefix_seed(proj, open(STOCK, "rb").read(), len(enabled) - 1)


def test_dropped_var_entry_is_not_silently_compiled(compile_prefix_seed, monkeypatch):
    """`levelfeed.VARS` drives both the appended-filter count (recipe_ops's list comprehension)
    and the routine's per-field pool references (`S("pitch")`, `S("carried")`, ...), which are
    hardcoded literal names, not driven by `VARS` at all. Dropping one entry keeps `_check_names`
    happy (both the built `slots` dict and its own reference tuple read the same shortened
    `VARS`), so the desync is invisible until `build()` asks for the missing name by its literal
    string -- an immediate `KeyError`, not a graceful `RecipeError` or a `verify()` assertion.
    Silent corruption is what this rules out: the op does not produce a build with, say, a
    "carried" cell that quietly reads someone else's slot."""
    proj = _project(False)
    stock = open(STOCK, "rb").read()
    cache = _cache_seed_before_level(compile_prefix_seed, proj)
    monkeypatch.setattr(levelfeed, "VARS", levelfeed.VARS[:-1])
    with pytest.raises(KeyError, match="carried"):
        recipe.compile_cached(proj, stock, cache)


def test_hollow_routine_fails_verify(compile_prefix_seed, monkeypatch):
    """A routine that never copies a single slot to a cell -- `levelfeed.build` replaced with one
    that emits nothing but the tail branch -- still passes every apply-time step (the detour and
    the section grow do not inspect the routine's content), so the defect only surfaces at
    `AddLevelPage.verify`'s `struct.pack("<I", addr) in code` checks. Routed through
    `validate.validate` (which turns a verify `AssertionError` into a FAIL verdict, `tests/
    test_validate.py`'s own pattern) rather than `recipe.compile_project` directly, since the
    latter lets that AssertionError propagate instead of returning a not-ok result."""
    def hollow_build(plan, code_load_addr):
        entry = code_load_addr + readout.ENTRY_OFF
        return bytes(thumb.b_w(entry, plan.tail_target)), []

    proj = _project(False)
    stock = open(STOCK, "rb").read()
    cache = _cache_seed_before_level(compile_prefix_seed, proj)
    monkeypatch.setattr(levelfeed, "build", hollow_build)
    monkeypatch.setattr(recipe, "compile_project",
                        lambda p, s: recipe.compile_cached(p, s, cache))
    res = validate.validate(proj, stock)
    assert not res.passed
    assert "no routine carries" in res.report, res.report


# ---- render states --------------------------------------------------------------------------

# Every render state is the cell set the LEVEL routine itself writes for a 0x0FFB0 frame set:
# `test_levelfeed._run` runs `levelfeed.build`'s code in the Thumb emulator over the frames.
# Record 1: pitch u16 @1, roll u16 @3, flags @5 (bits 0-1 level, 2-3 short, 4-5 fault), dirs @6
# (bits 0-1 nose up, 2-3 nose down, 4-5 driver high, 6-7 passenger high), total @7. Record 2:
# layers FL/FR/RL/RR @1-4. Record 3: lift (0.1 in) @1-4. Record 4: carried @6.
from tests.test_levelfeed import FAULT, _run as _emulate      # noqa: E402

NA = 0xFFFF
# The mockup's example: pitch 1.0 nose down, roll 0.4 driver side high, short, not level.
EXAMPLE_FRAMES = {1: "01 0A 00 04 00 04 14 08", 2: "02 03 04 00 01 FF FF FF",
                  3: "03 1C 21 00 05 FF FF FF", 4: "04 36 06 AD 02 14 05 19"}
# Level: every wheel 0, banner LEVEL, nothing short, no direction word.
LEVEL_FRAMES = {1: "01 01 00 02 00 01 00 00", 2: "02 00 00 00 00 FF FF FF",
                3: "03 00 00 00 00 FF FF FF", 4: "04 36 06 AD 02 14 05 19"}
# The example with 10 blocks carried and the short flag clear.
NOT_SHORT_FRAMES = EXAMPLE_FRAMES | {1: "01 0A 00 04 00 00 14 08", 4: "04 36 06 AD 02 14 0A 19"}
EXAMPLE_STATE = _emulate(EXAMPLE_FRAMES)


def test_example_frames_reproduce_the_mockup_state():
    assert EXAMPLE_STATE == dict(
        pitch=10, roll=4, lay_fl=3, lay_fr=4, lay_rl=0, lay_rr=1,
        lift_fl=28, lift_fr=33, lift_rl=0, lift_rr=5, wheel_fl=1, wheel_fr=1, wheel_rl=0,
        wheel_rr=1, banner=0, short=1, total=8, carried=5, pitch_word=2, roll_word=1)


def _render_state(res, h, values: dict, name: str):
    """Screen 29 (root 32) with display cells forced to `values` (cell name -> u16): switches by
    `slot_values` (layout's value-match walk), readouts by render's preview `cell_values` (the
    panel's mode-0 formatter under each readout's own directive)."""
    sv = {h["cells"][n]: v for n, v in values.items()}
    img = render.render_screen_image(res.data, LEVEL_ROOT, slot_values=sv,
                                     preview={"cell_values": sv}).convert("RGB")
    if SHOTS:
        img.save(f"{SHOTS}/{name}.png")
    return img


def _near(a, b, tol=12):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


ORIENTATION = "rear"   # levelpage.recipe_ops' default, which _project compiles


def _tile_mid(key):
    """Wheel `key`'s tile (left, vertical centre), placed by `tile_slot` as the build draws it."""
    x0, top = levelpage.tile_pos(key, ORIENTATION)
    return x0, top + levelpage.LAYOUT["tiles"]["h"] // 2


def _ink_in(img, box, colour=levelpage.WHITE, tol=40):
    x0, y0, x1, y1 = box
    return any(_near(img.getpixel((x, y)), colour, tol)
               for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))


def _readout_boxes():
    """The screen box each readout's digits occupy (value right edge back ~3 digits)."""
    R, Y = levelpage.LAYOUT["value_right"], levelpage.LAYOUT["value_y"]
    boxes = {n: (R - 60, Y[n] + 4, R - 14 if n in ("pitch", "roll") else R, Y[n] + 26)
             for n in ("pitch", "roll", "total", "carried")}
    for k in ("fl", "fr", "rl", "rr"):
        x0, yc = _tile_mid(k)
        boxes[f"lay_{k}"] = (x0 + 40, yc - 26, x0 + 63, yc - 6)
        boxes[f"lift_{k}"] = (x0 + 30, yc + 12, x0 + 60, yc + 28)
    return boxes


def _arrow_box(key):
    x0, yc = _tile_mid(key)
    ax, ay, aw, ah = levelpage.TILE["arrow"]
    return x0 + ax, yc + ay, x0 + ax + aw, yc + ay + ah


def test_render_example(live_build):
    res, h = live_build
    img = _render_state(res, h, EXAMPLE_STATE, "example")
    x0, yc = _tile_mid("fl")
    assert _near(img.getpixel((x0 + 100, yc + 30)), levelpage.ACCENT)        # amber FL tile
    x0, yc = _tile_mid("rl")
    assert _near(img.getpixel((x0 + 5, yc)), levelpage.GREEN)               # green RL tile
    sx, sy, _sw, _sh, _r = levelpage.LAYOUT["short"]
    assert _near(img.getpixel((sx + 6, sy + 4)), levelpage.ACCENT)           # SHORT ON BLOCKS
    bx, by, _bw, _bh, _r = levelpage.LAYOUT["banner"]
    assert _near(img.getpixel((bx + 6, by + 4)), levelpage.RED)             # NOT LEVEL
    boxes = _readout_boxes()
    for n in ("pitch", "roll", "total", "carried", "lay_fl", "lay_fr", "lay_rr",
              "lift_fl", "lift_fr", "lift_rr"):
        assert _ink_in(img, boxes[n]), f"no digits drawn for {n}"
    for k in ("fl", "fr", "rr"):
        assert _ink_in(img, _arrow_box(k)), f"no arrow on {k}"


def test_render_level(live_build):
    res, h = live_build
    st = _emulate(LEVEL_FRAMES)
    assert (st["banner"], st["short"], st["pitch_word"], st["roll_word"]) == (1, 0, 0, 0)
    img = _render_state(res, h, st, "level")
    for k in ("fl", "fr", "rl", "rr"):
        x0, yc = _tile_mid(k)
        assert _near(img.getpixel((x0 + 5, yc)), levelpage.GREEN), k
    bx, by, _bw, _bh, _r = levelpage.LAYOUT["banner"]
    assert _near(img.getpixel((bx + 6, by + 4)), levelpage.GREEN)
    sx, sy, _sw, _sh, _r = levelpage.LAYOUT["short"]
    assert _near(img.getpixel((sx + 6, sy + 4)), levelpage.NAVY)


def test_render_no_sensor(live_build):
    res, h = live_build
    st = _emulate({})                                   # no frames: every slot at its NA boot value
    img = _render_state(res, h, st, "no-sensor")
    bx, by, _bw, _bh, _r = levelpage.LAYOUT["banner"]
    assert _near(img.getpixel((bx + 6, by + 4)), levelpage.GREY)
    x0, yc = _tile_mid("fl")
    assert _near(img.getpixel((x0 + 5, yc)), levelpage.GREY)
    boxes = _readout_boxes()
    for n in ("pitch", "roll", "total", "carried"):      # n/a draws as the formatter's dashes
        assert _ink_in(img, boxes[n]), f"no dashes drawn for {n}"


def test_render_fault(live_build):
    res, h = live_build
    st = _emulate(FAULT)                                # the spec's fault records
    img = _render_state(res, h, st, "fault")
    bx, by, _bw, _bh, _r = levelpage.LAYOUT["banner"]
    assert _near(img.getpixel((bx + 6, by + 4)), levelpage.RED)               # SENSOR FAULT
    for k in ("fl", "fr", "rl", "rr"):                                        # tiles n/a: grey
        x0, yc = _tile_mid(k)
        assert _near(img.getpixel((x0 + 5, yc)), levelpage.GREY), k
    for name in ("pitch_word", "roll_word"):                                  # words blank
        x, y, w, hh = levelpage._word_box(res.data, name)
        assert not _ink_in(img, (x, y, x + w, y + hh), levelpage.GREY, 30), name
    sx, sy, _sw, _sh, _r = levelpage.LAYOUT["short"]
    assert _near(img.getpixel((sx + 6, sy + 4)), levelpage.NAVY)              # no SHORT


def test_render_short(live_build):
    res, h = live_build
    st = _emulate(NOT_SHORT_FRAMES)
    assert st["short"] == 0 and st["carried"] == 10
    img = _render_state(res, h, st, "not-short")
    sx, sy, _sw, _sh, _r = levelpage.LAYOUT["short"]
    assert _near(img.getpixel((sx + 6, sy + 4)), levelpage.NAVY)
    img = _render_state(res, h, EXAMPLE_STATE, "short")
    assert _near(img.getpixel((sx + 6, sy + 4)), levelpage.ACCENT)


def _crop(img, box):
    return img.crop((box[0], box[1], box[2] + 1, box[3] + 1)).tobytes()


def test_render_two_digit_layers(live_build):
    """The widest two-digit layer count draws inside the FL tile and leaves the arrow's pixels
    exactly as a one-digit count does."""
    res, h = live_build
    widest = max(range(10, 100),
                 key=lambda v: metrics.text_width(res.data, levelpage.F_TILE_LAYERS, str(v)))
    one = _render_state(res, h, dict(EXAMPLE_STATE, lay_fl=3), "one-digit")
    two = _render_state(res, h, dict(EXAMPLE_STATE, lay_fl=widest), "two-digit")
    assert _crop(one, _arrow_box("fl")) == _crop(two, _arrow_box("fl"))
    assert _crop(one, _readout_boxes()["lay_fl"]) != _crop(two, _readout_boxes()["lay_fl"])
    # no spill: the ring 1..2 px outside the tile is the page's navy (or the van's grey tyre)
    x0, top = levelpage.tile_pos("fl", ORIENTATION)
    w, hh = levelpage.LAYOUT["tiles"]["w"], levelpage.LAYOUT["tiles"]["h"]
    for d in (1, 2):
        ring = [(x, top - d) for x in range(x0, x0 + w + 1)] + \
               [(x, top + hh + d) for x in range(x0, x0 + w + 1)] + \
               [(x0 - d, y) for y in range(top, top + hh + 1)] + \
               [(x0 + w + d, y) for y in range(top, top + hh + 1)]
        for p in ring:
            assert _near(two.getpixel(p), levelpage.NAVY), (p, two.getpixel(p))
    # and the digits themselves stay left of "blocks" and right of the arrow
    arrow_right = _arrow_box("fl")[2]
    x0, yc = _tile_mid("fl")
    ink = [x for x in range(x0, x0 + levelpage.TILE["blocks"][0])
           for y in range(yc - 30, yc - 4)
           if _near(two.getpixel((x, y)), levelpage.WHITE, 60) and x > arrow_right]
    assert ink and min(ink) - arrow_right - 1 >= levelpage.ARROW_CLEARANCE
    # and "blocks" starts >= BLOCKS_GAP columns after the digits' last ink column
    bx = x0 + levelpage.TILE["blocks"][0]
    word = [x for x in range(bx - 8, x0 + levelpage.LAYOUT["tiles"]["w"])
            for y in range(yc - 30, yc - 4)
            if _near(two.getpixel((x, y)), levelpage.WHITE, 60)]
    digits = [x for x in ink if x < bx]
    assert word and digits and min(x for x in word if x >= bx - 1) - max(digits) - 1 \
        >= levelpage.BLOCKS_GAP, (max(digits), min(word))


def test_format_cell_value_models_the_panel_formatter():
    f = render.format_cell_value
    assert f(10, (0, 0x18, 5, 1)) == "1.0" and f(4, (0, 0x18, 5, 1)) == "0.4"
    assert f(28, (0, 0x18, 4, 1)) == "2.8" and f(5, (0, 0x18, 4, 1)) == "0.5"
    assert f(3, (0, 0x18, 2, 0)) == "3" and f(44, (0, 0x18, 2, 0)) == "44"
    assert f(0xFFFF, (0, 0x18, 5, 1)) == "-----" and f(0xFFFE, (0, 0x18, 5, 1)) == "-----"
    assert f(0xFFFF, (0, 0x18, 3, 0)) == "---" and f(0xFFFF, (0, 0x18, 2, 0)) == "--"
    assert f(120, (0, 0x08, 5, 2)) == "1.2" and f(120, (0, 0x18, 5, 2)) == "1.20"


# ---- whole-image render: only screen 29 changes -----------------------------------------------

COACH_CONFIG = "build/coach-wd.json"


def _render_bytes(data: bytes, root: int, tmp, shared_memo) -> bytes:
    """One page's pixels through `cli render` (config slots, derived slots, preview block), the
    same path an owner's render takes, at the reference coach's configuration. Shared across
    tests and workers: every parameter below renders the same v33 pages."""
    import hashlib
    from pathlib import Path
    from PIL import Image
    from fireflymcf import cli

    def produce():
        img = Path(tmp) / f"mcf-{hashlib.sha256(data).hexdigest()[:12]}.bin"
        if not img.exists():
            img.write_bytes(data)
        out = Path(tmp) / f"root-{root}.png"
        assert cli.main(["render", str(img), str(out), "--root", str(root),
                         "--config", COACH_CONFIG]) == 0
        return Image.open(out).convert("RGB").tobytes()

    config = Path(COACH_CONFIG).read_bytes()
    return shared_memo("render", [hashlib.sha256(data).hexdigest(), str(root), config], produce)


@pytest.mark.parametrize("build", ["builds/mcf-13.6-LevelPreview-v34a.bin",
                                   "builds/mcf-13.6-Level-v34b.bin",
                                   "builds/mcf-13.6-LevelControls-v35.bin"])
def test_only_screen_29_changes(build, tmp_path, shared_memo):
    """Every page renders pixel-identical to v33 except screen 29 (root 32), both images at the
    reference coach's configuration (a no-config render drops the nav rail's sixth icon on both
    sides alike, but would not exercise the configured pages)."""
    import os
    from fireflymcf import layout
    if not os.path.exists(COACH_CONFIG):
        pytest.skip(f"{COACH_CONFIG} is absent (gitignored); the whole-page diff needs the "
                    "reference coach's configuration")
    v33 = open(V33, "rb").read()
    v34 = open(build, "rb").read()
    roots = sorted(set(layout.screen_object_map(v33).values()))
    assert roots == sorted(set(layout.screen_object_map(v34).values()))
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    diffs = [r for r in roots
             if _render_bytes(v33, r, a, shared_memo) != _render_bytes(v34, r, b, shared_memo)]
    assert diffs == [LEVEL_ROOT], diffs


def test_short_text_is_centred_in_its_strip():
    """Owner, 2026-09-26: SHORT ON BLOCKS sits vertically centred in its strip, and its label box
    stays inside the strip (the earlier y=442 box ran 2 px below it)."""
    import numpy as np
    from fireflymcf import glyphs, metrics, render
    d = open("builds/mcf-13.6-BattHeatGlyph-v33.bin", "rb").read()
    x, y, w, h, _r = levelpage.LAYOUT["short"]
    ty = levelpage.LAYOUT["short_text_y"]
    parts = render.panel_text_images(d, levelpage.F_SHORT, "SHORT ON BLOCKS", (255, 255, 255),
                                     table=glyphs.resolve_glyph_table(d))
    rows = [dy + i for _dx, dy, img in parts
            for i, r in enumerate(np.array(img)[:, :, 3]) if r.any()]
    above, below = ty + min(rows) - y, (y + h - 1) - (ty + max(rows))
    assert abs(above - below) <= 1, (above, below)
    cell = metrics.font_metrics(d, levelpage.F_SHORT).height - 1
    assert y <= ty and ty + cell <= y + h, (ty, cell, y, h)


# ---- orientation (rear-facing, owner 2026-09-26) -------------------------------------------------
import hashlib


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


def test_rear_body_draws_front_below_and_tiles_at_their_slots(v33):
    """The rear bodies' records: "FRONT" below the van inside the page, and each wheel's tile
    capsule (preview) and switch (live) at `tile_slot`'s position, so the two cannot disagree."""
    by_bot = levelpage.LAYOUT["van"]["body"][1] + levelpage.LAYOUT["van"]["body"][3]
    _out, prev = levelpage.build_body(v33, cells=None, arrow=ARROW, orientation="rear")
    recs = records.object_records(prev["body"], 0)
    front = [r for r in recs if r.wtype == 11 and r.label == "FRONT"]
    assert len(front) == 1 and front[0].geom[1] == by_bot + 4 + levelpage.SHIFT_Y["rear"]
    assert front[0].geom[1] + front[0].geom[3] <= 479
    for r in recs:
        _check_record(r)
    t = levelpage.LAYOUT["tiles"]
    caps = {(r.geom[0], r.geom[1]): r for r in recs if r.wtype == 0x21
            and (r.geom[2], r.geom[3]) == (t["w"], t["h"])}
    colour = {k: levelpage.GREEN if levelpage.EXAMPLE["tiles"][k] is None else levelpage.ACCENT
              for k in ("fl", "fr", "rl", "rr")}
    for key in ("fl", "fr", "rl", "rr"):
        r = caps[levelpage.tile_pos(key, "rear")]
        c = colour[key]
        assert dict((role, v) for role, _m, v in r.colors)["fillA"] == \
            (c[0] << 16) | (c[1] << 8) | c[2], key
    cells = {name: 700 + i for i, (name, _w) in enumerate(levelfeed.CELLS)}
    _o, live = levelpage.build_body(v33, cells=cells, arrow=ARROW, orientation="rear")
    sw = {r.value_slot: r for r in records.object_records(live["body"], 0) if r.wtype == 6}
    for key in ("fl", "fr", "rl", "rr"):
        assert tuple(sw[cells[f"wheel_{key}"]].geom[:2]) == levelpage.tile_pos(key, "rear")


def test_bad_orientation_is_refused(v33):
    with pytest.raises(levelpage.LevelPageError):
        levelpage.build_body(v33, cells=None, arrow=ARROW, orientation="sideways")
    ops = levelpage.recipe_ops(preview=True)
    ops[2]["params"]["orientation"] = "sideways"
    proj = json.load(open(V33_RECIPE))
    proj["ops"] += ops
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(proj)),
                                 open(STOCK, "rb").read())
    assert not res.ok and any("BAD_PARAMS" in str(e) for e in res.errors), res.errors


def test_rear_tile_map_is_the_owner_mapping():
    assert levelpage.ORIENTATIONS == ("front", "rear", "rear-low")
    for o in ("rear", "rear-low"):
        assert {k: levelpage.tile_slot(k, o) for k in ("fl", "fr", "rl", "rr")} == \
            {"fl": "rr", "fr": "rl", "rl": "fr", "rr": "fl"}
    assert {k: levelpage.tile_slot(k, "front") for k in ("fl", "fr", "rl", "rr")} == \
        {"fl": "fl", "fr": "fr", "rl": "rl", "rr": "rr"}


def test_v34b_still_rebuilds_its_committed_image():
    proj = recipe.Project.from_json(open("builds/mcf-13.6-level.ffproj.json").read())
    res = recipe.compile_project(proj, open(STOCK, "rb").read())
    assert res.ok, res.errors
    assert hashlib.sha256(res.data).hexdigest() == \
        "9fa42e8ca19f3764b2e272644a0b7c2b4484bac720f6726927c8631bc3f0d5b6"


# ---- controls: the Blocks-carried stepper and Zero Level (spec 2026-09-26 section 2) -----------

# Example touch ids for build_body's standalone controls= param (not group indices; these are
# distinct from add_level_page's real claimed groups 61/70/76). 68 and 69 are deliberately avoided
# here since those are levelcontrols.RESERVED_GROUPS, the reserved inverter/charger templates.
CONTROL_IDS = {"minus": 61, "plus": 70, "zero": 76}


@pytest.fixture(scope="module")
def controlled(v33):
    cells = {name: 700 + i for i, (name, _w) in enumerate(levelfeed.CELLS)}
    out, info = levelpage.build_body(v33, cells=cells, arrow=ARROW, controls=CONTROL_IDS)
    return cells, out, info


def test_touch_regions_are_static_and_disjoint(controlled):
    _cells, out, info = controlled
    recs = records.object_records(info["body"], 0)
    touches = [r for r in recs if r.wtype == 12]
    rects = levelpage.controls_rects("rear")
    assert sorted(r.geom[:4] for r in touches) == sorted(list(v) for v in rects.values())
    boxes = [tuple(r.geom[:4]) for r in touches]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            gap_x = max(b[0] - (a[0] + a[2]), a[0] - (b[0] + b[2]))
            gap_y = max(b[1] - (a[1] + a[3]), a[1] - (b[1] + b[3]))
            assert max(gap_x, gap_y) >= 4, (a, b)
    # appended last, in the order minus, plus, zero, each carrying its id (the R50 tail)
    assert [r.wtype for r in recs[-3:]] == [12, 12, 12] and len(touches) == 3
    body = info["body"]
    for r, name in zip(recs[-3:], ("minus", "plus", "zero")):
        assert r.geom[:4] == list(rects[name])
        assert int.from_bytes(body[r.offset + r.size - 2:r.offset + r.size], "little") \
            == CONTROL_IDS[name]
    # no touch inside any switch arm (arms are appended objects, not body records)
    for name, s in info["switches"].items():
        for k in range(s["count"]):
            assert all(r.wtype != 12 for r in _arm_records(out, s["arms_base"] + k)), (name, k)


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


def test_controls_body_draws_the_stepper_and_zero_level(controlled):
    cells, _out, info = controlled
    recs = records.object_records(info["body"], 0)
    for r in recs:
        _check_record(r)
    texts = [r.label for r in recs if r.wtype == 11]
    assert texts.count("Carried") == 1 and "Zero Level" in texts and "hold 3 s" in texts
    # the carried readout lives in the stepper's centre panel, centred, on the centre colour
    ro = {r.value_slot: r for r in recs if r.wtype == 8}
    assert set(ro) == {cells[n] for n in ("pitch", "roll", "total", "carried")}
    c = ro[cells["carried"]]
    assert c.align == 2 and c.font == levelpage.F_VALUE
    assert ("fillB", 3, 0x272862) in c.colors
    mx, _my, mw, _mh = levelpage.CONTROLS["minus"]
    px, py, _pw, ph = levelpage.CONTROLS["plus"]
    assert mx + mw < c.geom[0] < px and py < c.geom[1] <= py + ph
    # the readout's opaque cell (top = pen_y - font[0x0b]) starts below the caption's ink, whose
    # lowest row is its baseline (label top + font[0x0b]), and ends inside the panel
    cap = [r for r in recs if r.wtype == 11 and r.label == "Carried"][0]
    f13 = metrics.font_metrics(_v33_bytes(), levelpage.F_STEPPER_CAPTION)
    f17 = metrics.font_metrics(_v33_bytes(), levelpage.F_VALUE)
    cell_top = c.geom[1] - f17.top
    assert cap.geom[1] + f13.top < cell_top
    assert cell_top + f17.height - 1 <= py + ph
    # every Blocks-box record sits inside the box (the box's own outline, notch and title aside)
    bx, by, bw, bh, _r = levelpage.LAYOUT["blocks"]["box"]
    inside = [r for r in recs if r.wtype != 9 and bx < r.geom[0] < bx + bw
              and by + 12 < r.geom[1] < by + bh]
    assert len(inside) >= 12
    for r in inside:
        if r.wtype in (4, 11, 12, 0x21):
            assert r.geom[0] + r.geom[2] < bx + bw and r.geom[1] + r.geom[3] < by + bh, r.geom
    # no Carried caption left on the old right-aligned row
    assert not any(r.wtype == 11 and r.label == "Carried"
                   and r.geom[:2] == list(levelpage.LAYOUT["captions"]["Carried"]) for r in recs)


def _directives(body):
    """{readout value_slot: the 4 directive bytes walked immediately before it}."""
    recs = records.object_records(body, 0)
    return {r.value_slot: bytes(recs[i - 1].fmt) for i, r in enumerate(recs)
            if r.wtype == 8 and recs[i - 1].wtype == 9}


def test_stepper_readout_is_centre_justified_and_the_rest_are_not(live, controlled):
    """The stepper's carried value sits on a centred pen, so its width-3 field must centre too
    (flags 0x14, as stock steppers 1131/1132; Finding 65 `0x002529DC`): a right-justified
    field pads with leading spaces on the panel and pushes the digits right of centre."""
    cells, _o, info = controlled
    d = _directives(info["body"])
    assert d[cells["carried"]] == bytes.fromhex("00140300")
    assert d[cells["pitch"]] == d[cells["roll"]] == bytes.fromhex("00180501")
    assert d[cells["total"]] == bytes.fromhex("00180300")
    lcells, _lo, linfo = live           # controls off: every readout keeps right-justify
    assert set(_directives(linfo["body"]).values()) <= {bytes.fromhex("00180501"),
                                                        bytes.fromhex("00180300")}
    assert _directives(linfo["body"])[lcells["carried"]] == bytes.fromhex("00180300")


def _v33_bytes():
    return open(V33, "rb").read()


def test_controls_none_leaves_the_body_as_it_was(v33, live):
    cells, _out, info = live
    _o, again = levelpage.build_body(v33, cells=cells, arrow=ARROW, controls=None)
    assert again["body"] == info["body"]
    p0 = levelpage.build_body(v33, cells=None, arrow=ARROW)[1]["body"]
    p1 = levelpage.build_body(v33, cells=None, arrow=ARROW, controls={"minus": 1, "plus": 2,
                                                                      "zero": 3})[1]["body"]
    assert p0 != p1 and all(r.wtype != 12 for r in records.object_records(p0, 0))


def test_controls_ids_are_checked(v33):
    with pytest.raises(levelpage.LevelPageError):
        levelpage.build_body(v33, cells=None, arrow=ARROW, controls={"minus": 1, "plus": 2})


# ---- controls wired into add_level_page (spec 2026-09-26 sections 3-4) -------------------------

def _controls_project(preview: bool = False):
    p = json.load(open("builds/mcf-13.6-level.ffproj.json"))
    p["build_name"] = "level-controls-test"
    op = [o for o in p["ops"] if o["id"] == "level"][0]
    op["params"].update(orientation="rear", controls=True, preview=preview)
    return recipe.Project.from_json(json.dumps(p))


def _compile_controls(compile_with_handles, preview: bool):
    res, handles = compile_with_handles(_controls_project(preview), open(STOCK, "rb").read())
    assert res.ok, res.errors
    return res, handles["level"]


@pytest.fixture(scope="module", params=[False, True], ids=["live", "preview"])
def controls_build(request, compile_with_handles):
    return (request.param,) + _compile_controls(compile_with_handles, request.param)


def _body_touches(data):
    gs = guidata.guidata_extent(data)[0]
    recs = records.object_records(data, gs + guidata.object_tables(data)[1][367])
    return recs, {tuple(r.geom[:4]): (r, data[r.end - 2] | (data[r.end - 1] << 8))
                  for r in recs if r.wtype == 12}


def test_controls_build_sends_the_spec_frames(controls_build):
    from fireflymcf import levelcontrols, newbutton
    _preview, res, h = controls_build
    d = res.data
    gt, bt = guidata.group_table(d), guidata.binding_table(d)
    _recs, touches = _body_touches(d)
    assert len(touches) == 3
    for name, rect in levelpage.controls_rects("rear").items():
        _r, tid = touches[tuple(rect)]
        assert tid == h["groups"][name]                 # the touch id IS the group index
        g = gt[tid]
        frames = [newbutton.binding_wire_frame(bt[i].raw) for i in range(g.start, g.start + g.count)]
        assert frames == levelcontrols.FRAMES[name], name


def test_controls_groups_are_fresh_and_contiguous(controls_build):
    from fireflymcf import levelcontrols, newbutton
    _preview, res, h = controls_build
    d = res.data
    gt, bt = guidata.group_table(d), guidata.binding_table(d)
    start = h["binding_start"]
    assert len(bt) == start + 4
    assert [gt[h["groups"][n]].start for n in ("minus", "plus", "zero")] == \
        [start, start + 1, start + 2]
    assert [gt[h["groups"][n]].count for n in ("minus", "plus", "zero")] == [1, 1, 2]
    assert gt[h["groups"]["minus"]].raw[:4] == bytes.fromhex("10000000")
    assert gt[h["groups"]["plus"]].raw[:4] == bytes.fromhex("10000000")
    assert gt[h["groups"]["zero"]].raw[:4] == bytes.fromhex("30000000")
    # the whole-press model: one frame per - / + press of any length; Zero only on a 3-s hold
    for name in ("minus", "plus", "zero"):
        g = gt[h["groups"][name]]
        got = levelcontrols.presses(g.raw, [bt[i].raw for i in range(g.start, g.start + g.count)])
        if name == "zero":
            assert got == {"tap": [], "held_2s": [], "held_3s": levelcontrols.FRAMES["zero"],
                           "held_3_5s": levelcontrols.FRAMES["zero"]}
        else:
            assert all(v == levelcontrols.FRAMES[name] for v in got.values()), (name, got)
    # the three groups were free on the image the op ran on (not referenced by anything else)
    stock_free = newbutton.free_group_slots(open(STOCK, "rb").read())
    assert set(h["groups"].values()) <= set(stock_free)


def test_preview_with_controls_draws_the_static_stepper(controls_build):
    preview, res, h = controls_build
    recs, _touches = _body_touches(res.data)
    texts = [r.label for r in recs if r.wtype == 11]
    if preview:
        assert levelpage.EXAMPLE["carried"] in texts and not h["vars"]
        assert not any(r.wtype == 8 for r in recs)
    else:
        assert any(r.wtype == 8 and r.value_slot == h["cells"]["carried"] for r in recs)


def test_controls_param_is_checked():
    p = json.load(open("builds/mcf-13.6-level.ffproj.json"))
    [o for o in p["ops"] if o["id"] == "level"][0]["params"]["controls"] = "yes"
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(p)),
                                 open(STOCK, "rb").read())
    assert not res.ok and any("BAD_PARAMS" in str(e) for e in res.errors), res.errors


def test_recipe_ops_carries_controls():
    ops = levelpage.recipe_ops(preview=False, controls=True)
    assert ops[2]["params"]["controls"] is True
    assert "controls" not in levelpage.recipe_ops(preview=False)[2]["params"]


def test_controls_never_claim_the_reserved_inverter_groups(controls_build):
    """Groups 68/69 (1FFD3/1FFC5 inverter/charger templates, the PD-inverter bridge's route,
    HARDWARE-VERIFIED.md) are never claimed and stay byte-identical to v34b's."""
    from fireflymcf import levelcontrols
    _preview, res, h = controls_build
    assert levelcontrols.RESERVED_GROUPS == {68, 69}
    assert not set(h["groups"].values()) & levelcontrols.RESERVED_GROUPS, h["groups"]
    v34b = open("builds/mcf-13.6-Level-v34b.bin", "rb").read()
    gt0, bt0 = guidata.group_table(v34b), guidata.binding_table(v34b)
    gt1, bt1 = guidata.group_table(res.data), guidata.binding_table(res.data)
    for g in (68, 69):
        assert gt1[g].raw == gt0[g].raw, g
        assert [bt1[i].raw for i in range(gt1[g].start, gt1[g].start + gt1[g].count)] == \
            [bt0[i].raw for i in range(gt0[g].start, gt0[g].start + gt0[g].count)], g


def test_new_touch_regions_do_not_overlap_anything_touchable_on_screen_29():
    """Spec section 5: a new touch rectangle must not overlap any OTHER touchable region reachable
    on screen 29, not just the three new ones against each other (`test_touch_regions_are_static_
    and_disjoint` already covers that, plus a 4 px gap, on the standalone body). Walk the real page
    root (`recipe_ops.AddLevelPage.ROOT`), which recurses through every container -- the header
    widgets (object 892) and the nav rail (object 974's switch, rendered at its base arm) included
    -- so this sees the whole screen's touch surface, not only object 367's own records."""
    from fireflymcf import layout, recipe_ops
    data = open("builds/mcf-13.6-LevelControls-v35.bin", "rb").read()
    placed = layout.Screen(data).walk(recipe_ops.AddLevelPage.ROOT)
    touches = [p for p in placed if p.wtype == 12]
    new = [(p.x0, p.y0, p.x1, p.y1) for p in touches if p.obj_index == recipe_ops.AddLevelPage.BODY
           and (p.x0, p.y0, p.x1, p.y1) in
           [(x, y, x + w, y + h) for x, y, w, h in levelpage.controls_rects("rear-low").values()]]
    assert len(new) == 3, touches
    others = [(p.x0, p.y0, p.x1, p.y1) for p in touches if (p.x0, p.y0, p.x1, p.y1) not in new]
    assert others, "no other touchable region reached from root %d -- the walk found nothing " \
        "to check against" % recipe_ops.AddLevelPage.ROOT

    def overlaps(a, b):
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1

    for n in new:
        for o in others:
            assert not overlaps(n, o), (n, o)


# ---- the raised rear layout (owner 2026-09-27) ---------------------------------------------------

def test_v35_still_rebuilds_its_committed_image():
    """v35 is the running image; its project pins orientation "rear-low" (the pre-2026-09-27 rear
    layout) and must keep rebuilding byte-identically."""
    proj = recipe.Project.from_json(open("builds/mcf-13.6-level-controls.ffproj.json").read())
    res = recipe.compile_project(proj, open(STOCK, "rb").read())
    assert res.ok, res.errors
    assert hashlib.sha256(res.data).hexdigest() == \
        "230773bf259d91027868d2983c5a16e9ff22b9dd61bac2d52dab74f9dae9823e"


def test_rear_group_sits_evenly_between_header_and_page_bottom(v33):
    """Owner 2026-09-27: the space above the van and below FRONT's lettering are even (within
    2 px). Measured on the drawing: the van body's top, and FRONT's lowest inked row (the panel's
    own glyphs for font F_CAPTION), against the page area."""
    import numpy as np
    from fireflymcf import glyphs, render
    parts = render.panel_text_images(v33, levelpage.F_CAPTION, "FRONT", (255, 255, 255),
                                     table=glyphs.resolve_glyph_table(v33))
    ink = max(dy + i for _dx, dy, img in parts
              for i, r in enumerate(np.array(img)[:, :, 3]) if r.any())
    px, py, pw, ph = levelpage.LAYOUT["page"]

    def gaps(o):
        v = levelpage.van(o)
        return v["body"][1] - py, (py + ph - 1) - (v["front"][1] + ink)

    top, bottom = gaps("rear")
    assert abs(top - bottom) <= 2, (top, bottom)
    low_top, low_bottom = gaps("rear-low")
    assert low_top - low_bottom > 30, (low_top, low_bottom)       # v35 sat low


# ---- RV-C custom DGNs (owner 2026-09-28) -------------------------------------------------------

def test_v37_rebuilds_on_the_rvc_custom_dgns():
    """v37 is v36 on RV-C's custom DGN 1EF00h: the sensor's records on 0x1EFBF (DGN-low pinned to
    BFh, the sensor's default address), and the - / + addressed to it on 0x1EFBF too. The
    registry count is unchanged (0x1EFBF replaces 0x0FFB0), and the image rebuilds
    byte-identically."""
    proj = recipe.Project.from_json(open("builds/mcf-13.6-level-rvc-dgn.ffproj.json").read())
    res = recipe.compile_project(proj, open(STOCK, "rb").read())
    assert res.ok, res.errors
    assert hashlib.sha256(res.data).hexdigest() == \
        "c42ad968559a5e325616943c0b7d9eccb2a0ab9056a81edfbdcb2d954f7761ad"
    reg = statusbind.registered_dgns(res.data)
    assert 0x1EFBF in reg and 0x0FFB0 not in reg and len(reg) == 68
    vt = guidata.variable_table(res.data)
    assert len(vt) == 314                                   # the 14 appended at 300-313
    assert [struct.unpack_from("<I", vt[v].raw, 0)[0] & statusbind.DGN_MASK
            for v in range(300, 314)] == [0x1EFBF] * 14
    bt = guidata.binding_table(res.data)                     # the controls' four, appended last
    assert [newbutton.binding_wire_frame(b.raw)[0] for b in bt[-4:]] == \
        [0x19EFBF9F, 0x19EFBF9F, 0x19FDE89F, 0x19FDEA9F]


def test_stepper_dgn_without_controls_is_refused():
    ops = levelpage.recipe_ops(preview=False, stepper_dgn=0x1EFBF)
    p = json.loads(open("builds/mcf-13.6-level-centred.ffproj.json").read())
    p["ops"] = [o for o in p["ops"] if o["id"] not in {x["id"] for x in ops}] + ops
    res = recipe.compile_project(recipe.Project.from_json(json.dumps(p)),
                                 open(STOCK, "rb").read())
    assert not res.ok and any("stepper_dgn needs controls" in str(e) for e in res.errors)


# ---- readout fills and the nav icon (v38, owner 2026-09-28) ------------------------------------

def _fill_conflicts(path):
    from fireflymcf import readoutfill
    from fireflymcf.recipe_ops import _gs_tab
    data = open(path, "rb").read()
    gs, tab = _gs_tab(data)
    return readoutfill.conflicts(data, gs, tab, readoutfill.body_and_arms(data, gs, tab, 367))


def _flagged_tiles(path):
    return sorted({c.obj for c in _fill_conflicts(path)
                   if c.kind == "overlap" and "\\x8f" in c.detail})


def test_readout_fill_guard_matches_the_coach():
    """The guard against what the coach showed. v37 and v38: every amber tile's arrow wiped (all
    four amber arms flagged). v40: only the tile that drew its arrow last (key rl, arm 1453) came
    out whole, so every arm but that one is flagged. v41 draws every arrow last: nothing."""
    amber = [1447, 1450, 1453, 1456]
    assert _flagged_tiles("builds/mcf-13.6-LevelRvcDgn-v37.bin") == amber
    assert _flagged_tiles("builds/mcf-13.6-LevelArrowIcon-v38.bin") == amber
    assert _flagged_tiles("builds/mcf-13.6-ArrowProbe2-v40.bin") == [1447, 1450, 1456]
    assert _fill_conflicts("builds/mcf-13.6-LevelArrowLast-v41.bin") == []


def test_v41_rebuilds_with_every_arrow_last():
    """v41 = v38 without fill_safe (its width model was disproven by v40) and with arrow_last: in
    each amber arm the arrow is the last record, after both readouts. Byte-identical rebuild."""
    proj = recipe.Project.from_json(open("builds/mcf-13.6-level-arrow-last.ffproj.json").read())
    res = recipe.compile_project(proj, open(STOCK, "rb").read())
    assert res.ok, res.errors
    assert hashlib.sha256(res.data).hexdigest() == "456fb564763df97674ee266c32717f63d4c24e4f63942971bf6a2b31d568641f"
    from fireflymcf.recipe_ops import _gs_tab
    gs, tab = _gs_tab(res.data)
    for obj in (1447, 1450, 1453, 1456):
        recs = records.object_records(res.data, gs + tab[obj])
        assert recs[-1].wtype == 11 and recs[-1].label == levelpage.ARROW[1], obj
        assert [r.fmt[2] for r in recs if r.wtype == 9] == [2, 4], obj


def test_v38_rebuilds_with_the_level_icon_on_the_rail():
    """v38 = v37 + fill_safe + the spirit-level icon at font 24 'Y' on the rail's two six-icon
    arms. It rebuilds byte-identically, and the rail draws 'Y' where it drew 'S'."""
    proj = recipe.Project.from_json(open("builds/mcf-13.6-level-arrow-icon.ffproj.json").read())
    res = recipe.compile_project(proj, open(STOCK, "rb").read())
    assert res.ok, res.errors
    assert hashlib.sha256(res.data).hexdigest() == "5fde543d6c5703c38eb0eca1584ec970bbb3268c3c70e64590ed763440f2366f"
    from fireflymcf.recipe_ops import _gs_tab
    gs, tab = _gs_tab(res.data)
    for obj in levelpage.NAV_ICON_OBJECTS:
        icons = [r.label for r in records.object_records(res.data, gs + tab[obj])
                 if r.wtype == 11 and r.font == levelpage.NAV_ICON[0]]
        assert "Y" in icons and "S" not in icons, (obj, icons)
    font = glyphs.font_for_slot(res.data, 24)
    g = glyphs.decode_glyph(res.data, font.glyph_index(ord("Y")))
    assert g.rows == tuple(tuple(int(c, 16) for c in row) for row in levelpage.NAV_ICON_ROWS)


# ---- the LEVEL SETUP popup (v42, owner 2026-09-29) -----------------------------------------------

def test_v42_rebuilds_with_the_setup_popup():
    """v42 = v41 + setup: the LEVEL page loses the stepper and Zero Level for a Setup button
    (touch 0x7ED, retargeted to screen 12) and shows Carried read-only; screen 12's root (object
    15) is the popup, with no background of its own, the header/rail references first, the nine
    touches last. Byte-identical rebuild; the fill guard is clean on both pages."""
    from fireflymcf import levelsetup, navmap, readoutfill
    from fireflymcf.recipe_ops import _gs_tab
    proj = recipe.Project.from_json(open("builds/mcf-13.6-level-setup.ffproj.json").read())
    res = recipe.compile_project(proj, open(STOCK, "rb").read())
    assert res.ok, res.errors
    data = res.data
    assert hashlib.sha256(data).hexdigest() == "4eb03d9790d83ad9e63c596ac4f59b195039e2b54892914df5930b4d3f27be5f"
    gs, tab = _gs_tab(data)
    body = records.object_records(data, gs + tab[367])
    touches = [r for r in body if r.wtype == 12]
    assert [(tuple(r.geom[:4]), data[r.end - 2] | data[r.end - 1] << 8) for r in touches] == \
        [(tuple(levelpage.controls_rects("rear")["zero"]), levelsetup.SETUP_ACTION)]
    assert any(r.wtype == 11 and r.label == "Setup" for r in body)
    assert not any(r.wtype == 11 and r.label == "Zero Level" for r in body)
    pop = records.object_records(data, gs + tab[levelsetup.POPUP_ROOT])
    assert [r.child_index for r in pop[:2]] == list(levelsetup.ROOT_REFS)
    assert not any(r.wtype == 4 and r.geom[2] == 799 for r in pop), "the popup paints a background"
    ptouch = [r for r in pop if r.wtype == 12]
    assert pop[-len(ptouch):] == ptouch and len(ptouch) == len(levelsetup.TOUCH_NAMES)
    assert data[ptouch[-1].end - 2] | data[ptouch[-1].end - 1] << 8 == levelsetup.CLOSE_ACTION
    leaves = {r.touch_id: r.screen for r in navmap.routes(data)}
    assert (leaves[levelsetup.SETUP_ACTION], leaves[levelsetup.CLOSE_ACTION]) == (12, 29)
    for root in (367, levelsetup.POPUP_ROOT):
        assert readoutfill.conflicts(data, gs, tab,
                                     readoutfill.body_and_arms(data, gs, tab, root)) == []


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

def test_v43_repaints_values_and_the_popup_has_its_own_background():
    """v43 = v42 + repaint + popup_background. Each wrapped value (pitch, roll, needed, carried on
    LEVEL; height, tolerance, carried in the popup) is a two-arm switch on its toggle cell whose
    arms both clear the value's box before the readout; no value readout is drawn outside one. The
    popup root starts with its full-screen background and carries the LEVEL SETUP title."""
    from fireflymcf import levelsetup
    from fireflymcf.recipe_ops import _gs_tab
    proj = recipe.Project.from_json(open("builds/mcf-13.6-level-refresh.ffproj.json").read())
    res = recipe.compile_project(proj, open(STOCK, "rb").read())
    assert res.ok, res.errors
    data = res.data
    assert hashlib.sha256(data).hexdigest() == "cf438c566587d225ac6bde99303935311cc7c53834d972d6ee5f453eab1b6c26"
    gs, tab = _gs_tab(data)

    def wrapped(root):
        recs = records.object_records(data, gs + tab[root])
        assert not [r for r in recs if r.wtype == 8], "a value is drawn outside a repaint switch"
        out = []
        for sw in (r for r in recs if r.wtype == 6):
            arms = [records.object_records(data, gs + tab[a])
                    for a in range(sw.child_index, sw.child_index + sw.count)]
            if sw.count != 2 or not all(any(x.wtype == 8 for x in a) for a in arms):
                continue                        # a state switch (tile, banner, AUTO)
            assert all(a[0].wtype == 4 and [x.wtype for x in a[1:]] == [9, 8] for a in arms)
            out.append(arms[0][-1].value_slot)
        return recs, out

    body, level_vals = wrapped(367)
    pop, popup_vals = wrapped(levelsetup.POPUP_ROOT)
    assert len(level_vals) == 4 and len(popup_vals) == 3
    assert pop[0].wtype == 4 and pop[0].geom[2:4] == [799, 479]
    assert any(r.wtype == 11 and r.label == levelsetup.TITLE for r in pop)
