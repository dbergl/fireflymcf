"""Two owner-requested adjustments to the compact AES Electrical page (2026-09-01).

1. The AES enable/disable pill sits 8 px right of and 7 px below where it should: its left edge
   must line up with the Engine start/stop control above it, and its top with the Inverter block's
   settings capsule beside it. The shift is two same-size u16 edits on the cloned cluster's pill
   box; every row below the pill follows it (flow layout), which lands the whole column on the
   AGS page's own rhythm rather than 7 px under it.

2. The Inverter settings capsule (object 1421, action id 0x7F7) navigates to screen 39, the
   READ-ONLY 'PD Inverter' readout. It should reach screen 14, 'INVERTER / CHARGER SETTINGS'
   (object 17) -- the page the HOME inverter's 3-second hold reaches, which carries five live
   controls. Object 1421 is shared with the AGS compact page, so this is deliberately global.
"""
import hashlib
import json
import struct
from pathlib import Path

from fireflymcf import aescompact, guidata, layout, records, theme, thumb

from tests.conftest import compiled_image

REPO = Path(__file__).resolve().parent.parent
STOCK = REPO / "firefly-13.6" / "mcf.bin"
PROJECT = REPO / "builds" / "golden-v22-anchor.ffproj.json"
AES_BODY = 829            # the AES-mode Electrical body (the compact frame)
AGS_BODY = 830            # the AGS compact frame, for reference
ENGINE_LEFT = 200         # the Engine start/stop control's left edge on the compact frame
INVERTER_BLOCK = 1421     # the shared Inverter settings capsule
INV_SETTINGS_SCREEN = 14  # object 17, 'INVERTER / CHARGER SETTINGS'
PD_INVERTER_SCREEN = 39   # object 38, the read-only 'PD Inverter' readout (the stock target)

AES_SLOTS = {5: 8, 112: 1, 113: 1, 37: 2}
AGS_SLOTS = {5: 8, 112: 1, 113: 1, 37: 1}


def _stock():
    return STOCK.read_bytes()


def _full_build():
    """The full `builds/golden-v22-anchor.ffproj.json` image, compiled from stock IN-TEST.

    The two Task-10 tests below used to read `build/mcf-pump-auto.bin`, with no existence
    guard and a cwd-relative path. That file is gitignored, untracked, produced by no script or
    recipe in this tree, and was not the shipped image (a one-off intermediate) -- so the suite
    was green only on the machine that happened to hold it, and the assertions ran against a
    build that is not what ships. Compiling here is the same discipline `tests/test_registers.py`
    uses, and it asserts against the actual v14 bytes.

    Cached by `conftest.compiled_image`: `compile_project` is pure and deterministic, so one
    compile serves both tests here, `test_registers.py`'s v14 pin, and every xdist worker.
    """
    return compiled_image(PROJECT.read_text(), _stock()).data


def _build():
    stock = _stock()
    return compiled_image(json.dumps({
        "schema": 1, "base_sha256": hashlib.sha256(stock).hexdigest(),
        "build_name": "aes layout test", "preview": {},
        "ops": [{"id": "aesswap", "type": "swap_aes_electrical", "params": {}},
                {"id": "invgear", "type": "retarget_inverter_gear", "params": {}}]}),
        stock).data


def _walk(data, root, slots):
    return layout.Screen(data).walk(root, slot_values=slots)


def _pill(placed):
    """The enable pill's box: the first box of the cloned CLUSTER (the object carrying the
    'Triggers:' caption) -- not the appended Engine clone, whose box is also new and also at
    x 200, which would make the left-alignment assertion pass for the wrong reason."""
    cluster = next(p.obj_index for p in placed
                   if (p.label or "").strip() == "Triggers:")
    boxes = [p for p in placed if p.wtype == 33 and p.obj_index == cluster]
    assert boxes, "no box on the cloned cluster %s" % cluster
    return boxes[0]


def _inverter_capsule(placed):
    return next(p for p in placed if p.obj_index == INVERTER_BLOCK and p.wtype == 33)


def _leaf_bytes(data, site):
    section = theme.guicode_section(data)
    off = section.offset + (site - theme.guicode_base(section))
    return bytes(data[off:off + 2])


# --- 1. the pill's alignment -------------------------------------------------------------

def test_stock_swap_leaves_the_pill_misaligned():
    """The characterization this change corrects: before it, the pill is 8 px right of the engine
    control and 7 px below the inverter capsule."""
    placed = _walk(_stock(), AGS_BODY, AGS_SLOTS)
    ags_pill = next(p for p in placed if p.obj_index == 315 and p.wtype == 33)
    assert ags_pill.x0 == 208 and ags_pill.y0 == 228, (ags_pill.x0, ags_pill.y0)


def test_enable_pill_left_aligns_with_the_engine_control():
    placed = _walk(_build(), AES_BODY, AES_SLOTS)
    assert _pill(placed).x0 == ENGINE_LEFT


def test_enable_pill_top_aligns_with_the_inverter_capsule():
    placed = _walk(_build(), AES_BODY, AES_SLOTS)
    assert _pill(placed).y0 == _inverter_capsule(placed).y0


def test_the_engine_control_above_the_pill_does_not_move():
    """The shift is inside the cluster, after its engine container, so the Engine control keeps
    its stock row (y 154, beside the Inverter status block at 153)."""
    placed = _walk(_build(), AES_BODY, AES_SLOTS)
    engine = next(p for p in placed if p.wtype == 11 and (p.label or "").strip() == "Engine")
    assert (engine.x0, engine.y0) == (ENGINE_LEFT, 154)


def test_the_column_below_the_pill_follows_it_onto_the_ags_rhythm():
    """Flow layout: the rows below the pill move with it. That is the intent -- it puts the
    column within a pixel of the AGS page's own, instead of 7 px lower."""
    aes = _walk(_build(), AES_BODY, AES_SLOTS)
    ags = _walk(_stock(), AGS_BODY, AGS_SLOTS)

    def row(placed, text):
        return next(p for p in placed if (p.label or "").strip().startswith(text))

    for text in ("Triggers:", "Low"):
        assert abs(row(aes, text).y0 - row(ags, text).y0) <= 1, text


def test_inverter_status_block_top_aligns_with_the_engine_control():
    """Owner, 2026-09-01: the Inverter enable/disable block sat one pixel above the Engine
    start/stop control. The offset is STOCK -- the AGS compact page has it too -- and it comes from
    the absolute y on the inverter switch record our own frame carries, so correcting it is a
    one-pixel edit confined to the AES frame."""
    placed = _walk(_build(), AES_BODY, AES_SLOTS)
    engine = next(p for p in placed if p.wtype == 11 and (p.label or "").strip() == "Engine")
    status = next(p for p in placed if p.obj_index == 1409 and p.wtype == 33)
    assert status.y0 == engine.y0


def test_the_stock_ags_page_has_the_one_pixel_offset():
    """Characterization: the offset being corrected is stock, not something the swap introduced."""
    placed = _walk(_stock(), AGS_BODY, AGS_SLOTS)
    gen = next(p for p in placed if p.wtype == 11 and (p.label or "").strip() == "GEN")
    status = next(p for p in placed if p.obj_index == 1409 and p.wtype == 33)
    assert status.y0 == gen.y0 - 1


def test_the_column_below_the_pill_matches_the_ags_page_exactly():
    """With the inverter column down one pixel and the pill following it, the AES column lands on
    the AGS page's rows exactly, not within a pixel of them."""
    aes = _walk(_build(), AES_BODY, AES_SLOTS)
    ags = _walk(_stock(), AGS_BODY, AGS_SLOTS)

    def row(placed, text):
        return next(p for p in placed if (p.label or "").strip().startswith(text))

    for text in ("Triggers:", "Low"):
        assert row(aes, text).y0 == row(ags, text).y0, text


# --- 2. the inverter gear's destination --------------------------------------------------

def test_inverter_gear_action_leaf_targets_the_settings_page():
    data = _build()
    assert _leaf_bytes(data, aescompact.INVERTER_GEAR_LEAF_SITE) == \
        thumb.movs_imm(3, INV_SETTINGS_SCREEN)
    assert _leaf_bytes(_stock(), aescompact.INVERTER_GEAR_LEAF_SITE) == \
        thumb.movs_imm(3, PD_INVERTER_SCREEN)


def test_the_targeted_screen_is_the_page_the_home_hold_reaches():
    """Screen 14 is object 17, 'INVERTER / CHARGER SETTINGS' -- the `split_hold_region` target."""
    data = _build()
    assert layout.screen_object_map(data)[INV_SETTINGS_SCREEN] == 17


def test_the_edit_is_deliberately_shared_with_the_ags_page():
    """Object 1421 carries the only 0x7F7 region and renders on BOTH compact pages, so the AGS
    inverter capsule reaches the same page. Asserted so the scope is a decision, not a surprise."""
    data = _build()
    gs, _ge = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    carriers = [i for i in range(len(tab))
                for r in records.object_records(data, gs + tab[i])
                if r.wtype == 12 and (data[r.end - 2] | (data[r.end - 1] << 8))
                == aescompact.INVERTER_GEAR_TOUCH_ID]
    assert carriers == [INVERTER_BLOCK]
    assert any(p.obj_index == INVERTER_BLOCK for p in _walk(data, AGS_BODY, AGS_SLOTS))
    assert any(p.obj_index == INVERTER_BLOCK for p in _walk(data, AES_BODY, AES_SLOTS))


def test_retarget_refuses_a_build_whose_leaf_is_not_stock():
    import pytest
    data = _build()          # already retargeted
    with pytest.raises(ValueError, match="0x7f7|leaf"):
        aescompact.retarget_inverter_gear(data, INV_SETTINGS_SCREEN)


# ======================================================================================
# Task 10: the Pump Link OPTIONS row is parked off-canvas, now that the AES Water Pump
# section carries the button (Task 11 wires that in). `remove_control` was tried first,
# per the brief -- it refuses NO_FIELD, because object 46's own wtype-5 attach for the
# row's object (1422) carries no override geometry at all (a plain flow attach, the same
# shape `test_remove_control_parks_off_canvas`'s comment documents for object 892): this
# row's absolute x/y lives on ITS OWN switch/label/touch records, not on the parent
# attach. `builds/golden-v22-anchor.ffproj.json` instead parks those three records directly
# with three `move_element` edits ("pumprow-switch"/"pumprow-label"/"pumprow-touch"),
# the same primitive "l-geom"/"l-cap"/"l-touch" already use for the HOME left button.
# ======================================================================================

PUMP_LINK_ROW = 1422       # add_option_follower's appended switch/label/touch object


def test_pump_link_row_is_off_the_options_canvas():
    """The row moved to the AES page; its OPTIONS carrier (object 1422) is parked past the
    480 px canvas on all three of its own records, and group 164's bindings stay intact
    because the new AES button carries the same touch id."""
    from fireflymcf import guidata, records
    out = _full_build()
    gs, _ = guidata.guidata_extent(out)
    tab = guidata.object_tables(out)[1]

    # object 46 still attaches the row (bindings/touch id untouched) -- it is parked, not
    # deleted.
    attach = [r for r in records.object_records(out, gs + tab[46])
              if r.wtype == 5 and r.child_index == PUMP_LINK_ROW]
    assert len(attach) == 1, "Pump Link row's attach in object 46 is gone -- it must be " \
        "parked, not removed, so group 164's bindings stay reachable"

    # every one of the row's own records (switch/label/touch) is past the visible canvas.
    row_recs = records.object_records(out, gs + tab[PUMP_LINK_ROW])
    labels = [r.label for r in row_recs if r.label]
    assert labels == ["   Pump Link"] or not labels
    for r in row_recs:
        off = records.geom_offset(out, r, 1)          # field 1 == y
        assert off is not None, "record wtype %d lost its y field" % r.wtype
        y = struct.unpack_from("<H", out, off)[0]
        assert y >= 480, "Pump Link row record wtype %d is still on-canvas: y=%d" % (
            r.wtype, y)

    # the group survives -- it now fires from the AES page (Task 11).
    assert guidata.group_table(out)[164].count == 4


def test_pump_link_row_parking_does_not_disturb_the_other_options_rows():
    """The rows around the parked one (Heat Pump, Bunk Accent, Bath Fan, ...) keep their
    stock geometry -- parking one row must not reflow the page."""
    from fireflymcf import guidata, records
    out = _full_build()
    stock = _stock()
    gs_out, _ = guidata.guidata_extent(out)
    gs_stock, _ = guidata.guidata_extent(stock)
    tab_out = guidata.object_tables(out)[1]
    tab_stock = guidata.object_tables(stock)[1]

    def label_ys(data, gs, tab):
        recs = records.object_records(data, gs + tab[46])
        out_map = {}
        for r in recs:
            if r.label and r.label.strip() in (
                    "Heat Pump", "Bunk Accent", "Bath Fan", "Black Tank"):
                off = records.geom_offset(data, r, 1)
                out_map[r.label.strip()] = struct.unpack_from("<H", data, off)[0]
        return out_map

    assert label_ys(out, gs_out, tab_out) == label_ys(stock, gs_stock, tab_stock)
