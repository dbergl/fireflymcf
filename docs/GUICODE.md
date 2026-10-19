# GuiCode — the renderer, disassembled

**Image:** `Lyra_*_GuiCode` section of `mcf.bin`, 105,400 bytes in 13.6, load address
`0x00240000` (from the blob descriptor table at `0x0021C010`; see `LCD-FIRMWARE.md`
Finding 1).

**GuiCode is native ARM Thumb-2 code, not bytecode.** That was established in
`LCD-FIRMWARE.md` Finding 10 and is confirmed here by disassembly: **154 functions and
88.9% of the blob's bytes become instructions** when the five header entry points are
marked and Thumb mode is forced. There is no interpreter anywhere in the MCF because
none is needed.

**Reproduce:**

```
python3 -m fireflymcf.cli extract firefly-13.6/mcf.bin extractions/13.6
cp extractions/13.6/01-Lyra_13v6_GuiCode.bin extractions/13.6/GuiCode.bin
GH=~/Downloads/ghidra_12.0.4_PUBLIC/support/analyzeHeadless
$GH ghidra/projects guicode136 -import extractions/13.6/GuiCode.bin \
  -processor ARM:LE:32:Cortex -loader BinaryLoader -loader-baseAddr 0x00240000 \
  -noanalysis -scriptPath ghidra -postScript analyze_guicode.py -overwrite
$GH ghidra/projects guicode136 -process GuiCode.bin -noanalysis \
  -scriptPath ghidra -postScript decompile_guicode.py
```

*Ghidra 12.1.3 dropped Jython: run `.py` scripts through the `ghidra/headless` wrapper, not
`support/analyzeHeadless`; the paths above are as recorded in 2026-08 (`CLAUDE.md` Commands).*

Output: `extractions/guicode-analysis.txt` and `extractions/guicode-decomp.txt`.

`-noanalysis` on the import is **required**. Without it, import-time auto-analysis
disassembles the blob as ARM, and the later `setValue(TMode, …)` throws
`ContextChangeException: Context register change conflicts with one or more
instructions`. `analyze_guicode.py` also calls `listing.clearCodeUnits` first for the
same reason. The blob maps 1:1 at `0x00240000` with **no header stripping** — its own
`0x30`-byte header is part of the mapped image, which the vector arithmetic proves (file
offset `0x7A90` → pointer `0x00247A91`).

## Why this matters

GuiData's field semantics were stuck at `inferred-from-data` because no code that reads
them had been found. The LCD Firmware turned out to read only GuiData's *header*
(`LCD-FIRMWARE.md` Findings 8–9). **The code that reads GuiData's records is here**, and
it addresses GuiData through ordinary literal-pool pointers, because `0x90000000` is
QUADSPI memory-mapped mode on this STM32F7.

## Finding 1 — GuiCode holds literal pointers to three of the four object tables

`proven-from-code`, and it upgrades `MCF-FORMAT.md` §5.5.

§5.5 located four object tables per file by a data-only heuristic — "a maximal run of
u32s `0x900XXXXX` whose targets land in the section". GuiCode's literal pools name three
of their base addresses outright:

| GuiData offset | Entries (13.6) | Referenced from | What the code does with it |
|---|---|---|---|
| `+0x10080` | 8 | `FUN_00255F80`, `FUN_00255FC4` | image table (Finding 2) |
| `+0x46148` | 1422 | — not referenced by literal | still `inferred-from-data` |
| `+0x74D80` | 30 | `FUN_002520F4` | **font descriptor table** (Finding 3) |
| `+0x7503C` | 3295 | `FUN_00251AA0` | **glyph table** (Finding 3) |

Each bound in the code matches the table's entry count exactly — `cmp #8`, `cmp #0x1e`
(30) — which is independent confirmation of §5.5's sizes from a different source.

**A caveat on the reference scan:** `analyze_guicode.py` reports any code reference to a
word in `0x90000000`–`0x9FFFFFFF`, and Thumb-2 branch encodings collide with that range —
`beq.w`/`cbnz`/`b` targets land on instruction bytes that read as `0x9Axxxxxx` or
`0x9Bxxxxxx`. **Only `ldr` hits are literal-pool loads.** Roughly half the raw output is
this artifact; the tables above are `ldr`-only.

## Finding 2 — the image table is three parallel arrays

`proven-from-code` for the structure; the width/height reading is `inferred`.

`FUN_00255F80`, 54 bytes, in full:

```c
if (7 < param_1) return;                             // bound 8 = the table's entry count
FUN_002513a4(*(undefined4 *)(DAT_00255fc0 + param_1 * 4),   // 0x90010080 + i*4  -> object ptr
             param_2, param_3,                              // x, y
             *(undefined2 *)(DAT_00255fb8 + param_1 * 2),    // 0x90010070 + i*2  -> u16
             *(undefined2 *)(DAT_00255fbc + param_1 * 2),    // 0x90010060 + i*2  -> u16
             0, 0, 0, 0, param_4, 0);
```

So GuiData carries **three parallel 8-element arrays** for the same index: object
pointers at `+0x10080` (stride 4), and two `u16` arrays at `+0x10070` and `+0x10060`
(stride 2). Passed to a draw call as `(object, x, y, a, b, …)`, `a` and `b` are almost
certainly **width and height** — `inferred`, but the shape is hard to read any other way.
13.6's values, in index order:

```
+0x10070:  125  150  208  180  139  180  139  150
+0x10060:  125  150   61  180  139  180  139  150
```

Only index 2 differs between the arrays (208 × 61), which is what a non-square asset
looks like. `FUN_00255FC4` is the same call with four extra parameters and a trailing `1`
rather than `0` — a variant blit.

## Finding 3 — the font and glyph mechanism, complete

`proven-from-code`. This is the strongest result in this pass, because the structure it
implies accounts for the glyph table exactly, with nothing left over.

`FUN_002520F4` selects the current font — bound 30, entry 0 as the fallback, stored into
a GUI state struct at `+0x98`:

```c
if (param_1 < 0x1e) { *(u32 *)(state + 0x98) = ((u32 *)0x90074D80)[param_1]; return; }
*(u32 *)(state + 0x98) = *(u32 *)0x90074D80;
```

`FUN_00251AA0` then renders a string, reading that same `+0x98` slot back, one byte at a
time (length capped at `0x400` = 1024):

```c
font = *(byte **)(state + 0x98);
if (c < font[0] || font[1] < c)                          // outside [first, last]
    glyph[i] = GLYPH_TABLE[*(u16 *)(font + 4)];          // fallback glyph
else
    glyph[i] = GLYPH_TABLE[*(u16 *)(font + 2) + c - font[0]];
```

with `GLYPH_TABLE` = `0x9007503C`. That gives a **20-byte font descriptor**:

```
font+0x00  u8   first character code
font+0x01  u8   last character code
font+0x02  u16  base index into the glyph table
font+0x04  u16  fallback glyph index, used for any character outside [first, last]
font+0x06..0x13  unknown (14 bytes)
```

**Why this is more than a plausible read.** Dereferencing all 30 descriptors, every one
is internally consistent, and their `base` indices **partition the 3295-entry glyph table
contiguously with no gap and no overlap**:

| Slot | first–last | base | span | fallback | `base + span` |
|---|---|---|---|---|---|
| 0 | `0x20`–`0x7A` | 0 | 91 | 91 | 91 |
| 2 | `0x20`–`0x73` | 92 | 84 | 176 | 176 |
| 3 | `0x20`–`0x68` | 177 | 73 | 250 | 250 |
| … | | | | | |
| 29 | `0x30`–`0x47` | 3270 | 24 | 3294 | 3294 |

The fallback index is always exactly `base + span` — the slot immediately past the
font's own range — and the last font's fallback is **3294**, the final index of a
3295-entry table. The table is fully consumed.

The descriptors themselves sit immediately below the glyph table, descending in 20-byte
steps from `0x90075028` to `0x90074DF8`; `0x90075028 + 0x14 = 0x9007503C`, the glyph
table's base. That span is `0x244` = 580 bytes = **29** descriptors, not 30 — because
slots 0 and 1 both point at `0x90075028`. One descriptor is shared by two slots, and the
arithmetic closes.

Character ranges are legible: most fonts start at `0x20` (space); slot 22 is `'0'`–`'5'`,
slot 23 is `'K'`–`'N'` (a units glyph set), slot 29 is `'0'`–`'G'`. Slots 11–18 run
`0x20`–`0xFF` with a 224-glyph span, i.e. full Latin-1 text fonts.

`FUN_00251AA0` also classifies each character against a per-something mode byte at
`state + param_3*0xc + 10`: mode `2` marks every character, mode `3` marks only those
outside `'0'`–`'9'`, space, `'+'`, `'-'`, `'*'`, `'/'`, `'='`. A numeric-vs-text
distinction — `inferred`, plausibly digit-cell alignment for readouts.

### Finding 3b — the glyph *bitmap* encoding

`inferred-from-data`, 2026-08-14. Finding 3 above decoded the **lookup** and stopped
there; the bitmaps themselves stayed opaque. They are 4-bit-per-pixel coverage maps:

```
header[10]  u8   x offset
header[11]  u8   width in pixels        <- stride comes from this, not from any other field
header[12]  u8   y offset
header[13]  u8   height-ish; NOT the row count for every glyph
bitmap starts 19 or 20 bytes in, VARYING PER GLYPH
  4 bpp, low nibble first, each row padded to a whole byte
  stride = ceil(width / 2)
  rows   = (byte extent - start) // stride
```

Implemented in `src/fireflymcf/glyphs.py`, pinned by `tests/test_glyphs.py`.

**Why this is more than a plausible read.** A mirror-symmetric glyph decodes to *exact*
symmetry only when both the start and the width are right. 13.6 slot 24 `'>'` is a filled
ellipse and reaches left/right asymmetry **0.0** at `(start=20, width=48)` — and stops
being 0 if either is perturbed. The fan at slot 24 `'E'` independently renders as the icon
the panel is photographed drawing on the Climate button.

**Two things stay `unknown`, and both cost real time before being pinned down:**

- **Why the start is 19 for some glyphs and 20 for others**, in the same font. No header
  field identified so far predicts it. The decoder picks whichever leaves the border
  emptier — a heuristic, and labelled as one.
- **`header[13]`.** It matches the row count for most glyphs but not all: slot 24 `'>'`
  declares 48 and holds 37 rows. The row count is derived from the byte extent instead.

Three retractions are recorded because each was briefly believed: the header is not 14
bytes; roughly half the glyphs are *not* run-length compressed (that was a wrong stride);
and rendering `stride*2` columns puts a stray padding column on every odd-width glyph,
which also moves the mirror axis half a pixel and makes the symmetry test unusable.

**Byte extents are bounded by the next glyph in address order, not index order.** Glyphs
are not stored in index sequence, and several indices share a single small record — the
placeholder that an out-of-range character resolves to. Eight of slot 24's 33 characters
share one. This is directly observable on hardware: setting a button's icon character to
`'['` drew a filled 48×48 block.

**Fonts differ between builds while descriptor geometry does not.** 13.6 and 14.7 have
identical character ranges for slots 19–26, but *different pictures* in them: 14.7 draws a
satellite dish at slot 24 `'['` where 13.6 has the placeholder block. So a glyph cannot be
borrowed across builds without moving bitmap data of a different size, which the patcher's
same-size rule forbids.

Slot 19 (`0x30..0x47`, 24 glyphs) holds the **floorplan line drawings** — the diagrams on
the floorplan selection screen are font glyphs, which is why earlier passes never located
them in an image table.

## Finding 4 — `GuiCode_entry0` is the screen/clip setup, and the panel is 800×480

`proven-from-code`. The first vector slot, the one `FUN_0021BE50` calls with a stack
struct:

```c
FUN_00258cf0(0x10, 0x10, ...);        //   16,  16
FUN_00258cf0(0x30f, 0x1cf, ...);      //  783, 463
```

`0x30F` = 783 = 800 − 16 − 1 and `0x1CF` = 463 = 480 − 16 − 1, so this sets a clip
rectangle inset 16 px on every side of the 800×480 panel — independently corroborating
the LTDC timings in `LCD-FIRMWARE.md` Finding 7 from a different blob. When
`param_1+0x18` or `+0x1A` reads `-1` the insets are used as-is; otherwise the caller's
values override. It also copies five words from `param_1+0x04`…`+0x14` into globals.

**Those five words are the host API table** (added 2026-08-15, Finding 23). `param_1` is a
five-function vector the host hands GuiCode at entry; the copies land in five SRAM slots, and
two of them are the ones the config path uses — `+0x08` → `0x2003986C` (the **CAN frame
sender**, `str` @`0x00247AA2`) and `+0x10` → `0x20039874` (the **config-changed notify**,
`str` @`0x00247AAE`), both in 13.6. So `GuiCode_entry0` is the screen/clip setup *and* the
host-API installer; that is an addition to this finding, not a contradiction of it.

## Finding 5 — a 665-entry lookup keyed by the `0xFFFF` sentinel

`proven-from-code` for the bound and the sentinel; the table's meaning is `unknown`.

`FUN_00256868` reads `0x90044BC8` as a pointer array:

```c
if (param_2 == 0xffff) return 0;
if (0x298 < param_2) return 0;              // 0x298 = 664, so 665 valid indices
piVar1 = *(int **)(DAT_0025689c + param_2 * 4);
if (piVar1 == NULL) return 0;
iVar2 = *piVar1;
if (*param_1 != iVar2) { *param_1 = iVar2; return 1; }   // returns 1 only on change
return 0;
```

`0xFFFF` is the same "no value" sentinel `FUN_0021BE50` writes across the GUI state
struct's u16 cache at `+0x10C`/`+0x144` (`LCD-FIRMWARE.md` Finding 11), so this is a
change-detect read on a per-variable current value. `GuiData+0x44BC8` is **not** one of
§5.5's four object tables — its entries point into SRAM (`0x2003A7D8`, `0x2003A1FC`, …),
not into GuiData.

> **Correction, 2026-08-15 (review fix wave 2).** This paragraph used to close *"so this is a
> table of pointers **fixed up at load time**, which is a mechanism the format spec does not
> currently describe"*. That inference is **withdrawn**, and was withdrawn in the format spec
> first: `MCF-FORMAT.md` §5.5's own correction block, and §5.4b — *"the RAM layout is fixed, so
> the addresses are correct as shipped. **Nothing fixes them up at load time.**"* The entries
> are **static SRAM addresses needing no relocation**; the array is stored complete and read
> as-is. What happens at boot is that the **cells they address** are initialised — for the
> type-10 (colour) entries, by GuiCode's theme-init writing hardcoded RGB888 constants
> (Finding 18, RESOLVED). Do not carry "fixed up at load time" forward from here: it has
> already been copied into two other documents and had to be reverted from both.

## Finding 6 — the per-floorplan defaults are NOT in GuiCode. A defensible negative.

> **SUPERSEDED on its headline, 2026-08-18 — see Finding 42.** The defaults *are* in GuiCode,
> as **immediates in eleven tap handlers**, not as a table. Every search recorded below was for a
> stored table and every one of them remains correct; what was too narrow was the question.

The sweep asked for. It comes back **negative**, and the negative is worth more than a
fitted answer would be, so what was searched is recorded in full.

### What the capture establishes first

Before sweeping code, the mechanism was pinned from `captures/pc5-floorplans-2026-08-12.log`.
Selecting a floorplan produces a burst on **DGN `0x1FED9`** from source **`0x9F`** — the
Spyder panel — with the layout

```
19FED99F#FF96<sel>0F<val>00D1EA        e.g. FF96F50F0500D1EA = selector F5 := 5 (TB)
```

Source `0x9C` is the **G12**: it is the only transmitter of `0x15FCE` (3,397 frames), which
it broadcasts as periodic status. So `0x15FCE` is configuration *status* and `0x1FED9` is
configuration *write*.

Two things follow. First, the eleven bursts in that capture **independently reproduce
PC.12's whole table** — all ten floorplans, from the write DGN rather than from the G12's
status broadcasts and the owner's screen readings. `answer_key.FLOORPLAN_OPTION_DEFAULTS`
is confirmed by a third, mechanically different route.

Second, and decisive for the hunt: each burst writes twelve selectors in **12 ms with no
G12 round-trip between them**. The panel is not querying anyone. **The values are in the
panel's own flash** — which refutes the otherwise-reasonable hypothesis that the defaults
live in the G12 and the panel merely echoes them.

### What was searched in GuiCode, and found absent

| Search | Result |
|---|---|
| DGN `0x15FCE` or `0x5FCE`, any width | **0 occurrences in GuiCode** |
| `movs rN,#<selector>` for the twelve selector bytes | 24 hits, but **no window of 0x80 holds even 3 distinct selectors** |
| `movw rN,#<variable index>` for indices 282–297 | **0 occurrences** |
| u16 words equal to those indices, clustered | 34 occurrences, **no 0x40 window holds 4 distinct** |
| `tbb`/`tbh` table branches bounded at 10 cases | 2 found, **both explained as something else** |

The two ten-way switches are not floorplan dispatches. `0x00251E58`'s cases are `ldrb` /
`ldrsb` / `ldrh` / `ldrsh` / `ldr` / `vldr.32` — a **data-type dispatch** over eleven type
codes. `0x002567B2`'s cases store 1/2/4 into a u16 at `[r3,#0x18A]`, indexed by a widget
type byte at `[r3,#0x155]` — a **redraw-flag dispatch**.

That GuiCode holds no DGN and no selector bytes is itself a structural finding: **GuiCode
addresses variables by index, and the LCD Firmware supplies the DGN and selector from
GuiData's variable table.** It also means PC.12's `(selector, value)` search could never
have hit GuiCode even if the table were there.

### Extending PC.12's data search

PC.12 searched the `EF` sequence, the six option bits as one byte per floorplan in both bit
orders, 10-byte records, and `(selector, value)` pairs — all at strides 1–32, across all
three MCFs. Three gaps in that shape, all now closed:

| Gap | Search | Result |
|---|---|---|
| **Strides > 32** | `EF` sequence `1,1,1,0,3,1,3,3,3,1` at strides **1–512** | **0 hits in all three files** |
| **`(index, value)`** rather than `(selector, value)` | indices 283–288 contiguous as u16 and as u8; best 0x40 window over all twelve | absent; best window holds 5/12, which is chance for 2-byte needles in ~1 MB |
| **Derived encodings** smaller than the raw table | per-floorplan pattern index (only four distinct option patterns exist, giving `0,0,0,3,1,1,0,0,0,2`); the E6/D7 family flag `1,1,1,0,0,1,0,0,0,1`; that flag as a 10-bit mask both bit orders | noise only — the pattern index hits **once per file at unrelated offsets**, and the family flag hits once in 11.23 and **zero times in 13.6 and 14.7** |

A real encoding would appear in all three builds. None does.

### One candidate eliminated outright

The LCD Firmware was the remaining place an immediate-operand table could hide, since
neither PC.12's search nor anything above sweeps its *code* for immediates. It is
eliminated on different grounds: **11.23 and 14.7 ship a byte-identical LCD Firmware**
(md5 `5679a922…`, 62,924 bytes; 13.6 differs at 62,920) while carrying **different
floorplan sets** — 11.23 uses `3RT`/`4RT`/`3WD`/`4WD` naming with no selection screen,
14.7 has nine codes including `RE`. A shared binary cannot hold build-specific
per-floorplan defaults.

### Where that leaves it

`find_option_defaults` **stays unimplemented**, and its `xfail(strict)` stays. The defaults
are in the panel's flash (capture-proven), are build-specific (so in GuiData or GuiCode, not
the shared firmware), are not in GuiCode's code or literals, and are not in any MCF as a
strided table up to 512 in any packing tried. Two possibilities survive, and neither is
reachable by pattern search:

1. **A computed or non-constant-stride layout in GuiData** — e.g. per-floorplan values
   reached through the object tables rather than stored adjacently. Any constant-stride
   search is blind to this by construction.
2. **A separate non-volatile config region in the panel that the MCF does not contain.**
   The MCF is an update container; nothing establishes that it carries the panel's
   factory configuration. If this is the answer, the defaults are *not findable from this
   corpus at all*, and saying so is the correct outcome.

Distinguishing them needs the code path, not more searching: find what GuiCode calls when
F5 changes, and follow it to the values. The five-function import table into the LCD
Firmware (`FUN_0021B678`, `FUN_0021825C`, `FUN_0021BE10`, `FUN_0021BE2C`, `FUN_0021BDF8`)
is where a `0x1FED9` write must ultimately be issued, so the burst has to pass through one
of them.

### A retracted step, recorded because it was nearly convincing

An earlier attempt in this pass mapped selectors to SRAM addresses by reading
`GuiData+0x44BC8[k]` for variable-table row `k`, then found that **all 11 resulting
addresses are written by one function**, `FUN_00247BF8` — which looked like a hit. It is
not. `FUN_00256868` bounds that table's index at **664 (666 entries)** while the variable
table has **298 rows**, so the two index spaces cannot be the same and the mapping was
unjustified. Inspecting `FUN_00247BF8` confirms it: every store there takes its value from
an incoming message struct (`ldrb.w r2,[r4,#0x212]` and similar), not from a per-floorplan
constant, and one store is `movs r2,#0xd` into a supposed boolean. `FUN_00247BF8` is the
**CAN receive/unpack path** that populates GuiCode's globals from RV-C traffic. The
addresses are real GuiCode globals; their identity as the six options was never
established.

## Finding 7 — the CAN frame template pool, and how GuiCode transmits

`proven-from-code`, and corroborated frame-for-frame against the bus. This is the
mechanism by which GuiCode sends anything, found by hunting the write-frame constants
(`FF 96`, `D1 EA`) that Finding 6 showed were absent from the LCD Firmware.

GuiCode's data section holds a table of **141 records of 12 bytes** at
**`0x00259430`–`0x00259ACC`**:

```
00 D9 FE 19 | FF 96 9B 0F 01 00 D1 FF
^^^^^^^^^^^   ^^^^^^^^^^^^^^^^^^^^^^^
u32 CAN id    the 8-byte payload
little-endian
= 0x19FED900  (DGN 0x1FED9, source-address byte left zero)
```

Complete pre-built CAN frames. The DGNs represented:

| DGN | Records |
|---|---|
| `0x1FED9` | 118 |
| `0x1FEDB` | 16 |
| `0x1FBDA` | 3 |
| `0x0EAFF` | 2 |
| `0x1FACE`, `0x1FEF9` | 1 each |

The 118 `0x1FED9` records give **117 distinct `(selector, value)` write templates** in group
`0x96`. Three independent corroborations that this table is what reaches the wire:

* The payloads are byte-identical to the writes in `captures/pc5-floorplans-2026-08-12.log`.
* Record `FF 96 CC 0F 18 FC D2 EA` carries `0xFC18` = **−1000 as a signed 16-bit delta**,
  matching the `CC`/`CD`/`CE`-are-deltas note already in `BUS-BASELINE.md`.
* Every selector also has an `0xFFFF` variant (`FF 96 <sel> 0F FF FF D3 FF`) — the
  terminator frame observed following each written selector on the bus.
  **Correction, 2026-08-15 (Finding 23): "every" is too strong.** Counted directly in 13.6,
  the 118 `0x1FED9` group-`0x96` records span **46** distinct selectors: 37 have both forms,
  **6 are write-only** (`0x00`, `0x06`, `0x4F`, `0x9B`, `0xAB`, `0xB8`) and **3 are
  read-back-only** (`0x2E`, `0x2F`, `0x33`). The observation still holds for every *option*
  selector; it is not a property of the pool as a whole.

**The send primitive**, at `0x0024A14E`:

```
0024a14e  add   r3,sp,#0x4        ; a stack buffer
0024a150  ldr   r2,[0x0024a298]   ; = 0x00259430, a template record, by LITERAL pointer
0024a152  ldmia r2,{r0,r1,r2}     ; load all 12 bytes
0024a154  stm   r3,{r0,r1,r2}     ; copy into the buffer
0024a158  ldr   r2,[0x0024a29c]   ; a global holding an imported function pointer
0024a15a  ldr   r2,[r2,#0x0]      ;   (one of the five the LCD Firmware passes in)
0024a15e  blx   r2                ; transmit
```

So a send site names its template with a **hardcoded literal pointer** and copies the
record wholesale. There is no index arithmetic on the table base anywhere.

### Why this does NOT locate the defaults, stated explicitly

It is tempting to read the pool as the defaults store, since every value the transaction
writes is in it. That reading is **wrong, and the temptation is a trap worth naming**:

* Every boolean selector in the pool carries **both `0` and `1`** (`EF` carries 0–3, `F5`
  carries 0–10). So "each floorplan's twelve defaults can be built from the pool" is a
  **tautology** — it would hold for any assignment of those selectors whatsoever, including
  a fabricated one. It is not evidence and is not used as any here.
* Templates are reached only by literal pointer, and there are **~23 such literals against
  the 120 references** ten floorplans × twelve writes would need. The defaults are not
  emitted by a run of literal-pointer send sites.
* No stored index list exists. Searched in GuiCode and GuiData, as `u8` and `u16` arrays, in
  wire order taken from the capture, reversed, with the `F5` write dropped, and
  options-only: **no hit**. GuiData also holds **no pointers at all** into the pool region,
  so nothing is fixed up to point there at load time.
* Positive evidence that the selection is computed rather than stored: floorplans 7 and 8
  have **identical** default sets, yet their bursts go out in **different orders**
  (`…73,17,57…` versus `…73,57,17…`). A stored list would emit a fixed order.

The pool is simply the complete set of frames the UI can ever send — one per possible widget
action — which is what a data-driven UI needs and is a real finding in its own right. It is
just not the defaults table.

### The next step, now sharply posed

`FUN_00247BF8` (25,934 bytes, reached from `GuiCode_entry1`) contains both the send site
above and the CAN receive/unpack path from Finding 6's retraction. It is too large for the
decompiler, so it has to be read in ranges. The `F5` write templates for floorplans 0–10 sit
at records 102 and 105–113; find the site that sends one of those and the surrounding code
is the floorplan-change handler. That is where the ten option values are set, and it is the
last place in this corpus they can be.

## Finding 8 — a control-name table in GuiCode, read by dynamic screen rows

`proven-from-code` for its existence and use; `observed-on-hardware` for the consequence.
2026-08-14.

At file `0x028AF8 .. 0x028C86` (GuiCode-relative, addresses `0x00259278 .. 0x00259406`)
sit **24 NUL-terminated C strings**, each padded to a 4-byte boundary:

```
0x028AF8 'Tank Heat Fresh'   0x028BAC 'Bath Lts'          0x028C08 'Bench OHC Lts (GND)'
0x028B0C 'Tank Heat Grey'    0x028BB8 'Bunk Accent Lts'   0x028C20 'Bath Lts (GND)'
0x028B1C 'Tank Heat Black'   0x028BCC 'Bath Fan'          0x028C30 'Living Rm Lts (GND)'
0x028B30 'Tank Heat Grey 2'  0x028BD8 'Bench OHC Lts'     0x028C48 'Security Lt (GND)'
0x028B44 'A/C Heat Pump'     0x028BE8 'Living Rm Lts'     0x028C5C 'Bench Seat OHC (GND)'
0x028B54 'AES Start/Stop'    0x028BF8 'Security Lt'       0x028C74 'AES'
0x028B64 'Gen Start' … 'Gen Stop' … 'AES Start' … 'AES Stop' … 'Cargo Lts' … 'Bed Lts'
```

**Code takes literal pointers to them**, in adjacent 4-byte slots — i.e. pointer arrays,
so the names are selected by index at run time:

```
'Tank Heat Fresh' 0x00259278  <- literal at 0x018E80
'Bath Lts'        0x0025932C  <- 0x018EC0, 0x0190B8
'A/C Heat Pump'   0x002592C4  <- 0x018EC4
'Bath Fan'        0x0025934C  <- 0x0190C0
'Bench OHC Lts'   0x00259358  <- 0x0190C4
```

The table sits immediately below the **141-frame CAN template pool at `0x00259430`**
(Finding 7).

**What consumes it.** The dynamic rows of the `G12 OUTPUTS` diagnostics screen
(`MCF-FORMAT.md` §6.1). Confirmed on the coach: after a patch renamed every GuiData
occurrence of `Bath Fan` to `Starlink`, the `40 - ` row still read `Bath Fan`, because its
name comes from here and the patch never wrote GuiCode.

**Why this matters beyond diagnostics text.** These are exactly the channels whose name
depends on option state — a name table indexed at run time, sitting beside the transmit
pool, is a plausible thread for the option → selector binding that §8.3 and Finding 6
leave open. It has **not** been followed; recorded as a lead, not a finding.

## Finding 9 — the widget renderer, and where on/off appearance lives

`proven-from-code` for the structure, `inferred-from-data` for the field semantics.
2026-08-14. This is the render architecture, and it is the handle for modifying look and
feel.

**The render state is one large SRAM struct at `0x200301B0`** (`DAT_00256630`,
`DAT_002582B0` and `DAT_00256EC8` all resolve to it). It is a **120-slot array of 224-byte
(`0xE0`) widget records** — `FUN_002565D8` guards writes with `index < 0x78` — followed by
a globals area past `+0x6900`. The receive struct that holds the RV-C value slots
(`0x20007EBC`, §`MCF-FORMAT.md` 5.4a) is a *different* struct; the two are joined further up
the render tree, not by a shared base.

**`FUN_00256C54` renders one widget**, dispatching on a **type byte at `record+0x8A`**
through an 11-way switch (cases 0–8 and 10). Each case calls the low-level primitives —
`FUN_002557C0` (blit), `FUN_00251BB4` (clip rectangle), `FUN_002559A0`, `FUN_00254040`.

**On/off appearance is a two-style selection.** A flag at global `+0x704F` chooses which
pair of style fields the widget draws with:

```c
if (state[0x704F] == 0) { fill = record[0x19]; aux = record[0x1a]; }   // OFF appearance
else                    { fill = record[0x1b]; aux = record[0x1c]; }   // ON appearance
```

So every stateful widget carries **two colour/style words** — `record+0x64/+0x68` for off
and `record+0x6C/+0x70` for on — and the renderer picks between them by one flag. That flag
is `FUN_00256C54`'s argument (`1` → on, `0`/`2` → off), passed down from `FUN_00257BFC`'s
`param_2`, which climbs toward the RV-C value slot several frames up. **This is the byte a
patch would change to alter a button's colours in each state** — subject to the same-size
rule, since the fields are fixed-width.

**`GuiData +0x44BC8` feeds the render state.** `FUN_00257BFC` reads
`*(u32*)(0x90044BC8 + state[0x6BF8]*4)` and a parallel byte from `+0x4492C` into
`state+0x7046`, so `+0x6BF8` is a render cursor into that indirection table (§`MCF-FORMAT.md`
5.4b). And `state+0x9E + reg*2` is the **layout register file** — `FUN_00257BFC` offsets the
pen by register `+0x703A` and stores the pen to register `+0x703C` (value = the pen y at
`+0x6BEC`), which is how records position relative to earlier ones.

Not yet closed: the exact frames between the RV-C slot and `FUN_00257BFC`'s `param_2`. The
path is `FUN_00258650`/`FUN_00258330`/`FUN_002588A8` → `FUN_00257BFC` → `FUN_00256C54`, and
tracing `param_2` back through those is the continuation.

## Finding 10 — the widget type dispatch, and the record decoder

`proven-from-code` for the structure and the type field; `inferred-from-code` for each
type's semantics (read off the draw primitive it calls). 2026-08-14. This is the map for
changing widget *behaviour*, the counterpart to Finding 9's look.

**`FUN_00252CB0` decodes one widget record into the render struct.** It pulls a byte stream
through `FUN_0025211C` (u8) / `FUN_00252128` (u16), and the **low 6 bits of the first byte
are the widget type**, stored to `render+0x8A`:

```c
type = firstByte & 0x3F;          // -> render+0x8A, the switch selector in FUN_00256C54
...
flags = stream[7];                // -> render+0x7033
if (flags & 0x20) {               // geometry present
    mask = next_u8();
    if (mask & 1) x = next_u16();  // then y, w, h by mask bits 2/4/8
    ...
}
```

That is `MCF-FORMAT.md` §6's record — an opcode, a flags byte, and a presence-masked
geometry block — seen from the consumer. The top two bits of the first byte (the `0x40` /
`0x80` / `0xC0` seen in records) are therefore a **sub-type above the 6-bit type**.

**`FUN_00256C54` then dispatches on the type** through an 11-way switch. What each draws,
from its primitive:

| type | primitive(s) | reading |
|---|---|---|
| 0 | `FUN_0025624C` (image index) → `FUN_002557C0` (blit) | **icon / image** |
| 1 | `FUN_00254040` | filled shape / bar |
| 2 | `FUN_00254064` or `FUN_00254350` (by flag bit 4) | rectangle, two fill styles |
| 3 | `FUN_00255E90`, corner radius at record `+0x8B` | **rounded button** — the standard control body |
| 4 | `FUN_0025572C`, gated on flag `+0x704C` | shape with independent visibility |
| 5, 6, 0x2E | `FUN_00258330` (recurses) | **container / list**; type 6 matches a value in the register table |
| 7 | `FUN_00253EA4`, flag bit `0x200` | clip / scroll region |
| 8 | `FUN_00251E38` → `FUN_002521C8` (glyph), value at record `+0x24`, font/mode at `+0x91` | **text / numeric value** |
| 10, 0x12 | `FUN_00255F80`; 0x12 also registers geometry into a 9-entry list | **image-button + touch target** |

Types `0x12` and `0x2E` alias `10` and `6` with an extra action (touch-region registration;
value match), so the top bits genuinely extend the type.

### Finding 10 completed — the FULL 24-case dispatch (2026-08-29)

The table above stopped at type 10; the switch in `FUN_00256C54` actually has **24 cases**, and
`render+0x8a = firstByte & 0x3f` is the raw selector (`FUN_00252CB0`), so every census wtype maps
to one. Read off the primitives (`proven-from-code` structure, `inferred-from-code` semantics):

| wtype | primitive | reading |
|---|---|---|
| 0 | `FUN_0025624c`→`FUN_002557c0` | single icon / char (unit labels) |
| 1 | `FUN_00254040` | filled bar / shape |
| 2 | `FUN_00254064` / `FUN_00254350` (flag bit 4) | rectangle, two fill styles |
| 3 | `FUN_00255e90` (radius `+0x8b`) | rounded-rect **button body** |
| 4 | `FUN_0025572c` (gated `+0x704c`) | visibility-gated fill |
| 5 / 6 / 0x2e | `FUN_00258330` (recurse) | container; **6** value-matches the register table |
| 7 | `FUN_00253ea4` (flag `0x200`) | clip / scroll region |
| 8 | `FUN_002521c8` (glyph) | **single-line text / numeric value** |
| **9** | *(no case → default)* | **structural / geometry-only, not drawn** |
| 10 / 0x12 | `FUN_00255f80` (the 8-image blitter) | **image-button**; 0x12 also registers a touch target |
| **0xb (11)** | `FUN_002559a0` | **multi-line text label** |
| **0xc (12)** | `FUN_00256b84` | **touch-region registration** — writes the hit-box + action id `+0x94` into the 371-entry hit table |
| **0xd (13)** | `FUN_002521c8`→`FUN_002559a0` | **dynamic (value-driven) multi-line text** |
| 0xe (14) | `FUN_00253e38` | flow/clip anchor (advances the pen, no draw) |
| **0x10 (16)** | `FUN_0025451a` | **filled dot / rounded indicator** (a rounded-rect primitive; square+rounded = a status dot) — *not* an image-button |
| 0x11 (17) | `FUN_0025451a` | rounded indicator (variant) |
| 0x13 (19) | `FUN_0025572c` | gated fill (alias of 4) |
| **0x20 (32)** | `FUN_00255e90`+`FUN_0025451a`+`FUN_002544f0`+`FUN_0025572c` | **tank / bar gauge well** (compound) |
| **0x21 (33)** | `FUN_0025572c`+`FUN_0025451a` | rounded-capsule fill (button body with round ends) |
| 0x22 / 0x23 | `FUN_0025451a` | rounded indicator |
| 0x2f (47) | `FUN_00256760`, `FUN_002565d8` | (specialised; undigested) |

Cases 9, 0xf, 0x14–0x1f, 0x24–0x2d, 0x30–0x3f fall to **default** (nothing drawn) — a wtype-9
record is present in the stream but is a geometry/spacer, consistent with `FUN_0025451a` being a
per-corner rounded-rect (its `param_7..10` are the four corner-round flags, which is why 16/32/33
share it). Mirrored in `records.WTYPES`. *Correction to earlier prose: wtype 16 is a dot, not an
image-button (the image path is 10/0x12); and wtype 12 is the touch region, not a label — anything
listing 12 among label types (`render._is_label`) is drawing nothing there anyway.*

**Why this matters for editing.** A control's body is a type-3 rounded button whose corner
radius is one byte (`+0x8B` in the record), whose two-state colours are Finding 9's style
words, and whose touch target is the type-10/0x12 record. All are fixed-width fields — same-
size-editable — so button shape, colour and hit-area are reachable by the existing patcher
once each field's record offset is pinned.

**Not yet pinned: the exact record byte offsets** of the type-3 radius, the style words and
the value source, as opposed to their *render-struct* offsets. `FUN_00252CB0` writes the
struct from the stream via the presence mask, so the file offset of each field depends on
which mask bits precede it — the decode order in `FUN_00252CB0` is the key, and reading it
fully is the next step.

## Finding 11 — the widget record decode, field by field

`proven-from-code`, 2026-08-14. `FUN_00252CB0` reads a widget record through **three
independent cursors** (`DAT_00252FFC` header, `DAT_00253324` geometry, `DAT_002535A4`
style), each a pointer advanced in place. This is the exact decode order, which is what
turns Finding 10's render-struct offsets into editable record bytes.

**Header — 8 contiguous bytes** (cursor A), landing at render `+0x702C..+0x7033`:

| byte | meaning |
|---|---|
| 0 | low 6 bits = **widget type**; top 2 bits = sub-type |
| 1 | → `+0x702D` |
| 2 | → `+0x702E` |
| 3 | **colour-mode nibbles**: bits `[2:0]` = off-fill mode, bits `[5:3]` = off-secondary mode |
| 4 | → `+0x7030` |
| 5 | **colour-mode nibbles**: on-fill and on-secondary modes (`(b5>>1)&7`, …) |
| 6 | → `+0x7032` |
| 7 | **flags**: bit `0x20` = geometry block present, bit `0x40` = colour-inheritance bytes present |

**Geometry** (cursor B, only if flag `0x20`): a presence-mask byte, then a `u16` for each
set bit — bit 1 → x (`+0x50`), bit 2 → y (`+0x52`), bit 4 → w (`+0x54`), bit 8 → h
(`+0x56`).

**Style** (cursor C): up to four colours — off-fill, off-secondary, on-fill, on-secondary —
each selected by its 3-bit mode from header bytes 3/5:

| mode | source |
|---|---|
| 1 | theme default (`DAT_002535AC`) |
| 2 | white (`0xFFFFFF`) |
| **3** | **inline 24-bit colour — 3 bytes, order B, G, R** (`FUN_00252140`) |
| 4 | inherit the sibling colour |
| 6 | `u8` index into a palette (`DAT_002535B0`) |
| 7 | `u16` index into a colour table (`DAT_002535B4`) |

**What this makes editable, and how.** A button's per-state colour is same-size-patchable
**when its mode is 3** — overwrite the 3 inline B/G/R bytes in the style stream. Mode 6/7
buttons reference a shared palette/table, so editing one entry recolours everything that
cites it. The `+0x8B` corner radius (type 3) and the geometry `u16`s are likewise
fixed-width edits.

**The record is a single contiguous stream — `proven-from-code`, 2026-08-14.** The three
"cursor" globals `DAT_00252FFC` / `DAT_00253324` / `DAT_002535A4` all hold the **same
address**, `0x20036D54`, which is render-state `+0x6BA4` — the one cursor `FUN_00257BFC`
saves into a local and restores around each `FUN_00252CB0` call. So header, geometry and
style are read sequentially from one position; they are not parallel sections. This closes
the contiguity question: a field's file offset is fixed by the decode order plus which
optional fields precede it.

**The full read order, in sequence:** 8 header bytes → (flag `0x20`) geometry mask + one
`u16` per set bit → (flag `0x40`) two inheritance bytes → up to four mode-tagged colours
(each 0/1/2/3 bytes by its mode, and only if the type is in mask `0x2003A97F` / `0x600BA969`)
→ (mask `0x00040400` and byte-6 bit 1) a fifth inline colour → three register-mode bytes
gated by byte-0 bits 6/7 and byte-7 bit 0 → byte-6-gated bytes → up to four `u16`s gated by
byte-2 bits.

**Now byte-exact — RESOLVED (2026-08-14), `src/fireflymcf/records.py`.** The earlier replay
derailed on the third record; the full read order is now traced and the decoder lands on the
object boundary for **every object in all three builds** (1421/1421, 1076/1076, 1200/1200 —
`tests/test_records.py`). The missing oracle turned out to exist after all: an object in the
`+0x46148` table is `<count><records><pad to 4-byte boundary>` and all objects are 4-byte
aligned, so a correct decoder walks exactly `count` records and lands 0-3 bytes short of the
next object — a hard, whole-corpus check that is not fitted to any screen. The last error was
the **label**: the final type-gated loop reads a length then that many string bytes plus a
NUL, i.e. the label text is in the record stream, not skipped. `set_style`/`set_size` are now
unblocked (every field's file offset follows from this read order), as is the approach-A
layout walk.

## Finding 12 — the layout engine: how a record's relative geometry becomes absolute

`proven-from-code`, 2026-08-14. The prize for the renderer (approach A): where a widget's
on-screen position actually comes from. It is **not** `FUN_00247BF8` (that is the config /
floorplan writer, Finding 6). It is `FUN_00257BFC` (the per-record pen arithmetic) and
`FUN_00258330` (container recursion), reached as `FUN_00258650` → `FUN_00257BFC` →
`FUN_00256C54` → (`FUN_00258330` for containers) → `FUN_00257BFC`.

**The formula, per coordinate:**

```
coord = static_geometry                         # seeded by FUN_00252CB0 (0 if absent)
      + register_file[in_reg]                   # if the record's in-reg byte != 0
      + base_reference(mode)                    # container/sibling origin, by a mode byte
then optionally: register_file[out_reg] = coord # if the out-reg byte != 0
committed to render-struct +0x2a..+0x34
```

- **Static seed.** `FUN_00252CB0` reads up to **six** geometry `u16`s — presence-masked by
  `+0x702e` bits 1/2/4/8 and `+0x7032` bits 0x10/0x20 — straight into the six pen
  accumulators `+0x6bec..+0x6bf6` (or writes 0 when the bit is clear). So a record carries
  **three (x, y) pairs**, not just x/y/w/h.
- **Register file** at `state+0x9E` (`(reg+0x4f)*2` for the x-of-pair, `(reg+0x53)*2+4`
  for the y). A record can *add from* one register (`+0x703a/b/e/f`, `+0x7042/3`) and *store
  to* another (`+0x703c/d`, `+0x7040/1`, `+0x7044/5`) — this is how a later element anchors
  to a position an earlier one saved.
- **Base reference** chosen by a mode byte (`+0x7034..+0x7039`, one per coordinate):
  1 → `+0x36`, 2 → `+0x3a`, 3 → `+0x3e` for the x-lane (and `+0x38/+0x3c/+0x40` for y). After
  committing, `+0x36 = +0x2a` (the just-placed coordinate becomes the next element's origin),
  which is the flow-layout / relative-to-previous behaviour.
- **Value-bound coordinates.** If `+0x702e & 0x10`, the pen instead advances by
  `FUN_00251E38(slot, type)` — a **value formatter** (u8/s8/u16/s16/float, case 7 does
  decimal-place formatting), so a gauge/bar position tracks an RV-C value. Static widgets
  skip this.
- **Alignment tail** (byte `+9`): 2 = centre, 3 = right, using the width in `+0x32`.

**Containers** (`FUN_00258330`, type 5/6/0x2E): push the coordinate frame by copying a
**0x98-byte** state snapshot, recurse into the child object — indexed as
`*(object_table + child_index*4)` — via `FUN_00257BFC`, union the children's bounding box
(min/max into `DAT_002584e0[0..3]`), then pop the snapshot. Children therefore inherit the
parent's origin through `+0x36..+0x40`.

**The screen root and the base seed (`FUN_00258650`), `proven-from-code`.** A screen is
entered by index: `root_records = *(u32*)(TABLE + screen_index*4)`, where `TABLE = 0x90046148`
= **GuiData `+0x46148`, the 1422-entry object table** (`DAT_0025887c` resolved from the 13.6
GuiCode blob). `FUN_00258650` then **zeroes the whole base frame** — `+0x2a..+0x40` and the
register file — so a screen's origin is **(0,0)** and absolute coordinates accumulate from
there, and calls `FUN_00257BFC(root, 2)`. The render state it writes into is `0x200301B0`
(`DAT_00258884`). Crucially the **same `+0x46148` table** is what `FUN_00258330` indexes for
container children (`DAT_002584e4` = `0x90046148`), so screen roots and nested widgets are one
namespace of object indices.

**What this settles:** the calibrated anchors in `src/fireflymcf/render.py` can be replaced
by a real compositor — start at a screen root in `+0x46148`, walk its records from base (0,0),
seed each record's six geometry values, add register + base, recurse containers into
`+0x46148[child_index]`. **Still open** (the reproduction milestone): which of the three pairs
is (x,y)/(corner)/(w,h); the Climate screen's specific index; and the record field that holds
a container's child index (needed to walk the tree). Those turn this formula into pixel
coordinates checkable against the panel photo.

## Finding 13 — multi-root screens, and the content-extent that gates approach A's vertical accuracy

`proven-from-code`, 2026-08-14. Follow-through on Finding 12's "still open" list and open
thread 7. Two results, one negative and one positive.

**Screens are multi-root; a page is not a `FUN_00258650` root.** `GuiCode_entry1` calls the
screen-setup `FUN_00258650(index)` at exactly three sites, `GuiCode_entry3` at two — the whole
census of callers. The indices are **literals**: `0` (home/background container), `0x2f2`
(the pairing screen — its two children 755/769 decode to `PAIRING` / `WIRELESS SWITCH`), and
`0x1ac`/`0x1ad` (entry3). There is **no `FUN_00258650(7)`**. So a page like Climate is *not*
a root passed to the setup routine; it is a child page selected inside root 0's tree by a
runtime **type-6 value-match switch** (obj 972, a type-10 record, carries `child_index = 7`;
the switch's candidate-child *list* — the value→child mapping in the match table parallel to
the object table — is now expanded by `guidata.switch_candidates`, and `guidata.reachable_objects`
follows it. **Resolved 2026-09-10, `MCF-FORMAT.md` §10 item 5**: a `child_index` BFS from root 0
that follows only base children reaches the CLIMATE *tab* in the chrome but not the CLIMATE
*body* — object 7 is candidate value 3 of the top screen switch, not its base — so following
all candidates is what pulls the body in). This **refutes** the "obj 7 is already internally correct / multi-root
compositing explains the residual" hypothesis from Finding 12's block: obj 7's own walk already
contains the whole page (header + nav + body), so compositing is not the missing piece.

**The missing piece is the type-0/8 content-render extents — confirmed.** `FUN_00257BFC` calls
`FUN_00256C54` (the render/draw dispatch) for the record, *then* runs a per-type switch on the
type byte at `state+0x8A` to set the frame box `+0x3a..+0x40`. For **types 0 and 8** that switch
reads the **rendered content extents**:

```
case 0: case 8:
  +0x3a = +0x6c12 ;  +0x3c = +0x6c14 ;  +0x3e = +0x6c16 + 1 ;  +0x40 = +0x6c18
```

`+0x6c12..+0x6c18` are written by the value/text drawing primitive **inside `FUN_00256C54`**
(the layout engine only reads them). A type-0/8 widget is a drawn value/number — e.g. the
Climate **setpoint** — so its frame is its *pixel* bounding box, needing glyph/text metrics to
reproduce. `layout.py`'s `_update_frame` approximates it with the static geometry box, which
collapses to zero height; the mode grid (Cool/High/Low/Auto), whose y is **base-mode 2**
(reads `+0x3c`), therefore rides up to the tab's y=76 instead of sitting below the setpoint.
This is the last mile for approach A and the only remaining cause of the vertical residual.
The fix is a fresh subsystem: reproduce the type-0/8 draw's width/height via `glyphs.py`
metrics — validated against the value **string**, never fitted to the photo.

## Finding 14 — the content-render extents: how a type-0/8 widget's frame becomes its rendered box

`proven-from-code`, 2026-08-14. Thread 7's remaining piece, dug out of the draw path. Finding
13 showed the frame box `+0x3a..+0x40` for widget **types 0/8** is copied from the content
extents `+0x6c12..+0x6c18`; this finding is where those extents come from.

**Who writes them.** Decompiling `FUN_00256C54` and its callees, exactly two functions write
`+0x6c12..+0x6c18`:
- **`FUN_002557C0`** — single-line text/value. Computes the box from the *measured* string.
- **`FUN_002559A0`** — multi-line / word-wrapped text. Commits the widget's own geometry box
  (`+0x2a..+0x30`); it also does the line-breaking on spaces / `-` / `\n` to a width budget.

The single-line primitive is the one that auto-sizes (the Climate setpoint). Its formula:

```
width = FUN_002518F8(font_id, glyph_count, out_positions)   # measure, fill per-glyph x
x0 = pen_x(+0x2a); if align(+9)==2: x0 -= width/2; elif align==3: x0 -= width-1
y0 = pen_y(+0x2c) - font[0x0b]        # font[0x0b] = ascent / top offset
+0x6c12 = x0 ;  +0x6c14 = y0 ;  +0x6c16 = x0 + width - 1 ;  +0x6c18 = y0 + font[0x07] - 1
```

**The measurer `FUN_002518F8`** accumulates a per-glyph-pair advance from `FUN_002517AC`
(which maxes the glyph bbox bytes `[5..9]` and adds font spacing `[0x11]/[0x12]`), seeded by
the first glyph's left bearing and closed by the font trailing `[0x06]`. Glyphs are resolved
by `FUN_00251AA0` exactly as Finding 3 / `glyphs.py` already model (descriptor `[0]`=first,
`[1]`=last, `[2:4]`=base index, `[4:6]`=fallback).

**Font-descriptor metric bytes** (beyond the four `glyphs.py` names), `proven-from-code`:

| byte | meaning |
|---|---|
| `[0x06]` | trailing advance after the last glyph |
| `[0x07]` | **cell height** — the frame's y-extent (`y1 = y0 + [0x07] - 1`) |
| `[0x0b]` | **top offset** — pen_y to glyph top (`y0 = pen_y - [0x0b]`) |
| `[0x11]`,`[0x12]` | inter-glyph spacing |
| `[0..4]` / `[5..9]` (per **glyph**) | left / right ink-edge profiles kerned by the pair-advance |

Across 13.6 the descriptors form a clean ladder — slots 0..9 climb height 11→47 (proportional
sizes), slot 21 is a 63px digit font (`'0'..'5'`), slots 23/24 the 32/48px icon fonts. This is
implemented in **`src/fireflymcf/metrics.py`** (`font_metrics`, `text_width`, `text_extent`),
tested in `tests/test_metrics.py`.

**Width — the `FUN_002518F8` replay.** `text_width` now transcribes the measurer rather than
approximating it. Each glyph header carries a **left-edge profile `[0..4]`** and **right-edge
profile `[5..9]`** (ink extent sampled at five heights); the kerned advance from one glyph to
the next is `space[0x12] + max_k(prev.right[k] − next.left[k]) + 1` (`FUN_002517AC`'s class-2
branch), seeded by the first glyph's left bearing and closed by the last glyph's right edge
(`x + width`). It is a faithful transcription — the per-glyph pen positions come out strictly
monotone — but there is **no independent pixel oracle** for width (as there is none for the
glyph bitmap `start`), so it is validated by that monotonicity and by magnitude, not measured
pixels. Two things it does not model: the per-glyph **char-class** is a runtime widget byte
(`+0x0a`) the record does not carry, so `text_width` assumes class 2 (proportional-kerned); and
numeric right-aligned fields (class 3, fixed per-digit cells) would measure wider. `text_extent`
sets `width_exact=True` when every glyph resolved, and falls back to the old glyph-width sum
(`width_exact=False`) if one is missing.

**The value-widget font — traced, `proven-from-code`.** Static text carries an inline
`01 <slot> ff` selector, but the type-0/8 value widgets do not; `FUN_00256C54` draws them with
the currently-loaded font `state[2]` (`FUN_002520F4((char)state[2])` at the function top).
`state[2]` is set by the decoder `FUN_00252CB0` **lines 659-664**: the R38 selector read (gated
`(0x2000A961 & aed) || (aee & 0x6cc0)` — exactly `records.py`'s R38) stores `byte + 1` when the
byte `!= 0xff`, and leaves it otherwise, so **the font is persistent across records** and a
value widget inherits the last one set. The raw byte is the `glyphs.py` slot (the decoder's
`+1` is only its font-pointer-table index): it reads `0x18`/`0x0f` on the Bath Fan icon/caption
(matching render.py's hardware-verified fonts) and `0x09` (height 47, the big digit font) on the
Climate setpoint. `records.py` now surfaces this as `WRecord.font`; `layout.py` threads it as a
persistent current font.

**The vertical origin — resolved.** With the setpoint's real font height wired into the type-0/8
frame, the mode grid *still* rode up, because of a second, independent bug: the flow origin
`+0x36/+0x38` is updated only inside `FUN_00257BFC`'s coordinate block, which is **type-gated**
(line 33: `(DAT_002582bc & aed) || aee`, `DAT_002582bc = 0x600FFDFF`). **Type 9 (the spacer) is
excluded** (bit 9 clear), so it must not touch the flow — but `layout.py` was advancing the flow
for every record, letting an `obj93` type-9 spacer at `(0,0)` reset the setpoint's origin and
drop it ~190 px off-screen. Gating the flow update on that mask fixes it: the CLIMATE tab holds
at `(211,76)`, the setpoint lands mid-body, and Cool/High/Low/Auto step below it (y = 252 /
300 / 348 / 396) — **identically across all three builds** from their own bytes
(`tests/test_layout.py::test_mode_row_sits_below_the_setpoint`). Two byte-derived facts, no
calibration.

## Finding 15 — how objects attach (and how the Starlink button does *not*)

`proven-from-code`, 2026-08-14. Chasing the one gap the approach-A render exposed (the
Starlink button is on the Climate screen but not in the page's walk), which turned out to be a
**defensible negative**: the button attaches by none of the object-graph mechanisms.

**The container graph.** `FUN_00256C54` recurses into a child object only for **types 5, 6,
0x2e** — it dispatches those to `FUN_00258330(child_index)` (save-flag clear, so children share
the parent's flow/register state). The child index is the record's `+0x28` field. `layout.py`
now gates recursion on these types (it previously recursed any record carrying `+0x28`).

**Type 10/18 is an image widget, not a reference.** Their `+0x28` field is an **image index**:
`FUN_00255F80(idx, x, y, count)` bails unless `idx < 8` and blits `image_table[idx]` from an
8-entry table (`DAT_00255FC0` data, `DAT_00255FB8/FBC` dims). So obj 972's "child 7" is *image
7*, not object 7 — an earlier parent-graph built on that was a false trail. (Latent bug only:
no type-10/18 record on the five main pages carries an in-range `+0x28`, so nothing mis-recursed.)

**The type-6 value-match switch.** For type 6, `FUN_00256C54` reads the RV-C value
(`FUN_00251E38`), then scans a **parallel match-value table** `DAT_00257BF8 = 0x9004562C`
(indexed like the object table) over `[child_base, child_base + count)` for the entry equal to
the value, and renders that object; `count` is the `+0x94` field (written only by type-10/18,
so a switch inherits the last one). **In this corpus every one of the 896 type-6 records
resolves to a single child** (effective `count ≤ 1`), i.e. it behaves as a plain container —
there is no multi-page candidate list to expand here.

**The negative on the Starlink button (obj 247, file `0x97200`).** It is a real object-table
entry, but: nothing references it by `child_index` (no parent in the whole graph); no absolute
`0x900xxxxx` pointer to it exists anywhere in GuiData; and no type-6 switch's `[base, base+count)`
range covers it. It is not reachable from root 0 or any page root. So it is placed by a path
outside the object/reference graph — almost certainly the **option → placement binding** (it is
an OPTIONS entry; open thread 4, unmapped) — not by the page/screen composition. Expanding the
page-switch, the hypothesis this dig set out to test, does **not** pull it in.

## Finding 16 — the option → widget binding: a value-match switch selects the conditional control

`proven-from-code` (mechanism) + `proven-from-data` (the group), 2026-08-14. Finding 15 left the
Starlink button (obj 247) attaching by an unknown path; this is that path, and it is the first
concrete handle on open thread 4.

**The mechanism.** An option-conditional control is a **candidate in a type-6 value-match
switch** on the page. `FUN_00256C54` (case 6) reads the switch's runtime value, then scans the
parallel match-value table `DAT_00257BF8 = 0x9004562C` (one `u16` per object, indexed like the
object table) over the candidate range `[child_base, child_base+count)` and renders the object
whose match value equals the runtime value; the base has match value 0.

**The worked example (Bath Fan / Starlink).** The Climate body container **obj 881** holds a
type-6 record with `child_base = 246`. The match table gives the group its selector values:

| obj | match value | content |
|---|---|---|
| 246 | 0 | empty (the default — nothing drawn) |
| 247 | 1 | the **Bath Fan** button (rounded frame + fan icon + caption + touch) |
| 248 | 2 | — |

So when the switch value is **1**, obj 247 renders. Driving it there places the button at
**(550, 76)** — the top-right corner where the coach photo shows the (renamed) Starlink button.
`layout.py` now supports this: `Screen(data, match_table_off=MATCH_TABLE_136).walk(7,
switch_values={246: 1})` pulls the button in; the default renders obj 246 (nothing), which is
why the button was absent. `render.climate_placements_decoded(..., switch_values={246: 1})`
renders the "option enabled" page (`build/climate-A-starlink-on.png`).

**What is still open (the rest of thread 4).** This pins *how* an option shows a widget, not yet
*where the switch's value comes from*. The obj-881 switch record is minimal (no inline value-slot
fields), so its selector variable (`state+0x91` / the `FUN_00251E38` argument) is set elsewhere —
the OPTIONS config (obj 46/47) writing a register the switch reads. Tracing that register from
the OPTIONS checkboxes to this switch is the remaining step, and it is what a *new* OPTIONS entry
would need (14.7's `'   Starlink Power'` is the shipped example). The switch `count` also stays
unread (the `+0x94` field is written only by type-10/18; a switch inherits it), but the match
table makes the group boundary recoverable without it.

## Finding 17 — the option register: a shared variable slot closes the option → widget binding

`proven-from-code`, 2026-08-14. Finding 16 showed a value-match switch *displays* the
conditional widget; this is *where the switch's value comes from*, closing the display side of
open thread 4. Following `state+0x91` back, as intended.

**The value path.** A type-6 switch (and any value widget) reads its value with
`FUN_00251E38(state+0x24, state+0x91)` — data pointer and type. Both are set from a **value-slot
index** the record carries (R40/R41/R42; type 6 uses R41, `records.WRecord.value_slot`):
`state+0x24 = DAT_00253D04[slot]` (u32 data pointer) and `state+0x91 = DAT_00253D08[slot]` (u8
type). Those two tables live in **GuiData** — `DAT_00253D04 = 0x90044BC8` (`+0x44BC8`),
`DAT_00253D08 = 0x9004492C` (`+0x4492C`) — so `layout.variable_register(data, slot)` resolves a
slot to its register straight from the file.

**The worked example.** The Bath Fan switch (obj 881, on the Climate page) carries
`value_slot = 109`. Slot 109 → **data pointer `0x20039B49` (SRAM), type 0 (bool)**: the "Bath
Fan option enabled" flag. So the switch renders obj 247 exactly when the byte at `0x20039B49` is 1.

**Connected to the OPTIONS checkbox.** On the OPTIONS page (obj 46) the "Bath Fan" entry is a
type-6 switch with the **same `value_slot = 109`** — the checkbox reads the same
`0x20039B49` register to draw itself checked/unchecked. So one register drives both: enabling
the option flips `0x20039B49`, and every switch bound to slot 109 (the checkbox *and* the
Climate button) follows. `tests/test_layout.py::test_option_checkbox_and_widget_share_the_register`.

More worked examples, same shape: the **black-tank** option is `value_slot = 112` → SRAM
`0x2003A4B4` (bool) — on HOME it gates the 4th tank (BLACK) and its gauge on top of the floorplan
(floorplan WD alone shows FRESH/GREY/GREY2; `slot 112 = 1` adds BLACK). `layout.variable_register`
resolves any slot, so a conditional widget's controlling register is now always recoverable.

The HOME **generator / AGS** section (Start/Stop/GEN, bottom-right) needs *two* registers:
the battery-quadrant switch on a generator-capable value (`slot 6 → 2/3`, `0x2003A03E`) so the
quadrant object with the generator lives in the tree, and the **AGS** option (`slot 37 → 1`,
`0x2003A38C`) which unhides it. `{floorplan WD, black-tank, slot 6=2, slot 37=1}` reproduces the
`HOME-INVERTER-AGS` coach photo (`build/cmp-HOME-INV-AGS.png`). Registers of note:
`AGS = slot 37` (`0x2003A38C`), `Progressive Inverter = slot 113` (`0x2003A62B`).

**An observed write-side dependency.** On hardware, enabling *Progressive Inverter* in Floorplan
Options *auto-selects AGS* — one checkbox writes two registers (`0x2003A62B` and `0x2003A38C`).
The display side reads them independently (each is just a value-slot a switch consumes); the
*coupling* lives in the OPTIONS write handler (thread 4 write side, likely `FUN_00247BF8`), which
we have not yet read. This is concrete evidence that option writes carry dependency logic, not
just a bit per checkbox — worth remembering when the write side is dug out or a new OPTIONS entry
is added.

**The floorplan variable (whole-page gate).** The same shape scales to the floorplan: the
switches that gate entire pages read **variable slot 5 → SRAM `0x2003A77A` (u8)**. Its value is
the floorplan index; the match-table group for the SETTINGS floorplan-name switch spells the
list out — 1=RT, 2=RA, 3=RD, 4=SY, 5=TB, 6=CB, 7=WA, **8=WD**, 9=WT, 10=RY, 11=4NJ, 12=3NG (0 /
-1 = "No Floorplan Selected"). Driving slot 5 to 8 (`slot_values={5: 8}`) reproduces the probe5
coach's **WD** configuration from the bytes: LIGHTS turns from "NO FLOORPLAN SELECTED" into the
Interior/Exterior light grid, SETTINGS shows "Floorplan: WD" + "Hallway", HOME gains GREY2 / Tank
Heater — matching the hardware photos (`build/cmp-*.png`). `layout.Screen.walk(...,
slot_values={slot: value})` is the principled driver (key on the register a switch reads);
`switch_values` (key on `child_base`) remains for one-off candidate overrides.

**What remains.** Only the *write* side: what physically sets `0x20039B49` / `0x2003A77A` on a
tap or config load (the touch handler / EEPROM persist, likely `FUN_00247BF8`). The display side
— which register each conditional widget or page reads, and rendering any configuration — is
complete.

## Finding 18 — the chrome colour source: mode-7 fills are SRAM cells written by GuiCode's theme-init

> **Status: RESOLVED 2026-08-15.** This finding is kept in three layers, in the order they were
> established — the original 2026-08-14 mechanism (below), the 2026-08-15 CORRECTION that overturned
> its "not in the file" conclusion, and the RESOLVED section that implements it. Read all three; the
> first layer's closing claim is superseded.

`proven-from-code`, 2026-08-14. Closes the mechanism half of open thread 1 (the button / bar /
tab colour source), using the colour fields `records.py` now captures.

Each widget record carries **mode-tagged colour fields** (colour blocks A/B, `records.WRecord.
colors`): the mode is `header[3]&7` / `(header[5]>>1)&7` etc., and per mode an inline value
follows — **mode 3 = inline BGR `u24`**, **mode 6 = palette index (u8)**, **mode 7 = colour-table
index (u16)**, mode 0/4 = inherit (no inline value).

The chrome (header, nav rail, tab, buttons, body) uses **mode 7**: background = index 3/4,
buttons = 8/10, nav rail = 184, etc. And the mode-7 colour table is `DAT_002535B4 = 0x90044BC8`
— **the very same table as the value-slot variable table** (`DAT_00253D04`, Finding 17). Its
entries are **SRAM pointers** (`0x90044BC8[8] = 0x20039974`, …), so a mode-7 fill is a **runtime
variable**: the box's colour is read live from SRAM, initialised by the firmware's theme, not
stored as a constant in GuiData. That is why the chrome colour never turned up as a static value
— it is a variable, on the same footing as an option state or a sensor reading.

**What is readable.** Mode-3 fills *are* in the bytes and byte-accurate — the battery indicator's
teal `RGB(0,167,157)`, the Inverter bar's black, some grays. `render.py` now uses those directly
(`_inline_fill`) and falls back to the position heuristic for mode-7. Reproducing the mode-7
chrome colours exactly would need the SRAM theme init (firmware), not the file; the probe5 photos
are a visual oracle for it but the values are runtime. Mechanism resolved; the constants are not
in GuiData.

**CORRECTION, 2026-08-15 (the colour dig): the mode-7 constants ARE in the file — in GuiCode.**
The SRAM theme init is a straight-line block inside **`FUN_00247bf8`** (13.6, ~`0x00247F44`–
`0x0024ADxx`) that writes **hardcoded `u32` RGB888 constants** into the very SRAM cells the mode-7
table `0x44BC8` points to. Traced examples: the mode-7 table entry for index 8 (`0x90044BC8[8] =
0x20039974`) is written `0x00272862` at `0x0024832A`; index 3 (`0x2003A124`) ← `0x0015183A`; index
4 (`0x20039A88`) ← `0x00BE1E2D` (RGB (190,30,45), the chrome red) via an `add`-chain from the same
base. The block also writes recognizable constants — `0x00FFFFFF` (white, `mvn r1,#0xff000000`),
`0x00141414` (dark grey), `0x00AA00AA` (magenta) — which pin the format as **RGB888 (`0x00RRGGBB`)**.
So a mode-7 fill is recoverable: `index → 0x44BC8[index] (SRAM cell) → the constant that init writes
to that cell`. Extraction needs the GuiCode section (not GuiData) and must handle the `add`-chain
variants (some colours are `base + imm`). This is the input to making chrome colour byte-derived in
`render.py` (open thread 1 → tractable, was thought impossible).

### RESOLVED, 2026-08-15 — `theme.mode7_palette` extracts it; open thread 1 closes

The correction above was implemented and merged (`6dcdabe` extractor, `1714f9b` render wiring; SDD
working notes, not published). **`src/fireflymcf/theme.py`** exposes

```python
mode7_palette(data: bytes) -> dict[int, tuple[int, int, int]]      # {mode-7 index: (R, G, B)}
```

**How it locates the theme-init, build-generally** (no hardcoded 13.6 addresses):

* the target set is every **type-10** entry of the variable table (`guidata.find_variable_tables`)
  — a colour cell per `FUN_00251E38`; its value is a 32-bit SRAM address;
* the **GuiCode section** is sliced from the container by name, and its mapped base read from the
  section descriptor's tag-`0x40` flash-destination entry (`0x00240000` on all three builds);
* the section is disassembled linearly (capstone, Thumb) and **emulated**, tracking only the
  constant-forming opcodes — PC-relative `ldr`, `mov`/`movw`/`movt`/`mvn` immediate,
  `add`/`addw`/`sub`/`subw` immediate — plus `str`.

**The three store shapes it recovers**, all present in the block:

```
direct      ldr r3,[=cell] ; ldr r2,[=const]           ; str r2,[r3]     idx 8  = 0x00272862
add-chain   ldr r3,[=cell] ; add.w r2,r2,#i ; addw r2,r2,#j ; str        idx 4  = 0x00BE1E2D
immediate   ldr r2,[=cell] ; mov.w/movw/mvn r1,#imm    ; str r1,[r2]     0x00FFFFFF via mvn
```

The add-chain builds on the register value the *previous* store used, so recovery needs running
register state across the block rather than a per-store peephole — hence linear emulation. Register
state is cleared at every control-flow instruction and at every data island (where the linear
decode desyncs and resyncs on the next 2-byte boundary), so an add-chain carry can only ever span
the contiguous, branch-free theme-init block.

**Guards — the reason a wrong colour is never reported as decoded.** A value is recorded only when
the store's base register holds an **exact** target cell address, obtainable solely from a correct
PC-relative literal load of that address, and the stored value is a valid RGB888 (`<= 0x00FFFFFF`).
An **ambiguity guard** drops any cell written more than one distinct constant (a runtime
value-match, not a theme colour). Runtime copies and untracked arithmetic fail safe to **omission**.
An omitted index is a **named residual**: the caller keeps its heuristic.

**Recovered on 13.6** (`proven-from-code`, each matching the Ghidra-traced constant):

| mode-7 index | constant | RGB | where |
|---|---|---|---|
| 3 | `0x0015183A` | (21, 24, 58) | background |
| 4 | `0x00BE1E2D` | (190, 30, 45) | header / nav rail / ON-OFF red, via the add-chain |
| 8 | `0x00272862` | (39, 40, 98) | button |
| 10 | `0x007C151D` | (124, 21, 29) | button |

**Coverage is build-dependent: 20 / 18 / 6 cells on 13.6 / 11.23 / 14.7.** 14.7 resolves far fewer;
that is recorded as a limitation, not papered over. `tests/test_theme.py` covers the traced
constants, the add-chain being genuinely followed, the RGB888 format constants, well-formedness on
every build, omission-not-guessing, and the ambiguity guard.

**In the render.** `render.py` computes the palette **once per render pass** and resolves mode-7
fills through it (`_mode7_fill` → `_decoded_fill`/`_decoded_color`): a resolved index draws
`color_src="decoded"`, an unresolved one keeps the labelled heuristic fill and is named. The Home
render now reproduces `HOME.jpg`'s reds, background and quadrant panels (`build/render-home-coach-13.6.png`).

**Why the remaining HOME fills stay unresolved — runtime-state selection (2026-08-15 dig).** The
still-heuristic cells are not missed constants; they are **runtime-state-selected** and correctly
omitted (the ambiguity guard's whole point):

- idx 160 (Water Pump, cell `0x20039eac`) and idx 455 (Tank Heater, `0x20039b74`): the theme-init
  writes **three** different constants — `0x4151A3` (65,81,163), `0xCC2127` (204,33,39), `0x00AEEF`
  (0,174,239) — the store reached by branch convergence / three separate stores, selected at runtime
  (idx 160: `ubfx(*(r4+0xac),2,3)` @`0x248e60`) by widget on/off/unavailable state.
- idx 202 (`0x20039d5c`) and idx 206 (`0x2003a664`): a runtime green `0x2BB673` vs red `0xCC2127`
  selected by the **live tank reading** vs the `0x270f` sentinel (@`0x24a344`/`0x24a32e`) — a
  level/alarm state indicator. **Owner-verified on the coach (2026-08-15): NOT the HOME fresh/grey/
  grey2/black tank fill** — those keep their per-type colour (teal/grey/grey/near-black) at 0/33/67/
  100%, none ever red. So idx 202/206 is a different indicator (its green isn't fresh's teal either);
  its identity is unconfirmed. Correctly omitted from the palette regardless.
- A linear/copy-forward extension would report a **confidently-wrong** colour (it sees only the
  fall-through edge of a multi-source store); safe recovery needs CFG-aware aggregation, after which
  the ambiguity guard omits anyway. So `theme.py` is left as-is. Full evidence:
  `build/residuals-dig-notes.md`.

**The HOME tank display IS byte-derived, except the fill level (2026-08-15, owner hardware oracle).**
The tank colours are all in the file — the earlier "unresolved" read conflated them with the runtime
cells above:

- **Well body** (the empty well behind the fill): each well record (wtype 32, `0x910d5`/`0x91051`/
  `0x90fcd`/`0x91159`) carries `fillB` = mode-7 **161** = `(65,81,163)` — a blue *lighter than the
  quadrant* `(39,40,98)`, matching the owner's description ("background slightly lighter than the
  quadrant"). This is the displayed body, not the `fillA` mode-2 inherit. (A single JPEG pixel read
  `~(48,96,246)`; the owner's relational description is the better oracle and agrees with the file.)
- **Per-type fill colour** (the inner level bar, wtype 33): FRESH `fillA` = mode-3 inline `#00A79D`
  (teal); GREY / GREY2 = mode-3 `#808285` (grey); BLACK = mode-7 **130** = `(20,20,20)` (near-black) —
  matching the owner's fresh/grey/black. All byte-derived.
- **Fill level (height) is runtime** — the record geometry is the 100 % extent; the actual level is
  bus-delivered (thread 5). So the render must draw the well *body* + the per-type colour only up to a
  supplied level (default empty = the coach's 0 %), never the full record height (which reads as a
  falsely-full tank).

**What this does not claim.** The *mechanism* is `proven-from-code` (above). The extractor is a
faithful replay of that block's register arithmetic, validated against the traced constants — not a
pixel oracle. Mode-6 palette-index colours were out of scope and remain unresolved.

## Finding 19 — the option write side: option/floorplan state comes from CAN, not the file

> **Half superseded, 2026-08-18 — see Finding 42.** What this finding read is the **receive**
> path, and it is right: the option registers are written from a received CAN payload. But the
> panel is also the *sender* of that payload on a floorplan change, and the values it sends are
> literals in its own flash. "The defaults are not in the file at all" was the wrong conclusion
> from a correct observation.

`proven-from-code`, 2026-08-14. The write-side half of open thread 4, run as a spike to decide
whether an option could be authored by patching the MCF. **It cannot** — a defensible negative,
independently reproducing Finding 6's capture-proven result from the write side.

**Each option/floorplan register has exactly one writer, and it copies from a received CAN
message.** In GuiCode `FUN_00247BF8` there is a single receive-unpack block (`0x00247C18`–
`0x00247C94`) that stores each register from a byte of a config payload passed in from
`GuiCode_entry1` (`r3 = [r4+0x214]`, the message context, `0x0024EE3C`):

| register | option | source | store |
|---|---|---|---|
| `0x2003A77A` | floorplan | `payload[0x00]` | `strb` @ `0x00247C20` |
| `0x20039B49` | Bath Fan | `payload[0x0D]` | `strb` @ `0x00247C5E` |
| `0x2003A4B4` | Black Tank | `payload[0x0E]` | `strb` @ `0x00247C64` |
| `0x2003A38C` | AGS | `payload[0x13]` | `strb` @ `0x00247C7C` |
| `0x2003A62B` | Progressive Inverter | `payload[0x17]` | `strb` @ `0x00247C82` |

Every other reference to these addresses across the image is a `ldrb` **read**. No store sources
its value from an immediate or a GuiData/file pointer; there is no boot-time copy of a file
constant into these cells. So the persisted option/floorplan **state has no byte-addressable
representation in the MCF** — it is delivered at runtime over the RV-C bus (the G12/config
authority), which is why it never turned up as a file constant and why the render can only be
*driven* to a config (via `slot_values`), not have that config *read back* from the image.

**~~The Progressive-Inverter→AGS coupling is upstream, not in GuiCode.~~ RETRACTED 2026-08-23 —
it is in GuiCode, in the tap handler. See Finding 50.** The reasoning below is correct about the
*receive* path and wrong in its conclusion, because it only ever looked at the receive path: this
finding traced the config-payload **unpack** and, finding no store of one register guarded by a
read of the other, concluded the coupling had to be somewhere else. But no register store was ever
the mechanism. The coupling is on the **transmit** side — the Progressive Inverter row's tap
handler at `0x0024CCF4` emits *two* `0x1FED9` templates in one straight-line run, `E6 := 1` then
`EF := 1`, and the panel's own AGS register updates only afterwards, from the G12's echo through
the very unpack traced here. Finding 23 (2026-08-15) already recorded the two-template run in
passing; nobody came back to correct this paragraph, so a false negative sat in the docs for eight
days. *The general lesson, and it has now cost twice: **a register with one writer is not the whole
story when the value round-trips through another node.** The write side of an option is the frame
the panel sends, not the store that later lands the echo.*

What survives unchanged: AGS and PI are each written once from independent payload bytes, and no
instruction writes one guarded by a read of the other. GuiCode reads PI+AGS together only for
*display* gating (a visibility flag `0x2003A617` cleared iff `AGS==0 && 0x2003A4E9==0 && PI==0`,
`0x00249D3E`–`0x00249D60`).

**Consequence (closes thread 4/5's write side and open thread 2's authoring question):** an
options-authoring patcher of the MCF is not possible — the file holds only the *display* mechanism
(which widget a value selects), not the *value*. Authoring an option means writing the CAN config
(the G12/EEPROM), out of scope for a same-size MCF patch. `options.py` + `cli render`'s
`slot_values` are therefore the ceiling of what the file supports: name the register, drive the
render, but not persist it. This is the owner-approved negative for ③b.

## Finding 20 — the option config message: a fixed byte schema, addressed by offset

`proven-from-code` (schema) + `inferred` (the `0x15FCE` selector join), 2026-08-14. Extends
Finding 19's negative with the actual message layout, and is the **first code-level pass on
14.7** — imported as the Ghidra project `guicode147` (`GuiCode147.bin`, base `0x00240000`,
90.5% of the blob decoded, 157 functions), the "repeat for 14.7" next-step.

**The unpack.** `GuiCode_entry1` calls the config-unpack (`FUN_00247bf8` @`0x00247BF8` in
13.6; `FUN_00247cac` @`0x00247CAC` in 14.7), which dereferences a config buffer pointer at
`GuiCodeState+0x214` (`ldr r3,[r4,#0x214]`) and copies **fixed byte offsets** straight into
the option registers — e.g. 14.7 Starlink at `0x00247D44`:

```
ldrb r1,[r3,#0x1b]     ; config byte 0x1B
ldr  r2,=0x200396FE    ; Starlink Power register
strb r1,[r2,#0x0]
```

**The offsets are build-stable across 13.6 ↔ 14.7** (the registers shift with the SRAM map;
the schema does not). **Correction, 2026-08-15 (Finding 23):** the original wording said
"build-stable" unqualified, and that is **not** true of the corpus as a whole — **11.23 carries
a different offset schema.** Measured on the selector join Finding 23 recovers, 11.23's cfg
`0x0D` emits selector **`0xEE`** and its cfg `0x0E` emits **`0xEF`**, where 13.6 emits `0xE9`
(Bath Fan) and `0xEC` (Black Tank) at the same two offsets — so the `(offset, selector)` pairs
do **not** line up. Only `0x00 → 0xF5` (floorplan) and `0x0A → 0xEB` (Heat Pump) do, across all
three. Scope the claim to 13.6 ↔ 14.7 wherever it is quoted. **Which side moved is not
established** (correction, review fix wave 2): an earlier wording said "those offsets carry
different options there", which attributes the movement to the *offset* side and so quietly
assumes a selector keeps its option across builds. This finding's own 14.7 reading undercuts
that assumption — `0xE4` is Bath Light in 13.6 and Starlink Power in 14.7 (residual 6 below).
The selector facts above are Rule-A/B results from the bytes and stand; the causal attribution
does not. Either side could have moved.

| option | cfg byte | 13.6 reg | 14.7 reg | 13.6 `0x15FCE` sel |
|---|---|---|---|---|
| floorplan | `0x00` | `0x2003A77A` | `0x2003A2A7` | `0xF5` |
| Heat Pump | `0x0A` | `0x2003A296` | `0x20039E0F` | `0xEB` |
| Bath Fan | `0x0D` | `0x20039B49` | *(absent)* | `0xE9` |
| Black Tank | `0x0E` | `0x2003A4B4` | `0x2003A008` | `0xEC` |
| Bunk Accent | `0x11` | `0x20039E70` | `0x20039A9C` | `0xE5` |
| AGS / AES | `0x13` | `0x2003A38C` | `0x20039F08` | `0xEF` † |
| Lithionics | `0x14` | *(absent)* | `0x2003A009` | *(new in 14.7 — 14.7 sel `0xE7` †, unverified)* |
| Progressive Inverter | `0x17` | `0x2003A62B` | `0x2003A169` | `0xE6` † |
| Bath Light | `0x19` | `0x20039EBD` | *(absent)* | `0xE4` |
| Cargo Light | `0x1A` | `0x20039CF8` | `0x20039978` | `0xE3` |
| **Starlink Power** | **`0x1B`** | *(absent)* | **`0x200396fe`** | *(absent in 13.6 — 14.7 sel **`0xE4`** †, unverified; ~~`0xE7`~~ **retracted**)* |

† Recovered 2026-08-15 by **Finding 23**, not by the elimination this finding originally used.
Their support differs and is not interchangeable: `0xE6` is corroborated on the bus
(`BUS-BASELINE` PC.3 Result 2) **and** by 14.7's stronger rule; `0xEF` rests on a three-gate
value correspondence that matches rvc2mqtt's `EF` enum; the two 14.7 entries are
`inferred-from-code` with **no oracle at all**. Grades per row are in Finding 23.

**The send path is offset-addressed too.** `FUN_0025b9b0` (@`0x0025B9B0`) is the config-set
helper: it compares `config_buffer[offset]` to a new value and, if changed, builds
`{u32 0x19EE0000, offset_lo, offset_hi, value, FF×5}` and calls two host callbacks held in
SRAM (`*DAT_0025ba34 = 0x20039558`, `*DAT_0025ba38 = 0x20039560`, installed at runtime — the
actual CAN framing is host-side). So the panel addresses a config parameter by its **buffer
offset**, not by the `0x15FCE` selector.

> **Correction, 2026-08-15 (Finding 23): those are 14.7 addresses.** The paragraph above gives
> them without saying which build they belong to, and they do not exist in 13.6 — 13.6's
> GuiCode is `0x19BB8` bytes and ends at `0x00259BB8`. **13.6's config-set helper is
> `FUN_00259100`** (`cmp r1,#0xf9` @`0x00259100`; `ldr.w r3,[r0,#0x214]` @`0x00259104`;
> `mov.w r1,#0x1ee00` @`0x0025911E`), and its two callback slots are **`0x2003986C`** (sender,
> literal @`0x00259184`) and **`0x20039874`** (notify, literal @`0x00259188`). Both slots are
> written exactly once, by **`GuiCode_entry0`** (13.6 `0x00247A90`, the header `+0x0C` vector
> value `0x00247A91` with the Thumb bit) — `str` @`0x00247AA2` from `[r4,#0x08]` and `str`
> @`0x00247AAE` from `[r4,#0x10]`. See Finding 4.
>
> Also corrected there: "the actual CAN framing is host-side" is **false as a description of
> where the selector comes from.** The frame is fully built inside GuiCode; the host callback
> is a transport that fills in one byte of the CAN id. See Finding 23.

**The offset→`0x15FCE`-selector join is not in the file — re-confirmed.** Searched every
section for an offset-indexed table satisfying the six known 13.6 pairs, and (per Finding 19 /
`find_option_bindings`) for strided selector sequences: nothing. The translation lives in
code (host/G12), consistent with the standing `unknown`.

> **Update, 2026-08-15: the searched negative was correct, and the conclusion drawn from it was
> not.** There genuinely is no offset-indexed table — **Finding 23** shows why: the selector is
> a literal byte inside a static 12-byte CAN-frame template (Finding 7's pool) and is joined to
> the offset by **code adjacency** inside GuiCode's own tap handlers, so no table can exist to
> be found. The translation does **not** live host-side or in the G12; it is inside the
> container, in `Lyra 13v6 GuiCode`.

**~~Starlink's selector = `0xE7`, by elimination~~ — RETRACTED, 2026-08-15 (Finding 23).**
The inference is reproduced here in full because retracting it is part of the record:

> The variable-table option-run (entries 283–288) reads `{E7,E4,E5,E3,EB,EC}` in 14.7; only
> entry 283 changed vs 13.6 (`E9`→`E7`). Selectors are build-stable (`RVC-GAPS` Result 1), so
> the 14.7 OPTIONS rows Heat Pump/Bunk/Cargo/Black Tank take `EB/E5/E3/EC`, leaving `{E7,E4}`;
> **`E4` is Bath Light's kept-but-hidden declaration**, so the remaining shown option —
> Starlink Power — is `0xE7`, the slot Bath Fan (`0xE9`) vacated.

The elimination rested on the bolded premise, which was never stated as a premise and is
**disproved by the code**: 14.7's dispatcher id `0x855` gates on Starlink Power's own register
`0x200396FE` (`ldr` @`0x0024C37E`, `ldrb` @`0x0024C380`) and emits
`FF 96 E4 0F 01 00 D1 EA` from template `0x0025C0D8` — `0xE4` is Starlink Power's live
selector, not a dead declaration. Finding 23 derives **cfg `0x1B` (Starlink Power) → `0xE4`**
and **cfg `0x14` (Lithionics, new in 14.7) → `0xE7`**, which accounts for every selector in the
14.7 run with no hidden entry and no orphan; the elimination version had to leave Lithionics
with no selector at all.

**Retracting `0xE7` does not promote `0xE4` to fact.** Both 14.7 rows are `inferred-from-code`
and **unverified** — no oracle entry, no 14.7 capture. This finding's standing advice is
unchanged and now applies to `0xE4`: confirm on the bus by toggling the option on a 14.7 panel
and diffing the `0x15FCE` broadcast (the `BUS-BASELINE` method) before treating it as fact.

## Finding 21 — the frame switch is exhaustive: every widget type, classified

`proven-from-code`, 2026-08-15 (render-fidelity Task 1). Finding 14 established the type-0/8
content-extent case of `FUN_00257BFC`'s per-type frame switch (`state+0x8a`, run right after
`FUN_00256C54` draws the record, setting `+0x3a..+0x40`). This finding transcribes the *whole*
switch, decompiled end to end, and closes the "unlisted types fall through to a generic box"
approximation `layout.py` carried until now.

**The switch has exactly six kinds of case, covering every value 0-0x3f (the type field is 6
bits):**

| case(s) | frame formula | reading |
|---|---|---|
| 0, 8 | copy `+0x6c12/14/(16+1)/18` | content-render extent (Finding 14) |
| 1 | `(x0, y0, x0+1, y0)` | a point |
| 0x10, 0x22 | `(x0, y0-c4, 2*c4+x0+1, y0+c4)` | centred on x (`c4` = record coord index 4, `+0x32`) |
| 0x11, 0x23 | `(x0, y0-c5, 2*c4+x0+1, y0+c5)` | centred on y (`c5` = index 5, `+0x34`; note the x-extent still uses `c4`) |
| 0x2f | `(x0, y0, x0, y0)` | a degenerate rect |
| 2,3,4,7,0xa-0xf,0x12,0x13,0x1d,0x1e,0x20,0x21,0x24,0x26-0x2d | `(x0, y0, x1+1, y1)` | the declared geometry box, verbatim |
| **everything else** — 5, 6, 9, 0x14-0x1c, 0x1f, 0x25, 0x2e, 0x30-0x3f | *(no case)* | **frame carries over from the previous sibling** |

The last row is not a fallback guess: the switch's C source genuinely has no `case` label for
those values, so control falls past the `switch` with no assignment. Containers (5, 6, 0x2e)
recurse into `FUN_00258330` instead of drawing, so they never reach this switch with new
content; 9 is the spacer; the rest (0x14-0x1c, 0x1f, 0x25, 0x30-0x3f) are simply absent from
the switch body — a real, cited negative, not an implementation gap. Of that last group, only
5/6/9 are ever emitted by any of the three builds' decoders (`tests/test_layout.py::
test_every_observed_wtype_is_frame_classified` walks every object in all three and confirms
the full set of emitted types is {0,2,3,4,5,6,8,9,0xa,0xb,0xc,0xd,0x10,0x13,0x20,0x21}); the
rest of the "no case" range and the non-emitted explicit cases (1, 0x11/0x23, 0x2f) are
asserted from the disassembly, not exercised by any corpus file.

**The type-0/8 case's x-extent needs the alignment byte, not just the geometry.**
`FUN_002557C0` applies the alignment adjustment (`x0 = pen_x; align==2: x0 -= width/2;
align==3: x0 -= width-1`) *before* writing the box, so the frame's x0 for this case is the
*aligned* pen, not the raw one — `layout._update_frame` now replays that with
`metrics.aligned_x0`/`metrics.text_width` whenever the record carries a resolvable label.
Type 0 structurally may carry a label (its `aed` mask includes the label loop); type 8 never
does (it draws a runtime-computed number, no string in the file) — so a type-8 frame's width,
and a type-0 frame's width when a glyph doesn't resolve, stay a **bounded gap**: the old
geometry-box approximation, not an invented number.

`src/fireflymcf/layout.py`'s `_update_frame` and `_GENERIC_FRAME_TYPES` implement this
table exactly; `tests/test_layout.py`'s Task-1 tests pin each case (including the "no case"
branch) against synthetic records and, separately, audit every type actually emitted across
all three builds.

## Finding 22 — the glyph bitmap is 4bpp with a per-row vertical-repeat RLE

`proven-from-code`, 2026-08-15 (the proportional-glyph decode dig). The glyph blitter is
**`FUN_002510C0`** (called per character by the text drawers `FUN_002557C0` / `FUN_002559A0`).
It settles the "small proportional fonts decode to a fraction of their height" gap that
`glyphs.py` had carried as `unknown`: the gap was never corruption — the bitmap is **compressed**,
and `glyphs.py`'s flat decoder simply did not implement the compression.

A glyph record is: header (`+0xa` x, `+0xb` width, `+0xc` y, `+0xd` height), then a **per-row RLE
bitstream** at `+0xe` of `ceil(height/8)` bytes (1 bit per row, LSB-first, starting at **bit 1** of
byte `+0xe`), then the **pixel data** at `+0xe + ceil(height/8)`. The blitter walks rows: it reads a
run of set bits (terminated by a clear bit) to get a run length `n`, blits **one** stored pixel row,
and tells the DMA to repeat that row **`n` times vertically** (`puVar8[0x11] = n | 0x10000`). So a
vertically-uniform stroke (an `I` stem, an `H`/`T` bar, a serif) stores a single row and repeats it —
which is exactly why sparse/thin glyphs were tiny on disk and decoded short under a flat reader.

Pixel rows are **4bpp** (two nibbles per byte, low-nibble = left pixel) `ceil(width/2)` bytes wide when
the font is anti-aliased (`*(font+8) == 4`), else **1bpp** `ceil(width/8)` bytes wide (`(v>>(7-k))&1`).
Verified: transcribing the loop and decoding font 13 at 4bpp+RLE reproduces clean `I H T A E x o 0 8`
and the `%` (the `%` was the same RLE issue, not a separate corruption). Byte-accounting across fonts
11/13/15/17 confirms 4bpp (fonts 15/17 match byte-exact; 11/13 carry a couple trailing pad bytes);
getting the multi-byte RLE bit-consumption byte-exact for a tall glyph (a 4bpp font's rows exceeding
one RLE byte, e.g. slot 17's tallest decoded glyph at height 25) is the one precision item for the
decoder rewrite. **Correction:** an earlier draft of this paragraph listed slot 9 among the fonts
byte-accounting confirmed 4bpp, and used "slot 9, height 47" as the tall-glyph example — both are
wrong. The descriptor byte says otherwise (`font+8 == 1` there, `OFF_BPP`), so slot 9 decodes cleanly
only at **1bpp** (`glyphs.py`'s module docstring) and was never part of the 4bpp byte-accounting set;
its own glyphs top out at height 30 ('0'-'9'), not 47 — the "47" is the *font descriptor's own cell
height* (`metrics.font_metrics`, the `[0x07]` byte used for line-spacing/layout, `GUICODE.md` Finding
14(c)'s "the setpoint is slot 9, height 47"), a different field entirely from a glyph record's own
declared height (`+0xd`) this paragraph is about. This is the last piece for pixel-faithful
proportional text; the decoder rewrite (`glyphs.decode_glyph`) and the `is_reliable`/DejaVu fallback
retirement in `render.py` are both now done (the glyph-decoder SDD, Tasks 1–2).

## Finding 23 — the option → `0x15FCE` selector join: a literal template byte, joined by code adjacency

`proven-from-code` for the **mechanism**; `inferred-from-code` for the individual **Rule-B
attributions**; the two 14.7 rows are `inferred-from-code` and **unverified**. 2026-08-15
(the selector-join SDD). This closes the last open half of Finding 20 and rewrites
`MCF-FORMAT.md` §8.3 row 1 — which asked the wrong question, as explained at the end.

**The one-paragraph answer.** The panel writes an option by config-buffer **offset**
(Finding 20); the frame on the wire carries a **selector**. The selector is **never computed
and never looked up**: it is a literal byte inside one of Finding 7's static 12-byte CAN-frame
templates in GuiCode `.rodata` (13.6 `0x00259430..0x00259ACC`), copied wholesale by
`ldm {r0,r1,r2}` / `stm`, and joined to the offset **only by being in the same basic block** of
a per-row tap handler. There is no offset-indexed table anywhere — which is exactly why
Finding 20's search for one came back empty and was **right to**. Two mechanical rules recover
the relation from the bytes; they are shipped as `src/fireflymcf/config.py`.

### The recovery chain, with addresses

All addresses are mapped GuiCode addresses (section base `0x00240000`) unless stated.
Disassembly by a capstone Thumb linear sweep with a constant-register emulator, in the style
`theme.py` established; no Ghidra script was needed.

**1. The callback slots, and who installs them.** Finding 20's `0x20039558`/`0x20039560` are
**14.7** cells (see its correction block). The 13.6 equivalents are **`0x2003986C`** (frame
sender) and **`0x20039874`** (config-changed notify). Each has **exactly one store** in
GuiCode, in both builds:

| build | slot | store | source |
|---|---|---|---|
| 13.6 | `0x2003986C` | `0x00247AA2  str r2,[r3]` (`r3` = lit@`0x00247B28`) | `[r4,#0x08]`, loaded @`0x00247A9E` |
| 13.6 | `0x20039874` | `0x00247AAE  str r2,[r3]` (`r3` = lit@`0x00247B30`) | `[r4,#0x10]`, loaded @`0x00247AAA` |
| 14.7 | `0x20039558` | `0x00247B56  str r2,[r3]` (`r3` = lit@`0x00247BDC`) | `[r4,#0x08]`, loaded @`0x00247B52` |
| 14.7 | `0x20039560` | `0x00247B62  str r2,[r3]` (`r3` = lit@`0x00247BE4`) | `[r4,#0x10]`, loaded @`0x00247B5E` |

The enclosing function opens `push {r4,lr}; mov r4,r0` at **`0x00247A90`** (13.6) /
`0x00247B44` (14.7) and copies five words out of the `r0` struct into five SRAM slots. That
function is **`GuiCode_entry0`**: the section header `+0x0C` holds `0x00247A91` (13.6) /
`0x00247B45` (14.7), the same address with the Thumb bit. So the host hands GuiCode a
five-function API table at entry, slot `+0x08` being the CAN sender (Finding 4, amended).

**2. The config-set helper, located structurally.** Not by any known pair: the only site in
13.6 that materialises the pseudo-PGN `0x1EE00` with a `mov.w` and dereferences the config
buffer at `[r0,#0x214]`. **`FUN_00259100`**, the exact analogue of 14.7's `FUN_0025b9b0`:

```
0x00259100  cmp    r1,#0xf9              ; offset bound
0x00259102  bhi    0x259176              ; -> return 0
0x00259104  ldr.w  r3,[r0,#0x214]        ; the config buffer
0x00259108  ldrb   r3,[r3,r1]
0x0025910A  cmp    r3,r2                 ; unchanged?
0x0025910C  beq    0x25917a              ; -> return 2
...
0x0025911E  mov.w  r1,#0x1ee00           ; the pseudo-PGN
0x00259122  bfi    r3,r1,#8,#0x12
0x0025913C  strb.w r4,[sp,#8]            ; offset_lo
0x00259142  strb.w r3,[sp,#9]            ; offset_hi
0x00259146  strb.w r5,[sp,#0xa]          ; value
0x0025914A..0x25915C                     ; FF x5
0x00259160  ldr    r3,[pc,#0x20]         ; = 0x2003986C  (lit@0x00259184)
0x00259162  ldr    r3,[r3]
0x00259166  blx    r3                    ; sender(&frame)
0x00259168  ldr    r3,[pc,#0x1c]         ; = 0x20039874  (lit@0x00259188)
0x0025916A  ldr    r3,[r3]
0x00259170  blx    r3                    ; notify(offset, value)
```

The message it builds is a **12-byte record** — `<u32 CAN id><8 data bytes>`. That shape is
the key: it is Finding 7's template shape.

**3. Where the selector byte enters the frame — it never does.** The emit idiom is a wholesale
copy of a pre-built template (Finding 7's send primitive, here at the Bath Fan row):

```
0x0024CA90  add    r3,sp,#4
0x0024CA92  ldr    r2,[pc,#0x140]        ; = 0x002596A0  (lit@0x0024CBD4)
0x0024CA94  ldm    r2,{r0,r1,r2}         ; 12 bytes of frame template
0x0024CA96  stm.w  r3,{r0,r1,r2}
0x0024CA9A  ldr    r2,[pc,#0x130]        ; = 0x2003986C  (lit@0x0024CBCC)  the sender slot
0x0024CA9C  ldr    r2,[r2]
0x0024CAA0  blx    r2
```

and the record at `0x002596A0` is, verbatim:

```
00 d9 fe 19 | ff 96 e9 0f 01 00 d1 ea
^ 0x19FED900  ^ the wire payload, byte 2 = the selector 0xE9
```

Byte 2 of the outgoing payload is a **literal `.rodata` byte**. No offset-indexed load, no
arithmetic, no table walk. 13.6's array spans `0x00259430..0x00259ACC` (Finding 7's pool). Its
`0x1FED9` group-`0x96` templates, counted here directly, are **118 records over 46 distinct
selector bytes** — 43 appearing in a write form (`… 0f <val> 00 d1 ea`) and 40 in the
`FF 96 <sel> 0F FF FF D3 FF` read-back form `BUS-BASELINE` PC.2 Result 3 documents. 14.7: 131
records, 50 selectors (46 write / 44 read-back). 11.23: 86 records, 39 selectors (27 / 33).
Alongside them sit `0x1FEDB` dimmer commands, `0x1FACE`, `0x1FBDA`, `0x0EAFF` requests and the
`0x19EE0000` config messages, all in the same 12-byte shape.

**4. The join itself.** A per-row handler does both halves in one straight-line run — 13.6's
Bath Fan row (cfg `0x0D`):

```
0x0024CA80  ldr    r3,[pc,#0x14c]   ; = 0x20039B49  (lit@0x0024CBD0)  Bath Fan's SRAM register
0x0024CA82  ldrb   r3,[r3]
0x0024CA84  cbnz   r3,0x24cab8      ; currently set? -> the "0" arm
0x0024CA86  movs   r2,#1            ; new value
0x0024CA88  movs   r1,#0xd          ; ** cfg offset 0x0D **
0x0024CA8C  bl     0x259100         ; config_set(state, 0x0D, 1)
0x0024CA92  ldr    r2,=0x002596A0   ; ** template carrying selector 0xE9 **
0x0024CAA0  blx    r2               ; send
0x0024CAA4  ldr    r2,=0x002596B8   ; the E9 read-back
0x0024CAB2  blx    r2
```

The offset is a literal immediate; the selector is a literal `.rodata` byte; **nothing joins
them but adjacency**. That is why no stored reference was ever found, and it is the same shape
as the control→status-variable negative (`MCF-FORMAT.md` §10 item 11a) — a positional join
with no field to point at.

### Why "the framer leaves the container" is ruled out

The sender pointer is installed from **outside** GuiCode (step 1), so in principle the LCD
Firmware could rewrite the payload in transit. It does not, and the argument is a byte
comparison rather than an absence:

```
template  0x002596A0            :  00 d9 fe 19 | ff 96 e9 0f 01 00 d1 ea
wire      BUS-BASELINE.md:1015  :  19FED99F    # FF 96 E9 0F 01 00 D1 EA
```

The **only** difference anywhere in the record is the CAN id's source-address byte — stored
`0x19FED900`, transmitted `0x19FED99F`, the panel's own address `0x9F` and the one field the
host must fill in. Every payload byte, selector included, is unchanged. The host callback is a
**transport, not a framer**, and the selector is fully determined inside the container.

**The LCD Firmware sweep is the weaker argument, and is not what settles it.** Two sweeps of
`00-LCD_Firmware.bin` (base `0x00210000`) return **zero** hits: (a) a raw 4-byte-word search
for `0x20039558`/`0x20039560`, i.e. the literal-pool form; (b) a linear Thumb sweep with a
constant-register emulator resolving PC-relative literal loads, `mov`/`movs`/`mov.w`/`movw`
immediates, `movt` pairs and `add rd,rn,#imm` from a concrete base, computing the effective
address of every memory operand with a concrete base and no index register, over
`0x20039000..0x2003A800`. **This is not a proof that the LCD Firmware never touches those
cells** — an access whose base came from a struct field, a register-indexed form, or a pointer
passed in as an argument is invisible to sweep (b). The honest claim is only: *no literal-pool,
`movw`/`movt` or statically-resolvable effective-address reference to GuiCode's SRAM window
exists in the LCD Firmware section.* Sweep (b) over **GuiCode** across the same window returns
hundreds of hits, so the method is not blind, and the result is consistent with Finding 6's
earlier negative (the `FF 96` / `D1 EA` frame bytes are absent from the LCD Firmware). But the
byte-identity check above is what rules the alternative out.

### The two rules, and their honest weight

- **Rule A (adjacency).** At each `bl <config_set>`, read `r1` (the offset) and take the first
  `ldm <concrete>,{r0,r1,r2}` in the run whose target holds a `0x19FED900` record, stopping at
  the next `bl`. `config.py` tightens this with a value check: the row's frame is the first
  write template in the run whose payload **value equals `r2`** (the value being written).
- **Rule B (gate register).** Rows that are bulk-saved rather than saved per tap have no `bl`
  to hang off; the handler still **gates on the option's own SRAM register**, and
  `config.config_offsets` maps that register back to its cfg offset. Take the first template
  **strictly after** the gate load, stopping at the next `bl`.

  **Rule B is forward-only, and that is load-bearing** — see false-positive class (i) below.

Confidence classes used in the table:

- **A2** — Rule A, and **both arms agree**: the same cfg offset appears at two `bl` sites with
  opposite values, each emitting a template carrying the same selector with the matching value.
  The value correspondence makes the pairing self-checking.
- **A1** — Rule A, single site. Adjacency is cited; nothing cross-checks it.
- **B+** — Rule B **plus something independent**: a value correspondence, or both arms of the
  toggle emitting the same selector with opposite values, or external corroboration (a capture,
  or the same pair recovered by Rule A in another build).
- **B0** — Rule B by **position alone**. Weakest class. **No row below is B0** — every Rule-B
  row that survived has either both toggle arms agreeing or a value correspondence, which is
  what makes the rule usable at all.

| cfg | rule | cited instruction | selector | oracle | class |
|---|---|---|---|---|---|
| `0x00` | A (11.23) | `bl` @`0x0024B000`, tmpl `0x002571A4` | `0xF5` | `0xF5` ✅ | **A1** (11.23 only; 13.6/14.7 route `0x00` through the bulk-save block) |
| `0x0A` | A | `bl` @`0x0024BA90` (v=1) tmpl `0x00259688`; `bl` @`0x0024CA4E` (v=0) tmpl `0x00259670` | `0xEB` | `0xEB` ✅ | **A2** |
| `0x0D` | A | `bl` @`0x0024CA8C` (v=1) tmpl `0x002596A0`; `bl` @`0x0024CABE` (v=0) tmpl `0x002596AC` | `0xE9` | `0xE9` ✅ | **A2** |
| `0x0E` | A | `bl` @`0x0024CAE4` (v=1) tmpl `0x002596DC`; `bl` @`0x0024CB1C` (v=0) tmpl `0x002596C4` | `0xEC` | `0xEC` ✅ | **A2** |
| `0x0F` | A | `bl` @`0x0024CB5C` (v=1) tmpl `0x00259700`; `bl` @`0x0024CB94` (v=0) tmpl `0x002596E8` | `0xEF` | — | **A2** |
| `0x10` | A | `bl` @`0x0024CBF6` (v=1) tmpl `0x00259724`; `bl` @`0x0024CC2E` (v=0) tmpl `0x0025970C` | `0xE8` | — | **A2** |
| `0x11` | A | `bl` @`0x0024BC44` (v=1) tmpl `0x00259760`; `bl` @`0x0024CCC2` (v=0) tmpl `0x00259778` | `0xE5` | `0xE5` ✅ | **A2** |
| `0x13` | B | gates @`0x0024D00E` / `0x0024D106` / `0x0024BF76` (reg `0x2003A38C`) | `0xEF` | — | **B+** (three-way value correspondence, below) |
| `0x17` | B | gate @`0x0024CCF6` (reg `0x2003A62B`), tmpl `0x00259784` | `0xE6` | — | **B+** (bus, below; and 14.7 Rule A agrees) |
| `0x19` | B | gate @`0x0024CF2A` (reg `0x20039EBD`), tmpl `0x0025985C` (`E4:=1`); other arm @`0x0024CF9A` tmpl `0x00259814` (`E4:=0`) | `0xE4` | `0xE4` ✅ | **B+** (both arms) |
| `0x1A` | B | gate @`0x0024CE0C` (reg `0x20039CF8`), tmpl `0x00259808` (`E3:=1`); other arm @`0x0024CE7C` tmpl `0x0025982C` (`E3:=0`) | `0xE3` | `0xE3` ✅ | **B+** (both arms) |

**All 7 of Finding 20's oracle pairs reproduce, none of them searched for.** The split, stated
exactly, because a coarser statement of it was wrong once already (an earlier draft said "Rule
A found six of the seven", conflating *offsets recovered* with *oracle pairs recovered*):

| | oracle pairs recovered |
|---|---|
| **Rule A on 13.6** | **4** — `0x0A`, `0x0D`, `0x0E`, `0x11` |
| **Rule B on 13.6** | **2** — `0x19`, `0x1A` |
| **Rule A on 11.23** | **1** — `0x00` |

Rule A on 13.6 recovers **six offsets** (`0x0A`, `0x0D`, `0x0E`, `0x0F`, `0x10`, `0x11`), but
only four are oracle pairs; `0x0F`→`0xEF` and `0x10`→`0xE8` are new and unverified. So **the
strong rule carries four of the seven, and two of the seven — including both rows whose shape
the 14.7 prediction reuses (`0x19`, `0x1A`) — rest on the weaker Rule B.** That is the honest
weight, and it is the reason the 14.7 rows below are graded as inference.

### Out-of-sample corroboration

**cfg `0x17` → `0xE6`, from the bus.** `BUS-BASELINE.md` **PC.3 Result 2**
(`docs/BUS-BASELINE.md:226-234`, *"options have dependencies: `E6` forces `EF` = AGS"*) records
at t=69.2:

```
FF 96 E6 0F 01 00 D1 EA     progressive inverter = 1
FF 96 EF 0F 01 00 D1 EA     gen/AES mode        = 1 (AGS)
```

Rule B independently yields cfg `0x17` (Progressive Inverter) → `0xE6`. **This is one
confirmation, not two.** The second frame is *not* an independent check on cfg `0x13`: the code
shows both frames come from the **same** straight-line run in the cfg-`0x17` handler (gate
`0x0024CCF6`, then `0x00259784` = `E6:=1`, then `0x00259700` = `EF:=1`) — which is precisely
the dependency the capture is titled after. It corroborates `0x17` and says nothing independent
about `0x13`. (It does, though, **locate** the dependency, confirming what Finding 19 predicted
without being able to point at it: Finding 19 established that AGS and PI are each written once
from independent payload bytes and that "the auto-select is enforced by whatever builds the
config message (the OPTIONS tap → CAN transmit), not by a GuiCode data dependency". That is
exactly right, and it is here — **two literal sends in one basic block** of the cfg-`0x17` tap
handler.)

**cfg `0x13` → `0xEF`, by three-gate value correspondence.** This is the genuinely independent
argument. The register `0x2003A38C` (AGS/AES) is read as a gate at three separate sites, each
comparing it against a different constant and each emitting `EF` with **the matching value**:

| gate | compare | template | payload |
|---|---|---|---|
| `0x0024D00E` | `cmp r3,#1` (`0x0024D010`) | `0x00259700` | `FF 96 EF 0F 01 00 D1 EA` — `EF := 1` |
| `0x0024D106` | `cmp r3,#2` (`0x0024D108`) | `0x00259898` | `FF 96 EF 0F 02 00 D1 EA` — `EF := 2` |
| `0x0024BF76` | `cmp r3,#3` (`0x0024BF78`) | `0x002598A4` | `FF 96 EF 0F 03 00 D1 EA` — `EF := 3` |

Three gates, three constants, three templates, and the emitted value equals the compared
constant every time. That 1/2/3 range is exactly rvc2mqtt's `EF` enum — `1: AGS`, `2: AES`,
`3: AES Single Output` — quoted in `BUS-BASELINE.md` **PC.3 Result 3**
(`docs/BUS-BASELINE.md:243`). A positional coincidence cannot reproduce a three-way
value-to-constant correspondence that also matches an externally documented enum. This is why
`0x13` is **B+** and not **B0**. *(The shipped extractor keeps only the middle gate
`0x0024D00E` — its dispatcher-containment filter drops the other two. Those two are **true
sites**, and losing them is a real cost, not a tightening; the argument above is the evidence
of record, not the extractor's single gate. 14.7 recovers all three, `cmp #1/#2/#3` at
`0x0024C2A4`/`0x0024D52E`/`0x0024D5DE` — plus a fourth gate at `0x0024D6BE` that compares `#1`
again, so "one gate per enum value" is the pattern, not a rule.)*

**The strongest single confirmation — the Bath/Cargo radio pair, frame for frame.**
`BUS-BASELINE.md` PC.2 Result 2 captured that row as a radio pair: at t=27.2 `E4=1` **and**
`E3=0`; at t=35.5 `E3=1` **and** `E4=0`. The disassembly reproduces exactly that, both arms.
Each handler opens with a **floorplan** guard and only then loads the option register — two
different `ldr`s, two different literals, transcribed in full because this is the passage that
carries the `proven-from-code` label:

```
Bath Light row (cfg 0x19):
  0x0024CF1C  ldr  r3,[pc,#0x2b8]   ; lit@0x0024D1D8 = 0x2003A77A   <- FLOORPLAN, not the option
  0x0024CF1E  ldrb r3,[r3]
  0x0024CF20  subs r3,#4 ; uxtb r3,r3 ; cmp r3,#1 ; bls 0x24cfc6    (floorplan in {4,5} -> other path)
  0x0024CF28  ldr  r3,[pc,#0x2b0]   ; lit@0x0024D1DC = 0x20039EBD   <- the Bath Light register
  0x0024CF2A  ldrb r3,[r3]
  0x0024CF2C  cmp  r3,#0 ; bne 0x24cf9a
    arm reg==0 : 0x0025985C = E4:=1 , 0x0025982C = E3:=0 , 1FEDB inst 0x19, then read-backs
    arm reg!=0 (0x0024CF9A) : 0x00259814 = E4:=0 , 0x00259820 = 1FEDB inst 0x1A

Cargo Light row (cfg 0x1A):
  0x0024CDFE  ldr  r3,[pc,#0xe4]    ; lit@0x0024CEE4 = 0x2003A77A   <- FLOORPLAN, not the option
  0x0024CE00  ldrb r3,[r3]
  0x0024CE02  subs r3,#4 ; uxtb r3,r3 ; cmp r3,#1 ; bls 0x24cea8
  0x0024CE0A  ldr  r3,[pc,#0xdc]    ; lit@0x0024CEE8 = 0x20039CF8   <- the Cargo Light register
  0x0024CE0C  ldrb r3,[r3]
  0x0024CE0E  cmp  r3,#0 ; bne 0x24ce7c
    arm reg==0 : 0x00259808 = E3:=1 , 0x00259814 = E4:=0 , 1FEDB inst 0x1A, then read-backs
    arm reg!=0 (0x0024CE7C) : 0x0025982C = E3:=0 , 0x00259838 = 1FEDB inst 0x19
```

Both sets are sent first and the read-backs afterwards — PC.2 Result 3's exact observation, now
`proven-from-code`. (An earlier draft of this snippet gave `0x0024CF1C`/`0x0024CDFE` as loading
the option registers. They load the **floorplan** register; the option loads are two
instructions later. The recovery table's rows were always right; only the illustration was not.
Recorded because the correction is the kind this project keeps having to make.)

### Handler identity is build-stable

The tap dispatcher compares a 16-bit action id. Recovered:

| id | 13.6 handler | 13.6 cfg / sel | 14.7 handler | 14.7 cfg / sel |
|---|---|---|---|---|
| `0x844` | `0x0024CA80` (branch @`0x0024BA78`) | `0x0D` / `0xE9` | *(absent)* | — |
| `0x856` | `0x0024CDFE` (branch @`0x0024BB64`) | `0x1A` / `0xE3` | `0x0024D3F2` (branch @`0x0024C284`) | `0x1A` / `0xE3` |
| `0x855` | *(absent)* | — | `0x0024C37E` (branch @`0x0024C374`) | `0x1B` / **`0xE4`** |
| `0x85B` | *(absent)* | — | `0x0024D68C` (branch @`0x0024BFCA`, gate `ldrb` @`0x0024D68E`) | `0x14` / **`0xE7`** |

Id `0x856` is the same row, same cfg offset and same selector in both builds — the ids are
build-stable — and `0x855`/`0x85B` are genuinely new rows in 14.7 (13.6 tests neither id
anywhere in its dispatcher).

One structural note the trace did not have to solve and the extractor did: 13.6's dispatcher is
a **binary search**, not a chain of equalities, so a handler can be reached by a *range*:

```
0x0024BB5E  movw   r3,#0x856
0x0024BB62  cmp    r2,r3
0x0024BB64  beq.w  0x24cdfe        <- Cargo Light  (cfg 0x1A)
0x0024BB68  bhi.w  0x24cf1c        <- Bath Light   (cfg 0x19), by RANGE
```

"Handler = target of an id equality" would lose one of the seven oracle pairs.

### The two 14.7 rows — derived, and not verified

Applying Rule B to 14.7's two new OPTIONS rows:

```
14.7 dispatcher id 0x855:
0x0024C374  movw r3,#0x855 ; cmp r2,r3 ; bne.w 0x24b41e
0x0024C37E  ldr  r3,[pc,#0x1f8]   ; = 0x200396FE  (lit@0x0024C578)  = cfg 0x1B
0x0024C380  ldrb r3,[r3]
0x0024C384  cmp  r3,#0 ; bne.w 0x24d3c4
  arm reg==0 : 0x0025C0D8 = FF 96 E4 0F 01 00 D1 EA   (E4 := 1) ; 0x0025C0E4 read-back
  arm reg!=0 (0x0024D3C4) : 0x0025C0F0 = FF 96 E4 0F 00 00 D1 EA   (E4 := 0)

14.7 dispatcher id 0x85B:
0x0024BFC4  movw r3,#0x85b ; cmp r2,r3 ; beq.w 0x24d68c
0x0024D68C  ldr  r3,[pc,#0xa4]    ; = 0x2003A009  (lit@0x0024D734)  = cfg 0x14
0x0024D68E  ldrb r3,[r3]
0x0024D690  cbnz r3,0x24d6f0
  arm reg==0 : 0x0025C18C = FF 96 E7 0F 01 00 D1 EA   (E7 := 1) + read-back 0x0025C138
  arm reg!=0 (0x0024D6F0) : 0x0025C12C = FF 96 E7 0F 00 00 D1 EA   (E7 := 0)
```

giving **cfg `0x1B` (Starlink Power) → `0xE4`** and **cfg `0x14` (Lithionics) → `0xE7`**.

The `register → row` half is **attested twice, from two sections, by two tools sharing no code
path**: `config.config_offsets` reads GuiCode's config-unpack (`0x1B → 0x200396FE`,
`0x14 → 0x2003A009`, matching Finding 20's own table), and `options.option_bindings` reaches the
same two registers from **GuiData** through Finding 17's value-slot tables — and supplies the
names `Starlink Power` and `Lithionics Batteries`, which `config.py` cannot. The weak link is
only the `handler → selector` half.

**Derivation status and verification status are two axes, and must not be conflated:**

| | `0x19`→`0xE4`, `0x1A`→`0xE3` (13.6) | `0x1B`→`0xE4`, `0x14`→`0xE7` (14.7) |
|---|---|---|
| **derivation** | Rule B, **B+** (both arms emit the same selector with opposite values) | Rule B, **B+** (both arms likewise) — *the same rule, the same shape* |
| **verification** | **confirmed** — against Finding 20's oracle *and* `BUS-BASELINE.md` PC.2 Result 2, frame for frame | **none.** No oracle entry, no capture |

So: derived identically, **verified not at all**. The 14.7 rows are the *output* of a rule
validated elsewhere; they are not themselves validated. What *is* settled is the incumbent's
status — Finding 20's `0x1B → 0xE7` is **retracted** (see that finding), because its elimination
rested on a premise the code contradicts. Retracting it does not promote `0xE4`; it leaves
`0xE4` as the best-supported candidate and `0x1B` still unverified. **Toggle Starlink Power on
a 14.7 panel and diff the `0x1FED9` write before treating either row as fact.**

> **THE JOIN IS DIRECTLY OBSERVABLE ON THE BUS — `BUS-BASELINE.md` PC.34, 2026-08-24.**
>
> A single-row OPTIONS tap emits the config-buffer **offset** on `0x1EE00` and the **selector** on
> `0x1FED9` about **0.5 ms apart, carrying the same value**:
>
> ```
> 500.969673  19EE009F#110000FFFFFFFFFF     cfg offset 0x11 := 0
> 500.970223  19FED99F#FF96E50F0000D1EA     selector  0xE5 := 0
> ```
>
> The owner swept **all six** of 13.6's option rows. Every pair reproduces
> `config.selectors_by_offset` exactly — `0x0A→0xEB`, `0x0D→0xE9`, `0x0E→0xEC`, `0x11→0xE5`,
> `0x19→0xE4`, `0x1A→0xE3`. Nothing in the capture was used to derive that map and nothing in the
> derivation reads a capture.
>
> **This retires "the weak link" for 13.6.** The `handler → selector` half no longer rests on code
> adjacency: the join can be read off a capture with no disassembly at all, and every attribution
> that came from the weaker Rule B is now confirmed alongside the Rule A ones. Wire verification
> previously covered **two** rows (`E3`/`E4`, PC.2); it now covers six of six.
>
> **It does not touch the 14.7 rows.** Those remain derived-but-unverified, and the instruction
> above is unchanged — except that it is now cheaper than it sounds: toggling the row and diffing
> the `1FED9` write is one half of it, and the `1EE00` frame beside it gives the offset for free,
> so a single tap on a 14.7 panel settles `0x1B` and `0x14` outright.
>
> **The pairing is only visible on a single-row tap, which is why eleven earlier captures missed
> it.** `0x1EE00` frames appear in ten of them (Finding 50 cites two), but in a floorplan *burst*
> the panel sends twelve `1FED9` writes as one block and five `1EE00` frames as a separate later
> block, shorter and differently ordered — `1FED9` writes every selector in the profile while
> `1EE00` reports only the offsets that actually **changed**. Nothing pairs there. The two are not
> 1:1 in the other direction either: a bouncing tap produced three `1EE00` frames in 15 ms against
> one `1FED9`, so the buffer can move without a write following.

Cross-build support for the rule itself: 14.7's **Rule A** — the strong rule — directly yields
cfg `0x17` → `0xE6` (`bl` @`0x0024D284` v=1 tmpl `0x0025C054`; `bl` @`0x0024D2BC` v=0 tmpl
`0x0025C06C`), an **A2** result, where 13.6 reaches the same pair only through Rule B. A Rule-B
result in one build reproduced by Rule A in another is direct evidence that Rule B is sound
where it fires — which is exactly the assumption the 14.7 rows depend on.

### The three false-positive classes

All three are real; all three were hit while deriving this finding. Any reimplementation must
guard them or restrict itself to Rule A plus an explicit dispatcher-id list.

- **(i) Backward bleed.** Scanning *backwards* from the gate load reaches the **previous**
  handler's emit sequence, which belongs to a different row. On 13.6 this produced an
  **oracle-contradicting** result: cfg `0x11` (Bunk Accent, register `0x20039E70`, gate load
  `0x0024BC36`) picked up templates `0x00259730`/`0x0025973C` behind it and reported selector
  **`0xEA` where the oracle says `0xE5`**. This was a discrepancy between the stated rule and
  the implemented one — the prose always said "first template *after* the gate load"; the first
  code scanned both ways. Forward-only returns nothing at that site, which is correct: cfg
  `0x11` is a Rule-A row (`bl` @`0x0024BC44`, template `0x00259760` → `0xE5`).
- **(ii) Gate-of-another-row.** An option register is also read as a plain guard by handlers
  belonging to *other* rows — 13.6's floorplan register `0x2003A77A` is read at `0x0024CE00`,
  two instructions into the **Cargo Light** handler, and a naive reading hands cfg `0x00` the
  selector `0xE3`. Only the option register's own load inside its own handler is evidence, not
  the floorplan guard that precedes it. (Measured in the shipped extractor: relaxing both the
  raw-test break and the equality restriction together makes cfg `0x00` → `{0xE3, 0xE4}`
  reappear. On this corpus the equality restriction alone already rejects it, since
  `cmp r3,#1 ; bls` is a *range* test — so the two halves are not separately falsifiable here,
  and are not claimed to be.)
- **(iii) Unrelated later read.** 14.7 cfg `0x14` yields a second hit (gate `0x0024E6D4`,
  tmpl `0x0025C270`, sel `0x0C`) from a read of the same register far outside its own handler.
  Only the hit inside the dispatcher-id handler is the row's selector, and — measured — the
  thing that rejects this one is the **value check** (does the emitted value change on that
  arm), not handler containment: `0x0024E6D4` sits two bytes past a handler prologue, so
  containment *accepts* it.

### Residuals and honest limits

1. **The map is not injective.** cfg `0x0F` and cfg `0x13` both emit `0xEF` in 13.6, and the
   Bath/Cargo pair shows one handler writing two selectors. "cfg offset → selector" is a
   **relation, not a function**; `config.selector_for_offset` returns a `frozenset`, and 11.23
   cfg `0x0F` already returns `{0xF0, 0xF5}` — reported as a disagreement, not resolved.
2. **cfg `0x00` → `0xF5` on 13.6/14.7 is weaker than the rest.** It comes from 11.23 adjacency
   plus the value pattern (`0xF5` is written with values `0..0x0A` from a family of
   one-handler-per-floorplan sites, e.g. `0x0024BCE4`, `0x0024C06C`, `0x0024C2F8`, …), not from
   a 13.6 adjacency — 13.6 routes `0x00` through the bulk-save block. Solid, but the 13.6
   citation is weaker than the others.
3. **The bulk-save block** (13.6 `0x00249E4E..0x00249EC6`; 14.7 `0x0024A27E..0x0024A2F6`) is a
   run of tail-call thunks pushing every option to the config buffer with a literal offset per
   option — an independent, complete enumeration of the option set and a free cross-check on
   `config_offsets`. It emits **no frames**, which is why the rows it owns need Rule B.
4. **11.23's option/selector layout differs substantially** and was not chased (see Finding
   20's scoping correction). Beyond `0x00 → 0xF5` and `0x0A → 0xEB` its offsets do not line up
   with 13.6/14.7.
5. **The extractor's dispatcher-containment filter is inert on this corpus.** Measured across
   all three builds: switching it off changes **no selector anywhere**; it only admits
   additional *same-selector* sites, and it rejects **no** false positive — including class
   (iii), which the value check rejects. It is not free either: it costs two cited true sites
   (13.6's `cmp #3` and `cmp #2` AGS gates). Kept as a build-generality guard, documented as
   inert. **The docs cite the three-gate argument above, not the extractor's surviving gate.**
6. **This finding's 14.7 reading undercuts selector *build*-stability — inference-grade,
   added review fix wave 2.** `RVC-GAPS.md` §2 advises rvc2mqtt's maintainer to keep
   `rvc-spec.yml`'s assumption that a selector means the same thing everywhere, and to keep the
   note that it is an assumption. That advice still stands — but this finding is a reason to
   weight the note, and "selectors are build-stable (`RVC-GAPS` Result 1)" is the very premise
   Finding 20's now-retracted elimination leaned on. On the reading above, **`0xE4` is Bath
   Light in 13.6 (cfg `0x19`) and Starlink Power in 14.7 (cfg `0x1B`)**, and 14.7's
   config-unpack resolves **neither** cfg `0x0D` nor cfg `0x19` (`config.config_offsets`,
   measured — 14.7's eighteen offsets are `0x00 0x02 0x0A 0x0B 0x0E 0x0F 0x11 0x12 0x13 0x14
   0x17 0x18 0x1A 0x1B 0x1C 0x1D 0x29 0x2A`), so both 13.6 options that could have carried
   `0xE4` forward are absent from that build and the byte was genuinely freed and reused.
   **This is inference, not a measurement of the wire**: the two 14.7 rows are Rule-B results
   with no oracle and no capture (see "The two 14.7 rows" above), which is exactly why it
   belongs *beside* the "keep the assumption" advice rather than replacing it. It is also why
   Finding 20's 11.23 correction can no longer say which side of the `(offset, option)`
   pairing moved.

### The tool

`src/fireflymcf/config.py`, both halves build-general (no hardcoded function addresses):

```
config_offsets(data)          -> {cfg offset: SRAM register}         # Finding 20's schema
selector_sites(data)          -> {cfg offset: (SelectorSite, ...)}   # the cited evidence
selectors_by_offset(data)     -> {cfg offset: frozenset[int]}
selector_for_offset(d, off)   -> frozenset[int]
option_selector(d, off)       -> int | None
```

and `guidata.find_option_bindings` now returns `OptionBinding(name, selector)` by chaining
`options.option_bindings` (GuiData: name → slot → register) → `config.config_offsets` (GuiCode:
register → cfg offset) → `config.option_selector` (GuiCode: cfg offset → selector). Two of that
test file's four strict `xfail`s closed. The other two stand: the `E5`/`EC` visibility flag has
not been located at all, and the `E3`/`E4` channel binding now has a **cited candidate** (the
by-product section below) that **no code reads** — so `OptionBinding.channel` stays `None` and
the `xfail` stays strict until an extractor and a test exist.

### The record side: the OPTIONS row's closing u16 *is* the tap action id

**`inferred-from-code` for all six rows; derived 2026-08-15 while writing this finding, not part
of the trace it transcribes, and no tool reads it yet.** It is recorded here because it bears
directly on the §8.3 row this finding rewrites.

**Why not `proven-from-code` — the missing link, stated up front** (regrade, review fix round
1). The dispatcher does not read the record. It reads a **GuiCodeState field**: the id arrives
as `ldrh.w r2,[r4,#0x208]` at **`0x0024B29E`**, and *nothing below cites the code that copies a
record's `+0x0E` u16 into `state+0x208`*. A short sweep for a `strh` to `[rX,#0x208]` found
none, and that sweep is not exhaustive. So what the equality compares prove is **what the
dispatcher does with id `0x844`** — not that the record byte *is* that id. Under this project's
own rule (`CLAUDE.md`: "`proven-from-code` requires a cited instruction") the identification
*record field == action id* cannot be `proven-from-code` for **any** row: there is no
instruction to cite for the copy. *(This passage used to add "…nothing inside GuiData
qualifies, because GuiData's parser has never been located". That clause was removed from
`CLAUDE.md` on 2026-08-18 — parsers **have** been located, this document's own Findings 10–12
among them — and the argument here never needed it. What blocks the grade is the specific
missing `strh` to `state+0x208`, not a blanket rule about GuiData.)* Equality-vs-range survives below as a
**sub-gradation within the inference**, not as a confidence label. Finding the `state+0x208`
writer would promote the whole table at once.

`MCF-FORMAT.md` §6.1 records that each OPTIONS row's `0x0C` record closes with a u16, that on
the OPTIONS screen those u16s are `0x0843, 0x084B, 0x0844, 0x0856, 0x0857, 0x0845`, and that
what they mean is `unknown` (§10 item 8; §8.2's `find_control_groups` resolves them into the
3295-entry image table by a **disclosed range heuristic**, not a decoded discriminator). They
are the **dispatcher action ids** this finding traces. Read out of 13.6 directly — each row's
16-byte `0c 55 0d 00 00 00 00 00 dd ff e6 00 32 00 <u16>` record follows its own label, the
u16 sitting at record `+0x0E`:

| display order | row | closing u16 (file offset) | how the dispatcher reaches it | handler | cfg | selector | answer key |
|---|---|---|---|---|---|---|---|
| 1 | Heat Pump | `0x0843` (`0x099FBC`) | subtree fallthrough below `cmp #0x844` @`0x0024BA76` | `0x0024BA80` | `0x0A` | `0xEB` | `0xEB` ✅ |
| 2 | Bunk Accent | `0x084B` (`0x09A007`) | `movw r3,#0x84b` @`0x0024BC2A`, `bne.w` away | `0x0024BC34` | `0x11` | `0xE5` | `0xE5` ✅ |
| 3 | Bath Fan | `0x0844` (`0x09A04F`) | `movw r3,#0x844` @`0x0024BA72`, `beq.w` | `0x0024CA80` | `0x0D` | `0xE9` | `0xE9` ✅ |
| 4 | Cargo / Bath Light | `0x0856` (`0x09A0AE`) | `movw r3,#0x856` @`0x0024BB5E`, `beq.w` | `0x0024CDFE` | `0x1A` | `0xE3` | `0xE3` ✅ |
| 5 | Bath Light | `0x0857` (`0x09A0F8`) | `bhi.w` @`0x0024BB68` (id > `0x856`) | `0x0024CF1C` | `0x19` | `0xE4` | `0xE4` ✅ |
| 6 | Black Tank | `0x0845` (`0x09A142`) | `bhi.w` @`0x0024BA7C` (id > `0x844`) | `0x0024CAD6` | `0x0E` | `0xEC` | `0xEC` ✅ |

Each handler's cfg offset is the `movs r1,#<off>` immediately before its `bl 0x259100` —
`#0xa` @`0x0024BA8C`, `#0x11` @`0x0024BC40`, `#0xd` @`0x0024CA88`, `#0xe` @`0x0024CAE0`; rows 4
and 5 are Rule-B rows and take their offsets from the gate registers cited above.

**Sub-gradation, kept apart from the result.** All six are `inferred-from-code` for the reason
above; within that, they are not equally tight.

* **Rows 2, 3 and 4** are reached by an **equality compare on the row's own u16** — the
  dispatcher side is a cited instruction and admits exactly one id.
* **Rows 1 and 5** are reached by a **range branch**, but the interval is a **singleton**, so
  they are as tight as the equality rows in practice (this is a correction of an earlier draft
  that graded them as merely "in the branch's interval" — conservative, not wrong):
  * row 1 — `movw #0x842` @`0x0024BA66` with `bls` @`0x0024BA70` sending everything `≤ 0x842`
    away, and `cmp #0x844` @`0x0024BA76` taking `0x844` and above. The fallthrough at
    `0x0024BA80` is reachable only by **`0x843`**.
  * row 5 — `cmp #0x856` @`0x0024BB62` with `bhi` @`0x0024BB68`, inside the subtree entered
    below `movw #0x858` @`0x0024BB38`, whose `beq.w` @`0x0024BB3E` takes `0x858` itself and
    whose `bhi.w` @`0x0024BB42` sends everything above it elsewhere — so the subtree holds only
    ids `< 0x858`. Only **`0x857`** reaches `0x0024CF1C`.
* **Row 6** is the one genuinely loose case: `bhi` @`0x0024BA7C` gives `> 0x844` with the upper
  bound set by an ancestor that was not chased, so the interval was not shown to be a singleton.
  Its attribution rests on the row's u16 lying in that interval and on the handler writing Black
  Tank's own cfg offset (`movs r1,#0xe` @`0x0024CAE0`).

**The selectors were not used to make any of these assignments**; they are the check, and all
six match `answer_key.OPTIONS` (which was read off the physical coach).

**What this means for the record question.** There *is* a record field that binds an option to
its selector — the row's closing u16 — but it does not contain the selector, or an index to
one. It contains an **action id**, and the join to the selector is the GuiCode dispatcher plus
the adjacency this finding is about. So the negative "no record stores the selector" stands
unchanged; what falls is "no record participates in the binding".

**What it does *not* settle.** It does not decode the u16's other use (§8.2's master/popup
controls, where the same field is a group index) or supply the discriminator between the two
uses — that is still `unknown`. It also does not touch the per-row id `0x6B..0x70` (`ff 6b 00`
… `ff 70 00`, one per row in the same records), which remains `unknown`. And it is not yet
encoded in any extractor or test; treat it as a documented lead with cited bytes, and confirm
it independently before building on it.

### A by-product: §8.3 row 2 (the `E3`/`E4` → channel 25/26 binding) has a candidate answer

**`inferred-from-code`, derived 2026-08-15 while writing this finding. Not implemented, not
tested — `test_bath_light_pair_binds_to_channels_25_and_26` stays `xfail(strict)` until an
extractor and a test exist.** Recorded because the bytes are cited and the result reproduces
hardware evidence that was not used to derive it.

Each Bath/Cargo arm emits a `0x1FEDB` (DC_DIMMER_COMMAND_2) frame beside its `0x1FED9` writes.
The two arms that write **one** selector are decisive, because there is nothing else in the run
to pair with:

| arm | its only `0x1FED9` write | its only `0x1FEDB` frame | pairing |
|---|---|---|---|
| Bath row, `0x0024CF9A` | `0x00259814` = `00 d9 fe 19 ff 96 e4 0f 00 00 d1 ea` (`E4 := 0`) | `0x00259820` = `00 db fe 19 1a ff 00 03 ff 00 ff ff` — instance `0x1A` = **26**, command 3 (OFF) | `E4` ↔ **26** |
| Cargo row, `0x0024CE7C` | `0x0025982C` = `… ff 96 e3 0f 00 00 d1 ea` (`E3 := 0`) | `0x00259838` = `00 db fe 19 19 ff 00 03 ff 00 ff ff` — instance `0x19` = **25**, command 3 (OFF) | `E3` ↔ **25** |

Both runs are exactly two emits long and then branch to a tail that sends only the two
`FF FF D3 FF` read-backs, so no third frame exists to complicate the pairing. **Correction
(review fix round 1): the tails are not shared** — an earlier draft of this paragraph said both
arms branch to `0x0024CF6C`, and that is wrong. The Bath arm branches there (`b 0x24cf6c`
@`0x0024CFC4`, read-backs `0x00259850` = `E3` then `0x00259844` = `E4`); the **Cargo arm
branches to its own tail `0x0024CE4E`** (`b 0x24ce4e` @`0x0024CEA6`, the same two read-backs in
the opposite order, `E4` then `E3`). Each handler has its own tail. The substantive claim —
two emits, nothing else in the run — survives unchanged; only the citation was wrong.

**An independent, non-code corroboration, in GuiData.** Row 4's own stored label is
`'   Cargo / Bath Light\n    (CH. 25)'` (13.6 `0x09A07C`, length byte `0x22` = 34) — **the `E3`
row names channel 25 in plain text**, in the file, with no reference to any template. That is a
second source for one half of the pairing and it shares nothing with the disassembly.

The two-write arms agree: the arm writing
`E4:=1, E3:=0` OFFs instance **25** (`E3`'s channel) and the arm writing `E3:=1, E4:=0` OFFs
instance **26** (`E4`'s channel). Across all four arms the pattern is the same — **the `0x1FEDB`
OFF accompanies the selector being written `0`**. That pattern is `proven-from-code` (it is four
cited template pairs); reading it as `BUS-BASELINE.md` PC.8's "sends an explicit OFF (command 3)
to the abandoned channel" is the step that adds the instance-is-a-channel identity.

The templates are addressed here as `base + immediate` (`ldr r6,=0x00259260` then
`addw r3,r6,#0x5b4`), not by a direct literal — worth noting for any reimplementation.

`{0xE3: 25, 0xE4: 26}` is exactly `answer_key.OPTION_CHANNEL_BINDING`, read off the coach in
PC.8. It was **not** used to derive the pairing; the pairing came from "one write, one OFF, one
run". The residual assumption is the standing one this project already relies on everywhere —
that an RV-C **instance** byte is the G12 channel number (PC.8, PC.9) — which is why this is
graded `inferred-from-code` rather than proven.

> **THE TEMPLATES FIRE — `observed-on-hardware`, `BUS-BASELINE.md` PC.33, 2026-08-24.**
>
> Six OPTIONS taps were captured, covering both selectors in both directions. Every one emitted a
> `19FEDB9F` command-3 OFF **0.6–2.0 ms after** its `0x1FED9` write, and the instance is exactly
> what the four template pairs above predict:
>
> ```
> 897.390391  19FED99F#FF96E30F0000D1EA     E3 := 0
> 897.390962  19FEDB9F#19FF0003FF00FFFF     -> instance 0x19 = 25 OFF
> 932.097427  19FED99F#FF96E40F0000D1EA     E4 := 0
> 932.097992  19FEDB9F#1AFF0003FF00FFFF     -> instance 0x1A = 26 OFF
> ```
>
> `E3 → 25` and `E4 → 26`, three times each, never crossed. On the two-write arms the OFF follows
> the selector written **`0`**, confirming this section's cross-arm pattern on the wire as well as
> in the code.
>
> **Two of the three things this section flagged as assumptions are now observations.** That the
> arms *execute* at all (a template in a pool is not a proof that anything reaches it) and that
> the emitted instance reaches the bus as written. What is **still** an assumption is the third:
> that the instance byte denotes the G12 channel. PC.33 shows the panel commanding instance 25/26
> and shows nothing about what those channels are, so the standing PC.8/PC.9 identity is doing the
> same work it was before. The grade stays `inferred-from-code` for that reason, and the honest
> summary is that this run removed two of the three doubts rather than all of them.
>
> **The strict `xfail` does not close.** `test_bath_light_pair_binds_to_channels_25_and_26` fails
> because no extractor reads this out of the file, which is a tooling gap a capture cannot fill.
> What changed is the risk of writing one: it is now transcription of a confirmed mechanism rather
> than a bet on an unexecuted code path.

### Why `MCF-FORMAT.md` §8.3 row 1 asked the wrong question

That row asks *"Which **record** binds an option to its `15FCE` selector"*, and its text ended
*"the offset→selector join is still not a file structure."* The second sentence **remains
literally true** — the offset→selector join is code adjacency, not a table, a record field or
any other data structure. What changes is the conclusion: the join is no longer `unknown` and
no longer outside the container.

The first half was the wrong question in a sharper way. It presumed the binding would be a
record **holding the selector** (or an index to it), which is what every search for it looked
for and why every search failed. The actual shape is a **three-link chain**, and only its first
link is in GuiData: the row record's closing u16 is an **action id**; GuiCode's dispatcher maps
that id to a handler; the handler carries the cfg offset as an immediate and the selector as a
literal template byte, adjacent. So the row is **rewritten, not closed** — marking it closed
while leaving the "not a file structure" sentence in place would make that document contradict
itself.

## Finding 24 — the align byte is persistent render state, and it right-justifies the box geometry

`proven-from-code`, 2026-08-15. (Numbered 24: master's Finding 23 is the option→`0x15FCE` selector
join.) Two joined facts, found chasing why the HOME **OFF** button rendered ~64px too far right
(x0=397) instead of above **RET** (x0=333), and why its separate label followed the wrong one.

**1. The align byte (`state+9`) is persistent, like the font selector.** The decoder `FUN_00252CB0`
computes `align = header_byte3 >> 6` (`0x2531f6`/`0x2531fc`) and stores it to `state+9` **only when it
is non-zero** — `0x2531fe beq` skips the store on align 0 (`0x253200 strb r3,[r2,#9]`), gated to types
in `0x600ffdfd`/`0x7fff` (`0x2531e2`/`0x2531ee`). So a record's align byte can only *set* the state to
1/2/3, never clear it; it persists across records — and across containers, since `FUN_00258330`'s
0x98-byte state snapshot is gated on a save flag the dispatch passes as 0 (`0x257ac2 movs r2,#0`,
`0x2535c cbz`). This is exactly how `state[2]` (the R38 font, Finding 14c) already persists. An
align-0 record inherits the last non-zero align — e.g. the align-0 **OFF label** inherits its
align-3 **OFF box**'s alignment.

**2. `FUN_00257BFC`'s align block right-justifies the box *geometry*.** After the flow-origin commit
(`+0x36 = +0x2a` @ `0x257d00`) and before the draw dispatch, a block at `0x258058-0x258180` reads
`state+9` (`0x258088`/`0x2815c`) and, for "path-1" types (guard `0x258060-0x258074`:
`(0x600c7c9c & 1<<t)` for t<32, `(1<<(t-32) & 0x3ff3)` for t≥32 — the same type-bit words the decoder
builds), shifts the record's own box coords `+0x2a`/`+0x2e` by the **box width** `(+0x2e)−(+0x2a)`:
**align 3** → `+0x2a = x0 − width` (declared x0 becomes the *right edge*); **align 2** → shift both by
`width/2` (centre). This is distinct from the type-0/8 *content* extents (Finding 14, placed by glyph
width, `FUN_002557C0`) — those types are path-2, not path-1. The flow origin is committed **before**
this block, from the pre-align pen, so alignment shifts a record's own drawn box (and its frame for
later mode-2/3 refs) but **not** the flow — using the aligned x for the flow regresses the Climate tab
211→183.

**Result.** OFF box (wtype 0x21, align 3) and its OFF label (wtype 0xb, align 0, inheriting the
persistent align 3) both right-justify by their 70px geometry to `[327,397]`, above RET; ON/EXT/RET
(align 0, the row bar resets persistent align to 1) and the Climate tab/setpoint are unchanged.
Replayed in `layout.py` (`_State.align`, `_writes_align`, `_align_path1`, `_placed_label_box`);
`tests/test_layout.py::test_off_button_right_justifies_above_ret`. The nav-rail icons centre by the
same class of byte-derived box centring (a font-24 glyph in the full-width 60px rail box, `render.py`).

## What this pass did NOT establish

> **Scope note, 2026-08-18.** "This pass" means the pass that ended at **Finding 24**. It sits
> here, mid-document, because Findings 25+ were appended later. Two of its bullets have since
> been overtaken by findings *below* it — annotated inline. Read it as a dated snapshot, not as
> the document's current standing.

- ~~**The screen and widget record layouts are still `inferred-from-data`.** Findings here
  cover the font, glyph and image tables and two lookup tables — not the screen records
  `MCF-FORMAT.md` §6 describes, and not the group or binding tables.~~ **Overtaken by Findings
  10–12 and 21** (in this document, above): `FUN_00252CB0` *is* the record decoder and
  `FUN_00257BFC` the layout engine, both transcribed field by field and validated by the
  object-table boundary oracle on every object in all three builds. The **group and binding
  tables** are the part of this bullet that still stands — they remain `inferred-from-data`.
- ~~**The 1422-entry object table at `+0x46148` is unreferenced** by any GuiCode literal~~
  **Resolved (Finding 12):** `+0x46148` is the **screen/object table** — `FUN_00258650`
  indexes it by screen id for a screen's root record list, `FUN_00258330` by child index for
  container children. `DAT_0025887c` and `DAT_002584e4` both hold `0x90046148`.
- **The per-floorplan option defaults were not found**, and GuiCode is now **eliminated**
  as the place they are stored in any pattern-searchable form — see Finding 6 for exactly
  what was searched, what was eliminated, and the two hypotheses that survive.
- 14 of the 20 bytes of each font descriptor are `unknown`.
- Findings 1–19 were derived from 13.6 alone. **14.7 is now disassembled too** (Finding 20,
  Ghidra project `guicode147`), but only the config-unpack/send path has been read there;
  11.23 remains unexamined at the code level. `tests/test_blob_crosschecks.py` pins the font
  table's structural invariants across all three.

## Next steps

> **Dated 2026-08-15, kept for the struck-through entries.** Items 2–5 are done or withdrawn
> and are struck through in place so the same steps are not re-added. **Item 1 is withdrawn
> too, 2026-08-18** — see its annotation. For what is actually open now, read `MCF-FORMAT.md`
> §10 (the open questions, maintained) and `docs/CLOSED-THREADS.md` (the original open threads, all
> resolved); this list is not.

1. ~~Sweep the 154 functions for the per-floorplan option defaults, using
   `tests/answer_key.py`'s oracle to test any candidate.~~ **WITHDRAWN — Finding 19 settled
   this negative from the write side.** Every option/floorplan register is written **once,
   from a received CAN config payload**, never from a file constant or a boot-time copy. The
   defaults are not in GuiCode, not in GuiData, and not in the G12 section — they are not in
   the file at all, so there is nothing for a sweep to find. Sweeping anyway is how a fitted
   answer gets manufactured; `find_option_defaults` stays `NotImplementedError` behind a
   strict `xfail`.
2. ~~Attribute the screen/widget record readers: find which functions consume
   `+0x46148` (the 1422-entry table) — it is the only object table no literal names, so
   it is probably reached through a computed base or a pointer stored at init.~~ **Done —
   Finding 12**, as the struck-through bullet twelve lines above this list already records:
   `FUN_00258650` indexes `+0x46148` by screen id and `FUN_00258330` by child index, and two
   literals (`DAT_0025887C`, `DAT_002584E4`) do name it. Annotated 2026-08-15 because a
   reviewer working from this list took the step as still open.
3. ~~Work out how `GuiData+0x44BC8`'s SRAM pointers are fixed up. Whatever performs that
   relocation also knows the record layout.~~ **WITHDRAWN, 2026-08-15 — the premise is false
   and there is no such mechanism to find.** `MCF-FORMAT.md` §5.4b/§5.5: the entries are
   static SRAM addresses, correct as shipped, and *nothing fixes them up at load time*, so no
   relocator exists and none can be attributed the record layout. What is initialised at boot
   is the **cells** those pointers address — for the type-10 (colour) entries by GuiCode's
   theme-init (Finding 18, RESOLVED). This step was left standing after the §5.5 withdrawal
   and was copied out of here into `MCF-FORMAT.md` §10 item 5 before being caught; struck
   through rather than deleted so the same step is not re-added.
4. ~~Repeat the disassembly for 14.7~~ **Done for the config path (Finding 20; project
   `guicode147`).** Still worth extending to the renderer/decoder functions for a full
   cross-check against the 13.6-derived Findings 9–17.
5. ~~Nail the option→`0x15FCE`-selector join (Finding 20's open thread)~~ **Done —
   Finding 23.** The join is code adjacency in GuiCode's own tap handlers, not a host path.
   What remains from it: confirm the two 14.7 rows (`0x1B → 0xE4`, `0x14 → 0xE7`) on a 14.7
   panel by toggling and diffing the `0x1FED9` write; and turn Finding 23's two cited
   by-products into tested extractors — the OPTIONS row u16 → action id, and the `E3`/`E4` →
   channel 25/26 pairing (`MCF-FORMAT.md` §8.3 row 2).

## Finding 25 — the type-6 value-match switch is COUNT-bounded, and no-match renders nothing

`proven-from-code`, 2026-08-15. Found chasing why the HOME **inverter** block (bottom-left, right
of the CLIMATE box) rendered an "Inverter"/"Disabled" widget where the owner's coach (no Progressive
Inverter option) shows an **empty** area. Corrects an earlier wrong read that the block's presence was
an unmodeled "option→placement binding" (Finding 15's characterisation of obj 247); it is the ordinary
type-6 switch (Finding 16), just decoded with the wrong group boundary.

**The switch carries its own candidate count in a `u16` at record `+0x94`.** `FUN_00252CB0` writes it
at `0x00253B2A` (`strh r0,[r4,#0x94]`) under the mask `(aed & 0x40) | (aee & 0x4000)` — i.e. `wtype`
**6 or 0x2e**, the two value-match container types (`aed = 1<<t`, `aee = 1<<(t-32)`; the field is read
at `0x00253B22`, the R45 stream position, immediately after the value-slot and before the child index).
The switch scan `FUN_00256C54` reads it back at `0x00257B3A` (`ldrsh r3,[r6,#0x94]`) and scans **exactly**
`[child_base, child_base+count)` for the first candidate whose match-table value (`DAT_00257BF8`,
`+0x4562C`) equals the switch value. There is **no** 0xffff-skipping (every entry decrements the count)
and **no** repeated-value heuristic.

**On no match, the firmware renders NOTHING** unless the base is an explicit default: `0x00257B5A–6E`
renders `child_base` only if `match[child_base] == 0xffff`, otherwise it calls `FUN_00258330(0xffff)`,
which returns immediately at `0x00258336`. So a driven switch whose value hits no candidate draws an
empty region — which is exactly the owner's inverter case.

**The HOME inverter block.** HOME root (obj 4) → plain wtype-5 container → obj 899 → its type-6 switch
`value_slot=113` (Progressive Inverter register `0x2003A62B`), `child_base=1409`, **count=2**: candidates
obj 1409 (mv 1) / obj 1410 (mv 2), both the "Inverter" widget. Progressive Inverter OFF drives slot
113=0, matching neither; `match[1409]=1≠0xffff` → render nothing → empty. Objs 1411 ("N/A", mv 0xffff)
and 1412 ("Disabled", mv 0) belong to a **separate inner** mode switch based at 1411 (Disabled/Enabled/
Passthrough/APS Only/Load Sense/Standby), reached only once the block is present. Byte-verified counts:
inverter 2 (`0x84F8A`: `71 00 · 02 00 · 81 05`), Bath Fan 3 (`0x85BD6`: `6D 00 · 03 00 · F6 00`), HOME
floorplan 11 (`0x9E126`: `05 00 · 0B 00 · 8F 03`).

**The seven status messages the block can display** (2026-08-26). Inside each present variant
(obj 1409 / 1410) the status line is a second type-6 switch — `value_slot=647` (1409) / `649`
(1410), `child_base=1411`, **count=7** — driven by the inverter's RV-C `1FFD4` INVERTER_STATUS
mode (`BUS-BASELINE.md` PC-inverter). Match values read from `MATCH_TABLE_136` (`0x4562C`),
`match[obj] = u16 @ base + obj*2`:

| slot value | obj | message | RV-C INVERTER_STATUS |
|---|---|---|---|
| `0xFFFF` (no match — default) | 1411 | **N/A** | no status received |
| 0 | 1412 | **Disabled** | 0 disabled |
| 1 | 1413 | **Enabled** | 1 invert |
| 2 | 1414 | **Passthrough** | 2 AC passthrough |
| 3 | 1415 | **APS Only** | 3 APS only |
| 4 | 1416 | **Load Sense** | 4 load sense |
| 5 | 1417 | **Standby** | 5 standby |

`N/A` is the `0xffff` no-match default, so an inverter that is present but has sent no status
draws "N/A" (not blank — blank is the *outer* switch's no-inverter case above). Beside the text a
**separate 5-state colour dot** (a second switch, `value_slot=648`/`650`, `child_base=635`,
count=5, match values 0-4, objs 635-639) draws an icon glyph, not text — the green/red status dot,
not one of the seven messages.

**The bug this fixed.** `layout._switch_child` had scanned up to 64 entries with a "repeated match value
⇒ next group" boundary (labelled heuristic) and fallen back to the base on no match. For the inverter it
over-scanned past count 2 into the inner switch and matched obj 1412 (mv 0) ⇒ a spurious "Disabled". The
fix: surface the count as `records.WRecord.count` (the R45 u16 was already consumed for alignment, just
discarded — the old note "every switch resolves to a single child" was the wrong assumption), and rewrite
`_switch_child` to the exact count-bounded scan + the 0xffff no-op rule (returning `None` = render
nothing; `layout.Screen.walk` skips it). This closes the display side of open thread 4 for the inverter:
its presence IS byte-derivable given the config vector; only the config vector itself is CAN-delivered,
not in the MCF (Finding 19).

## Finding 26 — multi-line text has its own box v-align byte; value units inherit the persistent align

`proven-from-code`, 2026-08-15. Two HOME placement fixes, both fully byte-derived, found chasing
labels that sat slightly wrong: the bulb-column captions ('Main\nCeiling' …) were too high, and the
tank '%' / battery 'V' units sat off to the right of their widgets.

**1. Multi-line text: the vertical block-align byte at state+0x95.** The multi-line text drawer
**`FUN_002559A0` @ 0x002559A0** positions the whole text block *inside the record's own box
[y0,y1]* by a byte read at **`0x00255C44` (`ldrb …,[r2,#0x95]`)**: `2` = centre
(`y0 + (box_h − block_h)/2`), `3` = bottom (`y1 − block_h + 1`), else top (`y0`), where
`block_h = (n_lines−1)·lineHeight + font[0x07]`. The per-line horizontal align is the twin byte
**state+0x94** (`0x00255DEC`), and the inter-line advance is **state+0x96**. All three come from
the decoder's **R49** block (`records.py`, gated `aed & 0x2800` = wtype 11/13), stored by
`FUN_00252CB0` at `0x00253D2E/D38/D42` as `strb …,[r4,#0x94/0x95/0x96]` = (h-align, v-align,
line-height). records.py consumed those 4 bytes for alignment but discarded them; they are now
`records.WRecord.h_align/v_align/line_height`, threaded to `layout.Placed`. The HOME bulb caption
'Main\nCeiling' is **v-align 3, line-height 18** → block 39px high → text at y 206..244, below the
bulb glyph (box 144..204), reproducing the coach. The **single-line** drawer `FUN_002557C0` has
**no** box-vertical align — it draws at `pen_y − font[0x0b]` — so this applies to multi-line only.

**2. Value units right-justify by inheriting the persistent align (Finding 24, value-widget case).**
A value+unit widget (a tank `%`, a battery `V`) is a value record (wt-8) followed by a unit record
(wt-0). `FUN_002557C0` anchors each by the **persistent** align `state+9` it reads at `0x002557FE`
(not the record's own byte): `align 3` → right edge at the pen, `2` → centre, `0/1` → left. The
value (wt-8, align 3) sets `state+9=3`; the unit then behaves by its OWN align byte's *persistence*
(Finding 24: align 0 does not overwrite state+9, 1/2/3 do). So the tank **`%` (align 0) inherits the
value's 3** → right-justified, tucked under the tank; the battery **`V` (align 1) overrides back to
left** → stays in its box (the designer used align 1 precisely to stop `V` inheriting the
right-justify). For drawing, align 0 and 1 are identical (both left); align 1 differs from 0 *only*
in that it sets the persistent state. The bug was `layout._placed_label_box` using `rec.align` for
the non-path-1 text-width branch; it now uses `state.align`, matching `FUN_002557C0`. The value
digits themselves are runtime (bus-delivered, open thread 5), but their right edge is pinned to the
pen, so the unit's placement is exact without them.

## Finding 27 — the clock/time value register, and the 12-24h and C/F format flags

`proven-from-code`, 2026-08-16 (GuiCode dig). Establishes how the header clock (and the CLIMATE
temperature) are identified and formatted, so a preview renderer can place/select them
byte-derived instead of by screen position. All three values are CAN-delivered, not in the MCF
(consistent with Finding 19); only their *display mechanism* is byte-derived.

**Correction:** the real registers are those `layout.variable_register` resolves for the slots
(it reproduces the documented 5/109/112 exactly), NOT hand-guessed SRAM addresses. Clock value =
slot **226 -> 0x200399DC**; 12/24h flag = slot **263 -> 0x2003A205**; temp-units = slot **9 ->
0x2003A2A3**; temp value = slot 238.

**1. Clock value = 0x200399DC (slot 226) — minutes-since-midnight, from a CAN message, not an RTC.**
Sole writer at `0x00249912-0x00249924` in a receive/status handler: it reads a packed time field
(`[r4+0x22]`, low byte = hours, high byte = minutes), computes `hours*60 + minutes`
(`rsb r3,r3,r3,lsl#4` then `lsls #2` then `add r3,r3,r2,asr#8`), and stores the u16 to 0x200399DC.
There is **no RTC peripheral (0x40002800) referenced anywhere in GuiCode** — only LTDC (0x40016800)
and DMA2D (0x4002B000). So the clock is a bus value. **Byte-derived clock identity:** a value
widget (wt-8) whose `value_slot == 226` IS the clock (13 across the pages, incl. the SETTINGS
"24 Hour" row time-preview); `render.CLOCK_SLOT`. This replaces the top-right-position heuristic.
~~Honest negative: the exact HH:MM digit-assembly opcode was not located — the value formatter
`FUN_002521c8` is a fixed-point decimal formatter with no ÷60 and no ':' (0x3a) constant anywhere
in GuiCode~~ — so the render supplies the "HH:MM" string as a preview value; the register's *meaning*
(minutes-since-midnight) is proven. *(Negative withdrawn 2026-09-04, **Finding 65**: the ÷60 is a
magic-constant multiply, `0x88888889` at `0x2527A8`, and the `:` is a string at `0x259B40`, not an
immediate. `FUN_002521C8` assembles `H:MM` itself under display modes 3-9, selected by a wtype-9
format directive. The preview string stays, but for a different reason: the value is live.)*

**2. 12/24h flag = 0x2003A205 (slot 263).** Sole writer at `0x00247C22-0x00247C2E` in the config
handler `FUN_00247BF8`: `0x2003A205 = (config_payload[2] == 1)`. Read by exactly nine records: the
seven count-2 header-clock switches (obj 755/757/758/766/767/768/892), which select **obj540
(match 0, 12h) vs obj541 (match 1, 24h)** — two structurally identical slot-226 clock widgets that
differ only in a spacing byte — plus obj1056, the SETTINGS row holding the literal "24 Hour" label
(obj283) and a clock preview. Driving `slot_values={263: 0|1}` picks 12h/24h byte-derived.

**3. Temp-units C/F flag = 0x2003A2A3 (slot 9).** Sole writer at `0x0024EE34-0x0024EE3A` (a receive
handler, then calls `FUN_00247BF8`). Read by the "Temp Units:" row switch (base 77: "Celsius"
match 0 / "Fahrenheit" match 1) and 14 count-2 temperature switches (child_base 93). Driving
`slot_values={9: 0|1}` picks Celsius/Fahrenheit byte-derived.

Neither format flag has an in-file default (each has one writer, both from a received payload), so a
preview render must choose (the shipped candidate is match 0 = 12h-style / Celsius) — the write-side
confirmation on the same footing as the floorplan/option state (Finding 19).

## Finding 28 — box fill vs border: the two colour blocks, the on/off pairing, and mode 2 = white

`proven-from-code`, 2026-08-16 (GuiCode dig). How a box widget draws its interior fill vs its
border/outline, so the render reproduces the tank-well outlines, the CLIMATE box outline, and the
Water Pump / Tank Heater button fills. Corrects the "block A = fill, block B = border" reading:
the renderer PAIRS the colour fields differently than `records.py` groups them.

**The on/off pairing (`FUN_00256C54:53-63`).** The flag `render+0x704F` (climbing to the RV-C value
slot) selects one style *pair*. At rest (OFF, flag 0): **primary `uVar16` = fillA = BORDER**,
**secondary `uVar17` = fillB = INTERIOR fill**. ON (flag 1): fillA2 / fillB2. The struct offsets
↔ `records.py` fields are proven from the decoder store order: `+0x64`=fillA (`b3&7`), `+0x68`=fillB
(`(b3>>3)&7`), `+0x6C`=fillA2 (`(b5>>1)&7`), `+0x70`=fillB2 (`(b5>>4)&7`). So **fillA is the
border/outline colour and fillB the interior**, the opposite of the decode-block labels.

**Per box type (dispatch on `render+0x8A`):**
- **wt-3 rounded box** (`FUN_00255E90`, called at `:123`): 4 edge rects of thickness = radius byte
  `+0x8B` (or the small global default `render+0x6C10` when 0, ~2px) in `uVar16` (fillA border),
  then interior filled in `uVar17` (fillB) **only if the transparent-interior flag is clear**. That
  flag is header byte0 bit1 (`iVar13`, `FUN_00256C54:57/62/64`); the CLIMATE box has byte0 0x43
  (bit1 set) so it renders as a **hollow outline** (`layout.Placed.hollow`).
- **wt-0x20/32 tank well** (case `:366`): border/edge WIDTH = geometry **coord index 4**
  (`render+0x32`, =5 for the wells; `layout.Placed.coord4`), stroked in `uVar16` (fillA); interior
  filled in `uVar17` (fillB). The wells' fillA is mode 2 → **white** outline.
- **wt-0x21/33 button/panel** (case `:430`): a SOLID rounded rect entirely in `uVar16` (fillA);
  fillB unused. So the quadrant panels (fillA idx8) and buttons are solid-fillA; a light button
  border is a separate stacked sibling record.

**The colour-mode table (decoder per-field switch):** 0/5 = keep previous (inherit last sibling in
decode order); 1 = theme default; **2 = hardcoded white 0xFFFFFF** (`str …,=0xffffff`); 3 = inline
BGR u24; 4 = inherit sibling slot; 6 = palette `DAT_002535B0[u8]`; 7 = colour-table
`DAT_002535B4[u16]` (`theme.mode7_palette`). **mode 2 is a concrete white, not an inherit** — it is
the tank wells' white outline (fillA mode 2). It is white as a STROKE colour; `render.py` resolves
it only in the border path (`_decoded_fill(..., border=True)`), because many boxes carry a mode-2
fillA border with no interior and resolving it as a fill would flood them white.

**Runtime-state fills (idx160 Water Pump, idx455 Tank Heater).** Selected in `FUN_00247BF8` by a
3-bit field `ubfx([r4,#0xac],2,3)`; the resting/default branch copies a base cell. idx160's base
`0x20039A58 = 0x004151A3 = (65,81,163)` is **proven-from-code** (init block `0x00248330-62`); idx455
is the structural twin, resting **inferred** (computed-address store not isolated). `theme.mode7_palette`
omits both (multi-constant cells, ambiguity guard), so `render._RESTING_FILLS` supplies the resting
value labelled `src="resting"` — never the runtime on/off/unavailable branches (defensible negative).

## Finding 29 — mode-0/5 colour inheritance: persistent, parent-scoped slots

`proven-from-code`, 2026-08-16 (GuiCode dig), with one render-side refinement (parent scoping)
forced by the photo oracle. Resolves the mode-0 "inherit" boxes (open thread 1): EXT/RET, the
battery body, the CLIMATE-area frames, the Inverter block.

**Mechanism.** The four colour fields decode into a single global render-state buffer (Finding 28
slots +0x64 fillA / +0x68 fillB / +0x6C fillA2 / +0x70 fillB2). Each field's decode switch in
`FUN_00252CB0` has cases 1/2/3/4/6/7 only — **no case 0** (and no case 5 on the block-A fields). So:
- **mode 0** (and mode 5 on block-A): the store is skipped → the slot **keeps its previous value**.
  A mode-0 box therefore INHERITS the last explicit colour written to that slot.
- **mode 5 on block-B**: sets a transparency flag (`*state |= 2` / `|= 0x8000`), NOT a colour — the
  slot keeps its previous value but the field paints nothing (transparent interior).
- explicit modes 1/2/3/4/6/7 write the slot (Finding 28's table).
The function head does not reset the colour slots, so "previous value" spans records.

**Parent scoping (render-side, required by the oracle).** A pure "global last write, never reset"
replay leaks colours across the whole tree — e.g. the header's red (idx4) propagated into ELECTRICAL
Start/Stop, which the coach shows not-red. The render therefore treats the slots as **parent-scoped**:
a child inherits the parent container's colour (set by the container record just before it recurses),
but the child's own writes are **restored after the recursion** so they do not leak into the parent's
LATER siblings. This matches every photo with **zero regressions** (0 existing boxes changed/removed
across all five pages; only previously-unpainted mode-0 boxes newly paint). Whether the hardware does
an explicit save/restore or relies on each real box's wrapper always re-setting its colour, the
parent-scoped model is the byte-faithful render behaviour; the pure-global model is not.

**Resolved boxes (13.6).** EXT (0x97865) / RET (0x96c69): fillA mode-0 inherits their wrapper's
mode-7 idx131/133 → runtime-state cells whose RESTING branch copies idx15 = **(65,81,163)**
(`render._RESTING_FILLS`, src="resting"; the copy is at GuiCode 0x248732/0x248760). CLIMATE frame
idx8=(39,40,98); battery body/nub idx3=(21,24,58); Inverter mode-3 black. All statically resolvable;
the runtime state VARIATION (pressed/disabled/active) stays a defensible negative.

**render.py/layout.py:** `_State.fill_slots` + `layout._apply_colors` apply each record's colour
fields in decode order (mode-0 substituted by the inherited slot, mode-5 left transparent); `walk`
save/restores `fill_slots` around each container recursion; `render._decoded_fill` resolves the
substituted colour unchanged.

## Finding 30 — a label's own alignment is its R49 h/v-align bytes, not the header align byte

`proven-from-code` (extends Finding 26), 2026-08-16. A wt-11/13 label's horizontal AND vertical
placement within its box come from its **R49 bytes** (`records.WRecord.h_align` = +0x94,
`v_align` = +0x95; `FUN_002559A0` reads them), NOT the header align byte (`header[3]>>6`, which
positions the record's box). The renderer had been using the header align for text placement, which
mis-aligned the ELECTRICAL value-box captions.

- **Horizontal (`_line_x`)**: use `p.h_align` (2 centre, 3 right, else left) when present, else the
  header align. The value captions ('Start at', 'Time at Start Volts', 'Stop at', 'Quiet Time …')
  are header-align 3 (right) but **h_align 2 (centre)** -> they centre in their box instead of
  right-justifying into the + button. ON/OFF/EXT/RET are header-align 0 but h_align 2 -> centre in
  their box (consistent with Finding 24's OFF-centres-in-its-right-justified-box).
- **Vertical (`base_y`)**: apply `v_align` to ANY label carrying it, single OR multi-line (was
  multi-line only). 2 = centre (guarded to box_h>block_h), 3 = bottom, else (0/1) top. 'Start at'
  is **v_align 1 (top)** -> it sits at the top of its box, above its value widget, not centred onto
  it; the House/Chassis/Water Pump captions are v_align 2 (centre); the bulb captions v_align 3.

Verified: only the ELECTRICAL value captions moved (8 text prims, all to the centred/top position
matching ELECTRICAL.jpg); HOME/CLIMATE/SETTINGS/LIGHTS text unchanged (0 moved) -- no regressions.

**Addendum (glyph path, 2026-08-16).** The same v-align applies to the ICON/glyph path, not just
text: a glyph is centred vertically in its own decoded box `[y0,y1]` (v-align 3 = bottom, else
centre when the box is taller than the glyph), not in the font cell. Cell-centring left the value
+/- adjuster glyphs (font-9 '-'/'+') in the top ~half of their tall (80px) button box; box-centring
per v_align puts them at the box centre (y≈99 for the ELECTRICAL top row), matching the coach. For a
box no taller than the glyph (bulb icons, nav-rail icons) this is unchanged beyond a few px.

## Finding 31 — the AES/AGS run-time label is mode-selected and copied to SRAM at config time

`proven-from-code`, 2026-08-16. The ELECTRICAL page's top-right caption reads **"Max Engine
Run Time"** on the coach (photo `build/13.6-images/ELECTRICAL.jpg`, mode indicator "AES") — but
that exact string exists **only in GuiCode** (file 0x28c7c / rt 0x2593fc), never in GuiData, and
no pool literal, `movw`/`movt`, `ADR`, or absolute pointer anywhere in the MCF references it. An
earlier read called it dead code. It is not: the reference is a **computed offset inside the 25 KB
`FUN_00247BF8`**, which flat scans miss and only Ghidra's recursive analysis surfaces
(`ghidra/find_aes_label.py`). The caption is **not a static label at all** — it is chosen by
AES-vs-AGS mode and **copied into a SRAM buffer when the CAN config arrives**.

**The words live in a 32-byte-strided GuiCode string table** (base rt `0x259260`), two entries:

| entry offset | +0x00 (indicator) | +0x08 (caption) |
|---|---|---|
| AES  (base+0x194) | `0x2593f4` `"AES\0"`  | `0x2593fc` `"Max Engine\nRun Time\0"` |
| AGS  (base+0x1b4) | `0x259414` `"AGS\0"`  | `0x25941c` `"Max Gen\nRun Time\0"` |

**The selector is the mode byte at SRAM `0x2003A38C`** (variable slot 37, type 1), delivered by
**CAN DGN 0x15FCE** config (selector 0x15) — the same DGN whose selector 0x16 delivers the *value*
"115 Min" (slot 270, `0x2003A280`; the value work above). The selection is in `FUN_00247BF8`:
`0x00249CFA` reads `[0x2003A38C]`; at `0x00249D14` `cmp (mode-2),#1 / bls` sends **mode ∈ {2,3} to
the AES branch** (`0x249EDC`) and everything else to the **AGS branch** (`0x249D20`). Each branch
is a literal `ldm`/`str` byte-copy:

- AES (`0x249EE2..0x249F00`): `"AES"`(4) -> `0x20039B9C`, `"Max Engine\nRun Time"`(20) -> `0x20039DEC`.
- AGS (`0x249D20..0x249D3E`): `"AGS"`(4) -> `0x20039B9C`, `"Max Gen\nRun Time"`(17) -> `0x20039DEC`.

**The display end is a `wtype-13` dynamic-string widget** (Finding's records.py addition). The
GuiData **variable table** (GuiData +0x44BC8) maps the two SRAM buffers to slots:

| slot | SRAM | var type | role | display widget |
|---|---|---|---|---|
| 129 | `0x20039B9C` | 9 (string) | AES/AGS mode indicator | wt-13, `value_slot=129` |
| 269 | `0x20039DEC` | 9 (string) | run-time caption | wt-13, `value_slot=269`, geom box (220,80) |
| 270 | `0x2003A280` | 3 (u16)    | the value "115 Min"     | wt-0, `value_slot=270` (adjacent) |

The caption widget (obj at GuiData 0x8fa84) sits at box (220,80) — the **same place** as an inline
static text record; see the build evolution below. `layout.variable_register(data, 269)` resolves
`(0x20039DEC, 9)`; that is the whole chain from CAN byte to pixel.

**Build evolution (owner-confirmed: AES was added in 13.6).** In **11.23** (AGS-only, pre-AES) the
caption is a plain **inline GuiData static label `"Max Gen Run Time"`** (file 0x85690), at the same
record shape/position (x=220 y=80 font 0x0e) that 13.6 re-captioned inline to a generic
`"Min\nRun Time"` (file 0x8fa20). 13.6 added the mode-dependent path: the live caption is the
`wtype-13` slot-269 widget fed by `FUN_00247BF8`, and the inline `"Min Run Time"` is the
static fallback for the no-AES/no-AGS case. So the words are firmware-static; **which** caption
shows is bus-driven (the AES/AGS mode config), and the string itself is never on the wire.

**records.py addition.** `WRecord.is_dynamic_text` (`wtype == 13`): a dynamic-string widget whose
on-screen text is the runtime value of `value_slot`'s variable-table entry, not an inline label.
Corpus-invariant across all three builds — every wtype-13 record carries a `value_slot` and no
inline label (48/75/35 in 11.23/13.6/14.7; `tests/test_records.py`). The variable *type*
(9 = string, a few = 1 = bool-selected string) comes from `layout.variable_register`, which the
byte-only decoder does not read.

**Render (2026-08-16, done; label source corrected below).** `render.py` *draws* the wtype-13
caption/indicator: `render.aes_ags_labels(data)` reads the two mode labels byte-derived from the
GuiCode table (locating the standalone `<TAG>\0`+pad+caption layout — no hardcoded offsets), and
the string is drawn through the normal label path (inheriting the record's font/box/align), tagged
`dynamic@slot<N>` — byte-derived text, runtime-selected. Verified on the ELECTRICAL render
(`--page electrical --config coach-wd.json`): the box reads "Max Engine Run Time", matching the
photo, and on HOME the "AGS/AES … DISABLED" badge (obj 903, below) matches `HOME-INVERTER-AGS.jpg`.

**The label is selected by slot 37, not the option name (corrected 2026-08-16).** The first cut
took the AES/AGS mode from the config's option *name* (`AES* → AES`), because `options.slot_values`
recorded the radio slot only as a bool. That was wrong twice: it forced the label from a different
source than the switch-selected page content (which reads slot 37), so a config could draw an "AES"
badge over AGS-style "GEN" content; and `slot_values` wrote `1` for "AES Single Output" when the
byte-correct value is 2. Both are fixed: `options._option_value` maps the radio name to its
byte-grounded slot-37 value (**AGS → 1, AES single → 2, AES dual → 3**), and `cli` derives the
label from the **final `slot_values[37]`** (1 = AGS, 2/3 = AES). Now the badge/caption text and the
switch-selected layout come from the same slot, so they can never disagree — proven by rendering
the HOME generator variant at both modes (`--slot 6=2 --slot 37=1` → "AGS"+"GEN"; `37=2` →
"AES"+"Engine Not Running").

**HOME/ELECTRICAL gating — where the badge and GEN panel come from (2026-08-16 dig).** The
AES/AGS widgets are **double-gated by type-6 switches**, both keyed on GuiData registers, and this
is why an AES coach's HOME need not show the generator panel at all:

| page | outer switch | inner switch (obj) | candidates |
|---|---|---|---|
| HOME | inside obj 4, child_base 901, **value_slot 6** (power-panel variant) | cand **903** switches on **slot 37**, child_base 163 count 4 | 163=AGS(mv 0xffff), 164=none(mv 0), 165=AES-single(mv 2), 166=AES-dual(mv 3) |
| ELECTRICAL | inside obj 5/39, child_base 829, **value_slot 7** | cand **830** switches on **slot 37**, child_base 314 count 2 | 314=default, 315=AGS(mv 1) |

**Slot 6 is the HOME bottom-right power-panel selector** (byte-derived by walking the candidates):
`0`=House+Chassis, `1`=Chassis only, `2`=House+Chassis+GEN panel+badge, `3`=SOC/alternator (Li).
The generator panel/badge on HOME appears only at **slot 6 ∈ {2,3}**; the two oracle photos bear
this out — `HOME.jpg` (slot 6 = 1, Chassis only, no badge) and `HOME-INVERTER-AGS.jpg` (slot 6 = 2:
House 12.3V + Chassis 12.2V + Start/GEN/Stop + "AGS DISABLED"). Inside the GEN variant (obj 903),
slot 37 chooses the wording: slot37=1 → "GEN"/"Unknown" (AGS), slot37=2/3 → "Engine Not Running"
(AES). **Slot 7 is the ELECTRICAL AES/AGS layout selector**: cand 829 (default) is the AES page
(Engine Not Running, "Max Engine Run Time", 48 V Battery Voltage — matching `ELECTRICAL.jpg`),
cand 830 (slot7=1) is the AGS page (GEN, Triggers, Go Power! Solar). Slots 6/7 are **not named
options** — they are config-payload state (bus-delivered, Finding 19), correlated with AGS/AES by
the config load but independent registers, set in a render via `extra_slots`/`--slot`.

> **Sharpened for slot 6, 2026-08-21 (`proven-from-code`).** "Correlated with AGS/AES by the
> config load" understates it: slot 6's register `0x2003A03E` is **computed** from the AGS
> register, not carried in the payload. `ghidra/find_reg_write.py 2003A03E` finds the register in
> exactly two literal pools (`0x00249E30`, `0x00249F24`) with **three** referencing writes, all in
> `FUN_00247BF8` and all writing an immediate:
>
> ```
> 0x00249CFA  ldr r3,[0x00249e2c]   ; = 0x2003A38C   (the AGS / AES selector, slot 37)
> 0x00249CFC  ldrb r2,[r3,#0x0]
> 0x00249CFE  subs r3,r2,#0x2       ; AGS in {2,3} — AES, AES Single Output?
> 0x00249D04  bls.w 0x00249ECC      ;   -> slot 6 = 1   (Chassis only)
> 0x00249D08  cmp r2,#0x1           ; AGS == 1?
> 0x00249D0A  beq.w 0x00249ED4      ;   -> slot 6 = 2   (House+Chassis+GEN)
> 0x00249D12  strb r1,[r2,#0x0]     ; else 0            (House+Chassis), r1 = 0
> ```
>
> So on 13.6 **no instruction ever writes slot 6 = 3**: the SOC/alternator variant (object 904)
> is a real candidate with a decoded meaning, but nothing in this image selects it — it would
> take a writer this scan did not find, or a different build. The scan is complete for the
> register: every reference to both pool slots is listed above.
>
> This is what lets Firefly Studio show the right battery block from the generator setting alone
> (`studio/server/reference.py` `derived_slots`) instead of asking for slot 6 by hand. **Slot 7
> was not traced** — nothing here should be read as applying to the ELECTRICAL selector. The
`Screen.walk` switch machinery already honours them; nothing new was needed in the engine — the
earlier bug was only that the *label* bypassed them by reading the option name.

**Voltage-widget slots (2026-08-16 dig, adjacent).** The 'V' value-widgets keyed by `value_slot`:
**205** (`0x2003A272`) = HOME Chassis; **209** (`0x2003A594`) = the House/battery register;
**272**/**274** (`0x20039D52`/`0x2003A22C`) = the ELECTRICAL AGS "Start at"/"Stop at" setpoints.
**Byte-certain: slot 209 drives BOTH** HOME "House" (obj 443) and the ELECTRICAL "Battery Voltage"
box (obj 829) — structurally identical records (a slot-208 visibility switch over empty wt-9
spacers, then the wt-8 slot-209 value; verified at byte level). **But the owner reports they show
different physical batteries**: HOME always the **12V** house (never 48V, even on a WD/Li coach),
ELECTRICAL the **48V** pack (~54V). So two different RV-C messages feed `0x2003A594` upstream in
the receive filter — a split the GuiData display graph does **not** encode; naming those DGNs is a
further LCD-firmware dig (`FUN_0021C760`). Because a render is one page, the shared slot-209 value
is **page-selected in `cli._cmd_render`**: the 12V `house_voltage` on HOME, the 48V `coach_voltage`
on ELECTRICAL. `render._VOLTAGE_SLOTS` otherwise routes each slot to its own preview key
(`chassis_voltage`/`coach_voltage`/`start_volts`/`stop_volts`); a 'V' slot with no key is left
undrawn rather than shown a wrong shared number.

## Finding 32 — the widget→status-slot join is not a decodable record field (negative)

`proven-from-code`, 2026-08-16 (status-slot join dig, Tasks 2–3; `build/status-join-notes.md`,
working notes, not published). Advances
`MCF-FORMAT.md` §5.4a open thread 2: how does a GUI object's on/off indicator get the receive-filter
status-slot index (= variable index) it displays? Two functions were read in full to answer it —
**answer: it does not come from any per-object GuiData field.**

**Part 1 — the LCD-side init builds only an identity map, never an object pairing
(`FUN_0021BE50`, LCD Firmware, image base `0x00210000`).** The function locates the GuiData
section tagged `'A'` (`0021bf84 cmp #0x41`) — the 298-entry **variable table** (count matches
`len(guidata.variable_table(data))` exactly) — and runs one loop whose single counter `N`
(`0021c028`–`0021c06a`) indexes three parallel arrays at once: the variable table itself
(`0021c032 ldr.w r0,[r1,r10,lsl #0x3]`, 8-byte entries), the receive-filter slot array at
struct `+0x10C`/`+0x144` (`0021c054`/`0021c058 strh … r3` = `0xFFFF`, N < / ≥ 0x100), and the
staleness timer array at `+0x914` (`0021c05c str.w r2,[r10,#0x914]`). It also registers entry N's
low-18-bit DGN with the CAN receive filter (`0021c03e bl 0x0021a618`). **The loop never touches
object-table 1 — there is no object walk in this function.** So the only join it establishes is
**slot index ≡ variable index** (identity, by the variable table's own construction order), not
object ↔ slot. Four coincidental `+0x10c` hits found by a base-register-blind scan
(`FUN_0021e53c`/`FUN_00218d38`/`FUN_00218f94`/`FUN_0021a738`) were checked and excluded: each has a
different base (a transfer descriptor, an allocator global, a separate registration-block global)
or a different field width (u32/byte vs. the receive struct's u16), confirmed via
`ghidra/resolve_bases.py` (`build/slot-10c-others.txt`).

A rank-ordered candidate rule ("the Nth on/off object, in object-table order, reads variable
`base+N`") was hand-tested against the oracle
(`{228:19, 229:68, 247:82, 255:39, 277:75, 278:24, 280:23, 281:25, 282:18, 295:86}`,
`statusjoin.known_status_pairings`) and falsified: the variable index is not monotone in object
index (247→82, then 255→39; 277→75, then 278→24, 282→18), and adjacent objects 228→19, 229→68 jump
by 49 while 247→82 jumps only 14 across an 18-object gap — no constant offset, no affine fit.

**Part 2 — the GuiCode render read site never indexes the receive-filter array, and the on/off
flag is a propagated constant (GuiCode, image base `0x00240000`; render state SRAM
`0x200301B0`).** Every `ldrh [base,idx,lsl #1]` site in the image (26 total,
`build/hw-index-sites.txt`) was based-provenance-checked against the GuiData literal pool: 6+1+9
sites in `FUN_002519f0`/`FUN_002557c0`/`FUN_002559a0` are glyph/font ink-profile metrics (idx =
glyph code); 4 in `FUN_00255f80`/`FUN_00255fc4` are the 8-image descriptor table (idx = image
index); 2 in `FUN_00256c54` (`0x257b44`,`0x257b60`) scan the type-6 option value-match table
`DAT_00257bf8 = 0x9004562C` (idx = switch child cursor); 3 in `FUN_00257bfc`
(`0x257d68`,`0x257f04`,`0x258238`) index the render state's own layout register file
`state+0x9E` (idx = a record's layout register, `state+0x703a…`). **None indexes the
receive-filter slot array** (`0x20007EBC+0x10C` = `0x20007FC8`).

The actual on/off appearance selector is the flag at render-state `state+0x704f`, read by
`FUN_00256C54` to choose between style pair `state[0x19]/state[0x1a]` (resting/OFF) and
`state[0x1b]/state[0x1c]` (ON). `+0x704f` is written **only** from `FUN_00256C54`'s own
`param_1` (`==1 → 1`, else `0`; lines 872–879), and `FUN_00256C54` has exactly **two** call
sites (`find_flag_origin.py`, `build/flag-origin.txt`): `0x2580a2` inside `FUN_00257bfc`
(`mov r0,r8` — `r8` is `param_2`, propagated unchanged from `FUN_00258650`→`FUN_00258330`→
`FUN_00257bfc` with no intervening computation), and `0x258902` inside `FUN_002588a8`
(`movs r0,#0x2`, a literal). Every root passes a constant (`FUN_002588a8`→2,
`FUN_00258650`→2 at `FUN_00257bfc(iVar7,2)`), and every other write to `+0x704f` in the image
(`FUN_00258650:567`, `FUN_002588a8:2588fa/2589d4`, `FUN_00258c60:25`) is the constant 0. **The
flag is a render-pass constant, never indexed by a widget-record field and never sourced from a
slot read.**

The one value-driven read path the renderer does have, `FUN_00251E38(state[9], state+0x91)`,
dereferences `state[9] = *(u32*)(0x90044BC8 + value_slot*4)` (`FUN_00252CB0:673/685/697`,
`records.WRecord.value_slot` — the Finding 17 table), and every resolved entry lands in
`0x2003xxxx` (option/config/theme SRAM), never in the receive-filter region. `value_slot` fails
the oracle on both counts: where present it disagrees (obj 247 Bath Fan carries `value_slot=101`
→ `0x2003a268` on every record, oracle variable is 82; obj 255's `value_slot`s 146/77 vs. oracle
39), and it is **absent entirely** on three of the ten oracle objects — 229, 282, and **295
(Water Pump, oracle variable 86)** — each of which nonetheless has a working indicator, so a
field missing on 3/10 rows cannot be the join.

**Clinching exhaustiveness check — enumeration + literal-availability, not enumeration alone.**
Beyond the 26 halfword-index sites, a full-section scan of the entire GuiCode payload for
little-endian literal references into `[0x20007E00, 0x20008400)` finds **zero** landing in the
298-entry slot array `[0x20007FC8, 0x2000821C)`, and the receive struct base `0x20007EBC` itself
appears **nowhere** in GuiCode (2 stray literals sit just above the array; 1528 literals point
into `0x2003xxxx` by contrast) — independently reproduced across both dig tasks. GuiCode and the
LCD firmware are **separately-based images sharing no linkage other than the struct-base argument
passed at call time** (§5.4a), so this is not merely "the sites we found don't reference it": no literal-pool or in-data reference to the slot array or struct base exists in GuiCode. (A raw little-endian value scan cannot by itself exclude a `movw`/`movt`-synthesized address — the split immediate never appears as a contiguous constant, per CLAUDE.md's standing scan rule.) The addressing-form-agnostic guarantee is the **26-site `ldrh` enumeration** above: every halfword-index load in the image was found and its base traced by provenance regardless of how that base register was formed, and none resolves to the receive-filter array.

**Result.** The widget → receive-filter status-slot join is **not a decodable per-object GuiData
record field** — closes the record-field question `proven-from-code`, NEGATIVE. It does *not*
prove the join unrecoverable in principle: `FUN_0021BE50` establishes slot ≡ variable identity,
and the missing indirection (object → variable index, demonstrably non-monotone and therefore
carried per-object somewhere) is positional/architectural, decided by the unread 25 KB
`FUN_00247BF8` / the CAN-config path (`inferred-from-code` — the only claim in this finding not
backed by a direct read, because that function has not been read). The exit, if ever pursued, is
reading `FUN_00247BF8`.

**Consequence for the new-HOME-output-button feature.** A live on/off indicator on a freely
cloned/inserted button is **not byte-deliverable** from the record alone
(its design note (not published) Phase 1). The predictor
`src/fireflymcf/statusjoin.py::predicted_status_slot` correctly stays `NotImplementedError`, and
`tests/test_status_join.py::test_predictor_reproduces_every_known_pairing` stays
`xfail(strict=True)` — a defensible negative, not a fitted guess.

**Refined by Finding 33 (2026-08-16):** the join IS recoverable by joining GuiData→cell→GuiCode;
a live *value* for a bridged instance is byte-deliverable via `value_slot`, though a coloured
indicator needs a relocator pointer-table insertion.

## Finding 33 — the widget→status join is a cross-image slot-base argument + hard-coded reads in FUN_00247BF8 (refines Finding 32)

`proven-from-code`, 2026-08-16 (status-cell-writer dig, branch `status-cell-writer-dig`;
`build/status-cell-writer-notes.md`, `build/status-cell-writer-report.md`, independently reviewed
and corrected). Reads the function Finding 32 flagged as unread and inferred must hold the
answer — `FUN_00247BF8` — and closes the apparent paradox left standing: Finding 32 proved
`proven-from-code` that GuiCode holds **no literal-pool reference** to the receive-filter slot
array, yet widgets visibly track live RV-C status. The two facts are consistent: the slot array
reaches GuiCode as a **register argument carried across the LCD→GuiCode call boundary**, never
as a literal, and it is consumed inside `FUN_00247BF8` — not inside the renderer
(`FUN_00256C54`) Finding 32 Part 2 correctly cleared.

### 1. The slot-array base is a cross-image argument (proven-from-code)

LCD Firmware's paint-tick call site (image base `0x00210000`) loads header-vector entry `+0x10`
(`GuiCode_entry1`, `0x0024EE28`) and calls it with the receive-filter slot-array base as its
argument:

```
0021cbf6  ldr r3,[0x0021cc38]   ; r3 = 0x00240000 (GuiCode header vector base)
0021cbfc  ldr r3,[r3,#0x10]     ; r3 = GuiCode_entry1
0021cbfe  ldr r0,[0x0021cc3c]   ; r0 = *0x0021cc3c = 0x20007fc8
0021cc00  blx r3                ; GuiCode_entry1(r0)
```

`0x20007fc8 = 0x20007ebc + 0x10c` — the receive-filter slot-array base, `0x20007ebc` being
Finding 32 Part 1's receive struct (`MCF-FORMAT.md` §5.4a). `GuiCode_entry1` holds it in a
callee-saved register and passes it unchanged into the 25 KB builder:

```
0024ee28  push {r3,r4,r5,lr}
0024ee32  mov r4,r0            ; r4 = slot base
0024ee3c  bl 0x00247bf8        ; FUN_00247BF8(r0=slot base)
```

**The light→pump follower detours this very `bl` — `observed-on-hardware`, 2026-08-31.** `0x0024EE3C`
is a **single-caller** `bl FUN_00247BF8` (the per-tick paint fn), which makes it a clean hook: the
`follow_channel` recipe op retargets it to an authored routine appended at **`0x00259BB4+`** (blob
layout: two 12-byte CAN templates, then the edge-detect routine, then a 4-aligned literal pool of
u32 absolute addresses — 13.6 has **no `movw`/`movt`**, so every 32-bit constant is materialised
from that pool). The routine edge-detects a watched G12 output's status, transmits one gated CAN
frame on a transition, then **tail-calls** the displaced `FUN_00247BF8` (`push {r0-r3,lr}` … `pop.w
{r0-r3,lr}` + `b.w 0x00247BF8`, never `pop {pc}`), so the per-tick paint path is preserved. **Flashed
and confirmed on the coach (v7):** each bath-light edge is matched at the same timestamp by a
panel-sourced `19FEDB9F#2C…` frame, the pump follows, and a light-off cold boot is silent — so the
detour, the appended routine, and the grown-GuiCode flash all work on hardware. The routine reads the
watched channel's state the firmware's own way: the receive-filter slot (`0x20007FC8+var*2`, §5.4c),
the `0xFFFF` "no data" sentinel as off, and the 3-bit operating-status field (`ubfx #2,#3`; on iff
field == 1) exactly as ch25's own indicator block at `0x248afc` decodes it. Full seven-round arc:
the reference coach's build log (not published).

The edge latch is SRAM **`0x20039F68`** — spare `+0x44BC8` colour cell **211**. The obvious first
spare, cell 203 / **`0x2003A2F8`**, is *widget*-spare but **firmware-live**: `FUN_00247BF8` loads it
via `ldr r2,[pc,#0x2f8]` at **`0x0024A600`** (inside the very function we detour), so latching there
would corrupt working memory every tick. `followchannel.resolve` therefore picks the first spare
cell whose SRAM address GuiCode references **nowhere** — scanning both raw little-endian u32 pool
words and T1 `ldr rX,[pc,#imm8]` targets, at both halfword alignments. Of 13.6's 16 spare cells only
7 (cells 211/213/215/217/219/221/223) are GuiCode-unreferenced; the scan is encoded as a standing
test (`tests/test_followchannel.py::test_latch_cell_is_referenced_by_nothing_but_our_routine`, which
also asserts 203 *is* referenced, so the skip is provably load-bearing). One residual the literal
scan cannot exclude: a base+offset table read of the latch address whose base is not itself the
literal — that awaits hardware validation.

This is exactly the linkage Finding 32 named without pursuing ("GuiCode and the LCD firmware are
separately-based images sharing no linkage other than the struct-base argument passed at call
time") — it is real, and it runs through `FUN_00247BF8`, not through the renderer.

### 2. `FUN_00247BF8` reads `slot[var]` at a hard-coded immediate, per widget (proven-from-code)

For each status widget, `FUN_00247BF8` does `ldrsh.w r2,[r4,#2*var]` — `r4` is the slot base
carried in from entry1, and **the variable index is a hard-coded immediate**, compiled per
widget, not read from any GuiData field. Worked example, obj 247 (Bath Fan/Starlink), oracle
variable 82:

```
00248dd2  ldrsh.w r2,[r4,#0xa4]   ; r2 = slot[82]  (0xa4 = 2*82)
00248dd6  ubfx r2,r2,#0x2,#0x3    ; dimmer operating-status field (latch vars: cmp #1 instead)
00248dda  cmp r2,#0x4             ; ON test (OFF: #0x1)
          beq ...                 ; branch picks the ON vs OFF *source* colour
00248de6  ldr r1,[0x00249024]; ldr r0,[r1]; ldr r1,[0x00249040]; str r0,[r1]   ; -> colour cell
00248dfc  ldr r1,[0x00249044]; strb r2,[r1]                                    ; -> bool cell
```

The `beq` target loads a *different* source colour into the *same* destination cell — so on/off
appearance is which colour the writer puts in the cell, selected by the live slot value read
that paint. This runs unconditionally every paint (the function is gated only by a frame-counter
wrap at `0x247c0e` and the global display-enable byte `GuiCode_entry1` tests at `0024ee2a` — no
per-widget or per-page gate).

### 3. The destination cells are exactly the render's Finding-17/18 cells (proven-from-code)

Destinations for var 82: `*0x00249040 = 0x20039b98` (colour), `*0x00249044 = 0x2003a268`
(bool). Using `cell(idx) = u32 @ GuiData +0x44BC8 + idx*4` (`DAT_00253D04`, Finding 17's table,
the same table Finding 18 identified as the mode-7 colour table):

- `0x20039b98 = cell(144)` = obj 247's **mode-7 fillA index 144** (Finding 32's own recorded
  cell for obj 247).
- `0x2003a268 = cell(101)` = obj 247's **`value_slot` 101** cell (Finding 32's own recorded
  cell for obj 247).

Both match Finding 32's cells for obj 247 exactly. So the join closes end-to-end: the render
reads a widget's `value_slot`/mode-7 colour cell (a decodable GuiData field, Findings 17/18);
`FUN_00247BF8` writes that identical cell from `slot[var]` every paint, where `var` is a
hard-coded immediate compiled into the corresponding `FUN_00247BF8` block. Cells are shared
(cell 101 is referenced by objects 42, 547–549, 553, 855 as well as 247) — a per-*cell* join,
many objects to one cell to one bridged variable, which is exactly why Finding 32 could not find
a clean per-object record field: there isn't one, and the join instead runs through the cell.

Reproduced (hand-checked, `proven-from-code`) for obj 255 (var 39 → `cell(146)` =
`0x2003a398` ← `slot[39]`, read `@0x248a72`) and obj 295 (var 86 → `cell(160)` = `0x20039eac`
← `slot[86]`, read `@0x248e5c`). All ten `known_status_pairings` oracle variables
{18,19,23,24,25,39,68,75,82,86} have a matching `ldrsh [r4,#2*var]` read in `FUN_00247BF8`.

### 4. Object → variable rule (recoverable, not stored)

Slot index ≡ variable index (LCD-init identity, Finding 32 Part 1 — unchanged). The missing
indirection Finding 32 left open — object → variable — is **not** a GuiData field and **not**
positional; it is the immediate `2*var` compiled into the `FUN_00247BF8` block whose store
targets that object's cell. It is recoverable from the MCF because the MCF holds both halves:
(a) GuiData gives the object's cell (`value_slot`/mode-7 index → `cell()`); (b) GuiCode gives
the `FUN_00247BF8` block whose store targets that cell, and its `ldrsh [r4,#imm]` gives
`var = imm/2`. Demonstrated for objs 247, 255, 295.

### 5. Feature consequence — refines, does not overturn, Finding 32's negative

`variable.channel` is the filtered RV-C instance. Instance **17 → variable 34**
(`DC_DIMMER_STATUS`, `0xfeda`, in the display list) — proven from `vt[34].channel == 17`. Var
34's read block, `0024891c ldrsh [r4,#0x44]`, writes colour to `0x2003a2d8` and bool to
`0x20039e21 = cell(69)`. Cross-check: `value_slot 69` is consumed by objs 344/348, the "G12
OUTPUTS" diagnostics row labelled `"17 - AWN/PATIO EXT"` — confirming instance 17's live value
genuinely reaches a render-visible cell.

**The catch.** Var 34's *bool* cell (`cell(69)`) is indexed in the 666-entry `0x90044BC8`
pointer table, so its **value/bool** is reachable from a GuiData `value_slot` field. Its
**colour** cell, `0x2003a2d8`, has **no entry** in that table — unlike var 82, whose colour cell
(`cell(144)`) *is* indexed. So:

- A live instance-17 **value/bool** is byte-deliverable from GuiData alone: point a cloned or
  inserted widget's `value_slot` at 69, a relocator-writable record field
  (its design note (not published)). This partially overturns
  Finding 32's blanket "not byte-deliverable" — for a bridged instance's raw value/bool, it now
  is.
- A live **coloured** on/off indicator (Water-Pump-style) for instance 17 is **not** a simple
  field edit: its colour destination has no pointer-table slot to name, so delivering it needs
  the relocator to **add a new entry to the `0x90044BC8` pointer table** aimed at `0x2003a2d8`
  — a larger, but still same-image, GuiData edit, not a record-field edit.
- Finding 32's separate finding that `+0x704f` (the on/off style-pair selector) is a propagated
  render-pass constant stands: the bool cell alone does not drive *appearance*, only whichever
  value a `value_slot`-bound widget reads. Coloured on/off appearance is driven by which colour
  `FUN_00247BF8` writes into the mode-7 cell, which is the mechanism above, not the `+0x704f`
  flag.

**Net verdict: PARTIAL.** The mechanism is now fully recovered (`proven-from-code`) and closes
the "exit" Finding 32 named. A bridged instance's live value/bool is byte-deliverable via
`value_slot` alone; a bridged instance's *coloured* indicator needs one relocator pointer-table
insertion; an instance whose variable has no `FUN_00247BF8` read block at all needs a GuiCode
code patch (out of scope for the relocator), unchanged from Finding 32.

**Caveat on the survey tooling.** `ghidra/extract_slot_bridge.py`'s scan over-attributes stores
(it assigns every store between one matched read and the next to the earlier variable), so
wide/multi-destination rows in `build/gc-slot-bridge.txt` are noise; only the tightly-scoped
two-destination rows (vars 34, 82, 39, and the hand-checked 86) are trustworthy as read.
Counting "how many of the ~70–75 status variables are bridged" is consequently approximate
(reviewed independently to ~44 of ~70, versus the dig's own ~45 of 75; the DGN-level grouping
used to produce either count is not reproducible directly via `guidata`) — the mechanism and
the individual hand-checked pairings (34, 82, 39, 86) are solid regardless of that count.
`statusjoin.predicted_status_slot` is left unimplemented here, matching Finding 32's own
"deliberately left unimplemented" — a robust `FUN_00247BF8` disassembly that steps over its
literal-pool islands is the remaining work to make this a coded predictor instead of a hand
join.

## Finding 34 — a runtime-state colour cell's RESTING default is a copy from a single-constant master; idx145 = navy (39,40,98)

**Context.** Finding 18's `theme.mode7_palette` extracts a mode-7 chrome colour only when the
theme-init stores a **single RGB888 constant directly** into the cell (`ldr rD,[=cell]; …; str
rV,[rD]`). It deliberately **omits** cells written any other way — the cardinal rule, never
guess. One such omitted class is the **button on/off indicator cells**, and `render.py` had been
filling their *resting* colour by a hand-curated `_RESTING_FILLS` dict. idx145 (instance-15's
indicator cell `0x2003a2fc`, the target of `newbutton.retarget_indicator`) was entered there as
`(65,81,163)` **inferred by analogy** to sibling indicator cells. This finding shows that
resting default is in fact **byte-derivable**, and that the inferred value was **wrong**.

**The mechanism (`proven-from-code`, 13.6).** idx145's cell `0x2003a2fc` is written at exactly
**one** site, `0x2488f0`, inside `FUN_00247bf8`'s theme/state-init:

```
0x2488e0  ldrsh.w r2,[r4,#0x40]     ; r4 = receive-filter slot base (Finding 33); read live state
0x2488e4  cmp     r2,#1
0x2488e6  beq.w   0x249272          ; state==1 -> ON override
0x2488ea  ldr     r1,[=0x20039974]  ; (fall-through / resting) master cell
0x2488ec  ldr     r0,[r1]           ; r0 = *master     <- a COPY, not a literal
0x2488ee  ldr     r1,[=0x2003a2fc]  ; idx145's cell
0x2488f0  str     r0,[r1]           ; *0x2003a2fc = r0
...
0x249272  ldr     r1,[=0x2003a1a0]  ; ON master (runtime, no clean constant)
0x249276  b.w     0x2488ee          ; -> same store
```

So the cell is a **two-state select** on the widget's live slot: **resting/off** copies master
`0x20039974`; **active/on** copies master `0x2003a1a0`. The override branches *into* the
cell-load (`0x2488ee`), so the two instructions before it are always the **fall-through
(resting) copy** — they name the resting master regardless of the override.

Master `0x20039974` is initialized **once** (`0x248328`: `ldr r3,[=0x20039974]; ldr
r2,[=0x00272862]; str r2,[r3]`) with the single constant **`0x00272862` = (39,40,98)** — the
dark navy (same value as chrome idx8). Hence idx145's resting default = **(39,40,98)**, NOT the
`(65,81,163)` button blue previously inferred. The ON master `0x2003a1a0` has no single-constant
store (it is copied/computed) → correctly undecodable, stays runtime.

**Why `mode7_palette` couldn't reach it.** Its tracer clears a register on any `ldr rX,[rY]`
memory read, so it cannot follow a value delivered by **copy from another cell**. The chain here
is two levels (`const → master cell → copy → indicator cell`); the tracer models one. Omitting
the cell was the correct defensible negative — the value simply had to be recovered one level up.

**The extractor: `theme.resting_defaults(data) -> {index: (r,g,b)}`.** A resyncing single pass
finds each colour cell's fall-through copy master (`_copy_store_masters`); a second pass resolves
every PC-relative SRAM address that holds a single constant (`_trace_constant_stores` seeded with
`_pcrel_sram_addrs`); a cell is reported **only** when it has exactly one default master and that
master holds exactly one RGB888 constant. Indices already in `mode7_palette` (direct constants)
are excluded. **Validation:** on 13.6 the resolver independently reproduces **every** curated
`_RESTING_FILLS` value it overlaps (idx120/131/133/144/160/167/168/376/377/378 — 11 indices,
zero disagreement), and corrects idx145 to (39,40,98). Cells whose default master is not a single
constant (idx455 Tank Heater — a deeper copy chain) or whose init is a value-match (idx375
CLIMATE header — active-tab branch) are **omitted**, not guessed, and stay curated. `render.py`'s
`_merged_resting_fills` overlays the derived values onto the curated dict, scoped to already-
curated indices, so the painted set is unchanged and only idx145's colour moves.

**Honest weight.** The *mechanism* and idx145's derivation are `proven-from-code`. The reported
value is the **resting/default only** — the live on-state (master `0x2003a1a0`) is runtime,
bus-driven via the Finding-33 slot, and never claimed here.

### Addendum, 2026-08-18 — the curated scope was the limiting factor, not the resolver

`render.py`'s `_merged_resting_fills` originally overlaid the derived values onto the curated
dict **scoped to already-curated indices**, "so the painted set is unchanged". That caution
turned the hand-entered list into the thing deciding what paints, and it was incomplete: the
**FLOORPLAN SELECTION** buttons (page object 19 on 13.6) went unpainted for exactly that reason.

Their construction is the EXT/RET shape of Finding 29, one level deeper. Each button is a
**wt-5 wrapper record** in object 19 carrying `fillA` mode-7 idx **24..34** and a `child_index`
to a two-record object — `[wt-33 box 130×50 r5 with fillA **mode 0**, wt-11 caption]`. The box
inherits the wrapper's index through the parent-scoped colour slots, so the colour is entirely
byte-derived; nothing was missing but the palette lookup.

| idx | button | resting master | constant |
|---|---|---|---|
| 24, 25, 26, 27, 29 | RA, RD, RT, CB, RY | `0x20039A58` | `0x4151A3` = **(65,81,163)** button blue |
| 30, 31, 32, 33, 34 | WA, WD, WT, TB, SY | `0x20039A58` | `0x4151A3` = **(65,81,163)** |
| **28** | **Ship Mode** | `0x2003A1A0` | `0x00AEEF` = **(0,174,239)** cyan |

**Ship Mode's resting default is the highlight cyan** — the same master every ON/selected branch
loads (§5.4c). `proven-from-code` as a statement about the *resting* value. The obvious reading —
that the panel boots with Ship Mode shown as selected, and the runtime moves the highlight when a
floorplan arrives over CAN (Finding 19) — is **`inferred`, not established**: no branch writing
these cells from the floorplan value has been read. A render of this page therefore shows the
**boot state**, not the coach's; on a WD coach the panel would be expected to highlight WD.

The scope was dropped after checking the thing that justifies dropping it: of the 13 curated
indices, **11 are derivable and all 11 match exactly**; only idx455 and idx375 are not derivable
and stay curated. Re-rendering all seven pages of 13.6 changed **two**: this page (11 buttons) and
SETTINGS's "24 Hour" toggle, which the `SETTINGS.jpg` photo oracle confirms **is** a filled blue
box on the coach. HOME, LIGHTS, ELECTRICAL, CLIMATE, OPTIONS and OUTPUTS are pixel-identical.
One ordering constraint fell out: `_battery_fill` must run **before** `_resting_fill`, because
idx202/206 now resolve in both and the voltage-state branch is the more specific reading.

---

## Finding 35 — the G12-OUTPUTS dynamic row names are gated by hardcoded per-floorplan whitelists, and no floorplan populates all four tank heaters (open defect)

*2026-10-19: Finding 81 runs this whole block (all 14 dynamic rows, not only these four) under every
floorplan and option, and reproduces this table.*

**Status: open TODO.** Diagnosed 2026-08-18 from the owner's coach (floorplan **WD**, all four
tank warmers physically fitted): the `G12 OUTPUTS` diagnostics rows **05** and **08** show
`Tank Heat Fresh` / `Tank Heat Grey 2`, while **06** and **07** stay blank. Not a config
problem, not a decode gap — a firmware oversight, reproduced exactly from the bytes.

A dynamic row's name is a **type-9 string cell** filled at boot by GuiCode from the name table
at `0x00259278` (`MCF-FORMAT.md` §6.1, corrected the same day). The filler is a straight-line
block at **`0x002494b2`** which reads the **floorplan** register `r5 = [0x2003a77a]` and gates
each of the four tank-heater names behind **its own hardcoded whitelist of floorplan codes**.
The four are not symmetric:

| cell | row | name | condition (13.6) | floorplans |
|---|---|---|---|---|
| `0x20039b04` | 05 | `Tank Heat Fresh` | `uxtb(r5-7) <= 2` @`0x2494b6` | WA, WD, WT |
| `0x2003a244` | 06 | `Tank Heat Grey` | `r5 ∈ {1,4,5,6,9,10}` @`0x2494c4..0x2494e6` | RT, SY, TB, CB, WT, RY |
| `0x2003a834` | 07 | `Tank Heat Black` | `r5 ∈ {1,9}` @`0x24951e` **and** `[0x2003a4b4] == 1` | RT, WT + the **Black Tank** option |
| `0x20039978` | 08 | `Tank Heat Grey 2` | `r5 == 7 ∨ uxtb(r5-2) <= 1 ∨ r5 == 8` @`0x249536..0x249544` | RA, RD, WA, WD |

`0x2003a4b4` is confirmed the **Black Tank** option register (`options.option_bindings`
→ `register=537109684`). Each write is an `ldm`/`str` block copying the name in place; the
not-selected path stores a `0` byte, leaving an empty string — which is why the row renders as
a bare `NN - ` prefix.

**Simulating the transcribed branch logic over all 14 floorplans** (Black Tank enabled):

```
 RT 2/4   RA 1/4   RD 1/4   SY 1/4   TB 1/4   CB 1/4   WA 2/4
 WD 2/4   WT 3/4   RY 1/4   4NJ 0/4  3NG 0/4  3NK 0/4  3NJ 0/4
best = 3/4 (WT);  floorplans reaching 4/4: NONE
```

**No floorplan can populate all four rows.** WD resolves to Fresh + Grey 2 and is simply absent
from the Grey and Black whitelists. The prediction was made from the bytes and matches the
owner's screen exactly — that agreement is the validation, not a fit. Corroborating: the HOME
page renders all four tanks (FRESH/GREY/GREY2/BLACK) for this coach, so the rest of the
firmware knows they exist; only these diagnostic names disagree.

**Not claimed:** 14.7 runs the same mechanism with a wider Grey list, but its floorplan codes are
a different lineup (9 plans, no WD), so it is **not** evidence upstream fixed this.

### The TODO — SUPERSEDED by a wider patch, 2026-08-18

*The two 1-byte edits below were the minimal 3/4 fix. The owner asked instead for all four rows on
the six Ethos/Swift floorplans — **WA, RA, WD, RD, WT, RT** (`MCF-FORMAT.md` §7.1) — accepting
collateral damage to floorplans this coach never runs. That is `build/build-tankheat-v10.py`:
**9 same-size GuiCode edits, 26 bytes**, GuiData byte-identical.*

**How the target set is reachable at all.** `{1,2,3,7,8,9}` is the union of two contiguous runs, so
no single `uxtb(r5-K) <= N` expresses it — but `uxtb(r5-1) <= 8` covers 1..9, which is the target
plus SY/TB/CB, and the owner authorised losing those. Grey needed no collateral: its whitelist has
exactly six equality slots, so repointing the four spare ones lands precisely on the target.

| row | edit | result |
|---|---|---|
| 05 Fresh | `subs r3,r5,#7`→`#1` @`0x2494b6`; `cmp r3,#2`→`#8` @`0x2494ba` | {1..9} |
| 06 Grey | `cmp r5,#6`→`#2`, `#4`→`#3`, `#5`→`#7`, `#0xa`→`#8` (keeps 1 via r7, and 9) | **exactly {1,2,3,7,8,9}** |
| 07 Black | the 10-byte `cmp/it/orreq/cbz` @`0x24951e` → `subs r3,r5,#1; uxtb; cmp #8; nop; bhi` | {1..9} ∧ Black Tank option |
| 08 Grey 2 | `subs r3,r5,#2`→`#1` @`0x249536`; `cmpne r3,#1`→`#8` @`0x24953e` | {1..9} |

**Cost:** SY/TB/CB gain three names they should not have (1/4 → 3/4, cosmetically wrong on
floorplans this coach never selects); **RY loses Grey** (1/4 → 0/4). Ship Mode and the four
unoffered codes are unchanged. Row 07 still requires the **Black Tank option**, the firmware's own
gate at `0x249528`, left deliberately intact.

**Verification is by emulation, not by eye — and that mattered.** `src/fireflymcf/tankgates.py`
runs the real instructions, so the same code simulates stock and patched. Three earlier hand-models
of this block produced confident, plausible, **wrong** whitelists: an `ite` predication bug, a
dropped conditional-branch form, and — the subtle one — IT-block state lost by disassembling one
instruction at a time, which makes capstone report `movne`/`moveq` as two unconditional `movs` so
both arms execute. The emulator is only trusted because it reproduces **two independent oracles** on
the stock image: the coach's WD = rows 05+08 only, and this finding's own "best 3/4, none 4/4".
`tests/test_tankgates.py` pins both.

**Status: CONFIRMED ON HARDWARE 2026-08-18** (`build/mcf-tankheat-v10.bin`, "Jayco B-Van V13
(Custom v10)"; `BUS-BASELINE.md` PC.18b). All four rows populate on WD, and row 07 tracks the
**Black Tank option** live — visible when selected, gone when deselected — confirming the
`[0x2003A4B4] == 1` gate at `0x249528` that the patch left intact, and that the option register is
re-read per render rather than latched at boot.

**The panel flashes GuiData and GuiCode together — the pair cannot be split** (owner, 2026-08-18;
`MCF-FORMAT.md` §4.1). So "a GuiCode-only build" describes the *edits*, not what gets written: v10
is built from **stock**, its GuiData carries no Pin-15 button, and flashing it replaced the
customised GuiData and removed the button. `build/mcf-pin15-tankheat-v11.bin` applies the same 9
edits on top of v9 instead, keeping both features. The enforced pairing is also what makes a
self-consistent build safe to flash: the PC.18 Result 4 boot loop came from a build whose *own*
GuiCode literals were stale for its *own* grown GuiData, not from mismatched sections.

*Superseded minimal version, kept for the record:*


Both gates are narrow `cmp r5,#imm8`, so retargeting a floorplan the coach does not use costs
one byte each:

| GuiCode addr | file (13.6) | now | to | effect |
|---|---|---|---|---|
| `0x002494d8` | `0x18d58` | `04 2d` (`cmp r5,#4`, SY) | `08 2d` | WD gains **Tank Heat Grey** |
| `0x0024951e` | `0x18d9e` | `09 2d` (`cmp r5,#9`, WT) | `08 2d` | WD gains **Tank Heat Black** |

**It is a trade, not a free fix**: the whitelist has no spare comparison slot, so SY loses Grey
and WT loses Black. Acceptable on a personal build; it would not be an upstream patch.
`0x002494dc` (`cmp r5,#5`, TB) is the alternative donor for the Grey gate.

Sequencing note: keep this as its own **same-size** build, separate from any relocating build.
*(That note originally cited the relocator's first hardware trial as still outstanding — it was
confirmed on 2026-08-17, `BUS-BASELINE.md` PC.18. The advice stands anyway: one variable per
build.)* Changing GuiCode means the flasher must load the **GuiCode** section, not GuiData alone.

---

## Finding 36 — the FLOORPLAN SELECTION highlight is a value-selected colour cascade

`proven-from-code`, 2026-08-18 (13.6). Closes the `inferred` note left by the Finding 34
addendum: *what* moves the cyan highlight from `Ship Mode` to the selected floorplan.

**The block.** `FUN_00247bf8`, `0x24987c`–`0x249912`. It reads the floorplan byte and then runs a
straight-line chain of eleven per-button colour writes:

```
0x24987c  ldr   r3,[=0x2003a77a]   ; the floorplan register (slot 5, Finding 17)
0x24987e  ldrb  r3,[r3]
0x249880  cmp   r3,#0
0x249882  bne.w 0x2499b8           ; not Ship Mode -> stub
0x249886  ldr   r2,[=0x2003a1a0]   ; CYAN master
0x249888  ldr   r1,[r2]
0x24988a  ldr   r2,[=0x2003a378]   ; idx28 Ship Mode      <-- stub 0x2499b8 branches back HERE
0x24988c  str   r1,[r2]
0x24988e  cmp   r3,#1
0x249890  beq.w 0x2499be           ; RT -> stub
0x249894  ldr   r2,[=0x20039a58]   ; BLUE master
0x249896  ldr   r1,[r2]
0x249898  ldr   r2,[=0x20039cb0]   ; idx26 RT             <-- stub 0x2499be branches back HERE
0x24989a  str   r1,[r2]
…                                   ; cmp #2 … #0xa, same shape
```

and eleven two-instruction stubs at `0x2499b8`–`0x2499f8`, each of which loads **the other
master** into `r1` and branches back to *that button's own store*:

```
0x2499b8  ldr r2,[=0x20039a58] ; ldr r1,[r2] ; b 0x24988a   ; Ship Mode -> BLUE
0x2499be  ldr r2,[=0x2003a1a0] ; ldr r1,[r2] ; b 0x249898   ; RT        -> CYAN
```

**So the semantics are:** the fall-through path paints Ship Mode cyan and all ten floorplans
blue; a matching `cmp` diverts *one* button to the opposite colour and re-enters the chain, which
then completes normally. Exactly one button ends cyan — `Ship Mode` at value 0, otherwise the
floorplan whose value matched. Masters: `0x2003a1a0` = `0x00AEEF` **(0,174,239)** cyan (the same
master every ON branch uses, §5.4c) and `0x20039a58` = `0x4151A3` **(65,81,163)** button blue.

**The value → button map, and a three-way agreement.** The `cmp` immediates give:

| value | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| cell idx | 28 | 26 | 24 | 25 | 34 | 33 | 27 | 30 | 31 | 32 | 29 |
| button | Ship Mode | RT | RA | RD | SY | TB | CB | WA | WD | WT | RY |

That column order is `MCF-FORMAT.md` §7's enum exactly — and the three derivations are
independent: §7 counts object-table positions from `"No Floorplan Selected"`; GuiData object 19
says which fillA index each *captioned* button carries; GuiCode says which cell index each `cmp`
highlights. **This promotes §7's enum from `inferred-from-data` to corroborated by code**, and it
is asserted, not just noted — `test_floorplan_highlight_cascade_matches_the_floorplan_enum`
re-derives all three per run and fails if any disagrees.

**Values 11–14 highlight nothing.** Emulating the cascade at `4NJ`/`3NG`/`3NK`/`3NJ` leaves every
button blue: no `cmp` matches, so no stub runs. An independent confirmation from the *code* that
those four codes have no button on this screen, which §7 had only from the screen's composition.

**The extractor: `theme.selection_fills(data, register)`.** Rather than pattern-match the idiom,
it **emulates** the cascade once per selector value over the small instruction set the block uses
(`ldr` literal / `ldr [r]` / `str [r]` / `cmp` / `b`/`beq`/`bne`), stopping at the first
instruction outside it — which is the block's natural end. The result is what the firmware
actually leaves in each cell, and it is keyed by register rather than by address, so it is not a
13.6-only read. `render._selection_overrides` applies it at the floorplan value from
`slot_values[5]`, defaulting to **0** — the boot state, which is genuinely what the cells hold
until the G12's config broadcast arrives (Finding 19). `cli render --page floorplan --config
build/coach-wd.json` now highlights `WD`.

**Honest limits.** The cascade is straight-line and its masters are single constants, so this is
`proven-from-code` for the *colour each button ends up with at a given register value*. What is
**not** claimed: that this block is the only writer of those cells over the panel's life (a later
tap handler could repaint them), and anything about the 11.23 build, which has no selection screen
— `selection_fills` returns `{}` there rather than inventing one.

---

## Finding 37 — R49 is four bytes: the multi-line advance is `line_height + font cell height`

`proven-from-code`, 2026-08-18. A decode-completeness fix found while chasing text overlap on the
ALTERNATOR / INVERTER screens.

**R49 decodes four bytes, not three.** `FUN_00252CB0` reads four consecutive stream bytes and
stores them at render state `+0x94..+0x97` (`0x253d2e`, `0x253d38`, `0x253d42`, `0x253d4c`):

| offset | field |
|---|---|
| `+0x94` | per-line horizontal align |
| `+0x95` | block vertical align (Finding 26) |
| `+0x96` | line height |
| `+0x97` | **flag — "add the font cell height to the line height"** |

`records.py` consumed the fourth byte and discarded it as padding. It is not padding: the
multi-line drawer `FUN_002559A0` applies it **once, before the first advance**
(`0x255a0c`-`0x255a26`):

```
0x255a0c  ldrb.w r3,[state,#0x97]   ; the flag
0x255a10  cbz    r3, 0x255a2a       ; unset -> use line_height as-is
0x255a14  ldr.w  r2,[state,#0x98]   ; the current font
0x255a18  ldrb   r2,[r2,#7]         ; font[0x07] = cell height (Finding 14)
0x255a1a  ldrb.w r1,[state,#0x96]   ; line_height
0x255a1e  add    r2,r1              ; line_height + cell height
0x255a20  strb.w r2,[state,#0x96]
0x255a26  strb.w r2,[state,#0x97]   ; clear, so it applies once
```

So the inter-line advance is **`line_height + font[0x07]` when the flag is set**, `line_height`
otherwise. On 13.6 the flag is set on **126** records (45 in 11.23, 43 in 14.7), and the great
majority carry `line_height = 0` — i.e. the flag is how a record says "space the lines by the
font's own cell height", the case `render.py` previously had to approximate with `size + 2`.
`records.WRecord.line_height_adds_font` surfaces it and `render._effective_line_height` applies it.

**It is a general modifier, not a "use the default" marker.** A few records carry a non-zero base
*and* the flag, meaning `base + cell`. The first draft of the test asserted the pairing was
exclusive; the test caught that over-claim on all three builds, which is recorded here because the
weaker reading is the one the instructions actually support.

**Honest weight.** The advance formula is `proven-from-code`. Its **visual** effect on 13.6 is
about 1 px — every affected label is two lines with a cell height within 1-2 px of the old
`size + 2` guess — so this is a correctness/decode-completeness fix, not a visible one. It is
recorded because "the renderer guesses here" was true and is now false.

### The open half — what selects the wrapping drawer

**Not resolved, and deliberately not guessed.** Text reaches one of two primitives, and only the
second word-wraps (Finding 14):

* `FUN_002557C0` — single line, auto-sizes from the measured string. Called at `0x256e3a`, `0x256f12`.
* `FUN_002559A0` — multi-line; **does its own line-breaking on spaces / `-` / `\n` to a width
  budget**. Called at `0x256eaa`, `0x256fb8`.

Both call sites sit in `FUN_00256C54` and are reached from a switch; the predicate at `0x256e10` /
`0x256e88` reads `state+0x704c` and a signed `state+0x4`, neither of which has been traced to a
record field. **ANSWERED 2026-08-18 (Finding 38/39) — and both of those were red herrings:** the
switch is a `tbh` jump table at `0x256cd2` on the **type byte** `record+0x8a`, so type 0 gets the
single-line drawer and type 11 the wrapping one. `+0x704c` is a "has content" guard checked
identically in both blocks, and `+0x4` selects a variant *inside* the single-line case. The
overlap this section blames on the wrap heuristic turned out to be a **one-pixel** budget error,
not a dispatch question. Until it is, `render.py` keeps its **own** wrap heuristic (wrap a line wider than its
box, break on spaces — documented at the `_wrap` closure), and that heuristic is why three screens
render overlapping text: `'Desired Charge State:'` (obj 1303), `'Charging Temperature:'` (obj 1304)
and `'I/C Firmware Rev.'` (obj 922) are each **one** label in a box **20 px tall** with
`line_height = 10` and a font cell of 17-21 px, so wrapping them stacks lines 10 px apart and they
collide. Two readings fit the bytes and they are not distinguishable without the predicate:

1. those records never reach the wrapping drawer, so the panel draws them on one line and lets them
   overflow their box; or
2. they do wrap, and the panel overlaps them exactly as the render does — a firmware defect.

Reading 1 is the more plausible (a shipped screen with overlapping text is unlikely, and the 20 px
box is exactly one line tall), but "more plausible" is not evidence, and there is no photo oracle
for the ALTERNATOR or INVERTER screens. Resolving it is a bounded next step: trace `state+0x704c`
and `state+0x4` back to the record fields that set them.

---

## Finding 38 — the mode-5 transparency bit is persistent render state; and (39) the wrap budget is the inclusive box width

`proven-from-code`, 2026-08-18. Refines Finding 29, which established mode 0 = "inherit the colour
slot" and mode 5 = "sets a transparency flag, not a colour". What it did not say is that the flag
is **persistent state on the same footing as the slot** — and that mode 0 inherits *both*.

Each colour-mode case in `FUN_00252CB0`'s block-B (fillB, render state `+0x68`) ends by writing
the slot **and clearing the flag**; mode 5 sets it and writes no colour; mode 0 has no case at all,
so it writes neither:

```
mode 1  ldr r2,[=0x00322b42] ; str r2,[r3,#0x68] ; ldr r2,[r3] ; bic r2,r2,#2 ; str r2,[r3]
mode 2  mvn r2,#0xff000000   ; str r2,[r3,#0x68] ;               bic r2,r2,#2      @0x253500
mode 3  bl 0x252140          ; str r0,[r3,#0x68] ;               bic r2,r2,#2      @0x253514
mode 6/7 str r7,[r3,#0x68]   ; strh r6,[r3,#0x76] ;              bic r2,r2,#2      @0x253526
mode 5  ldr r3,[r2]          ; orr r3,r3,#2 ; str r3,[r2]                          @0x253532
mode 0  (no case -- neither the slot nor the flag is touched)
```

The block-A twin clears bit `0x8000` instead (`0x2535e8`, `0x2535fa`).

**So a mode-0 field that follows a mode-5 one is still transparent.** `layout._apply_colors`
previously modelled mode 5 as "this record paints nothing" — per-record — and mode 0 as "inherit
the colour and paint it". That is wrong in exactly one situation, and the ALTERNATOR screen is it:
the `CLEAR EEPROM` caption (obj 24 rec 20) carries fillB **mode 0** and follows tab captions
carrying fillB **mode 5**, so the render resolved its background to the inherited quadrant navy and
painted it **over** the olive (idx 40) button box underneath. `ALTERNATOR-SYSTEM.jpg` shows olive
with text on it and no navy. `_State.fill_transparent` now carries the flag with the same
parent-scoped save/restore as `fill_slots`.

### Wrapping — RESOLVED, and the gate this section first shipped is RETRACTED

**Retraction.** This section originally introduced a render gate — "wrap only if `line_height` is
at least the font ascent" — and argued it was the best statement the bytes supported. It separated
every oracle case, and it was **fitted**: it worked by accident, and it is now removed. What
follows is the actual mechanism.

**Which drawer a record reaches is decided by its widget TYPE, not by a hidden predicate.**
Finding 37 left this open, naming `state+0x704c` and `state+0x4` as the things to trace. Both are
red herrings — `+0x704c` is a "has content" guard checked identically in *both* blocks, and
`+0x4` selects a variant *within* the single-line case. The real dispatch is the `tbh` jump table
at `0x256cd2`, indexed by the type byte `record+0x8a` (bounded `cmp #0x2f` / `bhi`):

| type | handler | drawer |
|---|---|---|
| **0** | `0x256e10` | `FUN_002557C0` — single line, no wrapping |
| **11** | `0x256e88` | `FUN_002559A0` — **wraps** |
| 8 | `0x256ecc` | own handler |
| 13 | `0x256f7c` | own handler (dynamic string) |
| 9, 15, 20-31, 36-45 | `0x257acc` | default: draws nothing |

So **every wtype-11 label goes to the wrapping drawer** — including the ones the coach draws on
one line. The wrap therefore had to be failing on the width test, not being skipped.

**And it was — by one pixel.** `FUN_002559A0` computes the budget at `0x255a7a`-`0x255a8e`:

```
0x255a7a  ldrh.w r3,[sp,#0x18]   ; x0  (state +0x2a)
0x255a80  ldr    r2,[sp,#8]      ; x1  (state +0x2e)
0x255a82  subs   r3,r2,r3        ; x1 - x0
0x255a86  adds   r3,#1           ; ... + 1     <-- the box is INCLUSIVE of its last pixel
0x255a8e  str    r3,[sp,#0x14]   ; the budget
```

and breaks only when a measured run **strictly exceeds** it (`0x255b0c` `cmp r0,r3` / `ble` =
fits). The inclusive `+1` is the same convention Finding 14 found on the vertical axis
(`y1 = y0 + font[0x07] - 1`). With it, every case falls out with no rule to tune:

| label | measured | box w | budget | breaks? | oracle |
|---|---|---|---|---|---|
| `Desired Charge State:` | 161 | 160 | **161** | no | `ALTERNATOR-SYSTEM.jpg` — one line |
| `I/C Firmware Rev.` | 151 | 150 | **151** | no | — |
| `DEVICES/ FAULTS` | 148 | 115 | 116 | **yes** | `ALTERNATOR-SYSTEM.jpg` — two lines |
| `Bath Fan` | 92 | 80 | 81 | **yes** | the 2026-08-13 rename probe |

`render.py` used the exclusive width and so wrapped two labels that fit by exactly one pixel.
`proven-from-code`; the ascent gate is gone.

**Result on the render:** text overlap across all 47 screens drops from 3 screens to 1 (the
remaining one, SLIDES/AWNINGS `RET`/`Ignition`, is two separate labels, not a wrap). The five
photo-oracle pages — HOME, LIGHTS, ELECTRICAL, CLIMATE, SETTINGS — are **pixel-identical** before
and after both changes.

---

## Finding 40 — `coord4` is the corner radius (a trap), and a mode-2 fillA box is a FRAME

`proven-from-code` + photo oracle, 2026-08-18. Two results from the same dig, and the first is
recorded mainly as a **near-miss**.

### coord4: the arithmetic that looks like a border width, and is not

The type-0x21 (33) handler at `0x257414` reads `coord4` from render state `+0x32` and then:

```
0x257458  ldrsh.w r2,[r4,#0x32]   ; coord4
0x25745e  ble     0x257554        ; coord4 <= 0 -> plain filled rect (FUN_0025572C)
0x257466  add     r0, r2          ; x0 + coord4
0x25746c  add     r1, r2          ; y0 + coord4
0x257480  mov     r3, r2
0x257486  bl      0x25451a        ; FUN_0025451A(x0+c4, y0+c4, c4, c4, ...)
0x257492  subs    r3, r5, r1      ; x1 - x0
0x257494  adds    r3, #1          ;   + 1        (the inclusive convention, Finding 39)
0x25749c  sub.w   r3, r3, r2, lsl #1   ; ... - 2*coord4
```

`(x1 - x0 + 1) - 2*coord4` reads exactly like "interior width after insetting a border of
coord4 on each side", and on that reading `render.py`'s use of coord4 as a **corner radius** is a
decode error. It was changed to `radius=0` on that basis.

**It is wrong.** `FUN_0025451A` is called with the origin offset to `(x0+c4, y0+c4)` and `c4`
passed twice — an **arc centre and radius**, i.e. corner drawing. So `width - 2*coord4` is the
**straight span between the two corner arcs**, which is the same expression a rounded-rect
routine computes. `HOME.jpg` settles it: the Water Pump and Tank Heater buttons carry `coord4 = 5`
and are visibly **rounded**. The change was reverted; `tests/test_render.py::
test_coord4_is_the_corner_radius_not_a_border_width` pins it so the same reading cannot return.

*Worth stating plainly: the arithmetic alone supported both readings, and the disambiguating
evidence was one instruction earlier (the arc call) plus a photo. Reading `sub` in isolation is
how this project keeps almost getting things wrong.*

### A mode-2 fillA box is a frame, not a fill

The real defect behind the report. `ALTERNATOR SYSTEM`'s two value boxes — obj 1377 rec 2
("Alternator Operating State" → *Disable*) and obj 1350 rec 2 (*Faults*) — are `wt-33` with
`fillA` **mode 2** and no `fillB` at all. Mode 2 is white *as a stroke colour* only
(`_decoded_fill`'s `border` gate: resolving it as a general fill would flood every bordered box
white), so the general fill path resolved nothing and the boxes **painted nothing**. The coach
shows each as a thick white frame around a dark interior.

**The frame is LAYERING, not a border attribute — corrected 2026-08-18, same day.** The first fix
here drew such a box as a 2 px white **stroke**, with the thickness admitted as a render choice
because "the type-0x21 border-width source has not been located". The owner reported the result as
visibly too thin against the coach, which was the tell: a chosen thickness is a fitted one. There
is no border width to find, because there is no border. The box is painted **solid white**, and
the *next* record covers its middle:

| | box | |
|---|---|---|
| obj 1377 rec 2 | `wt-33` (97,350)-(397,420) | filled **white** (fillA mode 2) |
| obj 1377 rec 3 | `wt-6` switch → child at (102,354)-(392,414) | filled navy (fillB mode-7 idx 8) |

~5 px of the white box survives on every side, and that margin **is** the frame. Entirely
byte-derived: the thickness is whatever the two decoded boxes leave, with nothing to tune.

Note this only applies where a record carries fillA *alone*. A record carrying **both** fillA and
fillB (obj 1377 rec 0, the `wt-32` caption frame: fillA mode 2 + fillB mode-7 idx 3) has no
geometric gap between them, so there the border really is a stroke — and *its* width is still
unlocated, drawn at the gauge-well path's 2 px.

### By-product: the Wakespeed fault-code table, and the stray `:`

The *Faults* value is a type-6 value-match switch on **slot 38** with **44 candidates**
(objs 1306-1349) — the alternator fault-code table, entirely byte-derived:

| code | text | code | text |
|---|---|---|---|
| `0xFFFF` **default** | `<code> : Wakespeed Internal Error` | 0 | **None** |
| 1 | Fault Present | 12-15 | Batt. Temp High / Volts High / Volts Low / Max Volts Exceeded |
| 21-24 | Alt. Temp High / RPM High / #2 Temp High / Temp High Ramping | 41-45 | FET Temp High / Required Sensor Missing / No Alt. Volt Detected / Excessive Alt+Batt Volt / Alt. Max Volt Exceeded |
| 51-62 | CAN Batt. Disconnect variants | 81-89 | DC-DC Error |
| 91-92 | CAN BMS Sync Lost / Forced to Idle | 202-207 | Primary/Secondary Batt. DC-DC Over/Under Volt, DC-DC High Temp, DC-DC Misconfiguration |

So the `:` an earlier render showed was **not a defect**: with no value for slot 38 the walk falls
to the `0xFFFF` default, whose content really is `<code> : Wakespeed Internal Error`. The coach
reads `None` — slot 38 = 0, candidate 1307. Supplying `38: 0` in the preview config (now in
`build/coach-wd.json`) renders it. A render that shows the default candidate is telling the truth
about the file; it just is not the coach's runtime state.

---

## Finding 41 — the GO POWER! tab is gated on the controller count, and >5 clamps to 1 (defect)

`proven-from-code`, 2026-08-18. Raised by the owner, who noticed the tab rendering on screens the
coach does not show it on, and suspected an empty option default could force it visible. Both
halves confirmed.

**The gate.** On `NETWORK DIAGNOSTICS` (obj 14), `ALTERNATOR SYSTEM` (obj 24) and the diagnostics
grid (obj 35) the tab is a **type-6 value-match switch on slot 39** with two candidates:

| candidate | match | content |
|---|---|---|
| obj **359** | `0x0000` | **empty** — the tab is hidden |
| obj **358** | `0xFFFF` **default** | the `GO POWER!` tab |

So the tab shows for **any value except exactly 0**. Slot 39 → SRAM `0x2003A4E9` (type 1), and it
is the same slot the OPTIONS row `'Go Power! Controllers:'` displays (obj **1236**, a wt-8 value
widget on slot 39). The gate really is "controllers > 0", exactly as the owner described, and
`ALTERNATOR-SYSTEM.jpg` shows the tab absent — this coach reads 0.

**The defect.** `FUN_00247BF8`'s config-payload unpack takes the count from payload byte `0x0B`
and clamps it at `0x247c48`:

```
0x247c48  ldrb r2,[r3,#0xb]   ; controller count from the CAN config payload
0x247c4a  cmp  r2,#5
0x247c4c  bls  0x247c50       ; <= 5: store as-is
0x247c4e  movs r2,#1          ; > 5: clamp to ONE
0x247c50  strb r2,[=0x2003a4e9]
```

**Any value above 5 becomes 1, not 0.** So an unset or out-of-range count — `0xFF` being the usual
"not configured" sentinel — lands as **1 controller**, and the GO POWER! tab appears on all three
screens with no hardware fitted. The owner's "the default of &lt;empty&gt; may trigger it to be
visible" is exactly right, and the clamp is why: the safe clamp for an out-of-range count is 0
(hide), not 1 (show one phantom controller). Clamping to 5 would at least be defensible; clamping
to 1 is not.

The bound itself is right — objects 1160/1162/1166/1170/1175 are `Go Power! Controller 1`…`5`, so
5 is the real maximum, and a separate UI setter at `0x2477d0` enforces the same ceiling before
storing.

**ANSWERED 2026-08-18 by Finding 42** — and the answer is reassuring. The count an unconfigured
coach carries is not a G12 invention: it is whatever **the floorplan-selection burst wrote**, and
that value is a literal in the panel's own flash (`MCF-FORMAT.md` §8.4). `D7` defaults to **1** on
the five Generator plans (RT, RA, RD, CB, RY) — so the tab is *shown* by default there — and to
**0** on WA, WD, WT, SY and TB. WD writes 0, which is exactly why this coach's ALTERNATOR screen
has no GO POWER! tab.

So the clamp defect stays **latent**: nothing in the panel's own defaults is out of range, and the
`> 5 → 1` path fires only if some other writer sends a bad count. The 1-byte fix remains unbuilt
and unnecessary unless that is ever observed.

**Render.** `build/coach-wd.json` now carries `39: 0`, so the preview matches the coach. Without a
value for slot 39 the walk falls to the `0xFFFF` default and shows the tab — correct behaviour for
"no runtime value", the same class as the Faults `:` in Finding 40.

---

## Finding 42 — the per-floorplan option defaults, RECOVERED: they are emitted as code, not stored

`proven-from-code`, 2026-08-18. Closes this project's **oldest strict `xfail`**, open since the
first pass, and overturns a negative that three separate findings had recorded.

### Why it stayed open, and why every earlier negative was correct

* **Finding 6** searched GuiCode for a defaults table and eliminated it "in any pattern-searchable
  form".
* **Finding 19** read the option registers' **write** side and found each is written once from a
  **received** CAN payload, concluding the state is bus-delivered and "not in the file at all".
* `guidata.find_option_defaults`'s docstring ruled out every GuiData header table, the OPTIONS row
  records, the byte-identical floorplan buttons and the whole G12 section.

**All of those still stand.** They were searches for **stored data**. The defaults are not data —
they are **emitted as code**, which is why no table exists to find. And `BUS-BASELINE.md` PC.12a
had already proved where they must live: a selection burst completes in ~50 ms with **no G12 frame
interleaved**, so the panel is not querying anyone; the values are in *its* flash.

### The chain

1. Each FLOORPLAN SELECTION button's **touch record** — in the page object (obj 19), not the
   button child — ends with a tap **action id**, and the ids are exactly `0x0898 + floorplan
   value`: Ship Mode `0x0898`, RT `0x0899` … RY `0x08A2`. *(A fourth independent derivation of the
   enum, after §7's positional reading, GuiData's `fillA` indices and Finding 36's cascade.)*
2. GuiCode's tap dispatcher — a binary-search tree of `movw/cmp/beq` with `bls`/`bhi` — branches
   to that floorplan's handler.
3. The handler emits a run of static **12-byte CAN-frame templates** from Finding 7's `.rodata`
   pool (`0x00259260`), each copied wholesale by `ldm {r0,r1,r2}` and passed to the transmit
   pointer at `0x2003986C`. This is the same mechanism Finding 23 found for the OPTIONS rows.
4. Each template is an `0x1FED9` config write:
   `00 d9 fe 19 | ff 96 <sel> 0f <val> 00 d1 ea` — PC.2's write shape exactly. **The literal
   `<sel, val>` bytes are the defaults.**

Interleaved with the writes are `0x1FEDB` command-3 (OFF) frames to the channel a rebinding option
abandons — the `E3`/`E4` behaviour PC.8 observed and Finding 23 predicted.

### Attribution needs no oracle

Every handler writes selector `0xF5` — the floorplan itself — with **its own value**, as its first
write. So `floorplandefaults.floorplan_defaults` finds handlers by "loads the template pool base"
and labels each by its own bytes. No hardcoded addresses, no dispatcher walk, and no way to
mis-attribute one: eleven independent self-checks, asserted by
`test_every_handler_writes_its_own_floorplan_value`.

### Result

All **11** handlers recovered (values 0-10, Ship Mode included), and the derived table reproduces
**90 of 90** answer-key entries from PC.12 with **zero mismatches**. The only absences are
selector `0xD8`, which PC.12a had already shown the burst never writes (its value in PC.12's table
came from *status*). Three structural predictions of PC.12a also hold without being sought:
twelve selectors per floorplan, `0x15` and `0xF7` present though not options, and `0xD8` absent.

`guidata.find_option_defaults` now returns `{option name: bool}` via Finding 23's name↔selector
join, and the three strict `xfail`s in `tests/test_floorplans.py` are removed.

**The lesson worth carrying.** The negative was never wrong; the *question* was too narrow. Every
search asked "where is the table stored?" and the answer was "there is no table — the values are
immediates in eleven handlers." Finding 23 had already demonstrated exactly this shape for the
OPTIONS rows a year of findings earlier, and the defaults search was not re-run against it.

---

## Finding 43 — the AES/AGS parameter profile lives in the template pool; Start-At defaults to 51.0 V

`proven-from-code`, 2026-08-18. Answers an owner question and closes an open note in
`BUS-BASELINE.md` PC.4 Result 4, which ended: *"The MCF decode should show where these profiles
live."* It does — they live in exactly the place Finding 42 just established for the floorplan
defaults, which is what made this a short dig rather than a long one.

**The question.** What is the AES *Start At* voltage before a user changes it, and can it be set
to 51.0 V?

**The answer: it is already 51.0 V.** The whole AES parameter profile is a run of static
`0x1FED9` write templates in GuiCode's `.rodata` pool, emitted by one handler at `0x24E500`:

| selector | meaning | template | value | physical |
|---|---|---|---|---|
| `0x2B` | — | `0x2599A8` | 5632 | — |
| `0x2C` | — | `0x2599C0` | 2048 | — |
| **`0x31`** | **start at voltage** | **`0x2599D0`** | **1020** | **51.00 V** |
| `0x0C` | time at start volts | `0x2599E8` | 60 | 60 s |
| `0x0D` | stop at voltage | `0x259A00` | 1060 | 53.00 V |
| `0x0E` | time at stop volts | `0x259A18` | 1800 | 1800 s |
| `0x0F` | companion of `0x0E` | `0x259A30` | 1800 | — |
| `0x16` | max engine run time | `0x259A48` | 115 | 115 min |

Each write is followed by its read-back request (value `0xFFFF`), the same pattern as the
floorplan burst. Every value matches PC.4 Result 4's **AES** column exactly — start 1020, stop
1060, `0E` 1800, `0F` 1800, `16` 115 — so the profile the coach reports is the profile the panel
ships.

**Encoding**: `value × 0.05 V`, per PC.4. The template is
`00 d9 fe 19 | ff 96 31 0f | fc 03 | d1 ea`, and `0xFC 0x03` = 1020 = 51.00 V.

**Feasibility of changing it.** Trivial, and the smallest edit class this project has: a **2-byte
same-size** write to the `u16` at **file offset `0x29258`** (GuiCode `0x2599D8`), no relocation,
no checksum beyond the GuiCode section reseal. 50.0 V would be `e8 03`, 52.0 V `10 04`, 53.0 V
`24 04`. **But no edit is needed for the owner's stated target** — 51.0 V is what it already is.

**Scope limits, stated because they bound the answer.**

* **The profile is GLOBAL, not per-floorplan.** A scan of the entire GuiCode section finds
  exactly **one** `0x31` template (plus its read-back), and **no floorplan handler writes `0x31`
  at all** — floorplan selection sets only the *mode* (`0xEF`), never the parameters. So every
  floorplan that lands in an AES mode shares this one profile: **WA, WD, WT and TB all default
  `EF = 3` (AES Single Output) and therefore all start at 51.0 V.** `SY` is the exception, and its
  exception is the mode rather than the voltage — it defaults `EF = 0` (none), so no AES profile
  applies to it by default. (Same SY anomaly as §8.4's family note: Li-AES by family, not by mode
  default.)
* Only the **AES** profile is present. PC.4 recorded a different AGS profile (start = 244 =
  12.20 V), and **no template for it exists** — so the AGS parameter set does not come from the
  panel's pool, and this finding says nothing about where it does come from.
* PC.11 established that a user's ± adjustments are transmitted as **signed deltas**, not
  absolutes. So this template is the *baseline* the panel writes when the profile is applied; a
  coach whose values have been adjusted will read something else, and re-applying the profile is
  what returns it to 51.0 V.
* The handler at `0x24E500` is reached from the tap dispatcher. **Which action id invokes it was
  not pinned** in this pass — one disassembly alignment ambiguity around the branch site — so
  "when the profile is (re)applied" is `inferred` rather than proven. The values themselves, and
  the fact that this handler writes them, are `proven-from-code`.

## Finding 44 — the panel already draws artwork as glyphs; glyphs are now writable as well as readable

**Confidence: `proven-from-data`** (glyph records decoded from all three builds by the
byte-exact `glyphs.decode_glyph`, itself a transcription of the blitter `FUN_002510C0`).

Two font slots hold **artwork, not letterforms**, which settles what a glyph is allowed to be:

| slot | bpp | cell | what it holds |
|---|---|---|---|
| 27 | 4 | 128×128 | the **Go Power! brand logo**, split across four glyphs: `A` 101×40, `B` 98×39, `C` 84×30, `D` 74×9. Thirteen further code points (`E`–`G`, `I`, `N`, `R`–`T`, `W`, `a`–`d`) are empty 20-byte stubs (`height 0`), and fifteen more share one solid-block placeholder. |
| 28 | 4 | 200×255 | **top-down coach floorplan line-art**, up to 200×224 per glyph — and each floorplan is drawn as **three adjacent glyphs** (rear / middle / front): `0,1,2` · `3,4,5` · `?,@,A` · `B,C,D` · `E,F,G`. Nine further code points share a solid-block placeholder. |

So both premises a logo button needs are already shipped in the firmware: a glyph may be far
larger than a text cell (101×40 and 200×224 both exist), and **one picture may be composed from
several adjacent glyphs**. No glyph in the corpus exceeds *its own font's* cell, which is the one
constraint worth respecting — put oversized artwork in a font whose cell accommodates it.

The floorplan trios are *not* selected by a stored string: no labelled record anywhere in the
corpus draws from slot 27 or 28, and the 43 records that report those slots inherit them
(the font selector is persistent render state). The character is computed in GuiCode from the
floorplan register. The selector site has **not** been located — that is open.

**`src/fireflymcf/glyphenc.py`** is the inverse of `decode_glyph`, and the project's first
glyph *writer*. It is tractable because the vertical-repeat RLE has an **identity encoding**: the
decoder ends a run on a clear bit, so an all-zero bitstream at `+0x0e` means "one stored row per
output row". *(Superseded 2026-08-21 by **Finding 46**: identity is legal but is not what the
corpus writes, and the encoder now emits greedy-maximal runs — matching 2,875 of 2,884 shipped
glyphs. F46 also shows bit 0 of `+0x0e` is the measurer's flag rather than RLE, and that the ink
profiles below are **not derivable** from the bitmap, so `_profiles` is an approximation used
only when authoring.)* `encode_glyph(rows, bpp, x, y, profiles, flag)` emits the left/right ink profiles, the u8
`x`/`width`/`y`/`height` header, that bitstream, and the packed rows — 4bpp low-nibble-left, 1bpp
**MSB-first** (packing 1bpp LSB-first passes every 4bpp font and fails only slot 21, which is how
the bug was found). Round-tripped against both depths in `tests/test_glyphenc.py`.

Demonstrated by `build/build-starlink-logo-glyph.py`: the Starlink logo rasterised to 108×54 at
4bpp (16 coverage levels, so the anti-aliasing survives), encoded to a 2,940-byte record,
appended to GuiData and installed on slot 27 `E` by repointing one glyph-table entry — a 4-byte
edit. All five relocation gates pass and the glyph reads back **pixel-identical** to the source
raster. See `build/starlink-logo-glyph.png`.

---

## Finding 45 — the Batteries widget: a runtime-gated group, and where its count comes from

**Confidence: `proven-from-code`** (every address below cited from GuiCode at base
`0x00240000`, except where marked). *Prompted by the widget failing to draw in Firefly
Studio's canvas. The renderer was never at fault.*

**Read `BUS-BASELINE.md` PC.1 and PC.12a first — the bus half is already documented there.**
Selector `0xD8` is named as the battery count, its `00 00` = "1 battery" encoding is
recorded, and PC.12a Result 2 states that the floorplan burst never writes it. Nothing
below re-derives any of that. What this finding adds is the **panel side**: how the value
reaches a widget, and why the widget is invisible until something unrelated happens.

### The widget is one of two candidates behind a switch

Object **1182** carries a type-6 value-match switch reading value slot **512**:

| candidate | match | content |
|---|---|---|
| object **1234** | 0 | a single `wtype 4` box — blank |
| object **1235** | 1 | `' Batteries:'`, then two (`wtype 6`, `wtype 11`, `wtype 12`) triples on slot **599**, labelled `'1'` and `'2'` |

With slot 512 unset the switch draws candidate 0, exactly as Finding 16 says it should.
A caller who has not set the slot sees the blank box and concludes the group is missing.
Slot 512 also fronts object **1121** (`'Battery'`, inner slot 509), which is reachable from
no page root `render.screen_placements` can walk — the object-247 situation of Finding 15.

### Slot 512 is runtime UI state, not configuration

It is **not** a floorplan default and **not** a config field:

- GuiCode writes it `1` at `0x00242F86` and `0x002466AA`, and `0` at `0x00243ADC`, all from
  tap/action handlers — the panel sets it on entering the panel that shows the group.
- Its register `0x20039F1C` is **absent** from the config-payload copy block
  (`0x00247C00`-`0x00247D00`), which covers payload offsets `+0x04`..`+0x1A` only.
- Toggling the group on the coach produced **no `0x1FED9` traffic for it at all**
  (`observed-on-hardware`, 2026-08-20; the capture was not retained — it re-confirmed
  PC.12a rather than adding to it).

So anything rendering a page statically must supply slot 512 itself if it wants to draw
what the panel draws. That is what `studio/server/reference.py`'s `RUNTIME_GATE_SLOTS` does.

### Where the count lands, and why it is 0/1

The config-payload block copies the received byte at `+0x18` into `0x2003A804`
(`0x00247C84`-`0x00247C88`), and `0x00247D5A`-`0x00247D5E` then passes it to a setter at
`0x002477E8`:

```
002477e8  ldr  r3, =0x20039e58      ; the register behind value slot 599
002477ea  ldrb r3, [r3]
002477ec  cmp  r3, r0               ; unchanged? return
002477ee  beq  0x2477f4
002477f0  cmp  r0, #1
002477f2  bls  0x2477f6             ; accept ONLY 0 or 1
002477f4  bx   lr                   ; otherwise reject
```

The `cmp r0,#1; bls` guard is the code-side proof of the encoding PC.1 records from the
bus: slot 599 is **zero-based**, `0` selects `'1'` and `1` selects `'2'`, and no other value
is legal. Rendering the slot at 0/1/2 agrees — 2 lights neither dot, because neither
candidate matches.

Its **sibling at `0x002477D0`** does the same for the Go Power! controller count (slot 39,
register `0x2003A4E9`) and rejects `> 5` — the same clamp neighbourhood as Finding 41,
which read the `> 5 → 1` mapping as the latent defect. The two counts are the same shape.

### The config-payload map, extending Finding 19

The copy block writes fifteen registers, one per payload offset:

| `+04` `0x20039F30` | `+05` `0x20039916` | `+06` `0x20039E9A` | `+0A` `0x2003A296` | `+0C` `0x20039E81` |
|---|---|---|---|---|
| `+0D` `0x20039B49` | `+0E` `0x2003A4B4` | `+0F` `0x20039C7C` | `+11` `0x20039E70` | `+12` `0x2003A528` |
| `+13` `0x2003A38C` | `+17` `0x2003A62B` | **`+18` `0x2003A804`** | `+19` `0x20039EBD` | `+1A` `0x20039CF8` |

`+0x0D` is the Bath Fan register Finding 19 already cites, which anchors the block.
`config.selectors_by_offset` recovers a selector for ten of these; **`+0x18` is not among
them**, and that is expected rather than surprising — that extractor walks OPTIONS *row* tap
handlers, and a count is driven by `−`/`+` buttons, not a checkbox row. Its silence is a
limit of the method, not evidence that no selector exists. PC.12a already answers the
question directly: the burst does not write `0xD8`.

## Finding 46 — the glyph RLE is greedy, byte +0x0e bit 0 is the measurer's flag, and the ink profiles are NOT derivable

Three results from making the glyph **encoder** faithful enough that a glyph can be exported to
JSON and installed back byte-identically (`glyphenc.export_glyph` / `encode_glyph`,
`tests/test_glyphenc.py`). Two are positive and pin the format; the third is a defensible
negative that will not fall to more searching of these files.

**1. The vertical-repeat RLE is written greedy-maximal.** Finding 22 established the decode side
(the bitstream at glyph `+0x0e` repeats each stored row `n`× vertically, `FUN_002510C0`), and
noted the encoding has an *identity* form — an all-zero bitstream, one stored row per output row
— which `encode_glyph` emitted from 2026-08-18. That is legal but it is **not what the corpus
does**. Re-encoding every glyph with maximal runs reproduces the shipped bitstream on **2,875 of
2,884** glyphs across the three builds (13.6 1112/1115, 11.23 671/674, 14.7 1092/1095), and the
nine that differ do so **only in bit 0**, which is not RLE at all (below). So identity was the
unusual encoding. Compressing costs nothing and matches the shipped convention: the authored
96×54 Starlink logo stores 50 rows instead of 54, 2616 → 2424 bytes.

**Hardware-confirmed 2026-08-21** (owner, reference build v2): the panel draws that
greedy-encoded logo correctly. The corpus match above says our encoder agrees with Firefly's;
this says the **blitter** accepts what we write, which is the claim that matters for a build.
The failure mode it rules out is specific — a wrong run encoding repeats or drops rows, so it
would have shown as vertical banding or a squashed glyph rather than as anything subtle.

**2. Byte `+0x0e` bit 0 belongs to the measurer, not the blitter.** `decode_glyph` reads stream
bit `r+1` for output row `r` (`FUN_002510C0`'s `uVar15 = glyph[0xe] >> 1` initial state), so bit
0 is never part of a run — and `metrics._GHdr` reads *the same byte* as the flag `FUN_002518F8`
tests to decide which bearing a string's first and last glyph contribute (`acc = -(left[0] if
flag & 1 else x)`). One byte, two consumers, disjoint bits. Consequence: dropping bit 0 is
**invisible to a pixel round-trip and still changes measured text width**, so an encoder must
carry it explicitly. The nine "mismatches" in (1) are the glyphs that set it (slot 21's `0`,
`2`, `4` in each build).

**3. The 10 ink-profile bytes cannot be derived from the bitmap.** `glyphenc._profiles`
approximates them (5 equal bands, min-left/max-right) and reproduces **6 of 1115** on 13.6.
Four independent lines say the real rule is not a function of the stored pixels at all:

* **No banding works.** A DP over *every* contiguous partition of a glyph's rows into 5 bands
  admits only **102 of 2884** profiles. 4 bands: 364. Example — slot 8 `'3'` carries entry 2 =
  `(3,15)`, but no contiguous run of its rows has min-left 3 *and* max-right 15.
* **No fixed window works.** Solving for a row window `[a,b)` that satisfies *every* glyph in a
  font cohort (slot 9 h=30, slot 13 h=13, slot 17 h=19) yields **zero** solutions, for all four
  entries, even allowing an empty window to fall back to the glyph's centre.
* **Values fall outside the ink.** **630** profile bytes lie outside their own glyph's ink
  bounds — the profile describes ink the stored bitmap does not contain.
* **Cell-space banding fails too** (93/2884), which also disposes of the argument from kerning
  semantics that band *k* must mean a fixed height across a font.

What *is* established: the profile is stable per source glyph — 1,077 groups of pixel-identical
glyphs (across builds, fonts and differing font cell heights, four of them under *different*
characters) carry byte-identical profiles, with zero disagreements, and it depends on neither
the font cell (0 groups differ) nor the y bearing. Read carefully that is weaker than "a
function of the bitmap": identical bitmaps almost always come from the same source outline, so
the test cannot separate the two. Taken with the four negatives above, the profiles are computed
by **Firefly's font-authoring tool from the source outline**, at a precision the 4bpp raster does
not preserve. That tool is in neither image, so this is not recoverable from the MCF.

*Practical consequence, and it is the whole reason the dig was worth doing:* an exported glyph
**carries its profiles and its flag bit as data** rather than recomputing them. A glyph copied
between builds is then byte-identical; a glyph authored from new artwork still gets the
approximation, which is what the Starlink logo has always had (and does not matter for a glyph
drawn alone — `text_width` cancels the profiles for a single glyph). *Partial derivations worth
not re-deriving:* entry 4 is the glyph's horizontal centre `((2x+w−1)/2, (2x+w)/2)` on 933/2884
— **100%** in slots 8, 9 and 20 but ~30% elsewhere; icon fonts are largely degenerate flat
`x .. x+w−1` (slot 27: 19 of 23); and the four remaining entries are unmistakably ordered
top-to-bottom, matching single scanlines at `k·(h−1)/3` on 45–61% of text glyphs.

## Finding 47 — the left nav rail is a derived widget: no Electrical icon without something electrical

`proven-from-code`, 2026-08-21. Reported from the coach by the owner: *"if neither AGS nor AES
are selected the Icon for Electrical (lightning bolt in circle) is not visible and the vertical
icons shift to fill the space."* The panel is right and every renderer here was wrong — this is
the second derived slot, after the HOME battery block, and the first one whose **default is the
wrong picture**.

**The rail is a switch, and both rails are authored.** Object **974** carries a single type-6
value-match record (`0x7fe31`) with `value_slot` **453**, `child_base` **385**, `count` **4**.
The candidates' entries in the match table (`+0x4562c`) are

| object | match value | contents |
|---|---|---|
| 385 | `0xffff` (the explicit default) | rail box + `P D Q R L` — five icons, 12 px gaps |
| 386 | `0` | rail box + `P Q R L` — four icons, **20 px gaps** |
| 387 | `2` | a further switch on slot 185 → 389/390 |
| 388 | `4` | the same switch |

`D` in font slot 24 is the bolt-in-a-circle. So Firefly authored the reflow: object 386 is not
385-minus-a-record, it is a second complete rail with wider spacing, which is exactly the "shift
to fill the space" the owner saw. Per `FUN_00256C54`'s scan, value 0 selects 386 and **every
other value falls through to the `0xffff` default, 385** — so an undriven switch draws the
five-icon rail, and the coach that does not have an Electrical page is precisely the one a
render got wrong.

**Slot 453 is computed, not received.** `FUN_00247BF8` writes it right after the battery block
(Finding 42's neighbour) and merges into the same path:

```
0x00249D3E  ldr   r3,[0x00249e2c]   ; = 0x2003A38C   AGS / AES selector      (slot 37)
0x00249D40  ldrb  r3,[r3,#0x0]
0x00249D44  bne.w 0x00249F02        ;   any generator mode        -> 453 = 1
0x00249D48  ldr   r3,[0x00249e24]   ; = 0x2003A4E9   Go Power! count         (slot 39)
0x00249D4E  bne.w 0x00249F02        ;   any solar controller      -> 453 = 1
0x00249D52  ldr   r3,[0x00249e0c]   ; = 0x2003A62B   Progressive Inverter    (slot 113)
0x00249D58  bne.w 0x00249F02        ;   inverter fitted           -> 453 = 1
0x00249D5C  ldr   r3,[0x00249e44]   ; = 0x2003A617   the rail selector
0x00249D5E  movs  r2,#0
0x00249D60  strb  r2,[r3]           ;   all three zero            -> 453 = 0
...
0x00249F02  ldr   r3,[0x00249f38]   ; = 0x2003A617
0x00249F04  movs  r2,#1
0x00249F06  strb  r2,[r3]
```

i.e. **the Electrical icon is drawn iff the coach has something electrical to show.** Those two
stores are the complete writer set: `0x2003A617` occurs exactly twice in GuiCode's literal pools,
in **no** `movw`/`movt` pair, and its only other occurrence in the whole file is slot 453's own
entry in GuiData's variable-table (`+0x44BC8 + 453*4`). Values 2 and 4 are therefore unreachable
on 13.6 — objects 387/388 and their six-icon children 389/390 are real but never selected, the
same shape of dead candidate as object 904 in Finding 42's battery switch.

**Finding 19 had already seen this flag and stopped one step short.** Its closing paragraph names
`0x2003A617` as "a visibility flag cleared iff `AGS==0 && 0x2003A4E9==0 && PI==0`" — correct, and
cited to the same instructions. What it did not say is *what reads it*, so nothing ever drove the
slot and the mismatch survived until the owner looked at the panel. Worth carrying: a register
whose **writer** is understood is not understood until its **reader** is named.

**Cross-check, not a fit.** Running the floorplan bursts' own defaults (`floorplandefaults`,
selectors `0xEF` AGS / `0xD7` Go Power! / `0xE6` Progressive Inverter) through the derivation
gives the four-icon rail for **exactly one** stock floorplan — **SY**, the floorplan CLAUDE.md
already records as the AGS-mode exception (`EF = 0`, none). Nothing in the derivation looks at
that list.

**Shipped as** `options.derived_slots(data, values)` — the tools layer, not the studio server, so
`cli render` and Studio cannot disagree about what the panel computes; `studio.server.reference`
delegates and `cli._cmd_render` applies it. `livecfg._COUNT_SLOTS` gained `0xD7 -> 39` so an
imported live config carries the Go Power! count that feeds it. The two derivations treat an
**absent** source differently, on purpose: slot 6 stays unset (its default candidate is already
the value-0 block, so deriving nothing draws the right thing), while slot 453 reads absent as 0
(its default candidate is the *five*-icon rail, so deriving nothing draws the wrong thing). That
asymmetry is stated in `derived_slots`' docstring rather than left for a reader to rediscover.

### Finding 47 addendum — the sixth icon exists, is a generator shortcut, and is dead on 13.6

Asked by the owner, 2026-08-21: *is there a provision in the firmware for a 6th icon?* **Yes**, and
it is not leftover — another build ships it.

**The rail objects, all four of them, across the corpus.** 13.6's 389/390 and 14.7's 347/348 carry
a sixth glyph `S` (font slot 24, 44×26 — an engine block with an exhaust cone and feet, i.e. a
generator set). **11.23 ships six icons as its normal rail**: its switch is `base 763, count 7,
slot 7`, replicated on every page object, and candidates 763-766 (match `0xffff`/0/1/2) are all
six-icon `P Q D R S L` while 767-769 (match 3/4/5) drop the bolt. So the six-icon rail is a
shipped Firefly configuration, and 13.6 kept the objects while its selector arithmetic stopped
producing the values that reach them.

**What the icon does.** Its `wtype 12` hit region carries touch id **`0x7DC`** in all three builds
(`gestures.touch_ids`). GuiCode's action dispatcher compares it at `0x0024B808` and branches to
`0x0024C702`, which writes screen id **`0x0C`** to `state+0x20A` — object **16**,
`'   AUTO ENGINE START SETTINGS'`: a full-screen AES page (AES enable, Start/Stop at, Time at
Start/Stop Volts, Min/Max Engine Run Time, Quiet Time Start/Stop, Reset Defaults).

**And `0x0024C702` is the ONLY writer of screen 12 in the image.** Scanned by encoding rather than
by linear disassembly (the blob desyncs on its pools): the 4-byte `strb.w rX,[r4,#0x20a]` pattern
occurs 64 times, 61 preceded by a `movs rX,#imm` (tabulated, every one landing on a distinctly
named screen), and the 3 that are not write 0, 0 and a remembered value from a pool cell. So on
13.6 object 16 is a complete page nothing can open.

*It is a shortcut, not hidden settings.* The same Start-at/Stop-at widgets (objects **786**/**788**,
slots 272/274) are also attached by object **829**, which object 5 (ELECTRICAL) selects with a
type-6 switch on slot 7 — that is where these values are reachable on the coach today. Object 31
(`'   AUTO GEN START SETTINGS'`, screen 27) is the AGS twin.

**Reachability on 13.6, and how to unblock it.** 389/390 sit behind objects 387/388 (match values
2 and 4 of the object-974 switch), each a further type-6 switch on **slot 185** — a plain config
field, written from payload `+0x0F` at `0x00247C6A` and toggled by the handler at `0x0024CB4E`
(`movs r1,#0xf` → `config_set`), which has no OPTIONS row in 13.6. Since GuiCode only ever writes
slot 453 = 0 or 1 (Finding 47), neither 387 nor 388 is ever selected.

The cheap unblock is **two u16 same-size edits in object 974's switch record** (`0x7fe31`):
`child_index` 385 → **389** and `count` 4 → **2**. Slot 453's existing 0/1 then lands directly on
389's and 390's own match values 0/1 — nothing electrical → `P Q R S L`, a generator/AES coach →
all six. No authoring is needed: object 390's geometry (10 px then 12 px gaps) is Firefly's.
**Not built and not flashed** — the render below is the only evidence it composes.

## Finding 48 — the PD inverter/charger is a SEPARATE protocol with its own device table, and `Firmware Rev.` is a byte-position hack

`proven-from-code` + **CONFIRMED ON HARDWARE 2026-08-23** (`BUS-BASELINE.md` PC.20). Everything
here was found with Ghidra headless against `guicode136` — linear capstone sweeps had failed on
this repeatedly, because the section interleaves code and literal pools and a sweep desynchronises
on every pool.

**The trap that cost the most time, and it is a general one: there are TWO twin inverter pages.**
Objects **1418** and **1419** carry identical label sets (`Inverter`, `DC Voltage`, `DC Current`,
`AC Out Volts`, `AC Out Freq.`, `AC Input Volts`, `Firmware Rev.`, `Inverter Fault`). Object 1418's
eight value cells each have a GuiCode literal; **object 1419's eight have none**. The coach draws
1418. Reasoning about 1419 produces a confident, wrong "this cell has no writer" for every field.
The control that catches it: known-good cells (the AES setpoints, slots 272/274 → `0x20039D52`,
`0x2003A22C`) *do* have literals, so absence is meaningful rather than a limitation of the scan.

**The device table.** `FUN_0024F1FC` maintains a **two-entry array of 56-byte structs at
`0x2003A8B8`** (`DAT_0024F500`/`F6D4`/`F008`/`F100` all hold that address; `FUN_0024EF1C` is its
initialiser, stride `r5*7*8 = 56`, setting `+0x00/+0x01 = 0xFF` and `+0x02 = 0`). A slot is
**adopted** by:

```c
cVar3 = *(char *)(TBL + (i-1)*0x38);              // stored source address
if (cVar3 == (char)*param_1) match;               // already ours: match on SA
if (cVar3 == -1 &&                                // free slot...
    (*param_1 & 0x03FFFF00) == 0x01FFD400 &&      // ...DGN 1FFD4 INVERTER_STATUS...
    (byte)param_1[1] == i) match;                 // ...and payload[0] == slot index (1 or 2)
```

So **this parser binds by source address**, unlike the receive-filter table, which never reads it
(`MCF-FORMAT.md` §5.4a). A device announces itself with `INVERTER_STATUS` at instance 1 or 2, its
SA is recorded, and every later frame from that SA is accepted. That is why the panel also
*requests* `0FEEB` from an address it has adopted.

**The DGN arms**, dispatched on the 18-bit DGN (`uVar12 = (id << 6) >> 0xe`):

| DGN | constant | effect |
|---|---|---|
| `1FFD4` INVERTER_STATUS | `DAT_0024F514 - 1` | stores SA, `payload[1]` → `+0x1C`, derives `+0x1D` |
| `1FFD7` INVERTER_AC_STATUS_1 | `DAT_0024F518 + 1` | **branches on `payload[0] >> 4`**: nibble 0 → `+0x2C` (AC **input** volts); else → `+0x2E/+0x30/+0x32` (output volts, amps, freq) |
| `1FFD6`/`1FFD5`/`1FF8F` | `F518`/`F514`/`F50C` | freshness counters `+0x0C`/`+0x0E`/`+0x10` = `0x14` |
| `1FEE8` INVERTER_DC_STATUS | `F50C - 0xD2` | `+0x1E`, `+0x22`, `+0x20` |
| `1FECA` DM_RV | `DAT_0024F510` | requires `payload[1] == 'B'` **and** `payload[3] == slot index`; decodes `payload[2]` and `payload[4]`'s two bitfields (`>>5`, `&0x1F`) into a fault code at `+0x34` |
| `0ECFF` TP.CM | literal | freshness `+0x06` |
| `0EBFF` TP.DT | literal | **the firmware revision, below** |

The `1FFD7` branch is the code behind the hardware observation that the page shows AC out and AC
in simultaneously from one DGN — something the receive-filter table cannot express, since vars
103/104 carry the `0xFF` sentinel and are written by *any* `1FFD7` frame.

**`Firmware Rev.` — a hardcoded byte position in the PRODUCT_IDENTIFICATION string.**

```c
if (uVar12 == 0xebff) {                            // TP.DT, BAM data transfer
    if ((short)param_1[1] != 0x7605) return;       // payload[0] == 5 (packet 5), payload[1] == 'v'
    struct[i].u16[+0x02] = (payload[2] - '0') * 100
                         + (payload[4] - '0') * 10
                         + (payload[5] - '0');     // payload[3] skipped: it is the '.'
}
```

`FUN_00247BF8` then copies `struct +0x02` to the cell at `0x00248042`
(`ldr r3,[0x0024821C]` = `0x2003A8B8`; `ldrh r1,[r3,#0x2]`; `strh r1,[r2]` with
`r2 = 0x2003A776` = object 1418's `Firmware Rev.` cell).

TP.DT packet *n* carries string bytes `[(n-1)*7, n*7)`, so packet 5 is bytes 28..34 and
`payload[1]` is **string index 28**. The panel therefore requires `vN.MM` at exactly index 28 of
the product-ID string — not in a named field, at a fixed offset. Confirmed by construction:

```
'Progressive Dynamics*PD-INV v1.23*0001*1*'   41 B, 6 packets
                            ^^^^^ index 28
18EBFF42#0576312E32332A30                     packet 5: 05 'v' '1' '.' '2' '3' '*' '0'
  -> (0x31-0x30)*100 + (0x32-0x30)*10 + (0x33-0x30) = 123
```

The panel drew **1.23**. The value had never been displayed before, so this is a *prediction* from
the instruction rather than a fit — the same standard as Finding 46's encoder and the transmit
probe's frame.

*Method note worth keeping.* Three earlier product-ID answers were refused, and the file said why
each time only in hindsight: the first omitted the trailing `*` that all three real responders use
(`Wakespeed Offshore*WS500*926993657**`, `Spyder Controls*G12 Panel 2v2 3v0d*0000000*22*`,
`Firefly*Standalone Module*0001**`), and the corrected one still had `0001*1*` sitting at index 28.
Comparing against what real devices already put on the bus — the owner's suggestion — is what
turned a blind sweep into a decode.


### Finding 48 addendum — `Inverter Fault`, and the THREE consumers of one DGN

**CONFIRMED ON HARDWARE 2026-08-23**: `19FECA42#0142010100FFFFFF` — DM_RV with `payload[1] = 'B'`,
`payload[3] = 1` (slot), `payload[2] = 1`, `payload[4] = 0` — made the panel draw
**`E2 - Over Voltage`**. `FUN_00247BF8` copies `struct[0] + 0x34` to cell `0x200399A3` at
`0x002480FA`; object 1418's slot-657 switch has 14 candidates and the parser's five hardcoded
constants all land on real ones (2 `E2 - Over Voltage`, 4 `E4 - Over Temp`, 7 `E7 - Volt Drop
Startup`, 0x14 `Low Batt. Volt Warn`, 0x15 `High Batt. Volt Warn`). Nothing in the switch decode
reads the parser, so that agreement is a cross-check rather than a fit.

**One DGN, three consumers, three different keying rules — do not generalise between them.** This
was got wrong in session and corrected by the owner, who asked why a DM_RV injection would disturb
the alternator page when DM_RV is broadcast by every device:

| consumer | keyed on |
|---|---|
| the receive-filter table (vars 162-164, 235, all `0xFF`) | **DGN only** — `FUN_0021C760` never reads the id's low byte (`MCF-FORMAT.md` §5.4a) |
| `FUN_0024F1FC`, the PD inverter/charger parser | **source address, after adoption** via `1FFD4` at instance 1 or 2 |
| `FUN_0024FCE8`, the ALTERNATOR SYSTEM parser (called from `GuiCode_entry4`) | **source address, hardcoded**: `if (((char)*param_1 == -0x80) && ...)` — signed `-0x80` is `0x80`, the Wakespeed regulator |

So a synthetic DM_RV from `0x42` cannot reach the alternator page: that parser accepts frames from
`0x80` and nothing else. The error was assuming the filter table's SA-blindness — real, and
`proven-from-code` — extended to a page fed by different code. **The filter table is one mechanism
among several; a claim about it is not a claim about a page.**

*Free from the same block:* `0x002480E6`-`0x002480EC` computes AC Out Freq as
`(raw + raw*4) * 2 >> 7` = `raw * 10 / 128`, so the ÷128 frequency scaling taken from rvc2mqtt is
now `proven-from-code` too.

## Finding 49 — object 922 `Inverter/Charger Status`: six fields live off the variable table, four are dead

`proven-from-code`, 2026-08-23. A second, richer inverter page than the PD Inverter one of Finding
48 — it adds `AC Input Freq.`, and carries `Faults:`, `I/C Firmware Rev.`, `I/C Model` and
**`Bridge Firmware`**, which is the page a bridge product is presumably meant to surface on.

**It is fed by the receive-filter table, not by a dedicated parser.** `FUN_00247BF8` reads the slot
array through `r4` at `2*var` offsets and stores to the cells (13.6, block `0x0024A5C0`-`0x0024A780`):

| field | cell | var | source | transform |
|---|---|---|---|---|
| AC Out Volts | `0x2003A192` | 103 | `1FFD7` INVERTER_AC_STATUS_1 bytes 1-2 | — |
| AC Out Freq. | `0x200399CA` | 104 | `1FFD7` bytes 5-6 | `raw*10 >> 7` (÷12.8, one decimal) |
| DC Voltage | `0x20039D6C` | 105 | `1FFFD` DC_SOURCE_STATUS_1 **instance 1** bytes 2-3 | `(v+1) >> 1` |
| DC Current | `0x2003A37E` | 106 | `1FFFD` instance 1 bytes **4-7** | — (see the correction below) |
| AC Input Volts | `0x2003A6E2` | 116 | **`1FFCA` CHARGER_AC_STATUS_1** bytes 1-2 | `(v+1)/20` (integer volts) |
| AC Input Freq. | `0x20039F7E` | 117 | `1FFCA` bytes 5-6 | `raw*10 >> 7` |

Two consequences worth carrying. Its DC pair is the **house battery** (`DC_SOURCE_STATUS_1`
instance 1), not the inverter's own `1FEE8` DC status, so the two inverter pages do not report the
same quantity. And its AC *input* comes from the **charger's** AC status, not from `1FFD7` with the
input/output nibble — the opposite of how object 1418 does it (Finding 48).

### Correction and confirmation on the coach — `observed-on-hardware`, 2026-08-24 (PC.29)

v8 gave screen 14 its first route and the page was photographed, which exercised this block for
the first time.

**`DC Current` is bytes 4-7, not 4-5.** RV-C carries that field as a 32-bit at 1 mA with a
−2,000,000 A offset. The alternator's frame `19FFFD80#0178380400943577` gives `[4:8]` =
`0x77359400` = 2,000,000,000 → **0.0 A** exactly; bytes 4-5 alone give `0x9400` = 37,888, which is
not a current at any scaling. The table above is corrected. **What the panel's own extraction mode
grabs has not been re-derived**, so treat the var→display half of that row as unconfirmed rather
than merely re-scaled — the DGN and instance are right, the width is now right, the extraction is
open.

**The "two pages, two quantities" consequence is now observed, not just derived.** With one load
running, this page showed `DC Current 0 A` while the PD Inverter page showed **−25 A** for the
same load: the alternator (SA `0x80`) publishes instance 1 with a real voltage and a zero current,
and `1FEE8` is where the inverter's own current lives.

**The four identity fields drew their placeholders**, confirming the negative below: `Faults:`
blank, `I/C Firmware Rev. 0.0`, `I/C Model UNKNOWN`, `Bridge Firmware 0.00`. `UNKNOWN` is a stored
string in the image (five occurrences, first at `0x783ed`) — the authored default, not a runtime
value.

### What the two unwired switches CAN show — the full enumerations, 2026-09-02

`proven-from-file`. `Faults:` and `I/C Model` are type-6 switches, so the set of strings each can
ever draw is enumerable from the file even though no code writes their cells. Recorded here
because "authored but unwired" says what does *not* happen and not what the page was designed to
say — and because a bridge that ever wires these needs the exact codes.

**`Faults:` — value slot 412, 23 arms** (match value → string):

| 1 `NO FAULT` | 2 `STUCK RELAY` | 3 `DC OVERLOAD` | 4 `AC OVERLOAD` |
|---|---|---|---|
| 5 `DEAD BATTERY` | 6 `AC BACKFEED` | 7 `LOW BATTERY` | 8 `HIGH BATTERY` |
| 9 `HIGH VAC` | 10 `HIGH SPEED BUS` | 11 `INTERNAL BRIDGE` | 12 `NTC` |
| 13 `FET OVERLOAD` | 14 `STACK MODE` | 15 `STACK NO CLOCK` | 16 `STACK CLOCK PHASE` |
| 17 `STACK PHASE LOSS` | 18 `OVERTEMP` | 19 `TRANSFER RELAY` | 20 `CHARGER` |
| 21 `HIGH BATTERY TEMP` | 22 `SELCO TRANSFORMER` | 23 `CB3 OPEN` | |

**The blank on the coach is the no-match path, not a missing string.** The arms start at match
value **1**; there is no arm for 0 and no `0xFFFF` default candidate, so an unwritten cell (0)
matches nothing and `FUN_00256C54` renders **nothing at all**. A wired implementation must send 1
for "healthy" — 0 draws an empty row, not `NO FAULT`.

**`I/C Model` — value slot 414, 40 arms.** Magnum inverter/charger model codes, sparse:
0 `UNKNOWN`, 6 `MM612`, 7 `MM612-AE`, 8 `MM1212`, 9 `MMS1012`, 10 `MM1012E`, 11 `MM1512`,
12 `MMS912E`, 13 `MMS912E` (the string is repeated at two codes, in the file), 15 `ME1512`,
20 `ME2012`, 21 `RD2212`, 25 `ME2512`, 30 `ME3112`, 35 `MS2012`, 36 `MS1512E`, 40 `MS2012E`,
44 `MSH3012M`, 45 `MS2812`, 47 `MS2712E`, 53 `MM1324E`, 54 `MM1524`, 55 `RD1824`, 59 `RD2624E`,
63 `RD2824`, 69 `RD4024E`, 74 `RD3924`, 90 `MS4124E`, 91 `MS2024`, 103 `MSH4024M`,
104 `MSH4024RE`, 105 `MS4024`, 106 `MS4024AE`, 107 `MS4024PAE`, 111 `MS4448AE`, 112 `MS3748AEJ`,
114 `MS4048`, 115 `MS4448PAE`, 116 `MS3748PAEJ`, 117 `MS4348PE`. Unlike `Faults:`, this one **does**
have a 0 arm, which is why the coach draws `UNKNOWN` rather than nothing — the same mechanism, a
different authoring choice, and the pair is what makes the mechanism legible.

`I/C Firmware Rev.` (slot 413) and `Bridge Firmware` (slot 415) are plain `wtype 8` numerics, not
switches, so they have no enumeration — they draw `0.0` / `0.00`.

**Four display conventions, distinguishable at a glance.** An unset `0xFF` renders as its literal
value (`255 A`); a cell that has never received renders as **dashes** (`---V`, which is what
`AC Input Volts` does here, since nothing sends `1FFCA`); a **type-6 switch whose cell matches no
arm** renders **nothing** — an empty gap where a string should be (`Faults:` here, `Battery Type`
on Finding 51's page) unless the switch carries a `0xFFFF` default candidate; and a gated-off block
renders as its sibling's empty box. Only the last is intentional, and telling them apart saves
diagnosing the wrong layer. *(This paragraph said "three" until 2026-09-02; the switch no-match
case was being read as the gated-off one, which points at the wrong layer — a gate is a cell you
can set, a no-match is an arm the file does not have.)*

**The four identity fields have NO writer**, in 13.6 or in 14.7: no literal in GuiCode or the LCD
image, no `movw`/`movt` construction of the address in either, and no Ghidra-resolved reference.
The six numeric fields above are the control that makes this a defensible negative rather than a
limitation of the scan. `Faults:`, `I/C Firmware Rev.`, `I/C Model` and `Bridge Firmware` are
authored but unwired.

*11.23 is inconclusive, deliberately.* The same scan finds no writer there even for the six control
fields, so the method does not transfer to that build and nothing is claimed about it.

### The tooling defect this exposed — `status_map.cell_table` was 13.6-only

The value-slot pointer table **moves between builds**: 13.6 `0x44BC8`, 11.23 `0x44B60`, 14.7
`0x42B74`. `cell_table()` hardcoded 13.6's base *and* its 665-entry length, so on any other build
it read the right table at the wrong offset — every `value_slot` resolving to a cell ~26 entries
away. It never raises: the neighbours are all SRAM addresses in the same region, so the answer
looks right.

This produced a confident cross-build claim in session ("11.23 wires all ten, 14.7 wires none",
implying the feature had been progressively dewired) that was **entirely an artifact**. It was
caught only by a control: tracing 11.23's supposed `DC Voltage` cell led to a boolean config flag
read with `cbnz`, and a DC voltage cannot be a boolean. Fixed to locate the base via
`guidata.find_variable_tables` and derive the length (665 / 562 / 505); 13.6 decodes byte-identical
to before. Pinned by `tests/test_status_map.py`, mutation-checked.

**The general rule:** a per-build offset used cross-build fails *silently* here, because every
table in this file is surrounded by data of the same shape. Locate structurally, and keep a
control whose expected value you know independently.

## Finding 50 — the option interlocks are panel-side: one tap emits several `0x1FED9` writes

`proven-from-code` (the emitting runs) + `proven-from-bus` (the G12 never originates one),
2026-08-23. Asked by the owner: *selecting Progressive Inverter also selects AGS and deselects
any AES option — where does that come from, and is the G12 enforcing it?* It comes from GuiCode,
and the G12 is not enforcing it. This **retracts Finding 19's** "the coupling is upstream, not in
GuiCode" (corrected in place there).

### The mechanism

There is no interlock *logic* anywhere. Each OPTIONS row's tap handler is a straight-line run
that copies a fixed list of pre-built 12-byte `0x1FED9` templates out of GuiCode's `.rodata` pool
and hands each to the host sender — the same emit idiom Finding 23 recovered (`ldm {r0,r1,r2}` /
`stm.w` / `blx [0x2003986C]`), and the same pool Finding 42's floorplan bursts use. **A coupled
option is simply a handler whose list is longer than one entry.** The dependency is authored into
the *sequence of templates*, not computed, so nothing reads one option to decide about another.

Every template address below is `0x00259260 + imm` (the pool base each handler `ldr`s), and each
write template is followed in the run by its `ff ff d3 ff` read-back twin, omitted here.

| row | arm, and the gate that picks it | templates emitted, in order |
|---|---|---|
| **Progressive Inverter** `0x0024CCF4` | `[0x2003A62B] == 0` → turning **ON** | `E6 := 1`, **`EF := 1`** |
| | `!= 0` → turning **OFF** (`0x0024CD4C`) | `E6 := 0` — *and nothing else* |
| **AGS** `0x0024D00C` | `[0x2003A38C] != 1` → **select** | `EF := 1`, `D7 := 0`, **`E6 := 1`**, `15 := 0x2801`, `F7 := 0x3C` |
| | `== 1` → **deselect** (`0x0024D0D8`) | `EF := 0`, then joins the tail at `0x0024D086`: `15 := 0x2801`, `F7 := 0x3C` |
| **AES** `0x0024D104` | `[0x2003A38C] != 2` → **select** | `EF := 2`, `D7 := 0`, **`E6 := 0`**, `15 := 0x2801`, `F7 := 0x3C` |
| | `== 2` → **deselect** (`0x0024D1E8`) | `EF := 0`, then the same tail |
| **AES Single Output** `0x0024BF74` | `[0x2003A38C] != 3` → **select** | `EF := 3`, `D7 := 0`, **`E6 := 0`**, `15 := 0x2801`, `F7 := 0x3C` |
| | `== 3` → **deselect** (`0x0024D214`) | `EF := 0`, then the same tail |

Selectors: `E6` Progressive Inverter, `EF` AGS/AES mode, `D7` Go Power! count, `0x15` AGS config
bitfield, `F7` retry interval. `EF` is a single four-valued mode register (0 none / 1 AGS / 2 AES
/ 3 AES Single Output), so "deselects any existing AES option" needs no deselect frame — writing
`EF := 1` *is* the deselect. The one genuine cross-option force is `E6`.

**The invariant the panel maintains is `PI ⟺ AGS`, and it is enforced in both directions on
*selection* only.** Selecting PI forces mode AGS; selecting AGS forces PI on; selecting either AES
mode forces PI off. **Deselection is not symmetric**: the PI-off arm emits `E6 := 0` alone and
leaves the mode at AGS, and every mode-deselect arm emits `EF := 0` alone and leaves PI set. So
the panel can reach — and the coach was observed sitting in — states its own selection logic would
never produce (`mode = AGS, PI = 0`, `BUS-BASELINE.md` PC.3 from t=76.7 to t=105.9). Anything
reasoning about these two options must treat the pair as unconstrained, not as an invariant.

### The bus agrees frame for frame

PC.3's transactions are byte-exact and *count*-exact against these runs — not merely consistent
in ordering. The Progressive-Inverter tap at t=69.152 put exactly four frames on the wire:

```
19FED99F#FF96E60F0100D1EA     <- template 0x00259784   E6 := 1
19FED99F#FF96E60FFFFFD3FF     <- template 0x00259790   E6 read-back
19FED99F#FF96EF0F0100D1EA     <- template 0x00259700   EF := 1   (the force)
19FED99F#FF96EF0FFFFFD3FF     <- template 0x002596F4   EF read-back
```

and the AES tap at t=116.497 put exactly ten, in the tabulated order. All from source `9F`, **the
panel**. The `0x1EE00` config-set frames the panel also emits (`19EE009F#170001…`, `#130001…` —
cfg offsets `0x17` and `0x13`, Finding 20's offset-addressed path) carry the same pair, so the
coupling reaches the G12 by both routes and neither originates there.

### The G12 does not enforce it — and one observation is decisive

Across all eleven captures, classifying every change in the G12's own `0x15FCE` reports as either
an echo of a panel write (same value, within 1 s) or a G12-originated change:

| selector | echoes | G12-originated |
|---|---|---|
| `E6` PI, `EF` mode, `D7` Go Power!, `D8`, `E3`, `E4`, `E5`, `E9`, `EB`, `EC`, `F5` | 84 | **0** |
| `0x0A`, `0x0C`, `0x0D`, `0x0E`, `0x0F`, `0x15`, `0x16`, `0x2B`, `0x2C`, `0x31`, `0xCC`, `0xCD`, `0xCE` | 20 | 223 |

The G12 changes plenty of config on its own — but **only the AGS/AES parameter profile**, the
values Finding 43 and PC.3 Result 4 describe (start/stop voltages, timers, `0x15`). It has never
been seen to change an option *selection* byte. Note the G12 does react to a mode change: `0x15`'s
bit 0 tracks `EF ∈ {0,1}`, flipping `0x5A00 ↔ 0x5A01` a moment after the panel writes `EF`, with
no panel write behind it. So "the G12 ignores these frames" would be wrong; what is true is that
it re-derives *parameters*, never *selections*.

**The decisive observation** is the PI-off arm. At PC.3 t=76.668 the panel wrote `E6 := 0` **and
nothing else**; the G12 then reported `mode = 1` in six consecutive 5 s broadcasts across the next
29 seconds, until a manual tap cleared it at t=105.942. A G12 enforcing `PI ⟺ AGS` would have
cleared the mode. It did not. This is a single-variable observation the captures already contained
— the panel isolated the variable for us by having an asymmetric handler.

### The setting direction — CONFIRMED BY INJECTION ON HARDWARE 2026-08-23

Every `E6 := 1` in the corpus arrives with an `EF := 1` from the same handler, because the panel
always sends both, so the captures could close only the *clearing* direction. No screen tap could
close the other — a tap goes through the panel, which supplies the coupling itself. It took a
**bus injection**, and it is now done (`BUS-BASELINE.md` **PC.21**).

From a live coach with AGS, PI and both AES modes off (`E6 = 0`, `EF = 0`, `D7 = 0`), one frame
from source `0xC0` on `can1`, with no `EF` frame and no read-back twin:

```
19FED9C0#FF96E60F0100D1EA        E6 := 1, and nothing else
```

- **Positive control:** `E6` went `0` → `1` and held across five consecutive G12 broadcasts
  (~25 s). The write landed — so a null result would have meant something. It also establishes
  that **the G12's config-write path is blind to the source address**, exactly as the panel's
  receive filter is: two independent nodes, same property, and a bridge may write any selector.
- **The result:** `EF` stayed **`0`** throughout, `D7` stayed `0`, `0x15` stayed `0x5A01`. The
  G12 does not force AGS.
- **And the panel does not re-impose it on receipt.** Owner, watching the screen: *"Only PI is
  selected."* The new value reached slot 113 through the ordinary config-payload unpack and lit
  the checkbox, and the panel transmitted nothing. The coupling is in the **tap handler alone** —
  not in any consistency check over received config.

So the interlock is the panel's, in both directions:

| direction | evidence |
|---|---|
| G12 does not *clear* the partner | PC.3 t=76.668 — `E6 := 0` alone, G12 held `mode = 1` for 29 s |
| G12 does not *set* the partner | PC.21 — `E6 := 1` alone, G12 held `mode = 0` for 25 s |

Restored with `E6 := 0`; the sweep returned byte-identical to baseline. These are config
selectors in G12 NVM, not loads, and any mode row on the screen recovers them — which is what
made the probe cheap enough to run.

**The deselect asymmetry is `observed-on-hardware` too**, reported by the owner independently of
the code: *"disabling AGS does not deselect progressive inverter; after that, selecting either AES
option does deselect it."* That is the table above — `0x0024D0D8` emits `EF := 0` alone, both AES
select arms carry `E6 := 0`.

### Generality

`config.py`'s `selector_sites` finds the multi-selector runs structurally in 13.6 (`cfg 0x17` →
`E6` then `EF`; `cfg 0x13` → `EF` then `D7`; and the Bath/Cargo Light pair `E4:=1, E3:=0` /
`E3:=1, E4:=0`, a second, independent interlock of the same shape). Note its `frames` tuple is
**truncated at the first call boundary** and under-reports a long run — it returns 2 frames for
`cfg 0x13` where the handler emits 5. Walk the run to enumerate a handler's full emission; do not
read `SelectorSite.frames` as complete. 11.23 shows two coupled runs of its own; **14.7 resolves
no site for `cfg 0x17` at all**, which is a tool result, not a finding — its PI row was not traced
and no claim is made that 14.7 lacks the coupling.

### Removing the interlock — `interlocks.uncouple`, CONFIRMED ON HARDWARE 2026-08-23

Owner request: allow **Progressive Inverter and AES together**, which stock cannot express, and
rename that row to **`Renogy 48v Inverter`** (the coach's inverter is being replaced). Shipped in
reference build v6.

Because there is no interlock *logic* to excise, there is nothing to rewrite — only emissions to
delete. Each of the four cross-writes is the same 36-byte, twelve-instruction pair (write frame,
then its read-back twin), so removing one is **NOP-ing 36 bytes**: same-size, nothing shifts, no
pointer repair. `src/fireflymcf/interlocks.py` locates each block by **walking the run and
matching the selector**, never by a stored offset, asserts the twelve-instruction shape before
writing, and re-walks afterwards to prove the run is exactly what it was minus that pair.

| arm | before | after |
|---|---|---|
| PI row, ON `0x0024CCFA` | `E6:=1`, `EF:=1` | `E6:=1` |
| AGS select `0x0024D014` | `EF:=1`, `D7:=0`, `E6:=1`, `15:=0x2801`, `F7:=0x3C` | same minus `E6` |
| AES select `0x0024D10C` | `EF:=2`, `D7:=0`, `E6:=0`, `15:=0x2801`, `F7:=0x3C` | same minus `E6` |
| AES SO select `0x0024BF7E` | `EF:=3`, `D7:=0`, `E6:=0`, `15:=0x2801`, `F7:=0x3C` | same minus `E6` |

Three things worth carrying:

- **This is only safe to do unilaterally because PC.21 ran first.** Had the G12 enforced the
  interlock too, removing the panel's copy would have produced a panel that disagrees with the
  device it commands — the screen showing PI+AES while the G12 quietly re-forced AGS. The
  injection is what licenses the edit, not the disassembly.
- **`D7 := 0` is deliberately left in place** on the AGS/AES arms. It is a separate interlock
  (Go Power! count), out of scope, and inert on a WD coach that defaults the count to 0. Recorded
  because "removed the interlocks" would otherwise read as covering it.
- **The deselect asymmetry is untouched and still applies.** These edits change only what a
  *selection* emits, so `mode = AGS, PI = 0` remains reachable exactly as on stock. The build does
  not make the pair consistent; it makes them **independent**, which is the weaker and more
  honest guarantee.

The rename is a same-size GuiData edit: the field is 22 bytes (`'  Progressive Inverter'`) and the
new name is 19, so it fits with one trailing space. These OPTIONS rows are **`h_align = 1`, left**
— the very rows the 2026-08-13 dry run settled padding on — so the pad goes right and the drawn
start position does not move. (Do *not* copy `align="center"` from the reference build's caption work; that
record is `h_align = 2` and centres by the padded width.)

**Status: CONFIRMED ON HARDWARE 2026-08-23**, shipped in v7 (1,069,684 B, sha `e10a5aca…`; v6's
own 1,069,668 B / `744cbeb7…` was superseded before it was ever flashed). The flash had to settle
one thing the file cannot: that each row still *writes its own selector* after the NOP runs, i.e.
the removal took exactly the intended block and not one instruction more. It did — the owner
reports the build working as expected (`BUS-BASELINE.md` PC.22).

*Scope, stated because it matters:* what was itemised on the coach is the region split and all
three holds (`CONTROL-BEHAVIOR.md` §9) plus the inverter tap. The specific **PI + AES selected
together** pairing this section exists for ships in the same image and is expected to work, but was
not separately reported. Treat it as confirmed-by-inclusion, not as an itemised observation, until
someone selects both and watches the bus.

## Finding 51 — object 17 `INVERTER / CHARGER SETTINGS`: seven writable controls, and both halves of each one

`proven-from-code`, 2026-08-23. Object 17 (screen **14**) is the settings counterpart to Finding
49's read-only object 922, and it is the only page in 13.6 that lets the panel *change* the
inverter/charger. Asked by the owner, who wants to reuse its controls.

Each control is **two independent halves**, and this finding pins both:

* the **write** — a `wtype 12` hit region whose touch id is below the group count, so it dispatches
  through the LCD phase machine to a binding group that transmits `0x1FED9`;
* the **read** — a `value_slot` resolving through the cell table to an SRAM cell that
  `FUN_00247BF8` fills from the slot array, `r4` at `2*var`, exactly as Finding 49 describes.

They are joined only by sitting in the same object. Nothing in the file ties a control's selector
to the var its readout displays, which is the same missing pairing open thread 2 records for
pin status.

### The five always-visible controls

| control | obj | touch ids (−/+) | selector | wire delta | cell | var | source DGN | transform |
|---|---|---|---|---|---|---|---|---|
| `Low Batt. Cutout` V | 927 | 115 / 116 | `0x82` | ∓2 | `0x2003A688` | 126 | `0x1FFD2` u16 @`payload[5]` | `(raw+1)>>1` |
| `VAC Dropout` VAC | 931 | 117 / 118 | `0x86` | ∓5 | `0x2003A32D` | 127 | `0x1FF89` u8 @`payload[2]` | none |
| `Max Chrge Rate` % | 928 | 121 / 122 | `0x92` | ∓20 | `0x20039F98` | 114 | `0x15FCE` u8 @`payload[2]`, **instance `0x92`** | `>>1` |
| `Shore Brkr Size` A | 929 | 199 / 200 | `0x99` | ∓5 | `0x20039EBC` | 118 | `0x1FF96` u8 @`payload[3]` | none |
| `Battery Type` | 926 | 125 (single) | `0x88` | +1 | `0x200399B8` | 125 | `0x1FFC6` u8 @`payload[3]` | `>>4` |

Cell stores are at `0x0024A6DE` (Max Chrge Rate), `0x0024A738` (Shore Brkr Size), `0x0024A7EA`
(Battery Type), `0x0024A804` (Low Batt. Cutout) and `0x0024A80C` (VAC Dropout).

> **Seen on the coach — PC.29, 2026-08-24.** All five draw, and their readouts behave exactly as
> Finding 53 predicted from the absent DGNs: `Max Chrge Rate` **100%** (the only live one),
> `Shore Brkr Size` **255 A** and `VAC Dropout` **255 VAC** (`0xFF` unset rendered literally),
> `Low Batt. Cutout` **`---V`** (never received → dashes), `Battery Type` **blank**. The slot-23
> switch reads 0, so `Absorb`/`Float` are not drawn and object **302**'s empty 460×104 box shows
> in their place — confirming both halves of the draw gate below.
>
> **The write side followed on PC.30**, and every number in the table above reproduced: wire
> deltas ∓20 / ∓5 / ∓2 / ∓5, the two emission families (`D2 EA` per step vs `D2 FF` + `D3 FF`
> read-back + `D2 EA` 0 on release), and the sweep membership — `0x92` reported 122 times in ten
> minutes while `0x86`/`0x82`/`0x88` reported **zero** and `0x99` reported exactly **once**,
> answering its own `D3` read-back. `Max Chrge Rate` round-tripped 200 → 180 → 200 (`>>1` = the
> 100% on screen) and `Low Batt. Cutout` 214 → 212 → 214, **214 being the same raw PC.23 read
> back** by a different route.
>
> **The `EA`/`FF` flag is NOT settled by this.** `D2 FF` and `D3` always travel together in that
> dataset, so the report cannot be attributed to one rather than the other. It stays this
> finding's least-settled byte.
>
> **"Two independent halves" was observed, not just derived.** `Low Batt. Cutout` reads `---V` —
> var 126's `0x1FFD2` is sent by nobody — yet pressing it moved the G12 from 214 to 212 and
> relayed both times. The control works while its own display shows dashes.

**`Max Chrge Rate` closes the loop by itself, and it is the only one that does.** Its var 114
matches `0x15FCE` — the G12's configuration *status* broadcast — with the instance byte `0x92`,
which is byte-for-byte the `0x1FED9` selector its own arrows write. Read and write name the same
number. The other four read genuine RV-C charger/inverter status DGNs straight from the device
through an **open** filter (`0xFF` at `+4`), so on a coach with two chargers the last transmitter
wins — the same hazard v5 pinned for `1FFC7`/`1FEA3`/`1FF99`.

> **THAT PREDICTION CAME TRUE — `observed-on-hardware` 2026-09-02, `BUS-BASELINE.md` PC.46.** A
> second device joined the bus (the owner's rvc2mqtt virtual inverter on SA `0x42`) beside the
> Wakespeed WS500 on SA `0x80`; both answer `0x1FFC6`, and `Battery Type` (var 125) drew the
> **alternator regulator's** value — `Flooded` on a lithium coach. The WS500's frame is
> request-driven rather than periodic, so the wrong value latched once and stayed. The signature
> worth carrying: a field that is right almost always and wrong just after something on the bus
> restarts is an open filter, not a bad value. Fixed by `statusbind.pin_filter_instance(var=125,
> instance=1)` — one byte at `+4`, `ff` → `01`, shipped as recipe op `pin125` (built as
> `mcf-experiment-v13.bin`, not yet flashed). `1FFCA` (vars 116/117) is open on the same page and
> stays unpinned: uncontended today, and a pin with no observation behind it is a guess.
> *Update: `pin125` shipped in v17, flashed 2026-09-04, and every image since (the reference coach's build log (not published)).*

### Two more controls behind a switch: `Absorb` and `Float`

Object 17's last record is a type-6 switch on **slot 23** choosing between object **302** (an empty
460×104 box) and object **303**, which carries two further spinners:

| control | touch ids (−/+) | selector | wire delta | cell | var | source DGN |
|---|---|---|---|---|---|---|
| `Absorb` V | 186 / 187 | `0xBA` | ∓2 | `0x20039F30` | 206 | `0x1FECC` u16 @`payload[3]` |
| `Float` V | 188 / 189 | `0xBC` | ∓2 | `0x20039916` | 207 | `0x1FECC` u16 @`payload[5]` |

**Both are gated twice, by different mechanisms, and both gates must open.**

*Draw gate.* Slot 23 is receive-filter entry **23**: `0x1FEDA` DC_DIMMER_STATUS_3, **instance 6**,
mode 1 of `payload[6]`. Object 303 draws when it reads 1. Object 17 record 11 is the **only**
reference to slot 23 anywhere in the file, so this cell exists for nothing else.

*Write gate.* `FUN_00247BF8` refuses to fill either cell unless the raw `Float` var is in
`[0xEE, 0x169]` (`0x0024A812`-`0x0024A82A`) — a plausibility check on the value, not a feature
flag. A device that never sends `0x1FECC` leaves both cells stale whatever slot 23 says.

### The step size, and the one part that is inference

The wire delta is **not** the number that moves on screen; the transform divides it. Cell-level
steps are ±1 (Low Batt. Cutout, Absorb, Float), ±5 (VAC Dropout, Shore Brkr Size) and ±10
(Max Chrge Rate).

Whether the voltage widgets render a decimal was `inferred-from-code` when this was written and
is **`observed-on-hardware` since the same day** (`BUS-BASELINE.md` **PC.23**): a read-back of
selector `0x82` returns raw **214**, so the cell holds 107 and the panel shows **10.7 V** — the
right answer for a 12 V inverter, where 107 V would be absurd. The argument below is kept because
it reached the same answer from the bytes alone, before the bus was asked.

*The write gate settles it about as well as an inference can.* It admits raw `[0xEE, 0x169]`, i.e. cell values
119 to 181. As whole volts that is a nonsense range for a charger absorb setpoint; as one decimal
it is **11.9 V to 18.1 V**, exactly the range a 12 V charger occupies. On that reading every
voltage arrow steps **0.1 V**, which is also the only step size that makes a ±2 delta worth
shipping. Nothing was fitted to a photo here: no oracle image of this page exists, which is
precisely why it is labelled inference.

### `D1`/`D2`/`D3` — the function pair, confirmed on a second population

Every emission is `FF 96 <selector> 0F <int16 value> <function> <flag>`, and the three function
pairs `RVC-GAPS.md` §b derived from a handful of frames hold across all fourteen bindings here:
`D2` carries a signed adjust, `D3` with `FFFF` is a read-back request, and `D1 EA` (absolute set)
appears on **none** of them. So these controls only ever nudge; nothing on this page sets a value
outright.

The two-arrow controls differ in how much they say while repeating. `Low Batt. Cutout`, `VAC
Dropout`, `Absorb` and `Float` send one `D2 EA` per step. `Max Chrge Rate` and `Shore Brkr Size`
send `D2 FF` plus a `D3 FF` read-back on every repeat and one `D2 EA` with value **0** on release
— a zero adjust, which reads as a commit-and-report. That `EA`/`FF` distinction is the least
settled byte here: it is consistent with "report back" versus "stay quiet", and it is not proven.

`Battery Type` is the odd one: a single region, one emission on release only
(`phases 0x0A`, no repeat), delta **+1** — a cycle, not a spinner. Its cell transform is `>>4`,
so +1 on the wire is a sixteenth of a displayed step; the G12 owns whatever that field means.

> **CORRECTED ON THE BUS — PC.31, 2026-08-24.** "A cycle, not a spinner" was right; treating the
> `+1` as an arithmetic delta was not. Six presses relayed `p[6]` = 1, 2, 3, **13**, **0**, 1 — so
> the G12 holds an **enumerated set of battery-type codes `{0, 1, 2, 3, 13}`** and `+1` advances
> to the next *member*, not to the next integer. `3 → 13 → 0` is neither addition nor a clamp.
> Every other control on this page sends a delta the G12 applies arithmetically; this one is a
> different kind of command wearing the same binding shape.
>
> **The stored byte is the RV-C code itself.** Each `D3` read-back returned exactly the value just
> relayed (`0x88` = 2, 3, 13, 0), so there is no index→code table to look for.
>
> **The five codes ARE named, and the panel names them — corrected 2026-09-02.** This note used to
> end "what the five codes denote is not established here … no chemistry name is claimed"; that was
> conservative by one step. The `Battery Type` readout (object 926 record 6 → object 465) is a
> type-6 switch on value slot **225** whose five arms carry match values **0, 1, 2, 3, 13** — the
> same set, in the same order, that the bus walked — labelled `Flooded`, `Gel`, `AGM`, `Lithium`,
> `Custom`. So `0x0D` is indeed Lithium, as the RV-C enumeration suggested, and it is now
> `proven-from-file` rather than a guess. Read and write agree: the cell's transform is `>>4`
> (Finding 51's table), so the high nibble the G12 puts in `0x1FFC6` payload[3] comes back as the
> very code the `0x88` cycle stores. The join is one number in two places, not a fit.
>
> *Why the coach showed it blank in PC.29/PC.31*: nothing on this bus sent `0x1FFC6` then, so var
> 125 held the unset `0xFF`, and `0xFF >> 4` = **15** matches no arm. The switch carries no
> `0xFFFF` default candidate, so `FUN_00256C54` renders **nothing** — the blank was the no-match
> path, not a missing label.
>
> **And that changed on 2026-09-02 — `BUS-BASELINE.md` PC.46.** Two devices now answer `0x1FFC6`,
> the entry's filter is **open** (`0xFF` at `+4`), and the field read **`Flooded`** on a lithium
> coach: it was showing the **Wakespeed WS500 alternator regulator's** battery type (charger
> instance `0x31`, `payload[3] = 0x0D`, nibble 0), not the inverter's. So this arm list is no
> longer only theoretical — it is drawing, from the wrong device. The fix is the one-byte filter
> pin recorded in Finding 51.
>
> The cycle was then used as a *prediction*: four probe `+1`s from SA `0xC0` walked 1 → 2 → 3 →
> 13 → 0 exactly as modelled, restoring the coach's original value. Rule #55 (`0x88` → `1FFC4`,
> `p[6] ← 0x88`, mask `40`) reproduces all ten frames byte-for-byte, completing the last of
> PC.23's four relay mappings under a real press.

### Reusing a control — what actually has to change

Repointing a control at a different setting is **two edits, in two sections**, and doing only the
first is the trap:

1. **Write side — one byte of GuiData.** The selector is binding byte `+5`. Same-size, and the
   `+`/`−` pair plus any read-back binding must all move together.
2. **Read side — four bytes of GuiCode.** The readout follows `ldrsh.w r3,[r4,#2*var]`, whose
   T1 encoding holds a 12-bit immediate, so pointing it at another var is a same-size 4-byte
   edit. Leave it alone and the arrows change one setting while the number on screen reports a
   different one — which looks exactly like a control that does not work.

The transform is code too (`asrs`/`ubfx`/none), so a target whose scaling differs needs that
adjusted or the displayed number is wrong by a factor. Neither edit has been built or flashed;
this is the mechanism, not a shipped capability.

### Confirmed on the bus, and what commands the device — PC.23, 2026-08-23

Every claim above about a selector was checked by transmitting to the coach, and the relay
question it left open is answered. The G12 stores each selector and, **2-10 ms later, broadcasts
an RV-C command DGN carrying instance 1**: `0x82` → `1FFD0`, `0x86` → `1FF87`, `0x92` → `1FF95`,
`0x88` → `1FFC4`. `0x99` (`Shore Brkr Size`) relays **nothing** — the G12 keeps that one.

Two corrections to how this finding should be read. The controls do not "write generic selectors
that go nowhere": the panel commands the inverter through the G12, which is the RV-C speaker.
And **absence from the G12's periodic `0x15FCE` sweep does not mean the G12 lacks a selector** —
only `0x92` of these five is swept, yet all five answer a `D3 FF FFFF` read-back immediately. A
read-back is also the safe probe: it provokes no relay at all.

## Finding 52 — the G12 relay table: config selector -> RV-C command, 12 bytes per rule

`proven-from-file` + `observed-on-hardware`, 2026-08-23. Finding 51 left the panel writing
"generic" G12 config selectors with no visible connection to any inverter. PC.23 showed the G12
relaying them as real RV-C commands. **This is the table that does it**, and it is in the G12
section, not GuiCode.

*(Filed here rather than in `G12-CODE.md` because it is a data table in section `0x62`, not part
of the undisassembled Thumb blob at table `0x67`.)*

### The record

12 bytes, in G12 table **`0x62`** (94 records; the inverter/charger block is indices 49-66):

    0-1   DGN, low 16 bits, little-endian
    2     DGN bits 16-17 plus a class flag. 0x01 = a config-relay command rule (this
          section). 0xC1 = the periodic-BROADCAST class (54 records; decoded 2026-08-26, see
          addendum below). 0x81 = the five-record `0x1FFB7` TANK_STATUS family (PC.25). The
          rest (0x02, 0x09, 0x21, 0x25, 0x29, 0x41, 0xCD) also occur and are NOT decoded.
    3     substitution mask -- one bit per payload byte
    4-11  the 8-byte payload template, in the order [7,6,5,4,11,10,9,8]

A payload byte is a **literal** unless its mask bit is set, in which case it names a config
selector and the G12 substitutes that selector's stored value. Mask bit set *with* `0xFF` means
"substitution slot, no selector assigned" — which is what reconciles the `1FFD0` rule's `0x66`
mask (bits 1,2,5,6) with its two real selectors at payload[5] and [6]: bits 1 and 2 are set over
`0xFF` bytes and nothing is substituted, exactly as the coach transmitted.

**Byte order `[7,6,5,4,11,10,9,8]` is not a new discovery — it is the panel's own binding layout**
(two little-endian u32 words, `newbutton.binding_payload`). The same encoding on both sides of the
bus is why this decode was believed quickly, and it was still checked against captured frames
rather than assumed. `tests/test_g12rules.py` pins the two to each other.

### The block, and the four confirmed rules

| # | file off | DGN | inst | template | selector |
|---|---|---|---|---|---|
| 52 | `0x102930` | `1FFD0` | 01 | `01FFFFFFFF____FF` | `0x82` **Low Batt. Cutout** |
| 53 | `0x10293c` | `1FF95` | 01 | `01__FFFFFFFFFFFF` | `0x92` **Max Chrge Rate** |
| 54 | `0x102948` | `1FF87` | 01 | `01__FFFFFFFFFFFF` | `0x86` **VAC Dropout** |
| 55 | `0x102954` | `1FFC4` | 01 | `01FFFFFFFFFF__FF` | `0x88` **Battery Type** |
| 49 | `0x10290c` | `1FEBE` | 01 | `01FFFF____FFFFFF` | `0x8E` |
| 56 | `0x102960` | `1FF95` | 01 | `01FFFF__FFFFFFFF` | `0x90` |
| 58 | `0x102978` | `1FFC4` | 01 | `01FFFFFF____FFFF` | `0x8E` |
| 61-63 | `0x10299c`+ | `1FECB`/`1FF97` | 01 | | `0xB0`/`0xB2`/`0xB4` |
| 64-66 | `0x1029c0`+ | `1FECB`/`1FF97` | **02** | | `0xB0`/`0xB2`/`0xB4` |

Records 64-66 are instance-**2** duplicates of 61-63, so Firefly already ships multi-instance
rules — the mechanism for a second charger exists in the table, it is simply not used for the
four the panel's settings page drives.

### Addendum — the `0xC1` class is periodic broadcast, and it closes the record-59 question (2026-08-26)

The `0x01` rules above are **event-relays**: the panel writes a config selector, the G12 stores it
and *relays it once* as a real RV-C command. Byte-2 `0xC1` is a **different class — periodic
broadcast** — and decoding it settles the long-open "what fires record 59" (the owner work item,
`BUS-BASELINE.md` PC.33/34).

Of the **54** `0xC1` records: **52 are the `0x15FCE` config-value sweep** — `ce5fc10c
<sel><sel>00<sel>…`, one selector per record, which is the `195FCE9C` broadcast seen **88,930×**.
The two that target another DGN are the tell:

- **Record 44 → `0x1FACF`** (`cffac1bcf3f14449f200f5f4`, selectors `0xF1`–`0xF5` incl. the
  floorplan). The G12 is its **only** source and it transmits **every 5.00 s** (2,049 frames,
  `19FACF9C#49440D080D080006`). So a `0xC1` non-sweep record broadcasts **periodically and
  unconditionally once its feature is present** — it is *not* option-change-triggered.
- **Record 59 → `0x1FED9`** (`d9fec114ffe396ff00cf00e4`) would broadcast `19FED99C#FF96[E3]FF[E4]00CF00`
  — Bath/Cargo light options (`0xE3`/`0xE4`) as a config write, function `0xCF`. It fires **neither**
  in the 5 s sweep (unlike its sibling 44) **nor** on an option toggle, so it is **gated by a
  per-record condition** — the presence of the `0xCF`-consuming device — which this coach lacks.

So record 59's "trigger" is answered: it is a gated periodic broadcast in the `0xC1` class, and the
gate lives in the **G12 application at `0x08006000`**, not the MCF (same wall as the `0xBF`/AES
trigger). The residual is only *what `0xCF` means / which device consumes it* — unanswerable on a
coach where the gate never opens; it needs the app dump or a coach that ships the feature. The
option-toggle watch is retired. *(Not to be confused with the two real `19FED99C`-from-G12 frames in
pc39: selectors `0x79`/`0x7D` fn `D1`, the AES watchdog's own config writes — not record 59.)*

### The falsifiable prediction, and it held

The decode was checked the way this project requires: by a negative it could have failed.
PC.23 adjusted selector **`0x99`** (`Shore Brkr Size`) and the G12 stored it (0 → 5 → 0, proven by
read-back) while transmitting **nothing**. If this table is the relay mechanism, `0x99` must
appear in **no** rule. It does not — not in table `0x62`, not anywhere in the section.
`test_shore_breaker_size_drives_no_rule` pins it.

The positive control is the other four, whose every non-substituted payload byte reproduces the
captured frames exactly. Both directions are in the test file, and the payload-order and mask
mutations were checked to fail.

### Editing a command — the cheapest build shape this project has

`src/fireflymcf/g12rules.py`, `cli g12-rules`. Every field is a same-size byte edit and the
blast radius is **the byte plus the G12 section's 2-byte checksum** — re-derived by a byte diff in
the tests, not assumed. Since the **G12 section is independently flashable** (owner, 2026-08-18),
a rule change ships as a G12-only build that never writes the GuiCode/GuiData pair, so it cannot
strand a relocated pointer or silently drop a GuiData feature. Nothing else in this project has
that property.

The most useful single byte is the **instance**, payload[0] = record byte 7: every shipped
charger/inverter rule commands instance `0x01`, so a device claiming a different instance is
retargeted one byte per rule.

Three refusals are deliberate. `set_rule_selector` will not write a selector into an **unmasked**
slot, because the mask is what makes substitution happen and the result would be a literal that
merely looks like a selector. `set_rule_dgn` will not change DGN bits 16-17, because record byte 2
mixes them with flags that are still `unknown`. And the module will not create a rule or grow the
table — the G12 runs a real event→action program whose interpreter is largely unread, so a
malformed record is a risk with no upside.

**Not yet flashed.** Every edit here is verified in-file only; no G12-only build has been built or
loaded onto the coach.

### Addendum, 2026-08-24 — the "registry" is the SAME format, and a u16 spans two slots

Chasing table `0x62` record 59 (below) turned up something larger. The `ce 5f c1` records that
`g12_config.RuleRecord.is_registry` has always split off are **not a second mechanism**: they are
ordinary records in this exact format, each the transmit template for the G12's periodic
`0x15FCE` broadcast of one selector. So **all 151 records in tables `0x20`/`0x30`/`0x61`/`0x62`
are transmit templates**, and "registry entry" is simply what a template for `0x15FCE` looks like.

Two corrections fall out, both proven against the capture rather than argued:

**Adjacent masked slots naming the same selector are ONE little-endian u16.** Selector `0x92`'s
template masks payload[2] and payload[3] and the coach transmits `C8 00` — 200 LE, the live value
— not `C8 C8`. `g12rules.CommandRule.value_fields` merges them; the `1FFD0` charger rule's
payload[5]/[6] pair is the same construction, which is why that rule carries a u16 setpoint.

**Validation, and it includes a genuine cross-record prediction.** For all **44** registry
selectors the coach was observed sweeping, every unmasked byte matches the captured frame — zero
mismatches. Most templates name only their own selector, which alone would be circular; **record
10 does not.** It broadcasts selector `0x01` and packs a second u16 at payload[6..7] naming
selector **`0x03`**, so the value in `0x01`'s frame must equal what `0x03`'s *own* frame reports.
It does (`FFFF` both). Two different frames predicted from one record.
`test_a_sweep_frame_carries_each_named_selectors_live_value` pins it; both halves mutation-checked.

*This supersedes the "two mechanisms share one table" framing used when Finding 52 was written.*

### Record 59 — decoded as a template, UNEXPLAINED as a behaviour

The record that prompted the above, recorded honestly because it is the one that did not resolve.

    0x102984   d9 fe c1 14 ff e3 96 ff 00 cf 00 e4
    DGN 1FED9 (config write), group 0x96, mask 0x14
    template   FF 96 [E3] FF [E4] 00 CF 00

**What is established.** The masked slots take the *live values* of option selectors `0xE3` and
`0xE4` — value substitution, not the selector numbers, which record 44 settles independently
(its payload[5] names selector `F5` and the coach transmits `08`, the floorplan **WD**, not `F5`).
Both are booleans; `E3` = 1 and `E4` = 0 on this coach, so the frame today would be
`FF 96 01 FF 00 00 CF 00`. Its shape `FF <grp> <sel> FF <val> .. <fn> ..` matches the **group
`0x94`** layout of `RVC-GAPS.md` §a, *not* the group-`0x96` layout the panel uses.

**What is not.** Function byte `0xCF` occurs in **no other record** and in **no captured frame**
— across all eleven captures the 782 `1FED9` frames carry 374 `D3`, 321 `D1`, 74 `D2`, 9 `E0`,
2 `F0`, 1 `E5`, 1 `E3`, and no `CF`. And the **G12 has never been observed sourcing `1FED9` at
all**. Byte 2's kind field (`0xC1` here) is shared with records that *do* broadcast periodically,
so it is not the trigger either. The template is decoded; what the rule does, and what makes it
fire, are **unknown**.

*A trap avoided, worth recording.* Grepping the captures for `FED9` outside the panel's source
address returns nine hits from `18EF8070` — they are `0001FED9` appearing as payload *data*
inside a proprietary frame, not DGN `1FED9` frames. Two of this project's eleven wrong assertions
came from exactly this kind of reconnaissance artifact; match the CAN id, never the substring.

**It does NOT close open thread 8.** That thread asks which of `E3`/`E4` binds to channel 25
versus 26. Record 59 carries both booleans in one frame and names no channel, so it adds nothing
to the pairing — only further evidence that the G12 treats the two as a pair, which was already
known.

> **TESTED ON THE BUS — NEGATIVE. `BUS-BASELINE.md` PC.33, 2026-08-24.**
>
> The experiment this section calls for was run: the owner toggled both OPTIONS rows six times,
> covering `E3` and `E4` in both directions, with `candump -L can1` watching. **Zero `19FED99C`
> frames in 13,087.** All 23 `1FED9` frames in the capture are panel-sourced. Record 59 did not
> transmit, `0xCF` is still undefined, and the "G12 has never been observed sourcing `1FED9`"
> record extends to a twelfth capture.
>
> **What this rules out is one trigger, not the rule.** The hypothesis tested was the obvious and
> cheapest one — that a rule whose payload substitutes `E3` and `E4` fires when `E3` or `E4`
> changes. It does not. Nothing here bounds what else could trigger it, so "dead code for a
> product this coach lacks" remains a possibility rather than a conclusion.
>
> A whole-capture scan for tap-correlated traffic found no CAN id appearing only within ±0.5 s of
> a tap, so the panel's `1FED9` write and its paired `1FEDB` OFF (below) are the complete
> tap-time traffic — there is no third frame to have missed.
>
> *What the run did settle, as a by-product:* `E3` = `Cargo / Bath Light` and `E4` = `Bath Light`
> confirmed against the owner's own narration of which control they pressed, the pair proved
> **mutually exclusive and enforced panel-side** (a third instance of Finding 50's mechanism,
> with Finding 50's deselect asymmetry intact — both-off is reachable), and Finding 23's final
> section observed **executing**. See the note there.

## Finding 53 — screen 14 `INVERTER / CHARGER SETTINGS` is ORPHANED in stock 13.6

`proven-from-code`, 2026-08-24. Raised by the owner, who asked whether the page might be a
leftover rather than a live screen. **It is a leftover.** Nothing that can be displayed opens it.

The chain is short and complete:

1. GuiCode contains **exactly one** store of screen 14 to `state+0x20A` — `0x0024B96C`, from a
   file-wide scan of `strb.w rX,[rY,#0x20a]` (77 sites, each traced back to the constant it
   writes).
2. That store is guarded by **action id `0x7DE`** (`movw r3,#0x7de` / `cmp` / `bne` at
   `0x0024B960`-`0x0024B96A`).
3. `0x7DE` is carried by **two** touch records in the whole file, in objects **1052 and 1053**.
4. Neither object is in the display graph — no container names them, and no type-6 switch has
   them in its candidate range.

So the page cannot be reached by touch. Both objects are menu rows whose labels **disagree with
their targets** (`'Switch Panel Info >'` → the inverter page; `'Mobile App >'` → CLEAR EEPROM),
which is what stale rows look like after screen ids have been reassigned between firmware
generations.

`tests/test_g12rules.py::test_screen_14_is_orphaned_in_stock` pins both halves and fails if a
future build attaches either object — giving the page a route should be a deliberate act.

### Three things this changes about Finding 51

**reference build v8 gives the page its first route.** The hold dispatch writes the screen
id straight to `state+0x20A` and never consults the action-id chain, so it bypasses the missing
registration entirely. That is a point in v8's favour — but it also means **no one has ever seen
this page on this coach**, and nothing about its runtime behaviour is confirmed.

**Three of its seven controls are wired to selectors the G12 does not relay.** Finding 52's rule
table has no entry for `0x99` (`Shore Brkr Size`, obj 929), `0xBA` (`Absorb`) or `0xBC` (`Float`).
Their **relaying twins exist in the file and are unattached**: obj **930** `Shore Brkr Size` on
selector `0x90` (rule #56, `1FF95` payload[3]) and obj **925** `Absorb Hours` on selector `0x8E`
(rules #49/#58). Firefly placed the non-relaying variants. Whatever the reason, pressing those
three on the page stores a value the G12 forwards to nobody.

**Its readouts have no source on this coach.** Of the page's six status fields only `Max Chrge
Rate` (var 114, `0x15FCE` instance `0x92`) is fed by anything the 30 s PC.23 baseline contains.
`0x1FFD2`, `0x1FF89`, `0x1FFC6`, `0x1FF96` and `0x1FECC` produced **zero frames**, so those cells
read whatever they were left at — and the read-backs agree (`0x99` = 0, `0x88` = 0).

### What is and is not confirmed, stated plainly

PC.23 proved the **G12** relays four selectors, by injecting `0x1FED9` from the Pi at SA `0xC0`.
It did **not** press a button, and it could not have — the page cannot be opened. So:

| claim | status |
|---|---|
| the G12 relays `0x82`/`0x86`/`0x88`/`0x92` to `1FFD0`/`1FF87`/`1FFC4`/`1FF95` | `observed-on-hardware` |
| the rule table encodes that mapping | `proven-from-file`, reproduces the captured frames |
| screen 14 is unreachable in stock | `proven-from-code` |
| the page renders correctly at runtime | **`observed-on-hardware`** — v8, PC.29 |
| its buttons emit the frames their bindings describe | **`observed-on-hardware`** for 4 of 7 — PC.30 |
| `0x99` is stored by the G12 and relayed to nobody | **`observed-on-hardware`** — PC.30 |

### Seen on the coach — PC.29, 2026-08-24, and the predictions held 6 for 6

reference build v8 gave the page its first route and the owner photographed it. Every row
of "its readouts have no source on this coach" is confirmed, including the one positive:

| control | displayed | this finding predicted |
|---|---|---|
| `Max Chrge Rate` | **100%** | live (var 114, `0x15FCE` inst `0x92`) |
| `Shore Brkr Size` | **255 A** | dead — no `1FF96` |
| `VAC Dropout` | **255 VAC** | dead — no `1FF89` |
| `Low Batt. Cutout` | **`---V`** | dead — no `1FFD2` |
| `Battery Type` | **blank** | dead — no `1FFC6` |
| `Absorb` / `Float` | **not drawn** | slot-23 gate closed → Finding 51's object 302 empty box |

Worth stating plainly because this project more often records the opposite: **a prediction made
entirely from the file, about a page nobody had ever displayed, was right on all six rows** —
including the negative rows, which are the ones a fitted answer gets wrong. The 30 s PC.23
baseline was doing real work as an oracle for what is absent from this bus.

### The write side — PC.30, 2026-08-24, and the `0x99` negative HOLDS

The owner pressed four of the controls while the bus was watched. All four transmit, every wire
delta matches Finding 51, and the G12 relayed three of them to the DGNs the rule table names
(`0x92`→`1FF95`, `0x86`→`1FF87`, `0x82`→`1FFD0`, instance 1, 1.6-10 ms).

**`0x99` produced no relay of any DGN**, while the G12 demonstrably *stored* it (one
`195FCE9C#990005…` two milliseconds later). That is this finding's falsifiable prediction, and it
is the reason the rule-table decode carries weight: a table that reproduced only the frames that
already exist could not have been wrong here.

`0xBA`/`0xBC` remain untested and unreachable — their controls do not draw while the slot-23 gate
is shut. `0x88` (`Battery Type`) was deliberately not pressed: `+1` per release with no decrement,
a blank readout, and it relays to `1FFC4`, a charger config command.

*A precision fix to a claim made for v7.* `CLAUDE.md` says id `0x7E1` (SERVICE MODE) "is
registered only by unattached objects 92/690/826". Object **690** is in fact a type-6 candidate
under the page header container (892), so it is in the graph — it is simply only drawn when that
switch selects it. The v7 conclusion is unaffected (the gear hold was confirmed on the coach), but
"unattached" was the wrong word for one of the three. Note the general caution the scan forces:
membership in the graph means *can be displayed*, never *is displayed*.

---

## Finding 54 — the G12 INPUT pins ride `0x1FBDA`, and table `0x20` is the input handler

**Question (owner, 2026-08-24):** *"Are any of the G12 values the G12 input status? Input pin
status is displayed on the Network Diagnostics → Devices/Faults screen."*

**Answer: no — and the real mechanism is a different DGN, a different rule table, and a path
that never touches a config selector.** Input state is broadcast as **`0x1FBDA`
GENERIC_INDICATOR_COMMAND, instance `0xA0 + pin`**, with the state in `payload[1]`. None of the
`0x15FCE` selectors decoded in Finding 52 carries it.

Confidence: **`proven-from-code`** on the panel side (cited instructions below),
**`proven-from-file`** on the G12 side. **Not `observed-on-hardware`** — see the honest limit at
the end.

### The chain, end to end

    G12 input pin N
      -> table 0x20 transmit rule    19FBDA9C#[A0+N] [01|00] FF FF FF FF FF FF
      -> panel receive-filter var 191+N   (DGN 1FBDA, instance filter A0+N, mode 1, payload[1])
      -> slot array [2*var]
      -> FUN_00247BF8 @0x24b382..0x24b420  normalise to 0/1, strb to an SRAM byte
      -> value slot on object 864 (`G12 INPUTS`, DEVICES/FAULTS = object 14, screen 11)

**(a) The G12 side — table `0x20`, file `0x102324`-`0x102474`.** Nine indicator instances, each
an ON/OFF pair of ordinary 12-byte transmit records (§9a's format, unchanged):

    0x102324  dafb 05 05 ffff01a1ffffffff   ->  wire  a1 01 ff ff ff ff ff ff
    0x102330  dafb 01 0A ffff00a1ffffffff   ->  wire  a1 00 ff ff ff ff ff ff

`payload[0]` is the instance, `payload[1]` the state. Record bytes 2 and 3 differ between the two
(`05`/`05` active, `01`/`0A` inactive) and are **not decoded** — the complementary `0x05`/`0x0A`
bit patterns look like an edge or level condition, but byte 3 is the *substitution mask* in table
`0x62` and plainly is not being used that way here, so `input_rules` tells the pair apart by
`payload[1]` and reads neither byte.

**(b) The panel side — receive-filter vars 192-201**, a perfectly regular block:

| var | DGN | instance filter | mode | payload byte |
|---|---|---|---|---|
| 192 | `0x1FBDA` | `0xA1` | 1 | 1 |
| … | … | … | … | … |
| 201 | `0x1FBDA` | `0xAA` | 1 | 1 |

**(c) GuiCode — `FUN_00247BF8` @ `0x24b382`-`0x24b420`**, ten identical five-instruction blocks:

    0024b382  ldrsh.w r3,[r4,#0x180]     ; 0x180 = 2*192 -> var 192 (input 1)
    0024b386  cmp     r3,#0x1
    0024b388  ite ne
    0024b38a  mov.ne  r3,#0x0
    0024b38c  mov.eq  r3,#0x1
    0024b38e  ldr     r2,[0x0024b548]    ; = 0x2003A14C
    0024b390  strb    r3,[r2,#0x0]

stepping `+2` per input to `0x192` (var 201). The ten pool words sit contiguously at
`0x24b548`-`0x24b56c`, each referenced exactly once — so this run is the complete writer set.

### The join, and why it is evidence rather than a fit

| pin | inst | var | SRAM (GuiCode) | slot (GuiData) | row caption |
|---|---|---|---|---|---|
| 1 | `0xA1` | 192 | `0x2003A14C` | 300 | `1 - Kitchen OHC Lts (GND)` |
| 2 | `0xA2` | 193 | `0x2003A0E5` | **242** | `2 - ` |
| 3 | `0xA3` | 194 | `0x2003A0D5` | 301 | `3 - Bath Fan (GND)` |
| 4 | `0xA4` | 195 | `0x2003A145` | 302 | `4 - ` |
| 5 | `0xA5` | 196 | `0x2003A139` | — | *(no row)* |
| 6 | `0xA6` | 197 | `0x2003A136` | 304 | `6 - Awning EXT (12V)` |
| 7 | `0xA7` | 198 | `0x2003A13A` | 305 | `7 - Awning RET (12V)` |
| 8 | `0xA8` | 199 | `0x2003A12C` | — | *(no row)* |
| 9 | `0xA9` | 200 | `0x2003A129` | 307 | `9 - Water Pump (12V)` |
| 10 | `0xAA` | 201 | `0x2003A4E5` | **132** | `10 - Ignition(12V)` |

Three sources were read separately and agree:

- **`2*var` lands exactly.** `0x180 / 2 = 192`, and entry 192 begins a run of ten consecutive
  `1FBDA` entries with consecutive instances. Finding 33's "var is one number throughout" holds
  here too, and nothing in the derivation assumed it would.
- **Slots 242 and 132 break the tidy run.** `300..307` covers pins 1,3,4,5,6,7,8,9 — a decode
  that guessed `slot = 299 + pin` would produce exactly that and be wrong for two rows. The
  GuiData walk (`g12rules.input_rows`, captions + records only, no GuiCode) recovers 242 and 132
  independently, and they resolve through the dptr table to the same registers the GuiCode run
  stores into. `tests/test_g12rules.py::test_screen_slots_reproduce_the_guicode_register_join`.
- **Pins 5 and 8 are the negative.** The panel allocates vars 196/199 for instances `0xA5`/`0xA8`,
  the G12 section holds **no** rule for either, and the screen shows **no** row for either. The
  two images were never compared to produce that; they simply agree.

### Table `0x20` is the input handler, not just an indicator list

The `1FBDA` pairs are interleaved with `1FEDB` DC_DIMMER_COMMAND_2 records, and the outputs those
name match the input captions:

| records | wire | reading |
|---|---|---|
| 0-2 | out `0x16` = 22 | input 1 `Kitchen OHC Lts` → output 22 `Kitchen OHC Lts` |
| 7 | out `0x28` = 40 | input 3 `Bath Fan` → output **40**, the Bath Fan's channel before the remap |
| 25 | out `0x2c` = 44 | input 9 `Water Pump` → output 44 `Water Pump` |

~~Grouping a rule with the pair that follows it is an inference from adjacency.~~ **Upgraded to
`proven-from-file` the same day — see Finding 54a below, which found the table that states the
grouping outright.**

**One consequence is load-bearing, and it corrects a claim in `CLAUDE.md`.** The awning input's
rules are

    0x1023B4  wire 04 ff 00 03 ff 00 ff ff   ; instance 4 (RET), command 3 = OFF
    0x1023C0  wire 03 ff c8 01 02 00 ff ff   ; instance 3 (EXT), command 1 = ON, level 200

which is byte-for-byte the `(4,3)` RET-off / `(3,1)` EXT-on pairing documented for **GuiData
binding group 3**. So the EXT/RET mutual exclusion is enforced **twice, independently**: by the
panel's bindings for the on-screen button, and by the G12's own table `0x20` for the physical
switch. The "Where a pin's behaviour is enforced" table credits GuiData alone; that is right for
the screen and incomplete for the switch.

### What is NOT claimed

- ~~**Never observed on the wire.**~~ **OBSERVED 2026-08-24** (`BUS-BASELINE.md` PC.24,
  `captures/pc24-input-pins-2026-08-24.log`). Every predicted frame appears byte for byte,
  instance `0xA0 + pin` and `payload[1]` as the state both confirmed, and pins 5 and 8 are
  silent exactly as their rule count of 0 predicted.
  **But the edge-triggered guess in this bullet was WRONG:** every instance repeats at
  **`1.000 s` while held**, GND and 12V alike — a refresh from the ON edge (eight taps from
  82 ms to 872 ms each produced one ON/OFF pair and no repeat). The re-send is not in the rule
  table because it is not a template; it lives in the code that fires them, table `0x67`, which
  is still mostly unread. *A table that holds only ON and OFF records tells you what is sent,
  never how often.*
  One further frame has no decode on either side: `0xBF`, twice, bracketing ignition-off, with
  an all-**zero** tail rather than the inputs' `FF`. It is in no rule table and no dispatch
  entry.
- Record bytes 2/3 in table `0x20` are undecoded, as above.
- Instance `0xFB` has a pair in table `0x20` and a receive-filter entry at **var 56** (`2*56 =
  0x70`, the `ldrsh.w r2,[r4,#0x70]` at `0x24b422` immediately after the input run). It is not an
  input *instance* — but it **is broadcast by input pin 10**, the ignition sense, which fires four
  rules (Finding 54a). **It is the ENGINE RUNNING signal** — identified the same day by
  Finding 55, which traces `0x20039FD9` (the byte that `0x24b430` stores it into) to the
  ELECTRICAL page's `Engine Running` / `Engine Not Running` text. So the ninth pair in table
  `0x20` is not a tenth input pin but a different sensor on the same indicator namespace.

### The cheap experiment this hands over

`10 - Ignition(12V)` is a *labelled* ignition sense input, which is a far more direct probe than
the open `1FFB7` instance-3 bit that "Where a pin's behaviour is enforced" flags as the one
unproven link in the awning interlock chain:

    ssh pi@<gateway>
    candump -L can1 | grep -i 19FBDA9C

then turn the key. `AA 01` / `AA 00` settles it outright — and slot 132 paints the row on
DEVICES/FAULTS, so the screen confirms it without the bus. The same trip can watch
`19FFB79C#03....` and close both.

### Tooling

`src/fireflymcf/g12rules.py`: `all_rules`, `table_census`, `indicator_rules`, `input_rules`,
`input_rows`, `input_status`, `describe_inputs`; `cli g12-inputs [--json]`.

**Why this stayed hidden for a day, which is the reusable part.** Every entry point in
`g12rules.py` read table `0x62` — `command_rules`, `sweep_templates` and therefore
`cli g12-rules` — so three of the four rule tables were invisible to the only tool that would
have shown them, and Finding 52's "151 records across tables `0x20`/`0x30`/`0x61`/`0x62`" was
true of the *decode* while the *listing* showed 94. `cli g12-rules` now opens with a census of
all four tables, and `test_table_census_covers_all_four_rule_tables` fails if that narrows again.
*A tool scoped to the question you asked last will answer the question you ask next, wrongly.*

---

## Finding 55 — `Engine Running` / `Engine Not Running`: what drives it, and why it goes stale

> **CLOSED 2026-08-24 — NOT A DEFECT, do not reopen.** The owner attributes the original
> observation to **an early build of a bad firmware**, an image not in this corpus. Correct
> behaviour is confirmed three ways: ignition on, ignition off, and an AES auto-start raising the
> sense line (PC.28). Everything below is retained as a record of how two mechanisms came to be
> built on an unpinned symptom, which is the only part still worth reading.
>
> **RETRACTED IN FULL 2026-08-24 — the argument AND the symptom.** The owner re-tested on the
> coach: sitting on ELECTRICAL and turning the ignition on, **the text changed to `Engine Running`
> immediately**, and back to `Engine Not Running` on ignition-off. It also updated correctly when
> the van's own AES started the engine. **There is no compose-time staleness.** The display path
> works.
>
> The residual is far narrower than the original report and may not be a display fault at all:
> the owner now believes the non-updating case was specifically **after using the `Start` button on
> the ELECTRICAL page**. That is a different question — see the note at the end of this finding —
> and until it is captured, *nothing here should be cited as a display defect*.
>
> **What this cost, recorded because the pattern repeated three times in one day.** Two hypotheses
> were built on this symptom — "screen 0 is the background root and re-walks, child pages do not"
> and "type-0 slots are status and re-read, type-1 slots are config and do not" — complete with a
> discriminating test. Both are now moot: ELECTRICAL is a child page, slot 169 is type 1, and it
> updates live, so each hypothesis is refuted by the same observation. **They were explanations for
> a symptom that had never been pinned down.** Reproduce the fault yourself, or get an exact
> reproduction, *before* building a mechanism for it.
>
> *(Superseded text follows.)* **ARGUMENT WITHDRAWN 2026-08-24, symptom intact.** The reasoning below — "`0x2003A50C` has one
> pool literal and six references, all six stores, so nothing in GuiCode reads it and its only
> consumer is the layout walk at compose time" — **does not hold.** A value-slot register is read
> as `DAT_00253D04[slot]` (Finding 17), i.e. by *index off a table base*, so its address never
> appears as an immediate and a literal scan cannot find the read however often it happens. The
> same step in Finding 56 produced a prediction the coach refuted within hours (`BUS-BASELINE.md`
> **PC.26**): slot 132's switch repaints **live**.
>
> **`Engine Running` really does go stale** — that is owner-observed and is not in question. What
> is now unknown is *why*, given that slot 132 and slot 169 come from the same DGN (`0x1FBDA`) and
> the same input pin (10), are both type-6 switches, and behave oppositely. PC.26 lists the
> untested candidates. Do not adopt one without evidence.


**Question (owner, 2026-08-24):** *"On the Electrical page, when AES is selected, what drives the
Engine Not Running / Engine Running text? Also, this seems to have a bug where it only updates if
you leave the screen and return."*

Both halves answered. The text is driven by **`0x1FBDA` instance `0xFB`** on this coach — the same
GENERIC_INDICATOR_COMMAND namespace as the input pins (Finding 54), which is how that finding's
unidentified ninth instance got a name. The staleness is **firmware behaviour and not fixable by
any same-size edit**, for a reason that is `proven-from-code`.

### The widget

`ELECTRICAL` → the AES block (object **829**, captions `  AES`, `Battery Voltage:`, `Quiet Time
Start`) → object **353**, which is a label `Engine` followed by a **type-6 value-match switch on
display slot 169**. Four candidates, not two:

| match | object | text |
|---|---|---|
| `0xFFFF` (default) | 526 | `Not\nRunning` |
| 2 | 527 | `Starting` |
| 3 | 528 | `Running` |
| 4 | 529 | `Stopping` |

So `Engine` is a static label and only the second word-pair is switched — which is why the two
strings never appear as one label anywhere in the file.

### What writes slot 169

Display slot 169 resolves through the variable dptr table to SRAM **`0x2003A50C`**, and that byte
is written by a state machine in `FUN_00247BF8`. **Beware the numbering trap here** — display
`value_slot` 169 and receive-filter *entry* 169 are different things that happen to share a
number (`MCF-FORMAT.md` §5.4a already flags this for entry 16). Entry 169 watches `0x1FBDA`
instance `0xFC` and is **not** what feeds this text.

    0024a42e  ldrsh.w r3,[r4,#0x52]      ; var 41 = 0x15FCE selector 0x01, payload[2]
    0024a432  cmp     r3,#0x1
    0024a434  beq     0x0024a49a         ; == 1 -> the generator path
    0024a436  ...                        ; else: 0x2003A5D8 := 0
    0024a43c  ldr     r3,[0x0024a5c4]    ; = 0x20039FD9  <- var 56 = 0x1FBDA instance 0xFB
    0024a440  cmp     r3,#0x1
    0024a442  beq     0x0024a4e0         ; engine indicator set -> state := 3 "Running"
    0024a444  ldr     r3,[0x0024a5c8]    ; = 0x2003A50C
    0024a448  strb    r2,[r3,#0x0]       ; else state := 0 -> default -> "Not Running"

and the generator path, for completeness:

    0024a49a  ldrsh.w r3,[r4,#0x6e]      ; var 55 = 0x15FCE selector 0x04
    0024a4a0  beq     0x0024a4ba         ; == 1 -> state := uxtb(var 296)
    0024a4a6  ldrsh.w r3,[r4,#0x250]     ; var 296 = 0x1FFFD GENERATOR_STATUS_1 inst 1, u16 @[2]
    0024a4aa  cmp     r3,#0x4
    0024a4ac  beq     0x0024a4ca         ;  == 4 -> "Stopping"
    0024a4ae  cbnz    r3,0x0024a4d4      ;  != 0 -> 6 (no candidate -> "Not Running")

**Which branch this coach takes is settled by the bus, not by inference.** Selector `0x01`'s
`0x15FCE` sweep frame carries `payload[2] == 0x00` in **910 frames across every capture**
(`195FCE9C#010000000000FFFF`), so `cmp r3,#1` never matches and the coach always takes the second
branch. On an AES coach the display is therefore **two-state**, driven by one bit:

    19FBDA9C#FB01FFFFFFFFFFFF   ->  Engine Running
    19FBDA9C#FB00FFFFFFFFFFFF   ->  Engine Not Running

`Starting` and `Stopping` are reachable only through the generator path and are dead on this
coach. As with Finding 54, **instance `0xFB` has never been observed** — consistent with a parked
coach and an edge-triggered rule, but unproven.

### Why it only updates on re-entry — `proven-from-code` in its load-bearing half

`0x2003A50C` has **exactly one pool literal** in GuiCode (`0x24a5c8`) with **six references, and
all six are stores** (`0x24a444`, `0x24a4b4`, `0x24a4c4`, `0x24a4ce`, `0x24a4da`, `0x24a4e0`).
**Nothing in GuiCode ever reads it.** Its only consumer is the GuiData layout walk, which reads it
through the dptr table when it evaluates the type-6 switch — that is, when the page is *composed*.

That is the whole bug. The panel's live-repaint path is driven by the **GuiCode bridge blocks**
that copy a slot into a colour cell (Findings 32-33, `statusbind`); a widget repaints in place
because some block wrote the cell it names. The engine state has no such block, so nothing exists
to trigger a repaint, and the selected child object stays whatever the last compose chose.

**The owner's own observation is the proof that the data path is fine.** If leaving the screen and
returning shows the correct state, then `0x2003A50C` was already up to date while the wrong text
was on screen — the frame arrived, the filter matched, the state machine ran. Only the *composed
selection* was stale. That isolates the fault to compose-time evaluation and rules out the bus,
the filter and the state machine in one stroke.

*Honest limit:* the "no bridge block, therefore no repaint" step is `inferred`, because the widget
redraw dispatch itself is still unlocated (see the correction in `CLAUDE.md`'s "Adding a new
button"). What is proven is the premise — nothing reads the byte — and the inference matches the
reported behaviour exactly.

### Can it be fixed? No, and the negative is a familiar one

- **The text cannot be made live.** It is selected by a type-6 switch, which is evaluated by the
  layout walk. Nothing about that is a data value an edit could repoint.
- **A live colour indicator is not available either.** That would need var 56 to be one of the
  bridged vars, and it is not: the bridged set on 13.6 is vars **18-43 and 66-85** — the pin-status
  vars, nothing else. This is the same hard constraint `statusbind` documents: GuiCode's per-var
  blocks are straight-line code, so *a var with no block cannot be given one* without inserting
  code.

So this is a **latent firmware defect** in the same class as Finding 41's GO POWER! tab: real,
diagnosed, and not worth a build. Recording it is the deliverable.

*Corollary worth carrying:* a type-6 switch is a **configuration** mechanism. Every other one in
the corpus gates on something that changes rarely — floorplan (slot 5), options, the config-derived
battery block (slot 6) and nav rail (slot 453). This one gates on a live bus value, and that
mismatch is the defect. **If a future build needs a display that tracks a live value, it must be a
value widget or a bridged colour cell, never a type-6 candidate switch.**

---

## Finding 54a — table `0x10` is the INPUT DISPATCH table, and it says what sets `0xFB`

**Question (owner, 2026-08-24):** *"Do we know what sets `0xFB`?"* — the instance Finding 55
traced the `Engine Running` text to.

**Yes: G12 input pin 10, the ignition sense.** `proven-from-file`.

### The record does not carry its own trigger

The first thing checked, because it decides where to look. Table `0x20`'s `0xFB` pair and input
pin 1's pair differ in **exactly one byte position — `+7`, the instance**:

    0x102324  da fb 05 05 ff ff 01 a1 ff ff ff ff   input 1, report ON
    0x102450  da fb 05 05 ff ff 01 fb ff ff ff ff   0xFB,    report ON

So nothing in the record says which input fires it. The association is external.

### Table `0x10`: ten 8-byte entries, one per input pin

| bytes | field |
|---|---|
| `+0..1` | `0x0010`, an entry tag |
| `+2` | `0x0C` configured, `0x00` unconfigured |
| `+3` | bit `0x40` = sense is **12V**; clear = switched to **ground** |
| `+4..5` | u16 index of the pin's **first** rule in table `0x20` |
| `+6` | how many rules the pin fires |
| `+7` | `0xFF` |

Decoded on 13.6:

| pin | sense | first | count | what it fires |
|---|---|---|---|---|
| 1 | ground | 0 | 5 | `1FEDB`×3 to output 22, then the `0xA1` pair |
| 2 | ground | 5 | 2 | the `0xA2` pair |
| 3 | ground | 7 | 3 | `1FEDB` to output 40, then the `0xA3` pair |
| 4 | ground | 10 | 5 | `1FEDB`×3 to output 35, then the `0xA4` pair |
| 5 | unconfigured | 15 | **0** | nothing |
| 6 | 12V | 15 | 5 | `1FEDB` out 4 OFF, out 3 ON ×2, then the `0xA6` pair |
| 7 | 12V | 20 | 5 | `1FEDB` out 3 OFF, out 4 ON ×2, then the `0xA7` pair |
| 8 | 12V | 25 | **0** | nothing |
| 9 | 12V | 25 | 3 | `1FEDB` to output 44, then the `0xA9` pair |
| 10 | 12V | 28 | **4** | **the `0xFB` pair AND the `0xAA` pair** |

**Pin 10 is the answer.** Its group is four rules: it broadcasts the engine-running instance
`0xFB` *and* its own input instance `0xAA`, and the two pairs are byte-identical apart from the
instance byte — so they fire on the same phases and **can never disagree**.

So the ELECTRICAL page's `Engine Running` means, precisely, **"12V is present on G12 input 10"**.
On a coach with no generator that is the ignition/run signal, which is why Firefly calls the
display *Engine* Running while the diagnostics row calls the same input *Ignition*.

**Testable on the panel with no bus at all:** `Engine Running` and the `10 - Ignition(12V)` dot on
DEVICES/FAULTS are driven by one input through two instances, so they must always agree. If they
ever differ, this decode is wrong.

### Three things it settles that were previously softer

- **Finding 54's adjacency grouping is now proven**, not inferred. Every pin's run ends with its
  own indicator pair, and the dispatch table states the run explicitly.
- **Pins 5 and 8 are structurally silent** — `rule_count == 0`, not merely "no record was found".
- **Byte 3 is a gesture PHASE mask in table `0x20`**, not a substitution mask. `1` press, `2`
  tap-release, `4` held, `8` release-after-hold — the same encoding as a panel binding's byte
  `+0x03` (`CONTROL-BEHAVIOR.md`). The evidence is that it makes pin 1, a light switch, read as
  the standard light gesture: `02` → command 5 (toggle), `04` → command 21 (ramp), `08` → command
  4 (stop). The indicator pairs are then `05` (press | held) → report ON and `0A` (tap-release |
  release-after-hold) → report OFF. `inferred`, but it is the reading under which every record in
  the table makes sense.

  *This one was a live near-miss.* `CommandRule.selectors` applied table `0x62`'s substitution-mask
  reading to every table, and so printed `mask 05 … p[0]<-0xfb` for a record whose payload[0] is a
  plain literal instance — a confident, wrong claim, of exactly the kind this project's
  `Known traps` section exists for. `selectors` is now gated to `SUBSTITUTION_TABLES` and returns
  nothing elsewhere; `phase_mask` returns nothing outside `0x20`.

### An independent cross-check nothing in the derivation used

`+3`'s sense bit agrees with the **screen captions** on all six pins whose caption carries a
suffix: pins 1 and 3 are `0x00` and read `(GND)`; pins 6, 7, 9 and 10 are `0x40` and read `(12V)`.
Those captions are authored text in GuiData; the config byte is in the G12 section. Two images,
no shared source, six for six.

### Build generality

Table `0x10` is 80 bytes — ten entries — in all three corpus builds. 11.23 has 25 table-`0x20`
rules, 14.7 has **none** (a Terrain with no configured inputs), and every dispatch entry in all
three stays inside its table's bounds. `src/fireflymcf/g12rules.py`: `input_dispatch`,
`input_group_rules`, `instance_source_pin`, `CommandRule.phase_mask`; `cli g12-inputs`.

### What is still unknown

Where the G12 *code* consumes table `0x10` — the blob at table `0x67` is still mostly unread —
and therefore whether "press/held" for a level-sensing input means edge or level. Nothing here
depends on that: the dispatch table's `(first, count)` is a data fact, and the phase reading is
flagged `inferred`.

---

## Finding 56 — `Awning Locked by Ignition`: traced end to end, and it is display-only *and* stale

`proven-from-code` + `observed-on-hardware`, 2026-08-24. Asked after **PC.25** refuted the
`1FFB7` interlock chain and left this label with no known source. It has one, and it is the
signal PC.24 had just proved live — but the trace also shows the panel does not enforce
anything, and that the label goes stale exactly like Finding 55's `Engine Running`.

### The chain

**1. The label that actually draws is `'Locked by Ign'`, not the long form.** Object **181** is
the `Awning` control block, and its record 1 is a type-6 value-match switch:

```
obj 181 rec 0: wtype 11                      label 'Awning'
obj 181 rec 1: wtype  6  value_slot=132  child_base=220  count=2
                   match 0 -> obj 220 = 'EXT'
                   match 1 -> obj 221 = 'Locked by Ign'      <== 
obj 181 rec 2: wtype 12                      (the touch region)
obj 181 rec 3: wtype  5  value_slot=133  child=272
obj 181 rec 4: wtype 12
```

The two long-form strings — obj **182** `'Awning Locked by Ignition'` and obj **701**
`'AWNING EXTEND LOCKED BY IGNITION'` — have **no parent anywhere in the object scan**. They are
unattached template objects (the Finding 15 class), so the string quoted in earlier notes is not
the one on screen.

**2. Slot 132 is G12 input pin 10, the ignition sense.** `layout.variable_register(data,132)`
→ SRAM **`0x2003A4E5`**, type 0 (u8) — byte-identical to what `cli g12-inputs` reports for pin 10
(var **201**, slot 132), derived independently.

**3. The register is written from the receive filter**, by the pin-10 arm of `FUN_00247BF8`'s
input mirror run (Finding 54, `0x24b382`-`0x24b420`):

```
0x0024B41E  ldr  r2,[0x0024b56c]   ; = 0x2003A4E5
0x0024B420  strb r3,[r2,#0x0]
```

**4. On the wire it is confirmed.** `BUS-BASELINE.md` **PC.24**: `19FBDA9C#AA01`/`AA00` tracks the
key exactly, across a 28.3 s ignition-on window.

So the display **is** correctly sourced — the hypothesis PC.25 left open is confirmed, and the
awning lock indicator reads the ignition through the input pin, not through `1FFB7`.

### Two things the trace settles that the label's existence does not

**It is DISPLAY-ONLY. The panel does not disable the button — CONFIRMED ON HARDWARE**
(`BUS-BASELINE.md` **PC.27**). Both switch arms are structurally identical — a `wtype 33` and a
`wtype 11` at the same 70×45 geometry — so the switch swaps *text* and nothing else. The **touch
region is object 181's own record 2, outside the switch**, present in both states. Group 3 is
untouched by the switch.

That was an inference from record structure when written, and the capture bears it out
frame-for-frame: with the channel locked, the panel still sends `cmd 3 ch 4` then `cmd 1 ch 3`
repeating at exactly **1.000 s**, and `cmd 3 ch 3` on release. **The `gestures` decode of group 3
is now confirmed on a control nobody had ever captured.**

Nor do the G12's rule tables lock it: pin 10 fires exactly **four** rules and all four are
`0x1FBDA` broadcasts (its own `0xAA` pair and the `0xFB` pair), none driving an output. If the
awning really is inhibited with the key on, the enforcement is in G12 table **`0x67`** — the
6,848-byte blob still mostly unread — and this project has not found it. **Do not write up the
interlock as located.**

### The interlock: FOUND, and the argument that led here was wrong

**`observed-on-hardware` 2026-08-24, PC.27.** It is not in table `0x67` and it is not proprietary.
The **G12 broadcasts RV-C `DC_DIMMER_COMMAND_2` command 33 (Lock)** on channel 3, 8 ms after
ignition-on and after first forcing the channel off; command 34 (Unlock) follows ignition-off by
2 ms. `DC_DIMMER_STATUS_3` byte 3 carries the lock flag (`0xFC`/`0xFD`) and byte 5 the last
command — and while locked the G12 **records the panel's ON command in byte 5 and holds brightness
at 0**, so it declines rather than ignores.

*A note on where it was hiding:* nowhere. It was on the wire the whole time, in a DGN this project
decoded long ago, and it had simply never been captured with the key turning. **The rule tables
were the wrong place to look, and "not in the rule tables" was never evidence it was in `0x67`.**

**RETRACTED — the keypad design argument.** This section had argued: EXT/RET appear on HOME *and*
on the wireless keypad; the keypad is a separate RV-C node commanding the G12 directly; therefore a
panel-side inhibit would be bypassed and enforcement must be G12-side. **The premise is false.**
In 7,003 frames only `19FEDB9C` (G12) and `19FEDB9F` (panel) ever send `1FEDB`, and the keypad
presses came out as `19FEDB9F` — **from the panel**. The keypad has no source address; it is a
wireless input the panel relays, battery status included (`19FFFD9F#81FF39…`). The conclusion
happened to be right, reached from a false premise. *Do not reason from a device's independence
without confirming it has a source address.*

### RETRACTED, same day, on hardware — it does NOT go stale

This finding claimed: *"`0x2003A4E5` has one pool literal (`0x0024B56C`) and one reference, which
is the store above — nothing in GuiCode reads it, so its only consumer is the layout walk at
compose time"*, and predicted the button would not relabel until you left HOME and came back.

**The owner turned the key and the label changed immediately** (`BUS-BASELINE.md` **PC.26**). The
switch is evaluated live.

**The error is in the method, and it is worth more than the finding was.** A value-slot register is
*never* read through its own pool literal. Finding 17 gives the read path:

    state+0x24 = DAT_00253D04[slot]      ; DAT_00253D04 = 0x90044BC8, the GuiData pointer table
    state+0x91 = DAT_00253D08[slot]

GuiCode loads the **table base** and indexes it by slot. The register's address never appears as an
immediate, so a literal scan **cannot** find the read — not for this slot, not for any slot, no
matter how often the read happens. The scan returned exactly what it must return for a register
that is written by name and read by index, and that result was treated as evidence of absence.

*The rule, and this project has now paid for it three times:* **an argument from absence is only
as good as the guarantee that what you searched for would have been present.** `find_reg_write.py`
finds pool-literal references. Value-slot reads are table-indexed. The two never intersect.

**This breaks Finding 55's argument too** — it makes the identical step about `0x2003A50C`
("six references, all six stores ... so nothing in GuiCode reads it"). Finding 55's *conclusion*
survives only because it was **owner-observed**: `Engine Running` really does go stale. Its
*explanation* is withdrawn, and the staleness is now an unexplained observation. See PC.26 for the
sharpened puzzle — slot 132 and slot 169 come from the same DGN and the same input pin, are both
type-6 switches, and behave oppositely.

**Var 201 is genuinely not bridged** (the 44 bridged vars are 18-40, 42, 43, 66-83, 85). That part
stands. What it does not license is the conclusion that nothing repaints — the bridge blocks are
how a *colour cell* is driven, not the only way a screen can be re-walked.

*(The prediction that stood here — that the button would not relabel until you left HOME and
returned — was tested and refuted; see the retraction above. Object 181 is reachable from **screen 0
(object 4), HOME, and no other screen**, which the owner confirmed: this coach has no awning
screen. That part held.)*

Finding 55's closing rule was going to be generalised here on the strength of a second instance.
**There is no second instance.** The rule is therefore back to what it always was — a statement
about one label, `Engine Running`, whose mechanism is now also withdrawn:

> A type-6 switch is a **configuration** mechanism. Firefly uses it for the floorplan, the options,
> the derived battery block and nav rail — values that are set once and then hold. Every time it
> has been used for a value that changes at *runtime* (`Engine Running`, `Locked by Ign`) the
> result is a display that is correct only at compose time.

Both defects share a root that is worth stating once: the signal arrives, the register updates,
and the screen does not — because **selection happens during the walk and repaint is driven by a
different mechanism entirely.** Anything that must track a live value needs a value widget or a
bridged colour cell, and the bridged set is fixed by hard-coded addresses.


## Finding 57 — `Low Volts` writes one selector and displays another — `proven-from-code`, 2026-08-25

Raised by the owner, reading `G12-CODE.md`'s arming block: *is the `Low Volts` control on
`AUTO GEN START SETTINGS` (screen 28, object 31) the thing that sets `0x2F` to 1?* **Yes on the
write side, and the read side is a different selector.**

### The control

| piece | where |
|---|---|
| the button | object 31 record 12, a `wtype 5` container with `value_slot 45`, `child_index` **285** = `'Low\nVolts'` |
| its touch region | object 31 record 13, `wtype 12`, touch id **129** |
| what it emits | group 129: variant 0 → `0x2F := 0`, variant 1 → `0x2F := 1` (bindings 447/448, each with a read-back) |

Object 795 is the same page's twin and carries the same three records.

### The variant is chosen by GuiCode, not by the press

Group 129's raw is `11 80 0c 80 bf 01 04 00` — byte 1 `0x80`, the **externally-selected** design
switch of `CONTROL-BEHAVIOR.md` §5a. So a tap does not cycle the variant; something outside does.
That something is `FUN_00247BF8`:

```asm
0024a176  ldrsh.w r3,[r4,#0x54]     ; config[0x2A]
0024a17a  cmp r3,#1 / ite ne / movne r3,#0 / moveq r3,#1
0024a184  strb r3,[0x20039a79]      ; flag := (config[0x2A] == 1)
0024a186  cmp r3,#0 / bne.w 0024a4e8
0024a190  movs r1,#1 / movs r0,#0x81 / blx [0x20039908]   ; setVariant(129, 1)
   0024a4e8  movs r1,#0 / movs r0,#0x81 / blx …            ; setVariant(129, 0)
0024a196  ldrb r3,[0x20039a79] / cmp r3,#1 / beq 0024a4f4
0024a1a4  str r2,[0x2003a814]       ; slot 45 := colour master, [0x20039a58] or [0x2003a1a0]
```

**Inverted, which is correct for a toggle:** state 1 → variant 0, so the next tap writes 0; state
0 → variant 1, so the next tap writes 1. One flag drives both the variant and the colour, and
`0x2003a814` (slot 45) has **exactly one writer** in the whole section.

*`[0x20039908]` is the group-variant setter*, and that is not assumed: 41 of 41 distinct ids passed
to it in `movs r0,#N` are group indices whose byte 1 is `0x80`, out of 96 such groups in 233. The
LCD half of the same mechanism is `CONTROL-BEHAVIOR.md` §5a's single setter caller. Group **199** —
defect 0b's silent `Shore Brkr Size` `−` — is in this called set, which independently supports that
defect's "externally selected, and nothing moved it" reading.

### And `0x2A` has never been on this bus

**Zero frames in 28 captures**, against 2,022 for `0x2F`. `0x2D`, `0x30` and `0x32` are equally
absent while `0x2B`/`0x2C`/`0x2E`/`0x2F`/`0x31` are all present ~2,000 times — so this is a gap in
what the G12 publishes, not a gap in the captures.

So the button **writes `0x2F` and reads `0x2A`**, and the panel never learns `0x2A`.

### The falsifiable prediction

Press `Low Volts` once and exactly this should happen:

1. `19FED99F#FF962F0F0100D1EA` on the wire — `0x2F` becomes **1**;
2. the button's appearance **does not change**, because its colour is fed by `0x2A`;
3. a second press writes `1` again, never 0 — the variant cannot move until `0x2A` does, so
   clearing it needs the bus (`19FED9C0#FF962F0F0000D1EA`).

Each outcome is worth a different thing. `0x2F` reaching 1 tests `G12-CODE.md`'s arming
hypothesis for defect 0c. The button lighting up refutes the `0x2A` read above. Nothing
transmitting puts it in defect 0b's class.

*The limit on this, stated because it is the one that would overturn it:* `0x2A`'s absence is
established from the `0x15FCE` sweep and from every `1FED9` write. It is **not** established that
the panel's `r4` config buffer is filled only from those — it is a >0x2C0-byte assembled image and
this work has not traced what fills it. If another payload feeds it, `0x2A` could be live and the
button would behave normally. One press settles it.

### RETRACTED the next day — the button alternates, so the display source is not `0x2A` (PC.39)

The owner pressed it three times and the panel emitted `0x2F :=` **`1, 0, 1`** — a working toggle,
not the stuck `1, 1` this predicted. `0x2A` is still absent from that capture (zero frames against
1,427 sweep frames from the G12), so **the flag at `[r4,#0x54]` is not selector `0x2A`** and the
`offset = 2 x selector` mapping does not hold for this block. Everything else in this finding
stands and is confirmed by the same capture: the control is object 31's `Low Volts`, it drives
group 129, group 129 writes `0x2F`, the variant is externally selected, and the inversion is real —
**the alternation is the proof of the inversion**, since a non-inverted variant could only ever
re-assert the value already shown.

**What actually repaints it: the control's own read-back.** Each tap emits the write and then a
`D3` read of the same selector 0.5 ms later, and the G12 answers in about a millisecond:

```
306.979451  19FED99F#FF962F0F0100D1EA    tap: 0x2F := 1
306.980012  19FED99F#FF962F0FFFFFD3FF    read-back request
306.981372  195FCE9C#2F00010000000000    the G12 replies, +1.9 ms
```

All three taps did this (+1.9, +0.7, +0.9 ms), and the owner reports the colour changed on every
one and reverted on the middle one. So the flag at `[r4,#0x54]` is carrying **`0x2F`'s own
value**, `0x2A` is not in this picture at all, and the second binding in each variant — the
read-back this project had been treating as decoration — is what closes the loop.

*The mapping error, and the rule it earns:* the `offset = 2 x selector` convention was carried in
from anchors verified far away in the same function (`[r4,#0x2]` = `0x01`, `[r4,#0x1de]` = `0xEF`)
and applied to this block without re-establishing the base. **An offset is only as good as the base
it is measured from, and a base verified 6 KB earlier in a 10 KB function is not verified here.**
The open work is to find where `r4` is reloaded between `FUN_00247BF8`'s entry and `0x0024A176`.
One hint worth starting from, not yet a claim: under a base where `[r4,#0x54]` is `0x2F`,
`[r4,#0x58]` is `0x31`, and the panel's `(x+1) >> 1` there turns the owner's start-at of 1058 into
**529** — which a one-decimal value widget draws as `52.9`, the number that was on his screen.


## Finding 58 — how the coach is reset: three targeted buttons, one real factory reset — 2026-08-25

Asked by the owner after PC.39: *"I have likely changed lots of things over time experimenting —
is it CLEAR EEPROM, flashing the G12, or a ship-mode power cycle?"* **CLEAR EEPROM, and it is the
only one of the three that does anything.**

### The three targeted reset buttons, and what each covers

Each is a straight-line run of absolute `D1 EA` sets out of GuiCode's template pool — the same
shape as the option handlers (Finding 23).

| button | action id → handler | writes |
|---|---|---|
| `Reset Defaults` (obj 146) | `0xBB9` → `0x0024E500` | **8**: `0x2B` 5632, `0x2C` 2048, `0x31` **1020**, `0x0C` 60, `0x0D` 1060, `0x0E` 1800, `0x0F` 1800, `0x16` 115 |
| `Set Inverter/Charger Defaults` (obj 992) | `0x83F` → `0x0024C8F0`, pool `+0x338` = `0x00259598` | **9**: `0x82` 214, `0x86` 80, `0x99` 0, `0x8E` 90, `0x92` 200, `0x88` 12, `0xB0` 290, `0xB2` 274, `0xB4` 292 |
| `Reset Default Thresholds` (obj 28, screen 25) | — | **3**: `0xCC` 54000, `0xCD` 40000, `0xCE` 25000 |

The first is Finding 43's run, hardware-confirmed as PC.36. The second matches Finding 51's seven
writable controls value for value. `Set AGS Defaults` (id `0x83E`) sits beside the second on the
same `Other Options` page and **its dispatch site was not located** — neither a `movw` nor a
`cmp.w` reaches that constant, so it is presumably built by subtraction inside the dispatch tree.
Recorded as unresolved rather than assumed to share `0x0024E500`.

**None of the three writes `0x2F`.** No `D1 EA` run anywhere in the pool does. So the flag PC.39
showed to be the AES arming gate cannot be restored by any reset button on the panel.

### CLEAR EEPROM is one frame, and its function byte is unique in the file

Object **22, screen 19**; the button holds for 3 s (`0x0024B690` arms `+0xBB8` into `0x200398C0`,
`0x0024EC18` fires on expiry), sends one template and navigates to screen 11:

```
19FED99F#FF96ABFF285DDEFF     selector 0xAB, value 0x5D28 = 23848, function 0xDE
```

`0xDE` appears on **no other template and no other binding** in any of the three builds — a survey
of every `0x1FED9` template and binding turns up only this one, plus an unreachable `0xCF` binding
(group 171, carried by no object — the panel-side twin of G12 rule record 59, which is why that
record has never fired). A magic constant behind a unique function byte is an erase key, and
nothing in the `0x67` blob compares against `0xDE` or holds `0x5D28`, so the **G12 application**
handles it.

### The firmware's own caption confirms the derivation, including its gap

Object 22 carries Firefly's text:

> *"Clearing EEPROM can help fix some unexpected operations that can arise, especially after
> updating the CLC (G12) from a USB upload."*
> *"Clearing EEPROM should only be done with the consultation of Firefly Support. **After clearing
> EEPROM, you may need to set the floorplan and options back to previous settings.** the 12V system
> in the coach should be power cycled after clearing EEPROM."*

That second sentence is exactly what `g12code.config_defaults_by_selector` predicts from the blob
and nothing else: the `0xFFFF`-sentinel routine covers **99 selectors, `0x00`-`0x9C`**, and the
floorplan (`0xF5`) and the option selectors (`0xE3`-`0xEF`, `0xF7`) have **no default at all**. So
after an erase everything below `0x9C` returns to factory and the floorplan/options come back
unset. Two independent sources, the same gap, neither derived from the other.

**And `0x2F`'s sentinel default is 1** — so CLEAR EEPROM restores the AES arming flag, which no
reset button does.

### The other two candidates do nothing, and one of them is already disproved on hardware

**Flashing does not reset the config.** The owner flashed stock mid-session on 2026-08-24, between
PC.36 and PC.37. Across that flash `0x2F` stayed **0**, `0xF5` stayed **8** (WD) and `0x0C` stayed
**60**. The G12 section of the MCF carries code and tables; the config cells live in NVM the
container never writes, which is the whole reason the sentinel routine exists.

**A power cycle does not reset it either.** The sentinel only writes a cell that reads `0xFFFF`,
and 28 captures over twelve days across many power cycles show every value persisting. Note the
caption asks for a power cycle **after** clearing, not as a way of clearing.

*(`0xF5` reads 0 for the first ~15 s of a panel boot. That is the known boot artefact, not a
reset — see `test_selector_f5_is_the_floorplan_code`.)*

### What this does not establish

The `0xDE` handler is in the G12 application at `0x08006000`, so *"CLEAR EEPROM erases the config
EEPROM"* rests on the unique function byte, the magic constant, Firefly's caption and the
sentinel-coverage match — not on a read of the code that acts on it. Whether it also touches
anything outside the config cell array is unknown. It is **not** expected to affect the wireless
keypad pairing, which lives in *panel* NVM while this frame is addressed to the G12, but that is an
inference from where the frame goes, not an observation.

## Finding 59 — AES "Start at"/"Stop at" defaults are decoded from the config payload, not held in the file — `proven-from-code`, 2026-08-25

Closes step (a) of the owner's AES "Start At" question (CLAUDE.md, "AES 'Start At' default
voltage"). The question was whether the pre-user default for the ELECTRICAL AGS **Start at**
setpoint (value slot 272, SRAM `0x20039D52`) comes from a **received CAN config payload** — the
`FUN_00247BF8` path Finding 19 traced — or from a **file-resident constant** an MCF patch could
change. It is the config-payload path. An MCF patch cannot set the default.

**The trace.** `find_cell_writer.py 0x20039D52` reports exactly **one** writer, and it is inside
`FUN_00247BF8`:

```
0024a00a  ldrsh.w r3,[r4,#0x58]      ; r3 = signed halfword field of the r4 struct
0024a00e  cmp.w   r3,#0xffffffff     ; raw == -1  -> "unset"
0024a012  beq.w   0x0024a3a0         ;   ...skip the write, leave the cell as it was
0024a016  adds    r3,#0x1            ; r3 = (raw + 1)
0024a01c  ubfx    r3,r3,#0x1,#0x10   ; r3 = (raw + 1) >> 1   (unit rescale)
0024a020  ldr     r2,[0x0024a248]    ; literal 0x0024a248 = 0x20039D52   (Start-at)
0024a022  strh    r3,[r2,#0x0]       ; *0x20039D52 = decoded value
```

Stop-at is the same shape four instructions on: `[r4,#0x5c]` → `(raw+1)>>1` → literal
`0x0024a250` = `0x2003A22C` (slot 274). Both literals were read out of `GuiCode.bin` and match the
slot map in the "Voltage-widget slots" note above.

**Why this is a payload decode, not a constant load — three legs:**

1. **The value is a struct field, sentinel-guarded.** `[r4,#0x58]` is a signed halfword pulled
   from a packed structure, with `-1` meaning "field absent — do not write." That is how you decode
   an optional field of a *message*, not how you seed a constant. The surrounding block fans dozens
   of `ldrsh.w r3,[r4,#off]` fields (`0x58`, `0x5a`, `0x5c`, `0x5e`, `0x154`, `0x17e`, `0x282`, …)
   out to individual `0x2003xxxx` SRAM cells — the canonical "unpack a received config frame into
   runtime cells" pattern.
2. **`r4` is an argument, not a baked address.** The prologue is `mov r4,r0` (`0x00247bfc`); the
   source buffer is *passed in*. No `0x90xxxxxx` (GuiData) or code constant is the source.
3. **The buffer is RAM, not the read-only file image.** `FUN_00247BF8`'s only caller
   (`0x0024EE28`) passes its own `r0` straight through, gated on a "config present" flag byte
   (`0x20030190+1`), and the dispatch above it (`GuiCode_entry1`) **writes into** the same struct
   (`strb.w r3,[r4,#0x21f]` at `0x0024EDDA`). A structure that GuiCode writes to cannot be the
   QUADSPI-mapped GuiData at `0x90000000`; it is the assembled-in-RAM config image Finding 19
   describes. The struct's config header is read at `[r0,#0x210]`/`[r0,#0x214]` — the same header
   fields the prologue unpacks (`[r4,#0x214]` → floorplan/option bytes).

**Consequence for the owner's question.** Start-at and Stop-at are written **once, from the
assembled config payload** — the Finding 19 mechanism, confirmed for these two slots specifically.
~~There is no MCF byte holding the default, so a GuiData/GuiCode patch cannot change it.~~
**[CORRECTED by Finding 60, 2026-08-25: this over-generalised. It is true for *this* slot — the
panel's read-only receive/display path — but the panel has a SECOND coupling to the setpoint, a
*transmit* template in GuiCode that pushes the default to the G12, and THAT is a patchable MCF byte.
This slot cannot change it; `0x002599D8` can.]** So the *display* slot's decode path cannot change
the default. This routes
the question to step (b): the pre-user value is bus-delivered, and setting it is a bus write (see
`BUS-BASELINE.md` PC.11's signed-delta threshold protocol), **or** — step (c) — a question about
where the G12 seeds its NVM default, which is a G12-section dig, not a panel-image edit. The panel
side is now closed: it only *decodes and displays* the setpoint; it does not *originate* it.

*(Artifacts: `extractions/aes_startat_writer.txt`, `aes_startat_listing.txt`, `aes_prologue.txt`,
`aes_callers.txt`. Regenerate with `find_cell_writer.py 0x20039D52` and `dump_range.py` on the
`guicode136` project.)*

## Finding 60 — the AES "Start At" default IS in the MCF: a GuiCode CAN-frame template, build-specific and patchable — `proven-from-data`, 2026-08-25

> **Qualified 2026-09-03 — `BUS-BASELINE.md` PC.51.** The template below is the source for the
> **panel-driven** defaults burst (the `Reset to Defaults` button's eight `D1 EA` sets, PC.36) and
> that stands. It is **not** the origin of the running 1020: on a panel mode change the panel sends
> only `EF`, `D7`, `0x15` and `F7`, and the G12 restores the whole AGS/AES parameter profile from
> its own NVM (AGS `0x31` = 244, AES `0x31` = 1020) with no `0x31` frame on the bus at all.
> Patching this template still changes what the panel pushes; it does not change what a mode
> switch restores.

**Corrects Finding 59's over-broad closing claim and the G12-CODE.md step-(c) headline.** The owner
pushed back on the conclusion that the AES Start-At default was not a patchable MCF constant, with a
decisive argument: **AES did not exist in 11.x and was added in 13.x, so its defaults had to ship
with the 13.x firmware — and the firmware update writes the MCF.** That is exactly right, and the
default was hiding in the section this dig had set aside.

**Where it is.** GuiCode carries a pool of pre-built 12-byte `0x1FED9` CAN-frame write templates
(Finding 7, 13.6 pool base `0x00259430`). One of them is the AES Start-At default:

```
file 0x0199D0 (abs 0x002599D0):  00 d9 fe 19  ff 96 31 0f  fc 03  d1 ea
                                 └ 0x1FED9 ┘  └ FF 96 = group; 31 = selector; 0F ┘  └val┘ └fn┘
                                 value field at file 0x0199D8 (abs 0x002599D8) = fc 03 = 1020 = 51.0 V
```

**How it reaches the G12.** The panel transmits this template verbatim (Finding 7's send primitive
copies the 12 bytes) as an **absolute `D1 EA` set**, `19FED99F#FF96310FFC03D1EA`, inside the
config-defaults burst the panel emits on floorplan/mode establishment and Reset to Defaults. It is
**observed on the wire in five captures** — `pc3-electrical`, `pc8-lighting`, `pc36-service-mode`,
and `pc37` (×2) — each time byte-identical to the template, each followed by a `D3 FF` read-back and
a `195FCE9C#31` sweep confirming the G12 stored it. In `pc3` it sits in a contiguous run with
`2B`, `2C`, `31:=1020`, `0C:=60` — the AGS/AES defaults being pushed together. (In `pc39` the
`0x31` 244→1020 flip carried no frame because the G12 had already stored 1020 from an earlier push
and recalled it on the mode toggle — the recall is internal, but the **value's origin is this
template**.)

**Why it must be in the MCF — the build comparison settles it.** The `0x31` set-value template is
build-specific:

| build | `0x31` set template | meaning |
|---|---|---|
| **11.23** | **none** (only a `D3 FF` read-back) | pre-AES: no Start-At default pushed at all |
| **13.6** | `0x31 := 1020` (`D1 EA`) | 48 V AES default, 51.0 V |
| **14.7 (Terrain)** | `0x31 := 244` (`D1 EA`) | 12 V default, 12.20 V |

A value that is absent in 11.23, 1020 in 13.6 and 244 in 14.7 is a per-build firmware constant, and
per-build firmware constants live in the MCF. This is the direct evidence Finding 59 could not see
from the *receive/decode* path alone: the panel has **two** independent couplings to this setpoint —
a receive path (`FUN_00247BF8` → display slot `0x20039D52`, Finding 59, read-only) **and** a
transmit path (this template → the G12's `0x31` cell, read-write, the default's actual origin).

**The patch.** A same-size 2-byte edit of the value field at abs `0x002599D8` changes the default the
panel establishes on the G12. Target = volts / 0.05, little-endian; must stay inside the G12 clamp
**[1000, 1080] = [50.0, 54.0 V]** (`g12code.AGS_LIMITS`) and honour `stop_at >= start_at + 8`. E.g.
52.0 V → 1040 → `10 04`. This is a GuiCode edit, so it flashes as the GuiCode+GuiData pair, and it
takes effect the next time the panel pushes defaults (Reset to Defaults, or a floorplan re-select).
**High-confidence but not yet flashed** — the template→wire copy is proven byte-exact (Finding 7),
but this specific edit has not been built or flashed, so it carries the same "verify on the coach"
caveat as any first-of-kind change.

**Consequence for the thread.** Step (c) flips from "no patchable default" to **"yes — a 2-byte
GuiCode edit."** The owner now has two routes: patch the template for a permanent default change, or
use the step-(b) bus-write recipe for an ad-hoc change. The bus recipe's `D1 EA` = absolute-set
reading is also corroborated here: the panel uses `D1 EA` for value establishment and `D2 FF`/`D2 EA`
for delta adjustment, seen side by side across these captures.

### Finding 60 addendum — `0x2F` (`Low Volts` enable) is NOT MCF-baked, unlike `0x31` — `proven-from-data`, 2026-08-25

The auto-start gate `0x0B = 0x2F ? 0x31 : 0` has two operands, and they live in different places.
`0x31` (Start-At) is pushed from a GuiCode template as a config default (this finding). `0x2F`
(`Low Volts` enable) is **not** — it is a user setting in G12 NVM. Three pieces of evidence:

- **No GuiCode set-template.** All three builds carry only a `0x2F` *read-back* template
  (`ff 96 2f 0f ff ff d3 ff`; 11.23 file `0x0173D0`, 13.6 `0x019AA0`, 14.7 `0x01C3D0`). There is no
  `0x2F := <value>` frame in the pool, so nothing pushes a `0x2F` default in the config-defaults
  burst — the exact opposite of `0x31:=1020`, which does ride that burst.
- **On the wire it is only ever user-driven.** Value writes (`0`/`1`, `D1 EA`) appear in **one**
  capture, `pc39`, where the owner toggled Low Volts on/off/on by hand; every other capture shows
  only read-backs, and `0x2F` never appears inside a defaults burst.
- **The one MCF-resident piece is the G12 sentinel seed** (`g12code.config_defaults_by_selector`:
  selector `0x2F`, cell offset `0x5E` = **1**), which lives in the G12 section but only fires on
  **cleared NVM** and is overridden the instant the setting is toggled.

This is confirmed on the coach: the seed is 1 (enabled), yet Low Volts was **off** at the start of
`pc39` — the live NVM value had diverged from the seed. **So `0x2F` cannot usefully be "baked in"
by an MCF patch** (patching the seed to 1 does nothing for a coach whose NVM already holds 0). Enable
it once — the on-screen `Low Volts` toggle, or a bus write `19FED99F#FF962F0F0100D1EA` — and it
persists. The contrast is the useful takeaway: **a pushed config default (`0x31`) is MCF-patchable at
its GuiCode template; a user setting persisted in G12 NVM (`0x2F`) is not — set it once instead.**

---

## Finding 61 — the `E3`/`E4` channel binding and the `E5`/`EC` "visibility flag" are ONE mechanism: type-6 switches keyed on the option's own slot — `proven-from-data`, 2026-09-02

Closes the last two strict `xfail`s in `tests/test_option_bindings.py`. Both fields were
searched for as a **stored** thing — a flag beside the row, a selector→channel table — and every
negative was right (`MCF-FORMAT.md` §8.3): nothing is stored. An OPTIONS row's **value slot** is the
key of ordinary type-6 value-match switches (Finding 16) elsewhere in the tree; the arm for 1 draws
the control, the arm for 0 draws nothing. `src/fireflymcf/optiongates.py`; `find_option_bindings`
now fills `channel` and `hides_control` from this and reads the answer key nowhere.

**`channel` — two switches, not one rebound control.** Every per-floorplan LIGHTS body (objects
**940-949** under root 6, the floorplan switch on slot 5) carries a switch on slot **110**
(`Cargo / Bath Light`, `0xE3`) with base 369 count 2 → arm 1 = object **370** `'Bath'` (943/944:
base 371 → **372** `'Cargo'`), touch id **25** → group 25 = `[(0x11,25),(0x15,25),(0x11,25),(0x01,25)]`
→ channel **25**; and a switch on slot **111** (`Bath Light`, `0xE4`) base 373 → arm 1 = **374**
`'Bath'` (943: base 618 count 3 → **619**), touch id **26** → group 26 → channel **26**. The 0-arms
(369/371/373/618) are a single wt-0 record at x = −95/−14: nothing drawn. Objects 370/372/374/619
have **no other parent** (every wt-5 child index and wt-6 band over all 1422 objects walked), so
each is visible only through its option. The tap handlers' mutual exclusion (Finding 50, PC.33)
is what makes exactly one visible at a time, which is why PC.8 *looked like* one control being
rebound. Both off — reachable, PC.33 — draws neither.

*Independent GuiCode route, agreeing:* Finding 23's companion OFF. The Cargo select arm at
`0x0024CE0C` emits `E3:=1, E4:=0` plus a `0x1FEDB` command-3 template at `0x00259820` (instance
`0x1A` = 26); the Bath select arm at `0x0024CF2A` emits `E4:=1, E3:=0` plus `0x00259838`
(instance `0x19` = 25). `config.off_channels_by_selector` reads it; the test asserts the two routes
agree. The GuiData route ships because it reaches rows that emit no OFF and needs no disassembler.

**`hides_control`.** Bunk Accent (`0xE5`, slot 108): all ten LIGHTS pages, base 375 → 0xFFFF
default arm 375 (empty wt-0 at x = −95), arm 1 = **376** `'Bunk Accent'`, touch id 34. Group 34
commands **{34, 23}** (variant-2 entries on 23), so `channel` stays `None` — the set is on the
gate, the field is not guessed. Black Tank (`0xEC`, slot 112): the HOME tank clusters **912-921**,
base 801 → 801 is a `'.'` at x = −20, arm 1 = **802** `'BLACK'` + wt-5 gauge child 719 — a gauge,
not a control, so `hides_control` True, `channel` None; the same slot also gates diagnostic rows
1014-1037. "Empty" is structural (no wt-12 and no wt-5 child in the arm's subtree), not a match on
the `'.'` text.

**Not filtered to the answer key.** Bath Fan (`0xE9` → Climate 247, group 40 → channel 40 —
Finding 16's own worked example), Progressive Inverter (→ 1421) and the AGS/AES radio also come
out gate-shaped. **Heat Pump (`0xEB`) does not**: arms 872/873/874 all carry touch regions and
differ only in the mode buttons they add — the *reveal* PC.34 photographed. That is what keeps
the predicate from being a tautology, and `tests/test_option_gates.py` pins it.

**14.7** has the same shape on its own indices: LIGHTS pages 794-799, Cargo → 328/330 → 25,
Starlink Power (`0xE4` there) → 240 → **17**, Bunk Accent → 334 → group 34, Black Tank → 738; no
OFF companions (nothing to rebind), so the GuiCode route correctly returns nothing. **11.23** has
none of these rows.

**Negatives.** No GuiCode reader of the six option registers (`0x20039CF8`, `0x20039EBD`,
`0x20039E70`, `0x2003A4B4`, E9, EB — pool literals and `movw/movt`) touches a binding's instance
byte: the readers are the config-unpack writers (`0x247C44-0x247C92`), the per-tick compare block
in `FUN_00247BF8` (`0x249BE2-0x249C9C`), the tank-name whitelist (`0x2495B0-0x2496E0`, `0x24977A`)
and the tap handlers. *Scope:* a literal scan cannot see slot-indexed `DAT_00253D04[slot]` reads —
and the switch path *is* that reader, which is the mechanism found. **Group 93**, the §10 lead, is
referenced by no wt-12 record anywhere: dead. Objects 278 `'Black Tank Heater'` and 950 are
parentless templates.

Tests: `tests/test_option_gates.py` (9), the two former xfails now pass; mutation-checked
(`gated_channel→None` 4 fail, `hides_control→False` 2, `reveals→True` 2, `off_channels→{}` 1).
Suite: 1513 passed, 1 skipped, 0 xfailed.

---

## Finding 62 — table `0x61` is a cell ↔ bus status table, not a transmit template — `proven-from-data`, 2026-09-02

Finding 52 counted "151 records across tables `0x20`/`0x30`/`0x61`/`0x62`, every one a transmit
template". Validated on `0x62` (PC.23) and `0x20` (Finding 54); **wrong for `0x61`**. The full
layout, the kind-byte census and the wire evidence are `MCF-FORMAT.md` **§9a.3**; the short form:

- `+6` is a **config cell**, proven by one number seen through two tables: `0x62` #93 substitutes
  *selector* `0xC2` into `1FFB7` inst 3 byte 1; `0x61` #16 names *cell* `0xC2` at `1AAB7` byte 1;
  the wire carries the same `0x50`/`0x51` in both (657/681 nearest pairs agree).
- Six of its records are the G12's **1 Hz status broadcasts** (`1AAFD`, `1FFFF`, `0BFC1`, `1FF9C`,
  `1AADC` ×2, `1AAB7`), all observed byte-position-exact; seven are **receive-class** (`inferred`),
  including `1FBDA` inst `FB` → cell `0x28` = struct **`+0x50`**, the ignition cell the blob reads
  and nowhere writes (`G12-CODE.md`), and DM_RV → `0x53` = `+0xA6`, the watchdog's "is set" gate.
  14.7 feeds `0x28` from `1FFB8` with no input rules at all.
- **Kind byte**: in `0x62`, bit 7 ⇔ periodic (1 s / 5 s), clear ⇔ event-fired, and every value is
  now wire-attributed (`0x09` = the AES-start group, which is what the two G12-sourced `19FED99C`
  frames in pc39 are). Across tables the same value means different things — no global bit map.
- **Table `0x20` `+2` bit 2 = re-fire at 1 s while held**, set on exactly the held-phase records
  (PC.24's 1.000 s re-sends). `inferred`, redundant with the phase mask on shipped builds.

Shipped read-only as `g12rules.status_rules` / `kind_census` / `CommandRule.repeats_while_held`;
`tests/test_g12rules.py` (62, six new, mutation-checked). The blob cross-check stands: nothing in
`0x67` references a `0x61`/`0x62` record, so the four-record AES-start group is fired by an
app-side "fire rule group" service the blob calls.

---

## Finding 63 — the `Absorb`/`Float` rows on `INVERTER / CHARGER SETTINGS` are gated by a type-6 switch on value slot 23 — `proven-from-data`, 2026-09-03

Asked by the owner, who reported the two controls missing from the page on the coach and offered
to reflash an older image to get them back. **No reflash is needed and none would have helped**:
the rows are ordinary switch-gated content, the same mechanism as Finding 61's OPTIONS rows.

**The gate.** Object **17** is screen 14's root. Its records 3-10 are the wt-5 containers that draw
the status panel and the four `±` blocks, and its **record 11** is a wt-6 value-match switch:

```
obj 17 rec 11: wtype 6  value_slot=23  child_index=302  count=2   at y=360
                  match 0 -> obj 302 = a bare wt-33 box, 460 x 104, nothing drawn
                  match 1 -> obj 303 = BOTH controls
```

Object **303** carries the pair in one object: `'Absorb'` + a wt-8 readout on **value_slot 162** +
`'V'`, then `-`/`+` wt-11 labels each followed by a wt-12 touch region, then the same again for
`'Float'` on **value_slot 163**. So Absorb and Float are one object, and the page either draws both
or neither.

**Confirmed by render, not by argument.** `cli render --root 17` at the default config draws the
page *without* them, matching what the coach showed with the owner watching;
`cli render --root 17 --slot 23=1` draws them at the bottom, in the owner's photographed positions,
under the same divider. The photo and the byte-derived render agree.

**What this rules out.** The rows are not gated on `Battery Type`, and not on the panel adopting an
inverter: `BUS-BASELINE.md` **PC.53** shows adoption firing on `1FFD4` alone with the rows staying
away. Nor is `1FFC6` filter pinning involved — that is `Battery Type`'s feed (PC.46), a different
readout, and in any case the pinning ships in **v13**, which the reference coach's build log (not published) records as built and
not flashed.

### Two residuals, both bounded

**1. What writes slot 23 is not yet named.** Its register is **`0x20039999`** (type 0, u8) and the
searches so far are negative in a way that is informative:

- It is **not a receive slot**. The index-split rule puts var 23 at `0x20007FC8 + 23*2` =
  `0x20007FF6`; the register is nowhere near that, so no variable-table filter entry feeds it.
  (Receive-filter *entry* 23 is a `0x1FEDA` `DC_DIMMER_STATUS_3` row in a regular run with entries
  21-25 — a different table at the same index, and reading the two as one number would be the
  error this document warns about elsewhere.)
- It is **not in the config-unpack map** (`config.config_offsets`, 18 registers) and has no config
  register within 8 bytes.
- The only GuiCode pool references near it are to `0x20039997`, at `+0x0F9DE`, `+0x10362` and
  `+0x106C4`, and all three are **bulk init/copy runs** — long straight-line sequences of
  `ldr rN,[pc]; strb/strh/str` over many unrelated registers, or a shadow-copy loop. None is a
  feature gate.
- Its neighbours in the widget→RAM indirection table are slots **465** (`0x2003999C`) and **556**
  (`0x2003999E`), so it sits inside a packed block of single-byte widget states.

**The indirection table was followed, 2026-09-03, and the negative got stronger rather than
resolving.** `DAT_00253D04` is itself the pool literal holding **`0x90044BC8`**, so the widget-slot
read really is `*(u8*)table[slot]`, and entry 23 gives `0x20039999` with parallel byte `0x00`
(slots 162/163, the Absorb and Float readouts, carry parallel byte `0x03`, the voltage type).
Three searches, all negative:

- **The table is READ-ONLY in GuiCode.** All fourteen sites that index it
  (`0x2533C4`, `0x253486`, `0x253566`, `0x25365E`, `0x253A88`, `0x253AD8`, `0x253BF2`, `0x256878`,
  `0x257C5A`, `0x257D3C`, `0x257E40`, `0x257ED8`, `0x257FF8`, `0x25820C`) load the pointer and only
  ever **dereference it for read** — checked instruction by instruction over a 40-instruction
  window past each load. No store through a table-loaded pointer exists, so there is no generic
  "set slot" path.
- **Neither image writes the address.** A full-coverage disassembly with restarts — 40,759
  instructions of GuiCode, 24,440 of `LCD Firmware` — tracking pc-relative literals, `movw`/`movs`
  immediates, and all three addressing forms (`[rn]`, `[rn,#imm]`, `[rn,rm,lsl#n]`) finds **zero**
  accesses to `0x20039999`. The register's offset from GuiCode's RAM base is `0x97E9`, too large
  for an imm12, so the `movw` form was the one that had to be covered, and it was.
- **Its neighbours are barely written either.** Sixteen widget slots pack into
  `0x2003998D`-`0x200399C0` at irregular byte offsets (23, 85, 86, 177, 225, 234, 277, 297, 313,
  465, 511, 541, 544, 556, 622, 657). The only one any identified routine writes is slot **622**
  (`0x20039997`), set to 0 by the straight-line builder at `0x24F900`-`0x24FB00` — a run that
  writes 88 addresses over `0x20039910`-`0x2003A992` with values including `0xD1`, `0xEA`, `0x0F`
  and `0xFF`, i.e. Finding 7's config-frame constants alongside other globals.

**RESOLVED 2026-09-03 — slot 23 is a RECEIVE-fed variable, and the invisibility is the documented
trap, not a mystery.** The negative above is real and complete for *direct* addressing, and that is
exactly its signature. Counting all 664 widget slots whose pointer lands in GuiCode's RAM, **209
have no literal reference in GuiCode at all** — and that set contains **var 115** and **var 125**,
which `builds/golden-v22-anchor.ffproj.json`'s `pin115`/`pin125` ops independently identify as
`CHARGER_STATUS` (`0x1FFC7`) and `CHARGER_CONFIGURATION_STATUS` (`0x1FFC6`). Those are written by
the LCD firmware's receive path **by index off a table base**, the exact mechanism `CLAUDE.md`
records as invisible to a pool-literal scan. The unreferenced 209 are the receive-fed class, and
slot 23 is one of them.

**The index correspondence is confirmed by a control this dig did not choose.** Reading the
variable table at the same indices the recipe pins gives `var 115 -> 0x1FFC7` and
`var 125 -> 0x1FFC6`, matching the ops' declared `dgn` parameters exactly, and `var 260 -> 0x1FEA3`,
`var 261 -> 0x1FF99`. So receive-filter entry index **is** widget slot index here, and entry 23 is
slot 23's feed.

**Entry 23 is `da fe 1d 06 06 00 00 00`** — DGN **`0x1FEDA`** (`DC_DIMMER_STATUS_3`) with `+4` =
`0x06`, i.e. **not** the `0xFF` accept-everything sentinel: this filter is already pinned to a
masked payload compare, and `+5` = `0x00` selects payload byte 0, which on that DGN is the
instance. Read through `statusbind.pin_filter_instance`'s decode of the same fields, **slot 23 is
fed by `DC_DIMMER_STATUS_3` instance 6**. The extraction of `+3` = `0x06` is not pinned here and
the instance reading is `inferred-from-data`, so this is a **prediction, not a result**.

~~For contrast the two readouts take a different DGN: `var 162`/`var 163` are `0x1FECA` with an
open filter.~~ **Withdrawn 2026-09-03 — that read the receive-filter table at indices 162/163, and
the filter table and the widget-slot table are different index spaces** (the same conflation that
produced the refuted slot-23 prediction). The readouts' real derivation is below, and it is a
config value, not a bus status read.

### The readouts ARE explained, and they are a CONFIG value — `proven-from-code`, 2026-09-03

The owner's observation is what cracked it: `Absorb` and `Float` both display **25.5 V**, and 255
is `0xFF`, RV-C's "not available" sentinel being rendered literally. `slotsource` classifies both
slots `written`, with two writers each, and neither is a receive path:

```
config-unpack, 0x00247C30:  ldrb r1,[r3,#4]  ; config buffer byte +4
                            strh r1,[0x20039F30]      -> slot 162  Absorb
                            ldrb r1,[r3,#5]  ; config buffer byte +5
                            strh r1,[0x20039916]      -> slot 163  Float
per-tick,      0x0024A82C:  ldrsh.w r2,[r4,#0x19c]    ; sentinel-guarded halfword
                            cmp -1 / adds #1 / ubfx   ; skip if unset, else (x+1)>>1
                            strh r2,[0x20039F30]      -> slot 162
                            ldrsh.w r3,[r4,#0x19e] … asrs #1
                            strh r2,[0x20039916]      -> slot 163
```

So the values come from the **RAM config struct** — byte `+0x04`/`+0x05` on the unpack path and
sentinel-guarded halfwords `+0x19C`/`+0x19E` on the per-tick path, the Finding 59 idiom. `0xFF` in
those config bytes is what puts 25.5 V on the screen. **Tested and consistent:** injecting `0x1FECA`
with bytes 2/3 set moved nothing, as this derivation predicts and the earlier one did not.

**Tooling gap this exposes.** `config.config_offsets` returns **18** registers and does not contain
`+0x04`, `+0x05` or `+0x06` (→ `0x20039E9A`), which the instructions above plainly write. The
config-offset map is incomplete, and these three are named here with their cite. `config.option_selector`
returns `None` for all three, which is consistent: they are not OPTIONS rows, so no tap handler
names them and no `0x1FED9` selector for them can be recovered by that route.

### RESOLVED 2026-09-03 — nothing writes it, and that IS the answer (`BUS-BASELINE.md` PC.56)

The owner cold-booted the screen **~10 times and the rows appeared every time**, flashed v12 and
**they were missing**, then cycled power and **they returned**. A boot-path writer is refuted by
the flash case (the same boot path runs). Uninitialised SRAM fits all three: power-on state is
per-cell fabrication bias, hence deterministic per device, and a flash is the one event that
dirties that memory wholesale by buffering a ~1 MB image through it. So the ten exhausted avenues
below were not missing a mechanism — **they were describing one**, and the gate is a latent
firmware defect: a control block whose visibility is decided by memory nothing initialises.

### Avenues exhausted for the gate's writer — do not redo these

Recorded so a future session starts where this one stopped. All on 13.6 unless noted.

| avenue | result |
|---|---|
| pool literals, `[rn]`, `[rn,#imm]`, `[rn,rm,lsl#n]`, `movw` state offsets, both images | **0 hits**, against 4-7 for known-written registers |
| Ghidra reference database + operand scan (`find_addr_xrefs.py`) | **0**, against 3 and 6 for two controls |
| stores **through** the `+0x44BC8` pointer table (all 14 index sites, 40-instruction window) | none — the table is **read-only** |
| block copies: every `bl` whose `(r0 = dest, r2 = len)` spans the address, both images | **0** |
| GuiCode **exporting** a pointer into its own `0x20039xxx` RAM | 7 exports, none covering the byte |
| `cli.bin`, the loader — the one image never scanned | 0 refs (12 literals in that 4 KB page, none this address) |
| the config-unpack register set | not among them; all 18 are literal-addressed |
| receive path | writes the **separate** status-slot area; refuted on the coach (PC.54) |
| sibling builds | 11.23's own gate slot 32 is equally unreferenced; **14.7 orphaned the object entirely** (no parent of any kind), so no build offers a written gate |
| the reader, verified rather than assumed | `FUN_00251E38` type 0 = `*(byte*)ptr != 0`, so the target address was right all along |

**What is actually left**, in order of cost: a **second cold boot** discriminates the two live
theories for free — if the rows appear *every* time the power-on value is deterministic and
something outside the MCF writes it, while intermittent appearance means uninitialised RAM; then
**behavioural bisection** on the coach, since the byte demonstrably changes during a session and
nothing on the bus does it; and last the **panel's bootloader/ROM**, which is not in the MCF at all
and has no known access, the same wall as the G12 application image.

**The contrast that sharpens the gate's mystery.** On one page, in one object, the two *readouts*
have four literal writers between them and the *gate* has none. Whatever owns slot 23 is not what
owns 162 and 163.

*Also explained, and it is not ours:* `Bridge Firmware` moved `0.99` → `1.07` because the panel
parses that field out of **packet 5 of the `PRODUCT_IDENTIFICATION` BAM string**, requiring
`payload[1] == 'v'` (the arm tabulated above). The owner's bridge now answers the identification
request with a string carrying `v1.07`, where in the PC.52 capture it answered nothing at all.

**The prediction was tested on the coach and is REFUTED — `observed-on-hardware`, 2026-09-03.**
`DC_DIMMER_STATUS_3` instance 6 was injected from a spare source address for 50 s with payload byte
6 set, then 50 s with **every** payload byte set (covering any extraction index), with the G12
broadcasting nothing on that instance throughout. The rows never appeared. The receive-fed reading
is wrong, and the mechanism says why: **the receive path writes the separate status-slot area**
(`0x20007FC8 + idx*2`, the index-split rule), not the `dptr` address, and a received value reaches
a *widget* only when one of the 46 hard-coded GuiCode bridge blocks copies it across — a copy that
needs the destination as a literal. There is no literal, so no bridge block can paint slot 23, and
no receive filter edit can ever make it. `var 115`/`var 125` being both receive-fed and
literal-free was a **coincidence of two index spaces**, and reading it as one was the error.

**Confirmed independently in Ghidra** (`ghidra/find_addr_xrefs.py`, project `guicode136`):
`0x20039999` has **0** entries in the reference database and 0 operand hits, against **3** for the
neighbour `0x20039997` and **6** for the option register `0x20039CF8` — so the tool that resolves
pool islands and computed operands agrees with the hand-rolled scan, and the controls prove the
query works. The LCD firmware does not own that RAM either: it holds essentially no literals in
that 4 KB page (1 each in the pages either side) and writes nothing into `0x20039000`-`0x2003A000`.

Two more hypotheses closed on the way. It is not another page's content seen by mistake: screen 39
(object 38) is titled `'   PD Inverter '`, not `INVERTER / CHARGER SETTINGS`, and carries no such
rows. And it is not the config-unpack: `config.config_offsets`' eighteen registers are all
literal-addressed and `0x20039999` is not among them.

### The real question this exposed, and it is bigger than one row

**210 of the 664 widget slots whose pointer lands in GuiCode RAM are reached by no instruction
at all** (`slotsource.slot_sources`; a bare literal-presence count gives 209, one slot having a
pool word but no resolvable access),
and they are not a type class — the unreferenced set spans type bytes 0, 1, 3, 4, 6, 9 and 10 in
roughly the same proportions as the referenced set. So either there is a write mechanism this
project has not found, or a third of the panel's widget slots read RAM that nothing initialises.
Slot 23 is one instance of that, not a special case, and the class is the thing worth solving:
whatever explains the 210 explains this row and 209 others.

**Shipped as tooling, 2026-09-03 — `src/fireflymcf/slotsource.py`, `cli slot-source`.** The
classification is now queryable per slot instead of living in a commit message:
`config` (15 on 13.6 — a `config.config_offsets` register, written by the config-payload unpack),
`written` (439 — at least one resolved `str*`), `read-only` (0 on 13.6, kept because the
distinction is real), and `unreferenced` (210 — the open class). `cli slot-source <image>
--unexplained` lists them and `--slot N` reports one. `tests/test_slotsource.py` pins the counts,
the sibling build's own gate slot, and two anti-vacuity guards — a classifier whose disassembly
silently stopped would report *everything* unreferenced and look like a discovery, so the tests
fail if fewer than 400 slots are explained or if no write site resolves anywhere. Both guards were
mutation-checked.

*Practical note for the owner, since the original ask was to use the controls:* the gate is a single
wt-6 record. Making the rows permanent does not require finding the writer — it is a GuiData edit
to object 17 record 11, either forcing the switch's selection or replacing it with a wt-5 child of
object 303. That is the same shape as edits this project already ships, and it is testable in the
renderer before it is ever flashed. **Do not read the renderer's behaviour as
evidence about the panel here:** `render.py` treats an unset slot as 0, so its drawing the page
without the rows is a default, not an observation, and this is exactly the "an unset switch is not
a safe default" trap. The owner's photo proves the value can be 1 on real hardware.

**Build-general, which is a useful constraint on any answer.** The same shape appears at the same
record position in the sibling build: 11.23's page root is object **20**, and its **record 11** is
a wt-6 switch on **value_slot 32** over band 222-223, arm 1 being the object carrying both labels.
13.6 uses object 17, record 11, slot 23, band 302-303. Two builds, one design, different slot
numbers — so whatever sets it is a per-build slot allocation, not a constant address.

**2. The renderer omits the little arrow — CLOSED 2026-09-06, `Finding 71`.** The owner's photo
shows a small upward triangle sitting on the divider line above the two rows; `render.py` drew the
line and not the triangle. It is object 303's records **3-13**: eleven wtype-4 filled rectangles,
21×1 down to 1×1, each a row higher and two pixels narrower than the last, the first carrying
fillA index 161 and the other ten inheriting it. Nothing was wrong with the shape path — the box
loop carried an unexplained `2 <` minimum-size floor that dropped every 1 px record in the corpus.
The suspicion recorded here was right in its shape: a missed shape record is exactly the class of
thing that makes a preview disagree with the panel, and this one was hiding four other hairlines
as well.


### The 210 explained — the C runtime never initialises GuiCode's RAM at all — `proven-from-code`, 2026-09-03

Prompted by the owner's observation that *"some of their documentation does tell you to power
cycle after firmware updates so they may rely on the boot up memory behavior"*. Following that
into the code turns the uninitialised-SRAM reading from an inference drawn off boot behaviour
into a statement about the image, and the derivation is short.

**The LCD firmware's startup is decoded** (`src/fireflymcf/raminit.py`, `raminit.ram_init`).
The reset vector is `0x002104D0`; the handler is textbook CMSIS — one `.data` copy loop
(`0x2104D8`-`0x2104E8`), one `.bss` zero loop (`0x2104EE`-`0x2104F8`), then `bl 0x210524`
(`SystemInit` — it sets `SCB->CPACR` bits `0xF00000`, the FPU enable), `bl 0x21EEE4`, `bl
0x21D398`. The bounds are the six literals in the pool at `0x210508`-`0x21051C`:

| literal | value | meaning |
|---|---|---|
| `0x210508` | `0x20050000` | initial SP |
| `0x21050C` | `0x0021F39C` | `.data` load address in flash |
| `0x210510`/`0x210514` | `0x20000000`-`0x2000021C` | `.data`, copied |
| `0x210518`/`0x21051C` | `0x20000228`-`0x2000AD98` | `.bss`, zeroed |

**So the C runtime establishes 43 KB and leaves 276 KB.** Everything from `_ebss` at
`0x2000AD98` up to the stack at `0x20050000` is untouched by the startup — and that tail is
precisely where GuiCode's working area lives. On all three corpus builds **every** widget slot
whose pointer lands in GuiCode RAM is outside both initialised regions: 664/664 on 13.6, 561/561
on 11.23, 504/504 on 14.7. All three carry byte-identical startup bounds.

**The LCD firmware's only fixed-address reference into the tail is `_ebss` itself** — the bound in
this very pool. A literal scan of both images over `[0x2000AD98, 0x20050000)` returns exactly one
hit for `LCD Firmware` (that bound) against **654** for GuiCode. The region is GuiCode's, and
GuiCode establishes it one address at a time — including the straight-line builder at
`0x24F900`-`0x24FB00` that sets 88 of them.

**Which is what closes residual 1.** `0x20039999` is in the unmanaged tail, no instruction in
either image writes it (the earlier full-coverage scan), and it sits in a one-byte hole between
`0x20039997` and `0x2003999C`, both of which GuiCode *does* address. Nothing establishes a value
for it. On an STM32F7 such a cell survives a **warm** reset — a software reset or NRST, which is
how a firmware update ends — and takes the die's power-up bias on a **cold** start. That is the
owner's observation exactly: flash leaves the rows hidden, pulling power brings them back, and ~10
cold boots reproduce the same state because the bias pattern is stable per device. It also makes
the vendor's power-cycle-after-update instruction a real requirement rather than boilerplate.

**Two honest caveats, neither of which weakens the conclusion.** The **stack** is in the tail too,
at its top, reached through `sp` rather than any literal — GuiCode's highest referenced address is
`0x2003B12A`, leaving ~84 KB of headroom, so there is no collision. And newlib grows a **heap**
upward from `_ebss`, so whether GuiCode's cells are statically linked or allocated is not settled
here; it does not need to be, because `malloc` does not zero its blocks (only `calloc` does), so a
cell no instruction writes has no value established on either layout.

**Scope — this is a class result, not one row.** The `unreferenced` class is now the
*never-established* class: `slotsource.never_established` intersects the two facts and returns
210 slots on 13.6. The receive-fed alternative is independently excluded by the same derivation:
the receive status-slot area (`0x20007FC8`, `0x20008000`) *is* inside `.bss`, which is why
received values are deterministic at boot while slot 23 is not. `tests/test_raminit.py` pins the
bounds, the cross-build identity, the discrimination (a predicate answering True for everything
would pass a weaker test), and the refusal path; the deriver raises rather than reporting bounds
it has not identified, and the mis-assignment mutation is caught by its adjacency guard.

**Still not a RAM read.** The falsifiable prediction from `BUS-BASELINE.md` PC.56 stands unchanged
and is now sharper: the behaviour should be **per-device**, so a second panel booting cold
*without* the rows would confirm this reading rather than contradict it.

---

## Finding 64 — the boot-lottery audit: which on-screen content rides on uninitialised SRAM — `proven-from-data`, 2026-09-03

"The 210 explained" established that 210 widget slots are written by no instruction and not
covered by the C runtime either. This finding asks the consequence: **which of them decide what
the panel draws.** `src/fireflymcf/bootlottery.py`, `cli boot-lottery`.

**The method.** Every wt-6 switch in the image is cross-referenced against
`slotsource.never_established`, its arms resolved by the firmware's own scan
(`layout.Screen._switch_child`), and then — the step that does the real work — walked back to the
screen roots from `layout.screen_object_map`. A gate is `at_risk` when a screen can reach it *and*
at least one arm draws content.

| 13.6 | count |
|---|---|
| wt-6 switches keyed on a never-established slot | 329 |
| …reachable from a screen root | 151 |
| …**at risk** (reachable + content-bearing arms) | 122, over 8 slots |
| …putting *controls* (not navigation) at stake | **3 slots** |

**Reachability is not a formality.** Without it the report names slots 115, 125, 282 and others
that sit on template objects no screen can display — the same false-positive class as the four
incomplete groups of which only object 929 was reachable. The mutation test for this is in
`tests/test_bootlottery.py`.

**The touch-id split matters too.** A wt-12 record's trailing u16 is matched against two id
spaces: group indices (`< 233` on 13.6, a control with bindings) and much larger ACTION ids that
navigate. Slot 529's twelve ids on `GO POWER! CONFIGURATION` look like twelve controls and are
twelve navigation targets; `LotteryGate.controls`/`.actions` separate them.

### The three slots with controls at stake

| slot | screen | page | arms |
|---|---|---|---|
| **23** | 14 | `INVERTER / CHARGER SETTINGS` | 302 (empty) ↔ 303 `Absorb`/`Float`, groups 186-189 |
| **174** | 29 | `SLIDES/AWNINGS` | 218 `Auto Level` ↔ 219 `Cancel` (groups 218/220) |
| **175** | 29 | `SLIDES/AWNINGS` | 192 `All Retract` ↔ 193 `Cancel` (groups 219/220) |

**The audit rediscovers slot 23 unprompted**, from the bytes, with no knowledge of Finding 63 —
which is the oracle it is held to, in the same shape as `find_option_defaults` and the
floorplan-slot derivation.

### The new pair is more interesting than another visibility bug, because it argues against the reading

Slots 174/175 are an **operation-in-progress toggle**: while auto-levelling runs the button reads
`Cancel`, and the same for `All Retract`. That is a designed state, and a designed state implies a
writer. So one of two things is true, and they are worth separating:

- **the reading holds** — nothing sets them, the `Cancel` arms are effectively dead content, and on
  some cold boots `SLIDES/AWNINGS` comes up showing `Cancel` for an operation that is not running;
- **or there is a write mechanism this project has not found** — in which case this is the sharpest
  lead yet toward it, far better than slot 23, because here the design says a writer *must* exist.

This is the standing rule about arguments from absence doing its job: the guarantee that a writer
would have been found is good but not perfect, and this case is where it is most likely to break.

*(2026-09-04: settled the first way — **no writer exists**, and the guarantee is now enumerated
rather than argued. Finding 67 lists every pointer-mediated store form in both images and reads each
hit; none can reach these cells. The `Cancel` arms are unfinished content.)*

**The same design is on 11.23 with different slot numbers and the identical control ids**, which
is real corroboration rather than a 13.6 artifact: slot 32 → screen 14 `Absorb` on groups
**186-189** (13.6's slot 23, same groups), slots 86/87 → screen 29 `Auto Level`/`All Retract` on
groups **218/219/220** (13.6's 174/175, same groups). 11.23 adds a fourth, its Service Mode banner
(slot 35), which carries a `0xFFFF` default and so degrades gracefully. **14.7 is the cleanest of
the three**: one at-risk gate, with a default arm and no controls.

### The cheap coach check, and a safety note

Whether `SLIDES/AWNINGS` ever shows `Cancel` is answerable by **looking at the page**, with no bus
injection and no flashing. If it can be made to read `Cancel` then something writes slots 174/175
and the hunt is on; if it never does across boots, the dead-content reading stands.

**Do not press these to find out casually.** `Auto Level` and `All Retract` actuate levelling jacks
and slide motors. Reading the page is free; operating it is a physical action and is the owner's
call, not a test step.

### Corroborated on the coach, 2026-09-03 — 18 cells photographed in two states

The strongest evidence for this finding arrived from the levelling page (screen 29), reached for
the first time by `mcf-experiment-v14.bin`. All 18 of its state-driven elements read
never-established cells (`docs/LEVELING-PAGE.md` §3), and the owner photographed it twice: once
after flashing **without** a power cycle, and once after one, with nothing on the bus changing
between.

The two buttons flipped from `Auto Level`/`All Retract` (slots 174/175 = 0) to **`Cancel`/`Cancel`**
(= 1); the five `Status` dots and four jack dots went blue to red; and the **eight arrows, uniformly
dark in the first photo, came up in eight different colours in the second**. Seven of those cells
are colour indices, so that second photo is very nearly a direct visual read-out of uninitialised
SRAM — 18 cells at once, on one screen.

That is exactly the prediction: a flash ends in a warm reset, which preserves SRAM, while pulling
power re-rolls it. It also lands the other way from `Absorb`/`Float` on the same panel (which is
*present* after a power cycle), which is what a per-cell bias pattern should look like and what a
single global explanation could not produce.

**One thing it settles that the `Absorb`/`Float` case could not:** these cells are genuinely read
and painted every tick. So `unreferenced` means "nothing writes it", not "nothing reads it" — the
display path is live and only the writer is absent.

## Finding 65 — widget type 9 is the NUMBER-FORMAT DIRECTIVE: mode, flags, width and decimal places, persistent along the walk — `proven-from-code`, 2026-09-04

Asked while designing an on-screen readout for `follow_register`: how does a `wtype-8` value
widget decide to draw `51.0`, `2:49PM`, `----` or a hex ID from a plain integer cell? The
answer is a record the replay had been calling a *spacer* with "no draw case".

**The field.** The record decoder's arm at `0x00253C62` is entered only when the per-type
attribute word has bit 9 set (`tst.w r3,#0x200` at `0x00253B44`), and that word is `1 << wtype`
(`records.py` `aed`), so **only widget type 9 carries it**. Four bytes, always consumed (the bit-5
branch at `0x00253D18` rejoins at `0x00253CBE`), stored into the render state:

| byte | store | state | meaning |
|---|---|---|---|
| 0 | `strb.w r0,[r3,#0x8f]` `0x00253C6C` | `+0x8F` | display mode (table below) |
| 1 | `0x00253C76`-`0x00253CB8` | bits 2-3 → `+0x8E`; bits 0/1/4/5 → state-word bits `0x8`/`0x10`/`0x1000`/`0x2000` | justification + three flags |
| 2 | `0x00253CC8` | `+0x8C` | minimum field width, 0 = none, max 9 |
| 3 | `0x00253CD2` | `+0x8D` | decimal places |

The state is **persistent along the screen walk**, exactly like the R38 font selector: the only
reset is at the screen root, `FUN_00258650` `0x00258720`-`0x00258734` (`+0x8C = 10`, `+0x8D = 0`,
`+0x8E = 2`, `+0x8F = 0`). A `wtype-8` readout therefore draws with the **last directive walked
before it**, which may live in another object. 13.6 has 385 such records in 228 objects, all 12
bytes; 38 of its 415 readouts carry no directive in their own object and inherit one.

**The mode byte (`+0x8F`), from the renderer `FUN_002521C8` (`0x00252242` onward) and the
formatter `FUN_00251E38`.** Strings come from the table at `0x00259B38`: `h`, `0`, `:`, `am`,
`pm`, `AM`, `PM`, `E`, `+`, `-`.

| mode | output | stock 13.6 use |
|---|---|---|
| 0 | plain decimal | 401 of 415 readouts |
| 1 | float-only flag; the formatter self-heals it to 0 for non-float types (`0x00251E72`-`0x00251E7E`) | none |
| 2 | decimal + `h` (`0x0025248E`) | none |
| 3, 4 | minutes → `H:MM` 24-hour, value mod 6000 (`0x00252388`: `0x057619F1` = 2^39/6000, then `0x88888889` = ÷60) | 4: the clock's 24-hour variants, objects 475/541 |
| 5 | seconds → `H:MM:SS` 24-hour, value mod 360000 (`0x002524D0`: `0x5D34EDEF`, `0x57E40`, `0x91A2B3C5` = 2^43/3600) | none |
| 6, 7 | 12-hour `H:MM` / `H:MM:SS` + `am`/`pm` (`0x00252632`) | none |
| 8, 9 | 12-hour + `AM`/`PM` (`0x002525C6`); hour 0 → 12 via `0x2AAAAAAB` = ÷12 at `0x0025252A` | 8: eleven clock widgets — the photo oracle's `2:49PM` |
| 10, 11 | hexadecimal, uppercased in place (`0x002523F8`-`0x0025240A`), capped at the width | 11: the wireless pairing ID, slot 264 (objects 769/771) |

**Decimal places (`+0x8D`).** Integer cells only — 13.6's variable table has **no float-typed
cell** (types present: 0-6, 9, 10). The digit string is zero-padded to `places + 1`
(`0x002526BC`), trailing fractional zeros are trimmed down to one unless flag bit 4 is set
(`0x002526EC`), and a `.` is inserted at `len − places` (`movs r1,#0x2e` at `0x0025275A`); a
locale table read at `0x00252746` swaps it for `,`. So `51.0` on the ELECTRICAL photo is a
type-3 cell holding 510 under directive `00 18 04 01`. This is the **only** route to a decimal
point on this build.

**The flag bits, read back by `ubfx` from the state word** (the renderer indexes a 12-byte
table by its third argument, which the `wtype-8` call site passes as 0 — `movs r2,#0` at
`0x00256F68` — so the "table entry" is the state word itself):

| flag bit | state | meaning | reader |
|---|---|---|---|
| 0 | `0x8` | force a sign: `+` for positive (`0x002528FC`) | `ubfx sl,r1,#3,#1` `0x00252326`; `orrs r3,fp,sl` `0x00252786` |
| 1 | `0x10` | zero-pad to the width (`0x002529D2`), or a leading `0` on a one-digit hour (`0x00252562`); honoured only when justification is 2 | `ubfx r1,r1,#4,#1` `0x00252336` |
| 2-3 | `+0x8E` | justification within the width: 0 left (spaces after, `0x002529F0`), 1 centre (`0x00252A18`), 2 right (spaces before, `0x00252AE0`) | `0x002529DC` |
| 4 | `0x1000` | keep trailing fractional zeros (`12.40` stays; clear trims to `12.4`) | `ubfx r1,r1,#0xc,#1` `0x00252348`; `0x002526D4` |
| 5 | `0x2000` | **never read**: no instruction tests it, no stock directive sets it | — |

**A value wider than its field draws as dashes** (`movs r4,#0x2d` at `0x002529B2`, entered from
`cmp r1,r5 / blo` at `0x002528A4`). That is the `I/C Firmware Rev. ----` on the inverter page: an
unfilled readout does not carry a placeholder string, it overflows and is dashed. **Observed on
hardware, 2026-09-04** (`docs/BUS-BASELINE.md` PC.59): a `minutes-home` readout
(`add_register_readout`) on a width-2 directive drew dashes from the `0xEE` sentinel `0xFF`
(255) before the G12's first sweep, then the stored minutes — the rule's first hardware
confirmation, and the first for a single-byte cell.

**Data corroboration, all from the 13.6 photo oracles.** HOME `2:49PM` = mode 8, no leading zero,
uppercase, no space — exactly the string table's `PM` appended after the minutes. HOME `12.4V`:
objects 440/443 (slots 205/209) carry no directive and are walked immediately after object 441's
`00 18 04 01` — inheritance, read off the layout walk. SETTINGS `10:00PM`: the minutes readout
sits under `00 1a 02 00`, the one stock use of the zero-pad bit, and prints `00`. Every directive
with `places ≥ 1` carries flag bit 4, which is why the panel shows `51.0`, not `51`.

**Corrections this makes.** (1) The 2026 "no ÷60 and no `:` constant" negative above is
withdrawn in place. (2) The pump-auto-off spec's caveat that a donor with `+0x8F ≥ 2` "would draw
`30` as `3.0`" was wrong: mode 2 draws `30h`, and the decimal point belongs to byte 3. Its
conclusion stands — 793's directive is `00 18 03 00`. (3) `records.WRecord` now surfaces the
four bytes as `fmt`; the type-9 name stays `spacer` since the geometry is real, but the comment
says what it is. Object 1055, a clock readout with no directive that looked like an inheritance
puzzle, is referenced by nothing and absent from the settings walk: a template object.

**Consequence for authored readouts.** A readout that inherits its directive is at the mercy of
walk order, so an appended `wtype-8` should carry its own type-9 record. `51.0` from a u16
register needs no scaling code and no float cell: a directive with `places = 1`.

## Finding 66 — base+offset reachability of the working area: the scratch-cell scan's premise was false, the cells survive anyway, and nine holes of 20+ bytes are free — `proven-from-code` with a stated residual, 2026-09-04

Asked because the `follow_register` readout needs a u16 display cell and v15 has no 2-aligned
spare cell left (Finding 65's session). Before looking for fresh SRAM, the existing safety scan
had to be trusted, and its docstring rested on a false premise.

**The premise.** `followchannel._guicode_references` checks raw pool words and T1 `ldr [pc]`
targets and argued completeness from "13.6 contains no movw/movt". 13.6 has **hundreds of
`movw`** — as 16-bit *offsets* off a base register: `movw r3,#0x6bb4; ldr r3,[r2,r3]` with
`r2 = 0x200301B0`, the render state, whose offsets run to `0x96B4`. A cell can therefore be
reached with no pool word naming it. (`movt` is genuinely absent, and T2 `ldr.w [pc]` pool
words — 23 of them, two into the cell area — are covered by the raw-u32 scan.)

**The trace, `src/fireflymcf/sramreach.py`.** Every T1/T2 literal load of a working-area
address (3,548 on 13.6) is followed until its register is redefined, collecting `[rX,#imm]`,
`[rX,rZ]` with a `movw`-known `rZ`, and writeback forms, each with its instruction's width.
Median lifetime is one instruction (a C global loaded and used at once), maximum 246. The
render-state span is derived from the `movw` offsets actually indexed off that base:
`0x200301B0`-`0x20039868`.

**Result for the shipping scratch cells.** Cells 211/213/215/217 (`0x20039F68`/`60`/`5E`/`58`),
which the follower, the Low Volts bridge and the pump routine allocate on v10-v15, are reached
by **no** base+offset form on stock; on v15 the only reaches are the appended routines' own
instructions. The unflashed v15's allocation therefore stands under a stronger scan than it was
made with. Two near misses show the width matters: a `strh` to `0x20039F66` and a pointer to
`0x20039F56` both stop two bytes short.

**The residual, bounded by hand.** Four bases are indexed by a register the trace cannot bound:
`0x200301B0` (+12·depth for the base-frame stack, +(nibble+0x2d)·4 for the register file — both
inside the state span), `0x200371D1` (the digit buffer, + strlen), and two tables *above* the cell
area, `0x2003A8B8` (stride 8, indexed from 0 upward — `FUN_0024F1FC`'s device table) and
`0x2003A9A8` (stride 4, `-1`-guarded). The module poisons everything above the last two, so no
candidate is offered there. What it still cannot see: a base that arrives as a function
parameter, and pointer walks — the same limit `slotsource` carries.

**Free SRAM.** After every form above, plus the cell table at variable-type width and the LCD
firmware's 29 unrelated words, nine holes of 20 bytes or more remain below `0x2003A8B8`, identical
on stock and v15: `0x2003A858` (44), `0x2003A000` (34, between cells 167 and 485), `0x2003A476`
(34), `0x2003A024`, `0x2003A2B0` (24), `0x2003A819` (23), `0x20039958`, `0x20039B28`, `0x20039D18`
(20). `sramreach.free_runs` lists them; `statusbind.index_cell` can point a spare index at one.
They sit above `_ebss`, so they hold garbage at power-on like every cell and a routine must
establish them (the pumpauto sentinel-path pattern). A long hole is the better candidate: a
mis-typed neighbour cannot explain 30 free bytes, but it can explain a 4-byte gap between two byte
cells. **Unreferenced by every form the file shows is not "unused"** — the first use of a fresh
address is a coach check, exactly as the first new watch register was.


## Finding 67 — slots 174/175 have NO writer in either image: every pointer-mediated store form enumerated and read — `proven-from-code`, a defensible negative, 2026-09-04

Finding 64 left one live question: `SLIDES/AWNINGS`'s `Auto Level`/`All Retract` ↔ `Cancel`
toggle (slots **174**/**175**, `0x2003A62C`/`0x20039DDD`, both type 0) is a *designed* in-progress
state, and a designed state implies a writer. PC.57 closed the per-tick reading by observation —
garbage persisted on screen for the whole time the page was displayed — but it could not close an
**event-driven** writer, one that runs only when `Auto Level` (group 218) is pressed, because
nobody presses that to find out. So this dig closes it from the code, and the way it does so is
the point: every form a store can take *without naming its address* is enumerated, and every hit
is read. `src/fireflymcf/indirectwrites.py` is the enumeration; `tests/test_indirectwrites.py`
pins it exactly, so a rebuilt image that adds a hit fails a test instead of quietly gaining a
writer.

### Where a cell's address can come from at all

A store needs the address in a register. On these images there are exactly three sources, and the
first and third were already negative: a **literal** (none — `slotsource` sees every direct form in
both sections and `sramreach` every raw word, the LCD's included); **arithmetic from a traced base**
(`sramreach`: no base+offset, no indexed table, the state span ends at `0x20039868`); and the
**GuiData pointer table** at `+0x44BC8`, whose entry 174 is the only copy of `0x2003A62C` anywhere
in the file. That table is reached by GuiCode's literal `0x90044BC8` and by nothing else: it is
**not a directory entry** (the directory has five — tags `0x10`, `0x20`, `0x30`, `0x41`, `0x42`),
the LCD firmware's fourteen GuiData literals are all `0x90000000`, and the LCD holds **no literal at
all** into `0x20030000`–`0x20050000`. So the LCD cannot materialise a cell address by any route,
and the whole question is what GuiCode does with the pointers it loads from that table.

**The fourteen table readers, by what they do with the pointer.** Three copy it into the render
state's data pointer `+0x24` (`0x253A88`, `0x253AD8`, `0x253BF2`, the record decoder); four copy the
*value* behind it into state `+0x64`/`+0x68`/`+0x6C`/`+0x70` (`0x2533C4`, `0x253486`, `0x253566`,
`0x25365E`); six hand it to the typed reader `FUN_00251E38` in `r0` (`0x257C5A` … `0x25820C`, one
per variable type); one (`0x256878`, in `FUN_00256868`) compares the value with the caller's cached
copy and stores the *value* into the caller's local. So a cell pointer lives in two places only:
state `+0x24`, and `r0`/`r5` inside `FUN_00251E38`.

### The forms, enumerated (13.6; the module runs on all three builds)

| form | GuiCode | LCD | what every hit is |
|---|---|---|---|
| store through a pointer loaded from memory (any base incl. `sp`, `ldrd`) | 15 sources, 36 sites | 26 sources, 188 sites | GuiCode: the **framebuffer pixel write** (`0x250FB6`, fb pointer at state `+0x6BA8`, stride `+0x6BCC`, 3 B/px); fifteen stack-parked *state* pointers storing to state `+0x20A`/`+0x20B` (the screen→object map); the **descriptor writer** `FUN_002509E8`/`FUN_00250B50` (single caller `0x24ED96`, ROM descriptor `0x259ACC` → u16 `0x20039AFA`, u8 `0x20039A29` — two fixed cells, neither ours); one stack out-param (`0x251A8A`); and four hits that are **data decoded as code** (the halfword table at `0x2467xx`, the `tbb` table at `0x241008`, pool strings at `0x2592xx`). **No hit loads from `[rX,#0x24]`: nothing in GuiCode stores through the state data pointer.** LCD: `handle->Instance` peripheral-register writes through a struct's first word (`[r4]` 95, `[r0]` 42 — the HAL shape) and the printf path's `*p++ = c` emitter (`[r4,#0x24]`, `[r4,#0x2C]`) |
| functions storing through a **parameter** pointer, or a callee-saved copy of one, anywhere in the body | 10 | 110 | GuiCode, each read with its callers' argument provenance: `FUN_00247BF8` (`strb [r4=cfg,#0x21A]`, into its own config-struct argument); `FUN_00250E04`/`FUN_00250E2C` (range helpers on `sp`-relative out-params, 18 callers, all stack); `FUN_00251734` (the number formatter, into the digit buffer `0x200371D1` or stack); `FUN_002519F0` (`strh` to `sp+0x42`); `FUN_0025211C`/`FUN_00252128`/`FUN_00252140` (the **record-stream cursor** readers — u8/u16/u24 at `*cursor`, then advance; `r0` is the cursor struct `0x20036D54` at every one of their 73 callers); `FUN_00256014` (**not a store** — the `ldr.w pc,[r2,r3,lsl #2]` jump table at `0x25607C` decoded as code); `FUN_00256868` (above). The LCD's 110 are irrelevant by the argument above: an LCD function can only store through an address its caller materialised, and no LCD caller can |
| calls whose argument came from a word-indexed table load or from state `+0x24` | 12 | 4 | GuiCode callees: `FUN_002409AC` (**strlen** — reads to NUL, no store; fed the `+0x24` pointer only for type-9 string cells), `FUN_002513A4` (the image blit, `r0` from the image table at `0x90010080`, walks its source, never stores through it), `FUN_00251E38` (the typed reader: `r5 = r0`, only `ldr*` through it — checked over the whole `0x251E38`–`0x25215E` body; its stores go to state via literal), `FUN_00257BFC` (the layout engine, `r0` from the object table). LCD: the init-array walker's two `blx r3` (calling the pointer, not passing it) and two HAL-struct field compares flagged by liveness |
| bulk calls with `r0` a literal in GuiCode RAM (the `memset`/`memcpy`/`strcpy` shape) | 99 | 0 | five `strcpy` (`FUN_00259236`) into `0x20039A60`/`AA0`/`ABC`/`AD8`/`AEC` from 12-byte stack strings — furthest reach `0x20039AF8`, `0x2E5` short of slot 175; the cursor readers on `0x20036D54`; the formatter on the digit buffer; `FUN_002591F2`/`FUN_00256450` on the state. The single "covering" hit, `0x253DE4`, is `r2 = movw #0x6BA4` — a state offset the tracker took for a length, pinned as the known false positive |
| block stores (`stm*`, which `sramreach` does not parse) | 562 | 16 | none with a literal base in GuiCode RAM; `FUN_00247BF8`'s 12-byte CAN-template copies go to `r5 = sp+4` |
| the LCD **heap** | — | found | `_sbrk` at `0x21F0CE`, break cell `0x2000A124`, initial break `0x2000AD98` = `_ebss` (`raminit`), and **one** `malloc` (`0x21EF2C`) caller, `0x218680`, for `0x21C` bytes: the break cannot pass `0x2000AFBC`, 148 KB below the working area |

The stack (top `0x20050000`) is the one writer this table cannot enumerate; the cells sit
88–90 KB below it, and PC.57's persistence — garbage unchanged while the page was drawn every tick —
is the observation that covers it.

### What it settles

**Neither image writes slot 174 or 175 by any form.** The event-driven arm of Finding 64 is closed
the same way as the per-tick arm: there is no code that could run on the press, because there is
no code that holds the address. The `Cancel` arms are **unfinished content**: on a cold boot the
page can read `Cancel` for an operation that is not running (photographed, PC.57), and nothing will
ever change it while the page is up. A coach with a real levelling controller would see exactly the
same, because the `0x1FFED` receive slots land in `0x20008198`/`0x2000819C` and no bridge block
carries them to these cells (`LEVELING-PAGE.md` §3).

**And it generalises to the whole `unreferenced` class.** Every pointer-mediated store in both
images resolves to a *fixed* target — the framebuffer, the state, the scratch cursor, the digit
buffer, two descriptor cells, five string cells — none of them selected by a variable index. So
the 210 slots `slotsource` cannot explain are not "written by a path the scan cannot see"; they are
written by nothing, and Finding 64's boot-lottery list is a list of content that draws power-up
SRAM. The residual is the one `sramreach` already states (a base arriving as a parameter is now
enumerated too; pointer walks with writeback are flagged, not traced) and the stack.

**If the toggle is wanted, it is ours to write.** The cheapest correct form is a per-tick store of
`0` to both cells (two `strb`s on the hook chain), which makes the buttons always read `Auto
Level`/`All Retract`; the designed form is `LEVELING-PAGE.md` §5's bridge from `0x1FFED`. Either is
the `readout`/`follow_register` routine shape, already coach-verified.

### A side result: the `Shore Brkr Size` counter is BSS-zeroed

`CONTROL-BEHAVIOR.md` §5a's open question was what the per-control variant counter holds at
power-on. Group 199's counter is `0x20006E6C + 199·16 + 0x0A = 0x20007AE6`, inside the `.bss` zero
loop's range `0x20000228`–`0x2000AD98` (`raminit`), so it is **0 at power-on** — the whole state table
is. A `0x80` group whose bindings are all variant 0 therefore fires normally from cold until the
setter (`FUN_0021B5E8`, sole caller `0x21B002`) moves it. PC.32's silence at power-on then needs
either a setter write that never appeared, or the `[state+0x09]` phase-byte route; the counter
alone no longer explains a cold-boot failure. The cell is an LCD-RAM address a GuiCode routine can
read, so `add_register_readout` could show it live if its register spec grew an `addr` form — the
"debugger" `CLAUDE.md` defect 0b asks for.

Tests: `tests/test_indirectwrites.py` (15) — four synthetic tests prove each scan fires and stops
(a redefinition ends a pointer's lifetime; a parameter survives a call only through a copy), the
rest pin the 13.6 enumeration above exactly and run every scan on all three builds. The heap and its
one 540-byte allocation are identical on 11.23 and 14.7.

## Finding 82 — every receive-filter entry, traced: 128 of 13.6's 298 reach a painted cell, 132 are written and never read, and the slot-array base never escapes — `proven-from-code` (the trace and the negatives), captions `inferred-from-data`, 2026-10-19

**Question.** Findings 80 and 81 named two families from the file: tank instances and G12 outputs.
For every other RV-C message the panel receives, what does the panel do with it, and what does it
call it? Nothing may be fixed per build; another firmware may differ.

**The method, as code** (`src/fireflymcf/slottrace.py`, `src/fireflymcf/busmap.py`;
`fireflymcf bus-map MCF [--dgn X] [--json]`):

```
receive-filter entry var (GuiData +0x2A70, §5.4a): DGN, mode, field, filter
  |  slot = register.status_slot_addr(var)             index split: var>255 is +0x38
  v
slottrace.state_reach: the base from GuiCode_entry1 (header word +0x10) to every load through it
  |  slot_reads: a load at slot_disp(var) through a register holding the base
  v  Tracer: data AND control flow to every store and call the value decides
sinks: SRAM cells / GuiCodeState fields / the config-set helper / other calls
  |  cell -> +0x44BC8 index -> a table-1 record naming it as value_slot or mode-6/7 colour
  v  (a cell no record names: one more hop, through another block's read of it)
widgets -> caption by record order (control, row, gauge, readout, parent)
```

### 1. Where the base goes — `proven-from-code`, and why the negatives hold

The LCD hands the slot-array base to `GuiCode_entry1` in `r0` (Finding 33). `state_reach` starts
there — the entry is read from the header vector word at GuiCode `+0x10` (13.6 `0x0024EE29`), not
assumed — and sweeps each function with the set of registers holding `base + offset`: a `mov` or
`add #imm` copies it, a stack spill is followed (13.6 `0x242B76 str r0,[sp,#4]` inside
`FUN_00242B70`, reloaded later), a `bl F` with the base in `r0`-`r3` queues `F` with it there. Any
other use is an *escape*. On 13.6 the base reaches ten functions — `0x242174`, `0x242B70`,
`0x247BF8`, `0x24EE28`, `0x24F00C`, `0x24F104`, `0x24F820`, `0x250984`, `0x250CF0` (base in `r1`),
`0x259100` — and **escapes nowhere, on all three builds** (`test_the_base_never_escapes`). Every
load through it outside `FUN_00247BF8` and `FUN_00250984` is at `+0x200`–`+0x219`, the state
fields past the low slot array, exactly as Finding 77 item 3 read from the decompilation.

So "no load at `slot_disp(var)`" is a guaranteed negative: the slot is **write-only**
(`unread_guarantee`). This generalises Finding 77's byte scan, which could not tell the slot
array from another struct with a field at the same offset and could not see a narrow load at all
(alone, it left 54 of 13.6's unread entries undecided; this analysis decides every one). Finding 77's residual — a base kept in a
spill and reloaded — is now followed rather than excluded by argument. What remains outside it:
the raw-frame parsers `GuiCode_entry4` drives (Findings 75, 77), which read frames, not slots.
`busmap` reports, per DGN, the GuiCode functions whose literal pool holds the DGN as
`DGN << 8` (the form `canid & 0x03FFFF00` is compared against): 13.6's solar DGNs all sit in
`FUN_00240D20`, `1FFFD`/`1FEA3`/`1FEC8`/`1FEC9`/`1FFFC` in `0x24FCE8` (Finding 77's alternator
parser), `1FFD4` in `0x24F104`.

### 2. The tracer — data and control flow

`tanknames._trace_from` follows data flow only. Most blocks decide on the value instead
(`cmp r2,#1 ; beq.w` choosing a colour master, var 80 at `0x248D96`), so the tracer also follows
control flow: a compare of a carrying register taints the flags; a decided branch explores both
successors to their join; every register either arm writes is carrying at the join and every
store either arm makes is a `control` sink. The join is the first instruction of the fall-through
path the taken path also reaches, moved forward while a branch in either arm skips over it to a
later point of that path — 13.6 var 207's `ble 0x24A86E` (`0x24A81C`) leaves the inner arm for
the end of the whole block, the 12-18 V decode fence (`decodeguards.py`). An arm that runs 160
instructions without rejoining is a runaway and its sinks are dropped. A store the trace made is
remembered: var 42 (`15FCE` selector `0x2F`, Low Volts arming) stores its bool to `0x20039A79`
and re-reads it two instructions later to pick cell 45's colour (`0x2003A814`) — the block
DGN-MAP describes.

**Checked against Ghidra.** `data/slot_bridge_136.txt`, extracted by a Ghidra script with no code
in common, lists 46 clean colour/bool blocks. The tracer reproduces 45 of them cell for cell. The
46th is the dump's error: it gives var 124 the cell `0x20039E24`, whose store at `0x24A7E0` writes
`r2 >> 1` (`mov r3,r2` at `0x24A7D4`), and `r2` was set before var 124's load at `0x24A7BC` and
not written between. The dump also gives var 138 two stores (`0x24853C`, `0x248542`) that are
var 3's (`cmp r2,#0x4e` on var 3's percentage, `0x248532`) — the LP tank's level decides them —
and gives var 206 var 207's Float store. `test_ghidra_bridge_dump_reproduced` pins all of it.

### 3. What it reaches, and the names

A sink cell is joined to widgets through the `+0x44BC8` cell table (§5.4b) and table 1's record
lists. The caption rules are record order, `inferred-from-data`, in this priority:

| rule | caption |
|---|---|
| `control` | the labels of the control whose box's mode-7 fill is the cell (`outputs._segments`) |
| `row` | a `"NN - NAME"` label immediately before the record (the G12 input rows) |
| `gauge` | the labelled record before a wt-5 container whose `value_slot` is the cell (`tanknames`, unchanged) |
| `readout` | the one label (wt-0/11, two or more characters) between the previous value record, less its unit, and a wt-8/13 readout; two or more is ambiguous and names nothing (13.6 object 922 has `'Inverter/Charger Status'` and `'DC Voltage'` before one readout) |
| `parent` | the `readout` rule at the wt-5 record that contains the object |

Two families keep the names earlier findings gave them: `DC_DIMMER_STATUS_3` entries take the
OUTPUTS-page names and control captions of Finding 81 for their pin, and a `15FCE` entry that
reaches the config-set helper takes the OPTIONS row of that offset's register.

**The config join, from the receive side, agrees with the send side everywhere.** Each OPTIONS
selector's slot is compared, in one block (13.6 `0x249BF6`–`0x249CE6`), with the panel's own
register, and on a difference a far arm calls the config-set helper with the config offset as an
immediate: `uxtb r2,r2 ; movs r1,#0xd ; mov r0,r4 ; bl 0x259100` at `0x249E60` for selector `0xE9`.
So the filter entry's selector and the offset are joined by one instruction. `config.option_selector`
joins them from the OPTIONS tap handlers (Finding 23's rules), a separate code path. They agree on
every selector both reach: eight on 13.6, ten on 14.7 (including `0xD7`/`0xD8`, which no OPTIONS
row names). 11.23 has no such compare block (no entry reaches its helper). This settles 14.7's `0xE4` = Starlink Power
(cfg `0x1B`) and `0xE7` = Lithionics Batteries (cfg `0x14`), until now `inferred-from-code` from
one route only (`config.option_selector` docstring).

### 4. The coverage, 13.6

| DGN | RV-C name | entries | named | drawn | drawn, unnamed | config | logic-only | write-only | inert | the panel's names (examples) |
|---|---|---|---|---|---|---|---|---|---|---|
| `1FEDA` | DC_DIMMER_STATUS_3 | 75 | 41 | 45 | 7 | 0 | 0 | 30 | 0 | OUTPUTS-page names, Finding 81 |
| `15FCE` | - | 53 | 27 | 24 | 6 | 11 | 4 | 14 | 0 | the eight OPTIONS rows, floorplan; `Low Volts`, `HVAC Load`, `Start at`, `Stop at`, `Min Run Time`, `Gen Start Retries`, `Between Retries`, `Max Chrge Rate`, `Absorb Hours` |
| `1FBDA` | - | 27 | 11 | 11 | 0 | 0 | 1 | 15 | 0 | the input rows, `'1 - SHORE SENSE (12V)'` ... `'10 - IGNITION (12V)'`; `Engine` |
| `1FFE2` | THERMOSTAT_STATUS_1 | 15 | 0 | 3 | 3 | 0 | 0 | 12 | 0 | (climate cluster, no caption) |
| `1FFFD` | DC_SOURCE_STATUS_1 | 11 | 3 | 4 | 1 | 0 | 2 | 5 | 0 | `DC Current / I/C Amps`, `House` |
| `1FFBF` | AC_LOAD_STATUS | 10 | 0 | 0 | 0 | 0 | 0 | 10 | 0 | |
| `0EBFF` | (J1939 TP.DT) | 8 | 0 | 0 | 0 | 0 | 0 | 6 | 2 | |
| `1FACE` | - | 7 | 2 | 6 | 4 | 0 | 1 | 0 | 0 | `Mira PIN:` / `Eclipse PIN:` digits, `Mira ID:` |
| `1FFAD` | ATS_AC_STATUS_1 | 6 | 2 | 6 | 4 | 0 | 0 | 0 | 0 | `LINE 1`, `LINE 2` (V, A, Hz) |
| `1FECA` | DM_RV | 5 | 0 | 0 | 0 | 0 | 0 | 4 | 1 | (Finding 77) |
| `1FFB7` | TANK_STATUS | 5 | 5 | 5 | 0 | 0 | 0 | 0 | 0 | Finding 80 |
| `0BFC1` | - | 4 | 4 | 4 | 0 | 0 | 0 | 0 | 0 | `ADC Raw Value:` / `Maximum:` (tank diagnostics) |
| `1FF9C` | THERMOSTAT_AMBIENT_STATUS | 4 | 1 | 1 | 0 | 0 | 0 | 3 | 0 | `FRONT` (instance 0) |
| `1FFD7` | INVERTER_AC_STATUS_1 | 3 | 2 | 2 | 0 | 0 | 0 | 0 | 1 | `AC Out Volts`, `AC Out Freq.` |
| `1FFCA` | CHARGER_AC_STATUS_1 | 2 | 2 | 2 | 0 | 0 | 0 | 0 | 0 | `AC Input Volts`, `AC Input Freq.` |
| `1FFDC` | GENERATOR_STATUS_1 | 2 | 2 | 2 | 0 | 0 | 0 | 0 | 0 | `Gen Hrs` |
| `1AAFD` | - | 2 | 2 | 2 | 0 | 0 | 0 | 0 | 0 | `House`, `Chassis` (battery gauges) |
| `1FECC`, `1FF96`, `1FFD2`, `1FF89` | charger/inverter configuration | 7 | 6 | 6 | 0 | 0 | 0 | 1 | 0 | `Absorb`, `Float`, `CHARGE RATE`, `Shore Brkr Size`, `Low Batt. Cutout`, `VAC Dropout` |
| `1FED8` | GENERIC_CONFIGURATION_STATUS | 2 | 0 | 2 | 2 | 0 | 0 | 0 | 0 | (G9 / G12 #2 `FIRMWARE:` rows, ambiguous) |
| `1FFED` | LEVELING_CONTROL_STATUS | 2 | 0 | 1 | 1 | 0 | 0 | 1 | 0 | §5 |
| 43 more DGNs | | 48 | 1 | 2 | 1 | 0 | 5 | 31 | 10 | |
| **total** | | **298** | **111** | **128** | **29** | **11** | **13** | **132** | **14** | |

11.23: 291 entries, 124 drawn, 120 write-only, 2 untraced (var 169: a pool word holds its slot
address but no traced read of it reaches a sink; var 213: read, its trace reaches no sink).
14.7: 310 entries, 115 drawn, 11 config, 146 write-only, 0 untraced. `fireflymcf bus-map` prints
the table for any build in about 9 s (most of it Finding 81's string-block emulation).

**Write-only, by family (13.6).** 30 `DC_DIMMER_STATUS_3` instances (24, 40, 43, 60-71, 90-104:
the panel watches them and nothing reads the slot); `THERMOSTAT_STATUS_1` instances 1-6 and
`THERMOSTAT_AMBIENT_STATUS` 1-3 (only zone 0 is read); all ten `AC_LOAD_STATUS`; the fourteen
solar DGNs (frame-parsed by `FUN_00240D20`); four DM_RV; `DC_SOURCE_STATUS_1` instances
`0x83`-`0x87`; fourteen `15FCE` selectors; fifteen `1FBDA` instances; `1FFED` var 234, `1FFEC`
var 236 and `1FFEE` var 240. Each carries its evidence line.

### 5. Where the panel's use and the RV-C name part

- **`1FFED` LEVELING_CONTROL_STATUS, var 232** (payload byte 0, open filter): read at `0x24A86E`
  (`ldrsh.w r1,[r4,#0x1d0]`), stored as a byte to `0x20039F28` (cell index 397), and compared
  `== 1` at `0x24AAA2` to pick cell 375's colour. Index 397 is the value slot of the three type-6
  switches in object 882 (`'Max of 2 A/Cs on gen power'`), which choose among the climate clusters
  872-874, 876-877 and 879-880; index 375 is a fill in clusters 872-875. **No screen-29 widget is
  reached** — the LEVELING-PAGE negative (`BUS-BASELINE.md` PC.57) stands — and the only widgets
  reached are on the climate page. The other two levelling entries (234, 236) and `1FFEE` (240)
  are write-only.
- **`1FBDA`** is not "G12 output / keypad status" (DGN-MAP's old row): instances `0xA1`-`0xAA` are
  the G12/G9 input rows (`row` rule, matching `g12rules.input_status`, Finding 54), with every
  floorplan's label for each input.
- **`1FFE2`** is not "DC source status" (DGN-MAP's old row): it is RV-C's THERMOSTAT_STATUS_1, and
  the panel paints instance 0 on the climate clusters (a `°` readout, the mode tiles, and
  `setVariant` — the function pointer at `0x20039908`, DGN-MAP — on groups `0x8C`-`0x92`).
- **`1FACE`** (no RV-C name; Firefly's): instances `0xF0`/`0xF1` carry the `Mira PIN:` /
  `Eclipse PIN:` and `Mira ID:` digits of the pairing page.

### 6. Corrections

- **`DGN-MAP.md` §1**'s table: `1FBDA`, `1FFE2`, `1FFFD`, `1FFBF`, `1FACE`, `1FFAD` rows corrected
  (above); its regenerate line now names `fireflymcf bus-map`.
- **`LEVELING-PAGE.md` §2** said "the page subscribes" to vars 232/234/236/240. Three of the four
  slots are read by nothing, and var 232 drives the climate page (§5 above).
- **The "46 bridge blocks"** of `CLAUDE.md`'s constraint and `slotsource`'s docstring are the
  clean colour/bool blocks of the Ghidra dump. 128 of 13.6's 298 entries reach a painted cell by
  some fixed block; the constraint's substance — only a fixed block, or an authored routine, can
  carry a received value to a cell — stands.
- **Finding 77**'s residual (a base kept elsewhere and reloaded) is narrowed: stack spills are
  followed, and nothing else holds the base.

### 7. Predictions for the coach

1. A frame on `1FFED` from any node, payload byte 0 = 1, changes the CLIMATE page (object 882's
   switches on index 397 select a different cluster; cell 375's tile colour changes) and nothing
   on screen 29. Byte 0 = 0 restores it. *Watch the climate page, not the levelling page.*
2. `THERMOSTAT_STATUS_1` (`1FFE2`) or `THERMOSTAT_AMBIENT_STATUS` (`1FF9C`) frames for instances
   1-6 / 1-3 change nothing on any page; instance 0 changes the climate cluster.
3. `AC_LOAD_STATUS` (`1FFBF`) on any of its ten instances changes nothing on any page.
4. `1FACE` instance `0xF0` frames change the `Mira PIN:` digits on the pairing page.

Tests: `tests/test_busmap.py` — the 13.6 coverage and names pinned; the Ghidra dump, the tank
names on all three builds, the OUTPUTS-page names, the selector join on 13.6 and 14.7 and the G12
input labels as oracles; the base's reach with its positive controls; and three stubs (no reads,
no control flow, an escape) that each break a pin.

## Finding 81 — every name the G12 OUTPUTS page can show, with the floorplans and options that show it — `proven-from-code`, captions `inferred-from-data`, 2026-10-19

**Question.** `outputs.output_names` read the G12 OUTPUTS rows from the page object's own records
and called them "static ... the same on every floorplan". The owner reported names that appear
only with an option enabled or on some floorplans. Where do those names live?

**Three mechanisms, all now read** (`outputs.output_name_variants`):

| kind | where the name is | builds | condition from |
|---|---|---|---|
| static | the row label itself, page object's own list (13.6 obj 35 `'12 - A/C Fan Low'`) | all | none |
| dynamic | a type-9 string cell drawn by the wtype-13 record after the bare `'NN - '` label (§6.1); GuiCode copies the name in | 13.6 (14 rows), 14.7 (13) | the GuiCode block's own compares |
| switched | the row label sits in the arms of a type-6 switch (11.23 obj 675 r7/r8 → objects 811-821 on slot 7, the floorplan slot) | 11.23 | `FUN_00256C54`'s arm scan (`layout.Screen._switch_child`), inverted |

**The dynamic block.** 13.6 `FUN_00247BF8`, `0x2494B2`–`0x249794`, one straight-line block, no
calls. It reads the floorplan register `[0x2003A77A]` (`ldrb r5,[r3]` @`0x2494B4`) and seven OPTIONS
registers, and for each of the 14 cells either copies a name out of the string table at
`0x259278` (`ldm r6!,{r0-r3}` / `str r0,[r7]` …, e.g. `Tank Heat Fresh` @`0x2494FE`–`0x249506`) or
stores one `0` byte (`strb r2,[r3]`, e.g. @`0x2494C2`). Finding 35 transcribed four of these gates by
hand; this finding runs all fourteen. Gates read, 13.6:

| register | gate | cells it decides |
|---|---|---|
| `0x2003A77A` | floorplan (cfg `0x00`) | 05, 06, 07, 08, 24, 25, 35 |
| `0x2003A296` | Heat Pump @`0x249570` | 10 |
| `0x2003A38C` | AGS / AES / AES Single Output @`0x24957E`: `==3` → 18, `==1`/`==2` → 19 and 20 (`0x259260+0x84/+0x90` vs `+0x9C/+0xA8`) | 18, 19, 20 |
| `0x20039CF8` | Cargo / Bath Light @`0x2495B0` | 25 |
| `0x20039EBD` | Bath Light @`0x2496C4` | 26 |
| `0x20039E70` | Bunk Accent @`0x2496D2` | 34 |
| `0x20039B49` | Bath Fan @`0x2496E0` | 40 |
| `0x2003A4B4` | Black Tank @`0x249528` | 07 |

**How it is read, not transcribed** (`cellwriters`). The write sites of the cells come from
`slotsource` (32 on 13.6, 29 on 14.7). The block starts at the nearest branch target before the lowest site
from which the code runs without reading a register or flag it did not set (`0x2494B2`, the target
of `b` @`0x2494F8`), and ends at the first unconditional branch after the highest site
(`b 0x249774` @`0x249794`, which closes `Security Lt`'s store run). A concrete Thumb-2
interpreter over capstone's decode (IT predicates from capstone) runs it under every combination
of the SRAM bytes it reads: 15 floorplan values × 4 generator modes × 2⁶ = 3,840 runs on 13.6. It
refuses a call, an instruction outside its set, a read outside GuiCode/SRAM, an unset register, or
a write site that no combination reached. A per-cell input is kept only if changing it changes
that cell's text, and the satisfying assignments are merged into the conditions below.

**Why it is trusted.** Four checks, none a fit (`tests/test_outputs.py`):
1. The coach: stock WD names 05 and 08 and leaves 06 and 07 blank (PC.18b).
2. `tankgates.gates`, Finding 35's separate interpreter, agrees on every floorplan with Black Tank
   on and off; and the `tankgates.widen` image reads back as its patch says (06 exactly RT, RA, RD,
   WA, WD, WT), the build that put all four rows on the coach.
3. Unicorn runs the same block from the same start to the same exit on 40 random input
   combinations per build (13.6, 14.7) and every cell matches.
4. The G12 pin sheet (`answer_key.CHANNELS`): every pin it names has a page name, and pin 25
   (`CARGO LTS / BATH LTS`) and pin 35 (`SECURITY LT / LIVRM OHC / BENCH SEAT OHC`) carry
   exactly two and three page names.

**13.6, every name** (`#0` is floorplan value 0, which no floorplan-name arm names):

| pin | name | shown when |
|---|---|---|
| 03 / 04 | Awning EXT / Awning RET | always |
| 05 | Tank Heat Fresh | floorplan WA, WD, WT |
| 06 | Tank Heat Grey | floorplan RT, SY, TB, CB, WT, RY |
| 07 | Tank Heat Black | floorplan RT, WT and Black Tank on |
| 08 | Tank Heat Grey 2 | floorplan RA, RD, WA, WD |
| 09 | A/C Cool | always |
| 10 | A/C Heat Pump | Heat Pump on |
| 11 / 12 | A/C Fan High / A/C Fan Low | always |
| 18 | AES Start/Stop | AES Single Output |
| 19 | AES Start · Gen Start | AES · AGS |
| 20 | AES Stop · Gen Stop | AES · AGS |
| 21 / 22 / 23 | Awning Lt / Kitchen OHC Lts / Step Lt | always |
| 24 | Bed Lts | any floorplan but RT, WT |
| 25 | Bath Lts · Cargo Lts | Cargo / Bath Light on, and any floorplan but SY, TB · SY, TB |
| 26 | Bath Lts | Bath Light on |
| 32 | Main Ceiling Lts | always |
| 34 | Bunk Accent Lts | Bunk Accent on |
| 35 | Bench OHC Lts · Living Rm Lts · Security Lt | SY, TB, CB, RY · RA, RD, WA, WD · #0, RT, WT, 4NJ, 3NG, 3NK, 3NJ |
| 40 | Bath Fan | Bath Fan on |
| 43 / 44 | A/C Cool / Water Pump | always |
| 01, 02, 13-17, 27-31, 33, 36-39, 41, 42 | (blank) | never named |

**14.7** runs the same block shape (`0x249964`–`0x249BDE`, 1,280 runs, floorplan register
`0x2003A2A7`) with its own whitelists (05 WA, WT; 06 any but #0, RA, WA; 07 RT, WT + Black Tank;
08 RA, WA), adds **17 Starlink Power** (Starlink Power on) and **25 Cargo Lts** (Cargo Light on), names
35 `Living Rm OHC Lts` on RA, WA, and has no row 26 or 40 names. Its row 08 is both: the static
label `'08 - Tank Heat Grey 2'` *and* a dynamic cell that writes `Tank Heat Grey 2` on RA, WA, so
those two floorplans would draw the name twice (inferred from the records; no 14.7 coach to look at).

**11.23** has no dynamic rows. Its tank, A/C, generator, bed and living-room rows are switch arms
on slot 7 (05 `TANK HEATER FRESH` on WT, WA, WD, CB; 06 `TANK HEATER GREY` on Ship Mode, RT, WT,
`TANK HEATER GR 1` on RA, WA, WD, RD; 08 `TANK HEATER GR 2` on RA, WA, WD, RD, `TANK HEATER GREY`
on CB; 35 `SECURITY LT RR` · `LIVING ROOM OHC` · `BENCH OHC LTS`), and 10 / 40 sit behind switches on
slots 248 and 247, which no OPTIONS row binds; they are the config bytes `0x0A` and `0x0E`
(`config.config_offsets`), reported raw.

**Corrections.** `outputs.py`'s "the same on every floorplan" is wrong for 14 of 13.6's rows, 13 of
14.7's and every switched row of 11.23. `MCF-FORMAT.md` §6.1's "nothing in the MCF maps a pin to a
table entry — that join arrives over the bus" and "a dynamic row's text is runtime state" are wrong
too: the pin → name join is the store's literal cell address, and the selector is the floorplan and
OPTIONS registers. What arrives over the bus is only those registers' values (the config payload,
Finding 19), so the name is runtime only in the sense that the floorplan is.

**Control captions** (`outputs.control_captions`), the second name source, kept apart. A control is
cut out of an object's own record list at every record carrying its own mode-7 fill. It is joined
to a pin by `status` (the box's fill index is the cell the pin's GuiCode status block colours,
`statusbind.pin_bindings`) or `command` (a touch region whose binding group commands only that pin).
Both joins are read from bytes; that the label inside the cut is the control's caption is the record
order (box, caption, touch region), so captions are `inferred-from-data`. Each carries the
floorplan/option switch arms on the containers above it; switches on screen slots (13.6 slots 1 and
2) are dropped and listed, which can only widen a caption's condition. A second, independent check on
the dynamic block: 13.6's per-floorplan LIGHTS cards (objects 940-949, arms of a slot-5 switch)
caption pin 35 `Security Light` on RT, WT, `Living Rm OHC / Exterior` on RA, RD, WA, WD and three
`Bench …` spellings on SY, TB, CB, RY, the same split the GuiCode block makes. Captions are many and
noisy where a box's colour is shared: pin 7's `Entry Awning` on the awning cards (objects 848-859)
shares the colour cell of `Black Tank Heater`, so a `status` join says "this box shows pin 7's
state", not "this control is pin 7's load".

**What changed in tools.** `fireflymcf pins` gains an OUTPUTS-page name column (`+` marks a
conditional name), `--names` lists every name with its condition and every caption, and `--json`
carries `outputs_page_names` and `captions` with their conditions and sources. `add_button` /
`wire_button`'s `NAMED_OUTPUT` refusal now counts a pin as named if any floorplan or option names it
(13.6's 05, 06, 07, 08, 10, 18-20, 24-26, 34, 35, 40 are newly refused). The recipe cannot narrow it
to the owner's floorplan: floorplan and options live in the G12's NVM, not in the build. A dynamic
row whose block cannot be run counts as named.

**Coach prediction** (the reference coach's running build carries `tank_heater_names`; WD with AES Single
Output, Bunk Accent, Cargo / Bath Light, Black Tank and Bath Fan on, Heat Pump and Bath Light off,
`build/coach-wd.json`). NETWORK DIAGNOSTICS → G12 OUTPUTS should read: 05 Tank Heat Fresh, 06 Tank Heat
Grey, 07 Tank Heat Black, 08 Tank Heat Grey 2, **18 AES Start/Stop, 19 and 20 blank, 10 blank, 24 Bed
Lts, 25 Bath Lts, 26 blank, 28 Overflow Valve, 29 Starlink, 34 Bunk Accent Lts, 35 Living Rm Lts, 40
Bath Fan**, and the static rows. Two option flips each move one row: Bath Light on names 26 `Bath
Lts`; Heat Pump on names 10 `A/C Heat Pump`. A mismatch on any row is a defect in this reading.

## Finding 80 — each `1FFB7` TANK_STATUS instance's name comes from the gauge it paints, not from the RV-C table — `proven-from-code` to the gauge, caption `inferred-from-data`, 2026-10-19

**The question.** `tankbroadcast.INSTANCE_NAMES` and `fireflymcf-tankpatch`'s flags name the tanks
from RV-C's table (0 fresh, 1 black, 2 grey, 3 LPG, 18 second grey). The standard says nothing
about what a panel draws for an instance, and a build is free to use others. Can the firmware
itself name each instance?

**It can, through the panel's receive path.** `src/fireflymcf/tanknames.py` follows each
`1FFB7` receive-filter entry to the gauge it paints. 13.6 addresses below; every joint is found
per build.

| joint | 13.6, instance 0 | level |
|---|---|---|
| receive filter entry (GuiData `+0x2A70`), byte 4 = instance | var 0 = `b7 ff 29 01 00 …` | `proven-from-code` (LCD `FUN_0021C760`, §5.4a) |
| decoder region where r4 = GuiCodeState: `push` / `mov r4,r0` (`0x247BFC`) / `ldr.w r3,[r4,#0x214]` (`0x247C18`), r4 not written again until the `pop` at `0x24EDB0` | `FUN_00247BF8` | `proven-from-code` (Findings 19, 33) |
| slot read and decode: `ldrsh.w r2,[r4]` `0x24838A`, then `uxtb`/`ubfx`/`mla`/`sdiv`, i.e. `pct = min(100, (100·level + res/2) / res)` | `strb` `0x2483B0` → `0x200399E0` | `proven-from-code` |
| second read of the same slot gives the 73-px "empty from top" offset `73 − 73·pct/100` (`0x51EB851F` divide by 100, `rsb #0x49`) | `strb` `0x2483EC` → `0x2003A6D5` | `proven-from-code` |
| cell table (`+0x44BC8`): index 255 → `0x200399E0`, 254 → `0x2003A6D5` | | `proven-from-code` (Finding 17) |
| HOME cluster record list: wt-11 `'FRESH'`, then wt-5 container `value_slot=255`, `child_index=721` (the gauge, whose wt-8 `%` also names 255) | objects 911-921, PROBE rows 1018/1019 | records decoded; **caption pairing `inferred-from-data`** |

The formula matches PC.62: 34/56 read 61 % and 13/100 read 13 %.

**The caption step is an inference, and its support.** No instruction joins a caption to a gauge.
The layout engine draws records in order, and every tank cluster on every corpus build is "caption
label, then its gauge container". The geometry agrees: on 13.6 WD with Black Tank on, the layout
replay places each caption box (`'FRESH'` 477-532 × 56-76) so it ends on the row where its well
starts (721: 482-527 × 76-161), on the same centre line. All four tanks do the same
(`tests/test_tanknames.py::test_caption_sits_on_its_gauge_well`). The PROBE TANK DIAGNOSTICS rows
repeat the pairing independently: 13.6 objects 1016-1022 are `'BLACK'`/`'FRESH'`/`'GREY'`/`'GREY2'`,
each followed by the same container, then that tank's `ADC Raw Value`.

**Results.** All three builds agree with RV-C's table. Only the caption text differs from RV-C's
wording.

| instance | var | 13.6 | 11.23 | 14.7 | 13.6 GuiCode read → cell (index) |
|---|---|---|---|---|---|
| 0 | 0 | FRESH | FRESH | FRESH | `0x24838A` → `0x200399E0` (255) |
| 1 | 1 | BLACK | BLACK | BLACK | `0x24844C` → `0x200399CD` (253) |
| 2 | 2 | GREY | GREY, **GREY1** in cluster 872 | GREY | `0x2483EE` → `0x2003A8A8` (257) |
| 3 | 3 | LP | LP | LP | `0x2484AA` → `0x20039E79` (243), percent only |
| 18 | 138 | GREY2 | GREY2 | GREY2 | `0x2484D2` → `0x2003A13B` (259) |

11.23 and 14.7 reads: 11.23 `0x247BD0`/`0x247CC4`/`0x247C4C`/`0x247D3C`/`0x247D64`; 14.7
`0x2483FA`/`0x2484BC`/`0x24845E`/`0x24851A`/`0x248542`. On 11.23 the decode is an if/else diamond:
the 0xFF no-data sentinel arm branches forward to the shared `strb`. The tracer follows a forward
`b`, and that is the only thing it needed added for 11.23.

**Gating: which configuration shows each name** (HOME, walked per floorplan):

- **13.6:** FRESH and GREY on RT-RY. GREY2 on RA, RD, WA, WD. LP on RT, RA, RD, CB, RY. BLACK
  only with the Black Tank option (slot 112 = 1; type-6 switch, base 801 → arm 802). The reference
  coach (WD) draws FRESH, GREY and GREY2, plus BLACK with the option, and **no LP gauge**, though
  the G12 broadcasts instance 3. 4NJ/3NG/3NK/3NJ draw no tanks.
- **14.7:** the same shape on its own floorplans. GREY2 on RA and WA; LP on RT, RA, CB, RY, RE;
  BLACK on every floorplan when slot 103 = 1 (type-6 switch, base 737 → arm 738).
- **11.23:** HOME's tank switch reads **slot 8**, not the floorplan slot 7. Values 0-5 select
  clusters 870-875, and 0 also needs slot 36 = 1. The writer of slot 8 was not traced. `GREY1`
  appears only in cluster 872 (slot 8 = 2), the one that also draws GREY2.

`tank_names` returns every variant, most-used first (`'GREY / GREY1'`), rather than choosing.

**Negatives, scoped.**

- **G12 OUTPUTS** (the owner's lead) names the tank-heater pins 5-8 (`05 Tank Heat Fresh` …
  `08 Tank Heat Grey 2`, Finding 35's floorplan whitelists). That is pin → name, not instance →
  name. In table `0x61`/`0x62` the level cells `B5`-`B8` are referenced only by the four `1FFB7`
  records. `C2` is referenced by `#93` and by the `1AAB7` record. So the rule tables carry no
  heater-pin ↔ instance join. The cells are written by the G12 application, which this project has
  not dumped (`G12-CODE.md`, config table: "app-written").
- **The G12 side gives instance → level cell, not a name:** `B5` → 0, `B6` → 2, `B7` → 1,
  `B8` → 18, `C2` → 3.

**What this does not claim.** The receive path names what the *panel draws* for an instance. It
does not say what the G12 measures: the G12 broadcasts instance 3 on WD, and WD has no LP gauge.
A trace that found no captioned gauge returns `None` and never falls back to RV-C's word
(`test_a_decoder_of_another_shape_names_nothing`).

**Coach check (prediction).** PC.62 already confirms instance 0 → FRESH: `19FFB7AF#002238…` read
61 % on FRESH while GREY, GREY2 and BLACK read `--%`. The rest, on the running image, with the G12
silent on that instance (otherwise frames alternate, PC.46), injecting `19FFB7AF#<inst>2238FFFFFFFFFF`:

| inject | HOME on WD should show |
|---|---|
| `02…` | **GREY** 61 %, every other gauge unchanged |
| `12…` | **GREY2** 61 % |
| `01…` | nothing on HOME with Black Tank off. **BLACK** 61 % with it on |
| `03…` | **no HOME change** (WD draws no LP gauge) |

The PROBE TANK DIAGNOSTICS row of the same name should show the same percent, since it is the same
container and cell.

Wiring, left for the session that owns `tankpatch.py`/`doctor`: `tankpatch`'s report can label
each rule by `tanknames.tank_names(data)[b.instance][0]` and fall back to
`tankbroadcast.INSTANCE_NAMES` only when that is `None`. The `--fresh/--grey/...` flags could
resolve through the same table.

Tests: `tests/test_tanknames.py` (9). They pin all three builds and the 13.6 chain addresses, and
check the caption geometry. They also show the answer does not depend on RV-C's table (emptied) or
on any instance constant: renumbering var 0's filter to instance 7 moves FRESH to 7. A broken
`mov r4,r0` names nothing. Stubbing the trace or the caption scan makes the pinned table fail.

## Finding 79 — the header's ⚠ triangle is always drawn and always tappable; slot 234 only picks its colour — `proven-from-code` plus `observed-on-hardware` (v47), 2026-10-06

**The question (owner, 2026-10-06).** On the coach, tapping about ½ in left of the clock opens
NETWORK DIAGNOSTICS even though no triangle is showing there. Is that something a build added?

**No: it is stock, byte-identical in 13.6 and v47.** The HOME header object **892** (stock)
holds, in order:

| record | what | touch id → screen |
|---|---|---|
| 892[0] wt6 at x 570, `value_slot` 234, 2 arms from object 523 | the triangle (font 23 char `'9'`, 32×36) | — |
| 892[1] wt12, flow-relative x −2, 35×36 (screen 568-603 × 0-36) | the triangle's touch region | `0x832` → **11** NETWORK DIAGNOSTICS |
| 892[2] wt6 at (772,26), slot 263, arms from 540 | the clock | — |
| 892[3] wt12 at x 800−148, 148×36 (screen 652-800 × 0-36) | the clock's touch region | `0x7D4` → **4** SETTINGS |

The touch region **892[1] is a sibling of the switch, not inside an arm**, so it exists whatever
slot 234 holds. Its leaf has no condition: `movw r3,#0x832 / cmp r2,r3 / beq.w 0x24C8A6` at
`0x24BAFC`, and `0x24C8A6` is `movs r3,#0xb / strb.w r3,[r4,#0x20a]`.

**Both arms draw the triangle; only the colour differs.** Arm 523 (slot 234 = 0) inks it in
mode-6 index 15 = `(190,30,45)`, the header bar's own red, over a fillB of the same index, so the
glyph is drawn but invisible. Arm 524 (slot 234 = 1) inks it in index 19 = `(242,251,0)`, yellow.

**Slot 234's only writer** (`slot-source`: cell `0x200399A2`, stores at `0x24E8CA`, `0x24E8E2`,
`0x24E8EA`), in the per-tick routine: it is set to 1 (`0x24E8C6`) only when the byte at
`0x20039CCC` is non-zero (incremented at `0x24E888` on each pass where `0x2003A6CA` is 0), the
timer word `0x20039880` is clear or has expired against `state+0x204`, the timer word
`0x200398BC` is set and has expired, and the byte at `0x20039A1D` is not 1; every other path
stores 0. *Not established:* what these timers and flags stand for (inference, unverified: a
network-health timeout, which fits where the tap leads).

**Renderer residual.** `cli render` draws this triangle white with no slot override, which
matches neither arm; read the header triangle in a render as "present", not as its colour.

## Finding 78 — a number readout fills its background across its MEASURED text, and the measuring class it uses varies at runtime — `proven-from-code` (the fill, the class byte, its writer and reset) plus `observed-on-hardware` (v37), 2026-09-28

The v37 LEVEL page drew each amber tile's up arrow as a 6 px fragment ("◢", its leftmost six
columns, cut sharply at x0+14). The arrow is drawn correctly; the layers readout drawn after it
in the same arm paints over it.

**The fill.** `FUN_002557C0` (the wt-0/wt-8 drawer) measures the formatted text with
`FUN_002518F8`, anchors it at the pen by the header align, then `FUN_00251C10` stores exactly
that extent at render `+0x6C04..0x6C0A` (widened by 1 px per side only under the style bits at
`+0x0B`) and `FUN_0025572C` fills it with the background colour. The formatter always emits
`width` characters: justified with spaces, or `width` dashes when the value is wider (Finding 65;
`render.format_cell_value`, the width-dash loop at `0x002529A4`).

**The class.** `FUN_002518F8`/`FUN_002517AC` measure by a character class read from render
`+0x0A` (both drawers are called with index 0: `movs r2,#0` at `0x256E38`, `0x256EA8`,
`0x256F0C`, `0x256FB2`). `FUN_00251AA0` turns it into per-character flags at `+0x6C20`. Class 1:
every character advances `font[0x06]`. Class 2: kerned on the glyph ink profiles. Class 3:
digits, space and `+ - * / =` advance `font[0x11] + font[0x12]`, anything else kerned.
- **Writer:** `0x253228`, `strb r0,[r4,#0xa]` with `r0 = render[+0x7030] & 3`, the current
  record's header byte 4, only when non-zero and a flag gate (`+0x6BB4 & 0x2961` or
  `+0x6BB8 & 0x6E80`) passes. So the class persists until a record sets another.
- **Reset:** `0x258716`, in `FUN_00258650` (every screen paint): entries 0 and 1 get `+0x0A := 2`.
- No record on the LEVEL page sets it (every header byte 4 there has low bits 0), and the stock
  records that do (objects 1229/1230, 1237-1241) are on other screens.

**What the coach shows, and what is not yet explained.** The cut at x0+14 fits only class 1: font
17's `font[0x06]` is 25, so the two-character field " 7" measures 50 and fills
`x0+63-49 .. x0+63`. It is the same on the "7", "6" and "1" tiles, where a kerned measure would
differ. The pitch and roll values (field 5) show a stray whole dash ("- 2.2"). The n/a text
"-----" left its first dash where the value's narrower fill did not reach. A kerned measure would
leave a 3 px sliver ("-----" 57 px against "  2.2" 54). Class 3 leaves a whole 9 px dash (85
against 76), which is what the photo shows. So the readouts draw under more than one class at
runtime, and **which path changes the class between a paint's reset and a
live redraw is not found**. Stated as open, not fitted.

**Correction, 2026-09-28 (v38 on the coach): the fill is NOT what cuts the arrow.** v38 bounds the
layers fill at x0+39 in every class, and the arrow is still its leftmost six columns, cut at
x0+13, exactly as in v37. The fill rule and its guard stand on the code, but they do not explain
the arrow. What does is open. The blitter clips only horizontally, to `+0x864A..0x864E`, which
only `FUN_00250ED0` writes, reached only through `FUN_00253E38`/`E18`/`EA4`. The text drawer sets
that clip to the record box intersected with the enclosing clip `+0x58..0x5E`. Every writer of
`+0x58..0x5E` is Finding 70's list, and no LEVEL record is wt-7 or wt-14. By the code, then, the
arrow's clip is its own 19×20 box, which holds all 20 columns. v39 (`arrow_probe`) tests it on
the coach. **v39's result:** only an arrow outside the tile arm is whole. Box width and
alignment do not matter, and text right after a capsule is not the trigger (the banners draw).
The suspect is the layers readout's fill after all, at about 50 px whatever the field width.
v40 paints it green to measure it. **v40's result** (`observed-on-hardware`): the fill is the
cause. Both tile readouts filled about 50 px left of their pens, at fields 1 and 3, which is not
what any class here computes. Drawing the arrow after the readouts showed it whole.
`readoutfill` now treats a tile readout's fill as reaching the tile's left edge. What sets the
50 px is open. The stray dash did not appear in the v38 photo either, though v38 changed nothing it
touches: also open.

**The rule this leaves, and its guard.** Lay a readout out for its widest fill in any class:
`field * max(font[0x06], font[0x11] + font[0x12], widest kerned number glyph)`
(`metrics.worst_fill_width`). `readoutfill.conflicts` walks an object in record order and fails
any readout whose widest fill covers an earlier label or glyph, or leaves the capsule it sits in.
It flags v37's four arrows and four lift fields and passes v38 (`fill_safe`: layers field 1,
lift 3). The stray dash is not guarded: equal character counts do not give equal extents outside
class 1, and no layout choice removes that.

## Finding 77 — the five DM_RV filter variables are write-only; DM_RV reaches the screen through three source-keyed parsers, one a solar-controller branch in `FUN_00240D20` — `proven-from-code`, a defensible negative, 2026-09-25

**The question.** The owner built an RV-C leveling sensor (`../level-sensor`) that broadcasts
DM_RV (`0x1FECA`) about once a second. The receive-filter table is blind to the sender
(`MCF-FORMAT.md` §5.4a), so every DM_RV on the bus lands in the panel's DM_RV variables and the
most recent frame wins. What reads those variables?

**The five variables.** Decoded from each entry's bytes `+2`/`+3` (§5.4a). None of them has an
instance filter (`+4 = 0xFF`).

| var | slot | extracts | DM_RV field | refresh class |
|---|---|---|---|---|
| 235 | `0x2000819E` | `payload[1]` | DSA | 2 (30 s) |
| 162 | `0x2000810C` | `payload[2]` | SPN high byte | 3 (never stale) |
| 163 | `0x2000810E` | `payload[3]` | SPN middle byte | 3 |
| 164 | `0x20008110` | `payload[4]` | SPN low bits and FMI | 3 |
| 277 | `0x2000822A` | nothing | mode 0: an inert entry (§5.4a) | — |

**Nothing reads the four live slots, and nothing could read the fifth** (277 is never written).
There are five ways a read could happen, and each is closed:

1. **The LCD Firmware.** It writes the slot array from three functions and reads it from none
   (`MCF-FORMAT.md` §5.4a, "exactly three writers and no reader").
2. **GuiCode, by base plus offset.** The array reaches GuiCode only as `GuiCode_entry1`'s `r0`
   (Finding 33). The slot offsets from that base are `+0x144`, `+0x146`, `+0x148`, `+0x1D6` and
   `+0x262`. A slot that far in is out of reach of any narrow Thumb load (the largest immediate is
   124 bytes, for `ldr`), so the read would have to be one of the wide loads (`ldr.w`, `ldrh.w`,
   `ldrsh.w`, `ldrb.w`, `ldrsb.w`, or `ldrd`). A byte scan of the whole section at every
   halfword, which does not depend on disassembly staying in sync, finds **no non-PC wide load at
   any of those offsets**, in stock 13.6 or in v32. The scan's positive control is var 138:
   `FUN_00247BF8` reads its slot with `ldrsh.w r6,[r4,#0x114]` at `0x2484D2`, and the scan finds
   that load.
3. **The functions the base is passed to.** `FUN_00247BF8` hands `r4` on to `FUN_00242B70`,
   `FUN_0024F104` (and on to `FUN_0024F00C`), `FUN_00250984` (and on to `FUN_0024F820`),
   `FUN_00250CF0` and `FUN_00259100`. Decompiled, they touch only the struct fields past the
   low slot array (`+0x208`, `+0x20A`, `+0x20B`, `+0x214`, `+0x220`, `+0x222`), never a slot.
   Item 2's scan covers them as well.
4. **An absolute address.** Neither stock nor v32 holds a literal for any of the five slot
   addresses. v32's appended routines read nine other slots.
5. **A widget cell or an indexed read.** None of the 665 cell-table entries points into
   `[0x20007EBC, 0x20008400)`. None of the 26 register-indexed halfword loads indexes the array
   (Finding 32).

So **the DM_RV subscriptions are write-only.** A DM_RV from any node, the leveling sensor
included, changes those slots and nothing else.

**DM_RV is used, but by frame parsers.** The LCD hands every received frame to `GuiCode_entry4`
(§5.4a; Finding 75), and three parsers there read DM_RV straight from the frame. **Each one keys
on the source address:**

| parser | accepts DM_RV from | drives |
|---|---|---|
| `FUN_0024FCE8` | address `0x80` only, fixed in the code (the Wakespeed regulator) | ALTERNATOR SYSTEM |
| `FUN_0024F1FC` | an inverter adopted earlier through `1FFD4`, and only when `payload[1] == 'B'` | `Inverter Fault` (Finding 48) |
| `FUN_00240D20` | one of five adopted **solar-controller** addresses, when `payload[3]` equals that controller's instance or `0xFF` | a per-controller fault code, 1–9 |

**The third parser is new.** `FUN_00240D20` dispatches on
`canid & 0x03FFFF00` (mask `DAT_0024103C`) against 17 literals at `0x241040`–`0x241080`:
`1FDFF`, `1FEB3`, `1FEB2`, `1FEAF`, `1FEAE`, `1FE85`–`1FE80`, `1FDFE`, `1FDFC`, `1FDFA`,
`0EBFF`, **`1FECA`** and `1FED8`. So it is a solar-controller parser for 14 of those DGNs, not
the `0x1FDFF`-only parser Finding 75 described.
- **Where DM_RV is tested.** The comparison sits at `0x240E12`: `ldr r7,[pc,#0x268]` loads the
  literal at `0x24107C` (`0x01FECA00`), then `cmp r2,r7` and `beq.w 0x241C8C`.
- **Matching the sender.** The branch compares the source address, in order, with the bytes at
  `0x20039879`, `0x20039870`, `0x20039872`, `0x20039871` and `0x20039878` (literals `0x241D3C`,
  `0x241D44`, `0x241D48`, `0x241D4C`, `0x24216C`). Those bytes are the controllers' adopted
  addresses. They are **zeroed at reset**: `movs r3,#0` at `0x2409BE`, then
  `ldr r1,[pc]` / `strb r3,[r1]` at `0x240A7E`–`0x240A90`. Only the solar DGN arms write them
  again, storing `(byte)*param_1`, the frame's source address, when the frame's instance matches
  a configured controller instance (`0x241108`–`0x241A7C`, for example `strb r2,[r3]` at
  `0x241108`).
- **The fault code.** For controller *k* the branch decodes `payload[2]` (1 or 2) and the two
  nibbles of `payload[4]` into a code from 1 to 9 (7 is never produced; anything else gives 0).
  It stores the code as a byte at `0x20030068 + k` (`strb` sites `0x241CE6`–`0x241FC4`, for
  example `movs r2,#9` / `strb r2,[r3,#2]` at `0x241F58`). Which widget shows these bytes is not
  traced here.

The frame layout the byte reads rest on is anchored by the inverter parser: its hardware-confirmed
`payload[1] == 'B'` test is `ldrb r4,[r0,#5]` / `cmp r4,#0x42` at `0x24F392`, so `payload[0]` is
at `+4`.

**For the leveling sensor**, the negative is complete. It claims an address in `0xB0`–`0xBF`,
so it is never `0x80`. It sends neither `1FFD4` nor any solar DGN, so neither the inverter parser
nor the solar parser ever adopts it. Its DSA (`payload[1]`) is 81, not `'B'`. And the four
filter variables it does overwrite are read by nothing.

**Corrections this makes.**
- **Finding 75**'s scope: see its correction note.
- **`BUS-BASELINE.md` PC.20** gave a reason for not injecting DM_RV: "vars 162-164/235 … the
  alternator drives them — a synthetic fault would have landed on the ALTERNATOR SYSTEM page".
  That is wrong on both counts. Every DM_RV sender writes those variables and nothing reads
  them, and the alternator page takes its data from the frame, from address `0x80` only.
  Finding 48 had already found the second half.

**Residual, stated.** Two gaps remain. A load from a base register that was computed first
(`add rX,r4,#imm` and then a narrow load) would not match an offset of `+0x144`. The sweep of
`FUN_00247BF8` found no such derivation of `r4`, and the decompiled callees have none. A read
through the slot array's address stored somewhere and then reloaded is excluded by item 4 and by
Finding 32's search for the struct base. Pinned by `tests/test_dmrv_subscriptions.py`: the
entries, the negative with its positive control on stock and v32, the dispatch literals, the
address-table reset, and the frame-layout anchor.

## Finding 74 — widget type 0x13 is a fill GATED on the transparent-interior bit, and every one in the corpus is gated OFF — `proven-from-code`, a defensible negative, 2026-09-06

The last entry on `RENDER.md`'s render-coverage punch list, and it closes as a negative. Case
`0x13` normalises the placed box and then

```c
if (uVar14 == 0) FUN_0025572C(x0, y0, x1, y1, uVar18);      // 0x25778C
```

where `uVar14 = (*state & 3) >> 1` — bit 1 of the record flags, the **transparent-interior** bit
`records.WRecord` exposes as `hollow`, and the same one the wtype-3 drawer passes to
`FUN_00255E90` (which is why the CLIMATE frame renders as an outline against its photo oracle).
`uVar18` is the persistent **fillB** register, not fillA.

**All 27 wtype-19 records across 11.23 / 13.6 / 14.7 set that bit** (3 / 20 / 4), so the firmware
never fills one. The renderer omitting the single placed record is *correct*, and
`tests/test_render_bytes.py::test_wtype_19_is_correctly_invisible` pins the premise so a build
carrying a non-hollow one fails rather than silently disagreeing with the panel.

Two names in `records.WTYPES` were wrong and are corrected with it: **`0x02` was "rect"** and is a
line (Finding 72), and **`0x04` was "gated_fill"** though case 4 carries no per-record gate at all —
only the global draw-enable at `state+0x704C` that every case tests. `0x13` keeps the name because
it is the one that really is gated.

---

## Finding 76 — the remaining dark GuiCode helpers, swept and categorised — `proven-from-code`, 2026-09-10

After Findings 73 (`FUN_00254F26`) and 75 (`FUN_00240D20`) the section-interior survey's dark
GuiCode set is down to **79 functions totalling 8.6 KB, none over 634 bytes**. Decompiling the
ten largest shows they fall into four categories, none an unexplained subsystem — the GuiCode
frontier is now understood at the category level, not just by size.

* **Compiler soft-float runtime (library).** `FUN_00240078` (single), `FUN_002403E8` and
  `FUN_0024063C` (double), with helpers `FUN_002405C4` / `FUN_002407AA`: the classic libgcc/AEABI
  shape — a 64-bit operand assembled as `CONCAT44(hi,lo)`, the exponent read as `>>0x14 & 0x7ff`,
  NaN/Inf tested against `0x7ff` and zero against `& 0x7fffffff`. GuiCode links a soft-double
  library (it has no FPU path here); these are its `__aeabi_d*`/`f*` routines, which is why they
  were dark — vendor toolchain code, not Firefly's.
* **State init / reset.** `FUN_002409BC` zeroes a long list of state globals (called from the
  touch main `FUN_00247BF8`); `FUN_00250320` clears a large state block when a counter passes 10
  (a reset guard); `FUN_0025063C` is a per-tick state **mirror** — it copies source cells to
  display cells behind a reload-at-`-1` timer and picks one of three constants by a `<0x1a`/`<0x33`
  threshold (an animation/among-N selector).
* **Render / layout helpers.** `FUN_00251CA8` computes a widget's rendered **content extent**,
  writing the `state+0x6c04`/`+0x6c06` cells (the same extent family as Finding 14's
  `+0x6c12..+0x6c18`) with the align byte at record `+9`; `FUN_002568A0` walks a 120-entry table
  of 224-byte object-property records (`DAT_00256A04`, beside the `+0x44BC8` array reader
  `FUN_00256868`) testing per-object flag bits.
* **Touch / CAN command path.** `FUN_0024F820` is an action-dispatcher leaf keyed on the touch id
  `state+0x208` (ids `0x5209`/`0x520A` → writes the screen id `state+0x20A` and sends);
  `FUN_0024F6D8` is a transmit helper that builds a priority-6 frame
  (`(dgn & 0x3ffff) << 8 | 0x18000000`, from a per-control table) and hands it to the transmit
  callback — the same TX path as Finding 7.

**The sweep was carried down, not assumed.** The next tier of ~18 (down to ~90 bytes) was
decompiled too, and every one lands in these categories: the largest, `FUN_00242174` (2022 B), is a
**config struct-explode** — it copies ~100 fields from a source struct (`DAT_0024238C`, offsets
`+0x15`…`+0x42`…) into individual state globals, the bulk-unpack counterpart to `FUN_00240D20`'s
scaled parse; the rest are hit-region / object-tree helpers over the 120-entry `×0xe0` table
(`FUN_0025634C`/`FUN_00256634`/`FUN_00258550`/`FUN_002584E8`, keyed on `state+0x6bbc`…), text/extent
draw helpers (`FUN_00253F8C` origin-offset, `FUN_00251C10` extent), touch-path leaves
(`FUN_0024F104`/`FUN_00250CF0`, `state+0x208`), a screen-setup step (`FUN_00258A1C` from
`GuiCode_entry1`/`entry3`), more soft-float (`FUN_0024081C`, the double-NaN test), and clamp/scale
math (`FUN_00250ED0`, `FUN_00252BC8`). None is an undecoded GUI mechanism.

The ~50 still-smaller dark functions (under ~90 bytes) are the same leaf kinds: soft-float, tiny
field accessors, and short state/geometry helpers. **What this settles:** GuiCode holds no undecoded
routine of consequence — the GUI, render and RV-C logic is all named (Findings 9-76), and the entire
dark residue is toolchain runtime plus small plumbing helpers, checked down the size curve rather
than inferred from the top. Pinned by
`tests/test_blob_crosschecks.py::test_guicode_links_a_soft_double_library`.

## Finding 75 — `GuiCode_entry4` is the RV-C receive path, and `FUN_00240D20` its largest status parser — `proven-from-code`, 2026-09-10

`FUN_00240D20` was the second-largest dark GuiCode function (4904 bytes;
`SECTION-INTERIOR-SURVEY.md`). Placing it also places its caller, and the caller is a whole
subsystem: **`GuiCode_entry4` (`0x00247800`) is the panel's received-CAN-frame handler** — the
RV-C receive path in GuiCode, the counterpart to the transmit path Finding 7 found.

**`GuiCode_entry4`.** It takes a pointer to a received frame (`param_1[0]` = the 29-bit CAN id,
`param_1[1]` = the first data word, payload bytes at `+5..+0x0b`). It extracts the PGN as
`(*param_1 & 0x3ffffff) >> 8` and dispatches on it: the main arm is **DGN `0x1FEDA`**
(`DAT_00247a4c`), the G12's per-channel indicator STATUS broadcast (`BUS-BASELINE.md`,
`19FEDA9C#…`), and on a change in the frame's lock/instance bytes it **emits** command frames
(`0x19EE…` built into a local buffer and handed to the transmit callback `*DAT_00247a5c`). It
also builds the firmware-version string into SRAM `0x20030190` from the `0xFF01xx` product DGN.
Before the dispatch it runs three per-frame parsers unconditionally: `FUN_0024FCE8` (the
ALTERNATOR SYSTEM / Wakespeed parser, source `0x80`, already named in Finding-era notes),
**`FUN_00240D20`**, and `FUN_0024F1FC`. `GuiCode_entry4` has no in-image caller — it is an
exported entry the LCD Firmware calls, like `GuiCode_entry0..3`.

**`FUN_00240D20`.** A received-**status** parser for one DGN. It gates on
`(*param_1 & 0x03ffff00) == 0x01fdff00` — **DGN `0x1FDFF`** — then branches on the frame's
**source address** (the id's low byte, matched against configured addresses at `0x20039879`…)
and on an **instance** byte (`param_1[1]`, a `switch` over cases 1-10), writing the payload into
a per-instance SRAM state block at **`0x20030068`** at fixed field offsets (`+0x4e/+0x54/+0x5a/…`
for the u16 group, `+8..+0x14` for the instance bytes). The values are converted in RV-C
engineering units on the way in: `>> 1` for the half-unit fields, and a fixed-point
multiply by `0x66666667` (the reciprocal-multiply constant for ÷10) for others. Those state
cells are what the variable table and the renderer read to paint the live readouts. Ghidra
renders the function as a wall of identical `uVarN = *puVar1` loads — a decompiler artefact of
the instance `switch`'s jump table, not real work.

**Scope.** This *places* the subsystem and pins its two dispatch DGNs and its state block; it does
not map every field of DGN `0x1FDFF` to its readout (a larger follow-up). What it settles: the
panel's inbound RV-C decoding lives in `GuiCode_entry4` + these three parsers, and `FUN_00240D20`
is the DGN-`0x1FDFF` status ingest, not display or layout code. Pinned by
`tests/test_blob_crosschecks.py::test_entry4_and_its_status_parser_gate_on_their_dgns`.

*Corrected 2026-09-25 (Finding 77).* `FUN_00240D20` is **not** a single-DGN parser.
`0x1FDFF` is only the first of 17 dispatch literals at `0x241040`–`0x241080`. It handles the
solar-controller family (`1FEB3`, `1FEB2`, `1FEAF`, `1FEAE`, `1FE80`–`1FE85`, `1FDFA`, `1FDFC`,
`1FDFE`, `1FDFF`), plus `0EBFF`, `1FED8` and DM_RV `1FECA`. The "configured addresses at
`0x20039879`…" are **adopted** addresses: they are zeroed at reset and written with the sender's
address by the solar DGN arms. Its DM_RV branch turns a solar controller's diagnostic into a fault
code at `0x20030068 + k`.

## Finding 73 — the rounded-shape primitive `FUN_00254F26`, the corner radius (`+0x32`) and the octant mask (`+0x8b`) — `proven-from-code`, 2026-09-10

`FUN_00254F26` was the largest undocumented GuiCode function (4904 bytes; `SECTION-INTERIOR-SURVEY.md`).
It is the **circle / arc rasteriser**, and decoding it pins two record fields the renderer reads
for every rounded widget.

**The primitive.** `FUN_00254F26(cx, cy, r, colour, e0..e7, thick)` draws a circle of radius `r`
about `(cx, cy)` with **8-way octant symmetry**: it walks `x` and derives `y = √(r² − x²)` in
1/8-subpixel fixed point (the `√` is `FUN_00252B24`, a Heron integer sqrt; the `<<3`/`+4>>3` are the
sub-pixel round), then plots the eight mirror points `(±x,±y)`/`(±y,±x)` — each gated by its own
enable flag `e0..e7`, so a caller draws a full circle (all eight) or just the octants of one
rounded corner. `thick` doubles the adjacent pixel for a 2 px stroke. Its plot helper
`FUN_00254040(x,y)` adds the frame origin (`state+0x6be0/0x6be2`) before calling the pixel writer
`FUN_00250F8C`. The straight-edge helpers beside it are `FUN_00254064(x1,y1,x2,y2,c)` (a line),
with `FUN_002544F0` = horizontal line and `FUN_00254506` = vertical line; `FUN_0025451A` is the
quadrant wrapper that turns four corner flags into octant enables.

**Who calls it.** The widget renderer `FUN_00256C54` uses these for its rounded shapes:

| wtype | shape | how it is drawn |
|---|---|---|
| `0x10` | circle / dot | `FUN_00254F26` **directly**, with the octant mask from `state+0x8b` (`0xff` = full circle, a bit subset = an arc) and radius from `state+0x32` |
| `0x20` | rounded rectangle | four corner arcs (`FUN_0025451A`) + four edges (h/v-line) + an interior fill (`FUN_0025572C`), radius `state+0x32` |
| `0x21` | capsule | same, with the radius reaching the half-height so the ends are round |
| `0x03` | button body | the rounded button frame |

**The two format fields, `proven-from-code`.**

* **Corner radius = geometry slot 4 (`state+0x32`).** The six geometry u16s seed `state+0x2a..0x34`;
  `+0x2a..0x30` are the box and **`+0x32` is the corner radius** the cases above read as `sVar9`.
  It was already decoded as `records.WRecord.geom[4]` but read as a coordinate; `WRecord.corner_radius`
  now names it. On 13.6 the circle carries 12, the sampled rounded rectangle 10, the capsule 5.
* **Octant mask = `state+0x8b`.** `FUN_00252CB0` writes it from the **R43** byte (line 700,
  `+0x8b = FUN_0025211c(cursor)`, gate `aed & 0x00030008` — types 3 / `0x10` / `0x11`; a second
  writer at gate `aee & 0xc` covers `0x22`/`0x23`). `records.py` consumed this byte to stay
  aligned but discarded it; it is now surfaced as `WRecord.corner_mask`. The `0x10` circle carries
  `0xff` (all octants → a full circle).

**Render.** The decoded compositor `render.screen_placements` **already draws these at the real
radius** — the 0x20 well, the 0x21 capsule, the tank/battery fill and the 0x10 circle all use
`max(0, coord4)`, pinned by `test_render.py` (`fill.radius == level.coord4`). The only place that
had hardcoded a radius was `decode_widget`, the legacy layer-1 / compositor-B path (the
Starlink-button unit tests), which was wired to `WRecord.corner_radius` here too, so no fixed radius
remains in `render.py`. The circle's **octant mask** (`corner_mask`) is decoded but not yet consumed
by the renderer — every on-screen `0x10` carries `0xff` (a full circle), so no partial arc is
exercised; honouring a subset is a follow-up if a build ever uses one.

---

## Finding 72 — widget type 2 is a 1 px LINE, and every one of them was invisible in the render — `proven-from-code`, 2026-09-06

The other half of Finding 71's punch list, and the reason its first draft was wrong. That entry
said zero-extent boxes are dropped because they are "shapes that never draw"; the histogram it
quoted contained `2: 13`, and wtype 2 is a line — for which a zero extent is the *correct* shape.

### The drawer

`FUN_00256C54` case 2 takes the placed box, and picks one of two routines on the record's flag
bit 2:

```c
if ((flags & 4) == 0) FUN_00254064(x0, y0, x1, y1, colour);      // 0x2530C4
else                  FUN_00254350(..., state+0x92);             // the styled variant
```

`FUN_00254064` translates the endpoints by the drawing origin and branches three ways:

| condition | routine | shape |
|---|---|---|
| `x0 == x1` | `FUN_00251058(x, y0, y1, c)` | vertical span, **inclusive** of both ends |
| `y0 == y1` | `FUN_00250FF4(x0, x1, y, c)` | horizontal span, likewise |
| otherwise | a fixed-point walk: `slope = abs(dy)*10000 / abs(dx)`, then run-length spans along the major axis (`FUN_00250F8C`, a single pixel, when a run is length 1) | a sloped line |

Every branch is **one pixel wide** — there is no thickness parameter anywhere in it, so a render
has nothing to invent. The colour is `uVar17`, the same persistent fill register wtype 4 uses
(`state+0x64`, or `+0x6C` in the selected state), so a mode-0 `fillA` inherits exactly as it does
there.

### What they are

43 / 31 / 36 wtype-2 records in 13.6 / 11.23 / 14.7; 17 of 13.6's are placed on a screen. Their
geometry is `(x, y, w, h)` with `w` or `h` absent for an axis-aligned line:

- **FLOORPLAN SELECTION** (root 19) — two 300×0 white rules underlining the `Generator Models`
  and `Li-AES Models` column headings.
- **Roots 27 / 28** — five white verticals (0×36, 0×83) splitting columns.
- **SLIDES/AWNINGS** (root 32) — **four 45° diagonals of ±80**, `fillA` mode 6 → grey
  `(128,130,133)`. They are the **levelling diamond**: the outline joining the FRONT / LEFT /
  RIGHT / REAR corner controls. The whole diamond was missing from the render.
- **Roots 37 / 42** — three `GO POWER!` rules in mode-7 index 3, the page's own background colour,
  so drawing them changes nothing. Worth knowing before reading a diff.

### The limit, stated

**Every wtype-2 record in all three builds is axis-aligned or exactly 45°** (39+4, 27+4, 36+0).
At 45° the fixed-point walk emits one pixel per step, which is what Bresenham draws — so
`render.py` drawing a straight line agrees with the firmware pixel-for-pixel on everything in the
corpus, and the general run-length walk did not need transcribing. A record at any other slope
would need it, and `tests/test_render_bytes.py::test_no_wtype_2_record_needs_the_general_slope_walk`
fails rather than rendering approximately if one ever appears.

The **styled** variant is unexercised on the same evidence: header byte 0's style nibble
(`& 0x3C`) is zero on all 110 wtype-2 records across the three builds, so `FUN_00254350` and
whatever `state+0x92` parameterises are undecoded and unreached.

### Blast radius

Four screens change (19, 27, 28, 32). **No photo-oracle page carries a wtype-2 record**, so all of
them render byte-identical; the only named page that moves is `floorplan`, which has no photo.

---

## Finding 71 — widget type 4 is a FILLED RECTANGLE, and eleven of them draw the `Absorb`/`Float` triangle — `proven-from-code`, 2026-09-06

`RENDER.md` carried a fidelity gap since 2026-09-03: on `INVERTER / CHARGER SETTINGS` (root 17)
with slot 23 = 1 the coach draws a small upward triangle on the divider above the `Absorb`/`Float`
rows, and the render drew the divider and omitted the triangle. It was logged against "the shape
path" without saying which record or why. Both are now read out.

### The drawer

`FUN_00256C54` case 4 is four lines. It takes the record's placed box (`+0x2A`-`+0x30`), normalises
each axis with `FUN_00250E12` (a plain swap when `hi < lo`), and calls the fill primitive:

```c
FUN_0025572C(x0, y0, x1, y1, colour);      // 0x2573D2
```

`FUN_0025572C` translates by the drawing origin (`+0x6BE0`/`+0x6BE2`), normalises again, clip-tests
(`FUN_00250E2C`), and then

```c
for (y = y0; y <= y1; y++) hline(x0, x1, y, colour);     // 0x255770
```

so it is a **filled rectangle, inclusive on both axes** — a box with `y0 == y1` paints exactly one
row. It is the same primitive `FUN_002559A0` uses for a text background when the record's `& 4`
flag is set, which is a second, independent sighting of it as a solid fill.

The colour is the persistent fill register `state+0x64` (`+0x6C` instead when the selected-state
flag at `+0x704F` is set), seeded to `0x00322B42` by `FUN_00258650` at screen setup. **A mode-0
fillA leaves it alone**: the fillA mode switch in the record decoder (`FUN_00252CB0` @ `0x25337C`)
has cases 1, 2, 3, 4, 6 and 7 and **no case 0**, so the register simply keeps what the previous
record left in it. That is the same persistence Finding 29 already recorded, confirmed here at
the instruction that would have had to exist for it to be otherwise.

Counts: 457 / 599 / 433 wtype-4 records in 11.23 / 13.6 / 14.7. They are the panel's hairlines and
its rasterised shapes.

### The triangle

Object 303, records 3-13 — eleven wtype-4 records:

| record | geom `(x, y, w, h)` | fillA |
|---|---|---|
| 3 | `(407, -3, 21, 1)` | mode 7, **index 161** |
| 4-13 | `(-, -1, 19 … 1, 1)` | mode 0 — inherits 161 |

Each is one row, two pixels narrower than the one below it and one row higher, so the eleven stack
into an upward triangle 21 px wide at the base and 1 px at the apex. Placed, they run
`(717,370)-(738,371)` up to `(727,360)-(728,361)`, centred on x ≈ 727.5 and sitting on the divider
line — which record 1 of the same object drew in the very same colour 161. It is a marker pointing
at the row below, drawn the only way this format can draw a diagonal.

### Why the render missed it

Nothing to do with the shape path. `render.py`'s generic box loop opened with

```python
if not (2 < gw <= PANEL_W and 2 < gh <= PANEL_H):
    continue
```

a **2 px floor with nothing behind it** — the loop above proves the firmware paints a single row.
It dropped every hairline in the corpus: the eleven triangle bars, the 710×1 rules under the
`GO POWER!` headers (roots 36/37/41/42), and root 23's 1×80 column divider inside the alternator
two-column box. Relaxed to `0 <`, and the change is contained: of 46 screens exactly those five
plus root 17 change, and all eight photo-oracle pages render **byte-identical**.

**Zero is still dropped, deliberately.** 484 records in 13.6 have a zero extent on an axis; all but
one are wtype 0/8/9/16 — value, directive and flow-anchor shapes that never draw — with a single
wtype-33 and **no** wtype-4 among them. The firmware's rectangle is inclusive and would paint a row
for each; painting them here would invent content, so this module's exclusive convention stands at
that line rather than being decided there.

---

## Finding 70 — the enclosing clip: the complete writer set, and why it never narrows anything in this corpus — `proven-from-code`, a defensible negative, 2026-09-06

Finding 69 left one thing open: the text drawer installs `record box ∩ enclosing clip`, and
`render._text_clip` modelled only the first term. This is the second term, read out of the code.

**Where it lives.** The live clip is four inclusive `s16` at render-state `0x200301B0 + 0x58`
(`x0`), `+0x5A` (`y0`), `+0x5C` (`x1`), `+0x5E` (`y1`). A saved outer copy sits at
`+0x8652..0x8658` and the screen rectangle at `+0x8642..0x8648`.

**The complete writer set.** Every `str*` in GuiCode at displacement `0x58`/`0x5A`/`0x5C`/`0x5E`
is 28 instructions in seven groups of four. Two groups (`0x240F74`, `0x243838`) write some other
structure: the render-state pointer literal `0x200301B0` occurs 87 times in GuiCode and **never
below `0x250000`**, so no instruction down there can be addressing this rectangle. The remaining
five groups are:

| site | function | what it does |
|---|---|---|
| `0x258C84`-`0x258C9E` | `FUN_00258C60` | screen rect `+0x8642..0x8648` := **`(0, 0, 799, 479)`**. One caller (`0x00247A90`, GUI init), and the **only** writer of that rect in the image |
| `0x2587AA`-`0x2587C8` | `FUN_00258650` | every screen paint: live clip := screen rect, and a copy to the saved outer `+0x8652..0x8658` |
| `0x253EB0`-`0x253ECE` | `FUN_00253EA4(0)` | restore: live := saved outer |
| `0x253F0C`-`0x253F4E` | `FUN_00253EA4(n≠0)` | normalise the live rect, then clamp it into the saved outer |
| `0x257A0E` / `0x257A7C` | `FUN_00256C54` cases **7** and **0x0E** | **the only place a record narrows it**: live := that record's own placed box `+0x2A..0x30`, gated on record flag bit `0x200`, then `FUN_00253EA4(1)` |

Case `0x0E` also overwrites the saved outer (`0x257A2E`-`0x257A40`), so a wtype-14 record
establishes a new outer for everything walked after it. The renderer's `switch` is on the record
wtype at state `+0x8A` and runs `0`-`0x2F`, so these are wtypes **7** and **14**.

`FUN_00258650`'s fast path corroborates the screen rect: it takes it only when `x0 == 0`,
`(y0 | x1<<16) == 0x031F0000` and `y1 == 0x1DF` — exactly 800×480 — and then calls
`FUN_00253E18` (clipping off) instead of `FUN_00253E38`.

**The negative.** Nothing in the corpus is a wtype-7 or wtype-14 record. Every object in all
three builds decodes with zero derailments (1201 / 1422 / 1077 objects → 6109 / 8272 / 5311
records), and the wtype set is *identical* in all three:

```
{0, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 16, 19, 32, 33}
```

Our own `mcf-experiment-v21.bin` adds none either. So for every text record this project can
render, `box ∩ enclosing` is `box ∩ (0,0,799,479)`, which is `box`. **`render._text_clip`
returning the record's own box is exact, not an approximation** — pinned by
`tests/test_render_bytes.py::test_no_record_in_the_corpus_can_narrow_the_enclosing_clip`, which
fails the day a build (ours or Firefly's) grows one.

**Two corrections this retires.** `RENDER.md` and `_text_clip` both said the clip is installed by
"`FUN_00251BB4`, the wtype-5/6 branch of the composer". Both halves are wrong. `FUN_00251BB4`
never touches the clip: it stores its argument rect at `+0x42..0x48`, sets `+0x89 := 1`, and
**unions** the rect into a separate one at `0x2003A994` (min on `x0`/`y0`, max on `x1`/`y1`) — a
drawn-extent accumulator, called after nearly every record, not a bound on anything. And the clip
belongs to wtypes 7/14, not 5/6.

**One rule read on the way, deliberately not implemented.** The drawer culls a whole line with

```c
if (clip_y0 <= line_y + font_cell_height && line_y <= clip_y1)   // 0x255BFC
```

which is redundant with per-pixel clipping as long as a glyph's ink stays inside
`[line_y, line_y + cell]`, and it is. `render` models the per-pixel bound only.

---

## Finding 69 — `FUN_002559A0`'s line breaking and its clip rectangle — `proven-from-code`, 2026-09-06

The wtype-11/13 text drawer, decompiled from the `guicode136` project. Two halves, both now
transcribed into `tools/` (`metrics.wrap_lines`, `render._text_clip`).

### The break rule

The box width is **inclusive**: `sVar5 = (x1 - x0) + 1`, and a line fits when its measured width is
`<= sVar5` (the compare is `<`, so `box_w + 1` px still fits — Finding 39 already had this).

The scan walks **break opportunities**. Its inner loop advances while all of

```c
i < len-1  &&  s[i] != '\n'  &&  (s[i] == ' ' || s[i+1] != ' ')
                              &&  (s[i] != '-' || s[i+1] == ' ')
```

so it stops at the last character of a chunk, where a chunk ends at: the end of the string, a
`'\n'`, the last non-space before a space, or **a `'-'` not followed by a space**. That last one
is the rule no fitted heuristic here had: `auto-start` may break after the hyphen, while `A - B`
does not break at that dash.

It is greedy with one memory. Measuring from the line start to each opportunity, when a candidate
overflows it falls back to the last opportunity that fitted (`ends[k] != -1`), starts the next line
after it and **skips the spaces between**. If nothing has fitted yet — a single chunk wider than
the box — the line takes that chunk anyway and **overflows**, which the clip below then cuts. That
is why a long unbreakable token appears truncated on the panel rather than broken mid-word.

Hard `'\n'` ends the line at `i-1`. The whole loop stops at **30 lines** (`if (0x1d < count) break`).

Two `if (line_count < 2)` arms inside the loop are unreachable — the count starts at 1 and each arm
runs only after an increment — and are recorded in `metrics.wrap_lines` rather than dropped.

### The clip rectangle

Before drawing any line the drawer installs the record's box intersected with the enclosing clip,
and restores the previous one afterwards:

```c
iVar21 = max(x0, clip_x0);   iVar17 = min(x1, clip_x1)
iVar12 = max(y0, clip_y0);   local_8c8 = min(y1, clip_y1)
FUN_00253e38(iVar21, iVar12, iVar17, local_8c8);      // set
    ... every line, with a per-line vertical skip ...
FUN_00253e38(iVar10, iVar9, iVar22, iVar11);          // restore
```

**The rectangle is inclusive**, which is the whole explanation of the v20 corner-mark defect: a
record whose box collapsed to `x0 == x1` still draws **exactly one pixel column** of its glyph.
That is what the coach showed and what the renderer now reproduces.

**It is specific to this drawer.** A **wtype-0** single character goes through `FUN_002557C0` and is
not bounded this way — HOME's `V` and CLIMATE's `°` have 0×0 placed boxes and draw in full on the
coach. "The panel clips text to its record box" is true for wtype 11/13 and false in general; both
readings were held here at different times before this decompilation settled it.

`render._text_clip` applies **both** bounds. A record whose box collapses therefore draws its
glyph's top-left pixel and nothing else — for v20's timer mark that pixel has coverage 0, so the
panel drew nothing, which is what the coach showed once the photo was measured rather than read
(`docs/RENDER.md`). *The enclosing clip is settled by **Finding 70**: only a wtype-7 or wtype-14
record can narrow it, and the corpus contains neither — so the record's own box IS the whole
rectangle, and this is exact rather than an approximation.*

---

## Finding 68 — `Shore Brkr Size`'s `−` is DISABLED BY DESIGN, on the wrong selector: a per-tick `setVariant(199, 1)` gated on config selector `0x95` — `proven-from-code`, 2026-09-05

`CLAUDE.md` defect 0b has stood since 2026-08-24: the `−` half of object 929 transmits nothing
(PC.32, five presses at stored sizes 0/5/35), while every record, group and binding it needs is
present and correct. The last hypothesis left was the variant counter, and Finding 67's side
result showed it is 0 from cold, which made the silence harder to explain, not easier. This
closes it, and the answer is that the silence is *intended* — just not for this coach.

### The mechanism, every joint cited

Group 199 is `11 80 04 00 76 02 03 00`: byte 1 `0x80` = **externally selected** (§5a of
`CONTROL-BEHAVIOR.md`: the press never cycles its counter, only the setter can), byte 0's low
nibble = 1 (the setter accepts variants 0 and 1, `FUN_0021B5E8` `0x21B606`-`0x21B610`). Its three
bindings all carry variant 0 (byte-2 high nibble; the sender `FUN_0021B8B4` matches
`counter == byte2 >> 4` at `0x21B944`-`0x21B94A`). So **variant 1 is a state with nothing to
send** — the group's "disabled" arm, a shape 74 other `11 80` groups share.

GuiCode's per-tick routine `FUN_00247BF8` (its `r0` is the receive-slot array, PC.55) selects the
arm every tick:

```asm
0024a604  ldrsh.w r3,[r4,#0x1aa]      ; receive slot of var 213 (0x20008172)
0024a608  cmp r3,#0 / bne.w 0024a896  ; non-zero -> setVariant(199, 0)   (enabled)
0024a60e  ldr r3,[0x20039908] / ldr r3,[r3]
0024a612  movs r1,#1
0024a614  movs r0,#0xc7               ; 199
0024a616  blx r3                      ; zero -> setVariant(199, 1)       (DISABLED)
```

`0x24A896`-`0x24A89E` is the other arm, `setVariant(199, 0)`. The setter writes the counter
(`0x21B618`), the press finds no variant-1 binding, and the button is silent. **The button is
disabled exactly while var 213 reads 0.**

### What var 213 is, and why it is always 0 here

Var 213 is `DGN_CONFIG` (`0x15FCE`) selector **`0x95`**, extract mode 1 field 2 — the value byte
of the G12's config sweep frame `195FCE9C#95 00 <lo> <hi> …`. `G12-CODE.md` decodes `0x95`: the
G12 sets it to 1 only when **all eight AES parameters sit at their factory seed values**
(`+0x0C14`-`+0x0CA8`). On a tuned coach it is 0, and it is 0 in all **3,329** `0x95` sweep frames
across every capture. So the `−` is disabled permanently, at every breaker size — exactly PC.32's
observation, including the press at 35 that killed the "floor at 0" reading.

### The one-byte regression, proven by the sibling builds

| build | var 209 | var 213 | the gate | `−` behaviour |
|---|---|---|---|---|
| 11.23 | `0x95` | **`0x99`** (the breaker size) | present, `ldrh.w [r4,#0x1aa]; cbnz` at `0x249D7E` | disabled only at **0 A** |
| 13.6 | `0x95` | **`0x95`** | present, identical shape at `0x24A604` | disabled **always** |
| 14.7 | — | — | **absent**: group 199 keeps `11 80`, nothing ever calls the setter for it | never disabled |

11.23's var table carries `0x95` on var 209 *and* `0x99` on var 213; 13.6's carries `0x95` on
both. The GuiCode gate, the group header and all six bindings are byte-identical between the
two builds. So the intended design was "disable `−` at 0 A" — the floor PC.32 reasoned about
and rejected because the file it rejected it against had lost the byte — and 13.6 duplicated the
wrong selector into the gate's entry. 14.7 fixed it the other way, by dropping the gate.

### Two one-byte fixes, and which is the firmware's own

`variantgates.restore_gate_selector(data, 199, 0x99)` puts 11.23's byte back (GuiData, var
213's compare byte `0x95` → `0x99`); `variantgates.ungate(data, 199)` rewrites `movs r1,#1` →
`movs r1,#0` (GuiCode) so the control follows its variant-0 bindings on every value. The recipe
op `fix_variant_gate` defaults to the first, because it is the design the vendor shipped on
11.23 and keeps the floor: after a `−` echo brings the size to 0 the button disables, and `+`
re-enables it. One consequence worth stating: `0x99` is not in the periodic sweep (ten frames in
all captures, every one an echo of a write), so after a cold boot the slot holds the `0xFFFF`
sentinel (non-zero, PC.60) and the button is enabled until the first echo — at size 0 that means
one `−` press goes out and the G12 clamps it (PC.32's injected −45 read back as 0).

### Predictions for the coach, in order of cost

1. **No flash:** hold `195FCEC0#9500010000000000` on the bus at about 5 Hz (the G12 re-asserts
   `0x95 = 0` in its sweep, so the injection has to outrun it) and press `−` at any non-zero size:
   a `19FED99F#FF96990F FBFF D2FF` frame appears, the first this button has ever sent. Stop the
   injection and it goes silent again within one sweep.
2. **v19** (`fix_variant_gate`): `−` transmits at any non-zero size with nothing injected;
   after a `−` that lands on 0, the next `−` is silent; `+` re-enables it.

`variantgates.gated_variants` is the scan (`tests/test_variantgates.py` pins all three builds);
it is the receive-slot-gated subset of the 85 `setVariant` calls, and the scan's shape — a slot
read, a compare with 0, two arms — is general enough that a future build gating another control
this way is enumerated, not rediscovered. The other 84 calls set variants from config registers
and computed state, not from a raw receive slot, and are outside this finding.

*Residual from the warm-up:* the receive-slot sentinel initialiser was not located. PC.60's
dashes-until-frame and the fenced decode's own `cmp r3,#-1` test both say the slots start at
`0xFFFF`; the store that puts it there is somewhere in the LCD's start-up and is still
`inferred`.
