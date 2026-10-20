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
from pathlib import Path

from fireflymcf import layout

REPO = Path(__file__).resolve().parent.parent
STOCK = REPO / "firefly-13.6" / "mcf.bin"
AGS_BODY = 830            # the AGS compact frame, for reference

AGS_SLOTS = {5: 8, 112: 1, 113: 1, 37: 1}


def _stock():
    return STOCK.read_bytes()


def _walk(data, root, slots):
    return layout.Screen(data).walk(root, slot_values=slots)


# --- 1. the pill's alignment -------------------------------------------------------------

def test_stock_swap_leaves_the_pill_misaligned():
    """The characterization this change corrects: before it, the pill is 8 px right of the engine
    control and 7 px below the inverter capsule."""
    placed = _walk(_stock(), AGS_BODY, AGS_SLOTS)
    ags_pill = next(p for p in placed if p.obj_index == 315 and p.wtype == 33)
    assert ags_pill.x0 == 208 and ags_pill.y0 == 228, (ags_pill.x0, ags_pill.y0)


def test_the_stock_ags_page_has_the_one_pixel_offset():
    """Characterization: the offset being corrected is stock, not something the swap introduced."""
    placed = _walk(_stock(), AGS_BODY, AGS_SLOTS)
    gen = next(p for p in placed if p.wtype == 11 and (p.label or "").strip() == "GEN")
    status = next(p for p in placed if p.obj_index == 1409 and p.wtype == 33)
    assert status.y0 == gen.y0 - 1
