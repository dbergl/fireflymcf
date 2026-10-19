"""`append_filter`: subscribe to a DGN by APPENDING a variable-table entry (appendfilter.py).

Pins the four things the boot loader checks and the one thing it would silently drop: the entry
reads back at the table's end, the descriptor count/size and GuiCode header +0x20 agree, nothing
at or after the image container moves, and the registry cap is enforced.
"""
import json
import struct

import pytest

from fireflymcf import appendfilter, container, guidata, recipe, register, statusbind

STOCK = "firefly-13.6/mcf.bin"
PROBE = appendfilter.NewFilter(dgn=0x1FEA0, mode=1, payload_byte=0)


def _stock():
    return open(STOCK, "rb").read()


def _gc_count(data):
    gc = appendfilter._guicode_section(data)
    return struct.unpack_from("<H", data, gc.offset + appendfilter.GUICODE_VAR_COUNT_FIELD)[0]


def test_append_reads_back_and_counts_agree():
    stock = _stock()
    out, _edits, idx = appendfilter.append_filters(stock, [PROBE])
    vt = guidata.variable_table(out)
    assert idx == [298] and len(vt) == 299
    assert vt[298].raw == PROBE.raw()
    assert [v.raw for v in vt[:298]] == [v.raw for v in guidata.variable_table(stock)]
    gs, _ = guidata.guidata_extent(out)
    desc = gs + guidata.VARIABLE_TABLE_DESCRIPTOR
    assert ((out[desc + 1] & 3) << 8) | out[desc + 3] == 299       # what FUN_0021BE50 reads
    assert _gc_count(out) == 299                                     # the boot cross-check
    assert register.status_slot_addr(298) == 0x20008254


def test_nothing_past_the_image_container_moves():
    stock = _stock()
    out, _e, _i = appendfilter.append_filters(stock, [PROBE])
    assert len(out) == len(stock)
    gs, ge = guidata.guidata_extent(stock)
    img = gs + appendfilter.coverage.IMAGE_CONTAINER_OFFSET
    # everything from the image container up to the section checksum (its last 4 bytes)
    assert out[img:ge - 4] == stock[img:ge - 4]
    lcd = next(s for s in container.parse_container(stock).sections if s.name == "LCD Firmware")
    assert out[lcd.offset:lcd.offset + lcd.length] == stock[lcd.offset:lcd.offset + lcd.length]


def test_aux_table_shifts_with_the_growth():
    stock = _stock()
    out, _e, _i = appendfilter.append_filters(stock, [PROBE])
    gs, _ = guidata.guidata_extent(stock)
    off0 = struct.unpack_from("<H", stock, gs + 0x2C)[0]
    off1 = struct.unpack_from("<H", out, gs + 0x2C)[0]
    assert off1 == off0 + 8
    assert out[gs + off1:gs + off1 + 12] == stock[gs + off0:gs + off0 + 12]


def test_refuses_past_the_registry_cap():
    stock = _stock()
    have = len(statusbind.registered_dgns(stock))
    fresh = [appendfilter.NewFilter(dgn=0x10000 | (0x0100 + i), mode=1, payload_byte=0)
             for i in range(statusbind.REGISTRY_CAP - have + 1)]
    with pytest.raises(appendfilter.AppendFilterError, match="cap"):
        appendfilter.append_filters(stock, fresh)


def test_record_gated_entry_packs_plus4_plus5():
    d = open(STOCK, "rb").read()
    out, _e, (v,) = appendfilter.append_filters(d, [appendfilter.NewFilter(
        dgn=0x0FFB0, mode=1, payload_byte=7, refresh=1, instance=1, compare_byte=0x00)])
    raw = guidata.variable_table(out)[v].raw
    assert raw[4] == 1 and raw[5] == 0x00
    assert (raw[2] >> 2) & 3 == 1            # refresh class 1 = 10 s


@pytest.mark.parametrize("kw", [dict(instance=1),                  # instance without compare byte
                                dict(instance=1, compare_byte=0x08)])  # low nibble > 7
def test_bad_filter_refused(kw):
    with pytest.raises(appendfilter.AppendFilterError):
        appendfilter.NewFilter(dgn=0x0FFB0, mode=1, payload_byte=1, **kw)


def test_op_hands_a_register_ref_to_add_register_readout():
    proj = json.dumps({
        "schema": 1,
        "base_sha256": "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382",
        "build_name": "append-filter-test",
        "ops": [
            {"id": "probe", "type": "append_filter", "enabled": True,
             "params": {"dgn": 0x1FEA0, "mode": 1, "payload_byte": 0}},
            {"id": "show", "type": "add_register_readout", "enabled": True,
             "params": {"register": {"ref": "probe"}, "mask": 255, "width_bytes": 1,
                        "page": 4, "x": 300, "y": 357}},
        ],
    })
    res = recipe.compile_project(recipe.Project.from_json(proj), _stock())
    assert res.ok, res.errors
    # coach-verified 2026-09-22 (see docs/HARDWARE-VERIFIED.md), so it must NOT report unverified
    assert "probe" not in res.unverified
    # the readout routine loads the NEW var's receive slot
    gc = appendfilter._guicode_section(res.data)
    code = res.data[gc.offset:gc.offset + gc.length]
    assert struct.pack("<I", register.status_slot_addr(298)) in code
