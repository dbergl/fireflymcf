# The Firefly Integrations MCF format

What `mcf.bin` is, field by field, with a confidence label on every field.

**Corpus.** Three files, read-only throughout: `firefly-11.23/mcf.bin` (905,408 B,
`Jayco Ethos/Swift V11`), `firefly-13.6/mcf.bin` (1,067,000 B, `Jayco B-Van V13`) and
`firefly-14.7 (Terrain)/mcf.bin` (800,752 B, `Jayco B-Van V14`). All offsets are file
offsets in **13.6** unless stated; the subject coach runs 13.6, floorplan `WD`.

**Reader, `src/fireflymcf/`**, exercised by `python3 -m pytest tests/` (`docs/TESTING.md`). The
suite carries **no `xfail`s as of 2026-09-10**. It once carried seven, each encoding a structure that
was searched for and not found; every one closed by locating the mechanism rather than a stored table:
the selector join (`GUICODE.md` Finding 23), the per-floorplan option defaults (Finding 42), the
object → status-variable pairing (§10 item 11a, 2026-08-26), and the `E3`/`E4` channel binding and
`E5`/`EC` visibility flag (Finding 61, 2026-09-02). Where this document still says a test "stays
`xfail`", read it as a dated statement that has since closed.

---

## How to read the confidence labels

Every field below carries **exactly one** of these:

| label | means |
|---|---|
| `proven-from-code` | a located instruction reads this field. The citation is in `docs/PARSER-LOCATION.md`, which gives the address of every proof. |
| `inferred-from-data` | consistent across the corpus, and usually cross-checked against the vehicle, but **no code has been observed reading it**. |
| `observed-on-hardware` | from `docs/BUS-BASELINE.md` — seen on the physical coach over CAN or on its screen. The strongest evidence in this project, but evidence about the *vehicle*, not about the file. |
| `owner-supplied` | external product knowledge from the coach's owner. **Not derived from the file and not checkable against it** — it explains what a value *means* in the world, never what the bytes say. Used only where the file genuinely does not carry the information (so far: §7.1, the floorplan-code → model mapping). |
| `unknown` | present, stable, and not explained. |

**The asymmetry matters more than the individual labels — but it is no longer the same
asymmetry.** The MCF *container* is proven: its parser was located in `cli.bin` region 1
(loaded at `0x20000000`), the only image in the corpus containing the magic `0xFD53B912`.
`cli.bin` seeks to each payload and copies it to flash without inspecting a byte of it
(`docs/PARSER-LOCATION.md`, "cli.bin does not interpret any section payload") — so for a
long time nothing that parsed `GuiData` had been located, and every field inside it was
`inferred-from-data` by default.

**That default no longer holds, and this document's labels reflect where it has been
lifted.** Two of the four embedded images have since been disassembled, and between them
they parse most of GuiData's *structure*:

* **`LCD Firmware`** (base `0x00210000`, `docs/LCD-FIRMWARE.md`) reads the section header,
  the table directory and the variable table — §5.1 and §5.4a are `proven-from-code` on its
  instructions.
* **`GuiCode`** (base `0x00240000`, `docs/GUICODE.md`) holds the **record decoder**
  (`FUN_00252CB0`), the **widget renderer** (`FUN_00256C54`), the **layout engine**
  (`FUN_00257BFC`) and the **object-table walk** (`FUN_00258650`/`FUN_00258330`). The record
  field order in §6 is a transcription of that decoder, validated by a boundary oracle on
  every object in all three builds.

What stays `inferred-from-data` is narrower and specific: the **group and binding tables'**
remaining fields (§5.2, §5.3; a binding's DGN and payload positions are `proven-from-code` since
`GUICODE.md` Finding 83), the **floorplan table** (§7), the OPTIONS-row screen semantics (§6.1), and the
*meaning* of individual undecoded fields everywhere. Read each label; do not assume a
section-wide default in either direction.

---

## 1. Container header — `0x00 .. 0xA3`

The firmware reads the header as one fixed **`0xA4`-byte** block
(`movs r2,#0xa4` at `0x20016144`, read at `0x2001614A`), then indexes into it.

| offset | width | value on this corpus | meaning | confidence |
|---|---|---|---|---|
| `+0x00` | u32 | `0xFD53B912` | magic; mismatch → error state | `proven-from-code` |
| `+0x04` | u16 | `0x00B8` | a file offset, used **only** to locate the header checksum at `hdr[0x04] - 4`. It equals the first descriptor's offset on this corpus, but nothing reads it as that | `proven-from-code` |
| `+0x06` | u16 | `0x0001` | **reserved.** Read by no field access — the parser reads `+0x04` as a `ldrh` (u16), so `+0x06` is never captured. Constant `0x0001` across the corpus; folded into the header checksum only | `proven-from-code` (unread) |
| `+0x08 .. +0x1F` | 24 B | all zero | **reserved.** Read by no field access; folded into the header checksum only. Constant zero across the corpus | `proven-from-code` (unread) |
| `+0x20` | u16 | `0x0004` | descriptor count; rejected above `0x100` | `proven-from-code` |
| `+0x22` | u16 | `0x00A4` | header size **and** the file offset of the descriptor pointer array | `proven-from-code` |
| `+0x24 ..` | ASCII | `Jayco B-Van V13` | NUL-terminated config name, zero-filled to `+0xA4` | `inferred-from-data` |

`+0x22` was the "unexplained `0xA4`". It is the header size: `0x2001617C..0x20016192`
computes `((hdr[0x22] - 0xA4) >> 2) - 1` as a countdown of extra header words to fold
into the checksum, and `0x20016196` feeds the same u16 straight into the seek at
`0x2001619C` that reads the pointer array.

**Only four fields are interpreted (`proven-from-code`, 2026-09-10).** A scan of `cli.bin`
region 1 for every `ldr/ldrh/ldrb/str.w [r4, #imm12]` in the header range finds the parser
touching exactly `{+0x00, +0x04, +0x20, +0x22}` (13.6 and 14.7, byte-identical clis). The rest
of the block — `+0x06` and `+0x08..+0x1F` — is folded into the header checksum but read by no
field access, so it is reserved. Those offsets are `≥ 0x8e8` off the struct base, reachable
only by the wide encoding this scan covers, so the negative is real (`docs/PARSER-LOCATION.md`;
`tests/test_container.py`). See §10 item 3.

### Directory, `0xA4 .. 0xB7`

| offset | width | content | confidence |
|---|---|---|---|
| `hdr[0x22]` = `0xA4` | u32 × `count` | descriptor file offsets — `[0xB8, 0x13C, 0x1C0, 0x244]` in all three files | `proven-from-code` |
| `hdr[0x04] - 4` = `0xB4` | u16 | checksum over the header and the pointer array | `proven-from-code` (position and extent) |
| `0xB6 .. 0xB7` | 2 B | zero padding before the first descriptor | `inferred-from-data` |

**There is no `0x84` stride.** The firmware reads `count` u32 file offsets and seeks to
each (`ldr.w r1,[r10,r5,lsl #0x2]` at `0x20016398` feeding the seek at `0x2001639C`). The
descriptors merely *happen* to be `0x84` apart on this corpus. The "0x84 section-table
stride" recorded in early recon was an artefact of the data, and modelling it as one
overlaps the first section by `0x14` bytes.

---

## 2. Section descriptor

Four per file in every corpus member — `LCD Firmware`, `Lyra … GuiCode`,
`Lyra … GuiData`, and a `G12` image. Offsets are descriptor-relative; descriptor 0 begins
at `0xB8`.

| offset | width | meaning | confidence |
|---|---|---|---|
| `+0x00` | u16 | magic `0xDECD` | `proven-from-code` |
| `+0x02` | u16 | descriptor size — `0x84`, or `0x74` for the `G12` descriptor | `proven-from-code` |
| `+0x04` | 0x40 B | section name, NUL-padded ASCII | `proven-from-code` |
| `+0x44` | u8 | descriptor type: `0` for LCD/GuiCode/GuiData, `1` for G12 | `proven-from-code` |
| `+0x45` | u8 | **flags.** `0x03` in every descriptor of every file. The loader reads it (`ldrb.w r3,[r4,#0x239]` at `0x20016a5e`/`0x20016a8c`) and branches on **bit 1** (`0x02`) and **bit 2** (`0x04`), each gating a call into the tagged-entry/section helpers `0x20014734`/`0x20014238`. Stock `0x03` sets bits 0-1, so the bit-1 path is enabled and bit-2 disabled. Which action each bit gates is not fully traced | `proven-from-code` (it is a tested bitfield) / `unknown` (per-bit meaning) |
| `+0x46` | u16 | count of the 8-byte tagged entries at `+0x58`; rejected above `0x40` | `proven-from-code` |
| `+0x48` | u32 | payload file offset | `proven-from-code` |
| `+0x4C` | u32 | payload length in bytes | `proven-from-code` |
| `+0x50 .. +0x57` | 8 B | **reserved**, all zero. A gap between the payload-length field and the `+0x58` tagged entries: the fixed-field reads stop at `+0x4C` and the entry walk is indexed from `+0x58`, so no code reads these bytes | `proven-from-code` (unread) |
| `+0x58` | 8 B × `+0x46` | tagged entries, `<u16 tag><u16 flags><u32 value>` | `proven-from-code` (all three fields, see below) |
| `+size-4` | u16 | descriptor checksum | `proven-from-code` (position) |

The payload extents tile each file exactly, end to end, to EOF — `container.py` re-derives
the extent position structurally on every parse and raises if the scan and the proven
position ever disagree.

### Tagged entries — decoded, `proven-from-code` (2026-08-20)

**This subsection used to label every tag but `0x40` `unknown`.** It was closed by the loader's
own log file (`BUS-BASELINE.md` PC.19), which named the tags on screen and let the matcher be
read against real values.

**The names are a table in the firmware, indexed by the tag itself.** The target matcher formats
each comparison with `"  Param %s = %X, target = %X"`, taking the name from
`[0xC0400010 + tag*4]` at `0x2001485A` — a 0x83-entry pointer array at `+0x10` of the
`0x8F7E6D5C` sub-container in `cli.bin` region 2 (§4.2, §10 item 12), whose out-of-range slots
point at the literal `"Unknown"`. `subcontainer.loader_param_names` reads it; dumping it gives
the whole namespace:

| tag | name | tag | name | tag | name |
|---|---|---|---|---|---|
| `0x00` | FuncCode | `0x08` | ConfigID | `0x11` | UpldType |
| `0x01` | FuncBank | `0x09` | ExtFwVer | `0x40` | LdAddr |
| `0x02` | FuncInst | `0x0A` | FwRlsLetter | `0x41` | ConfigCmd |
| `0x03` | FwVer | `0x0B` | ExtFuncCode | `0x42` | DataReqCmd |
| `0x04` | ConfigRev | `0x0C` | HwVer/LcdDescType | `0x43` | ConfigCmd2 |
| `0x05` | GuiRev | `0x0D` | HwOpt | `0x80` | McuDevID |
| `0x06` | MfgCode | `0x0E` | BtldrVer | `0x81` | McuFlashSize |
| `0x07` | ConfigType | `0x10` | DevInst | `0x82` | McuRamSize |

**`+0x02` is a flags word, and it decides both *when* the entry is used and *how* it compares.**
The matcher runs the entry list twice with a phase mask `fp`, gating each entry on
`bics fp, flags` (`0x20014794` — participate iff `flags & fp == fp`), reads the value from
`entry+4` (`0x2001479E`) and the tag from `entry+0` (`0x200147A2`), then selects a comparison
from `flags & 3` (`ands r6,r6,#3` @`0x20014878`) between the **device's** value and the
descriptor's:

| `flags & 3` | entry passes iff | | bit | meaning |
|---|---|---|---|---|
| `0` | device `==` file | | `0x08` | used in the **match** phase (`fp = 8`) |
| `1` | device `<` file | | `0x40` | used in the **validate** phase (`fp = 0x40`) |
| `2` | device `>` file | | `0x10` | set only on the G12's tag `0x00`; `unknown` |
| `3` | device `!=` file | | `flags == 0` | not a comparison — the entry is **data** |

*Reading the log, watch the direction:* `sprintf` gets the descriptor's value as the first `%X`
and the device's as the one labelled `target` (`0x20014852`-`0x20014864`) — "target" means the
target **device**, not the goal. So `Param FwRlsLetter = 70, target = 72` is file `0x70`,
panel `0x72`.

Every descriptor in the corpus, decoded through that:

| section | match phase | validate phase | data |
|---|---|---|---|
| `LCD Firmware` (all 3 builds) | ExtFuncCode `==` `0x02004665` | ExtFwVer `==` `0x106`; FwRlsLetter **`>`** `0x70` (13.6) / `0x73` (11.23, 14.7) | LdAddr `0x00210000`; LcdDescType `1` |
| `… GuiCode` | ExtFuncCode `==` `0x02004665` | ExtFwVer `==` `0x106` (11.23, 13.6) or FwVer `==` `0x16` (14.7); GuiRev `==` `0` | LdAddr `0x00240000`; LcdDescType `2` |
| `… GuiData` | same | same | LdAddr `0x90000000`; LcdDescType `3` |
| `G12` | FuncCode `==` `0x1F` (`flags 0x18`) | **none** | ConfigCmd `0xC0`; DataReqCmd `0xC1`; **no LdAddr** — §10 item 4 |

Two things fall out. `FwVer` (tag `0x03`) is the **packed** form of `ExtFwVer` (tag `0x09`) —
`(v & 0xF) | ((v >> 4) & 0xF0)` at `0x20014C90`-`0x20014C9E`, so 14.7's `0x16` and 13.6's `0x106`
are the same requirement written two ways. And **`FwRlsLetter` is a compatibility floor, not the
payload's own version**: 13.6's descriptor says `0x70` (`'p'`) while its LCD Firmware payload
carries `0x72` (`'r'`) at `+0x40C` (§4).

**`ExtFuncCode 0x02004665` is the panel's device-function identity (2026-09-10).** It is the
same value on **all three of the panel's images** — LCD Firmware, GuiCode and GuiData — and is
**absent on the G12**, which reports `0xFFFFFFFF` for tag `0x0B` and identifies instead by the
short `FuncCode 0x1F` (§10 item 4). So `ExtFuncCode` names the *device* (the Spyder 7" panel),
while `LcdDescType` (tag `0x0C`: `1` LCD, `2` GuiCode) names the *image within it*. The loader
reads it from the firmware's own device header (LCD payload `+0x410`, §4) into loader state at
`0x20014C86`, the descriptor's tag-`0x0B` entry declares it with match-phase equality (flags
`0x08`), and the device reports it over RV-C (DGN `0x0FF02`, ParamID `0x0B`) — matched all three
ways so panel firmware cannot flash to a non-panel device. What stays `unknown` is only the
**internal decomposition** of the 32-bit value: it is constant across the whole corpus (one
panel type), so no differential can split it into sub-fields, the same limit `ConfigType`'s
"product/config-family code" reading hits. *Still `unknown` in this structure:* flags bit `0x10`.

---

## 3. Checksums

| what | where | confidence |
|---|---|---|
| position and extent of the header, descriptor and section-payload checksums | header: `hdr[0x04]-4` over the header + pointer array; descriptor: `+size-4`; payload: `(length-4) & ~1` over `length-4` bytes, verified against flash read-back at `0x200151E4..0x20015202` | `proven-from-code` |
| the algorithm: **CRC-16/AUG-CCITT** (poly `0x1021`, init `0x1D0F`, unreflected, no final xor) | matches all 27 stored checksum fields in the corpus (3 header + 12 descriptor + 12 payload); 14 other standard CRC-16 variants match 0 of 27 | `inferred-from-data` |

`coverage.py` claims a checksum span only after verifying it, so a corrupted file raises
rather than being silently accounted for.

---

## 4. The four embedded images

| section | flash destination | first words | notes | confidence |
|---|---|---|---|---|
| `LCD Firmware` | `0x00210000` | `20050000 002104D1 0021E279 …` | Cortex-M image, **not compressed**. Initial SP `0x20050000`, reset vector `0x002104D1` (Thumb; entry `0x002104D0`) in all three files | destination `proven-from-code`; SP/entry `inferred-from-data` |
| `Lyra … GuiCode` | `0x00240000` | `1A2B3C4D 0164D501 01661234 …` | **native ARM Thumb-2 code** behind a header whose first word is a magic (the code proper starts past it; the five-entry function-pointer vector is at header `+0x0C`). **Disassembled** — 13.6 and 14.7, `docs/GUICODE.md`. *(This cell used to read "not ARM code … Never disassembled by any task"; corrected 2026-08-15 — the magic is the header's, not evidence about the section body.)* | destination `proven-from-code`; magic `inferred-from-data`; Thumb-2 `proven-from-code` (`GUICODE.md` Finding 1) |
| `Lyra … GuiData` | `0x90000000` | `03142066 05061234 00E90010 …` | external QSPI flash; mapped whole, so a stored `0x900XXXXX` addresses section byte `XXXXX` | destination `proven-from-code`; interior `inferred-from-data` |
| `G12 …` | — | `07162066 0B061234 …` | descriptor type `1`, and its three tagged entries include no tag `0x40`, so **no destination address is stated in the file** | destination `unknown`; signature `inferred-from-data` |

The `LCD Firmware` base is corroborated five ways (its own vector table validates at
`0x00210000` and nowhere else; the descriptor's tag-`0x40` entry; `cli.bin`'s literal
`0x00210400` landing exactly on the version magic `0x44D29AD6` at payload offset `0x400`;
a textbook Cortex-M startup whose `.data` literal `0x0021F39C` lands inside the image; and
the image's own literal pool holding GuiCode's `0x00240000`/`0x1A2B3C4D` and GuiData's
`0x90000000`). An earlier task concluded this section was a compressed multi-megabyte
image; that conclusion is **withdrawn** — it only ever tested base `0`.

**Build tag — it is `ConfigType`, and the byte beside it is the revision. `proven-from-code`,
2026-08-20.** This entry used to read *"the low u16 of `first_words[1]` … not a version
discriminator, `inferred-from-data`"*. That description was accurate and the name was missing.
`cli.bin`'s `FUN_20014C54` fills the panel's own parameter array (indexed by the §2 tag numbers,
`0x200474D4 + tag*4`) straight out of the installed **GuiData** header:

| tag | name | source | 13.6 value |
|---|---|---|---|
| `0x01` | FuncBank | bit 5 of byte `+0x02` (`0x20014CC4`) | `0` |
| `0x02` | FuncInst | byte `+0x02` `& 0x1F` (`0x20014CCC`) | `0x14` |
| `0x04` | ConfigRev | **byte `+0x06`** (`0x20014CD4`) | `6` |
| `0x06` | MfgCode | the constant `0x125` (`0x20014CD8`) | `0x125` |
| `0x07` | **ConfigType** | **u16 `+0x04`** (`0x20014CDE`) | `0x1234` |
| `0x08` | ConfigID | the constant `0xFF` (`0x20014CE2`) | `0xFF` |

So the section header's `<u32 signature><u16 ConfigType><u8 ConfigRev><u8 ?>` holds `0x1234` in
11.23 and 13.6 and `0x0807` in 14.7 — a **product/config-family code**, which is exactly why it
is consistent within a file and not unique per file. `GuiRev` (tag `0x05`) is a separate read:
**GuiData byte `+0x06`** again, but gated on GuiCode's ConfigType at its `+0x08` matching
GuiData's at `+0x04` (`0x20014CFC`-`0x20014D08`). On the coach the panel reports `GuiRev = 6`
and 13.6's GuiData byte `+6` is `0x06` (`BUS-BASELINE.md` PC.19).

**The `LCD Firmware` payload carries a version block at `+0x400` — `proven-from-code`,
2026-08-20.** `FUN_20014C54` probes `0x00210400` (falling back to `0x00218400`) for the magic
`0x44D29AD6` and reads three fields into the same parameter array:

| offset | width | field | 13.6 value | read at |
|---|---|---|---|---|
| `+0x400` | u32 | magic `0x44D29AD6` | | `0x20014D14` |
| `+0x404` | u32 | payload length **minus the 4-byte CRC tail** (`0xF5C8 - 4`) | `0x0000F5C4` | — |
| `+0x40A` | u16 | **ExtFwVer** (tag `0x09`) | `0x0106` = v1.06 | `0x20014C7C` |
| `+0x40C` | u8 | **FwRlsLetter** (tag `0x0A`) | `0x72` = `'r'` | `0x20014C82` |
| `+0x410` | u32 | **ExtFuncCode** (tag `0x0B`) | `0x02004665` | `0x20014C86` |
| `+0x414` / `+0x416` | u16 | `0x0320` / `0x01E0` = **800 × 480**, the panel resolution | | — `inferred-from-data` |
| `+0x418` / `+0x41C` | u32 | `0x90000000` / `0x00240000` — GuiData's and GuiCode's destinations | | — `inferred-from-data` |

All three read fields match both the descriptor's tagged entries and what the panel reported over
the bus (PC.19). The `+0x418`/`+0x41C` pair is the **sixth** independent witness for §4's
destination addresses.

### 4.1 How an MCF reaches the panel — `observed-on-hardware`

Reported by the coach's owner, 2026-08-13: a USB stick is inserted and the panel is power
cycled, whereupon the screen offers **four configurations to load manually**, named

```
LCD Firmware        Lyra 13v6 GuiCode        Lyra 13.6 GuiData        G12
```

These are exactly the four section names in the container's descriptors. This is the first
direct evidence of the update *mechanism* in this project; `PARSER-LOCATION.md` previously
documented the updater's code without knowing how it was invoked or what it exposed.

**Operational detail — `observed-on-hardware`, owner-reported 2026-08-29** (written up for owners in
`GETTING-STARTED.md`): the stick is **FAT32**, and `mcf.bin` + `cli.bin` go in a directory named
**`SpyderConfig`**. If several `mcf*.bin` files are present the panel first asks **which one** to
load; the next screen lists the units and you **touch each item that needs updating**. Firefly warns
owners to **never load a file that does not match their coach** — consistent with the checksum-only
acceptance below (the panel cannot tell), so the correct file is the one Firefly supplies for that
specific coach. **Recovery is confirmed:** an owner cleared a boot-loop by power-cycling and, at
startup, selecting a **known-good `mcf.bin`** — so the recovery menu is reachable on a boot-looped
panel, and a bad flash is a re-flash rather than a brick.

**Corrected 2026-08-18 — the menu has FOUR entries but THREE loadable units.** This section
originally read "they are selectable **independently**", and drew from it that "a patch can be
confined to one section". The owner reports (both halves `observed-on-hardware`):

| unit | granularity |
|---|---|
| `LCD Firmware` | **independently flashable** |
| `Lyra … GuiCode` + `Lyra … GuiData` | **an inseparable pair** — the panel will not flash one without the other |
| `G12` | **independently flashable** |

So the original claim was right for two of the four entries and wrong for the two that matter most
to this project, since every GuiData edit is also a GuiCode question (§4.1's boot-loop note below).
A patch can still be confined to *a unit*; it cannot be confined to GuiData.

Two consequences survive, and they are what make writing to an MCF tractable rather than a gamble:

* The updater is not in the flash being written. `cli.bin` region 1 is a RAM-resident
  updater (`PARSER-LOCATION.md`), and `cli.bin` ships on the stick beside `mcf.bin`.
* Recovery is the same mechanism: reload from a stock stick.

**What the pairing changes in practice.** It is mostly protective: a GuiData insertion shifts the
GuiCode `ldr`-literals that point into it (§4.1's boot-loop note below), and because the two are
always written together from one file, a *self-consistent* build can never be split across a
reflash. It also means the reverse — **every build carries both sections, so a build derived from
stock silently replaces a customised GuiData**. That is exactly how the Pin-15 button disappeared
when the GuiCode-only tank-heater build was flashed (`BUS-BASELINE.md` PC.18b Result 9): the
"GuiCode-only" description was true of the *edits*, never of what got written.

The panel's acceptance test for a section is therefore just the checksums the updater
verifies — header, descriptor, and a payload CRC re-read from flash after programming
(`0x200151E4..0x20015202`) — with **no inspection of the payload bytes** between the seek
and the write. A structurally identical, correctly resealed section is indistinguishable to
it from the original.

**Not established:** whether the menu is drawn before or after `LCD Firmware` parses
GuiData, and so whether a *structurally broken* GuiData could hang the panel ahead of the
menu. `unknown`. `src/fireflymcf/patch.py` sidesteps the question by only making
same-size edits, which leave every structure and pointer intact.

**`relocate.py` does not get to sidestep it — `observed-on-hardware`, 2026-08-17.** The
first flashed build containing an *inserted* record boot-looped the panel: shifting GuiData
left 12 absolute `ldr`-literal pointers in the **GuiCode** section stale, and the panel
dereferenced shifted content at boot until the watchdog reset it. Reflashing the stock pair
recovered it, which is the practical answer to the question above: **whatever the menu's
ordering, a broken GuiData is recoverable via the stock stick, and a matched section pair is
the thing to reflash.** The relocator now repairs those literals and refuses to emit a build
with a stale one (`GUICODE.md` open thread 6; `theme.guicode_guidata_pointers`).

### 4.2 The loader itself — `cli.bin`'s two regions, and where region 2 goes

`cli.bin` ships on the stick beside `mcf.bin` and is the flasher: the **`Spyder 7" LCD Config
Loader`** (its own banner string) that draws §4.1's menu. Its header is a two-region descriptor
(transcribed in `src/fireflymcf/armimg.py`):

| offset | 13.6 value | meaning |
|---|---|---|
| `+0x00` | `0xC11B102E` | magic |
| `+0x0C` | `0x20000000` | region 1 load address |
| `+0x10` / `+0x14` | `0x2C` / `0x1EB70` | region 1 file offset / length |
| `+0x18` | `0x20000000` | region 2 load address **as declared — not where it goes, see below** |
| `+0x1C` / `+0x20` | `0x1EB9C` / `0x6B94` | region 2 file offset / length |
| `+0x24` | `0xC0400000` | **region 2's real load address** |
| `+0x28` | `0x9E3` (`0x1E6A` on 11.23) | per-build scalar, `unknown` — and out of code-first reach (see below) |

**`+0x28` resists resolution because its reader is not in the corpus** (`defensible negative`,
2026-09-10). The header magic `0xC11B102E` appears in **no** extracted image — cli.bin's own
header is consumed only by the panel's primary bootloader, which lives in the MCU's boot flash
and is not part of any MCF section. So unlike the container header and section-descriptor fields
(§1, §2), whose reader *is* in cli.bin and which were closed by finding it, `+0x28` has no
in-corpus consumer to read. What was ruled out empirically: it is **not** a checksum of region 1,
region 2, the header, or the whole file under sum8/16/32, xor16, Fletcher-16, CRC16 (init
`0`/`0xFFFF`) or CRC32; **not** a clean offset into region 2 (`0x9E3` lands mid-string inside the
131-entry tag-name pool, `0x1E6A` lands in a binary table — two unrelated structures, so a shared
offset meaning is excluded); **not** region 1's entry or reset vector (13.6 resets at `+0x2C1`,
11.23 at `+0x851`); and **not** referenced by any region-1 literal. `+0x24`, by contrast, was
resolved by inference from region 1's own literals (§4.2) without the external reader; `+0x28`
has no such internal witness.

**Region 2 loads at `0xC0400000`, and it is the loader's own GUI resource blob, not code** —
resolved 2026-08-20, `inferred-from-data`. Four witnesses, none of which is a fit:

1. Region 2's **word 0 is `0x8F7E6D5C`** — the same sub-container magic `GuiData` carries at
   `+0x10000` (§5.5). It is a resource sub-container; it has no Cortex-M vector table, and
   `PARSER-LOCATION.md` already records that it contains no MCF magic.
2. Its **pointer array at `+0x10`** is 24 for 24 words in `0xC0400xxx` — self-pointers under
   that base, in the same shape as GuiData's tables.
3. Header **`+0x24` is literally `0xC0400000`**, the word §10 item 2 logged as unexplained.
4. Region 1's code holds **102 distinct literals** in `[0xC0400000, 0xC0400000+0x6B94)`.
   Resolving each as `region2[value - 0xC0400000]` lands **93** of them on readable
   NUL-terminated strings — `"MCF read error\n"`, `"Flash erase error\n"`,
   `"Upload Complete\n"`, `"0:/SpyderConfig/LogFile.txt"`, `"mcf*.bin"` — and the other 9 on
   pointer arrays and binary tables inside the same region. Under any other base they resolve
   to noise. (Example: `0x200160DC` `ldr r0,[pc,#0x260]` → `0xC04006AF` → `"mcf not found\n"`,
   two instructions before the branch to the logger at `0x20014440`.)

The declared `0x20000000` at `+0x18` cannot be literally correct — it would overlay region 1,
the running updater. Reading `+0x24` as region 2's base is what lets the two coexist: region 1
in SRAM, region 2 in external SDRAM 4 MB above the framebuffer at `0xC0000000`.

**The blob's structure — mapped 2026-09-10 (`src/fireflymcf/subcontainer.py`, §10 item 12).**
Region 2 is a `0x8F7E6D5C` sub-container (§5.5): a fixed **16-byte header** (magic `+0x00`,
build word `+0x04`, two reserved words) followed by two pointer tables and the pools they index.

| offset | what |
|---|---|
| `+0x00` | header — magic `0x8F7E6D5C`, build word `0x66_011234` (version `0x1234`, build id `0x66`), `+0x08`/`+0x0C` reserved 0 |
| `+0x10` | **131-entry ParamID → name pointer table** — the descriptor-tag / `0FF02`-parameter names the loader prints (`[0xC0400010 + tag*4]` at `0x2001485A`, §2); out-of-range slots point at `"Unknown"` |
| `+0x21C` | the name string pool (`FuncCode`, … `McuRamSize`) |
| `+0x10C8` | **138-entry pointer table of the loader's own GUI screen records** (`+0xEC4`, 137 entries, on 11.23), read by the loader's renderer at the `0xC04010C8` literals `0x2000E71C` / `0x2000E9C0` |
| `+0x12F0..+0x6B94` | the GUI record pool |

`subcontainer.loader_param_names` extracts the name table; it is build-stable and a consistent
superset of `upload.PARAM_NAMES` (which had been hand-transcribed from a log capture).

**The GUI record grammar is the GuiData widget-record format itself (2026-09-10), not a
dialect.** `records.object_records` — the replay of GuiData's decoder `FUN_00252CB0` — decodes
every 13.6/14.7 loader screen (all but two on 11.23), landing on clean object boundaries and
producing the loader's own on-screen text: `"Press START AUTO LOAD to perform any required
updates"`, `"START AUTO LOAD"`, `"CANCEL"`, `"AUTO LOAD Successful\nRemove USB Flash Drive to
resume normal operation"`, `"AUTO LOAD CONFIGURATION"`, `"Error accessing Master Config File"`,
`"EXIT"`, `"ON"`, `"OFF"`. So cli.bin reuses the panel's GUI toolkit outright: `FUN_2000E028`
walks an object's records with `table[i]` and unions child boxes the way GuiData's `FUN_00257BFC`
does, calling the per-record decoder `FUN_20009cd8`. `subcontainer.loader_gui_screens` /
`loader_gui_text` return the decoded records and their labels.

**The one decoder difference — classified 2026-09-10 by disassembling both.** `FUN_20009cd8` is
`FUN_00252CB0` field for field, with a single exception at the **R35** field. `FUN_00252CB0` reads
R35's cursor byte only when `(uV16 & 0x2901) || (aee & 0x40)` **and** header byte-5 bit `0x80` are
set (the outer gate is the `< '\0'` test on the stored b5 flag in the decompiled decoder);
`FUN_20009cd8` drops the first clause and reads it whenever byte-5 bit `0x80` is set. It is a real
difference between the two decoders, **not** a `records.py` gap — `records.py` transcribes
`FUN_00252CB0` faithfully, and the panel's GuiData corpus holds **zero** `wtype`-8 records with
that bit, so the path is never exercised there. `records.decode_record(..., loader_r35=True)`
applies the loader's gate, and with it **every 13.6 and 14.7 loader screen decodes** — slot 46, the
lone hold-out, is a `wtype`-8 with the bit set, and on the *same bytes* it overruns under the panel
gate and lands exactly on its boundary under the loader gate. **What stays open** is narrower: the
older **11.23** toolkit has two more screens (the AUTO LOAD / MANUAL LOAD CONFIGURATION pages,
slots 125 and 130) that use a *further* field-presence path this decoder does not yet model.
The strings are **length-prefixed** the way GuiData's are (the byte before `"Current Configurator
File Version:"` is `0x22` = 34, the length). Whether the pointers are fixed up at load or the
region is placed verbatim at `+0x24` was not traced; the literal-resolution evidence is
consistent with either.

**By-product: the loader has no dump-to-stick path** (`proven-from-code`, same dig). `f_open`
(`0x20011AA0`) has exactly five call sites — `0x20016004`, `0x200160F2`, `0x20016116`,
`0x200162F8`, `0x20017114` — and their mode arguments are `1,2,1,1,1`. Only
`0:/SpyderConfig/LogFile.txt` is opened for writing, and mode `2` is `FA_WRITE` *without* any
create bit, so the loader cannot create a file at all. `f_write` (`0x20011DE8`) has exactly two
callers, the FatFs putc-buffer flush (`0x20011FF0`) and `f_puts` (`0x20012616`); every byte the
loader writes to the stick is a formatted log line. The whole image names four paths —
`mcf*.bin`, `0:/SpyderConfig`, `0:/SpyderConfig/LogFile.txt`, `GUIConfig*.bin` — the same four in
11.23 but with the last spelled **`GuiConfig*.bin`** (§4.3), and 14.7's `cli.bin` is
byte-identical to 13.6's. `LCD Firmware`, `GuiCode` and `G12`
carry no USB, FatFs or path strings at all, so the loader is the only thing in the system that
mounts the stick. **There is no way to read the running firmware back out through the supported
update mechanism.** Two consequences worth carrying: creating an empty `LogFile.txt` in
`0:/SpyderConfig` turns the loader's ~60 diagnostic strings on (the open at `0x200160EC` sets
the enable flag at `0x200160FC`) — **confirmed on the coach 2026-08-20**, and the resulting log
is what closed §10 items 3 and 4 (`BUS-BASELINE.md` PC.19) — and the flash *is* readable from region 1
— the post-write verify at `0x200151E4` CRCs straight from the destination address — so the
missing piece is a route to a file, not a route to the bytes.

### 4.3 `GUIConfig*.bin` and CONFIGURATOR FILE LOAD — the loader's *other* file format

The loader has a **second, independent load path** that shares the `0:/SpyderConfig` directory
with the MCF path and nothing else. Documented 2026-08-20 because it writes to a region of the
panel this project has never looked at, and because it is easy to mistake for a route to GuiData.
It is not one.

| | MCF path (§1–§4) | configurator path |
|---|---|---|
| glob | `mcf*.bin` | `GUIConfig*.bin`; **`GuiConfig*.bin`** on 11.23 |
| magic | `0xFD53B912` | **`0xC04F16F7`** |
| shape | header + descriptor pointer array + 4 sections | one 64-byte header + an opaque payload |
| destination | per section, tag `0x40` or the device's `LdAddr` (§2) | one fixed place — the **last erase block of the QSPI** |
| screen | MCF FILE SELECTION → AUTO / MANUAL LOAD | CONFIGURATOR FILE LOAD |

**Enumeration** — `f_findfirst(dir, info, "0:/SpyderConfig", "GUIConfig*.bin")` at `0x2001627A`,
`f_findnext` at `0x2001655A`, capped at **32** entries (`cmp r0,#0x1f` @`0x2001629E`). Each hit is
logged `Found Configurator File: %s`, opened read-only, seeked to 0, read for **64 bytes**, and
closed (`0x200162F8`–`0x20016552`).

**The 64-byte header — `proven-from-code`:**

| offset | width | meaning | read at |
|---|---|---|---|
| `+0x00` | u32 | magic `0xC04F16F7` | `0x20016580`/`0x20016582` (see the caveat below) |
| `+0x09` | u8 | version major | `0x200165C4` |
| `+0x0A` | u8 | version minor | `0x200165C0` |
| `+0x0B` | u8 | version patch | `0x200165B8` |
| `+0x0C` | … | description, NUL-terminated | `0x200165A8` |

They become the row's Description, its `"%d.%d.%d"` Version, and the Status `Select to Load File`.
With no matches: `No Configurator files found`, status `Not Loaded`.

**The magic is *not* checked on the files being listed.** The only comparison against
`0xC04F16F7` is at `0x20016582`, on `state+0x458` — the copy already in flash — to fill the
screen's `Current Configurator File Desc:` / `Current Configurator File Version:` headers from the
same offsets. Any stick file matching the glob is listed with whatever bytes sit at `+0x09..+0x3F`.

**Where a load goes — the last sector of the QSPI, and it is not in the file.** `state+0x458` is
computed once at startup (`0x20014E64`-`0x20014E76`) as

```
dest = 0x90000000 + flash_size − erase_block_size
```

both terms coming from the QSPI SFDP identify in `FUN_20006A54`:
`struct[0] = 1 << (largest SFDP erase-type exponent)` (`0x20006C24`) and
`struct[1] = (density_bits + 1) >> 3` (`0x20006C2E`). So it targets the **final erase block of the
same QSPI chip GuiData lives in** — at the top, whereas GuiData occupies
`0x90000000..0x900D8E18` (~868 KB). On a 16 MB part with 64 KB sectors that is `0x90FF0000`. The
chip geometry has not been read off the hardware, so the absolute address is `inferred`; the
*formula* is `proven-from-code`.

Pressing a row logs `Configurator File %d Load Request` and runs the same primitives as the MCF
payload writer: a flash begin/end pair that dispatches on `addr >= 0x90000000` to the QSPI or the
internal-flash driver (`0x20006D18` / `0x20006D2C`), an erase of `state+0xB98` bytes
(`0x20006DA8`), then programming in `0x100`-byte pages — with `Update Failed (FileErr)` / `(FlshErr)` / `(CRC)` and `File Loaded`.
Note the erase length is the *payload* length, so a configurator file larger than one erase block
would erase past the end of the chip: these are evidently small, single-sector blobs.

**What is not known, and the negative that bounds it.** No `GUIConfig` file exists in the corpus or
on the owner's stick (PC.19 logs `No Configurator files found`), so **everything past the 64-byte
header is unknown**. And the magic `0xC04F16F7` appears in **none** of the four flashed images —
`LCD Firmware`, `GuiCode`, `GuiData`, `G12` — neither as a literal nor as a `movw`/`movt` pair
(checked per the standing scan rule; only `cli.bin` contains it, once). So nothing in the corpus
reads this blob *by its magic*: either its consumer does not check, or its consumer is not in the
corpus. Also unchecked: whether FatFs's pattern matcher is case-insensitive here, i.e. whether
11.23's `GuiConfig*.bin` and 13.6's `GUIConfig*.bin` are interchangeable — match the running
loader's spelling.

*Speculation, labelled as such:* the name, the wording (*"Press a Configurator File Description to
load file"*) and the dedicated top sector suggest a small per-coach blob emitted by Firefly's
PC-side Configurator tool.

**Two reasons this matters here.** It is the only mechanism found that writes to a region this
project has never examined — if the panel holds per-coach state that is neither in the MCF nor in
G12 NVM, that top sector is where to suspect it. And unlike the MCF path it **erases and programs
without validating the file's magic first**, so it is not a place to experiment casually.

### 4.4 The network upload path — how the `G12` section reaches the device over RV-C

§4.1's "three loadable units" hides a real asymmetry. `LCD Firmware` and the `GuiCode`+`GuiData`
pair are written by the panel **into its own flash**, and PC.37 confirmed that is invisible on the
bus. The **`G12` row is not**: the G12 is a separate node at source address `0x9C`, and the loader
has to get `0x25A8` bytes into `0x08038000` over CAN. `"Start Network Config Load"`
(`0xC040049A`) is that path, and it is the only part of an MCF update that RV-C carries.

**Enumeration is `observed-on-hardware`** — decoded frame by frame in `BUS-BASELINE.md`, "The G12
network-upload protocol, on the wire". In short: the loader claims SA **`0xF9`**, requests PGN
`1FED8` (broadcast, then **directed** at `0x9C`), and the G12 answers with four `0FF01`
GENERIC_UPLOAD_STATUS frames — one per upload target, each declaring a parameter count — followed
by one `0FF02` frame per parameter. That is where the destination address comes from, and it is
why §10 item 4's "the `G12` descriptor carries no tag `0x40`" is by design rather than an omission:
`LdAddr` is `0FF02` ParamID `0x40`, supplied by the device.

**Two properties of the enumeration, from re-reading PC.63 on 2026-09-08** (pinned by
`tests/test_upload.py`, and what `fireflymcf-g12flash`'s probe ladder is built on):

- **Both are answers; only the poller differs.** `0FF01` appears throughout a capture — 67
  against 12 inside the update window — but **not on the device's own initiative**: the *panel*
  broadcasts a `1FED8` request every **14.0 s** and the G12 replies within ~6 ms (`BUS-BASELINE.md`
  **PC.66**; an earlier draft here called it a periodic broadcast, which was wrong). `0FF02`
  answers a request alone (29, all in window). So **a listener that transmits nothing learns every
  upload target, its version and its parameter count — by overhearing the panel's poll**, and on a
  bus with no panel it hears nothing. Transmitting buys the parameter *values*.
- **The directed request is the one that dumps parameters.** The broadcast at t=220.7 drew four
  `0FF01` and nothing else; the directed request at t=221.7 drew four `0FF01` **plus all 29
  `0FF02`**.

**The transfer is `observed-on-hardware` as of 2026-09-08** — `BUS-BASELINE.md` PC.63,
`captures/pc63-g12-network-upload-2026-09-08.log`, decoded by `src/fireflymcf/upload.py`. The
code derivation below came first and **nothing in it had to be withdrawn**; the capture confirmed
each row and supplied the framing constants. On the wire:

- the channel is PROPRIETARY_A at **priority 4** — `10EF9CF9` loader→G12, `10EFF99C` G12→loader
  (bits 28-26 of the id: `0x10……` is 4, two steps above the coach's ordinary priority 6);
- the device requests each chunk as ``C1 FF <offset u24 LE> FF FF FF`` and the loader answers with
  the **8 bytes** at that offset, `0x0000` to `0x25A0` in steps of 8;
- **both opcodes come from this file's own `G12` descriptor**, §2's tagged entries `0x41`
  `ConfigCmd` = `0xC0` and `0x42` `DataReqCmd` = `0xC1` (read at `0x2001565a`, defaulting to
  `0xFF`). All three builds carry that pair; **none carries `0x43` `ConfigCmd2`**, so the loader's
  third command (`0x20015934`, which would send a 32-bit `LdAddr`) has never run here;
- each **32-bit word is sent byte-reversed** (`upload._unswap`), which is the `strb` branch pair
  below choosing its second order;
- the whole `0x25A8` section moves in **1.79 s** at 1,343 frames/s, ~69% of the bus;
- the G12 **re-claims its address 0.8 s later**, so it restarts itself on completion. There is no
  success frame: every device frame is a `C1` request.

Reconstructed and compared against the image the owner actually flashed, the payload is the `G12`
section **byte for byte**. Three loader frames are not chunk data and only one is decoded — see
`upload.Upload.control`; PC.63 lists them.

From `cli.bin` region 1 (base `0x20000000`, file `+0x2C`):

| what | where | evidence |
|---|---|---|
| the loader subscribes to PROPRIETARY_A **directed at itself** | `0x20017820` `ldrb.w r0,[r5,#0x20]` (own SA) then `orr r0, r0, #0xef00`, `bl 0x20017688` | the same call takes `0x1FED8` (`0x2001782a`, pool `0x200178d4`), `0xFF01` (`0x2001782e`) and `0xFF02` (`0x20017836`) |
| the receive loop accepts only PROPRIETARY_A | `0x20015c46`-`0x20015c52`: mask the id, `cmp.w r3, #0xef00`, `bne` back to the wait | — |
| the peer supplies a **24-bit byte offset** | `0x20015aae`: `[sp,#0x40]<<16 + [sp,#0x3f]<<8 + [sp,#0x3e]`, added to the file base at `[r8,#0x23c]` | — |
| the loader answers with **8 bytes** | `0x20015b00`-`0x20015b6a` builds the id (`bfi r3,r2,#8,#0x12` — an 18-bit PGN) and the payload, then `bl 0x20007d2c` (transmit) | the payload is written forward (`0x20015b46`) or reversed (`0x20015bba`) under a flag — the byte-order trap of `G12-CODE.md` again |
| progress is a percentage | `0x20015b6e`: `muls r5,#0x64` / `udiv r5,r5,fp`, stored `[r3,#0xe]`, capped at `#0x62` | — |
| timeouts | `0x2000096c` (tick) plus `#0x1960` (6,496 ms) and `#0x9c4` (2,500 ms) | `"Config Upload Cmd timeout"` `0xC04004F6`, `"Config Upload Cmd2 timeout"` `0xC040052C` |
| outcome codes | `0x20015cac`/`0x20015cda`/`0x20015d08`: `0xC0`, `0xD0`, `0xF0`, each with a 13-bit `0x1FFF` sub-field | `"Flash erase error"`, `"Flash write error"`, `"CRC validate failed"`, `"Signature[%d] Invalid"`, `"Gen Upload success"` |

**So the device pulls and the loader serves** — read off the receive-then-transmit shape above
before any capture existed, and **confirmed frame-for-frame by PC.63**. Three things had
corroborated it beforehand: the parameter names
`ConfigCmd`, `DataReqCmd` and `ConfigCmd2` (`0xC0400A7A`/`0xC0400A84`/`0xC0400A8F`) are a
*command* triple the device advertises; the error string
`"Direct upload can't be done, Buffered upload required."` (`0xC04003B9`) says the loader cannot
stream and must hold the image for random access; and the offset arrives in the *received* frame
while the data leaves in the *sent* one.

**The state machine is `FUN_20014f2c` and three of its residuals are now decoded (2026-09-08).**

- **`r7`, the mode flag.** A **source type** byte at `+0x903` of a `0x44`-stride descriptor
  (`0x20015270`) dispatches: type **4** → canonical base `0x90000000`, type **5** → `0x90080000`
  (both QUADSPI), anything else → the error path at `0x2001546e`. Both arms compute the same
  predicate and join at `0x20015286`: **`r7 = (source_base != that canonical base)`**. It then
  governs two `cbz r7` sites — `0x20015894` picks the **checksum** (0 = inline byte sum, 1 = the
  CRC unit via `0x20000e2c`), and `0x20015592` picks **which device table gives the destination
  address** (0 = stride `0x18` at `+0x4CC7`, 1 = stride `0x0C` at `+0x64CB`). That byte is OR-ed
  with `0xEF00` at `0x200155ac` to form the frame's 18-bit PGN, so it **is** the destination SA.
- **The opening frame is fully placed** (`0x2001556c`-`0x200155d8`): `[0]`=0, `[1:3]` an **11-bit**
  field (`ubfx #0,#0xb` at `0x2001557c`) from a `0x2c`-stride table, `[3:7]` = `AD 03 69 CF` as
  `movs` literals, `[7]`=1. Only the 11-bit value's *meaning* is still unread. *(An earlier note
  cited `0x20015786` for the `ubfx`; that site is `ubfx #0,#0x12`, the CAN id's PGN.)*
- **The outcome codes are not device frames.** PC.63's device frames are **1204 of 1204 `C1`**.
  The `0xC0`/`0xD0`/`0xF0` compares at `0x20015cac`/`0x20015cda`/`0x20015d08` run *after*
  `"Upload Complete"` and switch on **which command was run**, then re-read a **13-bit** field
  (`ldrh.w [sp,#0x40]`, `ubfx #0,#0xd`) whose all-ones value is the unset sentinel — logging
  `"Config Invalid"`/`"GUI Config invalid"`. **The loader's success test is a post-upload re-read
  of device state**, which is why PC.63 shows a reboot and a fresh `0FF01` burst before its log
  says `Config Load Success`.

*That capture was taken on 2026-09-08 and cost one flash: 2,410 frames, and the chunk size,
direction and word order fell out of it directly. The `0xC0` header is fully decoded — length at
`[1]`/`[4:6]`, and `[6:8]` a **byte sum of the payload mod 2^16, little-endian** — decoded
2026-09-08: the loop at `0x20015880` reads one byte (`0x20015890` `ldrb.w`, after `movs r2,#1`)
and accumulates it inline at `0x200158b4`-`0x200158bc` (`ldrh`/`add`/`uxth`/`strh`), and
`sum(section[:0x25A4]) & 0xFFFF == 0xCEB0` reproduces the observed `B0 CE`. *This said
"accumulated by `0x20000e2c`", which is the OTHER checksum mode — the STM32 CRC unit, selected
by `cbz r7` at `0x20015894` — and is not the path this frame took.* What is
still open is one field: the 11-bit value in the opening frame `00 25 01 AD 03 69 CF 01`, read
from a `0x2c`-stride table at `0x20015786`.*

### 4.4a The panel's QUADSPI image store — `proven-from-code`, 2026-09-08

Chasing `r7` turned up a second source for a network upload, and with it a container format this
project had not seen. **13.x and later only:** 11.23's `cli.bin` carries neither magic, and
**13.6's and 14.7's `cli.bin` are byte-identical** (sha256 `8f93fc0d…`), so a loader finding
covers both for free.

**The container.** At a QUADSPI source base the loader validates (`0x2001528e`-`0x200152c0`, and
the directory reader at `0x20014bde`-`0x20014c18`):

| offset | field |
|---|---|
| `+0x00` | **magic** — `0xB2285EA0`, or `0x4C99B178` (formed at `0x20015298`-`0x200152a0` by subtracting `0x65800000`, `0xEA000`, `0xD28`; also stored as a literal at `0x20014d7c`/`0x2001553c`) |
| `+0x08` | `u16` total length |
| `+0x0B` | a byte, the base index into the entry table |
| — | a **`0x14`-stride** entry table of `u32` offsets (`smlabb r6, #0x14, r6, r2` at `0x20014c0c`) |
| `+len-4` | `u16` checksum, verified by `FUN_20000e80` |

That is **not** the MCF container's shape (`0x84`-stride sections at `+0xBC`); it is a second,
smaller format that lives in the panel's flash rather than on the stick.

**Two source bases, and some are computed.** The source type byte at `+0x903` of a `0x44`-stride
descriptor selects: type **4** → `0x90000000`, type **5** → `0x90080000`. Other sites compute a
base as `0x90000000 + K + (v - 1 MiB) / 2` with `K` ∈ {`0x80000`, `0x100000`}, where `v` is
`FUN_20006d0c` = `[0x2001ec3c + 0x34]` (`0x20014c22`, `0x2001506e`). With a 4 MiB part that gives
`0x90200000` and `0x90280000` — consistent with a partitioned store, though **the writer of
`[0x2001ec3c+0x34]` was not located**, so the partition arithmetic is read but not confirmed.

**The loader writes the QSPI NOR — it does not only read it.** `cli.bin` carries a full driver:
the QUADSPI peripheral `0xA0001000` with an `MspInit` at `0x20018cd8` (RCC `0x40023800`, GPIO
`0x40021400`, AF9/AF10), and a command struct at `0x2004214c` whose Instruction field is
programmed with **`0x32` Quad Page Program** (`0x20006900`), `0xB7` Enter-4-Byte (`0x20006c4a`),
`0x05` RDSR (`0x20006baa`) and `0x5A` Read-SFDP (`0x20006b56`). *Read from the command struct, not
from a bare-constant scan — `movs r2,#2` is everywhere and proves nothing.*

**Why this matters.** The panel stores images in its own 4 MiB QSPI NOR and can source a network
config upload from there rather than from the USB stick. If the `G12` section is among what it
stores, then a panel can re-send it with **no stick present** — which would mean an owner could
recover their own `G12` section with nothing but the boot menu and a CAN sniffer, and the standing
"there is no backup" limit (`2026-09-08-g12-network-write-design.md` §8) would be softened.

**What is NOT established:** whether the `G12` section is among the QUADSPI-stored images (the
store certainly holds GuiData, 867.5 KB at `0x90000000`), what the two magics distinguish, and
what the `0x14`-stride entries mean.

**CLOSED — `BUS-BASELINE.md` PC.68 and PC.69, 2026-09-08. Both directions were tested and the
store is not reachable.** With no stick the panel boots to the normal GUI (no loader at all); with
a stick carrying `cli.bin` but no `mcf.bin` the loader runs and stops at **`Master Config File
Missing`**, offering nothing. So the QUADSPI store is **not an independent image bank** — it is
reachable only on a path that begins by loading an MCF, consistent with it being where the loader
*puts* GuiData rather than somewhere it keeps a spare copy. Everything above this paragraph stands
as code; only the practical reading is withdrawn. *Original note kept below.*

**That test was run — `BUS-BASELINE.md` PC.68, 2026-09-08 — and the answer is no.** With no USB
stick the panel boots straight to the normal GUI: the loader never runs, so however capable the
QUADSPI source path is, nothing reaches it from a cold boot. **The refined question is the live
one**: the loader needs *a stick*, not necessarily *the right file*, and PC.63's log already shows
`No Configurator files found` followed by a successful enumerate-and-match — so "found a file" and
"has targets to offer" are known not to be the same condition. The way to ask is a USB mass-storage gadget on the bus host
carrying **`SpyderConfig/cli.bin` and no `mcf*.bin`** -- an *empty* volume is a null test, because
`cli.bin` is itself the loader and ships on the stick (§4.1).

**And note what it does not reach.** `UploadType 2` writes the **config** region at `0x08038000`,
which is what an MCF's `G12` section is. The **application** at `0x08006000` is `UploadType 1`,
advertised by the same device on the same protocol but with no payload for it in any MCF this
project has. Reading the G12 application image — the standing ceiling behind
`docs/G12-CODE.md`'s open items and `BUS-BASELINE.md`'s record-59 residual — would need a *read*
primitive, and nothing in the loader has one: every path here writes.

---

## 5. Inside `GuiData`

**Corrected 2026-08-18.** This section used to open *"Everything in sections 5–8 is
`inferred-from-data`. No located code parses any of it."* That was true when it was written
and is now false — it contradicted §5.1, §5.4a, §5.4b and §5.4c on the very next pages, each
of which cites the instruction that reads the field. See the preamble for which images parse
what. **Every claim below carries its own label; there is no section-wide default.**

### 5.1 Section head and table directory

```
+0x00  u32  0x03142066   signature (low half 0x2066, shared with the G12 section)
             ^ byte +0x00 is a build id, u16 +0x04 a version stamp; both proven-from-code
+0x04  u32  0x05061234   build word (low half is the build tag)
+0x07  u8   directory entry count                                    proven-from-code
+0x08  8-byte table descriptors, <u8 tag><u8 b1><u8 b2><u8 b3><u16 offset><u16 size>,
       count of them from +0x07, running contiguously up to +0x30, which is where the
       first table they declare starts
```

**`proven-from-code`** for the header and the descriptor layout, cited in
`LCD-FIRMWARE.md` Findings 8 and 9. `FUN_0021BE50` in the LCD Firmware reads
`GuiData+0x00`, `+0x04` and `+0x07` directly (`0x0021BF1A`, `0x0021BF10`, `0x0021BF2C`)
and walks the descriptors at `0x0021BF7C`–`0x0021BFC2`. Byte `+0x00` and u16 `+0x04` are
cross-checked against `GuiCode+0x0A`/`+0x08` and against the sub-container's `+0x07`/`+0x04`;
a mismatch rejects the blobs. `+0x07` is loaded as a byte and shifted left by 3
(`lsl.w r12,r12,#0x3`), which is what proves both the count and the 8-byte stride.
Descriptor offsets are GuiData-relative (`add r6,r9`, where `r9` holds the base).

13.6's descriptors, and the tables they declare:

| descriptor | tag | b1 b2 b3 | offset | size | table | entries |
|---|---|---|---|---|---|---|
| `+0x08` | `0x10` | `00 E9 00` | `0x0030` | `0x0748` | **group table** | 233 × 8 |
| `+0x10` | `0x20` | `00 00 00` | `0x0778` | `0x22F8` | **binding table** | 746 × 12 |
| `+0x18` | `0x30` | `00 01 00` | `0x2A70` | `0x0000` | — | — |
| `+0x20` | `0x41` `'A'` | `01 00 2A` | `0x2A70` | `0x0950` | **variable table** | 298 × 8 |
| `+0x28` | `0x42` `'B'` | `00 00 01` | `0x33C0` | `0x000C` | — | 12 B |

Entry sizes are not guessed: each size divides exactly in all three builds
(11.23: 230/732/291, 14.7: 234/747/310) and the offsets chain contiguously.

The group count is also read from the directory: `FUN_0021B420` takes tag `0x10`'s **`b2 + b3`**
as the count, with `b3 ≤ 10`, and keeps tag `0x20`'s `b2`/`b3` (`LCD-FIRMWARE.md` Finding 14;
`b3` is 0 in every build). Tag `0x42`'s `b3` counts 12-byte records, which `FUN_0021CCA0` requires
before a 64-byte I2C transfer. The records themselves are undecoded.

**The tag is a `u8`.** An earlier revision of this section read the descriptor as
`<u16 tag><u16 aux>` and gave the variable table's tag as `0x0141`. The code disproves
that: `ldrb.w r11,[r10,#0x8]` / `cmp.w r11,#0x41` is a byte load against `'A'`, and the
`0x01` is `b1`, the high bits of the entry count.

For the `'A'` descriptor the middle three bytes are **`proven-from-code`**:

* `count = b3 | ((b1 & 3) << 8)` — `and r3,r0,#0x300` at `0x0021BF96`. Gives 291/298/310
  across the three builds, which independently equals `size / 8` **and** the u16 at
  `GuiCode+0x20`. `FUN_0021BE50` compares those two and rejects on mismatch, with a hard
  ceiling of 1000 (`cmp.w r6,#0x3e8`).
* `b2` is an attribute byte: low 5 bits kept as-is, `b2 >> 5` indexed through a table
  branch to `{20, 30, 45, 60, 75, 90, 120}` (0 otherwise). The control flow is proven, but
  `b2` is `0x00` on the `'A'` descriptor in all three builds, so no non-zero case is
  exercised anywhere in the corpus and the meaning of those values is `inferred`.

**The middle three bytes are a per-tag payload**, and each tag uses a different one — which
is why no single u16 `aux` reading can work. `FUN_0021A738` is a second walker of this same
directory and decodes tags `0x10` and `0x30` (`LCD-FIRMWARE.md` Finding 12):

| Tag | Field | Meaning | Bound | Confidence |
|---|---|---|---|---|
| `0x10` | `b3` | count of a `0x18`-stride array | `< 11` | `proven-from-code`, **never exercised** (`b3` = 0 in all three builds) |
| `0x10` | `b2` | entry count of the group table (`0xE9` = 233) | — | `inferred-from-data` — no code reads it |
| `0x20` | all | — | — | `unknown`; no code reads this descriptor |
| `0x30` | `b2` | a mode/kind selector | `< 5` | `proven-from-code` |
| `0x30` | `b3` | count of 12-byte DGN records at the offset | — | `proven-from-code`, **never exercised** (`b3` = 0, table length 0 in all three) |
| `'A'` | `b3 \| ((b1&3)<<8)` | variable count | `≤ 1000` | `proven-from-code`, exercised |
| `'B'` | all | — | — | `unknown` |

Note the split for tag `0x10`: the code reads **`b3`**, while the group table's entry count
is in **`b2`**. Those are different bytes, so the `b2` = 233 reading gains no support from
the code and stays `inferred-from-data`.

Section offsets are **u16**, so nothing a descriptor declares can live above `0xFFFF`;
the region from the end of the last table to `0x10000` is all zero in all three files.

### 5.2 Group table (`+0x30`)

`<u8 hold/variants><u8 flags><u8 repeat><u8 flags><u16 start><u8 count><u8 count2>` — a
contiguous slice of the binding table. **`start`, `count` and `count2` are `proven-from-code`
(2026-09-10, `LCD-FIRMWARE.md` Finding 14):** the LCD's group loader `FUN_0021B420` reads the slice
at `binding_base + (start + k) * 12`, with `count` entries, plus `count2` more unless `count2` is
`0xFF`. This entry used to call byte 7 padding. `CONTROL-BEHAVIOR.md` §5a has the after-hold path
that reads `count2`. The same loader drops the **whole** table if any group's byte 1 has both top
bits set (`0xC0`), so a builder must never write that value.

**The four leading bytes are the control's interaction model — decoded 2026-08-19,
`proven-from-code`** (`docs/CONTROL-BEHAVIOR.md`; this entry used to read "the two leading
u16s are `unknown`"). They are consumed by the **LCD Firmware**, not GuiCode:

| byte | field | meaning |
|---|---|---|
| `+0x00` bits 4-7 | hold threshold | **seconds** before a press counts as a hold (`mla r0,#1000,r3,r0` @`0x0021BD86`). Only 1, 3 and 5 occur |
| `+0x00` bits 0-3 | variant count | how many variant copies the per-control counter cycles (`0x0021BC6E`) |
| `+0x01` bits 6-7 | `0x80` disables the per-control variant cycle (`0x0021BC5E`) |
| `+0x02` bits 0-1 / 2-3 / 4-5 | repeat rate | index into `{0, 250, 500, 1000}` ms (table at `0x0021F2A2`) for the pressed / held / released phases |
| `+0x03` bit 7 | also advance the **global** variant counter (`0x0021BC7E`) |

`src/fireflymcf/gestures.py` decodes all of it; `cli gestures <image>` prints it.

A group index is **build-stable**: group `0x6B` is byte-identical in all three files even
though it sits at binding entry 397 in 13.6, 402 in 11.23 and 394 in 14.7.

### 5.3 Binding table (`+0x778`) — a stored CAN message

```
0x778 + 2*12   db fe 01 02 00 fb ff 16 ff ff 00 ff
0x778 + 3*12   db fe 01 02 00 fb ff 18 ff ff 00 ff      ... six identical but for byte 7
```

| offset | width | meaning | confidence |
|---|---|---|---|
| `+0x00` | u16 | low 16 bits of the RV-C DGN (`0xFEDB` = `DC_DIMMER_COMMAND_2`, `0xFED9` = `GENERIC_INDICATOR_COMMAND`, …): the sender inserts the entry's first u32's low 18 bits at CAN-id bit 8 (`ldr r1,[r4]` @`0x0021B9AA`, `bfi r2,r1,#8,#0x12` @`0x0021B9B4`; `GUICODE.md` Finding 83) | `proven-from-code` (2026-10-19; was `inferred-from-data`) |
| `+0x02` bits 0-1 | — | DGN bits 16-17 (the DGN is 18 bits; `db fe 01` = `0x1FEDB`) | `proven-from-code` |
| `+0x02` bit 2 | flag | **this frame is re-sent on the repeat ticks**, not only on the first tick of its phase (`lsls r1,r2,#0x1d` / `bpl` @`0x0021B950`) | `proven-from-code` |
| `+0x02` bits 4-7 | u4 | **variant** — matched against a *runtime* counter (`cmp sl,r2,lsr #4` @`0x0021B946`), which is why no file byte selects it. **ANSWERED 2026-08-19**; this row used to read "What selects a variant is not known" | `proven-from-code` |
| `+0x03` | u8 | **gesture-phase mask**: `1` press, `2` short-tap release, `4` while held, `8` release after a hold. **ANSWERED 2026-08-19**; this row used to read "varies within a group" | `proven-from-code` |
| `+0x02` bit 3 | flag | **sets CAN-id bit 29** (`ubfx r1,r1,#3,#1` / `bfi r2,r1,#5,#1` @`0x0021B9C4`/`0x0021B9C8`), which makes the transmit ring `FUN_0021825C` take the source address from the frame's own byte 0 instead of the panel's (`lsls r3,r3,#0x1a` / `bpl` @`0x00218282`) -- and the binding sender zeroes byte 0 (`strb fp,[r2,#-0xc]!` @`0x0021B9D4`). So such an entry goes out from SA `0x00`. Set on two live entries per build, `0x0EF42` `10 FF FF 00 ..`: 13.6 bindings 435/598 in groups 127/185, which no 13.6 touch region names; 11.23's group 185 is ELECTRICAL's `Inverter/Charger` | `proven-from-code` (2026-10-19) |
| `+0x04 .. +0x0B` | 8 B | the eight CAN data bytes, in the order 3,2,1,0,7,6,5,4 (two little-endian u32s): `FUN_0021B890` copies `src[3,2,1,0,7,6,5,4]` to `dst[0..7]` (`0x0021B890`-`0x0021B8AE`), called with `src` = entry `+4` (`adds r1,r4,#4` @`0x0021B9CC`) | `proven-from-code` (2026-10-19; was `inferred-from-data`); the value of each byte is the DGN's business |

Consequence, and the form the reader actually uses: for `0xFEDB` entries the byte at
`+0x07` is CAN data byte 0, the RV-C **instance** — i.e. the G12 channel and the PDF pin.
It is decoded **only** for that DGN; `+0x07` has not been shown to be an instance for any
other, and for the 224 `0xFED9` entries it is `0xFF`. Byte `+0x05` is likewise the RV-C
brightness for `0xFEDB` (`0xC8` = 200 = 100% in half-percent units).

**On the byte order (derived while writing this document, Task 11; not covered by the
test suite).** Reading `+0x04..+0x0B` as two byte-reversed u32s reproduces frames captured
on the coach exactly:

| stored entry | reads as | matches |
|---|---|---|
| `db fe 01 02 00 fb ff 16 ff ff 00 ff` | `16 ff fb 00 ff 00 ff ff` | instance 22, level `0xFB` = 251, command 0 — PC.8's interior-master **on** |
| `db fe 01 01 06 00 ff 16 ff ff 00 ff` | `16 ff 00 06 ff 00 ff ff` | level 0, command 6 (`Memory Off`) — PC.8's master **off** |
| `d9 fe 05 06 0f 16 96 ff ff d2 ff fb` | `ff 96 16 0f fb ff d2 ff` | the `FF 96 <sel> 0F <lo> <hi> <fn> <fn>` write shape from PC.2, selector `0x16`, value −5 |
| `d9 fe 05 0e 0f 16 96 ff ff d3 ff ff` | `ff 96 16 0f ff ff d3 ff` | PC.2's read-back request, byte for byte |
| `d9 fe 01 01 ff 01 94 ff ff e5 ff 1c` | `ff 94 01 ff 1c ff e5 ff` | PC.7's *unexplained* group-`0x94` frame, byte for byte |

The frame *values* are `observed-on-hardware`; that these byte positions are the payload, in
this order, is now `proven-from-code` (`GUICODE.md` Finding 83: the payload copy `FUN_0021B890`
and the id assembly in `FUN_0021B8B4`). Priority is not in the entry: the sender writes 6
(`movs r3,#6` / `bfi r2,r3,#2,#3` @`0x0021B9B2`/`0x0021B9C0`).

### 5.4 Variable table (`+0x2A70`)

`<u18 dgn (+14 unknown bits)><u8 value><3 B ?>` — that is, the first u32's **low 18 bits
are the DGN**, and bits 18–31 are `unknown`.

The 18-bit width is **`proven-from-code`** (`LCD-FIRMWARE.md` Finding 11): the init loop
in `FUN_0021BE50` reads each record's first u32 and masks it with `0x3FFFF` before passing
it to `FUN_0021A618`. An earlier revision of this section read the field as `<u16
dgn_low><u16 ?>`; the DGN in fact spans two bits into byte 2, which is where `0x15FCE`'s
17th bit comes from. 13.6 entry 41 is `CE 5F 1D 02 01 00 00 00` →
`0x021D5FCE & 0x3FFFF = 0x15FCE`. Masking all 298 records yields 66 distinct DGNs, all
well-formed RV-C — `0x1FEDA` ×75, `0x15FCE` ×53, `0x1FBDA` ×27, `0x1FFE2` ×15,
`0x1FACE` ×7 — which is a strong check on the mask, since a wrong width would produce
nonsense identifiers.

For `0x15FCE` (G12 configuration) the `value` byte is the **message-type selector**; for
`0x1FEDA` (`DC_DIMMER_STATUS_3`) it is a channel instance. Those two readings remain
`inferred-from-data`; the remaining bytes are `unknown`.

13.6 declares 48 distinct `0x15FCE` selectors, including a contiguous run at entries
283–288 reading `ce 5f 1d 02 {e9,e4,e5,e3,eb,ec}` — exactly the six selectors the coach's
OPTIONS screen writes — and `0xF5`, the floorplan parameter, at entry 294. **An entry only
declares that a variable exists.** It carries no name, no unit, no range, and nothing
found joins it to a screen row (§8).

The run's *positions* are not a key: 14.7 keeps six entries in the same slots but reads
`0xE7` where 13.6 reads `0xE9`, and shows five option rows against six variables.

### 5.4a The variable table is a receive filter — `proven-from-code`

`FUN_0021C760` in the LCD Firmware is the RV-C receive handler, and the variable table is
the subscription list it scans. Decompiled 2026-08-14; this is the mechanism behind
`BUS-BASELINE.md` PC.13–15.

```c
iVar5 = *(int *)(iVar4 + 0x104) + index * 8;                     // table base + index*8
if ((*(uint *)iVar5 & 0x3ffff) == (frame_id << 6) >> 0xe) {      // 18-bit DGN match
    if (entry[4] != 0xff && (mask & (entry[4] ^ payload[entry[5] & 0xf])))
        continue;                                                // instance filter
    switch (entry[2] >> 4) { ... }                               // extraction mode
    *(ushort *)(iVar4 + index * 2 + 0x10c) = extracted;          // per-variable slot
    *(int *)(iVar4 + index * 4 + 0x914) = deadline;              // staleness timer
}
```

An 8-byte entry therefore reads:

| bytes | field | confidence |
|---|---|---|
| `+0..+3` bits 0–17 | the 18-bit DGN to match (`ubfx r0,r0,#0,#0x12` @`0x0021C92C`, against `ubfx r2,r2,#8,#0x12` on the CAN id) | `proven-from-code` |
| `+2` bits 4–7 | **extraction mode** — the full arm-by-arm decode is the table below | `proven-from-code` |
| `+2` bits 2–3 | refresh class, selecting the staleness timeout (2 s / 10 s / 30 s / none) — `ubfx sl,r0,#2,#2` @`0x0021C9B6` | `proven-from-code` |
| `+3` bits 0–2 | which payload byte to extract from (`n`; mode 2 reads `n` **and** `n+1`) | `proven-from-code` |
| `+4` | expected value for the filter; `0xFF` disables it | `proven-from-code` |
| `+5` | low nibble: payload byte to compare. high nibble: which bit-pairs of it, expanded to a mask — bit 3→`0xC0`, 2→`0x30`, 1→`0x0C`, 0→`0x03`. **A high nibble of 0 means the mask is `0xFF`** (compare the whole byte), `moveq r2,#0xff` @`0x0021C980` | `proven-from-code` |
| `+6, +7` | zero in every observed entry | `unknown` |

#### The extraction modes, arm by arm — `proven-from-code`, 2026-08-21

The dispatch is a `tbb` at `0x0021C99A` over a 7-byte table at `0x0021C99E`, reached only
after `subs r2,#1` / `cmp r2,#6` / `bhi` at `0x0021C994`–`0x0021C998`. `n` below is
`entry[3] & 7`; every arm converges on the common store at `0x0021C9B0`.

| mode | arm | extracted value | entries (13.6 / 11.23 / 14.7) |
|---|---|---|---|
| **0** | — | **entry skipped entirely** — the `bhi` @`0x0021C998` jumps to the loop increment. No slot write, no timer | 14 / 14 / 24 |
| 1 | `0x0021C9A6` | `payload[n]`, zero-extended | 134 / 119 / 175 |
| **2** | `0x0021C9E0` | **`payload[n] \| payload[n+1] << 8`** — u16 little-endian, then `sxth` @`0x0021C9F2` | 96 / 95 / 95 |
| 3 | `0x0021CA66` | **entry skipped** — its own `tbb` slot points straight at the loop increment | 0 / 0 / 0 |
| 4 | `0x0021C9F6` | `payload[n] & 3` (bits 0–1) | 2 / 2 / 2 |
| 5 | `0x0021CA06` | `ubfx #2,#2` — bits 2–3 | 52 / 61 / 14 |
| 6 | `0x0021CA16` | `ubfx #4,#2` — bits 4–5 | 0 / 0 / 0 |
| 7 | `0x0021CA40` | `lsrs #6` — bits 6–7 | 0 / 0 / 0 |
| 8–15 | — | **entry skipped**, same `bhi` as mode 0 | 0 / 0 / 0 |

**Mode 2 is the second most common mode in the table and was previously undocumented** —
this row used to read *"1 = whole byte, 4/5/6/7 = a 2-bit field at bits 0/2/4/6"*, which
accounts for only 188 of 13.6's 298 entries. It is how a multi-byte value reaches a slot:
`15FCE` selector `0x31` reads `payload[2..3]` = `fc 03` = 1020 × 0.05 = **51.0 V** (the AES
start-at voltage of `GUICODE.md` Finding 43, known independently — which is what makes this
a check rather than a fit), `1FFFD` instance `0x81` reads `38 00` = **2.80 V** keypad
battery, and the `0BFC1` tank levels read `payload[1..2]`. It also derives what
`livecfg.py` had only as `observed-on-hardware`: that docstring pins the `15FCE` value
width to "u16 LE at bytes 2..3" from the same selector `0x31`, and mode 2 with `n = 2` is
the panel-side reason.

The `sxth` is a **signedness hint, not a transform**: the store at `0x0021C9C0` is a `strh`,
so the sign extension cannot change the 16 bits that land in the slot. It says the firmware
intends these slots as **int16**, which matters for any consumer of a value that can go
negative (PC.11's config *deltas* are signed). No mode-2 entry in any build uses `n > 6`, so
`payload[n+1]` never runs off the 8-byte payload.

**Mode 0 entries are inert subscriptions.** Fourteen of them in 13.6 — `0EBFF` ×2, and one
each of `0EEFF`, `0EFDF`, `0FF01`, `1FACF`, `1FECA`, `1FECE`, `1FEE8`, `1FF8F`, `1FFD4`,
`1FFD5`, `1FFD6`, `1FFD7`. They match a DGN and then write nothing, so their slots never
update and never acquire a staleness deadline. Do not read a mode-0 entry as evidence that
the panel *uses* that DGN. 14.7 has 24, adding `1FFFB`, `1FFFC`, `1FFFD`, `1FEA5`, `1FFB8`,
`0FF80`, `0FF81`, `0FF82`, `0FF85` and a second `0EFDF`.

Modes 3, 6 and 7 have **zero** entries across all three builds — the arms exist in code and
are never exercised by the corpus.

#### Which entries the panel uses — `proven-from-code`, 2026-10-19

An entry that writes its slot is not thereby *used*: GuiCode must read the slot. It can only do so
through the base the LCD hands `GuiCode_entry1` (`GUICODE.md` Finding 33), and
`slottrace.state_reach` follows that base to every load made through it, with no escape on any
corpus build. So each entry is classed from the code (`fireflymcf bus-map`, `GUICODE.md`
Finding 82): on 13.6, 128 of the 298 entries reach a cell a widget paints, 11 reach the config-set
helper, 13 reach logic only, **132 are written and never read**, and 14 are mode 0. A write-only
entry is the DM_RV pattern of Finding 77 generalised: injecting its DGN changes the slot and
nothing else, unless a raw-frame parser reads the DGN itself (`busmap` lists those per DGN).

#### The filter never sees the source address or the priority — `proven-from-code`, 2026-08-23

The match is one `ubfx` pair, and it takes bits 8–25 of the CAN id:

```
0021C92A  ldr    r2, [r6]            ; the 29-bit id
0021C92C  ubfx   r0, r0, #0, #0x12   ; entry DGN, 18 bits
0021C930  ubfx   r2, r2, #8, #0x12   ; id bits 8..25 = DGN
0021C934  cmp    r0, r2
```

Bits 0–7 are the **source address** and bits 26–28 the **priority**; neither is read here, nor
anywhere in `FUN_0021C760`, nor in its caller `FUN_0021A664` (whose only id tests are the same
DGN-field compares). The entry has no field to hold an address either. Two consequences:

- **A DGN is accepted from every node on the bus.** Where an entry also carries the `0xFF`
  sentinel at `+4`, *any* device driving that DGN drives that widget, and the last frame to
  arrive wins. On 13.6, 66 of 298 entries are open this way.
- **Priority is not a discriminator.** This is the panel-side reason the 2026-08-22 receive probe
  matched a priority-6 frame from the bus host, and why the three GPS injection runs at priorities 3, 5
  and 6 behaved identically.

The only per-device discrimination available is the masked payload compare at `+4`/`+5` — which
is a test of frame *contents*, not of bus identity. It happens to be pointed at `payload[0]` for
instance-bearing DGNs, which is why this project calls `+4` "the instance filter"; the mechanism
is more general than the name.

**Turning an open filter on is one byte** — `statusbind.pin_filter_instance`, since 2026-08-23.
It writes `+4` only, asserting the entry's DGN, its existing `+5`, and that its extraction mode is
one the `tbb` actually dispatches (narrowing a mode-0 entry changes nothing observable). It
refuses to re-point a filter that is already live, and refuses to write `0xFF` — it only ever
narrows. Widening, or moving the compare to a different payload byte, is `retarget_filter`.

> **Not yet hardware-confirmed.** reference build v5 pins the coach's three charger
> entries (vars 115/260/261, `1FFC7`/`1FEA3`/`1FF99`) to the alternator DC charger's instance
> `0x31`, ahead of a second charger joining that bus. With only one charger transmitting, the
> correct result is **no visible change at all**, which is a weak oracle: it confirms the compare
> is not pointed at the wrong byte, but the discrimination itself is only exercised once two
> devices send the same DGN. Treat the masked compare as `proven-from-code` and the *pin* as
> unexercised until that second device exists.

Checked against the frames captured on the coach. For `DC_DIMMER_STATUS_3` from the G12:

```
variable 80   da fe 5d 06 26   filter payload[0]==0x26 (38)   mode 5 -> (payload[6]>>2)&3
                 ON  payload[6]=0x04 -> 1        OFF payload[6]=0x40 -> 0
variable 82   da fe 1d 06 28   filter payload[0]==0x28 (40)   mode 1 -> payload[6]
                 ON  -> 4                        OFF -> 64
```

**This retracts two guesses recorded earlier in this project.** Byte 2's `0x1d` versus
`0x5d` is the *extraction mode* (1 versus 5), not a flag; and the idea that bit `0x40`
marks "channel in use" was already refuted by channels 9, 11, 12 and 18, which are
hardware-verified yet carry it set.

**It also explains the "phantom" channel 40 completely.** A widget draws a *slot*, not a
channel. Repointing variable 82 from instance 40 to 38 means a status frame for 38 now
fills slot 82, so anything drawing slot 82 — including the `G12 OUTPUTS` row *labelled*
40, whose `'40 -'` text is static — displays 38's state. No instance-40 frame need exist,
and none does in any capture.

**How the two halves are joined.** The struct is at SRAM `0x20007EBC`: table pointer at
`+0x104`, entry count `+0x108`, slot array `+0x10C`, staleness timers `+0x914`. Nine
literal-pool slots in the LCD Firmware hold that address. `DAT_0021CA2C` holds
`0x01FED900` — the config-write DGN pre-shifted into its CAN-id position — which is the
branch that diverts `1FED9` frames away from the table scan.

**The scan is exhaustive, and the handoff to GuiCode is unconditional.** A DGN match does
not break the loop — `0x0021CA66` increments the index and `bhi.w 0x0021C91C`
(@`0x0021CA70`) goes back to the top until the `+0x108` count is exhausted. That is required
for correctness: 75 entries watch `1FEDA`, and one frame has to be able to fill every slot
that subscribes to it. Only then does the handler load GuiCode's base from the literal at
`0x0021CABC` (= `0x240000`), read `+0x1c`, and **tail-call** it with the frame
(`ldr r3,[r3,#0x1c]` @`0x0021CA78`, `bx r3` @`0x0021CA82`) — the **fifth entry of GuiCode's
header vector**, confirming `GUICODE.md` Finding 1's five-entry table from the calling side.

*Corrected 2026-08-21.* This paragraph used to read *"When a frame matches **no** variable,
the handler calls…"*. There is no such condition in the code: every frame that is not the
`1FED9` config write reaches the vector, matched or not. The old wording would have you
conclude that a DGN present in the filter table is handled *instead of* being passed to
GuiCode, and it is passed either way.

#### Subscription is self-registering, and capped at 83 — `proven-from-code`, 2026-08-22

> **Confirmed on hardware 2026-08-22.** Var 32 was retargeted to `0x1FEA0`
> (GPS_DATE_TIME_STATUS), mode 1 of `payload[3]` — a DGN in none of the three builds and in no
> capture — by a four-byte same-size edit and nothing else. Driven from the bus, the widget
> tracked it: `G12 OUTPUTS` row 15 went green on one value and off on another. Registration,
> the 18-bit match, the extraction and the paint are therefore `observed-on-hardware`, not only
> `proven-from-code`.

The table is not just matched against at runtime; it is also what *registers* the panel's
interest at boot. `FUN_0021BE50`'s per-entry loop calls `FUN_0021A618(entry_u32 & 0x3FFFF)` at
`0x0021C03E` for **every** entry, bounded by the count at `state+0x108`. So an entry's DGN bytes
are the only thing that has to change for the panel to subscribe to something new — no code
edit, no second table.

`FUN_0021A618` maintains a registry at SRAM `0x20006BA0` (`u16` count at `+2`, `u32` array at
`+8`). It scans for an existing copy first, so it **dedupes**; on a miss it appends and calls
`FUN_00217F14`, which merely stores `{pointer, count}` for the software filter. The bxCAN banks
do not limit it: bank 4 is a pass-all (`id 0`, `mask 0`), and banks 0–3 only divert address
claims, REQUESTs and ACKs (`LCD-FIRMWARE.md` Finding 18), so no second hardware limit sits
underneath. The match is on the full 18 bits, so a PDU1 subscription names one destination.

```
0x0021A62A  cmp r2, #0x53      <- 83 distinct DGNs
0x0021A62C  beq 0x0021A656     <- returns 0; NO CALLER CHECKS IT
```

**The cap fails silently.** Past 83 there is no error, no log and no boot failure — the DGN
simply never arrives. 13.6 registers **66** distinct DGNs from this table, so the headroom is
about 15–17; "about", because four further call sites (`0x21A2F6`, `0x21A782`, `0x21A86C`,
`0x21B222`) also register and are not counted, at least one of them `0x1FED9`.
`statusbind.registered_dgns` computes the table's contribution and
`statusbind.REGISTRY_CAP` is the disassembled constant; `retarget_filter` refuses to cross it.

**A second, unexercised registration path exists.** `FUN_0021A738` walks the same directory and
registers through the same function from descriptor tags `0x10` and `0x30` — but the tag-`0x30`
table has length `0x0000` in all three builds and the loop never runs (Finding 12). Treat it as
`proven-from-code and never executed`, not as a usable mechanism.

**Which slot a given widget draws — RESOLVED 2026-08-16**, and §5.4c below is the answer in
full. The short version, because this question was open long enough to leave stale reasoning
in several places: the widget does **not** hold a slot index, and looking for one was right
to fail. It holds a **cell-table index** (`fillA` mode-7 / `value_slot`, §6.0a), which names
an SRAM cell; a straight-line per-variable block inside GuiCode's `FUN_00247BF8` reads
`slot[var]` at a hard-coded `ldrsh` immediate and *writes* that same cell. The two halves
meet at the cell, not at an index, and the slot-array base crosses from the LCD firmware to
GuiCode as a **call argument**, never as a literal — which is why GuiCode contains no
reference to it. `GUICODE.md` Findings 32 (the negative, exhaustive) and 33 (the mechanism).

Two negatives from that dig are worth keeping, because they still constrain what can be
edited: GuiCode holds **no literal-pool reference** to the slot array or the receive struct
anywhere in the section, and none of its 26 `ldrh [base, idx, lsl #1]` sites indexes the slot
array — they are glyph metrics, the image table, the type-6 match table, and the render
state's own **layout register file** at `state+0x9E` (which `FUN_00257BFC` both reads and
writes with pen coordinates — it is layout state, not RV-C state).

**The slot array has exactly three writers and no reader in the LCD Firmware.** All three
write the same triple — `strh` to `+0x10C` (or `+0x144` above index 0x100) and a `str` to
the timer at `+0x914`:

| function | role |
|---|---|
| `FUN_0021BE50` | the GuiData reader — initialises the slots |
| `FUN_0021C374` | staleness expiry: reads `+0x914`, then defaults the slot |
| `FUN_0021C760` | the receive handler (§ above) |

Nothing in the LCD Firmware reads a slot, and GuiCode holds no literal for the array, the
struct, or the count. So GuiCode must receive the base as an argument — but the predicted
reader (one of its 26 `ldrh [reg, idx, lsl #1]` sites) **is not there**: all 26 were
classified by base-register provenance, and every one reads a GuiCode-owned global, a
stack array, or a font-metrics array (`FUN_002519F0`). Three further routes are excluded
too: GuiCode imports exactly one LCD function (`0x0021F247`, the transmit call — no getter
API); the header vector's `+0x1C` entry is called only for *unmatched* frames, a fallback
not the indicator path; and the LCD Firmware never writes into GuiCode's RAM.

**Second round (2026-08-14) — every stored-reference shape eliminated.** These five results
stand; only the conclusion drawn from them at the time has been superseded (see below):

* **The index is not computable from the channel.** The `18+(n-1)` / `66+(n-24)` formula
  is only the table's position and *breaks* above channel 44 — channel 61 is variable 173,
  not 103 — so a widget cannot derive its slot.
* **By-channel resolution is disproven against the flashed v9/v10 bytes.** The button reads
  a *fixed* slot 82; resolving its post-remap channel (38) would read variable 80, whose
  slot is filled in *both* builds — yet v10's button is dead. Not channel-derived at render
  or parse. (`BUS-BASELINE.md` PC.13–15.)
* **The control object stores its group, not its variable.** Diffing four structurally
  identical single-icon controls (groups 22/26/80/205) isolates a byte that tracks the
  group; the variable index (39/68/19/75) is absent from the object in raw or `×2` form.
* **The button reads the display-list variable (82), not its standalone one (184).** Moving
  82 lights it (v9); moving only 184 does nothing (v10). 184's purpose is still unknown.
* **The parser establishes no linkage** — `FUN_0021BE50`'s init loop writes `0xFFFF` to
  every slot and registers each DGN for reception, nothing more.

**Third round (2026-08-16) — `FUN_00247BF8` was read, and the "positional" reading is
withdrawn.** This paragraph used to conclude that the join was *positional*, resolved by
construction order, "with no field to find", and that confirming it meant reading the 25 KB
`FUN_00247BF8`. That function has now been read (`GUICODE.md` Finding 33) and the conclusion
was wrong in an instructive way. There is no per-widget *variable index* — the searches above
were correct — but there **is** a per-widget stored reference: the cell-table index in
`fillA`/`value_slot`. The join runs widget → cell ← variable, meeting at an SRAM cell that
GuiCode's per-var block writes and the renderer reads. Nothing is resolved by construction
order; the apparent positionality was the missing middle term.

Two things survive unchanged. The **variable index really is a pure function of position**
in the variable table, so a cross-build differential cannot discriminate on it. And the
shipped remap really does need `move_display_list`: a control's *command* binding and the
*status variable* its indicator watches are still joined by nothing in the file (§10 item
11a), so pointing a button at a new channel and pointing its indicator at that channel's
status remain two separate edits. What changed is that the second edit is now a **known**
edit rather than an unreachable one — see §5.4c.

### 5.4b `GuiData +0x44BC8` is a widget→RAM indirection table — `proven-from-code`

A standing unexplained item (§10 item 12; `CLAUDE.md` recorded only that it "holds pointers
into SRAM"). `FUN_00257BFC` uses it:

```c
iVar8 = *(int *)(0x90044BC8 + (u16 at state+0x6BF8) * 4);          // pointer, u16-indexed
*(byte *)(state + 0x7046) = *(byte *)(0x9004492C + (u16 at state+0x6BF8));
```

So `+0x44BC8` is an array of **u32 pointers indexed by a u16 held in the render state**,
with a parallel array of **bytes at `+0x4492C`** on the same index. The pointers target
`0x2003xxxx`, inside GuiCode's own RAM working area (state base `0x200301B0`) — which is
why they look like "pointers into SRAM" and why no relocation is needed: the RAM layout is
fixed, so the addresses are correct as shipped. Nothing fixes them up at load time.

### 5.4c Pin → cell: the whole status chain, and which joints are editable — `proven-from-code`

Dug 2026-08-17, to answer "can I bind any G12 pin to any button, set its receive filter and its
on/off colours, without reusing an existing control?" Every link below is cited; the joints are
what an author can actually change.

```
CAN frame, DGN 0x1FEDA DC_DIMMER_STATUS_3, instance = pin
  │  LCD  FUN_0021C760 scans the variable table (GuiData +0x2A70), 8-byte entries (§5.4a)
  │        entry+0..3 = DGN · entry+2>>4 = extraction mode · entry+4 = instance filter
  ▼  writes slot[index]  at  0x20007FC8 + index*2      (= struct 0x20007EBC + 0x10C)
  │  GuiCode FUN_00247BF8 reads  ldrsh [r4, #2*index]  (r4 = the slot base, passed in)
  │        a straight-line per-var block; cmp/beq picks a colour master
  ▼  str → colour cell (0x2003xxxx) · strb → bool cell
  │  GuiData +0x44BC8 maps table index → that cell address (§5.4b)
  ▼  a widget whose fillA mode-7 index / value_slot names that index paints it (§6.0a)
```

**`var` is one number throughout** — receive-filter entry index == slot index == the `2*var`
immediate in GuiCode. Verified across **all 136** bridge blocks in
`data/slot_bridge_136.txt`: every read immediate equals `2*var` (the two var-0 blocks encode
`[r4]`, i.e. `#0`). The slot-array arithmetic closes too: `0x20007EBC + 0x10C = 0x20007FC8`.

**The four editable joints** (all same-size; no relocation):

| joint | where | edit |
|---|---|---|
| which pin feeds a var | variable-table entry `+4` | **1 byte** — the instance filter |
| extraction mode / payload byte | entry `+2`, `+3`, `+5` | bytes (§5.4a) |
| which cell a table index names | `+0x44BC8 + idx*4` | **4 bytes** — an SRAM pointer |
| which index a widget paints | record `fillA` + `value_slot` | **2 bytes** each (§6.0a) |
| off / on colour of a var | GuiCode master `ldr` in `FUN_00247BF8` | **2 bytes** — the `imm8` displacement, repointed at a pool word holding another master. Edits one var's one state; the master constants are untouched, so every other cell keeps its colour |

**The one hard constraint.** GuiCode's per-var blocks are straight-line code with hard-coded
cell addresses, so the *set* of bridged vars is fixed at 46 (42 of them `DC_DIMMER_STATUS_3`).
A var with no block cannot be given one without code surgery. Everything else is a byte edit.

**In practice on 13.6 this constraint barely binds**, because:

* **Every pin 1–44 already has a receive-filter entry watching it.** Retargeting a filter's
  instance byte is therefore rarely needed — only to point a *bridged* var at a pin whose own
  var is unbridged (proved 2026-08-17: setting entry 32 `+4` to 42 makes `status_map` report
  var 32 → instance 42, carrying colour idx 145 and bool idx 67 with it).
* Of the **26 pins commanded by no control**, **20 are bridged** and **16 of those already
  have an indexed colour cell** — immediately usable.
* The remaining bridged-but-unindexed pins (17, 18, 37, 38, 39, 41, 43) have a colour cell that
  no `+0x44BC8` entry names. There are **16 spare table indices** — referenced by no record's
  colour field or `value_slot` — 203, 207, 211, 213, 215, 217, 219, 221, 223, 252, 254, 256,
  258, 260, 638, 639. Pointing one at the pin's cell is a 4-byte edit. *(That the spares are
  dead is `inferred-from-data`: it is a whole-corpus scan of record references, and nothing is
  known to iterate the table rather than index it.)*
* **Pin 42** (var 84) has no bridge block at all — avoid it.

**Off-state colours are per-var, not per-button.** The default (off) branch of a var's block
loads a master; the ON branch of every block examined loads the same `0x2003a1a0` =
`(0,174,239)` cyan. The off masters differ: `0x20039a58` = `(65,81,163)` button blue (pins
3,4,5,9,10,19,20), `0x20039974` = `(39,40,98)` — the *quadrant panel colour*, so a button using
it is invisible when off (pins 11–16, 27, 28, 31) — and `0x20039cf4` = `(192,178,60)` olive
(pins 29, 30). `theme.resting_defaults` reports these; the fix is the `imm8` edit above.

### 5.5 Object tables and objects

A table is a maximal run of u32s `0x900XXXXX` whose targets land in the section. There are
**four** per file (13.6: 8, 1422, 30 and 3295 entries; 11.23: 8/1201/30/2687;
14.7: 9/1077/29/3171). Entries descend, so object *k* occupies `[table[k], table[k-1])`,
which is what makes an object's extent recoverable without sizing its records.

**Three of the four are now `proven-from-code`** — GuiCode holds literal pointers to their
base addresses, and the bounds it enforces match the entry counts derived here
(`GUICODE.md` Findings 1–3). For 13.6:

| offset | entries | named by | role |
|---|---|---|---|
| `+0x10080` | 8 | `FUN_00255F80` (`cmp #8`) | **image table** — 8 opaque RGB888 artwork bitmaps (per-row RLE; **row length 0 = repeat previous row**), u16 width/height arrays just below the table (`table-N*2` / `table-N*4`). **DECODED 2026-08-29 (`images.py`)** |
| `+0x46148` | 1422 | — | **unreferenced by any literal**; still `inferred-from-data` |
| `+0x74D80` | 30 | `FUN_002520F4` (`cmp #0x1e`) | **font descriptor table** |
| `+0x7503C` | 3295 | `FUN_00251AA0` | **glyph table** |

The font descriptor is 20 bytes, `<u8 first><u8 last><u16 base><u16 fallback>` + a 14-byte tail;
a character `c` resolves to glyph `base + c - first`, or to `fallback` when outside `[first, last]`.
The descriptors' ranges partition the glyph table contiguously and the last font's `fallback` is its
final index, so the table is fully accounted for — see `GUICODE.md` Finding 3, pinned in
`tests/test_blob_crosschecks.py`.

**The tail was "14 bytes unknown"; most is now named.** Six bytes are read by the render path
(`glyphs.py` / `metrics.py`, `proven-from-code`): `[0x08]` bpp (1/4), `[0x06]` trailing advance,
`[0x07]` cell height, `[0x0b]` top offset, `[0x11]`/`[0x12]` inter-glyph spacing. The other eight —
`[0x09],[0x0a],[0x0c]–[0x10],[0x13]` — are **not read** by the measurer (`FUN_002518F8`), the
frame-box (`FUN_002557C0`), or the glyph blitter (checked 2026-08-29, `proven-from-code` negative);
they hold a per-font default-glyph geometry that scales with the font size (for the fixed icon fonts
they equal the icon dimensions, e.g. size/size-1), most likely font-authoring metadata the runtime
ignores.

Not every pointer table in GuiData is one of these four. `GuiData+0x44BC8` is a 665-entry
pointer array read by `FUN_00256868`, but its entries point into **SRAM** (`0x2003A7D8`,
…), not into the section, so it is invisible to the rule above.

*Correction, 2026-08-15:* this paragraph previously concluded the array "must be **fixed up at
load time** by a mechanism this document does not yet describe". That inference is **withdrawn**
— the entries are static SRAM addresses needing no relocation. What happens at boot is that the
**cells they address** are initialised: for the type-10 (colour) entries, by GuiCode's theme-init
writing hardcoded RGB888 constants (`GUICODE.md` Finding 18, RESOLVED). The array itself is
stored, complete, and read as-is.

Descent is **not strict in every table**: only the first two tables hold distinct pointers.
The 30-entry table repeats one pointer, and the last table repeats many (907 of 3295 in
13.6, 1272 of 2687 in 11.23, 825 of 3171 in 14.7) — several entries addressing the same
object. So that bound can yield an **empty** span, and a caller must not read "entry *k*
exists" as "object *k* has bytes".

Each run is validated by a property that ties its contents to its own position — its
lowest pointer equals the run's own end offset. Across the corpus all 12 runs of ≥ 4 words
have it; none of the 616 runs of 1–3 words does. `inferred-from-data`.

A sub-container with magic `0x8F7E6D5C` sits at section offset `0x10000` in all three
files (the same constant `cli.bin` checks at `r5+0x10000` in its version-report routine).
It shares the **16-byte header** decoded in §4.2 — magic `+0x00`, build word `+0x04`
(version stamp + build id, cross-checked against this section's own head, §5.1), two reserved
words — with `cli.bin`'s region-2 instance; only the payload differs, and
`subcontainer.parse_header` reads either. Here the payload at `+0x10` is a **colour palette**
(`0x90010010`, the mode-6 palette
`DAT_002535B0` in `FUN_00252CB0`'s colour decode) — an array of `u32` colours stored
**B, G, R** (low byte first). The first ~28 entries are clean RGB — `#00a79d` teal,
`#00aeef` cyan, `#1b75bc` blue, `#cc2127` red, grays, white — and read like a UI theme.

**But it is not the main theme — `observed-on-hardware`, 2026-08-14.** Setting all 28 to
magenta changed only a *subset* of the screen: the inactive/placeholder mode boxes on the
Climate screen and the service-mode indicator area. The Starlink button, the header and nav
bars, the fan tabs and the background **kept their colours**. So this palette paints
**placeholder and conditional/image elements**, not the primary chrome — which is why an
earlier probe changing its "button blue" (`[5]`) and "header red" (`[8]`) entries had no
visible effect. The chrome's colour source is a *different* mechanism — the **mode-7 table**
`DAT_002535B4 = 0x90044BC8`, whose entries are SRAM cells — and it is **no longer `unknown`:
RESOLVED 2026-08-15**, those cells are written at boot with hardcoded RGB888 constants by a
theme-init block inside GuiCode's `FUN_00247bf8`, extracted by `theme.mode7_palette`
(`GUICODE.md` Finding 18 → its RESOLVED section). **Note what that means for patching:** a
chrome colour is *not* editable the way this palette is — it lives in the **GuiCode** section,
not GuiData, so changing one is a different (and unattempted) edit.
Editing a palette entry is a same-size `u32` write and does take effect, so it
is a real — if partial — handle on the colour scheme. The run of u16s after the palette is
still `unknown`.

---

## 6. Screen and widget records

A screen object:

```
<u8 record_count> <record> * record_count <zero pad to a 4-byte boundary>
```

The count byte *is* the object: the four words at `0x06F630 .. 0x06F63C` are
`0x90070E78`, `0x90070E24`, `0x90070AD4`, `0x9007081C` (the table descends), which resolve
to file offsets `0x09A2B0`, `0x09A25C`, `0x099F0C` and `0x099C54` — the four count bytes.

A record is an **8-byte header followed by a variable, type-gated body — not a fixed
`opcode + tail`.** `records.py` decodes it by replaying `FUN_00252CB0` field for field, and the
object-table boundary oracle (`tests/test_records.py::test_every_object_decodes_to_its_boundary`)
proves the decode is **byte-exact on every object of all three builds** (1200 / 1421 / 1076
objects, zero derailment). There is **no `TAILS[opcode]` table** in the real format — see the
retirement note below.

| offset | width | meaning | confidence |
|---|---|---|---|
| `+0x00` | u8 | header byte 0: low 6 bits are the **widget type** `t` (0-63) — it, via a per-type mask, and not a tail table, selects the body; bits `0x40`/`0x80` gate the per-coordinate input-register aux bytes | `proven-from-code` |
| `+0x01 .. +0x07` | 7 B | header bytes 1-7: **field-presence, base-reference and colour-mode bits.** Every optional body field (`records.py`'s R9-R51) is gated by a specific bit of these bytes, of `t`'s type mask, or of a colour-mode byte — each gate cited to a `FUN_00252CB0` instruction. The former "`+0x02` u16 / `+0x04` u32 with unknown bits" was the heuristic's coarse view of these seven bytes | `proven-from-code` (which bits gate which fields) |
| `+0x08 ..` | — | the body, in decoder order: colour fields, up to six u16 layout coordinates (`records.py`'s `_PEN_BITS`; the low four are x, y, w, h), value/index slots, an optional font selector, a wtype-9 format directive, alignment bytes, and finally the length-prefixed label string(s) | `proven-from-code` (presence and order); individual field *values* per §5.4/§6.0a and their own labels |

**The `opcode + TAILS` model is retired (2026-09-10, §10 item 7).** Earlier recon read a record
as an opcode plus a `+0x02` u16 and a `+0x04` u32 whose bits "each add a u16", ending in a fixed
per-opcode tail `TAILS[opcode]`. That was a fit that happened to land on boundaries for the
opcodes it had seen. The real structure has no fixed tail — the body is chosen field by field
from the widget type and the header bits — and the "unknown" flag bits are those header bytes,
every meaningful bit of which `records.py` now tests against a cited decoder instruction.
`guidata.py` keeps its `TAILS` dict **only** for `iter_screens`' title scan, which walks screens
by fitted lengths and deliberately skips any opcode it lacks; it is not the byte-exact decoder,
and its incompleteness bounds only that scan, not what the format decodes. What remains narrower:
a few decoded fields are consumed for byte-alignment without their *value* being surfaced
(documented inline in `records.py` — e.g. the R17 count and R9 clip geometry), which is a
residual on field *meaning*, not on the record grammar.

**Label encoding.** For a text-bearing widget (type `0x0B` is the common text record), the
label is the record's **final field** — `records.py`'s length-prefixed string loop — as
`<u8 length><length bytes><NUL>`, length excluding the terminator; a length byte of `0xFF`
escapes to a following u16 length, and **the label may contain a newline**. `proven-from-code`.

**The renderer reproduces leading spaces literally and left-aligns** —
`observed-on-hardware`, 2026-08-13. The dry-run patch rewrote the OPTIONS row
`'   Heat Pump'` (3 leading spaces) as `'     12v aux'` (5), and the panel drew it two
characters further right. So the leading spaces in these fields are real indent, not
slack, and a same-size rename must preserve them and put its padding on the right, where
it cannot be seen. `src/fireflymcf/patch.py` defaults to that; the first version padded
left and visibly moved the label.

**This is about LEFT-aligned labels, and does not generalise** — `observed-on-hardware`,
2026-08-21, **in both directions**: the owner saw the defect on the coach, and saw it gone
after the padding was balanced. A record whose R49 h-align byte is 2 centres each drawn line by the width of
the string it was given, **pad included**, so trailing padding is not invisible there: it
moves the text left by half the pad. The owner saw it on a caption padded to a 17-byte
donor field, `'Overflow\nValve   '`, whose second line sat 9 px left of centre while the
first looked right; `'Water\nPump'` beside it (10 bytes in a 10-byte field, and carrying
the same literal `0x0A`, so the newline is not the difference) looked correct. Pad a
centred field on **both sides of its last drawn line** — `patch.repad(align="center")`,
which `newbutton` selects from the record's own h-align byte. **37 stock 13.6 labels are
centre- or right-aligned with a trailing pad**, so the firmware's own labels sit slightly
off-centre too; theirs are mostly a single space, about 3 px.

This also means a label's *stored* length is its drawn width — there is no trimming step
between the two.

**A label can also be made longer than the field it shipped in** — `observed-on-hardware`,
2026-08-18 (`BUS-BASELINE.md` PC.18a). The `G12 OUTPUTS` static row `'15 - '` was grown by
8 bytes to `'15 - Pin 15  '` through the relocator and the panel draws it. Two constraints:
the growth must keep the object table's **4-byte alignment**, and the length byte must be
rewritten in the **same operation** as the text — `relocate.relocate()` alone refuses a grown
payload under a stale length byte, correctly, because the record does not decode.
`patch.set_label` remains same-size by design; this is the relocator path.

**A text record is preceded by `01 <font slot> ff`** — `observed-on-hardware`, 2026-08-14.
The byte three back from the length byte selects the font; slots count *down* in 20-byte
steps from the top of the descriptor table, so

```
descriptor address = <glyph table> - (slot + 1) * 20
```

13.6's Climate button carries `0x18` (slot 24) on its icon record at `0x097220` and `0x0f`
(slot 15) on its caption at `0x097238`. Two independent confirmations that the arithmetic
is right: slot 15 resolves to a 224-glyph `0x20..0xFF` Latin font, which is the only kind a
caption *could* use; and changing each byte on the coach produced exactly the predicted
result — the icon became the glyph slot 23 draws for that character, and the caption
re-rendered at the narrower font's width. See §6.1.

**The full text/icon draw record — `observed-on-hardware`, 2026-08-14.** The record around
that font selector is:

```
<x:u16> <y:u16> 01 <font slot:u8> ff 65 00 02 02 <flags:u16> <len:u8> <text bytes> 00
```

The leading `<x><y>` is an **absolute pixel position**, confirmed by a hardware probe:
writing the Climate Starlink caption's `x` (`0x097233`, `80 → 400`) moved the word
"Starlink" ~320 px right on the 800-px panel while its icon and button body stayed put —
so each draw element carries its **own** coordinates and they are independent. The button's
two elements read `(x=70, y=75)` for the `@` icon (font `0x17`) and `(x=80, y=75)` for the
`Starlink` caption (font `0x0c`); the shared `y` is the row, the `x` the horizontal anchor.

This makes **repositioning a text or icon element a same-size edit** — overwrite the `u16`
`x`/`y` four bytes ahead of its `01 <font> ff` prefix. Moving a whole control means moving
all of its elements (icon, caption, body) together. `flags`/`u16` after `02 02` is
`unknown`; on this button it is `0x000A` for the icon and `0x0019` for the caption.

**The touch region is a separate record from the visual — `observed-on-hardware`,
2026-08-14.** A second probe changed a `u16` (`0x097256`, `75 → 300`) inside the button's
`0c 55 0d …` record. **Nothing visible moved**, but the area that responds to touch
extended well below the button. So a control's hit rectangle lives in its own record — an
opcode-`0x0C` record (render type `12`, which is *not* in `FUN_00256C54`'s draw switch
because a hit region is registered, not drawn; `GUICODE.md` Finding 10 types 10/`0x12`) —
independent of the drawn box and text. `0x097256` grows it downward, so that `u16` is the
region's bottom edge or height.

Two consequences. It is a real capability: a control's **touch target can be resized or
moved on its own**, same-size. And it is a caution: moving a button's *visual* would leave
its *touch area* behind, because they are different records — anyone repositioning controls
must move both. The full hit-rectangle field layout (`b9 ff 96 00 4b 00 28 00` =
`-71, 150, 75, 40`, meaning not yet pinned) needs one more probe.

**The visible box — `observed-on-hardware`, 2026-08-14.** A third probe changed the `u16`
at `0x09720B` (`150 → 400`) inside the button's opcode-`0x04` record (render type 4). The
blue box **grew wider to the right**, its left edge fixed — so that `u16` is the box's
**width**. The touch area did *not* follow (independent record, confirming § above from the
visual side), and the colours were unchanged (a dimension edit only). The record's geometry
quad is `[144, 150, 75, 5]`; width is the confirmed second field, so the read is most likely
`[x, width, height, corner-radius]` — height `75` and radius `5` `inferred-from-data`, not
yet probed. **Corner radius CONFIRMED 2026-08-18** (`GUICODE.md` Finding 40): the type-0x21
drawer offsets the arc centre by `coord4` and calls `FUN_0025451A(x0+c4, y0+c4, c4, c4)`, and
`HOME.jpg`'s `coord4 = 5` buttons are visibly rounded. Note the near-miss recorded there — the
same handler's `(x1-x0+1) - 2*coord4` reads like a border inset and briefly persuaded; it is the
straight span between the two arcs.

**Button anatomy, complete.** A single control is (at least) three independent records:

| record | opcode / type | role | confirmed field |
|---|---|---|---|
| `01 <font> ff …` ×2 | type 1 draw | icon + caption glyphs | `<x><y>` before the prefix (probe 1) |
| `0c 55 0d …` | opcode `0x0C`, type 12 | touch / hit region | `0x097256` = vertical extent (probe 2) |
| `04 21 55 0c …` | opcode `0x04`, type 4 | visible box | `0x09720B` = width (probe 3) |

The three are edited independently and must be kept in sync by hand. This is the basis for
`set_position` / `set_size` primitives. The box's **fill colour** was not in the changed
bytes; it is either later in this record or an indexed palette reference (`GUICODE.md`
Finding 11 modes 6/7), and locating it is the remaining probe for a `set_style`.

**An icon is a character.** A button is two records in one object: a one-character text
record drawn in an icon font, then the caption. On 13.6's Climate button, `0x097228` holds
`<01> 'E' <NUL>` and `'E'` in slot 24 *is* the three-blade fan. So changing a button's icon
is a one-byte edit, and `src/fireflymcf/glyphs.py` renders any font so the character can
be chosen by eye.

Out-of-range characters do not fail — they draw the font's fallback glyph. Putting `'['`
on that button drew a filled 48×48 block, because slot 24 maps `'['` to a shared
placeholder record. That was the observation that pinned the font slot down.

### 6.0a Button composition and the live-redraw invariant — `inferred-from-data`, hardware-motivated

Dug 2026-08-17 while chasing a cloned HOME button whose colour updated live but whose
caption vanished until the page was re-entered. Decoded with `records.object_records`
over all 1422 objects of 13.6.

**A button is three records inside one object**, in this order:

| record | wtype | carries |
|---|---|---|
| box | 33 | the rounded-rect geometry `[.., .., w, h, radius]` and `fillA` |
| caption | 11 | the label string, its font, and the text colour pair |
| touch | 12 | the hit region; its trailing `u16` is the **command-group id** |

Objects `194`, `291`, `989` are exactly this shape; `222`/`255`/`277` are the same with a
second wtype-11 (an icon glyph beside the name). Nothing else is needed to make a working
control — visual, text and touch are still three independent records (§6), so a same-size
edit to one leaves the other two behind.

**A button is attached to a page by a wtype-5 container record in the parent**, whose
`child_index` names the button object. HOME (obj 4) is a 2×2 quadrant layout built this
way: wtype-5 → 907 at `(70,49)` and → 899 at `(70,264)` for the left column, and two
**wtype-6 value-match switches** → 911 (count 11, slot 5 = floorplan) and → 901 (count 4,
slot 6) for the right column. That is why the same page shows a Water-Pump *glyph* on one
floorplan and a Water/Pump *text button* on another: obj `989` holds
`[container→295 (Water Pump), inline Tank Heater button]` while obj `990` holds only
`[container→295]`, and a switch picks between them.

**The invariant that makes a button update live:**

> A button's `fillA` mode-7 colour index, its box's `value_slot`, and its caption's
> `value_slot` must all name the **same** cell.

Because the mode-7 colour table and the value-slot variable table are the same table
(`GUICODE.md` Finding 18), "colour index N" and "value_slot N" are one cell. Every live
button in 13.6 obeys this — objs 194 (114), 222 (136), 255 (146), 277 (154), 291 (159),
814 (122/146/136), 871 (156), 883 (146/136/154), 989 (455) — and every such cell is
**type 10**. Where the button object itself carries no `value_slot` (obj 295, Water Pump),
the **parent's wtype-5 container record supplies it**: obj 989's container has
`value_slot=160` *and* `fillA=(7,160)`, matching obj 295's box `fillA=(7,160)`.

**The six records that break the rule are not counter-examples** — they are unattached
template objects (247, 278, 279, 280, 281, 1038) reachable from no page, whose
`value_slot` is a **type-1/type-0 config gate** ("is this tank heater fitted?"), not a
type-10 status cell. Cloning one of these yields a button whose box paints from one cell
and takes its on/off style flag from another; the panel then repaints the box in place on
a status change but not the caption, so the text only returns on a full page render.

Practical consequence for authoring: pick a donor that is an **attached** button, and when
retargeting its colour cell, rewrite the box `value_slot` and the caption `value_slot` to
the same index. `records.value_slot_offset` and `newbutton._fillA_mode7_offset` expose
both as same-size `u16` edits.

### 6.1 Static and dynamic screen rows — `observed-on-hardware`

The `G12 OUTPUTS` diagnostics screen (13.6, `0x09A906`) draws its rows two different ways,
and which one a channel gets is not arbitrary:

| kind | stored | example |
|---|---|---|
| static | the whole row as one label | `0x09A978` `'03 - Awning EXT'` |
| dynamic | a prefix-only label plus an extra widget | `0x09B123` `'40 -'` |

Rows 39 and 41 are byte-identical in shape; row 40 stores `'40 -'` with **no trailing
space** and carries an extra `0d 55 0f …` widget the others lack. Every dynamic row's name
appears *only* in the GuiCode name table (`GUICODE.md` Finding 8) and nowhere in GuiData;
fixed-function channels get their name baked into the row. This is why enabling the Bath Fan
option makes a name appear after `40 - ` on the panel, and why renaming that name is **not**
a GuiData edit.

**Corrected 2026-08-18** (decoded with `records.object_records` over object 35; prompted by
the owner observing rows 05 and 08 populated on the coach while the render left them blank).
Two claims above were wrong:

* **There are 14 dynamic rows, not 8.** The full set is **05, 06, 07, 08, 10, 18, 19, 20,
  24, 25, 26, 34, 35, 40** — the earlier list missed 06, 07, 10, 19, 20 and 26.
* **They are not "the option-dependent channels."** The selector is not the floorplan/options
  switch (slot 5): heated tanks occupy 05–08 and are *not* a floorplan option, yet those rows
  are dynamic. The name is chosen by the **G12's received config broadcast** — what is
  physically wired — per `GUICODE.md` Finding 19, which found every such register written once
  from a received CAN payload. "Option-dependent" was too narrow. *(2026-10-19, Finding 81: the
  registers are the floorplan register and the OPTIONS registers — the tank rows are gated on the
  floorplan register, row 07 also on Black Tank — so "option-dependent" was too narrow only in
  leaving out the floorplan. Both arrive in that config payload.)*

A dynamic row is a `'NN - '` prefix label followed by **two** widgets, not one:

| widget | carries | resolves to |
|---|---|---|
| wtype **13** (dynamic string) | `value_slot` | a **type-9 string** variable — the name cell |
| wtype **6** (value-match switch) | `value_slot` | that pin's **bool cell** |

The two slots run as a consecutive pair, and the switch's slot equals that pin's `bool_idx`
in the status map (§5.4c) — an independent cross-check of the whole reading:

| row | 05 | 06 | 07 | 08 | 10 | 18 | 19 | 20 | 24 | 25 | 26 | 34 | 35 | 40 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| name slot | 52 | 54 | 56 | 58 | 61 | 70 | 72 | 74 | 79 | 81 | 83 | 92 | 94 | 100 |
| bool slot | 53 | 55 | 57 | 59 | 62 | 71 | 73 | 75 | 80 | 82 | 84 | 93 | 95 | 101 |

The name table itself is a run of NUL-terminated strings in **GuiCode** at file `0x28af8`
(vaddr `0x259278`): `'Tank Heat Fresh'`, `'Tank Heat Grey'`, `'Tank Heat Black'`,
`'Tank Heat Grey 2'`, `'A/C Heat Pump'`, `'AES Start/Stop'`, `'Gen Start'`, `'Bath Fan'`,
`'Bunk Accent Lts'`, `'Security Lt (GND)'`, … The **G12 section carries no output names**
(checked).

**Corrected 2026-10-19 (`GUICODE.md` Finding 81).** Two statements that stood here were wrong.
"Nothing in the MCF maps a pin to a table entry — that join arrives over the bus": the join is in
GuiCode. One straight-line block of `FUN_00247BF8` (13.6 `0x2494B2`–`0x249794`) copies each name
into its row's cell by literal address, gated on the floorplan register and the OPTIONS registers;
what arrives over the bus is only those registers' values (the config payload, Finding 19). And
"a dynamic row's text is runtime state … a defensible blank": it is runtime only as the floorplan
is, and `outputs.output_name_variants` reads every name with the floorplans and options that show
it by running that block (`cellwriters`). 11.23 has no dynamic rows; it puts its conditional row
labels in the arms of a type-6 switch on the floorplan slot instead (objects 811-821), read the
same way through the switch scan.

This settles the `(CH. 25)` question. At `0x09A07C` the length byte is `0x22` = 34 and the
34 bytes that follow are `"   Cargo / Bath Light\n    (CH. 25)"` — **one string, one
record**. There is no continuation record in this format; the design spec's "continuation
line" and the plan's "~0x49-byte stride" were both wrong. Records are variable-length: the
gaps between the six OPTIONS labels are `0x49, 0x4B, 0x48, 0x5F, 0x4A`, and
`gap − len(label)` is the constant 61.

**Widget.** An opcode-`0x46` list row plus the text record after it. On the OPTIONS screen
that selects the coach's six entries and nothing else, in display order, from a walk that
never matches on label text — reproducing `answer_key.OPTIONS`, which was read off the
physical vehicle. That check is why this section is trustworthy despite being inferred; it
is not a proof.

**Known incomplete — but only this title scan, not the format.** This walk uses `guidata.py`'s
heuristic `TAILS` dict, which covers only the opcodes met on decodable screens; an unknown
opcode raises rather than guessing, and chaining forward from `0x099C54` stops after twelve
screens. (The byte-exact record decoder is `records.py`, which needs no `TAILS` table and
decodes every object — §6, §10 item 7; the incompleteness here bounds only screen discovery.)
Screen discovery *by title* is a validating scan, not an index read, and admits false
positives (`0x099D90` and `0x09A050` sit *inside* the two OPTIONS screens). A title does
not identify a screen: Main Options and Electrical Options both render under `"   OPTIONS"`.
~~A real screen index is presumed to exist and has **not** been found — `unknown`.~~
**FOUND, 2026-08-23 (§10 item 6).** The screen index is not a flat table — which is why the
title scan and every flat-table search missed it — it is the **top-level wtype-6 dispatch
switch** keyed on the current-screen slot `state+0x20A`: its match values are the screen ids
and each child is that screen's root object in the `+0x46148` object table (the display-list
root, GUICODE Finding 12). `layout.screen_object_map` decodes it to `{screen id: object index}`
— 46 screens on 13.6 (42 on 11.23, 37 on 14.7), located structurally so it is build-invariant,
coach-validated (SERVICE MODE is screen 17, caught on the coach), and pinned by
`tests/test_aes_layout_nav.py`.

~~The row record's per-row id (`0x6B..0x70` on the OPTIONS screen) is `unknown`~~ —
**IDENTIFIED 2026-09-10 (§10 item 8): it is the `value_slot` of each row's wtype-6 switch.**
Each OPTIONS row is a wtype-6 value-match switch, and the six on the Main Options screen carry
`value_slot` `0x6b, 0x6c, 0x6d, 0x6e, 0x6f, 0x70` in display order (Heat Pump → Black Tank),
all sharing `child_index = 0x18a` and `count = 3` with candidate match values `{0, 1, 2}`. So
the "id" was never an opaque tag: it is the variable slot the switch reads (via `FUN_00251E38`,
`records.py` R40-R42) to select one of three shared appearance children — `variable_register`
resolves each of `0x6b..0x70` to its own SRAM config cell (`0x2003xxxx`), one per option. The
2026-08-12 "unknown" note predated `records.py`'s `value_slot` decode and so read the field as a
mystery. It is **not** an index into the group table or the receive-filter variable table (§5.4a)
and not the `15FCE` selector — those negatives stand; `value_slot` indexes the GuiCode slot table
`DAT_00253D04`, a different table (§5.4c).

The u16 that closes a row in the `0x0C` record does **not** have one meaning: on
master/popup controls it is a group index into the 233-entry table, which is the join §8.2
rests on, and on the OPTIONS rows it holds `0x0843, 0x084B, 0x0844, 0x0856, 0x0857,
0x0845`, which resolve into the 3295-entry image table. What distinguishes the two uses is
**`id < group_count`**, and the split is proven-from-code — but in the **LCD Firmware**, whose
control scanner bounds its loop with the group count at `0x0021BAD4` (`docs/CONTROL-BEHAVIOR.md`
§1, §10 item 8): an id below the count is a binding group, one at or above it a GuiCode action.
`find_control_groups`' range check at 233 transcribes that bound rather than standing in for a
missing flag.

**Update, 2026-08-15 (`GUICODE.md` Finding 23).** On the OPTIONS rows that u16 is the
**tap action id**: GuiCode's tap dispatcher compares it and branches to the row's handler.
All six resolve, in display order — `0x0843`→Heat Pump, `0x084B`→Bunk Accent,
`0x0844`→Bath Fan, `0x0856`→Cargo / Bath Light, `0x0857`→Bath Light, `0x0845`→Black Tank —
each handler writing that row's own config-buffer offset and emitting that row's own
`0x15FCE` selector, matching `answer_key.OPTIONS` on all six. **All six are
`inferred-from-code`, not proven:** the dispatcher reads the id from a GuiCodeState field
(`ldrh.w r2,[r4,#0x208]` @`0x0024B29E`) and the instruction that copies a record's `+0x0E`
u16 into that field has not been located — so the compares prove what the dispatcher does
with an id, not that the record byte *is* it. Within that inference, **five of the six** rows
are tight to a single id — three by equality compare (`0x084B`, `0x0844`, `0x0856`) and two by
singleton ranges (`0x0843`, `0x0857`) — leaving **one** loose: `0x0845` sits in a range whose
upper bound was not chased. The u16 was read out of the file at record `+0x0E`
(13.6: `0x099FBC`, `0x09A007`, `0x09A04F`, `0x09A0AE`, `0x09A0F8`, `0x09A142`). So on these
rows the value **is used as an action id**; the "resolves into the image table" reading
above only ever meant that the value exceeds 233 and so falls on the far side of the LCD
scanner's group-count bound (§10 item 8). Whether it *also* doubles as an object/image index
is `unknown`; what distinguishes the two uses in general is **resolved** — the `id < group_count`
bound at LCD `0x0021BAD4`. The per-row id `0x6B..0x70` was resolved separately as the
`value_slot` of each row's wtype-6 switch (§10 item 8).

---

## 7. Floorplan table

Sixteen consecutive object-table entries point at one-record objects whose entire content
is a label (`01 00 55 00 00 00 00 00 00 01 ff 02 52 54 00 00` — count 1, opcode `0x00`,
`"RT"`). The **value is positional, not stored**: counting from the last
`"No Floorplan Selected"` gives the enum. The floorplan *buttons* are byte-identical apart
from their label, so nothing in a button says which value it writes.

13.6, joined against the `"   FLOORPLAN SELECTION"` screen for membership and family:

| `0xF5` | code | family | | `0xF5` | code | family |
|---|---|---|---|---|---|---|
| 1 | RT | Generator | | 6 | CB | Generator |
| 2 | RA | Generator | | 7 | WA | Li-AES |
| 3 | RD | Generator | | 8 | **WD** *(this coach)* | Li-AES |
| 4 | SY | Li-AES | | 9 | WT | Li-AES |
| 5 | TB | Li-AES | | 10 | RY | Generator |

`inferred-from-data` — and it reproduces `answer_key.FLOORPLANS` and
`FLOORPLAN_FAMILIES`, both `observed-on-hardware` (PC.5), exactly and without the reader
containing a single floorplan code. **Corroborated from code, 2026-08-18:** GuiCode's
floorplan-highlight cascade compares the same register against `0`..`0xA` and lights the matching
button, and its `cmp` order reproduces this enum value for value (`GUICODE.md` Finding 36). The
enum's *derivation* here is still positional, so the label stands — but it is no longer the only
witness, and the two agree.

Four further codes (`4NJ`, `3NG`, `3NK`, `3NJ`) have string objects at values 11–14 and
button objects, but the selection screen never offers them.

### 7.1 What the codes mean — `owner-supplied`, 2026-08-18

The two-letter codes are model abbreviations. **This mapping is not in the MCF** and is not
derived from it — it was supplied by the coach's owner from product knowledge, and it carries
its own confidence label because of that. It cannot be checked against the corpus, and §10 item
11 (whether the codes expand to full names anywhere in the file) stays **open and negative**:
they do not.

| code | `0xF5` | family | model |
|---|---|---|---|
| `RA` | 2 | Generator | Entegra Ethos / Jayco Swift **20A** |
| `RD` | 3 | Generator | Entegra Ethos / Jayco Swift **20D** |
| `RT` | 1 | Generator | Entegra Ethos / Jayco Swift **20T** |
| `WA` | 7 | Li-AES | Entegra Ethos / Jayco Swift **20AL** |
| `WD` | 8 | Li-AES | Entegra Ethos / Jayco Swift **20DL** *(this coach)* |
| `WT` | 9 | Li-AES | Entegra Ethos / Jayco Swift **20TL** |
| `TB` | 5 | Li-AES | Entegra Expanse / Jayco **Solstice** |

**The naming is systematic across the R/W pair, and it lines up with the family split.** The
three `R*` codes are the plain 20A / 20D / 20T and sit in the *Generator* column; the three
`W*` codes are the same three floor plans in their **`L`** variant (20AL / 20DL / 20TL) and sit
in the *Li-AES* column. So the trailing `L` and the `R`→`W` letter change encode the same
distinction the screen's own two column headings do — an independent corroboration of the
family assignment above, which was derived from the screen's grouping.

**Do not over-generalise it.** The pattern covers six of the ten codes and breaks on the rest:
`RY` is `R*` but has no 20Y, `SY` is Li-AES without a `W`, `CB` is unexplained, and `TB` is a
different product line entirely (Expanse / Solstice, not Ethos / Swift). The remaining codes —
`CB`, `SY`, `RY`, and the four unoffered `4NJ` / `3NG` / `3NK` / `3NJ` — are **unknown**, as is
`Ship Mode`, which is a mode rather than a floor plan (§7.2).

### 7.2 The selection screen, and `Ship Mode` — 2026-08-18

Page object **19** on 13.6 (**21** on 14.7; 11.23 has no such screen). `cli render --page
floorplan` resolves it per build by its header label. It offers **eleven** buttons: the ten
`0xF5` values 1–10 in two family columns, plus **`Ship Mode`**, which sits in the Generator
column but is *not* a floor plan — it is the factory/transit state, and it is what 11.23's
reader wrongly lands at value 1 (see the "enum is per build" note above). The four unoffered
codes at values 11–14 do not appear, confirming that claim from the screen's own composition.

Each button is a **wtype-5 wrapper** carrying `fillA` mode-7 idx **24..34** and a `child_index`
to a two-record object `[wt-33 box 130×50 r5, fillA **mode 0**; wt-11 caption]`. The box
inherits the wrapper's colour through the parent-scoped slots (`GUICODE.md` Finding 29), so the
button fill is fully byte-derived. Ten resolve to `(65,81,163)` button blue; **`Ship Mode`
(idx 28) resolves to `(0,174,239)` cyan** — the highlight colour. `proven-from-code` as a
statement about the **resting** value (`GUICODE.md` Finding 34 addendum).

**The highlight mechanism — RESOLVED 2026-08-18, `proven-from-code`** (`GUICODE.md` Finding 36).
The paragraph here previously said the boot-state reading was `inferred` because "no branch
writing these cells from the floorplan value has been read". That branch has now been read:
`FUN_00247bf8` `0x24987c`–`0x249912` is a **value-selected cascade** on the floorplan register,
which paints exactly one button cyan — `Ship Mode` at value 0, otherwise the floorplan whose
value matched — and every other button blue. So the boot-state reading was right, and a render
can now show the coach's actual state: `cli render --page floorplan --config build/coach-wd.json`
highlights `WD`.

Two by-products worth carrying. The cascade's `cmp` immediates reproduce **§7's enum exactly**,
from GuiCode rather than from object-table positions — a third independent derivation of that
table (see Finding 36 for the three-way check, which is asserted in the suite). And emulating the
cascade at values **11–14** highlights **nothing**, confirming from the code that `4NJ`/`3NG`/
`3NK`/`3NJ` have no button here, which §7 previously had only from the screen's composition.

**The enum is per build, not universal.** 14.7 yields nine codes including `RE`, which
13.6 does not have, and lacking `RD`/`WD`. 11.23 (`Jayco Ethos/Swift`) carries a different
naming scheme entirely (`3RT / 4RT`, `3WD / 4WD`, …) and **has no selection screen**, so
its table comes back `confirmed=False` with `Ship Mode` landing at value 1, which is
almost certainly wrong as a floorplan. Treat 11.23's values as `unknown`.

**Caveat.** The reader depends on three English UI strings — `"No Floorplan Selected"`
(load-bearing: it is the origin for value 0), `"FLOORPLAN SELECTION"`, and the `" Models"`
group suffix. A localised or reworded build raises or reports `confirmed=False` rather
than returning a plausible wrong answer.

---

## 8. Options, channels and bindings

### 8.1 What the coach establishes (`observed-on-hardware`)

| fact | source |
|---|---|
| The six OPTIONS entries and their `15FCE` selectors: Heat Pump `0xEB`, Bunk Accent `0xE5`, Bath Fan `0xE9`, Cargo / Bath Light `0xE3`, Bath Light `0xE4`, Black Tank `0xEC` | `0xEB` and the `E3`/`E4` pair were established by toggling in **PC.2**; `0xE9` by toggling in **PC.3**. `0xE5` and `0xEC` come from rvc2mqtt's prior table and were reconfirmed on this coach — broadcast values in **PC.1**, the restore check in **PC.5**, and the owner's toggles in **PC.9** |
| `0xE3`/`0xE4` are one mutually exclusive pair, written together in opposite directions | PC.2 |
| Gating mechanism 1 — **rebinding**: switching `E3`/`E4` moves the Bath control between channel 25 and 26, and sends an explicit OFF (command 3) to the abandoned channel | PC.8 |
| Gating mechanism 2 — **visibility**: plain booleans `E5`, `EC` make their control disappear from the screen entirely. This emits no CAN frames and was only observable on the display | PC.9 |
| RV-C instance == G12 channel == PDF pin number, across 20 channels | PC.8, PC.9 |
| The option *list* is shared across floorplans; only the defaults differ | PC.5 |

### 8.2 What the file establishes: the channel join

A control's `0x0C` record ends in a u16; when that u16 is a valid group index, the group's
slice of the binding table says which G12 channels the control drives.

Offsets in the block below are **section offsets into GuiData**, not file offsets — the
one exception to this document's file-offset convention (see the preamble), because the
`+0x30` group table and the `+0x778` binding table are read from the section header and
are section-relative by construction. For 13.6, file offset = section offset + `0x029438`;
the file offset of each line is given beneath it.

```
sect 0x06BF01   0c 55 0f 00 00 00 00 00 f6 ff f6 ff 5a 00 41 00  01 00  <- group 1
(file 0x095339)
+0x30+1*8       11 80 0c 80 02 00 1a 00                      start 2, count 26
(file 0x029470)
+0x778+2*12     db fe 01 02 00 fb ff 16 ...                  channel 22, ...
(file 0x029BC8)
```

`inferred-from-data`, and it is a derivation rather than a fit — nothing in the chain
matches on a channel number or on label text:

* "Light Master"/ON → group 1 → channels 22, 24, 25, 26, 32, 34, 35, which is
  `INTERIOR_MASTER` — as the **union** over the group's byte-2 variant copies. No single
  variant holds all seven; "union" is load-bearing here and not a hedge.
* All 12 master ON/OFF pairs across three builds resolve to consecutive group indices;
  every ON group reaches full level and every OFF group is all zero.
* In 14.7 the four tank-heater labels and Water Pump resolve to channels 5, 6, 7, 8, 44 —
  the pinout's `TANK HEAT FRESH/GREY/BLACK/GREY 2` and `WATER PUMP`. The file predicts the
  sheet; it was not fitted to it.
* 13.6, which has no Fresh Tank Heater screen, has group 5 as a stub (DGN `0xFFFF`,
  no channel) where 14.7's group 5 carries channel 5.

**Publication caveat — carry this wherever the channel sets are quoted:**

> GuiData's binding table gives the UNION of channels commanded by a control's group
> across its byte-2 variant copies; which variant applies in a given build/vehicle is not
> known, so for groups with more than one variant (Light Master, Exterior Master — where
> channel 35 sits) the exact per-variant membership is unsettled by this file alone. The
> CAN-capture answer key remains the direct evidence for exact membership; GuiData
> corroborates rather than overrides it.

**Level fields.** `ControlGroup` exposes `levels` (every distinct level byte, sorted),
`has_full_level` ("some entry sets `0xC8` or above") and `all_levels_zero` ("every entry
sets `0x00`"). These are statements about bytes and are deliberately **not** named or
described as "turns on" / "turns off": most groups contain both a zero and a full-level
entry, so most groups do both. Group 80 reaches `has_full_level` on `0xFA` alone, a value
outside the `0..0xC8` brightness range whose meaning here is `unknown`.

**Heuristic, disclosed.** `find_control_groups` separates group references from image
references by a range check only. On the OPTIONS screen the same `0x0C` field resolves into
the 3295-entry image table instead (`0x0843`, …; that object 0x0843 is 96 bytes is fact,
that it is a *bitmap* is inference from its position and byte histogram). An icon index
that happened to land under 233 would be reported as a control.

**Cross-reference, 2026-08-15 — do not read that "resolves into the image table" as a
decode.** On the OPTIONS rows the same u16 is the **tap action id** (§6.1, `GUICODE.md`
Finding 23), and §6.1 states what the image-table reading was ever worth: it "only ever meant
that the value exceeds 233 and so falls on the far side of `find_control_groups`' disclosed
range check". Whether those values *also* index an image is `unknown`, as is the
discriminator between the two uses. The heuristic's behaviour here is unchanged; only the
inference one may draw from it is narrower than the sentence above suggests.

### 8.4 Per-floorplan option defaults — RECOVERED, `proven-from-code` (2026-08-18)

Selecting a floorplan makes the panel transmit a burst of `0x1FED9` config writes, and those
values are literals in its own flash — **not** a stored table, which is why every search for one
came back empty (`GUICODE.md` **Finding 42**; §8.3's fourth row was the standing negative).
`guidata.find_option_defaults(data, value)` returns them by name;
`floorplandefaults.floorplan_defaults(data)` returns the raw selector map.

**How to read it.** Each row is what the panel writes when that floorplan is chosen. Twelve
selectors go out per floorplan (Ship Mode writes eight — it is a transit state, not a floor plan).
`0xF5`, the floorplan value itself, is omitted from the table because every handler writes its own
value; that self-identification is what attributes a handler without needing an oracle.

| floorplan | `EB`<br>Heat Pump | `E5`<br>Bunk Accent | `E9`<br>Bath Fan | `E3`<br>Cargo/Bath | `E4`<br>Bath Light | `EC`<br>Black Tank | `EF`<br>gen/AES mode | `E6`<br>Prog. Inv. | `D7`<br>GoPower ctrls | `15`<br>AGS cfg | `F7`<br>retry |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Ship Mode** (0) | 0 | 1 | 0 | — | 0 | — | — | — | 1 | 1 | 60 |
| **RT** (1) | 0 | 1 | 0 | 1 | 0 | 1 | 1 | 1 | 1 | 1 | 60 |
| **RA** (2) | 0 | 1 | 0 | 1 | 0 | 1 | 1 | 1 | 1 | 1 | 60 |
| **RD** (3) | 0 | 1 | 0 | 1 | 0 | 1 | 1 | 1 | 1 | 1 | 60 |
| **SY** (4) | 0 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 60 |
| **TB** (5) | 0 | 0 | 0 | 0 | 0 | 1 | 3 | 0 | 0 | 1 | 60 |
| **CB** (6) | 0 | 0 | 0 | 0 | 0 | 1 | 1 | 1 | 1 | 1 | 60 |
| **WA** (7) | 0 | 1 | 0 | 1 | 0 | 1 | 3 | 0 | 0 | 1 | 60 |
| **WD** (8) | 0 | 1 | 0 | 1 | 0 | 1 | 3 | 0 | 0 | 1 | 60 |
| **WT** (9) | 0 | 1 | 0 | 1 | 0 | 1 | 3 | 0 | 0 | 1 | 60 |
| **RY** (10) | 0 | 1 | 0 | 0 | 0 | 1 | 1 | 1 | 1 | 1 | 60 |

**What each selector is**, from the bus captures (`BUS-BASELINE.md` PC.1/PC.4, `observed-on-hardware`):

| selector | meaning |
|---|---|
| `EB` `E5` `E9` `E3` `E4` `EC` | the six OPTIONS-screen entries: Heat Pump, Bunk Accent, Bath Fan, Cargo/Bath Light, Bath Light, Black Tank |
| `EF` | **gen/AES mode** — `0` none, `1` AGS (generator), `3` AES Single Output |
| `E6` | **Progressive Inverter** |
| `D7` | **Go Power! controller count** |
| `15` | AGS config (always `1`) |
| `F7` | AGS retry interval (always `60`) |

**Three patterns fall straight out, and each is a cross-check rather than a new claim.**

* **`EF` tracks the floorplan family.** The five *Generator* plans (RT, RA, RD, CB, RY) all default
  `EF = 1` (AGS); the *Li-AES* plans WA, WD, WT and TB default `EF = 3` (AES Single Output). This
  is the §7 family split, derived independently — from the selection burst rather than from the
  screen's column headings. **SY is the exception**: Li-AES by family, but it defaults `EF = 0`
  (none). Recorded, not explained.
* **`E6` moves with `EF = 1`.** Every plan defaulting to AGS also defaults Progressive Inverter on;
  every AES plan defaults it off. That is the dependency PC.4 Result 2 observed live ("`E6` forces
  `EF` = AGS"), now visible in the shipped defaults.
* **`D7` explains the GO POWER! tab.** The tab is gated on this count being non-zero
  (`GUICODE.md` Finding 41). Generator plans default `D7 = 1`, so the tab is **shown**; every W\*
  plan and SY/TB default `D7 = 0`, so it is **hidden**. WD defaults to 0, which is exactly why the
  coach's ALTERNATOR screen has no GO POWER! tab. This also **closes Finding 41's open question**:
  the count an unconfigured coach carries is whatever its floorplan wrote, and for WD that is 0 —
  so the `>5 → 1` clamp defect there stays latent, since nothing sends an out-of-range value.

**Confidence.** The mechanism and every value are `proven-from-code`. The *meanings* of the
selectors are `observed-on-hardware` from the captures, not decoded from the file — the file
carries the numbers, never the names. All 90 entries of `answer_key.FLOORPLAN_OPTION_DEFAULTS`
reproduce, with `0xD8` correctly absent because the burst never writes it (PC.12a).

### 8.3 What the file does **not** establish

| question | status |
|---|---|
| Which record binds an option to its `15FCE` selector | **The question was mis-posed — rewritten 2026-08-15** (`GUICODE.md` **Finding 23**). No record holds the selector, and none ever will: the selector is a literal byte inside a static 12-byte CAN-frame template in GuiCode `.rodata` (Finding 7's pool, 13.6 `0x00259430..0x00259ACC`), and it is joined to the option's **config-buffer offset** by **code adjacency** in a per-row tap handler — not by a table, a record field or any other data structure. So "the offset→selector join is not a file structure" **stands**; what falls is "the join is unknown" and "the translation lives host-side". The binding is a **three-link chain**, one link of which *is* in GuiData: the OPTIONS row's `0x0C` record closes with a u16 (§6.1) that is — `inferred-from-code`, see below — the **dispatcher action id** → GuiCode's tap dispatcher maps it to a handler → the handler carries the cfg offset as an immediate (`movs r1,#<off>` before `bl 0x00259100`) and the selector as the adjacent template's byte 2. All six of this coach's options resolve this way and reproduce `answer_key.OPTIONS`. `src/fireflymcf/config.py` extracts the offset→selector half (`selector_sites`, `option_selector`); `guidata.find_option_bindings` now returns name + selector. **Confidence, split by link — the chain as a whole is *not* proven:** the **offset→selector** mechanism is `proven-from-code` (a cited `bl`, a cited `.rodata` template), with individual attributions graded A2/A1/B+ in Finding 23 and the two 14.7 rows `inferred-from-code` and **unverified**; the **record u16 → action id** link is `inferred-from-code` for *every* row (§6.1, Finding 23) and cannot currently be anything else — the dispatcher reads the id from `GuiCodeState+0x208` (`ldrh.w r2,[r4,#0x208]` @`0x0024B29E`) and the instruction that copies a record's `+0x0E` u16 into that field has not been located, so the compares prove what the dispatcher does with an id, not that the record byte *is* it. |
| The `E3`/`E4` per-option channel binding (25 ↔ 26) inside the file | **RESOLVED 2026-09-02** (`GUICODE.md` **Finding 61**, `optiongates.py`). Not a rebound control: **two** type-6 switches on every LIGHTS page, one keyed on `E3`'s slot 110 revealing object 370/372 (touch id 25 → group 25 → channel 25), one on `E4`'s slot 111 revealing 374/619 (touch id 26 → group 26 → channel 26); the tap handlers' mutual exclusion makes one visible at a time. The Finding 23 OFF-template route (`config.off_channels_by_selector`) agrees. `find_option_bindings` fills `channel`; the xfail is lifted. |
| The per-control visibility flag for `E5`/`EC` | **RESOLVED 2026-09-02 — there is no flag** (`GUICODE.md` **Finding 61**). The option's value slot keys a type-6 switch whose 0-arm draws nothing: Bunk Accent (slot 108) base 375 → 376 on every LIGHTS page; Black Tank (slot 112) base 801 → 802 (the HOME BLACK gauge) on the tank clusters 912-921. `hides_control` is derived structurally (no wt-12 and no wt-5 child in the 0-arm); Heat Pump comes out *not* hidden, which keeps the predicate honest. |
| Per-floorplan option defaults | **RECOVERED 2026-08-18** — `GUICODE.md` Finding 42. Not stored anywhere: each floorplan's tap handler *emits* them as literal `<selector, value>` bytes in static `0x1FED9` CAN-frame templates. `guidata.find_option_defaults` returns them; all 90 answer-key entries reproduce. |

The three that remain are not unexplored; they are measured negatives, encoded as **five** of
the suite's six strict `xfail` tests, which will fail the day the structure is found (there
were seven until 2026-08-15, when the selector join closed two of them —
`test_every_option_has_a_binding` and `test_selectors_match_the_live_protocol` now pass; the
sixth xfail belongs to §10 item 11a, not to this section). What was ruled out — and note why most of
these had to come back negative: each looked for the selector **stored beside an option**, and
it is not stored, it is **emitted** from a pre-built frame (Finding 23):

* The OPTIONS screen object (848 bytes) contains **zero** occurrences of `0xEB`, `0xE5`,
  `0xE9`, `0xE3`, `0xE4`, `0xEC`, and no `ce 5f`.
* The binding table contains **zero** `0x15FCE` entries in any build (0/746, 0/732,
  0/747). Checked while writing this document, and **scoped deliberately**: among the 190
  group-`0x96` writes in 13.6 that take the `ff 96 <sel> 0f` form the selector position
  holds 37 distinct bytes, **none** of them one of the six option selectors — same result
  in 11.23 and 14.7. That is a statement about the group-`0x96` selector position only; see
  the `0x94` lead below before reading it as "the option toggles are absent from the file".

* **A further lead, and a caveat on the negative above.** Group-`0x94` entries put `0xE3`,
  `0xE4` or `0xE5` in payload byte 6 — the byte PC.7 labels "function" — 15 times in 13.6
  (`E3`×7, `E4`×2, `E5`×6), 15 in 14.7 and 24 in 11.23, clustered in groups 37, 38, 39, 41,
  42, 45, 46, 49, 50, 85, 86 (11.23 also 15 and 43, and lacks 86). Whether byte 6 is a
  *selector* there is `unknown`, and there is real evidence against reading it as one: the
  same byte position holds `0xE0` more often than anything else (15 of 33 entries in 13.6),
  plus `0x12`, `0x13` and `0xF0`, so it behaves like a small function space that happens to
  overlap the option selector range. What group-`0x94` messages *do* is not understood at
  all — PC.7 caught four of them on the coach and could not establish their purpose, and
  this work has not either. Recorded because it is the most option-shaped thing left in the
  file, not because it is evidence.
* The row id is not an index into the group, variable, binding or object tables.
* No stride in 1..32 stores the six selectors in display order anywhere in any file; no
  `(row id, selector)` pair table exists at any stride in −64..64.
* `Lyra 13v6 GuiCode` contains zero occurrences of `ce 5f`. **Still true, and now explained**
  (`GUICODE.md` Finding 23): the panel never transmits `0x15FCE` — that DGN is the *G12's*
  broadcast. The panel writes with DGN `0x1FED9`, whose frames appear in GuiCode as
  `00 d9 fe 19 | ff 96 <sel> 0f <val> 00 d1 ea` templates. Searching for `ce 5f` was searching
  for the wrong DGN, which is a separate reason this negative could never have found anything.
* The G12 section's rule table declares the selectors again (`ce 5f c1 0c XX XX 00 XX …`)
  with no channel, name or flag beside any of them.
* For the defaults: all four GuiData header tables, the six OPTIONS row records, the
  byte-identical floorplan buttons, the whole G12 section, and a brute-force scan for a
  10/11/14/16-row boolean matrix at strides 6/7/8/12/16 across all three files. A known
  blind spot remains: a **bitmask** packing (one byte per floorplan, population count ≤ 6)
  would not be found by that scan shape.

**Superseded, 2026-08-15.** This paragraph used to read: *"Strongest remaining lead for all
four: the `Lyra … GuiCode` bytecode, which no task has decoded, followed by an `LCD Firmware`
disassembly at `0x00210000`."* GuiCode is **not** bytecode (it is native Thumb-2, `GUICODE.md`
Finding 1 onward), it **has** been decoded, and following it is what closed row 1 — the lead
was right about *where* and wrong about *what*. For the three questions that remain, the
tractable next step is the same place: the **tap handlers and the dispatcher** in GuiCode
(Finding 23) — specifically what else a handler does besides `config_set` + emit (the Bath row
already emits an extra `0x1FEDB` instance `0x19`/`0x1A`, which is the `E3`/`E4` rebinding's
shape) — together with the GuiData side already mapped in Findings 16-17, where an
option-conditional widget is selected by a type-6 value-match switch reading the option's own
register. Which of the two carries the `E5`/`EC` visibility effect is still `unknown`. Finding
19 is the standing caution for the fourth question: the per-floorplan **defaults** are not in
any section at all, so it is a different kind of open.

---

## 9. Byte coverage — what 100% does and does not mean

**Use this wording, not a shortened one:**

> 100.0000% of every corpus file's bytes lie within a claimed span — every byte is
> accounted for by some structure. That is **not** the same as the format being decoded.
> On 13.6, **~65% of the GuiData section is decoded** in the sense that a parser recovers
> its byte layout and something validates the result: **20.5%** (182,318 B) is widget-record
> spans decoded by `records.py`, and **44.6%** (395,852 B) is glyph bitmap data decoded by
> `glyphs.py`. The two are disjoint (records `0x47781..0x74D70`, glyphs
> `0x783B8..0xD8E04`). Additionally, ~5.5–7.5% of each file (58,092–60,148 bytes) sits in
> "trailing object" spans whose end is asserted only by a size bound, not by a decode or a
> next-pointer. Three of the four object tables are non-uniform, and their last object is
> bounded only by "no larger than a sibling this same table delimits" — up to ~55 KB of
> slack on the biggest. The fourth is a uniform 20-byte-stride table, so its last object is
> pinned to exactly one stride (not a heuristic — its zero headroom is correct by
> construction, §10 item 13). "Claimed" means "a
> parser drew a boundary here that the file's structure is consistent with", not "the bytes
> inside are understood" — and **"decoded" means the layout is recovered, not that every
> field's meaning is known.** Many decoded fields are still labelled `unknown`.

**What validates each figure.** The record figure is not a self-report: `records.py` replays
`FUN_00252CB0` and its output is checked by the **object-table boundary oracle** — an object's
records must tile its table-derived span exactly — which passes on **every object in all three
builds** (1392 non-empty of 1422 in 13.6, zero derailments; the 30 empty spans are the repeated
pointers noted in §5.5). That is enforced, not asserted: `tests/test_records.py::
test_every_object_decodes_to_its_boundary` is parametrised over all three builds. The glyph
figure is validated by no glyph over-reading its record in any build, and by proof glyphs
reproducing (`GUICODE.md` Finding 22).

**Corrected 2026-08-18 — and note *how* it was stale.** This section previously led with
*"only ~1.8–2.6% of the GuiData section is decoded at the record level"*, measured as the union
of the spans `guidata.iter_screens` walks. **That figure is still accurate for what it
measures** — re-measured on 13.6 it is 2.44%, matching the 2.4% quoted. What made it
misleading was that it measures the *original boundary-fitted heuristic walk*, which
`records.py` superseded; it was never re-scoped to the byte-exact decoder, and `README.md`
quoted it as the project's headline caveat. Both figures are kept above so the difference is
visible: `iter_screens` covers 2.44%, `records.py` covers 20.5% of the same section.

*(Recorded because it is this project's recurring failure mode: the old number was not wrong,
it was answering a question no one was asking any more. Also recorded because the first attempt
at this correction claimed `iter_screens` "now yields zero spans" — that was a measurement bug
in the checking script, which read a `size` attribute that does not exist, and it would have
put a fabricated negative into the spec. Check the attribute, not the empty sum.)*

Composition of 13.6 at the deep pass: 75.3% object spans (start read out of the object
table, interior undecoded), 16.7% whole-section spans (descriptor extent plus a verified
CRC), 4.9% pure-`0x00` padding, 1.8% object tables, 1.2% GuiData tables, ~0.1% headers and
checksums.

---

## 9a. The G12 automation rule record (12 bytes)

`proven-from-file` + `observed-on-hardware`, 2026-08-24. The G12 section's fixed-stride tables
`0x20`, `0x30`, `0x61` and `0x62` hold **151 records in one format**, and every one is a
**transmit template** — a frame the G12 sends, with some bytes filled in from stored
configuration at send time. *(**Corrected 2026-09-02, §9a.3:** that was validated on `0x62` and
`0x20` and does **not** hold for table `0x61`, whose 17 records are a cell ↔ bus status table —
the same 12-byte stride, a different layout from `+3` on. Read `0x61` through §9a.3, not this
table.)*

| offset | width | field | confidence |
|---|---|---|---|
| `+0` | u16 LE | DGN, low 16 bits | `proven` |
| `+2` | u8 | bits 0-1 = DGN bits 16-17; bits 2-7 a **kind field**, 15 distinct values, **undecoded** | `proven` / `unknown` |
| `+3` | u8 | **substitution mask**, one bit per payload byte | `proven` |
| `+4`..`+11` | 8 B | payload template, in the order `[7,6,5,4,11,10,9,8]` | `proven` |

The payload order is **two little-endian u32 words** — byte for byte the same layout the panel's
own binding table uses for its 8 CAN data bytes (§8, `newbutton.binding_payload`). Both ends of
the bus store a frame the same way.

**Substitution.** A payload byte is a literal unless its mask bit is set, in which case the byte
names a **config selector** and the G12 substitutes that selector's stored *value*. Mask bit set
over `0xFF` means "substitution slot, no selector assigned" — nothing is substituted and `0xFF`
goes on the wire. **Adjacent masked slots naming the same selector are one little-endian u16.**

**Validation, both directions.** Positive: four charger/inverter rules reproduce captured frames
byte for byte on every non-substituted byte (`BUS-BASELINE.md` PC.23), and 44 of the periodic
`0x15FCE` sweep templates do the same — including a cross-record case where one record's frame
carries a *different* selector's value and matches that selector's own frame. Negative: selector
`0x99` relays nothing on the coach and appears in no record, a prediction the bus could have
falsified.

**The `0x15FCE` "registry" is not a separate structure.** Records beginning `ce 5f c1` are
ordinary records of this format whose DGN is `0x15FCE`; each is the template for the periodic
broadcast of one selector. *Consequence for tooling:* the registry names **config selectors, not
output pins** — `g12_config.output_config` used to test `pin_number in signal_registry(...)` and
so flagged pins 1, 3-6, 10, 12-15, 17, 21, 22, 38, 43 and 44 purely on a numeric coincidence
(selector `0x0F` is `Min Run Time` = 1800 s, `0x31` is the AES Start-at = 1020). Fixed
2026-08-24; no build had read the field.

**Editing.** `src/fireflymcf/g12rules.py`, `cli g12-rules`. Every field is a same-size byte
edit whose blast radius is that byte plus the G12 section's 2-byte checksum, and the G12 section
is independently flashable — so a rule change ships as a **G12-only build** that never writes the
GuiCode/GuiData pair. Nothing here has been flashed.

### 9a.1 The tables are not interchangeable — table `0x20` carries the INPUT pins

Added 2026-08-24 (`GUICODE.md` **Finding 54**). The format above is one format, but the four
tables hold different *kinds* of rule, and a tool scoped to one of them will silently under-report:

| table | records | what is in it |
|---|---|---|
| `0x20` | 32 | the **input-pin handler** — 18 `0x1FBDA` indicator records (9 instances × ON/OFF) interleaved with 14 `0x1FEDB` output commands |
| `0x30` | 8 | `0x1FEDA` |
| `0x61` | 17 | mixed, 13 distinct DGNs, mostly undecoded |
| `0x62` | 94 | the **config-selector command rules** and the 52 `0x15FCE` sweep templates |

**G12 input pin N is broadcast as `0x1FBDA` GENERIC_INDICATOR_COMMAND, instance `0xA0 + N`,
state in `payload[1]`** — matched by the panel's own receive filter (§5.4a) at vars 192-201 and
painted on the DEVICES/FAULTS screen. It is **not** a config selector and never appears in a
`0x15FCE` frame; the two mechanisms share only the record format. `cli g12-inputs`.

In table `0x20` the record's `+3` byte is **not** the substitution mask — it is a gesture
**phase mask**, the same encoding as a panel binding's byte `+0x03` (`1` press, `2` tap-release,
`4` held, `8` release-after-hold). Pin 1, a light switch, fires `02`→command 5 (toggle),
`04`→command 21 (ramp), `08`→command 4 (stop): the standard light gesture. Indicator pairs are
`05` (press|held) → report ON, `0A` (the two releases) → report OFF. `inferred`. `+2` remains
undecoded. **This is the concrete case behind `+2`'s "kind field" being marked `unknown`: the kind
changes what the later bytes mean**, so a decoder must not carry one table's reading into another.

### 9a.2 Table `0x10` — the input dispatch table

`proven-from-file`, 2026-08-24 (`GUICODE.md` **Finding 54a**). Ten 8-byte entries, one per input
pin, naming the run of table-`0x20` rules that pin fires. This is what binds a rule to its trigger;
the rule record itself carries none.

| bytes | field | confidence |
|---|---|---|
| `+0..1` | `0x0010`, an entry tag | `proven` |
| `+2` | `0x0C` configured, `0x00` unconfigured | `proven` |
| `+3` | bit `0x40` = 12V sense, clear = switched to ground | `proven` (agrees with all six screen captions' `(12V)`/`(GND)` suffix) |
| `+4..5` | u16 index of the pin's first rule in table `0x20` | `proven` |
| `+6` | rule count (0 = the pin fires nothing) | `proven` |
| `+7` | `0xFF` | `unknown` |

**Input pin 10 fires four rules** — the `0xAA` input indicator pair *and* the `0xFB` engine-running
pair, byte-identical apart from the instance. So `Engine Running` on ELECTRICAL and the
`10 - Ignition(12V)` diagnostics dot are one input reported twice and cannot disagree.

### 9a.3 Table `0x61` is a cell ↔ bus STATUS table, and the kind byte — 2026-09-02

`proven-from-data` for the join, `inferred` for the layout, `observed-on-hardware` for every
period. `g12rules.status_rules` / `kind_census`; `cli g12-rules` prints both. (`GUICODE.md`
**Finding 62** carries the narrative.)

**Why the template reading fails on `0x61`.** Its `0x1FBDA` record, read as a `[7,6,5,4,11,10,9,8]`
template, yields `FF 28 FB FB 01 01 00 00` — a frame the G12 has never sent in 34 captures. Read as
the layout below, the same record says *"payload byte 1 of `1FBDA` instance `0xFB` ↔ config cell
`0x28`"* — and cell `0x28` is struct `+0x50`, the ignition/engine-running cell the `0x67` blob reads
and never writes (`G12-CODE.md`).

| offset | field | confidence |
|---|---|---|
| `+0..2` | DGN, as §9a; `+2` bits 2-7 = kind | `proven` |
| `+3` | the **payload byte index** the value occupies | `inferred` (voltage lands at bytes 2-3, temperature at 1-2, tank at 1 — all wire-checked) |
| `+4`, `+5` | instance low / high (a filter or range; `FF FF` = any) | `inferred` |
| `+6` | **config cell / selector** | `proven-from-data` — the `0xC2` join below |
| `+7` | `0xFF` (`0xF0`/`0xF1` on two records) | `unknown` |
| `+8..+11` | two u16 parameters — a value bracket on transmit records (`200..320` = 10.0-16.0 V; `7100..11368` = −51.1-82.25 °C) | `inferred` |

**The join, proven both ways on one number.** Table `0x62` #93 (`b7 ff 81 02 ff 64 c2 03 …`, an
ordinary masked template) puts *selector* `0xC2` at byte 1 of `1FFB7` instance 3 (LPG), and the wire
carries `19FFB79C#035064…`/`035164…`. Table `0x61` #16 names *cell* `0xC2` at byte 1 of `1AAB7`
(Firefly's private mirror of `1FFB7`), and the wire carries `19AAB79C#035064…`/`035164…` — 657 of
681 nearest pairs agree, the rest 1 Hz phase skew across an `80`↔`81` flip. Two tables, two
mechanisms, one cell. Corroborated cross-build: the DC-voltage record reads cell `0x56` on 13.6
and `0x0A` on 11.23/14.7, exactly the builds where `g12code.start_arming` shows no `0x55`/`0x56`
split; 13.6's new `1FFFD` record reads `0x55`, the AES-mode voltage cell.

**13.6's 17 records** (`0x1025F4` + 12·#): six 1 Hz transmits observed byte-position-exact —
`1AAFD` DC source (cell `0x56`, instances 1 and 2), `1FFFF` date/time (hour ↔ cell `0x2A`, the cell
`+0x0E02` packs), `0BFC1` tank sensors 1-4 (cell block `0xB0`), `1FF9C` ambient (cell `0x6B`),
`1AADC` ×2 (cells `0x58`/`0x59`, generator = none), `1AAB7` LPG (cell `0xC2`); one transmit-class
record never seen (`1FFFD` from `0x55`, gate unknown, silent even in AES mode); seven receive-class
records (`inferred`): `1FBDA` `FB` → `0x28` (**the candidate feed for `+0x50`**), `1FBDA` `FE` →
`0x3A`, `1FBDA` `A2` (input pin 2) → `0x4A`, `1FF96` charger config inst 1-2 → `0x84`, `1AADC` → `0x2D`,
`1FFFE` set-time → `0xF6`, `1FECA` DM_RV (`80 80`) → `0x53` = struct `+0xA6`, the AES watchdog's
"is set" gate; plus `1FEDA` ch 20 (kind `0x5C`, unobserved). 14.7 feeds the same cell `0x28` from
**`1FFB8` DIGITAL_INPUT_STATUS** instead of `1FBDA`, with zero table-`0x20` rules — the Terrain
reads ignition off the bus, not a pin. 11.23 subscribes to the *panel's* own indicator `F9`.

**The kind byte (`+2` bits 2-7), 15 values, every `0x62` value now wire-attributed.** *Within
`0x62`*: **bit 7 set ⇔ periodic** (`0x81` 1 s — `1FFB7`; `0xC1`/`0xCD` 5 s — the `15FCE` sweep and
the A/C program's ON re-sends), **bit 7 clear ⇔ event-fired** (`0x01` config relay *and* the
never-fired AES-stop group #4-#7; `0x09` the AES-start group, all four records within 2 ms in pc39 —
the "two real `19FED99C`" frames of Finding 52's addendum are its #2/#3; `0x21`/`0x25`/`0x29`/`0x41`
the A/C program's end / start-A / start-B / 3×-at-250-ms phases). Bit 6 reads as *repeat*
(`inferred`). **A global bit semantics is refuted**: `0x24`/`0x28` are event pairs in `0x62` and
1 Hz broadcasts in `0x61`, where bits 2-3 track value width (u8 / u16 / per-instance) instead.
Readings are keyed on `(table, kind)` — `g12rules.KIND_OBSERVED`.

**Table `0x20`'s `+2` (the byte §9a.1 left undecoded): bit 2 = re-fire at 1 s while the input
stays active** (`CommandRule.repeats_while_held`, `inferred`). It is set on exactly the 13 (13.6)
+ 10 (11.23) records whose phase mask carries *held* — the nine ON indicators, the ramp, and the
EXT/RET `duration 2` commands — and PC.24 shows ON indicators re-sending at 1.000 s while no OFF
indicator ever repeats. Redundant with the phase mask on both shipped builds, so it cannot be
separated from "held" on the wire without editing a record.

**A mode gate seen on the way** (`observed`): the *standard* `1FFDC` and `1FFB7` inst 3 broadcast
only while `0xEF` ∈ {0, 1} (ON edges ≤ 35 ms after an `EF := 0/1` write, nine captures); no 13.6
record carries `1FFDC`, so its emitter is app-side. Table `0x10` `+7` stays `unknown` (`FF` on
13.6, mixed on the siblings, not derivable from the other fields).

**RECEIVE RECORDS CONFIRMED TO WRITE THEIR CELL — `observed-on-hardware` 2026-09-03**
(`BUS-BASELINE.md` **PC.48**). The experiment proposed here was run: an injected
`19FBDAC0#FB01FFFFFFFFFFFF` from a foreign source address with the key off made the G12 emit
PC.27's exact LOCK signature 4 ms later, and UNLOCK 2 ms after the clear, against a baseline
containing zero `19FEDB` frames. So the class is proven, **`+0x50` is bus-fed** (which is why
`G12-CODE.md` finds no store to it in the blob), and the lock is enforced at the output — the same
panel `EXT` command drove level `C8` unlocked and level `00` locked. One caution the run
surfaced: the panel's own `Locked by Ign` label is fed by the **`0xAA`** pin-10 indicator, not by
`FB`, so the two consumers can disagree.

**Still unknown, with the experiment that settles each:** whether `+4/+5` is a filter or a range,
and `80 80` on DM_RV (needs a second instance on the bus); `1FFFE` to set the G12 clock and watch
`1FFFF` follow (cheap, non-mutating).

## 10. Open questions

Carried forward from the design spec, plus what this work added. The spec's own
*Open questions* section records the same dispositions.

**Container**

1. ~~`cli.bin` region 2 (`0x6B94` bytes) — its declared load address `0x20000000` overlaps
   region 1 and cannot be literally correct.~~ **RESOLVED 2026-08-20 (§4.2):** it loads at
   **`0xC0400000`** — the header's `+0x24` word — and is the loader's own GUI resource blob
   (magic `0x8F7E6D5C`, self-pointers `0xC0400xxx`, and 93 of region 1's 102 literals into
   that range landing on readable strings). Its declared `+0x18` address is not where it goes.
   What stays open is narrower: the blob's **internal record format** (length-prefixed like
   GuiData's, otherwise undecoded), and whether its pointers are fixed up at load.
2. The `cli.bin` header word `0xC0400000` (+0x24) — **answered 2026-08-20 (§4.2)**, it is
   region 2's load address. The per-build `+0x28` word (`0x9E3`/`0x1E6A`) is **open, and out of
   code-first reach** — a defensible negative recorded 2026-09-10 (§4.2). Its only reader is the
   panel's primary bootloader, whose magic `0xC11B102E` is in no corpus image; and it is
   demonstrably not a checksum (sum8/16/32, xor16, Fletcher-16, CRC16, CRC32 over region 1 /
   region 2 / header / file), not a clean region-2 offset, not region 1's entry vector, and not
   referenced by any region-1 literal. Resolving it needs the boot ROM, which is not in the MCF.
3. ~~`mcf.bin` `+0x06` = `0x0001`, `+0x08..+0x1F`~~ — **RESOLVED 2026-09-10: reserved.** The
   container parser in `cli.bin` reads the header as a fixed `0xA4`-byte block, folds the whole
   block into a header checksum, and then reads exactly four fields out of it — `+0x00` (magic),
   `+0x04` (checksum locator), `+0x20` (count), `+0x22` (header size). `+0x06` and `+0x08..+0x1F`
   are covered by the checksum but read by no field access, and they are constant across the
   corpus (`+0x06 == 0x0001`, `+0x08..+0x1F` all zero) in all three builds. Those offsets are
   `≥ 0x8e8` off the struct base, reachable only by the wide T3 encoding, so the negative is real
   and not a scan blind spot (`docs/PARSER-LOCATION.md`, header table;
   `tests/test_container.py::test_the_loader_reads_only_four_header_fields`).
   **Descriptor `+0x45` and `+0x50..+0x57` — RESOLVED 2026-09-10 (§2).** `+0x45` (=`0x03`) is
   **not reserved**: it is a flags byte the loader reads from the state-struct descriptor copy
   (`ldrb.w r3,[r4,#0x239]` at `0x20016a5e`/`0x20016a8c`) and branches on — bit 1 (`0x02`) and
   bit 2 (`0x04`), each gating a call into the tagged-entry/section helpers
   `0x20014734`/`0x20014238`. Stock `0x03` enables the bit-1 path and disables bit-2; the exact
   action each bit gates is not fully traced, but it is a tested bitfield, not padding. `+0x50..
   +0x57` **is** reserved — 8 zero bytes between the `+0x4C` payload-length field and the `+0x58`
   tagged entries, read by nothing (fixed-field reads stop at `+0x4C`, the entry walk is indexed
   from `+0x58`). Pinned by `tests/test_container.py::test_the_loader_reads_descriptor_plus_45_
   as_a_flags_byte` and `::test_no_state_struct_read_touches_descriptor_plus_50_to_57`. With
   this, **item 3 is fully closed.** ~~and every tagged-entry tag except `0x40`~~ —
   **RESOLVED 2026-08-20 (§2):** the tag namespace is a firmware table indexed by the tag
   (`0xC0400010 + tag*4`), the entry's `+0x02` is a phase-and-operator flags word, and every
   entry in all three builds decodes. What is left inside that structure is narrow: ~~what
   `ExtFuncCode 0x02004665` denotes~~ (**RESOLVED 2026-09-10, §2: the panel's device-function
   identity** — same on all three panel images, absent on the G12, matched device↔descriptor↔
   payload; only its internal bit-decomposition is underivable from a single-panel corpus), and
   flags bit `0x10`.
4. ~~The `G12` section's flash destination — its descriptor carries no tag `0x40`.~~
   **ANSWERED 2026-08-20 — it is not in the file by design, and that is not an omission.**
   A network device advertises its own `LdAddr` over RV-C during enumeration; the loader has
   the matching error string `Missing External Load Address` for when one does not. On the
   coach (`BUS-BASELINE.md` PC.19) the G12 at source address `0x9C` reports **`LdAddr =
   0x08038000`** for `UploadType = 2` (config) — which is what the MCF's `G12` section is, and
   its `0x25A8` bytes land at `0x08038000..0x0803A5A8` inside a 256 KB flash. The same
   enumeration gives the rest of the device's map and identity: application at `0x08006000`,
   a second image (`DeviceInst 1`) at `0x08000000`, `BtldrVer 0x203`, and `McuDevID
   0x04351001` / 256 KB flash / 64 KB RAM — DEV_ID `0x435`, an **STM32L4**. Compare
   `docs/G12-CODE.md`, whose "Cortex-M0-class core" reading was corrected from this: the
   table-`0x67` blob really is Thumb-1-only, but that is a build choice, not the silicon.
4a. **`GUIConfig*.bin`'s payload format, and what consumes it.** The loader's second file path
    (§4.3) is decoded down to its 64-byte header (magic `0xC04F16F7`, version, description) and
    its destination (the QSPI's last erase block). Everything past the header is `unknown`, no
    such file exists in the corpus or on the coach's stick, and the magic appears in none of the
    four flashed images — so the consumer is unidentified. Related and equally open: **what is
    in that top QSPI sector on the coach**, which no part of this project has ever read.

**GuiData**

5. Where GuiData is parsed. `cli.bin` is a transport only. `LCD Firmware` at `0x00210000`
   is a tractable Ghidra target. **Corrected 2026-08-15:** this item used to close *"`GuiCode`
   is not ARM code and needs its own runtime understood"* — it **is** ARM code (native
   Thumb-2, no interpreter, `GUICODE.md` Finding 1), it has been disassembled for 13.6 and
   14.7, and reading it is what closed §8.3 row 1 (Finding 23). GuiCode is now the *first*
   place to look, not a blocked one: `GUICODE.md` Findings 9–12 already attribute the widget
   renderer (`FUN_00256C54`), the record decoder (`FUN_00252CB0`), the layout engine
   (`FUN_00257BFC`) and the `+0x46148` object-table walk (`FUN_00258650`/`FUN_00258330`)
   there. ~~What stays open is narrower: the **type-6 value-match switch's candidate-child
   list** — the one record structure `records.py` still does not expand, and the reason a
   `child_index` walk from root 0 reaches the CLIMATE *tab* but not the CLIMATE *body*.~~
   **RESOLVED 2026-09-10.** A type-6/0x2E switch chooses among a *list* of candidates, not one
   child: it scans `[child_index, child_index+count)` in the object table and renders the
   candidate whose match value — read from the table parallel to the object table
   (`find_match_table`, `0x4562C` on 13.6) — equals the slot it reads (`GUICODE.md` Findings
   15/16). `records.py` decodes `child_index`/`count` from the record but cannot carry the list
   itself, because the candidates live in the object table and their selectors in a parallel
   table; `guidata.switch_candidates(data, rec)` now expands it to `[(match_value, object)]`,
   and `guidata.reachable_objects(data, root)` follows every candidate. That is what closes the
   CLIMATE-body residual: object 7 (CLIMATE) is candidate value 3 of the top screen switch, not
   its base, so a base-only walk descends into the default screen and never into CLIMATE, while
   a candidate-following walk reaches object 7 and its whole 56-object subtree (13.6: 37 objects
   reachable base-only, 789 following candidates). `layout.screen_object_map` is now this
   expander minus the `0xFFFF` no-match default. Pinned by
   `tests/test_guidata.py::test_switch_candidates_expands_the_screen_switch` (cross-build) and
   `::test_following_switch_candidates_reaches_the_climate_body`. *(This clause previously named
   `GuiData+0x44BC8`'s pointer fix-up; that was wrong — §5.5 **withdrew** the "fixed up at load
   time" inference, and §5.4b states plainly that nothing fixes those pointers up. Corrected
   here rather than left to be copied forward again.)*
6. ~~The real screen index — a 233-entry table exists but chains to the binding table, not
   to display-list offsets.~~ **RESOLVED 2026-08-23, recorded here 2026-09-10.** The 233-entry
   table was the **group table** (GuiData `+0x30`), correctly ruled out — it chains to the
   binding table. The real screen index is **not a flat table at all**, which is why flat-table
   searches kept failing: it is the **top-level wtype-6 dispatch switch** keyed on the
   current-screen slot `state+0x20A`. Its match values are the screen ids; each child is that
   screen's root object in the `+0x46148` object table (the display-list root, `GUICODE.md`
   Finding 12). `layout.screen_object_map` decodes it to `{screen id: object index}` — 46 screens
   on 13.6, 42 on 11.23, 37 on 14.7 — located structurally (build-invariant), coach-validated
   (SERVICE MODE = screen 17), and pinned by `tests/test_aes_layout_nav.py`. This item was open
   only because §6.1's "presumed to exist and not found" (2026-08-12) predated the discovery
   (2026-08-23) and was never updated.
7. ~~The rest of the opcode table (`TAILS`), and what the record flag bits above `0x100F`
   mean.~~ **RESOLVED 2026-09-10 — the question was of the superseded model.** There is no
   opcode-indexed tail table in the real format: a record is an 8-byte header plus a body chosen
   field by field from the widget type (`byte0 & 0x3F`) and the header bits, and `records.py`
   decodes it by replaying `FUN_00252CB0`. The "flag bits above `0x100F`" were the heuristic's
   coarse view of header bytes 1-7; every meaningful bit of those bytes is now tested against a
   cited decoder instruction. The proof of completeness is the object-table boundary oracle,
   which tiles every record to its object boundary on **all three builds** (1200 / 1421 / 1076
   objects, `tests/test_records.py::test_every_object_decodes_to_its_boundary`). `guidata.py`'s
   `TAILS` dict survives only as `iter_screens`' title-scan heuristic — deliberately partial, and
   not the byte-exact decoder (§6). The remaining residual is narrower and on field *meaning*, not
   grammar: a handful of decoded fields are consumed for alignment without their value surfaced
   (documented inline in `records.py`).
8. ~~The OPTIONS row id (`0x6B..0x70`)~~ — **RESOLVED 2026-09-10: it is the `value_slot` of each
   OPTIONS row's wtype-6 switch** (§6.1). The six rows on the Main Options screen carry
   `value_slot` `0x6b..0x70` in display order, all sharing `child_index = 0x18a` / `count = 3`
   with match values `{0, 1, 2}`; the switch reads its slot's SRAM config cell (via `FUN_00251E38`,
   `records.py` R40-R42) to pick one of three shared appearance children. Not an opaque id — a
   value slot, which `records.py` decodes generally; the 2026-08-12 "unknown" note predated that
   decode. **The discriminator — RESOLVED 2026-09-10, and the earlier note looked in the wrong
   image.** The `0x0C`-record closing u16 has two uses — the **tap action id** on OPTIONS rows
   (answered 2026-08-15, `GUICODE.md` Finding 23) and a **group index on master/popup controls** —
   both carried in the wtype-12 record's `+0x94` field, which `FUN_00256b84` writes into the
   hit-region table at `state+0x7084` and the hit test returns as the pressed **touch id**. The
   discriminator is **`id < group_count`** (233 on 13.6, `find_control_groups`' threshold), and it
   is a real, cited instruction — but it is in the **LCD Firmware**, not in GuiCode's
   `FUN_00247bf8`. The LCD control scanner bounds its loop with the group count at
   `0x0021BAD4`-`0x0021BADA` (`ldrb.w r3,[r8]` = count / `cmp r3,r4` = id / `bhi`; the count is a
   *byte*, so 233 = `0xE9` fits), so an id **below** the count reaches the phase machine as a
   binding group (`docs/CONTROL-BEHAVIOR.md` §1, `gestures.is_group_id`); an id **at or above** it
   falls out and is handled by GuiCode's action dispatcher. This makes candidate mechanism (A) —
   a bounds check that makes 233 the real threshold — correct, and refutes (B): GuiCode's
   dispatcher (`FUN_00247bf8` at `0x0024ACB2`) is a compiler-generated **exact-value** binary
   search whose 22 tap arms all sit in `[0x3E8, 0x1C90]`, none `≤ 232`, and its no-match default
   at `0x0024B29E` is a per-tick UI mirror, not a group-command fallthrough — so a small group
   index reaching GuiCode does nothing there. The two classes are kept apart by a **disjoint
   value-range partition** authored into the ids (group band `0..232`, action band `≥ 0x3E8`),
   read by two different processors; there is no record-level flag, and none was ever needed.
   Why the earlier note stalled: it assumed the classifier lived in `FUN_00247bf8` (which Ghidra
   12.1.3 cannot decompile) and did not cross-reference `CONTROL-BEHAVIOR.md` §1, where the LCD
   bound was already located. A disassembly of all six of `FUN_00247bf8`'s `state+0x208` readers
   confirms the negative — none compares the id to 233 or indexes the group table — because the
   split happens upstream in the other image. Pinned by
   `test_the_group_action_discriminator_is_the_lcd_scanner_bound`.
9. ~~Binding entry byte 2 (`0x01`/`0x11`/`0x21`)~~ — **RESOLVED 2026-08-19**
   (`docs/CONTROL-BEHAVIOR.md` §5), and the answer is why every search of the *file* failed:
   the variant is chosen by a **runtime counter**, not by a stored byte. The LCD Firmware
   matches the entry's byte-2 high nibble against a per-control counter at SRAM
   `0x20006E6C + index*16 + 0x0A` (`cmp sl,r2,lsr #4` @`0x0021B946`), wrapped at the group
   entry's byte-0 low nibble. Two things move it: **pressing the control**, when group byte 1
   bits 6-7 allow (`0x0021BC66`-`0x0021BC7C`; 137 of 233 groups on 13.6), and **external
   setters** at `0x0021B60A` / `0x0021B694` for the rest. A second, **global** counter at
   `0x20006E5C + 0x0E` is advanced by any group whose byte 3 has bit 7 set (184 of 233 groups)
   and selects entries in the sender's second loop.

   **This strengthens rather than weakens the standing caveat on channel 35**: its
   per-variant membership is not merely unsettled by the file, it is *unsettleable* from the
   file, because it depends on a counter's live value. `answer_key`'s CAN capture remains the
   direct evidence, exactly as `guidata.py` said for a less specific reason.
10. ~~The option → selector join~~ — **RESOLVED 2026-08-15** (`GUICODE.md` Finding 23):
    the selector is a literal byte in a static CAN-frame template in GuiCode, joined to the
    option's config-buffer offset by code adjacency in a per-row tap handler, and reached
    from GuiData by the row's action-id u16 (question 8). `config.py` extracts it and
    `guidata.find_option_bindings` returns name + selector; two of the strict `xfail`s
    closed. **The `E3`/`E4` channel binding and the `E5`/`EC` visibility flag closed
    2026-09-02** (`GUICODE.md` Finding 61, §8.3 rows 2-3): both are type-6 switches keyed on
    the option's own value slot, and `find_option_bindings` now fills `channel` and
    `hides_control` from them — no xfails remain in that file. The per-floorplan option
    defaults were recovered 2026-08-18 (Finding 42). Note the defaults are open in a
    different sense: `GUICODE.md` Finding 19 shows the state is delivered over CAN and is
    **not in the file at all**, so there is nothing there to find.

    *One lead, recorded as a lead and not as a finding.* Group 93 is byte-identical in
    all three builds and holds two byte-2 variants that swap channels 25 and 26, each
    beginning with an explicit Off (command 3) to the channel it gives up — the shape
    PC.8 observed when the `E3`/`E4` option is switched. It was found by searching for
    command-3 entries on the channels the answer key already names, which is the failure
    mode this project keeps catching, and nothing independent ties group 93 to that
    option: no control record referencing it was located, and the same group is
    byte-identical in the Ethos build. Finding what references group 93, and whether
    byte 2 tracks option state, would settle both this and question 9.

    *Update, 2026-08-15.* The `E3`/`E4` half of that lead now has a **better-founded
    candidate that does not involve group 93 at all**: the tap handler itself emits a
    `0x1FEDB` command-3 OFF to the abandoned channel, and the instance byte of that template
    gives `E3` → 25 and `E4` → 26 (`GUICODE.md` Finding 23). Group 93 remains a lead for
    question 9 (what selects a binding-table variant), which is a different question.

    A second lead, weaker, is in §8.3: group-`0x94` entries carry `0xE3`/`0xE4`/`0xE5` in
    the byte PC.7 calls "function". Reading that byte as a selector would assume a role
    this document leaves `unknown`, and the same byte more often holds `0xE0`.
11. Whether the two-letter floorplan codes expand to full names anywhere in the MCF. Not
    found — and **still not found, 2026-08-18**. §7.1 now records what seven of the codes
    mean (`WD` = Entegra Ethos / Jayco Swift 20DL, and so on), but that is `owner-supplied`
    product knowledge, **not** a decode: nothing in any section carries it. Do not read §7.1
    as closing this item; if anything it sharpens it, since the expansions demonstrably exist
    somewhere and are demonstrably not here.
11a. **The control → status-variable link. Narrowed to one question, 2026-08-16.**
    *Read §5.4c first — most of what this item once asked has been answered.* The chain
    from a **pin to a lit widget** is now proven end to end and editable at four joints;
    what remains open is only the **pairing inside a single control**: nothing in the file
    says that *this* button's command binding and *that* status variable belong together.
    A control's on/off indicator and the `G12 OUTPUTS` diagnostics row read the same
    variable-table entry (`BUS-BASELINE.md` PC.13–15, confirmed both ways on the coach), so
    a remap still has to choose between them (`move_display_list`). The six searches below
    were run against the wrong target — a stored *variable index* beside a control — and
    correctly came back negative; they remain valid as negatives:

    * **No pointers into the variable table exist anywhere in GuiData.** Twenty-one u32
      words fall in its address range; only two are both entry-aligned and word-aligned,
      and both are coincidences — one sits inside glyph bitmap data, the other straddles
      two `u16` coordinate fields of an opcode-`0x0C` record (`00 2d 00 90` = x 45, y 144).
      *This retracts an earlier claim in this project that variable 82 had four pointers.*
    * **The index is not stored near any control record.** Five encodings (index as u8 and
      u16, section offset, offset/8, table-relative offset) tested in a ±0x100 window
      around every single-channel control: only scattered single-byte coincidences.
    * **Not in the group table.** Entries are 8 bytes and hold `start`/`count` only.
    * **Not in the button's own object**, and byte `0x52` (=82) does not occur in the
      Climate screen's region at all.
    * **GuiCode contains zero dimmer-DGN immediates**, so the RV-C receive path is not
      there — consistent with `GUICODE.md` Finding 7, where GuiCode transmits by calling
      an imported LCD Firmware routine.
    * The LCD Firmware's 8-byte-strided byte loads at `FUN_0021B5E8` / `FUN_0021B6FC`
      index a 16-entry bitfielded table (`& 0xC0`, `& 0x0F`, 16-byte sibling records), not
      the 298-entry variable table.

    **What is established:** the variable index is a pure function of the RV-C instance —
    `18 + (n-1)` for instances 1..23 and `66 + (n-24)` for 24..44 — and is identical in
    13.6 and 14.7. So a cross-build differential cannot discriminate, because the number
    never changes.

    **The next step named here in 2026-08-14 — "enumerate the readers of the variable
    table's base" — was taken and is done.** `FUN_0021BE50` establishes only slot ≡ variable
    identity and never walks the object table; the reader is `FUN_00247BF8` in GuiCode,
    which receives the slot base as a call argument (`GUICODE.md` Findings 32 and 33). That
    closed the *widget → status* half.

    **RESOLVED 2026-08-26 — settled negative, with the pairing rule recovered.** The dig ran
    the one remaining candidate to ground: is the pairing encoded in the **indicator widget**
    itself (widget's `fillA` mode-7 / `value_slot` → `+0x44BC8` cell → the var whose GuiCode
    block writes it, §5.4c)? **No.** Inverting that chain over the ten known controls
    reproduces only **4/10**, and the decisive case is object **295**: it commands channel 44
    (var 86), yet var 86's status cell is drawn by **no widget at all** — so its pairing
    *cannot* come from an indicator, because there is none. The command and the indicator are
    **independently anchored to the same physical G12 pin**; nothing in the file cross-links
    them (confirmed by the six negatives above *and* now by this widget-intrinsic negative).
    The **only** exact, build-invariant rule is the shared-instance law: a control commands
    channel `n`, that pin broadcasts `DC_DIMMER_STATUS_3` instance `n`, and the paired status
    variable is `18+(n-1)` (n≤23) / `66+(n-24)` (n≥24). `statusjoin.predicted_status_slot`
    derives the var from the control's **own command channel** via that law and reproduces
    **every** known pairing on 13.6 (10) *and* 14.7 (11); the strict `xfail` is lifted.
    *Practical consequence:* a command remap's indicator target is now computable, so
    `move_display_list` could be made automatic (point the widget at the cell for
    `instance_status_var(new_channel)`) — the two remain distinct fields in the file, but the
    join between them is no longer unknown, it is the instance.
    **Automated 2026-09-10.** `set_channel`'s default now retargets the control's own
    indicator records at the new pin's colour and bool cells (`McfPatcher._retarget_indicator`)
    and moves no status variable, so every G12 OUTPUTS row stays honest; `move_display_list`
    keeps PC.14's v9. It refuses the four 13.6 controls whose indicator names none of its own
    pin's cells. Tooling-verified, hardware-unverified.
12. The interiors of the `LCD Firmware`, `GuiCode` and `G12` sections (the standing
    disassembly frontier). **The `0x8F7E6D5C` sub-container is now mapped (2026-09-10, §4.2,
    §5.5, `src/fireflymcf/subcontainer.py`):** both known instances — `cli.bin` region 2 and
    `GuiData +0x10000` — share a fixed 16-byte header (magic, build word cross-checked against
    the parent section, two reserved words), then a resource-specific payload. In the loader
    that payload is two pointer tables — the 131-entry ParamID→name table (§2) and a 138-entry
    table of the loader's own GUI screen records — each over its own string/record pool; in
    GuiData it is the mode-6 colour palette (§5.5). **The loader's GUI record grammar is decoded
    (2026-09-10, §4.2): it is the GuiData widget-record format itself**, and the one decoder
    difference is classified — cli.bin's `FUN_20009cd8` is `FUN_00252CB0` field for field except
    at the **R35** field, which it reads whenever header byte-5 bit `0x80` is set, where the panel
    also gates that on `(uV16 & 0x2901) || (aee & 0x40)` (found by disassembling both; a real
    decoder difference, not a `records.py` gap). `records.decode_record(loader_r35=True)` applies
    it, and every 13.6/14.7 loader screen then decodes (`subcontainer.loader_gui_screens`).
    **What remains open here** is narrow: two 11.23 screens (the AUTO LOAD / MANUAL LOAD
    CONFIGURATION pages) that use a *further* field-presence path this decoder does not yet model;
    the run of u16s after GuiData's palette (§5.5); and the broad LCD/GuiCode/G12 section interiors,
    now mapped by `docs/SECTION-INTERIOR-SURVEY.md` — GuiCode is 81% documented (dark = specific GUI
    internals like the render primitive `FUN_00254f26`), the LCD is mostly library with a few dark
    orchestrators (e.g. the apparent status-update loop `FUN_0021317c`), and the G12's frontier is
    its rule table (`docs/G12-CODE.md`), not a function map.
13. The ~59 KB of trailing-object headroom per file (§9) — the last object of each of
    the three non-uniform tables (the image table and the two big object tables) is bounded
    only by a max-sibling size heuristic, because `guidata.parse_screen` cannot decode those
    objects to pin an end. This stays open. ~~and the zero-margin bound on the 29/30-entry
    table, which will raise on a build whose last object there is larger~~ —
    **RESOLVED 2026-09-10.** That table is not heuristically bounded at all: it is a
    **uniform 20-byte-stride table** (28 records in 14.7, 29 in 11.23/13.6, every one exactly
    20 bytes), so its trailing object is one more record of the same stride and `coverage.py`
    now asserts `trailing == stride` exactly. The zero margin was never a knife edge — it is
    correct by construction, and a trailing span that is not one stride is an unpointed record
    or an absorbed structure, which is exactly when the gate should fire
    (`tests/test_coverage.py::test_the_uniform_stride_table_is_uniform_20_byte_stride`).

**Answered elsewhere, recorded here so they are not re-asked**

* Floorplan and option *state* live in G12 non-volatile storage and reach the display over
  RV-C. The MCF is a schema, not an instance (PC.5, PC.8).
* An option gates a load two ways — rebinding and visibility (PC.8, PC.9) — but neither
  structure has been located in the file (§8.3).
* The `Spyder Controls 7in Lite LCD` string at `0x6F8` sits inside the `LCD Firmware`
  payload (which begins at `0x2B8`), not in the directory. The section table really does
  have four entries. (The design spec wrote the version as `1v6x`; the bytes read
  `1v6r` in 13.6 and `1v6t` in 11.23 and 14.7 — `x` was a wildcard, not a value.)
