# `fireflymcf-tankpatch` — freeing an RV-C tank instance from the Firefly G12

A standalone patcher that stops a Firefly G12 broadcasting `1FFB7` TANK_STATUS for tanks you
choose, so a different RV-C tank sender can own those instances. It edits an MCF file you
supply and writes a new one; it never touches the bus, the coach, or the original file.

```
fireflymcf-tankpatch mcf.bin                                  # report only
fireflymcf-tankpatch mcf.bin --all-water -o mcf-notank.bin    # the usual case
fireflymcf-tankpatch mcf.bin --fresh --grey -o mcf-notank.bin # pick tanks individually
```

Run it with no tank flags first. That prints what your image broadcasts and the build audit,
and writes nothing.

## Why the G12 has to be silenced first

With no probe wired to a tank input, the G12's ADC floats to rail (`0xFFF0`). That is above
every threshold in the `CC`/`CD`/`CE` bucketing ladder, so the ladder reports the bottom bucket
and the G12 broadcasts a perfectly well-formed TANK_STATUS frame reading **0%**, once a second,
forever. Nothing in RV-C distinguishes that from a real empty tank.

Adding your own sender does not fix it. The Firefly panel's receive filter matches the 18-bit
DGN and a masked payload compare — **nothing about who sent the frame**. Two senders on one
instance therefore do not resolve; they alternate at 1 Hz, and the gauge flickers between them.
This project watched exactly that failure on a different DGN (`BUS-BASELINE.md` PC.46, where a
second device answering `0x1FFC6` made the panel draw the alternator regulator's battery type
on a lithium coach). Silencing the G12 is a precondition, not a tidy-up.

## What it changes

Two bytes per tank, and nothing else.

The G12 section carries a rule engine: 151 rule records over four tables, driven by a small
Thumb-1 program in table `0x67`. Each periodic broadcast is one 12-byte record that some
instruction switches on. The patch replaces that instruction — a `blx r3` through the engine's
activate vtable slot — with a 2-byte `nop`.

The record is left intact. The image still *describes* the broadcast, the file length is
unchanged, nothing shifts, and the revert is one byte-pair per tank. The edit is confined to
the G12 section, which the bootloader can flash on its own, making this the lowest-risk build
shape this project has.

Deliberately untouched: `0BFC1` (G12_TANK_LEVEL_SENSOR, the raw ADC the `PROBE TANK
DIAGNOSTICS` page reads) and `1AAB7` (Firefly's private TANK_STATUS mirror). Both are
proprietary DGNs that collide with nothing standard. The LPG tank (instance 3) usually has a
real sender, so `--all-water` never includes it; `--lpg` will if you want it.

## What happens on the coach

Verified 2026-09-07 on a Jayco B-Van with a Firefly G12 and a 7" Spyder panel, firmware 13.6
(`BUS-BASELINE.md` PC.62):

- A 12-second capture after the flash carried **zero** `19FFB7` frames. Every earlier capture
  carried about 48.
- `18BFC19C` and `19AAB79C` kept running, unchanged — the diagnostics page still works.
- A foreign RV-C sender at source address `0xAF` then drove the panel's **stock** gauge:
  `19FFB7AF#002238FFFFFFFFFF` (34 of 56) read **61%** on HOME's FRESH gauge, and a 13-of-100
  frame read **13%**. No GuiData or GuiCode edit anywhere in the path.
- Tanks nobody claimed read the `--%` no-data sentinel, not a stale 0%.

One correction that fell out of the same session, worth knowing if you have read otherwise: the
panel does **not** quantise tank level to quarters. It computes `relative_level / resolution`
and shows the percent. The quarters everyone sees are an artefact of the G12 being the only
publisher the panel had ever had — its bucketing ladder emits nothing else.

## The no-patch alternative, and why it is not equivalent

On 13.6 the black tank's broadcast is gated on `cfg[0xEC]`, the **Black Tank** OPTIONS row,
re-read every tick. So instance 1 alone can be silenced over the bus with no patch at all:

```
cansend can1 19FED99F#FF96EC0F0000D1EA      # black tank option off  -> broadcast stops
cansend can1 19FED99F#FF96EC0F0100D1EA      # back on
```

That lever also **hides the HOME BLACK gauge**, which the patch does not — so it is a different
change, not a cheaper one. Instances 0 and 2 have no config gate anywhere in the blob and are
patch-only. The tool's report marks which of your tanks are gated.

## Which firmware it works on

Validated on 11.23, 13.6 and 14.7 — three builds that are structurally different from each
other. 11.23 has no LPG rule at all and gates nothing; 13.6 gates two tanks; 14.7 gates all
five. The rule indices differ on every build.

Nothing in the tool is a hardcoded offset. The tanks come out of rule table `0x62` by DGN, and
the instruction that switches each one on is found by scanning for its idiom at the instruction
boundaries the project's own recursive-descent walker established. That is why it lands on all
three despite their differences, and it is the reason to expect it to land on a build nobody
here has seen.

**But a scan is not a table read**, so before patching an unfamiliar image the tool runs an
audit and refuses unless it passes:

| signal | what it checks |
|---|---|
| `rule-shape` | every `1FFB7` record carries the fixed TANK_STATUS fields — kind `0x81` (1 Hz periodic), mask `0x02` |
| `known-records` | the 12-byte records are byte-identical to 11.23/13.6/14.7, which they are for every instance those builds ship |
| `activation-sites` | each rule is switched on by exactly one instruction, or by none (already suppressed) |
| `activate-slot` | **derived, not assumed**: the one vtable slot every live tank rule is called through |
| `slot-plurality` | that same slot is the blob's most-called engine slot, derived independently of the tanks |
| `blob-coverage` | the walker claimed enough of the blob for the scan to mean anything (corpus floor 92.3%) |

`--force` patches past a failed audit. Read the report before using it: a wrong edit here is a
wrong instruction in the G12's own program.

If it refuses on your image, the printed report is what to send to the maintainer. It names the
signal that failed and the evidence behind it.

## Flashing, and getting back

The output must be named `mcf-*.bin` — the bootloader lists nothing else — and the tool refuses
any other name unless you pass `--any-filename`. By default it also appends `No Tank Bcast` to
the **build name** in the header, because the boot menu lists images by that string and not by
filename; two files sharing a name are indistinguishable at the one moment picking the wrong
one is expensive. `--name` sets it outright, `--no-rename` leaves it alone.

Flash it the way you would a vendor image: USB stick, power cycle. **To revert, flash the image
you started from — so keep it.** The patch is same-size and self-contained, but the tool cannot
reconstruct the original instruction from a patched file, because the instruction it removed is
the very thing the scan looks for.

## After the flash

Your sender should publish `1FFB7` for the instances you freed. The panel keeps subscribing to
instances 0/1/2/3/18 and none of those decodes is range-fenced, so the stock gauges pick it up
with no further edit. Instance numbering is RV-C's: 0 fresh, 1 black, 2 grey, 3 LPG, 18 second
grey.

## Holding the G12 section on its own, and reading the device over the bus

`--extract-g12 FILE` writes out the **`G12` section by itself** — stock if you named no tank,
patched if you did:

```
fireflymcf-tankpatch mcf.bin --extract-g12 stock.g12                     # the section as shipped
fireflymcf-tankpatch mcf.bin --all-water --extract-g12 patched.g12       # the section after the patch
```

That section is the unit the *device* consumes. The panel's boot-menu `G12` row is not a USB
write at all: it is an **RV-C upload**, in which the G12 asks the panel for eight bytes at a
time and the panel serves them off the stick. The stick carries the file; the bus carries the
section. **A bare section is therefore not flashable by the bootloader** — that menu lists whole
images named `mcf-*.bin` — so use `-o FILE` for something you can flash.

`fireflymcf-g12flash` reads the device that consumes it, and **writes nothing**:

```
fireflymcf-g12flash probe --interface can1               # listen only; transmits NOTHING
fireflymcf-g12flash probe --interface can1 --request     # + one REQUEST frame, for the parameter values
```

It reports the G12's upload targets — application, config, bootloader — with their versions,
load addresses and identifiers, including the `ConfigType` / `ConfigID` / `ConfigRev` that say
**which** config the device is holding. That is how you check an MCF you have is the one it
wants.

**There is no way to read a G12's config back off it.** The protocol has no verb for it: the
loader's three commands all push, and there is nowhere in it for received bulk data to land. So
a G12's current config cannot be backed up before it is changed, and **the MCF you are running
is the only backup that exists** — which is the real reason to keep it.

## Where the mechanism is written up

`docs/G12-CODE.md`, "The per-rule broadcast switch" — the vtable slots, the rule-#93 oracle that
pins which slot is which, and the limit of the claim. `src/fireflymcf/tankbroadcast.py` is the
library; this tool is a front end to it.
