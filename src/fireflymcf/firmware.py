"""Which firmware an image is: its GuiCode name and its LCD firmware, together.

A built image keeps both (a build changes neither the section names nor the LCD section), so
it identifies as the stock it came from. A mixed setup (one release's GuiCode in another's
container) matches no pair and is not identified.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from . import container


@dataclass(frozen=True)
class FirmwareId:
    guicode: str
    lcd_sha256: str


_LCD_1123_147 = "cbed1028d988f67d1ddb8390a15cd66e28e08d93247539772446c57ea7e57adc"

KNOWN: dict[str, FirmwareId] = {
    "11.23": FirmwareId("Lyra 11v23 GuiCode", _LCD_1123_147),
    "13.6": FirmwareId("Lyra 13v6 GuiCode",
                       "a55344f9b224f917b5bf16dec752224f31df9d6beb0ef2b944f2c215edc49b87"),
    "14.7": FirmwareId("Lyra SW20 14v7 GuiCode", _LCD_1123_147),
}


def _read(image: bytes) -> tuple[str, str]:
    """(GuiCode section name, LCD section sha256); ValueError when absent, ambiguous or
    unparseable. Exactly one GuiCode, one GuiData and one LCD Firmware section: with two, the
    name pair cannot say which one the panel runs, so the image is not identified."""
    sections = container.parse_container(image).sections
    guicode = [s for s in sections if s.name.endswith("GuiCode")]
    guidata = [s for s in sections if s.name.endswith("GuiData")]
    lcd = [s for s in sections if s.name == "LCD Firmware"]
    if len(guicode) != 1 or len(guidata) != 1 or len(lcd) != 1:
        raise ValueError(f"{len(guicode)} GuiCode, {len(guidata)} GuiData and {len(lcd)} LCD "
                         f"Firmware sections, not one each")
    lcd = lcd[0]
    return guicode[0].name, hashlib.sha256(image[lcd.offset:lcd.offset + lcd.length]).hexdigest()


def identify(image: bytes) -> str | None:
    """The `KNOWN` key whose (GuiCode name, LCD sha256) pair the image carries, else None."""
    try:
        pair = _read(image)
    except ValueError:
        return None
    for key, fid in KNOWN.items():
        if pair == (fid.guicode, fid.lcd_sha256):
            return key
    return None


def describe(image: bytes) -> str:
    """"<GuiCode name> + LCD <sha[:12]>" for messages; never raises."""
    try:
        name, sha = _read(image)
    except Exception:
        return "unreadable"
    return f"{name} + LCD {sha[:12]}"


def guidata_checksum_label(image: bytes) -> str:
    """The reseal label of the image's own GuiData section."""
    for s in container.parse_container(image).sections:
        if s.name.endswith("GuiData"):
            return f"section-checksum:{s.name}"
    raise ValueError("no GuiData section")


def guicode_checksum_label(image: bytes) -> str:
    """The reseal label of the image's own GuiCode section (13.6 `Lyra 13v6 GuiCode`)."""
    for s in container.parse_container(image).sections:
        if s.name.endswith("GuiCode"):
            return f"section-checksum:{s.name}"
    raise ValueError("no GuiCode section")
