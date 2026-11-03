"""How large GuiData may grow: the end of the flash it is written to.

GuiData is mapped at `0x90000000`, the panel's QSPI flash, and is the only section there. The QSPI
is 4 MiB: the LCD firmware programs the QUADSPI device-configuration register's FSIZE field with
`0x15` (`2^(0x15 + 1)` bytes) at LCD `0x00216E0E` (*proven-from-code*). The top erase block of
that flash holds the configurator's files: the address formula is proven, the block size is
*inferred* (64 KB). So GuiData must end at or below `0x903F0000`: 4,128,768 bytes from its base.

`check_guidata_size` is the gate. `relocate.relocate` and `relocate.replace_object_records` run
it before they change anything, and the compile's final gates run it on the finished image, so
every grow and every build meets it. It raises `GuiDataTooLarge`, a `recipe.GateError` (so a
RecipeError) with the stable code `GUIDATA_TOO_LARGE`.
"""
from __future__ import annotations

from .recipe import GateError

GUIDATA_BASE = 0x90000000
QSPI_SIZE = 4 * 1024 * 1024        # FSIZE 0x15 at LCD 0x00216E0E (proven-from-code)
RESERVED_TOP = 64 * 1024           # the configurator's erase block (size inferred)
GUIDATA_END_MAX = GUIDATA_BASE + QSPI_SIZE - RESERVED_TOP          # 0x903F0000
CODE = "GUIDATA_TOO_LARGE"


class GuiDataTooLarge(GateError):
    """GuiData would end past `GUIDATA_END_MAX`. Code `GUIDATA_TOO_LARGE`."""

    def __init__(self, message: str, **kw):
        super().__init__(CODE, message, **kw)


def check_guidata_size(length: int, end_max: int | None = None) -> None:
    """Refuse a GuiData section of `length` bytes (checksum included) that would end past the
    ceiling. `end_max` defaults to `GUIDATA_END_MAX`, read at call time."""
    end_max = GUIDATA_END_MAX if end_max is None else end_max
    limit = end_max - GUIDATA_BASE
    if length > limit:
        raise GuiDataTooLarge(
            f"GuiData would be {length:,} bytes, ending at {GUIDATA_BASE + length:#x}; it must "
            f"end by {end_max:#x} ({limit:,} bytes). The QSPI flash it is written to is 4 MiB "
            f"(proven-from-code: FSIZE 0x15 at LCD 0x00216E0E), and its top erase block, "
            f"inferred to be 64 KB, holds the configurator's files.")
