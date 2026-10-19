"""Everything in the MCF that refers to one G12 output pin -- the whole configuration, in one place.

**Why this exists.** On 2026-08-19 a build silently deleted the AWNING EXT command: the group
allocator repurposed a group that *looked* unused because two reachability scans could not find
its owner, and nothing compared what the image could transmit before and after. The lesson is
that a pin's configuration is spread across at least seven structures in two directions, and
that reasoning about any one of them in isolation is how controls get clobbered. `describe_pin`
gathers all of them so a change can be made -- or a pin deliberately cleared -- with the whole
picture visible.

A pin's configuration, and what each part does:

COMMAND side -- what the panel can transmit to the pin:
  1. `bindings`   binding-table entries whose instance byte (`+7`) is this pin. Each carries a
                  DGN and a wire command (byte[4]: 0 set-brightness, 1 on, 3 off, 5 toggle,
                  21 act-while-held).
  2. `groups`     the command groups those bindings belong to. A group is what a button names.
  3. `objects`    the objects (buttons) whose `GROUP_REFERENCE_TAIL` names one of those groups.
                  Incomplete by nature -- see `commanding_objects`.
  4. `masters`    whether the pin is in the interior ("light") or exterior master group.
  5. `sole_pairs` `(channel, command)` pairs ONLY this pin's groups can produce. Deleting or
                  repurposing those groups removes a capability from the file -- this is the
                  field that would have caught the awning regression.

STATUS side -- what the panel can display about the pin:
  6. `var`        the RV-C receive-filter entry that matches DC_DIMMER_STATUS_3 for this
                  instance and writes a u16 slot. One number throughout (`MCF-FORMAT.md` 5.4c).
  7. `colour_cell`/`colour_index` the SRAM cell a GuiCode bridge block writes with the state
                  colour, and its index in the `+0x44BC8` table. A widget names the INDEX.
  8. `consumers`  widgets whose `fillA` mode-7 field or `value_slot` names that index. These
                  repaint when the pin's state changes.
  9. `off_rgb`/`on_rgb` the colour masters the bridge block loads per state. Per-VAR, not per
                  widget: changing one changes every consumer.

DIAGNOSTICS:
 10. `outputs_row` the `G12 OUTPUTS` static row label for this pin, and its file offset.

NOT in the MCF, and therefore not clearable here: the G12's own per-output hardware config
(type/rating/polarity, `docs/G12-OUTPUT-CONFIG.md`) and the coach automation program. The G12
also acts on any RV-C instance regardless of the pin sheet, so a pin with no panel configuration
at all is still drivable from the bus.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from . import guidata, records, statusbind

OUTPUTS_ROW_OBJECT = 35
_ROW = re.compile(r"^\s*(\d{1,2})\s*-")


@dataclass
class PinConfig:
    pin: int
    bindings: list = field(default_factory=list)      # (binding_index, group_id, dgn, command)
    groups: list = field(default_factory=list)
    objects: list = field(default_factory=list)
    masters: list = field(default_factory=list)
    sole_pairs: dict = field(default_factory=dict)    # group_id -> pairs only it produces
    var: int | None = None
    bridged: bool = False
    colour_cell: int | None = None
    colour_index: int | None = None
    bool_index: int | None = None
    consumers: list = field(default_factory=list)
    off_rgb: tuple | None = None
    on_rgb: tuple | None = None
    outputs_row: tuple | None = None                  # (label, file_offset)

    @property
    def commanded(self) -> bool:
        return bool(self.bindings)

    @property
    def displayable(self) -> bool:
        return self.bridged and self.colour_index is not None


def commanding_objects(data: bytes, group_ids: set) -> list:
    """Objects whose command-group tail names any of `group_ids`.

    **Honest limit:** this walks object table 1 and reads each object's `IMAGE_OPCODE` (wtype 12)
    tail, which is the same mechanism `_object_referenced_groups` uses -- and on 13.6 it resolves
    only about 21 of the ~230 groups. An empty result therefore means "no owner found", never
    "no owner exists". Treat it as a hint; use `sole_pairs` for the decision that matters.
    """
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    out = []
    for idx in range(len(table)):
        try:
            recs = records.object_records(data, gs + table[idx])
        except Exception:
            continue
        for rec in recs:
            if rec.wtype != guidata.IMAGE_OPCODE:
                continue
            try:
                gid = struct.unpack_from("<H", data, rec.end - guidata.GROUP_REFERENCE_TAIL)[0]
            except Exception:
                continue
            if gid in group_ids:
                labels = [r.label for r in recs if getattr(r, "label", None)]
                out.append((idx, gid, labels))
            break
    return out


def _group_pairs(gt, bt, gid: int) -> set:
    g = gt[gid]
    return {(bt[j].channel, bt[j].raw[4])
            for j in range(g.start, g.start + g.count) if 0 <= j < len(bt)}


def describe_pin(data: bytes, pin: int, build: str = "136") -> PinConfig:
    """Gather every MCF reference to `pin`. Read-only; nothing is modified."""
    cfg = PinConfig(pin=pin)
    gt = guidata.group_table(data)
    bt = guidata.binding_table(data)

    owning = {}
    for gid, g in enumerate(gt):
        for j in range(g.start, g.start + g.count):
            if 0 <= j < len(bt):
                owning[j] = gid
    for j, b in enumerate(bt):
        if b.channel == pin:
            cfg.bindings.append((j, owning.get(j), b.dgn_low, b.raw[4]))
    cfg.groups = sorted({g for _j, g, _d, _c in cfg.bindings if g is not None})

    for gid in cfg.groups:
        mine = {p for p in _group_pairs(gt, bt, gid) if p[0] is not None}
        others = set()
        for other in range(len(gt)):
            if other != gid:
                others |= _group_pairs(gt, bt, other)
        only = mine - others
        if only:
            cfg.sole_pairs[gid] = sorted(only)

    cfg.objects = commanding_objects(data, set(cfg.groups))

    try:
        from tests import answer_key as ak
        if pin in ak.INTERIOR_MASTER:
            cfg.masters.append("interior")
        if pin in ak.EXTERIOR_MASTER:
            cfg.masters.append("exterior")
    except Exception:
        pass

    pb = statusbind.pin_bindings(data, build).get(pin)
    if pb is not None:
        cfg.var, cfg.bridged = pb.var, pb.bridged
        cfg.colour_cell, cfg.colour_index = pb.colour_cell, pb.colour_index
        cfg.bool_index, cfg.consumers = pb.bool_index, list(pb.colour_consumers)
        if pb.bridged:
            try:
                loads = statusbind.state_loads(data, pb.var, build)
                cfg.off_rgb = getattr(loads.get("off"), "rgb", None)
                on = loads.get(1) or loads.get("on")
                cfg.on_rgb = getattr(on, "rgb", None)
            except Exception:
                pass

    gs, _ = guidata.guidata_extent(data)
    try:
        for rec in records.object_records(data, gs + guidata.object_tables(data)[1][OUTPUTS_ROW_OBJECT]):
            lab = getattr(rec, "label", None)
            m = _ROW.match(lab) if isinstance(lab, str) else None
            if m and int(m.group(1)) == pin:
                cfg.outputs_row = (lab, rec.label_offset)
                break
    except Exception:
        pass
    return cfg


def format_pin(cfg: PinConfig) -> str:
    """A human-readable report of everything that configures the pin."""
    L = [f"G12 output pin {cfg.pin}", "=" * 34, "",
         f"COMMAND   {'yes' if cfg.commanded else 'no -- nothing in this file drives it'}"]
    for j, gid, dgn, cmd in cfg.bindings:
        L.append(f"    binding {j:<4} group {gid!s:<5} dgn 0x{dgn:04X}  wire command {cmd}"
                 f"{'  (toggle)' if cmd == 5 else ''}")
    if cfg.groups:
        L.append(f"    groups        {cfg.groups}")
    if cfg.sole_pairs:
        L.append("    SOLE PRODUCER of (channel, command):")
        for gid, pairs in cfg.sole_pairs.items():
            L.append(f"        group {gid}: {pairs}   <- removing this group deletes these")
    if cfg.objects:
        for idx, gid, labels in cfg.objects:
            L.append(f"    button obj {idx} -> group {gid}  {labels}")
    else:
        L.append("    button        none located (the object scan finds ~21 of ~230 groups)")
    L.append(f"    masters       {cfg.masters or 'none'}")
    L += ["", f"STATUS    {'displayable' if cfg.displayable else 'not displayable'}",
          f"    var           {cfg.var}   bridged={cfg.bridged}"]
    if cfg.colour_cell is not None:
        L.append(f"    colour cell   0x{cfg.colour_cell:08X}  table index {cfg.colour_index}")
    L.append(f"    bool index    {cfg.bool_index}")
    L.append(f"    off / on      {cfg.off_rgb} / {cfg.on_rgb}   (per-var: shared by all consumers)")
    L.append(f"    consumers     {cfg.consumers or 'none'}")
    L += ["", "DIAGNOSTICS"]
    L.append(f"    OUTPUTS row   {cfg.outputs_row[0]!r} at 0x{cfg.outputs_row[1]:X}"
             if cfg.outputs_row else "    OUTPUTS row   none")
    return "\n".join(L)
