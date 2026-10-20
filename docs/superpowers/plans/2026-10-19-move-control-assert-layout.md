# move_control and assert_layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:**
- **`move_control`:** one recipe op moves a control between objects and proves nothing else on
  either page moved.
- **`assert_layout`:** a check-only op asserting page layout.

**Architecture:**
- **`pagewalk.py`:** a new module, built on `layout.Screen.walk` through a new optional trace
  hook. It gives every record on a page a stable key, with its placed box and effective inherited
  state, and diffs two such walks.
- **`assert_layout`:** a same-size op over `pagewalk`.
- **`absrecord.py`:** re-encodes a record as an absolute-positioned record.
- **`move_control`:** a structural op. It composes `remove_records`' removal path, a compensation
  loop over `pagewalk` diffs, `absrecord`, and `insert.insert_object`.

**Tech Stack:** Python 3, pytest (`-n auto`), the fireflymcf recipe engine.

**Spec:** `docs/superpowers/specs/2026-10-19-move-control-assert-layout-design.md`

## Global Constraints

- **Branch.** Work on `move-control` in `../fireflymcf`. Build recipes go on a
  new branch `lv-move-control` in `../firefly-firmware`. Never rebase, merge or
  delete a branch with a worktree attached.
- **Commits** end with:
  - `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  - `Claude-Session: https://claude.ai/code/session_017E2XietdkEfMrBUY7hvtXM`
- **Never modify** `tests/answer_key.py`'s data, the `.bin` corpus, `captures/` or the PDF. Never
  commit a firmware image or the PDF to fireflymcf. fireflymcf carries no personal names, LAN
  hosts, panel serials or home paths.
- **Verification status:**
  - `move_control`: `hardware_verified = False`, class `structural`.
  - `assert_layout`: `hardware_verified = True`, class `same-size`.
  - Both have `capability_delta` `(set(), set())`.
- **`assert_layout`** checks in `apply` (raising `RecipeError`) and returns `data` unchanged.
- **State a later op reads** lives in `ctx.handles[op_id]` (deep-copied into the compile cache).
- **Every committed build stays byte-identical.** Run firefly-firmware's
  `.venv/bin/python -m pytest tests/ -q -n auto` (34 tests) after any change to `layout.py`.
- **Full suite:** run in fireflymcf after `scripts/link-research.sh`, with
  `.venv/bin/python -m pytest tests -q -n auto`. The baseline is 2660 passed, 2 skipped.
- **Docs:** regenerate with `.venv/bin/python -m fireflymcf.ops_doc`. That writes `RECIPE-OPS.md`
  and `schema/ffproj.schema.json`. Add a `CHANGE-CATALOG.md` row per op, or
  `tests/test_change_catalog.py` fails.

## Review Focus

1. **`move_control` with `from == to`.** Expect a refusal (`BAD_PARAMS`), not a remove-then-paste
   onto the shrunk object. Test in Task 4.
2. **A later structural op after `move_control`.** Its `verify()` must still pass: it reads the new
   child by handle index, never by offset. Test in Task 4: `move_control`, then a `remove_records`
   elsewhere.
3. **`unchanged_since` naming an op that is not an `assert_layout` snapshot, or one with a
   different root.** Expect `BAD_PARAMS`, not a comparison of unrelated pages. Test in Task 2.
4. **A destination object that no root reaches.** It would never draw, so expect a refusal
   (`NO_ROOT`), not a silently invisible control. The same applies to the source. Test in Task 4.
5. **A snapshot taken, then the build compiled from the cache.** `compile_cached` resumed past the
   snapshot must still compare correctly. Test in Task 2.

---

### Task 1: pagewalk, keyed page walks and their diff

**Files:**
- Modify: `src/fireflymcf/layout.py`.
  - `Screen.walk` gains `trace: list | None = None` and `path: tuple = ()`, threaded through the
    recursion.
  - When `trace` is not None, append one entry per record (containers included) after its box and
    state are computed.
- Create: `src/fireflymcf/pagewalk.py`
- Test: `tests/test_pagewalk.py`

**Interfaces:**
- Produces:
  - `WalkRec`, a frozen dataclass:

    ```python
    key: tuple[tuple[int, int], ...]   # ((obj, rec_index), ...) from root to the record
    wtype: int; label: str | None
    obj: int; index: int               # the record's own object and index within it
    box: tuple[int, int, int, int]     # x0, y0, x1, y1, as Placed (aligned)
    font: int | None; align: int       # state.font and state.align after this record
    colors: tuple                      # effective colours (layout._apply_colors result)
    value_slot: int | None
    in_regs: tuple; out_regs: tuple
    ```

  - `walk_records(data, root, switch_values=None, slot_values=None) -> list[WalkRec]`.
  - `reaching_roots(data, obj) -> list[int]`: the sorted screen root objects
    (`layout.screen_object_map` values) whose object graph (`insert.object_child_graph`, every
    switch arm included) reaches `obj`.
  - `switch_arm_sets(data, roots) -> list[dict]`: the base `{}` first, then one `switch_values`
    dict per (type-6 switch reached from `roots`, arm value), each with only that switch set.
  - `remap_after_removal(key, obj, removed: set[int]) -> tuple | None`: rewrites the `(obj, i)`
    steps of a key for records removed from `obj`. It returns None if the key passes through a
    removed record.
  - `Delta`, a dataclass: `key`, `wtype`, `label`, `kind` (`"moved"`, `"state"`, `"gone"` or
    `"appeared"`), `before: WalkRec | None`, `after: WalkRec | None`, `dx: int`, `dy: int`.
  - `diff(before, after, remap=None, ignore=frozenset()) -> list[Delta]`.
    - `remap` maps each before-key to its after-key, or to None to drop it.
    - `kind="state"` when only font, align, colours or `value_slot` differ.
    - The list is in before-walk order, with appeared entries last.
  - `describe(deltas) -> str`: one line per delta, `key wtype 'label' kind (dx,dy) before→after`.

- [ ] **Step 1: Write the failing tests** in `tests/test_pagewalk.py`. They need the module
  fixture `lv_hold`: the compiled image of the v52 recipe `builds/mcf-13.6-low-volts-move.ffproj.json` with its last three ops (`lv-off-elec`, `lv-gear`, `lv-aes-settings`) dropped, skipped when
  `builds/` is absent.

```python
def test_walk_has_the_gear_with_its_box(lv_hold):
    recs = pagewalk.walk_records(lv_hold, 5)
    gear = [r for r in recs if r.obj == 1427 and r.wtype == 33]
    assert gear[0].box[:2] == (110, 134)

def test_walk_lists_containers(lv_hold):
    recs = pagewalk.walk_records(lv_hold, 5)
    assert any(r.obj == 1427 and r.wtype == 5 and r.label is None for r in recs)

def test_reaching_roots_of_the_compact_card(lv_hold):
    assert pagewalk.reaching_roots(lv_hold, 1427) == [5, 39]

def test_identical_walks_diff_empty(lv_hold):
    a = pagewalk.walk_records(lv_hold, 5)
    assert pagewalk.diff(a, pagewalk.walk_records(lv_hold, 5)) == []

def test_a_move_element_is_one_moved_delta(lv_hold, compile_on):
    out = compile_on(lv_hold, move_element(1427, {"wtype": 33, "nth": 1}, {"y": 30}))
    d = pagewalk.diff(pagewalk.walk_records(lv_hold, 5), pagewalk.walk_records(out, 5))
    assert d and d[0].kind == "moved" and (d[0].dx, d[0].dy) == (0, 1)

def test_remap_drops_removed_and_shifts_later():
    assert pagewalk.remap_after_removal(((5, 0), (1427, 7)), 1427, {3, 4}) == ((5, 0), (1427, 5))
    assert pagewalk.remap_after_removal(((5, 0), (1427, 3), (285, 0)), 1427, {3}) is None

def test_trace_off_leaves_walk_unchanged(lv_hold):
    a = layout.Screen(lv_hold).walk(5)
    b = layout.Screen(lv_hold); b.walk(5, trace=[])
    assert [p.__dict__ for p in a] == [p.__dict__ for p in b.placed]
```

  - The gear selector, nth 1 above, must match whatever `lv-gear` uses in
    the v52 recipe: wtype 33, nth 1. The y step of +1 is from `y: 29 → 30`.
    Read the current value first and use `expect`.
  - `compile_on(image, *specs)` is a test helper. It applies op specs to a non-stock image by
    instantiating the ops directly, as the engine's apply loop does.
  - If that loop is not callable on its own, compile the full recipe with the extra ops appended.

- [ ] **Step 2: Run the tests and confirm they fail.**
  Run `.venv/bin/python -m pytest tests/test_pagewalk.py -v`.
  Expected: an ImportError (no `pagewalk`).
- [ ] **Step 3: Implement the trace hook in `layout.py` and the `pagewalk` functions.**
  - The trace hook appends after `self._placed_label_box`, using the aligned `c`.
  - Its `path` is `path + ((obj_index, i),)`, where `i` is the record's index in
    `records.object_records`.
- [ ] **Step 4: Run the tests and confirm they pass.** Then run the firefly-firmware suite to
  confirm `layout.py` is unchanged in effect.
  Expected: all pass, 34 in firefly-firmware.
- [ ] **Step 5: Commit** with `git add src/fireflymcf/layout.py src/fireflymcf/pagewalk.py
  tests/test_pagewalk.py` and the message
  `pagewalk: keyed page walks (containers included) and their diff`.

---

### Task 2: assert_layout op

**Files:**
- Modify: `src/fireflymcf/recipe_ops.py`. Add `@op("assert_layout") class AssertLayout(Op)` next
  to `RemoveRecords`.
- Modify: `src/fireflymcf/ops_doc.py`, `docs/CHANGE-CATALOG.md`.
- Regenerate: `docs/RECIPE-OPS.md`, `schema/ffproj.schema.json`.
- Test: `tests/test_assert_layout.py`

**Interfaces:**
- Consumes: `pagewalk.walk_records`, `pagewalk.diff`, `pagewalk.describe` (Task 1).
- Produces:
  - **Op params:**
    - `root` (int or `{ref}`);
    - `switch_values` and `slot_values` (optional dicts, JSON string keys converted to int);
    - exactly one of:
      - `expect`, a list of `{select, x?, y?, w?, h?}`;
      - `snapshot: true`;
      - `unchanged_since`, an op id, with an optional `except: [select]`.
  - **`select`:** `{"key": [[obj, i], ...]}`, or `{"label": str, "wtype"?: int, "nth"?: int}`
    resolved against the walk.
  - **Handle on snapshot:**
    `ctx.handles[id] = {"assert_layout": "snapshot", "root": r, "switch_values": …, "slot_values": …, "walk": [WalkRec as dict]}`.
  - **Error codes:** `BAD_PARAMS`, `NO_MATCH`, `AMBIGUOUS`, `LAYOUT_MISMATCH` (expect form) and
    `LAYOUT_MOVED` (unchanged form). Each message includes `pagewalk.describe` output.

- [ ] **Step 1: Write the failing tests** (`lv_move` is the compiled v52 recipe, `builds/mcf-13.6-low-volts-move.ffproj.json`):

```python
def test_expect_passes_on_the_flashed_gear(...):  # gear at x 55, y 134 in LV-move
    r = run(lv_move, al(root=5, expect=[{"select": {"key": GEAR_KEY}, "x": 55, "y": 134}]))
    assert r.ok and r.data == lv_move

def test_expect_mismatch_names_the_record_and_delta(...):
    r = run(lv_move, al(root=5, expect=[{"select": {"key": GEAR_KEY}, "x": 110}]))
    assert r.errors[0].code == "LAYOUT_MISMATCH" and "-55" in r.errors[0].message

def test_snapshot_then_unchanged_passes(...)          # nothing between → ok, image unchanged
def test_unchanged_since_catches_a_move(...)          # snapshot, move_element gear y+1, check → LAYOUT_MOVED, dy 1
def test_except_allows_the_listed_record(...)         # same, gear in except → ok
def test_appeared_record_is_reported(...)             # snapshot 1425, add_low_volts_control, check → LAYOUT_MOVED "appeared"
@pytest.mark.parametrize  # BAD_PARAMS: no form; two forms; unchanged_since unknown id;
                          # unchanged_since a non-snapshot op; snapshot root != check root
def test_no_match_and_ambiguous(...)                  # label "nope" → NO_MATCH; label "AES" without nth → AMBIGUOUS if >1
def test_snapshot_survives_compile_cached(...)        # compile_cached with cache warmed past the snapshot,
                                                      # then a moved gear → still LAYOUT_MOVED
def test_comparing_nothing_breaks_the_tests(monkeypatch):  # monkeypatch pagewalk.diff → [] ; the
                                                      # catches-a-move case must then pass wrongly: assert it does
```

  - `GEAR_KEY` is read once from `pagewalk.walk_records(lv_move, 5)`, the wtype-33 record of 1427
    whose label-sibling is "AES". Pin it as a literal after the first run.
- [ ] **Step 2: Run the tests and confirm they fail.** Expected: unknown op `assert_layout`.
- [ ] **Step 3: Implement `AssertLayout`, the ops_doc entry and the CHANGE-CATALOG row.** The
  catalogue row is "Check that nothing on a page moved, or that a control sits where you expect",
  with status "Proven (check-only; changes no bytes)". Then regenerate the docs.
- [ ] **Step 4: Run the tests** (`tests/test_assert_layout.py tests/test_change_catalog.py
  tests/test_recipe_ops.py`) and confirm they pass.
- [ ] **Step 5: Commit** with the message `assert_layout: a check-only op over pagewalk`.

---

### Task 3: absrecord, re-encoding a record at an absolute position

**Files:**
- Create: `src/fireflymcf/absrecord.py`
- Test: `tests/test_absrecord.py`

**Interfaces:**
- Consumes: `records.decode_record`, `records.geom_offset`, `records.font_offset`,
  `records.value_slot_offset`, and `pagewalk.WalkRec` (Task 1).
- Produces:
  - `SUPPORTED = frozenset({0, 4, 5, 6, 8, 11, 12, 13, 32, 33})`.
  - `class Unsupported(ValueError)`.
  - `absolute(data, rec: records.WRecord, placed: WalkRec, x: int, y: int, explicit_font: bool, reset_align: bool, value_slot: int | None) -> bytes`.
    - **Coordinates.** The returned record bytes:
      - set base modes `[0,0,1,1]` with x, y absolute and w, h = box width and height
        (pen-relative);
      - clear `in_regs` and `out_regs`;
      - put geom[0] and geom[1] at (x, y);
      - keep geom[4] and geom[5].
    - **Inherited state.**
      - `explicit_font` writes `placed.font` into the record's R38 font.
      - `reset_align` writes `placed.align` as the record's align byte.
      - `value_slot` adds or overwrites R42.
    - **Child index.** wtype 5 and 6 keep `child_index`.
    - **Refusals.** It raises `Unsupported` for a wtype outside `SUPPORTED`. It also raises it when
      a requested field cannot be added to that wtype's header.
  - Header bit layouts come from the authored records in `AddLowVoltsControl`:
    - `_title_record`: `0x4b 0x50 0x0f`, align bits in b3;
    - `_box_record`: b4 `0x20` for R42;
    - `_label_record`;
    - `_attach_record`: `05 50 03`, R42 between the 0xff font byte and the child index.

  That op is not refactored in this plan.

- [ ] **Step 1: Write the failing tests.**
  - For each supported wtype, take a real record from the LV-hold image (1427, 1425, 315, 285).
    Re-encode it at (300, 200), place it in a scratch object appended to a page root via
    `insert.insert_object`, and walk it.
    - It decodes (`records.decode_record` round-trips its size).
    - Its walked box is `(300, 200, 300 + w, 200 + h)`, where w and h are the original walk's.
    - Its font, align and `value_slot` match the requested values.
    - It carries no `in_regs`.
  - A wtype outside `SUPPORTED`, for example wtype 9, raises `Unsupported`.
  - A label re-encoded with `explicit_font=False` after a record that sets font 20 draws in font
    20. This shows why `move_control` sets `explicit_font` on the first text record.
- [ ] **Step 2: Run the tests and confirm they fail.** Expected: an ImportError.
- [ ] **Step 3: Implement `absolute`.**
- [ ] **Step 4: Run the tests and confirm they pass.**
- [ ] **Step 5: Commit** with the message `absrecord: re-encode a record at an absolute position`.

---

### Task 4: move_control op

**Files:**
- Modify: `src/fireflymcf/recipe_ops.py`.
  - Factor `RemoveRecords.apply`'s body after validation into
    `_remove_records(data, ctx, obj, entries, op_id) -> tuple[bytes, list[WRecord]]`. It returns
    the new image and the removed records (before-image). `RemoveRecords` calls it unchanged in
    behaviour.
  - Add `@op("move_control") class MoveControl(Op)`.
- Modify: `ops_doc.py`, `docs/CHANGE-CATALOG.md`, regenerated docs, `docs/CONTROL-BEHAVIOR.md`
  (a short section on moving a control).
- Test: `tests/test_move_control.py`

**Interfaces:**
- Consumes: Task 1 (`walk_records`, `reaching_roots`, `switch_arm_sets`, `remap_after_removal`,
  `diff`, `describe`), Task 3 (`absolute`, `Unsupported`), `insert.insert_object`,
  `gestures.touch_ids`, and `_select_record`.
- Produces:
  - **Op params:**
    - `from` and `to` (int or `{ref}`);
    - `records` (`remove_records`' entry form);
    - `at: {x, y}`;
    - `source_root` and `dest_root` (optional int; default `reaching_roots`);
    - `switch_values` and `slot_values` (optional; restrict the arm sets to this one).
  - **Handle:** `{"from", "to", "object": child_index, "touch_ids": [...], "at": [x, y], "moved": n}`.
    `resolve_target({ref})` yields `object`.
  - **Error codes:** `BAD_PARAMS` (including `from == to`), `NO_SUCH_RECORD`, `EXPECT_MISMATCH`,
    `POINTER_INSIDE`, `RELOCATE_REFUSED`, `NO_ROOT`, `TOUCH_OVERLAP`, `OFF_CANVAS`,
    `UNSUPPORTED_RECORD`, `REGISTER_DEPENDENCY`, `CANNOT_COMPENSATE`, `STATE_CHANGED`,
    `ARM_DISAGREES`, `LAYOUT_MOVED`, `INSERT_REFUSED`.

**`apply` order** (spec, "move_control" steps 1-6):

1. **Measure before.** Compute the roots and arm sets. Walk before for every
   (root, arm) pair on both sides.
2. **Pre-checks**, all before any edit:
   - `NO_ROOT`;
   - `UNSUPPORTED_RECORD` (`absrecord.SUPPORTED`);
   - `REGISTER_DEPENDENCY`: a moved record's `out_regs` is read in a later surviving record's
     `in_regs`, in any walk;
   - `TOUCH_OVERLAP`: the translated wtype-12 boxes intersect a destination wtype-12 box;
   - `OFF_CANVAS`: outside 0..800 × 0..480.
3. **Remove.** Call `_remove_records`.
4. **Compensate** with this loop:

   ```
   for _ in range(len(surviving)):
       deltas = [diff(b, walk(after, root, arm), remap) for each (root, arm)]
       state = any kind=="state" → STATE_CHANGED
       first moved per walk; if their (dx,dy) differ → ARM_DISAGREES
       if none moved: break
       target = record of `from` placing it (itself, else the nearest attach of `from` on its key)
       no target, or no x/y geom field for a non-zero component → CANNOT_COMPENSATE
       write geom[0] -= dx, geom[1] -= dy (same-size field write, as move_element)
   else: CANNOT_COMPENSATE
   ```

5. **Paste.**
   - Build the object bytes: a count byte plus the moved records, each passed through
     `absolute(...)` at `at + (its before-box origin − the bounding box origin)`.
   - Use the before-walk on the first root and base arm.
   - Set `explicit_font=True` on the first text record and `reset_align=True` on the first
     align-writing type.
   - `value_slot` is the source's effective `value_slot` on the wrapping attach.
   - Call `insert.insert_object(data, obj_bytes, to)`. A refusal there is `INSERT_REFUSED`.
6. **Measure after and assert.**
   - Re-walk every (root, arm) pair.
   - On the source, compare with the step-4 remap; on the destination, compare with an empty
     remap.
   - Allow only the moved records (appeared under the new child, at their translated boxes, with
     matching font, align, colours and `value_slot`).
   - Anything else is `LAYOUT_MOVED` with `describe(...)`.

**`verify`:**
- The touch ids are gone from `from` and present on `handle.object`.
- `handle.object` is a child of `to`.
- The manifest line reads
  `move_control: N record(s) from object F to object T at (x, y); source compensated by K edit(s); UNVERIFIED on a coach`.

- [ ] **Step 1: Write the failing tests** (`lv_hold` fixture as in Task 1):

```python
LV = [{"selector": {"wtype": 5, "nth": 1}, "child": 285},
      {"selector": {"wtype": 12, "nth": 1}, "touch_id": 129}]   # lv-off-elec's entries

def mc(**kw): return spec("mv", "move_control", {"from": 1427, "to": 1425,
                          "records": LV, "at": {"x": 618, "y": 89}, **kw})

def test_moves_low_volts_and_restores_the_gear(lv_hold):
    r = run(lv_hold, mc())
    assert r.ok, r.errors
    for root in (5, 39):
        gear = box_of(r.data, root, obj=1427, wtype=33)
        assert gear[:2] == (110, 134)
    assert 129 not in gestures.touch_ids(r.data, 1427)
    new = r.handles["mv"]["object"]
    assert 129 in gestures.touch_ids(r.data, new)
    assert lv_box(r.data, AES_SETTINGS_ROOT)[:2] == (618, 89)
    assert r.unverified == ["move_control"]   # or the engine's form of the unverified list

def test_source_page_otherwise_unchanged(lv_hold)        # diff(before, after, remap) == [] on 5 and 39
def test_destination_otherwise_unchanged(lv_hold)        # only appeared entries, all under `new`
def test_moved_face_repaints_from_cell_45(lv_hold)       # the attach carries value_slot 45 (OBJECT-HIERARCHY 1a)
def test_without_compensation_it_refuses(lv_hold, monkeypatch):
    # stub the compensation write to a no-op → LAYOUT_MOVED naming the gear, dy -29
def test_from_equals_to_is_bad_params(lv_hold)
def test_later_structural_op_still_verifies(lv_hold)     # mc() then remove_records on 315 → ok
def test_unreachable_destination_is_no_root(lv_hold)     # `to` = a fresh unattached object → NO_ROOT
@pytest.mark.parametrize("change, code", [
    ({"at": {"x": 790, "y": 89}}, "OFF_CANVAS"),
    ({"at": <over an existing 1425 touch box>}, "TOUCH_OVERLAP"),
    ({"records": [{"selector": {"wtype": 9, "nth": 0}, "child": ...}]}, "UNSUPPORTED_RECORD"),
    ({"records": [<the 1427 records with child 286>]}, "EXPECT_MISMATCH"),
])
def test_register_dependency(lv_hold)   # synthetic: move a record with out_regs read later (find one
                                        # with pagewalk on 1425; if none on these pages, build one
                                        # with move_element-free byte pokes on a scratch copy)
def test_state_changed(lv_hold)         # synthetic: a removed record that sets the font a later
                                        # survivor inherits (scratch object built for the test)
def test_cannot_compensate(lv_hold)     # synthetic: the shifted record has no y field (touch region in a card)
```

  Read the TOUCH_OVERLAP coordinates from `pagewalk.walk_records(lv_hold, 31)` (root 31's
  wtype-12 boxes) and pin them.
- [ ] **Step 2: Run the tests and confirm they fail.** Expected: unknown op `move_control`.
- [ ] **Step 3: Factor out `_remove_records`.** Confirm `tests/test_recipe_ops.py -k
  remove_records` and the firefly-firmware suite still pass.
- [ ] **Step 4: Implement `MoveControl`, the ops_doc entry, the CHANGE-CATALOG row and the
  CONTROL-BEHAVIOR section.**
  - The CHANGE-CATALOG row is "Move a control to another page", with status "Unverified (built
    from coach-proven halves)".
  - Then regenerate the docs.
- [ ] **Step 5: Run the tests and confirm they pass.** Run `tests/test_move_control.py`,
  `test_recipe_ops.py` and `test_change_catalog.py`, then the full suite.
  Expected: everything passes, 2660 + new tests.
- [ ] **Step 6: Commit** with the message
  `move_control: move a control between objects, compensating the source (unverified)`.

---

### Task 5: The proof build (firefly-firmware)

**Files** (in `../firefly-firmware`, branch `lv-move-control`):
- Create: `builds/<owner>-lv-mc.ffproj.json`. It is LV-hold's ops (minus `hdr`), then:
  - `mv`: `move_control` with `mc()`'s params;
  - `lv-gear-centre`: `move_element` on 1427 with `{wtype: 33, nth: 1}`, `set {x: 55}`,
    `expect {x: 110}`;
  - `al`: `assert_layout` on root 5, with `expect` gear `x 55, y 134`;
  - `hdr`.

  Its build name is given at dispatch (the private repo's naming).
- Create: `builds/mcf-<owner>-lv-mc.bin` (+ `.json` sidecar), compiled with `--allow-unverified`.
- Create: `tests/test_recipe_<owner>_lv_mc.py`
- Modify: `docs/BUILD-LOG.md`, a "built, not flashed" entry.

**Interfaces:**
- Consumes: Tasks 2 and 4. `the existing LV-move pin test`'s `_load` and `_res` pattern.

- [ ] **Step 1: Write the failing tests.**
  - **Composition:** LV-hold's ops, then `[("mv","move_control"),("lv-gear-centre","move_element"),("al","assert_layout")]`,
    then `hdr` last.
  - **Unverified list:** LV-hold's plus `move_control`.
  - **Pages 5, 39 and 31:** `pagewalk.walk_records(mc, root)` equals `walk_records(lv_move,
    root)` key-for-key, box-for-box and state-for-state. There is one exception: the
    `"Triggers:"` title, which LV-move has and this build does not.
    - Keys under the new child differ in object index. Compare those by (wtype, label, box,
      state).
  - **Reproduction:** the committed image reproduces from the recipe (sha256 of the compile ==
    the committed file).
- [ ] **Step 2: Run the tests and confirm they fail.** Expected: a missing recipe file.
- [ ] **Step 3: Write the recipe and compile it.** Use
  `.venv/bin/fireflymcf compile builds/<owner>-lv-mc.ffproj.json firefly-13.6/mcf.bin -o builds/mcf-<owner>-lv-mc.bin --allow-unverified`,
  then run `fireflymcf verify` with `--config build/coach-wd.json`.
- [ ] **Step 4: Run the tests.** Run `.venv/bin/python -m pytest tests/ -q -n auto`.
  Expected: 34 + new tests pass.
- [ ] **Step 5: Render roots 5 and 31 for the owner.** Use `fireflymcf render ... --root N
  --config build/coach-wd.json`. They should look like LV-move, without "Triggers:".
- [ ] **Step 6: Commit** the recipe, image, sidecar, test and build-log entry, with the message
  `LV-mc: LV-hold plus move_control, the proof build for a coach flash (not flashed)`.

The flash, and marking `move_control` `hardware_verified`, are the owner's call after this plan.
