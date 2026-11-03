---
name: write-new-op
description: Use when an RV owner's Firefly / Spyder panel change cannot be built from the existing fireflymcf ops (build-firmware hands off here), when asked to write, fix, install or share a user-written op (`user:` type, an `ops/` folder with manifest.json, op.py and emu_test.py), or before running any op someone else wrote.
---

# Write a new op

A **user op** is a recipe op the owner (or you, for them) writes: one folder of Python that the
`fireflymcf` engine loads beside a project, gates the same way it gates its own ops, and runs in
the panel emulator before anything is flashed. Use the `fireflymcf` PATH the plugin's hook printed
(`F=PATH` below), exactly as `build-firmware` says. Everything in `build-firmware` still applies:
the owner's own stock `mcf.bin`, one project compiled from stock, `verify`, the handover. This
skill adds what a new op needs.

**Unofficial, no warranty.** fireflymcf is not affiliated with Firefly Integrations or any coach
maker, and comes without warranty of any kind. A user op has been checked by nobody but its author.

**Trust.** A user op, and its `emu_test.py`, runs with the same trust as any Python you run:
loading it executes it with your permissions, before any check. The engine's checks (each write
must match the bytes it expects, the op's ledger must replay to its output, every changed section
must be declared in `touches`) are correctness gates. They are not a sandbox. Never pass
`--allow-user-ops` for code you have not read.

**Where the docs are.** The package installs code only. The repository documents this skill
cites (`docs/BUS-BASELINE.md`, `docs/GUICODE.md`, `docs/CHANGE-CATALOG.md` and the rest of that folder)
are read on GitHub: https://github.com/dbergl/fireflymcf/tree/main/docs, raw text at
`https://raw.githubusercontent.com/dbergl/fireflymcf/main/docs/<NAME>.md`. The installed
package's own source (its docstrings name the proof behind each op) is the other reference.

## 1. Check composition first

A new op is the last resort. Run `$F ops` (and `$F ops NAME` for an op's parameters and proof)
and read the change catalogue (`docs/CHANGE-CATALOG.md` in the fireflymcf repository,
https://github.com/dbergl/fireflymcf/blob/main/docs/CHANGE-CATALOG.md). Two or three existing ops
in one project often do the job: a follower plus a readout, a button plus a status light. A
built-in op carries a hardware record; a user op carries none. Write one only when you can say
which part of the request no op or combination does, and tell the owner that.

## 2. Find what the change needs in the owner's image

Every address, slot, selector, DGN, channel, group and object index comes from a tool run on
**the owner's file**, never from memory, a forum or the RV-C specification. Builds differ.

```bash
F=fireflymcf; M=stock/mcf.bin                 # F=the PATH the plugin's hook printed
$F pages $M                                   # screens: --root index, screen id, header
$F labels $M --containing "Pump"              # records with that text, by object index
$F options $M                                 # OPTIONS rows: selector, binding, floorplans
$F pins $M --names                            # G12 outputs, their status cells and consumers
$F bus-map $M --name "pump"                   # what a control sends, what the panel shows
$F bus-map $M --dgn 1FED9 --detail            # one DGN: every receive entry, slot and sink
$F slot-source $M --slot 289                  # who writes a widget slot, and how
$F g12-rules $M --selector 0x92               # G12 rules a config selector drives
$F g12-inputs $M                              # G12 input pins and their receive variables
$F gestures $M --group 129                    # how a group answers tap, hold and release
```

Most take `--json`. At build time, resolve what you found through the primitives, not literals:
`ed.call("resolve_register", dgn=..., instance=..., selector=...)` gives a receive slot on the
image being built, so the op still works on the next build. **Scanning the image yourself:**
search `movw`/`movt` pairs and modified immediates as well as literal pools; an index-based read
or a Thumb-1 immediate past `#imm5` hides a reference from a literal scan.

If the project already carries ops the new one builds on (a follower, an OPTIONS row), compile
that project first and inspect **the compiled image**: what the new op meets is the output of the
ops before it, not stock.

## 3. Write `emu_test.py` first

The test states what the owner asked for, in the emulator's terms, before any op exists. Then
watch it fail on the image without the op:

```bash
$F emulate $M --scenario ops/my-op/emu_test.py      # exit 1 unless it passes: it must FAIL here
```

The file defines `scenario(s)`; `s` is a `fireflymcf.emu.scenario.Scenario` on the built image,
not yet booted (`help()` on that class is the full reference):

| call | does |
|---|---|
| `s.boot()` | power on, accept the terms page, land on HOME |
| `s.goto("OPTIONS")` | navigate: HOME, ELECTRICAL, LIGHTS, CLIMATE CONTROL, SETTINGS, OPTIONS, or any header the image draws |
| `s.tap("Water Pump", hold_s=0, page=None)` | tap or hold a control by its label (whitespace-normalised) or object index |
| `s.run(seconds)`, `s.now` | advance simulated time; seconds since boot |
| `s.set_tank_raw(sensor, raw)` | the G12's raw tank input (1 fresh, 2 grey, 3 black, 4 grey 2; high is empty), read once a second |
| `s.g12_selector(sel)` | the emulated G12's stored value for a config selector (None: unset) |
| `s.set_g12_selector(sel, value)` | give the G12 a stored value (before `boot()`: the coach comes up that way); the panel learns it from the G12's report, so run a few seconds |
| `s.frames(dgn=, sa=0x9F, since=)` | frames on the bus (`.t`, `.can_id`, `.dgn`, `.sa`, `.data`); `sa=None` for every sender |
| `s.read_mem(addr, n)` | emulated RAM, e.g. a cell your routine owns |
| `s.screen`, `s.page`, `s.control_box(c)` | the screen id and header showing; a control's touch box |
| `s.screenshot(path)`, `s.image` | save the display; the image bytes under test |

Only the calls in this table are the API; a name starting with `_` can change in any release.
Start the coach in the state the owner runs it (`set_g12_selector` for a setting the fresh
emulated G12 leaves unset), and make every manual change the way the owner would, with `tap`.
If the table cannot express a case, say so to the owner as a gap in the test; do not reach
into the emulator.

**One case per clause of the request.** Write down each thing the owner said the change must
do and each thing it must *not* do, then give each its own case: every "only if", "unless",
"but leave it when" and "if I change it by hand" is a case where the op must stay silent. For
each threshold or delay, test both sides of it (a little short of it: nothing; past it: the
action), in each direction the owner named. Where the op shares a setting or a cell with
something else, assert that the other part is unchanged after every step.

Assert crossings and counts, not single readings: "after X, exactly one frame with payload P
within 5 s, and none before". When asserting that nothing was sent, count every frame the op
could produce on that DGN and selector (any function byte), not only the kind it is meant to
send. Assert what the owner would see (a selector's value, a cell a widget draws), not only
that your code ran. Anything that raises fails the test with its line.

**Run every assertion on stock first.** The stock panel is not silent: stock 13.6 sends a D1
config write on selector `0x9B` at boot (observed in the emulator, not checked against a
capture). "Nothing was sent" is false on stock; a test must assert only what the op changes.

**What emulated means.** The emulator runs the panel's own code against a simulated G12 and RV-C
network on 13.6 only. Any other build reports `not emulated: <reason>`, never a pass. It is not
the coach: a pass says the panel does what the test asserts against simulated peers. When a coach
capture disagrees, the capture wins.

## 4. Write the op against `Editor` and `routinekit` only

Start from the worked example in this skill's folder, `examples/counter-op/` (a per-tick routine
that counts in fresh SRAM: manifest, `op.py`, `emu_test.py`, README). Copy it to
`ops/<name>/` beside the owner's `*.ffproj.json` and change it.

**`manifest.json`:** `name` (the folder name), `type` (`user:<name>`; a user op can never take a
built-in's name), `version`, `author`, `description`, `touches` (any of `guicode`, `guidata`,
`g12`, `lcd`: every section the op changes; the build refuses an undeclared change, and the
container header can never be declared), `written_against` (a list of objects: `build_name`,
`base_sha256`, `recipe_ops_required`), `provenance` (a list of objects: `kind` `emulated` or
`flashed`, `base_sha256`, `date`, `by`, `note`).

**`op.py`:** a `fireflymcf.userop.UserOp` subclass registered with `@recipe.op("user:<name>")`.

- `PARAMS` (a JSON Schema fragment) and `validate()`: read params with `self._param(name,
  default, kind)`; raise `recipe.RecipeError("BAD_PARAMS", ...)`. An undeclared param is refused.
- `build(self, ed, ctx)`: every byte goes through the `Editor` `ed`. `ed.patch(off, expected,
  new, note)` / `ed.patch_addr("GuiCode", addr, expected, new, note)` (same length; the expected
  bytes are checked, a mismatch is "this op does not match your firmware"),
  `ed.grow_guicode(payload, note)`, `ed.call("primitive", ...)`, `ed.section("GuiData")`, `ed.data` (read). Reading is free:
  `ctx.handles[ref_id]` holds what earlier ops published (an op refers to one with
  `{"ref": "<op id>"}`), and `ctx.handles[self.spec.id] = {...}` publishes yours.
- `check(self, data, ctx) -> str`: runs on the finished image; prove the change is there and
  return one manifest line. Raise on anything wrong.
- Do not override `apply` or `verify` (they are the gate). Import sibling files with
  `from . import helper`; only the hashed source in the folder runs (no `.pyc`, no symlinks).

**The primitives** (`fireflymcf.routinekit`, semver-stable; [`USER-OPS.md`](USER-OPS.md) beside
this file is the reference, with the authoring guide and worked hints, and
`help(fireflymcf.routinekit)` has the same). Call a primitive through `ed.call(name, ...)`
with JSON-able or bytes arguments (lists, not sets):

| primitive / helper | gives |
|---|---|
| `install_tick(ed, build, note)` | appends a per-tick routine: `build(code_load_addr, tail_target) -> (blob, entry_offset)` |
| `emit_prologue(r)`, `emit_tail(r, tail_target)`, `assemble_tick(r, code_load_addr, prefix=)` | the link contract and assembly (`thumb.Routine(pool, error=routinekit.RoutineKitError)`) |
| `ed.call("fresh_sram", n, taken=[...])` | `n` bytes of SRAM no form reaches |
| `ed.call("resolve_register", dgn=, instance=, selector=, var_index=, field=)` | an existing receive register and its `slot_addr` |
| `ed.call("slot_addr", var_index)` | a variable-table slot (index-split) |
| `config_frame(selector, value, function)` | a 12-byte `0x1FED9` OPTIONS frame template (`0xD1` write, `0xD2` delta, `0xD3` read-back) |
| `emit_send_frame(r, template_addr, value_in_r3=False)` | the panel's own send call for a template in your prefix |
| `ed.call("find_config_toggle", selector)` | an existing OPTIONS toggle group, or None |
| `guicode(data)`, `find_tag(data, tag)`, `tick_chain(data)` | readers: GuiCode at its runtime base; the address of a tag you put in a prefix; the tick chain |

`thumb` encoders (`thumb.ldr_imm`, `ldrh_imm`, `strh_imm`, `cmp_imm8`, `bcc_n`, `movw`, …) build
the routine; `Routine.assemble` checks every instruction against capstone. Read the worked hints
in [`USER-OPS.md`](USER-OPS.md) before writing a routine. Use only `userop`, `recipe` (`op`,
`RecipeError`), `routinekit` and its `thumb`, and the readers USER-OPS lists: every other module
can change in any release.

**The link contract** (each routine on the tick chain): starts `push {r0, r1, r2, r3, lr}`,
never writes r4-r7 (not checked for you), leaves through `pop.w {r0, r1, r2, r3, lr}` then `b.w`
to the previous link, and that `b.w` is its first. Call into the panel only through the
primitives (`emit_send_frame`); a call to any other GuiCode function needs its own proof.

## 5. Compile and emulate

The project lists the op like any other: `{"id": "my", "type": "user:<name>", "params": {...}}`.
Put it after the ops whose results it uses; a page-header rename and `set_theme` stay last.

```bash
$F ops --project P.ffproj.json               # lists the user ops it found (reads manifests only)
$F validate P.ffproj.json $M --allow-user-ops
$F compile  P.ffproj.json $M -o build/mcf-NAME.bin --allow-user-ops
$F verify build/mcf-NAME.bin $M --config coach.json --emulate --project P.ffproj.json --allow-user-ops
$F emulate build/mcf-NAME.bin --scenario ops/my-op/emu_test.py    # fast loop while debugging
```

Pass `--allow-user-ops` only after the risk statement (section 6). Without it `compile`
refuses (`USER_OPS`) and imports nothing; every op folder found in `ops/` is imported when it
is given, used or not, so remove folders the project does not use. `--allow-user-ops` admits
user ops only; an unverified built-in op still needs `--allow-unverified`. A G12 op also needs
the owner's recorded yes (`G12_ACK_REQUIRED` until then). `verify --emulate --project` runs each
op's `emu_test.py` and records the result in the build's sidecar (`<build>.json`, `emulation`);
it needs `--allow-user-ops` (the test is the author's code) but not the G12 acceptance, because
emulation touches no hardware.

**A gate failure stops the build**; fix the op. `FIRMWARE_MISMATCH` means the bytes at a site are
not what the op expects (wrong base, or an earlier op moved them). `USER_OP_TAMPERED`: a byte was
written outside the `Editor`. `USER_OP_UNDECLARED_TOUCH`: a section changed that `touches` does
not name. `STALE_PLAN`, `BAD_HOOK`, `BAD_ROUTINE`: the tick install's checks. `USER_OP_CRASHED`
names the file and line. Report each to the owner as "this op does not match your firmware" or
"the op failed", never as a policy refusal. **An emulator failure does not stop the image being
written**; it is reported, and the handover says so. Fix the op and rebuild rather than hand over
a failing one.

The emulator is the `emu` extra; the plugin's hook installs it with the rest. If the hook's line
said it did not install, run the `pip install` command that line gives. If `--emulate` says
`emulate needs the emu extra`, the install predates the extra: run the command it prints.

## 6. State the risk before the first build

Before the first `validate` or `compile` with `--allow-user-ops` of an op new to this owner
(written now, or installed), say what it changes in plain words, then the worst case, word for
word:

- **Panel only** (`touches` has no `g12`, and nothing it sends makes the G12 act): "This changes
  what your panel shows or sends. Worst case the panel draws wrongly or a button misbehaves; you
  can always flash your stock mcf.bin from USB to undo it."
- **G12** (`touches` has `g12`, or its routine sends what the G12 acts on): "This changes your
  G12 configuration, which drives real loads: pumps, slides, heaters, lights. A mistake can switch
  something on or off when you don't expect it. Do you want to go ahead?" Build only on a yes,
  then record it: `$F op accept P.ffproj.json user:<name> --g12`.

Choose by what the op can cause, not only by `touches`. A routine that makes the panel send
commands or OPTIONS writes the G12 acts on changes what drives real loads. Give it the G12
statement and record the yes with `--g12`, even though its manifest touches only `guicode`. Record the owner's go-ahead
for a panel-only op too (`$F op accept P.ffproj.json user:<name>`), so the sidecar shows it.
Acceptance is bound to the op folder's hash: any edit voids it, and the owner is asked again.

## 7. Hand over

Use `build-firmware`'s seven-slot handover. For a build with user ops, slot 1 and slot 3 carry:

- **File:** the `mcf-*.bin` name, its SHA-256 (`sha256sum`), and the build name.
- **Every user op named**: type, version and folder hash (the sidecar's `user_ops`), what it
  changes, and whether the owner accepted it.
- **Its emulator result, exactly one of:** "emulated, not flashed" (the test passed; no coach has
  run it); "not emulated: <reason>"; "emulator test FAILED: <reason>".
- That a user op has no hardware record: the owner is the first coach to run it.

## 8. A shared op: read it in full first

Someone else's op is code that runs on this computer with the owner's permissions. Install it
without running it, then read it:

```bash
$F op install P.ffproj.json https://example.org/their-op.git      # or a folder or a .zip
$F ops user:their-op --project P.ffproj.json                      # its manifest and README
```

`op install` copies the folder into `ops/<name>/`, records its source and hash, and prints the
manifest and every file; it never imports anything. Before any `compile --allow-user-ops`,
`validate`, `emulate --scenario` or `verify --emulate` that would run it: **read every file** (`op.py`,
`emu_test.py`, every helper `.py`, the README), and tell the owner what it touches: which
sections its `touches` names and whether the code writes only those, what it makes the panel
send, what its routine reads, whether it does anything a firmware op has no reason to (files,
network, processes, other paths). If the code and the manifest disagree, or you cannot follow
it, do not run it; say why. Then section 6, as for an op you wrote.

## 9. After the owner flashes it

When the owner reports the build on the coach, add a `provenance` entry to the op's
`manifest.json`: `{"kind": "flashed", "base_sha256": "<stock sha256>", "date": "YYYY-MM-DD",
"by": "<who flashed>", "note": "<what was checked and what was seen>"}`. Editing the manifest
changes the folder hash, so record the acceptance again (`op accept`) before the next build. To
share it: `$F op pack P.ffproj.json <name> -o <name>.zip` (deterministic; the zip comment carries
the folder hash, which `op install` checks).

## Evidence rules and traps for an op author

Each claim about the firmware carries its class: *hardware* (seen on a coach), *proven-from-code*
(a cited instruction), *emulator* (seen in the emulator only), *library* (this library's code,
pinned by its tests). `PC.N` and `Finding N` name entries in the fireflymcf repository's
`docs/BUS-BASELINE.md` and `docs/GUICODE.md`.

- **The capture wins.** A live CAN capture beats a reading of the file and the emulator. The
  file has twice been read as contradicting a capture, and the reading was wrong both times
  (project record, `BUS-BASELINE.md`).
- **A register with one writer is not understood until its reader is named.** An OPTIONS value is
  written by the frame the panel sends and updates from the G12's echo; poking the cell changes
  nothing the G12 does (*hardware*: PC.42, PC.45).
- **An argument from absence needs a guarantee the thing would have been found.** Index-based
  reads and Thumb-1 immediates past `#imm5` defeat literal scans.
- **Verifying only what you set is not verification.** Read `verify`'s whole report: every byte
  run, object and page; one your op did not mean to change is a defect.
- **Check the test can fail.** Run it on stock (or with the op's key instruction removed) and see
  it fail.
- **Variable-table slots are index-split:** index ≤ 255 at `0x20007FC8 + 2*i`, > 255 at
  `0x20008000 + 2*i` (*proven-from-code*: `FUN_0021C760` at `0x0021C9B0`). The flat formula wired
  a follower to the wrong slot (*hardware*: PC.44); use `slot_addr`.
- **SRAM above `0x2000AD98` holds nothing unless something writes it**: the C runtime initialises
  only below (*proven-from-code*: the reset handler's `.data`/`.bss` loops on 13.6). A fresh cell
  is garbage at power-on: initialise it or tolerate any start value.
- **Chain on the one hook.** Every routine shares the per-tick `bl` at GuiCode+`0xEE3C`
  (*proven-from-code*; chains of four or more links run on the coach, *hardware*). Install
  through `install_tick`, which keeps every earlier link, and never patch the hook yourself.
- **Switch arms must be flow-neutral, and the `0xFFFF` default arm sits at the base.** A record
  advances the layout pen by its own x, so an arm that does not put the pen back moves every later
  record, and only when it is selected (*library*: the layout re-walk); on no match the panel
  draws the base only if its match value is `0xFFFF`, otherwise nothing (*proven-from-code*,
  Finding 16).
- **A hold only works on a group GuiCode does not drive.** Holds put on groups GuiCode sets as a
  block (13.6: 130-139) took taps but never a hold (*hardware*, 2026-09-30; the mechanism is not
  traced, `CONTROL-BEHAVIOR.md`). The built-in ops refuse such a group; do not count on the engine
  checking a user op's choice.
- **The DGN registration cap is 83, and overflow is silent** (*proven-from-code*: `cmp r2, #0x53`
  at `0x0021A62A` in the registration function `FUN_0021A618`, whose refusal no caller checks).
  Stock 13.6's variable table registers 66 (*library*: `statusbind.registered_dgns`; other call
  sites register more, so treat it as at least 66). Every new subscription counts.
- **Most touch actions run on release** (*proven-from-code*: the finger-lifted `0xFF` handler,
  Finding 83), so a test taps and releases; the scenario's `tap` does.
- **A received value is drawn only if GuiCode copies its slot to a cell a widget draws**
  (*proven-from-code*, Finding 82). A new subscription alone shows nothing; `bus-map` says which
  entries reach a widget.
- **Two publishers on one DGN: the last frame wins**; the receive filter does not see the sender
  (*hardware*: PC.46).
- **Label inference as inference.** Tell the owner which claims rest on the file, the emulator or
  the coach, and which only the coach can settle.
