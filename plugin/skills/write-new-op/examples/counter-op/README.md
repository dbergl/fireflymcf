# counter-op (`user:tick-counter`)

A worked example for the `write-new-op` skill: the smallest complete user op. It appends one
per-tick routine to GuiCode that adds `step` (default 1) to a 4-byte cell in fresh SRAM every
time the panel's GuiCode ticks. It draws nothing and sends nothing.

**Do not flash it.** It is a template. It shows the shape every routine op has:

- `manifest.json`: the type (`user:` prefix), `touches: ["guicode"]` (the engine refuses an op
  that changes a section it does not declare), `written_against` and `provenance` as objects.
- `op.py`: a `UserOp` whose `build(ed, ctx)` writes only through the `Editor`:
  `ed.call("fresh_sram", 4)` for the cell, `routinekit.install_tick` for the two-phase install
  (plan, assemble, install), the link contract (`emit_prologue` / `emit_tail`), and a 4-byte tag
  in the routine's prefix so the test can find the cell in the built image. `check(data, ctx)`
  proves the routine is on the tick chain of the finished image and returns the manifest line.
- `emu_test.py`: `scenario(s)` boots the build in the emulator, asserts the cell rises across
  10 s of simulated time and that the panel still navigates to SETTINGS.

Use it in a project by copying the folder to `ops/counter-op/` beside the `*.ffproj.json` and
adding `{"id": "count", "type": "user:tick-counter", "params": {"step": 1}}` to `ops`. Then:

```bash
fireflymcf compile P.ffproj.json mcf.bin -o build/mcf-counter.bin --allow-user-ops
fireflymcf verify build/mcf-counter.bin mcf.bin --no-render --emulate --project P.ffproj.json --allow-user-ops
```

Written against stock 13.6 (the only build the emulator boots). It needs nothing from earlier ops.
