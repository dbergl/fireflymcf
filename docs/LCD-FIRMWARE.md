# LCD Firmware — first-pass disassembly

**Image:** `LCD Firmware` section of `firefly-13.6/mcf.bin`, 62,920 bytes, base
`0x00210000` (proven in `PARSER-LOCATION.md`, five witnesses).
**Reproduce:**

```
python3 -m fireflymcf.cli extract firefly-13.6/mcf.bin extractions/13.6
./ghidra/run_headless.sh extractions/13.6/00-LCD_Firmware.bin 0x00210000 lcd136
$GHIDRA_HOME/support/analyzeHeadless ghidra/projects lcd136 \
  -process 00-LCD_Firmware.bin -noanalysis -scriptPath ghidra -postScript analyze_lcd.py
```

*The paths above are as recorded in 2026-08. Ghidra 12.1.3 dropped Jython: run `.py` scripts through
the `ghidra/headless` wrapper (PyGhidra), never `support/analyzeHeadless`; see `CLAUDE.md` Commands.*

Output lands in `extractions/lcd-analysis.txt`. **467 functions** were recovered by
auto-analysis, and the reset handler at `0x002104D0` disassembles as a textbook Cortex-M
startup (`ldr.w sp,[0x00210508]` → SP `0x20050000`, then a `.data` copy loop), which
re-confirms the base independently of the earlier evidence.

## Why this image matters

`cli.bin` only *transports* GuiData and GuiCode — it copies payloads verbatim between
seek and flash write. `LCD Firmware` is the image that **consumes** them, so it is where
GuiData's field semantics and GuiCode's bytecode interpreter must live. That makes it the
only route to upgrading GuiData fields from `inferred-from-data` to `proven-from-code`.

## Finding 1 — a blob descriptor table at `0x0021C010`

`proven-from-code` for the layout; the two trailing words per entry are `unknown`.

| Address | Word | Reading |
|---|---|---|
| `0x0021C010` | `0x00240000` | GuiCode destination |
| `0x0021C014` | `0x1A2B3C4D` | GuiCode magic — matches the section's first word |
| `0x0021C018` | `0x2000A24C` | unknown (SRAM address) |
| `0x0021C01C` | `0x0021F2AC` | unknown (inside this image) |
| `0x0021C020` | `0x8F7E6D5C` | GuiData sub-container magic, found at GuiData+`0x10000` |
| `0x0021C024` | `0x90000000` | GuiData destination |
| `0x0021C028` | `0xFA86FA1F` | unknown |
| `0x0021C02C` | `0x1104F8D4` | unknown |

Four words per blob, `{destination, magic, …, …}`. This is the structure that pairs each
payload with the magic used to validate it.

## Finding 2 — `FUN_0021BE50` is the validator

1,190 bytes, and the only function that loads **all three** of GuiCode's destination
(`0x0021BEC6`), GuiCode's magic (`0x0021BEC8`) and the GuiData sub-container magic
(`0x0021BEFC`). It is the natural entry point for anyone continuing this work.

## Finding 3 — the consumers, by constant

| Constant | Loaded in |
|---|---|
| `0x90000000` GuiData | `FUN_00219DC4`, `FUN_0021A078`, `FUN_0021A114` (×5), `FUN_0021A540`, `FUN_0021A738` |
| `0x00240000` GuiCode | `FUN_002170DC`, `FUN_00217150`, `FUN_0021A114`, `FUN_0021BE50`, `FUN_0021E3FC` (×3) |

`FUN_0021A114` is the only function touching both, which makes it the bridge — and the
first place to look for how a GuiData record reaches GuiCode.

## Finding 4 — independent corroboration of the byte-reversal

`FUN_0021A114`'s decompilation is CAN protocol code: it builds a PGN with
`param_2 | 0xEF00` (J1939 proprietary), matches an incoming ID with
`(local_40 << 6) >> 0xE == param_2`, and assembles u32s as

```c
CONCAT31(CONCAT21(CONCAT11(param_1[4], param_1[5]), param_1[6]), param_1[7])
```

— bytes 4,5,6,7 in **reverse order**. That is the same byte-reversed-per-u32 encoding
the format spec inferred from the binding table's stored messages, now seen in the code
that performs it. Two independent derivations, from data and from code, agreeing.

## Finding 5 — `FUN_0021A114` writes GuiData, it does not read it

`proven-from-code`. Walking every register that holds `0x90000000` through the function
finds **zero** loads or stores using it as a base. It is used as a *value*:

```
0x21a228  ldr   r3,[pc,#0x174]   ; r3 = 0x20006B90  (a descriptor in SRAM)
0x21a22a  ldr   r2,[pc,#0x178]   ; r2 = 0x90000000
0x21a22c  strb  r4,[r3,#4]       ; .b4 = mode
0x21a22e  str   r2,[r3,#8]       ; .w8 = 0x90000000   <-- stored, not dereferenced
0x21a230  mov.w r2,#0x8000
0x21a238  str   r2,[r3,#0xc]     ; .wC = 0x8000
```

and at `0x21A264`–`0x21A274` it is passed as the first argument to `FUN_00217058`,
`FUN_002170DC` and `FUN_0021706C`. A second site at `0x21A2D0` builds the same descriptor
with `mode = 2` and a length read from `[fp]`.

An `{mode, address, length}` descriptor, a `0x8000` (32 KiB) length that looks like an
erase block, and a J1939 proprietary-PGN transport together read as a **flash programming
routine** — the path that *writes* GuiData to its destination over CAN. `0x90000000` is a
destination address in external flash, not a structure this function walks.

**Consequence:** the plan of harvesting field offsets from `ldr` displacements off
`0x90000000` does not apply here. The GuiData *readers* are elsewhere — the remaining
consumers (`FUN_00219DC4`, `FUN_0021A078`, `FUN_0021A540`, `FUN_0021A738`), or render code
that keeps the base in a register established once at init, which this
literal-pool-driven search would never surface. A different technique is needed: find the
screen-drawing path and work backwards to its data source.

---

# Second pass — the screen-drawing path

Reproduce with `ghidra/find_render_path.py` (same headless invocation, `-postScript
find_render_path.py`); output lands in `extractions/render-path.txt`. The instruction
listings quoted below come from `ghidra/dump_range.py` →
`extractions/be50-listing.txt`.

## Finding 6 — the part is an STM32F7, and that reframes everything

`proven-from-code` for the peripheral addresses; the exact part number is `inferred`.

An aligned-word scan of the image, clustered by 4 KiB page, lands squarely on the STM32
peripheral map: `0x40023800` RCC and `0x40023C00` FLASH (52 refs), `0x40020000`–
`0x40021C00` GPIOA–GPIOI (67 refs), `0x40006400` bxCAN (13 refs), and — the one that
matters — **`0x40016800`, the LTDC LCD-TFT controller** (4 refs, literal slots
`0x00213070`, `0x0021C340`, `0021DE30`, `0x0021DECC`).

Three numbers then agree on the family:

| Observation | Reading |
|---|---|
| `SP = 0x20050000` (vector 0) | top of 320 KiB SRAM — DTCM 64K + SRAM1 240K + SRAM2 16K, an **F7** layout, not F4 |
| image base `0x00210000` | STM32F7 maps flash at **`0x00200000`** via the ITCM interface, so this image sits at flash offset `0x10000`, behind a 64 KiB bootloader |
| `0x90000000` | **QUADSPI memory-mapped mode** on STM32F7 |

That last row is the load-bearing one. **GuiData is external QSPI NOR flash, mapped into
the address space and readable by ordinary `ldr` instructions.** Its readers therefore
need no literal `0x90000000` at all — which is exactly why the first pass's
literal-pool-driven search kept missing them.

Vectors 11, 14 and 15 (SVCall `0x002199B5`, PendSV `0x00219A4D`, SysTick `0x0021E2BF`)
are all populated: the FreeRTOS context-switch signature. This image is an application
with a scheduler, not a bootloader — worth stating because a 62 KiB image carrying a
magic-validating loader and a CAN flash-writer reads like one.

## Finding 7 — the display path, and the framebuffer

`proven-from-code`. `FUN_00213040` is `HAL_LTDC_ProgramLineEvent` instruction for
instruction — lock byte at `param_1[0x28]`, state byte at `+0xA1`, `LTDC_IER |= 1`
(`LTDC_IT_LI`), then `LTDC->LIPCR = Line` via `DAT_00213070 + 0x40`. So this is
STM32Cube HAL, with a line-interrupt hook — the usual double-buffer swap point.

`FUN_0021BE50` sets `RCC_AHB1ENR |= 0x800000` (bit 23 = **DMA2D**, the Chrom-ART 2D
graphics accelerator) at its first instruction, then fills the LTDC handle at
`0x2000A2FC`:

```c
puVar5[5]=0x1d; puVar5[6]=0xc;  puVar5[7]=0x2d; puVar5[8]=0x16;
puVar5[9]=0x34d; puVar5[10]=0x1f6; puVar5[0xb]=0x41f; puVar5[0xc]=0x20c;
```

`0x34D - 0x2D = 800` and `0x1F6 - 0x16 = 480`: an **800×480** panel, which is the 7"
Spyder. `FUN_00212fae(handle, &cfg, 0)` is then `HAL_LTDC_ConfigLayer`, and the config
struct built on its stack gives the far end of the drawing path:

| Field | Value |
|---|---|
| `WindowX0/X1`, `WindowY0/Y1` | `0`/`800`, `0`/`480` |
| `PixelFormat` | `1` = `LTDC_PIXEL_FORMAT_RGB888` |
| **`FBStartAdress`** | **`0xC0000000`** — FMC SDRAM bank 1 |
| `ImageWidth`/`ImageHeight` | `800`/`480` |

**The framebuffer is at `0xC0000000`.** Note that no literal-pool scan finds it:
Thumb-2 encodes `0xC0000000` as a modified immediate, so it never reaches a literal
pool. That is the same class of blind spot as Finding 6's, and worth remembering.

## Finding 8 — `FUN_0021BE50` reads GuiData. The first proven reads.

`proven-from-code`, and this is the finding the project was missing. `FUN_0021BE50` is
not merely "the validator" — it is the display-and-GUI init routine, and it
**dereferences the GuiData base**. From `extractions/be50-listing.txt`:

```
0021bf0c  ldr    r1,[0x0021c024]    ; r1 = 0x90000000, from the blob descriptor table
0021bf10  ldrh   r0,[r1,#0x4]       ; <== reads GuiData+0x04  (u16)
0021bf12  mov    r9,r1              ; r9 keeps the GuiData base for the walk below
0021bf1a  ldrb   r1,[r1,#0x0]       ; <== reads GuiData+0x00  (u8)
0021bf2c  ldrb.w r12,[r9,#0x7]      ; <== reads GuiData+0x07  (u8) = directory count
0021bf3a  lsl.w  r12,r12,#0x3       ;     × 8 = directory length in bytes
```

It cross-checks the three blobs against each other. Every one of these was verified
against the bytes of all three MCFs:

| Check | Instructions |
|---|---|
| `GuiCode+0x00 == 0x1A2B3C4D` | `0021beca` / `0021becc` |
| `GuiCode+0x04 == 1` | `0021bed0` / `0021bed2` |
| `subcontainer+0x00 == 0x8F7E6D5C` | `0021bf00` / `0021bf02` |
| `subcontainer+0x06 == 1` | `0021bf06` / `0021bf08` |
| **`GuiData+0x04 (u16) == GuiCode+0x08 (u16)`** | `0021bf10` / `0021bf0e` / `0021bf14` |
| **`GuiData+0x00 (u8) == GuiCode+0x0A (u8)`** | `0021bf1a` / `0021bf18` / `0021bf1c` |
| **`subcontainer+0x04 (u16) == GuiData+0x04`** | `0021bf20` / `0021bf22` |
| **`subcontainer+0x07 (u8) == GuiData+0x00`** | `0021bf26` / `0021bf28` |

Any mismatch branches to `0x0021BED6`, which returns 0 — the blobs are rejected. So
`GuiData+0x00` is a **build id** and `GuiData+0x04` a **version stamp**, and both are
locked across all three blobs. In 11.23 and 13.6 they read `0x66`/`0x1234`; 14.7 reads
`0x66`/`0x0807`, and the three-way equality still holds — the stamp varies, so this is
not a constant fitted to one build.

The pointer to the sub-container is *not* computed: `0x0021F2AC` holds it, loaded at
`0021befa`–`0021befe`.

## Finding 9 — the table directory, proven, and a correction to `MCF-FORMAT.md` §5.1

`proven-from-code` for the `tag == 'A'` entry; see the caveat at the end.

The directory walk at `0x0021BF7C`–`0x0021BFC2`:

```
0021bf7c  add.w  r10,r9,r1        ; r10 = GuiData + i
0021bf80  ldrb.w r11,[r10,#0x8]   ; tag  = BYTE at GuiData+8+i
0021bf84  cmp.w  r11,#0x41        ; == 'A'
0021bf8a  ldrb.w r0,[r10,#0x9]    ; b1
0021bf8e  ldrh.w r6,[r10,#0xc]    ; offset (u16)
0021bf94  add    r6,r9            ; ptr = GuiData + offset
0021bf96  and    r3,r0,#0x300     ; (b1 & 3) << 8
0021bf9a  ldrb.w r0,[r10,#0xb]    ; b3
0021bf9e  add    r0,r3            ; count = b3 | ((b1 & 3) << 8)
0021bfa0  ldrb.w r3,[r10,#0xa]    ; b2 = attr
0021bfa4  and    lr,r3,#0x1f      ;   attr & 0x1F
0021bfa8  lsrs   r3,r3,#0x5       ;   attr >> 5  -> tbb at 0021bfb0
0021bfc0  adds   r1,#0x8          ; stride 8
```

So an 8-byte descriptor is `{tag:u8, b1:u8, b2:u8, b3:u8, offset:u16, size:u16}`, and
offsets are **GuiData-relative** (`add r6,r9`).

**The correction:** `MCF-FORMAT.md` §5.1 reads the descriptor as `<u16 tag><u16 ?>`, and
lists the variable table's tag as `0x0141`. The code proves the tag is a **`u8`** — a
byte load compared against `#0x41`. The spec's `0x0141` conflates the tag `0x41` with
`b1 = 0x01`, which is the count's high bits. The spec's `aux` u16 cannot be a universal
field either: for tag `0x10` it reads `0x00E9` = 233, which is that table's entry count,
but applying the same grouping to the `'A'` descriptor gives `0x2A00` = 10752, where the
true count is 298.

That count decode is confirmed three independent ways, in all three builds:

| Build | `'A'` count from `b3 \| ((b1&3)<<8)` | `size / 8` | `GuiCode+0x20` (u16) |
|---|---|---|---|
| 11.23 | 291 | 2328/8 = 291 | 291 |
| 13.6 | 298 | 2384/8 = 298 | 298 |
| 14.7 | 310 | 2480/8 = 310 | 310 |

`0021bfe0`–`0021bfea` is the check itself: `cmp r6,r3` against `GuiCode+0x20`, bail on
mismatch, then `cmp.w r6,#0x3e8` — **a hard ceiling of 1000 variables**. When
`GuiCode+0x20` is zero the code instead compares `GuiCode+0x05` (u8) to the count
(`0021bf6e`); `+0x20` is non-zero in all three builds, so that fallback never fires here
and `GuiCode+0x05` should *not* be read as a count (it is `0xD5` = 213 in 13.6, against a
real count of 298).

**Caveat, stated because it is easy to over-read:** the loop only ever decodes the
descriptor whose tag is `'A'`. For tags `0x10`, `0x20`, `0x30` and `0x42` there is **no
code evidence** about `b1`/`b2`/`b3`, so the spec's "aux = entry count" reading for the
group table stays `inferred-from-data`. What the code refutes is only that the u16
grouping is universal. The middle three bytes are most likely a per-tag payload.

The `attr >> 5` table branch at `0021bfb0` maps 1–7 to `{20, 30, 45, 60, 75, 90, 120}`
and anything else to 0 (`0021bfdc`). That control flow is proven, but **the `'A'`
descriptor's attr byte is `0x00` in all three builds**, so no non-zero case is exercised
anywhere in the corpus, and what the values mean (seconds, plausibly a timeout) is
`inferred` and untested.

## Finding 10 — GuiCode is native ARM Thumb-2, not bytecode. There is no interpreter.

`proven-from-code`. This overturns a premise carried in `CLAUDE.md` and in this file's
first pass. GuiCode's header holds a **five-entry function-pointer vector table**, and
`FUN_0021BE50` calls through it:

```c
iVar11 = (*(code *)piVar13[3])(auStack_7c);   // piVar13 = 0x00240000 -> calls *(GuiCode+0x0C)
pcVar17 = (code *)piVar13[6];                 //                      -> calls *(GuiCode+0x18)
(*pcVar17)();
```

Every slot is an odd (Thumb) address inside GuiCode's own load region, and every target
begins with a real prologue:

| Slot | 13.6 | First halfword | Decodes as |
|---|---|---|---|
| `+0x0C` | `0x00247A91` | `0xB510` | `push {r4,lr}` |
| `+0x10` | `0x0024EE29` | `0xB538` | `push {r3,r4,r5,lr}` |
| `+0x14` | `0x00247B81` | `0xB538` | `push {r3,r4,r5,lr}` |
| `+0x18` | `0x00247B45` | `0xB510` | `push {r4,lr}` |
| `+0x1C` | `0x00247801` | `0xB510` | `push {r4,lr}` |

All five slots hold in-region Thumb pointers with `push` prologues in all three builds.

`GuiCode+0x30`, the first byte past the header, is where code begins — but it is **not**
always a `push`: 13.6 and 14.7 read `B5 10 4C 05` = `push {r4,lr}; ldr r4,[pc,#0x14]`,
while 11.23 reads `4B 04` = `ldr r3,[pc,#0x10]`, a leaf function that never saves `lr`.
An earlier draft of this finding generalised the 13.6 reading to all three builds;
`tests/test_blob_crosschecks.py` caught it.

**No bytecode interpreter was ever found because there is none.** GuiCode is a
relocatable native code module loaded to `0x00240000` and entered through its vector
table. That is also why the LCD Firmware image contains no widget-drawing code and no UI
strings — only one string exists in the whole image, `"Spyder Controls 7in Lite LCD 1v6r"`
at `0x440`. **The renderer that reads GuiData's fields lives in GuiCode.**

## Finding 11 — the variable table's DGN is 18 bits

`proven-from-code`, and it upgrades `MCF-FORMAT.md` §5.4. The per-entry init loop reads
the first u32 of each 8-byte variable record and masks it:

```c
FUN_0021a618(*(uint *)(*(int *)(iVar3 + 0x104) + uVar20 * 8) & 0x3ffff);
```

`0x3FFFF` is 18 bits, so the DGN is not the `u16` §5.4 describes — it spans two bits into
byte 2. 13.6 entry 41 is `CE 5F 1D 02 01 00 00 00`: `0x021D5FCE & 0x3FFFF = 0x15FCE`,
the G12 configuration DGN. Masking all 298 entries yields **66 distinct DGNs**, all
well-formed RV-C: `0x1FEDA` (`DC_DIMMER_STATUS_3`) ×75, `0x15FCE` ×53, `0x1FBDA` ×27,
`0x1FFE2` ×15, `0x1FACE` ×7. Bits 18–31 take 35 distinct values and remain `unknown`.

The surrounding loop also maps the GUI state struct at `0x20007EBC` (`DAT_0021C00C`):
`+0x104` variable-table pointer, `+0x108` count, `+0x101` the attr enum, `+0x10A`
`attr & 0x1F`, `+0x10C` and `+0x144` a `0xFFFF`-initialised u16 state cache split at
index 256, and `+0x914` a u32 per variable.

## Finding 12 — a second directory walker, and the `0x10` / `0x30` descriptor fields

`proven-from-code` for the layout. **Nothing is exercised by this corpus** — see the
warning at the end, which is the more important half of this finding.

`FUN_0021A738` (566 bytes, one of the consumers the `hits[:6]` bug hid) walks the **same
GuiData table directory** as `FUN_0021BE50` — same count byte at `+0x07`, same 8-byte
stride, same `u16` offset at descriptor `+0x0C` added to the GuiData base — but it handles
the tags Finding 9 had to leave `unknown`:

```c
if (DAT_0021a978[i + 8] == '\x10') {              // tag 0x10
    cVar16  = DAT_0021a978[i + 0xb];              //   b3      -> state +0x107
    pcVar14 = DAT_0021a978 + *(ushort *)(DAT_0021a978 + i + 0xc);
} else if (DAT_0021a978[i + 8] == '0') {          // tag 0x30
    cVar17  = DAT_0021a978[i + 10];               //   b2      -> state +0x10C
    cVar18  = DAT_0021a978[i + 0xb];              //   b3      -> state +0x10D
    pcVar11 = DAT_0021a978 + *(ushort *)(DAT_0021a978 + i + 0xc);
}
```

So the middle bytes are, as Finding 9 suspected, a **per-tag payload** — and each tag uses
a different one:

| Tag | Field read | Used as | Bound enforced |
|---|---|---|---|
| `0x10` | **`b3`** | count of a `0x18`-stride array | `< 0xB` (11) |
| `0x30` | **`b2`** | a mode or kind selector | `< 5` |
| `0x30` | **`b3`** | count of **12-byte** records at the descriptor's offset | — |
| `'A'` | `b3 \| ((b1&3)<<8)` | variable count (Finding 9) | `≤ 1000` |

Each of the tag-`0x30` records is read as `*(u32 *)(base + k * 0xC) & 0x3FFFF` and passed
to `FUN_0021A618` — the same 18-bit DGN mask as the variable table (Finding 11), so
**`FUN_0021A618` is the DGN-registration function**, and this is a second table of DGNs to
subscribe to. `FUN_0021A738` also registers **DGN `0x1FED9`** directly, from the literal at
`0x0021A974` — the configuration-*write* DGN (`BUS-BASELINE.md` PC.2, PC.12a).

It checks the build id too: `*pcVar7 == 'f'` is `GuiData[0] == 0x66`, a third site
enforcing what Finding 8 proved, alongside a `≤ 25` bound and a 2-bit field selecting one
of `0x7B`/`0x7D`/`0x7E`/`0x7F` and a bit choosing `0x65`/`0x66` — plausibly RV-C node
addresses, `inferred`.

**The warning.** In **all three builds** `b3` is `0x00` for both tags and the tag-`0x30`
table has length `0x0000`. So the tag-`0x10` loop bound is zero, the record loop never
executes, and the 12-byte record layout is **proven from the instructions but never
exercised by any file in this corpus**. Do not report the 12-byte records as observed
structure; no instance of one exists here. This is the same shape of caveat as Finding 9's
attr enum, and for the same reason.

## Finding 13 — the memory map, sized: 320 KiB internal, 8 MiB SDRAM, 4 MiB QSPI — `proven-from-code`, 2026-09-07

Finding 6 named the framebuffer's home (`0xC0000000`, FMC SDRAM bank 1) and GuiData's
(`0x90000000`, QUADSPI) but never sized either. Both are in the image: the app configures the
FMC and the QUADSPI itself, and each HAL init struct carries the geometry.

**The SDRAM — 8 MiB.** `0x0021D722` loads the handle, `0x0021D726` loads
`0xA0000140` = `FMC_SDRAM_DEVICE`, and the next ten instructions fill
`FMC_SDRAM_InitTypeDef` field by field:

| field | value | reading |
|---|---|---|
| `SDBank` | `0` | `FMC_SDRAM_BANK1` → mapped at `0xC0000000` |
| `ColumnBitsNumber` | `0x00` | 8 |
| `RowBitsNumber` | `0x04` | 12 |
| `MemoryDataWidth` | `0x10` | 16-bit |
| `InternalBankNumber` | `0x40` | 4 |
| `CASLatency` | `0x100` | 2 |
| `SDClockPeriod` | `0x800` | SDCLK = HCLK/2 |
| `WriteProtection` / `ReadPipeDelay` | `0` | off / 0 cycles |

2⁸ columns × 2¹² rows × 4 banks × 2 bytes = **8,388,608 bytes**. That is a 64 Mbit ×16 SDRAM
(`MT48LC4M16A2` class). One 800×480 RGB888 framebuffer is 1,152,000 bytes, so seven fit — ample
for the double-buffering the LTDC path implies.

**The QSPI NOR — 4 MiB.** `0x00216DF4` loads `0xA0001000` = QUADSPI and
`0x00216E0E`/`0x00216E14` write `QSPI_InitTypeDef.FlashSize = 0x15`. The field is `FSIZE`, and
the part is 2^(FSIZE+1) = 2²² = **4,194,304 bytes**. Also set: `ClockPrescaler 1` (QSPI clock =
HCLK/2), `FifoThreshold 4`, `SampleShifting 0x10` (half-cycle), `ChipSelectHighTime 0x100`. The
whole MCF is 1,070,792 bytes, so GuiCode+GuiData occupy roughly a quarter of the part.

**Internal SRAM — 320 KiB, and only 44 KB of it is initialised.** Vector 0 is `0x20050000`, so
SRAM runs `0x20000000`–`0x20050000` = 327,680 bytes. Against that, `raminit` reads one `.data`
copy of **540 bytes** and one `.bss` zero of **43,888 bytes** — 44,428 bytes total, **13.6%**.
The other **276 KiB above `_ebss` is never written by the C runtime**, and every GuiCode-RAM
widget slot lives in that tail. This is the sizing behind the boot lottery
(`src/fireflymcf/bootlottery.py`), and behind the vendor's power-cycle-after-update
instruction being a real requirement rather than superstition.

**Part narrowing, still `inferred`.** LTDC present *and* 320 KiB SRAM selects **STM32F746 /
F756** from the F7 line: F745 has the same 320 KiB but no LTDC, and F765/767/769/777/779 have
512 KiB (SP would read `0x20080000`). F756 is F746 plus the crypto block. Nothing in the image
reads `DBGMCU_IDCODE` to settle it — the one `0xE0042000` literal at `0x00217444` is not loaded
by any nearby `ldr`, and the usual use of that register is the debug-freeze write, not an
identity check. So the family is proven and the exact die is not.

## Finding 14 — the three unexamined consumers: the group-table loader, an I2C-backed module init, and the USB port's identify reply — `proven-from-code`, 2026-09-10

The `hits[:6]` bug hid four functions. `FUN_0021BE50` was examined long ago and `FUN_0021A738`
became Finding 12; these are the other three. All three run from one GuiData bring-up sequence
that Ghidra never made into a function (it begins at the `push {r0,r1,r7,lr}` at `0x0021B1B8`).
At boot it calls `FUN_0021BE50` (validate), `FUN_0021CCA0`, `FUN_0021A738` and `FUN_0021B420`
(`0x0021B1F6`–`0x0021B22E`), and it calls the same set again after `FUN_0021A114` has written a
GuiData image received over CAN (`0x0021B330`–`0x0021B358`).

### `FUN_0021B420` — the group-table loader

It walks the table directory the way Finding 12's walker does, with its state block at
`0x20006E5C` (`DAT_0021B5D8`).

* **Tag `0x10`** (`cmp.w fp,#0x10` @`0x0021B4BA`): the group table. Pointer = GuiData + offset;
  **group count = `b2 + b3`** (`ldrb r1,[r2,#0xa]` @`0x0021B4C8`, `ldrb.w ip,[r2,#0xb]`
  @`0x0021B4C0`); `b3` is kept at state `+0x0F` and must be `≤ 10` (`cmp r3,#0xa` @`0x0021B49A`).
  In every build `b3` is 0, so the count is `b2`: 230, 233, 234.
* **Tag `0x20`** (`cmp.w fp,#0x20` @`0x0021B4D6`): the binding table. Pointer, with `b2` and `b3`
  kept at `+0x0C` and `+0x0D` (both 0 in every build).

If either pointer is zero or `b3 > 10`, it zeroes the count and fails. Otherwise, for each group
`i`, it clears the 16-byte runtime record at `0x20006E6C + i*16` (the record whose `+0x0A`/`+0x0B`
are `CONTROL-BEHAVIOR.md` §5's variant counters) and reads the 8-byte group entry:

| group byte | what the loader does |
|---|---|
| `+1` bits 6-7 | **both set: abort, and the whole table is dropped** (`and r3,#0xc0` / `cmp r3,#0xc0` @`0x0021B532`/`0x0021B536`). Only `0x00` and `0x80` occur in the corpus |
| `+7` | `0xFF` (`cmp r3,#0xff` @`0x0021B53E`): record `+0x0D` = 1 and the slice is the `+6` entries; otherwise `+0x0D` = 0 and the slice is `+6` + `+7` entries |
| `+4` | u16 **binding start index**; the slice is read at `binding_base + (start + k) * 12` (`movs r1,#0xc` @`0x0021B562`) |
| `+2` bits 4-5 | non-zero (`tst.w r1,#0x30` @`0x0021B558`): scan the slice for the first binding whose `+2`/`+3` u16 has `0x204` set (`and lr,#0x204` / `cmp.w lr,#0x204` @`0x0021B582`/`0x0021B586`) |

When that scan finds entry `k`, the record's phase byte `+0x09` becomes 2 (`k` inside the `+6`
slice) or 6 (inside the `+7` slice), and `+0x04` gets a deadline of now plus
`{0, 250, 500, 1000}[bits 4-5]` ms from the table at `0x0021F2A2`. So a group whose release-phase
repeat rate is set, and whose slice holds a binding flagged "re-send on repeat ticks" (binding
`+2` bit 2) in the short-tap-release phase (`+3` bit 1), is loaded **already in its
release-repeat phase**: the panel would re-send that frame periodically from power-on, with no
press. **No build exercises it:** no group in 11.23, 13.6 or 14.7 sets `+2` bits 4-5.

One more branch is dead in this corpus. When the tag-`0x11` state's flag is set (`FUN_0021A06C`
reads `0x20006B6C + 0x0D`, which `FUN_00219DC4` writes when it finds a tag-`0x11` table), the
group count gains one. No build has a tag-`0x11` descriptor.

**What this proves.** The group entry's `+4` start, `+6` count and `+7` second count are read by
located instructions, the binding stride is 12, and a group's slice is a contiguous run from
`start`. In all three builds the slices end exactly at the binding count (732, 746, 747).
`MCF-FORMAT.md` §5.2 carried `start` and `count` as `inferred-from-data` and byte 7 as padding;
all three are now `proven-from-code`. It also adds two conditions a builder must never create:
group byte 1 `0xC0`, and a tag-`0x10` `b3` above 10. Either makes the loader drop every group,
and so, `inferred`, every control. `tests/test_group_loader.py` pins both, and the no-pre-arm
condition, on every build and on the coach's recipe.

### `FUN_0021CCA0` — the tag-`'B'` table, and a 64-byte transfer over I2C

It finds tag `0x42` (`cmp.w lr,#0x42` @`0x0021CD64`) and keeps its pointer and `b3`
(`ldrbeq r5,[r0,#0xb]` @`0x0021CD6E`) in a state block at `0x2000978C`. It goes on only if that
table exists and both validity flags at `0x20000130` (`FUN_0021A0FC`, `FUN_0021A108`) are set.
Then it calls `FUN_002169E0(0x14, 0x20009798, 0x40)` (`movs r0,#0x14` @`0x0021CCE0`,
`movs r2,#0x40` @`0x0021CCDC`), which calls `FUN_00212036(handle, 0xA0, 0x14, 2, buffer, 0x40,
0xF)` (`movs r1,#0xa0` @`0x00216A2C`) with up to four retries. That argument list has the shape of
ST HAL's `HAL_I2C_Mem_Read`/`Mem_Write`: device `0xA0` (the usual serial-EEPROM address),
16-bit memory address `0x14`, 64 bytes, a 15 ms timeout. That it is the *read* is `inferred`,
because the destination is a RAM buffer at initialisation. After the transfer it:

* clears four 16-entry arrays at `0x200097E4` (u32 zero, u16 zero, u16 `0xFFFF`, u16 `0xFFFF`);
* makes GPIO port H pin 12 an output and drives it low (`0x40021C00`: MODER bits 24-25 = `01`,
  BSRR = `0x10000000`);
* starts a timer and arms a 10 s deadline (`0xFFFFD8F0` = −10000 is the wrap guard).

13.6 and 14.7 carry one 12-byte `'B'` record, `e7 15 03 01 20 e8 04 02 81 ff 00 00`. 11.23
carries five (`17 15 03 3d 20 22 04 3e 81 ff 00 00` and four variants), whose byte 8 runs
`0x81`–`0x85`. `b3` is the record count (1 or 5). **The records are not decoded, and nothing here
names the module.** One reading fits and is only that: 16 slots of 4 bytes is exactly the 64 bytes
transferred, and the panel carries an RF receiver (`RF1v1a`, the Wireless Switch Panel). A table of
paired-remote IDs would look like this.

### `FUN_0021E7F8` — the identify reply on the USB port, and the port's command parser

It fills an 18-byte packet in the buffer at `0x200001A4` and hands it to `FUN_0021E468`. That
function masks IRQs 20 and 21 (CAN1 RX0/RX1 on the F7) and calls `FUN_002186E8`/`FUN_00218704`,
whose layout is ST's USB-device CDC pair: set the transmit buffer (class data `+0x208`/`+0x210`),
then transmit on endpoint `0x81` if the `+0x214` state is idle. So the packet goes out of the
micro-USB port (`LYRA-USB-CAN-GATEWAY.md`), not onto CAN.

The packet is `5A C3 01 06`, then `00 <mode> FF <flag>` from the `.data` initialiser, then eight
bytes built from the GuiData header: `0x25`; `(GuiData+2 low byte << 3) | 1`; `0x65` or `0x66` by
GuiData+2 bit 5; `0x16`; GuiData`+6`; GuiData+2 `& 0x3F`; and the GuiData`+4` u16, or `FFFF` when
the second validity flag is clear. For 13.6 that is `25 A1 65 16 06 14 34 12`. A Fletcher-16 over
bytes 2-15 fills the last two (`movs r2,#0xe` @`0x0021E846`, `bl 0x0021E7B0` @`0x0021E852`).

**The checksum is also the USB mirror's frame trailer.** `FUN_0021E7B0` is a ones'-complement
Fletcher-16, folded every 21 bytes. The mirror's frame builder `FUN_0021EE2C` pre-sets the trailer
to `0xFFFF` and sums 18 bytes from frame byte 2 into it (`movs r2,#0x12` @`0x0021EEB2`), so the
span includes the trailer's own two `0xFF` bytes. That reproduces the trailer of every frame in
the three USB captures, 11,326 of 11,326. `LYRA-USB-CAN-GATEWAY.md` had recorded it as unsolved
after eight CRC-16 variants failed. `tools/lyra_usbcan.py` now checks it.

`FUN_0021E7F8`'s only caller, `FUN_0021E9E0`, is the port's **receive parser**, and it is run
by polling, not by an interrupt: the main service loop `FUN_00217B98` calls it each pass
(`0x00217BEC`), alongside the CAN bus servicing that shares that loop. It drains a ring buffer at
state `+0x5D` (indices `+0x866` read, `+0x868` write, wrapping at `0x7FF`), and it is defensive
about a torn stream — a frame whose bytes have not all arrived arms a byte-timeout at `+0x86C`
(`FUN_00210A60` + `0x32` ticks) and, on expiry, skips the two sync bytes and resynchronises.

For each frame it looks for `5A C3` (@`0x0021EAB8`, `0x0021EACE`), then a **type** byte and a
**length** byte, where the payload is **twice the length byte** (`lsl.w sb,r2,#1` @`0x0021EB0C`).
It Fletcher-sums type, length and payload from `0xFFFF` — **no `FF FF` padding on this side**, the
one difference from the outbound trailer — (@`0x0021EC24`–`0x0021EC36`), compares the result with
the two bytes that follow, and only then dispatches on the type (@`0x0021EC68`):

| type | payload | action |
|---|---|---|
| `0x81` | — | send the identify reply above |
| `0x82` | 1 byte | if it is not `0xFF`, stop the display refresh (`FUN_002170DC`/`FUN_0021706C`, both GuiData-guarded) and clear `+0x870`; either way, reply |
| `0x84` | u16 | **`0x0101` reboots the panel** — `FUN_0021E78C` writes `0x05FA0004` (VECTKEY + `SYSRESETREQ`) to SCB `AIRCR` at `0xE000ED0C` and spins until the core resets. `0x0201` sets mode 1, `0x0202` sets mode 2 (mode byte at struct `+5`) |
| `0x85` | ≥ 14 bytes | **only in mode 2**: queue a CAN frame through `FUN_0021825C`, the 64-entry transmit ring. The id word is assembled from payload bytes 0-3 — byte 0 the source address, an 18-bit DGN from bytes 1-3, a 3-bit priority from byte 3, and the extended-frame bit `0x20000000` — and the eight data bytes are payload bytes 6-13 |

The mode byte gates two things. The mirror builder `FUN_0021EE2C` skips mirroring in mode 1
(`ldrb r3,[r3,#5]` / `cmp r3,#1` @`0x0021EE40`), and `0x85`'s CAN transmit runs only in mode 2. So
the port is a mirror **and** a command channel: a host can silence the mirror, reboot the panel,
and — once it sends the mode-2 command — put arbitrary RV-C frames on the coach bus. `FUN_0021E880`
is the bring-up: it configures the CDC endpoints, pre-loads the ring with five `5A C3 05 0E`
frame templates, and sets mode 0.

**The length rule that looked wrong is two grammars sharing one sync — resolved 2026-09-10.**
Finding 14 first flagged that the mirror's own frames carry `0x0E` in byte 3 (14 data-through
bytes) while this parser reads byte 3 as `payload ÷ 2`. They are different frame formats. On the
**outbound** (panel → host) mirror path `FUN_0021E880` writes a constant `05 0E` into bytes 2-3 of
every slot and `FUN_0021EE2C` never overwrites them; the checksum is a fixed 18-byte span. On the
**inbound** (host → panel) path byte 2 is the command type (`0x81`-`0x85`, never `0x05`) and byte 3
is `payload ÷ 2`. A mirror frame fed back to the parser would be read as a 28-byte payload, fail
the checksum, and be dropped — which is exactly why the panel ignores its own frame shape on
receive. The `0x0E` is a protocol magic constant, not a length.

**Everything about the inbound path is read from code and has never been exercised** — no packet
has been sent to the port, and transmit through it reaches the live coach bus, so it is not
something to try without the owner. `can1` + `cansend` stays the known-good path.

## Finding 15 — `FUN_0021317C` is the CAN mailbox-servicing task, not an RV-C status loop — `proven-from-code`, 2026-09-10

`FUN_0021317C` was the largest LCD orchestrator the section survey flagged as "an apparent
receive / status-update loop, RV-C-relevant" (`SECTION-INTERIOR-SURVEY.md`). Decompiling it
**corrects that guess**: it is one layer lower — the **CAN peripheral mailbox-servicing task**,
driver code, not application status logic.

**What it does.** Its argument is a CAN context handle (`param_1[0]` = the peripheral/handle
block). It reads pending-and-enabled interrupt flags as `*(base+0x18) & *(base+0x14)` (the
classic "flags & mask" ISR idiom; a reentrancy guard bit at `+0x14 & 1` returns early), then
switches on the flag bits:

* bit `0x80000` — walks the **transmit mailboxes** (`FUN_002162EC` gives the per-mailbox bitmap,
  `FUN_0021630C` each mailbox's status) and records the completion state into
  `+mailbox*0x20+8`: `1` sent OK, `8`/`0x10`/`0x20` the arbitration-lost / error / abort cases,
  invoking the per-mailbox callback `FUN_0021E53C`;
* bit `0x40000` (`<< 0xd`) — services the **receive path** (`FUN_002162FC`/`FUN_00216320`),
  clearing the object's pending bit in `+0x834`, marking `+object*0x20+0x908`, and calling
  `FUN_0021E54E`;
* `FUN_0021637E` re-arms an object descriptor (`0x80008000` / `0x60000000` control words).

The per-object records are 32-byte-strided (`+0xb08`, `+0x908`); the callback family
`FUN_0021E530..E5D8` is a table of small field accessors over a 28-byte-strided companion array,
with a ring buffer (head/tail at sub-context `+0x3bc`, `+0x10c`/`+0x110`).

**How it is reached.** No literal pointer, vector-table slot, or `bl` names it by address — which
is why the survey's caller scan came up empty. Ghidra finds one `UNCONDITIONAL_CALL` from
`0x0021E392`, inside a region it never promoted to a function: a **traced scheduler-step stub**
(`push {r3,lr}` / `movs r0,#0xD0` / `bl 0x21CC64` trace-enter / `ldr r0,[pc,#…]` load context /
`bl FUN_0021317C` / `movs r0,#0xD1` / `b.w 0x21CC64` trace-exit), one of a run of such stubs each
wrapping a subsystem handler with a trace id and a context pointer. So `FUN_0021317C` runs as one
**step of the LCD's main task loop**, servicing CAN alongside its siblings — a periodic driver
pump, not an RV-C decode.

This is the second confirmation of the survey's headline: the LCD's dark bulk is peripheral/driver
and library code. The RV-C *application* decode lives in **GuiCode**, in `GuiCode_entry4` and its
parsers (`GUICODE.md` Finding 75) — the LCD's job here is to move mailboxes, and the frames it
services are handed up to that path. Pinned by
`tests/test_blob_crosschecks.py::test_lcd_can_task_is_a_traced_scheduler_step`.

## Finding 16 — `FUN_0021CE28` is a framed serial receiver for a multi-node switch bus (the Wireless Switch Panel lead) — `proven-from-code` (mechanism), 2026-09-10

The next-largest dark LCD orchestrator (1086 bytes; `SECTION-INTERIOR-SURVEY.md`), and like
`FUN_0021317C` (Finding 15) it is a **main-loop task**, not the RV-C application: the same traced
scheduler harness calls it at `0x0021B27E` as **step 5** (`movs r0,#4`/`bl 0x21CC64` … `bl
FUN_0021CE28` … `movs r0,#5`), inside a region Ghidra left undefined — which is why no defined
caller shows.

**Mechanism, `proven-from-code`.** It drains a **128-byte serial RX ring** — `FUN_00216AE8`
returns `(head − tail) & 0x7f` (bytes available), `FUN_00216AFC` reads one byte from the ring's
data at `+0x44` and advances the masked tail — and parses a **framed packet**: it hunts the sync
sequence `0xF0 0x0F`, arms a timeout deadline (`context+0x4c`, from the `FUN_00210A60` millisecond
tick), and reads a **10-byte payload** with a trailing checksum. It then matches the packet's
4-byte **node ID** (`+0xfd`) against an address table of up to `context[8]` entries to find the
node index, and dispatches on the command byte at `+0x101`:

* `0xA5` — **per-node status**: decodes payload words into per-node arrays (`+0x98`/`+0xb8`/`+0xd8`,
  strided by node) with engineering-unit scaling (`(v + 0x1110) * 2`, `(v + 0x19) / 0x32`);
* `0xFA` — **node registration / address assignment**: population-counts a mask, and on a match
  assigns the node the next free index and stores its 4-byte ID (a pairing/enumeration handshake).

**What it is — a lead, not a finding.** A framed serial bus whose nodes have 4-byte IDs, are
*paired* by address assignment, and report per-node status is, on this panel, most plausibly the
**Wireless Switch Panel**: the panel carries a `PAIRING` / `WIRELESS SWITCH` screen (`GUICODE.md`
Finding 13, objects 755/769), the owner has one whose battery icon this firmware drives
(`reflash-clears-wireless-pairing`), and `CONTROL-BEHAVIOR.md` §2 already notes a **ten-slot input
array** (`0x00216414`/`0x00216420`) that "matches the ten-button wireless switch panel." The
mechanism above is proven; tying it specifically to the wireless panel stays **inference** — the
transport is a UART/serial ring here, not the CAN bus, so the wireless nodes would reach it through
an RF/serial gateway module, which is consistent but not shown in code.

Third confirmation of the survey headline: the LCD's dark bulk is transport/driver/protocol code.
Pinned by `tests/test_blob_crosschecks.py::test_lcd_serial_receiver_syncs_and_is_a_scheduler_step`.

## Finding 17 — `FUN_002145FC` is the STM32F7 clock/power configuration (RCC + PWR), the last >1 KB dark LCD function — `proven-from-code`, 2026-09-10

The one dark LCD function over 1 KB the survey had not touched (1842 bytes). It is **HAL/startup
code**, and its two data pointers name the peripherals outright:

* `DAT_00214900` = **`0x40023800`** — the STM32F7 **RCC** (reset & clock control) base;
* `DAT_00214904` = **`0x40007000`** — the **PWR** (power control) base.

It takes a config struct (`param_1`) whose first word is a field-presence mask and read-modify-writes
each selected field into the RCC registers (`[2]` = RCC_CFGR, `[0x10]` = an ENR, `[0x1c]`/`[0x23]`
= further RCC words) under precise masks, then runs the STM32F7 **ready-flag handshakes**: it sets a
request bit and polls the corresponding ready bit off the millisecond tick `FUN_00210A60`, giving up
with **error `3`** after 100 ms (a clock/oscillator ready) or 5000 ms (the PWR over-drive switch,
`*0x40007000 |= 0x100` then poll). That is the textbook oscillator/PLL + over-drive bring-up — a
`HAL_RCC_OscConfig` / `HAL_RCC_ClockConfig` equivalent.

Its sole caller `FUN_0021D2BC` is the **system-init** routine: it enables peripheral clocks, fills
RCC config structs (`{9, 0x10000, 0x400000, 6, 2, …}` — HSE/PLL settings) and calls the config
helpers, and on any failure drops into `while (true) {}` — the classic HAL `Error_Handler`.

So the last large dark LCD function is the clock tree bring-up. This is the cleanest possible
confirmation of the survey's thesis: **the LCD's dark bulk is STM32 HAL, driver and startup code**,
not Firefly application logic — the application (RV-C decode, controls, rendering) lives in GuiCode
(`GUICODE.md`) and the named LCD functions. Pinned by
`tests/test_blob_crosschecks.py::test_lcd_clock_config_targets_rcc_and_pwr`.

## A first-pass bug worth recording

`analyze_lcd.py` truncates its slot list with `hits[:6]`, so the first pass reported five
functions loading `0x90000000` when there are **ten aligned slots and nine functions**.
The four it hid are `FUN_0021B420` (two loads), `FUN_0021CCA0`, `FUN_0021E7F8`, and
`FUN_0021BE50` — the last of which is the one that actually reads GuiData. A display
limit silently became a finding. None of the four had been examined then; Finding 12 covers
`FUN_0021A738`, and Finding 14 covers the remaining three.

## What this second pass did NOT establish

> **Dated snapshot.** This list and the "Next steps" below record where the *LCD* work
> stopped. Both were overtaken by the GuiCode disassembly they recommended — annotated
> inline, 2026-08-18. `docs/GUICODE.md` is the continuation.

- ~~**The per-floorplan option defaults were not found.**~~ **Settled negative** —
  `GUICODE.md` Finding 19: every option/floorplan register is written once from a received
  CAN config payload, so the defaults are not in the file at all. There is nothing to find.
- ~~**No GuiData *record* semantics were recovered** — only the section header, the table
  directory, and the variable table's DGN field. The group table, binding table, screen
  and widget records are all still `inferred-from-data`.~~ **Recovered — in GuiCode, not
  here** (`GUICODE.md` Findings 10–12, 21): the record decoder, the widget-type dispatch and
  the layout engine are all transcribed. The **group and binding tables** remain
  `inferred-from-data`.
- ~~`FUN_0021B420`, `FUN_0021CCA0` and `FUN_0021E7F8` are unexamined.~~ **Examined 2026-09-10,
  Finding 14.**
- The four `unknown` words in the `0x0021C010` descriptor table are still unknown, except
  that `0x0021F2AC` (`+0x0C`) is now known to hold the sub-container pointer.

## Next steps, in order

1. ~~**Disassemble GuiCode as Thumb-2 at base `0x00240000`**, entry points
   `GuiCode+0x0C`/`+0x10`/`+0x14`/`+0x18`/`+0x1C`. This is now the main line: the
   renderer, the GuiData field readers, and the best candidate for the per-floorplan
   defaults are all in there. 105 KB, and it is ordinary ARM code.~~ **Done — this became
   `docs/GUICODE.md`**, 35 findings covering the record decoder, the renderer, the layout
   engine, the font/glyph mechanism and the theme palette. The call was right about the
   renderer and the field readers; it was wrong about the defaults, which are not in any
   section (Finding 19).
2. ~~In that disassembly, look for reads off the GuiData base and off the framebuffer
   `0xC0000000`~~ **Done** — but keep the *method* note, which still applies to any scan of
   either image: search **`movw`/`movt` pairs and modified immediates as well as literal
   pools**, since Findings 6 and 7 both show constants this project cares about never
   reaching a pool.
3. ~~Examine the four consumers the `hits[:6]` bug hid, `FUN_0021B420` first (it is the
   only one of them with two GuiData loads).~~ **Done 2026-09-10**: Findings 12 and 14.
4. ~~Decompile `FUN_0021BE50` and follow it to the interpreter.~~ Done; there is no
   interpreter (Finding 10).
5. ~~Work outward from `FUN_0021A114`'s GuiData accesses.~~ Refuted in Finding 5.

## A tooling note worth carrying

`mem.getBytes(addr, bytearray)` **silently returns zeros** on this Ghidra build — the
first run of `analyze_lcd.py` reported every constant as absent because of it. The script
now reads the image from disk instead. This is the second time the defect has cost time in
this project; `ghidra/apply_vectors.py` works around the same thing with `mem.getInt()`.
Do not trust `getBytes` here.

---

## The physical panel — labels and connectors, 2026-09-03

`observed-on-hardware`, from a photograph of the unit's back with the rear cover open. Recorded
because it is provenance this project had only inferred from the image files.

**Labels.**

| field | value |
|---|---|
| product | `7in Lite LCD - Firefly`, `PLVSa(AOI),LT`, `FiveM,Ci44,B2v2,RF1v1a` |
| **VERSION** | **11.21** |
| **BOOTLOADER** | **2v2** |
| **FW** | **1v6m** |

Two things follow. The unit **shipped with MCF 11.21**, adjacent to this project's 11.23 corpus
build, which is consistent with 11.23 being the era the coach left the factory. And the label's
**`FW: 1v6m`** is *older* than what the panel reports on the wire today —
`Spyder Controls*7in Lite LCD 1v6r*` (`BUS-BASELINE.md` PC.52) — so the LCD firmware has been
updated since manufacture, `m` → `r`, which matches `MCF-FORMAT.md` §4's decode of the version
block (`ExtFwVer 0x0106`, `FwRlsLetter 0x72` = `'r'`). The label and the wire agree on the
version and differ only in the release letter, exactly as an in-service update would leave them.
The **bootloader is a separate component at `2v2`** and is not any of the four MCF sections.

**Connectors, and the two-port layout confirmed.** A small daughter board on the right carries
**both** USB connectors — the **micro-USB** (the CDC gateway, `LYRA-USB-CAN-GATEWAY.md`) and the
**USB-A** host socket (the firmware-update port). That is the physical form of the port split the
owner established on 2026-09-03: a known-good flash stick on the micro-USB via an OTG adapter does
**not** offer the update menu, because updates are taken on the USB-A socket. A second small board
on the left is the RF module (`RF1v1a` in the product string, the Wireless Switch Panel receiver).

**Unverified, and stated as a lead only:** that same daughter board shows unpopulated multi-pin
headers and silkscreened test points (`TP32` and neighbours). **SWD would be the route to a live
RAM read** and so to proving `BUS-BASELINE.md` PC.56's mechanism — but a header cannot be
identified as SWD from a photograph, and the STM32F7 itself is more likely on the main board behind
the display than on this I/O board. Identifying it needs continuity or voltage checks against known
pins, not inspection.


## Finding 18 — the receive path has five bxCAN filter banks, and bank 4 passes everything to the 18-bit registry — `proven-from-code`, 2026-09-28

The question: does the panel accept a PDU1 frame addressed to some **other** node, such as the
RV-C custom DGN `1EF00h` with DGN-low `BFh` (`19EFBF<SA>`)? It does, and nothing checks the
destination against the panel's own address.

**The banks.** `FUN_002164F0(bank r0, fifo r1, id r2, mask r3)` programs one bank in 32-bit
mask mode: `FR1 = id<<3 | IDE`, `FR2 = mask<<3 | IDE` (`str.w r2,[r0,#0x240]` /
`str.w r3,[r0,#0x244]` @`0x216530`, `r0 = 0x40006400 + bank*8`). Both init paths
(`0x217C18`–`0x217CEA`, `0x2180D6`–`0x218120`) set the same five. `SA` is the byte at
`0x20000018`, the panel's own address:

| bank | id | mask | catches |
|---|---|---|---|
| 0 | `0x00EEFF00 \| SA` | `0x03FFFFFF` | an address claim for the panel's own SA |
| 1 | `0x00EAFF00` | `0x03FFFF00` | a global REQUEST |
| 2 | `0x00EA0000 \| SA<<8` | `0x03FFFF00` | a REQUEST addressed to the panel |
| 3 | `0x00E8FF00` | `0x03FFFF00` | a global ACK |
| 4 | `0` | `0` | **everything else** |

**The dispatch.** The FIFO reader stores the filter match index, `RDTR >> 8`, at frame `+0x11`
(`strb r3,[r1,#0x11]` @`0x2166AC`). `FUN_00218424` switches on it (`tbb` @`0x218434`, indices
0–4). Index 4 lands at `0x2184B6`. Unless the flag at `+0x6A8` passes everything, it takes
`ubfx r0, id, #8, #0x12`, the full 18-bit DGN *including the PDU1 destination byte*, and calls
`0x2174C8`. That function is an exact-equality scan of the registry `FUN_0021A618` fills
(`ldr r4,[r3,#0xc]` list, `ldrb r1,[r3,#8]` count, `cmp r2,r0` @`0x2174E6`).

**Consequences.**
- A PDU1 frame to any destination reaches the variable table if its 18-bit DGN (destination
  included) is registered. `0x19EFBF<SA>` matches a `0x1EFBF` subscription. Stock 13.6 already
  subscribes to PDU1 DGNs addressed elsewhere: `0x0EF00` (vars 110/111) and `0x0EFDF` (238).
- There is no wildcard. `1EFB?` cannot be subscribed as one entry; each destination is its own
  DGN, against the 83-DGN cap.
- `MCF-FORMAT.md` §5.4a's "no bxCAN acceptance banks are involved" is right about the cap: bank 4
  is a pass-all, and the 83 limit is the software registry's. It is wrong in its letter: the banks
  exist, and they divert address claims, REQUESTs and ACKs away from the registry path.
