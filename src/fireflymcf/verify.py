"""Generalised L3: prove an accumulated `Edit` list explains **every** byte an
insertion+retarget pipeline changed, with nothing left over and nothing fitted.

Task 3's primary correctness surface. The original L3 (`patch.verify`) asserts a *same-size*
patch's diff is contained in its edits; it cannot handle an image that changed **length**,
which is exactly what `insert.insert_object` does. This module generalises it: given the
stock input, the final output (longer, because bytes were inserted), and the accumulated
`Edit` list (Task 1's `relocate.Edit`s from the insert **plus** the same-size `patch.Edit`s
from retargeting the clone), it proves the edits are both **complete** (they account for
every differing byte) and **legitimate** (each one is a recognised structural change, not an
arbitrary write), deriving the expected-diff set analytically from the edits — no
hand-maintained allowlist.

## The two proofs

1. **Reconstruct-and-compare (completeness + correctness).** Rebuild the final image from
   `stock` and the edit list alone: insert every payload (`old == b""`) at its offset, then
   replay every other edit's `new` at its offset in list order. The result must equal `final`
   byte-for-byte. This single assertion proves the edits *exactly* reproduce the
   transformation — any unexplained differing byte, or any edit with a wrong value, makes the
   reconstruction diverge. It also transparently handles a position carrying **two edits**
   (an object-table/container/checksum field shifted once by the append and again by the
   attach, or a checksum resealed by the insert and again by the retarget): replaying in list
   order lets the last edit win, which is the carried-forward "last edit per position is
   authoritative" rule applied uniformly, not just to checksums.

2. **Per-edit categorisation (legitimacy).** Every edit must be exactly one recognised kind:
   an **inserted span** (`old == b""`); an **object-table entry / header-directory offset /
   container field shifted by an insertion length** (value delta ∈ the payload lengths); a
   **resealed checksum** (in a `patch.checksum_fields` position); the parent's **record-count**
   byte; or a **same-size payload edit landing inside the inserted object's own span** (the
   clone retarget — a rename or a reposition, which by construction can only touch the new
   object). Anything else raises. This is what stops a "reconstruction happens to match"
   answer that reached `final` through some edit the pipeline should never have made.

Then, as the brief phrases it, the differing-byte set (`stock`-with-insertions vs `final`) is
computed explicitly and every position asserted to fall in exactly one edit's authoritative
range — a redundant restatement of proof 1 in the brief's own terms, kept because a redundant
independent check is cheap and this is the load-bearing gate.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import patch

# Structural `Edit.structure` tags this module recognises, grouped by category. These are the
# exact strings `relocate`/`insert` stamp on their `Edit`s (`relocate._apply_downstream_shift`,
# `insert._append_object`), so the categorisation reads them rather than re-deriving intent.
INSERT_STRUCTS = frozenset({"object-insertion", "guidata-insertion"})
SHIFT_STRUCTS = frozenset({"object-table-entry", "guidata-directory-offset",
                           "container-descriptor-field", "guicode-pointer"})
# "guicode-pointer": a GuiCode literal that holds an absolute pointer into GuiData, bumped by the
# insertion length so it tracks its (shifted) target -- a reference shift like the others, so it
# is categorised the same way (its old->new delta must equal an insertion length). Without this,
# GuiCode dereferences shifted content at boot (a boot loop); see relocate._verify_guicode_pointers.
RECORD_COUNT_STRUCT = "record-count"
CHECKSUM_STRUCT = "checksum"
# `insert.insert_object` emits this when the parent's content was not 4-aligned (oldpad in 1..3):
# the new attach record starts inside the parent's old trailing pad, so its first `oldpad` bytes
# overwrite that pad in place. It is a same-size overwrite of pad (all-zero old bytes) whose end
# abuts the record insertion — not a shift, checksum, count, or clone retarget.
RECORD_HEAD_STRUCT = "record-head"


class VerifyError(AssertionError):
    """The edit list does not fully and legitimately explain `stock` -> `final`.

    An `AssertionError` subclass: this is a verification gate, and a failure here means a build
    that must not be trusted (an unexplained byte, a mis-valued edit, or an edit touching
    something outside the inserted object). Raised rather than returning a status, matching the
    fail-closed discipline of `relocate.RelocationError` / `patch.PatchError`.
    """


@dataclass(frozen=True)
class L3Report:
    """What generalised L3 accounted for. Every field is a fact it proved, not a summary."""
    grew_by: int                       # len(final) - len(stock)
    inserted_spans: list               # [(offset, length)] of every payload span
    differing_bytes: int               # positions where stock-with-insertions != final
    counts: dict                       # category -> number of edits in it


def _apply_insertions(stock: bytes, insertions: list) -> bytes:
    """`stock` with every payload inserted at its (final-image) offset.

    Processed in ascending offset order: each payload is spliced at its final offset into the
    progressively grown image, which is correct because a later insertion's offset already
    accounts for every earlier insertion's length (they are final-image coordinates). The
    result — call it S' — is stock as it would look with the bytes inserted but *no* downstream
    reference repaired; proof 1 then transforms S' into `final` with the remaining edits.
    """
    out = bytearray(stock)
    for offset, payload in sorted(insertions, key=lambda t: t[0]):
        if not 0 <= offset <= len(out):
            raise VerifyError(f"insertion offset {offset:#x} outside [0,{len(out):#x}]")
        out[offset:offset] = payload
    return bytes(out)


def generalized_l3(stock: bytes, final: bytes, edits: list) -> L3Report:
    """Prove `edits` completely and legitimately explains `stock` -> `final`. Raise otherwise.

    `edits` is the accumulated list in **application order** (insert edits, then the same-size
    retarget edits) — the order `relocate_demo.build_new_button` hands back. Returns an
    `L3Report` of what was accounted for; raises `VerifyError` on any unexplained byte, any edit
    whose value is wrong, or any edit that is not a recognised structural change.
    """
    if len(final) <= len(stock):
        raise VerifyError(
            f"final ({len(final)}) is not longer than stock ({len(stock)}); this checker is "
            f"for a length-growing insert+retarget pipeline")

    insertions = [(e.file_offset, bytes(e.new)) for e in edits if len(e.old) == 0]
    if not insertions:
        raise VerifyError("no insertion edit (old == b'') in the list")
    insertion_lengths = {len(p) for _, p in insertions}

    grew_by = len(final) - len(stock)
    if sum(len(p) for _, p in insertions) != grew_by:
        raise VerifyError(
            f"insertion payloads total {sum(len(p) for _, p in insertions)} bytes but the "
            f"image grew by {grew_by}")

    # --- proof 1: reconstruct-and-compare -------------------------------------------
    grown = _apply_insertions(stock, insertions)
    if len(grown) != len(final):
        raise VerifyError(
            f"stock-with-insertions is {len(grown)} bytes, final is {len(final)}")
    rebuilt = bytearray(grown)
    for e in edits:
        if len(e.old) == 0:
            continue                                    # insertions already applied
        if len(e.old) != len(e.new):
            raise VerifyError(
                f"non-insertion edit at {e.file_offset:#x} changes length "
                f"{len(e.old)}->{len(e.new)}")
        rebuilt[e.file_offset:e.file_offset + len(e.new)] = e.new
    if bytes(rebuilt) != final:
        first = next(i for i, (a, b) in enumerate(zip(rebuilt, final)) if a != b)
        raise VerifyError(
            f"edit list does not reconstruct final; first unexplained/mis-valued byte at "
            f"{first:#x} (rebuilt {rebuilt[first]:#02x}, final {final[first]:#02x})")

    # --- proof 2: per-edit categorisation -------------------------------------------
    # The inserted OBJECT span (the clone) — retarget edits may only land here. It is the
    # object-insertion payload: a 4-byte pointer word followed by the cloned object's bytes.
    obj_ins = [(o, p) for o, p in insertions
               if any(e.file_offset == o and e.structure == "object-insertion"
                      and len(e.old) == 0 for e in edits)]
    if len(obj_ins) != 1:
        raise VerifyError(
            f"expected exactly one 'object-insertion' edit, found {len(obj_ins)}")
    clone_start, clone_payload = obj_ins[0]
    clone_end = clone_start + len(clone_payload)

    checksum_positions = {f.position for f in patch.checksum_fields(final)}
    insertion_offsets = {o for o, _ in insertions}
    counts = {"insertion": 0, "shift": 0, "checksum": 0, "record-count": 0,
              "record-head": 0, "retarget": 0}

    for e in edits:
        if len(e.old) == 0:
            if e.structure not in INSERT_STRUCTS:
                raise VerifyError(
                    f"insertion at {e.file_offset:#x} has unrecognised structure "
                    f"{e.structure!r}")
            counts["insertion"] += 1
        elif e.structure in SHIFT_STRUCTS:
            delta = (int.from_bytes(e.new, "little") - int.from_bytes(e.old, "little"))
            if delta not in insertion_lengths:
                raise VerifyError(
                    f"{e.structure} at {e.file_offset:#x} shifted by {delta}, not one of the "
                    f"insertion lengths {sorted(insertion_lengths)}")
            counts["shift"] += 1
        elif e.structure == CHECKSUM_STRUCT:
            if e.file_offset not in checksum_positions:
                raise VerifyError(
                    f"checksum edit at {e.file_offset:#x} is not a stored-checksum position")
            counts["checksum"] += 1
        elif e.structure == RECORD_COUNT_STRUCT:
            if len(e.new) != 1:
                raise VerifyError(
                    f"record-count edit at {e.file_offset:#x} is {len(e.new)} bytes, not 1")
            counts["record-count"] += 1
        elif e.structure == RECORD_HEAD_STRUCT:
            # The attach record's head overwriting the parent's old trailing pad: 1..3 bytes,
            # the overwritten bytes must be pad (all zero), and the edit must end exactly at an
            # insertion point (the record bytes relocate then inserts). Any other shape means the
            # head landed somewhere it should not.
            if not 1 <= len(e.new) <= 3:
                raise VerifyError(
                    f"record-head edit at {e.file_offset:#x} is {len(e.new)} bytes, not 1..3")
            if any(b != 0 for b in e.old):
                raise VerifyError(
                    f"record-head edit at {e.file_offset:#x} overwrote non-pad bytes "
                    f"{e.old.hex()}; only zero pad may be overwritten")
            if e.file_offset + len(e.new) not in insertion_offsets:
                raise VerifyError(
                    f"record-head edit at {e.file_offset:#x} does not abut an insertion point "
                    f"{sorted(insertion_offsets)}")
            counts["record-head"] += 1
        else:
            # The only remaining legitimate edit is a same-size retarget of the clone (rename
            # or reposition). It MUST land inside the inserted object's own span — that is what
            # proves the retarget touched the new button and nothing else.
            if not (clone_start <= e.file_offset and e.file_offset + len(e.new) <= clone_end):
                raise VerifyError(
                    f"edit at {e.file_offset:#x} ({e.structure!r}) is neither a shift, a "
                    f"checksum, a record-count, nor inside the inserted object span "
                    f"[{clone_start:#x},{clone_end:#x}); it is unexplained")
            counts["retarget"] += 1

    # --- brief's explicit restatement: every differing byte in exactly one edit range ------
    # `grown` (stock-with-insertions) vs `final`. Every position that differs must be covered
    # by some non-insertion edit; the reconstruction above already guarantees it, so this is a
    # second, independent statement of the same fact in the brief's terms.
    covered: set = set()
    for e in edits:
        if len(e.old) == 0:
            continue
        covered.update(range(e.file_offset, e.file_offset + len(e.new)))
    differing = [o for o in range(len(final)) if grown[o] != final[o]]
    unexplained = [o for o in differing if o not in covered]
    if unexplained:
        raise VerifyError(
            f"{len(unexplained)} differing byte(s) not covered by any edit, first at "
            f"{unexplained[0]:#x}")

    return L3Report(grew_by=grew_by,
                    inserted_spans=[(o, len(p)) for o, p in sorted(insertions)],
                    differing_bytes=len(differing), counts=counts)
