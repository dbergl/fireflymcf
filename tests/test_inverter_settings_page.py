"""Screen 14's `INVERTER / CHARGER SETTINGS` display switches (GUICODE Findings 49/51).

Three type-6 switches on that page enumerate, in the file, every string the panel can draw for
`Faults:`, `I/C Model` and `Battery Type`. Two of them have no writer (Finding 49) and the third
reads a DGN nothing on this coach sends, so nothing on the bus can pin them -- but the *arms* are
static data, and the doc quotes them, so they are pinned here.

The two assertions that carry weight beyond bookkeeping:

* the `Faults:` switch starts at match value **1** and carries no `0xffff` default candidate, so
  an unwritten cell (0) selects NOTHING -- which is the blank the coach photographed (PC.29), not
  a missing string. `I/C Model` is the contrast: it has a 0 arm, so it draws `UNKNOWN`;
* `Battery Type`'s five arms are `{0, 1, 2, 3, 13}`, byte-for-byte the enumerated code set the G12
  was watched cycling through on the bus (PC.31) -- which is what names those codes
  `Flooded`/`Gel`/`AGM`/`Lithium`/`Custom` (GUICODE Finding 51, corrected 2026-09-02).
"""
from __future__ import annotations

import struct

import pytest

from fireflymcf import layout, records


BUILD = "firefly-13.6/mcf.bin"
STATUS_BLOCK = 922          # the `Inverter/Charger Status` body of object 17
BATTERY_TYPE_LIST = 465     # object 926's `Battery Type` readout container


@pytest.fixture(scope="module")
def data() -> bytes:
    with open(BUILD, "rb") as handle:
        return handle.read()


def _switch(scr: "layout.Screen", obj: int, slot: int) -> "records.WRecord":
    """The one type-6 record of `obj` reading `slot`."""
    recs = records.object_records(scr.data, scr.gs + scr._obj_offset(obj))
    hits = [r for r in recs if r.wtype == 6 and r.value_slot == slot]
    assert len(hits) == 1, "expected exactly one slot-%d switch on object %d" % (slot, obj)
    return hits[0]


def _arms(scr: "layout.Screen", rec: "records.WRecord") -> dict[int, str | None]:
    """`{match value: first label}` over the switch's own `[child, child+count)` band."""
    out: dict[int, str | None] = {}
    for k in range(rec.count or 0):
        idx = rec.child_index + k
        label = None
        for r in records.object_records(scr.data, scr.gs + scr._obj_offset(idx)):
            if r.label is not None:
                label = r.label
                break
        out[scr._match_value(idx)] = label
    return out


def test_faults_enumerates_23_strings_starting_at_one(data: bytes) -> None:
    scr = layout.Screen(data)
    rec = _switch(scr, STATUS_BLOCK, 412)
    arms = _arms(scr, rec)
    assert len(arms) == 23
    assert min(arms) == 1 and max(arms) == 23
    assert sorted(arms) == list(range(1, 24))            # dense, no gaps
    assert arms[1] == "NO FAULT"
    assert arms[13] == "FET OVERLOAD"
    assert arms[23] == "CB3 OPEN"


def test_an_unwritten_faults_cell_selects_nothing(data: bytes) -> None:
    """The blank on the coach is the no-match path, not an absent string."""
    scr = layout.Screen(data)
    rec = _switch(scr, STATUS_BLOCK, 412)
    assert scr._match_value(rec.child_index) != 0xFFFF   # no default candidate
    assert scr._switch_child(rec.child_index, 0, rec.count) is None
    # ... and the same call is not vacuously None: a real code does select an arm.
    assert scr._switch_child(rec.child_index, 1, rec.count) == rec.child_index


def test_ic_model_has_a_zero_arm_so_it_draws_unknown(data: bytes) -> None:
    """The contrast that makes the mechanism legible: same switch type, a 0 arm, so 0 draws."""
    scr = layout.Screen(data)
    rec = _switch(scr, STATUS_BLOCK, 414)
    arms = _arms(scr, rec)
    assert len(arms) == 40
    assert arms[0] == "UNKNOWN"
    assert scr._switch_child(rec.child_index, 0, rec.count) == rec.child_index
    assert arms[117] == "MS4348PE"                       # the top code
    assert arms[13] == "MMS912E" == arms[12]             # the one repeated string, as shipped


def test_battery_type_arms_are_the_codes_the_bus_cycled(data: bytes) -> None:
    """PC.31 watched the G12 walk `{0,1,2,3,13}`; the panel names exactly those five."""
    scr = layout.Screen(data)
    rec = _switch(scr, BATTERY_TYPE_LIST, 225)
    assert _arms(scr, rec) == {
        0: "Flooded", 1: "Gel", 2: "AGM", 3: "Lithium", 13: "Custom",
    }


def test_an_unset_battery_type_selects_nothing(data: bytes) -> None:
    """Var 125 unset is `0xff`; the cell transform is `>>4`, so the switch sees 15 -- no arm."""
    scr = layout.Screen(data)
    rec = _switch(scr, BATTERY_TYPE_LIST, 225)
    assert scr._switch_child(rec.child_index, 0xFF >> 4, rec.count) is None
    assert scr._switch_child(rec.child_index, 13, rec.count) == rec.child_index + 4   # Custom


# --- the `1FFC6` filter pin (BUS-BASELINE PC.46) -------------------------------------------

BATTERY_TYPE_VAR = 125          # the receive-filter entry `Battery Type` reads
CHARGER_CONFIG_DGN = 0x1FFC6    # CHARGER_CONFIGURATION_STATUS
INVERTER_INSTANCE = 1           # the coach's inverter/charger; every command DGN addresses it
ALTERNATOR_INSTANCE = 0x31      # the Wakespeed WS500's charger instance


def test_stock_leaves_the_battery_type_filter_open(data: bytes) -> None:
    """The defect PC.46 observed: any device's `1FFC6` drives this readout."""
    from fireflymcf import guidata, statusbind
    entry = guidata.variable_table(data)[BATTERY_TYPE_VAR]
    assert struct.unpack_from("<I", entry.raw, 0)[0] & statusbind.DGN_MASK == CHARGER_CONFIG_DGN
    assert entry.raw[statusbind.FILTER_INSTANCE_FIELD] == statusbind.FILTER_DISABLED
    assert entry.raw[statusbind.FILTER_INSTANCE_FIELD + 1] == 0x00   # compare payload[0], mask 0xff


def test_pinning_the_filter_admits_the_inverter_and_rejects_the_alternator(data: bytes) -> None:
    from fireflymcf import guidata, statusbind
    out, edits = statusbind.pin_filter_instance(
        data, BATTERY_TYPE_VAR, INVERTER_INSTANCE, expect_dgn=CHARGER_CONFIG_DGN)
    entry = guidata.variable_table(out)[BATTERY_TYPE_VAR]
    assert entry.raw[statusbind.FILTER_INSTANCE_FIELD] == INVERTER_INSTANCE
    assert entry.raw[statusbind.FILTER_INSTANCE_FIELD] != ALTERNATOR_INSTANCE
    assert len(out) == len(data)                       # same-size, as every filter edit is
    assert sum(1 for e in edits if e.structure != "checksum") == 1   # ... and exactly one byte


def test_the_experiment_build_ships_the_pin() -> None:
    """The shipped recipe must carry it, not just the primitive."""
    import json
    from pathlib import Path
    from fireflymcf import guidata, recipe, statusbind

    repo = Path(__file__).resolve().parent.parent
    stock = (repo / "firefly-13.6" / "mcf.bin").read_bytes()
    project = recipe.Project.from_json((repo / "builds" / "golden-v22-anchor.ffproj.json").read_text())
    result = recipe.compile_project(project, stock)
    assert result.ok, result.errors
    entry = guidata.variable_table(result.data)[BATTERY_TYPE_VAR]
    assert entry.raw[statusbind.FILTER_INSTANCE_FIELD] == INVERTER_INSTANCE, \
        "the experiment build no longer pins `Battery Type` to the inverter's instance"
