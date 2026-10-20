"""Golden AGS-unchanged net for the compact AES ELECTRICAL page swap.

The `swap_aes_electrical` recipe op (Task 5) replaces the inline all-in-one AES body
(object 829) with a compact frame and wires a settings sub-page. Nothing on the AGS
render path (object 5's mode switch, 830, 315, 795, 45, 486-488) may change: this file
is the gate that proves it, byte-for-byte and pixel-for-pixel.
"""
import json
import struct

from fireflymcf import recipe, guidata, records, render, layout

AGS_OBJS = [5, 830, 315, 795, 45, 486, 487, 488]

# The two AES ops in isolation. Was `builds/aes-compact.ffproj.json`; inlined here (and in
# test_aes_trigger.py) when that fixture file was removed, so this golden net still compiles
# `swap_aes_electrical` + `add_aes_low_volts_trigger` as a standalone 2-op build against stock.
AES_COMPACT_RECIPE = json.dumps({
    "schema": 1,
    "base_sha256": "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382",
    "build_name": "aes-compact",
    "ops": [
        {"id": "aesswap", "type": "swap_aes_electrical", "params": {}, "enabled": True},
        {"id": "aeslowvolts", "type": "add_aes_low_volts_trigger", "params": {}, "enabled": True},
    ],
})


def _span(data, i):
    gs, _ = guidata.guidata_extent(data)
    start = gs + guidata.object_tables(data)[1][i]
    recs = records.object_records(data, start)
    return data[start:recs[-1].end]


def _build():
    stock = open("firefly-13.6/mcf.bin", "rb").read()
    proj = recipe.Project.from_json(AES_COMPACT_RECIPE)
    res = recipe.compile_project(proj, stock)
    assert res.ok, res.errors
    return stock, res


def test_ags_object_spans_byte_identical():
    stock, res = _build()
    for i in AGS_OBJS:
        assert _span(res.data, i) == _span(stock, i), f"AGS object {i} drifted"


def test_ags_electrical_renders_pixel_identical():
    stock, res = _build()
    a = render.render_screen_image(stock, 5, slot_values={7: 1, 37: 1})
    b = render.render_screen_image(res.data, 5, slot_values={7: 1, 37: 1})
    assert list(a.getdata()) == list(b.getdata())


def test_swap_is_flagged_hardware_verified():
    """`swap_aes_electrical` was flashed and confirmed on the coach 2026-09-01,
    so it must NOT be reported as unverified -- that flag is what the Studio UI surfaces."""
    _, res = _build()
    assert "aesswap" not in res.unverified


def test_compact_frame_actually_shrunk_and_settings_screen_wired():
    stock, res = _build()
    gs_stock, _ = guidata.guidata_extent(stock)
    stock_recs = records.object_records(stock, gs_stock + guidata.object_tables(stock)[1][829])

    gs_out, _ = guidata.guidata_extent(res.data)
    out_recs = records.object_records(res.data, gs_out + guidata.object_tables(res.data)[1][829])

    assert len(out_recs) < len(stock_recs), (
        f"built object 829 has {len(out_recs)} records, stock has {len(stock_recs)}; "
        "expected the compact swap to shrink it")

    # Screen 28 still maps to object 31 -- the rebuilt AES settings frame is in place,
    # not relocated to a new index.
    assert layout.screen_object_map(res.data)[28] == 31
