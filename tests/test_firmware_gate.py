"""Ops declare the firmware they run on; compile refuses an op outside it, and refuses an image
fireflymcf cannot identify. Hardware verification is per firmware (`Op.is_verified`)."""
import pytest
import hashlib
import shutil
from pathlib import Path

from fireflymcf import recipe, userop
from tests.firmware_helpers import _rename_guicode

THEME_PARAMS = {"colours": {"bg": [10, 20, 30]}}
# Real labels: object 5 holds '   ELECTRICAL' on 13.6, object 11 holds '   LIGHTS' on 14.7
# (`fireflymcf labels MCF --containing TEXT`).
RENAME_136 = {"object": 5, "old": "ELECTRICAL", "new": "ELECTRICAX"}
RENAME_147 = {"object": 11, "old": "LIGHTS", "new": "LIGHTX"}
# Object 240 is 14.7's Star Link button (group 17, one toggle).
GESTURE_147 = {"target": 240, "kind": "hold_to_toggle"}
USEROPS = Path(__file__).resolve().parent / "fixtures" / "userops"


def _project(stock: bytes, specs) -> recipe.Project:
    return recipe.Project(base_sha256=hashlib.sha256(stock).hexdigest(),
                          build_name="firmware gate test",
                          ops=[recipe.OpSpec(id=i, type=t, params=dict(p)) for i, t, p in specs])


def _compile(stock: bytes, specs) -> recipe.CompileResult:
    return recipe.compile_project(_project(stock, specs), stock)


def test_op_defaults():
    assert recipe.Op.firmwares == frozenset({"13.6"})
    assert recipe.Op.verified_on == frozenset({"13.6"})
    assert recipe.Op.emulated_on == frozenset()
    o = recipe.REGISTRY["rename_label"](recipe.OpSpec("r", "rename_label", dict(RENAME_136)))
    assert o.is_verified("13.6") and not o.is_verified("14.7")
    o.hardware_verified = False
    assert not o.is_verified("13.6")


def test_compile_refuses_an_op_not_listed_for_the_firmware(mcf_147):
    # set_gesture lists only 13.6 (its 14.7 emulator test fails: tests/emu/test_ops147.py)
    res = _compile(mcf_147, [("t", "set_gesture", GESTURE_147)])
    assert not res.ok and res.data is None
    assert [(e["code"], e["op_id"]) for e in res.errors] == [("UNSUPPORTED_FIRMWARE", "t")]
    assert "14.7" in res.errors[0]["message"] and "13.6" in res.errors[0]["message"]
    assert res.errors[0]["message"].startswith(
        "set_gesture is not supported on firmware 14.7 (Lyra SW20 14v7 GuiCode + LCD cbed1028d988)")


def test_an_unsupported_op_reaches_no_op_code(mcf_147, monkeypatch):
    cls = recipe.REGISTRY["set_gesture"]

    def boom(self, *a, **k):
        raise AssertionError("op code ran")
    monkeypatch.setattr(cls, "validate", boom)
    monkeypatch.setattr(cls, "apply", boom)
    res = _compile(mcf_147, [("t", "set_gesture", GESTURE_147)])
    assert [e["code"] for e in res.errors] == ["UNSUPPORTED_FIRMWARE"]


def test_every_refusal_is_reported_before_any_apply(mcf_147):
    res = _compile(mcf_147, [("a", "set_gesture", GESTURE_147), ("b", "tank_heater_names", {})])
    assert [e["op_id"] for e in res.errors] == ["a", "b"]
    assert {e["code"] for e in res.errors} == {"UNSUPPORTED_FIRMWARE"}


def test_a_disabled_op_is_not_refused(mcf_136):
    proj = _project(mcf_136, [("t", "set_theme", THEME_PARAMS)])
    proj.ops[0].enabled = False
    assert recipe.compile_project(proj, mcf_136).ok


def test_compile_refuses_an_unidentified_image(mcf_136, mcf_147):
    mixed = _rename_guicode(mcf_136, b"Lyra SW20 14v7 GuiCode")
    res = _compile(mixed, [("r", "rename_label", RENAME_136)])
    assert res.errors[0]["code"] == "UNSUPPORTED_FIRMWARE" and res.errors[0]["op_id"] == "r"
    assert "not one fireflymcf has been tested on" in res.errors[0]["message"]


def test_refusal_names_the_unknown_guicode(mcf_147):
    img = _rename_guicode(mcf_147, b"Lyra SW20 14v8 GuiCode")
    res = _compile(img, [("r", "rename_label", RENAME_147)])
    assert res.errors[0]["code"] == "UNSUPPORTED_FIRMWARE"
    assert "Lyra SW20 14v8 GuiCode" in res.errors[0]["message"]


def test_an_unidentified_image_is_refused_without_ops(mcf_147):
    img = _rename_guicode(mcf_147, b"Lyra SW20 14v8 GuiCode")
    res = _compile(img, [])
    assert [(e["code"], e["op_id"]) for e in res.errors] == [("UNSUPPORTED_FIRMWARE", None)]


@pytest.mark.slow
def test_unverified_is_per_firmware(mcf_136, mcf_147, monkeypatch):
    monkeypatch.setattr(recipe.REGISTRY["rename_label"], "firmwares", frozenset({"13.6", "14.7"}))
    on136 = _compile(mcf_136, [("r", "rename_label", RENAME_136)])
    on147 = _compile(mcf_147, [("r", "rename_label", RENAME_147)])
    assert on136.ok and on136.unverified == [] and on136.firmware == "13.6"
    assert on147.ok and on147.unverified == ["r"] and on147.firmware == "14.7"


def test_emulated_on_does_not_verify(mcf_147, monkeypatch):
    cls = recipe.REGISTRY["rename_label"]
    monkeypatch.setattr(cls, "firmwares", frozenset({"13.6", "14.7"}))
    monkeypatch.setattr(cls, "emulated_on", frozenset({"14.7"}))
    assert _compile(mcf_147, [("r", "rename_label", RENAME_147)]).unverified == ["r"]


def test_cached_compile_refuses_the_same_way(mcf_147):
    cache = {}
    a = recipe.compile_cached(_project(mcf_147, [("t", "set_gesture", GESTURE_147)]), mcf_147,
                              cache)
    b = recipe.compile_cached(_project(mcf_147, [("t", "set_gesture", GESTURE_147)]), mcf_147,
                              cache)
    assert a.errors == b.errors and a.errors[0]["code"] == "UNSUPPORTED_FIRMWARE"


def test_a_cached_prefix_does_not_let_an_unsupported_op_through(mcf_147, monkeypatch):
    """A prefix compiled while an op was listed for 14.7 stays in the cache; once the op is no
    longer listed, the cached prefix must not carry it past the gate."""
    cache = {}
    # set_gesture lists only 13.6 (its 14.7 emulator test fails: tests/emu/test_ops147.py)
    cls = recipe.REGISTRY["set_gesture"]
    with monkeypatch.context() as m:
        m.setattr(cls, "firmwares", frozenset({"13.6", "14.7"}))
        assert recipe.compile_cached(_project(mcf_147, [("r", "set_gesture", GESTURE_147)]),
                                     mcf_147, cache).ok
    assert cache
    res = recipe.compile_cached(_project(mcf_147, [("r", "set_gesture", GESTURE_147)]),
                                mcf_147, cache)
    assert [(e["code"], e["op_id"]) for e in res.errors] == [("UNSUPPORTED_FIRMWARE", "r")]


def _user_op_project(tmp_path: Path, stock: bytes):
    shutil.copytree(USEROPS / "cmd_flip", tmp_path / "ops" / "cmd_flip")
    pf = tmp_path / "p.ffproj.json"
    pf.write_text("{}")
    proj = _project(stock, [("u", "user:cmd-flip", {"declare": True})])
    return pf, proj


def test_user_op_runs_on_identified_firmware_only(mcf_147, tmp_path):
    # cmd_flip finds its binding structurally, so it runs on any firmware; no op lists it
    pf, proj = _user_op_project(tmp_path, mcf_147)
    with userop.registered(userop.load_user_ops(pf, proj)):
        res = recipe.compile_project(proj, mcf_147)
    assert res.ok, res.errors
    assert res.firmware == "14.7" and res.unverified == ["u"]

    img = _rename_guicode(mcf_147, b"Lyra SW20 14v8 GuiCode")
    pf, proj = _user_op_project(tmp_path / "x", img)
    with userop.registered(userop.load_user_ops(pf, proj)):
        res = recipe.compile_project(proj, img)
    assert [(e["code"], e["op_id"]) for e in res.errors] == [("UNSUPPORTED_FIRMWARE", "u")]
