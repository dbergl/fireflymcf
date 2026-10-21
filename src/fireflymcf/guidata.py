"""Screen and widget records inside the Lyra `GuiData` section of an MCF.

STATUS: inferred-from-data, not proven-from-code.

Task 6 established that cli.bin only transports the GuiData payload -- it seeks,
reads and flashes it without ever looking inside -- so there is no located code that
parses these structures. Everything below was recovered by walking the bytes of
`firefly-13.6/mcf.bin` and checking the result against the subject coach's OPTIONS
screen, whose six entries were read off the physical vehicle (`answer_key.OPTIONS`).
That check is strong -- the walk reproduces the list exactly, in order, without ever
matching on label text -- but it is not a proof, and this module says so rather than
overstating. Code-level proof would mean disassembling the `LCD Firmware` image,
which carries the literals for GuiData's flash destination 0x90000000; that was not
done for this task.


The display list
----------------

GuiData's flash destination is 0x90000000 and the section is mapped there whole, so
a pointer 0x900XXXXX inside it addresses section byte XXXXX. The screens live in a
display list well past the section's header tables, stored as a run of consecutive,
self-delimiting screen objects:

    <u8 record_count> <record> * record_count <zero padding to a 4-byte boundary>

Evidence, at file offsets in `firefly-13.6/mcf.bin`:

  * 0x099C54 holds 0x1F = 31 and 0x099F0C holds 0x24 = 36. Walking that many records
    from each lands on 0x099F09 and 0x09A25A respectively -- three and two zero bytes
    short of the next 4-byte boundary, where the next screen's count byte sits. A
    record size that is wrong by even one byte does not land on a boundary with only
    zeros behind it, and does not land on a byte that is itself a valid count.
  * A u32 pointer array elsewhere in the section holds 0x9007081C, 0x90070AD4,
    0x90070E24 and 0x90070E78 at file offsets 0x06F63C..0x06F630 -- exactly the four
    count bytes 0x099C54, 0x099F0C, 0x09A25C, 0x09A2B0. The firmware addresses these
    objects by their count byte, so the count byte is the object.


The record
----------

Every record shares an 8-byte head, then a variable geometry block, then an
opcode-specific tail:

    +0x00  u8   opcode
    +0x01  u8   sub-type / style selector (0x50, 0x53, 0x54, 0x55, ... ) -- not
                length-bearing; 0x46 rows appear as both 0x50 and 0x54 at the same
                size
    +0x02  u16  field-presence flags
    +0x04  u32  a second flag word
    +0x08  ...  one u16 per set bit of (flags & 0x100F), then one u16 per set bit of
                (extra & 0x20), then TAILS[opcode] further bytes

The low four flag bits are the geometry (x, y, w, h), which is what makes the
presence model checkable rather than a fitted constant:

    0x099F89  0b 55 0f 2a 00 00 00 00  19 00 e7 ff fa 00 32 00  ...
              op fl ---- extra ------  x=25  y=-25 w=250 h=50
    0x099F29  0b 55 0c 02 00 00 00 00  20 03 24 00  ...
              op fl ---- extra ------  w=800 h=36

Both are text records. Dropping flag bits 0 and 1 drops exactly two u16s and the
record shrinks by exactly four bytes -- that much is measured. Reading those u16s as
x, y, w, h is inference, but a well-supported one: the second record is the screen
header and its remaining pair is 800x36 on an 800x480 panel, while the first is a
list row and reads 250x50 at (25, -25).

Flag bit 12 (0x1000) and bit 5 of the second word (0x20) each add one more u16; they
were found the same way, as the only sizes that keep a screen's record count landing
on its boundary.


The label
---------

Opcode 0x0B is the text record. Its tail is 7 bytes, then the label:

    <u8 length> <length bytes> <NUL>

The length excludes the terminator and the label may contain a newline. This is the
whole of the "(CH. 25) is a continuation record" question: at 0x09A07C the length
byte is 0x22 = 34, and the 34 bytes that follow are

    "   Cargo / Bath Light\n    (CH. 25)"

one string, one record. Compare its neighbours' length bytes -- 0x0C for
"   Heat Pump" (12), 0x0E for "   Bunk Accent" (14), 0x0D for "   Bath Light" (13).
`Widget.is_continuation` is retained from the task's interface but is always False:
this format has no continuation records, only labels with newlines in them.


The widget
----------

A widget here is a *list row*: an opcode 0x46 record immediately followed by a text
record. On the OPTIONS screen the display list runs

    04 04 0B 05 05 44 | 46 0B 0C | 46 0B 0C | ... | 21 0B 0C | 21 0B 0C | ...
    dividers, header,   six rows, one per      the four navigation tabs
    list chrome         coach option

which is why the header ("   OPTIONS") and the tab labels ("Floorplans",
"Main\nOptions", "Electrical Options", "Go Power!\nSettings") are not widgets: they
are text records under a divider and under 0x21 buttons respectively. Selecting the
rows by their 0x46 parent yields the coach's six entries and nothing else. Every one
of the 22 opcode-0x46 records found across the decodable screens is followed by a
text record, so the pairing is not opportunistic.

`Widget.fields` is the raw 0x46 row record. It carries a per-row id in its last
bytes -- 0x6B..0x70 for the six OPTIONS rows -- which is sequential and therefore
*not* the 15FCE selector byte that `answer_key.OPTIONS` records (0xEB, 0xE5, 0xE9,
0xE3, 0xE4, 0xEC, which are not in display order). The 0x0C record that closes each
row carries another unidentified u16 (0x0843, 0x084B, 0x0844, 0x0856, 0x0857,
0x0845). Neither field is claimed to be anything here.


The object table and the floorplans
-----------------------------------

The pointer array the display list is addressed through is a real table, not a lucky
run of words: in `firefly-13.6/mcf.bin` file offsets 0x06F580..0x070BB4 hold 1422
consecutive u32s, every one of them 0x900XXXXX with XXXXX inside the section. There
are FOUR such tables per file, not two: 13.6 holds 8, 1422, 30 and 3295 entries, at
file offsets 0x0394B8, 0x06F580, 0x09E1B8 and 0x09E474 -- so the 1422-entry table is
**index 1 of 4** in offset order, and the 3295-entry one is index 3. 11.23 holds
8/1201/30/2687 and 14.7 holds 9/1077/29/3171. See `object_tables`, which is where the
count is measured rather than asserted; this docstring claimed "two" until Task 10,
which was an artefact of `MIN_OBJECT_TABLE` hiding the 8- and 30-entry tables.
Entries descend, so object k occupies [table[k], table[k-1]) -- which is what makes an
object's extent, and therefore its trailing label, recoverable without sizing its
records.

Sixteen consecutive entries of table index 1 (the 1422-entry one) point at one-record
objects whose single record is opcode 0x00 and whose whole content is a label. At file
offset 0x095F68 (section offset 0x06CB30) they read

    01 00 55 00 00 00 00 00 00 01 ff 03 33 4e 4a 00   count 1, op 0x00, "3NJ"
    ...
    01 00 55 00 00 00 00 00 00 01 ff 02 52 54 00 00   "RT"
    01 00 55 00 00 00 00 00 00 01 ff 15 4e 6f 20 ...  "No Floorplan Selected"

and in table order they are "No Floorplan Selected" twice (indices 317, 318) then RT,
RA, RD, SY, TB, CB, WA, WD, WT, RY, 4NJ, 3NG, 3NK, 3NJ (319..332). Counting from the
last anchor gives RT=1 .. RY=10, which is `answer_key.FLOORPLANS` exactly, and 4NJ,
3NG, 3NK, 3NJ at 11..14 beyond it. The same anchor works on the other two files:
14.7 carries the anchor twice as well, at 283 and 284, with RT, RA, SY, TB, CB, WA,
WT, RY, RE behind it; 11.23 carries it once, at 235. The value is positional, not
stored -- the floorplan *buttons* (indices 335..350) are byte-identical apart from
their label, so nothing in a button says which value it writes. Their labels are
padded, not bare: the RT button ends `0a 00 03 52 54 20 00 00`, a length-3 label
"RT " with a trailing space, which is why the join against the code strings strips.

The FLOORPLAN SELECTION screen (section 0x73A8C..0x73DD8, titled "   FLOORPLAN
SELECTION") is what says which of those fourteen codes are offered, and under which
group. It carries two labels ending " Models" and, after each, one row per floorplan:

    05 5d 02 07 20 00 00 00 1a 00 0a 00 ff 00 00 4f 01 | 0c 55 0c ...
                                        ff 00 00 ^^^^^   0x0C image record
                                                 0x014F = object 335 = the "RT" button

Reading those references gives RA, RD, RT, CB, RY under "Generator Models" and WA,
WD, WT, TB, SY under "Li-AES Models" -- `answer_key.FLOORPLAN_FAMILIES` exactly, and
ten codes, not fourteen. 14.7's screen resolves the same way for all nine of its
codes.

The `ff 00 00` prefix and the following opcode 0x0C are load-bearing but not
individually necessary. Measured on 13.6's screen: 12 byte positions hold a u16 that
indexes a button whose label is one of the codes; 10 of them carry the prefix, the
same 10 are followed by a 0x0C record, and both filters admit the same 10. The two
rejects, 0x73AFE and 0x73BD4, are unaligned coincidences. Either half alone would do
on this corpus; both are applied because both are part of the row's actual shape, and
neither has been shown to hold on a build not in the corpus.

Why the OPTIONS row id is not an index into the group table
------------------------------------------------------------

Task 7's review proposed that a row's `fields[-6]` (0x6B..0x70 for the six OPTIONS
rows) indexes the 233-entry table at section 0x30, whose entries give (start, count)
into the 746-entry binding table at 0x778. It does not. An earlier draft of this
docstring rejected it for the wrong reason -- that the selected groups are DGN 0x1FED9
rather than 0x15FCE -- which is no argument at all: 0x1FED9 is
GENERIC_INDICATOR_COMMAND, the DGN that carries G12 configuration writes, so finding
it under six configuration toggles is confirming, not disconfirming. The contrast that
draft drew was with binding entries [2..7], which belong to group 1 (start=2,
count=26) and are selected by no OPTIONS row at all.

The actual disproof, measured on the corpus:

  * **The groups are +/- adjust pairs on shared targets, not booleans.** Group 0x6B is
    `d9 fe 05 06 0f 16 96 ff ff d2 ff fb` and group 0x6C is `... 0f 16 ... 00 05` --
    the same target byte 0x16, and trailing big-endian deltas of -5 and +5. Groups
    0x6D and 0x6E are the same pair on target 0x15 with -1 and +1. Six independent
    on/off options cannot be the decrement and increment halves of two numeric
    settings.
  * **The binding table holds no 0x15FCE entries at all.** Scanning every entry for a
    leading `ce 5f` gives zero hits in all three files (746 entries in 13.6, 732 in
    11.23, 747 in 14.7). The six selectors the OPTIONS rows write are not reachable
    through that table by any index.
  * **`fields[-6]` is the low byte of a u16.** The 0x46 tail is three u16s. Across
    13.6's decodable screens the first of them is 22, 37, 107..113, but also 399 for
    "Mid HVAC Zone" and 456 for "Set Inverter/Charger Defaults" -- outside a 233-entry
    table. Only truncating to a byte keeps every row in range, and truncation is not a
    thing an index does.
  * **The ids are not unique per row.** "AGS", "AES" and "AES Single Output" all carry
    37.
  * **Group indices are build-stable; row ids are not.** Group 0x6B is byte-identical
    in all three files (at binding entries 397 in 13.6, 402 in 11.23, 394 in 14.7),
    but 13.6's Heat Pump row carries 107 and 14.7's carries 99.

The positive results worth carrying forward:

  * **The group index is a stable, build-independent function id.** The same index
    names the same three binding entries across all three builds even though the
    binding table shifts underneath it. Whatever selects a group, it is not the row id.
  * **The option selectors live in the variable table, not the binding table.** The
    header entry at +0x20 (`41 01 00 2a 70 2a 50 09`: offset 0x2A70, size 0x0950)
    describes 298 entries of 8 bytes. In 13.6, entries 283..288 read
    `ce 5f 1d 02 {e9, e4, e5, e3, eb, ec}` -- a contiguous run holding exactly the six
    selectors of `answer_key.OPTIONS` -- and entry 294 is `ce 5f 1d 02 f5`, the
    floorplan parameter. Entry 294 is 0xF5 in 14.7 too; the option run is only
    near-stable there (entry 283 is 0xE7, not 0xE9), so the positions are a starting
    point, not a key.


What a control commands
-----------------------

The group table is not only build-stable, it is reachable from the display list. A
control's opcode-0x0C record ends in a u16; when that u16 is a valid group index, the
group's slice of the binding table says which G12 channels the control drives.

Offsets in this block are **section offsets** into GuiData (file offset = section
offset + 0x029438 in `firefly-13.6/mcf.bin`), because the two table offsets below are
read from the section header and are section-relative by construction:

    sect 0x06BF01    0c 55 0f 00 00 00 00 00 f6 ff f6 ff 5a 00 41 00  01 00
    (file 0x095339)  the 0x0C under "Light Master" / "ON"        group 1
    +0x30+1*8        11 80 0c 80 02 00 1a 00                     start 2, count 26
    (file 0x029470)
    +0x778+2*12      db fe 01 02 00 fb ff 16 ff ff 00 ff ...     channel 22, ...
    (file 0x029BC8)

The union of group 1's twenty-six entries names channels 22, 24, 25, 26, 32, 34 and
35 -- which is `answer_key.INTERIOR_MASTER` exactly. "Union" is load-bearing and not a
hedge: the entries come in variant copies (see byte 2, below), and no single variant
holds all seven. The OFF button next to it ends `02 00`, selecting group 2, whose
thirteen entries name the same seven channels with a level of 0x00 where group 1
carries 0xC8 (200, i.e. 100% in RV-C half-percent units). The "Exterior Master" object
does the same at groups 216 and 217. That pattern holds for all twelve button/group
pairs across the three builds: every ON group reaches full level, every OFF group is
all zero, each pair takes consecutive indices, and the two halves of a pair always name
the same channels.

The labels agree with the pinout without matching on any text. In 14.7, "Fresh Tank
Heater", "Grey Tank Heater", "Black Tank Heater", "Grey 2 Tank Heater" and
"Water\nPump" resolve through their groups to channels 5, 6, 7, 8 and 44 -- the
channels the coach calls TANK HEAT FRESH, TANK HEAT GREY, TANK HEAT BLACK, TANK HEAT
GREY 2 and WATER PUMP. That is five independent confirmations that the number in a
binding entry's byte 7 is the RV-C instance, the G12 channel and the PDF pin.

A third, independent corroboration comes from a group that is *empty* where the screen
is missing. 13.6 has no Fresh Tank Heater screen and its group 5 is a stub -- one entry
of `ff ff ff ff ff ff ff ff ff ff ff ff`, DGN 0xFFFF, no channel -- while 14.7, which
does have that screen, gives group 5 two entries on channel 5. The group table tracks
what the build actually offers.

The 0x0C tail is NOT always a group index: on the OPTIONS screen the same field holds
0x0843, 0x084B, 0x0844, 0x0856, 0x0857 and 0x0845, which index the 3295-entry image
table. Object 0x0843 is 96 bytes of non-record data; that it is a bitmap is INFERENCE
from its position in an image table and its byte histogram, not something decoded.
`find_control_groups` only reads in-range values, which is a heuristic, not a
discriminator -- see its docstring.


Byte 2 of a binding entry, and why channel 35 is not settled here
-----------------------------------------------------------------

A group's entries come in variant copies distinguished by byte 2, and **what selects a
variant is not known**. Read per variant rather than as a union, 13.6's masters are

    group 1   byte2=0x01 -> 22, 24, 25, 26, 32, 34        (interior, no 35)
              byte2=0x11 -> 22, 24, 25, 26, 32, 34, 35
    group 216 byte2=0x01 -> 21, 23                        (exterior, no 35)
              byte2=0x11 -> 21, 23, 35

which invites the reading "the 0x11 copies add channel 35 to both masters". **That
reading is wrong**, and 11.23 is the disproof: there the polarity is reversed on the
exterior side and there is a third variant on the interior side.

    group 61  byte2=0x01 -> 22, 24, 25, 32, 34
              byte2=0x11 -> 22, 24, 25, 32, 34, 35
              byte2=0x21 -> 22, 24, 32, 35
    group 216 byte2=0x01 -> 21, 23, 35        <- 0x01 is the copy WITH 35 here
              byte2=0x11 -> 21, 23

Taken variant by variant, 11.23 puts 35 in the exterior master under 0x01 and in the
interior master under 0x11 -- **disjoint either way**, which is what
`answer_key.INTERIOR_MASTER` and `EXTERIOR_MASTER` record from a live CAN capture. The
file corroborates the capture; it does not overturn it, and the capture is the direct
evidence in any case.

What is therefore safe to say, and all this module claims: byte 2 selects among two or
three variant copies of a group, `ControlGroup.channels` is the union over them, and
GuiData does not settle which copy applies on a given coach -- so it does not settle
channel 35's membership. An earlier draft of this docstring concluded that the file
contradicted the answer key on that point. It does not; that conclusion required
understanding byte 2.

UPDATE 2026-08-19 -- byte 2 IS now understood, and it confirms the paragraph above
rather than replacing it (`docs/CONTROL-BEHAVIOR.md` section 5, `gestures.py`). The
LCD Firmware matches the entry's byte-2 HIGH NIBBLE against a **runtime counter** in
SRAM that steps every time you release the control (`cmp sl,r2,lsr #4` at 0x0021B946;
the counter lives at 0x20006E6C + index*16 + 0x0A and wraps at the group entry's byte-0
low nibble). So which copy applies is a live value, not a stored one: "GuiData does not
settle which copy applies" is not a gap in this decode, it is the mechanism. Reading the
union stays correct, and the CAN capture stays the direct evidence for channel 35.


What this module cannot do
--------------------------

TAILS covers only the opcodes met while walking decodable screens; an unknown opcode
raises `UnknownRecord` rather than guessing a size. Chaining forward from 0x099C54
stops after twelve screens on an opcode whose size no candidate value resolves, so
the table is known to be incomplete.

Screen discovery is therefore a validating scan rather than an index read, and an
index into the display list has still not been found -- the group table chains to the
binding table, not to screen offsets, so it is not that index.

Until then `iter_screens` returns the 283 positions in the 13.6 file that walk
cleanly, and those do include false positives: 0x099D90 and 0x09A050 are positions
*inside* the two OPTIONS screens that happen to walk to a clean boundary. Callers
should check the result against something known, as the tests do.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Iterator

from . import container, memo, wordruns

# numpy is used only by the two structural-discovery scans below (`find_match_table`,
# `find_variable_tables`); it is imported lazily inside them so the common read/patch commands
# work on a stdlib-only install (see `pyproject.toml` extras).

GUIDATA_SECTION_MARKER = "GuiData"

# Opcode-specific trailing bytes, after the head and the geometry block. Derived by
# requiring each screen's record count to land on its 4-byte boundary; see the module
# docstring. This table is deliberately not extrapolated beyond what was observed.
TAILS: dict[int, int] = {
    0x04: 2,   # divider / filled rule
    0x05: 3,
    0x06: 7,
    0x0A: 2,
    0x0B: 7,   # text, then the length-prefixed label
    0x0C: 2,
    0x0F: 10,
    0x21: 4,   # navigation button
    0x44: 3,
    0x46: 6,   # list row
    0x61: 5,
}

TEXT_OPCODE = 0x0B
ROW_OPCODE = 0x46

# GuiData is mapped whole at 0x90000000, so a stored u32 0x900XXXXX is section byte
# XXXXX. The section carries object tables built out of exactly those words.
OBJECT_POINTER_BASE = 0x90000000
OBJECT_POINTER_MASK = 0xFFF00000
# Below this many consecutive in-range pointers a run is noise, not a table. There are
# FOUR real tables per file, not two: 13.6 holds 8, 1422, 30 and 3295 entries. This was
# 64 until Task 10, which hid the 8- and 30-entry tables; it is 4 because every real run
# is self-terminating -- its lowest pointer equals the run's own end offset -- and
# measured across the corpus all 12 runs of >= 4 words have that property while none of
# the 616 runs of 1-3 words do (see coverage._pointer_tables, which checks it).
# Lowering it is output-neutral here: `_floorplan_table` returns byte-identical results
# on all three files, because the two newly visible tables carry no code objects and it
# skips them.
MIN_OBJECT_TABLE = 4
# An object longer than this is not one of the small objects this module looks at;
# the bound only limits work, it never changes an answer.
MAX_SMALL_OBJECT = 4096

# Heads of the two object shapes the floorplan table is built from. Byte 0 is the
# record count, byte 1 the first record's opcode.
CODE_OBJECT_HEAD = b"\x01\x00"     # one record, opcode 0x00: a bare label
BUTTON_OBJECT_HEAD = b"\x02\x21"   # a 0x21 button record plus its text record

FLOORPLAN_ANCHOR = "No Floorplan Selected"
FLOORPLAN_SCREEN_TITLE = "FLOORPLAN SELECTION"
FAMILY_LABEL_SUFFIX = " Models"
# On the selection screen a floorplan row ends `ff 00 00 <u16 object index>` and is
# followed immediately by the row's 0x0C image record. Both halves are required so a
# stray aligned u16 cannot be mistaken for a row reference.
FLOORPLAN_REF_PREFIX = b"\xff\x00\x00"
IMAGE_OPCODE = 0x0C

HEAD_SIZE = 8
GEOMETRY_FLAG_BITS = 0x100F     # u16 at +0x02: bits 0..3 are x, y, w, h
EXTRA_U16_BITS = 0x20           # u32 at +0x04
SCREEN_ALIGNMENT = 4

# A label byte must be printable ASCII or a newline. Used only to reject a mis-framed
# walk, never to locate a record.
_LABEL_OK = frozenset(list(range(0x20, 0x7F)) + [0x0A])


class UnknownRecord(ValueError):
    """A record whose opcode has no known size. Raised rather than guessed at."""


class MalformedRecord(ValueError):
    """A record of a known opcode whose contents do not decode."""


@dataclass(frozen=True)
class Record:
    """One display-list record."""
    offset: int
    size: int
    opcode: int
    subtype: int
    flags: int
    extra: int
    label: str | None = None
    label_offset: int | None = None
    length_prefix: int | None = None

    @property
    def end(self) -> int:
        return self.offset + self.size


@dataclass(frozen=True)
class Widget:
    """A list row: an opcode-0x46 record plus the text record that follows it.

    `offset` is the offset of the label's first character -- the position the answer
    key records -- not of the record. `fields` is the raw row record.
    """
    offset: int
    label: str
    fields: bytes
    is_continuation: bool = False
    length_prefix: int = 0
    row_offset: int = 0


@dataclass(frozen=True)
class Screen:
    """One screen object: a record count, its records, and the padding after them.

    `offset` is the offset of the title label's first character, so that it can be
    compared against `answer_key.OPTIONS_SCREEN_136`. `start` is the count byte.
    """
    start: int
    offset: int
    title: str
    widgets: list[Widget]
    record_count: int
    records_end: int
    end: int
    title_length_prefix: int = 0
    records: list[Record] = field(default_factory=list, repr=False, compare=False)


@memo.content_cached(copy=None)
def guidata_extent(data: bytes) -> tuple[int, int]:
    """(start, end) file offsets of the GuiData section."""
    for section in container.parse_container(data).sections:
        if GUIDATA_SECTION_MARKER in section.name:
            return section.offset, section.offset + section.length
    raise ValueError("no GuiData section in this MCF")


def _record_body_size(data: bytes, offset: int) -> int:
    """Head plus geometry block, before the opcode-specific tail."""
    flags = struct.unpack_from("<H", data, offset + 2)[0]
    extra = struct.unpack_from("<I", data, offset + 4)[0]
    return (HEAD_SIZE
            + 2 * bin(flags & GEOMETRY_FLAG_BITS).count("1")
            + 2 * bin(extra & EXTRA_U16_BITS).count("1"))


def parse_record(data: bytes, offset: int, limit: int) -> Record:
    """Decode the record at `offset`. Raises rather than resynchronising."""
    if offset + HEAD_SIZE + 4 > limit:
        raise MalformedRecord(f"record at {offset:#x} runs past {limit:#x}")
    opcode = data[offset]
    tail = TAILS.get(opcode)
    if tail is None:
        raise UnknownRecord(f"unknown record opcode {opcode:#04x} at {offset:#x}")

    size = _record_body_size(data, offset) + tail
    label = label_offset = length_prefix = None
    if opcode == TEXT_OPCODE:
        length_at = offset + size
        if length_at >= limit:
            raise MalformedRecord(f"text record at {offset:#x} runs past {limit:#x}")
        length_prefix = data[length_at]
        label_offset = length_at + 1
        end = label_offset + length_prefix
        if length_prefix == 0 or end >= limit:
            raise MalformedRecord(
                f"text record at {offset:#x} has an unusable length {length_prefix}")
        raw = data[label_offset:end]
        if data[end] != 0:
            raise MalformedRecord(
                f"label at {label_offset:#x} is not NUL-terminated at its length")
        if not set(raw) <= _LABEL_OK:
            raise MalformedRecord(f"label at {label_offset:#x} is not text")
        label = raw.decode("ascii")
        size = end + 1 - offset

    if offset + size > limit:
        raise MalformedRecord(f"record at {offset:#x} runs past {limit:#x}")
    return Record(
        offset=offset,
        size=size,
        opcode=opcode,
        subtype=data[offset + 1],
        flags=struct.unpack_from("<H", data, offset + 2)[0],
        extra=struct.unpack_from("<I", data, offset + 4)[0],
        label=label,
        label_offset=label_offset,
        length_prefix=length_prefix,
    )


def parse_records(data: bytes, start: int, count: int,
                  limit: int | None = None) -> list[Record]:
    """Walk exactly `count` records from `start`."""
    if limit is None:
        limit = len(data)
    records: list[Record] = []
    offset = start
    for _ in range(count):
        record = parse_record(data, offset, limit)
        records.append(record)
        offset = record.end
    return records


def widgets_from_records(data: bytes, records: list[Record]) -> list[Widget]:
    """The list rows among `records`: each opcode-0x46 record and the text record
    that follows it. A row with no text after it is skipped, not invented."""
    return [
        Widget(
            offset=following.label_offset,
            label=following.label,
            fields=bytes(data[row.offset:row.end]),
            is_continuation=False,
            length_prefix=following.length_prefix,
            row_offset=row.offset,
        )
        for row, following in zip(records, records[1:])
        if row.opcode == ROW_OPCODE and following.label is not None
    ]


def parse_widgets(data: bytes, start: int, count: int,
                  limit: int | None = None) -> list[Widget]:
    """Walk `count` records from `start` and return the list rows among them.

    `start` is the first record -- for a screen, one past its count byte.
    """
    return widgets_from_records(data, parse_records(data, start, count, limit))


def parse_screen(data: bytes, offset: int, limit: int | None = None) -> Screen | None:
    """Decode the screen object whose count byte is at `offset`, or None.

    None means "this is not a screen": the count is zero, a record does not decode,
    the records do not end just short of a 4-byte boundary, the padding is not zero,
    or there is no text record to take a title from. Only an unknown opcode is
    tolerated as a None -- a screen built from records this module cannot size is
    indistinguishable, from here, from a run of unrelated bytes.
    """
    if limit is None:
        limit = len(data)
    if offset + 1 >= limit:
        return None
    count = data[offset]
    if count == 0:
        return None
    try:
        records = parse_records(data, offset + 1, count, limit)
    except (UnknownRecord, MalformedRecord):
        return None

    records_end = records[-1].end
    padding = (-records_end) % SCREEN_ALIGNMENT
    end = records_end + padding
    if end > limit or any(data[records_end:end]):
        return None

    title_record = next((r for r in records if r.label is not None), None)
    if title_record is None:
        return None

    widgets = widgets_from_records(data, records)
    return Screen(
        start=offset,
        offset=title_record.label_offset,
        title=title_record.label,
        widgets=widgets,
        record_count=count,
        records_end=records_end,
        end=end,
        title_length_prefix=title_record.length_prefix,
        records=records,
    )


def iter_screens(data: bytes) -> Iterator[Screen]:
    """Every screen in GuiData that this opcode table can decode, in offset order.

    A validating scan of every 4-byte-aligned position, not an index read -- see the
    module docstring. Screens using an opcode not in TAILS are missed rather than
    mis-parsed, and a run of unrelated bytes that happens to walk cleanly would be
    admitted, so callers wanting certainty should check the result against something
    known.
    """
    yield from _all_screens(data)


@memo.content_cached(copy=None)
def _all_screens(data: bytes) -> tuple:
    """`iter_screens`' scan, memoised on content (a `Screen` is a frozen dataclass, so the
    tuple is shared, not copied). The scan decodes ~1.3 M candidate positions per file."""
    start, end = guidata_extent(data)
    out = []
    for offset in range(start, end, SCREEN_ALIGNMENT):
        screen = parse_screen(data, offset, end)
        if screen is not None:
            out.append(screen)
    return tuple(out)


def find_screens_by_title(data: bytes, title: str) -> list[Screen]:
    """Every decodable screen whose title matches `title`, in offset order.

    Titles are not unique: on the 13.6 corpus the Main Options and Electrical Options
    tabs both render under the header "   OPTIONS". Comparison ignores the leading
    and trailing spaces the header carries for layout.
    """
    wanted = title.strip()
    return [s for s in iter_screens(data) if s.title.strip() == wanted]


def find_screen_by_title(data: bytes, title: str) -> Screen | None:
    """The first decodable screen with this title, or None.

    "First" is by offset. Because titles are not unique, prefer
    `find_screens_by_title` when the answer matters.
    """
    screens = find_screens_by_title(data, title)
    return screens[0] if screens else None


# ---------------------------------------------------------------------------
# Object tables and the floorplan table
# ---------------------------------------------------------------------------


class FloorplanTableNotFound(ValueError):
    """No object table in this GuiData carries the floorplan value anchor.

    Raised rather than returning an empty list, so that "this file does not store
    floorplans the way the corpus does" cannot be mistaken for "this file has no
    floorplans".
    """


@dataclass(frozen=True)
class Floorplan:
    """One floorplan: its two- or three-character code, its 15FCE-0xF5 value, and the
    group it is listed under on the FLOORPLAN SELECTION screen.

    `confirmed` is the honesty flag. True means the FLOORPLAN SELECTION screen was
    found and this entry is one it offers, with the family read off the screen's group
    label. False means the file has no such screen, so this entry is a raw member of
    the value run with no evidence it is a floorplan at all and no family available --
    11.23 takes that path and its run begins with "Ship Mode" at value 1. Callers that
    need a real table must check it; `family` is "" whenever it is False.
    """
    code: str
    value: int
    family: str = ""
    confirmed: bool = True


def object_tables(data: bytes) -> list[list[int]]:
    """Every object-pointer table in GuiData, as lists of section-relative offsets.

    A table is a maximal run of at least `MIN_OBJECT_TABLE` consecutive u32s that are
    all 0x900XXXXX with XXXXX inside the section. In `firefly-13.6/mcf.bin` that finds
    exactly FOUR runs: 8 entries at section offset 0x10080, 1422 at 0x46148, 30 at
    0x74D80 and 3295 at 0x7503C. 11.23 has 8/1201/30/2687 and 14.7 has 9/1077/29/3171.

    This docstring said "exactly two runs -- 1422 ... and 3295" until Task 10. That was
    wrong: it was an artefact of `MIN_OBJECT_TABLE` being 64, which hid the 8-entry
    table belonging to the image sub-container at 0x10000 and the 30-entry table nested
    inside the last object of the 1422-entry one. The four tables tile the section from
    0x10080 to the section checksum -- see `coverage._claim_guidata`.

    Entries descend, so object k occupies [table[k], table[k-1]). That holds strictly
    for the 8- and 1422-entry tables; the 30- and 3295-entry tables repeat pointers, so
    `_object_bounds` can return an empty span for a repeated entry.
    """
    # Memoised on content AND on `MIN_OBJECT_TABLE`, read at call time, so a test that
    # monkeypatches the minimum gets a fresh scan instead of a cached answer.
    return _object_tables(data, MIN_OBJECT_TABLE)


@memo.content_cached(copy=lambda t: [list(x) for x in t])
def _object_tables(data: bytes, min_run: int) -> list[list[int]]:
    start, end = guidata_extent(data)
    span = end - start
    # A word at `start + 4*i` qualifies when it is `0x900XXXXX` with XXXXX inside the section
    # (`wordruns.pointer_runs`). The original loop stopped at the last position `< end - 4`;
    # `n` reproduces that bound.
    n = max(0, (end - 4 - start + 3) // 4)
    return [run for _, run in wordruns.pointer_runs(data, start, n, OBJECT_POINTER_BASE,
                                                     OBJECT_POINTER_MASK, span)
            if len(run) >= min_run]


def object_table_offset(data: bytes, index: int) -> int:
    """Section-relative offset of object-pointer table `index` (the position of the table
    itself, not of the objects it names).

    `object_tables` returns each table's ENTRIES; anything that needs to REWRITE an entry needs
    the table's own position, and hardcoding one is a build-specific bug waiting to happen: any
    insertion below a table shifts it. (Repointing a glyph with 13.6's stock 0x7503C on a build
    that had already had an object appended wrote the pointer into unrelated bytes, and the glyph
    silently kept its old artwork.)

    Located the same way `object_tables` locates the runs -- by matching the table's first
    entries as consecutive `0x900XXXXX` words -- so it follows the table wherever it moved.
    """
    section_start, section_end = guidata_extent(data)
    table = object_tables(data)[index]
    if not table:
        raise ValueError("table %d is empty" % index)
    want = struct.pack("<%dI" % min(4, len(table)),
                       *(OBJECT_POINTER_BASE + t for t in table[:min(4, len(table))]))
    at = data.find(want, section_start, section_end)
    if at < 0:
        raise ValueError("could not locate object table %d" % index)
    if data.find(want, at + 1, section_end) >= 0:
        raise ValueError("object table %d's head is not unique" % index)
    return at - section_start


def _object_bounds(table: list[int], index: int, section_end_rel: int) -> tuple[int, int]:
    """(start, end) section-relative bounds of object `index`, which the table stores
    in descending address order."""
    return table[index], (table[index - 1] if index else section_end_rel)


def object_label(data: bytes, start: int, end: int) -> str | None:
    """The length-prefixed label an object ends with, or None.

    An object that ends in a label ends `<u8 length><length bytes><NUL>` followed only
    by the zero padding to the next 4-byte boundary. That makes the label's position
    recoverable without knowing the sizes of the records in front of it: the trailing
    zero run pins the NUL, and only one length byte can sit exactly that far back.
    """
    stop = end
    while stop > start and data[stop - 1] == 0:
        stop -= 1
    terminator = stop
    if terminator <= start or terminator >= end:
        return None
    for length in range(1, min(0x80, terminator - start)):
        at = terminator - 1 - length
        if at < start or data[at] != length:
            continue
        raw = data[at + 1:terminator]
        if set(raw) <= _LABEL_OK:
            return raw.decode("ascii")
    return None


def _labelled_objects(data: bytes, table: list[int], section_end_rel: int,
                      head: bytes) -> dict[int, str]:
    """{object index: label} for the objects in `table` starting with `head`."""
    section_start, _ = guidata_extent(data)
    out: dict[int, str] = {}
    for index in range(len(table)):
        start, end = _object_bounds(table, index, section_end_rel)
        if not 0 < end - start <= MAX_SMALL_OBJECT:
            continue
        at = section_start + start
        if data[at:at + len(head)] != head:
            continue
        label = object_label(data, at, section_start + end)
        if label is not None:
            out[index] = label
    return out


def _code_values(codes: dict[int, str]) -> dict[str, int]:
    """{code: value} from the run of label objects that follows the anchor.

    The floorplan codes are stored as a contiguous run of one-record label objects.
    The run is anchored by "No Floorplan Selected" -- the label the screens show when
    0xF5 is unset -- and the entry after the last anchor is value 1. Nothing here
    knows what a floorplan code looks like.
    """
    anchors = [index for index, label in codes.items() if label == FLOORPLAN_ANCHOR]
    if not anchors:
        return {}
    base = max(anchors)
    values: dict[str, int] = {}
    index = base + 1
    while index in codes:
        values.setdefault(codes[index], index - base)
        index += 1
    return values


def _selection_screen_bounds(data: bytes, table: list[int],
                             section_end_rel: int) -> tuple[int, int] | None:
    """Section-relative bounds of the object holding the FLOORPLAN SELECTION screen."""
    section_start, section_end = guidata_extent(data)
    at = data.find(FLOORPLAN_SCREEN_TITLE.encode("ascii"), section_start, section_end)
    if at < 0:
        return None
    target = at - section_start
    holders = [index for index, rel in enumerate(table) if rel <= target]
    if not holders:
        return None
    start, end = _object_bounds(table, min(holders), section_end_rel)
    if not 0 < end - start <= MAX_SMALL_OBJECT:
        # Index 0 has no upper neighbour, so its "object" is the rest of the section.
        # Reading rows out of that would mix in other screens; report nothing instead.
        return None
    return start, end


def _selection_screen_rows(data: bytes, start: int, end: int,
                           buttons: dict[int, str]) -> list[tuple[str, str]]:
    """[(family, button label)] for the floorplan rows in [start, end).

    A row reference is a u16 object index preceded by `ff 00 00` and followed by the
    row's 0x0C image record; the family is whichever "<name> Models" label most
    recently preceded it. See the module docstring for what each half of that filter
    is measured to remove.
    """
    section_start, section_end = guidata_extent(data)
    rows: list[tuple[str, str]] = []
    family = ""
    for offset in range(start, end - 1):
        at = section_start + offset
        length = data[at]
        if 0 < length < 0x40 and at + 1 + length < section_end and data[at + 1 + length] == 0:
            raw = data[at + 1:at + 1 + length]
            if set(raw) <= _LABEL_OK:
                text = raw.decode("ascii")
                if text.endswith(FAMILY_LABEL_SUFFIX):
                    family = text[:-len(FAMILY_LABEL_SUFFIX)].strip().lower()
        prefix_at = at - len(FLOORPLAN_REF_PREFIX)
        if prefix_at < section_start or data[prefix_at:at] != FLOORPLAN_REF_PREFIX:
            continue
        if data[at + 2] != IMAGE_OPCODE:
            continue
        index = struct.unpack_from("<H", data, at)[0]
        if index in buttons:
            rows.append((family, buttons[index]))
    return rows


def _defaults_handler_floorplan_values(data: bytes) -> set[int]:
    """The set of 0xF5 values that a per-floorplan tap handler emits (`floorplandefaults`).

    An INDEPENDENT oracle for the floorplan value run: each handler self-identifies by writing
    its own value to selector 0xF5, so a value present here is a proven `0xF5` target on the bus,
    recovered by a mechanism (`GUICODE.md` Finding 42) that never reads the object table this
    function's caller scans. Empty when capstone is unavailable (the handlers are disassembled),
    in which case the caller falls back to reporting the run unconfirmed.
    """
    from . import floorplandefaults, theme
    try:
        return set(floorplandefaults.floorplan_defaults(data))
    except (theme.CapstoneUnavailable, ModuleNotFoundError):
        return set()


def _floorplan_table(data: bytes) -> tuple[Floorplan, ...]:
    section_start, section_end = guidata_extent(data)
    section_end_rel = section_end - section_start
    for table in object_tables(data):
        codes = _labelled_objects(data, table, section_end_rel, CODE_OBJECT_HEAD)
        values = _code_values(codes)
        if not values:
            continue
        bounds = _selection_screen_bounds(data, table, section_end_rel)
        if bounds is None:
            # No FLOORPLAN SELECTION screen (11.23): the codes live only as this anchor-indexed
            # label run -- there is no grouped picker and no type-6 name switch to read membership
            # or family from (both confirmed absent, 2026-08-29). So confirm the run against an
            # INDEPENDENT oracle instead: the floorplan tap handlers, each of which self-identifies
            # by writing its own value to selector 0xF5 (`floorplandefaults`, Finding 42). The
            # handler-value <-> run-value alignment was proven on 13.6/14.7, where handler 0xF5=N
            # reproduces the selection-screen-confirmed floorplan of value N exactly (and the
            # handler's own mode writes match that floorplan's family). A value backed by a handler
            # is therefore a real floorplan, confirmed without a selection screen; family stays ""
            # because there is no grouped screen and 11.23 predates the generator/li-AES split.
            handler_values = _defaults_handler_floorplan_values(data)
            # The run's value BASE is per-build: 13.6's run counts from 1, 11.23's from 0
            # (`observed-on-hardware` 2026-08-30 -- picker taps put 1=3RT/4RT ... 7=3RD/4RD,
            # Ship Mode=0 on the wire; the 1-based read mis-named every floorplan by one).
            # With no selection screen to anchor it, align the run to the handler oracle:
            # try small shifts and keep the one that covers strictly more handler values
            # than the unshifted read. A tie keeps the run as stored -- never a guess.
            def coverage(shift: int) -> int:
                shifted = [v + shift for v in values.values()]
                if any(v < 0 for v in shifted):
                    return -1
                return sum(1 for v in shifted if v in handler_values)
            shift = 0
            if handler_values:
                best = max((-1, 0, 1), key=lambda s: (coverage(s), s == 0))
                if coverage(best) > coverage(0):
                    shift = best
            corroborated = bool(handler_values) and (
                sum(1 for v in values.values() if v + shift in handler_values) * 2
                >= len(values))
            return tuple(sorted(
                (Floorplan(code=code, value=value + shift,
                           confirmed=corroborated and value + shift in handler_values)
                 for code, value in values.items()),
                key=lambda fp: fp.value))
        buttons = {index: label.strip() for index, label in _labelled_objects(
            data, table, section_end_rel, BUTTON_OBJECT_HEAD).items()}
        found: dict[str, Floorplan] = {}
        for family, label in _selection_screen_rows(data, *bounds, buttons):
            if label in values and label not in found:
                found[label] = Floorplan(code=label, value=values[label], family=family)
        return tuple(sorted(found.values(), key=lambda fp: fp.value))
    raise FloorplanTableNotFound(
        f"no object table in this GuiData carries a {FLOORPLAN_ANCHOR!r} anchor")


def find_floorplan_table(data: bytes) -> list[Floorplan]:
    """Every floorplan the FLOORPLAN SELECTION screen offers, in 0xF5 value order.

    Two independent structures are joined, neither of which matches on code text:

      * the run of one-record label objects that follows "No Floorplan Selected" in
        the object table gives each code its 15FCE-0xF5 value -- counting from a
        PER-BUILD base: 1 on 13.6, 0 on 11.23 (`observed-on-hardware` 2026-08-30; the
        no-screen path below aligns the base to the tap-handler oracle);
      * the FLOORPLAN SELECTION screen references a subset of the button objects by
        object index, under "Generator Models" / "Li-AES Models" group labels, which
        gives both membership and family.

    A code present in the run but not offered by the selection screen is omitted --
    13.6 stores 4NJ, 3NG, 3NK and 3NJ at values 11..14 and shows none of them.

    Known gap, deliberate: **Ship Mode (value 0) is missing from the 13.6/14.7 results**
    even though the real panels offer it as a button -- proven on a coach flashed to 14.7
    (BUS-BASELINE PC.41: the Ship Mode tap writes `0xF5 := 0`, same value as 11.23's). It
    has no label-run entry on those builds (it is the name switch's DEFAULT arm there), so
    nothing structural anchors it to a value; hardcoding 0 from the hardware observation
    would be this project's first fitted table row, so the omission stands, documented.

    Contract when the file does not look like the corpus:

      * no value anchor anywhere -> raises `FloorplanTableNotFound`;
      * anchor but no selection screen -> returns the whole value run, confirming each
        entry against the tap-handler oracle instead (`_defaults_handler_floorplan_values`):
        `confirmed` is True for a value a floorplan handler emits, False otherwise, and
        `family` is always "" (no grouped screen to read it from). 11.23 takes this path:
        it has no FLOORPLAN SELECTION screen (its picker is the ADDITIONAL OPTIONS grid;
        a name switch DOES exist -- objects 852/853, found 2026-08-30 once this table's
        base was fixed), so the run (led by "Ship Mode" at value 0) anchors here -- all
        eight of its values carry
        a handler once the base is aligned to the handler set, and the whole map was
        confirmed on the coach 2026-08-30 (`captures/pc-1123-session-2026-08-30.log`:
        stated-order picker taps and photographed highlights pin 0=Ship Mode through
        7=3RD/4RD on the wire; a 1-based read had mis-named every floorplan by one). If
        capstone is unavailable the handlers cannot be disassembled, so the run comes back
        1-based and all unconfirmed; check `confirmed` before believing a result.

    Not cached: the scan is ~50 ms on 13.6's 1.02 MB file and the callers are few. (The
    heavy readers in this module ARE memoised, on content -- see `memo.content_cached`.)
    """
    return list(_floorplan_table(data))


def find_option_defaults(data: bytes, floorplan_value: int) -> dict[str, bool]:
    """`{option name: default state}` for `floorplan_value` -- RECOVERED 2026-08-18.

    This raised `NotImplementedError` for most of the project's life, behind three strict
    `xfail`s, and its docstring recorded an extensive negative: the GuiData header tables, the
    OPTIONS row records, the byte-identical floorplan buttons and the whole G12 section were all
    searched and none carries a per-floorplan value. Every one of those negatives still stands.
    They were all searches of **stored data**, and the defaults are not stored as data -- they are
    **emitted as code**.

    `GUICODE.md` Finding 42: each FLOORPLAN SELECTION button's touch record carries a tap action
    id (`0x0898 + value`); the tap dispatcher branches to that floorplan's handler; and the
    handler transmits a run of static 12-byte `0x1FED9` CAN-frame templates from Finding 7's
    `.rodata` pool, each carrying a literal `<selector, value>` pair. Those literals ARE the
    defaults -- the same shape Finding 23 found for the OPTIONS rows, and the reason
    `BUS-BASELINE.md` PC.12a could prove the values were "already in the panel's flash" while
    Finding 19's read of the *receive* path correctly found nothing.

    `floorplandefaults.floorplan_defaults` does the extraction (selector-keyed); this wraps it
    with the name<->selector join `find_option_bindings` recovered in Finding 23. Selectors the
    burst never writes are absent -- notably `0xD8`, which PC.12a showed comes from status, not
    from the transaction.
    """
    from . import floorplandefaults
    table = floorplandefaults.floorplan_defaults(data)
    if floorplan_value not in table:
        raise KeyError(f"no floorplan handler for value {floorplan_value}")
    writes = table[floorplan_value]
    out: dict[str, bool] = {}
    for binding in find_option_bindings(data):
        if binding.selector in writes:
            out[binding.name] = bool(writes[binding.selector])
    return out



# ---------------------------------------------------------------------------
# The header tables: groups, bindings and variables
# ---------------------------------------------------------------------------
#
# The GuiData header is a run of 8-byte table descriptors starting at +0x08. Each
# holds a tag in its first u16 and the table's (offset, size) in its last two. Three
# of them matter here; in `firefly-13.6/mcf.bin` they read
#
#   +0x08  10 00 e9 00 30 00 48 07   group table    at 0x0030, 0x0748 = 233 * 8
#   +0x10  20 00 00 00 78 07 f8 22   binding table  at 0x0778, 0x22F8 = 746 * 12
#   +0x20  41 01 00 2a 70 2a 50 09   variable table at 0x2A70, 0x0950 = 298 * 8
#
# and the same three descriptors sit at the same three positions in 11.23
# (230/732/291 entries) and 14.7 (234/747/310). The entry sizes are not guessed: each
# descriptor's size divides exactly by them in all three builds, and 0x2A70 + 0x0950
# lands on 0x33C0, which is where the next descriptor says the following table starts.

GROUP_TABLE_DESCRIPTOR = 0x08
BINDING_TABLE_DESCRIPTOR = 0x10
VARIABLE_TABLE_DESCRIPTOR = 0x20
DESCRIPTOR_SIZE = 8

GROUP_ENTRY_SIZE = 8
BINDING_ENTRY_SIZE = 12
VARIABLE_ENTRY_SIZE = 8

# The tag each descriptor must carry, as a check that this file is laid out like the
# corpus rather than a silent mis-read of some other header shape.
DESCRIPTOR_TAGS = {
    GROUP_TABLE_DESCRIPTOR: 0x0010,
    BINDING_TABLE_DESCRIPTOR: 0x0020,
    VARIABLE_TABLE_DESCRIPTOR: 0x0141,
}

# A group entry is `<u16 ?> <u16 ?> <u16 start> <u8 count> <u8 pad>`: entry 1 of 13.6
# is `11 80 0c 80 02 00 1a 00`, and start=2 count=26 is exactly the run of binding
# entries the Light Master's ON button commands. `count` is read as a byte rather than
# a u16 because the G12 section's copy of this table pads with 0xFF instead of 0x00.
GROUP_START_FIELD = 4
GROUP_COUNT_FIELD = 6

# Low 16 bits of the RV-C DGN, which is how both tables store it. The high bits are
# not stored; every DGN identified so far is 0x1XXXX.
DGN_DC_DIMMER_COMMAND_2 = 0xFEDB   # 0x1FEDB, the per-channel load command
DGN_DC_DIMMER_STATUS_3 = 0xFEDA    # 0x1FEDA, the per-channel load status
DGN_CONFIG = 0x5FCE                # 0x15FCE, the Firefly configuration DGN

# Where the channel (== RV-C instance == G12 pin) sits in each record. See
# `find_channel_references` for why these two positions and no others.
BINDING_CHANNEL_FIELD = 7
BINDING_LEVEL_FIELD = 5
VARIABLE_VALUE_FIELD = 4

# DC_DIMMER_COMMAND_2 carries brightness in half-percent units, so 200 is 100%. A
# group whose dimmer entries reach it is an "on" group; the matching "off" group's
# entries are all zero.
FULL_BRIGHTNESS = 0xC8

# Opcode 0x0C's trailing u16 is the group index. Only in-range values are treated as
# one -- see `find_control_groups`.
GROUP_REFERENCE_TAIL = 2

# Where the RV-C command sits in a binding record, and the command that only a dimmable
# load is ever sent. See `dimmable_channels` for the evidence and its limits.
BINDING_COMMAND_FIELD = 4
COMMAND_SET_BRIGHTNESS = 0


class TableNotFound(ValueError):
    """This GuiData's header does not describe the tables this module reads."""


@dataclass(frozen=True)
class Group:
    """One entry of the 233-entry table at GuiData+0x30: a slice of the binding table."""
    index: int
    offset: int          # section-relative
    start: int
    count: int
    raw: bytes = b""


@dataclass(frozen=True)
class Binding:
    """One 12-byte entry of the table at GuiData+0x778: a message the panel sends."""
    index: int
    offset: int          # section-relative
    dgn_low: int
    raw: bytes

    @property
    def channel(self) -> int | None:
        """The G12 channel this commands, or None if the record shape is unknown.

        Only DC_DIMMER_COMMAND_2 entries are decoded. Byte 7 has not been shown to be
        the instance for any other DGN, and guessing would put invented channels into
        `find_channel_references`.
        """
        if self.dgn_low != DGN_DC_DIMMER_COMMAND_2:
            return None
        return self.raw[BINDING_CHANNEL_FIELD]

    @property
    def level(self) -> int | None:
        if self.dgn_low != DGN_DC_DIMMER_COMMAND_2:
            return None
        return self.raw[BINDING_LEVEL_FIELD]


@dataclass(frozen=True)
class Variable:
    """One 8-byte entry of the table at GuiData+0x2A70: a value the panel reads or
    writes. `value` is the instance for a per-channel DGN and the selector byte for
    the 15FCE configuration DGN -- the same field position serves both."""
    index: int
    offset: int          # section-relative
    dgn_low: int
    value: int
    raw: bytes

    @property
    def channel(self) -> int | None:
        if self.dgn_low != DGN_DC_DIMMER_STATUS_3:
            return None
        return self.value

    @property
    def selector(self) -> int | None:
        if self.dgn_low != DGN_CONFIG:
            return None
        return self.value


@dataclass(frozen=True)
class ControlGroup:
    """A labelled on-screen control and the channels its group commands.

    `channels` is the UNION over the group's entries. A group holds two or three
    variant copies of itself distinguished by binding byte 2, and no single variant
    need contain every channel listed here -- see the module docstring's "Byte 2 of a
    binding entry" section before using this for exact group membership.

    `levels` is every distinct DC_DIMMER_COMMAND_2 level byte in the group, sorted.
    The two booleans are statements about those bytes and nothing more; deliberately
    NOT named `turns_on`/`turns_off`, because most groups contain both a zero and a
    full-level entry and so do both. On 13.6, "Water Pump" (group 44) has levels
    {0x00, 0xC8}, and group 80 reaches `has_full_level` on 0xFA alone -- a value
    outside the 0..0xC8 brightness range, whose meaning here is not known.
    """
    screen_title: str
    label: str
    group: int
    channels: list[int]
    offset: int          # section-relative offset of the 0x0C record that names the group
    levels: tuple[int, ...]

    @property
    def has_full_level(self) -> bool:
        """Some entry sets a level of 0xC8 (100%) or above. NOT "this switches on"."""
        return any(level >= FULL_BRIGHTNESS for level in self.levels)

    @property
    def all_levels_zero(self) -> bool:
        """Every entry sets a level of 0x00. NOT "this switches off"."""
        return bool(self.levels) and not any(self.levels)


def _table_bounds(data: bytes, descriptor: int, entry_size: int) -> tuple[int, int]:
    """(section-relative offset, entry count) of the table `descriptor` describes."""
    start, end = guidata_extent(data)
    at = start + descriptor
    if at + DESCRIPTOR_SIZE > end:
        raise TableNotFound(f"header descriptor {descriptor:#x} runs past the section")
    tag, _, offset, size = struct.unpack_from("<HHHH", data, at)
    expected = DESCRIPTOR_TAGS[descriptor]
    if tag != expected:
        raise TableNotFound(
            f"descriptor {descriptor:#x} has tag {tag:#06x}, expected {expected:#06x}")
    if size == 0 or size % entry_size or start + offset + size > end:
        raise TableNotFound(
            f"descriptor {descriptor:#x} sizes {size:#x} bytes, not a whole number of "
            f"{entry_size}-byte entries inside the section")
    return offset, size // entry_size


@memo.content_cached
def group_table(data: bytes) -> list[Group]:
    """The 233-entry table at GuiData+0x30, in index order.

    Each entry names a contiguous run of the binding table. Group indices are
    build-independent where row ids are not: group 0x6B is byte-identical in all three
    corpus files even though it sits at binding entry 397 in 13.6, 402 in 11.23 and
    394 in 14.7.
    """
    start, _ = guidata_extent(data)
    offset, count = _table_bounds(data, GROUP_TABLE_DESCRIPTOR, GROUP_ENTRY_SIZE)
    out = []
    for index in range(count):
        rel = offset + index * GROUP_ENTRY_SIZE
        raw = bytes(data[start + rel:start + rel + GROUP_ENTRY_SIZE])
        out.append(Group(
            index=index,
            offset=rel,
            start=struct.unpack_from("<H", raw, GROUP_START_FIELD)[0],
            count=raw[GROUP_COUNT_FIELD],
            raw=raw,
        ))
    return out


@memo.content_cached
def binding_table(data: bytes) -> list[Binding]:
    """The 746-entry table at GuiData+0x778, in index order."""
    start, _ = guidata_extent(data)
    offset, count = _table_bounds(data, BINDING_TABLE_DESCRIPTOR, BINDING_ENTRY_SIZE)
    out = []
    for index in range(count):
        rel = offset + index * BINDING_ENTRY_SIZE
        raw = bytes(data[start + rel:start + rel + BINDING_ENTRY_SIZE])
        out.append(Binding(index=index, offset=rel,
                           dgn_low=struct.unpack_from("<H", raw, 0)[0], raw=raw))
    return out


@memo.content_cached
def variable_table(data: bytes) -> list[Variable]:
    """The 298-entry table at GuiData+0x2A70, in index order."""
    start, _ = guidata_extent(data)
    offset, count = _table_bounds(data, VARIABLE_TABLE_DESCRIPTOR, VARIABLE_ENTRY_SIZE)
    out = []
    for index in range(count):
        rel = offset + index * VARIABLE_ENTRY_SIZE
        raw = bytes(data[start + rel:start + rel + VARIABLE_ENTRY_SIZE])
        out.append(Variable(index=index, offset=rel,
                            dgn_low=struct.unpack_from("<H", raw, 0)[0],
                            value=raw[VARIABLE_VALUE_FIELD], raw=raw))
    return out


def group_bindings(groups: list[Group], bindings: list[Binding],
                   index: int) -> list[Binding]:
    """The binding entries group `index` selects, clipped to the table."""
    group = groups[index]
    stop = min(group.start + group.count, len(bindings))
    return bindings[group.start:stop]


def find_channel_references(data: bytes,
                            sources: tuple[str, ...] = ("binding", "variable"),
                            ) -> dict[int, list[int]]:
    """{G12 channel: section-relative offsets of every byte that names it}.

    Two tables name channels, and only these two field positions are decoded:

      * **binding table, byte 7**, for DGN 0x1FEDB (DC_DIMMER_COMMAND_2). Entries
        [2..7] are `db fe 01 02 00 fb ff {16,18,19,1a,20,22}` -- six records identical
        but for that byte -- and entries [14..20] repeat the run with 0x23 added. Those
        two sets are `answer_key.INTERIOR_MASTER` minus and including channel 35, and
        the group that selects them is the one the screen labelled "Light Master"
        reaches through its ON button. See `find_control_groups`.
      * **variable table, byte 4**, for DGN 0x1FEDA (DC_DIMMER_STATUS_3), the status
        counterpart. In 13.6 that run covers instances 1..44 with no gaps.

    Bytes at other positions, and instance-looking bytes under DGNs whose layout has
    not been established, are deliberately not reported: an over-eager scan would fill
    this dict with numbers that are not channels.

    A caveat worth carrying: because the status run is exhaustive over 1..44, "every
    known channel is referenced" is nearly free from the variable table alone. Pass
    `sources=("binding",)` for the discriminating half -- 64 sparse channels in 13.6,
    which does *not* include 9, 11, 12 or 43.
    """
    unknown = set(sources) - {"binding", "variable"}
    if unknown:
        raise ValueError(f"unknown channel-reference sources: {sorted(unknown)}")
    refs: dict[int, list[int]] = {}
    if "binding" in sources:
        for entry in binding_table(data):
            if entry.channel is not None:
                refs.setdefault(entry.channel, []).append(
                    entry.offset + BINDING_CHANNEL_FIELD)
    if "variable" in sources:
        for variable in variable_table(data):
            if variable.channel is not None:
                refs.setdefault(variable.channel, []).append(
                    variable.offset + VARIABLE_VALUE_FIELD)
    return {channel: sorted(offsets) for channel, offsets in sorted(refs.items())}


# A per-channel display list is a run of consecutive variable indices whose instances also
# ascend by one. Eight is well clear of the longest coincidental ascending run observed
# (three) and well under the real lists, which are 11 to 23 entries.
STATUS_LIST_MIN_RUN = 8


def status_list_variables(data: bytes) -> set[int]:
    """Indices of status variables that belong to a per-channel *display list*.

    `observed-on-hardware`, 2026-08-14. Two variables watched instance 40 in 13.6, and
    repointing both moved the Starlink button's indicator *and* broke the `G12 OUTPUTS`
    diagnostics row for channel 40, which then displayed channel 38's state. The two are
    told apart by where they sit in the table, not by any flag byte:

        idx 66..86  ->  instances 24..44, one per index      the diagnostics list
        idx 184     ->  instance 40, after an unrelated run  the button's watcher

    A diagnostics row is a member of a long ascending run; a control's watcher is not. So
    a remap must move the watcher and leave the list alone, or the screen that reports
    channel state starts lying about it.

    Byte 2 of the entry (`0x1d` vs `0x5d`) does *not* separate them -- both of instance
    40's entries carry `0x1d` -- and an earlier guess that bit `0x40` marks "channel in
    use" is refuted by channels 9, 11, 12 and 18, which are hardware-verified yet carry
    it set.
    """
    status = sorted((v for v in variable_table(data)
                     if v.dgn_low == DGN_DC_DIMMER_STATUS_3),
                    key=lambda v: v.index)
    listed: set[int] = set()
    run: list[Variable] = []

    def flush() -> None:
        if len(run) >= STATUS_LIST_MIN_RUN:
            listed.update(v.index for v in run)

    for variable in status:
        if run and (variable.index == run[-1].index + 1
                    and variable.value == run[-1].value + 1):
            run.append(variable)
            continue
        flush()
        run = [variable]
    flush()
    return listed


def channel_commands(data: bytes) -> dict[int, set[int]]:
    """Every distinct RV-C command byte each channel is sent, across the binding table."""
    out: dict[int, set[int]] = {}
    for binding in binding_table(data):
        channel = binding.channel
        if channel is None:                 # not DC_DIMMER_COMMAND_2; byte 4 undecoded
            continue
        out.setdefault(channel, set()).add(binding.raw[BINDING_COMMAND_FIELD])
    return out


def dimmable_channels(data: bytes) -> set[int]:
    """Channels the file ever sends a *set brightness* (command 0) to.

    This reproduces the pin sheet's `DIM` vs `LATCH` note. On 13.6 it agrees with all
    fifteen channels the G12 pinout PDF labels -- nine `DIM`, six `LATCH` -- with no
    misses in either direction:

        LATCH  5, 6, 7, 8, 40, 44        commands are a subset of {1, 2, 3, 5}
        DIM    21..26, 32, 34, 35        commands always include {0, 6, 21}

    **Command 0 is the marker, and 21 is not.** An earlier version of this docstring
    claimed commands 0, 6 and 21 always co-occur so any of them would do. That is true
    only across the 15 PDF-labelled channels. Across the file, 0 and 6 mark the same nine
    channels, while 21 additionally covers 2, 10, 27, 36 and 86 -- which carry
    `{4, 5, 21}`: ramp, **stop**, toggle, and no set-brightness at all. That is the
    hold-to-*move* pattern of a motor (extend while held, stop on release), not dimming.
    Command 21 means "act while held", which dimmers and motors share; command 0 is what
    distinguishes a light. Picking 21 as the marker would call four motor channels
    dimmable.

    Corroborated three ways: the stored table, the G12 pin sheet's `DIM`/`LATCH` notes,
    and what the panel transmits (`docs/BUS-BASELINE.md` PC.15) -- a dimmable load is sent
    `cmd 0 + 0xFB` by the light master and `cmd 5 + 0xFA` by its own button, where a latch
    load gets `cmd 5 + 0xC8`. Levels above 0xC8 are "use the remembered level" sentinels.

    Still `inferred-from-data`: the sample is small and lopsided -- 9 of the 15 labelled
    channels are lights and 4 of the 6 latches are tank heaters -- and nothing has been
    found in code that reads a per-channel capability flag.

    A channel absent from the binding table entirely returns False, which is the useful
    behaviour for `patch.set_channel`: an unreferenced destination is exactly the case
    where the file cannot tell you what the pin can do.
    """
    return {channel for channel, commands in channel_commands(data).items()
            if COMMAND_SET_BRIGHTNESS in commands}


@memo.content_cached
def find_control_groups(data: bytes) -> list[ControlGroup]:
    """Every labelled control whose 0x0C record names a group, in offset order.

    **The join, and why it is believed.** A control's opcode-0x0C record ends in a u16.
    When that u16 is a valid group index the group's binding entries say what the
    control commands. Five things make this a reading rather than a coincidence:

      * On 13.6's "Light Master" object the ON button's 0x0C ends `01 00` and the OFF
        button's ends `02 00`; group 1 is (start 2, count 26) and group 2 is
        (start 28, count 13). Group 1's dimmer entries carry level 0xC8/0xFB, group
        2's carry 0x00, and both name the same seven channels -- 22, 24, 25, 26, 32,
        34, 35, which is `answer_key.INTERIOR_MASTER` exactly. The seven are the UNION
        over the group's variant copies; see `ControlGroup`.
      * The same holds for "Exterior Master" at groups 216/217, and for both objects in
        all three builds: twelve button/group pairs, every ON group reaching full level
        and every OFF group all zero, with no exceptions.
      * The labels agree with the pinout without anything matching on text: 14.7's
        "Fresh Tank Heater", "Grey Tank Heater", "Black Tank Heater", "Grey 2 Tank
        Heater" and "Water\\nPump" resolve to channels 5, 6, 7, 8 and 44, which the
        coach names TANK HEAT FRESH, GREY, BLACK, GREY 2 and WATER PUMP.
      * An ON/OFF pair takes consecutive group indices in every case.
      * The one build without a Fresh Tank Heater screen is the one whose group 5 is a
        stub: 13.6's group 5 holds a single `ff ff ...` entry with DGN 0xFFFF and no
        channel, where 14.7's holds two entries on channel 5.

    **What this does not claim.** The 0x0C tail is not always a group index -- the
    OPTIONS rows' 0x0C records end 0x0843, 0x084B, 0x0844, 0x0856, 0x0857, 0x0845,
    which are indices into the 3295-entry image table, not groups. Out-of-range values
    are skipped, so a control whose icon index happened to fall under the group count
    would be reported wrongly. No such case is known on this corpus, but nothing here
    rules one out; treat a single-channel result with an implausible label as suspect
    ("C" -> group 28 -> channel 1 on 13.6 is one to look at twice).

    `label` is the nearest preceding text record inside the same object, which is the
    button's own caption; `screen_title` is the object's first label. Neither is unique:
    "ON", "OFF" and "E" each label several controls, so key results by group, not label.
    """
    groups = group_table(data)
    bindings = binding_table(data)
    start, _ = guidata_extent(data)
    out: list[ControlGroup] = []
    for screen in iter_screens(data):
        for position, record in enumerate(screen.records):
            if record.opcode != IMAGE_OPCODE:
                continue
            index = struct.unpack_from(
                "<H", data, record.end - GROUP_REFERENCE_TAIL)[0]
            if index >= len(groups):
                continue
            selected = [b for b in group_bindings(groups, bindings, index)
                        if b.channel is not None]
            if not selected:
                continue
            label = next((r.label for r in reversed(screen.records[:position])
                          if r.label is not None), screen.title)
            out.append(ControlGroup(
                screen_title=screen.title,
                label=label,
                group=index,
                channels=sorted({b.channel for b in selected}),
                offset=record.offset - start,
                levels=tuple(sorted({b.level for b in selected})),
            ))
    out.sort(key=lambda c: c.offset)
    return out


@dataclass(frozen=True)
class OptionBinding:
    """One OPTIONS entry: its label, the 15FCE selector it writes, the channel it
    rebinds and whether it hides its control.

    `name` and `selector` are recovered (see `find_option_bindings`). `channel` and
    `hides_control` were RECOVERED 2026-09-02 (`optiongates.py`): both are read off the
    type-6 switches keyed on the row's value slot. `hides_control` is True when some such
    switch draws a control or container at the row's ON value and nothing at 0;
    `channel` is the one DC_DIMMER_COMMAND_2 instance the revealed control commands, and
    stays None when there is none (Black Tank reveals a gauge) or more than one (Bunk
    Accent's group 34 spans {23, 34}) -- never a pick from an ambiguous set.

    `name` is a **display** name: the caption's first line, whitespace-stripped, not the
    byte-exact stored label (see `find_option_bindings`). Producing one of these requires
    **capstone**, since the selector half is disassembled out of GuiCode."""
    name: str
    selector: int
    channel: int | None = None
    hides_control: bool = False


def find_option_bindings(data: bytes) -> list[OptionBinding]:
    """`name -> 15FCE selector` for every OPTIONS entry whose selector is recoverable.

    RECOVERED 2026-08-15, and **not from a GuiData record** -- the searches below stay
    valid, because there is no such record. The join is a three-step chain across two
    already-proven mechanisms plus one new one:

        options.option_bindings(data)   name -> value slot -> SRAM register  (GuiData,
                                        GUICODE Findings 16-17)
        config.config_offsets(data)     SRAM register -> config-buffer offset (GuiCode's
                                        config-unpack, Finding 20)
        config.option_selector(data, o) config-buffer offset -> selector (GuiCode code
                                        adjacency; see config.py's "selector join" section)

    The last step is the one that was missing. The selector is a literal byte inside a
    static 12-byte CAN-frame template in GuiCode's `.rodata`, copied wholesale by an
    `ldm {r0,r1,r2}` in the row's own tap handler; the config offset is a literal
    immediate in the same straight-line run. Nothing stores the pair, which is why every
    table search below was correctly negative.

    Both halves of a name's identity are attested twice over: the register comes from
    GuiData (`options.option_bindings`) and from GuiCode (`config.config_offsets`),
    which share no code path.

    An option whose selector is ambiguous or unrecovered is **omitted, never guessed** --
    `config.option_selector` returns None when a row's sites disagree. The returned list
    is therefore not always the whole OPTIONS page, and it is wider than the subject
    coach's six rows: it covers every OPTIONS-labelled tab in the file, so 13.6 also
    yields AGS/AES/AES Single Output (all three write 0xEF, with values 1/2/3) and
    Progressive Inverter (0xE6). Rows sharing a register share a selector by
    construction; that is the file's own arrangement, not a collision.

    `name` is the row's caption with any hard-wrapped continuation lines dropped **and
    surrounding whitespace stripped**: 13.6 stores the Cargo row as
    `'   Cargo / Bath Light\\n    (CH. 25)'` and this returns `'Cargo / Bath Light'` -- one
    caption wrapped across two rendered lines (`MCF-FORMAT.md` Section 6 -- the renderer
    reproduces the stored newline *and* the leading spaces literally). **Those leading
    spaces are load-bearing elsewhere in this project** and are being discarded here: a
    same-size rename pads on the right precisely because the renderer left-aligns and
    reproduces stored indent verbatim (`MCF-FORMAT.md` Section 6; `patch.McfPatcher.set_label`).
    So `OptionBinding.name` is a display name for humans, **not** the byte-exact stored label
    -- do not feed it back to any patch primitive. (`options.option_bindings` strips too;
    only the file bytes and a record's own decoded label carry the indent.)

    Requires **capstone** -- new as of 2026-08-15, and a real but undeclared dependency (the
    same note `render.py:665` carries about its own use). Both `config.config_offsets` and
    `config.option_selector` disassemble GuiCode and raise `theme.CapstoneUnavailable`
    without it, so this function raises it too, where before the selector join it read
    GuiData only. Note the consequence for the test suite: the two remaining strict `xfail`s
    in `tests/test_option_bindings.py` are `raises=AssertionError` and call this function, so
    on a machine without capstone they would report **errors**, not xfails or skips.

    THE OTHER TWO FIELDS -- RECOVERED 2026-09-02, closing the last two strict `xfail`s in
    `tests/test_option_bindings.py` (`optiongates.py` has the full account):

      * **`channel`** -- there is no stored channel and no rebinding. 0xE3 and 0xE4 each
        key their OWN type-6 switch on every LIGHTS page (13.6 objects 940-949), whose ON
        arm is a 'Bath' control commanding channel 25 (object 370, group 25) or 26 (object
        374, group 26). The tap handlers' mutual exclusion (Finding 50, PC.33) is what makes
        one of the two visible at a time, so it *looks* like one control being rebound. The
        tap handlers' companion `0x1FEDB` OFF templates (Finding 23) give the same numbers
        from GuiCode (`config.off_channels_by_selector`); the two routes share no bytes.
      * **`hides_control`** -- there is no visibility flag. The row's value slot keys a
        switch whose 0 arm is empty (13.6: 375 for Bunk Accent, 801 for Black Tank) and
        whose 1 arm is the control (376) or gauge (802). That was always Finding 16's
        mechanism; what was searched for before was a *flag*, and none exists.

    WHY NO GUIDATA RECORD CARRIES THE SELECTOR -- searched and ruled out, all measured
    on the corpus, and still true:

      * **The screen records.** The task brief's plan was to scan each option widget's
        fields for its selector. The OPTIONS screen object (13.6, section
        0x70AD4..0x70E24, 848 bytes) contains none of the six bytes -- not one
        occurrence of 0xEB, 0xE5, 0xE9, 0xE3, 0xE4 or 0xEC anywhere in it -- and no
        `ce 5f` either. The rows differ only in a row id, an icon id and two style
        bytes.
      * **The binding table, by any index.** No entry in any build carries DGN 0x15FCE
        (0 of 746 in 13.6, 732 in 11.23, 747 in 14.7), so no group index can reach a
        selector.
      * **The row id as a group index.** The six rows carry ids 0x6B..0x70. Group
        entries 0x6B and 0x6C are `10 00 04 80 8d 01 03 00` and
        `10 00 04 80 90 01 03 00` -- (start 397, count 3) and (start 400, count 3) --
        and the binding entries they select are `d9 fe 05 06 0f 16 96 ff ff d2 ff fb`
        and the same with trailing `00 05`: a -5/+5 adjust pair on target 0x16. Groups
        0x6D/0x6E select a -1/+1 pair on 0x15. Numeric adjust pairs, not booleans.
        (Full disproof in the "Why the OPTIONS row id is not an index into the group
        table" section above.)
      * **The row id as an index into any other table.** Variable-table entries
        107..112 are `fd ff 29 06 01`, `e8 fe 29 ..`, `00 ef 1c ..`, `96 ff 1d ..`;
        object-table entries 107..112 are dividers and 2-record fragments; binding
        entries 107..112 are dimmer commands on instances 36, 24, 24, 24, 24, 25 --
        four of the six naming the same channel, so they cannot distinguish six
        options. None of them is a 15FCE anything.
      * **A stored ordering of the selectors.** For every stride k in 1..32 and every
        starting offset, no position in any of the three mcf.bin files holds
        0xEB, 0xE5, 0xE9, 0xE3, 0xE4, 0xEC in display order. Nor does any file hold the
        variable indices 287, 285, 283, 286, 284, 288 as a byte or u16 sequence.
      * **A (row id, selector) pair table.** For each of the six pairs, the offsets
        where the row id sits within 16 bytes of its selector were enumerated (32, 9,
        7, 7, 41 and 71 candidates in 13.6); no constant stride in -64..64 links one
        candidate from each of the six sets. There is no such table in the file.
      * **The variable table's option run.** 13.6 entries 283..288 are
        `ce 5f 1d 02 {e9,e4,e5,e3,eb,ec}`, the six selectors contiguous -- but in
        neither display order nor any order recoverable from the run, and 14.7 keeps
        six entries in the same slots (0xE7 replacing 0xE9) while its OPTIONS screen
        shows only five rows. Position in the run cannot name an option.
      * **GuiCode.** The `Lyra 13v6 GuiCode` section contains zero occurrences of
        `ce 5f`, and two of the six (row 109/0xE9 and row 110/0xE3) never co-occur
        within 16 bytes anywhere in it.
      * **The G12 section.** Its rule table (tag 0x62, section 0x470, 94 entries of 12
        bytes) declares each selector as `ce 5f c1 0c XX XX 00 XX 00 00 00 00` -- 0xEB
        at entry 67, 0xE5 at 80, 0xE9 at 69 and 87, 0xE3 at 90, 0xE4 at 86 -- and the
        run from entry 67 to 92 is nothing but such declarations. No channel, name or
        visibility flag sits next to any of them.

    HOW THAT LIST WAS RIGHT AND STILL POINTED THE WAY. Its closing note said the row id
    is not build-stable, "which points at the GuiCode bytecode rather than at another
    table ... it would have to be computed, not stored, to survive the stride and pair
    searches above". The first half held; the second was half right. GuiCode is indeed
    where the join lives, but the selector is not computed there either -- it is a
    literal byte in a pre-built frame, joined to the offset by nothing but code
    adjacency. That is why it survived every stride and pair search: the two bytes are
    never near each other in the file at all. The row id plays no part.

    Returning a fitted mapping would still be easy and worthless -- `answer_key.OPTIONS`
    is six names against six bytes, so any permutation-finding heuristic can be tuned
    until it matches. Nothing here is fitted: `config.selector_sites` cites, per row, the
    `bl`/gate instruction and the `.rodata` template address it read the byte from, and
    the seven known pairs were used only to check the result afterwards.
    """
    from . import config, optiongates, options

    offsets = {register: offset for offset, register in config.config_offsets(data).items()}
    out: list[OptionBinding] = []
    for option in options.option_bindings(data):
        offset = offsets.get(option.register)
        if offset is None:
            continue
        selector = config.option_selector(data, offset)
        if selector is None:
            continue
        on_value = options._option_value(option.name)
        out.append(OptionBinding(
            name=option.name.splitlines()[0].strip(), selector=selector,
            channel=optiongates.gated_channel(data, option.value_slot, on_value),
            hides_control=optiongates.hides_control(data, option.value_slot, on_value)))
    return out


# ---------------------------------------------------------------------------
# Structural locators: the match-value table and the variable register tables
# ---------------------------------------------------------------------------
#
# `layout.py` reads two more GuiData structures that are unrelated to the DGN-keyed
# `variable_table` above despite the similar name: a per-object match-value table (used
# by a type-6 switch to pick its rendered candidate, GUICODE Finding 16) and a pair of
# arrays -- SRAM data pointers and their types -- that a value-slot resolves through to
# the register a checkbox or switch actually reads (GUICODE Finding 17). Neither has a
# `(tag, offset, size)` descriptor in the header run above: both are addressed only
# from GuiCode (`DAT_00257BF8` and `DAT_00253D04`/`DAT_00253D08`), so unlike the group/
# binding/(DGN-)variable tables they cannot be found by reading a header field and have
# to be found from the shape of the bytes. `layout.py` used to hardcode their 13.6
# offsets (`MATCH_TABLE_136`, `VAR_DPTR_TABLE_136`, `VAR_TYPE_TABLE_136`); the two
# finders below locate them the same way on all three corpus files.


class MatchTableNotFound(ValueError):
    """No window of this GuiData has the shape of the per-object match-value table."""


class VariableTableNotFound(ValueError):
    """No run of this GuiData has the shape of the variable register table pair."""


# A non-0xFFFF match-value entry is always small: measured on the known 13.6 table
# (`layout.MATCH_TABLE_136`, GuiData+0x4562C), every one of its 1273 non-0xFFFF entries
# is under 256 (max 255). That is a hard filter, not a fitted threshold. NOTE: the
# common description of this table as "overwhelmingly 0xFFFF" does not hold on a literal
# tally -- 13.6's table is only 10.5% 0xFFFF (149 of 1422), the rest split mostly
# between 0 (424) and 1 (289) -- so that fraction is not used here as a filter. What is
# real, and is the shape `Screen._switch_child` actually walks, is that the table is
# built from many runs of ascending values 0, 1, 2, ... -- one run's start value is a
# switch group's own base (which renders when the driven value is 0).
MATCH_TABLE_VALUE_BOUND = 256
# A candidate must contain at least one ascending-from-zero run to be considered a
# match table at all, not just a coincidental all-small region (e.g. a run of padding
# zeros, which would otherwise pass the value-bound filter trivially).
MATCH_TABLE_MIN_SCORE = 1
# `src/fireflymcf/insert.py` appends objects (growing `object_tables[1]`) WITHOUT
# extending this per-object table -- an appended top-index object is never a type-6 switch
# candidate, so no switch ever reads its entry and the table stays one-per-*original*-object
# (functionally complete). So after K such inserts the table is K entries short of the object
# count; the locator tries the object count first (exact, unchanged for un-patched files) then
# falls back to narrower widths, up to this many, to still find the (shorter) table.
MATCH_TABLE_MAX_APPENDED = 16


def find_match_table(data: bytes) -> int:
    """GuiData-relative offset of the per-object match-value `u16` table.

    Located by structure, not fitted to the known constant. A candidate window is
    exactly `len(object_tables(data)[1])` consecutive `u16`s -- the table is parallel to
    the screen/object table, one entry per object -- where EVERY entry is 0xFFFF or
    smaller than `MATCH_TABLE_VALUE_BOUND`. That hard filter alone admits 24719 of the
    ~415k possible start positions in 13.6's GuiData. Candidates are then scored by how
    many times the literal sequence `0, 1, 2` occurs -- the start of an ascending
    switch-group run (GUICODE Finding 16) -- and the highest-scoring candidate is
    returned. On all three corpus files exactly one candidate reaches the maximum score
    (73 of the 24719 candidates in 13.6, with the next-highest candidate nowhere near
    the top since scores are counted, not thresholded); on 13.6 that candidate is
    `layout.MATCH_TABLE_136` (0x4562C) -- checked in `tests/test_guidata.py`, which
    locates first and only afterward compares to the constant, per this task's ruling.

    The window width is the object count on an un-patched file; on a build carrying objects
    appended by `insert.py` (which grows the object table but not this per-object table) the
    width is retried downward by up to `MATCH_TABLE_MAX_APPENDED` so the shorter table still
    locates (see that constant).

    Anchored first: the table ENDS where object table 1 begins (`object_table_offset(data, 1)`;
    `relocate.grow_match_table` inserts there and an appended object never moves it, so it holds
    on every build), and the u16 before the table's first entry is half of a variable-table SRAM
    pointer (`0x2003` on all three corpus files) that fails the hard filter. Walking back from
    that end while entries pass the filter gives the table exactly, however many objects were
    appended since it was last grown: the width scan below gives up after
    `MATCH_TABLE_MAX_APPENDED` (a 58-op build appended more). The walk is bounded at `n + 2`
    entries (the table is at most one 4-alignment pad wider than the object count) and must
    score at least `MATCH_TABLE_MIN_SCORE`; a file where it does not hold falls back to the
    width scan.

    Raises `MatchTableNotFound` if no window at any tried width satisfies the hard filter with
    a score of at least `MATCH_TABLE_MIN_SCORE`.
    """
    gs, ge = guidata_extent(data)
    n = len(object_tables(data)[1])
    m = (ge - gs) // 2
    if n == 0 or m < n:
        raise MatchTableNotFound(
            "GuiData is too short to hold a table this wide")
    import numpy as np
    arr = np.frombuffer(data[gs:gs + 2 * m], dtype="<u2").astype(np.int64)

    is_ffff = arr == 0xFFFF
    is_small = (~is_ffff) & (arr < MATCH_TABLE_VALUE_BOUND)
    ok = (is_ffff | is_small).astype(np.int64)
    ok_csum = np.concatenate(([0], np.cumsum(ok)))

    run_start = np.zeros(m, dtype=np.int64)
    if m >= 3:
        run_start[:-2] = (arr[:-2] == 0) & (arr[1:-1] == 1) & (arr[2:] == 2)
    run_csum = np.concatenate(([0], np.cumsum(run_start)))

    try:
        end = object_table_offset(data, 1) // 2
    except (ValueError, IndexError):
        end = None
    if end is not None and 0 < end <= m:
        lo = max(0, end - (n + 2))
        k = end
        while k > lo and ok[k - 1]:
            k -= 1
        if k > lo and end > k and run_csum[end] - run_csum[k] >= MATCH_TABLE_MIN_SCORE:
            return k * 2

    # Try the exact object count first (unchanged behaviour for un-patched files), then fall
    # back to narrower widths so a build with objects appended by `insert.py` -- which grows
    # the object table but not this per-object table -- still locates its (correspondingly
    # shorter) table. The real table always carries the maximum 0/1/2-run score at its own
    # width, so the first width that yields a scoring candidate returns it.
    for width in range(n, max(1, n - MATCH_TABLE_MAX_APPENDED) - 1, -1):
        if m < width:
            continue
        window_ok = (ok_csum[width:] - ok_csum[:-width]) == width
        scores = np.where(window_ok, run_csum[width:] - run_csum[:-width], -1)
        best = int(np.argmax(scores))
        if scores[best] >= MATCH_TABLE_MIN_SCORE:
            return best * 2
    raise MatchTableNotFound(
        "no window of this GuiData has the shape of the match-value table")


# An SRAM address on this STM32F7 falls in 0x20000000..0x20100000 (the 1 MB SRAM
# region); every data-pointer entry seen on the corpus (13.6, 11.23, 14.7) is either
# that or a bare 0. A run of at least this many REAL (nonzero) SRAM pointers is treated
# as the table -- measured margin is enormous: the winning run is 504-664 entries long
# on the three corpus files, and the next-best run anywhere else in the same GuiData
# tops out at 3.
SRAM_LOW = 0x20000000
SRAM_HIGH = 0x20100000
VARIABLE_TABLE_MIN_RUN = 20
# A type byte is a small enum (0..10 observed across all three files: bool, u8, etc.).
# Used only to find where the preceding type array starts, scanning backward from the
# data-pointer table; generous relative to the observed max so as not to be fitted to
# it.
VARIABLE_TYPE_MAX = 32
# How far back to look for the type array's leading edge. The real type arrays are
# 516-668 bytes on the corpus; this bounds the backward scan so a pathological run of
# small bytes elsewhere cannot make it unbounded.
VARIABLE_TYPE_SCAN_WINDOW = 8192


def find_variable_tables(data: bytes) -> tuple[int, int]:
    """`(data_ptr_off, type_off)`: GuiData-relative offsets of the variable register
    tables `layout.variable_register` reads.

    `data_ptr_off` is the u32 SRAM-pointer array: the maximal run of GuiData words that
    are each either 0 or an address in `[SRAM_LOW, SRAM_HIGH)`, scored by how many are
    real (nonzero) SRAM pointers rather than by raw length -- otherwise a stretch of
    zero padding elsewhere would out-run the real table. On the corpus this is not a
    close call: 13.6 scores 664, 11.23 scores 561, 14.7 scores 504, and nothing else
    in any of the three files scores above 3.

    `type_off` is the adjacent u8 type array, found by scanning backward from
    `data_ptr_off` while bytes stay under `VARIABLE_TYPE_MAX`, then rounding forward to
    the next 4-byte boundary. The rounding matters: the type array's own leading bytes
    are small too (13.6 starts `00 00 01 0a ...`), so the raw backward scan overruns
    into a few bytes of unrelated alignment padding before it (measured: 3 bytes in
    13.6, landing on 0x44929 instead of 0x4492C) -- alignment is what recovers the exact
    boundary, since every table in this format starts on a 4-byte edge.

    On 13.6 this returns exactly `(layout.VAR_DPTR_TABLE_136, layout.VAR_TYPE_TABLE_136)`
    = `(0x44BC8, 0x4492C)` -- checked in `tests/test_guidata.py` after locating, not
    fitted to it. Raises `VariableTableNotFound` if no run scores at least
    `VARIABLE_TABLE_MIN_RUN`.
    """
    gs, ge = guidata_extent(data)
    n_words = (ge - gs) // 4
    if n_words == 0:
        raise VariableTableNotFound("GuiData is too short to hold this table")
    import numpy as np
    words = np.frombuffer(data[gs:gs + 4 * n_words], dtype="<u4").astype(np.int64)

    is_sram = (words >= SRAM_LOW) & (words < SRAM_HIGH)
    is_zero = words == 0
    valid = is_sram | is_zero

    # Score every maximal run of `valid` words by its count of real SRAM pointers, and
    # keep the best. A vectorised run-length pass: label each run, then bin the
    # per-word "is real" flag by label.
    boundary = np.zeros(n_words, dtype=bool)
    boundary[0] = valid[0]
    boundary[1:] = valid[1:] & ~valid[:-1]
    run_id = np.cumsum(boundary) - 1   # -1 outside any run

    best_start = -1
    best_real = 0
    in_run = np.nonzero(valid)[0]
    if in_run.size:
        ids = run_id[in_run]
        real_flags = is_sram[in_run].astype(np.int64)
        totals = np.bincount(ids, weights=real_flags)
        best_id = int(np.argmax(totals))
        best_real = int(totals[best_id])
        best_start = int(in_run[ids == best_id][0])

    if best_start < 0 or best_real < VARIABLE_TABLE_MIN_RUN:
        raise VariableTableNotFound(
            "no run of this GuiData has the shape of the variable data-pointer table")
    dptr_off = best_start * 4

    lo = max(0, dptr_off - VARIABLE_TYPE_SCAN_WINDOW)
    segment = np.frombuffer(data[gs + lo:gs + dptr_off], dtype=np.uint8)
    bad = np.nonzero(segment > VARIABLE_TYPE_MAX)[0]
    pos = lo + (int(bad[-1]) + 1 if bad.size else 0)
    type_off = (pos + 3) & ~3

    # A `tablegrow` relocation moves these tables to appended GuiData and repoints the GuiCode
    # base literals that index them; the panel then reads via those literals, so they are
    # authoritative. The structural scan above can pick the stale pre-grow copy or the relocated
    # one inconsistently once two copies exist, so if a 13.6 cell-base literal points at a
    # DIFFERENT, still-valid variable table, trust it. Guarded to leave non-13.6 and ungrown
    # images on the structural result (see `_relocated_variable_tables`).
    override = _relocated_variable_tables(data, gs, ge)
    if override is not None:
        return override
    return dptr_off, type_off


# 13.6 GuiCode literals that load the two variable-table bases (see `readout._CELL_BASE_REF` /
# `_TYPE_BASE_REF`); a relocation repoints them to the tables' new home.
_VARTAB_CELL_LIT = 0x2535B4
_VARTAB_TYPE_LIT = 0x253D08
_GUIDATA_MAP_BASE = 0x90000000


def _relocated_variable_tables(data: bytes, gs: int, ge: int):
    """`(dptr_off, type_off)` from the GuiCode base literals if a grow relocated the tables, else
    `None`. Trusted only when the cell-base literal is no longer the stock 0x44BC8, both literals
    are in-GuiData pointers, the type table precedes the cell table (stock layout), and the cell
    base actually points at a run of SRAM pointers -- so a non-13.6 image (whose literal at this
    address is unrelated bytes) and an ungrown image both fall through to the structural scan."""
    from . import theme
    try:
        sec = theme.guicode_section(data)
        base = theme.guicode_base(sec)
    except Exception:
        return None

    def _rd(runtime):
        off = sec.offset + (runtime - base)
        if off < 0 or off + 4 > len(data):
            return None
        return struct.unpack_from("<I", data, off)[0]

    cell_lit = _rd(_VARTAB_CELL_LIT)
    type_lit = _rd(_VARTAB_TYPE_LIT)
    if cell_lit is None or type_lit is None:
        return None
    if cell_lit == _GUIDATA_MAP_BASE + 0x44BC8:      # stock cell base: not grown
        return None
    span = ge - gs
    if not (_GUIDATA_MAP_BASE <= cell_lit < _GUIDATA_MAP_BASE + span):
        return None
    if not (_GUIDATA_MAP_BASE <= type_lit < _GUIDATA_MAP_BASE + span):
        return None
    new_dptr = cell_lit - _GUIDATA_MAP_BASE
    new_type = type_lit - _GUIDATA_MAP_BASE
    if new_type >= new_dptr or new_dptr % 4:         # type before cell; cell base 4-aligned
        return None
    real = 0
    for i in range(16):
        off = gs + new_dptr + i * 4
        if off + 4 > len(data):
            break
        w = struct.unpack_from("<I", data, off)[0]
        if SRAM_LOW <= w < SRAM_HIGH:
            real += 1
    if real < 8:                                     # a real relocated cell table, not a coincidence
        return None
    return new_dptr, new_type


def switch_candidates(data: bytes, rec, match_off: int | None = None) -> list[tuple[int, int]]:
    """The decoded candidate-child list of a type-6 / 0x2E value-match switch record.

    Returns ``[(match_value, object_index), ...]`` for every object in the switch's scan
    range ``[child_index, child_index + count)`` (`records.WRecord.child_index` is the
    child base, `.count` the `+0x94` candidate count). ``match_value`` is read from the
    parallel per-object match-value table (`find_match_table`) at that object's index; the
    firmware renders the one candidate whose value equals the slot the switch reads
    (`FUN_00256C54`'s scan at `0x00257B3A`, `GUICODE.md` Findings 15/16). The base entry's
    value is ``0`` for an ascending group and ``0xFFFF`` for an explicit no-match default;
    both are returned verbatim so the caller can decide what a default means.

    This is the structure a single record's bytes cannot carry -- the candidates live in the
    object table and their selectors in a table parallel to it -- which is why `records.py`
    stops at ``child_index``/``count`` and does not expand the list (`MCF-FORMAT.md` §10
    item 5). ``match_off`` (GuiData-relative) pins the match table; ``None`` locates it
    structurally per `data` via `find_match_table`, correct on 11.23 / 14.7 too. A record
    that is not a switch, or carries no ``count``, yields ``[]``.
    """
    if getattr(rec, "wtype", None) not in (6, 0x2E) or rec.child_index is None or not rec.count:
        return []
    gs, _ge = guidata_extent(data)
    off = find_match_table(data) if match_off is None else match_off
    n = len(object_tables(data)[1])
    out = []
    for obj in range(rec.child_index, rec.child_index + rec.count):
        if not 0 <= obj < n:                       # a real switch stays in range; be defensive
            continue
        value = struct.unpack_from("<H", data, gs + off + obj * 2)[0]
        out.append((value, obj))
    return out


def reachable_objects(data: bytes, root: int = 0, match_off: int | None = None,
                      follow_switch_candidates: bool = True) -> set[int]:
    """Object indices reachable from ``root`` in the ``+0x46148`` object graph.

    Recursion follows exactly the three container types the renderer descends into --
    plain containers (5) and value-match switches (6, 0x2E) -- via each record's
    ``child_index`` (`FUN_00256C54` dispatches only 5/6/0x2E to `FUN_00258330`,
    `GUICODE.md` Finding 15). A type-6/0x2E switch is not a single-child container: it has a
    *candidate list*, and with ``follow_switch_candidates`` (the default) every candidate in
    ``[child_index, child_index + count)`` is followed. That is what reaches a page *body*:
    on 13.6 the CLIMATE page (object 7) is candidate value 3 of the top screen switch
    (object 1), so a base-only walk descends into the default screen and never into CLIMATE
    (`MCF-FORMAT.md` §10 item 5, the "reaches the tab but not the body" residual). Set
    ``follow_switch_candidates=False`` for that base-only walk.

    The match table is located once per call (``match_off=None`` -> `find_match_table`) and
    threaded down, so the numpy locate runs at most once. Undecodable objects are skipped
    rather than raised on, so a walk over a partly-decodable build still terminates.
    """
    from . import records as _records

    tab = object_tables(data)[1]
    n = len(tab)
    gs, _ge = guidata_extent(data)
    off = None
    if follow_switch_candidates:
        off = find_match_table(data) if match_off is None else match_off

    seen: set[int] = set()
    stack = [root]
    while stack:
        obj = stack.pop()
        if obj in seen or not 0 <= obj < n:
            continue
        seen.add(obj)
        try:
            recs = _records.object_records(data, gs + tab[obj])
        except Exception:
            continue
        for r in recs:
            if r.wtype not in (5, 6, 0x2E) or r.child_index is None:
                continue
            if r.wtype in (6, 0x2E) and follow_switch_candidates and r.count:
                stack.extend(c for _v, c in switch_candidates(data, r, off))
            else:
                stack.append(r.child_index)
    return seen
