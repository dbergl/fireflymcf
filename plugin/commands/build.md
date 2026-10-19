---
description: Build a flashable Firefly firmware from the owner's description of the change
argument-hint: "<the change, in the owner's words>"
---

The owner wants this change to their Firefly / Spyder panel:

$ARGUMENTS

Use the `build-firmware` skill and follow it exactly: the owner's own stock `mcf.bin`, one
project compiled from stock, `fireflymcf verify` on the result, and the seven-slot handover.
If the description is empty, ask what they want changed before doing anything else.
