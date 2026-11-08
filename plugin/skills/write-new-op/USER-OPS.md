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
The same holds for its `emu_test.py`, which the emulator gate runs: `verify --emulate
--project`, `emulate --scenario` and `verify --emulate --scenario` refuse (`USER_OPS`) to run a
test file from an op folder without `--allow-user-ops`. The gate checks your op; it does not
sandbox your scenario's code, which can also report its own result. Before running anything
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
| `author`, `description` | string | who wrote it (a name; see below); one line of what it does |
| `touches` | list of strings | every section the op changes: `guicode`, `guidata`, `g12`, `lcd` |
| `written_against` | list of objects | `{build_name, base_sha256, recipe_ops_required}` per base it was written for |
| `provenance` | list of objects | `{kind: "emulated" or "flashed", base_sha256, date, by, note}` |

**`author` travels with the op.** `op pack` puts the manifest in the zip, so whatever `author`
says goes to everyone the op is shared with. Write a name only (`"Dan, written with Claude"`).
Ask the owner before putting an email address, phone number or any other contact detail there,
even one you can see in their files or git configuration.

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
  appends to GuiCode and `grow_guidata(payload, note)` to GuiData ("Growing a section", below);
  `call(name, ...)` runs a primitive; `section(marker)`, `offset_of(marker, addr)` and `data`
  read. Every step lands in a ledger; the engine replays it on the op's input
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
| run the test while writing (fails on stock first) | `fireflymcf emulate MCF --scenario ops/my-op/emu_test.py --allow-user-ops` |
| gate and build | `fireflymcf compile P.ffproj.json STOCK -o build/mcf-NAME.bin --allow-user-ops` |
| record the owner's acceptance | `fireflymcf op accept P.ffproj.json user:my-op [--g12]` |
| verify and emulate | `fireflymcf verify build/mcf-NAME.bin STOCK --emulate --project P.ffproj.json --allow-user-ops` |
| share | `fireflymcf op pack P.ffproj.json my-op -o my-op.zip` |
| receive | `fireflymcf op install P.ffproj.json SRC` (a folder, a `.zip` or a git URL) |

- `compile` and `validate` refuse a project with user ops (`USER_OPS`) without `--allow-user-ops`
  and import nothing. They gate and import only the op folders whose type an enabled op of the
  project names: a folder beside it that the project does not use is never run, never needs an
  acceptance, and, even broken, never blocks the build. So one folder can hold several projects
  that use different ops.
  `--allow-user-ops` admits user ops only; an unverified built-in op still needs
  `--allow-unverified`.
- An op the G12 acts on also needs the owner's recorded acceptance (`op accept --g12`;
  `G12_ACK_REQUIRED` until then). The engine asks for it when the op's `touches` includes `g12`
  (before anything is imported), and also, after the compile and before anything is written,
  when the op changes what the panel can send: a non-empty `capability_delta`, or a
  `(channel, command)` pair of the group and binding tables that its own build added or removed,
  or a tick routine that sends frames of its own. A routine's frames are found in the blob of
  each `install_tick_routine` call: the host sender's pointer (the image's own cell, host API +0x08;
  13.6's is `0x2003986C`; the pool word `emit_send_frame` adds) or a `config_frame` template (CAN id, then `FF 96 <selector> 0F`, with
  D1, D2 or D3), and the refusal names each template's selector and write kind. The record names
  the folder hash, so any edit to the folder voids it.
- **What that detection cannot see.** It is a disclosure prompt, not a sandbox: a routine that
  computes the sender's address in code (`movw`/`movt`) and builds its frame on the stack carries
  neither pattern and is not found. And the table check compares sets of `(channel, command)`
  pairs (`recipe.capabilities`), so re-pointing a binding to a pair the image already sends from
  another control changes neither set: it trips neither the capability gate nor the prompt.
  Give any op whose effect reaches the G12 the `--g12` acceptance whatever the engine asks.
- `verify --emulate --project` runs each op's `emu_test.py` and records the result in the
  build's sidecar under the op's type: `passed`, `failed` with its reason, or `not emulated` with
  its reason (no `emu_test.py`, or a build the emulator does not model: it models 13.6 and 14.7). It
  needs `--allow-user-ops`, because the test is the author's code, but not the G12 acceptance:
  emulation touches no hardware. A failing test is reported; it never stops the image being
  written. A gate failure (expected bytes, replay, touches, `verify`) does.
- The sidecar's `user_ops` names every op the project uses: type, version, folder hash, source,
  touches and the acceptance record.
- `op install` never imports; it prints the manifest and every file for reading. `op pack`
  writes a deterministic zip whose comment carries the folder hash, which `op install` checks.
- **An acceptance is recorded in the project file** (`user_op_acceptance`), so a project file
  received from someone else carries their acceptances, not yours, and with them a build skips
  the `G12_ACK_REQUIRED` question. `fireflymcf ops --project P.ffproj.json` shows each op's
  record ("accepted DATE (G12 included) in this project file"; `accepted` in `--json`). Delete
  every `user_op_acceptance` entry you did not make yourself before building. `op pack` leaves
  acceptance records out of any JSON file in the op folder, and `op install` never takes one in.
- After a flash, add a `provenance` entry (`kind: "flashed"`) to the manifest; that edit changes
  the folder hash, so record the acceptance again.

### The emulator test

`emu_test.py` defines `scenario(s)`; `s` is a `fireflymcf.emu.scenario.Scenario` on the built
image, not yet booted. `s.boot()`, `s.goto(page)`, `s.tap(control, hold_s=, page=)`,
`s.run(seconds)`, `s.set_tank_raw(sensor, raw)`, `s.g12_selector(selector)`,
`s.set_g12_selector(selector, value)`, `s.set_g12_output(instance, level)`,
`s.frames(dgn=, sa=, since=)`, `s.read_mem(addr, n)`,
`s.screen`, `s.page`, `s.control_box(control)`, `s.screenshot(path)` and `s.image` are its
surface (a name starting with `_` is not); its docstrings are the reference. `set_g12_output`
forces an output as a test stimulus, not as a wall switch would: it sends no command, solicits no
ACK and runs none of the G12's rules. Anything that raises fails the test with its line.
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
*hardware*, the chain). The LCD's paint tick calls `GuiCode_entry1`, and entry1's first `bl` calls
the per-tick builder: on 13.6 the `bl` at GuiCode+`0xEE3C` into `FUN_00247BF8`, on 14.7 the `bl` at
GuiCode+`0xF288` into `0x247CAC`. An appended routine takes over
that `bl` and tail-branches to whatever the `bl` reached before, so routines chain: hook → newest
→ … → the builder. Every link obeys one contract. It starts with `push {r0, r1, r2, r3, lr}`,
never writes r4-r7, and leaves through `pop.w {r0, r1, r2, r3, lr}` followed by `b.w` to the
previous callee. That `b.w` is the first `b.w` in the routine, because
`followchannel.chain_from_hook` follows it.

Proof (*hardware*): the light→pump follower (`follow_channel`, then `add_option_follower`, PC.45) was the
first grown GuiCode on the coach (2026-08-31). Two chained routines ran on 2026-09-01 (Pump Link
and the AES Low Volts bridge). Every coach build since then carries four or more links. The
library does not check r4-r7 for you.

**User tick ops run on 13.6 and 14.7.** Nothing here names a firmware's address: the hook, the
chain, the builder it ends in, the append point and the host CAN sender are found in each image
(`hooklocate`, *proven-from-code* on 13.6, 14.7 and 11.23), and the chain limit
(`tick_chain_limit`) counts the links that walk finds. On 14.7 (*emulator*, 2026-11-07,
`tests/emu/test_hook147.py`): one link and two chained links each run once per tick, in step with
the builder's own tick counter, and HOME draws as stock; a link that calls host API `+0x08`'s
cell (`0x20039558`) puts its frame on the bus once, from the panel's address, which settles that
`+0x08` is the frame sender on 14.7's LCD firmware too; the `send_frame` test op compiles on 14.7
and sends. No 14.7 tick routine has run on a coach. Two gaps on 14.7: `fresh_sram` refuses
(`NO_SRAM`), because `sramreach` bounds 13.6's render-state and digit-buffer bases by hand and
14.7's sit 4 bytes higher (`0x200301B4`, `0x200371D5`), so every SRAM word reads as reachable;
and the variant setter (host API `+0x04`) is located but unproven there.

**`tick_install_plan(data) -> (data, TickPlan)`** (read-only). Returns where the next routine
loads and what it must tail-branch to. `TickPlan` has these fields:

- `code_load_addr`: the GuiCode append point, as a runtime address.
- `tail_target`: the hook's current callee.
- `hook_at`: the hook's GuiCode offset, located in the image (13.6 `0xEE3C`, 14.7 `0xF288`).
- `hook_old`: the hook's current 4 bytes.
- `sender_addr`: the host CAN sender's SRAM cell, located in this image (host API +0x08; 13.6
  `0x2003986C`, 14.7 `0x20039558`). Pass it to `emit_send_frame` when you call it outside
  `install_tick`.

It refuses (`BAD_HOOK`) in any of these cases:

- the hook does not prove out (`hooklocate.locate_hook`: entry1's first `bl`, every link's
  prologue and `pop.w; b.w` tail, and a builder that loads the config buffer and has that one
  caller), or the host-API cells cannot be read from `GuiCode_entry0`;
- the append point is not 4-aligned. The image alone cannot say where stock's GuiCode ended;
  the built-in ops also check the append point against the stock image (`_check_append_point`);
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
`add_pump_auto_off` and `add_register_readout`. Each install is one more routine against the
project's `tick_chain_limit` (16 unless the project raises it); compile refuses the op that goes
past it (`TICK_CHAIN_FULL`), so an op should install one routine, not one per item.

**`install_tick(ed, build, note) -> TickInstall`** (helper). Installs a routine in two phases,
because an editor call cannot carry a callable:

```python
plan = ed.call("tick_install_plan")
blob, entry = build(plan.code_load_addr, plan.tail_target)   # emit_send_frame(..., sender_addr=plan.sender_addr)
ed.call("install_tick_routine", blob, entry, note,
        code_load_addr=plan.code_load_addr, tail_target=plan.tail_target)
```

`build(code_load_addr, tail_target)` returns `(blob, entry_offset)`. Only the two calls are
recorded, so replay never runs `build` again. Inside `install_tick`'s `build`, `emit_send_frame`
finds the sender cell itself; in the manual two-phase form you pass `sender_addr=plan.sender_addr`.

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
`lsrs_imm` and `muls`. For the text of an instruction you emit, `thumb.imm_text(v)` spells an
immediate as capstone does (`#9`, `#0xa`, `#-0x10`) and `thumb.mem_text(rn, imm)` a memory
operand (`[r0]` for a zero offset, `[r0, #4]`, `[sp, #8]`, `[pc, #0]`). Other operand forms
(register lists, shifts) follow capstone's own output; run `thumb.disassemble` on the bytes when
unsure. Use `thumb.Routine(pool, error=routinekit.RoutineKitError)`, so an assembly failure is reported
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

**`emit_send_frame(r, template_addr, *, value_in_r3=False, sender_addr=None)`** (pure given the
sender cell: inside `install_tick`'s `build` it is the plan's; outside, pass
`sender_addr=plan.sender_addr`, or it refuses `BAD_ROUTINE`; there is no 13.6 fallback). Emits the host CAN sender
call for the 12-byte frame at `template_addr`. It is the send sequence from `add_pump_auto_off`
(*hardware*: flashed and verified 2026-09-04), instruction for instruction, and a test pins it against the
built routine:

```
sub sp, #0x14
ldr r0, =template
ldr r1, [r0]; ldr r2, [r0, #4]; str r1, [sp]; str r2, [sp, #4]
ldr r1, [r0, #8]; str r1, [sp, #8]
mov r0, sp
ldr r2, =<sender cell>; ldr r2, [r2]        ; 13.6: 0x2003986C, 14.7: 0x20039558
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
- The free runs are holes between the panel's own cells, not one block (`0x20038000` to
  `0x2003B000`): stock 13.6's longest is 44 bytes, and a build already carrying many routine
  ops can be down to under 20 (*library*: `sramreach.free_runs` on such a build). Keep state
  in bytes, not words, and ask for it in one call; `NO_SRAM` means it does not fit.
- Power-on contents are garbage, because the C runtime initialises SRAM only up to `0x2000AD98`
  (*proven-from-code*: the reset handler's `.data` copy and `.bss` zero loops, bounded by the
  literals after them; 13.6). Initialise what you read.

**`find_config_toggle(data, selector) -> (data, int | None)`** (read-only). Returns the group
index of the existing config-toggle group on OPTIONS `selector`, or `None`
(`optionrow.find_config_toggle`, which `add_pump_auto_off` uses to adopt `add_option_follower`'s
Pump Link toggle, PC.45). It refuses (`AMBIGUOUS_TOGGLE`) when several groups qualify, as on a
build carrying `add_pump_auto_off`: the 0xEE ±5 stepper groups match too (*library*).

### Growing a section

**`ed.grow_guicode(payload, note) -> int`** appends `payload` (a multiple of 4 bytes, or it
refuses `BAD_EDIT`) to GuiCode and returns the runtime address of its first byte.

**`ed.grow_guidata(payload, note) -> int`** appends `payload` to GuiData and returns the **panel
address** of its first byte: GuiData's mapped base, `0x90000000` on 11.23, 13.6 and 14.7, plus the
offset. `ed.offset_of("GuiData", addr)` turns it into a file offset, and `patch_addr("GuiData",
addr, ...)` takes it as it is. Keep these points in mind (*library*, pinned by its tests):

- The bytes go where GuiData's checksum word was (section offset `length - 4`; the checksum
  moves after them), padded with zeros to a multiple of 4, so the address is 4-aligned and no
  earlier GuiData byte moves. An empty payload is refused (`BAD_EDIT`).
- It is the GuiData append the library's table-growing ops use (`relocate.relocate`): GuiData's
  descriptor length and the later sections' file offsets grow, every checksum is resealed, and
  the relocator's self-check gates run on the result.
- The step is recorded padded, so replay appends the same bytes. It changes GuiData only:
  declare `guidata` in `touches`. The container header does not change.
- Appended bytes are data nothing reads until something points at them: a record field, a
  table row, a routine's literal.
- They spend GuiData's **tail headroom**. The coverage gate claims every GuiData byte by
  structure; bytes after the last object that it cannot name, zeros included, are bounded by
  the size of the largest object in that table (18,124 bytes left on stock 13.6,
  `coverage.analyse(data, deep=True).tail_headroom`). An append past it is refused
  (`BAD_EDIT`).
- GuiData must end by `0x903F0000` (4,128,768 bytes): the QSPI flash it is written to is 4 MiB
  (*proven-from-code*: FSIZE `0x15` at LCD `0x00216E0E`) and its top erase block, *inferred* to
  be 64 KB, holds the configurator's files. Every grow and every compile checks it; past it
  the call refuses `GUIDATA_TOO_LARGE` and nothing is written.

### Colour cells

A colour field in **mode 7** names a row of the display-cell table, and the row holds an SRAM
address: the renderer paints the u32 it finds there as RGB888 `0x00RRGGBB` (*proven-from-code*:
the theme-init in `FUN_00247BF8` stores such words into these cells, Finding 18). A field in
mode 2 (white), 3 (an inline colour) or 6 (a palette entry) is a constant. So a control whose
colour should change at run time needs its field in mode 7 on a row nothing else names. Most
rows are shared: on stock 13.6 every tank well's interior names row 161, the button colour.

**`own_colour_cell(data, object_index, field, *, record=None, expect=None, repaint=False) ->
(data, ColourCell)`** gives one record's `field`, `"border"` or `"fill"`, its own row:

- `object_index` is an object-table index (`fireflymcf labels`, `bus-map`, `records`), never a
  screen id. `record` is the record's index in the object's record list; without it the object
  must have exactly one record with that field (`AMBIGUOUS_RECORD` names the candidates).
- Fields by widget type (*proven-from-code*, `FUN_00256C54`'s box dispatch, Finding 28): wtype
  3 (rounded box) and 32 (tank well) draw the border in fillA and the interior in fillB; wtype
  33 (a solid panel, button or level fill) draws one rect in fillA, its `"fill"`. Other types
  are refused (`UNSUPPORTED_FIELD`).
- `expect` is the record's bytes as you wrote the op against them; other bytes refuse
  `FIRMWARE_MISMATCH`.

It rewrites the field to mode 7 (a mode-2 field grows its record by 2 bytes and the object is
swapped back in at its own index; every other byte of every record stays as it was) and grows
the cell table by one row on 4 fresh bytes of SRAM. The grow is the one `add_voltage_battery`
uses for its colour word (*hardware*: flashed and verified 2026-09-12). It copies the table to
the end of GuiData and repoints the GuiCode literals that load it, so **declare both `guicode`
and `guidata`** in `touches`. Each call adds its own row and its own SRAM word, and later calls
and `fresh_sram` avoid that word. Every placement of the object shares the cell: an object
drawn on two pages changes on both.

How the panel reads the row (*proven-from-code*, 13.6 GuiCode `0x2533B2`-`0x2533D6` in the
record decoder `FUN_00252CB0`): it clamps the u16 row (`cmp.w r0,#0x298`; `bls`, else row 0),
loads the row's pointer (`ldr.w r3,[DAT_002535B4,r0,lsl #2]`), takes the default colour
literal at `0x2535AC` when the pointer is null (`cbnz`), and otherwise reads the u32 at it
(`ldr r2,[r3]`). It never reads the type table on this path. The new row's type byte is 10,
which matters only to the library's own SRAM reach (`sramreach`, where type 10 is 4 bytes wide).

**`own_colour_cells(data, requests, *, repaint=False) -> (data, [ColourCell, ...])`** does the
same for several fields with **one** grow. A request is `[object_index, field]`,
`[object_index, field, record]` or `{"object": ..., "field": ..., "record": ..., "expect": ...,
"repaint": ...}` (a request's `repaint` overrides the call's). The cells are on consecutive
rows, in request order. Every request is checked before anything changes, one refusal refuses
them all, and the same field twice is `BAD_EDIT`. Prefer it whenever an op drives more than
one field: each grow copies the whole table (about 5 bytes a row, some 3.3 KB on 13.6) to the
end of GuiData. The copies are claimed by the coverage gate as tables, so they do not spend
GuiData's tail headroom, but they do spend flash.

`ColourCell` has these fields:

- `addr`, `width` (4) and `encoding` (`"rgb888"`): store the colour with `str` as `0x00RRGGBB`.
- `cell_index`: the new row.
- `before` (`(r, g, b)`), `before_word` (`0x00RRGGBB`) and `before_mode`: the colour the field
  drew before, read from the image as it stands, after every earlier op. A mode-7 field's
  colour is known only when the theme-init sets its row (`theme.mode7_palette`); otherwise
  `before` is `None`.
- `before_cell_addr`: for a field that named a row before (mode 7), that row's SRAM word. Copy
  it at run time to follow whatever writes it.
- `object_index`, `record`, `role` (`"fillA"` or `"fillB"`), and `initialised_at_boot`, which
  is always `False`.
- `repaint`: with `repaint=True`, the `Repaint` that binds the cell for repainting (below);
  otherwise `None`.

It refuses these (`RoutineKitError` codes):

| code | when |
|---|---|
| `BAD_OBJECT` | the index is outside the object table |
| `NO_COLOUR_FIELD` | no record of the object has the field, or `record` is out of range |
| `AMBIGUOUS_RECORD` | several records have it; pass `record=` |
| `UNSUPPORTED_FIELD` | the widget type has no such field, or its colour-mode bytes gate it off |
| `UNSUPPORTED_MODE` | the field inherits (mode 0, 4 or 5) or is the theme default (mode 1): there is no colour to restore |
| `STATEFUL_RECORD` | the record watches a value slot, so its on/off state picks the fillA2/fillB2 pair (Finding 28) |
| `INHERITED_COLOUR` | a later record of the object takes this field's colour (the colour state persists from record to record, Finding 29), or draws a child before setting it again, or the object ends before any record sets it again (whatever the page draws next could inherit it) |
| `FIRMWARE_MISMATCH` | `expect` differs from the record |
| `NO_DISPLAY_CELL`, `BAD_EDIT` | the table could not grow, or a structural gate failed; a relocation failure names GuiData's tail headroom |
| `GUIDATA_TOO_LARGE` | GuiData would end past `0x903F0000`, the flash ceiling ("Growing a section") |

With `repaint=True` it also refuses as `repaint_on_cell` does ("Repainting"), before anything
changes.

**The cell holds garbage at power-on.** The C runtime initialises SRAM only up to `0x2000AD98`,
and the cell is above it. Your op must install a tick routine of this shape:

1. at entry, **unconditionally** store `before_word` (or the word at `before_cell_addr`) into
   `addr`;
2. then, when your condition holds, store the new colour over it.

So the cell is written on every tick whatever path the routine takes. That is enough for the
control never to draw garbage (*inference*, not checked on the coach): the tick chain runs
before `FUN_00247BF8`, whose theme-init is the only writer of the stock chrome cells, which sit
in the same uninitialised SRAM, so no page is drawn before the chain first runs. In the emulator
a cell seeded with magenta before the first tick never draws magenta, and the outline has the
routine's colour in the first screenshot of HOME (*emulator*).

**A store alone repaints nothing; `repaint=True` makes it repaint.** Between page draws the
panel repaints a control only through its live redraw, which re-checks the records it enrolled
when it drew the page. The field `own_colour_cell` rewrites is inside the object; what the
live redraw watches is the enrolled container that draws the object. Without `repaint` the new
colour shows at the control's next repaint: when the value its container watches changes (a
tank gauge's level) or its page is drawn. That can be never: on the coach a tank gauge reads 0 %
from 54000 raw upward (*hardware*: PC.10), so a colour driven from the raw reading above that
changes no percentage and the outline stays stale. Pass `repaint=True` (next section).

**Theme.** `before` is read when your op runs. Put the op after `set_theme` in the project, so the
colour you restore is the themed one; a header rename can still come last.

**Worked pattern: a tank gauge's outline turns red when the tank is low.** On stock 13.6 the
gauges are the objects whose percent readout a `TANK_STATUS` (`0x1FFB7`) receive entry paints and
whose first record is a wtype-32 well: `fireflymcf bus-map MCF --dgn 0x1FFB7` names them (fresh
721, black 719, grey 723, grey 2 725). The well's border is fillA in mode 2, white. The receive
slot holds the level in its low byte and the resolution in its high byte.

```python
cell = ed.call("own_colour_cell", 721, "border")             # manifest: guicode, guidata
reg = ed.call("resolve_register", dgn=0x1FFB7, instance=0)   # fresh

def build(code_load_addr, tail_target):
    r = thumb.Routine({"slot": reg.slot_addr, "cell": cell.addr, "red": 0xFF0000,
                       "restore": cell.before_word}, error=routinekit.RoutineKitError)
    routinekit.emit_prologue(r)
    r.ldr_lit(0, "cell")                                        # 1. every tick: the old colour
    r.ldr_lit(1, "restore")
    r.emit(2, lambda h, s: (thumb.str_imm(1, 0, 0), "str r1, [r0]"))
    r.ldr_lit(0, "slot")
    r.emit(2, lambda h, s: (thumb.ldrh_imm(1, 0, 0), "ldrh r1, [r0]"))
    r.emit(2, lambda h, s: (thumb.lsls_imm(2, 1, 24), "lsls r2, r1, #0x18"))
    r.emit(2, lambda h, s: (thumb.lsrs_imm(2, 2, 24), "lsrs r2, r2, #0x18"))   # level
    r.emit(2, lambda h, s: (thumb.lsrs_imm(3, 1, 8), "lsrs r3, r1, #8"))       # resolution
    r.emit(2, lambda h, s: (thumb.movs_imm(0, 10), "movs r0, #0xa"))
    r.emit(2, lambda h, s: (thumb.muls(2, 0), "muls r2, r0, r2"))
    r.emit(2, lambda h, s: (thumb.cmp_reg(2, 3), "cmp r2, r3"))
    r.bcc("hi", "done")                                         # above 10 %: keep it
    r.ldr_lit(0, "cell")                                        # 2. low: red over it
    r.ldr_lit(1, "red")
    r.emit(2, lambda h, s: (thumb.str_imm(1, 0, 0), "str r1, [r0]"))
    r.label("done")
    routinekit.emit_tail(r, tail_target)
    return routinekit.assemble_tick(r, code_load_addr)

routinekit.install_tick(ed, build, "fresh low outline")
```

The library runs this routine in the emulator: the outline is white at 100 %, red at 0 %, white
again at 33 % (the emulated G12 moves a tank one 33 % step every 10 s), never the magenta seeded
into the cell before the first tick, and the other gauges' outlines never change (*emulator*).
The `0xFFFF` the slot holds before any report reads as above 10 %. A pool word and a label cannot
share a name. For several gauges, take every cell in one `own_colour_cells` call and drive them
from one routine.

### Repainting

**`repaint_on_cell(data, object_index, cell_index) -> (data, Repaint)`** makes object
`object_index` repaint in place whenever the u32 at display-cell row `cell_index` changes.
`own_colour_cell(..., repaint=True)` is `own_colour_cell` and then this call on the new cell's
row, so **one store of a new colour repaints the control**. Use it whenever the colour must show
without waiting for the control's own value to change.

How the panel does it (*proven-from-code*, 13.6 GuiCode, `GUICODE.md` Finding 87). Every tick,
after the tick chain (`GuiCode_entry1`: `0x24EE3C` runs the chain, `0x24EEB8 bl 0x2588A8` the live
redraw), `FUN_002588A8` asks `FUN_002568A0` of each enrolled widget whether it changed. That makes
four change-detect reads (`FUN_00256868`, at `0x2568F2`, `0x256910`, `0x256920`, `0x25693A`) on the
widget's own record's mode-7 colour rows, comparing each row's u32 with the word it last saw, and
on a change draws the widget again from its saved render state. A widget is a record with the
enrol bit (header byte 4 bit `0x20`; `0x256C98 tst.w r3, #0x20`), and the rows are the ones its
own colour fields named when it was enrolled (`0x2533D8 strh.w r0, [r3, #0x74]` for fillA). An
object is drawn by the wt-5 containers whose `child_index` names it, and an enrolled container
redraws its whole child. `repaint_on_cell` gives each enrolled container of the object a mode-7
field on your row.

What it rewrites, and why nothing else changes:

- Only a container field in **mode 0** (it set no colour) that the object's **first record sets
  itself** (mode 1, 2, 3, 6 or 7). The word sits in the render state from the container's decode
  until that first record overwrites it, and nothing draws in between, so the container's copy
  reaches no pixel. A container whose coordinates store to registers is refused (the decoder
  copies colour words into registers there too). Only **fillA**, then **fillA2**: the wt-5
  case fills the container's box with its fillB or fillB2 word when the renderer's `+0x704C`
  flag is set (`0x257AFC bl 0x255F44`), and never reads fillA or fillA2 (*proven-from-code*,
  Finding 87). So an object has at most two watchable fields, and on the tank gauges one
  (their first record sets fillA but not fillA2): watch **one** cell per object, the one that
  changes whenever the control's look does (or an `own_repaint_trigger` the routine bumps),
  and write the object's other cells in the same tick. A second bind on such an object refuses
  `UNSUPPORTED_CONTAINER`.
- That nothing after the child sees the word (the parent's later records) rests on the child's
  walk not ending before its first record decodes: *inference*, not traced in
  `FUN_00257BFC`. In the emulator a full compose of HOME with the alarm word set draws exactly
  what the unbound op's compose draws, which is consistent with it but would not catch every
  leak (HOME's compose does not take the `+0x704C` box fill).
- Each parent object's container record grows by 2 bytes and the parent is swapped back in at
  its own index, every other byte of every record unchanged; later GuiData moves, so GuiCode's
  literals into it are shifted: **declare `guicode` and `guidata`**.
- No slot, no receive entry, no cell another widget reads is written: the tank reading and every
  other real value are what they were. The repaint draws the object from its own records, as a
  level change does.

`Repaint` has `object_index`, `cell_index`, `addr` (the row's SRAM word), `watchers` (one
`Watcher(parent, record, role)` per bound container; every placement of the object is bound, so
an object drawn on several pages repaints on each) and `skipped` (containers of the object
without the enrol bit; a page drawing the object through one shows a change only at its next
draw).

**`own_repaint_trigger(data, object_index) -> (data, RepaintTrigger)`** is a private trigger: a
new display-cell row on 4 fresh bytes of SRAM (the same grow as `own_colour_cell`), bound by
`repaint_on_cell`. A routine that stores a word different from the last tick's (add one to it)
repaints the object once. `RepaintTrigger` has `addr`, `width` (4), `cell_index`, `repaint` and
`initialised_at_boot` (`False`: the first store after power-on may repaint once). Touches GuiCode
and GuiData.

Both refuse (`RoutineKitError` codes):

| code | when |
|---|---|
| `BAD_OBJECT` | the index is outside the object table |
| `BAD_CELL` | the row is outside the display-cell table, or its pointer is null |
| `NO_LIVE_CONTAINER` | no enrolled wt-5 container draws the object: nothing on the live redraw can repaint it |
| `CHILD_INHERITS` | the object's first record sets no colour field itself, so a container's field would reach what it draws |
| `UNSUPPORTED_CONTAINER` | a container has no mode-0 field the object's first record sets (all taken, or it sets colours itself), or stores coordinates to registers |
| `BAD_EDIT`, `NO_DISPLAY_CELL`, `GUIDATA_TOO_LARGE` | a structural gate failed, the trigger's table could not grow, or GuiData would end past the flash ceiling |

On stock 13.6, 31 objects can be bound, among them the four tank gauges (719, 721, 723, 725)
(*library*: `repaint.plan` over every object).

**The routine shape: store, overwrite, repaint on change.** With `repaint=True` the routine is the
one above and nothing more:

1. every tick, store `before_word` into `cell.addr` (the theme's colour, never garbage);
2. when the alarm holds, store the alarm colour over it;
3. there is no step 3: the live redraw runs after the chain and sees only the word the routine
   left, so it repaints exactly in the tick where that word differs from the last tick's. The
   two stores of one tick are never seen apart (*proven-from-code*: one function,
   `GuiCode_entry1`, runs the chain and then the redraw).

To repaint on some other decision, add one to an `own_repaint_trigger`'s word in the tick where
you decide (`ldr r1, [r0]; adds r1, #1; str r1, [r0]`).

**Worked pattern: the outline turns red on a raw reading the gauge does not show.** The owner
asks for FRESH's outline to go red once the tank's raw reading has stayed above 62000 for 10 s,
and back once it has stayed below 58000 for 10 s. "62000" is not a percentage: it is the raw
16-bit ADC count the G12 reports on `0BFC1` (`G12_TANK_LEVEL_SENSOR`, instance 1 fresh, u16 at
byte 1), which the hidden PROBE TANK DIAGNOSTICS page shows as "ADC Raw Value"
(`resolve_register(dgn=0x0BFC1, instance=1)`). The gauge reads 0 % from 54000 up, so no level
change ever repaints it there:

```python
reg = ed.call("resolve_register", dgn=0x0BFC1, instance=1)        # raw fresh ADC
cell = ed.call("own_colour_cell", 721, "border", repaint=True)   # guicode, guidata
st = ed.call("fresh_sram", 8, taken=list(range(cell.addr, cell.addr + 4)))
# routine: str before_word -> cell; hold-timed hysteresis on [reg.slot_addr] (state in st,
# the ms clock at [r4, #0x204]); while alarmed, str 0xFF0000 -> cell. Nothing else.
```

The library runs this op in the emulator (`tests/test_repaint.py`, *emulator*): with FRESH at
56000, then 63000, then 57000 raw (0 % throughout) and HOME never left, the outline turns red 10 s
after the reading passes 62000 and white again 10 s after it drops below 58000, and no pixel
outside the gauge changes. The same op without `repaint` writes the same words at the same ticks
and HOME never shows them. Not yet seen on the coach.

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

`ed.grow_guidata` appends to GuiData, and `own_colour_cell` and `own_repaint_trigger` grow the
cell table; growing any
other GuiData table (inserting inside the section) is not a primitive yet. It will be added, with
its own test, when a user op first needs it.
