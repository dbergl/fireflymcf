# G12 code blob (section table `0x67`)

*Started 2026-08-19 as a first pass; as of 2026-09-10 the blob is read to more than 97% (the
`lr`-relative jump table is now followed) and its three functions (init, the 10 ms tick, a jump
helper) are named, with about 60 of the 146 config
cells it touches identified and the 128 sentinel defaults enumerated. The core is an STM32L4
(Cortex-M4), which the device states on the bus (PC.19); the encoding is Thumb-1 only. Sections are
dated and later ones correct earlier ones in place. Findings carry the project's confidence labels.*

## Where it is

| | |
|---|---|
| container section | `G12` (index 3), file `0x102250`, 9,640 bytes |
| table | tag `0x67`, table-relative `0x0AE4`, **6,848 bytes** |
| file span | `0x102D34` … `0x1047F4` |

It is by far the largest table in the G12 section — 71% of it. The other ten tables are the
per-output hardware config (`0x82`) and the automation-rule/registry tables.

## It is ARM Thumb code — `proven-from-code`

Not an assumption inherited from the section decoder: the blob disassembles coherently, with
register-consistent idioms, pc-relative literal pools whose targets land on plausible constants,
and an entry branch as its first halfword.

**Correction to `g12_config.py`'s module docstring**, which called this "a Thumb-2 code blob."
Across the **full recursive walk (3,003 instructions, >90% of the blob)** there is **no
Thumb-2-only encoding at all** — no `movw`/`movt`, no `it`/`ite`, no `tbb`/`tbh`, no `ubfx`, no
`sdiv`. The only 32-bit instructions are the **101 `bl` calls**, and `bl` is a 32-bit encoding
that *exists in Thumb-1* as a halfword pair, so width does not distinguish the architectures.

*(An earlier draft of this file said "100% are 16-bit". That was measured on the linear sweep,
which reached only ~20% of the blob and stopped before any `bl`. The claim was corrected when the
test written to defend it failed on all three builds — the criterion had to become "no
Thumb-2-only mnemonic", not "no 32-bit instruction".)*

Large
constants are built the Thumb-1 way, `movs` + `lsls` (e.g. `movs r1,#0xb4` then `lsls r1,r1,#2`
for 720), which is exactly what a compiler emits when `movw` is unavailable. High registers
(r8–r12) are used once in the whole decoded body.

So the blob is **Thumb-1 only**. Worth carrying either way: **Thumb-2 scanning idioms do
not apply here.** A `movw`/`movt` pair search, which is standard practice for the panel image,
will find nothing in the G12.

**Correction, 2026-08-20 — the *core* is not Cortex-M0.** This paragraph used to conclude "the
G12 runs a Thumb-1 / Cortex-M0-class core", inferring the hardware from the instruction subset.
The device itself says otherwise: during a flash it advertises `McuDevID = 0x04351001`,
`McuFlashSize = 0x100` (256 KB) and `McuRamSize = 0x40` (64 KB) over RV-C
(`BUS-BASELINE.md` PC.19). Read as `DEV_ID:REV_ID` that is **DEV_ID `0x435` — an
STM32L43x/L44x**, a **Cortex-M4**, at rev `0x1001`; the flash and RAM sizes match that part.
`observed-on-hardware`, and it outranks the inference.

The *observation* below is unaffected — the blob really does contain no Thumb-2 — but the
explanation changes: this is a compiler/target-flags choice (or hand-written M0-compatible
code), not a limit of the silicon. Nothing may be concluded about the core from the encoding.
The G12's own map, also from PC.19: bootloader slot `0x08000000`, application `0x08006000`,
config (this section) `0x08038000`, `BtldrVer 0x203`.

### Where it probably lives in the G12's flash — `inferred`, 2026-08-20

Everything below is **blob-relative**, and nothing here needs an absolute base: every branch and
every literal load in the blob is pc-relative. But now that the device's `LdAddr` is known
(`0x08038000` for its config upload, PC.19), the natural reading is that the `G12` payload is
written there verbatim — as the LCD sections are at their own `LdAddr` — which would put the
table-`0x67` blob at **`0x08038000 + 0x0AE4 = 0x08038AE4`**.

**That is a hypothesis, and the section's own bytes do not confirm it.** A scan for u32 words
inside `[0x08038000, 0x0803A5A8)` across the whole payload finds **zero** — consistent with the
pc-relative self-containment above, so the negative is expected rather than contrary. (A scan
for SRAM-range words looks like it corroborates an STM32L4 memory map; it does not. Restricted
to 4-aligned offsets it yields 7 distinct values, and they are Thumb *instruction* pairs
(`0x2000d101` = `movs`/`bne`) repeated through the code, not literal-pool pointers. Recorded
because the unaligned version of that scan is exactly the reconnaissance artifact `CLAUDE.md`
warns about.) Cheapest confirmation available: dump the G12's flash at `0x08038AE4` over its
bootloader protocol, or find any absolute code pointer in the blob.

## Layout — `proven-from-code`

```
+0x0000  b #0x33A            entry branch (target +0x33E)
+0x0004  u32 0x0000000D      \  two data words, meaning unknown
+0x0008  u32 0x00000919      /
+0x000C  code                config-defaults routine, part 1
+0x02BC  literal pool        first word is 0x0000FFFF, the "unset" sentinel
+0x033A  code                config-defaults routine, part 2 — the `+0x0000` branch lands HERE, not +0x033E, and it is
                             not a working entry: `+0x033A adds r1,#0xd0` needs r3 = base and r2 = 0xFFFF, set only at
                             +0x000C/+0x000E. Part 1 falls through into it (`+0x02B8 b +0x2C8` hops the pool). Corrected 2026-09-02
+0x0918  code                the 10 ms service tick (header pointer `+0x08`), one function to +0x1928
+0x19F8  code                the lr-relative table-jump helper, the blob's only real callee
+0x1A60  data                RV-C frame templates
+0x1AB4  "G8 Test\0"         the only genuine ASCII string in the blob
+0x1ABC  00 00 00 00         padding to the table end
```

The apparent "strings" a naive scan finds elsewhere (`'K{D$3b'`, `'#"hSS#h'`, …) are coincidental
printable bytes inside code, not text. `G8 Test` is the only real one — note **G8**, not G12.

The tail templates are 12-byte RV-C records in the same shape as the automation-rule records
(`docs/G12-OUTPUT-CONFIG.md`), e.g. `db fe 19 03 ff 00 21 ff 00 ff ff` — DGN `0xFEDB`
(DC_DIMMER_COMMAND_2) carrying commands `0x21`/`0x22` (33/34), the act-while-held pair the slide
groups use.

## The config-defaults routine — `proven-from-code`

The single largest identified structure, and it explains the blob's odd mnemonic profile
(`movs` 168, `cmp` 108, `bne` 108, `strh` 108, `ldrh` 107 — one idiom repeated).

Two spellings of the same operation, differing only in how the field address and the constant
are formed:

```asm
; A — small offset, small constant
ldrh r1, [r3, #0x0e]     ; read a u16 config field
cmp  r1, r2              ; r2 = 0x0000FFFF, the "unset" sentinel
bne  keep                ; already set? leave it alone
movs r1, #0x1e           ; else write the default …
strh r1, [r3, #0x0e]     ; … back into the field
keep:

; B — larger offset, or a constant too big for movs
movs r2, r3
adds r2, #0xd2           ; field address = base + 0xD2
ldrh r0, [r2]
cmp  r0, r1              ; r1 = the sentinel
bne  keep
ldr  r0, [pc, #0x2b4]    ; default from the literal pool
strh r0, [r2]
```

So: **a u16 field reading `0xFFFF` is "unset", and the G12 writes a hardcoded default into it.**

**Complete extraction, 2026-08-24 — `g12code.config_defaults`.** An ad-hoc script previously
reported 103 sites / 94 distinct pairs; matching fixed instruction sequences is what limited it,
because the routine spells the same operation several ways (`movs`, `movs`+`lsls`, a literal-pool
`ldr` for the constant; an immediate offset, `adds rD,rB,#n`, or `movs rD,rB`+`adds rD,#n` for the
address). `config_defaults` **simulates the registers** instead, so the address arithmetic falls
out rather than needing a case per spelling:

| build | sites | distinct offsets | struct range |
|---|---|---|---|
| 11.23 | 80 | 80 | — |
| **13.6** | **99** | **99** | `+0x000 … +0x138` |
| 14.7 | 93 | 93 | — |

All fifteen pairs this document already tabulated reproduce exactly
(`test_defaults_reproduce_the_documented_table`). Instruction boundaries come from the
recursive-descent walk, never a linear sweep, so a literal pool is never decoded as code.

A sample of the first block, raw:

| offset | default | offset | default | offset | default |
|---|---|---|---|---|---|
| +0x004 | 720 | +0x016 | 1000 | +0x028 | 2048 |
| +0x00A | 768 | +0x018 | 60 | +0x02C | 180 |
| +0x00E | 30 | +0x01A | 1080 | +0x0A0 | 255 |
| +0x010 | 30 | +0x01C | 1800 | +0x0E0 | 80 |
| +0x012 | 255 | +0x01E | 1800 | +0x0E2 | 200 |

### What this is *not* yet

- **The struct is unidentified.** Nothing here says which field is which. Do not map a field to
  a feature by matching a number to a value you expect to see — that is exactly the
  reconnaissance-artifact mistake this project has made and retracted before.
- **The scale is unknown.** `GUICODE.md` Finding 43 established 0.05 V/unit for the *panel's*
  AES template pool, and under that scale 1000/1080 read as 50.0/54.0 V, which is suggestive
  next to the coach's 51.0/53.0 V AES setpoints and next to `RVC-GAPS.md` §3's still-missing
  AGS/AES *limits*. **Suggestive is not evidence.** The scale has not been shown to apply to this
  struct, and several of these fields are plainly not voltages (30, 1, 0, 255).
- ~~**Some literal-derived defaults are not trustworthy yet.**~~ **MISDIAGNOSED — resolved
  2026-08-24.** This bullet blamed the implausible values (30721, 5144, 9467, 9568) on a bad
  pc-relative pool address. **The addresses were right all along:** across all three builds,
  **0 of 36 / 84 / 151** `ldr rX,[pc,#N]` sites resolve outside the pool the recursive-descent
  walk independently claims as data (`test_the_pc_relative_literal_calculation_agrees_with_the_walk`).
  What was wrong was the assumption that every cell holds a **scalar**. `+0x02A` is selector
  `0x15`'s cell, and `0x15` is a **packed byte pair** — the coach's captured values are `0x2801`
  and `0x5A00`, the AGS burst writes `15 := 0x2801` (`GUICODE.md` Finding 50), and the blob's own
  code at `+0x0E02` builds it as `((x + 30) << 8) | (old & 0xFF)`. `30721` is `0x7801`, a pair,
  not an implausible number. *An out-of-range value is evidence about the unit, not automatically
  about the extraction.*

## Across the three builds — `proven-from-data`

Every build carries a table `0x67`, all are 100% 16-bit Thumb, and all begin with the same entry
branch halfword `0xE19B`. Beyond that they are almost entirely different code:

| build | size | bytes identical to 13.6 (prefix) | `G8 Test` |
|---|---|---|---|
| 11.23 | 3,264 | 8 | yes |
| 13.6 | 6,848 | — | yes |
| 14.7 | 8,680 | 2 | **no** |

Two things follow. The blob **grows monotonically with firmware version and feature count**, and
it is **not** a fixed program with a variable data area — it diverges within the first 8 bytes,
so it is per-build generated/compiled code, in the same way the automation-rule tables are
coach-specific while the hardware-config table `0x82` is byte-identical everywhere.

**The defaults struct is the stable part.** Restricting to the reliable `movs`-derived idiom,
each build defines ~52 distinct defaults over the same offset range `+0x00 … +0x3E`. ~~Of the
27 offsets all three share, 25 carry an identical default and only two differ: `+0x01C`
7200/1800/7200 and `+0x02E` 1/0/1.~~ **Corrected 2026-09-02, with the complete extractor (the
register-indexed form, below):** `+0x02E` (selector `0x17`) is **0 on all three builds** —
11.23 `+0x00FC movs r1,#0 / +0x00FE strh r1,[r3,#0x2e]`, 13.6 `+0x00E6/+0x00E8`, 14.7
`+0x00EA/+0x00EC` — never read, never written outside init, no template, no rule, no bus report;
there is nothing to name. And **eight** shared offsets differ, not two:

| offset | sel | 11.23 | 13.6 | 14.7 | name |
|---|---|---|---|---|---|
| +0x016 | 0x0B | 244 | **1000** | 244 | start threshold |
| +0x01A | 0x0D | 270 | **1080** | 270 | stop-at voltage |
| +0x01C | 0x0E | 7200 | **1800** | 7200 | time at stop volts |
| +0x01E | 0x0F | 900 | 1800 | 1800 | min run time |
| +0x028 | 0x14 | 1024 | 2048 | 2048 | unknown |
| +0x02C | 0x16 | 720 | **180** | 720 | max engine run time |
| +0x062 | 0x31 | 250 | **600** | 244 | start-at voltage |
| +0x06A | 0x35 | 256 | 250 | 250 | unknown |

plus, among the option-range cells the old extractor never saw, `0xD8` –/2/1, `0xEF` 1/0/0,
`0xF1` 11/13/14, `0xF2` 23/6/7, `0xF4` 7/13/13. **13.6 is the outlier because its whole
AGS/AES block seeds a third, deliberately recognisable set** — `{0C 60, 0D 1080, 0E 1800, 0F
1800, 16 180, 2B 5632, 2C 2048, 31 1000}` — which `+0x0C14`–`+0x0C66` tests for *exactly* and
raises selector `0x95` when all eight match (`proven-from-code`; see "The config struct named"
below). Read as "parameters still at fresh-seed values" (inference); 14.7 went back to the 12 V
AGS seeds except `0x0F`. 11.23/14.7's 7200 for `0x0E` is PC.4's AGS-column value. That 7200/1800 pair is worth noting for what it does to the
scale question above: as seconds they are **2 hours and 30 minutes**, which is a far more natural
pair than the 360.0 V / 90.0 V the panel's 0.05 V/unit scale would give. Combined with the many
small values (30, 60, 180, 250), the struct is very likely a **mix of units** — timeouts,
counts, flags and possibly voltages — not one scale throughout. Another reason not to apply
Finding 43's voltage scale here without separate evidence.

## Recursive descent — `src/fireflymcf/g12code.py`, >90% coverage

A linear sweep reaches ~20% and stops at the first literal pool, which it cannot distinguish
from code. `g12code.analyse` follows control flow from seed entry points, stops at returns, and
marks each pc-relative literal target as **data** so the walk never tries to decode it.

**The finding that unlocked it: header word `+0x08` is a Thumb function pointer.** It is odd
(Thumb bit set), and with the low bit cleared it lands inside the blob — on 11.23 and 13.6 at
the exact first byte a two-seed walk cannot reach (`0x6FC` / `0x918`), on 14.7 inside that same
unreached region. It is a third entry point, not data. Adding it is the difference between 34%
and 94% on 13.6, which `test_the_header_pointer_is_what_unlocks_the_coverage` pins.

| build | size | coverage (flow only) | coverage (with jump table) | instructions | "functions" (walker) | returns |
|---|---|---|---|---|---|---|
| 11.23 | 3,264 | 92.3% | **98.1%** | — | 4 | 3 |
| 13.6 | 6,848 | 94.4% | **97.1%** | 3,003 | 90 | 3 |
| 14.7 | 8,680 | 95.0% | **97.2%** | — | 104 | 7 |

The flow-only column is the recursive walk following branches, calls and returns; the second
column adds the one control-flow form it cannot follow, the `lr`-relative jump table
(`follow_jump_tables=True`, the default; see "The per-instance tank bucketer" below). With it the
only gaps left are the header, the data tail, and — on 14.7 — one 20-byte byte-copy leaf.

**The "functions" column is an artifact — corrected 2026-09-02.** 13.6 has **three** functions:
`push` at `+0x0012` / `+0x0918` / `+0x19F8`, `pop`/return at `+0x08E2` / `+0x1928` / `+0x1A0A`
(init, the 10 ms tick, and a 10-instruction `lr`-relative table-jump helper called from
`+0x118A`). Every other `bl` target is a **long-branch arm** inside the tick — the pattern the
clamp section already noted, and it is the rule throughout. Side effect worth knowing: treating
`bl` as a call makes the walker decode pool words after some `bl`s as code (`+0x173C`–`+0x174B`
is the pool `+0x1740 = 0x19FFDC00`, `+0x1748 = 930`).

13.6's instruction mix over the full walk: `movs` 579, `strh` 356, `cmp` 322, `ldrh` 284,
`ldr` 281, `adds` 222, `bne` 200, `lsls` 138, `blx` 118, `bl` 101, `b` 55, `subs` 48.
~~**101 call sites**, so there is a real call graph — 11.23 has only 4 functions against 13.6's 90.~~
The 101 `bl`s are long branches (above); there is one real callee.

### What is still unclaimed (13.6: 192 bytes after the jump table is followed — header + data tail)

| span | bytes | what it is |
|---|---|---|
| `+0x0000` | 12 | the header itself — branch + the two words |
| `+0x1A0C` | 180 | the **data tail**: RV-C templates + `G8 Test` (correctly *not* decoded) |
| ~~`+0x1858`, `+0x192A`, `+0x1948`~~ | 188 | **NOW REACHED — 2026-09-10.** These are the arms of the per-instance tank bucketer, entered through the `+0x19F8` `lr`-relative jump table; the walker follows it as of `follow_jump_tables`. They read selectors `0xCC`/`0xCD`/`0xCE` (the tank thresholds), which is what first flagged them as live (owner-adjusted on the bus). See "The per-instance tank bucketer" below. |

~~**Nothing here bears on the ignition/awning interlock yet** — that is the rule record in table
`0x62`, not this blob.~~ **SUPERSEDED 2026-08-24 — the interlock IS in this blob**, and the tail
templates named just above are the frames it sends. See "The frame table and the AWNING IGNITION
INTERLOCK" below. *(The `0x62` half of that sentence was retracted separately: PC.25 refuted the
rule-record chain outright — the five `0x1FFB7` rules are `TANK_STATUS`, not an awning trigger.)*

> **The `0x62` rule records are now decoded — `GUICODE.md` Finding 52** (2026-08-24). All 151
> fixed-stride records across tables `0x20`/`0x30`/`0x61`/`0x62` are 12-byte **transmit
> templates**: `<u16 DGN lo><u8 DGN-hi+flags><u8 substitution mask><8-byte payload template>`,
> the payload in the panel's own `[7,6,5,4,11,10,9,8]` order, a masked byte naming a config
> selector whose stored value the G12 substitutes (adjacent same-selector slots = one LE u16).
> Validated against captured frames in both directions, including the falsifiable negative that
> selector `0x99` drives no rule and relays nothing. Tooling: `src/fireflymcf/g12rules.py`,
> `cli g12-rules`. Editing a rule is a same-size byte edit whose blast radius is that byte plus
> the G12 section checksum, so it ships as a **G12-only flash**.
>
> That decode is filed in `GUICODE.md` rather than here because the records are a **data table**,
> not part of this blob. It does not close the question above: whether table `0x67`'s code also
> participates in the automation program is still open.

## The frame table and the AWNING IGNITION INTERLOCK — `proven-from-code`, 2026-08-24

The dig's first target, and it closes. `BUS-BASELINE.md` **PC.27** observed the G12 broadcasting
an RV-C `DC_DIMMER_COMMAND_2` **command 33 (Lock)** on channel 3 after ignition-on, and command 34
(Unlock) after ignition-off; `GUICODE.md` Finding 52 proved **no rule record can emit `0x21`/`0x22`**,
so the frames had to be code-generated. They are, and this is the code.

### The data tail is a 14-entry frame table

The 180-byte tail this document already called "RV-C frame templates" is **14 fixed 12-byte
records starting at `+0x1A0C`** (the `00` at `+0x1A0C` is the record's own first byte, not padding
— the earlier reading of these as `db fe 19 03 …` was off by one and produced a record shape that
did not match any captured frame):

```
<u8 flags/pad = 00><u16 DGN-low LE><u8 CAN-id high byte><8-byte payload>
```

| # | offset | record | resulting frame | on the wire? |
|---|---|---|---|---|
| 0 | `+0x1A0C` | `00dafb19bf01000000000000` | `19FBDA9C#BF01000000000000` | **yes** (PC.24) |
| 1 | `+0x1A18` | `00dafb19bf02000000000000` | `19FBDA9C#BF02000000000000` | no |
| 2 | `+0x1A24` | `00dafb19bf00000000000000` | `19FBDA9C#BF00000000000000` | **yes** (PC.24) |
| 3 | `+0x1A30` | `00dbfe1912ff00030000ffff` | `19FEDB9C#12FF00030000FFFF` | no |
| 4 | `+0x1A3C` | `00d9fe19ff96f70fffffd3ff` | `19FED99C#FF96F70FFFFFD3FF` | no |
| 5-10 | `+0x1A48`…`+0x1A84` | instances `0x23`/`0x19`, commands 4 / 5 / 21 | — | no |
| 11 | `+0x1A90` | `00dbfe1903ff0003ff00ffff` | `19FEDB9C#03FF0003FF00FFFF` | **yes** (PC.27) |
| 12 | `+0x1A9C` | `00dbfe1903ff0021ff00ffff` | `19FEDB9C#03FF0021FF00FFFF` | **yes** (PC.27) |
| 13 | `+0x1AA8` | `00dbfe1903ff0022ff00ffff` | `19FEDB9C#03FF0022FF00FFFF` | **yes** (PC.27/28) |

**Six of the fourteen reproduce a captured frame byte for byte**, and the CAN id's high byte is
carried literally in the record (`0x19`), which is why the table needs no id construction.

**The payload is in WIRE order — no reordering.** That is a real structural difference from the
panel's binding tables and from the `0x62` rule records, whose payloads are stored
`[7,6,5,4,11,10,9,8]` (`GUICODE.md` Finding 52, `newbutton.binding_payload`). Do not carry the
panel's byte order into this table.

### It is the blob's ONLY pc-relative data reference

Every one of the **19 `add rX, pc` sites in the whole 6,848-byte blob** resolves into this table
(18 to the base `+0x1A0C` or the secondary anchor `+0x1A8C`, one to the `G8 Test` string). The
idiom is the standard PIC pair — `ldr rX,[pc,#N]` loading a *pc-relative displacement*, then
`add rX, pc` — which is why the earlier absolute-literal and `ADR` scans both returned **zero**:
there is no absolute pointer to find. A record is selected with `adds rX,#imm` and copied with a
single register triple:

```asm
ldr  r6,[pc,#0x2cc]      ; displacement
add  r6,pc               ; r6 = +0x1A8C
adds r3,r6,#4            ; = +0x1A90, record 11
ldm  r3!,{r0,r2,r7}      ; 12 bytes out of the table
stm  r1!,{r0,r2,r7}      ; ... into the outgoing frame buffer
blx  ip                  ; transmit  (fn ptr from [r4,#0x18])
```

The panel does exactly this with `ldm {r0,r1,r2}` on its own `0x1FED9` templates (Finding 23), so
**both devices emit CAN frames by block-copying a static 12-byte record**. Same design, two
independent codebases.

### The interlock itself — `+0x10A8` … `+0x10EC`, with its unlock tail at `+0x183A`

```asm
+10a8  ldr  r3,[r4]          ; r3 = the config/state struct
+10ac  adds r3,#0x52         ;   +0x52 = last actioned state
+10ae  adds r2,#0x50         ;   +0x50 = current sense state
+10b0  ldrh r1,[r2]
+10b2  ldrh r3,[r3]
+10b4  cmp  r3,r1
+10b6  beq  +0x10ee          ; unchanged -> do nothing        <<< EDGE DETECT
+10b8  ldr  r3,[r4,#0x18]    ; the transmit function
+10bc  mov  ip,r3
+10be  cmp  r1,#0
+10c0  bne  +0x10c4
+10c2  b    +0x183a          ; current == 0 -> UNLOCK path
+10c6  ldr  r6,[pc,#0x2cc]
+10c8  add  r6,pc            ; +0x1A8C
+10ca  adds r3,r6,#4         ; record 11 = ch 3 command 3 OFF
+10cc  ldm  r3!,{r0,r2,r7}
+10ce  stm  r1!,{r0,r2,r7}
+10d2  blx  ip               ; SEND OFF
+10d6  adds r6,#0x10         ; record 12 = ch 3 command 33 LOCK
+10d8  ldm  r6!,{r0,r1,r2}
+10da  stm  r3!,{r0,r1,r2}
+10e0  blx  r3               ; SEND LOCK
+10e2  ldr  r3,[r4]          ; latch:
+10e6  adds r3,#0x50         ;   [+0x52] = [+0x50]
+10ec  strh r3,[r2]
```

and the unlock tail:

```asm
+183a  movs r1,r5
+183c  ldr  r3,[pc,#0x1a8]
+183e  add  r3,pc            ; +0x1A8C
+1840  adds r3,#0x1c         ; record 13 = ch 3 command 34 UNLOCK
+1842  ldm  r3!,{r0,r2,r6}
+1844  stm  r1!,{r0,r2,r6}
+1848  blx  ip               ; SEND UNLOCK
+184a  b    +0x10e2          ; back to the same latch
```

**This is PC.27 frame for frame and order for order** — OFF immediately followed by LOCK on the
rising edge, UNLOCK alone on the falling edge, one latch afterwards. It also explains **PC.28**
without further assumption: the trigger is a plain `cmp` on a *state cell*, so an AES auto-start
that raises the sense line locks the awning exactly as a key-turn does. There is no key, no
ignition concept and no timer anywhere in this code — only `[+0x50] != [+0x52]`.

### What this does NOT establish

- **Nothing in the blob writes `+0x50`.** All eight accesses to it are reads (`+0x0B70`, `+0x0CBC`,
  `+0x0CEA`, `+0x0DA2`, `+0x10AE`, `+0x1560`) plus the two latch sites on `+0x52`. Its writer is in
  the G12's **application** image at `0x08006000`, which is not in the MCF at all. So "`+0x50` is
  the ignition sense" is `inferred` — from PC.24/27/28's behaviour, not from a store.
- `+0x50` and `+0x52` are **also config fields**, defaulted to 0 by the sentinel routine
  (`+0x012A`, `+0x0138`). Persistent state and live state share the struct.
- `+0x50` is compared against **1** as well as 0 (`+0x0CEA`, `+0x1560` `cmp #1` / `bls #1`), so it
  is not proven to be a boolean.
- **Channel 18 appears to be gated on the same cell.** Record 3 (`19FEDB9C#12FF00030000FFFF`,
  instance `0x12` = 18, command 3 OFF) is referenced from **four** sites at `+0x0CDA`, `+0x0D3C`,
  `+0x0D7C` and `+0x0DC6`, inside the same region that reads `+0x50`. Unread, and never observed on
  the wire. PC.27 watched channel 3 only.

### Two things this hands to other open questions

**`0xBF` is decoded structurally** — the `BUS-BASELINE.md` PC.24 frame *"undecoded by anyone"*.
Records 0/1/2 are `1FBDA` instance `0xBF` carrying `payload[1]` = 1, 2, 0 with an **all-zero tail**,
which is exactly what PC.24 saw and exactly what distinguishes them from the input-pin templates in
table `0x20` (those carry `FF` tails). So `0xBF` is **not an input pin**: it is a code-emitted
three-state status from this blob. **State 2 has never been observed.** *(An earlier version of
this sentence said "six call sites" and listed `+0x0DF2` and `+0x1666` among them. Both are wrong:
they load the table base for **record 4**, whose `adds r3,#0x30` sits six bytes after the
`add rX,pc` rather than immediately after it, so the automated anchor map attributed them to
record 0. There are **four** emitter sites — corrected below, where the mechanism is decoded.)*

**The G12 does have a `0x1FED9` transmit template** — record 4, a `D3 FF` *read-back* of selector
`0xF7`. Worth carrying against `BUS-BASELINE.md` PC.33/PC.34, which found the G12 sourcing `1FED9`
in **zero** frames across thirteen captures: the capability is in the code, it simply has not
fired. That is a second dormant `1FED9` transmitter beside rule record 59, and it is a *different*
one — record 59 is a group-`0x96` write of `E3`/`E4`, this is a read of `0xF7`.

## The `+0x0CAA` region — four AES START/STOP timeouts — `proven-from-code`, 2026-08-24

Opened because it was the most concentrated unread code left and it reads the same state cell
`+0x50` as the awning interlock. **It is not a second interlock.** It is four instances of one
*timeout*, and only one of the four looks at `+0x50` at all.

Record 3 is `19FEDB9C#12FF00030000FFFF` — instance `0x12` = **channel 18**, which
`answer_key.CHANNELS` names **`AES START/STOP`**. So this region is the G12's **auto-engine-start
watchdog**: it drops the start/stop line when a timer runs out.

### The shape, repeated four times

```asm
ldr  r3,[r4]                  ; the state struct
ldrh r2,[r3, <TIMER>]
cmp  r2,#0
bne  skip                     ; not expired -> nothing
  ... zero a fixed group of fields, set one status cell ...
  ldr  r3,[pc,#N] / add r3,pc / adds r3,#0x24    ; = +0x1A30, record 3
  ldm  r3!,{...} / stm r2!,{...}
  blx  [r4,#0x18]             ; SEND channel 18 OFF
  strh #-1,[r3, <TIMER>]      ; disarm
skip:
```

| block | timer cell | its status cell | extra condition |
|---|---|---|---|
| `+0x0CAA` | `[base+1000]` | `[base+0xEA]` | `[base+0x50] == 0` |
| `+0x0D0C` | `[base+996]` | `[base+0xEE]` | `[base+0xA6] <= 0xFFFD` (i.e. *set*) |
| `+0x0D5A` | `[base+994]` | `[base+0xF0]` | none |
| `+0x0D90` | `[base+998]` | `[base+0xEC]` | none (but calls `+0x1648` when `[+0x50] != 0`) |

Four adjacent u16 timers at `+994`/`+996`/`+998`/`+1000` and four adjacent u16 status cells at
`+0xEA`/`+0xEC`/`+0xEE`/`+0xF0` — one quartet of instances, not four unrelated features.

### The timer discipline, and it is a closed cycle — every literal cited

- **Expiry disarms.** The block runs only when the cell reads **0**, and its last act is
  `subs #1` on that zero, storing **`0xFFFF`**. So it fires exactly once.
- **`0xFFFF` means disarmed.** `+0x1310` re-arms only after `cmp r0, 0xFFFF` matches
  (literal `+0x1398` = `65535`), writing **60** (`+0x1320`: `adds r0,#0x3b` onto `r0 = 1`).
- **The tick floors at 0 and skips the disarmed.** `+0x18AA`: `subs r1,r3,#1; uxth r1,r1;
  cmp r1, 0xFFFD; bhi keep` (literal `+0x19F0` = `65533`). `0 → 0xFFFF > 0xFFFD →` keep 0;
  `0xFFFF → 0xFFFE > 0xFFFD →` keep `0xFFFF`. It is a loop over an array bounded by **172**
  entries (`cmp r5,#0xac`) with the live count in `[r4+4]`.
- The `bhi` against **65533** at `+0x0D20` is the same sentinel family — an **unset guard** on
  `[base+0xA6]`, not a physical threshold. *An earlier reading of that instruction as a magnitude
  comparison would have invented a limit that does not exist.*

~~**The tick rate is ~1 Hz — `inferred`, with one measurement carrying it (2026-08-24).**~~
**RETRACTED 2026-10-21: the tick runs every 10 ms, and the timer bank below it counts seconds
through a 100-tick divider the reading below missed.** See "The tick is 100 Hz" in "Executing
the blob". The conclusion that a timer armed to 60 is about a minute still stands; the tick
rate does not. Original text kept for the record: Nothing in
the blob sets the loop's period; its caller is in the G12 application at `0x08006000`, which the MCF
does not contain. Three independent things now point the same way, and they are not equally strong:

- **The load-bearing one is a measurement.** `0xBF`'s timer is armed to **1** (`+0x0B06`), and
  `BUS-BASELINE.md` PC.24 has `BF 01` at t and `BF 00` at **t + 714 ms**. A timer armed to 1 fires
  between 0 and one tick after arming, so the tick is **≈1 s**. At 10 Hz that gap would be ≤100 ms;
  at 100 Hz, ≤10 ms.
- **PC.24 independently measured a 1.000 s periodic in the G12** — every `0x1FBDA` input instance
  re-sends at exactly that cadence while held. A 1 Hz service loop exists; this bank plausibly
  rides it.
- **The owner reports the panel labelling the related config value in seconds** — cell `0x0C`
  (`+0x18`, "time at start volts") shows **`60 Sec`** on the ELECTRICAL page, and its default is 60.

**So a timer armed to 60 is about a minute.** *Stated as inference, not proof:* the third point is
the weakest — cell `0x0C` and the timer at `+1000` are different cells that share the number 60,
and matching numbers is exactly what produced the wrong unit for the tank thresholds. It is the
714 ms measurement that does the work here.

### Cell `0x0C` — "time at start volts"

Owner-identified from the screen, 2026-08-24: the ELECTRICAL page shows it as **`60 Sec`**, and 60
is its default.

~~**A clamp to 60 means the panel cannot set it above 60 and have it stick.**~~ **RETRACTED the
same day — `BUS-BASELINE.md` PC.37.** This section read `+0x14C6`'s
`ldrh r2,[r3,#0x18] / cmp r2,#0x3c / bhi / movs r2,#0x3c / strh` as a clamp on this cell. On the
coach the owner walked the value **120 → 310 → 10** and **every step held**. The error: `r3` was
assumed to hold the config-struct base in that function, without the `ldr r3,[r4]` that establishes
it at every other site quoted here. *Either that path does not run, or it is not this cell — but
the claim was never supported.*

What survives: the cell is read and written from several sites (`+0x0A14`, `+0x0C14`, `+0x1260`,
`+0x09DA`, `+0x11CE`), so it is not a passive setting.

### Record 4 is on a live path — the G12's dormant `0x1FED9` transmitter

`+0x0DDC`-`+0x0E00`, immediately after the four blocks:

```asm
movs r5,#0xf7 / lsls r5,r5,#1     ; r5 = 494
ldrh r2,[r3,r5]
cmp  r2,#0x3c                      ; 60
bhi  +0x0dec
bl   +0x1658
movs r2,#0x3c
strh r2,[r3,r5]                    ; [base+494] = 60
ldr  r3,[pc,#0x240] / add r3,pc / adds r3,#0x30   ; = +0x1A3C, record 4
ldm  r3! / stm r2! / blx [r4,#0x18]               ; SEND 19FED99C#FF96F70FFFFFD3FF
```

So the `0x1FED9` read-back of selector `0xF7` is **not merely present in the table, it is emitted
from a reachable path**. It has still never been seen on the wire (`BUS-BASELINE.md` PC.33/PC.34:
zero G12-sourced `1FED9` in thirteen captures), so the guarding condition has not been met — but
"the G12 never sends `1FED9`" is now a statement about conditions, not about capability.

*(This reference is why the automated `add rX,pc` map missed record 4: the `adds r3,#0x30` is six
bytes after the `add r3,pc`, not immediately after it. `g12code.add_pc_sites` reports the anchor;
resolving the record needs the following `adds`, which may be separated.)*

**A striking coincidence, flagged rather than believed.** The offset `494` is built as `0xF7 << 1`,
and the record sent two instructions later names selector **`0xF7`**. `base + 2*selector` would be
a selector→cell mapping of exactly the shape the panel uses (`GUICODE.md`: *"`var` is one number
throughout — entry index == slot index == GuiCode's `2*var` immediate"*), and `0xF7`'s value is
independently known to be **60**: Finding 50 records the AGS-selection burst emitting
`F7 := 0x3C`, and this code clamps `[base+494]` to `0x3C`. That is three things agreeing.

**It is still not established**, and the failure mode is the one this project has paid for before:
the selector in the frame comes from the *record's payload byte*, not from `r5`, so nothing here
links the two except the number.

**RESOLVED — the mapping holds. `proven-from-data`, 2026-08-24.**

With the defaults extractor completed (99 pairs on 13.6 instead of the ad-hoc script's partial
set), the test has enough overlap to mean something. If `base + 2*selector` is the config array,
the defaults should agree with the G12's own `0x15FCE` sweep wherever the coach has not changed a
value. Scored against PC.34's 42 live selectors:

| mapping | testable | exact | **exact on values other than 0/1** |
|---|---|---|---|
| **`2*sel`** | **20** | **11** | **5** |
| `2*sel − 2` | 17 | 3 | 1 |
| `2*sel + 2` | 22 | 2 | 0 |
| `2*sel ± 4` | 18 / 19 | 3 / 1 | 0 / 0 |
| `2*sel ± 6` | 16 / 18 | 0 / 0 | 0 / 0 |
| `1*sel` | 15 | 4 | 0 |
| `4*sel` | 12 | 2 | 0 |
| shuffled selectors, 200 trials | — | — | **mean 0.12** |

**The non-trivial column is the one that discriminates.** A config struct is full of 0s and 1s, so
"some values agree" is cheap and every neighbouring alignment collects a few. Agreement on values
like **5632**, **2048**, **1800**, **200** and **60** is not cheap, the candidate is the only
mapping that gets any, and a permutation control averages 0.12. The nine disagreements are all
*same-kind drift* — `0x05` 768→832 and `0x0D` 1080→1088 read as 38.4→41.6 V and 54.0→54.4 V at
the 0.05 V/unit scale; `0x0E` 1800→900 as 30→15 minutes.

**Three independent corroborations, none of which uses the scoring above:**
- **Selector `0x31` → offset `0x62`, live value 1020.** `0x31` is AES **"Start At"**, and
  `GUICODE.md` **Finding 43** independently pins the panel's own template constant at **1020**
  (51.0 V at 0.05 V/unit). A panel-side literal and a G12-side config cell, derived by routes that
  share no code, holding the same number.
- **Selector `0x15` → offset `0x2A`, and the blob packs that exact cell.** `+0x0E02` builds
  `[base+0x2A]` as `((x + 30) << 8) | (old & 0xFF)`; `0x15`'s captured values (`0x2801`, `0x5A00`)
  are byte pairs and the AGS burst writes `15 := 0x2801`. The code manipulating the cell and the
  wire values agree that it is packed.
- **Selector `0xF7` → offset 494**, which `+0x0DDC` clamps to **60**, and Finding 50 records the
  AGS burst emitting `F7 := 0x3C` = 60.

**It is the same convention the panel uses** — `GUICODE.md`: *"`var` is one number throughout —
receive-filter entry index == slot index == GuiCode's `2*var` immediate."* Two independent
codebases, one addressing rule.

Shipped as `g12code.config_cell_offset` / `config_defaults_by_selector`, with the shift controls
themselves in the test (`test_the_g12_addresses_a_config_value_by_twice_its_selector`) so the
claim fails if a future build makes it indistinguishable from noise.

**Scope, stated:** validated over selectors `0x00`–`0x9B`, the range where defaults exist. ~~The
option selectors (`0xE3`–`0xEF`, `0xF5`, `0xF7`) are above it and have no default — which is
what Findings 19/42 predict.~~ **False by the bytes — corrected 2026-09-02.** `config_defaults`
does not model the register-indexed spelling (`_mem_operand`: "register-offset form: not
modelled"), and the routine's `+0x050E`–`+0x06C8` run uses exactly that — `movs r2,#sel / lsls
r2,r2,#1 / ldrh r0,[r3,r2] / cmp r0,r1 / bne / movs r0,#v / strh r0,[r3,r2]` — to default **29
more selectors** (`AE AF B5 B6 B7 B8 BA BC BE CC CD CE D7 D8 E3 E4 E5 E6 E8 E9 EB EC EF F1 F2
F3 F4 F5 F7`): `+0x05DC` `E3 := 0`, `+0x0624` `E6 := 1`, `+0x0640` `E9 := 1`, `+0x066A` `EF := 0`,
`+0x06C0` `F7 := 60`, `+0x05C0` `D7 := 1`, `+0x05CE` `D8 := 2` on 13.6. So the routine defaults
**128** selectors, not 99. The Finding 19/42 point survives in a weaker form: these are
`0xFFFF`-only seeds, and the panel's floorplan/mode bursts overwrite them afterwards (every
seeded-and-templated selector seen on the bus reports the *panel's* value — `0x0D` 1080→1060,
`0x16` 180→115, `0x31` 600→1020, `0xD7` 1→0, `0xD8` 2→0, `0xE9` 1→0, `0xEF` 0→3).

**Tooling gap CLOSED — 2026-09-10.** `config_defaults` now models the register-indexed address
(`_mem_target` resolves `[rB,rM]` when `rM` holds a known immediate from `movs`+`lsls`), so it
recovers the whole routine: **138 sentinel-filled fields on 13.6**, of which `config_defaults_by_selector`
reports the **128** below `+0x200` (the config array) and drops the ten runtime cells at/above it
(`+0x204`..`+0x218`, the per-instance status bank). It now covers the option selectors as the
placeholders above — `E3 0`, `E6 1`, `E9 1`, `EF 0`, `F7 60`, `D7 1`, `D8 2`, and floorplan
`F5 0` — which the panel overwrites with the coach's values. 11.23 seeds 105, 14.7 seeds 125.

### The region's cells, named — and one derivation confirmed on the wire

With `offset = 2 x selector` established, the region's anonymous cells get selector numbers.
**That buys much less than it looks like it should, and the negative is the larger half:**

| cell | selector | role in the region | ever reported by the G12? |
|---|---|---|---|
| `+0x02A` | `0x15` | the packed pair built at `+0x0E02` | **yes** |
| `+0x050` | `0x28` | the state cell the awning interlock also reads | **never** |
| `+0x09E` | `0x4F` | re-arm gate (`== 2`) | **never** |
| `+0x0A6` | `0x53` | block C's guard | **never** |
| `+0x0E8` | `0x74` | re-arm gate (`== 1`) | **never** |
| `+0x0EA` | `0x75` | block A's status cell | **yes** |
| `+0x0EC`/`+0x0EE`/`+0x0F0` | `0x76`/`0x77`/`0x78` | blocks E / C / D status | **never** |

**Seven of eleven are invisible to the SWEEP** — but not unreadable: the `D3` read-back returns
any selector on demand, and all 256 cells were dumped on 2026-09-03 (`BUS-BASELINE.md` PC.49).
The G12's `0x15FCE` sweep reports 42 selectors; the config array has cells for at least 157.
**The sweep is a subset, not a dump**, so the mapping gives
traceability for some cells and nothing at all for others — including `+0x50`, the one that
matters most. Pinned as a negative by
`test_most_of_the_watchdog_regions_cells_are_invisible_on_the_bus`, which fails if any of the
seven ever appears.

**Also worth carrying: the four timers are NOT config cells.** Selectors are one byte, so the
config array occupies `+0x000 … +0x1FE`. The timers at `+994`/`+996`/`+998`/`+1000` sit well
above it, as does everything else the region uses for live state. Config below `+0x200`, runtime
above.

#### Selector `0x15`'s high byte is DERIVED, and the G12 overrides the panel

`observed-on-hardware`, across **24 captures**. The one cell here that *is* on the bus turns out
to close the loop between this code and the wire:

```asm
+0dec  movs r2,#0x3c / strh r2,[r3,r5]     ; config[0xF7] := 60      (r5 = 494 = 2*0xF7)
...
+0e04  ldrh r2,[r3,r5]                     ; = 60
+0e06  ldrh r1,[r3,#0x2a]                  ; the old cell 0x15
+0e08  adds r2,#0x1e                       ; + 30                    -> 90
+0e0a  lsls r2,r2,#8
+0e0c  uxtb r1,r1                          ; keep the old LOW byte
+0e0e  orrs r2,r1
+0e10  strh r2,[r3,#0x2a]                  ; [cell 0x15] = 0x5A | old_low
```

`60 + 30 = 90 = 0x5A`. And on the bus:

- **The panel writes `0x2801` — always.** Every one of the 24 `19FED99F#FF96150F0128D1EA` frames
  in the corpus carries the same value, `(40, 1)`.
- **The G12 reports `0x5A00`/`0x5A01` in all 24 captures.** `0x2800`/`0x2801` appear only
  transiently, in the three captures where the panel had just written it (PC.3, PC.5, PC.12 — the
  floorplan/options bursts), and are gone by the next sweep.
- **The low byte survives.** `0x5A01` shows up right after the panel writes `…01`, which is the
  `uxtb`/`orrs` doing exactly what the code says.

So the panel's write is **overridden by the G12 within a sweep period**: the panel asks for a high
byte of 40, the G12 recomputes 90 from its own `config[0xF7]`, and keeps only the flag byte. That
is the "the G12 re-derives the AGS/AES parameter profile" behaviour of `GUICODE.md` Finding 50,
seen at instruction level for the first time. `test_the_g12_derives_selector_15_high_byte_from_selector_f7`.

*Read the units with care:* the derivation says the high byte is `config[0xF7] + 30`, so 90 means
`0xF7 == 60` and the panel's 40 meant 10. **What the quantity is remains unknown** — the `+30` and
the packing are decoded, the meaning is not.

### What this region does NOT show

- **Channel 18 is a timeout, not an interlock.** Three of the four blocks never read `+0x50`.
  An earlier note in this dig guessed "a second interlocked output"; that was wrong and is
  withdrawn here.
- **`+0x50`'s meaning is still inferred.** This region does strengthen it — the AES watchdog and
  the awning lock both keying on one cell fits "engine running / ignition sense" well — but there
  is still no store to it anywhere in the blob. **CONFIRMED BUS-FED ON THE COACH, 2026-09-03
  (`BUS-BASELINE.md` PC.48): injecting `19FBDA..#FB01…` from a foreign source address made the G12
  lock the awning 4 ms later and unlock 2 ms after the clear.** The writer is the app, from the
  bus, via the table-`0x61` receive record — which is why no store exists here. Original note:
  **candidate writer found outside the blob, 2026-09-02 (`MCF-FORMAT.md` §9a.3, `inferred`):** table `0x61` #0 binds `1FBDA` instance `FB`
  payload byte 1 to cell `0x28` = **`+0x50`**, and 14.7 binds the same cell to `1FFB8`
  DIGITAL_INPUT_STATUS with no input rules at all. So `+0x50` is most likely **bus-fed by the
  app** from the engine-running indicator (a loop-back of table `0x20`'s own `FB` broadcast on
  13.6). One injected `19FBDA00#FB01…` with the key off, watched for PC.27's LOCK, would settle it.
- **`[base+0xA6]`, `[base+0xE8]`, `[base+0x9E]`, `[base+0x2A]` are unnamed.** `+0x0E02` packs
  `[base+0x2A]` as `((timer + 30) << 8) | (old & 0xFF)`, which is decoded as arithmetic and
  unexplained as meaning.
- **The four timers' arming conditions are only known for one** (`+1000`, at `+0x1320`, gated on
  `[base+0x9E] == 2` and `[base+0xE8] == 1`).

## The `+0x17C0` light dispatch — the G12 picks a CHANNEL BY FLOORPLAN — `proven-from-code`, 2026-08-24

Records 5-10 are three command pairs on instances `0x23` (35, `SECURITY LT / LIVRM OHC / BENCH
SEAT OHC`) and `0x19` (25, `CARGO LTS / BATH LTS`): **command 4 (stop)**, **command 5 (toggle, level
250)** and **command 21 (ramp)** — a tap / hold / release dimmer triad. The code that sends them
chooses *which of the two channels* from the coach's **floorplan**.

```asm
+17ae  movs r3,#0xf5 / lsls r3,r3,#1     ; r3 = 0x1EA = 2 * 0xF5
+17b6  ldrh r1,[r0,r3]                   ; r1 = config[0xF5]
+17b4  movs r7,#0x82 / lsls r7,r7,#2     ; r7 = 520  (above 0x200 -> runtime, not config)
+17b8  ldrh r0,[r0,r7]                   ; r6 = [base+520]
+17c0  subs r2,r1,#4 / uxth r2,r2
+17ce  cmp  r2,#2 / bls  -> instance 35 arm       ; floorplan in {4,5,6}   (unsigned)
+17d2  cmp  r1,#0xa / bne -> instance 25 arm      ; ... or floorplan == 10
```

So **floorplan ∈ {4, 5, 6, 10} → channel 35; everything else → channel 25.** `[base+520]` then
picks the command: non-zero sends *stop*, zero sends *toggle* and *ramp*.

**`0xF5` is the floorplan code — `observed-on-hardware`.** It reads **8** in 22 of the corpus's 24
captures, and 8 is **WD**, this coach's plan (`GUICODE.md` Finding 17: 7=WA, 8=WD, 9=WT). In the
two floorplan-selection captures (PC.5, PC.12) it walks **1…10** as the owner cycled plans, and the
panel is seen writing each value with its own `19FED99F#FF96F50F..00D1EA`. Finding 52's record 44
independently names `F5` and reports the coach transmitting `08`.

**On this coach the dispatch resolves to channel 25** — floorplan 8 is neither in {4,5,6} nor 10.

### Why this matters beyond one control

**It is the first evidence that the G12's own code varies behaviour by floorplan.** Floorplan
dependence was previously a *panel-side* mechanism throughout this project — the type-6 value-match
switches that gate pages and options on slot 5 (`GUICODE.md` Findings 16/17), and the tap handlers
that emit per-floorplan defaults (Finding 42). The G12 was understood as holding the floorplan in
NVM and reporting it. It also **branches on it**, in compiled code, to command a different physical
output.

Consequences worth carrying:
- **A rule-table census cannot tell you what the G12 does.** `g12rules.py` reads fixed templates;
  this selection exists only as a `cmp`/`bls` pair in table `0x67`, so a coach on floorplan 5 and a
  coach on floorplan 8 run the same rules and drive different pins.
- **"Which channel does this control use?" is not floorplan-independent**, and the pin sheet
  (`answer_key.CHANNELS`) names both 25 and 35 without saying either is conditional.
- Channel 25 is also the channel option `E3` (`Cargo / Bath Light`) binds to (`BUS-BASELINE.md`
  PC.33), so two independent mechanisms — a panel option and a G12 floorplan branch — both decide
  something about the same output. Neither is aware of the other.

Pinned by `test_the_light_dispatch_picks_its_channel_by_FLOORPLAN` (which reads the predicate's
immediates out of the blob rather than restating them) and `test_selector_f5_is_the_floorplan_code`.

### The caller — and the dispatch is GATED OFF at the config default

`+0x0FDC` is the function's only call site, and reading it qualifies the result above. The caller
unpacks **`config[0x68]`** (cell `+0xD0`) into three fields and gates the call on two of them:

```asm
+0fb6  adds r2,#0xd0 / ldrh r2,[r2]      ; r2 = config[0x68]
+0fc0  ands r5,r0 (0x0f) / strh -> +0xBC ; low nibble
+0fc4  lsrs r1,r2,#4 / ands r1,r0        ; second nibble  -> +0xBE
+0fca  lsrs r2,r2,#8                     ; high byte      -> +0xC0
+0fd4  cmp  r1,#1 / bne +0x100c          ; unless nibble1 == 1, SKIP
+0fd8  cmp  r2,#0xc8 / beq +0x0fe0       ; if high byte == 200, SKIP
+0fdc  bl   +0x1778                      ; the floorplan light dispatch
```

**`config[0x68]` defaults to 0**, so `nibble1 == 0` and **the dispatch does not run at the
default**. It is enabled only when that nibble is 1. Nothing in the corpus reports selector `0x68`,
so the coach's live value is unknown.

*This does not retract the floorplan finding — it bounds it.* The channel selection resolves to 25
for floorplan 8 **whenever the dispatch runs**; whether it runs on this coach is a separate
question this blob cannot answer. Read the earlier section with that condition attached.

### `[cell 0x69] = config[0x6A] − 54`, and the defaults agree

`+0x0FAA`-`+0x0FB2` computes it. The defaults routine, extracted independently, gives
`+0xD4 = 9467` and `+0xD2 = **9413**` — and 9467 − 54 = 9413 **exactly**.

That is a second, unplanned confirmation that the "implausible" literal defaults were never
extraction errors: two of them are a *derived pair*, related by the same constant the code
subtracts. `0x69`/`0x6A`/`0x6B` (9413 / 9467 / 9568) are a coherent family, not noise.

### How narrow the `0x15FCE` sweep really is

Quantified, since it bounds every "name the cell, then look it up on the bus" move:

| | count |
|---|---|
| selectors with a default in the blob | **99** |
| selectors the G12 has **ever** reported, whole corpus | **46** |
| both | 24 |
| defaulted but **never reported** | **75** |
| reported with no default | 22 |

So three quarters of the config cells the G12 defaults are invisible on the wire, and a fifth of
what it does report has no default here. **The selector mapping is a decoding aid, not an
observation channel** — every cell named in this section (`0x5E`, `0x5F`, `0x60`, `0x68`, `0x69`,
`0x6A`, `0x6B`) falls in the invisible three quarters.

**Not established:** what `[base+520]` is, beyond "runtime state that selects stop vs
toggle/ramp"; what `config[0x68]`'s nibbles mean or what sets them; and what calls `+0x0FA0`.

## `0xBF` decoded — a trigger with a timed end — `proven-from-code`, 2026-08-24

The frames PC.24 called *"undecoded by anyone"*. **Four** emitter sites, and they form one small
state machine around a trigger cell and two timers:

```asm
+0aec  movs r6,#0xf8 / lsls r6,r6,#2   ; r6 = 992      \  two timers,
+0af8  ldr  r5,[pc,#0x180]             ; r5 = 990      /  adjacent u16s
+0af0  adds r2,#0xfa / ldrh r2,[r2]    ; the TRIGGER = config cell 0x7D
+0afa  cmp  r2,#0  / beq  -> nothing
+0afe  cmp  r2,#1  / beq  -> +0x0b06   ; value 1
+0b02  bl   +0x12cc                    ; value 2 -> its own arm, or >=3 -> clear and drop
+0b06  subs r2,#2 / strh to BOTH timers
+0b12  ldm/stm record 0 / blx          ; SEND  19FBDA9C#BF01000000000000
+0b1e  [timer 992] = the trigger value ; arm
+0b28  [cell 0x7D] = 0                 ; consume the trigger
```

and the two expiry arms, each the same one-shot idiom as the AES watchdog — fire when the cell
reads 0, then write `0xFFFF` to disarm:

```asm
+0b2c  ldrh [timer 992] ; cmp #0 ; bne …  ->  +0b3e  record 2  SEND BF00
+0b4a  ldrh [timer 990] ; cmp #0 ; bne …  ->  +0b5c  record 2  SEND BF00
+12e2  record 1  SEND BF02   (the value-2 arm, which arms timer 990 instead)
```

**So `BF 01` / `BF 02` are *starts* and `BF 00` is the timed *end*.** PC.24's pair is exactly this,
and the two frames bracket nothing — they are one event:

```
789.442691  19FBDA9C#AA00…    ignition sense (pin 10) goes low
789.453839  19FBDA9C#BF01…    +11 ms    start
790.167863  19FBDA9C#BF00…    +714 ms   end
```

**`BF 02` has never been observed**, which now means something specific: **trigger value 2 has
never occurred**, not that a frame was missed.

*The same ceiling as everywhere else in this blob.* Only three sites touch cell `0x7D`: the
defaults routine (writes 0), the read above, and the clear. **Nothing here ever writes it non-zero**
— the trigger comes from the G12 application at `0x08006000`, which the MCF does not contain. What
the event *is* remains unknown; its shape does not.

**A timer bank, not scattered timers.** `+990`, `+992` (here) and `+994`, `+996`, `+998`, `+1000`
(the AES watchdog) are six adjacent u16s, all driven by the one decrement loop at `+0x18AA` and all
using `0xFFFF` for disarmed. Above `+0x200`, so runtime, not config.

## The 1 Hz input refresh is not in this blob — `proven-from-code` (negative), 2026-09-10

*2026-10-21: true of 11.23 and 13.6. 14.7 moved push-button handling into its blob, including a
once-a-second re-send of a held button's ramp; see "The context for ABI 17 (14.7)".*

`BUS-BASELINE.md` PC.24 measured every held input pin re-broadcasting its `0x1FBDA`
GENERIC_INDICATOR_COMMAND at **1.000 s** while the pin stayed active — a tap under ~1 s sends one
ON/OFF pair and no repeat. This document long carried it as open step 6, with the standing guess
that it "drives table `0x20`'s templates from somewhere else in this blob." **That guess is wrong,
and the negative is airtight: no code path in the table-`0x67` blob emits an input-pin broadcast.**

**The blob has exactly two transmit mechanisms, and both were enumerated over the 94%-walked
body:**

1. **The block-copy frame table** (`+0x1A0C`, `blx [r4,#0x18]`). Its only `0x1FBDA` records are
   instances `0xBF` (the code-generated three-state status). **No input instance (`0xA1`–`0xAA`,
   `0xFB`) appears in it** on any build — `0xBF` on 13.6/14.7, none on 11.23.
2. **Table-`0x62` rule activation** (`[r4,#8](N)`). Every activation site resolves to a
   **table-`0x62`-local** index in `[4, 93]` (`scan`: `#52` = Low Batt. Cutout, `#8` = the
   `1FF95` charge-rate relay, `#4` = the `1FEDB` inst-`0x50` generator-stop, `#93` = the LPG tank —
   all table-`0x62`-local, which a global index would not satisfy). And **table `0x62` holds no
   `0x1FBDA` record at all** (its DGNs are `15FCE`/`1FACF`/`1FEBE`/`1FECB`/`1FED9`/`1FEDB`/`1FF87`/
   `1FF95`/`1FF97`/`1FFB7`/`1FFC4`/`1FFD0`/`2FEBE`), so no activated rule can emit an input frame
   either. This holds on 11.23/13.6/14.7.

The per-instance loop `+0x1146`–`+0x1196` (dispatch on a type byte `∈ 0..3` via the `+0x19F8`
`lr`-relative jump table) and the timer-decrement loop `+0x18AA` are runtime state machines whose
only outward transmit is the same `[r4,#8]` activation — already covered.

**Where the repeat actually lives — `proven-from-data` for the marker, `inferred` for the actor.**
The input templates are in **table `0x20`**, and each held/active template carries **byte-2 bit
`0x04`** (`g12rules` "kind `0x04`"); its OFF counterpart for the same instance clears it. The
pairing is exact on 13.6 (instances `A1 A2 A3 A4 A6 A7 A9 AA FB`) and 11.23 (`A1 A2 A3 A4 A9 AA
FB`); 14.7 (Terrain) configures no `0x1FBDA` inputs at all. That bit maps to PC.24 frame for frame
— the ACTIVE frame repeats while held, the OFF frame fires once — so the marker for "refresh while
held" is a field of the record. **The code that reads that field and re-sends is the G12
application at `0x08006000`**, which the MCF does not contain; it is not the flashable table-`0x67`
blob. `inferred` for the app being the actor (that image has never been dumped), `proven-from-code`
for the blob being *not* the actor.

Pinned by `test_the_blob_emits_no_input_pin_broadcast` (per build: frame-table `0x1FBDA` instances
`⊆ {0xBF}`; no table-`0x62` `0x1FBDA` rule) and
`test_the_held_input_marker_is_a_rule_record_bit_not_blob_code` (byte-2 bit `0x04` on every ACTIVE
template, cleared on its OFF counterpart). The `g12-inputs` note previously said the repeat "is in
the code that fires it", which read as this blob; corrected to name the record bit and the app.

## The per-instance tank bucketer, and the jump table that hid it — `proven-from-code`, 2026-09-10

The 188 bytes at `+0x1858`/`+0x192A`/`+0x1948` were the last block the recursive walk left
"unreached", carried for weeks as *"either dead code the generator emitted, or reached by a
control-flow form the walker does not model."* It is the second, and the form is a computed jump.

### The loop and the dispatch

`+0x1146` is a per-tick loop over **four instances, `0xA8`–`0xAB`** (`+0x1148 movs r5,#0xa8`;
`+0x18C2 cmp r5,#0xac` exits). For each it loads the tank's raw reading `r7 = [base + 2*inst + 0x10]`
and the tank's current **bucket byte** `[base + 2*inst + 0x1a]`, and — if that byte is `≤ 3`
(`+0x1180 cmp r1,#3 / bls`) — dispatches on it:

```asm
+1186  movs r0,r1              ; r0 = current bucket (0..3)
+1188  ldr  r1,[pc,#0x20c]     ; (an unrelated literal load; the table is pc-relative to lr)
+118A  bl   +0x19f8            ; the lr-relative jump thunk
+118E  <halfword jump table: 0x0365 0x0375 0x03DD 0x03F8>
```

The thunk reads `table[r0]` and adds `entry*2` to `lr`, so the arm is `(+0x118E) + entry*2`:

| bucket | entry | arm | reads |
|---|---|---|---|
| 0 | `0x0365` | `+0x1858` | `0xCC` (0% / 33% boundary) |
| 1 | `0x0375` | `+0x1878` | `0xCD` (33% / 67%) |
| 2 | `0x03DD` | `+0x1948` | `0xCE` (67% / 100%) |
| 3 | `0x03F8` | `+0x197E` | `0xCE` (the full arm) |

Each arm compares the raw reading `r7` against its threshold and, when the boundary is crossed,
writes the **new** bucket back to `[base + 2*inst + 0x1a]` (`+0x18A8 strh r0,[r1,#0x1a]`). The
split branch directions (`bhi` at `+0x1860`, `bls` at `+0x1880`, `blo`/`bhs` at `+0x1932`/`+0x196A`)
and the secondary compares at `+0x192A`/`+0x1962` are **hysteresis** — a re-arm timer (`movs
r3,#0xfa; lsls #2` = 1000 at `+0x1898`, decremented at `+0x18AA`) keeps the bucket from chattering
at a boundary. So it is not a linear ladder; it is a four-state machine, one instance at a time,
entered at the arm for the state the tank is already in.

### The thunk is the control-flow form the walker could not follow

`+0x19F8` ends in `bx lr` — a *return*, to a flow walker — after adding a jump-table entry to `lr`.
That is why `g12code.analyse`'s recursive descent stopped at the four arms: nothing branches to
them directly. `analyse` now detects the thunk structurally (`_find_jump_thunk`) and follows it
(`jump_table_arms`, `follow_jump_tables=True`), which closes the gap: **94.4% → 97.1% on 13.6**,
and the same on the other builds — **11.23 92.3% → 98.1%, 14.7 95.0% → 97.2%**. The thunk is present
on all three, in two spellings distinguished by table-entry width: **13.6 a u16 table** (`ldrh`,
index pre-doubled, thunk `+0x19F8`); **11.23/14.7 a u8 table** (`ldrb`, thunks `+0x0C80`/`+0x1FF8`).
On every build the four arms open `movs r0,#<0xCC/0xCD/0xCE>` — the tank thresholds — which is what
identifies the dispatch as the bucketer rather than an arbitrary jump.

Pinned by `test_the_lr_relative_jump_thunk_is_present_on_every_build`,
`test_the_jump_table_arms_are_the_tank_bucketer` (the arms are `{0x1858, 0x1878, 0x1948, 0x197E}`
and the flow-only walk misses them) and `test_following_the_jump_table_reaches_the_arms`.

## The "unreached" block is NOT dead code — it reads three live user settings

`+0x1858` / `+0x1878` / `+0x1948` are the 188 bytes this document listed as *"either dead code the
generator emitted, or reached by a control-flow form the walker does not model. Open."* They read:

```asm
+1858  movs r0,#0xcc / lsls r0,r0,#1 / ldrh r0,[r2,r0] / cmp r0,r7 / bhi …
+1878  movs r0,#0xcd / lsls r0,r0,#1 / ldrh r0,[r2,r0] / cmp r0,r7 / bls …
+1948  movs r0,#0xce / …
```

`2 x 0xCC`, `2 x 0xCD`, `2 x 0xCE` — **config selectors `0xCC`, `0xCD`, `0xCE`**, compared against a
register inside the same function as the timer-decrement loop.

**Those three are live, user-adjustable settings**, and the corpus proves it twice over:

- **GuiCode carries four templates for each**, in one run at file `0x028CF1`-`0x028D80`:
  `e8 03 d2 ea` = **+1000**, `ff ff d3 ff` = read-back, `18 fc d2 ea` = **−1000**, and an absolute
  `d1 ea` **restore-to-default** carrying `54000` / `40000` / `25000`.
- **The owner exercised all three on the bus.** In PC.8 at t+1543 s each went down 1000 and back up
  within ~5 s (`0xCC` 54000→53000→54000, `0xCD` 40000→39000→40000, `0xCE` 25000→24000→25000), and
  they sit at the default triple in all 20 captures that report them.

So the block reads settings a person can change from the panel. **"Dead code the generator emitted"
is a poor explanation for that**, and the walker's failure to reach it was correctly read as a limit
of the walker — a control-flow form it did not model. **That form is now identified and modelled: the
`+0x19F8` `lr`-relative jump table, decoded in "The per-instance tank bucketer" above, and its arm 0
is `+0x1858`.** So the path is no longer inference — the walker follows it. **Confirmed on
hardware** — PC.35 identifies the block as the tank level bucketing ladder and the settings as the
thresholds the `PROBE TANK DIAGNOSTICS` screen edits.

**IDENTIFIED — they are the TANK LEVEL THRESHOLDS. `observed-on-hardware`, `BUS-BASELINE.md`
PC.35, 2026-08-24.** The owner recognised the numbers, went to `PROBE TANK DIAGNOSTICS`, and the
predicted frame appeared: `19FED99F#FF96CC0FE803D2EA`, `0xCC` 54000 → 55000.

```
      0%
  54000   <- 0xCC     the 0% / 33% boundary
     33%
  40000   <- 0xCD     the 33% / 67% boundary
     67%
  25000   <- 0xCE     the 67% / 100% boundary
    100%
```

They are **raw 16-bit ADC counts, and higher means emptier**, which is why the ladder descends and
why the step is 1000. So this block is the **tank level bucketing ladder** — `cmp r0,r7 / bhi`
against each threshold in turn, unsigned, exactly right for an ADC compare, with `r7` the tank's
raw reading.

*Every unit guessed beforehand was wrong.* The paragraph this replaces offered 1.000 V, 1 s and 1 A
as fitting equally well and declined to pick; the answer was none of them. Declining was right, and
the thing that settled it was a person who knew the coach looking at three numbers.

**It also predicts the screen.** The photo's raw values — FRESH 65520, GREY 65515, GREY2 57412,
BLACK 65520 — are all above 54000, so all four gauges read 0%, which they do. That explains PC.25's
note that the four tanks read 0 % mechanically for the first time.

**`Reset Default Thresholds` is the three `d1 ea` templates** at file `0x028D5D`/`0x028D69`/
`0x028D75`, carrying 54000 / 40000 / 25000 — identified without needing to press it.

## Cross-build auto-start watchdog: 11.23 / 13.6 / 14.7 — `proven-from-code`, 2026-08-28

Loaded 14.7's table-0x67 blob (8,680 bytes, vs 13.6's 6,848 and 11.23's 3,264) into Ghidra
(`g12code147`) and diffed the auto-start arming path across all three with `g12code.py` + capstone.
The clean split: one piece is the **AGS** watchdog and is in every build; one piece is **AES-new**
and is 13.6-only.

1. **The Low-Volts→Start-At gate is AGS, not AES — present in ALL THREE builds.** Every build runs
   `threshold(cell 0x0B) = config[0x2F] ? config[0x31] : 0` in a byte-identical shape
   (`adds rX,#0x5e (0x2F); ldrh; cmp #0; beq; adds rX,#0x62 (0x31); ldrh; strh rX,[_,#0x16]`):
   **11.23 `+0x08FE`, 13.6 `+0x1924`, 14.7 `+0x1D96`**. `0x2F` = "AGS low volts trigger" and AGS
   predates AES, so this — the defect-0c mechanism (`BUS-BASELINE.md` PC.39, `Low Volts` off → start
   threshold 0) — is *not* AES-new. `g12code.low_volts_gate` decodes it in any build.

2. **The AES-new part is the mode-gated measured-voltage re-point, and it is 13.6-ONLY.** 13.6's
   `+0x18FE` block (`g12code.start_arming`) additionally does
   `measured(0x0A) = config[0xEF] in {2,3} ? config[0x55] : config[0x56]` — pointing the watchdog at
   the AES voltage cell in AES mode. **11.23 never had it** (no `config[0x55]→measured` copy at all;
   `movs #0xf4`-class defaults), and **14.7 dropped the `0x55` copy too** — neither carries an
   `adds #0xaa` (selector 0x55). So `start_arming` correctly returns `None` for both; only the
   low-volts gate generalises.

3. **14.7 rewrote the watchdog as a per-channel loop with hysteresis (the +1,832 bytes).** The gate
   at `+0x1D96` sits *after* a loop `for r5 in ..0xac` (`+0x1CC8…+0x1D84`, `cmp r5,#0xac; bne`
   is the loop terminator — NOT a mode gate; my first read of it as one was wrong). A following block
   (`+0x1DF4…+0x1ED2`) nudges the threshold cell by **±0x3c (±60)** gated on the measured cell `0x14`,
   cell `0x1a` (vs `0x140`), cell `0xc` (== 3), and a new selector `config[0xDA]` — hysteresis 13.6
   has none of. So 14.7's start/stop state machine is materially richer, and its measured source is
   no longer `0x55`; fully decoding it needs the loop body, out of scope here.

Defaults differ too (`config_defaults_by_selector`): Start-At `0x31` = **250 / 600 / 244**
(11.23/13.6/14.7); threshold seed `0x0B` = **244 / 1000 / 244**. The actual auto-start *trigger*
still lives in the G12 application at `0x08006000` (not the MCF) in every build — this blob only
feeds it the threshold.

*Tooling note (2026-08-28): Ghidra 12.1.3 dropped Jython, so `support/analyzeHeadless` fails on this
repo's `.py` scripts ("not started with PyGhidra, Python is not available"). **Fixed** — use the
`ghidra/headless` wrapper (PyGhidra in `.ghidra-venv`, created offline by `ghidra/setup-pyghidra.sh`
from Ghidra's own bundled wheels); it is a drop-in for `analyzeHeadless` and runs the `.py`
pre/post scripts. `g12code147` imported as `ARM:LE:32:Cortex` (Thumb-only) at base 0 — 101 functions,
addresses match `g12code.py`.*

## The AGS/AES parameter limits — `proven-from-code`, 2026-08-24

`+0x0A14` … `+0x0AB8`, and they close a standing negative: `RVC-GAPS.md` §3 recorded these as
*"not in the firmware, as far as this work can tell"*.

Each parameter gets the same three-part treatment — clamp high, clamp low, else keep. The high
bound is an inline `movs`+`lsls`; the low bound is a `bls` against a pool word, with the `bl`
instructions acting as **long branches, not calls** (Thumb-1 has no other long branch, and the
targets land inside the same block):

```asm
+0a2a  movs r1,#0x87 / lsls r1,r1,#3   ; 1080, the high bound
+0a30  adds r3,#0x62                    ; cell +0x62 = selector 0x31, start-at
+0a32  ldrh r2,[r3]
+0a34  cmp  r2,r1
+0a36  bhi  +0x0a3c                     ; above -> store 1080
+0a38  bl   +0x1266                     ; else -> low-bound arm
   +1266  ldr r1,[pc,#0x140]            ; 999
   +1268  cmp r2,r1 / bls +0x1270       ; at or below -> store 1000
   +126a  bl  +0x0a46                   ; else -> in range, keep it
```

| selector | parameter | min | max |
|---|---|---|---|
| `0x31` | start at voltage | 1000 = **50.0 V** | 1080 = **54.0 V** |
| `0x0D` | stop at voltage | 1040 = **52.0 V** | 1096 = **54.8 V** |
| `0x0C` | time at start volts | **10** | **300** |
| `0x0E` | time at stop volts | **600** | **3600** |
| `0x16` | max engine run time | **60** | **115** |

Shipped as `g12code.AGS_LIMITS` / `clamp_ags_parameter`.

### And a relational rule no table could hold — `+0x0A76`

```asm
+0a7a  adds r0,#0x62 / ldrh r3,[r0]     ; start-at
+0a7e  ldrh r2,[r1,#0x1a]                ; stop-at
+0a82  adds r5,#8 / cmp r5,r2 / blt      ; require stop >= start + 8
+0a9a  adds r3,#0xa / strh r3,[r1,#0x1a] ; else force stop = start + 10
```

**The two setpoints are not independent**: the G12 demands `stop_at >= start_at + 8` (0.4 V) and
rewrites `stop_at` when it isn't. A client that writes them one at a time may see the other move.

### Why it stayed missing, and the rule to carry

The original search looked for a min/max **structure** — a table of bounds beside the values.
There isn't one. **The limits exist only as `cmp`/`bhi`/`bls` pairs in compiled code**, in the one
section this project left undisassembled longest. The negative was honest and its caveat ("a
negative from not finding, not a proof of absence") was exactly right. *What made it unfindable was
the assumption that a limit must be data.*

*Two scan artifacts on the way here, both worth remembering:* an `ldrh rX,[rY,#imm5]` scan **cannot
reach** cell `+0x62`, because Thumb-1's imm5 form tops out at offset 62 — so "zero accesses to the
start-at cell" was a property of the encoding, not the firmware. And a `movs`/`lsls` constant scan
missed it too, because the code forms the address as `adds r3,#0x62` on a base pointer. The cell is
read; two different scans were structurally blind to it.

## What arms the automatic engine start — `proven-from-code`, 2026-08-24

**The AES logic IS in the MCF.** This document previously concluded that the auto-start engine
lives entirely in the G12 application at `0x08006000`. That was wrong, and the owner's argument is
what overturned it: *AES did not exist in 11.x, it was added in 13.x, and Firefly ships only
`cli.bin` and `mcf.bin`.* Three independent things confirm it, all absent from 11.23 and present
in 13.6:

| added in 13.x | where |
|---|---|
| frame-table record 3, `19FEDB9C#12FF00030000FFFF` — **channel 18** OFF | `+0x1A0C + 36` |
| `config[0x73] := 18` under `mode == 3`, against `:= 19` for AGS | `+0x09F6` / `+0x14C2` |
| the 48 V AES parameter defaults: ~~start-at **1020** (51.0 V),~~ `0x0C` 60, `0x0D` 1060 (`+0x09DE`, pool `+0x0C74`), `0x0E`/`0x0F` 1800, `0x16` 115, `0x73` 18, and `0x7B`/`0x7C` — **no 1020 anywhere in the blob (no u16 or u32 literal, 2026-09-02)**; the only `0x31` write on the mode path is the clamp into [1000, 1080], which takes 244 → **1000**, ~~so PC.39's frameless 244→1020 has no blob mechanism~~ — **explained 2026-09-03, `BUS-BASELINE.md` PC.51: the G12 keeps two complete parameter profiles in NVM and swaps them on a mode change (AGS 244 / AES 1020, observed with the panel sending only `EF`), so 1020 is neither a blob literal nor a clamp result and correctly appears in no image we hold** | `+0x09DA`…`+0x0A12` |

`config[0x73]` is the **auto-start output channel** — 18 in AES Single Output, 19 in AGS
(beside `20 - GENERATOR STOP`). It is written three times and read nowhere in the blob, so the
application consumes it. Those defaults are Finding 43's eight selectors, value for value, from a
route that reads no GuiCode.

### The block that decides whether the start can ever fire — `+0x18FE`

The tail of entry `+0x0918`, and the whole of it:

```asm
+18fe  movs r2,#0xef / ldr r3,[r4] / lsls r2,r2,#1
+1904  ldrh r2,[r3,r2]                  ; config[0xEF], the AGS/AES mode
+1906  subs r2,#2 / cmp r2,#1 / bhi +0x19b0    ; mode in {2,3} = AES / AES Single Output
+190c  adds r2,#0xaa / ldrh r2,[r2]     ; config[0x55]      (else arm: +0x19b0 -> config[0x56])
+1912  strh r2,[r3,#0x14]               ; config[0x0A] := it
+1914  adds r2,#0x5e / ldrh r2,[r2]     ; config[0x2F]
+191a  cmp  r2,#0 / beq +0x1924         ; zero -> fall through with r2 = 0
+191e  adds r2,#0x62 / ldrh r2,[r2]     ; config[0x31], AES "Start At"
+1924  strh r2,[r3,#0x16]               ; config[0x0B] := it, or := 0
+1926  add sp,#0x24 / pop {r4-r7,pc}
```

    config[0x0A] = config[0xEF] in {2,3} ? config[0x55] : config[0x56]
    config[0x0B] = config[0x2F] ? config[0x31] : 0

**`0x0A` is the measured house-battery voltage.** It is published in the `0x15FCE` sweep and
drifts — 1049…1062 (52.45 – 53.10 V) across PC.38's 24 minutes — which is what identifies it. It is
written here and read nowhere else in the blob.

**`0x0B` is the threshold that value is compared against.** Never published; its two
`0xFFFF`-sentinel defaults are **1000** (50.0 V, `+0x007C`) and **223** (11.15 V, `+0x0490`) — a
start-at voltage in each product's own scale. Also written here and read nowhere else.

So the blob's job is to **feed the application's watchdog**: in AES mode it re-points the source to
the AES voltage cell and copies the AES setpoint into the threshold the watchdog already used for
AGS. Both arms of the mode test run the `0x2F` gate — `+0x19B0` re-enters at `+0x1910` — so the gate
is unconditional, not AES-specific. The floorplan test just above (`+0x18C8`, floorplan in
{1,2,3,6,10}) only chooses between an edge-detect and a notify call; **both paths reach `+0x18FE`**,
so WD (floorplan 8) is not excluded.

### And `0x2F` is zero on this coach

**Every capture, 2,000+ frames, twelve days, without one exception** — pinned by
`test_the_arming_selector_reads_zero_in_every_capture`, which fails on a single non-zero sample.
Its own sentinel default in this blob is **1** (`+0x018C`), so **0 is a stored value, not an unset
one**. Nothing else in the blob writes it, and the panel's only template for it is a read-back
(`0x00259A9C`) — the sole writer anywhere is **group 129**, a two-variant `0/1` toggle (bindings
447/448) whose touch region sits beside the `Triggers:` label on `AUTO GEN START SETTINGS`
(object 31/795, record 8). Finding 43's `Reset to Defaults` profile does not include it.

**With `0x2F = 0`, the start threshold is written 0 — a value the battery can never fall below.**
That is a mechanism for `CLAUDE.md` defect **0c**, and it is the first one offered that survives
PC.38: it does not depend on the engine-run state, on channel 18 having a start event, or on
anything the panel does.

*The honest limit, stated plainly:* the comparison itself is **not in this blob**. `0x0A` and `0x0B`
are written here and consumed by the application at `0x08006000`, so this shows the threshold being
zeroed, not the start being suppressed. It is a strong candidate, not a proof.

**CONFIRMED ON HARDWARE the next day — `BUS-BASELINE.md` PC.39, 2026-08-25.** Setting `0x2F := 1`
was the only change made to the coach, and the auto-start that had never fired across a whole
evening fired within one battery sag: `19FEDB9C#12FFC801FF00FFFF`, **from the G12**, 117.7 s in,
about 10 s after the battery crossed the threshold, against `time at start volts` = 10 s. Defect 0c
is closed. Two things the same capture settled: `19FBDA9C#BF02` — state 2, previously emitted from
the frame table but never observed — is the **start** indicator; and the channel-18 **ON** command
is **not** in the frame table, so the application owns the command while the blob owns the
configuration and the indicator.

*The test, and it is cheap and reversible.* Write the arming flag and watch:

```
19FED9C0#FF962F0F0100D1EA      # 0x2F := 1
19FED9C0#FF962F0F0000D1EA      # restore
```

with AES enabled and the battery below `Start At`. Or reach it from the panel: the `Triggers:`
control on `AUTO GEN START SETTINGS` is group 129 and writes exactly those two values.

*One thing not reconciled.* The panel also reads `0x2F` as a **duration** — `FUN_00247BF8`
`0x0024A058` divides it by 60 and stores it to slot 278, which objects 793/794 label
`Time at Stop Volts` / `Time at Stop %` with unit `Min`. A boolean and a seconds count cannot both
be right. Two things lean boolean: the sentinel default of **1**, and the absence of any clamp on
`0x2F` where `0x0C`/`0x0D`/`0x0E` all have one. Recorded as unresolved rather than decided.

### While confirming the above: the Start and Stop buttons are not the same mechanism

Worth carrying, because it misdirected this dig for a while. **Start is a GuiData binding**, held-
phase (mask 4), at GuiData `+0x0A64`/`+0x0A70` — `19FBDA9F#BF02…` then
`19FEDB9F#12FFC801FF00FFFF` (channel 18, level 200, command 1). The `0xBF` indicator is
**panel-sourced** here, not the G12's frame-table copy. **Stop is a GuiCode template run** from the
pool at `0x00259430`+: `0x01:=0`, `0x06:=0`, `0x58:=0`, `0x4F:=0`, `0xB8:=0`, then channel 18 OFF.
The G12's own AES timeout at `+0x0D40`…`+0x0DD2` re-implements that same Stop — clearing
`config[0x06]`, `config[0x4F]`, `config[0x01]` and sending frame record 3 — which is what confirms
record 3 is the AES stop rather than a stray channel-18 reference.

*A pool-format correction that cost an hour:* GuiCode's CAN templates are
`<u32 LE CAN-id base><8-byte payload>` — **the id comes first**. Read payload-first and channel 18
disappears from 13.6 and reappears misaligned under the wrong DGN.

## The per-rule broadcast switch, and how to stop the G12 claiming a tank instance — `proven-from-code`, 2026-09-07

Asked by the owner: the G12 broadcasts a tank status even with no sensor connected — can it be
turned off so a different RV-C tank sender can be fitted? It can, and the mechanism is a per-rule
on/off switch the rule engine exposes to this blob.

**Why the frame is there at all.** An unwired tank input floats to rail (`0xFFF0` on the bus as
`0BFC1`), which is above every threshold in the `CC`/`CD`/`CE` ladder above, so the ladder buckets
it to **0%** and the rule fires normally. The result is a well-formed `1FFB7` TANK_STATUS at 1 Hz
reading empty — indistinguishable, to any receiver, from a real empty tank.

### Three context-struct slots, named

The blob is handed a context pointer in `r4`. Three of its function-pointer slots account for 100
of the 111 indirect calls in 13.6:

```
[r4,#0x08](N)   activate rule N        [r4,#0x0C](N)   deactivate rule N
[r4,#0x10](N)   is rule N active?
```

**The oracle is rule #93** — `1FFB7` instance 3, the LPG tank. It is activated at `+0x18F6` and
deactivated at `+0x19AC`, and that is exactly the broadcast the coach is observed starting and
stopping with the `0xEF` mode write (`MCF-FORMAT.md` §9a.3, "A mode gate seen on the way"). Two
sites, one rule, and the wire showing it go on and off.

Sharper still, the black-tank gate reuses **one call instruction for both directions**:

```
+1104  movs r2,#0xec / lsls r2,#1 / ldrh r3,[r3,r2]   ; cfg[0xEC], the Black Tank option
+110C  cmp  r3,#1
+110E  beq  +0x1112                                   ; on  -> fall through
+1110  b    +0x184c                                   ; off -> ...
+1112  ldr  r3,[r4,#0x10] / blx r3                    ; already active?
+1118  bne  +0x1120                                   ; yes: nothing to do
+111A  ldr  r3,[r4,#8]                                ; the ACTIVATE pointer
+111C  adds r0,#0x2f                                  ; r0 was 0 from the query's return
+111E  blx  r3
...
+184C  ldr  r3,[r4,#0xc]                              ; the DEACTIVATE pointer
+184E  b    +0x111e                                   ; back into the SAME blx
```

A helper that merely "fired a rule once" could not be reached by that branch, and would not need
the idempotence query at `+0x1112` either.

*(2026-10-21: the full context, including `[r4,#0x14]` log and `[r4,#0x18]` send, is tabulated in
"Executing the blob" at the end of this document.)*

### The four water/waste broadcasts on 13.6

| rule | instance | tank | activation | gate |
|---|---|---|---|---|
| #45 | 0 | fresh | `+0x08DA` | **none** — config-apply function, never deactivated |
| #46 | 2 | grey | `+0x08E0` | **none** — same |
| #47 | 1 | black | `+0x111E` | `cfg[0xEC] == 1`, re-read every tick |
| #48 | 18 | grey 2 | `+0x1144` | floorplan `cfg[0xF5]` ∈ {2,3,7,8} = RA/RD/WA/WD |

`0xEC` is the **Black Tank** OPTIONS row (`interlocks.option_for_selector`), and `0xF5` the
floorplan. Both gates match the coach: it is a WD with Black Tank ticked, and all four instances
are on the wire in every capture.

### So: bus, or patch?

**Instance 1 is bus-controllable today, both ways, and the frames are already in the captures** —
absolute `D1 EA` sets, not the PC.11 delta trap:

```
19FED99F#FF96EC0F0000D1EA    Black Tank := 0  -> the G12 deactivates #47; 19FFB79C#01… stops
19FED99F#FF96EC0F0100D1EA    Black Tank := 1  -> resumes
195FCE9C#EC000000…           the G12's own 5 s sweep reports the state back
```

It persists in G12 NVM and the tick re-reads it, so it survives a power cycle. **Instances 0 and
2 have no config gate anywhere in the blob** and are patch-only. Instance 18's gate is the
floorplan, which is not a usable lever — repointing `0xF5` rewrites a burst of defaults.

One asymmetry worth carrying: the bus lever for black **also hides the HOME BLACK gauge** (slot
112 keys a type-6 switch, base 801 → 802 — `MCF-FORMAT.md` §5.4c note, Finding 61), so it is not
equivalent to the patch. The patch silences the broadcast and leaves the gauge drawing, which is
what you want if a replacement sender is going to feed it.

### The edit

`tankbroadcast.suppress` NOPs the activating `blx r3` — two bytes per instance, same size,
confined to the **independently flashable** G12 section, and one byte-pair per instance to
revert. The rule records are left intact, so the image still describes what it would send. On
13.6 that is file `0x10360E`, `0x103614`, `0x103E52`, `0x103E78`; nothing is hardcoded, and the
sites are re-derived per build from table `0x62` by DGN plus the instruction boundaries
`g12code.analyse` establishes. Shipped as the `suppress_tank_status` recipe op in **Experimental
v22**.

**Build-general, checked:** the twelve-byte `1FFB7` records are **byte-identical on 11.23, 13.6
and 14.7** — same kind `0x81`, same mask, same resolution 3, same level cells `B5`/`B6`/`B7`/`B8`,
same instances — and only the record index shifts. 11.23 activates all four unconditionally and
has no LPG rule at all; 14.7 gates all five.

**The limit of the claim, stated because it is the only soft joint here.** The call sites and
their pairing are read from these bytes. The helpers themselves are function pointers into the
G12 application at `0x08006000`, which this project has never dumped — so "activate" and
"deactivate" are named from the call-site shape plus the #93 wire oracle, not from reading the
callee. The cheap coach check that would close it needs no flash: untick Black Tank and watch
`19FFB79C#01…` stop.

### What is deliberately left alone

`1FFB7` instance 3 (LPG) has a real sender and already switches itself off in AES mode. `0BFC1`
(G12_TANK_LEVEL_SENSOR, the raw ADC the `PROBE TANK DIAGNOSTICS` page reads) and `1AAB7`
(Firefly's private TANK_STATUS mirror, LPG only) are proprietary DGNs that collide with nothing
standard.

## Next steps, in order

1. ~~Recursive-descent disassembly.~~ **Done — `g12code.py`, >97% on all three builds** (2026-09-10:
   the `lr`-relative jump table is now followed, closing the ~188-byte tank-bucketer gap). ~~and
   naming the 90 functions~~ — there are three (2026-09-02); the tick's arms are named in "The
   config struct named" below.
2. ~~**Identify the config struct**~~ **Done 2026-09-02 — "The config struct named" below.** 146
   selectors touched by code, 128 defaulted, ~60 named; ~45 defaulted-untouched-unreported cells
   cannot be named from the blob.
3. ~~Check the sibling builds.~~ **Done.** ~~Chase `+0x01C`, `+0x02E`~~ — done: `+0x02E` was never
   divergent (extractor artifact), `+0x01C` is `0x0E` and 13.6's whole seed block is the
   recognisable "fresh" set (cross-build section).
4. ~~Identify the two header words (`0x0D`, `0x0919`) and what `G8 Test` belongs to.~~ **Bounded
   2026-09-02:** `+0x08` is the tick pointer; `+0x04` = 13/13/17 changes only on 14.7, the build
   whose context-struct ABI also changed (`[r4,#{0,4,7,8,0xC,0x10,0x14,0x18}]` → `{0,4,8,0xC,0x10,
   0x18,0x24,0x40}`) — read as an app↔blob ABI/schema version, `inferred`. `G8 Test` is the `r0`
   of context callback `[r4,#0x14]`, called once in init right after the timer disarm (`+0x077E`
   `ldr r0,[pc]` → `+0x1AB4`; `+0x0786 ldr r3,[r4,#0x14]; blx r3`; 11.23 `+0x05EA` → `+0x0CB8`);
   14.7 has neither the string nor the call. What the callback does is in the app image.
5. ~~Read the `+0x0CAA`…`+0x0DF2` region.~~ **Done** — see "four AES START/STOP timeouts" above.
   Its follow-up is the `base + 2*selector` hypothesis flagged there: check whether other
   `movs rX,#<sel>; lsls rX,rX,#1` sites sit beside records naming that selector. If it holds, the
   G12's whole config-cell array is addressable and every `0x15FCE` selector gets a home.
6. ~~**Find the ~1 s input refresh**~~ **RESOLVED 2026-09-10 — it is NOT in this blob.** See "The
   1 Hz input refresh is not in this blob" above. The guess that it "drives table `0x20`'s
   templates from somewhere else in this blob" was wrong: the blob has two transmit paths and
   neither emits an input-pin frame. The repeat is a bit in the table-`0x20` record; the app acts
   on it.
7. ~~What are the three `0xBF` states?~~ **Mechanism done** — trigger cell `0x7D`, value 1 or 2
   selects the start frame, a timer expiry sends the end. What the *event* is still needs the
   application image, since nothing here writes the trigger.
8. ~~Name `0xCC`/`0xCD`/`0xCE`.~~ **DONE, PC.35** — the tank level thresholds (0/33/67/100%
   boundaries in raw ADC counts). The "second cheapest" route in this entry is the one that worked,
   and it took minutes: *ask the owner*. Worth remembering next time a value resists the file.
8. ~~**Where does `+0x50` come from?**~~ **ANSWERED 2026-10-21** — the G12 receives its own pin-10
   `1FBDA FB` frame through table `0x61` #0; see "Executing the blob". *Original entry:* Not from
   this blob — its writer is in the G12 application at `0x08006000`, which the MCF does not contain. Dumping that image over the bootloader protocol
   (PC.19 has the memory map) is the only route, and it would answer far more than this.

---

## The AES "Start At" default is not a patchable MCF constant — `proven-from-data`/`observed-on-hardware`, 2026-08-25

> **CORRECTED 2026-08-25 — the headline of this section is WRONG. There IS a patchable MCF default,
> and the owner called it: AES is new in 13.x, so its defaults had to arrive with the 13.x firmware,
> i.e. in the MCF.** The mistake below was scanning the **G12** section for the constant, on the
> assumption that "the G12 seeds the default." It does not — the **panel** does, by transmitting
> `0x31 := 1020` to the G12 from a CAN-frame template in **GuiCode's** `.rodata` template pool
> (Finding 7), as part of the config-defaults burst on floorplan/mode establishment and Reset to
> Defaults. That template is at GuiCode file `0x0199D0` (abs `0x002599D0`), its value field
> `fc 03` = 1020 = 51.0 V at file `0x0199D8`, and it is **build-specific — present as 1020 in 13.6,
> as 244 (12.20 V) in 14.7, and ABSENT in 11.23** (pre-AES). A same-size 2-byte edit changes the
> default the panel establishes. The full corrected finding is `GUICODE.md` **Finding 60**. What
> remains true below: the G12's own *sentinel-seed* for the `0x31` cell is 600 (a cleared-NVM
> fallback, not the value the coach uses), and the G12 does re-derive/recall a per-profile value
> internally — but its **origin** is the panel's push from the MCF template, not a G12-resident
> constant. Read the rest of this section as "why the value is not in the *G12* section," which is
> correct; its conclusion about the *MCF* is not.

Closes step (c) of the owner's AES "Start At" question. Steps (a) and (b) showed the panel only
*decodes* the setpoint (`GUICODE.md` Finding 59) and that it is set over the bus with a signed-delta
frame (`BUS-BASELINE.md`, "The AES 'Start At' bus-write recipe"). Step (c) asks the last thing that
could make an MCF edit possible: does the **G12** seed the 51.0 V default (`0x31` = 1020) from a
constant in the **flashable G12 section**, or from the out-of-container application at `0x08006000`?
It is not in the MCF. Four results, and together they are conclusive:

1. **The G12 re-derives `0x31` internally on a mode switch — no bus frame carries the value**
   (`observed-on-hardware`, PC.39). At the AES-Single-Output switch the panel emitted exactly
   `EF:=3, D7:=0, E6:=0, 15:=0x2801, F7:=60` and **no `0x31` frame at all**, yet the `195FCE9C#31`
   sweep flipped `0x31` from `F400` (244 = 12.20 V, the 12 V AGS profile) to `FC03` (1020 = 51.0 V,
   the 48 V AES profile) across that transition. So the 51.0 V is produced by the G12, not sent by
   the panel — this is *not* the panel's Reset-to-Defaults template (`GUICODE.md` Finding 43, GuiCode
   `+0x2599D0`), which is a separate, panel-side path.

2. **The in-MCF sentinel-seed default for the `0x31` cell is 600, not 1020.**
   `g12code.config_defaults_by_selector(mcf)[0x31]` = **600** (cell offset `0x62` = `2 × 0x31`).
   Whatever 600 means, it is not the 51.0 V the coach shows, and it only ever fires when the cell
   reads `0xFFFF` (fresh or cleared NVM). The config-defaults table — the one part of the profile
   seeding that *is* in the MCF — does not hold the value in question.

3. **Neither profile value is stored as a literal in the MCF G12 section.** A byte scan of the
   6,848-byte table-`0x67` blob and the whole `03-G12.bin` section finds **244 (the 12 V default):
   zero occurrences** — it is computed at runtime — and **1020 (the 48 V default): one occurrence,
   at blob offset `0x12D5`, which is odd/unaligned and which the recursive-descent walk classifies
   as *code*, not a literal** (bytes 4812–4832 are one code run; `0x12D5 ∈ Analysis.code`,
   `∉ Analysis.literals`). That lone `fc 03` is instruction encoding, i.e. the constant-scan
   reconnaissance artifact this project has been burned by before — not a usable default.

4. **Consistent with every prior split of this logic.** The AES watchdog, the `+0x0B` threshold
   writer (`config[0x0B] = config[0x2F] ? config[0x31] : 0`), and the `+0x50` cell writer all live
   in the application at `0x08006000`, outside the container (this file's "Where does `+0x50` come
   from?" and the `+0x0CAA`/`+0x0CC4` notes). The mode→profile start-at derivation belongs to that
   same body of AES control logic.

**Answer: no.** Even a G12-only build cannot change the pre-user AES Start-At default. The only
in-MCF G12 constant touching the `0x31` cell is the sentinel seed (600), which is neither the value
the coach uses nor active except on cleared NVM; the 51.0 V is derived by the G12 application at
`0x08006000`, which the MCF does not contain, and its profile values are not stored as patchable
literals anywhere in the section regardless. **Setting Start-At is a bus write (step b), and there
is no file-resident default to edit.**

*The one caveat, for completeness.* Patching the panel's Reset-to-Defaults template (Finding 43,
GuiCode `+0x2599D0`) *would* change the value the panel broadcasts when someone presses Reset to
Defaults — that constant is in the flashable GuiCode. But that is not the ambient default (the G12
re-derives its own on every mode switch), and whether the G12 even applies that broadcast as an
*absolute* is the unconfirmed question from step (b). So it is a fragile, partial lever, not a
default change. The clean answer stands: set it over the bus.

*(Reproduce: `g12code.config_defaults_by_selector(mcf)[0x31]`; the byte/offset scan and the
`analyse(mcf)` code/literal classification are in this entry. Capture: `pc39-low-volts-trigger`.)*

## Does the G12 enforce the 14.7 Lithionics↔AES interlock? — NO, panel-side only — `proven-from-code`, 2026-08-29

The 14.7 G12 code blob is **8680 bytes, +1832 over 13.6's 6848** (11.23 is 3264) — real new code.
This dig asked one question of it: 14.7 makes the new **Lithionics Batteries** option (config
selector `0xE7`, proven in `GUICODE.md` Finding 50 / `interlocks.option_for_selector`) mutually
exclusive with the generator/AES mode *on the panel side* — does the G12 **also** enforce it, the
way the question was settled for 13.6's Progressive-Inverter interlock (panel-only, `BUS-BASELINE.md`
PC.21)?

**Answer: no. The G12 references Lithionics only to give it a power-on default; it never reads the
value to make a decision.** In the 14.7 blob (blob-relative addresses), the Lithionics config cell
`0x1CE` (= `0xE7 × 2`) is touched at exactly two instructions, **both inside the `0xFFFF`-sentinel
config-defaults routine** (the same routine as the "config-defaults" section above, here spanning
`+0x0500…+0x0620`):

- **read `+0x060A`** — `movs r1,#0xe7 ; lsls r1,#1 ; ldrh r0,[r3,r1] ; cmp r0,r2 ; bne +0x614` — the
  standard "is this cell the unset sentinel?" test (`r2` = the sentinel), and
- **write `+0x0612`** — `movs r0,#0 ; strh r0,[r3,r1]` — default it to `0` (off) when unset.

Nowhere else in the blob is `0xE7` read or written (the defaults routine defaults `0xE7` but
notably **skips `0xE6`/Progressive Inverter** on 14.7, e5→e7). Because the G12 never reads
Lithionics at runtime, no code path can gate the AES-mode cell `0x1DE` (= `0xEF × 2`) — or anything
else — on it. The mode cell has many writers (`+0x064A`, and the `+0x075A…+0x0790` block) but none
can depend on a value that is never read. **So the Lithionics↔AES mutual exclusion is enforced
entirely panel-side** (the GuiCode tap handlers, `interlocks.find_coupled_handlers`), exactly like
13.6's PI↔AGS interlock: the G12 stores and defaults the config and leaves the coupling to the panel.

**Two method traps this dig hit and corrected — both are the standing "argument from absence"
warning in `CLAUDE.md`:**
- An **immediate-offset-only** scan (`ldrh [rB,#0x1ce]`) found **zero** hits and would have wrongly
  concluded "the G12 never touches Lithionics." The cell is addressed by the **register-indexed**
  form `movs #0xe7 ; lsls #1 ; ldrh [rB,rM]`, invisible to an `#imm` scan. A Thumb-1 `ldrh [rX,#imm5]`
  cannot even encode offset `0x1CE`, so the indexed form is *forced* — the immediate scan could never
  have found it.
- Naive cross-instruction **register tracking** reported a second "read" at `+0x0A08`; it is a **false
  positive** — that `ldrh [r3,r0]` is inside the config-unpack loop at `+0x090C` where `r0` is the
  loop index (`lsls r0,r0,#1`), not a stale `0xe7`. The finding rests only on disassembly-confirmed
  sites, not on the tracker.

*(Reproduce: disassemble `g12code.blob(mcf)` at `+0x0500`; `config_cell_offset(0xE7)==0x1CE`,
`config_cell_offset(0xEF)==0x1DE`. The `0xE7` = Lithionics identity is `interlocks.option_for_selector`.)*

## 14.7's AES watchdog gains three new parameters — `0xDA`, `0xDB`, `0xDC` — `proven-from-code`, 2026-08-29

Continuing the 14.7 G12 dig (the +1832-byte blob), the robust "new selectors accessed" set — after
discarding the register-tracking artifacts a naive scan produced (`0x400`/`0x116`/`0x8c`… were all
false) — is just **`0xC2, 0xDA, 0xDB, 0xDC, 0xE7`**. `0xE7` is Lithionics (previous section); `0xC2`
and `0xDC`-as-a-default are inert. The live additions are **three new config selectors in the AES
auto-gen-start watchdog** — the same routine as `low_volts_gate` (`+0x1D96`) and the AES start/stop
timers. All three are read on 14.7 and on **neither** 11.23 nor 13.6.

- **`0xDB`** — default **80** (`+0x059C`), two-sided **clamped to [20, 180]**: upper at `+0x1DA0`
  (`ldrh cell[0xDB]; cmp #0xB4; movs #0xB4; strh`), lower at `+0x1E8A` (`cmp #0x13; movs #0x14`).
  After each clamp the value is re-broadcast on the `0x15FCE` config sweep (`str =0x195FCE00`,
  `str =<value>00DB`), exactly the sweep the panel already reads (Finding 23 / `RVC-GAPS.md` §a).
- **`0xDC`** — default **200** (`+0x05AA`), **clamped to [150, 200]**: upper `+0x1DC2` (→200), lower
  `+0x1E9A` (→150); re-broadcast on `0x15FCE` (`=<value>00DC`).
- **`0xDA`** — **not** defaulted by the sentinel routine (so it is a measured/received quantity, not
  a stored setpoint). Read at `+0x1DF4` and compared to `0xDB`: **if `cell[0xDB] > cell[0xDA]`**, the
  watchdog sets the start-threshold cell `0x0B := measured(0x0A) + 60` and cell `0x0D := 320`
  (`+0x1DF4…+0x1E0A`). A second path at `+0x0BAC` gates on `state[+0x58] == 0x800` **and**
  `cell[0xDB] == 80` (its default) before deriving from `cell[0xDC]` against `190`.

So 14.7 added a clamped, bus-broadcast parameter pair (`0xDB`/`0xDC`) and a measured input (`0xDA`)
that together adjust the AES start threshold — a genuine new auto-gen-start behaviour, on top of the
existing start-at/stop-at/timeout clamps (`g12code.AGS_LIMITS`, `RVC-GAPS.md` §3).

**What is NOT established:** the *units/meaning*. The clamp ranges [20,180] and [150,200] do not fit
the panel's `0.05 V`/unit voltage scale (that would be 1–9 V), and could be SOC %, current, or a
time — the file does not say, and guessing a scale is the exact trap `RVC-GAPS.md` §3 and this
project's discipline warn against. And although these are new in the **same** 14.7 firmware that
added Lithionics, the region does **not** read the Lithionics selector `0xE7`, so there is **no
proven link** to Lithionics — only co-occurrence. Settling the units needs a `195FCE9C#…DB`/`…DC`
capture from a 14.7 coach, or the G12 application at `0x08006000`.

*(Reproduce: disassemble `g12code.blob(mcf)` — the AES watchdog at `+0x1D80…+0x1E20`, the
sentinel-default writes at `+0x0584…+0x05AC`. The new-selector set is the contiguous
`movs #sel ; lsls #1 ; ldrh/strh [base,rM]` idiom, diffed 14.7 vs 13.6 — NOT a cross-instruction
register tracker, which produces false hits here.)*

---

## The config struct named — `proven-from-code` / `proven-from-data` / `inferred` per row, 2026-09-02

Method: a fixpoint dataflow over the reachable code (register state `base+k` / immediate /
unknown, met at joins, flowing through `bl` because they are long branches), giving **379
config-cell accesses with base and offset both resolved** (197 reads, 182 writes) plus 11
index-resolved sites the earlier sections already cite; **zero odd offsets**; **146 distinct
selectors touched**; the highest config cell any code touches is `+0x1EE` (`0xF7`). Runtime
cells: `+0x204`–`+0x218` (per-instance status, sentinel-defaulted at init), `+0x3DA`–`+0x3FA` the
timer bank, `+0x3F8 := 5`, and `cell[count−1] := 100` with the count from `[r4,#4]`. Scratch
(`flow.py`, per-build JSON) is not in the repo; every row below cites the instruction.

**Corroboration that the mapping holds outside the defaults routine — 21 sites.** The idiom
`movs r0,#n ; ldr r3,[r4,#8] ; blx r3` is **"transmit table-`0x62` record #n"**, pinned by five
relays whose wire behaviour is coach-confirmed (`0x82`→#52, `0x86`→#54, `0x88`→#55 at `+0x0ECC
movs r0,#0x37`, `0x90`→#56, `0x81`→#8) and holding for `0x11`→#29 (`+0x0E7C`), `0x92`→#57
(`+0x0E22`), `0x95`→#77 (`+0x0CA2`), `0x01`→#10 (`+0x1586`), `0xC2`→#93 (`+0x18F4`), `0x15`→#24
(`+0x0AE8`, `+0x154E`), both clamp arms re-broadcasting the sweep record of the cell just clamped
(AES `+0x0A28`…`+0x0AD4`, AGS `+0x14D2`…`+0x153A`), frame record 4 after the `0xF7` clamp, and rule
#4 at `+0x159A`. **Four mismatches, real and unexplained:** `0x9C` (shadow `0x9D`) fires #49/#50
which substitute **`0x8E`**; `0xBA`→#61/#64 substitute **`0xB0`**; `0xBC`→#62/#65 → `0xB2`;
`0xBE`→#63/#66 → `0xB4`. Nothing copies `BA`→`B0`; the defaults coincide pairwise (290/274/292),
so either the rule engine reads the "+10" cell or these are two copies of one value. All four
relays gate on `config[0x94] == 1` (`+0x0F0A`) — `0x94` has no default and has never been
reported, a plausible reason PC.30 saw zero `1FECB`/`1FF97`. *Wire test:* `0x94 := 1`, press
Absorb, watch `19FECB9C`.

**Which seed wins on the wire.** Every selector with both a G12 sentinel default and a panel
`D1 EA` template that has been seen on the bus reports the **panel's** value (list in the
corrected "Scope" paragraph above). Not a contest: the sentinel fires only on `0xFFFF` cells and
the panel's bursts land afterwards. Two exceptions: `0x15`, where the G12 re-derives the high
byte over *both* seeds; and `0x88` (0 vs panel 12 → wire 0 — the 12 template is a specific
restore, not a boot default).

### The table

Columns: G12 sentinel default 11.23 / 13.6 / 14.7 (`–` none); **bus** = the G12's `195FCE9C`
report across the 29 13.6-era captures (value:count; `never` = no report); **panel** = 13.6
GuiCode `D1 EA` template value(s). Name sources: **r** rvc2mqtt `rvc-spec.yml` (hardware-derived),
**B** BUS-BASELINE, **G** this document, **GC** GUICODE, **new** this dig with its cite.

| sel | off | 11.23 / 13.6 / 14.7 | name (source) — confidence | bus | panel |
|---|---|---|---|---|---|
| 00 | +000 | 1/1/1 | unknown; panel writes `00:=1` with a `D1 FF` tail (RVC-GAPS §1) | 1:1928, 0:124 | 1 |
| 01 | +002 | 0/0/0 | AES-enabled flag (GC; r) — cleared by the stop blocks `+0x0CD4/+0x0D36/+0x0D76` and `+0x1588` before rule #10 | 0:1732, 1:354 | 0,1 |
| 02 | +004 | 720 | unknown; untouched outside init | never | – |
| 03 | +006 | – | unknown; never defaulted, never touched — app-owned | 65535:1700; 720 in pc37-39 | – |
| 04 | +008 | 1 | quiet-hours flag — **new, inferred**: `+0x0F9C` writes 0 in the comparator | 1:2044 | – |
| 05 | +00A | 768 | AES state (byte 2) + mode (byte 3) (r); untouched by the blob → app-written | 832:1236, 768:446 | – |
| 06 | +00C | 0 | run-state cell — **new, inferred**: stop blocks write it (`+0x0CCA/+0x0D2A/+0x0D6A`); `+0x1590 cmp #3` gates rules #4/#6/#7 | 0; 3 in pc27/28 (engine running) | 0 |
| 07, 08 | +00E, +010 | 30 | unknown | never | – |
| 09 | +012 | 255 | unknown | never | – |
| 0A | +014 | – | measured house voltage (B PC.4); written `+0x1912` | drifting ~1057 | – |
| 0B | +016 | 244/1000/244 | start threshold (`+0x1924`) — `proven-from-code` | never | – |
| 0C | +018 | 60 | time at start volts (r, owner) | 60:1501, 20, 10 | 60 |
| 0D | +01A | 270/1080/270 | stop-at voltage (r) | 1060:760, 1088:587 | 1060 |
| 0E | +01C | 7200/1800/7200 | time at stop volts (r) | 1800:1134, 900:835 | 1800 |
| 0F | +01E | 900/1800/1800 | min run time (B PC.4; M §9a) | 1800:1961 | 1800 |
| 10, 13 | | 0 | unknown | never | – |
| 11 | +022 | 1000/–/– | unknown; **clamped to [30,45]** at `+0x0E6E`/`+0x167A`, rebroadcast via #29 — coach sits at the ceiling | 45:2046 | ±5 (binding table) |
| 14 | +028 | 1024/2048/2048 | unknown | never | – |
| 15 | +02A | ?/0x7801/0x7801 | packed AGS config: hi = `config[0xF7]+30` (`+0x0E08`), lo = retries clamped ≤5 (`+0x1544`–`+0x1556`, AGS arm) (G, r) | 0x5A00:1969 | 0x2801 |
| 16 | +02C | 720/180/720 | max engine run time (r) | 115:1967, 720:98 | 115 |
| 17 | +02E | 0/0/0 | unknown; never touched outside init | never | – |
| 18, 21-23 | | 0 | unknown | never | – |
| 26 | +04C | 15 | unknown; sweep record #51 exists and is **never broadcast** | never | – |
| 28 | +050 | 0 | ignition/engine-sense state (G, inferred; candidate feed `MCF-FORMAT.md` §9a.3) | never | – |
| 29 | +052 | 0 | its latch (G) | never | – |
| 2A | +054 | – | **time-of-day, packed like 2B/2C but byte-swapped — new, inferred**: `+0x0F7A ldrh [base+0x54] / +0x0F82 rev16 / uxth`, compared against 2B and 2C at `+0x0F86`–`+0x0F96` | never | – |
| 2B, 2C | +056, +058 | 5632, 2048 | quiet time start/stop (`0x1600` = 22:00, `0x0800` = 08:00) (r) | 5632:1665; 2048:1726 | 5632; 2048 |
| 2D | +05A | –/0/0 | unknown (11.23 has sweep record #40 for it) | never | – |
| 2E | +05C | 0 | boolean, unknown | 0:2055 | – |
| 2F | +05E | 1 | Low Volts arming (r, B PC.39) | 0:2030, 1:28 | – |
| 30 | +060 | 0 | unknown | never | – |
| 31 | +062 | 250/600/244 | start-at voltage (r) | 1020:1344 | 1020 |
| 32 | +064 | 5144 | unknown | never | – |
| 33 | +066 | 0 | unknown; read-back only | 0:11 | read-back |
| 34 | +068 | 0 | unknown | never | – |
| 35 | +06A | 256/250/250 | unknown | never | – |
| 36 | +06C | 1 | unknown; sweep record #25 never broadcast | never | – |
| 37 | +06E | 0 | quiet-time path writes 1 (`+0x0FA4`) — inferred | never | – |
| 39-3C, 3E, 3F, 47, 49 | | 0 | unknown | never | – |
| 3D, 4B | | 0/–/– | 11.23-only, unknown | never | – |
| 4A | +094 | 0/0/– | unknown; read `+0x1014`, `+0x1064` (copied to a runtime cell) | never | – |
| 4F | +09E | –/0/0 | re-arm gate (`== 2`, `+0x1306`); the Stop template clears it (G) | never | 0 |
| 50 | +0A0 | –/255/255 | **generator-STOP output channel — new, `proven-from-code`**: rule #4 = `1FEDB` with instance ← `0x50`, byte 4 ← `0x7C`; the mode switch writes 20 (AGS `+0x14B2`; AES mode 2 via `+0x1250`→`+0x0A0A`) or 255 = none (AES-single, `+0x0A0A` with r2 = mode+0xFC) | never | – |
| 53 | +0A6 | –/0/– | watchdog block-C guard (G); DM_RV-fed per §9a.3 | never | – |
| 54, 57, 59, 5D, 62 | | 0 | unknown | never | – |
| 55 / 56 | +0AA / +0AC | – | AES / AGS measured-voltage source (`+0x190C`/`+0x19B0`) | never | – |
| 58 | +0B0 | –/0/0 | unknown; Stop clears; read `+0x148A` | 0 (read-back) | 0 |
| 5E, 5F, 60 | | 0 | the three unpacked fields of `0x68` (`+0x0FC0`–`+0x0FD2`) | never | – |
| 68 | +0D0 | 0 | packed light-dispatch enable (G) | never | – |
| 69, 6A, 6B | | 9413, 9467, 9568 | derived family (G) | never | – |
| 6C, 6E | | 0 | unknown | never | – |
| 6F | +0DE | –/255/– | unknown | never | – |
| 70, 71, 72 | | 255/80/– ; 80/200/– ; 200/–/– | unknown; the (255, 80, 200) triple sits at 70/71/72 in 11.23, 6F/70/71 in 13.6, and 14.7 has 80/200 at DB/DC — renumbering, inferred; 13.6 never reads 70/71 | never (#83-85 silent) | – |
| 73 | +0E6 | –/19/19 | auto-start output channel (G); the AES arm writes 18 (`+0x0A06`) | never | – |
| 74 | +0E8 | 0 | re-arm gate (G) | never | – |
| 75 | +0EA | 0 | watchdog status: `:= 2` while the F7 timer is armed, else `:= config[0x79]` (`+0x0BAC`–`+0x0BBA`) — inferred | 1:2040, 2:6 | – |
| 76, 77, 78 | | 0 | blocks E/C/D status (G) | never | – |
| 79 | +0F2 | 0 | flag read `+0x0B7E` (`!= 1` → AGS arm); written only via the self-addressed `1FED9` rules #2/#6 (`79 := 1/0`, PC.39's `FF96790F0100D1FF`) — new | never | – |
| 7A | +0F4 | 1 | **last-mode latch for the mode-change handler — new** (`+0x09B0`–`+0x09C2`; the handler runs only when `config[0xEF] != config[0x7A]`) | never | – |
| 7B, 7C | +0F6, +0F8 | 1, 1 | byte 4 of rules #0/#4 (`1FEDB` ON frames): 30/30 in AGS (`+0x14BA/+0x14BC`), mode+252 / 1 in AES (`+0x0A04/+0x0A12`) — new | never | – |
| 7D | +0FA | 0 | `0xBF` trigger (G); rules #3/#7 transmit `7D := 2/1` as `1FED9`, so the blob raises it by **bus loop-back**, not a store (`+0x15A6 movs r0,#7`; PC.39 `FF967D0F0200D1FF`) | never | – |
| 81 | +102 | 20 | **charge-rate soft-start — new, inferred**: ramps +20 per 10 ticks toward `0x92` (`+0x0E28`–`+0x0E6A`), each step relayed by #8 (`1FF95` p[1] ← 0x81) | never | – |
| 82 / 83 | +104/+106 | 214 / – | Low Batt. Cutout (B PC.23) / its last-relayed shadow (`+0x0E86`–`+0x1698`, #52) | 214 (read-back) | 214 |
| 84 | +108 | 160 | unknown | never | – |
| 86 / 87 | | 80 / – | VAC Dropout (B) / shadow (#54) | 80 (read-back) | 80 |
| 88 / 89 | | 0 / – | Battery Type (B) / shadow (#55) | 0 (read-back) | 12 |
| 8A, 8C | | 223, 247 | unknown (`+0x0490 strh r0,[r2,#0x16]` with r2 = base+0xFE is `0x8A`'s 223, not a second `0x0B` default — the earlier "second one" reading is withdrawn) | never | – |
| 8E | +11C | 400 | Absorb Hours, obj 925 (GC F53) | never | **90** |
| 90 / 91 | | 30 / – | Shore Brkr Size, relaying twin obj 930 (GC) / shadow (#56) | never | – |
| 92 | +124 | 200 | Max Chrge Rate (B); clamped ≤200 `+0x0E16` → #57 | 200:2046 | 200 |
| 93 | +126 | 0 | Service Mode (B PC.40); 13.6 has sweep record #60, never broadcast | never | – |
| 94 | +128 | – | gate (`== 1`) for the B0/B2/B4 relays (`+0x0F0A`) — unknown | never | – |
| 95 / 96 | +12A/+12C | 1 / 1 | **"AES parameters at fresh-seed values" flag — new, `proven-from-code` for the mechanism**: `+0x0C14`–`+0x0C66` tests `0C==60 && 0D==1080 && 0E==1800 && 0F==1800 && 16==180 && 2B==5632 && 2C==2048 && 31==1000` → `0x95 := result` (`+0x0C98 adds r3,#0xfe; +0x0C9C strh [r3,#0x2c]`), `0x96` shadow, #77 on change | 0:2052 | – |
| 97, 98 | | 1 | unknown | never | – |
| 99 | +132 | 0 | Shore Brkr Size (non-relaying, obj 929) | 0 (read-back) | 0 |
| 9B | +136 | 0 | AES enabled (r) | 0:1700, 1:344 | 0,1,2 |
| 9C / 9D | +138/+13A | 0 / – | unknown; clamped [6,1530] (`+0x0EF8` lit 1530, `+0x16DC`) and fires #49/#50 which substitute `0x8E` (mismatch, above) | never | – |
| A8-AB | +150-156 | forced 0xFFFF at init (`+0x076C`–`+0x0778`) | unknown (11.23 has A0-A3 = 1 / 54000 / 40000 / 25000: its tank thresholds + a flag) | never | – |
| AE, AF | | 0 | unknown (AE's sweep record #26 silent) | never | – |
| B5-B8 | | 0 | tank levels for `TANK_STATUS` instances 0/2/1/0x12 — #45-#48 substitute them at payload[1]; app-written | never | B8: 0 |
| BA / BB | +174 | 290 / – | Absorb (GC F53) / shadow; clamp [240,320] `+0x0F20`–`+0x16FA` → #61/#64 (which name `0xB0`) | never | – |
| BC / BD | | 274 / – | Float (GC) / shadow → #62/#65 (name `0xB2`) | never | – |
| BE / BF | | 292 / – | unknown / shadow → #63/#66 (name `0xB4`) | never | – |
| C2 / C4 | +184/+188 | –/–/0 | tank level, `TANK_STATUS` inst 3 (#93) / shadow (`+0x18EA`–`+0x18FC`) — **`0xC2` IS read on 13.6** (`+0x18EC`), the 14.7 section's "new selector" is new only as a default | never | – |
| CC, CD, CE | | –/54000, 40000, 25000 | tank thresholds (B PC.35) | same | same |
| D7 | +1AE | –/1/1 | Go Power! controllers (r) | 0:2004 | 0,1,5 |
| D8 | +1B0 | –/2/1 | battery count (r) | 0:2044 | 0,1 |
| E3, E4, E5 | | 0 | Cargo/Bath, Bath, Bunk Accent (r, B PC.34) | 1 / 0 / 1 | 0,1 |
| E6 | +1CC | –/1/1 | Progressive Inverter (r) | 0:1243, 1:849 | 0,1 |
| E8 | +1D0 | 0 | panel toggle, unnamed (RVC-GAPS) | 0:4095 | 0,1 |
| E9 | +1D2 | –/1/– | Bath Fan (r) | 0:1959 | 0,1 |
| EB | +1D6 | 0 | Heat Pump (r) | 0:2032 | 0,1 |
| EC | +1D8 | 0/1/1 | Black Tank (r) | 1:2055 | 0,1 |
| EF | +1DE | 1/0/0 | gen/AES mode (r) | 3:1959 | 0-3 |
| F1, F2 | | 11,23 / 13,6 / 14,7 | firmware version pair, ride in `1FACF` #44 (RVC-GAPS) | 13; 6 | – |
| F3, F4 | | 8,7 / 8,13 / 8,13 | also in `1FACF` #44 payload[3]/[4] — the captured `19FACF9C#49440D080D080006` carries 08 and 0D = the 13.6 defaults | via 1FACF | – |
| F5 | +1EA | 0 | floorplan (r) | 8:1915 | 0-10 |
| F7 | +1EE | –/60/60 | retry interval (r); clamped ≥60 at `+0x0DE4` | 60:2086 | 60 |
| 14.7-only | | C2 0, DB 80, DC 200, E7 0 | | | |
| 11.23-only | | 11 1000, 3D 0, 4B 0, A0 1, A1-A3 54000/40000/25000, A6 0, ED 1, EE 1, F0 1 | | | |

**Genuinely unnamed cells with a default** — `02 07 08 09 10 13 14 17 18 21-23 26 2D 30 32 34
35 36 39-3F 47 49 4A 54 57 59 5D 62 6C 6E 6F 70 71 84 8A 8C 97 98 AE AF`: none is touched
outside the defaults routine on 13.6, none is on the wire (`26/36/AE/70/71/72` even have silent
sweep records). This blob cannot name them; only the app image or the owner's eye (the PC.35
route) can.

### The init routine, read to its end

`+0x000C`–`+0x08E2` is one function: sentinel-fill 128 config cells and the runtime cells
`+0x204`–`+0x218`/`+0x3F8`; `cell[count−1] := 100` unconditionally (`+0x0750`–`+0x075C`);
`A8-AB := 0xFFFF` (`+0x076C`–`+0x0778`); disarm the whole timer bank (`+0x0794`–`+0x07BE`);
call `[r4,#0x14]("G8 Test")` (`+0x077E`–`+0x0788`); then **transmit 48 rule records in a fixed
order** via `[r4,#8]` (`+0x07C6`–`+0x08E0`) — the boot re-assert PC.40 saw — and return. That
boot list omits records #25/#26/#51/#60/#83-85, **exactly the seven `15FCE` selectors (`36 AE 26
93 70 71 72`) that have a sweep record and have never appeared on the wire**. The periodic 5 s
sweep is *not* this routine: `pc23-baseline`'s wire order matches neither the init order nor
table index order, so the sweep scheduler is app-side and happens to sweep the set the blob has
ever transmitted (`inferred`).

### The tick's arms, named

| region | what it is |
|---|---|
| `+0x0918`–`+0x1928` | the 10 ms service tick (header pointer); prologue reads `[r4,#4]` count |
| `+0x09A8`–`+0x0A12`, `+0x1240`–`+0x1266` | mode-change handler (`0xEF` vs latch `0x7A`): AES defaults `0C/0D/0E/0F/16`, `73`, `50`, `7B/7C` |
| `+0x0A14`–`+0x0AD4` | AES-profile clamps + sweep re-broadcast (#13-16, 19, 20) |
| `+0x13DA`–`+0x15DA` | AGS-profile arm: `50 := 20, 7B/7C := 30, 73 := 19`, clamps `0C ≤ 60, 31 ≤ 250, 0D ≤ 290, 0E ≤ 7200, 16 ≤ 720, 0F ≤ 1800, 15.lo ≤ 5`, then `01 := 0`, and on `06 == 3` rule #4 + #6/#7 |
| `+0x0AD6`–`+0x0B6E` | `0xBF` trigger state machine |
| `+0x0B70`–`+0x0C12` | F7-retry arming: `timer[1000] := 60` when disarmed and `74 == 0`; `75 := armed ? 2 : 79`; guards on `53`/`55` |
| `+0x0C14`–`+0x0CA8` | "parameters at seed values" detector → `0x95`/`0x96`, #77 |
| `+0x0CAA`–`+0x0DD0` | the four channel-18 timeouts |
| `+0x0DD2`–`+0x0E26` | `F7` clamp + record-4 read-back, `0x15` hi-byte pack, `0x92 ≤ 200` → #57 |
| `+0x0E28`–`+0x0E80` | charge-rate soft-start `0x81 → 0x92`; `0x11` clamp [30,45] → #29 |
| `+0x0E82`–`+0x0F02` (+ `+0x1688`–`+0x16EA`) | change-detect / clamp / relay for `82` (≤244, #52), `86` (≤100, #54), `88` (#55), `90` (≤30, #56), `9C` ([6,1530], #49/#50) |
| `+0x0F04`–`+0x0F6E` (+ `+0x16F6`–`+0x1758`) | same for `BA/BC/BE` (#61-66), gated `0x94 == 1` |
| `+0x0F70`–`+0x0FA4` | quiet-hours comparator: `2A` (rev16) vs `2B/2C`, writes `04`, `37` (inferred) |
| `+0x0FA6`–`+0x100A` | `0x68` unpack + light-dispatch gate; climate rules #34-38 via `[r4,#0xC]`/`[r4,#0x10]` |
| `+0x100C`–`+0x109E` | `0x4A`, `0xF5` floorplan test, record copy (partially read) |
| `+0x10A0`–`+0x1144` | awning interlock + tank rules #47/#48 via `[r4,#0x10]` |
| `+0x1146`–`+0x1196` | per-instance **tank bucketer** loop (instances `0xA8`–`0xAB`, `cmp #0xAC`); dispatches each tank's bucket byte via the `+0x19F8` jump table |
| `+0x12FA`–`+0x1350`, `+0x1858`–`+0x19A8` | F7 re-arm; the four bucketer arms (`CC/CD/CE` thresholds, hysteresis) + `C2/C4` → #93; the arming block `+0x18FE` |
| `+0x19F8`–`+0x1A0A` | `lr`-relative jump-table thunk (`push {r0,r1}; … add lr,r1; bx lr`); `analyse` follows it via `jump_table_arms` |

### A caveat this hands to `g12code.clamp_ags_parameter`

The AES clamps `+0x0A14`–`+0x0AB8` (`AGS_LIMITS`) and the AGS clamps `+0x14C6`… sit inside the
**mode-change handler**, which runs only when `config[0xEF] != config[0x7A]` (`+0x09AE ldrh 0xEF`
… `+0x09B2 ldrh [base+0xF4]` … `+0x09B8 cmp r5,r2 / bne +0x9C0 / bl +0x1240` = skip). They are
**mode-switch clamps, not per-write clamps** — PC.37 walked `0x0C` 120→310→10 in AES mode with no
mode change and no clamp fired, which the "`+0x14C6` retraction" earlier in this document
explained by doubting that `r3` was the base there. `r3` *is* the base (`+0x14AA ldr r3,[r4]`,
only `movs r2,r3`/`movs r1,r3` copies until `+0x14C6`); the clamp is real and simply did not run.
So `clamp_ags_parameter`'s docstring "what the G12 will store" overstates: it is what the G12
stores *at the next mode switch*. Not edited here.

### What remains, and what settles it

1. ~~The four cross-selector relays and gate `0x94`.~~ **ANSWERED ON THE COACH 2026-09-03
   (`BUS-BASELINE.md` PC.50):** with `0x94 := 1` the relays fire, and the payload carries
   **`0xB0`/`0xB2`'s value (65520), not `0xBA`/`0xBC`'s (292/274)** — the rule engine reads the
   cell its record names, and "two copies of one value" is refuted. `1FF97` goes out all-`FF`
   because `0xB4` is unset, so the family is unprovisioned on this coach.
2. Where 1020 comes from on the mode switch (PC.39): the blob clamps to 1000, so either the app
   holds 1020 or the panel frame was missed — a capture with the panel's `1FED9` filtered *in*
   during an AGS→AES switch.
3. Init once vs periodic: init disarms every timer unconditionally, so a 5 s re-call would let
   no 60 s retry complete; the sweep order says the scheduler is the app's. App image.
4. Header word `+0x04` and the `[r4,#0x14]` callback: app image.
5. The ~45 unnamed cells and the units of `0x14`, `0x32`, `0x35`, `0x69-0x6B`, `0x84`, `0x8A`,
   `0x8C`: no reader in the blob. **Their live values are now readable** — the config read-back is
   a general read primitive and all 256 cells were dumped (`BUS-BASELINE.md` PC.49). A value is
   not a name, but the cells are no longer invisible.
6. Tooling: ~~`config_defaults` should model `[rB,rM]`~~ **done 2026-09-10** (`_mem_target`; 128
   selectors on 13.6, the runtime cells split off); **`analyse` now follows the `lr`-relative jump
   table** (`follow_jump_tables`, 2026-09-10), which was the largest coverage gap. The `bl`-as-function
   over-count is **left deliberately, 2026-09-11**: the obvious fix — continue past a `bl` only when
   its target opens `push {..,lr}` — is sound on 13.6 (every `bl` but the jump thunk is a long
   branch) but **wrong on 14.7**, which calls leaf functions that return via `bx lr` without pushing
   `lr`; keying on the prologue there stops after real calls and loses a quarter of the code
   (measured: 95% → 70%). Telling a long branch from a leaf call needs interprocedural return
   analysis, not worth it for a cosmetic count, so `functions` stays an over-count.


---

## Executing the blob — `observed-in-emulation` / `observed-on-hardware`, 2026-10-21

The blob has now been **run**, not only read. `firefly-emulator` (`firefly_emu/g12.py`) loads table
`0x67` unmodified into a Unicorn Cortex-M4 (mapped at the inferred `0x08038AE4`; it is
position-independent, so the base does not matter), calls init and the tick with a context built to
the layout below, and sends the rule records the code activates. New label for this section:
**`observed-in-emulation`** — the 13.6 code executed on these inputs did this. It proves what the
blob does; it says nothing about the G12 application around it, which is modelled from captures
and named as such.

### The context, complete for ABI 13 (11.23 and 13.6)

```
+0x00  u32  cell array            u16 per selector, selector N at index N, 0xFFFF = unset
+0x04  u16  cell count            the timer bank sits at the top (cell[count-2] ... cell[count-19])
+0x06  u8   ?                     never read by 13.6
+0x07  u8   extra seconds         +0x0936/+0x095A: the first two timers drop by ctx[7]+1 per call;
                                  the 16-timer loop +0x098E-+0x09A6 drops each by exactly 1
+0x08  fn   activate rule N       \
+0x0C  fn   deactivate rule N      | "The per-rule broadcast switch" above
+0x10  fn   is rule N active?     /
+0x14  fn   log(char *)           called once by init with "G8 Test" (+0x077E-+0x0788)
+0x18  fn   send(record *)        a 12-byte frame-table record copied to the stack
                                  (+0x10B8 ldr r3,[r4,#0x18] ... +0x10D2 blx ip)
```

`+0x18` was already named as "transmit" in "The frame table and the AWNING IGNITION INTERLOCK";
executing the code confirms it is a context slot like the other four and that the interlock cannot
run without it (with the slot absent the first ignition edge branches to address 0). ~~Every call
being one second follows from the 16-timer loop~~ (*wrong: one call is 10 ms; see "The tick is 100 Hz"
below*): any count works for `+0x04` as long as the bank
clears the config cells (the emulator uses `0x200`). The header word `+0x04` is the ABI version
(next steps #4): 13 on 11.23 and 13.6, both of which run under this layout; 14.7's 17 does not
(its slots moved) and is mapped in "The context for ABI 17 (14.7)" below.

**Re-derived by execution, all agreeing with this document:** init's 128 sentinel defaults and its
48 boot activations; the tank bucketer (raw cells `B0`-`B3` = `0BFC1` instances 1-4 → buckets 0-3
in `B5`-`B8`, descending: ≥ `CC` empty, < `CE` full; `TANK_STATUS` resolution 3); #47 gated on
`0xEC`, #48 on floorplan ∈ {RA, RD, WA, WD}, #93 off in AES; the change-detect relays (`82` → #52
after a write); the four `0x95` seed-test cells. Two values this document did not have:

- **`0x16 = 115` comes from the AES mode-change handler.** From fresh cells with `EF := 3` the first
  tick leaves `0D 1080, 31 1000, 73 18, 16 115` — 115 is the coach's swept value (115:1967 in the
  table above), so the coach's max engine run time is the handler's, not only the panel template's.
- **Switching AES → AGS leaves `0D = 270`**, `50 = 20`, `7B/7C = 30`: the 12 V AGS stop voltage, not
  the 290 ceiling of the AGS clamps.

### The tick is 100 Hz — `proven-from-code`, 2026-10-21

The tick is called every **10 ms**, and the seconds come from a divider inside it. This corrects
"The tick rate is ~1 Hz" above, which took the 714 ms `BF` gap as one tick.

- Init arms the top cell, `cell[count-1]`, to **100**. Each tick drops it by `ctx[7]+1`. Only
  when it wraps does the 16-timer bank drop by 1, and the cell reloads as `cell - extra + 99`
  (13.6 `+0x0968`, 14.7 `+0x1FA2`). So the bank counts seconds and `cell[count-1]` is its
  100-tick divider.
- The first two timers drop by `ctx[7]+1` every tick, so they count 10 ms. `ctx[7]` is the number
  of missed ticks; the application passes 0 normally.
- **14.7 settles the rate.** A held button's ramp is re-sent when a 300-tick counter (cell `0x1FD`)
  is a multiple of 100. At 100 Hz that is the once-a-second re-send the panel's own ramp uses; at
  1 Hz it would be every 100 s.
- The measurements fit. A bank timer armed to 1 fires at the next divider wrap, anywhere from 0 to
  1 s later: `BF` clears 714 ms after it is set in PC.24 and 343 ms after in PC.39. The coach relays
  a config write in 2–10 ms (PC.23) and locks the awning 4 ms after ignition-off (`pc28`). Both
  are one tick, with no event-driven call needed.

Running the blob at 100 Hz turns on three settle-after-boot behaviours that a 1 Hz emulation hid,
all `observed-in-emulation` on 13.6:

- **Charge-rate soft start** (`+0x0E48`): cell `0x81` rises by 20 every 10 s up to cell `0x92`,
  sending rule #8 (`1FF95`) at each step. The coach reads `0x81 = 0x92 = 200`, i.e. settled.
- **Quiet hours** (`+0x0F70`): cell `0x04` comes from comparing the clock cell `0x2A` with
  `0x2B`/`0x2C`.
- **The AES watchdog** arms a 60 s OFF of channel 18 when cell `0x53` is set. Init defaults `0x53`
  to 0; on the coach the WS500's out-of-range DM_RV keeps it at `0xFFFF` (see the live read-back
  below), so the coach never sends it.

### The context for ABI 17 (14.7) — `proven-from-code` / `observed-in-emulation`, 2026-10-21

Mapped by disassembling both builds and running 14.7's blob in the emulator:

```
+0x00  u32  cell array            as ABI 13
+0x04  u16  cell count            as ABI 13
+0x07  u8   extra ticks           still read by the tick (ldrb [r0,#7])
+0x08  fn   activate rule N       \
+0x0C  fn   deactivate rule N      | as ABI 13
+0x10  fn   is rule N active?     /
+0x14  —                          ABI 13's log slot; gone with the "G8 Test" string
+0x18  fn   send(record *)        as ABI 13
+0x24  fn   button(n) -> state    push-button input n: 0 idle, 1 pressed, 2 tapped, 3 held,
                                  4 released after a hold
+0x40  fn   notify(value)         return value unused
```

Call sites, 13.6 / 14.7: activate 88 / 91, deactivate 5 / 10, is_active 6 / 11, send 14 / 34,
`+0x24` 0 / 7, `+0x40` 0 / 3.

**`button(n)` moves the wall switches into the blob.** The five states are table `0x20`'s input
phases, and 14.7's table `0x20` has no input rules. The blob polls inputs n = 0, 1, 3, 5, 6, 8 and
9 and keeps the last state of each as a nibble in cell `0x116` (a real runtime cell, despite the
"false hit" note in the `0xDA`–`0xDC` section). On a change it sends from its frame table
(`1FEDB`), then `1FFB8` DIGITAL_INPUT_STATUS `[n+1, down, FF, 02, FF FF FF FF]`, where down means
state 1 or 3. 14.7 also feeds cell `0x28` (ignition) from `1FFB8`. Executed:

| input | tap | hold | release |
|---|---|---|---|
| 0 | toggle output 22 (`FA 05`) | ramp (`21`), re-sent every 1 s | stop (`04`) |
| 1 and 3 | toggle output 35 | ramp, re-sent every 1 s | stop |
| 5 / 6 | relay 3 / 4 on at 200 while pressed | — | off |
| 8 | toggle output 44 | — | toggle again |
| 9 | output 3 off, then lock (`33`), on press | — | unlock (`34`) |

**`notify(value)`** is called three times by a defaults routine, with cells `0x0E` (300), `0x0F`
(1800) and `0x16` (115) just after it sets them. What the application does with them is not in
the MCF.

### Table `0x61` #15 (`1FFFD` instance 1 → cell `0x55`) is a RECEIVE — `observed-on-hardware`

`StatusRule.direction` reads kind `0x28` as a transmit and `g12rules` records `1FFFD` as "never —
gated". It is never seen from the G12 because the G12 **receives** it:

- `pc23-baseline`: the G12 sweeps `0x0A` (AES measured voltage, copied from `0x55`/`0x56` at
  `+0x190C`/`+0x1912`) as **1077**; the APS-500 (SA `0x80`) sends `1FFFD` instance 1 with raw
  **1077** in all 60 of its frames. 53.85 V on a 48 V house bank; `1AAFD` instance 1 (cell `0x56`,
  12.3 V) is the other source.
- `pc28`: `0x0A` follows the APS-500's `1FFFD` through 1086 … 1095 over the 366 s, step for step.
- Run the same way, the emulated G12 fed the APS-500's frames into `0x55` reproduces both.

So **kind does not give direction**: `0x28` is a transmit on #1 (`1AAFD`) and a receive on #15.
`StatusRule.direction` is unchanged by this note; a caller wanting the truth for #15 needs an
override until a second distinguishing field is found.

### `+0x50` is written by the G12 receiving its OWN ignition frame — next steps #8 answered

The G12 sends `1FBDA` instance `0xFB` itself (table `0x20` #28/#29, input pin 10), and table `0x61`
#0 copies `1FBDA` instance `0xFB` byte 1 into cell `0x28` (= struct `+0x50`). `pc28` shows the loop
closing on the wire, `observed-on-hardware`:

```
260.563  19FBDA9C#FB00FFFFFFFFFFFF    ignition off (pin 10)
260.567  19FEDB9C#03FF0022FF00FFFF    UNLOCK, ch 3 (record 13, the +0x183A tail)
261.099  195FCE9C#0500C003...         0x05 832 -> 960
261.108  195FCE9C#0600000000000000    run state 0x06 3 -> 0
```

Four milliseconds, so the application both **loops its own transmissions through its receive
rules** and ~~**runs the tick on the event** rather than waiting for the 1 Hz call~~ — *2026-10-21: no event
needed; the tick runs every 10 ms, so the next tick picks the cell up* — the same
latency PC.23 measured for config-write relays (2-10 ms). Replaying only the recorded ignition and
sensor frames into the emulated G12 (`tools/fidelity.py --g12`) puts the UNLOCK at **260.569 s**
against 260.567 s on the coach, from this blob's code. What remains app-side and unread: cell `0x05`
(832 ↔ 960 around the stop), ~~and whether the event-driven tick advances the timers~~ (moot at 100 Hz).

The G12's own self-addressed `1FED9` rules (#2/#3/#6/#7, "bus loop-back" in the table above) are
the same mechanism: the emulator applies every frame the G12 sends to its own receive path, and
the `0x79`/`0x7D` writes follow.

### Application behaviour the emulator models from captures (for completeness)

Not from this blob; cited so the boundary is explicit. Rule repeat per kind (`KIND_OBSERVED`); the
15FCE sweep = every kind-`0xC0` record activated since power-up, one burst every 5 s about 1 ms
apart (`pc23`), which makes the sweep set history (the coach sweeps `0x11` although a stored 45
no longer triggers its clamp relay); answers to the panel's 14 s requests (`1FED8` + four `0FF01`;
product ID by BAM, `BUS-BASELINE.md` line ~1390); DM_RV `05 84 1F 16`, `1FFE2` and `17EFE` every
5 s; the bus clock `1FFFF` (table `0x61` #3 puts cell `0x2A`'s low byte, the hour, at byte 4;
`0x2A = hour | minute << 8`); input pins held from 1 s and refreshed each second (PC.24).

### Live read-back on the coach, 2026-10-21 — `observed-on-hardware`

PC.49's read-only sweep repeated (D3 queries from `0xC0`, 4 Hz): 256/256 answered; no CAN id
outside a 25-minute live baseline but the probe's own `19FED9C0`.

- **#15 confirmed as a receive, live:** cell `0x55` = 1071 = the WS500's (`0x80`, formerly
  "APS-500") `1FFFD` instance 1 at the same instant, and `0x0A` steps 1072 → 1071 with it; `0x56`
  = 248 = the G12's own house reading (`1AAFD` instance 1).
- **Table `0x61` params are the accepted value range.** #14 (`1FECA`, byte 0, params 80..65535)
  receives the WS500's DM_RV, whose byte 0 is 5: out of range, and cell `0x53` (block C's guard,
  default 0 in init) reads `0xFFFF`; block C's status `0x77` reads 0 accordingly. Same reading fits
  #0 (0..257), #2 (0..3), #6 (0..200) and the transmit records' brackets (10–16 V, -51..82 °C).
- **Cell `0xC2` is packed:** `0x6450` = LPG level 80 | resolution 100 << 8; #16 (`1AAB7`, u8) and
  #93 (`TANK_STATUS`, one byte) both take the low byte.
- **The blob explains most of the hidden state:** an emulation seeded only with what the G12
  broadcasts reproduces 231/256 cells, 189 of the 214 never broadcast. The rest are owner settings
  never swept (`82`/`83` 210, `86`/`87` 60, `88`/`89` 13, `99` 5, `BA` 288, `BC` 268), cells the
  13.6 code never writes (`12 19 1A 1D 1E A0 DB DC E7 ED F0`; `1D`/`1E` = 14, `DB`/`DC` = 29 as in
  PC.49), and live drift.
