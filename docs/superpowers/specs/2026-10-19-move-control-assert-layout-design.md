# move_control and assert_layout: design

Date: 2026-10-19. Status: approved in conversation; to be planned.

## Why

Moving Low Volts from the compact AES ELECTRICAL card to the AES settings page (the LV-move build,
flashed and verified 2026-10-19) took four recipe ops: `remove_records` on object 1427,
`move_element` to restore the "AES ⚙" gear, and `add_low_volts_control`, a Low-Volts-only copy op
on body 1425. The gear fix was needed because the removed wtype-5 attach had carried a +29 y flow
offset, so the gear rose 29 px. The control worked; the side effect was caught only because the
page was rendered by hand.

Two ops close that:

- **`move_control`**: one op moves any control (its draw, touch and box records) from one object
  to another. It puts back anything on the source page that the removal moved, and asserts that
  nothing else moved on either page.
- **`assert_layout`**: a check-only op a recipe can put anywhere to assert where things sit on a
  page, or that nothing on a page moved since an earlier point in the recipe.

There is no specific next move in mind (owner, 2026-10-19). Build them general, and prove them by
reproducing LV-move.

## Goals

- `move_control` handles any control whose records are among the supported record types (below),
  between any two objects, without the author knowing which records carry flow offsets.
- Every record on the source and destination pages that is not part of the moved control sits
  where it sat before the op. The op asserts this itself, using the layout replay.
- The moved control draws at the absolute position the author gives, with the same font, colours,
  alignment and `value_slot` it had on the source page, so it repaints live (the live-redraw
  invariant: fillA index, box `value_slot` and caption `value_slot` are one cell).
- `assert_layout` turns "diff the render" into a recipe step that fails the compile.

## Non-goals

- No new groups, bindings or DGNs. Touch ids map to groups globally (`FUN_00256B84`), so a moved
  touch region runs the same group from the new page. `capability_delta` is `(set(), set())`.
- Not a copy op (that is `copy_control`, later; it shares most of this code). The source loses the
  control.
- Not retiring `remove_control`. That reuses this op's compensation step later.
- No pixel-level render diff. The check is on the layout replay's placed boxes
  (`layout.Placed`: x0, y0, x1, y1 per record), not on rendered pixels.

## Anti-goals

- A move that silently shifts any other record, on either page, in the replay. That is the failure
  this exists to prevent.
- Fitting a fix: compensation must restore each moved record's position exactly, from its geometry
  fields, or refuse. It must never nudge a record until it looks right.
- A moved control that draws but does not repaint on a status change (the pin-15 v7 defect).

## Constraints

- **A control is three records**: draw (usually a wtype-5 attach to a byte-shared child, or a
  switch carrying `value_slot`), touch region (wtype-12, trailing touch id), and box. They move
  together.
- **Layout is flow-relative.** Each record lays out from the frame the previous drawn record left
  (`layout.py`, `FUN_00257BFC`). Font, colours, alignment and `value_slot` are persistent state a
  record can inherit from its predecessors or its wrapping container
  (`layout._placed_label_box`, the persistent-align trap).
- **Removal** goes through `relocate.replace_object_records`, as `remove_records` does, with the
  same `POINTER_INSIDE` refusal for a GuiCode or LCD literal between the first removed record and
  the object's end.
- **Paste** goes through `insert.insert_object` (a new child object with a wtype-5 attach on the
  destination object), as `add_low_volts_control` does. Child objects the control attaches are
  byte-shared, never cloned.
- **Visible text** (wtype-11/13) must declare a box (`GUICODE.md` Findings 69, 70).
  - x, y are absolute and w, h are pen-relative: base modes `[0,0,1,1]`, header `b1 = 0x50`. This
    is the coach-confirmed encoding.
  - `[0,0,0,0]` drew nothing on the coach (the 2026-08-25 bug in `add_low_volts_control`).
- **`hardware_verified = False`** for `move_control` until a coach has run it.
  - The remove and paste halves both reuse coach-proven mechanisms.
  - The new parts are the generic re-encoding of records as absolute and the automatic
    compensation, and neither has run on a coach.
- `assert_layout` changes no bytes, so `hardware_verified = True` is honest: there is nothing to
  run on a coach.
- Ops run against an image; handles are snapshotted into the compile cache
  (`recipe._snapshot`), so state one op leaves for a later one must live in `ctx.handles`.
- `verify()` runs at the end, against the final image. `assert_layout` checks at its own position
  in the recipe, so its check belongs in `apply` (raise `RecipeError`), not `verify`.

## How it works (decided)

### Measuring a page

Both ops measure a page the same way: `layout` walks a root object and gives a list of
`Placed`. Each placed item needs a key that survives relocation, because `record_offset` changes
when an object grows or shrinks. The key is the record's position in the walk:

- the chain of (object index, record index within the object) from the root down to the record,
  and
- its wtype and label, which the check reports in errors.

Removing records changes record indices in one object only. The comparison maps old indices to
new ones for that object, by dropping the removed indices.

Keys include the attach and switch records (wtype 4/5/6) on the path, not only drawn
records, because compensation edits attaches (below) and `Placed` lists only leaves.

A walk depends on switch values. Both ops take optional `switch_values` and `slot_values` (passed
to `layout.walk`); the default is the walk's base arm. **Builder call:** `move_control` measures
more than one arm. It walks the base set, then each type-6 switch reached on either page with that
switch's arms one at a time (every other switch at base). The before/after comparison must hold on
every one of those walks, and compensation must find the same (dx, dy) on each, or it refuses
`ARM_DISAGREES`. `assert_layout` checks only the set it is given; a page that draws differently per
mode needs one `assert_layout` per value set.

### move_control

Params:

- `from`: object index or `{ref}` holding the control.
- `records`: entries as in `remove_records` (`{selector, child?, touch_id?}`), each with an
  identity assertion.
- `to`: destination object index or `{ref}`.
- `at`: `{x, y}`, the absolute position of the control's top-left on the destination page. This is
  the bounding box of the moved records' placed boxes.
- `source_root`, `dest_root`: the page roots to measure. Default: every root whose walk
  reaches the object (`layout.screen_object_map` plus `insert.object_child_graph`). **Builder
  call:** all reaching roots are measured and every one must pass, so `AMBIGUOUS_ROOT` is dropped.
  1427 is reached by roots 5 and 39, the two ELECTRICAL roots; both are checked. Giving a root
  limits the check to it.
- `switch_values` (optional).

Steps, all in `apply`:

1. **Measure before.**
   - Walk the source root and the destination root.
   - Record each moved record's placed box and the effective inherited state when it was placed:
     font, align, colours, `value_slot`.
2. **Check the destination.**
   - Refuse `TOUCH_OVERLAP` if the moved touch region, translated to `at`, overlaps any existing
     wtype-12 hit box on the destination page.
   - Refuse `OFF_CANVAS` if it falls outside 800 x 480.
3. **Remove.** As `remove_records` does (same identity checks, `POINTER_INSIDE`, relocation).
4. **Compensate the source.**
   - Re-walk the source root and compare every surviving record to its before-box.
   - Take the first record in walk order whose box moved by (dx, dy), and the record of the
     `from` object that places it: itself, or the attach in `from` that the moved record hangs
     below.
     - **Builder call:** compensation edits records of the `from` object only, attaches
       included. A shift first seen inside a child object that is not `from` (a shared child,
       which other pages also draw) is corrected on the attach in `from` that reaches it, and
       never inside the child. If no record of `from` places it, refuse `CANNOT_COMPENSATE`.
     - If that record carries the needed geometry fields, subtract (dx, dy) from them and re-walk.
       The records after it usually come back with it.
     - If it lacks the fields, refuse `CANNOT_COMPENSATE`, naming the record and the delta.
   - **Builder call:** "unchanged" includes inherited state. A surviving record whose effective
     font, align, colours or `value_slot` differs after the removal (because a removed record or
     its child set what it inherited) is refused as `STATE_CHANGED`. It is not compensated: fixing
     geometry would leave the text styled wrong, and writing explicit fields grows records, a
     relocation this op does not do.
   - **Builder call:** a moved record that writes a register (`out_regs`) that a surviving record
     reads is refused as `REGISTER_DEPENDENCY` before anything is removed.
   - Repeat until nothing moved, or refuse after N passes (N = the number of surviving records).
   - This is exactly the gear fix, found automatically.
5. **Paste.**
   - Build a new object holding the moved records, re-encoded with absolute x, y.
   - Each record keeps its offset from the bounding box's top-left, plus `at`.
   - Make every inherited property explicit on the first record that needs it:
     - an align-writing record resets align;
     - fonts are explicit on text records;
     - `value_slot` goes on the wrapping attach, the shape of object 315 rec[8].
   - Attach the object to `to` with an absolute wtype-5 attach.
   - **Deviation (Ruling 15).** The attach is not authored: it is `insert.insert_object`'s byte
     clone of `to`'s first wtype-5 record (`insert._container_template`), at that template's own
     geometry and register reads (in the proof, 1425 record 6: y base mode 3, x read from
     register 1). It is a zero-size container, so it draws nothing; in the proof it sits off
     canvas at (81, 537), and the pasted records are absolute, so its pen does not matter. The proof's walks
     and renders match LV-move. A template carrying a `value_slot` is refused
     `UNSUPPORTED_RECORD`, since the pasted control would inherit it. A moved record with no
     `value_slot` of its own that inherits one from an attach above `from` is refused the same
     way (Ruling 14), rather than having the slot written into it.
6. **Measure after and assert.**
   - **Destination:** every record that was there is unchanged; the moved records sit at their
     before-box, translated by `at`; and their recorded state (font, align, colours,
     `value_slot`) matches.
   - **Source:** every surviving record is unchanged.
   - Any failure is `LAYOUT_MOVED`, listing each record and its delta.

Supported record types for re-encoding (**builder call**): wtype 4 (button, part of the − and +
controls on both proof pages), 5 (attach, including the `value_slot` wrapper of 315 rec[8]), 6
(type-6 switch: its own geometry and `value_slot` are re-encoded; its arm children stay
byte-shared), 11 and 13 (labels), 12 (touch), 32 and 33 (boxes), 0 and 8 (text and line). Any other
wtype is refused as `UNSUPPORTED_RECORD`.

Register reads: the walk tracks `regx`/`regy` itself, so every register read in a moved record
resolves to a number. **Builder call:** the paste flattens each such read to an absolute value
(the record's `in_regs` cleared, as `_title_record` already does for the donor's register-3 read).
There is no "unresolvable register" refusal; register writers are covered by
`REGISTER_DEPENDENCY` above.

`verify()` (on the final image):

- The touch ids are gone from the source and registered on the destination
  (`gestures.touch_ids`).
- The moved records' boxes are where step 6 found them. A later op can move the page, so this
  check is against the handle and is reported as a manifest line, not re-asserted if a later op
  relocated the page. The planner should settle that point.

Handle:

- `{"from", "to", "object": new_child_index, "moved": [...keys], "at": ...}`.
- `{ref: op_id}` resolves to the new child object, so a later `move_element` can adjust it.

Error codes: `BAD_PARAMS`, `NO_SUCH_RECORD`, `EXPECT_MISMATCH`, `POINTER_INSIDE`,
`RELOCATE_REFUSED`, `TOUCH_OVERLAP`, `OFF_CANVAS`, `UNSUPPORTED_RECORD`, `REGISTER_DEPENDENCY`,
`CANNOT_COMPENSATE`, `STATE_CHANGED`, `ARM_DISAGREES`, `LAYOUT_MOVED`, `INSERT_REFUSED`.

### assert_layout

Params:

- `root`: the page root object, or `{ref}`.
- `switch_values` (optional).
- One of the following forms:
  - `expect`: a list of `{select, x?, y?, w?, h?}`.
    - `select` names a record by path (`{object, record}`) or by `{label, wtype?, nth?}` within
      the walk.
    - Every given field must equal the placed box (x = x0, y = y0, w = x1 - x0, h = y1 - y0).
    - Refuse `NO_MATCH` if the selection names nothing, or `AMBIGUOUS` if it names more than one.
  - `snapshot: true`: record the walk in `ctx.handles[op_id]`, and check nothing.
  - `unchanged_since: <snapshot op id>`, with an optional `except: [select…]`.
    - Every record in the snapshot, other than the excepted ones, must be present and at the same
      box.
    - New records are allowed only if they are listed in `except`. Otherwise report them as
      `LAYOUT_MOVED` ("appeared").

On failure it raises `LAYOUT_MOVED` or `LAYOUT_MISMATCH`, listing each record (path, wtype,
label), expected box, actual box and delta.

Op class: same-size. `hardware_verified = True`. `capability_delta` is empty. The image passes
through unchanged.

## Proof

1. **Reproduce LV-move.** Replace LV-move's `lv-off-elec`, `lv-gear` and `lv-aes-settings` with
   one `move_control`, from 1427 to 1425, with `at` set to where the flashed build draws the
   button.
   - **Builder call:** keep the owner's gear centring as a separate step. The proof recipe is
     `move_control` (1427 → 1425), then `move_element` on the gear with `set {x: 55}`,
     `expect {x: 110}`. LV-move's `lv-gear` restored the 29 px of y and also centred x (−55);
     `move_control` now restores the y itself, so only the centring remains.
   - Compare the walk of every page against the flashed LV-move image (`94fe0f0a…`), using
     `assert_layout`-style keys:
     - the source card (roots 5 and 39) must be identical;
     - the settings page must match, except for the "Triggers:" title, which LV-move's op
       authored and `move_control` does not carry over.
   - Byte equality is not required; layout equality is.
   - If the walks are identical, the build is a candidate for one coach flash. That flash, and only
     that flash, sets `hardware_verified`.
   - **Note:** LV-move's source records are the Low Volts attach and touch on 1427. Its title came
     from the AGS donor, so the move is not a byte-for-byte reproduction. The planner should decide
     whether the proof build also adds a title.
2. **Tests that can fail.**
   - Stub the compensation pass to do nothing, and confirm the LV-move test fails with
     `LAYOUT_MOVED` naming the gear (−29 y; the design review measured its box going from
     (110,134) to (110,105) after the removal alone).
   - Stub `assert_layout` to compare nothing, and confirm its tests break.
3. **Refusal tests:**
   - each error code;
   - a move whose touch region overlaps;
   - a moved register writer with a surviving reader;
   - a removal that changes a survivor's inherited font or align;
   - an unsupported wtype.
4. **Docs:**
   - `ops_doc` entries, `RECIPE-OPS.md`, the schema and `CHANGE-CATALOG.md` rows (status:
     `move_control` unverified, `assert_layout` proven/check-only);
   - the "What can be built" table in `firefly-firmware/CLAUDE.md`, once flashed.

## Left to the builder

- The data structure for walk keys, and the mapping across a shrink.
- How "the root that reaches an object" is computed (`layout.screen_object_map` plus a child
  graph: `insert.object_child_graph`).
- Re-encoding records as absolute, per wtype, factored out of `add_low_volts_control`'s
  `_title_record`, `_box_record`, `_label_record` and `_attach_record`. Refactoring those to share
  it is fine if the private LV-move pin test and every committed build stay byte-identical.
- Whether compensation edits use `move_element`'s field-writing helper or its own.
- The exact text of manifest lines.
