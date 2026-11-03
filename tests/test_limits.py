"""The GuiData flash ceiling: GuiData must end by 0x903F0000 (4 MiB of QSPI, less the top
erase block). Every grow and every compile meets it, built-in ops and user ops alike."""
import hashlib
import json

import pytest

from fireflymcf import cli, container, limits, recipe, relocate, routinekit, userop

NEEDS_RESEARCH_DATA = "grows the 13.6 corpus image's GuiData up to the ceiling"


def _gd_len(data: bytes) -> int:
    return next(s for s in container.parse_container(data).sections
                if "GuiData" in s.name).length


def test_the_ceiling():
    assert limits.GUIDATA_END_MAX == 0x903F0000
    assert limits.GUIDATA_END_MAX - limits.GUIDATA_BASE == 4_128_768
    assert issubclass(limits.GuiDataTooLarge, recipe.RecipeError)
    limits.check_guidata_size(4_128_768)
    with pytest.raises(limits.GuiDataTooLarge) as e:
        limits.check_guidata_size(4_128_769)
    assert e.value.code == "GUIDATA_TOO_LARGE"
    msg = str(e.value)
    assert "0x903f0000" in msg and "proven-from-code" in msg and "inferred" in msg


def test_a_grow_past_the_real_ceiling_is_refused(mcf_136):
    """Refused before anything is inserted: relocate raises it, the Editor maps it, and the
    image is unchanged."""
    over = 4_128_768 - _gd_len(mcf_136) + 4
    gd = _gd_len(mcf_136)
    with pytest.raises(limits.GuiDataTooLarge):
        relocate.relocate(mcf_136, gd - 4, bytes(over))
    ed = userop.Editor(mcf_136)
    with pytest.raises(userop.EditorError) as e:
        ed.grow_guidata(bytes(over), "too much")
    assert e.value.code == "GUIDATA_TOO_LARGE"
    assert ed.data == mcf_136 and ed.ledger == ()


def _ceiling_at(monkeypatch, data, slack):
    monkeypatch.setattr(limits, "GUIDATA_END_MAX", limits.GUIDATA_BASE + _gd_len(data) + slack)


def test_just_under_passes_just_over_refuses(mcf_136, monkeypatch):
    """With the ceiling 64 bytes past stock's GuiData end: 64 bytes pass, 68 do not."""
    _ceiling_at(monkeypatch, mcf_136, 64)
    ed = userop.Editor(mcf_136)
    ed.grow_guidata(bytes(range(64)), "fits")
    assert _gd_len(ed.data) == _gd_len(mcf_136) + 64
    with pytest.raises(userop.EditorError) as e:
        userop.Editor(mcf_136).grow_guidata(bytes(68), "does not")
    assert e.value.code == "GUIDATA_TOO_LARGE"


def test_colour_cells_meet_the_ceiling(mcf_136, monkeypatch):
    """The object swap (+4 bytes) is refused first, mapped to a coded RoutineKitError."""
    _ceiling_at(monkeypatch, mcf_136, 0)
    ed = userop.Editor(mcf_136)
    with pytest.raises(routinekit.RoutineKitError) as e:
        ed.call("own_colour_cells", [[721, "border"]])
    assert e.value.code == "GUIDATA_TOO_LARGE"
    with pytest.raises(routinekit.RoutineKitError) as e:
        ed.call("own_colour_cell", 721, "border")
    assert e.value.code == "GUIDATA_TOO_LARGE"
    assert ed.data == mcf_136


def _readout_project(stock):
    return {"schema": 1, "base_sha256": hashlib.sha256(stock).hexdigest(),
            "build_name": "Jayco B-Van V13 ceiling test",
            "ops": [{"id": "f", "type": "append_filter",
                     "params": {"dgn": 0x1FFD7, "mode": 2, "payload_byte": 1, "instance": 1,
                                "compare_byte": 0}},
                    {"id": "r", "type": "add_register_readout",
                     "params": {"register": {"ref": "f"}, "draw": False, "shift": 1}}]}


@pytest.mark.parametrize("where", ["relocate", "final gate"])
def test_a_built_in_build_over_the_ceiling_writes_nothing(mcf_136, monkeypatch, tmp_path,
                                                          capsys, where):
    """A built-in op's grow (the readout's cell-table grow) past the ceiling: the compile fails
    with GUIDATA_TOO_LARGE and the CLI writes no image. With the grow's own check switched off,
    the compile's final gate still refuses it."""
    _ceiling_at(monkeypatch, mcf_136, 64)
    if where == "final gate":
        monkeypatch.setattr(relocate, "_check_size", lambda length: None)
    pf = tmp_path / "p.ffproj.json"
    pf.write_text(json.dumps(_readout_project(mcf_136)))
    stock = tmp_path / "stock.bin"
    stock.write_bytes(mcf_136)
    out = tmp_path / "mcf-ceiling.bin"
    rc = cli.main(["compile", str(pf), str(stock), "-o", str(out), "--json"])
    assert rc != 0 and not out.exists()
    assert "GUIDATA_TOO_LARGE" in capsys.readouterr().out
