"""The reusable panel "register": an SRAM status slot the receive filter (FUN_0021C760)
writes from a matched bus value. Creating/using one is data-only. This module owns the
mechanism so it is no longer private to the follower.

Slot addressing is INDEX-SPLIT (proven-from-code, FUN_0021C760 0x0021C9B0-0x0021C9C4; the
PC.44 lesson): var index <= 0xFF lands at STATUS_SLOT_BASE + idx*2, index > 0xFF at
STATUS_SLOT_BASE_HIGH + idx*2 -- two separate slot arrays 0x38 bytes apart.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import guidata

STATUS_SLOT_BASE = 0x20007FC8          # low array (var index <= 0xFF)
STATUS_SLOT_BASE_HIGH = 0x20008000     # high array (var index > 0xFF)
STATUS_SLOT_INDEX_SPLIT = 0xFF         # the `cmp r5,#0xff` boundary: index <= 0xFF is low

DGN_NAMES = {
    "DC_DIMMER_STATUS_3": guidata.DGN_DC_DIMMER_STATUS_3,
    "DC_DIMMER_COMMAND_2": guidata.DGN_DC_DIMMER_COMMAND_2,
    "DGN_CONFIG": guidata.DGN_CONFIG,
}


class RegisterError(Exception):
    """A register could not be resolved unambiguously; refuse rather than guess/corrupt."""


@dataclass(frozen=True)
class Register:
    var_index: int
    slot_addr: int
    dgn_low: int
    value: int          # instance for a per-channel DGN, selector for DGN_CONFIG
    extract_mode: int   # raw[2] >> 4 (0 = inert)
    source_field: int   # raw[3] & 7
    raw: bytes


def status_slot_addr(var_index: int) -> int:
    """SRAM address the receive filter writes for variable-table entry `var_index`,
    applying the index-split base (FUN_0021C760)."""
    if var_index < 0:
        raise RegisterError(f"var index out of range: {var_index}")
    base = STATUS_SLOT_BASE if var_index <= STATUS_SLOT_INDEX_SPLIT else STATUS_SLOT_BASE_HIGH
    return base + var_index * 2


def _dgn_low(dgn) -> int:
    if isinstance(dgn, str):
        if dgn not in DGN_NAMES:
            raise RegisterError(f"unknown DGN name {dgn!r}; known: {sorted(DGN_NAMES)}")
        return DGN_NAMES[dgn]
    return dgn & 0xFFFF        # accept an 18-bit DGN or a low-16; the tables store low-16


def resolve_register(data: bytes, *, dgn, instance: int | None = None,
                     selector: int | None = None, var_index: int | None = None) -> Register:
    """Find the ONE existing variable-table entry for `dgn` (+ instance/selector filter) and
    return it as a Register with its index-split slot address. Reuse-existing only: a
    subscription that does not already exist is refused (appending a new entry is a future,
    hardware-unverified capability). Refuses on 0 or >1 matches -- the same discipline the
    follower's _gate_slot/_gate_register use.

    `var_index` names one entry outright, for DGNs whose entries cannot be told apart by their
    filter byte: `0x1FECC`'s two entries (206 `Absorb`, 207 `Float`, `BUS-BASELINE.md` PC.55)
    both carry the open `0xFF` filter and differ only in the payload field they extract. The
    `dgn` is still required and still checked, so a wrong index is refused, not silently
    accepted."""
    if instance is not None and selector is not None:
        raise RegisterError("give at most one of instance / selector, not both")
    dgn_low = _dgn_low(dgn)
    want = instance if instance is not None else selector
    matches = [v for v in guidata.variable_table(data)
               if v.dgn_low == dgn_low and (want is None or v.value == want)
               and (var_index is None or v.index == var_index)]
    if not matches:
        raise RegisterError(
            f"no existing variable-table subscription for dgn={dgn_low:#06x} value={want!r}; "
            "appending a new entry is not yet supported (hardware-unverified append-new path)")
    if len(matches) > 1:
        raise RegisterError(
            f"dgn={dgn_low:#06x} value={want!r} matches {len(matches)} variable-table entries, "
            "not exactly one; add an instance/selector filter to disambiguate")
    v = matches[0]
    return Register(var_index=v.index, slot_addr=status_slot_addr(v.index), dgn_low=v.dgn_low,
                    value=v.value, extract_mode=v.raw[2] >> 4, source_field=v.raw[3] & 7,
                    raw=v.raw)
