"""Build driver for cloning an on-screen button into a NEW control that commands its
own RV-C instance.

A byte-cloned object (Water Pump, obj 295) inherits the donor's command-group id (44),
so the clone and Water Pump would share the same binding entries (174/175): a
`set_channel` on either moves both, and `set_channel`'s invariant I6 refuses to touch a
group two controls share. The clone therefore needs its **own** command group.

There are **no spare binding-table entries** on any corpus build -- all 746 are inside
some group's `[start, start+count)` window -- so "repurpose a spare binding" does not
exist. What *does* exist is a supply of command **groups** that no located control
commands and no object references: `unused_command_groups`. `allocate_command_group`
hands the build one of those (route "repurpose", no edits -- Task 3 retargets its
bindings with `set_channel`), and only if that supply were empty would it fall back to
growing the tables (route "grow").

Mechanism note (`guidata.find_control_groups`): an object references a command group
through the trailing `GROUP_REFERENCE_TAIL` u16 of an `IMAGE_OPCODE` (0x0C) record. That
is the *only* object->group reference; a naive "any wtype-12 record" scan mis-attributes
(it wrongly flags the live group 80), so "referenced" here is built exactly the way
`find_control_groups` reads the join.
"""
from __future__ import annotations

import struct

from . import guidata, insert, memo, patch, records, relocate
from .cli import _resolve_page_root


@memo.content_cached(copy=set)
def _object_referenced_groups(data: bytes) -> set[int]:
    """Every group id an object references, via the `IMAGE_OPCODE` record's trailing
    `GROUP_REFERENCE_TAIL` u16 -- the only object->group reference there is. Out-of-range
    tails are ignored (they are image-table indices, not group indices).

    **Corrected 2026-08-19.** This used to walk `guidata.iter_screens`, the older
    boundary-fitted positional screen scan that `records.py` replaced -- so it saw only the
    objects that walk reaches and reported ~21 of the ~230 groups. Objects 108 and 186 both
    name group 65 in stock 13.6, and it missed both, which is how `unused_command_groups`
    came to offer live groups for repurposing. It now walks the object-pointer table and
    decodes each object with `records.object_records`, the decoder validated by the boundary
    oracle on every object in all three builds, and considers EVERY `IMAGE_OPCODE` record in
    an object rather than stopping at the first (object 186 carries three, naming 65/32/31).
    """
    gt = guidata.group_table(data)
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    referenced: set[int] = set()
    for idx in range(len(table)):
        try:
            recs = records.object_records(data, gs + table[idx])
        except Exception:
            continue                      # an object the decoder refuses cannot be a reference
        for rec in recs:
            if rec.wtype != guidata.IMAGE_OPCODE:
                continue
            gid = struct.unpack_from("<H", data, rec.end - guidata.GROUP_REFERENCE_TAIL)[0]
            if gid < len(gt):
                referenced.add(gid)
    return referenced


def group_command_pairs(data: bytes, gid: int) -> set:
    """The `(channel, wire_command)` pairs group `gid` can transmit.

    The wire command is binding byte[4] (byte[3] is metadata -- CAN capture, 2026-08-17).
    """
    gt = guidata.group_table(data)
    bt = guidata.binding_table(data)
    g = gt[gid]
    return {(bt[j].channel, bt[j].raw[4])
            for j in range(g.start, g.start + g.count) if 0 <= j < len(bt)}


@memo.content_cached(copy=None)
def _all_group_command_pairs(data: bytes) -> tuple:
    """`group_command_pairs` for every group, in one pass over the two tables (memoised on
    content -- `sole_produced_pairs` needs every OTHER group's pairs, and used to rebuild all
    233 sets per call, 2,563 table walks per build)."""
    gt = guidata.group_table(data)
    bt = guidata.binding_table(data)
    return tuple(frozenset((bt[j].channel, bt[j].raw[4])
                           for j in range(g.start, g.start + g.count) if 0 <= j < len(bt))
                 for g in gt)


def sole_produced_pairs(data: bytes, gid: int) -> set:
    """`(channel, command)` pairs that ONLY group `gid` produces.

    Repurposing such a group deletes a capability from the file: nothing else can send that
    command to that channel any more. This is the check that matters, and it needs no
    reachability analysis at all -- just the group and binding tables.
    """
    pairs = _all_group_command_pairs(data)
    mine = {p for p in pairs[gid] if p[0] is not None}
    if not mine:
        return set()
    others: set = set()
    for other, ps in enumerate(pairs):
        if other != gid:
            others |= ps
    return mine - others


@memo.content_cached
def unused_command_groups(data: bytes) -> list[int]:
    """Group-table indices that are safe to repurpose, in ascending id order.

    Three independent exclusions, of which the third is the one that actually holds:

    1. referenced by no object record tail (`_object_referenced_groups`);
    2. named by no located control (`guidata.find_control_groups`);
    3. **the sole producer of no `(channel, command)` pair** (`sole_produced_pairs`).

    **Why (3) was added, 2026-08-19.** (1) and (2) are reachability scans and both are badly
    incomplete on this file -- they locate roughly 21 and 17 of the ~230 groups -- so a group
    carrying perfectly live bindings sails through both. On stock 13.6 that made **23 of 33**
    candidates unsafe, including group 3, the sole producer of `(3, 1)` = AWNING EXT, and
    group 4, the sole producer of `(4, 1)` = AWNING RET. `build_home_button` took group 3, so
    every Pin-15 build (v8/v9/v11 and their descendants -- all flashed) silently lost the
    awning-extend command. Criterion (3) is not a reachability guess: it is a direct statement
    about what the file can still transmit, computed from the two tables alone, and it demotes
    exactly those groups.

    Restricted, as before, to Water-Pump-shaped groups: every binding a
    `DGN_DC_DIMMER_COMMAND_2`, count 2-3.
    """
    gt = guidata.group_table(data)
    bt = guidata.binding_table(data)
    referenced = _object_referenced_groups(data)
    control = {c.group for c in guidata.find_control_groups(data)}
    out: list[int] = []
    for gid, g in enumerate(gt):
        if gid in referenced or gid in control:
            continue
        if not (2 <= g.count <= 3):
            continue
        ents = [bt[j] for j in range(g.start, g.start + g.count) if 0 <= j < len(bt)]
        if len(ents) != g.count or not all(
                e.dgn_low == guidata.DGN_DC_DIMMER_COMMAND_2 for e in ents):
            continue
        if sole_produced_pairs(data, gid):
            continue
        out.append(gid)
    return out


# GuiCode's group-variant setter is reached through the function pointer at this SRAM word
# (`GUICODE.md`: 41 of 41 distinct call ids are group-setter calls).
SET_VARIANT_PTR = 0x20039908


@memo.content_cached
def guicode_selected_groups(data: bytes) -> frozenset:
    """Group indices GuiCode passes BY IMMEDIATE to `setVariant(group, v)`. A pointer load
    `ldr rN,[pc,#imm]` of `0x20039908` is followed (within 512 bytes) by calls of the shape
    `ldr r3,[rN]` ... `movs r0,#group` / `blx r3` (up to three halfwords apart), or by
    `ldr r3,[r3]` when N is 3 itself. These groups are driven from outside the screen.

    Why an allocator must avoid them (v45, coach 2026-09-30): 13.6 sets the variant of groups
    130-139 (`0x82`-`0x8B`) as a block (`0x24AC66`-`0x24ACB0`, `0x24AF1A`-`0x24AF64`); no stock
    touch region uses 136-139, and `free_group_slots` listed them. v45's Track + (group 136)
    sent a tap but never repeated on a hold, and the APS-500 reboot (137, hold-only) sent
    nothing, while the identically built 96/126/127 worked. How the block's other input
    cancels a screen hold is NOT established (inference); that GuiCode drives these groups is.
    """
    from . import theme
    gc = theme.guicode_section(data)
    img = data[gc.offset:gc.offset + gc.length]
    base = theme.GUICODE_BASE_DEFAULT

    def hw(pc):
        return img[pc] | (img[pc + 1] << 8)

    ptr_lits = {base + i for i in range(0, len(img) - 3, 4)
                if struct.unpack_from("<I", img, i)[0] == SET_VARIANT_PTR}
    out = set()
    for pc in range(0, len(img) - 1, 2):
        h = hw(pc)
        if h >> 11 != 0b01001 or (((base + pc + 4) & ~3) + (h & 0xFF) * 4) not in ptr_lits:
            continue
        n = (h >> 8) & 7
        deref = 0x6800 | (n << 3) | 3                 # ldr r3,[rN]
        for q in range(pc + 2, min(pc + 512, len(img) - 8), 2):
            if hw(q) != deref:
                continue
            for k in range(1, 4):
                a = q + 2 * k
                if hw(a) >> 8 == 0x20 and hw(a + 2) == 0x4798:   # movs r0,#imm8 ; blx r3
                    out.add(hw(a) & 0xFF)
                    break
    return frozenset(out)


def hold_safe_groups(data: bytes, reserved=frozenset()) -> list[int]:
    """Free group slots fit for a control whose HOLD must work: not in `reserved`, not driven by
    GuiCode (`guicode_selected_groups`), and still carrying a press-cycled header (byte 1 bits
    6-7 == 0). Every hold proven on the coach sits on such a slot (Batt Heat 46; v45's
    wheelbase and track - on 96/126/127); v45's failures (136/137) were externally selected
    (`11 80`) and GuiCode-driven. The byte-1 rule is the conservative half: 61/77-82 were `0x80`
    slots whose TAPS work, and no hold on one has been seen."""
    selected = guicode_selected_groups(data)
    gt = guidata.group_table(data)
    return [g for g in free_group_slots(data)
            if g not in reserved and g not in selected and not gt[g].raw[1] & 0xC0]


@memo.content_cached
def free_group_slots(data: bytes) -> list[int]:
    """Group-table indices safe to repurpose for a **config-shaped** group, ascending.

    The same three exclusions as `unused_command_groups` — referenced by no object
    record tail, named by no located control, and the sole producer of no
    `(channel, command)` pair — **without** its fourth restriction to Water-Pump-shaped
    (`DGN_DC_DIMMER_COMMAND_2`, count 2-3) groups.

    Why a separate function rather than a flag: `unused_command_groups` allocates slots
    for *output command* groups, where the shape restriction is a genuine safety property
    (a group that transmits a channel command is one whose loss is a lost capability).
    A `0x1FED9` config group commands no channel, so the shape restriction only hides
    capacity — but relaxing it inside the existing function would silently widen the gate
    for every existing caller, including `build_home_button`. Callers of this function opt
    in explicitly.

    **Keep all three exclusions.** Criterion 3 is not a reachability guess: `build_home_button`
    once took group 3, the sole producer of `(3, 1)` = AWNING EXT, and every flashed Pin-15
    build silently lost awning-extend.

    **This allocator carries strictly less support than `unused_command_groups`**, and no
    evidence label of its own: the shape restriction it drops is the one that made the narrower
    function's output self-limiting, and nothing here has been flashed. Do not read the shared
    three exclusions as evidentiary parity with the sibling.
    """
    gt = guidata.group_table(data)
    referenced = _object_referenced_groups(data)
    control = {c.group for c in guidata.find_control_groups(data)}
    return [g for g in range(len(gt))
            if g not in referenced
            and g not in control
            and not sole_produced_pairs(data, g)]


def allocate_command_group(data: bytes):
    """Allocate a single-use command group for a cloned button.

    Returns ``(group_id, edits, route)`` where ``route in {"repurpose", "grow"}``:

    * **repurpose** (route taken on every corpus build): the lowest
      `unused_command_groups` id. It is referenced by no object and commanded by no
      located control, and its two/three bindings are Water-Pump-shaped
      (`DGN_DC_DIMMER_COMMAND_2`) -- ready for `set_channel` (Task 3) to point at the new
      instance. No edits are needed here; the clone simply carries this id in its
      `GROUP_REFERENCE_TAIL`. ``edits`` is ``[]``.

    * **grow** (only if no unused group exists -- never on this corpus): append two
      Water-Pump-shaped binding entries cloned from 174/175 and a group-table entry
      pointing at them. See the caveat below.
    """
    unused = unused_command_groups(data)
    if unused:
        return unused[0], [], "repurpose"
    return _grow_command_group(data)


def _grow_command_group(data: bytes):
    """Fallback allocation when no unused group exists.

    **Unreachable on the corpus** (all three builds expose dozens of unused
    Water-Pump-shaped groups; see `unused_command_groups`), and therefore delivered as a
    guarded, documented negative rather than as unverified byte-surgery.

    Growing a group means appending entries to the binding table *and* the group table.
    Both are index-addressed with no self-pointers, so their *entries* relocate cleanly --
    but each is a header-declared table, and `relocate.relocate` cannot grow one: its
    coverage gate (`relocate._deep_coverage_ok`) requires every GuiData byte to be claimed,
    and a declared table is claimed only up to its descriptor `size`. Inserting entries
    without also enlarging that `size` (which `relocate` deliberately does not do -- it
    preserves every table's entry count) leaves the new bytes unclaimed and the gate fails.
    A correct grow needs a descriptor-size-aware relocation on the pattern of
    `insert._append_object` (reuse relocate's `_apply_downstream_shift` helper, edit the two
    table descriptors' `size` fields, reseal, then run the four gates), not a bare
    `relocate.relocate` call. That path is deferred until a build without an unused group
    actually appears.
    """
    from .relocate import RelocationError
    raise RelocationError(
        "no unused command group to repurpose; the grow route (append binding + group "
        "entries) requires a descriptor-size-aware relocation -- relocate.relocate alone "
        "cannot enlarge a declared table (its coverage gate rejects the un-declared "
        "bytes). Unreachable on the current corpus; implement when a build without an "
        "unused group appears.")


# --------------------------------------------------------------------------------------
# clone a boxed button and attach it to the HOME root
# --------------------------------------------------------------------------------------

# The donor for the byte-clone: obj 279 ("Fresh Tank Heater"), record shape `[33, 11, 12]` --
# a visible 84x50 box, a caption, and a touch region (MCF-FORMAT.md Sec.6.0a).
#
# It is chosen for a structural reason: it carries a `value_slot` field on BOTH its box and its
# caption. That field's PRESENCE is gated by the record header, so a same-size edit cannot add
# one -- and without it the clone can never register for in-place redraw. The obvious donor,
# the HOME Water Pump (obj 295), has no value_slot on any of its records: it inherits one from
# its PARENT wtype-5 container record (obj 989/990, value_slot 160 == its fillA index 160), and
# a clone attached by our own generated container does not inherit that. Cloning 295 therefore
# gives a button whose colour is right but which only repaints on re-entering the page (v6,
# on the coach, 2026-08-17).
#
# The donor's own value_slots (53 box, 59 caption) are type-1 CONFIG GATES ("is this tank heater
# fitted?"), not status cells, so `retarget_indicator` rewrites both to the pin's cell.
DONOR_OBJ = 279


def clone_onto_home(data: bytes, position: tuple[int, int] | None = None
                    ) -> tuple[bytes, int, list["insert.Edit"]]:
    """Byte-clone the donor button (object `DONOR_OBJ`) and attach it as a new child of the
    HOME root, optionally at absolute `position=(x, y)`.

    Two calls into `insert.py`, in sequence: `clone_object` returns the donor's exact byte
    span (so the clone decodes identically -- no new correctness proof needed beyond the
    boundary oracle that already covers the donor's shape); `insert_object` appends it as a
    new object at the top object-table index (so no existing `child_index` shifts) and wires
    it in as a child of the HOME page root (`cli._resolve_page_root(data, "home")`). When
    `position` is given, `insert_object` uses a geometry-bearing wtype-5 attach template and
    rewrites its x/y so the framed clone lands at that origin (the clone's own geometry sets
    the box *size*, not its position).

    Returns `(new_data, new_obj_index, edits)`.
    """
    clone = insert.clone_object(data, DONOR_OBJ)
    home = _resolve_page_root(data, "home")
    new_data, edits = insert.insert_object(data, clone, home, position=position)
    new_idx = len(guidata.object_tables(new_data)[1]) - 1
    return new_data, new_idx, edits


# --------------------------------------------------------------------------------------
# retarget the clone's command group to its own RV-C instance
# --------------------------------------------------------------------------------------

# The clone commands RV-C instance == G12 pin 15: unbound (no existing binding commands
# it, per BUS-BASELINE/answer-key), latch, and its indicator colour cell is indexed --
# chosen 2026-08-16 after a dig found the earlier candidate (27) is a motor channel with
# a command conflict.
TARGET_CHANNEL = 15

# The single-press toggle: ONE DC_DIMMER_COMMAND_2 binding carrying command 5 (toggle). The
# TRANSMITTED command is binding byte [4] (byte [3]=0x0a is metadata, not the wire command --
# established by CAN capture 2026-08-17). Every tap sends command 5 and the G12 alternates the
# output's state -- the proven light-button / Bath-Fan-remap pattern (CLAUDE.md remap section:
# "the panel only ever sends command 5; the G12 holds and alternates state").
#
# Why not the Water Pump's own TWO-binding group [toggle(5), off(3)]: that group is a *smart
# toggle* -- the panel picks off-vs-toggle from the commanded instance's tracked on/off state.
# On the coach the clone commanded instance 15, which nothing drives, so the panel had no status
# for it, defaulted to "believed on", and sent OFF (command 3) on every tap (CAN capture, v5,
# 2026-08-17: only ever `0FFF0003FF00FFFF`). A SINGLE-binding group removes the choice: there is
# no off binding to select, so command 5 fires unconditionally -- exactly how the 15 single-
# command-5 light groups on this build toggle their (latching) outputs on/off from the panel.
#
# The bytes are light group 78's binding verbatim (`dbfe010a05c8ff0a...`, channel 10) with the
# instance byte (BINDING_CHANNEL_FIELD = 7) zeroed as a placeholder for TARGET_CHANNEL -- a
# proven-working single-command-5 entry, retargeted, the same discipline as the remap.
_TOGGLE_ONLY = bytes.fromhex("dbfe010a05c8ff00ffff00ff")      # byte[4]=05 command 5 (toggle), level 0xC8


# A binding stores its 8-byte wire payload as TWO LITTLE-ENDIAN u32 WORDS, so the payload the
# panel puts on the wire is `raw[7], raw[6], raw[5], raw[4], raw[11], raw[10], raw[9], raw[8]`.
# `proven-from-bus` 2026-08-22, and PREDICTIVE 2026-08-23: three stock bindings reproduce
# captured `19FEDB9F#` frames byte-for-byte under this rule --
#     binding  52  db fe 01 0a 03 00 ff 08 ff ff 00 ff  ->  08FF0003FF00FFFF
#     binding 119  db fe 01 0a 03 00 ff 1a ff ff 00 ff  ->  1AFF0003FF00FFFF
#     binding 167  db fe 01 0a 03 00 ff 28 ff ff 00 ff  ->  28FF0003FF00FFFF
# -- which is why `BINDING_CHANNEL_FIELD` is byte 7 (payload[0], the RV-C instance) and the
# "wire command" is byte 4 (payload[3]). The order is plain byte order, NOT a per-DGN layout,
# so a binding can carry a DGN the firmware has no special knowledge of.
#
# Confirmed on hardware 2026-08-23 in the stronger direction: the transmit probe used this rule
# to PREDICT a frame that had never been sent, and the coach put exactly it on the wire --
# `19FEA09F#1DFFC805FF00FFFF`, CAN id included. So the runtime also supplies priority 6 for a
# DGN it has no knowledge of, which the binding itself does not encode.
_PAYLOAD_ORDER = (7, 6, 5, 4, 11, 10, 9, 8)


def binding_payload(raw: bytes) -> bytes:
    """The 8 wire payload bytes a 12-byte binding entry transmits."""
    return bytes(raw[i] for i in _PAYLOAD_ORDER)


def binding_raw(dgn: int, wire_payload: bytes, *, gesture_mask: int,
                variant: int = 0, repeat: bool = False) -> bytes:
    """Compose the 12 stored bytes of a binding that transmits `dgn` (an 18-bit RV-C DGN)
    with the eight given WIRE data bytes on the gesture phase(s) named by `gesture_mask`.

    This is the inverse of `binding_payload`: the eight wire bytes are stored as two
    little-endian u32 words (the `_PAYLOAD_ORDER` reversal), so `binding_payload(binding_raw
    (d, p, ...)) == p` and the entry decodes back to `d`. Priority (always 6) and the source
    address (0x9F) are supplied by the firmware at transmit and are NOT part of the entry --
    see `binding_wire_frame` and the `_PAYLOAD_ORDER` comment above.

    `gesture_mask` selects the phase(s) the frame fires on: `1` press, `2` short-tap release,
    `4` while held, `8` release after a hold (`CONTROL-BEHAVIOR.md` §3). `repeat` sets byte 2
    bit 2 so a held frame re-sends on the repeat ticks; left clear a `4` frame fires once when
    the hold threshold is crossed. `variant` (0-15) is matched against the runtime variant
    counter; single-variant groups use 0.
    """
    if not 0 <= dgn <= 0x3FFFF:
        raise ValueError(f"DGN {dgn:#x} is not an 18-bit value")
    if len(wire_payload) != 8:
        raise ValueError(f"wire_payload must be 8 bytes, got {len(wire_payload)}")
    if not 0 <= gesture_mask <= 0xFF:
        raise ValueError(f"gesture_mask {gesture_mask:#x} is not a byte")
    if not 0 <= variant <= 0xF:
        raise ValueError(f"variant {variant} is not a 4-bit value")
    raw = bytearray(12)
    raw[0] = dgn & 0xFF
    raw[1] = (dgn >> 8) & 0xFF
    # byte 2: bits 0-1 = DGN bits 16-17, bit 2 = re-send-on-repeat, bits 4-7 = variant.
    raw[2] = ((dgn >> 16) & 0x03) | (0x04 if repeat else 0x00) | ((variant & 0xF) << 4)
    raw[3] = gesture_mask & 0xFF
    for i, pos in enumerate(_PAYLOAD_ORDER):
        raw[pos] = wire_payload[i]
    return bytes(raw)


def binding_wire_frame(raw: bytes, *, source_address: int = 0x9F,
                       priority: int = 6) -> tuple[int, bytes]:
    """The exact 29-bit CAN id and 8 data bytes a 12-byte binding entry puts on the wire,
    reconstructed the way the firmware assembles them (`FUN_0021B8B4` / `FUN_0021825C`,
    decompiled 2026-09-21): the 18-bit DGN is bit-field-inserted at id bit 8 (so a PDU1 DGN's
    low byte lands in the PS / destination-address position), the priority is bit-field-
    inserted at bit 26 -- hard-wired to 6 in the firmware, not carried by the entry -- and the
    source-address byte is stamped by the firmware (0x9F for this panel).

    `proven-from-code` and corroborated by a hardware oracle: the stock binding
    `db fe 01 02 00 fb ff 16 ff ff 00 ff` reconstructs to `0x19FEDB9F` / `16 ff fb 00 ff 00 ff
    ff`, the exact frame in `captures/candump-2026-08-12_121941.log`. The `source_address` and
    `priority` arguments exist only to make those firmware-forced values explicit and testable;
    the real panel always transmits priority 6 from SA 0x9F.
    """
    if len(raw) != 12:
        raise ValueError(f"a binding entry is 12 bytes, got {len(raw)}")
    dgn = raw[0] | (raw[1] << 8) | ((raw[2] & 0x03) << 16)
    can_id = ((priority & 0x7) << 26) | (dgn << 8) | (source_address & 0xFF)
    return can_id, binding_payload(raw)


def set_binding_dgn(data: bytes, binding_index: int, dgn: int, *,
                    allow_capability_change: bool = False) -> tuple[bytes, list]:
    """Point one binding entry at a different DGN -- a 2-byte same-size edit of `+0..+2`.

    The payload bytes are left exactly as they are: what they *mean* is the new DGN's business,
    and for a DGN nothing on the bus consumes there is nothing to mean. Callers wanting a
    specific payload should write it separately.

    Moving a binding off `DC_DIMMER_COMMAND_2` **removes a channel capability** -- the panel can
    no longer command that pin through this entry -- so it needs `allow_capability_change`, the
    same gate `patch.set_channel` puts on the equivalent move. That is not ceremony: v11 and v18
    both shipped having silently lost a real control this way.
    """
    if not 0 <= dgn <= 0x3FFFF:
        raise ValueError(f"DGN {dgn:#x} is not an 18-bit value")
    entries = guidata.binding_table(data)
    if not 0 <= binding_index < len(entries):
        raise ValueError(f"binding {binding_index} out of range [0, {len(entries)})")
    entry = entries[binding_index]
    old_dgn = struct.unpack_from("<I", entry.raw, 0)[0] & 0x3FFFF
    if old_dgn == dgn:
        raise ValueError(f"binding {binding_index} already carries DGN {dgn:#07x}")
    if entry.channel is not None and not allow_capability_change:
        raise ValueError(
            f"binding {binding_index} commands channel {entry.channel} on "
            f"DC_DIMMER_COMMAND_2; moving it to {dgn:#07x} removes that capability. "
            f"Pass allow_capability_change=True if that is the intent")

    gs, _ = guidata.guidata_extent(data)
    off = gs + entry.offset
    out = bytearray(data)
    out[off] = dgn & 0xFF
    out[off + 1] = (dgn >> 8) & 0xFF
    # byte 2's bit 0 is DGN bit 16; the rest of the byte is the variant selector, kept as-is.
    out[off + 2] = (entry.raw[2] & 0xFE) | ((dgn >> 16) & 1)

    edits = [relocate.Edit(off, entry.raw[:3], bytes(out[off:off + 3]), "binding-dgn",
                           f"binding {binding_index}: DGN {old_dgn:#07x} -> {dgn:#07x} "
                           f"(payload unchanged, wire {binding_payload(entry.raw).hex()})")]
    checksum_positions = {f.label: f.position for f in patch.checksum_fields(bytes(out))}
    resealed, changed_labels = patch.reseal(bytes(out))
    expected = ["section-checksum:Lyra 13v6 GuiData"]
    if changed_labels != expected:
        raise ValueError(f"reseal changed {changed_labels}, expected exactly {expected}")
    for label in changed_labels:
        pos = checksum_positions[label]
        edits.append(relocate.Edit(pos, bytes(out)[pos:pos + 2], resealed[pos:pos + 2],
                                   "checksum", f"resealed {label}"))
    return resealed, edits


def object_group(data: bytes, obj_index: int) -> int:
    """The command-group id object `obj_index` references, read the same way
    `_object_referenced_groups`/`guidata.find_control_groups` do: the trailing
    `GROUP_REFERENCE_TAIL` u16 of its `IMAGE_OPCODE` (wtype 12, opcode 0x0C) record.

    Unlike `find_control_groups`, this does not go through `guidata.iter_screens`'s
    positional scan -- it decodes the object directly from the object-pointer table
    (`records.object_records`), so it sees a standalone object attached only by
    child-pointer (a relocator-inserted clone), which `iter_screens` does not walk.
    Raises `ValueError` if the object carries no `IMAGE_OPCODE` record.
    """
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX]
    if not 0 <= obj_index < len(table):
        raise ValueError(f"object index {obj_index} out of range [0, {len(table)})")
    for rec in records.object_records(data, gs + table[obj_index]):
        if rec.wtype == guidata.IMAGE_OPCODE:
            return struct.unpack_from(
                "<H", data, rec.end - guidata.GROUP_REFERENCE_TAIL)[0]
    raise ValueError(f"object {obj_index} has no IMAGE_OPCODE (wtype 12) record")


def retarget_command(data: bytes, obj_index: int, *, channel: int | None = None,
                     allow_commanded_channel: bool = False) -> tuple[bytes, list]:
    """Give the cloned button (`obj_index`, from `clone_onto_home`) its own command
    group, commanding an RV-C instance (`channel`, default `TARGET_CHANNEL`) with a clean
    toggle.

    **Why not `patch.set_channel`:** that primitive's I6 check requires the group's one
    sharer to be locatable via `guidata.find_control_groups`, which walks screens
    positionally (`guidata.iter_screens`) -- a standalone object attached only by
    child-pointer (the clone) is invisible to it, so I6 would always fail. This function
    performs the same *kind* of edit (repoint a group's bindings' channel byte) but reads
    the clone's group through `object_group` above instead, and re-derives the safety
    checks `set_channel` would have made inline:

    1. `allocate_command_group` hands back the lowest `unused_command_groups` id (route
       "repurpose", no edits of its own) -- referenced by no object and commanded by no
       located control, so retargeting it disturbs nothing already on screen.
    2. The allocated group must have room for the single toggle binding (`count >= 1`) --
       always true for the Water-Pump-shaped (count 2-3) candidates `unused_command_groups`
       returns.
    3. `TARGET_CHANNEL` must be commanded by no existing binding (the I7 check
       `set_channel` would have made) -- this is a deliberate move onto a pin the file has
       never commanded, same as `set_channel(..., allow_capability_change=True)`.
    4. Every binding in the allocated group must be `DGN_DC_DIMMER_COMMAND_2` (the I5
       check) -- `unused_command_groups` already restricts its candidates to exactly this
       shape, so this re-asserts rather than newly discovers it.

    Three same-size edits follow: the clone's `GROUP_REFERENCE_TAIL` is pointed at the
    allocated group, the group's FIRST binding entry is overwritten with the single
    command-5 toggle template (`_TOGGLE_ONLY`) on `TARGET_CHANNEL`, and the group's binding
    count is set to 1 so the panel sees only that one binding (and always sends command 5;
    see `_TOGGLE_ONLY` for why single-binding, not the Water Pump's two-binding smart
    toggle). `patch.reseal` then fixes the one checksum a same-size payload edit always
    invalidates.

    `channel` selects the RV-C instance to command, defaulting to `TARGET_CHANNEL` (15).

    `allow_commanded_channel` opts out of check 3 above. Use it only when the bindings that
    already name the channel belong to controls that cannot be reached on the target coach --
    e.g. pin 29 on 13.6 is named by the slide-out groups 29/30/95 (paired with 30/83/84,
    act-while-held commands 0x21/0x22), which a B-van floorplan never shows. Note that the
    object scan behind "reachable" (`_object_referenced_groups`) locates only ~21 of the ~230
    groups, so its silence is weak evidence: passing this flag is a judgement about the coach,
    not a proof, and it must be recorded by the caller.

    Returns `(new_data, edits)`.
    """
    channel = TARGET_CHANNEL if channel is None else channel
    if not 0 <= channel <= 0xFF:
        raise ValueError(f"channel {channel} does not fit the binding's u8 instance field")
    gid, _alloc_edits, _route = allocate_command_group(data)

    gt = guidata.group_table(data)
    bt = guidata.binding_table(data)
    if not 0 <= gid < len(gt):
        raise ValueError(f"allocated group {gid} is outside the {len(gt)}-entry group table")
    group = gt[gid]
    entries = [bt[j] for j in range(group.start, group.start + group.count)]
    if not entries:
        raise ValueError(f"allocated group {gid} selects no binding entries")

    # A single-press G12-output toggle is a ONE-binding group carrying command 5: we overwrite the
    # group's first binding with `_TOGGLE_ONLY` (command 5) and shorten the group to 1. Every tap
    # then sends command 5 and the G12 alternates the output -- the proven light-button pattern,
    # with no state-dependent off-vs-toggle selection (see `_TOGGLE_ONLY`). Needs a group with at
    # least one binding.
    if group.count < 1:
        raise ValueError(
            f"allocated group {gid} has {group.count} binding(s); the toggle template needs 1")

    # I7-equivalent: the channel must not already be commanded.
    commanding = [b.index for b in bt if b.channel == channel]
    if commanding and not allow_commanded_channel:
        raise ValueError(
            f"channel {channel} is already commanded by binding(s) {commanding}; "
            f"retargeting onto it would drive one pin from two controls. Pass "
            f"allow_commanded_channel=True if those bindings belong to controls this coach "
            f"cannot reach, and say so in the build script")

    # I5-equivalent: every entry in the allocated group is the decoded DGN.
    foreign = {e.dgn_low for e in entries if e.dgn_low != guidata.DGN_DC_DIMMER_COMMAND_2}
    if foreign:
        listed = ", ".join(hex(dgn) for dgn in sorted(foreign))
        raise ValueError(
            f"allocated group {gid} contains non-DC_DIMMER_COMMAND_2 entries ({listed})")

    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX]
    if not 0 <= obj_index < len(table):
        raise ValueError(f"object index {obj_index} out of range [0, {len(table)})")
    group_rec = None
    for rec in records.object_records(data, gs + table[obj_index]):
        if rec.wtype == guidata.IMAGE_OPCODE:
            group_rec = rec
            break
    if group_rec is None:
        raise ValueError(f"object {obj_index} has no IMAGE_OPCODE (wtype 12) record")

    out = bytearray(data)
    edits: list[relocate.Edit] = []

    # Point the clone's group reference at the allocated group.
    tail_off = group_rec.end - guidata.GROUP_REFERENCE_TAIL
    old_tail = data[tail_off:tail_off + 2]
    struct.pack_into("<H", out, tail_off, gid)
    edits.append(relocate.Edit(
        tail_off, old_tail, out[tail_off:tail_off + 2], "group-reference-tail",
        f"object {obj_index} IMAGE_OPCODE group tail {struct.unpack('<H', old_tail)[0]} "
        f"-> {gid}"))

    # Overwrite the group's first binding with the single command-5 toggle template on channel =
    # TARGET_CHANNEL, and shorten the group to that one binding. The G12 alternates the output on
    # each command 5 -- a state-independent toggle (see `_TOGGLE_ONLY`). The earlier two-binding
    # smart toggle sent only OFF on the coach because instance 15 has no tracked status; dropping
    # to a single command-5 binding removes that dependence.
    entry = entries[0]
    payload = _TOGGLE_ONLY[:guidata.BINDING_CHANNEL_FIELD] + bytes([channel]) \
        + _TOGGLE_ONLY[guidata.BINDING_CHANNEL_FIELD + 1:]
    rec_off = gs + entry.offset
    old = data[rec_off:rec_off + guidata.BINDING_ENTRY_SIZE]
    out[rec_off:rec_off + guidata.BINDING_ENTRY_SIZE] = payload
    edits.append(relocate.Edit(
        rec_off, old, payload, "binding-toggle-template",
        f"binding {entry.index} -> toggle (command 5) on channel {channel} (group {gid})"))
    count_off = gs + group.offset + guidata.GROUP_COUNT_FIELD
    if data[count_off] != 1:
        old = data[count_off:count_off + 1]
        out[count_off] = 1
        edits.append(relocate.Edit(
            count_off, old, bytes([1]), "group-count",
            f"group {gid} binding count {old[0]} -> 1 (single command-5 toggle)"))

    checksum_positions = {f.label: f.position for f in patch.checksum_fields(bytes(out))}
    resealed, changed_labels = patch.reseal(bytes(out))
    expected = ["section-checksum:Lyra 13v6 GuiData"]
    if changed_labels != expected:
        raise ValueError(
            f"reseal changed {changed_labels}, expected exactly {expected}")
    for label in changed_labels:
        pos = checksum_positions[label]
        before = bytes(out)[pos:pos + 2]
        after = resealed[pos:pos + 2]
        edits.append(relocate.Edit(pos, before, after, "checksum", f"resealed {label}"))

    edits.sort(key=lambda e: e.file_offset)
    return resealed, edits


# --------------------------------------------------------------------------------------
# retarget the clone's on/off indicator to instance 15's colour cell (Task 4)
# --------------------------------------------------------------------------------------

# Instance 15's indexed colour cell. The clone inherits the donor's own indicator cell
# (278's wtype-33 fillA mode-7 index 155, byte-cloned) -- 145 is the cell already indexed
# for instance 15, chosen 2026-08-16 alongside TARGET_CHANNEL (see that constant's note).
INDICATOR_COLOUR_INDEX = 145

# The value_slot written to BOTH the box (wtype 33) and the caption (wtype 11).
#
# It is deliberately `INDICATOR_COLOUR_INDEX`, not an independent number. The mode-7 colour
# table and the value-slot variable table are the SAME table (GUICODE.md Finding 18), so a
# "colour index N" and a "value_slot N" name one cell -- and every live button in 13.6 has its
# box fillA index, its box value_slot and its caption value_slot all equal to that one cell:
# objs 194(114), 222(136), 255(146), 277(154), 291(159), 814(122/146/136), 871(156),
# 883(146/136/154), 989(455); all type 10. See MCF-FORMAT.md Sec.6.0a.
#
# The six records that break the rule are unattached TEMPLATE objects (247, 278-281, 1038) whose
# value_slot is a type-1/0 config gate ("is this tank heater fitted?"), not a status cell. The
# donor is one of those, so both of its value_slot fields must be retargeted here.
#
# Hardware history (all on the coach): v6 left the box with no value_slot at all -> colour was
# right but only repainted on re-entering the page. v7 set the value_slots to 32 while fillA
# stayed 145 -- two DIFFERENT live cells -- so the box repainted in place but the caption
# vanished until a full page render. v8, with all three equal, redraws box AND caption in place
# (verified 2026-08-17).
# Kept as a name for the default: the status slot and the colour index are one cell
# (Finding 18 -- the colour table IS the value-slot table), which is the invariant itself.
INDICATOR_STATUS_SLOT = INDICATOR_COLOUR_INDEX


def _fillA_mode7_offset(data: bytes, rec: "records.WRecord") -> int:
    """Byte offset (GuiData/file coordinates) of `rec`'s `fillA` colour field's mode-7
    `u16`, found by replaying `records.decode_record`'s exact read order up to that field
    -- header (R1-R8), optional clip geometry (R9-R10, gated on header byte7 bit 0x20),
    optional colour-mode bytes (R11-R12, gated on byte7 bit 0x40) -- `fillA` is the very
    next field read (`records.decode_record`'s first `colour(...)` call).

    Self-verifying: the value at the computed offset must equal what `rec.colors` already
    decoded, so a future change to the header-gate order would be caught here rather than
    silently rewriting the wrong bytes. Raises `ValueError` if `rec` carries no mode-7
    `fillA` field (the only shape this targets -- the wtype-33 indicator record has it).
    """
    fillA = next((c for c in rec.colors if c[0] == "fillA"), None)
    if fillA is None or fillA[1] != 7:
        raise ValueError(
            f"record at {rec.offset} (wtype {rec.wtype}) has no mode-7 fillA colour "
            f"field: {rec.colors}")

    b7 = rec.header[7]
    p = rec.offset + 8                             # R1-R8 header
    if b7 & 0x20:                                  # R9-R10 optional clip geometry
        m = data[p]; p += 1
        for bit in (1, 2, 4, 8):
            if m & bit:
                p += 2
    if b7 & 0x40:                                   # R11-R12 colour-mode bytes
        p += 2

    value = struct.unpack_from("<H", data, p)[0]
    if value != fillA[2]:
        raise ValueError(
            f"computed fillA offset {p} holds {value}, expected {fillA[2]} from the "
            f"already-decoded colors -- header-gate replay is out of sync with "
            f"records.decode_record")
    return p


def retarget_indicator(data: bytes, obj_index: int, *,
                       colour_index: int | None = None) -> tuple[bytes, list]:
    """Rewrite the cloned button's on/off indicator to a pin's colour cell.

    `colour_index` is that cell's `+0x44BC8` table index -- `statusbind.bind_pin` returns the
    right one for a given pin. It defaults to `INDICATOR_COLOUR_INDEX` (145, instance 15).

    The clone's wtype-33 record (the visible box / indicator, byte-cloned from the donor)
    carries `fillA` as a mode-7 colour-table index -- inherited verbatim as 155, the
    donor's own indicator cell. This rewrites that one `u16` to `INDICATOR_COLOUR_INDEX`
    (145), the cell already indexed for instance 15, leaving the donor object's own
    indicator and the clone's other two records (draw, touch) alone. A same-size edit;
    `patch.reseal` fixes the one checksum it always invalidates.

    Independent of `retarget_command` -- it touches a different record (wtype 33, not the
    wtype-12 `IMAGE_OPCODE`/binding table) -- so it can run directly on a
    `clone_onto_home` result, before or after `retarget_command`.

    Returns `(new_data, edits)`.
    """
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[insert.OBJECT_TABLE_INDEX]
    if not 0 <= obj_index < len(table):
        raise ValueError(f"object index {obj_index} out of range [0, {len(table)})")

    colour_index = INDICATOR_COLOUR_INDEX if colour_index is None else colour_index

    recs = records.object_records(data, gs + table[obj_index])
    w33 = next((r for r in recs if r.wtype == 33), None)
    if w33 is None:
        raise ValueError(f"object {obj_index} has no wtype-33 (indicator) record")
    # EVERY wtype-11, not just the first: a button with both an icon record and a caption record
    # has two, and a record left on the donor's config gate does not repaint in place (v7).
    w11s = [r for r in recs if r.wtype == 11]

    off = _fillA_mode7_offset(data, w33)
    old = data[off:off + 2]

    out = bytearray(data)
    struct.pack_into("<H", out, off, colour_index)
    edits: list[relocate.Edit] = [relocate.Edit(
        off, old, out[off:off + 2], "fillA-colour-index",
        f"object {obj_index} wtype-33 fillA colour index "
        f"{struct.unpack('<H', old)[0]} -> {colour_index}")]

    # Point the box AND the caption at the same cell the box paints from. Both inherited the
    # donor's type-1 config gates (53 box, 59 caption); rewriting them all to `colour_index`
    # (== INDICATOR_COLOUR_INDEX) satisfies the live-redraw invariant every working button on
    # this build obeys -- fillA index == box value_slot == caption value_slot (MCF-FORMAT.md
    # Sec.6.0a). Retargeting only the box is what v7 did, and on the coach the box repainted in
    # place while the caption vanished until a full page render. Same-size u16 edits; a record
    # carrying no value_slot is skipped (the caller then keeps colour-cell-only behaviour).
    targets = [(w33, "wtype-33 box")] + [(r, f"wtype-11 text #{i}") for i, r in enumerate(w11s)]
    for rec, what in targets:
        if rec is None:
            continue
        vso = records.value_slot_offset(data, rec)
        if vso is None:
            continue
        old_vs = data[vso:vso + 2]
        struct.pack_into("<H", out, vso, colour_index)
        edits.append(relocate.Edit(
            vso, old_vs, out[vso:vso + 2], "indicator-status-slot",
            f"object {obj_index} {what} value_slot {struct.unpack('<H', old_vs)[0]} -> "
            f"{colour_index} (the pin's colour/status cell)"))

    checksum_positions = {f.label: f.position for f in patch.checksum_fields(bytes(out))}
    resealed, changed_labels = patch.reseal(bytes(out))
    expected = ["section-checksum:Lyra 13v6 GuiData"]
    if changed_labels != expected:
        raise ValueError(
            f"reseal changed {changed_labels}, expected exactly {expected}")
    for label in changed_labels:
        pos = checksum_positions[label]
        before = bytes(out)[pos:pos + 2]
        after = resealed[pos:pos + 2]
        edits.append(relocate.Edit(pos, before, after, "checksum", f"resealed {label}"))

    edits.sort(key=lambda e: e.file_offset)
    return resealed, edits


# --------------------------------------------------------------------------------------
# Task 5: label, place, and compose the end-to-end build
# --------------------------------------------------------------------------------------

# The donor caption ("Black Tank Heater", 17 bytes) and the new one, same size (6 chars +
# 11 trailing spaces, matching the corpus's right-pad-to-fit convention -- MCF-FORMAT.md
# Sec.6). A same-size rename cannot add a character; "Pin 15" fits comfortably.
TARGET_LABEL_RAW = b"Fresh Tank Heater"      # obj 279's caption, 17 bytes
NEW_LABEL_RAW = b"Pin 15" + b" " * 11        # 17 bytes, same field width
assert len(NEW_LABEL_RAW) == len(TARGET_LABEL_RAW)

# The (x, y) origin the positioned attach record places the framed button at (its wtype-5
# geom[0]/geom[1], base-mode 0). The clone is a real box, so its OWN geometry sets the box
# size and the donor's own geom[0]=135 x-offset rides on top: the drawn box left edge lands
# at NEW_X + 135, the box is 84 wide x 50 tall, and the "Pin 15" caption centres in it.
# Tuned 2026-08-17 by rendering `--page home --config build/coach-wd.json` against
# `build/13.6-images/HOME.jpg`: NEW_X=425 -> box left edge 560, so the box sits at
# (560..644, 418..468) in the empty lower-right area below the House/Chassis batteries,
# clear of every existing control.
# Centred in the HOME bottom-right quadrant (panel x[435,790], y[263,468]; centre x=612), below
# the Chassis battery indicator (bottom y~391) with padding from the quadrant edges. Donor 279's
# box is 84 wide and carries its own geom[0]=135 x-offset, which rides on top of the attach
# record's origin, so the drawn left edge is NEW_X + 135: centring an 84-wide box at x=612 wants
# a left edge of 570, hence NEW_X = 435. y=404 sits ~13px under the battery and ~14px above the
# quadrant's bottom edge. Verified on the coach 2026-08-17.
NEW_X = 435
NEW_Y = 404


def last_object_index(data: bytes) -> int:
    """The current top object-table index -- what `clone_onto_home` appends at."""
    return len(guidata.object_tables(data)[1]) - 1


def build_home_button(data: bytes, *, channel: int | None = None,
                      allow_commanded_channel: bool = False,
                      colour_index: int | None = None,
                      label: str | None = None) -> tuple[bytes, list]:
    """The full byte-verified build: clone the donor button onto HOME as its own boxed
    button placed at `(NEW_X, NEW_Y)`, command RV-C instance `TARGET_CHANNEL` (15) with a
    clean toggle, indicate it via colour cell `INDICATOR_COLOUR_INDEX` (145), relabel it
    "Pin 15", and reseal.

    `channel`, `allow_commanded_channel`, `colour_index` and `label` override those three
    defaults; each is forwarded to the composed call that already understood it
    (`retarget_command`, `retarget_indicator`, and the label rewrite respectively). They
    exist because building on pin 15 and *then* retargeting leaves a real defect: every
    `retarget_command` call allocates a FRESH group from `allocate_command_group`, so the
    first group -- still carrying a command-5 binding on the abandoned channel -- stays in
    the table as an orphan. It is unreachable, but it is a live capability the image can
    still express, and `builds/from_stock.py`'s capability gate correctly refuses it. Pass
    the destination channel here and only one group is ever allocated.

    `label` is padded with trailing spaces to the donor field's width and must not exceed
    it; a longer caption is a relocating edit, not this one.

    Composes Tasks 1-4 in sequence (`clone_onto_home` with a positioned attach ->
    `retarget_command` -> `retarget_indicator`), then one more same-size edit pass of its
    own -- the label rewrite -- followed by one final `patch.reseal`. Positioning is done
    by the attach record (not by moving the clone's own geometry): a framed clone's geometry
    is its box *size*, so the box is placed by `insert_object`'s `position=` argument. Each
    composed call already reseals internally (one checksum each, every intermediate stage is
    a valid re-decodable image); the label edit is resealed once more at the end.

    Returns `(new_data, edits)`, edits accumulated across every stage in final-image
    coordinates (structural insert + every same-size field edit + every reseal), sorted by
    offset.
    """
    edits: list = []

    if label is not None:
        raw = label.encode("ascii")
        if len(raw) > len(TARGET_LABEL_RAW):
            raise ValueError(
                f"label {label!r} is {len(raw)} bytes and the donor field is "
                f"{len(TARGET_LABEL_RAW)}; growing it needs a relocating edit")
    # ...and the PADDING is decided further down, once the caption record has been decoded:
    # where the slack goes depends on that record's own h-align byte, and getting it wrong is
    # visible on the panel (see the padding block below).

    out, idx, e = clone_onto_home(data, position=(NEW_X, NEW_Y))
    edits += e
    out, e = retarget_command(out, idx, channel=channel,
                              allow_commanded_channel=allow_commanded_channel)
    edits += e
    out, e = retarget_indicator(out, idx, colour_index=colour_index)
    edits += e

    gs, _ = guidata.guidata_extent(out)
    table = guidata.object_tables(out)[insert.OBJECT_TABLE_INDEX]
    if not 0 <= idx < len(table):
        raise ValueError(f"object index {idx} out of range [0, {len(table)})")
    recs = records.object_records(out, gs + table[idx])

    w11 = next((r for r in recs if r.wtype == 11), None)
    if w11 is None:
        raise ValueError(f"clone {idx} has no wtype-11 (caption) record")
    if w11.label_offset is None:
        raise ValueError(f"clone {idx} wtype-11 record carries no label")
    label_off = w11.label_offset
    current = out[label_off:label_off + len(TARGET_LABEL_RAW)]
    if current != TARGET_LABEL_RAW:
        raise ValueError(
            f"clone {idx} wtype-11 label bytes are {current!r}, expected "
            f"{TARGET_LABEL_RAW!r} (the donor's own caption) before relabelling")

    # Pad the caption to the donor field, the way THIS record is drawn.
    #
    # A left-aligned label draws from its box's left edge, so trailing spaces are invisible and
    # right-padding preserves the indent (`patch.repad`'s hardware-settled default). A CENTRED
    # one is different: the panel centres each line by the width of the string it was given, pad
    # included, so slack parked at the end of the last line drags the visible text left by half
    # of it. That is what shipped in reference build v1/v2 -- 'Overflow\nValve   ' drew its second line 9 px
    # left of centre, next to a 'Water\nPump' that needed no padding and looked right (owner,
    # 2026-08-21). `align="center"` splits the slack around that line instead.
    if label is None:
        new_label_raw = NEW_LABEL_RAW
    else:
        donor_field = TARGET_LABEL_RAW.decode("ascii")
        new_label_raw = patch.repad(
            donor_field, donor_field, label, len(TARGET_LABEL_RAW),
            "center" if w11.h_align == 2 else "right").encode("ascii")

    working = bytearray(out)
    working[label_off:label_off + len(new_label_raw)] = new_label_raw
    edits.append(relocate.Edit(
        label_off, bytes(current), bytes(new_label_raw), "label",
        f"object {idx} wtype-11 label {current!r} -> {new_label_raw!r}"))

    checksum_positions = {f.label: f.position for f in patch.checksum_fields(bytes(working))}
    resealed, changed_labels = patch.reseal(bytes(working))
    expected = ["section-checksum:Lyra 13v6 GuiData"]
    if changed_labels != expected:
        raise ValueError(
            f"reseal changed {changed_labels}, expected exactly {expected}")
    for label in changed_labels:
        pos = checksum_positions[label]
        before = bytes(working)[pos:pos + 2]
        after = resealed[pos:pos + 2]
        edits.append(relocate.Edit(pos, before, after, "checksum", f"resealed {label}"))

    edits.sort(key=lambda e: e.file_offset)
    return resealed, edits
