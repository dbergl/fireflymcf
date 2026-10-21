# Generic RV-C Control Ops Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add generic RV-C control ops (`add_rvc_button`, `add_button_status`, `add_enum_label`),
add `parent` to `add_card_readouts`, add two re-pointing extensions (`pin_filter_instance
from_instance`, `set_binding_bytes`), and turn `add_aes_batt_heat` / `add_aes_bms_button_status`
into byte-identical presets.

**Architecture:**
- **Shared builders:** three new modules hold the pieces every op shares:
  - `rvcbutton.py`: button records, gesture groups and bindings, placement;
  - `statusfold.py`: one per-tick routine that lights cells, maps enums and drives variants;
  - `statelights.py`: a parent-generic two-arm and n-arm switch wrap.
- **Ops:** in `recipe_ops.py`, as today; the AES ops call the same cores with their old choices
  passed explicitly.
- **Byte identity:** a golden-hash script proves every existing recipe still compiles to the same
  bytes.

**Tech Stack:** Python 3.10+, pytest (`-n auto`), capstone (`thumb.assert_disassembles_to`), and
the in-repo Thumb interpreter `tests/thumbsim.py`.

**Spec:** `docs/superpowers/specs/2026-10-20-generic-rvc-control-ops-design.md` (approved
2026-10-20). Owner rulings since: the new device uses thermostat **instance 7**; the setpoint
arrows are re-pointed by `set_binding_bytes` (groups 130/131, byte 0, 0 → 7); var 13 is re-pinned
after its extraction is decoded; fan speed comes from a new `append_filter` entry on `1FFE2` byte 2.

## Global Constraints

- **Byte identity:** every existing project compiles byte-identically. The golden table (Task 1)
  is checked at the end of every task that touches a shared builder (Tasks 4, 7, 8, 9, 10, 11).
- **Privacy:** fireflymcf is public. No owner or coach names, no private repository paths, no LAN
  hosts in code, tests, docs or commit messages. Downstream projects are "a downstream project".
- **Private hashes stay private:** golden tables for private recipes live in the private
  repositories, never in fireflymcf.
- **Encoding:** every text open, `read_text` and `write_text` passes `encoding="utf-8"`
  (`tests/test_portability.py` enforces it).
- **Hardware status:** every new op ships `hardware_verified = False`. The presets keep their
  current values and conditions unchanged.
- **Groups:** hold and toggle controls come from `newbutton.hold_safe_groups(data,
  levelcontrols.RESERVED_GROUPS)`, and never 68/69.
- **Emitted code:** every appended routine is checked with `thumb.assert_disassembles_to`, chained
  through `followchannel.hook_state`, and its detour bytes are asserted before writing (the
  `AddAesBmsButtonStatus.apply` pattern).
- **Colours:** every colour resolves through `theme.resolve_fill_index`. A miss raises
  `NO_BODY_COLOUR` (buttons) or `NO_COLOUR` (labels).
- **Default colours:** off `[0x41, 0x51, 0xA3]`, on `[0x00, 0xAE, 0xEF]`.
- **RV-C not-available guard:** `valid_max` 0xFA is the documented value for raw RV-C bytes.
- **Tests:** data-free where possible (they run in public CI). Corpus-reading tests use
  `firefly-13.6/mcf.bin` and skip without it via `tests/conftest.py`.
- **Test file names:** a test module's docstring must not contain the words `tests/conftest.py`
  treats as research tokens (`_RESEARCH_TOKENS`, e.g. "corpus"), or CI skips it.
- **Commits:** each commit ends with:
  - `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  - `Claude-Session: https://claude.ai/code/session_017E2XietdkEfMrBUY7hvtXM`

## Review Focus

1. **A toggle whose driving status op is disabled** (`enabled: false`) must refuse with
   `TOGGLE_UNDRIVEN`, not compile a button that only sends "on". The test goes in Task 11.
2. **The stale sentinel must not pass the mask.** Stale (`0xFFFF` in the slot) must read off
   even when `mask`/`shift` would turn it into a matching value: the stale test happens on the
   raw slot first. For example, mode `eq 15` with mask `0x0F` must stay off. The test goes in
   Task 5.
3. **Two status ops lighting the same button,** or `add_button_status` naming a button that a
   preset status op already wrapped, must refuse (`BAD_PARAMS`) rather than wrap a switch in a
   switch. The test goes in Task 9.
4. **`compile_cached`** (Studio's resume path) must give the same bytes and the same
   `TOGGLE_UNDRIVEN` refusal as `compile_project`. The test goes in Task 11.
5. **`set_binding_bytes` payload positions:** byte N means wire payload byte N (as candump shows
   it), not raw entry offset N. Byte 0 of a stock arrow is raw offset 7. The test goes in Task 3.

---

### Task 1: Golden-output table for byte identity

**Files:**
- Create: `scripts/golden.py`
- Create: `tests/test_golden_script.py`

**Interfaces:**
- Produces: `python scripts/golden.py record TABLE.json STOCK_DIR PROJECT...` and `python
  scripts/golden.py check TABLE.json STOCK_DIR PROJECT...`.
  - Each `PROJECT` is a `.ffproj.json`; its stock image is found under `STOCK_DIR` by
    `base_sha256`.
  - `record` writes `{project path: sha256 of the compiled image}`. Unverified ops do not block
    the hash: it is the result's `data`, whatever `unverified` lists.
  - `check` exits 1 and prints every mismatch.
  - Functions: `compile_sha(project_path: Path, stock_dir: Path) -> str`,
    `main(argv: list[str]) -> int`.

- [ ] **Step 0: Set up the worktree.** Create its own venv (`python3 -m venv .venv &&
  .venv/bin/pip install -e '.[full,test]'`) and run `scripts/link-research.sh`. Never reinstall
  the research repository's shared `.venv`: other sessions use it.
- [ ] **Step 1: Write the failing tests** (data-free: a fake project over a fake stock is enough,
  since an empty op list compiles to the stock image with the build name stamped).
  - `test_record_then_check_passes`: on a tmp stock and project with `ops: []`, `record` then
    `check` returns 0.
  - `test_check_reports_mismatch`: edit the table's hash, and `check` returns 1 and prints the
    project path.

  If a fake stock cannot pass `compile_project`'s structural gates, mark both tests to need
  `firefly-13.6/mcf.bin` instead.
- [ ] **Step 2:** `pytest tests/test_golden_script.py -v`. Expected: FAIL (no module).
- [ ] **Step 3: Implement `scripts/golden.py`** with `recipe.Project.from_json` and
  `recipe.compile_project`. `utf-8` everywhere.
- [ ] **Step 4:** the tests pass.
- [ ] **Step 5: Record the baseline tables** (before any other task), each private table outside
  fireflymcf:

```bash
.venv/bin/python scripts/golden.py record ../firefly-firmware/build/golden-generic-rvc.json ../firefly-firmware ../firefly-firmware/builds/*.ffproj.json
```

  The downstream project's session records its own table for its `builds/*.ffproj.json` and adds
  its v54 byte-pin test (the owner approved this). **Do not start Task 4 until the downstream
  session confirms its table is recorded.**
- [ ] **Step 6: Commit** `scripts/golden.py`, the test, and a README "Byte identity" line saying
  how to run it.

### Task 2: `pin_filter_instance` `from_instance`

**Files:**
- Modify: `src/fireflymcf/statusbind.py` (`pin_filter_instance`, line ~328)
- Modify: `src/fireflymcf/recipe_ops.py` (`PinFilterInstance`, line ~3781)
- Test: `tests/test_pin_filter_instance.py` (create; corpus)

**Interfaces:**
- Produces: `statusbind.pin_filter_instance(data, var, instance, *, expect_dgn=None,
  expect_compare=0x00, from_instance: int | None = None) -> tuple`, and the op param
  `from_instance` (integer|null, default null).

- [ ] **Step 1: Failing tests** on stock 13.6:
  - `test_repin_var13_from_0_to_7`: the op `{var: 13, instance: 7, dgn: 0x1FFE2, compare: 0,
    from_instance: 0}` compiles. `variable_table(out)[13].raw[4] == 7`, and every other byte of
    the entry is unchanged.
  - `test_repin_refuses_wrong_from`: `from_instance: 1` on var 13 gives `FILTER_REFUSED`, and the
    message names the current `0x00`.
  - `test_narrow_path_unchanged`: `from_instance` omitted on var 13 still refuses ("already
    compares").
  - `test_repin_rooms_and_setpoint`: vars 9 (`1FF9C`) and 14 (`1FFE2`) re-pin 0 → 7.
- [ ] **Step 2:** the tests fail.
- [ ] **Step 3: Implement.** When `from_instance` is given, require `old[4] == from_instance`
  (else raise), skip the sentinel refusal, and write `instance`. When it is None, behave exactly
  as before. Docstring: a re-pin moves an entry from one device to another.
- [ ] **Step 4:** the tests pass. `pytest tests -q -n auto -k "filter or statusbind"` is green.
- [ ] **Step 5: Commit.**

### Task 3: `set_binding_bytes`

**Files:**
- Modify: `src/fireflymcf/newbutton.py` (new `set_binding_bytes`, beside `set_binding_dgn`)
- Modify: `src/fireflymcf/recipe_ops.py` (new `SetBindingBytes`, `@op("set_binding_bytes")`)
- Modify: `src/fireflymcf/ops_doc.py` (`OP_NOTES` entry: status unverified, proof
  `newbutton.binding_payload`)
- Test: `tests/test_set_binding_bytes.py` (create; corpus)

**Interfaces:**
- Produces: `newbutton.set_binding_bytes(data: bytes, group: int, *, dgn: int, bindings:
  "str | list[int]", changes: dict[int, tuple[int, int]]) -> tuple[bytes, list[relocate.Edit]]`.
  - `changes` maps wire byte → `(from, to)`.
  - It raises `NewButtonError` on a mismatch.
- Produces the op params `group`, `dgn`, `bindings`, and `set` as specified in the spec.
  - Error codes: `BAD_PARAMS`, `EXPECT_MISMATCH`, `NO_SUCH_GROUP`.
  - `capability_delta`: if the DGN is a G12 channel DGN (`recipe.capabilities` sees it), declare
    the pairs moved, as `RemapChannel` does. Otherwise `(set(), set())`.

- [ ] **Step 1: Failing tests:**
  - `test_arrows_to_instance_7`: on group 130 with `dgn 0x1FEF9`, `bindings "all"`, `{0: {from:
    0, to: 7}}`, then on 131. `binding_wire_frame` of bindings 451-454 gives payloads `07ff ff ff
    ff fc ff ff`, `07ff ff ff ff fa ff ff`, `07…fb…` and `07…f9…`. The CAN ids are unchanged, and
    so are raw bytes 0-3 (DGN, variant, mask).
  - `test_byte_position_is_wire_order` (Review Focus 5): editing wire byte 5 of binding 451 (`0xFC
    → 0xFD`) changes exactly one raw byte, the one `binding_payload` maps to wire 5, and
    `binding_wire_frame` shows `fd` at index 5.
  - `test_refuses_wrong_from`: a wrong `from` gives `EXPECT_MISMATCH`, with the current wire bytes
    in the message.
  - `test_refuses_wrong_dgn`.
  - `test_bindings_subset`: `bindings [0]` edits only the variant-0 binding.
- [ ] **Step 2:** the tests fail.
- [ ] **Step 3: Implement** through `binding_payload` / `binding_raw`'s word order, one `Edit` per
  binding, then `patch.reseal`. `verify` re-decodes each edited binding and asserts every `to`.
- [ ] **Step 4:** the tests pass. `pytest tests/test_ops_doc.py` passes.
- [ ] **Step 5: Commit.**

### Task 4: `add_card_readouts` `parent`

**Files:**
- Modify: `src/fireflymcf/cardreadouts.py` (`attach(data, obj_bytes, parent=ELECTRICAL_AES_BODY,
  at: tuple[int,int] | None = None)`)
- Modify: `src/fireflymcf/recipe_ops.py` (`AddCardReadouts`, line ~6896: the `parent` param,
  int|ref, default 829)
- Test: `tests/test_card_readouts.py` (extend)

**Interfaces:**
- Produces: on parent 829, `attach` does exactly today's path (`check_frame` plus the insertion
  at `ATTACH_AT`). On any other parent it uses `insert.insert_object(data, obj_bytes, parent)`.
  `WRONG_SHAPE` then means "no wtype-5 attach template on `parent`".

- [ ] **Step 1: Failing tests:**
  - `test_parent_default_is_byte_identical`: today's card-readouts project, with `parent` omitted
    and with `parent: 829` given, has the same sha256.
  - `test_parent_other_object`: the readout object attaches under HOME's object 902 (it carries a
    wtype-5 template), and the rows' captions decode on it.
  - `test_parent_without_template_refuses`: `WRONG_SHAPE`.
- [ ] **Step 2:** the tests fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** the tests pass. The golden tables check clean (fireflymcf run plus both private
  tables).
- [ ] **Step 5: Commit.**

### Task 5: `statusfold`: one routine for lights, enums and variants

**Files:**
- Create: `src/fireflymcf/statusfold.py`
- Modify: `tests/thumbsim.py` (support `blx r3`, recording `(r0, r1)` into `sim.calls` when the
  target is the setter)
- Test: `tests/test_statusfold.py` (create; data-free, built at a fixed `code_load_addr` with a
  fake hook state)

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class Light:
    slot_addr: int; mask: int; shift: int
    kind: str               # "bit" | "eq" | "ne" | "ge"
    value: int; valid_max: int | None
    cell_addr: int          # u16 cell: 0 off, 1 on
    variant_group: int | None   # setVariant(group, lit) after the write

@dataclass(frozen=True)
class Enum:
    slot_addr: int; mask: int; shift: int
    keys: tuple[int, ...]   # cell = index of the matching key, len(keys) otherwise
    cell_addr: int

@dataclass(frozen=True)
class FoldPlan:
    items: tuple            # Light | Enum, emitted in order
    hook_at: int; hook_old: bytes; tail_target: int
    setter_addr: int = followchannel.VARIANT_SETTER

def resolve(data: bytes, items) -> FoldPlan          # validates, reads hook_state
def build(plan: FoldPlan, code_load_addr: int) -> tuple[bytes, list]
def detour_edit(plan: FoldPlan, code_load_addr: int) -> tuple[int, bytes, bytes]
class StatusFoldError(ValueError)
```

- **Semantics per item:**
  - Load the halfword slot. If it equals `readout.STALE_SENTINEL`, the result is "off" (Light) or
    `len(keys)` (Enum).
  - Otherwise `v = (slot & mask) >> shift`.
  - **Light:** if `valid_max` is set and `v > valid_max`, the light is off. Otherwise the light
    is lit when `bit`: `v & value != 0`; `eq`: `v == value`; `ne`: `v != value`; `ge`:
    `v >= value`.
  - **Enum:** cell = the index of the first matching key, or `len(keys)`.
  - Store a halfword to the cell. For a Light with `variant_group`, call the setter
    `(variant_group, lit)` the way `build_display_bridge` does (`ldr r3,[pool]; ldr r3,[r3];
    movs r0,#g; movs r1,lit; blx r3`).
- **Contract:** the routine preserves r4-r11, `push {r0-r3,lr}` / `pop`, and tail-branches to
  `tail_target` (the readout/bitcombine contract). Use `bitcombine.build`'s stream, label and pool
  builder; factor it out if it is not already reusable, without changing `bitcombine`'s bytes.
- **`resolve` refuses (`StatusFoldError`):**
  - no items;
  - an odd `slot_addr` or `cell_addr`;
  - a cell or slot outside `theme.SRAM_LOW..HIGH`;
  - mask 0, or shift > 15;
  - `value` > 0xFFFF;
  - a `variant_group` > 0xFF;
  - a duplicate `cell_addr`;
  - more than 16 Enum keys, or duplicate keys.

- [ ] **Step 1: Failing tests** (Sim over each item; the slot and cell are in Sim SRAM):
  - `test_light_eq_over_values`: mode `eq 1`, mask 0x0F. Slot values 0x00, 0x01, 0xF1 (upper
    bits ignored) and 0x03 give cells 0, 1, 1, 0.
  - `test_light_stale_is_off_before_mask` (Review Focus 2): `eq 15`, mask 0x0F, slot 0xFFFF gives
    cell 0.
  - `test_light_ge_valid_max`: `ge 0x78`, `valid_max 0xFA`. Slots 0x50, 0x78, 0xC8, 0xFA and 0xFF
    give 0, 1, 1, 1, 0.
  - `test_light_ne_and_bit`: `ne 0` (0 gives 0, 3 gives 1, stale gives 0) and `bit 0x02`.
  - `test_variant_calls`: two Lights with groups 162 and 168. One tick records `calls ==
    [(162, lit1), (168, lit2)]`, in item order.
  - `test_enum_index`: keys (0, 1, 3, 4, 6). Slots 0, 3, 6, 2 and stale give cells 0, 2, 4, 5, 5.
  - `test_tail_branch_and_registers`: after the run, pc reaches `tail_target`, and r4-r11 equal
    their entry values.
  - `test_resolve_refusals`: one case per refusal listed above.
  - `test_disassembles`: `build` runs `assert_disassembles_to` itself. Assert it returns the
    disassembly, and that a hand-corrupted blob re-checked through `thumb.assert_disassembles_to`
    raises.
- [ ] **Step 2:** the tests fail.
- [ ] **Step 3: Implement.** Keep the `bitcombine` tests green (`pytest
  tests/test_bitcombine.py tests/test_bms_button.py`).
- [ ] **Step 4:** the tests pass.
- [ ] **Step 5: Commit.**

### Task 6: `add_enum_label`

**Files:**
- Create: `src/fireflymcf/enumlabel.py` (the arms, the switch and placement)
- Modify: `src/fireflymcf/recipe_ops.py` (`AddEnumLabel`, `@op("add_enum_label")`)
- Modify: `src/fireflymcf/ops_doc.py`
- Test: `tests/test_enum_label.py` (corpus)

**Interfaces:**
- Consumes:
  - `statusfold.Enum`, `statusfold.resolve/build/detour_edit` (Task 5);
  - `switchbuild.author_switch(data, *, value_slot, arms_base, count, …)` and
    `switchbuild.append_arms(data, arm_objects, match_values)`;
  - `insert.insert_object`;
  - the source resolution in `recipe_ops` used by `AddRegisterReadout` (`{ref}` | `{var}` |
    `{dgn, instance|selector}`). Extend it with `{var}` if it is not there.
- Produces: `enumlabel.build_arm(data, *, text, font, w, h, align, colour_index, bg_index|None)
  -> bytes` (one arm object: optional wtype-33 fill box plus one wtype-11 label with a declared
  box), and `enumlabel.place(data, *, parent, x, y, arms: list[bytes], value_slot) -> tuple[bytes,
  list, int]`.
- Params exactly as specified in the spec. The cell is `followchannel.scan_display_cell`, falling
  back to `readout.allocate_fresh_cells`. Handle: `{object, slot, cell_addr, arms_base, values,
  default}`.

- [ ] **Step 1: Failing tests:**
  - `test_mode_label_on_home`: an `append_filter`-free label on var 13 (mask 0x0F) with `values
    {0:"Off",1:"Cool",3:"Auto",4:"Fan",6:"Dry"}` and `default "--"`, placed on HOME's CLIMATE
    parent. Assert:
    - one switch with 6 arms, enrolled;
    - arm texts in key order, then `--`;
    - match values 0..5;
    - each label has a nonzero declared box (`GUICODE.md` Finding 69).
  - `test_routine_maps_values`: pull the appended blob (found by its pool pair, the slot address
    plus the cell address, as `test_bms_button.py` does). Sim it over 0, 3, 6, 2 and stale, giving
    0, 2, 4, 5, 5.
  - `test_background_arm`: with `background` given, each arm starts with a wtype-33 fill of `w×h`.
  - `test_refusals`: `BAD_PARAMS` (an empty `values`, 17 keys, a non-integer key), `NO_COLOUR`,
    and `OFF_CANVAS` (x + w > 800).
  - `test_layout_unchanged_elsewhere`: `assert_layout`'s comparison (`pagewalk`) over HOME shows
    only the new records.
- [ ] **Step 2:** the tests fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** the tests pass. `pytest tests/test_ops_doc.py` passes.
- [ ] **Step 5: Commit.**

### Task 7: `rvcbutton` core and `add_rvc_button` (tap, hold, tap + hold)

**Files:**
- Create: `src/fireflymcf/rvcbutton.py`
- Modify: `src/fireflymcf/aescompact.py`: `build_batt_heat_button` delegates to
  `rvcbutton.button_records`. Its output for every existing caller is byte-identical.
- Modify: `src/fireflymcf/recipe_ops.py` (`AddRvcButton`, `@op("add_rvc_button")`)
- Modify: `src/fireflymcf/ops_doc.py`
- Test: `tests/test_rvc_button.py` (corpus), plus data-free payload/param tests

**Interfaces:**
- Produces:

```python
def button_records(donors: bytes, *, touch_id: int, w: int, h: int, text: str | None,
                   glyph: tuple[int, str] | None, font: int | None,
                   fill_index: int | None, x: int = 0, y: int = 0) -> list[bytes]
    # flow-relative fragments: anchor, box, outline, caption?, glyph?, touch.
    # fill_index None keeps today's static colour; x, y are the anchor offset
    # (build_batt_heat_button passes its own).
def gesture_group(data: bytes, *, form: str, hold_seconds: int | None,
                  binding_start: int, count: int) -> bytes
    # form: "tap" | "hold" | "tap_hold" | "toggle"; returns the 4-byte group entry.
    # tap: prefix 10 00 00 00; tap_hold/hold: group-17 clone, hold nibble = seconds;
    # toggle: tap prefix with byte 1 = 0x80.
def bindings(dgn: int, *, form: str, tap: bytes | None, hold: bytes | None,
             off: bytes | None, on: bytes | None) -> bytes
    # tap mask 0x06 (0x02 for tap_hold); hold mask 0x04; toggle: variant 0 = on,
    # variant 1 = off, both 0x06.
def allocate(data: bytes, *, pool: str, need: int = 1) -> list[int]
    # "free" | "hold_safe"; never RESERVED_GROUPS; raises RvcButtonError.
def place(data: bytes, *, parent: int, x: int, y: int, frags: list[bytes],
          mode: str = "attach") -> tuple[bytes, list, int]
    # "attach": a new object under a clone of parent's first wtype-5 (move_control's paste);
    # "append": records appended to parent's stream (the preset only). Returns the object index.
class RvcButtonError(ValueError)
```

- **Op params, validation and error codes are exactly as specified in the spec.** `AddRvcButton`
  raises `BAD_PARAMS` for `toggle` in this task (until Task 11).
- **The touch overlap and canvas checks** reuse `MoveControl`'s helpers (`TOUCH_OVERLAP`,
  `OFF_CANVAS`) over every page root that reaches `parent` (`pagewalk`).
- **The glyph** is a wtype-0 single-character record. Copy the record shape from
  `cornermark.add_mark`'s mark. `NO_GLYPH` when `glyphs` resolves the char to the placeholder.
- **Handle:** `{object, group, touch_id, binding_start, x, y, w, h, text, glyph, font, colour,
  toggle: None, parent, placement}`.

- [ ] **Step 1: Failing tests:**
  - `test_tap_button_frames`: `parent` 4 (HOME root object, chosen with a free area), `dgn
    0x1FEF9`, `tap "07f6ffffffffffff"`. The group prefix is `10 00 00 00`, and there is one binding
    with mask 0x06 and `binding_wire_frame == (0x19FEF99F, 07f6…)`.
  - `test_tap_hold_frames`: `hold {payload, seconds: 3}`. The hold nibble is 3; tap mask 0x02 and
    hold mask 0x04.
  - `test_hold_only` (the `add_alt_reboot` shape).
  - `test_records_and_layout`: box at `x, y, w, h` after a page walk; caption decodes; touch id ==
    group; nothing else on the page moved.
  - `test_glyph_and_caption`: glyph slot 24 with a char an `install_glyph` op installed earlier in
    the project.
  - `test_refusals`:
    - `BAD_PARAMS`: no gesture, a 7-byte payload, `hold` with the `free` pool;
    - `NO_GLYPH`;
    - `NO_BODY_COLOUR`: an RGB not in the palette;
    - `TOUCH_OVERLAP`: placed over an existing HOME button;
    - `OFF_CANVAS`;
    - `NO_FREE_GROUP`: monkeypatch `allocate` to return [].
  - `test_batt_heat_records_unchanged`: `aescompact.build_batt_heat_button(stock, touch_id=46)`
    returns the same bytes as before the delegation. Pin the sha256 of the joined fragments,
    recorded from `main` before the change.
- [ ] **Step 2:** the tests fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** the tests pass. The full suite is green, and the golden tables check clean.
- [ ] **Step 5: Commit.**

### Task 8: `add_aes_batt_heat` as a preset

**Files:**
- Modify: `src/fireflymcf/recipe_ops.py` (`AddAesBattHeat.apply`, line ~6197)
- Test: `tests/test_batt_heater.py`, `tests/test_bms_button.py` (run, do not change)

**Interfaces:**
- Consumes: `rvcbutton.allocate` (pool as today: `free_group_slots` for `"free"`, and
  `hold_safe_groups(..., RESERVED_GROUPS)` for `"hold_safe"`), `rvcbutton.bindings(form="tap_hold",
  tap=off_wire, hold=enable_wire)`, `rvcbutton.gesture_group` from donor group 17 (this must
  equal today's `optionrow._clone_group_entry(donor.raw, …)`), and `rvcbutton.place(mode="append",
  parent=829)`.
- **The handle keys stay exactly today's,** plus `parent: 829`, `placement: "append"` and
  `toggle: None`, so `add_aes_batt_heat_status`, `add_aes_bms_button_status` and the new
  `add_button_status` can all read it.

- [ ] **Step 1:** Run `pytest tests/test_batt_heater.py tests/test_bms_button.py
  tests/test_batt_heat_status.py -q` and the golden tables. Record that all are green before
  editing.
- [ ] **Step 2: Rewrite `apply` over the core.** `validate`, `verify`, `PARAMS`,
  `hardware_verified` and the error codes are unchanged.
- [ ] **Step 3:** The same tests and golden tables are all green and byte-identical. Also run the
  research repository's suite with this worktree's venv (which has the worktree installed):
  `cd ../../../firefly-firmware && ../fireflymcf/.worktrees/generic-rvc-ops/.venv/bin/python -m
  pytest tests -q -n auto`.
- [ ] **Step 4: Commit.**

### Task 9: `statelights` and `add_button_status`

**Files:**
- Create: `src/fireflymcf/statelights.py`
- Modify: `src/fireflymcf/aescompact.py` (`add_batt_heat_state_switch` delegates to
  `statelights.wrap_button` with its own arms; byte-identical)
- Modify: `src/fireflymcf/recipe_ops.py` (`AddButtonStatus`, `@op("add_button_status")`)
- Modify: `src/fireflymcf/ops_doc.py`
- Test: `tests/test_button_status.py` (corpus)

**Interfaces:**
- Consumes: the Task 5 fold (`Light` items), the Task 7/8 button handles (`object`, `group`, `w`,
  `h`, `text`, `glyph`, `font`, `placement`, `toggle`).
- Produces:

```python
def wrap_button(data: bytes, *, object_index: int, value_slot: int, donors: bytes,
                arms: tuple, off_index: int, on_index: int, w: int, h: int,
                text: str | None, glyph: tuple[int, str] | None = None,
                font: int | None = None, grow: bool = True) -> tuple[bytes, dict]
    # replaces the button's static body records in object_index with ONE enrolled
    # wtype-6 switch over whole-button arms; returns {"arms_base", "switch_offset"}
```

- **The op:**
  - One `statusfold` routine for every entry in `buttons`.
  - One cell per entry: `scan_display_cell`, excluding cells already taken in this op, then
    `allocate_fresh_cells`.
  - One `wrap_button` per entry, with `BMS_BIT_STATE_ARMS` (match 0xFFFF → off arm, 1 → on arm).
  - A `variant_group` for an entry whose button handle has `toggle` set (it is None until Task 11).
- **Each wrapped button's handle** gains `status: op_id`.
- **`BAD_PARAMS`** when a button handle already has `status` (Review Focus 3), when two entries
  name one button, or when `ge` or `ne` has no `valid_max` on a whole-byte or u16 source (the
  source's `LIVE_MODES` 1 or 2).

- [ ] **Step 1: Failing tests** (project: three `add_rvc_button` taps on one page, then one
  `add_button_status`):
  - `test_batch_one_routine`: exactly one new detour chain link (`followchannel.chain_from_hook`
    grows by 1); three switches on three distinct cells; each switch enrolled.
  - `test_arms_draw_whole_button`: each arm has a box `w-2·inset × h-2·inset`, the caption, and
    the glyph when the button had one; off and on fills are 0x4151a3 and 0x00aeef.
  - `test_fold_semantics_in_image`: Sim the appended blob from the image over a var-13 slot value
    sequence (eq 1/3/6/4 entries), and check the cells.
  - `test_double_wrap_refuses` (Review Focus 3): a second `add_button_status` on the same button
    gives `BAD_PARAMS`. So does an `add_button_status` on a button `add_aes_bms_button_status`
    already wrapped.
  - `test_ge_requires_valid_max`.
  - `test_bad_ref`, `test_no_body_colour`.
  - `test_heater_switch_unchanged`: the v32 heater-status compile has the same sha256 before and
    after (golden).
- [ ] **Step 2:** the tests fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** the tests pass. The full suite is green, and the golden tables check clean.
- [ ] **Step 5: Commit.**

### Task 10: `add_aes_bms_button_status` as a preset

**Files:**
- Modify: `src/fireflymcf/recipe_ops.py` (`AddAesBmsButtonStatus.apply`, line ~6994)
- Test: `tests/test_bms_button.py` (run, do not change)

**Interfaces:**
- Consumes: Task 9's core with one entry `{source: heater var, mask, compare: bit 1}`. The cell
  comes from `readout.allocate_fresh_cells` (today's tier-3 choice, passed explicitly) and
  `object_index=829`.
- **The preset's routine bytes must equal today's `bitcombine` routine.** Either the preset keeps
  calling `bitcombine.build` (allowed: the generic op does not have to share its emitter with the
  preset), or `statusfold` reproduces it. **Ruling: keep `bitcombine` for the preset**; byte
  identity outranks sharing.
- The handle gains `status: op_id` on the button it wraps (Review Focus 3).

- [ ] **Step 1:** Golden tables and `tests/test_bms_button.py` are green before editing.
- [ ] **Step 2: Rewrite `apply`** to share `statelights.wrap_button` and the cell allocation path.
- [ ] **Step 3:** Byte-identical: the golden tables (all three) and the research repository's
  suite, as in Task 8 Step 3.
- [ ] **Step 4: Commit.**

### Task 11: The toggle form

**Files:**
- Modify: `src/fireflymcf/rvcbutton.py` (the toggle form in `gesture_group` / `bindings`)
- Modify: `src/fireflymcf/recipe_ops.py` (`AddRvcButton` accepts `toggle`; `AddButtonStatus`
  passes `variant_group`)
- Modify: `src/fireflymcf/recipe.py` (new `_check_toggles_driven(enabled_specs) -> list[RecipeError]`,
  called in **both** `compile_project` and `compile_cached` right after `_build_ops`)
- Modify: `docs/CONTROL-BEHAVIOR.md` (a new subsection after §10: status-driven toggle groups,
  citing `FUN_0021B5E8`, the setter pointer `0x20039908`, and stock's own use of it for groups
  140-146)
- Test: `tests/test_rvc_toggle.py` (corpus), plus data-free tests of `_check_toggles_driven`

**Interfaces:**
- Produces: the toggle handle `toggle: {off_payload, on_payload}`. A toggle's group comes from
  `hold_safe` only.
- **`_check_toggles_driven`:** for every enabled `add_rvc_button` spec with `toggle`, an enabled
  `add_button_status` spec *later in the list* must have an entry whose `button.ref` is that id.
  Otherwise the error is `RecipeError("TOGGLE_UNDRIVEN", …, op_id=<button id>)`. Disabled specs do
  not count (Review Focus 1).

- [ ] **Step 1: Failing tests:**
  - `test_toggle_bindings`: the group's byte 1 is 0x80. There are two bindings: variant 0 sends
    `on_payload` and variant 1 sends `off_payload`, both with mask 0x06. Use the Eco payloads from
    the spec's motivating map: on `07fff7ffffffffff`, off `07fff3ffffffffff`.
  - `test_toggle_variant_driven`: Sim the status fold. When the source reads off, it calls
    `setVariant(group, 0)`; on, `(group, 1)`; stale, `(group, 0)`.
  - `test_undriven_refuses`: a toggle with no status op gives `TOGGLE_UNDRIVEN`.
  - `test_driven_by_disabled_refuses` (Review Focus 1).
  - `test_driven_before_refuses`: a status op *before* the button gives `TOGGLE_UNDRIVEN` (or
    `BAD_REF` from the status op; assert one of the two, documented).
  - `test_cached_matches` (Review Focus 4): `compile_cached` over a fresh cache and over a warm
    cache (one param edited) both match `compile_project`'s bytes, and both refuse the undriven
    case.
  - `test_toggle_free_pool_refuses`: `BAD_PARAMS`.
  - `test_toggle_group_not_guicode_driven`: the allocated group is not in
    `newbutton.guicode_selected_groups(out)`.
- [ ] **Step 2:** the tests fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** the tests pass. The full suite is green, and the golden tables check clean.
- [ ] **Step 5: Commit.**

### Task 12: Docs and the final identity check

**Files:**
- Modify:
  - `src/fireflymcf/ops_doc.py` (every new op's `OP_NOTES`: status "unverified", proof modules,
    the bench check from the spec's table);
  - `docs/RECIPE-OPS.md` and `schema/ffproj.schema.json` (regenerated);
  - `docs/CHANGE-CATALOG.md` (one entry per new op and per extension);
  - `plugin/skills/build-firmware/SKILL.md`: one short section on generic RV-C buttons and status
    lights, saying an RV-C command is a fixed payload from SA 0x9F at priority 6, and that `ge`
    needs `valid_max`.

- [ ] **Step 1:** `python3 -m fireflymcf.ops_doc`. Then `pytest tests/test_ops_doc.py
  tests/test_change_catalog.py -q` passes.
- [ ] **Step 2: Final identity.** All three golden tables check clean. The research repository's
  suite is green against this worktree. The downstream session confirms its pins are green.
- [ ] **Step 3: Privacy scan of the branch diff.** It must print nothing:

```bash
git diff main --name-only | xargs grep -n -i -E 'hank|julie|berglund|myfirmware|/home/|coach2mqtt|pi@' || true
```

- [ ] **Step 4:** Full suite: `.venv/bin/python -m pytest tests -q -n auto` (with research data
  linked) is green, and the data-free subset (no links) is green.
- [ ] **Step 5: Commit.** Hand off with superpowers:finishing-a-development-branch (merge and push
  are the owner's call).

## After this plan (not tasks here)

- **The downstream Climate and Home recipe** uses these ops: instance 7, vars 9/13/14 re-pinned,
  arrows via `set_binding_bytes`, new `append_filter` entries on `1FFE2` byte 2, `1FEFA` and
  P_STAT. The downstream session writes it.
- **Bench and coach checks** per the spec's table. `hardware_verified` flips op by op with the
  evidence in `docs/HARDWARE-VERIFIED.md`.
