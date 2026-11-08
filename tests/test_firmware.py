"""Firmware identity: an image is known by its GuiCode name and LCD firmware together."""
from __future__ import annotations

from tests.firmware_helpers import _rename_guicode, _rename_section

from fireflymcf import firmware, patch

NEEDS_RESEARCH_DATA = "reads the corpus images through mcf_1123/mcf_136/mcf_147"


def test_identifies_each_corpus_image(mcf_1123, mcf_136, mcf_147):
    assert [firmware.identify(x) for x in (mcf_1123, mcf_136, mcf_147)] == ["11.23", "13.6", "14.7"]


def test_guidata_label_is_the_images_own(mcf_136, mcf_147):
    assert firmware.guidata_checksum_label(mcf_136) == "section-checksum:Lyra 13v6 GuiData"
    assert firmware.guidata_checksum_label(mcf_147) == "section-checksum:Lyra SW20 14v7 GuiData"


def test_a_built_image_keeps_its_identity(mcf_136):
    built = patch.set_build_name(patch.reseal(mcf_136)[0], "x")[0]
    assert firmware.identify(built) == "13.6"


def test_mixed_pair_is_not_identified(mcf_136):
    assert firmware.identify(_rename_guicode(mcf_136, b"Lyra SW20 14v7 GuiCode")) is None


def test_a_second_guicode_or_guidata_section_is_not_identified(mcf_136):
    """Which of two GuiCode (or GuiData) sections runs is not something the name pair says, so
    an image carrying two is unidentified rather than named by the first."""
    from fireflymcf import container
    g12 = next(s for s in container.parse_container(mcf_136).sections if s.name == "G12")
    for extra in (b"Lyra 13v6 GuiCode", b"Other GuiCode", b"Lyra 13v6 GuiData"):
        image = _rename_section(mcf_136, g12.index, extra)
        assert firmware.identify(image) is None, extra
        assert "unreadable" in firmware.describe(image)


def test_unknown_and_garbage_are_none(mcf_147):
    assert firmware.identify(_rename_guicode(mcf_147, b"Lyra SW20 14v8 GuiCode")) is None
    assert firmware.identify(b"\x00" * 64) is None
    assert "unreadable" in firmware.describe(b"\x00" * 64)


def test_describe_names_guicode_and_lcd(mcf_136):
    assert firmware.describe(mcf_136) == (
        "Lyra 13v6 GuiCode + LCD " + firmware.KNOWN["13.6"].lcd_sha256[:12])


def _a_guicode_byte(image: bytes) -> int:
    from fireflymcf import container
    c = container.parse_container(image)
    s = next(s for s in c.sections if s.name.endswith("GuiCode"))
    return s.offset + 8


def test_statusbind_reseals_14_7(mcf_147):
    from fireflymcf import statusbind
    _data, edits = statusbind.pin_filter_instance(mcf_147, 115, 49)
    assert any(e.structure == "checksum" for e in edits)


def test_newbutton_reseals_14_7(mcf_147):
    from fireflymcf import guidata, newbutton
    groups = guidata.group_table(mcf_147)
    table = guidata.binding_table(mcf_147)
    g = next(i for i, gr in enumerate(groups) if gr.count)
    first = guidata.group_bindings(groups, table, g)[0]
    dgn = first.raw[0] | (first.raw[1] << 8) | ((first.raw[2] & 0x03) << 16)
    cur = first.raw[newbutton._PAYLOAD_ORDER[0]]
    _data, edits = newbutton.set_binding_bytes(
        mcf_147, g, dgn=dgn, bindings=[0], changes={0: (cur, cur ^ 1)})
    assert any(e.structure == "checksum" for e in edits)


def test_reseal_check_still_refuses_a_foreign_section(mcf_136):
    import pytest

    from fireflymcf import statusbind
    out = bytearray(mcf_136)
    out[_a_guicode_byte(mcf_136)] ^= 1
    with pytest.raises(statusbind.StatusBindError):
        statusbind._reseal_with(bytes(out), [], [firmware.guidata_checksum_label(mcf_136)])
