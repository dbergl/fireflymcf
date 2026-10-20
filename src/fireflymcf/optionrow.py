"""Build the tap-emission group + bindings for a config-toggle OPTIONS row.

`build_config_toggle(data, selector)` clones the firmware's `0x2F` config-toggle group
(group 129, the "Low Volts" toggle) and retargets its four bindings' selector byte to an
arbitrary CAN config selector, growing the binding table by those four entries and pointing
an unused group slot (164) at them. The on-screen widget that emits the group's touch id is
a **later** task; this module builds only the group+bindings and returns the byte edits.

## Why the growth is a same-size rewrite, not an append (the padding-arena mechanism)

Growing the binding table means the four new entries must sit at binding indices 746..749,
which is physically the byte range right after the existing 746 entries -- i.e. at the
binding table's own end (GuiData+0x2A70), which is also where the variable table begins.
The obvious move (`newbutton._grow_command_group`'s deferred recipe: insert 48 bytes there,
bump the binding descriptor `size`, and shift every downstream reference via
`relocate._apply_downstream_shift`) **fails `relocate`'s coverage gate**, and the reason is a
structural invariant that `relocate` itself was never built to cross:

`coverage._claim_guidata` reads the image sub-container's magic at a **fixed** GuiData offset
(`coverage.IMAGE_CONTAINER_OFFSET` == 0x10000) and the entire object region lives at or after
it. A uniform downstream shift slides that container off 0x10000 and coverage raises
"expected the image sub-container magic ... at section offset 0x10000, found 0x0" (verified
empirically, 2026-08-31). `relocate.relocate` has only ever inserted in the object region
(>= 0x10000), so it never had to keep the container pinned; it cannot express an insertion
*below* it.

What makes the growth possible without moving the container is the ~52 KB run of zero
**padding** between the variable/aux tables (ending ~0x33CC on 13.6) and the image container
at 0x10000 -- a growth *arena*. This module inserts the 48 new binding bytes at 0x2A70 and
absorbs 48 bytes from the **tail of that padding**, so:

  * the region `[0x2A70, 0x10000)` is rewritten in place -- 4 new bindings, then the variable
    and aux tables shifted +48, then 48 fewer padding bytes -- a **same-size** rewrite;
  * everything at or after 0x10000 (the image container, all object-pointer tables, every
    object, the image table) is **byte-identical and unmoved**;
  * therefore no object-pointer table entry, no GuiCode literal, and no container section
    field needs adjusting -- they all address >= 0x10000. The only fixups are three header
    descriptors (binding `size` += 48; variable and each in-window table `offset` += 48) and
    the one GuiData section checksum.

Because nothing moves outside the window, the whole operation is a set of **same-size byte
overwrites** -- closer to a `patch` than a relocation -- and it touches **no shared relocator
code**: the risky arithmetic in `relocate.py` is not modified, only *reused* as validation.
Every one of `relocate`'s self-check gates is run on the finished image before this module
returns; if any fails, it raises `OptionRowError` rather than hand back a build it has not
itself re-verified (the project's standing discipline).

## Refusals (a corrupt build must never be produced silently)

`build_config_toggle` refuses -- `OptionRowError` -- unless every precondition the padding-
arena mechanism relies on is checked and holds on *this* image, not assumed from 13.6:

  * `selector` is a byte and is not already emitted by an existing config-toggle binding;
  * the donor group (129) has exactly the four expected `0xFED9` config-toggle bindings;
  * the reuse slot (group 164) is one of `newbutton.unused_command_groups` (safe to repoint);
  * the binding table ends below the image container, with enough zero, unreferenced padding
    to absorb the growth, and no declared table straddles the insertion or trim points;
  * no object-pointer table entry and no GuiCode pointer targets the rewritten window (which
    would be stranded, since this mechanism deliberately does not move object-region refs);
  * and, finally, the finished image passes every `relocate` gate.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import coverage, guidata, insert, newbutton, patch, records, relocate, theme
from .relocate import Edit

# The config-toggle DGN. Group 129's four bindings are `d9 fe 01 0a 0f 2f 96 ...`: bytes[0:2]
# = 0xFED9 and byte[2] bit0 set -> the 0x1FED9 config-write/echo DGN the G12 stores and replies
# to (hardware-verified). The SELECTOR the toggle writes/reads is binding byte[5] (0x2F for
# group 129); byte[4] = 0x0F is the command. Two variant copies (byte[2] 0x01/0x11) each carry
# a D1EA write and a D3 readback (the readbacks give the ~1 ms no-lag G12 reply, PC.43).
CONFIG_TOGGLE_DGN_LOW = 0xFED9
SELECTOR_FIELD = 5

# The donor: group 129, raw `11 80 0c 80 bf 01 04 00` -> start 447, count 4. Its four
# bindings are cloned verbatim but for byte[5].
DONOR_GROUP = 129
DONOR_COUNT = 4

# The selector byte[5] group 129 must carry to BE the 0x2F "Low Volts" config toggle we clone.
# Pinned (not just the generic 0xFED9x4 shape) so that on any build whose index-129 group is a
# *different* config toggle, `build_config_toggle` refuses rather than silently cloning the
# wrong donor -- honouring the project's 13.6-pinned-constants-must-refuse-elsewhere rule.
DONOR_SELECTOR = 0x2F

# The unused group slot repointed at the new bindings (avoids growing the group table). Its
# membership in `newbutton.unused_command_groups` is re-checked at build time, not assumed.
REUSE_GROUP_SLOT = 164

# A group entry is `<u16><u16><u16 start><u8 count><u8 pad>`; the four bytes before `start`
# are group 129's `11 80 0c 80` (byte2 = 0x0c is the config-toggle variant), copied verbatim.
GROUP_ENTRY_PREFIX = bytes([0x11, 0x80, 0x0C, 0x80])

# The image sub-container is pinned here (GuiData-relative); the growth must keep it in place.
IMAGE_CONTAINER_OFFSET = coverage.IMAGE_CONTAINER_OFFSET


class OptionRowError(relocate.RelocationError):
    """`build_config_toggle` cannot proceed, or produced a build that fails a gate.

    A subclass of `RelocationError` so callers already catching relocation failures catch
    these too; raised rather than returning a build that only looks right -- the same
    discipline as `insert.InsertError`/`patch.PatchError`.
    """


@dataclass(frozen=True)
class ConfigToggle:
    """The result of `build_config_toggle`.

    * `new_group_index` -- the group-table index that now selects the four new bindings
      (the reused slot 164).
    * `touch_id` -- the on-screen touch id a later widget must emit to fire this group;
      equal to `new_group_index` per the touch-id -> group map.
    * `bindings` -- the four retargeted 12-byte binding entries, in order.
    * `edits` -- every byte range this growth changes, as same-size `relocate.Edit`s in
      final-image coordinates (a provenance trail for the L3 ladder); NOT applied by the
      caller-facing contract beyond what `grown` already reflects.
    * `grown` -- the finished, resealed, gate-verified image (same length as the input).
    """
    new_group_index: int
    touch_id: int
    bindings: list[bytes]
    edits: list[Edit]
    grown: bytes


def _donor_bindings(data: bytes) -> list[bytes]:
    """The four raw binding entries group `DONOR_GROUP` selects, checked to be the expected
    config-toggle shape (`0xFED9`, count 4). Raises `OptionRowError` otherwise."""
    gt = guidata.group_table(data)
    if not 0 <= DONOR_GROUP < len(gt):
        raise OptionRowError(f"donor group {DONOR_GROUP} is outside the group table")
    donor = gt[DONOR_GROUP]
    if donor.count != DONOR_COUNT:
        raise OptionRowError(
            f"donor group {DONOR_GROUP} has count {donor.count}, expected {DONOR_COUNT}")
    bt = guidata.binding_table(data)
    ents = [bt[j] for j in range(donor.start, donor.start + donor.count) if j < len(bt)]
    if len(ents) != DONOR_COUNT:
        raise OptionRowError(
            f"donor group {DONOR_GROUP} selects {len(ents)} in-range bindings, "
            f"expected {DONOR_COUNT}")
    if any(e.dgn_low != CONFIG_TOGGLE_DGN_LOW for e in ents):
        raise OptionRowError(
            f"donor group {DONOR_GROUP} is not all config-toggle ({CONFIG_TOGGLE_DGN_LOW:#06x}) "
            f"bindings: {[hex(e.dgn_low) for e in ents]}")
    # Pin the DONOR IDENTITY, not just its shape: every one of group 129's four bindings must
    # write selector 0x2F (the Low Volts toggle). On a build whose index-129 group is a
    # *different* 0xFED9x4 toggle, this refuses instead of cloning the wrong donor.
    sels = {e.raw[SELECTOR_FIELD] for e in ents}
    if sels != {DONOR_SELECTOR}:
        raise OptionRowError(
            f"donor group {DONOR_GROUP} carries selector(s) "
            f"{sorted(hex(s) for s in sels)}, not the expected 0x2F 'Low Volts' toggle; "
            f"this build's index-{DONOR_GROUP} group is not the donor this module clones")
    return [e.raw for e in ents]


def find_config_toggle(data: bytes, selector: int) -> int | None:
    """The group index of an EXISTING config-toggle group on `selector`, or `None`.

    A group qualifies when it has at least one binding and EVERY one of its bindings has
    `dgn_low == CONFIG_TOGGLE_DGN_LOW` and `raw[SELECTOR_FIELD] == selector` -- the exact
    shape `build_config_toggle` creates and `toggle_to_flag_delta` requires. `build_config_toggle`
    uses this (a group already qualifying is what "selector already has a config-toggle group"
    means) and a caller that wants to REUSE rather than mint a second toggle (e.g. a later op
    composing onto an image an earlier op already toggled) can call it directly to adopt the
    existing group instead of refusing.

    Raises `OptionRowError` if MORE THAN ONE group qualifies: with two candidate toggles on the
    same selector there is no principled way to pick one, so this refuses rather than guess.
    """
    gt = guidata.group_table(data)
    bt = guidata.binding_table(data)
    found = []
    for gi, g in enumerate(gt):
        if g.count == 0:
            continue
        ents = [bt[j] for j in range(g.start, g.start + g.count) if j < len(bt)]
        if len(ents) != g.count:
            continue
        if all(e.dgn_low == CONFIG_TOGGLE_DGN_LOW and e.raw[SELECTOR_FIELD] == selector
              for e in ents):
            found.append(gi)
    if len(found) > 1:
        raise OptionRowError(
            f"selector {selector:#04x} has {len(found)} config-toggle groups {found}; "
            f"ambiguous -- refusing to guess which one is the toggle to reuse")
    return found[0] if found else None


def _window_references(data: bytes, gs: int, lo: int, hi: int) -> list[str]:
    """Every object-pointer table entry or GuiCode pointer whose GuiData-relative target is
    in `[lo, hi)` -- the window this growth rewrites in place. Any such reference would be
    stranded (its target moves but the pointer does not), so the mechanism refuses if the
    list is non-empty. Returns human-readable descriptions.
    """
    stranded: list[str] = []
    for ti, table in enumerate(guidata.object_tables(data)):
        for ei, target in enumerate(table):
            if lo <= target < hi:
                stranded.append(f"object-pointer table {ti} entry {ei} -> {target:#x}")
    for pos, rel in theme.guicode_guidata_pointers(data):
        if lo <= rel < hi:
            stranded.append(f"GuiCode literal @ {pos:#x} -> GuiData+{rel:#x}")
    return stranded


def _grow_bindings(data: bytes, payload: bytes,
                   group_writes: list[tuple[int, bytes]]) -> tuple[bytes, list[Edit]]:
    """Grow the binding table by `payload` (a whole number of 12-byte entries), absorbing the
    growth from the padding arena between the variable/aux tables and the pinned image
    sub-container (module docstring), then write each `(group_slot, entry)` in `group_writes`
    into the group table -- `entry` a raw `guidata.GROUP_ENTRY_SIZE`-byte group-table entry,
    verbatim.

    This is `build_config_toggle`'s original growth body, lifted so `build_config_stepper` can
    reuse it for a six-entry payload split across TWO new groups instead of `build_config_toggle`'s
    one; every precondition check, the window rewrite, the descriptor fixups, the reseal, and the
    self-check gates below are unchanged from that original body.

    `group_writes` takes whole entries rather than `(group_slot, start, count)` on purpose: a
    group-table entry's leading 4 bytes are NOT a generic constant that this helper could
    synthesise on the caller's behalf -- the 233-entry table on stock 13.6 carries 27 distinct
    4-byte prefixes (verified empirically), and group 129's own (`11 80 0c 80`, `GROUP_ENTRY_PREFIX`
    below) is not the arrow-donor groups' (`10 00 04 80`, groups 103/104). Reusing one donor's
    prefix for another donor's clone would write a real-but-wrong group-table entry that no test
    here happens to check -- exactly the class of silent corruption this project refuses to risk.
    Each caller builds its own entry from ITS OWN donor's raw bytes; this helper only places them.

    Returns `(grown, edits)`. Raises `OptionRowError` on any precondition or gate failure.
    """
    gs, ge = guidata.guidata_extent(data)
    bt = guidata.binding_table(data)
    new_start = len(bt)                        # first new binding index (746 on 13.6)
    delta = len(payload)
    if delta % guidata.BINDING_ENTRY_SIZE:
        raise OptionRowError(
            f"binding growth {delta} is not a whole number of "
            f"{guidata.BINDING_ENTRY_SIZE}-byte entries")
    if delta % 4:
        raise OptionRowError(f"binding growth {delta} is not 4-aligned")
    n_new = delta // guidata.BINDING_ENTRY_SIZE

    gt = guidata.group_table(data)
    for group_slot, entry in group_writes:
        if not 0 <= group_slot < len(gt):
            raise OptionRowError(
                f"group slot {group_slot} is outside the {len(gt)}-entry group table")
        if len(entry) != guidata.GROUP_ENTRY_SIZE:
            raise OptionRowError(
                f"group {group_slot}'s entry is {len(entry)} bytes, expected "
                f"{guidata.GROUP_ENTRY_SIZE}")
        start = struct.unpack_from("<H", entry, guidata.GROUP_START_FIELD)[0]
        count = entry[guidata.GROUP_COUNT_FIELD]
        if not (new_start <= start and start + count <= new_start + n_new):
            raise OptionRowError(
                f"group {group_slot}'s entry points at start={start} count={count}, outside "
                f"the {n_new} entries this growth appends (indices "
                f"{new_start}..{new_start + n_new - 1})")

    # --- padding-arena preconditions -----------------------------------------------
    _, _, boff, bsize = struct.unpack_from(
        "<HHHH", data, gs + guidata.BINDING_TABLE_DESCRIPTOR)
    at = boff + bsize                          # binding table end == window start (0x2A70)
    img = IMAGE_CONTAINER_OFFSET
    if not at < img:
        raise OptionRowError(
            f"binding table ends at GuiData+{at:#x}, not below the image container "
            f"at {img:#x}; the padding-arena growth does not apply")
    if img > ge - gs:
        raise OptionRowError("image container offset is outside the GuiData section")
    if img - at <= delta:
        raise OptionRowError(
            f"only {img - at:#x} bytes between the binding table and the image container; "
            f"not enough room to grow by {delta:#x}")
    trim_lo = gs + img - delta
    if set(data[trim_lo:gs + img]) != {0}:
        raise OptionRowError(
            f"the {delta} bytes before the image container are not zero padding "
            f"({sorted(set(data[trim_lo:gs + img]))}); refusing to trim live bytes")

    # No declared table may straddle the insertion point (`at`) or lie within the trim region
    # [img-delta, img): the trim must consume only padding, and the insert must land on a
    # table boundary. Walk the header directory the same self-terminating way coverage does.
    first_tag, _, first_off, _ = struct.unpack_from(
        "<HHHH", data, gs + coverage.GUIDATA_HEAD_SIZE)
    desc_end = gs + first_off
    p = gs + coverage.GUIDATA_HEAD_SIZE
    binding_desc_at = None
    shift_desc: list[tuple[int, int]] = []     # (descriptor_offset_field_pos, old_offset)
    while p < desc_end:
        tag, _, off, size = struct.unpack_from("<HHHH", data, p)
        end = off + size
        if off == boff and tag == guidata.DESCRIPTOR_TAGS[guidata.BINDING_TABLE_DESCRIPTOR]:
            binding_desc_at = p
        elif size and off < at and end > at:
            raise OptionRowError(
                f"declared table {tag:#06x} [{off:#x},{end:#x}) straddles the insertion "
                f"point {at:#x}; refusing")
        elif at <= off < img:
            shift_desc.append((p + 4, off))
        if size and end > img - delta and off < img:
            raise OptionRowError(
                f"declared table {tag:#06x} [{off:#x},{end:#x}) reaches into the trim "
                f"region [{img - delta:#x},{img:#x}); the trim would corrupt it")
        p += coverage.GUIDATA_DESCRIPTOR_SIZE
    if binding_desc_at is None:
        raise OptionRowError("could not locate the binding-table descriptor to grow")

    # No object-pointer entry or GuiCode literal may target the window we rewrite in place.
    stranded = _window_references(data, gs, at, img)
    if stranded:
        raise OptionRowError(
            "the growth window is referenced by pointers this mechanism does not relocate; "
            "refusing to strand them:\n  " + "\n  ".join(stranded[:8]))

    # --- build the grown image (same-size window rewrite) --------------------------
    out = bytearray(data)
    edits: list[Edit] = []

    # (1) write each group slot's new entry (already built by the caller from its own donor).
    for group_slot, entry in group_writes:
        slot_off = gs + gt[group_slot].offset
        old_slot = bytes(out[slot_off:slot_off + guidata.GROUP_ENTRY_SIZE])
        out[slot_off:slot_off + guidata.GROUP_ENTRY_SIZE] = entry
        start = struct.unpack_from("<H", entry, guidata.GROUP_START_FIELD)[0]
        count = entry[guidata.GROUP_COUNT_FIELD]
        edits.append(Edit(slot_off, old_slot, entry, "group-slot-repoint",
                          f"group {group_slot} -> start={start} count={count}"))

    # (2) the window rewrite. `[at, img)` becomes: the new bindings, then the old window content
    #     shifted +delta, minus the `delta` trailing padding bytes -- a same-size replacement.
    win_lo, win_hi = gs + at, gs + img
    old_window = bytes(out[win_lo:win_hi])
    new_window = payload + old_window[:img - at - delta]
    assert len(new_window) == len(old_window)
    # Emit as two same-size edits so the new bindings surface as their own provenance:
    edits.append(Edit(win_lo, old_window[:delta], payload, "binding-append",
                      f"{n_new} binding(s) appended at indices "
                      f"{new_start}..{new_start + n_new - 1}"))
    edits.append(Edit(win_lo + delta, old_window[delta:], new_window[delta:], "window-shift",
                      f"variable/aux tables +{delta}, padding -{delta} (image container "
                      f"stays pinned at {img:#x})"))
    out[win_lo:win_hi] = new_window

    # (3) descriptor fixups: binding size += delta, each in-window table offset += delta.
    size_pos = binding_desc_at + 6
    old_size = struct.unpack_from("<H", out, size_pos)[0]
    struct.pack_into("<H", out, size_pos, old_size + delta)
    edits.append(Edit(size_pos, struct.pack("<H", old_size),
                      struct.pack("<H", old_size + delta), "binding-table-size",
                      f"binding table size {old_size:#x} -> {old_size + delta:#x}"))
    for off_pos, old_off in shift_desc:
        struct.pack_into("<H", out, off_pos, old_off + delta)
        edits.append(Edit(off_pos, struct.pack("<H", old_off),
                          struct.pack("<H", old_off + delta), "table-offset",
                          f"declared table offset {old_off:#x} -> {old_off + delta:#x}"))

    grown = bytes(out)

    # --- reseal --------------------------------------------------------------------
    checksum_positions = {f.label: f.position for f in patch.checksum_fields(grown)}
    resealed, changed_labels = patch.reseal(grown)
    for label in changed_labels:
        pos = checksum_positions[label]
        edits.append(Edit(pos, grown[pos:pos + 2], resealed[pos:pos + 2],
                          "checksum", f"resealed {label}"))

    # --- self-check: every relocate gate, on the finished image --------------------
    # Nothing at or after the pinned image container moved, so the object-pointer tables and
    # GuiCode pointers must be byte-identical -- verified with (at=img, k=0), i.e. "unchanged".
    relocate._verified_pointer_tables(resealed)
    ok, reason = relocate._deep_coverage_ok(resealed)
    if not ok:
        raise OptionRowError(f"grown image fails deep coverage: {reason}")
    ok, reason = relocate._verify_table_shifts(data, resealed, img, 0)
    if not ok:
        raise OptionRowError(f"grown image moved an object-pointer table entry: {reason}")
    ok, reason = relocate._boundary_oracle(resealed)
    if not ok:
        raise OptionRowError(f"grown image fails the boundary oracle: {reason}")
    ok, reason = relocate._verify_guicode_pointers(data, resealed, img, 0)
    if not ok:
        raise OptionRowError(f"grown image moved a GuiCode pointer: {reason}")
    ok, reason = relocate._verify_no_stranded_lcd_pointers(resealed, at)
    if not ok:
        raise OptionRowError(f"grown image: {reason}")

    # Post-condition the reseal above must already satisfy: a further reseal is a no-op.
    _, again = patch.reseal(resealed)
    if again:
        raise OptionRowError(f"grown image is not fully resealed: {again}")

    edits.sort(key=lambda e: e.file_offset)
    return resealed, edits


def build_config_toggle(data: bytes, selector: int) -> ConfigToggle:
    """Clone group 129's config-toggle group for `selector`, growing the binding table by four
    entries absorbed from the padding arena (see the module docstring). Returns a gate-verified
    `ConfigToggle`. Raises `OptionRowError` on any precondition or gate failure.
    """
    # --- selector validity ---------------------------------------------------------
    if not isinstance(selector, int) or isinstance(selector, bool) \
            or not 0 <= selector <= 0xFF:
        raise OptionRowError(f"selector {selector!r} is not a byte (0..255)")
    if find_config_toggle(data, selector) is not None:
        raise OptionRowError(
            f"selector {selector:#04x} already has a config-toggle group; refusing to "
            f"create a second one")

    # --- donor + reuse slot --------------------------------------------------------
    donor = _donor_bindings(data)
    retargeted = [b[:SELECTOR_FIELD] + bytes([selector]) + b[SELECTOR_FIELD + 1:]
                  for b in donor]

    gt = guidata.group_table(data)
    if REUSE_GROUP_SLOT >= len(gt):
        raise OptionRowError(
            f"reuse slot {REUSE_GROUP_SLOT} is outside the {len(gt)}-entry group table")
    if REUSE_GROUP_SLOT not in newbutton.unused_command_groups(data):
        raise OptionRowError(
            f"group {REUSE_GROUP_SLOT} is not an unused command group on this image; "
            f"repointing it would disturb a live control")

    new_start = len(guidata.binding_table(data))     # first new binding index (746 on 13.6)
    payload = b"".join(retargeted)
    new_slot = GROUP_ENTRY_PREFIX + struct.pack("<H", new_start) + bytes([DONOR_COUNT, 0])

    grown, edits = _grow_bindings(data, payload, [(REUSE_GROUP_SLOT, new_slot)])

    return ConfigToggle(new_group_index=REUSE_GROUP_SLOT, touch_id=REUSE_GROUP_SLOT,
                        bindings=retargeted, edits=edits, grown=grown)


# ======================================================================================
# append_row -- clone a stock OPTIONS checkbox row (switch + label + touch) onto a page
# ======================================================================================
#
# The on-screen half of a config-toggle OPTIONS row. `build_config_toggle` (above) grew the
# tap-emission group; this appends the WIDGET a stock OPTIONS row is made of -- the triple
#
#     wtype-6  switch  (child 394, value_slot -> the display cell, count 3)
#     wtype-11 label   (the row caption)
#     wtype-12 touch   (the hit region, carrying the group's touch id)
#
# as ONE new object attached to the OPTIONS page (object 46). `insert.insert_object` places it
# at the TOP object index and grows object-table 1 by a single entry, so no existing
# `child_index` shifts (its own gates re-verify that). This is the same append path
# `recipe_ops.add_low_volts_control` uses; the difference is purely which records are cloned.
#
# ## The switch is a value-MATCH widget, kept byte-for-byte but for its value_slot
#
# The wtype-6 switch reads `value_slot` (a variable-table cell) and renders the candidate in
# `[child_index, child_index+count)` whose per-object match value equals the cell's value
# (layout `_switch_child`; GUICODE Finding 16). All six stock rows share `child_index=394`,
# `count=3`: objects 394/395/396 carry match values 0/1/2, so cell 0 draws the empty box (394)
# and cell 1 the checked glyph (395). The match-value table is GLOBAL (indexed by object, at
# `guidata.find_match_table`), so keeping `child_index`+`count` unchanged carries those arms
# across verbatim -- the appended switch draws checked/empty off the cell with no new object
# and no match-table edit. Only `value_slot` is repointed, to `display_slot` (the B1 cell the
# per-tick bridge writes 0/1). Verified by rendering both states (see the module's tests).
#
# ## Absolute anchoring + the persistent-align reset (the two traps add_low_volts documents)
#
# The stock row's label/touch are PEN-RELATIVE to the switch, and the switch itself adds a pen
# register (`in_regs`) inherited from object 46's own flow. Spliced into an appended child that
# is reached by a plain (flow) attach, that inherited pen is not the OPTIONS grid, so the three
# records are authored ABSOLUTE instead (base-modes 0, no register reads) at explicit
# coordinates -- exactly what `add_low_volts_control` does. And because object 46 can end its
# walk right-justified (`state.align==2/3` from its bottom nav buttons), the LEADING record (the
# switch) carries a non-zero left align byte, which resets the persistent align so the label's
# box is not dragged off by an inherited justify (the persistent-align trap,
# `layout._placed_label_box`).

# Row layout: the caller's (x, y) is the switch/checkbox anchor (the row's left origin). The
# label and touch sit at these deltas from it, and at these sizes -- the stock Heat Pump row's
# own placement (switch child drawn at (95,88); label box (123,63)-(373,113); touch box
# (88,63)-(318,113)), i.e. label +28/-25 @ 250x50, touch -7/-25 @ 230x50. Reproducing the stock
# offsets is what makes the appended row sit like the other six.
_ROW_LABEL_DX, _ROW_LABEL_DY, _ROW_LABEL_W, _ROW_LABEL_H = 28, -25, 250, 50
_ROW_TOUCH_DX, _ROW_TOUCH_DY, _ROW_TOUCH_W, _ROW_TOUCH_H = -7, -25, 230, 50


def _author_switch(data: bytes, donor: records.WRecord, x: int, y: int,
                   display_slot: int) -> bytes:
    """Clone the donor wtype-6 switch and make it absolute at (x, y) with its value_slot
    repointed to `display_slot`; `child_index` (394) and `count` (3) -- the match-value arms --
    are kept byte-for-byte. Four in-place edits on the donor bytes:

      * the register-index byte (record+8, present because the donor header sets byte0 bit
        0x40) -> 0x00, clearing the pen read so geom[0]/[1] anchor absolutely;
      * header byte3 align bits -> 1 (left, NON-zero: resets the persistent align state);
      * geom[0]/geom[1] -> (x, y) (via `records.geom_offset`, round-trip guarded);
      * value_slot -> `display_slot` (via `records.value_slot_offset`).

    The result is decoded and its shape re-asserted (refuse-rather-than-corrupt): a build whose
    donor switch is not the expected register-anchored shape raises rather than emit a record
    that would decode wrong."""
    if not (donor.header[0] & 0x40):
        raise OptionRowError(
            "donor switch carries no register-index byte to clear; it is not the stock "
            "pen-anchored OPTIONS switch this clone expects")
    if donor.value_slot is None or donor.child_index is None or donor.count is None:
        raise OptionRowError(
            "donor switch is missing a value_slot/child_index/count; not a stock OPTIONS switch")

    sb = bytearray(data[donor.offset:donor.end])
    sb[8] = 0x00                                   # register-index byte -> no pen read (absolute)
    sb[3] = (sb[3] & 0x3F) | 0x40                  # align bits -> 1 (left; resets state.align)
    struct.pack_into("<H", sb, records.geom_offset(data, donor, 0) - donor.offset, x)
    struct.pack_into("<H", sb, records.geom_offset(data, donor, 1) - donor.offset, y)
    struct.pack_into("<H", sb, records.value_slot_offset(data, donor) - donor.offset, display_slot)
    out = bytes(sb)

    chk = records.decode_record(out, 0)
    if not (chk.wtype == 6 and chk.value_slot == display_slot
            and chk.child_index == donor.child_index and chk.count == donor.count
            and chk.geom[0] == x and chk.geom[1] == y
            and set(chk.in_regs) == {0} and chk.align == 1 and chk.size == donor.size):
        raise OptionRowError(
            "authored switch did not decode to the expected absolute value-match shape "
            f"(wt={chk.wtype} vslot={chk.value_slot} child={chk.child_index} count={chk.count} "
            f"geom={chk.geom[:2]} in_regs={chk.in_regs} align={chk.align})")
    return out


def _author_label(data: bytes, donor: records.WRecord, label: str, x: int, y: int) -> bytes:
    """Author an absolute wtype-11 row label reading `label`, sized `_ROW_LABEL_W` x
    `_ROW_LABEL_H` at (x, y). Header byte-shape is the donor's own, made absolute:

      * byte1 0x50 -> base-modes [0,0,1,1]: geom[0]/[1] (x, y) absolute, geom[2]/[3] pen-relative
        (ADDED to x/y, so they read as WIDTH/HEIGHT -> box (x,y)-(x+w,y+h)) -- the confirmed-on-
        coach encoding `add_low_volts_control` uses;
      * byte3 0x2a -> the donor's fillA/fillB modes, align bits 0 (inherit the switch's left
        reset -- the label must NOT re-justify itself);
      * R38 font + R39 + the four R49 line-layout bytes are the donor label's own.

    Refuses if the donor is not a font-bearing wtype-11 label."""
    if donor.wtype != 11 or donor.font is None:
        raise OptionRowError(
            f"donor label record is wtype-{donor.wtype} (font={donor.font}); "
            "not the stock wtype-11 OPTIONS caption")
    text = f"   {label}".encode("ascii")         # the stock caption's 3-space left indent
    hdr = bytes([0x0B, 0x50, 0x0F, 0x2A, 0x00, 0x00, 0x00, 0x00])
    geom = struct.pack("<HHHH", x, y, _ROW_LABEL_W, _ROW_LABEL_H)
    tail = bytes([0x01, donor.font, 0xFF,        # R37 count=1, R38 font, R39 inherit
                  donor.h_align or 0, donor.v_align or 0,
                  donor.line_height or 0, donor.line_height_adds_font or 0])   # R49
    body = bytes([len(text)]) + text + b"\x00"
    out = hdr + geom + tail + body

    chk = records.decode_record(out, 0)
    if not (chk.wtype == 11 and records.label_text(chk.label or "").strip() == label
            and chk.geom[0] == x and chk.geom[1] == y):
        raise OptionRowError(
            f"authored label did not decode to {label!r} at ({x},{y}) "
            f"(got {chk.label!r} at {chk.geom[:2]})")
    return out


def _author_touch(x: int, y: int, touch_id: int) -> bytes:
    """Author an absolute wtype-12 touch region covering the row (`_ROW_TOUCH_W` x
    `_ROW_TOUCH_H` at (x, y)) and carrying `touch_id`. For a wtype-12 the touch id is the
    trailing R50 u16 (`aed & 0x1000`); base-modes [0,0,1,1] make the box (x,y)-(x+w,y+h). The
    touch-id -> group map is GLOBAL, so the id fires its group regardless of the hosting
    object -- no per-screen wiring."""
    if not 0 <= touch_id <= 0xFFFF:
        raise OptionRowError(f"touch id {touch_id} is not a u16")
    hdr = bytes([0x0C, 0x50, 0x0F, 0x00, 0x00, 0x00, 0x00, 0x00])
    out = hdr + struct.pack("<HHHH", x, y, _ROW_TOUCH_W, _ROW_TOUCH_H) + struct.pack("<H", touch_id)

    chk = records.decode_record(out, 0)
    tid = out[-2] | (out[-1] << 8)
    if not (chk.wtype == 12 and tid == touch_id and chk.geom[0] == x and chk.geom[1] == y):
        raise OptionRowError(
            f"authored touch did not decode to id {touch_id} at ({x},{y}) "
            f"(wt={chk.wtype} id={tid} geom={chk.geom[:2]})")
    return out


def append_row(data: bytes, parent_obj: int, donor_switch_obj: int, donor_switch_rec: int,
               label: str, touch_id: int, display_slot: int, x: int,
               y: int) -> tuple[bytes, int, list[Edit]]:
    """Append a stock-shaped OPTIONS checkbox row to `parent_obj` as one new object.

    Clones the donor triple at records `donor_switch_rec` (a wtype-6 switch), `+1` (a wtype-11
    label) and `+2` (a wtype-12 touch) of object `donor_switch_obj`. The switch's value_slot is
    repointed to `display_slot` (child 394 + count kept, so it draws empty at cell 0 / checked at
    cell 1); the label becomes `label`; the touch carries `touch_id`. The three records are
    authored ABSOLUTE at the row origin (x, y) with a leading left-align reset, then attached via
    `insert.insert_object`, which grows object-table 1 by one entry so no existing `child_index`
    shifts.

    Returns `(out_bytes, new_object_index, edits)`. Raises `OptionRowError` on any precondition
    or gate failure (including `insert`'s own refusals, re-raised as `OptionRowError`).
    """
    if not isinstance(label, str) or not label:
        raise OptionRowError("label must be a non-empty string")
    for name, v in (("touch_id", touch_id), ("display_slot", display_slot),
                    ("x", x), ("y", y)):
        if not isinstance(v, int) or isinstance(v, bool):
            raise OptionRowError(f"{name} must be an int, got {v!r}")

    gs, _ = guidata.guidata_extent(data)
    tables = guidata.object_tables(data)
    tab = tables[insert.OBJECT_TABLE_INDEX]
    for name, idx in (("donor_switch_obj", donor_switch_obj), ("parent_obj", parent_obj)):
        if not 0 <= idx < len(tab):
            raise OptionRowError(f"{name} {idx} is outside the {len(tab)}-entry object table")

    donor_recs = records.object_records(data, gs + tab[donor_switch_obj])
    if donor_switch_rec + 2 >= len(donor_recs):
        raise OptionRowError(
            f"donor object {donor_switch_obj} has {len(donor_recs)} records; no switch/label/"
            f"touch triple at record {donor_switch_rec}")
    switch_d, label_d, touch_d = donor_recs[donor_switch_rec:donor_switch_rec + 3]
    if (switch_d.wtype, label_d.wtype, touch_d.wtype) != (6, 11, 12):
        raise OptionRowError(
            f"records {donor_switch_rec}..{donor_switch_rec + 2} of object {donor_switch_obj} are "
            f"wtypes {(switch_d.wtype, label_d.wtype, touch_d.wtype)}, not a (6, 11, 12) OPTIONS "
            "row triple")

    # Author the three records absolute at the row origin (see module notes for each field).
    switch = _author_switch(data, switch_d, x, y, display_slot)
    lbl = _author_label(data, label_d, label,
                        x + _ROW_LABEL_DX, y + _ROW_LABEL_DY)
    touch = _author_touch(x + _ROW_TOUCH_DX, y + _ROW_TOUCH_DY, touch_id)

    payload = bytes([3]) + switch + lbl + touch      # <count=3><switch><label><touch>
    payload += b"\x00" * (-len(payload) % 4)         # object offsets are 4-aligned

    # Attach via the flow template (object 46 has no positioned wtype-5 template): the records are
    # absolute, so the attach's flow origin does not move them. insert_object appends at the top
    # index and re-verifies every relocate gate + the growth-shift analogue.
    try:
        out, edits = insert.insert_object(data, payload, parent_obj, position=None)
    except insert.InsertError as exc:
        raise OptionRowError(f"insert_object refused the appended row: {exc}") from exc
    out, _ = patch.reseal(out)

    new_index = len(guidata.object_tables(out)[insert.OBJECT_TABLE_INDEX]) - 1
    return out, new_index, edits


# ======================================================================================
# build_config_stepper -- clone the stock 0x0E +/-300 arrow groups (103/104) onto any
# selector with a +/-step delta, reusing `_grow_bindings`.
# ======================================================================================
#
# The stock "Time at Stop Volts" +/- arrows on selector 0x0E are pure binding data: each
# arrow is a three-binding group -- a repeating press delta (function 0xD2, byte[9]), a
# release zero-delta, and a D3 read-back -- with no on-screen widget of its own authored
# here (a later task wires the touch ids to a stepper control). The G12 on the other end of
# the bus adds the signed delta to the addressed selector's register and echoes the result,
# so cloning these bindings onto a new selector authors no firmware code, only data the G12
# already knows how to interpret.

# Group 104 (+300) and 103 (-300): both selector 0x0E, three bindings each (press/release/
# read-back). Pinned by group index AND by press delta -- see `_arrow_donor`.
ARROW_DONOR_PLUS = 104
ARROW_DONOR_MINUS = 103
ARROW_DONOR_SELECTOR = 0x0E
ARROW_DONOR_COUNT = 3

FUNCTION_FIELD = 9      # 0xD1 absolute / 0xD2 signed delta / 0xD3 read-back
VALUE_FIELD = 10        # u16, MSB FIRST (+300 == 01 2c, -300 == fe d4)
DELTA_FUNCTION = 0xD2


@dataclass(frozen=True)
class ConfigStepper:
    """The two arrow groups `build_config_stepper` created.

    `bindings` is the six 12-byte entries in emission order: plus press / plus release /
    plus read-back, then the same three for minus.
    """
    plus_group: int
    minus_group: int
    plus_touch_id: int
    minus_touch_id: int
    bindings: list[bytes]
    edits: list[Edit]
    grown: bytes


def _arrow_donor(data: bytes, group: int, expect_delta: int) -> list[bytes]:
    """Group `group`'s three raw bindings, pinned to the stock 0x0E arrow identity."""
    gt = guidata.group_table(data)
    if not 0 <= group < len(gt):
        raise OptionRowError(f"arrow donor group {group} is outside the group table")
    g = gt[group]
    if g.count != ARROW_DONOR_COUNT:
        raise OptionRowError(
            f"arrow donor group {group} has count {g.count}, expected {ARROW_DONOR_COUNT}")
    bt = guidata.binding_table(data)
    ents = [bt[j] for j in range(g.start, g.start + g.count) if j < len(bt)]
    if len(ents) != ARROW_DONOR_COUNT:
        raise OptionRowError(
            f"arrow donor group {group} selects {len(ents)} in-range bindings")
    if any(e.dgn_low != CONFIG_TOGGLE_DGN_LOW for e in ents):
        raise OptionRowError(
            f"arrow donor group {group} is not all config ({CONFIG_TOGGLE_DGN_LOW:#06x}) "
            f"bindings: {[hex(e.dgn_low) for e in ents]}")
    sels = {e.raw[SELECTOR_FIELD] for e in ents}
    if sels != {ARROW_DONOR_SELECTOR}:
        raise OptionRowError(
            f"arrow donor group {group} carries selector(s) {sorted(hex(s) for s in sels)}, "
            f"not the expected {ARROW_DONOR_SELECTOR:#04x} 'Time at Stop Volts' arrow; this "
            f"build's group {group} is not the donor this module clones")
    press = ents[0].raw
    if press[FUNCTION_FIELD] != DELTA_FUNCTION:
        raise OptionRowError(
            f"arrow donor group {group} press binding function is "
            f"{press[FUNCTION_FIELD]:#04x}, not the delta {DELTA_FUNCTION:#04x}")
    got = struct.unpack_from(">h", press, VALUE_FIELD)[0]
    if got != expect_delta:
        raise OptionRowError(
            f"arrow donor group {group} press delta is {got}, expected {expect_delta}")
    return [e.raw for e in ents]


def _retarget_arrow(raw: bytes, selector: int, value: int | None) -> bytes:
    """One donor binding with its selector replaced and, when `value` is given, its
    16-bit delta replaced. `value` is None for the release and read-back entries, whose
    payloads carry no per-control delta."""
    out = bytearray(raw)
    out[SELECTOR_FIELD] = selector
    if value is not None:
        struct.pack_into(">h", out, VALUE_FIELD, value)
    return bytes(out)


def _clone_group_entry(donor_raw: bytes, start: int, count: int) -> bytes:
    """`donor_raw`'s own leading prefix + trailing pad byte, with `start`/`count` spliced in --
    the group-table analogue of `_retarget_arrow` for bindings. As `_grow_bindings` documents,
    a group entry's prefix bytes are NOT a generic constant (27 distinct 4-byte prefixes appear
    across the 233-entry table on 13.6); only the donor's own bytes are trustworthy here."""
    return (donor_raw[:guidata.GROUP_START_FIELD]
            + struct.pack("<H", start)
            + bytes([count])
            + donor_raw[guidata.GROUP_COUNT_FIELD + 1:guidata.GROUP_ENTRY_SIZE])


def build_config_stepper(data: bytes, selector: int, step: int) -> ConfigStepper:
    """Clone the stock `0x0E` +/- arrow groups onto `selector` with a `+/-step` delta.

    The arrows are pure binding data: a repeating press delta, a release zero-delta, and
    a `D3` read-back.
    The G12 adds the delta and echoes the result, so nothing here authors code.

    `step` must be a byte-safe magnitude (1..255): the packed 0xEE register keeps the
    minutes in the LOW byte, and a step that carries out of it would corrupt the flag
    (PC.47's borrow, in the other direction).
    """
    if not isinstance(step, int) or isinstance(step, bool) or not 1 <= step <= 0xFF:
        raise OptionRowError(
            f"step {step!r} must be a positive byte-safe magnitude (1..255); a larger "
            f"step would carry out of the minute byte and disturb the flag")
    if not isinstance(selector, int) or isinstance(selector, bool) \
            or not 0 <= selector <= 0xFF:
        raise OptionRowError(f"selector {selector!r} is not a byte (0..255)")

    plus_donor = _arrow_donor(data, ARROW_DONOR_PLUS, 300)
    minus_donor = _arrow_donor(data, ARROW_DONOR_MINUS, -300)

    bindings = [
        _retarget_arrow(plus_donor[0], selector, step),
        _retarget_arrow(plus_donor[1], selector, None),
        _retarget_arrow(plus_donor[2], selector, None),
        _retarget_arrow(minus_donor[0], selector, -step),
        _retarget_arrow(minus_donor[1], selector, None),
        _retarget_arrow(minus_donor[2], selector, None),
    ]

    free = newbutton.free_group_slots(data)
    if len(free) < 2:
        raise OptionRowError(
            f"need 2 free group slots for a stepper, this image has {len(free)}: {free}")
    plus_group, minus_group = free[0], free[1]

    # Each new group's entry is cloned from ITS OWN donor's raw bytes (not a shared constant --
    # see `_clone_group_entry`), only the start index changed (count stays 3, the donor's own).
    gt = guidata.group_table(data)
    start = len(guidata.binding_table(data))
    plus_entry = _clone_group_entry(gt[ARROW_DONOR_PLUS].raw, start, ARROW_DONOR_COUNT)
    minus_entry = _clone_group_entry(
        gt[ARROW_DONOR_MINUS].raw, start + ARROW_DONOR_COUNT, ARROW_DONOR_COUNT)

    grown, edits = _grow_bindings(
        data, b"".join(bindings),
        [(plus_group, plus_entry), (minus_group, minus_entry)])

    return ConfigStepper(plus_group=plus_group, minus_group=minus_group,
                         plus_touch_id=plus_group, minus_touch_id=minus_group,
                         bindings=bindings, edits=edits, grown=grown)


# ======================================================================================
# toggle_to_flag_delta -- convert a config toggle's two D1 absolute writes to D2 signed
# deltas on one byte of the u16, for a selector whose register is packed with a second
# value that an absolute write would erase.
# ======================================================================================

ABSOLUTE_FUNCTION = 0xD1
READBACK_FUNCTION = 0xD3


def toggle_to_flag_delta(data: bytes, group: int, selector: int,
                         shift: int = 8) -> tuple[bytes, list[Edit]]:
    """Rewrite a config toggle's two ABSOLUTE writes as signed deltas on one byte field.

    A stock config toggle writes the whole u16 (`D1` absolute 0 / 1). When the selector
    is a **packed** register that also carries another setting, an absolute write erases
    the other field. This converts the pair to `D2` deltas of `-(1 << shift)` and
    `+(1 << shift)`, so the toggle moves only its own byte.

    Three same-size bytes per write binding (the function byte and the 16-bit value);
    the read-backs are untouched. Returns `(sealed, edits)`.

    Refuses unless the group is exactly the expected absolute-toggle shape on
    `selector` -- which also makes it idempotent-safe: a second call finds `D2`, not
    `D1`, and raises rather than doubling the deltas.
    """
    if not 0 <= shift <= 15:
        raise OptionRowError(f"shift {shift} is not a u16 bit position")
    delta = 1 << shift

    gt = guidata.group_table(data)
    if not 0 <= group < len(gt):
        raise OptionRowError(f"group {group} is outside the group table")
    g = gt[group]
    bt = guidata.binding_table(data)
    ents = [bt[j] for j in range(g.start, g.start + g.count) if j < len(bt)]
    if len(ents) != g.count:
        raise OptionRowError(f"group {group} selects out-of-range bindings")

    sels = {e.raw[SELECTOR_FIELD] for e in ents}
    if sels != {selector}:
        raise OptionRowError(
            f"group {group} carries selector(s) {sorted(hex(s) for s in sels)}, "
            f"not {selector:#04x}")

    writes = [e for e in ents if e.raw[FUNCTION_FIELD] == ABSOLUTE_FUNCTION]
    reads = [e for e in ents if e.raw[FUNCTION_FIELD] == READBACK_FUNCTION]
    if len(writes) != 2 or len(reads) != len(ents) - 2:
        raise OptionRowError(
            f"group {group} is not an absolute config toggle: functions are "
            f"{[hex(e.raw[FUNCTION_FIELD]) for e in ents]}; expected exactly two "
            f"{ABSOLUTE_FUNCTION:#04x} writes (already converted?)")

    values = {struct.unpack_from(">h", e.raw, VALUE_FIELD)[0]: e for e in writes}
    if set(values) != {0, 1}:
        raise OptionRowError(
            f"group {group}'s absolute writes carry {sorted(values)}, expected 0 and 1")

    # Binding.offset is GUIDATA-RELATIVE (binding 388 -> 0x19A8), so every write and
    # every Edit position must be rebased onto the section start. Indexing the file with
    # a bare .offset silently corrupts an unrelated part of the image. Verified in the
    # pre-flight scan; the same trap as Task 2's Variable.offset.
    gs, _ = guidata.guidata_extent(data)
    out = bytearray(data)
    edits: list[Edit] = []
    for absolute, new in ((1, delta), (0, -delta)):
        e = values[absolute]
        at = gs + e.offset + FUNCTION_FIELD
        end = gs + e.offset + VALUE_FIELD + 2
        old = data[at:end]
        out[at] = DELTA_FUNCTION
        struct.pack_into(">h", out, gs + e.offset + VALUE_FIELD, new)
        edits.append(Edit(
            at, old, bytes(out[at:end]), "toggle-flag-delta",
            f"group {group} binding {e.index}: absolute {absolute} -> delta {new:+#06x}"))

    grown = bytes(out)
    checksum_positions = {f.label: f.position for f in patch.checksum_fields(grown)}
    sealed, changed_labels = patch.reseal(grown)
    for label in changed_labels:
        pos = checksum_positions[label]
        edits.append(Edit(pos, grown[pos:pos + 2], sealed[pos:pos + 2],
                          "checksum", f"resealed {label}"))
    return sealed, edits
