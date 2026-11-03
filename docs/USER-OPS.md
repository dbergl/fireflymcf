# User ops

A user op is a recipe op you write yourself: a folder with a `manifest.json` and an `op.py` whose
`UserOp` subclass changes the image only through a `userop.Editor`. This page is the reference for
the building blocks such an op has. The authoring guide comes later.

**Trust.** A user op runs with the same trust as any Python you run: importing its `op.py`
executes it, with your permissions, before any check runs. The engine replays the op's `Editor`
ledger against its input and refuses an image the ledger does not account for. That replay is a
correctness gate. It is not a sandbox, and it cannot stop an op from doing anything else Python
can do. Read an op before you allow it (`--allow-user-ops`).

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

Every routine-authoring op in the library runs from the same place. GuiCode calls the per-tick
paint function `FUN_00247BF8` from one `bl` at GuiCode+`0xEE3C`. An appended routine takes over
that `bl` and tail-branches to whatever the `bl` reached before, so routines chain: hook → newest
→ … → `FUN_00247BF8`. Every link obeys one contract. It starts with `push {r0, r1, r2, r3, lr}`,
never writes r4-r7, and leaves through `pop.w {r0, r1, r2, r3, lr}` followed by `b.w` to the
previous callee. That `b.w` is the first `b.w` in the routine, because
`followchannel.chain_from_hook` follows it.

Proof: the light→pump follower (`follow_channel`, then `add_option_follower`, PC.45) was the
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
`pumpauto.config_frame`. The D1 payload on selector `0xEE` is hardware-observed (`FF96EE0F0100D1EA`,
PC.47). 13.6's own pool carries D2 and D3 records of this shape (`GUICODE.md` Finding 7).

**`emit_send_frame(r, template_addr, *, value_in_r3=False)`** (pure). Emits the host CAN sender
call for the 12-byte frame at `template_addr`. It is the send sequence from `add_pump_auto_off`
(flashed and verified 2026-09-04), instruction for instruction, and a test pins it against the
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
It wraps `register.resolve_register`, which every follower and readout op uses (PC.45, PC.59,
PC.60). It never appends a subscription.

**`slot_addr(data, var_index) -> (data, int)`** (read-only). Returns the receive slot for
variable-table entry `var_index`. The base is index-split: an index of 255 or less lands at
`0x20007FC8 + 2*i`, and a higher index at `0x20008000 + 2*i` (`FUN_0021C760` at `0x0021C9B0`).
The flat formula was the PC.44 failure. The primitive refuses (`BAD_SLOT`) an index outside the
table.

**`fresh_sram(data, nbytes, *, taken=()) -> (data, int)`** (read-only). Returns a 4-aligned
address of `nbytes` bytes of SRAM that no form `sramreach` traces reaches. It takes the longest
free run first, the allocation `add_register_readout` (tier 3) and `add_voltage_battery` use;
`add_voltage_battery`'s fresh cells were verified on the coach on 2026-09-12. Keep these points
in mind:

- The scan sees only what the image references. Two cells taken before the routine that uses
  them is installed need `taken=list(range(a, a + n))` on the second call. After the install,
  the routine's pool word makes the first cell referenced.
- Power-on contents are garbage, because the C runtime initialises SRAM only up to `0x2000AD98`.
  Initialise what you read.

**`find_config_toggle(data, selector) -> (data, int | None)`** (read-only). Returns the group
index of the existing config-toggle group on OPTIONS `selector`, or `None`
(`optionrow.find_config_toggle`, which `add_pump_auto_off` uses to adopt `add_option_follower`'s
Pump Link toggle, PC.45). It refuses (`AMBIGUOUS_TOGGLE`) when several groups qualify, as on a
build carrying `add_pump_auto_off`: the 0xEE ±5 stepper groups match too.

### Readers

User ops may also read images through the modules the primitives come from. Reading is always
safe; only an `Editor` writes. The reader names below are part of the stable surface.

- `records.object_records(data, obj_offset)` and `records.decode_record(data, off)` decode
  GuiData records. `object_records` takes `gs + Screen.table[i]`, an offset, not an object index;
  an index yields plausible-looking derailed decodes.
- `records.geom_offset`, `color_offset`, `value_slot_offset` and `font_offset` give the file
  offset of one field of a decoded record, which is what an `Editor.patch` needs.
- `relocate.object_table_bounds(data, index)` gives an object's record span.

Growing GuiData (`relocate`) is not a primitive yet. It will be added, with its own test, when a
user op first needs it.
