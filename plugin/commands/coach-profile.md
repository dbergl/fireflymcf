---
description: Record the coach's floorplan and fitted options so every render matches the real panel
argument-hint: "[candump.log | floorplan code]"
---

Write `coach.json` beside the owner's stock `mcf.bin`: the floorplan and options that decide what
each page draws. Input: `$ARGUMENTS`.

- **A `candump -L` log of the RV-C bus** (10 s or more): the coach's own broadcast settings.
  `fireflymcf coach-config mcf.bin -o coach.json --capture LOG`. Best: nothing is guessed.
- **A floorplan code and option names**: `fireflymcf coach-config mcf.bin -o coach.json
  --floorplan CODE --option "NAME" ...`. `fireflymcf options mcf.bin` lists the build's codes
  and option names; the panel shows the active ones under Settings → OPTIONS and FLOORPLAN
  SELECTION.
- **Neither**: ask the owner which they can provide, and explain that the floorplan decides
  which controls HOME shows.

The command refuses a floorplan or option the build does not have; report its message rather than
guessing a near match. Then render HOME with `--config coach.json` and ask the owner whether it
matches their panel. If it doesn't, the profile is wrong; fix it before building anything.
