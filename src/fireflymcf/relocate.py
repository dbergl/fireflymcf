"""Insert bytes into GuiData and repair every downstream reference.

its design note (not published) (Phase-1 spike, run with
`python3 -m fireflymcf.relocator_scan`) established that a GuiData record embeds
**no live absolute self-pointer** -- the only `0x900xxxxx` words a relocator must repair
are the four object-pointer tables (`guidata.object_tables`), which carry 94.5-97.2% of
every self-pointer in the corpus, plus a small, fully-known header table directory
(`docs/MCF-FORMAT.md` S5.1, `proven-from-code`). So the whole operation is mechanical:
shift a table entry whose target moved, shift a header offset whose table moved, grow the
container fields that describe GuiData's own length and every later section's position,
then reseal. `relocate` never parses or interprets a record body -- only the enumerated
tables/offsets/container fields above.

The four things that move, each cited:

1. **Object-pointer table entries** (`guidata.object_tables`): every entry whose target
   section-offset is `>= at` gains `len(payload)`. A table's own storage position also
   moves if it sits at or after `at` -- both are tracked, independently, per entry.
2. **GuiData's header table directory** (`docs/MCF-FORMAT.md` S5.1, `coverage.py`'s
   `GUIDATA_HEAD_SIZE`/`GUIDATA_DESCRIPTOR_SIZE`): each 8-byte descriptor's `offset` field
   gains `len(payload)` if the table it declares starts at or after `at`. Splitting a
   declared table's body (inserting strictly inside `[offset, offset+size)`) is refused --
   Task 1 only supports insertion at a table boundary, never mid-table.
3. **The container** (`container.py`): GuiData's own descriptor payload length (`+0x4C`)
   always gains `len(payload)`; every section whose payload file offset is *after*
   GuiData's (`+0x48`) gains it too.
4. **Reseal** (`patch.reseal`): the GuiData payload CRC always changes; header/descriptor
   CRCs reseal only if their covered bytes did.

**Self-check, entry-level, all four tables.** Four gates run on the finished bytes before
they are returned, and any one failing raises `RelocationError`:

1. `_verified_pointer_tables` -- a table's lowest pointer must equal the file offset
   immediately following the table's own words. Cheap, and checked going in (before a
   single shift is computed) as well as on the output. **This alone is not enough**: it
   is a fact about one entry per table (whichever holds the minimum), so a wrong shift on
   any of a table's *other* entries passes it silently.
2. `coverage.analyse(result, deep=True)` -- every object span's start is exactly where
   the *previous* entry in the same table says it ends, so a mis-shifted entry usually
   moves an `object:` claim's boundary into an unclaimed gap or an overlap
   (`coverage.CoverageError`, or a plain `ValueError` from `coverage.claim`'s own overlap
   guard). **Also not enough on its own**: each table's *trailing* object (the one entry
   with no next entry to be checked against, `coverage.py`'s own "weakest claim in the
   module") is bounded only by a heuristic ("no bigger than any object this table already
   delimits") with tens of thousands of bytes of slack in this corpus -- a one-byte error
   there does not move that bound past its heuristic limit, so it passes both gate 1 and
   gate 2 silently. Proven empirically, Task-1 quality re-review round 2, 2026-08-14:
   shifting table 0's or table 3's maximum-target entry (their trailing object, in both
   cases) by `K+1` instead of `K` passes gates 1 and 2 and both would return successfully.
3. **`_verify_table_shifts`** -- the gate that actually closes it. Re-reads every
   object-pointer table entry fresh from the output and compares it, independently of
   `_new_target` or any other of `relocate`'s own intermediate state, against the plain
   formula this whole module implements (`old + K` if `old >= at`, else `old` unchanged).
   Because it is an exact value comparison rather than a structural inference, it has no
   heuristic blind spot: it is what catches the trailing-entry corruption gates 1 and 2
   miss, for every entry of all four tables, not only the ones with a hard neighbour-to-
   neighbour boundary.
4. `_boundary_oracle` -- re-decodes every object in `guidata.object_tables(result)[1]`
   that has a next boundary (`records.object_records`, the same oracle
   `tests/test_records.py` runs over the whole corpus with zero derailments). A
   content-level check beyond what coverage claims structurally; the other three tables
   have no known `<count><records>` shape to support it.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from . import container, coverage, guidata, memo, patch, records, theme

# The boundary-oracle table: the object/screen table validated end-to-end by
# `tests/test_records.py` (1421/1076/1200 objects, zero derailments across the corpus).
# The other three tables (image sub-container, the 30-entry nested one, the 3295-entry
# image table) have no known `<count><records>` shape, so only this one is re-checked.
OBJECT_TABLE_INDEX = 1


class RelocationError(ValueError):
    """`relocate` would produce, or has produced, a GuiData that fails a correctness gate.

    Raised rather than returning a build that only looks right -- same discipline as
    `guidata.UnknownRecord`/`patch.PatchError`: an assumption this module depends on (no
    mid-table split, one of the four self-check gates still passing) turned out false, so
    it stops instead of guessing.
    """


@dataclass(frozen=True)
class Edit:
    """One byte range `relocate` changed in the OUTPUT image (post-insertion), and why.

    `old`/`new` are almost always equal length -- a same-size value rewrite at a shifted
    position, same spirit as `patch.Edit`. The one exception is the inserted span itself,
    whose `old` is empty: there is no prior content to have replaced.
    """
    file_offset: int
    old: bytes
    new: bytes
    structure: str
    note: str

    @property
    def length(self) -> int:
        return len(self.new)

    @property
    def end(self) -> int:
        return self.file_offset + self.length


def _new_target(value: int, k: int) -> int:
    """The single shift arithmetic every category below uses: `value + k`.

    Kept as its own function -- not inlined -- so a test can monkeypatch it to inject a
    wrong shift and confirm a self-check gate actually catches it
    (`tests/test_relocate.py::test_a_wrong_shift_trips_some_self_check_gate`,
    `::test_a_wrong_shift_on_a_trailing_entry_is_caught_only_by_verify_table_shifts`).
    """
    return value + k


def _verified_pointer_tables(data: bytes) -> list[tuple[int, int, list[int]]]:
    """Every object-pointer table in `data`'s GuiData section, as `coverage._pointer_tables`
    finds them: `(table_start_abs, table_end_abs, pointers_abs)`.

    This is the one place `relocate` learns where a table's own words live in the file --
    it is what makes an entry's *storage position* recoverable without rescanning bytes
    (`table_start_abs`), not just its target value. `coverage._pointer_tables` asserts the
    self-terminating invariant (a table's lowest pointer equals the run's own end) for
    EVERY run it finds, not only table 1 -- so if that invariant is violated for table 0,
    2 or 3 (the three `_boundary_oracle` never re-decodes), this raises before `relocate`
    computes a single shift from it, let alone writes one. Routing every table-bounds
    lookup through this one function (rather than each caller re-deriving
    `min(table) - 4*len(table)`, as an earlier revision did in two places) is what makes
    that guarantee automatic instead of something each call site had to remember to check.
    """
    gs, ge = guidata.guidata_extent(data)
    try:
        return coverage._pointer_tables(data, gs, ge)
    except coverage.CoverageError as exc:
        raise RelocationError(
            f"object-pointer table structure does not hold: {exc}") from exc


def object_table_bounds(data: bytes, index: int) -> tuple[int, int]:
    """Section-relative `(start, end)` of object-pointer table `index`'s own byte span --
    the pointer array itself, not the objects it addresses. See `_verified_pointer_tables`.
    """
    gs, _ = guidata.guidata_extent(data)
    tables = _verified_pointer_tables(data)
    if not 0 <= index < len(tables):
        raise RelocationError(
            f"no object-pointer table at index {index}; found {len(tables)}")
    start_abs, end_abs, _ = tables[index]
    return start_abs - gs, end_abs - gs


def _guidata_directory(data: bytes, gs: int) -> list[tuple[int, int, int, int]]:
    """`(descriptor_file_offset, tag, table_offset_rel, table_size)` for every 8-byte
    descriptor in GuiData's header table directory (`docs/MCF-FORMAT.md` S5.1).

    Walked the same self-terminating way `coverage._claim_guidata` does: the loop stops
    where the first descriptor says its own table begins, rather than trusting the u8
    entry count at `+0x07` (which this module does not read). The struct format
    `<HHHH>` reads the `offset`/`size` u16s at descriptor-relative `+4`/`+6` correctly
    regardless of whether the leading 4 bytes are one u16 tag or `<u8 tag><u8 b1 b2 b3>`
    (`MCF-FORMAT.md`'s correction of the tag width) -- both land `offset`/`size` in the
    same place, which is all this function reads.
    """
    first_tag, _, first_offset, _ = struct.unpack_from(
        "<HHHH", data, gs + coverage.GUIDATA_HEAD_SIZE)
    descriptor_end = gs + first_offset
    out: list[tuple[int, int, int, int]] = []
    at = gs + coverage.GUIDATA_HEAD_SIZE
    while at < descriptor_end:
        tag, _, offset, size = struct.unpack_from("<HHHH", data, at)
        out.append((at, tag, offset, size))
        at += coverage.GUIDATA_DESCRIPTOR_SIZE
    if out and out[0][1] != first_tag:
        raise RelocationError("GuiData directory walk disagrees with its first entry")
    return out


@memo.content_cached(maxsize=16384, copy=None)
def _standalone_end(chunk: bytes) -> int | None:
    """Where the records of the object whose bytes are `chunk` end, decoding `chunk` as a buffer
    of its own (relative offset), or None if the decode raises.

    The record decoder reads forward from the object's first byte and nothing else, so a decode
    that succeeds inside `chunk` reads exactly the bytes it would read in place. Memoised on the
    object's bytes: a relocation shifts objects without changing them, so after the first gate
    only the objects an op touched are decoded again."""
    try:
        recs = records.object_records(chunk, 0)
    except Exception:  # noqa: BLE001 - the caller re-decodes in place to report it
        return None
    return recs[-1].end if recs else 1


def _boundary_oracle(data: bytes) -> tuple[bool, str]:
    """Zero-derailment check over every `OBJECT_TABLE_INDEX` object with a known next
    boundary -- the same oracle `tests/test_records.py::test_every_object_decodes_to_its_boundary`
    runs over the whole corpus. Returns `(ok, reason)`; `reason` is empty when `ok`.
    """
    try:
        gs, ge = guidata.guidata_extent(data)
        tables = guidata.object_tables(data)
    except Exception as exc:  # noqa: BLE001 - report the failure, do not hide it
        return False, f"could not re-read GuiData structure: {exc!r}"
    if len(tables) <= OBJECT_TABLE_INDEX:
        return False, (f"expected at least {OBJECT_TABLE_INDEX + 1} object tables, "
                       f"found {len(tables)}")
    offs = sorted(set(p for p in tables[OBJECT_TABLE_INDEX] if 0 < p < (ge - gs)))
    nxt = {a: b for a, b in zip(offs, offs[1:])}
    checked = 0
    for o in offs:
        if o not in nxt:
            continue
        fo, end = gs + o, gs + nxt[o]
        rel = _standalone_end(data[fo:end])
        if rel is not None and 0 <= (end - fo) - rel < 4:
            checked += 1
            continue
        # Not settled inside the object's own bytes: decode in place, which is the verdict and
        # the message this gate has always given.
        try:
            recs = records.object_records(data, fo)
        except Exception as exc:  # noqa: BLE001 - a decode failure IS the guard firing
            return False, f"object at {fo:#x} raised {exc!r} while decoding"
        pos = recs[-1].end if recs else fo + 1
        gap = end - pos
        if not (0 <= gap < 4):
            return False, (f"object {fo:#x} ended at +{pos - fo} (gap {gap} to its "
                           f"next boundary {nxt[o]:#x}) -- read order/offsets are off")
        checked += 1
    if checked == 0:
        return False, "no object in the table had a next boundary to check against"
    return True, ""


def _verify_record_geometry(data: bytes) -> tuple[bool, str]:
    """Reject records whose geometry encodes a box with negative extent.

    This catches the class of authoring bug that shipped an invisible "Triggers:"
    title to the coach (2026-08-25): a record built with base-mode 0 (absolute) on
    BOTH an axis position and its extent, but whose extent word was authored as a
    width/height. With absolute extent, `geom[2]`/`geom[3]` are read as absolute
    x1/y1, so a "height" of 26 under a y of 110 yields y1 < y0 -- an inverted box the
    software renderer happily draws but the panel does not (see the
    `firefly-renderer-not-a-hardware-oracle` note / docs/HARDWARE-VERIFIED.md).

    The check is deliberately LOCAL to each record's own encoding, not the resolved
    on-screen box: `layout.Screen.walk` legitimately produces x1 < x0 / y1 < y0 for
    ~36 stock records positioned with negative pen offsets, so a resolved-extent test
    would false-positive. An axis is only checked when BOTH its position mode
    (`base_modes[0]`/`[1]`) and its extent mode (`base_modes[2]`/`[3]`) are absolute
    (0) -- the only case in which `geom[2]`/`geom[3]` are absolute coordinates that
    must not precede the position. Verified to flag 0 records across stock 13.6.

    Returns `(ok, reason)`; `reason` is empty when `ok`.
    """
    try:
        gs, ge = guidata.guidata_extent(data)
        tables = guidata.object_tables(data)
    except Exception as exc:  # noqa: BLE001 - report the failure, do not hide it
        return False, f"could not re-read GuiData structure: {exc!r}"
    if len(tables) <= OBJECT_TABLE_INDEX:
        return False, (f"expected at least {OBJECT_TABLE_INDEX + 1} object tables, "
                       f"found {len(tables)}")
    offs = sorted(set(p for p in tables[OBJECT_TABLE_INDEX] if 0 < p < (ge - gs)))
    for o in offs:
        fo = gs + o
        try:
            recs = records.object_records(data, fo)
        except Exception:  # noqa: BLE001 - the boundary oracle owns decode failures
            continue
        for i, r in enumerate(recs):
            bm = getattr(r, "base_modes", None)
            g = getattr(r, "geom", None)
            if not bm or not g:
                continue
            if (bm[0] == 0 and bm[2] == 0 and g[0] is not None
                    and g[2] is not None and g[2] < g[0]):
                return False, (f"object {fo:#x} record {i} (wtype {r.wtype}): absolute "
                               f"x-extent {g[2]} precedes x {g[0]} -- negative-width box")
            if (bm[1] == 0 and bm[3] == 0 and g[1] is not None
                    and g[3] is not None and g[3] < g[1]):
                return False, (f"object {fo:#x} record {i} (wtype {r.wtype}): absolute "
                               f"y-extent {g[3]} precedes y {g[1]} -- negative-height box "
                               "(the inverted-box bug: authored a height as an absolute y1)")
    return True, ""


def _deep_coverage_ok(data: bytes) -> tuple[bool, str]:
    """Whole-section, structural check: `coverage.analyse(data, deep=True)` must claim
    every byte of GuiData with no gap and no overlap. Returns `(ok, reason)`.

    This is broader than `_verified_pointer_tables` (which only asserts that a table's
    LOWEST pointer equals the table's own end -- a fact about one entry, not the other
    N-1): here, every object's span is bounded by *its own* table entry against its
    *neighbour's*, so a wrong entry usually shifts a claimed boundary into a gap or an
    overlap. `coverage.CoverageError` is a `ValueError` subclass, and `coverage.claim`'s
    own overlap guard raises a plain `ValueError` too, so catching `ValueError` catches
    both without also catching whatever unrelated exception a caller might introduce by
    mistake at a different layer.

    **Still not sufficient by itself.** Each table's one *trailing* object (no next entry
    to be checked against; `coverage.py`'s own "weakest claim in the module") is bounded
    only by a heuristic with a large slack in this corpus, so a small error there does not
    trip this gate -- proven empirically (Task-1 quality re-review round 2, 2026-08-14):
    shifting object-table 0's or table 3's maximum-target entry (in both cases, the
    trailing object) by `K+1` instead of `K` passes this check. `_verify_table_shifts` is
    what closes that gap; this function stays as a real, independent structural gate
    (checksums, directory offsets, container fields, every non-trailing entry) rather than
    being replaced by it.
    """
    try:
        report = coverage.analyse(data, deep=True)
    except ValueError as exc:
        return False, f"deep coverage raised: {exc!r}"
    if report.unclaimed:
        first_start, first_len = report.unclaimed[0]
        return False, (f"deep coverage left {len(report.unclaimed)} span(s) unclaimed, "
                       f"first {first_len} byte(s) at {first_start:#x}")
    if report.claimed != report.total:
        return False, (f"deep coverage claimed {report.claimed} of {report.total} bytes "
                       f"but reported no unclaimed spans -- inconsistent report")
    return True, ""


def _verify_table_shifts(before: bytes, after: bytes, at: int, k: int) -> tuple[bool, str]:
    """The gate that actually closes the entry-level gap `_deep_coverage_ok` cannot:
    re-read every object-pointer table entry fresh from `after` (`guidata.object_tables`,
    a completely separate code path from the `_verified_pointer_tables`-based loop that
    computed the shifts) and compare it against the plain formula this whole module
    implements -- `old + k` if `old >= at`, else `old` unchanged -- computed here again,
    independently, not by calling `_new_target`. Returns `(ok, reason)`.

    Because this is an exact value comparison rather than a structural inference from
    neighbouring claims, it has no heuristic blind spot: it is what catches a wrong shift
    on a table's *trailing* entry (proven, Task-1 quality re-review round 2, 2026-08-14:
    `K+1` instead of `K` on object-table 0's and table 3's maximum-target/trailing entry
    passes both `_verified_pointer_tables` and `coverage.analyse(deep=True)` silently, but
    not this). It is, in effect, `tests/test_relocate.py`'s own
    `test_object_table_targets_shift_by_exactly_k_or_not_at_all` promoted from an external
    assertion to something `relocate` guarantees for every caller, not only the ones under
    test.
    """
    before_tables = guidata.object_tables(before)
    after_tables = guidata.object_tables(after)
    if len(before_tables) != len(after_tables):
        return False, (f"object-table count changed: {len(before_tables)} -> "
                       f"{len(after_tables)}")
    for ti, (b_table, a_table) in enumerate(zip(before_tables, after_tables)):
        if len(b_table) != len(a_table):
            return False, (f"table {ti}: entry count changed {len(b_table)} -> "
                           f"{len(a_table)}")
        for ei, (old, new) in enumerate(zip(b_table, a_table)):
            expected = old + k if old >= at else old
            if new != expected:
                return False, (f"table {ti} entry {ei}: {old:#x} -> {new:#x}, "
                               f"expected {expected:#x}")
    return True, ""


def _verify_guicode_pointers(before: bytes, after: bytes, at: int,
                             delta: int) -> tuple[bool, str]:
    """Fifth gate (insertion only): every GuiCode absolute pointer into GuiData must, in `after`,
    address the SAME GuiData content it did in `before`.

    Re-disassembles the OUTPUT (`after`) independently of the forward relocation and checks, for
    each GuiCode literal: its position is unchanged (GuiCode sits before GuiData and never moves),
    and its target gained exactly `delta` if it was `>= at`, or is unchanged if below `at`. A
    literal the forward pass forgot to bump would still read its old target here and fail; a
    wrong delta fails; a literal set that is not stable across the edit fails. This catches the
    boot-loop failure mode -- a GuiCode pointer left aiming at shifted content -- that the four
    GuiData-only gates cannot see.

    Note it checks the pointer VALUE, not the bytes it addresses: several of these pointers name a
    GuiData *pointer table*, whose own entries are relocated by the same insertion, so the bytes at
    the target legitimately change. The value invariant is the correct, composition-safe one -- a
    later insertion re-runs this gate against the already-shifted frame.

    **KNOWN LIMIT, pre-existing and deliberately not closed here (noted while auditing the
    2026-09-06 relaxation).** For a SHRINK (`delta < 0`, which `replace_object_records` can
    produce), a literal whose target falls in the removed range `[at, at + |delta|)` is bumped to
    `rel + delta`, which lands below `at`, and this gate accepts it -- the arithmetic is what the
    gate asks for, but the pointer now names content that no longer exists. It is not closed
    because a GuiCode pointer into the middle of an object whose records are being replaced is
    already broken by the replacement itself, whatever this gate says; catching it belongs in
    `replace_object_records` (refuse a shrink that swallows a referenced address), not here. No
    build in this project has hit it: 13.6's 27 literals name the four pointer tables and a few
    object-data addresses, none inside an object this project rewrites.
    """
    # Both maps are keyed by WHOLE-FILE offset, and the checks below read the word at that offset
    # out of BOTH images, so the two must agree on where GuiCode is. That has always been the
    # gate's premise ("GuiCode sits before GuiData and never moves") and it holds -- an insertion
    # into GuiData leaves GuiCode's descriptor untouched. It is asserted rather than assumed
    # because the consequence of it being false is a silent misread rather than a mismatch:
    # the scan-artifact rule below compares words across the two images, and reading them from
    # different sections would compare unrelated bytes.
    b_sec, a_sec = theme.guicode_section(before), theme.guicode_section(after)
    if (b_sec.offset, b_sec.length) != (a_sec.offset, a_sec.length):
        return False, (f"GuiCode moved or resized across the edit "
                       f"({b_sec.offset:#x}+{b_sec.length} -> {a_sec.offset:#x}+{a_sec.length}); "
                       f"every literal position in this gate would be meaningless")

    before_ptrs = dict(theme.guicode_guidata_pointers(before))
    after_ptrs = dict(theme.guicode_guidata_pointers(after))

    # A position seen in only ONE of the two scans is a disassembly artifact, not necessarily a
    # missed relocation -- the scan is a linear sweep, and this project's standing rule is that
    # instruction bytes collide with the `0x9xxxxxxx` range and produce false positives. Two
    # conditions together make such a position provably harmless, and BOTH are required:
    #
    #   * its word is byte-identical in `before` and `after` -- the forward pass did not touch
    #     it, so nothing was half-relocated; and
    #   * its target is BELOW `at` -- content there did not move, so a pointer to it must NOT be
    #     bumped and is still correct however the scan classified it.
    #
    # A position at or above `at` that only one scan saw is exactly the dangerous case (a real
    # pointer the forward pass never bumped, left aiming at shifted content), and still refuses.
    #
    # Found 2026-09-06 building v21: dropping one op moved GuiData enough that the sweep exposed
    # a bogus `ldr` naming code offset 0x12508, whose word `0x9003b200` is stock, unchanged, and
    # sits mid-instruction-stream (`sxth r0,r0` / `str r0,[sp,#0xc]`). Target 0x3b200, insertion
    # at 0x6d804: below it, so untouched and correct either way.
    only_one = set(before_ptrs) ^ set(after_ptrs)
    for pos in sorted(only_one):
        rel = before_ptrs.get(pos, after_ptrs.get(pos))
        word_before = struct.unpack_from("<I", before, pos)[0]
        word_after = struct.unpack_from("<I", after, pos)[0]
        if word_before != word_after:
            return False, (f"GuiCode literal @ {pos:#x} is seen by only one scan AND its word "
                           f"changed ({word_before:#010x} -> {word_after:#010x})")
        if rel >= at:
            return False, (f"GuiCode literal @ {pos:#x} (target {rel:#x}, at or above the "
                           f"insertion point {at:#x}) is seen by only one scan -- it may be a "
                           f"real pointer the forward pass never bumped")
    for pos, rel_before in before_ptrs.items():
        if pos not in after_ptrs:
            continue
        rel_after = after_ptrs[pos]
        expected = rel_before + delta if rel_before >= at else rel_before
        if rel_after != expected:
            return False, (f"GuiCode literal @ {pos:#x}: target {rel_before:#x} -> "
                           f"{rel_after:#x}, expected {expected:#x}")
    return True, ""


def _verify_no_stranded_lcd_pointers(data: bytes, at: int) -> tuple[bool, str]:
    """The LCD Firmware section flashes independently of GuiData/GuiCode (the boot menu loads it
    separately), so an insertion cannot relocate its pointers -- the panel keeps stock LCD
    Firmware. If any LCD pointer into GuiData targets `>= at`, the insertion would strand it
    (stock LCD dereferences shifted content) -> an unfixable boot loop, so the relocator must
    refuse. On 13.6 LCD Firmware only references GuiData's base (rel 0), which never shifts, so
    this passes; it is a guard against a future build/version where that stops being true.
    """
    for _pos, rel in theme.section_guidata_pointers(data, "LCD"):
        if rel >= at:
            return False, (f"LCD Firmware references GuiData+{rel:#x} (>= insertion {at:#x}); "
                           f"LCD is not reflashed with GuiData, so that pointer would be "
                           f"stranded on the panel -- refusing an unfixable insertion")
    return True, ""


def _apply_downstream_shift(data: bytes, out: bytearray, at: int,
                            delta: int) -> list[Edit]:
    """Bump every downstream reference for an insertion of `delta` bytes at GuiData-relative
    `at`, writing the shifted values into `out` and returning the `Edit`s that describe them.

    `data` is the **pre-insertion** image -- the source of truth for the object-pointer tables,
    the header directory and the container. `out` is the **post-insertion** bytearray the caller
    has already built as `data[:ins] + payload + data[ins:]` (with `ins == gs + at` and
    `len(payload) == delta`); this function writes the shifted values into it at their moved
    positions. It does **not** touch the inserted payload bytes, reseal, or run any self-check --
    the caller owns the insertion `Edit`, the reseal, and the validation gates. The one thing it
    enforces is the mid-table-split refusal (a `RelocationError`), because that is a property of
    the shift itself, not of any particular caller.

    This is the single copy of the shift arithmetic -- object-table entries, header-directory
    offsets, and the two kinds of container descriptor field -- shared by `relocate` (which
    inserts a payload keeping every table's entry count) and `insert._append_object` (which
    grows object-table 1 by one entry). Both walk the four object-pointer tables in the same
    order, so the sequence of `_new_target` calls is identical for both, which is what keeps the
    `_new_target` monkeypatch guard tests meaningful for the shared path. Threading the shift
    delta through `_new_target` (rather than inlining `value + delta`) preserves that injection
    hook.
    """
    gs, _ = guidata.guidata_extent(data)
    ins = gs + at

    def moved(pos: int) -> int:
        return pos + delta if pos >= ins else pos

    edits: list[Edit] = []

    # 1. object-pointer table entries: every entry whose target is >= at gains delta.
    for table_start_abs, _table_end_abs, pointers_abs in _verified_pointer_tables(data):
        for i, target_abs in enumerate(pointers_abs):
            target_rel = target_abs - gs
            if target_rel >= at:
                new_pos = moved(table_start_abs + 4 * i)
                new_val = _new_target(target_rel, delta)
                old_bytes = struct.pack("<I", guidata.OBJECT_POINTER_BASE + target_rel)
                new_bytes = struct.pack("<I", guidata.OBJECT_POINTER_BASE + new_val)
                struct.pack_into("<I", out, new_pos, guidata.OBJECT_POINTER_BASE + new_val)
                edits.append(Edit(new_pos, old_bytes, new_bytes, "object-table-entry",
                                  f"target {target_rel:#x} -> {new_val:#x}"))

    # 2. header table directory: bump an offset, or refuse a mid-table split.
    for entry_at, tag, offset, size in _guidata_directory(data, gs):
        if offset >= at:
            new_pos = moved(entry_at + 4)
            new_off = _new_target(offset, delta)
            struct.pack_into("<H", out, new_pos, new_off)
            edits.append(Edit(new_pos, struct.pack("<H", offset), struct.pack("<H", new_off),
                              "guidata-directory-offset",
                              f"table offset {offset:#x} -> {new_off:#x}"))
        elif size and offset + size > at:
            raise RelocationError(
                f"insertion at GuiData+{at:#x} falls inside header table (tag "
                f"{tag:#04x}) declared at [{offset:#x}, {offset + size:#x}); relocate "
                f"only supports insertion at a table boundary, never mid-table")

    # 3. container: GuiData's own length, and every later section's offset.
    c = container.parse_container(data)
    guidata_section = next(s for s in c.sections
                           if guidata.GUIDATA_SECTION_MARKER in s.name)
    descriptor_offsets = container._descriptor_offsets(
        data, c.section_count, c.header_word_20)
    for section, desc_at in zip(c.sections, descriptor_offsets):
        if section.index == guidata_section.index:
            pos = desc_at + container.DESC_EXTENT_OFFSET + 4              # +0x4C length
            old_val = section.length
        elif section.offset > guidata_section.offset:
            pos = desc_at + container.DESC_EXTENT_OFFSET                   # +0x48 offset
            old_val = section.offset
        else:
            continue
        new_pos = moved(pos)
        new_val = _new_target(old_val, delta)
        struct.pack_into("<I", out, new_pos, new_val)
        edits.append(Edit(new_pos, struct.pack("<I", old_val), struct.pack("<I", new_val),
                          "container-descriptor-field", f"{old_val:#x} -> {new_val:#x}"))

    # 4. GuiCode's absolute pointers into GuiData. GuiCode references several GuiData structures
    #    (the four pointer tables and a few object-data addresses) by absolute value; it sits
    #    before GuiData in the file, so each literal's position is unchanged, but a literal whose
    #    target is >= at must gain delta or the panel dereferences shifted content at boot (a boot
    #    loop -- the failure mode relocator_scan.py originally missed by only checking GuiData ->
    #    GuiData references, never GuiCode -> GuiData). Requires capstone; without it an insertion
    #    cannot be made runtime-safe, so the scan raises rather than emit an unsafe build.
    for lit_pos, target_rel in theme.guicode_guidata_pointers(data):
        if target_rel >= at:
            old_val = guidata.OBJECT_POINTER_BASE + target_rel
            new_val = guidata.OBJECT_POINTER_BASE + _new_target(target_rel, delta)
            struct.pack_into("<I", out, lit_pos, new_val)
            edits.append(Edit(lit_pos, struct.pack("<I", old_val), struct.pack("<I", new_val),
                              "guicode-pointer",
                              f"GuiCode ptr into GuiData {target_rel:#x} -> "
                              f"{_new_target(target_rel, delta):#x}"))

    return edits


def relocate(data: bytes, at: int, payload: bytes) -> tuple[bytes, list[Edit]]:
    """Insert `payload` into GuiData at section-relative offset `at`.

    Returns a new MCF with every object-pointer table entry, header directory offset and
    container field that names a position `>= at` bumped by `len(payload)`, checksums
    resealed, and four self-check gates re-verified on the output -- plus the `Edit` list
    describing every byte range that changed and why (Task 3's generalised-L3 ladder
    consumes it).

    `at == len(guidata)` (append at the section's very end) and `payload == b""` (a no-op
    that returns `data` unchanged, byte-identical) are both valid. Raises `RelocationError`
    if `at` is out of range, if it would split a header-declared table's body, or if the
    resulting build fails any of the four self-check gates (`_verified_pointer_tables`,
    `_deep_coverage_ok`, `_verify_table_shifts`, `_boundary_oracle` -- see the module
    docstring for what each one catches that the others cannot) -- this function never
    returns a build it has not itself re-verified, entry by entry, across all four
    object-pointer tables.
    """
    gs, ge = guidata.guidata_extent(data)
    span = ge - gs
    if not 0 <= at <= span:
        raise RelocationError(
            f"insertion offset {at:#x} is outside GuiData [0, {span:#x}]")
    k = len(payload)
    if k == 0:
        return data, []

    ins = gs + at  # absolute file offset of the insertion point

    # Insert the payload, then shift every downstream reference (object-table entries, header
    # directory offsets, container fields) by K via the shared `_apply_downstream_shift` -- the
    # same helper `insert._append_object` uses, so the two never drift. `relocate` keeps every
    # table's entry count; the shift's mid-table-split refusal is enforced inside the helper.
    out = bytearray(data[:ins] + payload + data[ins:])
    edits: list[Edit] = [Edit(ins, b"", bytes(payload), "guidata-insertion",
                              f"inserted {k} byte(s) at GuiData+{at:#x}")]
    edits.extend(_apply_downstream_shift(data, out, at, k))

    grown = bytes(out)

    # --- reseal ------------------------------------------------------------------
    checksum_positions = {f.label: f.position for f in patch.checksum_fields(grown)}
    resealed, changed_labels = patch.reseal(grown)
    for label in changed_labels:
        pos = checksum_positions[label]
        edits.append(Edit(pos, grown[pos:pos + 2], resealed[pos:pos + 2],
                          "checksum", f"resealed {label}"))

    # --- self-check ----------------------------------------------------------------
    # Four gates, re-derived fresh from `resealed` (never carried over from the
    # input-side state `table_entries` etc. were built from), covering ALL FOUR
    # object-pointer tables between them, entry by entry:
    #  (a) every table's self-terminating invariant still holds (cheap, but only a fact
    #      about each table's minimum-valued entry);
    #  (b) coverage.analyse(deep=True) claims every byte with no gap and no overlap --
    #      broad, but blind to a small error on each table's one trailing entry (see
    #      _deep_coverage_ok's docstring);
    #  (c) _verify_table_shifts -- the exact, independent, entry-by-entry re-derivation
    #      that closes (b)'s trailing-entry gap;
    #  (d) table 1's objects still decode byte-exact to their boundaries.
    _verified_pointer_tables(resealed)
    ok, reason = _deep_coverage_ok(resealed)
    if not ok:
        raise RelocationError(
            f"relocate produced a build that fails deep coverage: {reason}")
    ok, reason = _verify_table_shifts(data, resealed, at, k)
    if not ok:
        raise RelocationError(
            f"relocate produced a build with a wrong object-table shift: {reason}")
    ok, reason = _boundary_oracle(resealed)
    if not ok:
        raise RelocationError(
            f"relocate produced a build that fails the boundary oracle: {reason}")
    ok, reason = _verify_guicode_pointers(data, resealed, at, k)
    if not ok:
        raise RelocationError(
            f"relocate produced a build whose GuiCode points into shifted GuiData "
            f"(would boot-loop): {reason}")
    ok, reason = _verify_no_stranded_lcd_pointers(data, at)
    if not ok:
        raise RelocationError(f"relocate: {reason}")

    edits.sort(key=lambda e: e.file_offset)
    return resealed, edits


def _object_footprint(data: bytes, obj_index: int) -> tuple[int, int, int]:
    """`(start_abs, next_abs, count_byte_offset)` for object `obj_index` in object-table
    `OBJECT_TABLE_INDEX` -- the full on-disk footprint `[start_abs, next_abs)` (leading count
    byte + records + trailing pad-to-4).

    `start_abs` is the absolute file offset of the object's **leading count byte** (what
    object-table 1 stores, and what `records.object_records` is handed). `next_abs` is the next
    object's boundary -- the smallest table-1 entry strictly greater than this one, or, for the
    top-address object, the start of the next pointer table (its region boundary). Both are
    4-byte aligned, so `next_abs - start_abs` is always a multiple of 4. This is the exact
    recipe `insert._object_span` uses, kept here so the shrink/replace primitive needs no import
    of `insert` (which would be circular).
    """
    gs, ge = guidata.guidata_extent(data)
    tables = _verified_pointer_tables(data)
    if not 0 <= OBJECT_TABLE_INDEX < len(tables):
        raise RelocationError(f"no object table at index {OBJECT_TABLE_INDEX}")
    table = guidata.object_tables(data)[OBJECT_TABLE_INDEX]
    if not 0 <= obj_index < len(table):
        raise RelocationError(
            f"object index {obj_index} out of range [0, {len(table)})")
    start_rel = table[obj_index]
    higher = [v for v in table if v > start_rel]
    if higher:
        next_rel = min(higher)
    else:
        after = [ts - gs for ts, _, _ in tables if ts - gs > start_rel]
        next_rel = min(after) if after else (ge - gs)
    if next_rel <= start_rel:
        raise RelocationError(
            f"object {obj_index} has an empty footprint [{start_rel:#x}, {next_rel:#x}) -- "
            f"a repeated pointer; not a replaceable object")
    return gs + start_rel, gs + next_rel, gs + start_rel


def replace_object_records(data: bytes, obj_index: int,
                           new_body: bytes) -> tuple[bytes, list[Edit]]:
    """Replace object `obj_index`'s record stream in place -- at its own object index -- with
    `new_body`, handling a size DELTA of either sign (the key case is a SHRINK: a compact frame
    is smaller than the object it replaces). Returns `(sealed, edits)`.

    `new_body` is the full object payload -- the leading `count` byte followed by its records,
    with **no trailing pad** (this primitive re-pads to the 4-byte object boundary itself). It
    replaces the current object's payload; every downstream reference (the other object-pointer
    table entries, the header directory, the container fields, and GuiCode's `ldr`-literals into
    GuiData) is shifted by the net footprint change, the checksums are resealed, and the same
    self-check gates `relocate` runs are re-verified on the output. A `delta` of 0 (a
    byte-identical body) is a valid no-op that still reseals to the identical image.

    The operation keeps object-table 1's entry count unchanged (it is a pure in-place
    relocation, never an append), so it goes through the ordinary `_verify_table_shifts` gate --
    unlike `insert._append_object`, which grows the table by one and needs the growth-aware
    analogue. The one capability `relocate.relocate` lacks is exactly this: a same-index payload
    swap of a DIFFERENT length; `relocate.relocate` only inserts, and its top-level refuses a
    replacement because it takes an insertion point, not an object span (`relocate.py:117`).

    Algorithm: locate the object's footprint `[start_abs, next_abs)` (count byte through the next
    object boundary, pad included); pad `new_body` up to a 4-byte boundary; build the output as
    `data[:start_abs] + padded + data[next_abs:]`; the net `delta = round4(len(new_body)) -
    (next_abs - start_abs)` is always a multiple of 4, keeping every following pointer table
    aligned; shift every downstream reference by `delta` at the object boundary
    (`_apply_downstream_shift`, whose shift math accepts a negative `delta`); reseal; then assert
    every gate. Raises `RelocationError` on a bad index, an empty/malformed `new_body`, or any
    gate failure -- it never returns an unsealed or ungated image.
    """
    if not new_body:
        raise RelocationError("new_body is empty; an object needs at least its count byte")

    gs, _ = guidata.guidata_extent(data)
    start_abs, next_abs, _ = _object_footprint(data, obj_index)
    old_footprint = next_abs - start_abs                 # a multiple of 4 (object boundaries)
    at = next_abs - gs                                   # section-relative object boundary

    pad = (-len(new_body)) % 4
    padded = new_body + b"\x00" * pad                    # re-pad to the 4-byte object boundary
    delta = len(padded) - old_footprint                  # net footprint change; a multiple of 4

    # Swap the object's whole footprint (payload + old pad) for the re-padded new body, then let
    # `_apply_downstream_shift` bump every reference that names a position at or after the object
    # boundary by `delta`. It reads the pre-image `data` for the reference values and writes the
    # shifted values into `out` at their moved positions; it never touches the swapped span, so
    # a same-index replacement is equivalent to its insertion model as long as no reference lives
    # inside an object payload (none does -- tables/directory/container/GuiCode all precede it).
    out = bytearray(data[:start_abs] + padded + data[next_abs:])
    edits: list[Edit] = [Edit(start_abs, bytes(data[start_abs:next_abs]), bytes(padded),
                              "object-replace",
                              f"replaced object {obj_index} payload "
                              f"({old_footprint} -> {len(padded)} bytes, delta {delta:+d})")]
    edits.extend(_apply_downstream_shift(data, out, at, delta))

    grown = bytes(out)

    # --- reseal ------------------------------------------------------------------
    checksum_positions = {f.label: f.position for f in patch.checksum_fields(grown)}
    resealed, changed_labels = patch.reseal(grown)
    for label in changed_labels:
        pos = checksum_positions[label]
        edits.append(Edit(pos, grown[pos:pos + 2], resealed[pos:pos + 2],
                          "checksum", f"resealed {label}"))

    # --- self-check ----------------------------------------------------------------
    # The same gates `relocate.relocate` runs, re-derived fresh from `resealed`, covering all
    # four object-pointer tables between them. `_verify_table_shifts` applies unchanged because
    # a replacement preserves every table's entry count (only entry VALUES shift, by `delta`).
    _verified_pointer_tables(resealed)
    ok, reason = _deep_coverage_ok(resealed)
    if not ok:
        raise RelocationError(
            f"replace_object_records produced a build that fails deep coverage: {reason}")
    ok, reason = _verify_table_shifts(data, resealed, at, delta)
    if not ok:
        raise RelocationError(
            f"replace_object_records produced a build with a wrong object-table shift: {reason}")
    ok, reason = _boundary_oracle(resealed)
    if not ok:
        raise RelocationError(
            f"replace_object_records produced a build that fails the boundary oracle: {reason}")
    ok, reason = _verify_guicode_pointers(data, resealed, at, delta)
    if not ok:
        raise RelocationError(
            f"replace_object_records produced a build whose GuiCode points into shifted GuiData "
            f"(would boot-loop): {reason}")
    ok, reason = _verify_no_stranded_lcd_pointers(data, at)
    if not ok:
        raise RelocationError(f"replace_object_records: {reason}")

    edits.sort(key=lambda e: e.file_offset)
    return resealed, edits


def grow_match_table(data: bytes, values: list[int]) -> tuple[bytes, list[Edit]]:
    """Append u16 match-value entries for the NEXT `len(values)` objects to be appended.

    The match table (`guidata.find_match_table`) holds one u16 per object and ends exactly
    where object-table 1 begins, so an object appended by `insert._append_object` would read an
    object-table word as its match value. Growing the table first gives objects
    `n .. n+len(values)-1` the values given, in order, where `n = len(guidata.object_tables(
    data)[1])` -- the index the NEXT `insert._append_object` call will assign. The insertion
    goes through `relocate`, so every pointer table, the directory and the GuiCode literals are
    repaired and the five gates run. `len(values)` must be even: object offsets are 4-aligned.

    `n` can already be AHEAD of the match table's own current physical width `w` -- on an image
    where objects were appended (via `insert._append_object`) since the table was last grown (or
    since stock), `find_match_table`'s own docstring notes exactly this: appending an object
    grows object-table 1 but never this table, so `w < n` is the normal state of a build with
    prior appends, not a corruption. Using `n` (not `w`) as the insertion point -- what an
    earlier revision of this function did -- lands the insert `(n - w)` u16s INSIDE object-table
    1's own pointer words instead of at the match table's real end, corrupting the table (caught
    downstream as a wrong object-table shift, not raised here). The fix inserts `(n - w)`
    `0xFFFF` filler entries -- for the un-covered, already-appended objects `w .. n-1`, none of
    which is ever read as a switch arm's match value, since `0xFFFF` is the format's own "no
    match" sentinel (`layout.Screen._match_value`/`find_match_table`'s hard filter both treat it
    as valid) -- immediately followed by `values`, so `values` still lands at slots `n ..
    n+len(values)-1` exactly as documented.

    The total insert length must stay a 4-aligned (even u16 count). `gap = n - w`'s parity is a
    property of the WHOLE preceding op chain (however many un-tracked appends happened before
    this call), not something a caller here controls -- so an odd `gap + len(values)` is a legal
    situation, not refused: ONE extra trailing `0xFFFF` is appended after `values`, restoring
    4-alignment. That harmlessly pre-covers index `n + len(values)` -- the firmware's own
    default-arm marker (a switch with no matching arm falls back to the base object when ITS
    match value is `0xFFFF`, per `layout.Screen._switch_child`), so a future object landing
    there that never needed a real entry costs nothing.

    That trailer means the table CAN be wider than the object count: an earlier grow that
    needed it leaves `w == n + 1` until the next object is appended, so `gap < 0` is a legal
    state (found 2026-09-24: v32's Batt Heat switch grew four entries after an odd gap, and the
    next grow in the same build was refused). The extra entries `n .. w-1` are exactly those
    pre-covering `0xFFFF` sentinels, so they are OVERWRITTEN in place with the first `values`
    and only the rest is inserted.

    Raises only for a genuinely impossible state: a table wider than the object count whose
    extra entries are NOT all `0xFFFF` -- `find_match_table` found the wrong window.
    """
    if not values or len(values) % 2:
        raise RelocationError(
            f"grow_match_table needs an even, non-empty count of entries (4-aligned insert); "
            f"got {len(values)}")
    for v in values:
        if not 0 <= v <= 0xFFFF:
            raise RelocationError(f"match value {v!r} is not a u16")
    mt = guidata.find_match_table(data)
    if mt is None:
        raise RelocationError("no match table located in this image")
    n = len(guidata.object_tables(data)[1])
    tbl1_start, _tbl1_end = object_table_bounds(data, OBJECT_TABLE_INDEX)
    w = (tbl1_start - mt) // 2           # the table's own current physical width
    gap = n - w
    gs, _ = guidata.guidata_extent(data)
    edits: list[Edit] = []
    covered = 0
    if gap < 0:
        extra = [struct.unpack_from("<H", data, gs + mt + 2 * i)[0] for i in range(n, w)]
        if any(v != 0xFFFF for v in extra):
            raise RelocationError(
                f"grow_match_table: match table width {w} exceeds the object count {n} and the "
                f"extra entries are not pre-covering 0xFFFF sentinels -- find_match_table likely "
                f"located the wrong window")
        covered = min(-gap, len(values))
        buf = bytearray(data)
        pos = gs + mt + 2 * n
        old = bytes(buf[pos:pos + 2 * covered])
        buf[pos:pos + 2 * covered] = b"".join(struct.pack("<H", v) for v in values[:covered])
        edits.append(Edit(pos, old, bytes(buf[pos:pos + 2 * covered]), "match-table",
                          f"overwrote {covered} pre-covered sentinel(s) at index {n}"))
        data, _ = patch.reseal(bytes(buf))
        gap = 0
    rest = values[covered:]
    if not rest and gap == 0:
        return data, edits
    trailer = 1 if (gap + len(rest)) % 2 else 0      # one extra 0xFFFF keeps the insert 4-aligned
    at = mt + 2 * w
    payload = (b"\xff\xff" * gap + b"".join(struct.pack("<H", v) for v in rest)
              + b"\xff\xff" * trailer)
    out, more = relocate(data, at, payload)
    if guidata.find_match_table(out) != mt:
        raise RelocationError("the match table moved while being grown; refusing the result")
    return out, edits + more
