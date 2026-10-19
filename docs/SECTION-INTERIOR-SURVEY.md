# Section-interior survey — the disassembly frontier (`MCF-FORMAT.md` §10 item 12)

A data-driven map of what is still undecoded inside the three code sections, so a dig starts
from evidence rather than from the top of a 100 KB image. Generated 2026-09-10 from the Ghidra
projects (`guicode136`, `lcd136`, `g12code136`) with `ghidra/list_all_fns.py` and
`ghidra/fn_context.py`; the coverage counts cross-reference every function body against the
~1000 distinct `00xxxxxx` addresses cited across `docs/` and `tools/`. This is an **orientation
map**, `inferred-from-data`: it says where the dark code is and what a function's call context
suggests it does, not what any dark function *is* — that still takes a dig.

## Coverage

| section | load | fns | in-fn bytes | documented (fns / bytes) | dark (fns / bytes) |
|---|---|---|---|---|---|
| GuiCode | `0x00240000` | 154 | 93 682 (89% of section) | 68 / 75 628 | 86 / 18 054 — **19%** of fn-bytes |
| LCD Firmware | `0x00210000` | 467 | 54 832 (87% of section) | 61 / 16 474 | 406 / 38 358 — **69%** of fn-bytes |
| G12 | `0x08038000` | — | — | — | see below (a rule table, not functions) |

A function counts as *documented* when some address a doc or tool cites falls inside its body.

**GuiCode is nearly mapped.** Only 19% of its function-bytes are dark, and because the whole
section is GUI runtime, those dark functions are the interesting remainder — not library noise.
This is where a dig has the best ratio of GUI-relevance to unread code.

**LCD Firmware is mostly dark, but mostly library.** 406 of its 467 functions are unattributed,
yet the bulk are STM32 HAL / FatFs / USB / libc leaf routines (small, many-callered). The
panel-specific logic already has names (`FUN_0021BAB4` scanner, `FUN_0021B8B4` sender,
`FUN_0021BE50` header reader, the receive filter). The dark set still hides a few **top-level
orchestrators** worth separating from the library — see the table below.

**G12 is not a function map.** Ghidra defines one function in the image; the G12's behaviour lives
in the **rule table `0x67`** (Thumb-1 rule program), decoded in `docs/G12-CODE.md` by walking the
table, not by enumerating functions. Its frontier is tracked there, not here.

## The largest dark functions, with their call context

Roles are `inferred-from-data` from callers/callees only.

### GuiCode — the high-value frontier

| fn | bytes | called by | apparent role |
|---|---|---|---|
| ~~`FUN_00240d20`~~ | 4904 | `GuiCode_entry4` only | **DECODED 2026-09-10, `GUICODE.md` Finding 75** — the received-status parser for DGN `0x1FDFF`, dispatched by source address + instance, scaling into SRAM `0x20030068`; placed `GuiCode_entry4` itself as the RV-C receive path. **Scope corrected by Finding 77 (2026-09-25):** it is the solar-controller parser for 17 DGNs, including a DM_RV branch keyed on adopted source addresses |
| ~~the smaller helpers~~ | ≤634 | various | **SWEPT 2026-09-10, `GUICODE.md` Finding 76** — the remaining dark GuiCode set (79 fns / 8.6 KB, none > 634 B) is compiler soft-float runtime, state init/mirror, small render/layout helpers and touch/CAN-command leaves; no undecoded subsystem left |
| ~~`FUN_00254f26`~~ | 1638 | **`FUN_00256c54`** (the widget renderer) | **DECODED 2026-09-10, `GUICODE.md` Finding 73** — the 8-octant circle/arc rasteriser behind the rounded rectangle, capsule, button and circle widgets; pinned the corner radius (`+0x32`) and octant mask (`+0x8b`) |
| `FUN_0024f998` | 428 | `FUN_00247bf8` (the touch main) | a touch-handler helper |
| `FUN_0025558c` | 412 | `FUN_00255e90` | calls the text/layout helpers (`0x250e12`…) — a text-render helper |

### LCD Firmware — orchestrators worth pulling out of the library

| fn | bytes | shape | apparent role |
|---|---|---|---|
| ~~`FUN_0021317c`~~ | 1436 | called from a scheduler stub, 50 callees | **DECODED 2026-09-10, `LCD-FIRMWARE.md` Finding 15** — the **CAN mailbox-servicing task** (TX-complete + RX-FIFO per-object state machine), run as one traced step of the LCD main loop; driver code, not the RV-C status loop the first pass guessed |
| ~~`FUN_0021ce28`~~ | 1086 | scheduler-step caller, 20 callees | **DECODED 2026-09-10, `LCD-FIRMWARE.md` Finding 16** — a framed serial-ring receiver for a multi-node switch bus (node pairing `0xFA` + per-node status `0xA5`); the Wireless Switch Panel lead. Another main-loop step, driver/protocol code |
| ~~`FUN_002145fc`~~ | 1842 | called from system-init | **DECODED 2026-09-10, `LCD-FIRMWARE.md` Finding 17** — the STM32F7 RCC + PWR clock/power bring-up (peripheral bases `0x40023800`/`0x40007000`); HAL/startup, not application |

## Where to dig first

1. ~~**`FUN_00254f26`**~~ — **DONE 2026-09-10** (Finding 73): the rounded-shape / circle rasteriser.
2. ~~**`FUN_00240d20`**~~ — **DONE 2026-09-10** (Finding 75): placed the RV-C receive path (`GuiCode_entry4`) and this DGN-`0x1FDFF` status parser.
3. ~~**`FUN_0021317c`**~~ — **DONE 2026-09-10** (Finding 15): the CAN mailbox-servicing task (a driver-layer pump), confirming the LCD's dark bulk is peripheral/library code; the RV-C application decode is in GuiCode (Finding 75), not here.
4. ~~the remaining GuiCode helpers~~ — **SWEPT 2026-09-10** (Finding 76): soft-float runtime + small state/render/touch helpers; no large undecoded GuiCode routine remains.

**Where the frontier stands (2026-09-10).** GuiCode is fully placed — the dark set was swept down the
size curve (Finding 76), every function confirmed toolchain runtime or small plumbing (state-unpack,
object-tree, render, touch, soft-float), no undecoded routine of consequence. Both flagged LCD
orchestrators are placed (Findings 15/16) and confirmed driver/protocol code. What is left is the
LCD's library floor (STM32 HAL / FatFs / USB / libc / soft-float) -- and `FUN_002145fc`, the last
dark item over 1 KB, is now placed as the RCC/PWR clock bring-up (Finding 17), so **no dark function
over 1 KB remains in any section**. The G12 frontier remains its rule table (`docs/G12-CODE.md`).

Regenerate the inventory with `ghidra/list_all_fns.py` (per project) and label candidates with
`ghidra/fn_context.py`.
