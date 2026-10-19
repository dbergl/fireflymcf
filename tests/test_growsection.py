# tests/test_growsection.py
import struct
from fireflymcf import growsection, container, patch

DATA = open("firefly-13.6/mcf.bin", "rb").read()

def test_grow_guicode_appends_before_checksum_and_shifts_downstream():
    before = container.parse_container(DATA)
    gc = next(s for s in before.sections if "GuiCode" in s.name)
    gd = next(s for s in before.sections if "GuiData" in s.name)
    g12 = next(s for s in before.sections if s.name == "G12")

    payload = bytes(range(16))  # 16 bytes, 4-aligned
    out, load_off, edits = growsection.grow_section(DATA, "GuiCode", payload)

    after = container.parse_container(out)
    gc2 = next(s for s in after.sections if "GuiCode" in s.name)
    gd2 = next(s for s in after.sections if "GuiData" in s.name)
    g122 = next(s for s in after.sections if s.name == "G12")

    # GuiCode grew by exactly len(payload); downstream offsets each shifted by the same.
    assert gc2.length == gc.length + len(payload)
    assert gd2.offset == gd.offset + len(payload)
    assert g122.offset == g12.offset + len(payload)

    # The payload lands immediately before the (recomputed) GuiCode checksum word.
    # load_off is section-relative; content (minus 4-byte checksum) previously ended there.
    assert load_off == gc.length - 4
    body_at = gc.offset + gc.length - 4
    assert out[body_at:body_at + len(payload)] == payload

    # Every stored checksum verifies: resealing an already-sealed image changes nothing.
    _resealed, changed = patch.reseal(out)
    assert changed == [], changed
    # File grew by exactly len(payload).
    assert len(out) == len(DATA) + len(payload)

def test_grow_section_rejects_unaligned_payload():
    import pytest
    with pytest.raises(growsection.GrowSectionError):
        growsection.grow_section(DATA, "GuiCode", b"\x00\x00\x00")  # 3 bytes
