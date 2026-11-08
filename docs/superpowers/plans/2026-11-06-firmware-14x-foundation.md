# Firmware 14.x, Plan 1: Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** fireflymcf knows which firmware it is building for, refuses ops on firmware they have not been tested on, records hardware verification per firmware, and runs 14.7 in the emulator, with the ops that already work there enabled on 14.7.

**Architecture:**
- A new `firmware` module identifies an image by the pair (GuiCode section name, LCD Firmware
  sha256). It also gives that image's own section names.
- Every op declares the firmware it supports (`firmwares`) and where a coach has run it
  (`verified_on`).
- `compile` refuses an op outside its list (`UNSUPPORTED_FIRMWARE`) before applying anything,
  and computes "unverified" for the image's firmware.
- The emulator's version check accepts 14.7.

The later plans (template controls, the per-refresh hook, the display-value table, colours,
`set_theme`) each add a locator and then add `"14.7"` to more ops' lists.

**Tech Stack:** Python 3.11+, pytest (`-n auto`), the in-repo emulator (`fireflymcf.emu`).

**Spec:** `docs/superpowers/specs/2026-11-06-firmware-14x-support-design.md`

## Global Constraints

- **Firmware keys and identity pairs, exact:**
  - `"11.23"` = (`Lyra 11v23 GuiCode`, LCD `cbed1028d988f67d1ddb8390a15cd66e28e08d93247539772446c57ea7e57adc`)
  - `"13.6"` = (`Lyra 13v6 GuiCode`, LCD `a55344f9b224f917b5bf16dec752224f31df9d6beb0ef2b944f2c215edc49b87`)
  - `"14.7"` = (`Lyra SW20 14v7 GuiCode`, LCD `cbed1028d988f67d1ddb8390a15cd66e28e08d93247539772446c57ea7e57adc`)
- **13.6 builds stay byte-identical.** The full suite in this repo passes. So do the research
  repo's byte pins (`cd ../firefly-firmware && .venv/bin/python -m pytest tests -q -n auto`,
  457 tests at the time of writing). Run both before every merge.
- **No 14.7 constant becomes a locator.** A 14.7 address or object number appears only as a
  test expectation.
- **An unidentified image is refused, never guessed** (`UNSUPPORTED_FIRMWARE`).
- **Emulator evidence never clears `--allow-unverified`.** `emulated_on` is shown as
  information only.
- **No raw exception for an op refusal.** An op compiled on an unlisted firmware reaches no op
  code.
- **Docs cite evidence by date, never by build name.** `.venv/bin/python scripts/scrub_check.py`
  exits 0.
- Work on a feature branch. Never `gh release create` by hand: a release is a pushed `vX.Y.Z`
  tag, and `release.yml` publishes it.

## Review Focus

1. **A built image used as the stock.** Its section names and LCD firmware are unchanged, so it
   identifies as its stock firmware (Task 1 test `test_a_built_image_keeps_its_identity`).
2. **The mixed setup the coach ran on 2026-08-30:** 14.7's GuiCode and GuiData in 13.6's
   container, with 13.6's LCD Firmware. It must not identify as 14.7 or 13.6. It gets
   `UNSUPPORTED_FIRMWARE` (Task 1 `test_mixed_pair_is_not_identified`, Task 3
   `test_compile_refuses_an_unidentified_image`).
3. **A 14.x image nobody has seen** (e.g. GuiCode `Lyra SW20 14v8 GuiCode`). It gets a clear
   refusal naming the image's GuiCode, not a crash in an op (Task 3
   `test_refusal_names_the_unknown_guicode`).
4. **The Studio compile cache on 14.7.** A cached prefix must not let an unsupported op through
   (Task 3 `test_cached_compile_refuses_the_same_way`).
5. **User ops on 14.7.** They are not on any op's list. They run on any *identified* firmware
   and are refused on an unidentified one, because their own `routinekit` checks are their
   evidence (Task 3 `test_user_op_runs_on_identified_firmware_only`).

---

### Task 1: Identify the firmware

**Files:**
- Create: `src/fireflymcf/firmware.py`
- Test: `tests/test_firmware.py`

**Interfaces:**
- Produces:
  - `firmware.KNOWN: dict[str, FirmwareId]`
  - `@dataclass(frozen=True) class FirmwareId: guicode: str; lcd_sha256: str`
  - `firmware.identify(image: bytes) -> str | None`: the key from `KNOWN`, or None.
  - `firmware.describe(image: bytes) -> str`: `"<GuiCode name> + LCD <sha[:12]>"` for messages;
    never raises (`"unreadable"`).
  - `firmware.guidata_checksum_label(image: bytes) -> str`: `"section-checksum:" +` the
    image's GuiData section name.

- [ ] **Step 1: Write the failing tests** (`NEEDS_RESEARCH_DATA` at module level; fixtures
  `mcf_1123`, `mcf_136`, `mcf_147` from `tests/conftest.py`)

```python
def test_identifies_each_corpus_image(mcf_1123, mcf_136, mcf_147):
    assert [firmware.identify(x) for x in (mcf_1123, mcf_136, mcf_147)] == ["11.23", "13.6", "14.7"]

def test_guidata_label_is_the_images_own(mcf_136, mcf_147):
    assert firmware.guidata_checksum_label(mcf_136) == "section-checksum:Lyra 13v6 GuiData"
    assert firmware.guidata_checksum_label(mcf_147) == "section-checksum:Lyra SW20 14v7 GuiData"

def test_a_built_image_keeps_its_identity(mcf_136):
    built = patch.set_build_name(patch.reseal(mcf_136)[0], "x")[0]
    assert firmware.identify(built) == "13.6"

def test_mixed_pair_is_not_identified(mcf_136, mcf_147):
    # 14.7's GuiCode name with 13.6's LCD firmware: rename 13.6's GuiCode section in place
    assert firmware.identify(_rename_guicode(mcf_136, b"Lyra SW20 14v7 GuiCode")) is None

def test_unknown_and_garbage_are_none(mcf_147):
    assert firmware.identify(_rename_guicode(mcf_147, b"Lyra SW20 14v8 GuiCode")) is None
    assert firmware.identify(b"\x00" * 64) is None
    assert "unreadable" in firmware.describe(b"\x00" * 64)
```

`_rename_guicode(image, name)` is a test helper. It overwrites the section's name field (found
through `container.parse_container`) with `name`, padded to the field's length, and does not
reseal.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_firmware.py -q`. Expected: ImportError, no module `fireflymcf.firmware`.

- [ ] **Step 3: Implement `firmware.py`.** `identify` parses with `container.parse_container`,
  catches `ValueError` (returning None), and matches the exact (GuiCode name, LCD sha256) pair.

- [ ] **Step 4: Run the tests to verify they pass.** Same command; expected: all PASS.

- [ ] **Step 5: Commit** — `git commit -m "firmware: identify an image by its GuiCode name and LCD firmware"`

### Task 2: The GuiData section name comes from the image

**Files:**
- Modify: `src/fireflymcf/newbutton.py` (the five `expected = ["section-checksum:Lyra 13v6 GuiData"]`, near lines 501, 589, 762, 917, 1063)
- Modify: `src/fireflymcf/statusbind.py:57` and its six `[GUIDATA_CHECKSUM]` uses
- Test: `tests/test_firmware.py`

**Interfaces:**
- Consumes: `firmware.guidata_checksum_label`.
- Produces: no new names. `statusbind.GUIDATA_CHECKSUM` is removed; any importer switches to
  the function.

- [ ] **Step 1: Write the failing tests.** On 2026-11-06 both of these calls reached their
  reseal on 14.7 and failed only on the 13v6 label:

```python
def test_statusbind_reseals_14_7(mcf_147):
    data, edits = statusbind.pin_filter_instance(mcf_147, 115, 49)   # var 115, instance 49
    assert any(e.what == "checksum" for e in edits)   # use relocate.Edit's actual field name

def test_newbutton_reseals_14_7(mcf_147):
    # set_binding_bytes on a group 14.7 has (find one with bindings via guidata), any
    # one-byte change; it must not raise "reseal changed ... expected exactly [...13v6...]"
    ...

def test_reseal_check_still_refuses_a_foreign_section(mcf_136):
    out = bytearray(mcf_136); out[_a_guicode_byte(mcf_136)] ^= 1    # a GuiCode byte, not GuiData
    with pytest.raises(statusbind.StatusBindError):
        statusbind._reseal_with(bytes(out), [], [firmware.guidata_checksum_label(mcf_136)])
```

  Call `pin_filter_instance` with the signature it has (`(data, var, instance, *, ...)`; pass
  `dgn=0x1FFC7` if 14.7's var 115 needs it).

- [ ] **Step 2: Run them.** Expected: the first two FAIL with `reseal changed ['section-checksum:Lyra SW20 14v7 GuiData'], expected exactly ['section-checksum:Lyra 13v6 GuiData']`. The third passes, which keeps the check honest.

- [ ] **Step 3: Replace each literal with `firmware.guidata_checksum_label(<the image being edited>)`:** `newbutton.set_binding_dgn`, `set_binding_bytes`, `retarget_command`, `retarget_indicator`, `build_home_button`, and the six `statusbind` callers.

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests -q -n auto`. Expected: all pass.

- [ ] **Step 5: Run the research repo's byte pins.** Expected: all pass.

- [ ] **Step 6: Commit** — `"newbutton, statusbind: the GuiData checksum label is the image's own"`

### Task 3: Ops declare their firmware; compile refuses outside it; verification is per firmware

**Files:**
- Modify: `src/fireflymcf/recipe.py`:
  - the `Op` base (near line 209);
  - `_build_ops`/`compile_project`/`compile_cached`;
  - the `unverified` computation in `_finish` (near line 501);
  - `CompileResult` (add `firmware`).
- Modify: `src/fireflymcf/userop.py` (user ops are exempt from `firmwares`)
- Modify: `src/fireflymcf/cli.py:1265-1290` (the unverified message names the firmware)
- Test: `tests/test_firmware_gate.py`

**Interfaces:**
- Consumes: `firmware.identify`, `firmware.describe`.
- Produces:
  - `Op.firmwares: frozenset[str] = frozenset({"13.6"})` (class default);
  - `Op.verified_on: frozenset[str] = frozenset({"13.6"})`;
  - `Op.emulated_on: frozenset[str] = frozenset()`;
  - `Op.is_verified(fw: str) -> bool`, returning `self.hardware_verified and fw in self.verified_on`.
    `hardware_verified` keeps its meaning and its instance overrides; it now means "verified
    on the firmware in `verified_on`";
  - `CompileResult.firmware: str | None`;
  - error code `UNSUPPORTED_FIRMWARE`, with `op_id` set.

- [ ] **Step 1: Write the failing tests**

```python
def test_compile_refuses_an_op_not_listed_for_the_firmware(mcf_147):
    # set_theme lists only 13.6
    res = _compile(mcf_147, [("t", "set_theme", THEME_PARAMS)])
    assert [(e["code"], e["op_id"]) for e in res.errors] == [("UNSUPPORTED_FIRMWARE", "t")]
    assert "14.7" in res.errors[0]["message"] and "13.6" in res.errors[0]["message"]

def test_every_refusal_is_reported_before_any_apply(mcf_147):
    res = _compile(mcf_147, [("a", "set_theme", THEME_PARAMS), ("b", "tank_heater_names", {})])
    assert [e["op_id"] for e in res.errors] == ["a", "b"]

def test_compile_refuses_an_unidentified_image(mcf_136, mcf_147):
    mixed = _rename_guicode(mcf_136, b"Lyra SW20 14v7 GuiCode")
    res = _compile(mixed, [("r", "rename_label", {"object": 5, "old": "ELECTRICAL", "new": "ELECTRICAX"})])
    assert res.errors[0]["code"] == "UNSUPPORTED_FIRMWARE"

def test_refusal_names_the_unknown_guicode(mcf_147):
    img = _rename_guicode(mcf_147, b"Lyra SW20 14v8 GuiCode")
    res = _compile(img, [("r", "rename_label", {"object": 11, "old": "LIGHTS", "new": "LIGHTX"})])
    assert "Lyra SW20 14v8 GuiCode" in res.errors[0]["message"]

def test_unverified_is_per_firmware(mcf_136, mcf_147, monkeypatch):
    monkeypatch.setattr(recipe.REGISTRY["rename_label"], "firmwares", frozenset({"13.6", "14.7"}))
    on136 = _compile(mcf_136, [("r", "rename_label", {"object": 5, "old": "ELECTRICAL", "new": "ELECTRICAX"})])
    on147 = _compile(mcf_147, [("r", "rename_label", {"object": 11, "old": "LIGHTS", "new": "LIGHTX"})])
    assert on136.ok and on136.unverified == [] and on136.firmware == "13.6"
    assert on147.ok and on147.unverified == ["r"] and on147.firmware == "14.7"

def test_cached_compile_refuses_the_same_way(mcf_147):
    cache = {}
    a = recipe.compile_cached(_project(mcf_147, [("t", "set_theme", THEME_PARAMS)]), mcf_147, cache)
    b = recipe.compile_cached(_project(mcf_147, [("t", "set_theme", THEME_PARAMS)]), mcf_147, cache)
    assert a.errors == b.errors and a.errors[0]["code"] == "UNSUPPORTED_FIRMWARE"

def test_user_op_runs_on_identified_firmware_only(mcf_147, tmp_path):
    # the no-op user op fixture (tests/fixtures/userops) compiles on 14.7 and is refused on
    # the renamed-GuiCode image
    ...
```

`THEME_PARAMS` is the smallest valid `set_theme` params; copy them from an existing
`set_theme` test. `_compile`/`_project` build a `recipe.Project` with the image's sha256 as
`base_sha256`.

- [ ] **Step 2: Run them to verify they fail.** Expected: `set_theme` reaches its own
  `WRONG_BUILD`, not `UNSUPPORTED_FIRMWARE`; `CompileResult` has no `firmware`.

- [ ] **Step 3: Implement.**
  - **The refusal** is a check beside `_check_toggles_driven`, so it is collected for every
    enabled op before any apply.
  - **The message:** `"<type> is not supported on <describe(stock)>: it has been tested on
    <sorted firmwares>"`. For an unidentified image: `"this firmware is not one fireflymcf has
    been tested on (<describe>)"`.
  - **Unverified:** `_finish` computes it with `o.is_verified(fw)`; an unidentified image never
    reaches `_finish`.
  - **User ops** (`getattr(o, "user_op", False)`) skip the `firmwares` check but not the
    identification check.
  - **CLI:** the refusal line becomes `"… unverified on hardware for firmware <fw>: …"`.

- [ ] **Step 4: Make the existing tests agree.** Run the full suite. Each test that compiled an
  op successfully on 11.23 or 14.7 is evidence for that op on that firmware: add the firmware
  to that op's `firmwares` with a one-line comment citing the test. Each test that asserted an
  op's own refusal on 11.23/14.7 (e.g. `test_alt_reboot.py`'s `WRONG_BUILD`) now asserts
  `UNSUPPORTED_FIRMWARE` at compile. Its direct call to the op's library function keeps the
  old assertion. Do not add a firmware to any list without a test that compiles there.

- [ ] **Step 5: Run** the full suite and the research pins. Expected: all pass.

- [ ] **Step 6: Commit** — `"recipe: ops declare their firmware; UNSUPPORTED_FIRMWARE; verification per firmware"`

### Task 4: Show firmware support everywhere an op is described

**Files:**
- Modify: `src/fireflymcf/ops_doc.py:418-431` (entries gain `firmwares`, `verified_on`, `emulated_on`; the RECIPE-OPS table gains a "Firmware" column)
- Modify: `studio/server/app.py:78` (the op schema gains the same three lists)
- Modify: `src/fireflymcf/cli.py` (`fireflymcf ops NAME` prints them)
- Regenerate: `docs/RECIPE-OPS.md`, `schema/ffproj.schema.json` (`.venv/bin/python -m fireflymcf.ops_doc`)
- Modify: `plugin/skills/build-firmware/SKILL.md` (one paragraph: a 14.x owner's stock is identified; `UNSUPPORTED_FIRMWARE` means "not yet on this firmware", never work around it; `--allow-unverified` is per firmware)
- Test: `tests/test_ops_doc.py`

- [ ] **Step 1: Write the failing test**

```python
def test_entries_carry_firmware_support():
    e = {x["name"]: x for x in ops_doc.entries(recipe.REGISTRY)}
    assert e["set_theme"]["firmwares"] == ["13.6"]
    assert e["set_theme"]["verified_on"] == ["13.6"] and e["set_theme"]["emulated_on"] == []
    assert "| Firmware |" in ops_doc.render_markdown(recipe.REGISTRY)
```

(Use the names `ops_doc` actually exports for its entries and markdown; read `main()`.)

- [ ] **Step 2: Run it; expected to FAIL** with KeyError `firmwares`.
- [ ] **Step 3: Implement**, regenerate the docs, and edit SKILL.md.
- [ ] **Step 4: Run** `tests/test_ops_doc.py tests/test_install_docs.py tests/test_studio_server.py`, then `scripts/scrub_check.py`. Expected: pass, exit 0.
- [ ] **Step 5: Commit** — `"ops: firmware support in ops, RECIPE-OPS and Studio"`

### Task 5: The emulator runs 14.7

An agent may extend the emulator wherever 14.7 differs. Every change keeps 13.6's emulator
tests green.

**Files:**
- Modify: `src/fireflymcf/emu/scenario.py:104-136` (`supported` accepts `firmware.identify(image) in {"13.6", "14.7"}`; the message for others becomes `"not emulated: <describe> (13.6, 14.7)"`)
- Modify: whatever in `src/fireflymcf/emu/` the tests below show to be wrong on 14.7
- Modify: `tests/emu/test_scenario.py:172-180`, `tests/emu/test_gate.py:66` (the unsupported example becomes 11.23 only)
- Test: `tests/emu/test_scenario147.py`

**Interfaces:**
- Consumes: `firmware.identify`, `firmware.describe`.
- Produces: `Scenario(mcf_147)` works with the existing API (`boot`, `goto`, `page`, `tap`,
  `frames`, `set_g12_selector`, `screenshot`, `read_mem`).

- [ ] **Step 1: Write the failing tests.** All must pass on 14.7 stock with no op applied:

```python
def test_boots_to_home(mcf_147):
    s = Scenario(mcf_147); s.boot(); assert s.page() == "home"

@pytest.mark.parametrize("screen", [0, 1, 2, 3, 4])
def test_rail_reaches_each_page(mcf_147, screen):
    # the five rail pages by screen number (14.7's screen 1 has a blank header, so go by
    # number, not by name); after goto, s.screen() == screen
    ...

def test_tap_sends_on_the_bus(mcf_147):
    # Light Master ON on HOME: at least one new panel frame (SA 0x9F) appears after the tap,
    # and none in the same window without it
    ...

def test_status_repaints(mcf_147):
    # the G12 peer reports a load ON (set_g12_selector or the peer's output state): the
    # control's indicator pixels change between OFF and ON screenshots of HOME
    ...
```

- [ ] **Step 2: Run them.** Expected: every test fails in `Scenario.__init__` with `not emulated`.
- [ ] **Step 3: Lift the check in `supported`.** Re-run. Fix each remaining failure in the
  emulator, not in the test. Each fix gets a comment citing the 14.7 instruction or record
  that differs.
- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/emu -q -n auto`. Expected: all pass,
  13.6 included.
- [ ] **Step 5: Commit** — `"emu: run 14.7 (boot, rail, tap sends, status repaint)"`

### Task 6: Enable on 14.7 the ops that already work there

The 2026-11-06 measurement found these op types compile on 14.7 unchanged or with a real 14.7
target. They need an emulator test on 14.7 before `"14.7"` joins their `firmwares` and
`emulated_on` lists:

- **Unchanged:** `append_filter`, `drop_from_master`, `rename_label`, `set_record_color`,
  `install_glyph`, `set_gesture`, `set_corner_radius`.
- **With a real 14.7 target:** `move_element`, `move_attach`, `recolor`, `remove_records`,
  `remove_control`, `set_icon`, `fill_to_capsule`.

**Files:**
- Modify: `src/fireflymcf/recipe_ops.py` (the `firmwares` and `emulated_on` of each op that passes)
- Test: `tests/emu/test_ops147.py`

- [ ] **Step 1: Write one failing test per op type.** Each compiles a one-op project
  (`compile_project`) against 14.7 stock, on a target found on 14.7, then checks the behaviour
  in `Scenario` on the built image:

| op | assertion on 14.7 |
|---|---|
| `rename_label` | the new text's pixels appear on the page (screenshot differs from stock only inside the label's box) |
| `set_record_color`, `recolor` | the recoloured record's box changes colour; nothing outside it changes |
| `move_element`, `move_attach` | the element is drawn at its new place and not at its old one |
| `set_corner_radius`, `fill_to_capsule` | the box's corner pixels change; its interior does not |
| `remove_control`, `remove_records` | the control is not drawn and its touch region no longer responds (tap there sends nothing) |
| `set_icon`, `install_glyph` | the icon draws the new glyph's pixels |
| `append_filter` | a frame on the new DGN changes the subscribed slot (`read_mem` on `followchannel.status_slot_addr(var)`) |
| `drop_from_master` | a master ON tap no longer sends the dropped channel |
| `set_gesture` | the control's frames follow the new gesture (hold → its hold frame) |

  Find each target from 14.7's own records (`records.object_records`, `fireflymcf pages`).
  Never reuse a 13.6 object number.

- [ ] **Step 2: Run them.** Expected: each fails at compile with `UNSUPPORTED_FIRMWARE`.
- [ ] **Step 3: For each op whose test then passes with `"14.7"` added to `firmwares`, add it
  there and to `emulated_on`.** An op whose emulator behaviour is wrong on 14.7 stays off the
  list. Its test is marked `xfail(strict=True)` with the observed failure as the reason, and
  it is reported at hand-off.
- [ ] **Step 4: Run** the full suite, the research pins and `scrub_check`. Regenerate the
  docs (`python -m fireflymcf.ops_doc`). Expected: all pass. RECIPE-OPS shows 14.7 for each
  enabled op.
- [ ] **Step 5: Commit** — `"ops: enable on 14.7 the ops whose emulator tests pass there"`

### Task 7: Hand-off

- [ ] **Step 1:** Merge to `main` with `--no-ff`, bump the patch version (`pyproject.toml`,
  `plugin/.claude-plugin/plugin.json`, the SKILL.md install URL), push `main`, then push the
  tag. Watch the tag's `release` run with `gh run watch` until it succeeds.
- [ ] **Step 2:** Tell the owner:
  - which ops now run on 14.7 and which stayed off, with why;
  - that all of them are **unverified on 14.7** until their panel runs them;
  - the first hardware step: flash **stock 14.7's LCD Firmware and GuiCode+GuiData units**
    (not the G12) and confirm the panel boots and drives the 13.6 G12. Only then flash a
    built 14.7 image.
