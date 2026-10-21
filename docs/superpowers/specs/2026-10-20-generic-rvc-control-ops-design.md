# Generic RV-C control ops: design

Date: 2026-10-20
Status: **draft, for owner approval. No code is written until it is approved.**
Branch: `generic-rvc-ops`

## Why

A downstream project is replacing a G12-driven air conditioner with a unit controlled only over
RV-C. Its Climate page needs about fifteen new controls that send RV-C commands and light from
RV-C status. Its Home page needs a mode shown as text. The project's own design (layout, payloads,
bench plan) is private; this document specifies the fireflymcf ops it needs.

The ops this requires mostly exist already, but they are pinned to one page: `add_aes_batt_heat`,
`add_aes_batt_heat_status`, `add_aes_bms_button_status` and `add_card_readouts` work only on the
compact AES ELECTRICAL frame (object 829). The owner's rule for this work is that **an op takes a
page or parent object and works anywhere, and stays page-specific only when its behaviour
belongs to one page.** The existing AES ops keep working as thin presets over the generic ones.

## Goals

- `add_rvc_button`: a button on any page that sends a fixed RV-C frame on tap (optional hold). It
  has a toggle form that sends one payload when a status reads off and another when it reads on.
- `add_button_status`: lights one or many buttons from registers, comparing by bit, equality,
  inequality or at-least. One per-tick routine serves the whole batch.
- `add_enum_label`: shows a register's value as text from a value table, on any page.
- `add_card_readouts` takes `parent`.
- Two small re-pointing extensions: `pin_filter_instance` can move an entry from one instance to
  another, and a new `set_binding_bytes` rewrites chosen payload bytes of existing bindings.
- `add_aes_batt_heat` and `add_aes_bms_button_status` become presets of the generic ops.

## Non-goals

- A stepper that sends a payload computed from a register. The downstream project keeps the stock
  setpoint arrows, and its device interprets their step codes.
- Readout scaling (multiply, temperature presets). The stock thermostat readouts are reused.
- Silencing the G12's thermostat broadcast (a generalised `suppress_tank_status`). Deferred.
- Priority or source-address parameters. They are firmware-forced (below).

## Anti-goals (a failure even if it works)

- **Any existing project compiling to different bytes after the refactor.** This covers every
  recipe fireflymcf's tests pin, the private research repository's pinned builds, and the
  downstream projects' recipes. Each downstream project adds a byte-pin test for its latest
  recipe *before* the refactor lands.
- A generic op that silently assumes object 829's shape.
- A toggle or hold control on a group GuiCode also drives, so that the two fight over its variant.
- A status light that shows "on" when the field is RV-C "not available".

## What the design rests on (cited)

**Sending.** A button sends through 12-byte bindings in its touch id's group. Each binding holds an
18-bit DGN, a gesture mask, a variant nibble and a fixed 8-byte payload, stored as two little-endian
words (`newbutton.binding_raw` / `binding_payload`). The LCD sender hard-wires priority 6 and source
address 0x9F (`FUN_0021B8B4`, `bfi r2,r1,#8,#0x12` / `movs r3,#6` @0x21B9B2-0x21B9C0). For a PDU1
DGN the low byte is the destination (Batt Heat's `0x0EF70` → SA 0x70). The panel transmits on RV-C
itself. Coach-proven by Batt Heat, the APS-500 reboot and the LEVEL controls. Payloads are fixed in
flash: nothing on the binding path computes a byte at send time.

**Gestures.** These shapes are coach-proven (`levelcontrols.py`, `gestures.py`,
`docs/CONTROL-BEHAVIOR.md` §2-3):
- **Tap, one frame per press of any length:** group prefix `10 00 00 00`, binding mask `0x06`.
- **Hold:** hold nibble = seconds, mask `0x04`, held rate 0.
- **Tap plus hold:** Batt Heat's group-17 clone, off on `0x02`, enable on `0x04`.
- **Most actions run on release.**

**Variants.** A group's bindings carry a variant nibble, and only bindings matching the group's
live variant fire. A group whose byte 1 is `0x80` does not cycle its variant on release. An
external setter chooses it: `setVariant(group, v)` through the function pointer at `0x20039908`
(`FUN_0021B5E8`). The Low Volts bridge (`followchannel.build_display_bridge`) drives group 129
this way every tick. It was proven on the coach for tap and, in LV-hold, for hold.

**Stock uses the same mechanism for Climate.** `fireflymcf bus-map --dgn 1FFE2 --detail` on stock
13.6 shows GuiCode reading var 13 (THERMOSTAT_STATUS_1, instance 0) and calling the setter for
groups 0x8C-0x92 (140-146). **Groups 140-146 are GuiCode-driven and must not host a new toggle or
hold.** `newbutton.guicode_selected_groups` already excludes them, as it does 130-139.

**Per-tick routines.** Appended GuiCode chains on the one hook at GuiCode+`0xEE3C`
(`followchannel.hook_state`). `bitcombine` folds receive slots into a display cell, and
`switchbuild.author_switch` draws a wtype-6 switch over arms on that cell. Both are coach-proven
(the v32 heater status). The arms must each draw the whole control, caption included, or a repaint
loses it (v31).

**Receive entries.** An entry matches on the 18-bit DGN only, then on `payload[+5 low nibble] ==
+4` unless `+4` is `0xFF` (`FUN_0021C760`). It extracts by mode (`statusbind.LIVE_MODES`: whole
byte, u16, or one 2-bit field) into a slot that reads as the stale sentinel until a frame arrives.
New DGNs count against the silent registration cap of 83 (`statusbind.REGISTRY_CAP`); 13.6
registers 66. A new entry on an already-registered DGN costs no registration.

**Stock thermostat entries are already pinned per instance.** 13.6 carries instances 0-6:
- 1FFE2: vars 13, 14 (instance 0), 15, 131 (1), 132, 133, 202 (2), 134, 135, 167 (3), 136, 137,
  168 (4), 156 (5), 157 (6).
- 1FF9C: vars 9-12 (instances 0-3).
- Bindings: zone setpoint arrows exist per instance (groups 130/131 instance 0, 132/133 instance 1,
  134/135 instance 2, 136-139 instances 3/4). Mode and fan groups exist for instances 0-4 (for
  example 153-159 carry instance 2).

Only the instance-0 entries (vars 9, 13, 14) and var 167 reach a drawn cell; the rest are
write-only. **Re-pointing the panel to another instance is therefore a change of `+4` on vars 9,
13 and 14 from 0 to N.** `pin_filter_instance` refuses that today because it only narrows from
`0xFF`. A pinned entry for an instance nobody sends simply stays stale.

## The ops

All five ops share these rules:

- **Target:** `parent` is an object index or `{"ref": op_id}`.
- **Hardware status:** each op is `hardware_verified = False` until a coach (or bench, where this
  says so) run proves it.
- **Presets:** a preset keeps its own verified status exactly as today.
- **Handles:** each op leaves a handle naming its object, group, cells and arms.

### 1. `add_rvc_button`

A button that sends RV-C frames on any page.

| parameter | type | default | meaning |
|---|---|---|---|
| `parent` | int\|ref | required | object the button is drawn on |
| `x`, `y`, `w`, `h` | int | required | absolute box on the 800×480 canvas |
| `text` | string\|null | null | caption; `\n` makes two lines |
| `glyph` | `{slot, char}`\|null | null | icon drawn above the caption (or centred without one); the char must not fall back to the placeholder |
| `font` | int\|null | null | caption font slot; null keeps the donor's |
| `dgn` | int | required | 18-bit DGN every binding of this button sends |
| `tap` | `{payload}`\|null | null | 8-byte hex payload sent once per press |
| `hold` | `{payload, seconds}`\|null | null | sent once after a hold of `seconds` (1-15); a tap still sends `tap` |
| `toggle` | object\|null | null | `{off_payload, on_payload}`: the toggle form (below); excludes `tap`/`hold` |
| `colour` | `[r,g,b]` | `[65,81,163]` (0x4151a3) | body fill; must already be in the build's mode-7 palette |
| `group_pool` | `"free"`\|`"hold_safe"` | `"free"`, or `"hold_safe"` when `hold` or `toggle` is given | where the command group comes from |

At least one of `tap`, `hold` or `toggle` is required. The group comes from the pool's first slot
and never from 68/69 (`levelcontrols.RESERVED_GROUPS`). A pool of `"free"` with `hold` or `toggle`
is refused: a hold or toggle on a GuiCode-driven group is dead (v45).

**Placement.** By default the records are pasted as a new object at absolute `x, y` under a clone
of `parent`'s first wtype-5 attach. This is `move_control`'s paste path (`insert.insert_object` +
`absrecord.absolute`), coach-proven on 2026-10-20. It is refused if a touch region would meet an
existing one on any page that reaches `parent`, or if a record leaves the canvas.

The records are the Batt Heat body: a filled box, an inset outline, the caption, the glyph if
given, and a wtype-12 touch region whose id is the group. The donors are the same stock records
`aescompact.build_batt_heat_button` reads. A second placement mode, `append` (records appended to
`parent`'s own stream at flow-relative coordinates), exists only for the preset's byte identity;
it is not a public parameter.

**Gestures:**

| form | group prefix | bindings |
|---|---|---|
| tap only | `10 00 00 00` (the LEVEL stepper's) | one, mask `0x06`, variant 0 |
| tap + hold | Batt Heat's 1-s group-17 clone with the hold nibble set to `seconds` | tap mask `0x02`, hold mask `0x04` |
| hold only | the same, with no tap binding | (`add_alt_reboot`'s shape) |

**Toggle form.** One group with byte 1 `0x80` (no cycle on release). It has two bindings on
`dgn`: variant 0 sends `on_payload`, and variant 1 sends `off_payload`. Both use the tap mask
`0x06`. The group entry's byte 0 low nibble is 1 (the variant maximum), giving the header
`11 80 00 00`: `FUN_0021B5E8` stores a variant only while it is at most that nibble (gate at
0x21B5FC needs byte 1 & 0xC0 == 0x80; bound at 0x21B60A-0x21B610), so nibble 0 would refuse
`setVariant(g, 1)`. Stock groups 129/143/146 carry `11 80 ...`. The op writes no routine. The variant is driven every tick by the `add_button_status`
entry that lights this button, which calls `setVariant(group, lit ? 1 : 0)`. So the button sends
"on" while its status reads off, and "off" while it reads on.

A toggle button that no later `add_button_status` drives is refused at the end of compile
(`TOGGLE_UNDRIVEN`). Its variant would sit at 0 forever and it would only ever send "on".

**Error codes:**
- `BAD_PARAMS`: a malformed payload, no gesture, `toggle` with `tap`/`hold`, or the `free` pool with `hold`/`toggle`.
- `NO_FREE_GROUP`: the pool is empty.
- `NO_GLYPH`: the glyph falls back to the placeholder.
- `NO_BODY_COLOUR`: the colour is not in the palette.
- `TOUCH_OVERLAP`, `OFF_CANVAS`.
- `GROW_REFUSED`: the binding table, arena or gates.
- `INSERT_REFUSED`.
- `TOGGLE_UNDRIVEN`.

**Verify:**
- Re-decode every binding through `newbutton.binding_wire_frame`, and assert the CAN id (priority
  6, SA 0x9F) and the payload of each, the gesture mask, the variant nibble and the group prefix.
- Re-walk every page root that reaches `parent`, and assert the box lands at `x, y, w, h` and that
  nothing else moved (`assert_layout`'s comparison).

**Handle:** `{object, group, touch_id, binding_start, x, y, w, h, text, glyph, font, colour,
toggle}`.

### 2. `add_button_status`

Lights one or many buttons from receive slots. One routine and one cell per button.

| parameter | type | default | meaning |
|---|---|---|---|
| `buttons` | array | required | entries below; one op = one fold routine |
| `off_colour` / `on_colour` | `[r,g,b]` | 0x4151a3 / 0x00aeef | defaults for every entry; each must be in the palette |

Each entry has these fields:

| field | type | default | meaning |
|---|---|---|---|
| `button` | ref | required | an `add_rvc_button` handle (or a preset's) |
| `source` | object | required | `{var}`, `{ref: append_filter op}` or `{dgn, instance, compare}` (resolved like `resolve_register`) |
| `mask` | int | 0xFFFF | ANDed with the slot first |
| `shift` | int | 0 | then shifted right |
| `compare` | object | required | exactly one of `{"bit": m}` (any bit of m set), `{"eq": v}`, `{"ne": v}`, `{"ge": v}` |
| `valid_max` | int\|null | null | for `ge`/`ne`: a value above it reads off |
| `off_colour` / `on_colour` | `[r,g,b]` | the op's | per entry |

**The routine.** For each entry it reads the slot, applies mask and shift, and computes `lit`:
- **Stale or above `valid_max`:** off. The stale sentinel is off.
- **Otherwise:** the comparison.

It writes `lit` (0/1) to the entry's cell. If the button is a toggle, it then calls
`setVariant(group, lit)`. The routine is chained once on the per-tick hook, so twelve buttons cost
one routine and one detour, not twelve. It extends `bitcombine`'s emitter with `eq`/`ne`/`ge`
tests. The emitted code is verified instruction by instruction (`thumb.assert_disassembles_to`)
like every appended routine.

**`valid_max` exists for RV-C "not available".** An 8-bit RV-C field reports `0xFF` (and
`0xFB-0xFE` are reserved), which is `ge` every threshold. A `ge` or `ne` compare on a raw RV-C
byte therefore needs `valid_max: 0xFA`. The op refuses a `ge` without `valid_max` on a whole-byte
or u16 source (`BAD_PARAMS`), so the omission cannot ship.

**The drawing.** The button's static body is replaced by one two-arm wtype-6 switch on its cell.
Each arm draws the whole button: box, outline, caption and glyph, in the off or on fill. This is
`aescompact.add_batt_heat_state_switch`'s shape with `BMS_BIT_STATE_ARMS`, made parent-generic.
The switch is enrolled for live redraw. Cells come from `followchannel.scan_display_cell`
(tier-2, spare and GuiCode-unreferenced) first, and only then from `readout.allocate_fresh_cells`
(tier-3, reported unverified per instance, as today).

**Error codes:**
- `BAD_PARAMS`: no compare or two compares, `ge` without `valid_max` on a byte source, two entries
  naming one button, or `mask`/`shift` out of range.
- `BAD_REF`: a button or source names no matching handle.
- `NO_SUCH_REGISTER`: a `{dgn, …}` source resolves to no entry.
- `NO_DISPLAY_CELL`, `NO_BODY_COLOUR`.
- `INDICATOR_REFUSED`: the routine or a switch could not be built or spliced.
- `DETOUR_REFUSED`.

**Verify:**
- Each button's switch: there is exactly one, on its cell, enrolled, and each arm draws the full
  button with the caption.
- The routine is decoded back to its entries (source address, mask, shift, compare, cell, group).
- The chain from the hook reaches it.
- **An emulated tick:** fireflymcf's `thumbsim`, as the readout tests use, runs the routine over
  chosen slot values (stale, each threshold, `valid_max + 1`) and asserts every cell and every
  `setVariant` call.

### 3. `add_enum_label`

Shows a value as text from a table, on any page.

| parameter | type | default | meaning |
|---|---|---|---|
| `parent` | int\|ref | required | |
| `x`, `y` | int | required | absolute pen position |
| `w`, `h` | int | required | the label box (wtype-11 text clips to it, `GUICODE.md` Findings 69-70) |
| `font` | int | required | |
| `align` | `"left"`\|`"centre"`\|`"right"` | `"left"` | |
| `colour` | `[r,g,b]` | white | text colour (palette) |
| `background` | `[r,g,b]`\|null | null | the arm's fill, so a shorter word does not leave the longer one's edge (the LEVEL page's repaint lesson) |
| `source` | object | required | as in `add_button_status`, with `mask` and `shift` |
| `values` | object | required | `{"0": "Off", "1": "Cool", …}`, 1-16 entries |
| `default` | string | `"--"` | shown for stale and for any value not in `values` |

**How it works.** A small per-tick routine (one per op; a later revision may fold several labels
into one) writes the cell:
- an index 0..n-1 when the masked, shifted value is one of the table's keys;
- n otherwise, stale included.

The label is a wtype-6 switch with n+1 arms, matched on 0..n, the last arm drawing `default`.
Mapping in the routine, rather than matching raw values, means the switch never meets a value its
match table lacks. That case is not proven from code, so the design does not depend on it.

**Error codes:** `BAD_PARAMS`, `BAD_REF`, `NO_SUCH_REGISTER`, `NO_DISPLAY_CELL`, `NO_COLOUR`,
`LABEL_REFUSED` (an arm or switch could not be built), `OFF_CANVAS`, `DETOUR_REFUSED`.

**Verify:**
- The switch and its arm texts.
- The decoded routine.
- An emulated tick over every key, one non-key value and stale.

### 4. `add_card_readouts` gains `parent`

`parent` (default 829) names the object the readout object attaches to. `cardreadouts.check_frame`
stays the precondition when `parent` is 829. For any other parent, the precondition is "carries a
wtype-5 attach template" (what `insert.insert_object` needs). The coordinate defaults stay 829's.
A project that omits `parent` compiles byte-identically.

### 5. Re-pointing extensions

**`pin_filter_instance` gains `from_instance`** (int|null, default null).
- **Null:** today's behaviour, which only narrows an entry whose `+4` is `0xFF`.
- **Given:** the entry's current `+4` must equal it (asserted), and is rewritten to `instance`. The
  extraction mode and `+5` are untouched, as today.

Use: vars 9, 13 and 14 from instance 0 to the new device's instance. Every stock thermostat readout
and GuiCode block that reads them follows.

**`set_binding_bytes`** (new, same-size edit):

| parameter | type | meaning |
|---|---|---|
| `group` | int | the group whose bindings are edited |
| `dgn` | int | asserted on every binding edited |
| `bindings` | `"all"`\|array of int | which of the group's bindings, by position |
| `set` | object | `{"<byte 0-7>": {"from": v, "to": w}}` on the wire payload |

Every `from` is asserted before the edit, so a project written against one build fails loudly on
another. The edit goes through `binding_raw`'s word order, never raw offsets. The op updates the
capability ledger the way `remap_channel` does when a byte it changes is the instance of a G12
channel DGN.

Use: the stock setpoint arrows (groups 130/131, byte 0, 0 → N) keep their GuiCode-driven variants
and their step codes.

**Error codes:** `BAD_PARAMS`; `EXPECT_MISMATCH` (a DGN or a `from` byte differs, with the
binding's current bytes in the message); `NO_SUCH_GROUP`.

## Presets and byte identity

- **`add_aes_batt_heat`** keeps its parameters and defaults and calls `add_rvc_button`'s core with
  `parent=829`, `append` placement, the tap-plus-hold gesture and its payloads on `0x0EF70`.
- **`add_aes_bms_button_status`** calls `add_button_status`'s core with one entry: the heater var,
  `mask`, compare `bit`, and the cell allocated the way it is today (tier-3).
- **`add_aes_batt_heat_status`** and **`add_aes_low_volts_trigger`** stay specific. Their
  behaviour belongs to those controls: a four-arm heater/contactor state, and the Low Volts
  config-selector bridge.
- **`swap_aes_electrical`**, **`aes_card_layout`** and **`add_alt_reboot`** are page edits by
  nature and stay.

A preset's output is defined to be its pre-refactor output. Where the generic path would choose
differently (a cell tier, a group pool, record order), the preset passes the old choice explicitly.
The generic default never bends to match it.

**Proof of identity:**
- Before the refactor, a golden table records the sha256 of every pinned recipe's output:
  fireflymcf's test recipes, the research repository's `tests/test_recipe_*.py` and `tests/engine/`
  builds, and each downstream project's pin test.
- After it, every one must match.
- The refactor is one commit per op, and each commit runs the whole table.

## Invariants the ops gate

- **Groups.** Hold and toggle controls only on `hold_safe_groups` (never 68/69, never
  GuiCode-driven: 130-146 among them on 13.6). An op that cannot find enough refuses before
  writing anything.
- **Registrations.** `append_filter` already refuses past the cap. These ops add no subscription
  themselves.
- **Touch and canvas.** No new touch region meets an existing one on any page reaching the parent,
  and nothing leaves 800×480.
- **Palette.** Every colour resolves through `theme.resolve_fill_index`; none is invented.
- **Hook chain.** Each routine re-reads `hook_state` from the image it is built on and chains
  ahead of earlier routines. The detour bytes are asserted.
- **Live redraw.** The switch is enrolled, and each arm carries the whole control.

## Bench and coach checks (what makes each op verified)

A CAN tool on the bench injects status and captures every frame the panel sends.

| op | check | evidence |
|---|---|---|
| `add_rvc_button` tap / hold | each button sends exactly its frame(s) from 9F at priority 6, once per press | candump |
| `add_rvc_button` toggle + `add_button_status` | injected status off → tap sends `on_payload`; status on → tap sends `off_payload`; the light follows the status, not the tap | candump + photo |
| `add_button_status` `eq`/`ne`/`ge`/`bit` | every compare, plus stale (no frames for the refresh window) and `0xFF` (not available) reading off | photo per state |
| `add_enum_label` | every value, a non-key value and stale; no residue between a long word and a short one | photo per state |
| `pin_filter_instance from_instance` | the readouts follow instance N's frames and ignore instance 0's | injected pair |
| `set_binding_bytes` | the arrows send instance N, both variants (°F and °C) | candump |

The downstream project records these on its coach. fireflymcf's `hardware_verified` flips per op
when the evidence is in, and the evidence goes into `docs/HARDWARE-VERIFIED.md`.

## Build order

1. Golden identity table for every pinned recipe (no behaviour change).
2. `pin_filter_instance from_instance`, `set_binding_bytes`. These are same-size edits and are
   independently useful.
3. `add_card_readouts parent`.
4. `add_enum_label`.
5. `add_rvc_button` tap/hold, then the `add_aes_batt_heat` preset (golden table must hold).
6. `add_button_status`, then the `add_aes_bms_button_status` preset (golden table must hold).
7. The toggle form of `add_rvc_button`, with the `TOGGLE_UNDRIVEN` gate and `add_button_status`'s
   `setVariant`.
8. `RECIPE-OPS.md` regenerated, plus the CHANGE-CATALOG and CONTROL-BEHAVIOR entries for the
   toggle form.

Each step is its own commit with its tests; the suite and the golden table run at every step.

## Open points for the owner

1. **Which instance the new device uses.** Stock already carries entries and groups for thermostat
   instances 1-6 (above).
   - Instance 2 works: its stock entries are write-only, and its groups are only reachable from
     controls the page does not draw.
   - The real question is whether the G12 answers instance N. It may be configured for more zones
     than the coach has. Bench check: send `1FEF9` instance N and watch for a `1FFE2` instance N
     from the G12's address.
   - An instance the stock image does not carry (7 and up) avoids the question entirely.
2. **The setpoint arrows by `set_binding_bytes`.** For N = 2 there is an alternative: retarget the
   arrows' touch ids to groups 134/135, which already send instance 2 with the same step codes.
   That needs no binding edit, but ties the page to N = 2. This design recommends
   `set_binding_bytes`.
3. **Mode and fan status sources.** Var 13 (re-pinned to N) is the existing THERMOSTAT_STATUS_1
   mode entry. The builder decodes its extraction before reusing it for Cool/Auto/Dry/Fan/Power
   and the Home mode label. Fan speed (byte 2) needs a new `append_filter` entry on `1FFE2`,
   which costs no registration. THERMOSTAT_STATUS_2 (`1FEFA`) and the proprietary status DGN cost
   one registration each.

## Left to the builder

- Internal factoring (where the shared button and switch builders live).
- The routine's exact instruction sequence, within the verified-emitter discipline.
- Error-message wording.
- Test layout: data-free tests where possible, so the public CI exercises them; the corpus-reading
  tests skip without the corpus as today.
