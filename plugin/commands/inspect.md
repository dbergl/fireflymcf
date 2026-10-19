---
description: Read an owner's Firefly mcf.bin and summarise what the panel holds and what can be changed
argument-hint: "[path/to/mcf.bin] [floorplan]"
---

Inspect the Firefly MCF firmware at `$ARGUMENTS` (if no path is given, look for `mcf.bin` in the
current directory, then ask). Everything here only reads; write scratch files under `work/`.

1. `sha256sum` the file, then `fireflymcf doctor FILE --json` and `fireflymcf pages FILE`.
2. Floorplan: use the one given, else a `coach.json` in the directory, else ask the owner. If
   they don't know, say renders will assume none. Write it with
   `fireflymcf coach-config FILE -o coach.json --floorplan CODE [--option NAME]...`
   (`fireflymcf options FILE` ends with this build's codes).
3. Render HOME, ELECTRICAL and LIGHTS with `--config coach.json --placements`, and look at each
   PNG.

Report to the owner, plainly, in this order: the build name and SHA-256; the firmware version
(the section names carry it, e.g. `Lyra 13v6`); the floorplan used; each page with its main
controls as the owner would name them; what `doctor` says is safe to change on this build; and
anything that refuses here (13.6-only ops on another build). End by asking what they would like
changed. Change nothing.
