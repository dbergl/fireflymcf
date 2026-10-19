"""G12 OUTPUTS names (`outputs`) and the button ops' refusal to wire a named output."""
import hashlib
import json
import random
from pathlib import Path

import pytest

from fireflymcf import cellwriters, cli, guidata, layout, options, outputs, recipe, tankgates

from tests import answer_key

STOCK = Path("firefly-13.6/mcf.bin")
BUILDS = {"136": Path("firefly-13.6/mcf.bin"), "147": Path("firefly-14.7 (Terrain)/mcf.bin"),
          "1123": Path("firefly-11.23/mcf.bin")}



@pytest.fixture(scope="module")
def stock():
    return STOCK.read_bytes()


def test_names_come_from_the_outputs_list(stock):
    names = outputs.output_names(stock)
    assert len(names) == 44
    assert names[12] == "A/C Fan Low" and names[44] == "Water Pump"
    assert names[28] == "" and names[29] == ""
    assert names[5] == ""        # the label is a bare prefix; the name is a GuiCode string cell


# --- every name each build's OUTPUTS page can show, and when (pinned) ----------------------
#
# (name, kind, condition). 13.6 rows 05-08 reproduce GUICODE Finding 35's hand transcription;
# "#0" is floorplan value 0, which no floorplan-name arm names on 13.6/14.7.

PINNED_136 = {
    3: [('Awning EXT', 'static', 'always')],
    4: [('Awning RET', 'static', 'always')],
    5: [('Tank Heat Fresh', 'dynamic', 'floorplan WA, WD, WT')],
    6: [('Tank Heat Grey', 'dynamic', 'floorplan RT, SY, TB, CB, WT, RY')],
    7: [('Tank Heat Black', 'dynamic', 'floorplan RT, WT and Black Tank on')],
    8: [('Tank Heat Grey 2', 'dynamic', 'floorplan RA, RD, WA, WD')],
    9: [('A/C Cool', 'static', 'always')],
    10: [('A/C Heat Pump', 'dynamic', 'Heat Pump on')],
    11: [('A/C Fan High', 'static', 'always')],
    12: [('A/C Fan Low', 'static', 'always')],
    18: [('AES Start/Stop', 'dynamic', 'AGS / AES / AES Single Output = AES Single Output')],
    19: [
        ('AES Start', 'dynamic', 'AGS / AES / AES Single Output = AES'),
        ('Gen Start', 'dynamic', 'AGS / AES / AES Single Output = AGS'),
    ],
    20: [
        ('AES Stop', 'dynamic', 'AGS / AES / AES Single Output = AES'),
        ('Gen Stop', 'dynamic', 'AGS / AES / AES Single Output = AGS'),
    ],
    21: [('Awning Lt', 'static', 'always')],
    22: [('Kitchen OHC Lts', 'static', 'always')],
    23: [('Step Lt', 'static', 'always')],
    24: [('Bed Lts', 'dynamic', 'any floorplan but RT, WT')],
    25: [
        ('Bath Lts', 'dynamic', 'any floorplan but SY, TB and Cargo / Bath Light on'),
        ('Cargo Lts', 'dynamic', 'floorplan SY, TB and Cargo / Bath Light on'),
    ],
    26: [('Bath Lts', 'dynamic', 'Bath Light on')],
    32: [('Main Ceiling Lts', 'static', 'always')],
    34: [('Bunk Accent Lts', 'dynamic', 'Bunk Accent on')],
    35: [
        ('Bench OHC Lts', 'dynamic', 'floorplan SY, TB, CB, RY'),
        ('Living Rm Lts', 'dynamic', 'floorplan RA, RD, WA, WD'),
        ('Security Lt', 'dynamic', 'floorplan #0, RT, WT, 4NJ, 3NG, 3NK, 3NJ'),
    ],
    40: [('Bath Fan', 'dynamic', 'Bath Fan on')],
    43: [('A/C Cool', 'static', 'always')],
    44: [('Water Pump', 'static', 'always')],
}
BLANK_136 = [1, 2, 13, 14, 15, 16, 17, 27, 28, 29, 30, 31, 33, 36, 37, 38, 39, 41, 42]

PINNED_147 = {
    3: [('Awning EXT', 'static', 'always')],
    4: [('Awning RET', 'static', 'always')],
    5: [('Tank Heat Fresh', 'dynamic', 'floorplan WA, WT')],
    6: [('Tank Heat Grey', 'dynamic', 'any floorplan but #0, RA, WA')],
    7: [('Tank Heat Black', 'dynamic', 'floorplan RT, WT and Black Tank on')],
    8: [
        ('Tank Heat Grey 2', 'static', 'always'),
        ('Tank Heat Grey 2', 'dynamic', 'floorplan RA, WA'),
    ],
    9: [('A/C Cool', 'static', 'always')],
    10: [('A/C Heat Pump', 'dynamic', 'Heat Pump on')],
    11: [('A/C Fan High', 'static', 'always')],
    12: [('A/C Fan Low', 'static', 'always')],
    17: [('Starlink Power', 'dynamic', 'Starlink Power on')],
    18: [('AES Start/Stop', 'dynamic', 'AGS / AES / AES Single Output = AES Single Output')],
    19: [
        ('AES Start', 'dynamic', 'AGS / AES / AES Single Output = AES'),
        ('Gen Start', 'dynamic', 'AGS / AES / AES Single Output = AGS'),
    ],
    20: [
        ('AES Stop', 'dynamic', 'AGS / AES / AES Single Output = AES'),
        ('Gen Stop', 'dynamic', 'AGS / AES / AES Single Output = AGS'),
    ],
    21: [('Awning Lt', 'static', 'always')],
    22: [('Kitchen OHC Lts', 'static', 'always')],
    23: [('Step Lt', 'static', 'always')],
    24: [('Bed Lts', 'dynamic', 'any floorplan but RT, WT')],
    25: [('Cargo Lts', 'dynamic', 'Cargo Light on')],
    32: [('Main Ceiling Lts', 'static', 'always')],
    34: [('Bunk Accent Lts', 'dynamic', 'Bunk Accent on')],
    35: [
        ('Bench OHC Lts', 'dynamic', 'floorplan SY, TB, CB, RY, RE'),
        ('Living Rm OHC Lts', 'dynamic', 'floorplan RA, WA'),
        ('Security Lt', 'dynamic', 'floorplan #0, RT, WT'),
    ],
    43: [('A/C Cool', 'static', 'always')],
    44: [('Water Pump', 'static', 'always')],
}
BLANK_147 = [1, 2, 13, 14, 15, 16, 26, 27, 28, 29, 30, 31, 33, 36, 37, 38, 39, 40, 41, 42]

PINNED_1123 = {
    3: [('AWNING EXT', 'static', 'always')],
    4: [('AWNING RET', 'static', 'always')],
    5: [('TANK HEATER FRESH', 'switched', 'floorplan 3WT / 4WT, 3WA / 4WA, 3WD / 4WD, 3CB / 4CB')],
    6: [
        ('TANK HEATER GREY', 'switched', 'floorplan Ship Mode, 3RT / 4RT, 3WT / 4WT'),
        ('TANK HEATER GR 1', 'switched', 'floorplan 3RA / 4RA, 3WA / 4WA, 3WD / 4WD, 3RD / 4RD'),
    ],
    7: [('TANK HEATER BLACK', 'switched', 'any floorplan but 3RA / 4RA, 3RD / 4RD')],
    8: [
        ('TANK HEATER GR 2', 'switched', 'floorplan 3RA / 4RA, 3WA / 4WA, 3WD / 4WD, 3RD / 4RD'),
        ('TANK HEATER GREY', 'switched', 'floorplan 3CB / 4CB'),
    ],
    9: [('A/C COOL', 'switched', 'floorplan 3RT / 4RT, 3RA / 4RA, 3CB / 4CB, 3RD / 4RD')],
    10: [('A/C 1 HEAT PUMP', 'switched', 'slot 248 (config byte 0x0a) = 1')],
    11: [('A/C 1 FAN HIGH', 'static', 'always')],
    12: [('A/C 1 FAN LOW', 'static', 'always')],
    19: [('GENERATOR START', 'switched', 'floorplan 3RT / 4RT, 3RA / 4RA, 3CB / 4CB, 3RD / 4RD')],
    20: [('GENERATOR STOP', 'switched', 'floorplan 3RT / 4RT, 3RA / 4RA, 3CB / 4CB, 3RD / 4RD')],
    21: [('AWNING LT', 'static', 'always')],
    22: [('KITCHEN OHC LTS', 'static', 'always')],
    23: [('STEP LT', 'static', 'always')],
    24: [('BED LTS', 'switched', 'any floorplan but Ship Mode, 3RT / 4RT, 3WT / 4WT')],
    25: [('BATH LTS', 'static', 'always')],
    32: [('MAIN CEILING LTS', 'static', 'always')],
    34: [('BUNK ACCENT', 'static', 'always')],
    35: [
        ('SECURITY LT RR', 'switched', 'floorplan Ship Mode, 3RT / 4RT, 3WT / 4WT'),
        ('LIVING ROOM OHC', 'switched', 'floorplan 3RA / 4RA, 3WA / 4WA, 3WD / 4WD, 3RD / 4RD'),
        ('BENCH OHC LTS', 'switched', 'floorplan 3CB / 4CB'),
    ],
    40: [('BATH FAN', 'switched', 'slot 247 (config byte 0x0e) = 1')],
    43: [('A/C 1 COOL', 'switched', 'floorplan 3WT / 4WT, 3WA / 4WA, 3WD / 4WD')],
    44: [('WATER PUMP', 'static', 'always')],
}
BLANK_1123 = []



@pytest.mark.parametrize("build, expected, blank", [
    ("136", PINNED_136, BLANK_136), ("147", PINNED_147, BLANK_147),
    ("1123", PINNED_1123, BLANK_1123)])
def test_every_outputs_page_name_with_its_condition(build, expected, blank):
    data = BUILDS[build].read_bytes()
    got = outputs.output_name_variants(data)
    assert {p: [(n.name, n.kind, n.when) for n in ns] for p, ns in got.items() if ns} == expected
    assert sorted(p for p, ns in got.items() if not ns) == blank


def test_every_name_cites_its_chain(stock):
    for pin, names in outputs.output_name_variants(stock).items():
        for n in names:
            assert n.source.startswith("object 35 record @0x"), n
            if n.kind == "dynamic":
                assert "GuiCode 0x2494b2-0x249794 stores it at 0x" in n.source, n


def test_the_coach_wd_screen_is_reproduced(stock):
    """Hardware oracle (BUS-BASELINE PC.18b, owner 2026-08-18): on stock 13.6 floorplan WD names
    row 05 Tank Heat Fresh and 08 Tank Heat Grey 2 and leaves 06 and 07 blank."""
    wd = outputs.named_outputs(stock, floorplan="WD")
    assert [n.name for n in wd[5]] == ["Tank Heat Fresh"]
    assert [n.name for n in wd[8]] == ["Tank Heat Grey 2"]
    assert 6 not in wd and 7 not in wd


def test_tank_rows_agree_with_the_hand_checked_gate_emulator(stock):
    """`tankgates.gates` (Finding 35, its own interpreter over the same four gates) and this
    module's generic block emulation must agree on every floorplan, Black Tank on and off."""
    row = {"05 Fresh": 5, "06 Grey": 6, "07 Black": 7, "08 Grey 2": 8}
    variants = outputs.output_name_variants(stock)
    for value, code in options.floorplan_options(stock).items():
        for black in (False, True):
            want = {row[r] for r in tankgates.gates(stock, value, black_tank=black)}
            got = set()
            for pin in (5, 6, 7, 8):
                for n in variants[pin]:
                    for clause in n.condition:
                        ok = all(
                            (code in {g.name(v) for v in vals}) if g.kind == "floorplan"
                            else (("on" if black else "off") in {g.name(v) for v in vals})
                            for g, vals in clause)
                        if ok:
                            got.add(pin)
            assert got == want, (code, black)


def test_the_widened_tank_gates_read_back_from_a_patched_image(stock):
    """`tankgates.widen` (flashed as v10, confirmed all four rows on WD, PC.18b): the same
    emulation reads the patched whitelists off the patched bytes."""
    patched, _ = tankgates.widen(stock)
    wd = outputs.named_outputs(patched, floorplan="WD")
    assert {p for p in (5, 6, 7, 8) if p in wd} == {5, 6, 7, 8}
    grey = outputs.output_name_variants(patched)[6]
    assert [n.when for n in grey] == ["floorplan RT, RA, RD, WA, WD, WT"]


def test_the_pin_sheet_alternatives_are_the_outputs_page_variants(stock):
    """The G12 pin sheet (answer_key.CHANNELS, checked on the bus) gives 25 as
    'CARGO LTS / BATH LTS' and 35 as 'SECURITY LT / LIVRM OHC / BENCH SEAT OHC'. The page shows
    exactly that many names for those pins, and names every pin the sheet names."""
    variants = outputs.output_name_variants(stock)
    for pin, sheet in answer_key.CHANNELS.items():
        assert variants[pin], pin            # the old own-records read left 5-8, 18, 24-26, 34, 35 blank
    for pin in (25, 35):
        assert len({n.name for n in variants[pin]}) == len(answer_key.CHANNELS[pin].split(" / "))


def test_captions_agree_with_the_outputs_page_on_floorplan_variants(stock):
    """Two independent mechanisms: the LIGHTS cards are GuiData switch arms on the floorplan slot,
    the OUTPUTS row is a GuiCode string block. Pin 35's three names split the floorplans the same
    way both times."""
    page = {n.name: n.when for n in outputs.output_name_variants(stock)[35]}
    caps = {c.text: c.when for c in outputs.control_captions(stock)[35]}
    assert page["Security Lt"].startswith("floorplan #0, RT, WT")
    assert caps["Security Light"] == "floorplan RT, WT"
    assert page["Living Rm Lts"] == caps["Living Rm OHC / Exterior"] == "floorplan RA, RD, WA, WD"
    assert page["Bench OHC Lts"] == "floorplan SY, TB, CB, RY"
    bench = {caps["Bench OHC / Exterior"], caps["Bench OHC"], caps["Bench Seat OHC / Exterior"]}
    assert bench == {"floorplan SY", "floorplan TB, CB", "floorplan RY"}


def test_control_captions_name_the_home_buttons(stock):
    caps = outputs.control_captions(stock)
    water = [c for c in caps[44] if c.text == "Water Pump"]
    assert water and water[0].via == ("command",)
    fan = [c for c in caps[40] if c.text == "Bath Fan"]
    assert fan and fan[0].via == ("status", "command") and fan[0].when == "Bath Fan on"
    assert "inferred-from-data" in fan[0].source


# --- the emulator itself ---------------------------------------------------------------------

def _cells_and_values(data):
    gates = outputs._gates(data)
    dptr, toff = guidata.find_variable_tables(data)
    root = outputs.outputs_root(data)
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    cells = [layout.variable_register(data, r.value_slot, dptr, toff)[0]
             for r in options._own_records(data, gs, ge, table, root)
             if r.wtype == outputs.DYNAMIC_STRING_WTYPE]
    return cells, {g.key: g.values for g in gates.by_register.values()}


def _unicorn_cells(data, start, end, inputs, cells):
    unicorn = pytest.importorskip("unicorn")
    from unicorn.arm_const import UC_ARM_REG_PC
    from fireflymcf import theme
    sec = theme.guicode_section(data)
    base = theme.guicode_base(sec)
    code = data[sec.offset:sec.offset + sec.length]
    mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
    mu.mem_map(base & ~0xFFF, (len(code) + (base & 0xFFF) + 0xFFF) & ~0xFFF)
    mu.mem_write(base, bytes(code))
    mu.mem_map(0x20000000, 0x80000)
    for addr, v in inputs.items():
        mu.mem_write(addr, bytes([v]))

    def hook(uc, address, size, _user):
        if not start <= address <= end:
            uc.emu_stop()
    mu.hook_add(unicorn.UC_HOOK_CODE, hook)
    mu.emu_start(start | 1, 0, count=4000)
    assert not start <= mu.reg_read(UC_ARM_REG_PC) <= end
    out = {}
    for c in cells:
        raw = bytes(mu.mem_read(c, cellwriters.NAME_MAX))
        out[c] = raw.split(b"\0")[0].decode("latin-1")
    return out


@pytest.mark.parametrize("build", ["136", "147"])
def test_the_interpreter_matches_unicorn(build):
    """Independent CPU: the same block, start and exit run on Unicorn give the same string in
    every cell, on 40 random input combinations per build."""
    data = BUILDS[build].read_bytes()
    cells, values = _cells_and_values(data)
    ct = cellwriters.cell_table(data, cells, values)
    tab = ct.table()
    keys = list(tab[cells[0]])
    rng = random.Random(1123)
    for key in rng.sample(keys, 40):
        got = _unicorn_cells(data, ct.start, ct.end, dict(zip(ct.inputs, key)), cells)
        for c in cells:
            assert (tab[c][key] or "") == got[c], (hex(c), key)


def test_every_write_site_ran(stock):
    cells, values = _cells_and_values(stock)
    ct = cellwriters.cell_table(stock, cells, values)
    assert len(ct.sites) == 32 and (ct.start, ct.end) == (0x2494B2, 0x249794)
    assert len(ct.inputs) == 8 and len(ct.outcomes) == 15 * 4 * 2 ** 6


def test_a_block_the_interpreter_cannot_run_is_refused(stock):
    """A `muls` written over `cmp r5,#1` (0x2494C4) is outside the supported set: the emulation
    refuses, no name is reported for the dynamic rows, and the button ops still refuse them."""
    from fireflymcf import theme
    sec = theme.guicode_section(stock)
    off = sec.offset + (0x2494C4 - theme.guicode_base(sec))
    assert stock[off:off + 2] == bytes.fromhex("012d")
    bad = bytearray(stock)
    bad[off:off + 2] = bytes.fromhex("6b43")          # muls r3, r5, r3
    cells, values = _cells_and_values(bytes(bad))
    with pytest.raises(cellwriters.CellEmulationError):
        cellwriters.cell_table(bytes(bad), cells, values)
    v = outputs.output_name_variants(bytes(bad))
    assert [n.name for n in v[5]] == [None] and "CellEmulationError" in v[5][0].source
    assert 5 in outputs.named_outputs(bytes(bad))


def test_relevant_inputs_drop_what_never_changes_the_text():
    table = {(a, b): ("x" if a else "") for a in (0, 1) for b in (0, 1)}
    assert cellwriters.relevant_inputs(table, ("A", "B"), ((0, 1), (0, 1))) == (0,)


# --- the button ops' refusal --------------------------------------------------------------

def _compile(stock, op):
    project = recipe.Project.from_json(json.dumps({
        "schema": 1, "base_sha256": hashlib.sha256(stock).hexdigest(),
        "build_name": "named output test", "ops": [op]}))
    return recipe.compile_project(project, stock)


def _porch(channel, **extra):
    return {"id": "porch", "type": "add_button",
            "params": dict({"channel": channel, "label": "Porch", "off_colour": [65, 81, 163]},
                           **extra)}


def test_add_button_refuses_an_output_named_for_another_load(stock):
    # The skill scenario's trap: the owner said "output 12" for a porch light; the panel's
    # own list names 12 "A/C Fan Low", and the channel-collision check does not catch it.
    res = _compile(stock, _porch(12))
    assert not res.ok
    [err] = res.errors
    assert err["code"] == "NAMED_OUTPUT" and "A/C Fan Low" in err["message"]
    assert err["offers"] == ["acknowledge_named_output"]


def test_add_button_refuses_an_output_named_only_under_an_option(stock):
    """26 is a bare "26 - " label; GuiCode names it "Bath Lts" only with the Bath Light option.
    A recipe cannot know the coach's options (they live in G12 NVM), so it is refused."""
    res = _compile(stock, _porch(26, allow_commanded_channel=True))
    assert not res.ok
    [err] = res.errors
    assert err["code"] == "NAMED_OUTPUT" and "'Bath Lts' (Bath Light on)" in err["message"]


def test_allow_named_output_is_the_acknowledgement(stock):
    res = _compile(stock, _porch(12, allow_named_output=True))
    assert res.ok, res.errors


def test_an_unnamed_output_needs_no_acknowledgement(stock):
    res = _compile(stock, _porch(28, allow_commanded_channel=True))
    assert res.ok, res.errors


def test_pins_reports_both_names(stock, capsys):
    assert cli.main(["pins", "--all", "--json", str(STOCK)]) == 0
    rows = {r["pin"]: r for r in json.loads(capsys.readouterr().out)["pins"]}
    [fresh] = rows[5]["outputs_page_names"]
    assert fresh["name"] == "Tank Heat Fresh" and fresh["conditions"] == [
        {"floorplan": ["WA", "WD", "WT"]}]
    assert "Fresh Tank Heater" in [c["text"] for c in rows[5]["captions"]]
    assert rows[44]["outputs_page_names"][0]["when"] == "always"
    assert cli.main(["pins", "--all", "--names", str(STOCK)]) == 0
    out = capsys.readouterr().out
    assert "Tank Heat Fresh+" in out and "OUTPUTS  'Tank Heat Fresh': floorplan WA, WD, WT" in out


# --- recolor refuses a card of controls ---------------------------------------------------

def _recolor(stock, obj, **extra):
    return _compile(stock, {"id": "c", "type": "recolor",
                            "params": dict({"object": obj, "color": [27, 117, 188]}, **extra)})


def test_recolor_refuses_a_card_of_controls(stock):
    # HOME's lights card (905) has a box, so the box lookup succeeds, and repainting it turns
    # the whole card blue: Main Ceiling, Awning Light and Step Light together.
    res = _recolor(stock, 905)
    assert not res.ok and res.errors[0]["code"] == "CONTAINER"
    assert "3 touch regions" in res.errors[0]["message"]
    assert res.errors[0]["offers"] == ["acknowledge_container"]


def test_recolor_a_card_on_purpose(stock):
    assert _recolor(stock, 905, allow_container=True).ok


def test_recolor_one_button(stock):
    assert _recolor(stock, 279).ok
