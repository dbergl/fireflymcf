# Where the MCF is parsed

**Answer: `cli.bin`, region 1, loaded at `0x20000000`.** It is the only image in the
corpus that contains the MCF magic `0xFD53B912`, and it contains a complete container
parser: header validation, the descriptor pointer array, the descriptor walk, and the
payload copy to flash.

All Ghidra addresses below are from `extractions/13.6/cli-region1.bin` imported at
`0x20000000` with `./ghidra/run_headless.sh` (project `cli136r1`). **14.7 is
byte-identical at every address quoted**; 11.23 is the same code shifted (its magic
literal is at `0x200166A0`, loaded by `LDR r3,[pc,#684]` at `0x200163F2`). Ghidra glues
`0x20015DD8`–`0x200174D7` into one function, `FUN_20015DD8`, because of the `tbh`/`tbb`
jump tables; the MCF parse is one state of that state machine, not a separate function.

Every claim in the "Proven" sections below cites the instruction that proves it. The
"Inferred" section is separated deliberately and is not to be relied on as fact.

---

## How the image was found

`xref.scan_images` over every extracted image, all three firmware versions — the four
MCF sections plus both `cli.bin` regions, 18 images in total:

| image | `0xFD53B912` |
|---|---|
| `cli-region1.bin` (11.23) | `0x166A0` |
| `cli-region1.bin` (13.6, 14.7) | `0x1635C` |
| every other image, all versions | *none* |

`LCD Firmware`, `GuiCode`, `GuiData`, `G12` and `cli-region2.bin` contain no occurrence
of the magic in any version.

Both hits are 4-byte aligned, consistent with a Thumb literal pool. Scanning all Thumb
PC-relative literal loads (T1 `LDR Rt,[PC,#imm8*4]` and T2 `LDR.W Rt,[PC,#±imm12]`) that
target that address finds **exactly one** in each file:

```
13.6 / 14.7   0x2001615E   ldr r3,[0x2001635C]
11.23         0x200163F2   ldr r3,[0x200166A0]
```

(Scope note: that scan covers the two PC-relative literal-load encodings only. A
literal reached by some other addressing mode would not be found — but Ghidra's own
reference analysis independently reports the same single reference.)

---

## Proven: the container header

Immediately around the literal load, in `FUN_20015DD8`:

```
20016144  movs r2,#0xa4              ; length
20016146  subw r0,r1,#0x484          ; r0 = 0x20035234, the stream object
2001614a  bl 0x20011c60              ; read(stream, dest=0x200356B8, len=0xA4)
...
2001615a  ldr.w r2,[r4,#0x8e8]       ; r4 = 0x20034DD0, so this is 0x200356B8 + 0 = header[0x00]
2001615e  ldr r3,[0x2001635c]        ; r3 = 0xFD53B912
20016160  cmp r2,r3
20016162  bne 0x20016150             ; -> error state 0xC
```

`0x200356B8` is the header buffer (it is also the literal at `0x20016358`). The header
is read as a fixed **`0xA4`-byte** block; every header field below is a fixed offset
into that buffer.

| file offset | width | proven by | meaning |
|---|---|---|---|
| `+0x00` | u32 | `0x2001615E`–`0x20016162` | magic `0xFD53B912` |
| `+0x04` | u16 | `0x200161C6` `ldrh r1,[r4,#0x8ec]` then `subs r1,#4`, seek, read 2 bytes at `0x200161E0`, compared at `0x200161F0` against the checksum accumulated over the header and pointer array | a file offset; the parser uses it **only** to locate that u16 checksum, at `hdr[0x04] - 4`. It happens to equal the first descriptor's offset (`0xB8`) on this corpus, but nothing in the code reads it as such |
| `+0x06` | u16 | **no field access reads it** (see below) | **reserved.** Constant `0x0001` in all three builds; folded into the header checksum but interpreted by nothing |
| `+0x08`..`+0x1F` | 24 B | **no field access reads it** (see below) | **reserved.** All zero in all three builds; folded into the header checksum but interpreted by nothing |
| `+0x20` | u16 | `0x20016164` `ldrh r3,[r4,#0x908]`, `cmp r3,#0x100`, `bhi` error | descriptor count, rejected above `0x100` |
| `+0x22` | u16 | `0x20016196` `ldrh r1,[r4,#0x90a]` fed straight into the seek at `0x2001619C` | header size **and** the file offset of the descriptor pointer array |

`+0x22` was previously recorded as "unexplained; NOT the 0x84 table stride". It is the
header size. `0x2001617C`–`0x20016192` computes `((hdr[0x22] - 0xA4) >> 2) - 1` as a
countdown of extra u32 words to fold into the header checksum; when `hdr[0x22] == 0xA4`
that yields `0xFFFF` and the extra-word loop is skipped.

**`+0x06` and `+0x08..+0x1F` are read by nothing (`proven-from-code`, 2026-09-10, closing
the header half of `MCF-FORMAT.md` §10 item 3).** The header struct lives at `[r4, #0x8e8]`
(so `hdr[+N]` is `[r4, #0x8e8 + N]`), and a scan of all of region 1 for `ldr/ldrh/ldrb/str.w
[r4, #imm12]` accesses in the header range finds exactly four field offsets touched —
`{0x00, 0x04, 0x20, 0x22}` — in both 13.6 and 14.7 (byte-identical clis). Those offsets are
`≥ 0x8e8`, so only the wide T3 encoding can reach them; the narrow T1 immediate cannot, which
makes the absence of any `+0x06` / `+0x08..0x1F` read a real negative rather than a scan blind
spot. The header block *is* consumed whole — by the checksum call at `0x20016178` — but no
byte of `+0x06..+0x1F` is read as a value. Pinned by
`tests/test_container.py::test_the_loader_reads_only_four_header_fields` and
`::test_the_reserved_header_bytes_are_constant_across_the_corpus`. *(11.23 is the shifted
build: its header struct sits at a different `r4` offset, so this `0x8e8`-relative scan does
not transfer to it — but the container header layout is byte-for-byte identical in all three
files, and 11.23's parser is the same code, so it reads the same four fields.)*

On this corpus `hdr[0x04] = 0xB8` and `hdr[0x22] = 0xA4` in all three files.

## Proven: descriptors are found by a pointer array, not by a stride

```
20016196  ldrh.w r1,[r4,#0x90a]      ; r1 = hdr[0x22] = 0xA4
2001619c  bl 0x200120ec              ; seek(stream, 0xA4)
200161a4  ldrh.w r2,[r4,#0x908]      ; count
200161ac  lsls r2,r2,#0x2            ; count * 4
200161b0  bl 0x20011c60              ; read into 0x200470D0
```

and then, once per section, in the walk loop at `0x20016394`:

```
20016394  uxth r5,r7                 ; i
20016398  ldr.w r1,[r10,r5,lsl #0x2] ; r10 = 0x200470D0 -> ptr[i]
2001639c  bl 0x200120ec              ; seek(stream, ptr[i])
```

**The firmware never uses `0x84` as a stride.** It reads `count` u32 file offsets and
seeks to each one. On this corpus that array reads `[0xB8, 0x13C, 0x1C0, 0x244]` in all
three files, which is why the descriptors *appear* to be `0x84` apart.

## Proven: the descriptor layout

Offsets are descriptor-relative (descriptor 0 begins at file offset `0xB8`).

| offset | width | proven by | meaning |
|---|---|---|---|
| `+0x00` | u16 | `0x200163B8`–`0x200163C2`: `ldrh r2,[sp,#0xd4]` / `movw r3,#0xdecd` / `cmp` / `bne` error | magic `0xDECD` |
| `+0x02` | u16 | `0x200163C6` `ldrh r6,[sp,#0xd6]`, `subs r6,#8`, `lsrs r6,r6,#2`, `subs r6,#1` — the extra-word checksum countdown at `0x200164BE` | descriptor size; checksum u16 lands at `+size-4` |
| `+0x04` | 0x40 bytes | `0x20016402`–`0x2001640A` seeks to `ptr[i]+4` (`ldr.w r1,[r10,r5,lsl #0x2]` / `adds r1,#0x4` / `bl 0x200120ec`); `0x20016414`–`0x20016428` then reads `0x40` bytes (`movs r2,#0x40`) | section name, NUL-padded |
| `+0x44` | u8 | `0x20016446` `ldrb r2,[sp,#0xd4]` → stored at per-section `+0x901`; branched on at `0x20016466` | descriptor type: `0` for LCD/GuiCode/GuiData, `1` for G12 |
| `+0x45` | u8 | `0x20016450` `ldrb r2,[sp,#0xd5]` → stored at per-section `+0x902` (that copy is then never read); the live reads are `0x20016a5e`/`0x20016a8c` `ldrb.w r3,[r4,#0x239]` (state-struct copy, `[r5,#0x1f4+N]`), where **bit 1** (`lsls #0x1e`+`bpl`) and **bit 2** (`lsls #0x1d`+`bpl`) gate calls to `0x20014734`/`0x20014238` | **flags byte** (`0x03` in every descriptor: bits 0-1). A tested bitfield, not padding; per-bit meaning not fully traced |
| `+0x46` | u16 | `0x20016458` `ldrh r2,[sp,#0xd6]`, `cmp r2,#0x40`, `bhi` error; used as the loop bound at `0x200150A8` | count of the 8-byte tagged entries at `+0x58` |
| **`+0x48`** | **u32** | `0x20014FFE` `ldr.w r1,[r5,#0x23c]` → `0x2003500C` → seek at `0x20015004`, the same seek routine `0x200120EC` used for the descriptor pointers | **payload file offset** |
| **`+0x4C`** | **u32** | `0x20015018` `ldr.w r1,[r5,#0x240]` → `0x20035010` → passed as the size to the flash-erase call `0x20006DA8` at `0x20015020`; `0x20015104` `lsr.w r9,r3,#0x8` derives the `0x100`-byte page count and `0x20015120` `ldrb r7,[r5,#0x240]` the tail byte count | **payload length in bytes** |
| `+0x50 .. +0x57` | 8 B | **no access** — the fixed-field reads stop at `+0x4C` and the entry walk is indexed from `+0x58` (below), so nothing reads these bytes | **reserved**, all zero |
| `+0x58` | 8 × `+0x46` | `0x200150E2`–`0x200150EE` walks 8-byte entries (`add.w r1,r7,r0,lsl #0x3` / `ldrh r1,[r1,#0xc]` / `cmp r1,#0x40`), bounded by `+0x46` at `0x200150A8`; `0x200150F4` `ldr.w r4,[r3,#0x250]` then takes the u32 at `entry+4` of the entry whose tag is `0x40` | tagged entries; tag `0x40` carries the flash destination address |

The mapping from the RAM addresses to descriptor offsets is fixed by `0x2001647C`–
`0x20016488`, which reads `0x214` bytes starting at `ptr[i]+0x44` into the buffer at
`0x20035008`; therefore `0x20035008+k` is descriptor offset `0x44+k`. `0x2003500C` is
`+0x48` and `0x20035010` is `+0x4C`.

### `0x44` is CONFIRMED, in Task 3's frame

Task 3 modelled the table as starting at `0xBC` with the extent at entry-relative
`0x44`. The descriptors actually start at `0xB8` and the extent is at descriptor-relative
`0x48`; Task 3's entry base `0xBC` is the *name* field, which really is at
descriptor `+0x04`. Both frames name the same absolute bytes:

```
0xBC + 0x44 == 0xB8 + 0x48 == 0x100
```

So Task 3's structural derivation was right. `container.py` now reads the proven
positions and keeps `_find_extent_position` as a cross-check that runs on every file and
raises if the scan and the proven position ever disagree.

## Proven: `cli.bin` does not interpret any section payload

After the descriptor walk, `FUN_20014F2C` copies the payload verbatim:

1. `0x20014FFE`–`0x20015004` — seek to `desc[+0x48]`.
2. `0x2001500E`–`0x20015020` — erase `desc[+0x4C]` bytes at the destination address
   (`r4`, from the tag-`0x40` entry).
3. `0x2001513C`–`0x2001514A` — read `0x100` bytes from the stream; `0x2001517A`
   `bl 0x20006E48` programs them; `0x200151BC` `add.w r8,r8,#0x100` advances the flash
   pointer. `0x20015128`–`0x20015130` handles the `length & 0xFF` tail.
4. `0x200151E4`–`0x20015202` — checksum `length-4` bytes read back from flash and
   compare against the u16 stored at flash offset `(length-4) & ~1`.

Between the seek and the flash write there is **no** inspection of the payload bytes.
Corroborating scan: neither `cli.bin` region, in any of the three versions, contains
`GuiData`'s first word `0x03142066` or `G12`'s `0x07162066`.

Consequence: **`cli.bin` is not the authoritative parser of `GuiData`.** It is a
transport — it moves the section from the MCF into flash and verifies a trailing
checksum.

Two further proven facts that fall out of the destination addresses (tag-`0x40` entry,
descriptor `+0x5C`):

| section | destination | independent corroboration |
|---|---|---|
| `LCD Firmware` | `0x00210000` | `armimg.looks_like_vector_table(payload, 0x00210000, len)` is `True` in all three files — reset `0x002104D0`, and **nine** Thumb-tagged handlers all in range: `0x002199B5` and `0x00219A4D` as well as the `0x0021E2xx` cluster (13.6: `E279 E283 E29B E2A5 E2AF E2B9 E2BF`); and `cli.bin`'s literal `0x00210400` at `0x20014D88` points exactly at file offset `0x400` of the `LCD Firmware` payload, where the version magic `0x44D29AD6` sits in all three files |
| `Lyra … GuiCode` | `0x00240000` | see the fifth witness below |
| `Lyra … GuiData` | `0x90000000` | external QSPI flash; `0x90000000` also appears as a literal at `0x20017C84` and as an immediate at `0x20014BEA` |

**Fifth witness (contributed by review, re-derived here).** All three destinations
reappear as literals inside the `LCD Firmware` image itself, once it is read at base
`0x00210000` — in one literal pool, loaded by four instructions a few bytes apart, in
all three versions:

| literal | address | loaded by |
|---|---|---|
| `0x00240000` (GuiCode's destination) | `0x0021C010` | `0x0021BEC6` |
| `0x1A2B3C4D` (GuiCode's magic) | `0x0021C014` | `0x0021BEC8` |
| `0x8F7E6D5C` | `0x0021C020` | `0x0021BEFC` |
| `0x90000000` (GuiData's destination) | `0x0021C024` | `0x0021BF0C` |

That is the same "load base, load magic, compare" shape as `cli.bin`'s `FUN_20014C54`
(`0x20014D80`/`0x20014D84` loaded at `0x20014CBE`/`0x20014CF6`) — the same version-check
routine compiled into both the updater and the application. It is independent
corroboration of the destinations, which were derived from the MCF descriptors, and of
`0x00210000` as the base, since the pool only lines up at that base.

**This refutes Task 4's conclusion that `LCD Firmware` is "a compressed representation of
a multi-megabyte flash image".** It is an ordinary uncompressed Cortex-M image whose base
address is `0x00210000`; Task 4 only tested base `0` and so needed an implausible image
length. Note `0x20050000`, its initial SP, is the same as `cli.bin`'s because `cli.bin`
region 1 is a RAM-resident updater for the same MCU — `LCD Firmware` is that MCU's main
application, not a separate display processor's firmware. `src/fireflymcf/embedded.py`
has been corrected: `0x00210000` and `0x00240000` are now in `DEFAULT_BASES`, and
`identify()` resolves `LCD Firmware` (and only `LCD Firmware`) on this corpus.

---

## Where `GuiData` is parsed — open when written, **answered 2026-08-15**

> **Read the outcome at the end of this section first.** The parsers were found, in `GuiCode`.
> What follows is the reasoning as it stood when only `cli.bin` had been read; it is kept
> because the mis-aim is instructive, and because its closing instruction leaked into two other
> documents before being caught.

Not determined *at the time*. What the evidence then did and did not support:

- **`cli.bin` reads a few `GuiData` header bytes for version reporting.**
  `FUN_20014C54` at `0x20014CB2` does `ldrb r3,[r5,#0x1]` / `cmp r3,#0x20`, and at
  `0x20014CB8`–`0x20014CC2` checks the word at `r5+0x10000` against `0x8F7E6D5C`. Both
  match `GuiData` exactly: its word 0 is `0x03142066`, so byte 1 is `0x20`, and it
  carries `0x8F7E6D5C` at offset `0x10000` in all three versions. It then reads
  `r5[2]`, `r5[4]` and `r5[6]` as flags/id/version.
  *Why this is inferred, not proven:* `r5` is resolved by `FUN_20014BA0`, which looks up
  a tag (`0xB2285EA0` / `0x4C99B178`) in a directory at `0x90000000`, and **neither tag
  appears anywhere in the `GuiData` payload**. So the object being inspected is reached
  through a flash structure this corpus does not contain, and the identification rests
  on two matching constants rather than a traced data path.
  Either way this is header-only: no `GuiData` body parsing exists in `cli.bin`.

- **The `LCD Firmware` image is the strongest remaining candidate.** It is genuine ARM
  Thumb code at `0x00210000`, and it loads the same two magics as `FUN_20014C54`, in the
  same order and 12 bytes apart in its literal pool — `0x1A2B3C4D` at `0x0021C014`
  (loaded by `ldr r3,[pc,#328]` at `0x0021BEC8`) and `0x8F7E6D5C` at `0x0021C020`
  (loaded by `ldr r2,[pc,#288]` at `0x0021BEFC`), identically in all three versions.
  That is the same version-check routine compiled into the application. It is *not*
  evidence that the application parses `GuiData`'s body.

- **`Lyra GuiCode` was not disassembled *by this task*.** Its reasoning was wrong, and is
  kept here because the correction matters more than the note: it read *"it is not ARM code
  (its first word is the magic `0x1A2B3C4D`, per Task 4), so an ARM disassembly would be
  meaningless"*. **Corrected 2026-08-15:** `0x1A2B3C4D` is the section **header's** magic, not
  a statement about the body — GuiCode is **native ARM Thumb-2**, with a five-entry
  function-pointer vector at header `+0x0C` and no interpreter anywhere (`docs/GUICODE.md`
  Finding 1). It has since been disassembled for 13.6 and 14.7, which is what produced
  Findings 1–23, including the option→selector join that closed `MCF-FORMAT.md` §8.3 row 1.
  The pre-authorisation to disassemble it was not used *here*; it should have been.

- **A negative u32 scan is weak here.** No image in the corpus contains `0x03142066` as
  a u32 other than `GuiData` itself. But `cli.bin` itself checks `GuiData`'s signature
  one *byte* at a time (`cmp #0x20`), and a byte or u16 comparison uses an inline
  immediate that a 32-bit constant scan cannot see. Absence of `0x03142066` from an
  image is therefore **not** evidence that the image does not parse `GuiData`.

~~For the tasks that follow: `GuiData`'s internal layout must be labelled
`inferred-from-data` unless a parser for it is located. The `LCD Firmware` image at base
`0x00210000` is now a tractable Ghidra target and is where that search should start.~~

**⚠ That instruction is SUPERSEDED — do not copy it forward.** Parsers *have* been located, so
the blanket `inferred-from-data` rule it imposes is wrong and will make you downgrade cited
instructions. It had already been copied into `CLAUDE.md`'s confidence rule and
`MCF-FORMAT.md`'s preamble, where it survived several rounds of correction before being caught
on 2026-08-18. Both now carry the corrected form: labels are **per claim**, and
`proven-from-code` is available for GuiData fields whose reader is cited in either image.

**Outcome, 2026-08-15 — the search started in the wrong place.** The readers were in **`GuiCode`**,
the section this task declined to disassemble: `FUN_00252CB0` decodes a widget record,
`FUN_00256C54` dispatches it, `FUN_00257BFC` lays it out and `FUN_00258650`/`FUN_00258330`
walk the `+0x46148` object table (`docs/GUICODE.md` Findings 9–12). `MCF-FORMAT.md` §10
item 5 is written from that outcome and says GuiCode is now the **first** place to look;
this paragraph records where the search was pointed at the time, not where it should be
pointed now. The `LCD Firmware` remains the right target for its own questions (the
container/version check above, `docs/LCD-FIRMWARE.md`).

---

## Reproducing this

```bash
python3 -m fireflymcf.cli extract firefly-13.6/mcf.bin extractions/13.6
python3 -m fireflymcf.cli regions firefly-13.6/cli.bin extractions/13.6
./ghidra/run_headless.sh extractions/13.6/cli-region1.bin 0x20000000 cli136r1
```

Then in Ghidra, go to `0x2001635C` (the magic literal) and follow its single reference
to `0x2001615E`.
