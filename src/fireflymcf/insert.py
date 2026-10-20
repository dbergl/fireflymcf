"""Clone an existing GuiData object and attach it to a page as a genuinely new object.

This is Task 2 of the GuiData relocator build. It sits
on top of Task 1's `relocate` (`relocate.py`) and adds two things:

- `clone_object(data, donor_index)` -- the exact byte span of an existing object. Because
  the clone is a byte-for-byte copy, it decodes *identically* to its donor; it needs no new
  correctness proof beyond the boundary oracle that already covers that shape.
- `insert_object(data, object_bytes, parent_index)` -- append `object_bytes` as a brand-new
  object at the **top object index** (so no existing `child_index` shifts) and wire it in as
  a child of `parent_index`.

## Why the append cannot be a single `relocate` call

`relocate` moves bytes and repairs every reference, but its self-check (`_verify_table_shifts`)
asserts the object-pointer **table entry count is unchanged** -- it is a pure relocation, not
a structural edit. Appending a new object legitimately grows object-table 1 by one entry, so
it can never pass that gate. The two constraints are, in fact, mutually exclusive within one
`relocate` call: the object-pointer table's self-terminating invariant
(`coverage._pointer_tables`: a table's lowest pointer must equal the file offset right after
its own words) *requires* the new tail entry to be a valid in-range pointer -- which makes the
run scan count it, tripping the count gate; and anything that is *not* a valid pointer there
leaves the table no longer immediately followed by its objects, tripping the invariant gate.

So `_append_object` performs the one operation `relocate`'s guarded top-level cannot express,
reusing `relocate`'s **helper functions** for reading the tables/directory
(`_verified_pointer_tables`, `_guidata_directory`), `patch.reseal`, and -- crucially -- three
of `relocate`'s four **validation gates** (`_verified_pointer_tables`, `_deep_coverage_ok`,
`_boundary_oracle`) on the result, plus a growth-aware analogue of the fourth
(`_verify_growth_shifts`, below) that expects exactly one added entry. The **attachment** step
(inserting a container record into the parent, which does *not* change any table count) goes
through the ordinary guarded `relocate`.

## Alignment (a hard requirement, learned the hard way)

Every object-pointer table past the insertion point is a run of 4-byte `0x900xxxxx` words that
the firmware reads as `u32`s at 4-byte-aligned addresses. Inserting a number of bytes that is
**not a multiple of 4** slides those tables to unaligned offsets and they cease to be readable
as pointer runs at all (`object_tables` then finds only the tables *before* the insertion). So
every payload this module hands to `relocate` is padded to a 4-byte multiple. Object spans are
always 4-aligned already (objects are padded to a 4-byte boundary, so consecutive table entries
differ by a multiple of 4), which is why a clone's length needs no padding; the cloned
*container record* does.
"""
from __future__ import annotations

import struct

from . import guidata, patch, records, relocate
from .relocate import Edit, RelocationError

# Object-pointer table 1 is the widget/screen table `child_index` values index into and the
# only one with a `<count><records>` object shape the boundary oracle can validate -- the same
# table `relocate.OBJECT_TABLE_INDEX` names.
OBJECT_TABLE_INDEX = relocate.OBJECT_TABLE_INDEX
BASE = guidata.OBJECT_POINTER_BASE


class InsertError(RelocationError):
    """`clone_object`/`insert_object` cannot proceed, or produced a build that fails a gate.

    A subclass of `RelocationError` so callers already catching relocation failures catch
    these too; raised rather than returning a build that only looks right, matching this
    project's standing discipline (a defensible refusal beats a fitted answer).
    """


# --------------------------------------------------------------------------------------
# clone
# --------------------------------------------------------------------------------------
def _object_span(data: bytes, index: int) -> tuple[int, int]:
    """Section-relative `[start, end)` of object `index` in object-table `OBJECT_TABLE_INDEX`.

    The table stores entries in descending address order, so an object's end is the *next
    larger* pointer value (or, for the top-address object, the start of the following pointer
    table -- its region boundary). Computing the end as "the smallest entry strictly greater
    than this one" is robust to any index, including the trailing (highest-address) object,
    without assuming the raw storage order.
    """
    gs, ge = guidata.guidata_extent(data)
    tables = relocate._verified_pointer_tables(data)
    if not 0 <= OBJECT_TABLE_INDEX < len(tables):
        raise InsertError(f"no object table at index {OBJECT_TABLE_INDEX}")
    table = guidata.object_tables(data)[OBJECT_TABLE_INDEX]
    if not 0 <= index < len(table):
        raise InsertError(
            f"object index {index} out of range [0, {len(table)})")
    start = table[index]
    higher = [v for v in table if v > start]
    if higher:
        end = min(higher)
    else:
        # Top-address object: its region ends where the next pointer table's words begin.
        after = [ts - gs for ts, _, _ in tables if ts - gs > start]
        end = min(after) if after else (ge - gs)
    if end <= start:
        raise InsertError(
            f"object {index} has an empty span [{start:#x}, {end:#x}) -- a repeated "
            f"pointer; not a cloneable object")
    return start, end


def clone_object(data: bytes, donor_index: int) -> bytes:
    """Return the exact, unmodified byte span of object `donor_index` (object-table 1).

    The bytes decode identically to the donor -- `insert_object` places them verbatim as a new
    object, and the boundary oracle that already passes over the donor covers the clone too.
    Raises `InsertError` for an out-of-range index or a zero-length (repeated-pointer) span.
    """
    gs, _ = guidata.guidata_extent(data)
    start, end = _object_span(data, donor_index)
    return bytes(data[gs + start:gs + end])


# --------------------------------------------------------------------------------------
# append a new object at the top index (the one thing `relocate` cannot do)
# --------------------------------------------------------------------------------------
def _verify_growth_shifts(before: bytes, after: bytes, at: int, delta: int,
                          new_entry_rel: int) -> None:
    """The growth-aware analogue of `relocate._verify_table_shifts`: re-read every object-pointer
    table fresh from `after` and confirm it is exactly `before` shifted by `delta` for targets
    `>= at`, PLUS one appended entry (value `new_entry_rel`) on `OBJECT_TABLE_INDEX`. Because
    it is an exact value comparison independent of how the shift was computed, it catches a
    wrong shift on any entry -- including a table's trailing one, the blind spot
    `_deep_coverage_ok` documents. Raises `InsertError` on any mismatch.
    """
    b_tables = guidata.object_tables(before)
    a_tables = guidata.object_tables(after)
    if len(b_tables) != len(a_tables):
        raise InsertError(
            f"object-table count changed {len(b_tables)} -> {len(a_tables)}")
    for ti, (b_table, a_table) in enumerate(zip(b_tables, a_tables)):
        grew = ti == OBJECT_TABLE_INDEX
        expected_len = len(b_table) + (1 if grew else 0)
        if len(a_table) != expected_len:
            raise InsertError(
                f"table {ti}: entry count {len(b_table)} -> {len(a_table)}, "
                f"expected {expected_len}")
        for ei, old in enumerate(b_table):
            want = old + delta if old >= at else old
            if a_table[ei] != want:
                raise InsertError(
                    f"table {ti} entry {ei}: {old:#x} -> {a_table[ei]:#x}, "
                    f"expected {want:#x}")
        if grew and a_table[-1] != new_entry_rel:
            raise InsertError(
                f"appended entry is {a_table[-1]:#x}, expected {new_entry_rel:#x}")


def _append_object(data: bytes, object_bytes: bytes) -> tuple[bytes, list[Edit], int]:
    """Place `object_bytes` as a new object at the top index of object-table 1 and append a
    pointer to it, repairing every downstream reference and reslealing. Returns
    `(new_data, edits, new_index)`.

    The new object goes at the very start of the object region (immediately after the grown
    table words, i.e. the new lowest-address / lowest-value entry), preceded by its own 4-byte
    pointer entry. Placing it there is what keeps the table self-terminating (its lowest
    pointer still equals the offset right after its own words) while giving it the highest
    index, so no existing `child_index` moves.
    """
    if len(object_bytes) == 0:
        raise InsertError("refusing to append an empty object")
    if len(object_bytes) % 4:
        raise InsertError(
            f"object length {len(object_bytes)} is not a multiple of 4; inserting it would "
            f"unalign every following pointer table")

    gs, _ = guidata.guidata_extent(data)
    tables = relocate._verified_pointer_tables(data)
    _, obj_tbl_end_abs, _ = tables[OBJECT_TABLE_INDEX]
    tbl_end = obj_tbl_end_abs - gs          # == min(table1) == insertion point (section-rel)
    k = len(object_bytes)
    delta = 4 + k                            # 4-byte pointer entry + the object
    ins = gs + tbl_end                       # absolute insertion offset
    new_entry_rel = tbl_end + 4              # the object sits right after its own pointer word

    payload = struct.pack("<I", BASE + new_entry_rel) + object_bytes
    assert len(payload) == delta

    # Insert the new pointer word + object, then shift every downstream reference by `delta`
    # through `relocate`'s shared `_apply_downstream_shift` -- the exact same offset math
    # `relocate` itself uses, so the two cannot drift. The only thing this does that a plain
    # `relocate` call cannot is grow object-table 1 by the one appended entry (the new lowest
    # pointer, part of `payload`), which `relocate`'s count-preserving self-check forbids.
    out = bytearray(data[:ins] + payload + data[ins:])
    edits: list[Edit] = [Edit(ins, b"", bytes(payload), "object-insertion",
                              f"new object ({k} bytes) + pointer entry at GuiData+{tbl_end:#x}")]
    try:
        edits.extend(relocate._apply_downstream_shift(data, out, tbl_end, delta))
    except RelocationError as exc:  # re-label a mid-table split in this module's terms
        raise InsertError(str(exc)) from exc

    grown = bytes(out)

    # 4. reseal.
    checksum_positions = {f.label: f.position for f in patch.checksum_fields(grown)}
    resealed, changed_labels = patch.reseal(grown)
    for label in changed_labels:
        pos = checksum_positions[label]
        edits.append(Edit(pos, grown[pos:pos + 2], resealed[pos:pos + 2],
                          "checksum", f"resealed {label}"))

    # 5. self-check: three of relocate's four gates verbatim, plus the growth-aware fourth.
    relocate._verified_pointer_tables(resealed)
    ok, reason = relocate._deep_coverage_ok(resealed)
    if not ok:
        raise InsertError(f"append produced a build that fails deep coverage: {reason}")
    ok, reason = relocate._boundary_oracle(resealed)
    if not ok:
        raise InsertError(f"append produced a build that fails the boundary oracle: {reason}")
    ok, reason = relocate._verify_guicode_pointers(data, resealed, tbl_end, delta)
    if not ok:
        raise InsertError(
            f"append produced a build whose GuiCode points into shifted GuiData "
            f"(would boot-loop): {reason}")
    ok, reason = relocate._verify_no_stranded_lcd_pointers(data, tbl_end)
    if not ok:
        raise InsertError(f"append: {reason}")
    _verify_growth_shifts(data, resealed, tbl_end, delta, new_entry_rel)

    new_index = len(guidata.object_tables(resealed)[OBJECT_TABLE_INDEX]) - 1
    edits.sort(key=lambda e: e.file_offset)
    return resealed, edits, new_index


# --------------------------------------------------------------------------------------
# attach: make the new object a child of a page
# --------------------------------------------------------------------------------------
def _child_index_field_offset(data: bytes, record: records.WRecord) -> int:
    """Record-relative byte offset of a wtype-5 container record's `child_index` u16.

    Recovered by replaying the decoder's cursor (`records.child_index_offset`, R48,
    round-trip guarded) rather than assuming `size - 2`, so it stays correct if the record
    carries presence-masked fields (a value-slot, R49 alignment bytes, ...) after the child
    index. Returns the offset relative to the record's own start.
    """
    if record.child_index is None:
        raise InsertError("record has no child_index to relocate")
    off = records.child_index_offset(data, record)
    if off is None:
        raise InsertError("record decode disagrees it carries a child_index")
    return off - record.offset


def _geom_field_offset(data: bytes, record: records.WRecord, i: int) -> int:
    """Record-relative byte offset of geometry slot `i` (`records.geom_offset`, round-trip
    guarded). Raises `InsertError` if the slot is absent on this record."""
    off = records.geom_offset(data, record, i)
    if off is None:
        raise InsertError(
            f"template wtype-{record.wtype} record has no geometry slot {i} to position with")
    return off - record.offset


def _container_template(data: bytes, recs: list[records.WRecord],
                        positioned: bool) -> tuple[bytes, records.WRecord]:
    """Pick, and byte-copy, one of `parent`'s wtype-5 container records to use as the
    attachment template. Returns `(record_bytes, template_record)`.

    When `positioned` is True the template must carry both x/y geometry slots (geom[0] and
    geom[1] present) so the child can be placed at an absolute (x, y) -- e.g. HOME's child-907
    template `[70, 49]`. When False, the first wtype-5-with-child record is used (HOME's
    child-892 template has empty geometry, so the child pins at the parent's flow origin).
    """
    for r in recs:
        if r.wtype != 5 or r.child_index is None:
            continue
        if positioned and (r.geom[0] is None or r.geom[1] is None):
            continue
        return bytes(data[r.offset:r.offset + r.size]), r
    what = ("wtype-5 container record carrying x/y geometry" if positioned
            else "wtype-5 container record")
    raise InsertError(f"parent has no {what} to clone as an attachment point")


def insert_object(data: bytes, object_bytes: bytes, parent_index: int,
                  position: tuple[int, int] | None = None) -> tuple[bytes, list[Edit]]:
    """Append `object_bytes` as a new top-index object and wire it in as a child of
    `parent_index`. Returns `(new_data, edits)` -- the accumulated `Edit` list (in final-image
    coordinates) that Task 3's generalised-L3 ladder consumes.

    Two moves, both alignment-safe and both re-verified:

    1. **Append** the object at the top index via `_append_object` (grows object-table 1 by
       one entry; the one operation `relocate`'s count-preserving self-check cannot do).
    2. **Attach** it: clone one of the parent's wtype-5 container records, repoint its
       `child_index` at the new top index, insert it into the parent's record stream, and bump
       the parent's leading record-count byte -- all via the ordinary guarded `relocate`, since
       inserting a record changes no table count.

    `position=(x, y)` requests a **positioned** attach: the template must be a wtype-5 record
    that carries x/y geometry (HOME's child-907 template), and its geom[0]/geom[1] are rewritten
    to `(x, y)` so the child lands at that absolute origin (a framed clone's own geometry sets
    the box *size*, not its position -- position comes from the attach record). With `position`
    None the first plain wtype-5-with-child template is used and the child pins at the parent's
    flow origin.

    **Padding.** A wtype-5 record is rarely a multiple of 4 bytes, but every insertion must be a
    multiple of 4 or the following pointer tables unalign (module docstring). The object's own
    trailing pad absorbs the difference: the record is placed right after the parent's last
    existing record, and the *net* bytes inserted are `round4(C+rl) - round4(C)` (C = the
    parent's content length, rl = the record length) -- so the object stays 4-aligned AND decodes
    to within `[0,4)` of its boundary. (Padding the *record* to 4 instead, as an earlier revision
    did, left `oldpad + recordpad` trailing bytes, which overflows the boundary oracle's `<4`
    tolerance for a 17-byte record on a parent whose content was not already 4-aligned -- e.g.
    HOME, C=171, oldpad=1: a 17-byte record padded to 20 left a 4-byte gap.)

    Raises `InsertError` if `parent_index` is out of range, if the parent has no suitable wtype-5
    container record, or if either step fails a correctness gate.
    """
    gs, _ = guidata.guidata_extent(data)
    parent_table = guidata.object_tables(data)[OBJECT_TABLE_INDEX]
    if not 0 <= parent_index < len(parent_table):
        raise InsertError(
            f"parent index {parent_index} out of range [0, {len(parent_table)})")

    # Confirm the parent is decodable and carries a suitable template BEFORE we touch anything,
    # so an unsuitable parent is a clean refusal, not a half-applied edit.
    parent_off0 = gs + parent_table[parent_index]
    parent_recs0 = records.object_records(data, parent_off0)
    _container_template(data, parent_recs0, position is not None)  # raises if none

    # --- 1. append ------------------------------------------------------------------
    grown, edits1, new_index = _append_object(data, object_bytes)

    # --- 2. attach ------------------------------------------------------------------
    gs1, _ = guidata.guidata_extent(grown)
    grown_table = guidata.object_tables(grown)[OBJECT_TABLE_INDEX]
    parent_off = gs1 + grown_table[parent_index]
    recs = records.object_records(grown, parent_off)
    if grown[parent_off] >= 0xFF:
        raise InsertError("parent record count is already at its single-byte maximum")

    record_bytes, template = _container_template(grown, recs, position is not None)
    clone = bytearray(record_bytes)
    struct.pack_into("<H", clone, _child_index_field_offset(grown, template), new_index)
    if position is not None:
        x, y = position
        struct.pack_into("<H", clone, _geom_field_offset(grown, template, 0), x)
        struct.pack_into("<H", clone, _geom_field_offset(grown, template, 1), y)
    newrec = bytes(clone)
    rl = len(newrec)

    # Padding-correct insertion (see docstring): net delta absorbs the parent's old trailing pad.
    def round4(n: int) -> int:
        return (n + 3) & ~3

    at = recs[-1].end - gs1                         # section-rel, right after the last record
    c_old = recs[-1].end - parent_off               # parent content length (count byte + records)
    oldpad = (-c_old) % 4
    newpad = (-(c_old + rl)) % 4
    target = newrec + b"\x00" * newpad              # the exact bytes the object must end with
    delta = round4(c_old + rl) - round4(c_old)      # net bytes inserted (a multiple of 4)

    # Prepare `tmp`: bump the parent's record count and overwrite the parent's old pad region
    # with the head of `target`; `relocate` then inserts the remaining `target[oldpad:]` (delta
    # bytes) at the object boundary. Both pre-edits sit before the insertion point and are
    # resealed by `relocate` along with everything else.
    tmp = bytearray(grown)
    old_count = tmp[parent_off]
    tmp[parent_off] = old_count + 1
    pre_edits = [Edit(parent_off, bytes([old_count]), bytes([old_count + 1]), "record-count",
                      f"parent {parent_index} record count {old_count} -> {old_count + 1}")]
    if oldpad:
        head_pos = gs1 + at
        old_head = bytes(tmp[head_pos:head_pos + oldpad])
        tmp[head_pos:head_pos + oldpad] = target[:oldpad]
        pre_edits.append(Edit(head_pos, old_head, target[:oldpad], "record-head",
                              f"parent {parent_index} attach-record head into old pad"))

    result, edits2 = relocate.relocate(bytes(tmp), at + oldpad, target[oldpad:])

    # --- accumulate edits in FINAL (result) coordinates -----------------------------
    # relocate inserted delta bytes at `attach_ins`; remap the append-step edits and the
    # pre-edits that sit at or past it. edits2 are already in result coordinates.
    attach_ins = gs1 + at + oldpad

    def remap(e: Edit) -> Edit:
        if e.file_offset >= attach_ins:
            return Edit(e.file_offset + delta, e.old, e.new, e.structure, e.note)
        return e

    edits = [remap(e) for e in edits1] + [remap(e) for e in pre_edits] + list(edits2)
    edits.sort(key=lambda e: e.file_offset)
    return result, edits


# --------------------------------------------------------------------------------------
# graph helper (used by tests; cheap and self-contained)
# --------------------------------------------------------------------------------------
def object_child_graph(data: bytes) -> dict[int, list[int]]:
    """`{object index: sorted child_index list}` over object-table 1 -- the decoded object
    graph. Objects that fail to decode are omitted (they are not part of the reachable graph);
    this is a *view for equivalence checks*, not a validator (the boundary oracle is that).
    """
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[OBJECT_TABLE_INDEX]
    graph: dict[int, list[int]] = {}
    for index, start in enumerate(table):
        try:
            recs = records.object_records(data, gs + start)
        except Exception:  # noqa: BLE001 - a non-decoding object simply isn't in the view
            continue
        children = sorted(r.child_index for r in recs if r.child_index is not None)
        graph[index] = children
    return graph
