"""Writing to an MCF container: same-size edits only.

This is the first writer in a project that has been read-only by policy. Its value is in
what it refuses, not in what it can do.

**The invariant.** Every edit is byte-for-byte the same length as what it replaces
(`PatchError` otherwise). GuiData is full of absolute `0x900xxxxx` pointers into itself --
the object tables, and at `+0x44BC8` a run that points into SRAM and must be fixed up at
load by code nobody has located. Inserting or deleting a single byte would invalidate every
one of them past the edit. Forbidding length changes does not make relocation a *hard*
problem, it makes it a *nonexistent* one, and unlike "we were careful" it is mechanically
checkable.

**What resealing costs.** An MCF carries nine stored checksums -- one header, one per
section descriptor, one per section payload (27 across the three-build corpus). A same-size
payload edit invalidates exactly one of them: the payload CRC of the section touched. The
header and descriptor CRCs cover bytes a payload edit does not reach. `reseal` recomputes
all nine and rewrites those that differ; the caller then asserts the differing set was the
predicted one.

**What this module does not know.** No located code draws a label, so whether trailing
spaces affect rendering is unknown; see `repad`. Nothing here relies on the answer.

Confidence: the container and checksum geometry are `proven-from-code`
(`docs/PARSER-LOCATION.md`); the CRC algorithm and the label record layout are
`inferred-from-data` (`docs/MCF-FORMAT.md` §3, §6).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field as dc_field

from . import container, coverage, guidata, memo, records, theme

# The label record is <u8 length><length bytes><NUL>, so a field cannot exceed 255 bytes
# and the length byte cannot be further back than that from any byte inside the field.
MAX_LABEL_FIELD = 0xFF

# Bytes a label field may contain. Newline is in because MCF-FORMAT.md §6 records that a
# label may hold one; the rest is printable ASCII. This is a *validity* test used to reject
# false-positive length bytes, not a claim about the renderer's character set.
_LABEL_BYTES = frozenset(range(0x20, 0x7F)) | {0x0A}


class PatchError(ValueError):
    """A patch would violate an invariant this module guarantees."""


# --------------------------------------------------------------------------- checksums


@dataclass(frozen=True)
class ChecksumField:
    """One stored CRC-16/AUG-CCITT field: where it sits and what it covers."""
    position: int
    covered_start: int
    covered_end: int
    label: str

    def computed(self, data: bytes) -> int:
        return coverage.crc16_aug_ccitt(data[self.covered_start:self.covered_end])

    def stored(self, data: bytes) -> int:
        return struct.unpack_from("<H", data, self.position)[0]


def checksum_fields(data: bytes) -> list[ChecksumField]:
    """Every stored checksum in the container, with the span each covers.

    Positions mirror `coverage.analyse` exactly -- the same three geometries the firmware
    itself verifies (`docs/PARSER-LOCATION.md`): the header CRC at `hdr[0x04]-4` over the
    header and pointer array, a CRC at `+size-4` of each descriptor, and each section's CRC
    at `(length-4) & ~1` over `length-4` payload bytes. Duplicating the arithmetic rather
    than importing it would let the two drift, so this function is checked against
    `coverage.analyse` in the tests.
    """
    c = container.parse_container(data)
    header_size = c.header_word_20
    fields: list[ChecksumField] = []

    header_at = (c.header_word_04 & 0xFFFF) - coverage.HEADER_CRC_BACKOFF
    fields.append(ChecksumField(header_at, 0, header_at, "header-checksum"))

    for i in range(c.section_count):
        descriptor_at = struct.unpack_from("<I", data, header_size + 4 * i)[0]
        size = struct.unpack_from("<H", data, descriptor_at + 2)[0]
        body = size - coverage.DESC_CRC_BACKOFF
        fields.append(ChecksumField(
            descriptor_at + body, descriptor_at, descriptor_at + body,
            f"descriptor-checksum:{i}"))

    for section in c.sections:
        if not section.length:
            continue
        at = (section.length - coverage.SECTION_CRC_BACKOFF) & ~1
        name = section.name or str(section.index)
        fields.append(ChecksumField(
            section.offset + at, section.offset, section.offset + at,
            f"section-checksum:{name}"))

    return fields


# The container's human-readable build name -- the string the USB flasher lists in its
# file-selection menu ("Jayco B-Van V13"). It is a NUL-terminated field in the header structure
# `[0x0, 0xA4)`, covered only by the header checksum (@0xB4); the section directory begins at the
# fixed offset 0xA4, so the field has 0xA4-0x24 = 128 bytes of capacity regardless of the name's
# length (an edit here never changes the file length or shifts anything).
BUILD_NAME_OFFSET = 0x24
BUILD_NAME_CAPACITY = 0xA4 - BUILD_NAME_OFFSET      # 128 bytes, must include the NUL terminator
CUSTOM_MARKER = " (Custom)"


def build_name(data: bytes) -> str:
    """The build name the flasher displays -- the NUL-terminated string at `BUILD_NAME_OFFSET`."""
    field = data[BUILD_NAME_OFFSET:BUILD_NAME_OFFSET + BUILD_NAME_CAPACITY]
    nul = field.find(b"\x00")
    return field[:nul if nul >= 0 else len(field)].decode("ascii", "replace")


def set_build_name(data: bytes, name: str) -> tuple[bytes, list[str]]:
    """Return a copy of `data` with the flasher build name set to `name`, header checksum resealed.

    `name` is written ASCII, NUL-terminated, into the fixed 128-byte header field and the rest of
    the field is zero-filled. Raises `PatchError` if it does not fit (127 chars max) or is not
    ASCII. Only the header checksum changes -- the name is length-invariant on the file.
    """
    try:
        raw = name.encode("ascii")
    except UnicodeEncodeError as exc:
        raise PatchError(f"build name {name!r} is not ASCII") from exc
    if len(raw) + 1 > BUILD_NAME_CAPACITY:
        raise PatchError(
            f"build name {name!r} is {len(raw)} chars; the field holds at most "
            f"{BUILD_NAME_CAPACITY - 1}")
    out = bytearray(data)
    out[BUILD_NAME_OFFSET:BUILD_NAME_OFFSET + BUILD_NAME_CAPACITY] = (
        raw + b"\x00" * (BUILD_NAME_CAPACITY - len(raw)))
    return reseal(bytes(out))


def mark_custom(data: bytes, marker: str = CUSTOM_MARKER) -> bytes:
    """Idempotently append `marker` to the build name so a custom build is distinguishable in the
    flasher menu. Returns `data` unchanged (same object) if the marker is already present -- so
    marking a build twice, or re-patching an already-marked build, never stacks the suffix."""
    current = build_name(data)
    if marker.strip() and marker.strip() in current:
        return data
    resealed, _ = set_build_name(data, current + marker)
    return resealed


def reseal(data: bytes) -> tuple[bytes, list[str]]:
    """Recompute every stored checksum; return the new image and the labels that changed.

    The covered spans are pairwise disjoint and no field sits inside another's span, so a
    single pass is sufficient; that property is asserted rather than assumed, because a
    nested checksum would need an innermost-first ordering and silently getting it wrong
    would produce a file that verifies here and is rejected by the panel.
    """
    fields = checksum_fields(data)
    for a in fields:
        for b in fields:
            if a is b:
                continue
            if b.covered_start <= a.position < b.covered_end:
                raise PatchError(
                    f"{a.label} at {a.position:#x} sits inside the span covered by "
                    f"{b.label} [{b.covered_start:#x},{b.covered_end:#x}); reseal would "
                    f"need an innermost-first ordering this function does not implement")

    out = bytearray(data)
    changed: list[str] = []
    for f in fields:
        computed = f.computed(bytes(out))
        if f.stored(bytes(out)) != computed:
            struct.pack_into("<H", out, f.position, computed)
            changed.append(f.label)
    return bytes(out), changed


# ------------------------------------------------------------------------- label sites


@dataclass(frozen=True)
class LabelSite:
    """One `<u8 length><length bytes><NUL>` label record, by file offset."""
    length_offset: int          # the u8 length byte
    text_offset: int            # first character of the field
    length: int                 # the field length, excluding the NUL
    text: str

    @property
    def end(self) -> int:
        """One past the NUL terminator."""
        return self.text_offset + self.length + 1


# A text/icon draw record carries a font selector `01 <font> ff` a few bytes ahead of its
# length byte, and the two u16 immediately before that selector are the element's absolute
# (x, y) pixel position. Confirmed on the coach 2026-08-14: writing the Climate Starlink
# caption's x (file 0x097233) moved the word ~320 px right (docs/MCF-FORMAT.md §6). The
# selector sits within this many bytes before the length byte across the corpus.
_FONT_SELECTOR_WINDOW = 14
SCREEN_MAX = 0x320                       # 800 px; a sanity bound on a placed coordinate


@dataclass(frozen=True)
class DrawElement:
    """A text or icon draw element and the screen position it is drawn at.

    `x`/`y` are absolute pixels. They are the two u16 immediately before the record's
    `01 <font> ff` selector -- `proven` by the hardware probe cited above, and the same
    layout holds for every label checked across the three builds (Water Pump `(100,50)`,
    Cool `(112,45)`, the Bath Fan/Starlink caption `(80,75)`).
    """
    label_offset: int           # the u8 length byte of the associated label record
    font_offset: int            # the <font> byte of the `01 <font> ff` selector
    x_offset: int
    y_offset: int
    x: int
    y: int


def find_draw_element(data: bytes, length_offset: int) -> DrawElement:
    """The draw element whose label record's length byte is at `length_offset`.

    Scans back for the single `01 <font> ff` selector and reads the `(x, y)` pair before
    it. Raises if no selector is found in range, or if two are (ambiguous) -- the same
    refuse-rather-than-guess rule `find_label_sites` uses.
    """
    lo = max(0, length_offset - _FONT_SELECTOR_WINDOW)
    anchors = [o for o in range(lo, length_offset - 2)
               if data[o] == 0x01 and data[o + 2] == 0xFF]
    if not anchors:
        raise PatchError(
            f"no `01 <font> ff` selector within {_FONT_SELECTOR_WINDOW} bytes before the "
            f"label at {length_offset:#x}; this record has no locatable draw position")
    if len(anchors) > 1:
        raise PatchError(
            f"ambiguous draw element before {length_offset:#x}: selectors at "
            f"{', '.join(hex(a) for a in anchors)}")
    font = anchors[0]
    x_off, y_off = font - 4, font - 2
    return DrawElement(
        label_offset=length_offset, font_offset=font, x_offset=x_off, y_offset=y_off,
        x=struct.unpack_from("<H", data, x_off)[0],
        y=struct.unpack_from("<H", data, y_off)[0])


def _is_label_field(data: bytes, lo: int) -> bool:
    """Could the u8 at `lo` be the length byte of a well-formed label record?"""
    length = data[lo]
    if not length or lo + 1 + length >= len(data):
        return False
    if data[lo + 1 + length] != 0x00:
        return False
    return set(data[lo + 1:lo + 1 + length]) <= _LABEL_BYTES


@memo.content_cached(copy=frozenset)
def _decoded_length_offsets(data: bytes) -> frozenset:
    """The length byte of every label record the record decoder reads, in every object."""
    gs, _ = guidata.guidata_extent(data)
    out = set()
    for table in guidata.object_tables(data):
        for obj in set(table):
            try:
                recs = records.object_records(data, gs + obj)
            except Exception:       # not every table entry is a record stream
                continue
            out.update(r.label_offset - 1 for r in recs if r.label_offset is not None)
    return frozenset(out)


def find_label_sites(data: bytes, text: str) -> list[LabelSite]:
    """Every label record in GuiData whose field contains `text`, in offset order.

    A candidate length byte is accepted only if the field it describes is NUL-terminated,
    holds only label bytes, *and* contains the hit. All three conditions together, because
    any one of them alone admits false positives in an 888 KB blob. If two candidates
    survive for one hit the site is ambiguous and this raises rather than picking one --
    guessing the length byte would corrupt the record that follows.

    Returns every site, because a rename that misses one leaves the old text on some other
    screen: "Heat Pump" occupies seven label records in 13.6.
    """
    needle = text.encode("ascii")
    start, end = guidata.guidata_extent(data)
    sites: list[LabelSite] = []
    at = start
    while True:
        hit = data.find(needle, at, end)
        if hit < 0:
            break
        at = hit + 1
        candidates = []
        for lo in range(hit - 1, max(start, hit - MAX_LABEL_FIELD) - 1, -1):
            if not _is_label_field(data, lo):
                continue
            length = data[lo]
            if lo + 1 <= hit and hit + len(needle) <= lo + 1 + length:
                candidates.append(lo)
        if not candidates:
            continue
        if len(candidates) > 1:
            # Nested candidates end at one NUL when the real length byte is itself a label
            # byte ('\n' = 10 before a 10-byte text). The record that holds the text decodes
            # its own length byte; take that one, and still refuse a tie it does not settle.
            decoded = [c for c in candidates if c in _decoded_length_offsets(data)]
            if len(decoded) == 1:
                candidates = decoded
        if len(candidates) > 1:
            raise PatchError(
                f"the label record holding {text!r} at {hit:#x} is ambiguous: length "
                f"bytes at {', '.join(hex(c) for c in candidates)} all describe a "
                f"well-formed field containing it")
        lo = candidates[0]
        length = data[lo]
        sites.append(LabelSite(
            length_offset=lo, text_offset=lo + 1, length=length,
            text=data[lo + 1:lo + 1 + length].decode("ascii")))
    return sites


def repad(field: str, old: str, new: str, length: int, align: str = "right") -> str:
    """Replace `old` with `new` inside `field`, re-padding to exactly `length` bytes.

    Padding goes on the **right**, preserving the original leading indent exactly, and only
    on the line that contained the replacement so a second line's alignment is untouched.

    **This is settled by hardware, not chosen.** The dry run of 2026-08-13 renamed the
    OPTIONS row '   Heat Pump' (3 leading spaces) to '     12v aux' (5), and the panel drew
    it indented two characters further right than the original -- so the renderer reproduces
    leading spaces literally and left-aligns. Padding on the left therefore *moves* every
    renamed label by the difference in name length, which is a visible defect. Padding on
    the right leaves the label's start position identical, and trailing spaces on a
    left-aligned string are invisible. `MCF-FORMAT.md` §6 and the design spec both record
    the observation.

    A longer name still consumes the indent when it has to: `align="left"` is kept for that,
    and because a rename must stay invertible -- restoring '     12v aux' to '   Heat Pump'
    has to give two spaces back, so re-padding works in both directions.

    **`align="center"` exists because the observation above is about LEFT-aligned labels.**
    A record whose R49 h-align byte is 2 centres each line by the width of the string it was
    given, pad included, so trailing spaces are not invisible there -- they drag the visible
    text left by half the pad. Owner-observed on the coach 2026-08-21: the caption
    'Overflow\\nValve   ', padded to its 17-byte donor field, drew its second line 9 px left of
    centre next to a 'Water\\nPump' that needed no padding and looked right. Centre mode splits
    the pad around the last drawn line instead, which lands the word within half a space of
    true centre ('  Valve ' measures to 38 px against a centre of 37). Exact is not reachable:
    the pad here is odd and every relocating growth is +4, so it stays odd. The odd space goes
    on the LEFT, which is the closer of the two -- a leading run is measured with the string's
    own start kerning, making ' Valve  ' land at 32 against '  Valve ' at 38.
    """
    if align in ("centre", "center"):
        align = "center"
    if align not in ("right", "left", "center"):
        raise PatchError(f"align must be 'right', 'left' or 'center', not {align!r}")
    if old not in field:
        raise PatchError(f"{old!r} does not occur in {field!r}")
    # The lines `old` spans are one unit: 'Water\nPump' -> 'Pump' crosses a newline.
    at = field.index(old)
    target, last = field.count("\n", 0, at), field.count("\n", 0, at + len(old))
    lines = field.split("\n")
    lines[target:last + 1] = ["\n".join(lines[target:last + 1])]
    lines[target] = lines[target].replace(old, new)

    # Decompose the changed line into indent + core + trail and re-solve for the two
    # padding runs. Expressing it this way is what makes a rename invertible: growing takes
    # the trailing run back before it touches the indent, so right-padding then renaming
    # back reproduces the original field exactly, indent included.
    line = lines[target]
    core = line.strip(" ")
    indent = len(line) - len(line.lstrip(" "))
    trail = len(line) - indent - len(core)
    target_len = len(line) + (length - len("\n".join(lines)))

    if align == "center":
        # Split the slack around the LAST drawn line of the core. `new` may itself contain
        # newlines (a one-line donor field relabelled to a two-line caption), and only the
        # final line can carry trailing pad, so that is the line to balance. A single-line
        # core is its own last line, which is what makes this right for both shapes.
        pad = target_len - len(core)
        if pad < 0:
            new_indent = new_trail = 0          # falls through to the size check below
        else:
            head, _, last = core.rpartition("\n")
            lead, trail_n = (pad + 1) // 2, pad // 2      # odd space to the left; see docstring
            lines[target] = (head + "\n" if head or "\n" in core else "") \
                + " " * lead + last + " " * trail_n
            out = "\n".join(lines)
            if len(out) != length:
                raise PatchError(f"re-padded {out!r} is {len(out)} bytes, expected {length}")
            return out
    elif align == "right":
        new_indent = indent
        new_trail = target_len - new_indent - len(core)
        if new_trail < 0:                       # give up indent, but only as much as needed
            new_indent = max(0, new_indent + new_trail)
            new_trail = 0
    else:
        new_trail = trail
        new_indent = target_len - new_trail - len(core)
        if new_indent < 0:
            new_trail = max(0, new_trail + new_indent)
            new_indent = 0

    if new_indent + len(core) + new_trail != target_len:
        raise PatchError(
            f"replacing {old!r} with {new!r} in {field!r} needs "
            f"{len(core) - target_len} byte(s) more than the {length}-byte field holds, "
            f"and only {indent} space(s) of indent and {trail} of trailing pad are "
            f"available to give up; this module never grows a record")
    lines[target] = " " * new_indent + core + " " * new_trail

    out = "\n".join(lines)
    if len(out) != length:
        raise PatchError(f"re-padded {out!r} is {len(out)} bytes, expected {length}")
    return out


# ------------------------------------------------------------------------------ patcher


@dataclass(frozen=True)
class Edit:
    """One same-size byte replacement, and the structure it lands in."""
    file_offset: int
    old: bytes
    new: bytes
    structure: str
    note: str

    @property
    def end(self) -> int:
        return self.file_offset + len(self.old)


# The main object table (relocate.OBJECT_TABLE_INDEX); named here so patch does not import
# relocate. It is table index 1 in every build (`guidata.object_tables`).
_OBJECT_TABLE_INDEX = 1
# Box-drawing widget types whose fillA mode-7 index is the button's visible colour: wtype-33
# (rounded button / indicator box) and wtype-4 (rectangle). Preferred over any other record that
# happens to carry a mode-7 fillA.
_BOX_WTYPES = (33, 4)


def find_button_box(data: bytes, obj_index: int, role: str = "fillA") -> records.WRecord:
    """The record inside object `obj_index` whose `role` fill is a mode-7 colour index — the
    button's visible box. Prefers a box wtype (33/4); raises `PatchError` if the object has no
    such record or more than one candidate remains ambiguous.

    This is the recolour counterpart of `find_draw_element` (move) and `guidata.ControlGroup`
    (rechannel): it locates the exact record a `set_fill_color` edit targets, decoded from the
    bytes rather than by a hardcoded offset."""
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[_OBJECT_TABLE_INDEX]
    if not 0 <= obj_index < len(table):
        raise PatchError(f"object index {obj_index} out of range [0, {len(table)})")
    recs = records.object_records(data, gs + table[obj_index])
    boxes = [r for r in recs
             if any(c[0] == role and c[1] == 7 for c in r.colors)]
    if not boxes:
        raise PatchError(
            f"object {obj_index} has no record with a mode-7 {role} colour to recolour")
    preferred = [r for r in boxes if r.wtype in _BOX_WTYPES]
    chosen = preferred or boxes
    if len(chosen) != 1:
        raise PatchError(
            f"object {obj_index} has {len(chosen)} candidate {role} box records "
            f"(wtypes {[r.wtype for r in chosen]}); ambiguous which to recolour")
    return chosen[0]


# The only build with a committed GuiCode bridge dump (`status_map.load_bridge`): the map from
# a status variable to the cells its block writes. The indicator retarget needs it.
BRIDGE_DUMP_BUILD = "section-checksum:Lyra 13v6 GuiCode"


def indicator_sites(data: bytes, obj: int,
                    cells: set[int]) -> list[tuple[int, int, int, str]]:
    """Every u16 in object `obj`'s records that names one of `cells` through the `+0x44BC8`
    table -- a mode-7 colour index or a value_slot. `(file_offset, index, cell, what)`.

    The widget side of the status chain (`MCF-FORMAT.md` §5.4c): which pin a control's
    indicator shows is decided by these names, not by any field beside the control.
    """
    from . import status_map                     # status_map imports cli, which imports this
    gs, _ = guidata.guidata_extent(data)
    table = status_map.cell_table(data)
    out: list[tuple[int, int, int, str]] = []
    for rec in records.object_records(data, gs + guidata.object_tables(data)[1][obj]):
        for role, mode, index in rec.colors:
            if mode == 7 and 0 <= index < len(table) and table[index] in cells:
                out.append((records.color_offset(data, rec, role), index, table[index],
                            f"wtype-{rec.wtype} {role}"))
        slot = rec.value_slot
        if slot is not None and 0 <= slot < len(table) and table[slot] in cells:
            out.append((records.value_slot_offset(data, rec), slot, table[slot],
                        f"wtype-{rec.wtype} value_slot"))
    return out


class McfPatcher:
    """Accumulate same-size edits against an MCF image, then build a resealed copy.

    The input buffer is never mutated; `build` returns a new `bytes`.
    """

    def __init__(self, data: bytes) -> None:
        self._original = bytes(data)
        self._edits: list[Edit] = []
        self._spans: list[tuple[int, int, str]] | None = None
        self._claimed_spares: set[int] = set()     # +0x44BC8 indices this patcher has pointed

    # -- invariant I3: an edit must land inside a structure the reader already claims.
    def _structure_at(self, offset: int, length: int) -> str:
        if self._spans is None:
            self._spans = sorted(coverage.analyse(self._original, deep=True).spans)
        for start, size, label in self._spans:
            if start <= offset and offset + length <= start + size:
                return label
        raise PatchError(
            f"[{offset:#x}+{length:#x}] is not contained in any single structure the "
            f"coverage pass claims; an edit here is either straddling two structures or "
            f"computed against the wrong base")

    def _add(self, offset: int, old: bytes, new: bytes, note: str) -> None:
        if len(old) != len(new):                                    # I1
            raise PatchError(
                f"edit at {offset:#x} would change the length from {len(old)} to "
                f"{len(new)}; this module only makes same-size edits")
        if self._original[offset:offset + len(old)] != old:
            raise PatchError(
                f"edit at {offset:#x} expected {old!r} but the image holds "
                f"{self._original[offset:offset + len(old)]!r}")
        for existing in self._edits:                                # I2
            if offset < existing.end and existing.file_offset < offset + len(old):
                raise PatchError(
                    f"edit at [{offset:#x}+{len(old):#x}] overlaps an earlier edit at "
                    f"[{existing.file_offset:#x}+{len(existing.old):#x}]")
        structure = self._structure_at(offset, len(old))            # I3
        if structure.startswith(("padding", "header-checksum", "descriptor-checksum",
                                 "section-checksum")):
            raise PatchError(
                f"refusing to edit {structure} at {offset:#x}: checksum fields are "
                f"derived by reseal, and padding carries no structure to change")
        if old != new:
            self._edits.append(Edit(offset, old, new, structure, note))

    def set_label(self, site: LabelSite, old: str, new: str,
                  align: str = "right") -> None:
        """Rewrite `site`'s field, replacing `old` with `new` and re-padding to fit.

        `align` defaults to 'right', which preserves the indent the panel draws; see
        `repad` for the hardware observation that settled it.
        """
        field = repad(site.text, old, new, site.length, align)
        self._add(site.text_offset, site.text.encode("ascii"), field.encode("ascii"),
                  f"label {site.text!r} -> {field!r}")

    def set_position(self, element: DrawElement,
                     x: int | None = None, y: int | None = None) -> None:
        """Move a draw element to absolute pixel `(x, y)`, rewriting only the coords given.

        Each u16 is a same-size edit. Note two things the hardware probes established
        (`docs/MCF-FORMAT.md` §6): a control's icon, caption, box and touch region are
        *separate* records, so moving one element moves only that element -- repositioning
        a whole button means moving each of its elements; and the drawn box and the touch
        target are independent, so a visual move leaves the hit area behind.
        """
        for value, offset, axis in ((x, element.x_offset, "x"),
                                    (y, element.y_offset, "y")):
            if value is None:
                continue
            if not 0 <= value <= SCREEN_MAX:
                raise PatchError(
                    f"{axis}={value} is outside a plausible 0..{SCREEN_MAX} screen range; "
                    f"pass a pixel coordinate")
            old = self._original[offset:offset + 2]
            self._add(offset, old, struct.pack("<H", value),
                      f"element {axis} {struct.unpack('<H', old)[0]} -> {value} "
                      f"(near label {element.label_offset:#x})")

    def set_fill_color(self, box: records.WRecord, *,
                       color: tuple[int, int, int] | None = None,
                       index: int | None = None, role: str = "fillA") -> None:
        """Recolour a button by repointing `box`'s mode-7 `role` fill index (a same-size u16 edit).

        Give exactly one of `color=(r,g,b)` or `index=N`. `color` is resolved to a firmware-legal
        index via `theme.resolve_fill_index` (preferring a static/invariant cell), so only colours
        the firmware palette already defines are accepted; `index` names a cell directly and is
        verified to resolve to a real colour. A colour/index the palette cannot name raises
        `theme.PaletteError` -- the writer never points a fill at an undefined colour.

        `box` is the record `find_button_box` returns. The mode-7 index is the *colour*, not the
        colour value; the value lives in a GuiCode-written SRAM cell (`theme.mode7_palette`), which
        is why a same-size index swap is the whole edit -- no GuiData colour bytes move.
        """
        target = theme.resolve_fill_index(self._original, color=color, index=index)
        off = records.color_offset(self._original, box, role)
        if off is None:
            raise PatchError(
                f"record @ {box.offset:#x} has no inline mode-7 {role} index to recolour")
        old = self._original[off:off + 2]
        old_index = struct.unpack("<H", old)[0]
        self._add(off, old, struct.pack("<H", target),
                  f"{role} colour index {old_index} -> {target} (record @ {box.offset:#x})")

    def set_channel(self, control: guidata.ControlGroup, channel: int,
                    allow_capability_change: bool = False,
                    move_status: bool = True,
                    move_display_list: bool = False) -> None:
        """Repoint every binding entry in `control`'s group at G12 `channel`.

        One byte per entry, so the same-size invariant is free; what this primitive is
        really for is the four checks below, each of which refuses a plausible-looking
        remap that would misbehave.

        **Every entry, not just the matching one.** A group holds two or three variant
        copies of itself distinguished by binding byte 2, and which one applies at runtime
        is `unknown` (`MCF-FORMAT.md` §8.2). Rewriting only the entry whose channel you
        searched for would leave the others pointing at the old pin, and the button would
        work or not depending on a variant nobody has decoded.

        **Command and status are two different tables, and a remap needs both.**
        `observed-on-hardware`, 2026-08-14: moving only the binding produced a button that
        *switched the new channel* -- confirmed on the bus and on the G12 OUTPUTS screen --
        while its own on/off indicator stayed dead, because the indicator reads a
        `DC_DIMMER_STATUS_3` entry in the variable table (`+0x2A70`, instance at byte 4)
        and that still named the old channel. `move_status` moves the status side too, in one
        of two ways:

        * **default** -- retarget the control's own indicator records at `channel`'s status
          cells and move no variable (`_retarget_indicator`). Every G12 OUTPUTS row keeps
          reporting its own channel. Hardware-unverified.
        * `move_display_list=True` -- repoint the status variables watching the old channel
          (`_move_status_variables`). PC.14's v9, proven on the coach, at the cost of the old
          channel's G12 OUTPUTS row, which then reports the new one.

        `allow_capability_change` suppresses I8 only. It exists because the file cannot
        know what an unreferenced pin is wired to, so a deliberate move onto a pin the
        file has never commanded has to be expressible -- but it must be said out loud.
        """
        bindings = guidata.binding_table(self._original)
        groups = guidata.group_table(self._original)
        if not 0 <= control.group < len(groups):
            raise PatchError(
                f"group {control.group} is outside the {len(groups)}-entry group table")
        entries = guidata.group_bindings(groups, bindings, control.group)
        if not entries:
            raise PatchError(f"group {control.group} ({control.label!r}) selects no "
                             f"binding entries; there is nothing to repoint")

        if not 0 <= channel <= 0xFF:
            raise PatchError(f"channel {channel} does not fit the one-byte instance field")

        # I5 -- the channel field is decoded only for DC_DIMMER_COMMAND_2. Byte 7 has not
        # been shown to be the instance for any other DGN, so a mixed group is refused
        # rather than half-rewritten on a guess.
        foreign = {entry.dgn_low for entry in entries
                   if entry.dgn_low != guidata.DGN_DC_DIMMER_COMMAND_2}
        if foreign:
            listed = ", ".join(hex(dgn) for dgn in sorted(foreign))
            raise PatchError(
                f"group {control.group} contains non-DC_DIMMER_COMMAND_2 entries "
                f"({listed}); byte 7 is not known to be the channel for those, so this "
                f"group cannot be remapped")

        # I6 -- assert the group drives exactly one located control. The spec requires
        # this be checked, not assumed: a shared group would make a remap a side effect on
        # some other button.
        sharers = [other for other in guidata.find_control_groups(self._original)
                   if other.group == control.group]
        if len(sharers) != 1:
            names = ", ".join(repr(other.label) for other in sharers) or "none"
            raise PatchError(
                f"group {control.group} is referenced by {len(sharers)} located controls "
                f"({names}); remapping it would move more than {control.label!r}")

        # I7 -- refuse a destination another binding already commands, which would tie two
        # controls to one pin. The source channel is exempt: a no-op remap is caught by I4.
        current = {entry.raw[guidata.BINDING_CHANNEL_FIELD] for entry in entries}
        occupied = {other.channel for other in bindings
                    if other.channel is not None} - current
        if channel in occupied:
            raise PatchError(
                f"channel {channel} is already commanded by another binding entry; "
                f"remapping onto it would drive one pin from two controls")

        # I8 -- moving between capability classes. Only one direction of this is
        # reachable, and the reason is worth stating: `dimmable_channels` recognises a
        # channel *because* something commands it, so a dimmable destination is always an
        # occupied one and I7 refuses it first. What I8 therefore catches in practice is
        # moving a dimmable control onto a pin this file commands nowhere -- where the
        # levels it sends would be silently meaningless. The latch-to-dimmable branch is
        # kept because it costs nothing and would fire if I7 were ever relaxed.
        dimmable = guidata.dimmable_channels(self._original)
        if not allow_capability_change:
            was = bool(current & dimmable)
            now = channel in dimmable
            if was != now:
                had = "dimmable" if was else "latch-only"
                gets = ("dimmable" if now else
                        "not dimmable, or is commanded nowhere in this file")
                raise PatchError(
                    f"{control.label!r} currently drives a {had} channel and {channel} is "
                    f"{gets}; pass allow_capability_change=True to do it anyway "
                    f"(see guidata.dimmable_channels)")

        gd_start, _ = guidata.guidata_extent(self._original)
        for entry in entries:
            offset = gd_start + entry.offset + guidata.BINDING_CHANNEL_FIELD
            old = self._original[offset:offset + 1]
            self._add(offset, old, bytes([channel]),
                      f"binding {entry.index} channel {old[0]} -> {channel} "
                      f"({control.label!r}, group {control.group})")

        if not move_status:
            return
        # The status side. Only meaningful when the group commands a single channel: a
        # master drives several, and "which status follows the remap" has no answer.
        if len(current) != 1:
            raise PatchError(
                f"group {control.group} commands {len(current)} distinct channels "
                f"{sorted(current)}; which status variable should follow the remap is "
                f"undefined -- pass move_status=False to move the commands only")
        (was,) = current
        if move_display_list:
            self._move_status_variables(control, was, channel)
        else:
            self._retarget_indicator(control, was, channel)

    def _move_status_variables(self, control: guidata.ControlGroup, was: int,
                               channel: int) -> None:
        """Repoint every `DC_DIMMER_STATUS_3` variable watching `was` at `channel` -- PC.14's v9.

        Coach-proven, and it costs a diagnostics row: a control's indicator and the G12 OUTPUTS
        row for its channel read the same variable (82 for channel 40), so once it moves the
        row for `was` reports `channel`. Moving only the watcher outside the display list (184)
        was tried too, as v10, and did nothing -- 184 has no GuiCode bridge block -- so that
        shape is no longer offered.
        """
        gd_start, _ = guidata.guidata_extent(self._original)
        watchers = [v for v in guidata.variable_table(self._original) if v.channel == was]
        if not watchers:
            raise PatchError(f"no DC_DIMMER_STATUS_3 variable watches channel {was}; "
                             f"pass move_status=False to move the commands only")
        for variable in watchers:
            offset = gd_start + variable.offset + guidata.VARIABLE_VALUE_FIELD
            old = self._original[offset:offset + 1]
            self._add(offset, old, bytes([channel]),
                      f"status variable {variable.index} instance {old[0]} -> {channel} "
                      f"({control.label!r})")

    def _retarget_indicator(self, control: guidata.ControlGroup, was: int,
                            channel: int) -> None:
        """Point `control`'s own indicator at `channel`'s status cells; move no variable.

        The chain is `MCF-FORMAT.md` §5.4c: status instance n -> its variable -> a GuiCode
        bridge block -> one colour cell and one bool cell -> a `+0x44BC8` index a record names.
        A widget follows a pin by *which cells its records name*, so a remap swaps those names
        and leaves the variable table alone; every G12 OUTPUTS row keeps reporting its own
        channel. Only names of `was`'s cells move, and only inside the control's own object: a
        record naming another cell (a tank heater caption's gate) is left as it is, and so are
        the diagnostics rows that share `was`'s bool cell.

        Refuses when the indicator names none of `was`'s cells -- four of 13.6's ten
        single-channel controls paint another pin's cell or one nothing writes, so nothing on
        them follows the command channel -- and when `channel` has no bridge block. A
        destination cell with no table index gets the first spare, as `statusbind.bind_pin`
        does for a new button.
        """
        from . import celltables, status_map, statusbind      # statusbind imports this module
        data = self._original
        if not any(f.label == BRIDGE_DUMP_BUILD for f in checksum_fields(data)):
            raise PatchError(
                "the GuiCode bridge dump that names a status variable's cells is 13.6's alone "
                "(src/fireflymcf/data/slot_bridge_136.txt) and this image is another build, "
                "so its indicator cells cannot be named. Pass move_display_list=True to move "
                "the status variable instead, or move_status=False to move the commands only")
        old: dict[int, str] = {}
        for row in status_map.backbone(data):
            if row.instance == was and row.bridged:
                old[row.colour_cell] = "colour"
                old[row.bool_cell] = "bool"
        obj = status_map._control_object(data, control)
        sites = indicator_sites(data, obj, set(old))
        if not sites:
            raise PatchError(
                f"{control.label!r}'s indicator (object {obj}) names none of channel {was}'s "
                f"status cells, so it does not follow its command channel in this file and "
                f"there is nothing to retarget; pass move_status=False to move the commands "
                f"only")
        new = statusbind.pin_bindings(data).get(channel)
        if new is None or not new.bridged:
            raise PatchError(
                f"channel {channel} has no GuiCode bridge block (or no status variable watches "
                f"it), so no cell ever shows its state; pass move_display_list=True to point "
                f"the control's bridged variable at it instead")

        target = {"colour": new.colour_index, "bool": new.bool_index}
        cell = {"colour": new.colour_cell, "bool": new.bool_cell}
        gs, _ = guidata.guidata_extent(data)
        for kind in sorted({old[c] for _off, _idx, c, _what in sites}):
            if target[kind] is not None:
                continue
            spare = [i for i in statusbind.spare_cell_indices(data)
                     if i not in self._claimed_spares]
            if not spare:
                raise PatchError(f"channel {channel}'s {kind} cell has no +0x44BC8 index and "
                                 f"no spare index remains to give it one")
            index = spare[0]
            self._claimed_spares.add(index)
            offset = gs + celltables.cell_table_off(data) + index * 4
            self._add(offset, data[offset:offset + 4], struct.pack("<I", cell[kind]),
                      f"+0x44BC8[{index}] -> channel {channel}'s {kind} cell "
                      f"{cell[kind]:#010x}")
            target[kind] = index
        for offset, index, c, what in sites:
            self._add(offset, data[offset:offset + 2], struct.pack("<H", target[old[c]]),
                      f"object {obj} {what} {index} -> {target[old[c]]} "
                      f"(channel {was} -> {channel} {old[c]} cell, {control.label!r})")

    def edits(self) -> list[Edit]:
        return sorted(self._edits, key=lambda e: e.file_offset)

    def build(self) -> tuple[bytes, list[str]]:
        """Apply the edits, reseal, and return (image, checksum labels that changed)."""
        if not self._edits:
            raise PatchError("no edits to apply")
        out = bytearray(self._original)
        for edit in self._edits:
            out[edit.file_offset:edit.end] = edit.new
        image, changed = reseal(bytes(out))
        if not changed:
            raise PatchError(
                "the edits changed no checksum, which cannot happen for a real payload "
                "edit; the checksum geometry must be wrong")
        return image, changed


# --------------------------------------------------------------------------- the ladder


def diff_ranges(before: bytes, after: bytes) -> list[tuple[int, int]]:
    """Maximal (offset, length) runs where the two images differ. Lengths must match."""
    if len(before) != len(after):
        raise PatchError(
            f"images differ in length ({len(before)} vs {len(after)}); a same-size patch "
            f"cannot do that")
    runs: list[tuple[int, int]] = []
    start = None
    for i, (x, y) in enumerate(zip(before, after)):
        if x != y and start is None:
            start = i
        elif x == y and start is not None:
            runs.append((start, i - start))
            start = None
    if start is not None:
        runs.append((start, len(before) - start))
    return runs


def verify(before: bytes, after: bytes, edits: list[Edit],
           changed_checksums: list[str]) -> list[str]:
    """Run the ladder from the design spec. Returns one line per rung, or raises.

    L1 coverage passes (all 27 CRCs verify, 100% claimed, nothing unclaimed)
    L2 the reader sees the change
    L3 only the intended ranges plus resealed checksums differ
    L4 the inverse patch restores the original byte for byte
    L5 the three checks the panel's own updater performs hold

    L3 and L4 carry the weight: L1 alone would pass a patcher that corrupted an undecoded
    region and resealed over it.
    """
    lines: list[str] = []

    # coverage raises its own exception type on a checksum mismatch. Wrap it so every rung
    # reports failure the same way and a caller only has to catch PatchError.
    try:
        report = coverage.analyse(after, deep=True)
    except coverage.CoverageError as exc:
        raise PatchError(f"L1: {exc}") from exc
    if report.claimed != report.total or report.unclaimed:
        raise PatchError(
            f"L1: coverage claims {report.claimed} of {report.total} bytes with "
            f"{len(report.unclaimed)} unclaimed span(s)")
    lines.append(f"L1  coverage: {report.claimed}/{report.total} bytes claimed, "
                 f"all {len(checksum_fields(after))} checksums verify")

    for edit in edits:
        if after[edit.file_offset:edit.end] != edit.new:
            raise PatchError(f"L2: {edit.note} is not present at {edit.file_offset:#x}")
    lines.append(f"L2  {len(edits)} edit(s) present and re-readable")

    # Containment, not equality: an edit's leading bytes often match (re-padding
    # '   Heat Pump' to '     12v aux' leaves the first three spaces alone), so the
    # differing run is a subset of the edit's extent. What must hold is that *nothing
    # outside* the intended regions moved -- that is the collateral damage this rung is
    # for. Equality here would fail on a correct patch, which is how this was found.
    csums = {f.label: f.position for f in checksum_fields(after)}
    allowed: set[int] = set()
    for edit in edits:
        allowed |= set(range(edit.file_offset, edit.end))
    for label in changed_checksums:
        allowed |= {csums[label], csums[label] + 1}

    differing = {i for start, size in diff_ranges(before, after)
                 for i in range(start, start + size)}
    stray = differing - allowed
    if stray:
        runs = diff_ranges(before, after)
        raise PatchError(
            f"L3: {len(stray)} byte(s) changed outside the intended edits and resealed "
            f"checksums, first at {min(stray):#x}; differing runs {runs}")
    if not differing:
        raise PatchError("L3: the images are identical, so no edit was applied")
    for edit in edits:
        if not (set(range(edit.file_offset, edit.end)) & differing):
            raise PatchError(
                f"L3: {edit.note} at {edit.file_offset:#x} changed nothing")
    lines.append(f"L3  {len(differing)} byte(s) differ, all inside {len(edits)} edit(s) + "
                 f"{len(changed_checksums)} resealed checksum(s)")

    inverse = McfPatcher(after)
    for edit in edits:
        inverse._add(edit.file_offset, edit.new, edit.old, f"revert {edit.note}")
    restored, _ = inverse.build()
    if restored != before:
        raise PatchError("L4: the inverse patch does not restore the original image")
    lines.append("L4  inverse patch restores the original byte for byte")

    for f in checksum_fields(after):
        if f.stored(after) != f.computed(after):
            raise PatchError(
                f"L5: {f.label} is {f.stored(after):#06x}, computes "
                f"{f.computed(after):#06x} -- the panel's updater would reject this")
    lines.append("L5  header, descriptor and payload CRCs all hold "
                 "(what cli.bin verifies at 0x200151E4)")
    return lines
