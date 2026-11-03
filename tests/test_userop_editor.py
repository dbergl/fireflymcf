"""The checked Editor: guarded writes, a replayable ledger."""
import pytest

from fireflymcf import container, followchannel, theme, userop
from fireflymcf.userop import Editor, EditorError, replay

HOOK_ADDR = theme.GUICODE_BASE_DEFAULT + followchannel.HOOK_OFF
NEW = bytes.fromhex("f8f7ddfe")


def test_patch_records_and_replays(mcf_136):
    ed = Editor(mcf_136)
    ed.patch_addr("GuiCode", HOOK_ADDR, followchannel.HOOK_STOCK, NEW, "hook")
    assert ed.data != mcf_136
    assert len(ed.ledger) == 1 and ed.ledger[0].kind == "patch"
    assert replay(mcf_136, ed.ledger) == ed.data
    assert isinstance(ed.data, bytes)


def test_expect_mismatch_on_other_build(mcf_1123, mcf_147):
    for img in (mcf_1123, mcf_147):
        ed = Editor(img)
        try:
            ed.patch_addr("GuiCode", HOOK_ADDR, followchannel.HOOK_STOCK, NEW, "hook")
        except EditorError as e:
            assert e.code == "FIRMWARE_MISMATCH"
            assert "GuiCode+" in str(e) and hex(HOOK_ADDR) in str(e)
            assert followchannel.HOOK_STOCK.hex() in str(e)
            assert "holds " in str(e)
        else:
            pytest.skip("hook site matches on this build")  # pragma: no cover
        assert ed.ledger == ()


def test_size_changing_patch_refused(mcf_136):
    ed = Editor(mcf_136)
    with pytest.raises(EditorError) as e:
        ed.patch_addr("GuiCode", HOOK_ADDR, followchannel.HOOK_STOCK, NEW + b"\0", "x")
    assert e.value.code == "BAD_EDIT"


def test_unknown_primitive_refused(mcf_136):
    with pytest.raises(EditorError) as e:
        Editor(mcf_136).call("nope")
    assert e.value.code == "NOT_A_PRIMITIVE"


def test_grow_returns_load_address(mcf_136):
    gc = theme.guicode_section(mcf_136)
    ed = Editor(mcf_136)
    addr = ed.grow_guicode(bytes(8), "pad")
    assert addr == theme.GUICODE_BASE_DEFAULT + gc.length - 4
    assert replay(mcf_136, ed.ledger) == ed.data


@pytest.mark.parametrize("name", ["mcf_136", "mcf_1123", "mcf_147"])
def test_markers_select_one_section(name, request):
    ed = Editor(request.getfixturevalue(name))
    for m in ("GuiCode", "GuiData", "LCD", "G12"):
        assert ed.section(m)


def test_address_outside_section_refused(mcf_136):
    ed = Editor(mcf_136)
    with pytest.raises(EditorError) as e:
        ed.offset_of("GuiCode", 0x10)
    assert e.value.code == "BAD_EDIT"
    assert ed.offset_of("GuiData", 0x90000000) == ed.section("GuiData").offset
    assert ed.offset_of("G12", 4) == ed.section("G12").offset + 4


def test_call_dispatch_and_replay(mcf_136, monkeypatch):
    from fireflymcf import routinekit
    monkeypatch.setitem(routinekit.PRIMITIVES, "flip",
                        lambda d, i: (d[:i] + bytes([d[i] ^ 1]) + d[i + 1:], "ok"))
    ed = Editor(mcf_136)
    assert ed.call("flip", 5) == "ok"
    assert replay(mcf_136, ed.ledger) == ed.data
    with pytest.raises(EditorError):
        ed.call("flip", object())


def test_call_arguments_are_copied(mcf_136, monkeypatch):
    from fireflymcf import routinekit

    def eat(d, lst, opt=None):
        n = len(lst)
        lst.append(99)                      # a primitive mutating its argument in place
        opt["k"].append(1)
        return d[:5] + bytes([n + len(opt["k"])]) + d[6:]

    monkeypatch.setitem(routinekit.PRIMITIVES, "eat", eat)
    lst, opt = [1, 2], {"k": []}
    ed = Editor(mcf_136)
    ed.call("eat", lst, opt=opt)
    lst.append(7)                           # the caller editing after the call
    opt["k"].append(5)
    ed.ledger[0].args[1][0].append(3)       # reaching through the ledger
    assert ed.ledger[0].args == ("eat", ([1, 2],), {"opt": {"k": []}})
    assert replay(mcf_136, ed.ledger) == ed.data
