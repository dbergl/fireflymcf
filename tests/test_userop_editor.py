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


# --- grow_guidata -------------------------------------------------------------------------------

def _guidata(data):
    return next(s for s in container.parse_container(data).sections if "GuiData" in s.name)


@pytest.mark.parametrize("payload", [bytes(range(8)), b"\x5a" * 5])
def test_grow_guidata_appends_before_the_checksum(mcf_136, payload):
    """The new bytes land where the checksum was (`ge - gs - 4`), padded with zeros to a multiple
    of 4; the returned panel address is where `offset_of` finds them; the section and the image
    are sealed; nothing before the append point moved."""
    from fireflymcf import patch
    gd = _guidata(mcf_136)
    ed = Editor(mcf_136)
    addr = ed.grow_guidata(payload, "table")
    padded = payload + bytes(-len(payload) % 4)
    assert addr == theme.guicode_base(gd) + gd.length - 4 and addr % 4 == 0
    off = ed.offset_of("GuiData", addr)
    assert off == gd.offset + gd.length - 4
    assert ed.data[off:off + len(padded)] == padded
    new = _guidata(ed.data)
    assert new.length == gd.length + len(padded) and new.length % 4 == 0
    assert ed.data[gd.offset:off] == mcf_136[gd.offset:off]     # GuiData before it in place
    assert patch.reseal(ed.data)[1] == []                       # every checksum already right
    assert len(ed.data) == len(mcf_136) + len(padded)


def test_grow_guidata_ledger_replays(mcf_136):
    ed = Editor(mcf_136)
    ed.grow_guidata(b"\x01\x02\x03", "first")
    a2 = ed.grow_guidata(b"\x04" * 4, "second")
    assert [s.kind for s in ed.ledger] == ["grow_guidata", "grow_guidata"]
    assert ed.ledger[0].args == (b"\x01\x02\x03\x00",)          # recorded as appended
    assert replay(mcf_136, ed.ledger) == ed.data
    replayed, edits = userop._replay(mcf_136, ed.ledger)
    assert [(e.old, e.new) for e in edits] == [(b"", b"\x01\x02\x03\x00"), (b"", b"\x04" * 4)]
    assert edits[1].file_offset == ed.offset_of("GuiData", a2)


def test_grow_guidata_touches_only_guidata(mcf_136):
    """GuiData's payload and descriptor change, and the sections after it move in the file; the
    container header does not (it is never declarable)."""
    ed = Editor(mcf_136)
    ed.grow_guidata(bytes(16), "pad")
    assert userop._touched(mcf_136, ed.data) == {"guidata": _guidata(mcf_136).name}


def test_grow_guidata_refuses_empty(mcf_136):
    with pytest.raises(EditorError) as e:
        Editor(mcf_136).grow_guidata(b"", "nothing")
    assert e.value.code == "BAD_EDIT"
