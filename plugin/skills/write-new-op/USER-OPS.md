# User ops

A user op is a recipe op you write yourself: a folder with a `manifest.json` and an `op.py` whose
`UserOp` subclass changes the image only through a `userop.Editor`. This page is the authoring
guide ("Writing a user op") and the reference for the building blocks such an op has
("Primitives"). It ships with the plugin's `write-new-op` skill: [`SKILL.md`](SKILL.md) beside it
is the procedure an agent follows, and [`examples/counter-op/`](examples/counter-op/) is a
complete, tested example op.

**Evidence classes.** A claim about the firmware carries its class:
- *hardware*: seen on a coach. `PC.N` names an entry in the fireflymcf repository's
  `docs/BUS-BASELINE.md`.
- *proven-from-code*: a cited instruction or function in the image. `Finding N` names a section
  of the repository's `docs/GUICODE.md`.
- *emulator*: observed in the emulator, not checked against a capture.
- *library*: what this library's code does, pinned by its tests.

The repository's documents are not installed with the package; read them on GitHub,
https://github.com/dbergl/fireflymcf/tree/main/docs (raw text at
`https://raw.githubusercontent.com/dbergl/fireflymcf/main/docs/<NAME>.md`).

fireflymcf is unofficial (not affiliated with Firefly Integrations or any coach maker) and comes
without warranty of any kind. A user op has been checked by nobody but its author.

**Trust.** A user op runs with the same trust as any Python you run: importing its `op.py`
executes it, with your permissions, before any check runs. The engine replays the op's `Editor`
ledger against its input and refuses an image the ledger does not account for. That replay is a
correctness gate. It is not a sandbox, and it cannot stop an op from doing anything else Python
can do. Read an op before you allow it (`--allow-user-ops`).
The same holds for its `emu_test.py`, which the emulator gate runs. Before running anything
from an op someone else wrote, read every file in it and know what it touches.

## Writing a user op

### The folder

One op is one folder, found in `ops/` beside the `*.ffproj.json` or listed in the project's
`ext_ops` (paths relative to the project file):

```
ops/my-op/
  manifest.json   # what it is and what it touches
  op.py           # the UserOp subclass
  emu_test.py     # scenario(s): the emulator test
  README.md       # what it does, for the person who receives it
```

`manifest.json` fields, all required:

| field | type | meaning |
|---|---|---|
| `name` | string | the folder name |
| `type` | string | `user:<name>`; the recipe's op type. A built-in's name is refused |
| `version` | string | the op's own version |
| `author`, `description` | string | who wrote it; one line of what it does |
| `touches` | list of strings | every section the op changes: `guicode`, `guidata`, `g12`, `lcd` |
| `written_against` | list of objects | `{build_name, base_sha256, recipe_ops_required}` per base it was written for |
| `provenance` | list of objects | `{kind: "emulated" or "flashed", base_sha256, date, by, note}` |

The engine compares the op's input and output section by section and refuses
(`USER_OP_UNDECLARED_TOUCH`) a change to a section `touches` does not name. A section's
descriptor counts as part of it; the container header (build name, pointers) can never be
declared, and resealing a checksum alone touches nothing.

### `op.py`

```python
from fireflymcf import recipe, routinekit, thumb
from fireflymcf.userop import UserOp


@recipe.op("user:my-op")
class MyOp(UserOp):
    PARAMS = {"properties": {"step": {"type": "integer"}}, "required": []}

    def validate(self):                       # params; raise RecipeError("BAD_PARAMS", ...)
        self.step = self._param("step", 1, int)

    def build(self, ed, ctx):                 # every write through the Editor `ed`
        cell = ed.call("fresh_sram", 4)
        routinekit.install_tick(ed, lambda load, tail: ..., "my routine")

    def check(self, data, ctx) -> str:        # on the finished image: prove it, return a line
        return "..."
```

- **`Editor`**: `patch(off, expected, new, note)` and `patch_addr(marker, addr, expected, new,
  note)` replace bytes of the same length after checking the bytes expected there (a mismatch
  is `FIRMWARE_MISMATCH`, "this op does not match your firmware"); `grow_guicode(payload, note)`
  appends to GuiCode; `call(name, ...)` runs a primitive; `section(marker)`, `offset_of(marker,
  addr)` and `data` read. Every step lands in a ledger; the engine replays it on the op's input
  and refuses (`USER_OP_TAMPERED`) an output the replay does not reproduce.
- **Handles**: `ctx.handles[op_id]` holds what earlier ops published, so a param
  `{"ref": "<op id>"}` can name one; `ctx.handles[self.spec.id] = {...}` publishes yours.
- **Do not override** `apply` or `verify` (the gate) or set `hardware_verified` (always False).
  A crash anywhere in the op becomes `USER_OP_CRASHED` naming the file and line.
- **Imports**: `from . import helper` loads a sibling file. Only the source the folder hash
  covers runs: no `.pyc`, no symlinks, no plain `import helper`.
- **`capability_delta(ctx)`** is the one other hook an op may define: an op that changes what a
  binding sends returns `(lost, gained)` sets of `(channel, command)` pairs, as built-in ops do.

### Building, testing, sharing

| step | command |
|---|---|
| list the project's user ops (manifests only, nothing runs) | `fireflymcf ops --project P.ffproj.json` |
| one op's manifest and README | `fireflymcf ops user:my-op --project P.ffproj.json` |
| run the test while writing (fails on stock first) | `fireflymcf emulate MCF --scenario ops/my-op/emu_test.py` |
| gate and build | `fireflymcf compile P.ffproj.json STOCK -o build/mcf-NAME.bin --allow-user-ops` |
| record the owner's acceptance | `fireflymcf op accept P.ffproj.json user:my-op [--g12]` |
| verify and emulate | `fireflymcf verify build/mcf-NAME.bin STOCK --emulate --project P.ffproj.json --allow-user-ops` |
| share | `fireflymcf op pack P.ffproj.json my-op -o my-op.zip` |
| receive | `fireflymcf op install P.ffproj.json SRC` (a folder, a `.zip` or a git URL) |

- `compile` and `validate` refuse a project with user ops (`USER_OPS`) without `--allow-user-ops`
  and import nothing. With it, every op folder they find is imported, used or not.
  `--allow-user-ops` admits user ops only; an unverified built-in op still needs
  `--allow-unverified`.
- An op whose `touches` includes `g12` also needs the owner's recorded acceptance
  (`op accept --g12`; `G12_ACK_REQUIRED` until then). The record names the folder hash, so any
  edit to the folder voids it.
- `verify --emulate --project` runs each op's `emu_test.py` and records the result in the
  build's sidecar under the op's type: `passed`, `failed` with its reason, or `not emulated` with
  its reason (no `emu_test.py`, or a build the emulator does not model: it models 13.6 only). It
  needs `--allow-user-ops`, because the test is the author's code, but not the G12 acceptance:
  emulation touches no hardware. A failing test is reported; it never stops the image being
  written. A gate failure (expected bytes, replay, touches, `verify`) does.
- The sidecar's `user_ops` names every op the project uses: type, version, folder hash, source,
  touches and the acceptance record.
- `op install` never imports; it prints the manifest and every file for reading. `op pack`
  writes a deterministic zip whose comment carries the folder hash, which `op install` checks.
- After a flash, add a `provenance` entry (`kind: "flashed"`) to the manifest; that edit changes
  the folder hash, so record the acceptance again.

### The emulator test

`emu_test.py` defines `scenario(s)`; `s` is a `fireflymcf.emu.scenario.Scenario` on the built
image, not yet booted. `s.boot()`, `s.goto(page)`, `s.tap(control, hold_s=, page=)`,
`s.run(seconds)`, `s.set_tank_raw(sensor, raw)`, `s.g12_selector(selector)`,
`s.set_g12_selector(selector, value)`, `s.frames(dgn=, sa=, since=)`, `s.read_mem(addr, n)`,
`s.screen`, `s.page`, `s.control_box(control)`, `s.screenshot(path)` and `s.image` are its
surface (a name starting with `_` is not); its docstrings are the reference. Anything that raises fails the test with its line.
Write it first and run it on the image without the op: it must fail there. Assert only what the op
changes: stock 13.6 itself sends a D1 config write on selector `0x9B` at boot (*emulator*: observed
in the emulator, not checked against a capture), so "the panel sent nothing" is false without any
op.

## Primitives

`fireflymcf.routinekit` is the surface a user op builds on. It wraps the functions the library's
own routine ops use, so each primitive carries the proof of the op it comes from. **The surface is
semver-stable:** a name, signature or behaviour listed here changes only with a major version.
Anything not listed here (the modules the primitives wrap included) can change in any release.

There are two kinds of name.

- **Primitives**, in `routinekit.PRIMITIVES`. Each is `f(data: bytes, ...) -> (data_out, result)`
  and is deterministic in its arguments. Call one through the editor,
  `ed.call("name", *args, **kwargs)`, which returns `result` and records the call so the engine
  can replay it. Arguments must be JSON-able or `bytes`, so pass lists, not sets or callables. A
  read-only primitive returns `data` unchanged.
- **Helpers**: plain functions you import and call directly. They do not touch an image, or they
  take an `Editor` and go through its primitives.

A primitive that refuses raises `routinekit.RoutineKitError`, a `RecipeError` with a code. The
compile reports it as the op's failure.

### Per-tick routines

Every routine-authoring op in the library runs from the same place (*proven-from-code*, the hook;
*hardware*, the chain). GuiCode calls the per-tick paint function `FUN_00247BF8` from one `bl` at GuiCode+`0xEE3C`. An appended routine takes over
that `bl` and tail-branches to whatever the `bl` reached before, so routines chain: hook → newest
→ … → `FUN_00247BF8`. Every link obeys one contract. It starts with `push {r0, r1, r2, r3, lr}`,
never writes r4-r7, and leaves through `pop.w {r0, r1, r2, r3, lr}` followed by `b.w` to the
previous callee. That `b.w` is the first `b.w` in the routine, because
`followchannel.chain_from_hook` follows it.

Proof (*hardware*): the light→pump follower (`follow_channel`, then `add_option_follower`, PC.45) was the
first grown GuiCode on the coach (2026-08-31). Two chained routines ran on 2026-09-01 (Pump Link
and the AES Low Volts bridge). Every coach build since then carries four or more links. The
library does not check r4-r7 for you.

**`tick_install_plan(data) -> (data, TickPlan)`** (read-only). Returns where the next routine
loads and what it must tail-branch to. `TickPlan` has these fields:

- `code_load_addr`: the GuiCode append point, as a runtime address.
- `tail_target`: the hook's current callee.
- `hook_at`: the hook's GuiCode offset, `0xEE3C`.
- `hook_old`: the hook's current 4 bytes.

It refuses (`BAD_HOOK`) in any of these cases:

- the hook is neither the stock `bl` nor a `bl` into the appended region (`followchannel.hook_state`);
- the append point is below stock 13.6's `0x00259BB4` or not 4-aligned (the rule
  `_check_append_point` applies to every built-in op);
- the GuiCode descriptor maps the section anywhere but `0x00240000`. Every address here uses
  that one base.

**`install_tick_routine(data, blob, entry_offset, note, *, code_load_addr, tail_target) -> (data,
TickInstall)`**. Appends `blob` to GuiCode (`growsection.grow_section`) and points the hook at
`code_load_addr + entry_offset`, after checking that the hook still holds the planned bytes.
`code_load_addr` and `tail_target` are the plan values the blob was assembled against. The call
refuses (`STALE_PLAN`) if the image no longer gives them, which happens when something grew
GuiCode in between. It also refuses (`BAD_ROUTINE`) a blob whose entry breaks the link contract,
and (`BAD_HOOK`) any install after which `chain_from_hook` does not list the new entry first and
every earlier link after it. `TickInstall` has these fields: `load_addr`, `entry_addr`,
`tail_target`, `hook_old`, `hook_new`. This is the same grow, assert and detour sequence as
`add_pump_auto_off` and `add_register_readout`.

**`install_tick(ed, build, note) -> TickInstall`** (helper). Installs a routine in two phases,
because an editor call cannot carry a callable:

```python
plan = ed.call("tick_install_plan")
blob, entry = build(plan.code_load_addr, plan.tail_target)
ed.call("install_tick_routine", blob, entry, note,
        code_load_addr=plan.code_load_addr, tail_target=plan.tail_target)
```

`build(code_load_addr, tail_target)` returns `(blob, entry_offset)`. Only the two calls are
recorded, so replay never runs `build` again.

**Routine helpers** (pure, on a `thumb.Routine`):

- `emit_prologue(r)` emits `push {r0, r1, r2, r3, lr}`.
- `emit_tail(r, tail_target)` emits `pop.w {r0, r1, r2, r3, lr}` and then `b.w tail_target`.
- `assemble_tick(r, code_load_addr, *, prefix=b"") -> (blob, entry_offset)` puts `prefix` at
  `code_load_addr`, padded to 4. The prefix usually holds frame templates, so a template at prefix
  offset k is at runtime address `code_load_addr + k`. The routine follows the prefix and its
  literal pool comes last. `thumb.Routine.assemble` checks every instruction with capstone
  (`thumb.assert_disassembles_to`) before it returns.

`routinekit.thumb` is the assembler (`thumb.Routine`, its encoders, `assert_disassembles_to`).
Every instruction is emitted with the text capstone prints for it, and `assemble` refuses a
mismatch. `r.b(label)` and `r.bcc(cond, label)` write a narrow branch with that text for you
(capstone prints `cc`/`cs` as `blo`/`bhs`; either name is accepted); `r.ldr_lit(rt, name)` loads
a pool word. The encoders cover the T1 loads and stores (`ldr_imm`, `str_imm`, `ldrb_imm`,
`strb_imm`, `ldrh_imm`, `strh_imm`, and `strb_imm12` for `strb.w`), `movs_imm`, `movw`,
`cmp_imm8`, `cmp_reg`, `adds_imm8`, `subs_imm8`, `adds_reg`, `subs_reg`, `ands_reg`, `lsls_imm`,
`lsrs_imm` and `muls`; capstone prints an immediate in decimal below 10 and in hex from 10.
Use `thumb.Routine(pool, error=routinekit.RoutineKitError)`, so an assembly failure is reported
as the op's refusal.

### Sending a frame

**`config_frame(selector, value, function) -> bytes`** (pure). Builds the 12-byte `0x1FED9`
OPTIONS config frame in the template pool's wire order: the u32 little-endian CAN id, then
`FF 96 <selector> 0F <value u16 LE> <function> <trailer>`. `function` is one of these:

| function | meaning | trailer |
|---|---|---|
| `0xD1` | absolute write | `0xEA` |
| `0xD2` | signed delta | `0xEA` |
| `0xD3` | read-back | `0xFF` |

13.6's own read-back records fill the value field with `0xFFFF`. This is
`pumpauto.config_frame`. The D1 payload on selector `0xEE` is *hardware* (`FF96EE0F0100D1EA`,
PC.47). 13.6's own pool carries D2 and D3 records of this shape (*proven-from-code*, Finding 7).

**`emit_send_frame(r, template_addr, *, value_in_r3=False)`** (pure). Emits the host CAN sender
call for the 12-byte frame at `template_addr`. It is the send sequence from `add_pump_auto_off`
(*hardware*: flashed and verified 2026-09-04), instruction for instruction, and a test pins it against the
built routine:

```
sub sp, #0x14
ldr r0, =template
ldr r1, [r0]; ldr r2, [r0, #4]; str r1, [sp]; str r2, [sp, #4]
ldr r1, [r0, #8]; str r1, [sp, #8]
mov r0, sp
ldr r2, =0x2003986C; ldr r2, [r2]
blx r2
add sp, #0x14
```

With `value_in_r3`, it emits `strh r3, [r0, #8]` before the call, so a D1 write carries a word
the routine computed in r3. Keep these points in mind:

- `sub sp, #0x14` keeps sp 8-aligned at the `blx` only after the prologue's five pushed words, so
  call this only between `emit_prologue` and `emit_tail`.
- r0-r3 are dead after the call (AAPCS).
- The call adds the sender pointer and the template address to `r.pool`.

### Registers, slots and SRAM

**`resolve_register(data, *, dgn, instance=None, selector=None, var_index=None, field=None) ->
(data, Register)`** (read-only). Returns the one existing variable-table subscription for `dgn`
(a name in `register.DGN_NAMES`, or a DGN number), narrowed by any of these filters:

- `instance`, for a per-channel DGN;
- `selector`, for `DGN_CONFIG`;
- `var_index`;
- `field`, the entry's source field `raw[3] & 7`, for a DGN whose entries share a filter byte.

`Register.slot_addr` is the halfword the panel's receive filter writes. The primitive refuses
`NO_SUCH_REGISTER` when nothing matches and `AMBIGUOUS_REGISTER` when several entries match.
It wraps `register.resolve_register`, which every follower and readout op uses (*hardware*:
PC.45, PC.59, PC.60). It never appends a subscription.

**`slot_addr(data, var_index) -> (data, int)`** (read-only). Returns the receive slot for
variable-table entry `var_index`. The base is index-split: an index of 255 or less lands at
`0x20007FC8 + 2*i`, and a higher index at `0x20008000 + 2*i` (*proven-from-code*: `FUN_0021C760`
at `0x0021C9B0`). The flat formula was the PC.44 failure (*hardware*). The primitive refuses (`BAD_SLOT`) an index outside the
table.

**`fresh_sram(data, nbytes, *, taken=()) -> (data, int)`** (read-only). Returns a 4-aligned
address of `nbytes` bytes of SRAM that no form `sramreach` traces reaches. It takes the longest
free run first, the allocation `add_register_readout` (tier 3) and `add_voltage_battery` use;
`add_voltage_battery`'s fresh cells were verified on the coach on 2026-09-12 (*hardware*). Keep these points
in mind:

- The scan sees only what the image references. Two cells taken before the routine that uses
  them is installed need `taken=list(range(a, a + n))` on the second call. After the install,
  the routine's pool word makes the first cell referenced.
- Power-on contents are garbage, because the C runtime initialises SRAM only up to `0x2000AD98`
  (*proven-from-code*: the reset handler's `.data` copy and `.bss` zero loops, bounded by the
  literals after them; 13.6). Initialise what you read.

**`find_config_toggle(data, selector) -> (data, int | None)`** (read-only). Returns the group
index of the existing config-toggle group on OPTIONS `selector`, or `None`
(`optionrow.find_config_toggle`, which `add_pump_auto_off` uses to adopt `add_option_follower`'s
Pump Link toggle, PC.45). It refuses (`AMBIGUOUS_TOGGLE`) when several groups qualify, as on a
build carrying `add_pump_auto_off`: the 0xEE ±5 stepper groups match too (*library*).

### Worked hints

- **Find what an earlier op built from the image, not from its params.** A routine's cells are
  chosen at build time (`fresh_sram`), so an `emu_test.py` cannot know them in advance: put a
  short tag in the routine's prefix (`assemble_tick(..., prefix=TAG + cell)`) and have the test
  find it with `find_tag` ([`examples/counter-op`](examples/counter-op/)). A later op can read an earlier one's
  `ctx.handles` instead.
- **A selector shared by several groups: send your own frame.** When one OPTIONS selector is
  shared by several toggle or stepper groups, `find_config_toggle` refuses (`AMBIGUOUS_TOGGLE`).
  Send your own `config_frame` with `emit_send_frame` instead of reusing a group, and read the
  G12's echo (`resolve_register`) rather than writing the register. Found on `0xEE` on builds
  carrying Auto Off (`add_pump_auto_off`), whose ±5 stepper groups match too (*library*).
- **Read what earlier ops left.** An op appended to a project meets the output of the ops before
  it, not stock: compile the project without it and inspect that image (`bus-map`, `options`,
  `slot-source`) before deciding what to expect at a site.

### Readers

User ops may also read images through the modules the primitives come from. Reading is always
safe; only an `Editor` writes. The reader names below are part of the stable surface.

- `routinekit.guicode(data) -> GuiCodeView` gives GuiCode at its runtime base: `load_addr` (the
  runtime address of `body[0]`), `offset` (its file offset), `body` (the section's bytes,
  checksum included), `at(addr, n)` and `u32(addr)` by runtime address. It refuses (`BAD_HOOK`)
  a descriptor that maps GuiCode anywhere but the base every primitive uses.
- `routinekit.find_tag(data, tag) -> int` gives the runtime address of the one occurrence of a
  tag (4 bytes or more) in GuiCode, refusing `NO_TAG` and `AMBIGUOUS_TAG`. Put a tag in a
  routine's prefix and a test or `check` finds the routine's cells by it.
- `routinekit.tick_chain(data) -> list[int]` gives the per-tick chain: each appended routine's
  entry, newest first, ending with the stock paint function.

- `records.object_records(data, obj_offset)` and `records.decode_record(data, off)` decode
  GuiData records. `object_records` takes `gs + Screen.table[i]`, an offset, not an object index;
  an index yields plausible-looking derailed decodes.
- `records.geom_offset`, `color_offset`, `value_slot_offset` and `font_offset` give the file
  offset of one field of a decoded record, which is what an `Editor.patch` needs.
- `relocate.object_table_bounds(data, index)` gives an object's record span.

Growing GuiData (`relocate`) is not a primitive yet. It will be added, with its own test, when a
user op first needs it.
