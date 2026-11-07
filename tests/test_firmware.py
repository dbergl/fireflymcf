"""Firmware identity: an image is known by its GuiCode name and LCD firmware together."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from firmware_helpers import _rename_guicode  # noqa: E402

from fireflymcf import firmware, patch  # noqa: E402

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


def test_unknown_and_garbage_are_none(mcf_147):
    assert firmware.identify(_rename_guicode(mcf_147, b"Lyra SW20 14v8 GuiCode")) is None
    assert firmware.identify(b"\x00" * 64) is None
    assert "unreadable" in firmware.describe(b"\x00" * 64)


def test_describe_names_guicode_and_lcd(mcf_136):
    assert firmware.describe(mcf_136) == (
        "Lyra 13v6 GuiCode + LCD " + firmware.KNOWN["13.6"].lcd_sha256[:12])
