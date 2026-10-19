"""The Battery Heater button carries its owner-chosen text and size through both ops
(add_aes_batt_heat's `text`/`w`/`h`, add_aes_batt_heat_status's `button` ref)."""
import json

from fireflymcf import aescompact, guidata, recipe, records

STOCK = "firefly-13.6/mcf.bin"
SHA = "d66b41ffafaae4f5ec47cf90714f14bb1954fc3a5ae38defa98a94cd9643d382"


def _compile(battheat_params, status_params):
    rec = json.dumps({"schema": 1, "base_sha256": SHA, "build_name": "heater-text-test", "ops": [
        {"id": "aesswap", "type": "swap_aes_electrical", "params": {}, "enabled": True},
        {"id": "battheat", "type": "add_aes_batt_heat", "params": battheat_params, "enabled": True},
        {"id": "status", "type": "add_aes_batt_heat_status", "params": status_params,
         "enabled": True}]})
    res = recipe.compile_project(recipe.Project.from_json(rec), open(STOCK, "rb").read())
    assert res.ok, res.errors
    return res.data


def _arms(data):
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    frame = records.object_records(data, gs + tab[aescompact.ELECTRICAL_AES_BODY])
    sw = [r for r in frame if r.wtype == 6 and r.count == 4]
    assert len(sw) == 1
    return [records.object_records(data, gs + tab[i])
            for i in range(sw[0].child_index, sw[0].child_index + 4)]


WIDE = {"x": 84, "y": 156, "w": 328, "h": 54, "text": "Battery Heater"}


def test_every_arm_reads_battery_heater_at_the_wide_size():
    data = _compile(WIDE, {"button": {"ref": "battheat"}})
    for arm in _arms(data):
        assert arm[0].wtype == 33 and arm[0].geom[2:4] == [324, 50]
        assert any(r.wtype == 11 and r.label == "Battery Heater" for r in arm)
        assert not any(r.label == "Batt Heat" for r in arm)


def test_wide_button_glyph_top_right():
    data = _compile(WIDE, {"button": {"ref": "battheat"}})
    glyph_arms = [a for a in _arms(data) if any(r.font == aescompact.CONTACTOR_GLYPH_FONT_SLOT
                                                for r in a)]
    assert len(glyph_arms) == 2
    for arm in glyph_arms:
        g = next(r for r in arm if r.font == aescompact.CONTACTOR_GLYPH_FONT_SLOT)
        assert g.geom[0] == 324 - 26 == aescompact.contactor_glyph_dx(328)


def test_defaults_unchanged():
    assert aescompact.contactor_glyph_dx(aescompact.BATT_HEAT_W) == aescompact.CONTACTOR_GLYPH_DX
    data = _compile({}, {})
    for arm in _arms(data):
        assert arm[0].geom[2:4] == [aescompact.BATT_HEAT_W - 4, aescompact.BATT_HEAT_H - 4]
        assert any(r.label == aescompact.BATT_HEAT_TEXT for r in arm)
