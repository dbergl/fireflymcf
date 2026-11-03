"""The checked `Editor`: the only way a user-written op changes image bytes.

Every write carries the bytes expected at the site (a mismatch means the op was written for
another firmware), and every step lands in a ledger. `replay(input, ledger)` re-applies the
ledger to the op's input; equality with the op's output proves no byte changed except through a
recorded, checked step (a section growth shifts offsets, so a run-by-run diff cannot attribute
bytes, but replay can).

Addresses: GuiCode, GuiData and the LCD firmware are mapped at their descriptor's flash
destination (`theme.guicode_base`). G12 has no CPU address the panel uses, so its "address" is
the offset within the section.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Literal

from . import container, growsection, routinekit, theme
from .recipe import RecipeError


class EditorError(RecipeError):
    """Codes: FIRMWARE_MISMATCH, NOT_A_PRIMITIVE, BAD_EDIT."""


@dataclass(frozen=True)
class Step:
    kind: Literal["patch", "grow", "call"]
    args: tuple
    note: str


def _check_arg(v) -> None:
    if isinstance(v, (bytes, str, int, float, bool)) or v is None:
        return
    if isinstance(v, (list, tuple)):
        for x in v:
            _check_arg(x)
        return
    if isinstance(v, dict):
        for k, x in v.items():
            if not isinstance(k, str):
                raise EditorError("BAD_EDIT", f"call argument key {k!r} is not a string")
            _check_arg(x)
        return
    raise EditorError("BAD_EDIT", f"call argument {v!r} is not JSON-able or bytes")


class Editor:
    def __init__(self, data: bytes):
        self._buf = bytes(data)
        self._ledger: list[Step] = []

    @property
    def data(self) -> bytes:
        return bytes(self._buf)

    @property
    def ledger(self) -> tuple[Step, ...]:
        return copy.deepcopy(tuple(self._ledger))

    def section(self, marker: str) -> container.Section:
        found = [s for s in container.parse_container(self._buf).sections if marker in s.name]
        if not found:
            raise EditorError("BAD_EDIT", f"no section matching {marker!r}")
        if len(found) > 1:
            raise EditorError("BAD_EDIT", f"{marker!r} matches several sections: "
                              f"{[s.name for s in found]}")
        return found[0]

    def offset_of(self, marker: str, addr: int) -> int:
        s = self.section(marker)
        rel = addr if marker == "G12" else addr - theme.guicode_base(s)
        if not 0 <= rel < s.length:
            raise EditorError("BAD_EDIT", f"{marker}+{addr:#x} is outside the section "
                              f"({s.length:#x} bytes)")
        return s.offset + rel

    def expect(self, off: int, expected: bytes) -> None:
        self._expect(off, expected, f"@{off:#x}")

    def _expect(self, off: int, expected: bytes, where: str) -> None:
        if off < 0 or off + len(expected) > len(self._buf):
            raise EditorError("BAD_EDIT", f"{where} is outside the image")
        found = self._buf[off:off + len(expected)]
        if found != expected:
            raise EditorError(
                "FIRMWARE_MISMATCH",
                f"this op does not match your firmware: {where} holds {found.hex()}, "
                f"the op expects {expected.hex()}")

    def patch(self, off: int, expected: bytes, new: bytes, note: str,
              _where: str | None = None) -> None:
        expected, new = bytes(expected), bytes(new)
        if len(expected) != len(new):
            raise EditorError("BAD_EDIT", "patch must not change the length "
                              f"({len(expected)} expected, {len(new)} new)")
        self._expect(off, expected, _where or f"@{off:#x}")
        self._buf = self._buf[:off] + new + self._buf[off + len(new):]
        self._ledger.append(Step("patch", (off, expected, new), note))

    def patch_addr(self, marker: str, addr: int, expected: bytes, new: bytes, note: str) -> None:
        off = self.offset_of(marker, addr)
        self.patch(off, expected, new, note, _where=f"{marker}+{addr:#x}")

    def grow_guicode(self, payload: bytes, note: str) -> int:
        payload = bytes(payload)
        try:
            sealed, load_off, _ = growsection.grow_section(self._buf, "GuiCode", payload)
        except growsection.GrowSectionError as e:
            raise EditorError("BAD_EDIT", str(e)) from e
        self._buf = sealed
        self._ledger.append(Step("grow", (payload,), note))
        return theme.GUICODE_BASE_DEFAULT + load_off

    def call(self, name: str, *args, **kwargs):
        fn = routinekit.PRIMITIVES.get(name)
        if fn is None:
            raise EditorError("NOT_A_PRIMITIVE", f"{name!r} is not a routinekit primitive")
        _check_arg(args)
        _check_arg(kwargs)
        # The primitive gets its own copy and the ledger keeps another, so neither an in-place
        # mutation by the primitive nor a later edit by the caller changes what replay re-invokes.
        rec = copy.deepcopy((name, args, kwargs))
        out = fn(self._buf, *copy.deepcopy(args), **copy.deepcopy(kwargs))
        new, result = out if isinstance(out, tuple) else (out, None)
        self._buf = bytes(new)
        self._ledger.append(Step("call", rec, f"call {name}"))
        return result


def replay(data: bytes, ledger: tuple[Step, ...]) -> bytes:
    ed = Editor(data)
    for st in ledger:
        if st.kind == "patch":
            ed.patch(*st.args, st.note)
        elif st.kind == "grow":
            ed.grow_guicode(*st.args, st.note)
        elif st.kind == "call":
            name, args, kwargs = st.args
            ed.call(name, *args, **kwargs)
        else:
            raise EditorError("BAD_EDIT", f"unknown step kind {st.kind!r}")
    return ed.data
