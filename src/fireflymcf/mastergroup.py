"""Remove one G12 channel from a master control (its ON and OFF group pair).

A "master" is a single on-screen control whose two buttons each drive several channels
at once -- "Light Master" (groups 1/2) and "Exterior Master" (groups 216/217) on 13.6.
`guidata.find_control_groups` recovers them by screen title. Dropping a channel from a
master neutralises every binding entry that names it, in BOTH the ON and the OFF group
and across every variant copy, so the master neither turns that load on nor off.

**The neutralisation, and why it is inert.** Each 12-byte binding entry that names the
channel gets two same-size edits:

  * the DGN u16 (bytes 0-1) -> ``0xFFFF``. This is the inert "dead entry" form Firefly
    itself ships (13.6's empty group 5 is a single all-``0xFF`` entry), and the exact
    liveness convention `gestures.py` reads (`raw[:2] != b"\\xff\\xff"`). It also makes
    `guidata.Binding.channel` return None, so the tooling stops reporting the channel
    as a member.
  * the phase mask (byte 3) -> ``0x00``. `docs/CONTROL-BEHAVIOR.md` section 3 proves the
    sender `FUN_0021B8B4` emits an entry only when the gesture phase matches one of byte
    3's four bits; with no bit set, no phase ever sends. This is the hard guarantee that
    no CAN frame goes out for the entry, and it does not depend on the sender skipping a
    ``0xFFFF`` DGN -- belt-and-braces, the way `gestures.make_hold_to_toggle` pairs two
    independent reasons a hold fires only once.

This module owns no reseal: like `gestures.py` it returns an unsealed image and its
caller (`recipe_ops.DropFromMaster`) reseals.
"""
from __future__ import annotations

from fireflymcf import guidata, relocate

DEAD_DGN = 0xFFFF          # the inert DGN of a dead binding entry (group-5 stub form)
PHASE_MASK_FIELD = 3       # byte 3 of a binding entry: the gesture-phase mask


class MasterEditError(Exception):
    """A drop that cannot be applied as asked -- no such master, or the channel is not
    one of its members. Raised rather than silently editing nothing. `code` is a stable
    token (`NO_SUCH_MASTER` / `NOT_A_MEMBER`) the recipe op maps to its own error codes."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def master_controls(data: bytes, title: str) -> list[guidata.ControlGroup]:
    """The control groups (ON and OFF) whose screen title is `title`."""
    return [c for c in guidata.find_control_groups(data)
            if c.screen_title.strip() == title.strip()]


def drop_channel(data: bytes, title: str, channel: int) -> tuple[bytes, list[relocate.Edit]]:
    """Neutralise every binding naming `channel` in master `title`'s ON/OFF groups.

    Returns `(unsealed_image, edits)`. Raises `MasterEditError` if no master is titled
    `title`, or if `channel` is not currently a member of it.
    """
    controls = master_controls(data, title)
    if not controls:
        known = sorted({c.screen_title.strip() for c in guidata.find_control_groups(data)
                        if "master" in c.screen_title.lower()})
        raise MasterEditError(
            "NO_SUCH_MASTER",
            "no master control titled %r; known masters: %s"
            % (title, ", ".join(known) or "none"))
    if not any(channel in c.channels for c in controls):
        members = sorted({ch for c in controls for ch in c.channels})
        raise MasterEditError(
            "NOT_A_MEMBER",
            "channel %d is not a member of %r (members: %s)"
            % (channel, title, members))

    gs, _ = guidata.guidata_extent(data)
    groups = guidata.group_table(data)
    bindings = guidata.binding_table(data)
    out = bytearray(data)
    edits: list[relocate.Edit] = []
    for control in controls:
        g = groups[control.group]
        for j in range(g.start, min(g.start + g.count, len(bindings))):
            b = bindings[j]
            if b.channel != channel:
                continue
            base = gs + b.offset
            old_dgn = bytes(out[base:base + 2])
            out[base:base + 2] = DEAD_DGN.to_bytes(2, "little")
            edits.append(relocate.Edit(
                base, old_dgn, bytes(out[base:base + 2]), "master-drop",
                "group %d binding %d: DGN -> dead (drop ch%d from %s)"
                % (control.group, j, channel, title)))
            pm = base + PHASE_MASK_FIELD
            old_pm = bytes(out[pm:pm + 1])
            out[pm] = 0x00
            edits.append(relocate.Edit(
                pm, old_pm, bytes(out[pm:pm + 1]), "master-drop",
                "group %d binding %d: phase mask -> 0 (no gesture emits)"
                % (control.group, j)))
    return bytes(out), edits
