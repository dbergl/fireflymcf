# OBJECT-HIERARCHY.md — how GuiData composes a "button", and how to add/move one

Aimed at one job: **cloning or authoring a clean, independent on-screen button** (box +
caption + touch + on/off indicator) that pulls in no stray shared element. It documents the
object → record → child model, then works the HOME "Water Pump" / "Tank Heater" case end to
end because that case is the trap: cloning the obvious "full button" object drags in a shared
sub-widget.

Confidence labels follow the project convention. `proven-from-code` cites a decoder read
order or a firmware instruction; `proven-from-data` cites the corpus bytes / the boundary
oracle; `inferred` is called out. Everything below is 13.6 unless noted, but the mechanism is
build-general (the tables move per build; `records.py`/`layout.py` locate them structurally).

Companions: `GUICODE.md` Findings 10-17 (record decoder, container recursion, type-6
switches), `MCF-FORMAT.md` §6 (widget anatomy), `records.py`/`layout.py`/`insert.py`.

---

## 1. The model in one paragraph

A **screen object** is `<count u8><record...><pad to 4-byte boundary>`, stored in object
table 1 (`+0x46148` in 13.6) and addressed by **index** (`proven-from-code`, boundary oracle
over all 1421/1076/1200 objects — `tests/test_records.py`). A **button is not one object per
se**; it is a short flat list of records inside one object. Records carry no absolute
back-pointers — every reference between objects is a **u16 index**. An object is drawn by
another object's **container record** naming its index; geometry composes down that nesting via
per-coordinate base-modes.

### The four record roles that make a button

| role | wtype | what it is | key fields |
|---|---|---|---|
| **frame / box** | **33** (`0x21`) | the visible rounded rectangle — the button's body | `geom[0]` = **width** (>0 → a real box; `None`/0 → *no box drawn*, text only); `geom[4]` = border width; `fillA` (mode-7 index) **and** `value_slot` → the on/off indicator fill. Read "and", not "or": see the invariant in §1a |
| **caption** | **11** (`0xb`) | the label text (`\n` = multi-line) | `label`, `font` slot, `h_align`/`v_align` (R49) |
| **touch / command** | **12** (`0xc`) | the hit region — the tap target and command group (render type 12, opcode `0x0C`, `MCF-FORMAT.md` §6) | geometry only; its tail carries the command binding |
| **container** *(optional)* | **5** | draws **another object inline** | `child_index` → object index; `value_slot` → a colour cell |

The box, caption, and touch are **three independent records** (`MCF-FORMAT.md` §6, hardware
probes 1-3): moving one leaves the others behind. A minimal standalone button is exactly
`[wt-33(width>0), wt-11, wt-12]` — e.g. obj **278-281** ("Fresh/Grey/Black Tank Heater",
`build/hierarchy-dig-report.md`), which have a real box (width 135), a `value_slot` on the
box, a caption and a touch, and **no container child**. Those are the cleanest single-button
templates *structurally* — but see §1a before cloning one.

`proven-from-code`: the wtype → role mapping is `records.WRecord.wtype` (`= header byte0 &
0x3f`) and the frame/box case switch `layout._update_frame` (`FUN_00257BFC`, GUICODE Finding
21). `geom[0]`-is-width for wt-33 is `proven-from-data` (obj 989 box width 140 draws; obj 295
box width `None` draws nothing — §3).

### 1a. The live-redraw invariant — read this before choosing a donor

`inferred-from-data`, three hardware round-trips (`MCF-FORMAT.md` §6.0a is the full write-up).

> A button's box `fillA` mode-7 colour index, its box's `value_slot`, and its caption's
> `value_slot` must all name the **same** cell.

The mode-7 colour table and the value-slot table are one table (GUICODE Finding 18), so
"colour index N" and "value_slot N" address one cell. All nine *attached* buttons in 13.6
obey this, and every cell they name is **type 10** (a status cell).

**Objects 278-281 do not obey it, and that is the trap.** They are **unattached templates**,
reachable from no page, and their `value_slot` is a **type-1/type-0 config gate** ("is this
tank heater fitted?") rather than a status cell — so §1's phrase "a `value_slot` on the box"
describes a *field*, not a working indicator. Clone one unchanged and you get a button whose
box repaints in place on a status change while its **caption vanishes** until a full page
render. That is not hypothetical: it was flashed as pin-15 build v7. Setting all three fields
to one status cell (v8) fixed it on the coach.

So 278-281 remain good donors — the shipped `newbutton.py` uses **279** — provided the clone's
box `fillA`, box `value_slot` and caption `value_slot` are all rewritten to the chosen status
cell. Note also that field *presence* is header-gated, so a same-size edit **cannot add** a
`value_slot` to a record that lacks one; a donor must already carry one on **both** the box
and the caption. Water Pump (295) carries neither — it inherits from its wt-5 parent (§3) —
which is why it is a poor donor despite being the obvious one.

---

## 2. How an object is attached into the tree, and how geometry composes

An object is rendered only because a **parent record** names its index. Two record types do
that (`layout.Screen.walk`, replaying `FUN_00258330` / `FUN_00256C54`):

- **wt-5 container — unconditional.** Recurses into `object_table[child_index]` **always**
  (no gate). `FUN_00258330` runs with its save-state flag clear, so the child **shares** the
  parent's flow origin (`+0x36/+0x38`), registers, colour slots and frame — it is laid out
  *relative to the parent's accumulated origin*, not from `(0,0)`. `proven-from-code`
  (`layout.py` lines ~524-553; GUICODE Finding 12).
- **wt-6 / wt-0x2e switch — conditional.** Reads `value_slot` (a variable), scans the match
  table `DAT_00257BF8` (`0x9004562C` in 13.6) over `[child_index, child_index+count)`, and
  renders the **one** candidate whose match value equals the runtime value. Match value
  `0xFFFF` marks an explicit **default** candidate (rendered on no match); no match and no
  default → **renders nothing** (`FUN_00258330(0xFFFF)` returns immediately, `0x00258336`).
  `proven-from-code` (GUICODE Finding 16 + the `count` field; `layout._switch_child`).
  - A candidate reached **only** by a switch's contiguous range (not by any explicit
    `child_index`) is invisible to `insert.object_child_graph` — it will show as
    **parentless** even though the switch renders it (obj 990, §3).

**Geometry composition.** Per coordinate (six per record = three x/y pairs):

```
coord[i] = geom[i]                       # WRecord.geom[i] (static)
         + register_file[in_reg[i]]      # a saved pen, if named
         + base_reference(mode[i])       # container / sibling origin  (GUICODE Finding 12)
```

`base_reference` mode 2/3 reads the previous record's **frame** `+0x3a..+0x40`, which is how a
caption/touch inherits its box's position (they carry `geom[0]=None` and ride the box frame).
So nesting depth **quadrant container → button object → fragment** carries an origin down at
each level. `proven-from-code` (`layout._base_ref` / `_update_frame`; validated by the Climate
page decoding to the photo, `tests/test_layout.py`).

---

## 3. The worked case: HOME "Water Pump" / "Tank Heater" — is the shared child rendered?

**Yes, the shared child 295 IS rendered when 989 is walked — but as a separate adjacent button,
never overlaid on Tank Heater. Obj 989 is deliberately a Water-Pump + Tank-Heater *pair*, not a
single button.** That is the whole reconciliation. Details:

### The objects (13.6, decoded)

- **obj 295** `[wt-33 frame (geom[0]=None → NO box), wt-11 'Water\nPump', wt-12 touch]` — a
  **box-less fragment**: renders **text only** (its frame has no width). A shared template.
- **obj 990** `[wt-5 (child_index=295, value_slot=160)]` — a thin wrapper: renders **just 295**
  (Water Pump text). Its wt-5 record is **byte-identical** to 989's first record
  (`0555004720000000a000ffa0002701`) — same shared child.
- **obj 989** `[wt-5 (child_index=295, value_slot=160), wt-33 frame (geom[0]=140 → a real box,
  fillA mode-7 455), wt-11 'Tank\nHeater', wt-12 touch]` — renders **295 (Water Pump)** *and*
  its own **Tank Heater** box.

### How they are selected — two nested type-6 switches, both on the floorplan (slot 5)

```
HOME (obj 4)
└─ wt-6 switch  geom (435,49)  slot 5 (floorplan)  [911 .. 922)   → tank quadrant by floorplan
   ├─ mv[911]=0  → quadrant 911  (FRESH/GREY/BLACK/LP, no pump/heater)
   ├─ mv[912]=1  → quadrant 912  (FRESH/GREY/LP + the pump/heater switch)   ← floorplan 1
   │   └─ wt-6 switch  geom (56,14)  slot 5  [989 .. 991)
   │       ├─ mv[990]=0       → obj 990 → 295            ("Water Pump only")
   │       └─ mv[989]=0xFFFF  → obj 989 → 295 + own box  ("Water Pump + Tank Heater")  ← default
   └─ … 913-921 = other floorplans
```

`proven-from-data`: match values read from `DAT_00257BF8`; `count` fields from the switch
records (`tests/test_records.py::test_type6_switch_count_is_decoded`).

The inner switch reads the **same** floorplan slot. When quadrant 912 is shown (floorplan 1),
the inner switch scans `[989,991)` for value 1, finds no match (values are `0xFFFF`, `0`), and
falls to the `0xFFFF` **default → 989**. So **whenever the pump/heater slot is populated,
Tank Heater (989) is the default, and 989 always draws its shared Water-Pump child.** Floorplan
0 in that slot instead selects 990 → Water Pump alone.

### Where the two land (absolute, HOME with floorplan 1, `layout.Screen.walk`)

| element | source | absolute box |
|---|---|---|
| 'Water\nPump' | obj **295** (989's child), rel x0 = 0 | **(491,190)–(595,240)** |
| 'Tank\nHeater' | obj **989** own box, rel x0 = **140** | **(631,190)–(735,240)** |

Both on-canvas (800×480), **140 px apart, side by side, non-overlapping**. So "the Tank Heater
button does not show Water Pump" is true — the Water Pump text is a **separate adjacent button**,
not text sitting on the Tank Heater box. The 140 px comes from 989's box `geom[0]=140`, the gap
that leaves room for the shared Water Pump to its left. `proven-from-code` (the replay);
consistent with the coach's HOME.

### Verdict on the crux

The shared child 295 is **rendered, not gated and not hidden**. It is a legitimate shared
template that both the "pump only" (990) and "pump + heater" (989) variants reuse. It is
**benign for the real UI only because 989 is a composite pair** — it is *not* benign if you
clone 989 intending a single independent button, because the clone will faithfully draw the
Water Pump 140 px to the left. See §4.

---

## 4. Recipe: clone a clean, independent button

Weighed against §3, the options are:

- **(a) clone the composite 989 and NULL its child_index.** Works: set the wt-5 record's
  `child_index` u16 to **`0xFFFF`** (the firmware's own no-child sentinel — the recursion
  returns immediately at `0x00258336` and draws nothing; `layout.py`'s guard
  `child_index < len(table)` also skips it). Verified by replay: `walk(989)` then yields only
  `[wt-5 empty, wt-33 box, wt-11 'Tank Heater', wt-12 touch]` — a clean boxed button, its own
  indicator (box `value_slot 455` → colour cell `0x20039b74`) and its own command (wt-12).
  Note the box keeps `geom[0]=140`, so the button sits 140 px right of its placement origin;
  zero that byte too if you want it flush at the origin.
- **(b) subtree-clone 989 + 295, repoint child_index to the cloned 295.** Gives the clone a
  *private* Water-Pump element — i.e. it reproduces the **pair**, still two buttons. **Not** a
  single clean button. Only correct if you actually want a pump/heater pair.
- **(c) clone a genuinely childless single-button donor.** Objects **278-281** are already
  `[wt-33(width) + value_slot, wt-11 caption, wt-12 touch]` with **no container child** — the
  cleanest structure. Nothing to null; clone, rename the caption (same-size, `set_label`),
  repoint the command, and — **required, not optional** — rewrite box `fillA`, box `value_slot`
  and caption `value_slot` to one status cell (§1a).

**Recommendation: prefer (c); use (a) when the donor you have is a composite.** (c) is the
lowest-risk clean button (no stray child possible); (a) is the reliable fallback that turns any
composite "full button" into a single one with a 2-byte edit. This is what shipped: `newbutton.py`
takes path (c) with donor **279**.

### What `newbutton.py` does

*Written as a design sketch, kept as a description: the module was built to this shape,
flashed, and confirmed on the coach 2026-08-17 (`MCF-FORMAT.md` §6.0a). Step 4 is where the
sketch was incomplete — the indicator retarget is three fields, not one.*

1. **Pick the donor.** Prefer a childless single button (`records.object_records` →
   `wtypes == [33, 11, 12]`, `recs[0].geom[0]` truthy). Fall back to a composite full button
   (has a wt-5 with a `child_index`).
2. **Clone** it via `insert.clone_object(data, donor_index)` (exact byte span; decodes
   identically — the boundary oracle already covers it).
3. **If the donor carries a container child** (composite case a), for each wt-5 record in the
   clone whose child you don't want, overwrite its child-index u16 with `0xFFFF`. Locate that
   u16 with **`records.child_index_offset(data, rec)`** (replays the decoder's cursor to R48;
   round-trip-guarded — §5) — do **not** assume `size - 2`, and do **not** hardcode an offset.
4. **Retarget the clone in place** (same-size edits): rename the caption (`patch.set_label`),
   move the box/caption/touch (`patch.set_position`), give it its **own command group** so its
   binding is independent of the donor's, and retarget the indicator. The indicator retarget is
   **three fields, all set to the same status-cell index** — box `fillA` mode-7 index, box
   `value_slot`, caption `value_slot` (§1a). `newbutton.retarget_indicator` writes all three and
   expresses the identity as `INDICATOR_STATUS_SLOT = INDICATOR_COLOUR_INDEX` so they cannot
   drift apart; `records.value_slot_offset` and `newbutton._fillA_mode7_offset` locate the bytes.
   On the command side, a **single command-5 binding** is a plain toggle; a two-binding
   `[toggle, off]` group is a *smart toggle* whose behaviour depends on tracked state and which
   sends only OFF for an instance with no status (`MCF-FORMAT.md` §6.0a).
5. **Attach** with `insert.insert_object(data, clone_bytes, parent_index)` — appends at the top
   index (no existing `child_index` shifts) and wires it in as a **wt-5 child** of the page
   (always-visible), then reseals and runs the relocator gates — **five** of them, the fifth
   being `_verify_guicode_pointers`, added after an inserted build boot-looped the panel
   (`MCF-FORMAT.md` §4.1). For a *conditional* button, attach instead by adding it as a
   **switch candidate** (a match-table entry in a type-6 group) — that path is still not
   automated (open item, §6).

---

## 5. `records.child_index_offset` — the byte-offset helper

`records.child_index_offset(data, rec) -> int | None` returns the **file offset of a
wtype-5/6/0x2e record's `child_index` u16**, or `None` if the record carries none. It is
recovered by **replaying the decoder's cursor**: `decode_record` now records the position at
which it reads R48 (`WRecord.child_index_off`), so the offset is correct no matter which
presence-masked fields precede it — it is *not* the fixed `size - 2` (which merely happens to
be right for the container types because R49-R51 and the label loop are all absent there).
Round-trip-guarded: it re-reads the u16 and raises `ValueError` unless it equals
`rec.child_index`.

Use it to **repoint** a container at a cloned child, or to **null** a child by writing `0xFFFF`
there (the firmware's no-child sentinel). `proven-from-code` (records R48). Tests:
`tests/test_records.py::test_child_index_offset_round_trips_on_containers` (round-trips across
989/990/295/4/912/911/278) and `…_detects_a_wrong_offset` (corrupts the byte and asserts it
raises — the standing "check your tests can fail" rule).

---

## 6. Open questions

- **Conditional attach — built as a new switch, not as a new candidate** (corrected 2026-09-10;
  this bullet said "not automated" because the relocator could not grow the match table).
  `relocate.grow_match_table` grows it now, through `relocate`'s gates, and three shipped features
  use it the same way: grow the table by the arms' match values, append the arms as a contiguous
  band, and author a **new** wtype-6 switch record over that band on the gating slot
  (`cornermark.append_arms`/`build_switch_record`, `aescompact.append_readout_arms` and
  `append_link_arms`, behind `add_corner_mark`, `add_pump_auto_off` and `swap_aes_electrical`).
  So an option- or state-conditional object is a solved shape. What is still unbuilt is (a) a
  **general op** that wraps an arbitrary button in such a switch, and (b) adding a candidate to an
  **existing** switch: its band `[child_base, child_base+count)` is contiguous and the object after
  it is already taken, so a new candidate cannot join it in place. Re-basing the switch onto a
  fresh appended band holding clones of the old arms plus the new one would work in principle
  (`inferred`, unbuilt); §7's in-place rebuild is the tool used so far.
- **The command ↔ status-variable join is not a stored field** (open thread 2): nothing in the
  file says a button's wt-12 command and its indicator's status variable belong together, so a
  cloned button driving a *new* channel still needs the `move_display_list` care documented in
  CLAUDE.md / `MCF-FORMAT.md` §8. *(This bullet used to call the join "positional, by
  construction order". That reading was withdrawn in 2026-08-16 —* `MCF-FORMAT.md` *§5.4 third
  round. The two halves are set independently and simply have to be set consistently, which is
  what `statusbind.bind_pin` does.)*
  *For a remap this is automatic since 2026-09-10: `set_channel`'s default points the
  control's own records at the new pin's cells (`McfPatcher._retarget_indicator`).*
- **`value_slot` on a wt-5 container is a colour cell, not a visibility gate** (`proven-from-
  data`: slots 160/455 → type-10 cells `0x20039eac`/`0x20039b74`, the residuals-dig on/off
  fills). So a wt-5's child recursion is genuinely unconditional; there is no per-container
  "hide" flag to find here (consistent with open thread 4's `E5`/`EC` staying `xfail`).
  *Closed 2026-09-02 (`GUICODE.md` Finding 61): there is no flag; the option's value slot keys
  ordinary type-6 switches whose 0-arm draws nothing. The negative above stands.*

---

## 7. Worked case: replacing a switch-selected object in place, and reusing a stranded root
   (the compact AES Electrical page)

This is a second recipe for
attaching a page-sized amount of content, alongside §4's clean-button recipe, for the case where
the target is not a free child slot but an object *already selected by a switch's contiguous
band* — the situation §2's "switch candidates are a contiguous object band" rule describes.

**The constraint that forces an in-place rebuild.** Object 5 (the Electrical page) carries a
wt-6 switch on slot 7 (AGS/AES-Single mode selector), band `[829, 831)`: match 0 → object
**829** (stock: the all-in-one AES layout, drawn directly, not through a container — content
*is* the switch candidate); match 1 → object **830** (the AGS frame). Because a switch's
candidates are a fixed-position band read off the match table (`GuiData +0x4562C`), **an
appended (high-index) object cannot become a new candidate** without growing that table, which
no insertion tool did then (§6's first open question, generalized). *`relocate.grow_match_table`
now grows it, but that does not change this case: the switch's band is contiguous, so a new
candidate still cannot join an existing band in place (§6).* So replacing object 829's
*content* while keeping the switch valid means replacing it **at index 829** — the same object,
a different record stream, same table entry, same match value.

`relocate.relocate` (§2's insertion path elsewhere in this project) cannot express this when the
new content is *smaller* than the old (a negative length delta), because it is insert-only and
boundary-oracle-guarded for growth. `relocate.replace_object_records(data, obj_index, new_body)`
is the primitive this case needed: it overwrites `obj_index`'s `count`+records span, computes
the signed delta (shrink or grow), runs `relocate._apply_downstream_shift`, reseals, and re-runs
all five relocate gates (`_verified_pointer_tables`, `_deep_coverage_ok`, `_boundary_oracle`,
`_verify_guicode_pointers`, `_verify_no_stranded_lcd_pointers`). `proven-from-code`/tooling-
verified — unit-tested on an identity round-trip and a real shrink before object 829 was ever
touched. It is general beyond this feature: any time a switch (or other position-addressed)
candidate's *content* must change without moving its index, this is the tool.

The old content is not discarded: object 829's stock record stream (52 records, the full AES
setpoint UI) is first cloned verbatim to an appended object (`insert.clone_object` +
`insert.insert_object` — the ordinary top-index append from §4 step 5) *before* 829 is
rewritten, so it survives as a body other pages can point to.

**Reusing a stranded screen root as a destination.** A cloned, appended object has no screen of
its own — `insert.insert_object` only wires a new object in as an always-visible wt-5 child of
an *existing* page (§4 step 5); it does not mint a new top-level screen. Growing the top-level
screen switch (object 1, band `[3, 50)`) to add a genuinely new screen id is the expensive,
structurally awkward path this feature avoids. Instead it reuses one of the **eight screen ids
already mapped to a root object but reachable by no navigation in stock 13.6** — "stranded"
roots: screens 22, 28, 33, 34, 40, 42, 43, 45 → objects 25, 31, 36, 37, 39, 41, 42, 44
(`layout.screen_object_map`). Screen 28 → object 31 (stock: a title-only page, `AUTO GEN START
SETTINGS`, `docs/CLOSED-THREADS.md`) is rebuilt via `replace_object_records` into a settings
*frame* mirroring object 45 (the AGS settings screen's own frame): a cloned header + nav rail,
plus a **wt-5 container** whose `child_index` points at the appended AES body clone. A wt-5's
`child_index` is a free pointer (§2), so — unlike a switch — it *can* target a high, appended
index; that is the whole reason the settings frame is a container, not another switch. Because
screen 28 already maps to object 31, no screen-map / match-table / band edit is needed at all —
the reuse is a pure content replacement of an object nobody could previously reach.

**Reaching the reused root: an ACTION-dispatcher leaf, not a binding group.** The settings
button still needs *something* to route its tap to screen 28. `CONTROL-BEHAVIOR.md`'s
ACTION dispatcher (`FUN_0024ACB2`) resolves a `wtype-12` touch id ≥ the group count via a chain
of baked `movw rX,#<id> / cmp / bne / movs rX,#<screen> / strb.w rX,[r4,#0x20a]` leaves — one
leaf per action id. Stock carries a **working but orphaned** leaf for id `0x7DE` (carried only
by two non-displayed touch records, objects 1052/1053, neither in the display graph —
`GUICODE.md` Finding 53) that targets **screen 14** (`INVERTER / CHARGER SETTINGS`, itself
orphaned and unrelated to this feature — only the dispatcher leaf is reused, not that screen).
The settings button repurposes that leaf rather than appending a new one: a guarded, same-size
2-byte GuiCode edit changes its screen constant
(`movs r3,#0xe` → `movs r3,#0x1c`, i.e. screen 14 → 28) and the compact frame's gear region is
stamped with touch id `0x7DE`. This is native code, not a data edit — `hardware_verified=False`,
flagged in the compile result's `unverified` manifest, coach-check before trust.

**The compact frame's final content — a faithful structural mirror, not a flat rebuild.**
Object 829's replacement content is not authored from scratch: it is a **structural mirror of
the AGS compact frame, object 830**, built by preserving 830's container wrappers rather than
flattening donor geometry to absolute coordinates. This matters because the coordinate model is
**flow-relative** (child geometry is an offset from its container's flow origin, base-mode 1): a
donor's box and its own touch region are only guaranteed to line up if they stay inside the same
container they were cloned from. An earlier revision of this build absolutised the visible parts
of a cloned control while leaving its touch region at donor-relative coordinates, which desynced
the two — the gear capsule rendered in one place and its hit-region sat off-screen. The fix
(shipped, hardware-verified) was to keep the wrapper: clone whole containers verbatim and let the
layout engine re-derive positions from them, the same way 830 itself composes.

Under that strategy, object 829 carries: the House/Chassis voltage gauges (containers pointing at
objects 443/440, mirroring 830[1]-830[4]); the compact cluster, a **clone of object 315** (830's
own slot-37-switch target) retargeted for AES rather than reused directly, because it needs
AES-specific content substitutions (below); and the Inverter block (`wtype-6` switch on slot 113,
band [1420,1422), object 1421) gated exactly as on the AGS side. Solar is omitted — the AES page
never had a solar block and the owner asked to keep the compact page lean.

**AES-specific substitutions inside the cluster clone** (the clone is not byte-identical to 315;
these are the differences):
- **Engine control: the real AES control, object 353, not the AGS GEN control (352) with content
  swapped in.** 315's body container points at 352 (`Auto Gen Start`: touch groups 19/20, status
  `value_slot` 166 → band 530, a numeric gen-hours line on `value_slot` 46). AES has its own
  native control at object 353 (`Auto Engine Start`: touch groups 13/14, status `value_slot` 169
  → band [526,529), Start/Stop indicators on `value_slot` 167/168 sharing indicator objects
  249/251 with 352) — same footprint (220×57) as 352, but no gen-hours line and no center-touch
  region. The cluster's body-container child is repointed to a clone of 353. Because 353 lacks
  352's gen-hours line, a bare repoint left the shorter control's neighbours flowing up into it;
  the fix is an **invisible flow-gated spacer** — an empty `wtype-11` label placed at 352[9]'s
  y-offset — that reproduces the vertical rhythm 352's gen-hours record used to provide. (A
  `wtype-9` record will not substitute: the flow-gate mask `0x600FFDFF` excludes type 9 from
  advancing flow at all, while type 11 is included — an empty type-9 draws nothing *and* moves
  nothing, an empty type-11 draws nothing but still advances the pen.)
- **Settings gear: touch id `0x7DE`, not stock `0x7FE`.** 315's gear region carries stock id
  `0x7FE` (→ screen 46, the AGS settings frame). The AES gear is stamped with the repurposed
  action leaf `0x7DE` (§ above) instead, at the **same donor-relative geometry** as the capsule it
  sits under — the container-preservation strategy is what makes the touch region land under the
  visible capsule without a separate coordinate fix.
- **Enable pill: touch id `0x7E2`, not stock `0x0080` (group 128).** 315's enable pill is a
  binding-group toggle (group 128) that jumps straight to the 3-second-hold Warning screen,
  skipping the "Notice:" interstitial. AES's own enable control is a GuiCode **action**, id
  `0x7E2` (handler `0x0024C72A`, gate `0x20039F19`): disabled → Notice screen 18 / object 21 (its
  OK button is group 128, i.e. the same Warning screen 315 jumps to directly); enabled → the
  disable burst. Stamping the cloned pill's touch id `0x0080`→`0x7E2` (same-size 2-byte edit)
  routes the AES compact page through the correct Notice-then-Warning flow instead of the AGS
  shortcut.
- **Low Volts trigger:** unchanged from the mechanism `docs/DGN-MAP.md` §1 and CLAUDE.md's
  `aeslowvolts` row describe — a verbatim clone of 315's Triggers/Low-Volts recolor box
  (`value_slot 45`, child 285) plus its group-129 touch region, driven live by the appended
  bridge rather than the frozen stock path.

**One accepted behavior, not a bug:** enabling AES from the settings sub-page returns to the
(now compact) Electrical page rather than the full-screen AES body the user was just looking at.
This is stock hard-coded behavior — the enable handler (`0x0024EC54`) writes screen 1
unconditionally before the enable burst, shared with the AGS enable path and the Cancel path — so
fixing it for AES alone would mean touching a handler the AGS path also depends on. The owner
chose to leave it.

**AGS isolation, restated in this section's terms:** the only objects created or modified are
829 (rebuilt in place as the faithful mirror above), the appended AES-body clone (the settings
sub-page body), the cluster clone (an appended object, not 315 itself), and stranded root 31
(rebuilt in place). The switch on object 5, object 830, object 315, and object 45 are never
touched — the AGS render path is byte-identical by construction, not by a golden-test
coincidence. **Hardware-verified on the coach, firefly-13.6, 2026-09-01**:
AGS page unchanged; the AES compact page mirrors it; the gear reaches the settings sub-page; both
voltage gauges are live; the engine control shows the real AES Engine/Start/Stop/Not-Running
content; Low Volts recolors and arms both ways; the enable pill routes through the Notice page.
