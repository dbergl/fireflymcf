"""Compact AES ELECTRICAL frame -- rebuild object 829 as a FAITHFUL STRUCTURAL MIRROR of the AGS
compact frame (object 830), replacing the inline all-in-one AES body in place.

Object 829 was the ELECTRICAL page's inline AES all-in-one body: 52 records packing the AES enable
pill, the enable/engine readouts, the Quiet-Time Start/Stop setpoints, the battery-voltage block
and the inverter block into one screen. rev4 trades it for a **compact frame** that mirrors object
830 (the AGS/slot-7=1 compact body) record-for-record -- House/Chassis voltages, the mode/engine
cluster, and the inverter -- by CLONING the AGS cluster and engine control and keeping the whole
CONTAINER WRAPPER CHAIN, so every donor record retains its byte-verbatim flow-relative geometry
and composes off its container origin (the fix for rev3's desynced gear touch). The original
52-record body is first cloned out to a fresh standalone object (`AES_BODY`) so `build_settings_
screen` can hang it off screen 28 as the setpoint sub-page.

Nothing here mutates any AGS-path object (object 5's mode switch, 830, 315, 795, 45, 486-488): the
AGS branch renders byte-identically. The donors (830/315/352/353 and the referenced 443/440/1420)
are read and cloned or merely referenced by child-index; the donor objects are never written.

The rebuilt frame (see `build_compact_frame` and `swap_electrical_to_compact`) is 7 records
mirroring 830[0..6] minus the slot-37 switch (the cluster is drawn unconditionally in AES) and
solar. The AES-only content changes: on the cloned cluster (`build_cluster`) the gear (id 0x7FE ->
0x7DE, label 'AGS' -> 'AES'), the engine container repointed from the AGS GEN control (352) to a
CLONE of the REAL AES Engine control (353) + an invisible flow spacer that restores 352's
engine-to-pill separation (`build_engine`, FIX #4), and the enable-pill touch repointed from the
3-sec-hold warning group (0x0080) to the AES 'Notice:' enable action (0x7E2, FIX #5). Built on
`relocate.replace_object_records` (in-place record-stream swap, re-verifies every structural gate)
and `insert._append_object` (append the cloned cluster/engine/all-in-one at fresh top indices).
"""

from __future__ import annotations

import struct

from . import cornermark, guidata, insert, layout, records, relocate, switchbuild, theme, thumb

# Object indices (object-table 1). AES all-in-one body on the ELECTRICAL page.
ELECTRICAL_AES_BODY = 829

# The stranded screen-root object (screen 28 -- a page reachable by no navigation in stock 13.6)
# that Task 3 rebuilds into the AES settings frame. Screen 28 already maps to object 31
# (`layout.screen_object_map`), so the rebuild is in-place at this index and needs no
# screen-map/match-table edit.
AES_SETTINGS_ROOT = 31

# Stock's own group-129 Low Volts controls: the AGS Triggers panel and the AGS settings body
# (`objects 31/315/795` register touch id 129 on stock 13.6; 31 is rebuilt above). Neither is an
# AES control, so neither counts as the control the AES Low Volts bridge drives.
AGS_LOW_VOLTS_HOSTS = (315, 795)
SETTINGS_SCREEN_ID = 28

# The AGS settings frame (screen 46 == object 45) whose six records are the read-only donors for
# the mirrored frame. Object 45 is on the AGS render path -- it is CLONED here (its bytes are read
# to build object 31's records), never written.
AGS_SETTINGS_FRAME = 45

# The record index of the wtype-5 body container in the built settings frame: it mirrors object
# 45's own body-container record (its last, record 5), repointed at the appended AES body clone.
CONTAINER_RECORD_INDEX = 5

# The stock touch id the gear button carries on the donor cluster (object 315, record 12): the
# AGS settings-button id, retargeted to SETTINGS_NAV_TOUCH_ID by `build_cluster` (the gear then
# opens the AES settings screen, not the AGS one).
SETTINGS_BUTTON_STOCK_ID = 0x7FE

# The fresh navigation touch id the compact settings button is retargeted to, and whose GuiCode
# ACTION-dispatcher (tap) leaf is repointed at screen 28 (the AES settings screen).
#
# **Why the action dispatcher, not the hold path.** A `wtype-12` id >= the group count (233) falls
# through to GuiCode's action dispatcher `FUN_0024ACB2` -- the same tap path the stock settings
# buttons use (0x7FE -> 46, 0x7D1 -> 1). The expired-deadline "hold" consumer (`holdregion`) is the
# WRONG mechanism for a tap gear: a hold-only-registered id arms no deadline, so the consumer's
# gate (`cbz` @0x0024E8F0 / `blt` @0x0024E8FA) skips it on BOTH tap and hold and it never
# navigates. Confirmed by a mechanism spike (fix round 1).
#
# **Why 0x7DE.** The action dispatcher has a working, ORPHANED tap leaf: id 0x7DE -> screen 14
# (INVERTER/CHARGER SETTINGS), carried in stock only by the non-displayed objects 1052/1053
# (GUICODE Finding 53), so nothing displayed collides on it (the hit test returns the lowest
# matching id, so a displayed carrier would shadow the gear). Repurposing it needs only a same-size
# 2-byte constant swap on that leaf -- no authored control flow, no NOP scratch, no interlock
# uncouple. Exposed as a constant so Task 5's recipe op stamps and wires the same id.
SETTINGS_NAV_TOUCH_ID = 0x7DE

# The action-dispatcher leaf for 0x7DE: `movw r3,#0x7de / cmp r2,r3 / bne.w / movs r3,#<screen> /
# strb.w r3,[r4,#0x20a]` in `FUN_0024ACB2`. Only the screen constant is edited, same-size:
# `movs r3,#0xe` (screen 14) -> `movs r3,#0x1c` (screen 28). The runtime address maps to the
# GuiCode file offset the way `holdregion` locates its pokes (`section.offset + (vaddr - base)`);
# the write is GUARDED against the stock bytes, so it refuses on any build whose leaf already
# differs rather than corrupting it.
SETTINGS_LEAF_SITE = 0x0024B96A                 # movs r3,#<screen> in the 0x7DE tap leaf
SETTINGS_LEAF_STOCK = bytes.fromhex("0e23")     # movs r3,#0xe  (screen 14)

# ----------------------------------------------------------------------------------------
# The Inverter settings capsule (owner request, 2026-09-01).
# ----------------------------------------------------------------------------------------
#
# Object 1421 -- the "Inverter (gear)" capsule that both compact frames draw beside the cluster --
# carries touch id 0x7F7, an ACTION id (>= the 233 group count), whose dispatcher leaf sets screen
# 39: object 38, the READ-ONLY 'PD Inverter' readout. The owner wants it to reach screen 14,
# object 17, 'INVERTER / CHARGER SETTINGS' -- the page the HOME inverter's 3-second hold reaches
# (`holdregion`, reference build v8), which carries five live controls (Low Batt. Cutout, VAC Dropout, Max
# Chrge Rate, Shore Brkr Size, Battery Type).
#
# 1421 is the ONLY carrier of 0x7F7 and it renders on the AGS compact frame as well as the AES one,
# so this retarget is deliberately GLOBAL: both pages' inverter capsules move together. Retargeting
# only the AES copy would mean cloning 1421 and finding a second action leaf that reaches screen 14
# -- and the one orphaned leaf that did (0x7DE) is already spent on the AES settings sub-page.
INVERTER_GEAR_BLOCK = 1421                       # the shared Inverter capsule object
INVERTER_GEAR_TOUCH_ID = 0x07F7
INVERTER_SETTINGS_SCREEN_ID = 14                 # object 17, INVERTER / CHARGER SETTINGS
INVERTER_GEAR_LEAF_SITE = 0x0024C842             # movs r3,#<screen> in the 0x7F7 action body
INVERTER_GEAR_LEAF_STOCK = bytes.fromhex("2723")  # movs r3,#0x27 (screen 39, PD Inverter)

# The enable pill's offset within the cloned cluster (owner request, 2026-09-01). Stock 315 draws
# the pill 8 px right of the Engine control's left edge and, on OUR frame, 7 px below the Inverter
# capsule's top -- the 7 px being the AES Engine clone's extra height, since 353 carries no
# generator-hours line and the invisible spacer that restores 352's rhythm is not pixel-exact.
# Both are corrected by shifting the pill BOX (315[1]) alone: the rows below it are flow-relative,
# so 'Triggers:', the Low Volts box and the gear capsule follow, landing within 1 px of the AGS
# page's own column. The Engine control does NOT move: it hangs off 315[0], before the pill.
PILL_DX = -8
PILL_DY = -6
PILL_BOX_REC = 1                                 # 315[1]: the wtype-33 enable-pill box

# The inverter column's own one-pixel correction (owner, 2026-09-01). The Inverter enable/disable
# block (object 1409) is anchored by the absolute y on the inverter switch record, and stock puts
# it at 153 while the Engine/GEN control's row starts at 154 -- a one-pixel stagger the AGS compact
# page has too (`tests/test_aes_layout_nav.py` pins that characterization). Our frame carries its
# own copy of that record, so +1 here corrects the AES page and leaves AGS untouched. It also moves
# the Inverter capsule 227 -> 228, which is why PILL_DY is -6 rather than -7: the pill tracks the
# capsule, and the column below the pill then lands on the AGS page's rows exactly.
INVERTER_COLUMN_DY = 1
INVERTER_SWITCH_REC = 6                          # 830[6] / 829[6]: the slot-113 inverter switch

# ----------------------------------------------------------------------------------------
# Task 7 / rev 2: the live Low Volts arm-indicator on the compact frame.
# ----------------------------------------------------------------------------------------
#
# The Low Volts control ships as part of the cloned cluster (object 315 records [7] 'Triggers:'
# label / [8] wtype-5 recolor container value_slot 45 -> child 285 = the 'Low Volts' box /
# [9] wtype-12 group-129 touch), so rev4 needs no splice -- `build_cluster` carries them verbatim
# with their donor geometry. The constants below are the joins the `aeslowvolts` bridge op still
# reads (register resolve + verify); the bridge recolors cell 45 and drives the group-129 counter
# each tick, un-gated, so the control recolors AND toggles both ways in AES (stock freezes it).
#
# The recolor cell the wtype-5 container reads (== the cell the bridge writes); slot 45 resolves
# to SRAM 0x2003A814 via the cell table. Pinned for the guard/report.
RECOLOR_SLOT = 45

# The DGN_CONFIG selector the Low Volts trigger arms, and its receive slot (var 42 ->
# 0x2000801C via the index-split base). Resolved live by `register.resolve_register`; pinned
# here only for the guard/report.
TRIGGER_SELECTOR = 0x2F

# Touch id 129 -> group 129, the stock 0x2F "Low Volts" config-write toggle (the GLOBAL
# touch-id -> group map, `FUN_00256B84`). Reused verbatim: no new group, binding or DGN.
TRIGGER_TOUCH_ID = 129


# ----------------------------------------------------------------------------------------
# REV 4: faithful structural mirror of the AGS compact frame (object 830).
# ----------------------------------------------------------------------------------------
#
# Object 829 is rebuilt to MIRROR object 830 (the AGS/slot-7=1 compact ELECTRICAL body) with the
# donor geometry preserved by keeping the CONTAINER WRAPPER CHAIN: instead of flattening records
# and absolutising some (which desynced the gear touch from its capsule -- the rev3 bug), the
# frame is a handful of container records that recurse into byte-verbatim clones, so every donor
# record keeps its flow-relative geometry and composes off its container's origin.
#
# The mirrored 7-record frame (wtypes [33, 11, 5, 11, 5, 5, 6]):
#   [0] frame capsule            = 830[0]  (box [200,60,460,80], verbatim)
#   [1] 'House' label            = 830[1]  (verbatim)
#   [2] House voltage container  = 830[2]  (verbatim; value_slot 209 -> obj 443, the gauge)
#   [3] 'Chassis' label          = 830[3]  (verbatim)
#   [4] Chassis voltage container= 830[4]  (verbatim; value_slot 205 -> obj 440, the gauge)
#   [5] compact-cluster container= NEW wtype-5 -> the cloned cluster (315'); UNCONDITIONAL
#       (829 renders only in AES, so the cluster is drawn always, not gated on slot 37 like
#       830[5]'s switch). Its geometry mirrors 830[5]'s (in-register x + y), so the cluster lands
#       at the AGS flow origin.
#   [6] inverter switch          = 830[6]  (verbatim; value_slot 113 -> obj 1420 band, draws the
#       Inverter block only when the inverter is on -- gated as stock).
# 830[7] (solar) is SKIPPED per owner.
AGS_COMPACT_FRAME = 830

# The AGS/GEN cluster arm (830[5]'s slot-37=1 candidate): the mode pill, enable-state readout,
# 'Triggers:'/Low-Volts control, the gear button and -- via 315[0] -> obj 352 -- the engine
# control. Cloned VERBATIM into 315' and then given the AES retargets (the enable-pill id, the
# engine container -> the appended 353 clone, and the gear id/label); everything else keeps its
# donor geometry so the pill/readout/Low-Volts/gear compose faithfully.
CLUSTER_DONOR_OBJECT = 315
CLUSTER_ENGINE_CONTAINER_REC = 0    # 315[0]: wtype-5 container -> the engine control (AES obj 353)
GEAR_LABEL_REC = 11                 # 315[11]: wtype-11 gear label 'AGS' -> 'AES'
GEAR_TOUCH_REC = 12                 # 315[12]: wtype-12 gear touch, id 0x7FE -> SETTINGS_NAV_TOUCH_ID

# rev5 FIX #5: the compact enable pill. 315[2] is a wtype-12 touch carrying group 128 (0x0080),
# which navigates straight to the Warning/3-sec-hold (screen 45), SKIPPING the AES "Notice:" page.
# The stock AES enable is action 0x7E2 (an id >= the group count -> the action dispatcher), whose
# handler shows the Notice (screen 18) first and is a proper enable/disable toggle. `build_cluster`
# rewrites the cloned pill's trailing id 0x0080 -> 0x07E2 (a same-size little-endian `80 00` ->
# `E2 07`), guarded against the stock id. AES-branch only: stock 315 and every AGS object untouched.
PILL_TOUCH_REC = 2                  # 315[2]: wtype-12 enable-pill touch
PILL_STOCK_ID = 0x0080             # stock group 128 -> Warning/3-sec-hold (screen 45)
AES_NOTICE_ACTION = 0x07E2         # the stock AES enable action -> the 'Notice:' page + toggle

# The compact-cluster container's y. rev4 nudged it up 8px (94 -> 86) to clear a taller engine
# footprint; rev5 uses the real AES Engine (object 353) plus ONE invisible flow spacer, whose
# footprint restores 352's vertical rhythm, so the stock 830[5] value of 94 fits with the whole
# cluster on the panel (render-validated: max y1 = 479, gear capsule bottom 437, nothing past
# 480). Reverted to stock 94. Coach-verify.
CLUSTER_CONTAINER_Y = 94

# The engine control, rev5 FIX #4. rev4 pointed the cloned cluster body (315[0]) at obj 352 = the
# AGS *GEN* control (generator; Start/Stop groups 19/20; status 530 'Unknown'; label 'GEN'; plus a
# stray center-touch group 170 and a generator-hours numeric line value_slot 46). rev5 uses the
# REAL AES *Engine* control = obj 353 (vehicle engine; Start/Stop groups 13/14 -> channel 18;
# indicator slots 167/168; status slot 169 -> the 526 'Not Running/Running' 2-line band; label
# 'Engine'; 7 records, NO group-170 touch, NO value_slot-46 line).
#
# The cluster is a SHARED-FLOW composition: 353's records advance the SAME flow pen the cluster's
# pill/readout/Triggers records ride on (they do not get a fresh frame). 352 ended with a spacer
# (352[8], wtype-9) AND a generator-hours line (352[9], wtype-8 at flow-y +38) -- and it is that
# LAST record that pushes the flow pen down ~one line before the pill. 353 has neither, so with a
# bare 353 the pill/readout flow UP into the engine's 2-line status band and overlap it (the rev5
# coach-review regression). The wtype-9 spacer is a red herring: type 9 is the ONE type EXCLUDED
# from the layout flow-gate mask (`layout._FLOW_GATE_MASK` = 0x600FFDFF, FUN_00257BFC line 33,
# proven-from-code), so it advances no flow on renderer OR hardware. Only a flow-gated record
# separates the rows.
#
# So 353 is CLONED (not shared) and given ONE appended flow-gated-but-INVISIBLE record: an empty
# wtype-11 label (`_engine_flow_spacer`) carrying 352[9]'s exact flow-y offset (+38). It draws
# nothing (zero-length text, unlike 352[9]'s gen-hours value -- so no generator data leaks onto
# the AES engine, which is the whole point of FIX #4) yet, being a label (flow-gated), advances
# the shared pen the same ~one line 352[9] did -- restoring 352's proven rhythm. The clone keeps
# 353's own child references verbatim, so the Start/Stop visuals (249/251, shared with 352) and
# the private status band (526-529 = Not Running/Starting/Running/Stopping) are all referenced by
# their stock indices and never written.
ENGINE_CONTENT_DONOR = 353          # the real AES Engine control; cloned, its 249/251/526-529 shared
ENGINE_GEOMETRY_DONOR = 352         # the AGS GEN control; source of the flow-spacer y-offset (352[9])
ENGINE_SPACER_DONOR_REC = 9         # 352[9]: the wtype-8 line whose flow-y offset the spacer copies



# ----------------------------------------------------------------------------------------
# Task 7: the outlined "Water Pump" section (owner-approved mockup, 2026-09-02).
# ----------------------------------------------------------------------------------------
#
# An outlined section in the compact frame's RIGHT-HAND column, below the Inverter capsule
# (which ends at y=278), carrying a heading, a full-width "Pump Link" button and an
# "Auto Off" stepper. It is APPENDED to what `build_compact_frame` emits -- the frame is
# rebuilt exactly once by `swap_electrical_to_compact`, so there is no second splice.
#
# Every fragment is a CLONE of a stock donor record; no donor object is ever written, so the
# AGS render path (830/315/793/393/285 and their children) stays byte-golden. The section is
# anchored ABSOLUTELY on its first record (base-mode 0 on x and y), so nothing that precedes
# it in the frame can move it and -- more to the point -- it can move nothing that precedes
# it: every other coordinate in the section is flow-relative to that anchor.
PUMP_SECTION_X = 434
PUMP_SECTION_Y = 290
PUMP_SECTION_W = 232
PUMP_SECTION_H = 173

# The 1 px inset outline: the second box, `PUMP_OUTLINE_INSET` px inside the filled one on
# every edge (the `Battery Voltage` block's own frame idiom, donors 829[7]/829[8]).
PUMP_OUTLINE_INSET = 2

# The content column, relative to the SECTION BOX's left edge, and its width. 8 px in on each
# side of the box leaves 6 px of clearance inside the outline, so nothing touches the stroke.
PUMP_CONTENT_DX = 8
PUMP_CONTENT_W = 220

# Row geometry, all relative to the OUTLINE's top (PUMP_SECTION_Y + PUMP_OUTLINE_INSET = 292),
# which is the flow origin the heading and everything after it inherits. The heading's donor
# carries no y coordinate of its own (829[9] has no geom[1]), so it starts ON that origin and
# its own box height + centred v-align is what places the text; the button and the stepper are
# each positioned by an invisible flow anchor (`_flow_anchor`).
PUMP_HEADING_H = 28                 # 292..320; the caption centres in this box
PUMP_BUTTON_DY = 34                 # button top, from the heading's origin -> 326
PUMP_BUTTON_H = 44                  # 326..370
PUMP_STEPPER_DY = 50                # stepper top, from the button's origin -> 376
PUMP_STEPPER_H = 80                 # the donor chrome (object 393) is a fixed 220 x 80 -> ..456

# Donors. All read from stock, all cloned.
PUMP_FRAME_DONOR = ELECTRICAL_AES_BODY      # 829: the all-in-one body, read BEFORE the swap
PUMP_BOX_REC = 7                            # 829[7]: wtype-32 filled box  (301,272,220,80)
PUMP_OUTLINE_REC = 8                        # 829[8]: wtype-32 inset box   (-2,274,216,76)
PUMP_HEADING_REC = 9                        # 829[9]: wtype-11 'Battery\nVoltage:'
PUMP_HEADING_TEXT = "Water Pump"            # 10 chars into a 16-char field -> NOT same-size

# The Low Volts control: the visible box + label live in object 285, reached through the
# cluster's container 315[8]; 315[9] is its wtype-12 touch. The section emits 285's two
# records DIRECTLY (rather than the container) so the button can be widened to the section's
# content column without writing the shared object 285.
PUMP_BUTTON_BODY_DONOR = 285                # the Low Volts box (rec 0) + label (rec 1)
PUMP_BUTTON_BOX_REC = 0
PUMP_BUTTON_LABEL_REC = 1
PUMP_BUTTON_TEXT = "Pump Link"              # 9 chars over 'Low\nVolts' (9) -- same size
PUMP_BUTTON_TOUCH_REC = 9                   # 315[9]: the Low Volts wtype-12 touch region

# The Pump Link state arms (v16, the build log v15 defect 1). Stock shows an on/off state on a
# button with a wtype-6 switch over pre-coloured capsule arms that carry their own caption --
# 45 stock switches do it, the AES enable pill among them: object 173 'Enable' (fill index 10,
# dark) and 174 'Enabled' (fill index 35, cyan), keyed by a byte cell. Those two are the donors;
# cell 211 (the Pump Link bridge's 0/1 display cell) is the key.
LINK_ARM_OFF_DONOR = 173
LINK_ARM_ON_DONOR = 174
LINK_ARM_BOX_REC = 0                        # wtype-33 capsule, geom [w, h, radius] pen-relative
LINK_ARM_LABEL_REC = 1                      # wtype-11 caption, geom [w, h]

# The switch that picks between the two arms above: object 9's own enable-pill switch (9[9]),
# cloned and made flow-relative. Stock reads a value slot and selects a two-object band, which
# is exactly the shape the Pump Link arms need over their own display cell.
# Re-exported from `switchbuild`, which now owns the switch author -- kept here so existing
# imports of `aescompact.LINK_SWITCH_*` keep working.
LINK_SWITCH_DONOR = switchbuild.LINK_SWITCH_DONOR
LINK_SWITCH_REC = switchbuild.LINK_SWITCH_REC
LINK_SWITCH_STOCK = switchbuild.LINK_SWITCH_STOCK

# The stepper: object 793, the AGS 'Time at Stop Volts' setpoint row -- a wtype-5 container
# on the shared +/- chrome (object 393), the caption, the two arrow touches (104 = plus,
# 103 = minus, matching `optionrow.ARROW_DONOR_PLUS/MINUS`), a spacer, the numeric readout
# (value_slot 278) and its 'Min' unit.
PUMP_STEPPER_DONOR = 793
PUMP_STEPPER_CAPTION_REC = 1                # wtype-11 'Time at\nStop Volts'
PUMP_STEPPER_TEXT = "Auto Off"              # 8 chars into an 18-char field -> NOT same-size
PUMP_STEPPER_PLUS_REC = 2                   # wtype-12, stock id 104 (the +300 arrow group)
PUMP_STEPPER_MINUS_REC = 3                  # wtype-12, stock id 103 (the -300 arrow group)
PUMP_STEPPER_VALUE_REC = 5                  # wtype-8, stock value_slot 278
PUMP_STEPPER_STOCK_PLUS_ID = 104
PUMP_STEPPER_STOCK_MINUS_ID = 103
PUMP_STEPPER_STOCK_SLOT = 278
PUMP_STEPPER_UNIT_REC = 6                   # wtype-0 'Min'
PUMP_READOUT_OFF_TEXT = "Off"               # 3 chars over 'Min' -> same-size, no rebuild needed

# The two match values the Auto Off readout switch selects on, IN BAND ORDER. Value 0 -- the
# disabled setting `pumpauto.MINUTES_MIN` -- picks the 'Off' arm; every other minute count falls
# through to the number on the `0xFFFF` DEFAULT arm. That asymmetry is why this is not `[0, 1]`
# like the Pump Link arms: the link cell holds exactly two values, the minute cell holds
# thirteen.
#
# THE DEFAULT ARM IS THE BASE, and the order below is load-bearing because of it. On no match
# `layout._resolve_switch` (transcribing GUICODE Finding 16) renders the base only when
# `match[base] == 0xFFFF`, and otherwise renders NOTHING -- so a `[0, 0xFFFF]` band draws an
# empty capsule for every minute count except 0. Built that way first; the layout re-walk caught
# it, which is the check this project keeps insisting on. Stock's own pump/heater switch is the
# same shape (`OBJECT-HIERARCHY.md` Sec 3: `mv[989] = 0xFFFF` at the base, `mv[990] = 0` after
# it).
READOUT_MATCH_VALUES = [0xFFFF, 0]

def _object_records(data: bytes, index: int) -> list[records.WRecord]:
    """Decode object `index`'s record stream via the standard accessor recipe."""
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    return records.object_records(data, gs + table[index])


def frame_roles(data: bytes, recs: list) -> dict:
    """Indices of the compact frame's records by ROLE, so records a later op prepends or appends
    (v48's cards and titles) do not break the verifies that used to pin 0..6 and 7.. by index.

    `frame` is the battery strip (wtype 33 followed by a label and a voltage container),
    `cluster` the wtype-5 whose child carries the AES gear's nav touch, `inverter` the slot-113
    switch, `pump` the first wtype-32 after it (None when there is no pump section). Raises
    ValueError when a role is missing or ambiguous."""
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]

    def touch_ids(obj):
        rs = records.object_records(data, gs + tab[obj])
        return [data[r.end - 2] | (data[r.end - 1] << 8) for r in rs if r.wtype == 12]

    frame = [i for i in range(len(recs) - 2)
             if [recs[i].wtype, recs[i + 1].wtype, recs[i + 2].wtype] == [33, 11, 5]]
    cluster = [i for i, r in enumerate(recs)
               if r.wtype == 5 and r.child_index is not None and r.child_index < len(tab)
               and SETTINGS_NAV_TOUCH_ID in touch_ids(r.child_index)]
    inverter = [i for i, r in enumerate(recs) if r.wtype == 6 and r.value_slot == 113]
    for name, hits in (("frame", frame), ("cluster", cluster), ("inverter", inverter)):
        if len(hits) != 1:
            raise ValueError("compact frame role %r found at %s, expected exactly one"
                             % (name, hits))
    pump = next((i for i in range(inverter[0] + 1, len(recs)) if recs[i].wtype == 32), None)
    return {"frame": frame[0], "cluster": cluster[0], "inverter": inverter[0], "pump": pump}


def _record_bytes(data: bytes, index: int, j: int) -> bytes:
    """The raw byte span of record `j` of object `index` -- a byte-verbatim donor clone.

    A widget record holds no absolute self-pointer (every inter-object reference is a u16 index
    into the child table, which the compact frame does not disturb), so a cloned record decodes
    and renders identically wherever it is placed.
    """
    r = _object_records(data, index)[j]
    return bytes(data[r.offset:r.offset + r.size])


def clone_all_in_one(data: bytes) -> tuple[bytes, int]:
    """Clone object 829's 52-record body VERBATIM to a fresh standalone object and return
    `(out, aes_body_index)`.

    The clone (`AES_BODY`) is the settings sub-page body a later task hangs off the compact
    frame's settings button. It is appended unattached at the top object index -- so no existing
    object's records or child indices move and no AGS-path object is touched -- via
    `insert._append_object` (the append half of `insert.insert_object`, without an attach).
    Object 829 itself is left fully intact by this step.
    """
    body = insert.clone_object(data, ELECTRICAL_AES_BODY)
    out, _edits, new_index = insert._append_object(data, body)
    return out, new_index


def _repoint_container(data: bytes, index: int, j: int, new_child: int) -> bytes:
    """Clone record `j` of object `index` (a wtype-5 container) and rewrite its child-index u16 to
    `new_child`.

    The child-index field's position within the record is located by replaying the decoder
    (`records.child_index_offset`), not assumed at a fixed offset, so it is correct regardless of
    which optional fields precede it. A container's child-index is a u16 INDEX into object-table 1,
    not a byte offset -- so retargeting it needs only this 2-byte rewrite and is untouched by the
    downstream OFFSET shift the in-place rebuild applies.
    """
    rec = _object_records(data, index)[j]
    ci_abs = records.child_index_offset(data, rec)
    if ci_abs is None:
        raise ValueError(f"object {index} record {j} (wtype {rec.wtype}) has no child_index")
    raw = bytearray(data[rec.offset:rec.offset + rec.size])
    struct.pack_into("<H", raw, ci_abs - rec.offset, new_child & 0xFFFF)
    return bytes(raw)


def build_settings_screen(data: bytes, aes_body_index: int) -> tuple[bytes, int]:
    """Rebuild the stranded screen-root object 31 (screen 28) into an AES settings frame mirroring
    object 45, whose body container points at the appended AES body clone. Returns
    `(out, screen_id=28)`.

    The frame is six BYTE-VERBATIM clones of object 45's records -- the full-screen background box,
    the header bar box, the header mode readout (value_slot 37, child 486), the nav container
    (child 892) and the derived left nav rail (value_slot 0, child 974) -- plus object 45's own
    wtype-5 body container (record 5) with its child-index retargeted from the AGS body (795) to
    `aes_body_index`. A container pointer legally targets the appended clone (a switch could not:
    its recursion resolves a child by a different mechanism). Object 45 is never written; only its
    record bytes are read as donors, so the AGS render path (screen 46) is byte-golden.

    `aes_body_index` is the DYNAMIC index `clone_all_in_one` returned for the appended all-in-one
    AES body (1422 on stock -- taken from the return value, never hardcoded); this function must be
    called on the image AFTER that clone exists. The rebuild is written at object index 31 in place
    via `relocate.replace_object_records`, so screen 28 still maps to object 31 and no
    screen-map/match-table edit is needed.
    """
    frame = [
        _record_bytes(data, AGS_SETTINGS_FRAME, 0),   # full-screen background box (wtype-4)
        _record_bytes(data, AGS_SETTINGS_FRAME, 1),   # header bar box (wtype-4)
        _record_bytes(data, AGS_SETTINGS_FRAME, 2),   # header mode readout: wtype-6, slot 37 (child 486)
        _record_bytes(data, AGS_SETTINGS_FRAME, 3),   # nav container: wtype-5 (child 892)
        _record_bytes(data, AGS_SETTINGS_FRAME, 4),   # left nav rail: wtype-5, slot 0 (child 974)
        _repoint_container(data, AGS_SETTINGS_FRAME, 5, aes_body_index),  # body container -> AES body
    ]
    assert len(frame) == CONTAINER_RECORD_INDEX + 1
    body = bytes([len(frame)]) + b"".join(frame)
    out, _edits = relocate.replace_object_records(data, AES_SETTINGS_ROOT, body)
    return out, SETTINGS_SCREEN_ID


def _engine_flow_spacer(data: bytes) -> bytes:
    """A single flow-gated but INVISIBLE record that restores 352's engine-to-pill separation.

    An empty `wtype-11` label (zero-length text -> draws nothing) carrying the same flow-relative
    y-offset the AGS GEN control's generator-hours line (352[`ENGINE_SPACER_DONOR_REC`]) used to
    push the shared flow pen down ~one line before the pill. A label is IN the flow-gate mask (only
    wtype-9 is excluded), so it advances the pen exactly as 352[9] did -- but, being an empty
    label rather than a value widget on slot 46, it shows no generator data (the point of FIX #4).

    The x/y offset is taken from the donor at build time (geom[0]/geom[1] of 352[9]) so the spacer
    tracks the real AGS control rather than a hardcoded magic number; the rest is the persistent
    color/font/align/line-height tail of a stock label (353's 'Engine' label). Self-decoded at
    build time to guarantee it is a valid, empty, flow-gated wtype-11.
    """
    donor = _object_records(data, ENGINE_GEOMETRY_DONOR)[ENGINE_SPACER_DONOR_REC]
    x_off = donor.geom[0] or 0
    y_off = donor.geom[1] or 0
    rec = bytes([0x0B, 0x55, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00]) \
        + struct.pack("<H", x_off) + struct.pack("<H", y_off) \
        + bytes([0x01, 0x0D, 0xFF, 0x02, 0x02, 0x0A, 0x00]) \
        + bytes([0x00, 0x00])                            # label: length 0 + NUL -> empty
    dec = records.decode_record(rec, 0)
    assert (dec.wtype == 11 and (dec.label or "") == "" and dec.geom[1] == y_off
            and dec.size == len(rec) and layout._in_flow_gate(dec.wtype)), \
        "engine flow spacer failed its self-decode / is not a flow-gated empty label"
    return rec


def build_engine(data: bytes) -> bytes:
    """Clone the real AES Engine control (obj `ENGINE_CONTENT_DONOR` = 353) VERBATIM and append one
    invisible flow spacer, returning the standalone object bytes (`count` + records + 4-pad) for
    `insert._append_object`.

    353's 7 records are copied byte-for-byte -- so 'Engine', the Start/Stop touches (groups 13/14),
    the Start/Stop indicators (slots 167/168), the status switch (slot 169 -> the 526 band) and
    the shared child references (249/251, 526-529) all compose exactly as the stock AES engine --
    and `_engine_flow_spacer` is appended as record [7] to restore the engine-to-pill separation
    352 provided (see FIX #4). The clone writes no donor object: 353, its 249/251 and its 526-529
    band are all referenced by their stock indices.
    """
    recs = _object_records(data, ENGINE_CONTENT_DONOR)
    base = recs[0].offset - 1                            # object start (the count byte)
    body = bytearray(data[recs[0].offset:recs[-1].end])  # the 7 records, verbatim (no count byte)
    frags = bytes([len(recs) + 1]) + bytes(body) + _engine_flow_spacer(data)
    frags += b"\x00" * ((-len(frags)) % 4)               # 4-align for _append_object
    return frags


def build_cluster(data: bytes, engine_index: int) -> bytes:
    """Clone the AGS cluster arm (obj 315) VERBATIM and apply the AES retargets, returning the
    standalone object bytes (`count` + records) for `insert._append_object`.

    All 14 donor records keep their byte-verbatim flow-relative geometry (so the pill, the
    enable-state readout, 'Triggers:'/Low-Volts and the gear compose exactly as they do in the AGS
    cluster). Four same-size edits: 315[0]'s child_index is repointed from the AGS GEN control
    (352) to the appended AES Engine clone `engine_index` (`build_engine`: real 353 content + the
    invisible flow spacer, FIX #4); the enable-pill touch 315[2]'s id 0x0080 -> `AES_NOTICE_ACTION`
    (0x07E2), routing the pill through the AES 'Notice:' enable flow instead of the 3-sec-hold
    warning (FIX #5); the gear label 315[11] 'AGS' -> 'AES'; and the gear touch 315[12]'s id
    0x7FE -> `SETTINGS_NAV_TOUCH_ID` (0x7DE), so the visible capsule and its wtype-12 touch region
    -- both flow-relative within 315 -- stay perfectly aligned (the rev3 bug was absolutising the
    capsule while leaving the touch relative). Every edit is same-size, so the clone stays
    4-aligned and stock 315 is byte-golden.
    """
    recs = _object_records(data, CLUSTER_DONOR_OBJECT)
    base = recs[0].offset - 1                            # object start (the count byte)
    clone = bytearray(data[base:recs[-1].end])
    clone += b"\x00" * ((-len(clone)) % 4)

    # 315[0]: engine container child 352 (AGS GEN) -> the appended AES Engine clone.
    r0 = recs[CLUSTER_ENGINE_CONTAINER_REC]
    struct.pack_into("<H", clone, records.child_index_offset(data, r0) - base, engine_index)

    # 315[2]: enable-pill touch id 0x0080 (Warning/hold) -> AES_NOTICE_ACTION (0x7E2, Notice flow).
    r2 = recs[PILL_TOUCH_REC]
    pill_id = data[r2.end - 2] | (data[r2.end - 1] << 8)
    if pill_id != PILL_STOCK_ID:
        raise ValueError(
            f"object {CLUSTER_DONOR_OBJECT} enable-pill touch (record {PILL_TOUCH_REC}) carries id "
            f"{pill_id:#06x}, expected the stock {PILL_STOCK_ID:#06x}")
    struct.pack_into("<H", clone, (r2.end - 2) - base, AES_NOTICE_ACTION)

    # 315[11]: gear label 'AGS' -> 'AES'.
    r11 = recs[GEAR_LABEL_REC]
    span = data[r11.offset:r11.end]
    idx = span.rfind(b"AGS")
    if idx < 0:
        raise ValueError(
            f"object {CLUSTER_DONOR_OBJECT} record {GEAR_LABEL_REC} does not carry the 'AGS' gear "
            "label -- is this the stock cluster?")
    clone[(r11.offset - base) + idx + 1] = ord("E")      # 'G' -> 'E'

    # 315[12]: gear touch id 0x7FE -> SETTINGS_NAV_TOUCH_ID.
    r12 = recs[GEAR_TOUCH_REC]
    old_id = data[r12.end - 2] | (data[r12.end - 1] << 8)
    if old_id != SETTINGS_BUTTON_STOCK_ID:
        raise ValueError(
            f"object {CLUSTER_DONOR_OBJECT} gear touch (record {GEAR_TOUCH_REC}) carries id "
            f"{old_id:#06x}, expected the stock {SETTINGS_BUTTON_STOCK_ID:#06x}")
    struct.pack_into("<H", clone, (r12.end - 2) - base, SETTINGS_NAV_TOUCH_ID)

    # 315[1]: shift the enable-pill box by (PILL_DX, PILL_DY) -- two same-size u16 edits on the
    # box's own flow offsets, so the pill's left lands on the Engine control's and its top on the
    # Inverter capsule's. Every row below the pill is flow-relative and follows it (intended: it
    # puts the column on the AGS page's rhythm); the Engine control hangs off 315[0] and does not.
    r1 = recs[PILL_BOX_REC]
    for i, delta in ((0, PILL_DX), (1, PILL_DY)):
        if delta == 0:
            continue
        off = records.geom_offset(data, r1, i)
        if off is None:
            raise ValueError(
                f"object {CLUSTER_DONOR_OBJECT} pill box (record {PILL_BOX_REC}) has no geom[{i}] "
                "to shift -- is this the stock cluster?")
        value = struct.unpack_from("<h", data, off)[0] + delta
        struct.pack_into("<h", clone, off - base, value)
    return bytes(clone)



# ----------------------------------------------------------------------------------------
# The Water Pump section fragments.
# ----------------------------------------------------------------------------------------


def _clone_with_geom(data: bytes, index: int, j: int, edits: dict) -> bytearray:
    """Record `j` of object `index`, cloned, with `{geom_slot: value}` rewritten in place.

    Each write is a same-size u16 on a geometry slot the donor ALREADY carries, located by
    replaying the decoder (`records.geom_offset`) rather than a fixed field position, and
    rebased to the fragment's own start. A slot the donor does not carry raises rather than
    silently drawing at the wrong place.
    """
    rec = _object_records(data, index)[j]
    frag = bytearray(_record_bytes(data, index, j))
    for slot, value in edits.items():
        off = records.geom_offset(data, rec, slot)
        if off is None:
            raise ValueError(
                f"object {index} record {j} (wtype {rec.wtype}) has no geom[{slot}] to set")
        if not -0x8000 <= value <= 0x7FFF:
            raise ValueError(f"geom[{slot}] value {value} does not fit a signed u16")
        struct.pack_into("<h", frag, off - rec.offset, value)
    return frag


def _set_align(frag: bytearray, align: int) -> None:
    """Rewrite a fragment's alignment byte -- header byte 3's top two bits (`state+9`, read
    unconditionally for every record; `records.WRecord.align`). The remaining six bits are the
    fillA/fillB colour modes, so this is a bit-surgical same-size edit that changes nothing but
    the alignment. Alignment is PERSISTENT across records, so the section's first record sets
    it explicitly (1 = left) instead of inheriting whatever the inverter subtree left behind.
    """
    if not 0 <= align <= 3:
        raise ValueError(f"alignment {align} is not 0..3")
    frag[3] = (frag[3] & 0x3F) | (align << 6)


def _set_touch_id(frag: bytearray, touch_id: int) -> None:
    """Rewrite a wtype-12 region's trailing action/group id (the last u16 of the record)."""
    if not 0 <= touch_id <= 0xFFFF:
        raise ValueError(f"touch id {touch_id} does not fit a u16")
    struct.pack_into("<H", frag, len(frag) - 2, touch_id)


def _relabel(data: bytes, index: int, j: int, text: str, frag: bytearray) -> bytes:
    """`frag` (a clone of object `index` record `j`) with its label field REBUILT at the new
    text's own length.

    The length-prefixed, NUL-terminated label is the LAST field of a record (the decoder's
    final loop, then a single trailing NUL -- so `rec.end == label_offset + len + 1`), which is
    what makes a non-same-size rename a pure truncate-and-re-emit rather than a splice: the
    fragment keeps every byte up to its length prefix and gets a fresh prefix + text + NUL.
    `replace_object_records` re-pads the object and repairs every downstream pointer, so the
    record stream's total length is free to change; this is NOT a poke into a fixed field, and
    a longer string is never forced into a shorter one.

    Self-decoded before it is returned: the rebuilt record must decode to the same widget type
    with exactly the requested text and its own new size.
    """
    rec = _object_records(data, index)[j]
    if rec.label is None or rec.label_offset is None:
        raise ValueError(f"object {index} record {j} carries no label to rebuild")
    n = len(rec.label)
    if data[rec.label_offset - 1] != n or data[rec.label_offset + n] != 0:
        raise ValueError(
            f"object {index} record {j}'s label at {rec.label_offset:#x} is not a "
            "NUL-terminated length-prefixed field")
    if rec.end != rec.label_offset + n + 1:
        raise ValueError(
            f"object {index} record {j}'s label is not the record's final field; refusing to "
            "rebuild it")
    if len(text) > 0xFE:
        raise ValueError(f"label {text!r} is too long for a one-byte length prefix")
    head = bytes(frag[:(rec.label_offset - 1) - rec.offset])
    out = head + bytes([len(text)]) + text.encode("ascii") + b"\x00"
    dec = records.decode_record(out, 0)
    assert dec.wtype == rec.wtype and dec.label == text and dec.size == len(out), \
        f"rebuilt label record for {text!r} failed its self-decode"
    return out


def _flow_anchor(dx: int, dy: int) -> bytes:
    """An invisible, flow-gated record that moves the flow origin by `(dx, dy)`.

    Same idiom as `_engine_flow_spacer`: an empty `wtype-11` label (zero-length text -> draws
    nothing) which, being a label, IS in the layout flow-gate mask, so the layout engine commits
    its coordinates as the new flow origin (`state.ref[0x36]/[0x38]`) for every record after it.
    It exists because the button's and the stepper's donor records carry no x/y geometry of
    their own (they inherit the flow origin their stock containers set) -- so the section places
    each row by setting that origin, exactly as the stock cluster does, instead of inventing
    absolute coordinates the donors were never composed against.

    Base-mode 1 on all four coords (relative to the flow origin) and a zero-size box; alignment
    byte 0, so the persistent alignment the section set is left alone. Self-decoded at build
    time to guarantee it is a valid, empty, flow-gated wtype-11.
    """
    rec = bytes([0x0B, 0x55, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00]) \
        + struct.pack("<h", dx) + struct.pack("<h", dy) \
        + bytes([0x01, 0x0D, 0xFF, 0x02, 0x02, 0x0A, 0x00]) \
        + bytes([0x00, 0x00])                            # label: length 0 + NUL -> empty
    dec = records.decode_record(rec, 0)
    assert (dec.wtype == 11 and (dec.label or "") == "" and dec.size == len(rec)
            and dec.align == 0 and dec.base_modes[:4] == [1, 1, 1, 1]
            and layout._s16(dec.geom[0]) == dx and layout._s16(dec.geom[1]) == dy
            and layout._in_flow_gate(dec.wtype)), \
        "flow anchor failed its self-decode / is not a flow-gated empty label"
    return rec


def _author_n_arm_switch(data: bytes, display_slot: int, arms_base: int, count: int) -> bytes:
    """An `count`-arm state switch: stock's enable-pill switch (9[9]) with x/y made flow-relative
    (header byte 1 `0x55`, x = y = 0 -- the box it replaces drew at the flow origin), reading
    `display_slot` and selecting the `count`-object band at `arms_base`. The record shape is
    identical for any arm count; only the R45 count field differs. The match-table values the arms
    were appended with live in the match table, not here.

    Thin wrapper over `switchbuild.author_switch` (`at=None`, its flow-relative branch) -- kept so
    existing callers/tests in this module do not need to change."""
    return switchbuild.author_switch(data, value_slot=display_slot, arms_base=arms_base,
                                     count=count)


def _author_two_arm_switch(data: bytes, display_slot: int, arms_base: int) -> bytes:
    """A two-arm state switch (the Pump Link button and the v20 Auto Off readout) -- thin wrapper
    over `_author_n_arm_switch` with `count = 2`."""
    return _author_n_arm_switch(data, display_slot, arms_base, 2)


def build_pump_section(data: bytes, *, link_touch_id: int, plus_touch_id: int,
                       minus_touch_id: int, readout_slot: int,
                       link_display_slot: int | None = None,
                       link_arms_base: int | None = None,
                       readout_arms_base: int | None = None) -> list[bytes]:
    """The Water Pump section's record fragments, in flow order.

    Fifteen fragments (`link_display_slot`/`link_arms_base` both `None`, the v14/v15 shape) or
    fourteen (both given, v16), wtypes `[32, 32, 11, 11, 33, 11, 12, 11, 5, 11, 12, 12, 9, 8, 0]`
    or `[32, 32, 11, 11, 6, 12, 11, 5, 11, 12, 12, 9, 8, 0]`:

      [0]  wtype-32  section box       -- 829[7], absolute at (X, Y), W x H, alignment forced
                                         to left so the section anchors its own column
      [1]  wtype-32  inset outline     -- 829[8], `PUMP_OUTLINE_INSET` px inside [0]
      [2]  wtype-11  'Water Pump'      -- 829[9] relabelled; a 10-char string into a 16-char
                                         field, so the record is REBUILT at its own length
      [3]  wtype-11  flow anchor       -- invisible; puts the button row on its origin
      [4]  wtype-33  button box        -- v14/v15 only: 285[0] widened to the content column
      [5]  wtype-11  'Pump Link'       -- v14/v15 only: 285[1] relabelled (9 over 9: same size)
                                         and widened
      [4]  wtype-6   link switch       -- v16 only: `_author_two_arm_switch`, reading
                                         `link_display_slot` and selecting the two-object band at
                                         `link_arms_base`; the arms carry their own caption, so
                                         the separate box/label pair above is not emitted
      [6]  wtype-12  button touch      -- 315[9] widened, carrying `link_touch_id`
      [7]  wtype-11  flow anchor       -- invisible; puts the stepper row on its origin
      [8]  wtype-5   stepper chrome    -- 793[0] -> object 393 (the +/- capsule), verbatim
      [9]  wtype-11  'Auto Off'        -- 793[1] relabelled; 8 chars into an 18-char field, so
                                         this record is REBUILT at its own length too
      [10] wtype-12  plus touch        -- 793[2], stock id 104 -> `plus_touch_id`
      [11] wtype-12  minus touch       -- 793[3], stock id 103 -> `minus_touch_id`
      [12] wtype-9   spacer            -- 793[4], verbatim
      [13] wtype-8   numeric readout   -- 793[5], stock value_slot 278 -> `readout_slot`
      [14] wtype-0   'Min'             -- 793[6], verbatim

    With `readout_arms_base` (v20) those last TWO fragments are replaced by ONE wtype-6 switch
    on `readout_slot`, selecting the band at `readout_arms_base`: 'Off' at match 0, the number +
    'Min' on the default arm. So the section is thirteen fragments, ending
    `[..., 12, 9, 6]`. The switch is authored by the same `_author_two_arm_switch` the Pump Link
    button uses and sits at x = y = 0, i.e. exactly the flow origin the numeric readout drew
    from, so the arm's records inherit the origin the pair had.

    The stepper's parts (chrome, caption, both arrows, readout, unit) are cloned as ONE
    contiguous run in their donor order, so they keep the flow relationship the stock row was
    composed with and cannot drift out of alignment with each other; only the two ids and the
    one slot are retargeted, each guarded against its stock value.

    Every donor is READ from the stock image and cloned. Objects 829, 285, 315, 793 and 393 are
    never written, so the AGS render path is untouched.
    """
    x, y, w, h = PUMP_SECTION_X, PUMP_SECTION_Y, PUMP_SECTION_W, PUMP_SECTION_H
    inset = PUMP_OUTLINE_INSET
    content_dx = PUMP_CONTENT_DX - inset        # content column, relative to the OUTLINE's x

    # [0] the filled section box: absolute (base-mode 0) on x and y, width/height relative.
    box = _clone_with_geom(data, PUMP_FRAME_DONOR, PUMP_BOX_REC, {0: x, 1: y, 2: w, 3: h})
    _set_align(box, 1)                           # left: the donor's align-3 would right-justify
                                                 # the whole column off its own anchor
    # [1] the inset outline: x relative to [0]'s origin, y absolute (as the donor carries it).
    outline = _clone_with_geom(data, PUMP_FRAME_DONOR, PUMP_OUTLINE_REC,
                               {0: inset, 1: y + inset, 2: w - 2 * inset, 3: h - 2 * inset})

    # [2] the heading. Its donor has no y geometry, so it starts on the outline's origin and
    # the centred v-align inside PUMP_HEADING_H is what seats the text below the box's top.
    heading = _clone_with_geom(data, PUMP_FRAME_DONOR, PUMP_HEADING_REC,
                               {0: content_dx, 2: PUMP_CONTENT_W, 3: PUMP_HEADING_H})
    heading = _relabel(data, PUMP_FRAME_DONOR, PUMP_HEADING_REC, PUMP_HEADING_TEXT, heading)

    # [3..6] the full-width 'Pump Link' button: state (box+label, or v16's switch) and touch
    # region, widened to the content column and stacked on one flow origin.
    if (link_display_slot is None) != (link_arms_base is None):
        raise ValueError("link_display_slot and link_arms_base must be given together")
    if link_display_slot is None:
        # v14/v15 shape: a static box + caption (the box's fill index is the Low Volts cell --
        # the build log v15 defect 1 -- kept only so the pre-v16 image stays reproducible).
        btn_box = _clone_with_geom(data, PUMP_BUTTON_BODY_DONOR, PUMP_BUTTON_BOX_REC,
                                   {2: PUMP_CONTENT_W, 3: PUMP_BUTTON_H})
        btn_label = _clone_with_geom(data, PUMP_BUTTON_BODY_DONOR, PUMP_BUTTON_LABEL_REC,
                                     {2: PUMP_CONTENT_W, 3: PUMP_BUTTON_H})
        btn_label = _relabel(data, PUMP_BUTTON_BODY_DONOR, PUMP_BUTTON_LABEL_REC,
                             PUMP_BUTTON_TEXT, btn_label)
        button = [bytes(btn_box), bytes(btn_label)]
    else:
        # v16: the stock state-button pattern -- one wtype-6 switch on the bridge's 0/1 cell
        # selecting the dark/lit arm objects, which carry the caption themselves.
        button = [_author_two_arm_switch(data, link_display_slot, link_arms_base)]
    btn_touch = _clone_with_geom(data, CLUSTER_DONOR_OBJECT, PUMP_BUTTON_TOUCH_REC,
                                 {2: PUMP_CONTENT_W, 3: PUMP_BUTTON_H})
    _set_touch_id(btn_touch, link_touch_id)

    # [7..14] the 'Auto Off' stepper, cloned as one contiguous run of 793[0..6].
    stepper_chrome = bytearray(_record_bytes(data, PUMP_STEPPER_DONOR, 0))
    caption = bytearray(_record_bytes(data, PUMP_STEPPER_DONOR, PUMP_STEPPER_CAPTION_REC))
    caption = _relabel(data, PUMP_STEPPER_DONOR, PUMP_STEPPER_CAPTION_REC,
                       PUMP_STEPPER_TEXT, caption)

    plus = bytearray(_record_bytes(data, PUMP_STEPPER_DONOR, PUMP_STEPPER_PLUS_REC))
    _guard_touch_id(data, PUMP_STEPPER_DONOR, PUMP_STEPPER_PLUS_REC, PUMP_STEPPER_STOCK_PLUS_ID)
    _set_touch_id(plus, plus_touch_id)
    minus = bytearray(_record_bytes(data, PUMP_STEPPER_DONOR, PUMP_STEPPER_MINUS_REC))
    _guard_touch_id(data, PUMP_STEPPER_DONOR, PUMP_STEPPER_MINUS_REC,
                    PUMP_STEPPER_STOCK_MINUS_ID)
    _set_touch_id(minus, minus_touch_id)

    spacer = bytearray(_record_bytes(data, PUMP_STEPPER_DONOR, 4))
    value = bytearray(_record_bytes(data, PUMP_STEPPER_DONOR, PUMP_STEPPER_VALUE_REC))
    vrec = _object_records(data, PUMP_STEPPER_DONOR)[PUMP_STEPPER_VALUE_REC]
    if vrec.value_slot != PUMP_STEPPER_STOCK_SLOT:
        raise ValueError(
            f"object {PUMP_STEPPER_DONOR} record {PUMP_STEPPER_VALUE_REC} reads value_slot "
            f"{vrec.value_slot}, expected the stock {PUMP_STEPPER_STOCK_SLOT}")
    struct.pack_into("<H", value, records.value_slot_offset(data, vrec) - vrec.offset,
                     readout_slot & 0xFFFF)
    unit = bytearray(_record_bytes(data, PUMP_STEPPER_DONOR, 6))

    # v20: one switch in place of the (number, unit) pair. The spacer stays OUTSIDE it, so the
    # flow state entering the switch is byte-for-byte the state that entered the numeric readout.
    readout = ([_author_two_arm_switch(data, readout_slot, readout_arms_base)]
               if readout_arms_base is not None else [bytes(value), bytes(unit)])

    return [bytes(box), bytes(outline), bytes(heading),
            _flow_anchor(0, PUMP_BUTTON_DY),
            *button, bytes(btn_touch),
            _flow_anchor(0, PUMP_STEPPER_DY),
            bytes(stepper_chrome), bytes(caption), bytes(plus), bytes(minus),
            bytes(spacer), *readout]


# ----------------------------------------------------------------------------------------
# Battery-heater command button (2026-09-21).
# ----------------------------------------------------------------------------------------
#
# A labelled, tappable box in the empty column to the RIGHT of the Water Pump section. It
# transmits the BMS "Enable battery heater" proprietary-A frame (18EF70xx) on a press-and-
# hold and the "off" frame on a tap; the two frames live in its command group's bindings,
# written separately (`newbutton.binding_raw`). This helper only builds the on-screen records.
#
# Shape mirrors the Water Pump section's box + inset outline + centred heading (829[7]/[8]/[9]),
# absolute-anchored on the first record (base-mode 0) so it moves nothing before it, plus a
# wtype-12 touch (315[9]) covering the box and stamped with the command group id. There is no
# state readout because the status DGN is still being defined -- command-only, indicator later.
BATT_HEAT_X = 672
BATT_HEAT_Y = 290                           # aligned with the Water Pump section top
BATT_HEAT_W = 122
BATT_HEAT_H = 173                           # == PUMP_SECTION_H, so the right column is balanced
BATT_HEAT_TEXT = "Batt Heat"                # 9 chars into 829[9]'s 16-char field -> rebuilt

# The command the button transmits, on the BMS proprietary-A DGN (18EF70xx, DP=0, destination
# 0x70). Big-endian; the last two bytes are the 100ms-tick auto-off count (0x8CA0 = 36000 =
# 60 min, 0x0000 = off). The panel stamps its own source address 0x9F (accepted -- the BMS is
# destination-filtered), so the wire frames are 18EF709F#AA55000000008CA0 / ...0000.
# Owner behaviour (2026-09-21): press-and-hold arms the heater, a quick tap turns it off.
BATT_HEAT_DGN = 0x0EF70
BATT_HEAT_ENABLE_WIRE = bytes.fromhex("aa55000000008ca0")
BATT_HEAT_OFF_WIRE = bytes.fromhex("aa55000000000000")
BATT_HEAT_ENABLE_MASK = 0x04                # gesture "while held" -> arm (fires once, no repeat)
BATT_HEAT_OFF_MASK = 0x02                   # gesture "short-tap release" -> off
BATT_HEAT_HOLD_DONOR_GROUP = 17             # a stock tap-or-hold group: 1-s hold prefix donor
BATT_HEAT_ENABLE_ID = 0x18EF709F
BATT_HEAT_OFF_ID = 0x18EF709F


# A solid, static button fill (a colour-table index that resolves to a fixed colour, not a live
# cell): the Low Volts box donor (285[0]) uses mode-7 index 45, which is the Low Volts RECOLOR
# cell -- copying it verbatim makes the button track the Low Volts state (the build log v15 defect 1).
# Repointing that index to 35 (the enable pill's lit "Enabled" colour, cyan) gives a solid button
# that stands out against the navy page and is not wired to any live cell.
BATT_HEAT_FILL_INDEX = 35


# --- Batt Heat status indicator (BMS status DGN 0x18FF7070, from SA 0x70) ---------------------
# The status frame broadcasts ~every 2 s and immediately after a command. Two live bits drive the
# button: byte 2 bit 0 = the battery HEATER (the button body's colour) and byte 1 bit 2 = the
# battery's internal HEATER CONTACTOR (the red glyph). The contactor is a relay inside the battery
# pack; it is NOT a heat pump, which is what v31's constants and docs called it until the owner
# corrected the naming on 2026-09-22. The receive filter extracts a 2-bit field, not a single bit,
# so the finest extraction covering each bit is used and the "on" bit is masked out of the field.
BATT_HEAT_STATUS_DGN = 0x0FF70              # low-16 0xFF70; CAN id 0x18FF7070 (PDU2, DP=0)
BATT_HEAT_STATUS_HEATER_BYTE = 2           # heater bit 0 lives in payload byte 2
BATT_HEAT_STATUS_HEATER_MODE = 4           # extract bits 0-1 (finest covering bit 0)
BATT_HEAT_STATUS_CONTACTOR_BYTE = 1        # contactor bit 2 lives in payload byte 1
BATT_HEAT_STATUS_CONTACTOR_MODE = 5        # extract bits 2-3 (finest covering bit 2)
# Within each extracted 2-bit field, bit 0 is the state and bit 1 is a neighbour we do not show
# (byte 2 bit 1 = "alarm mask active"; byte 1 bit 3 is always 0), so both fields mask to bit 0.
BATT_HEAT_HEATER_ON_MASK = 1
BATT_HEAT_CONTACTOR_ON_MASK = 1

# Receiving 0xFF70 costs NO existing subscription: `append_filter` adds two new variable-table
# entries (coach-verified 2026-09-22, docs/HARDWARE-VERIFIED.md), so their indices are whatever the
# table's end is on the image being built -- the op reads them back from the append, and nothing
# here pins a donor. Two earlier revisions retargeted donors and both were wrong: the DC_DIMMER
# instances 69/70 paint G12 OUTPUTS rows (row 18 is the AES/engine Start/Stop), and the solar pair
# 251/252 feeds the GuiCode solar parser FUN_00240D20 (GUICODE Finding 75).

# Contactor glyph: font slot 23 char '2' (glyph 3120, three wavy vertical lines), tinted red via a
# fillA mode-3 inline BGR. dx/dy are the top-right inset from the button's flow origin; w/h the box.
CONTACTOR_GLYPH_FONT_SLOT = 23
CONTACTOR_GLYPH_CHAR = "2"
CONTACTOR_GLYPH_RED_BGR = (0x2D, 0x1E, 0xBE)   # RGB(190, 30, 45) as file bytes B, G, R
CONTACTOR_GLYPH_DX = 92                    # tuned against the render (top-right of the button)
CONTACTOR_GLYPH_DY = 8
CONTACTOR_GLYPH_W = 24
CONTACTOR_GLYPH_H = 24

# Body colours, as RGB rather than as fill indices: `theme.resolve_fill_index` turns each into a
# STATIC mode-7 index on the image being built (13.6 offers 15 for the blue and 35 for the cyan),
# so the pair follows the firmware's own palette instead of pinning numbers that mean nothing on
# another build. These are the standard Firefly button pair the owner asked for on 2026-09-22 --
# exactly what Water Pump, Starlink, Overflow and Tank Heater rest at and light up to.
BATT_HEAT_OFF_RGB = (65, 81, 163)          # button blue  -- heater off
BATT_HEAT_ON_RGB = (0, 174, 239)           # cyan         -- heater on

# ONE switch owns the whole button, over four arms that each draw EVERY element: capsule, caption
# and (when the contactor is closed) the glyph. That is not redundancy, it is the fix for both
# repaint defects the owner reported on v31 (2026-09-22). A switch repaints only the arm it
# selects, so v31's two independent switches each left something stale: the body switch's capsule
# painted over the sibling 'Batt Heat' caption, which nothing then redrew, and the glyph switch's
# off arm drew nothing at all, so the red glyph stayed lit. Because every arm here repaints the
# opaque full-size capsule first, whatever the previous arm drew is covered by construction.
#
# The arms are keyed by the value `bitcombine` folds the two receive slots into:
# bit 0 = heater on, bit 1 = contactor closed. The 0xFFFF arm is the firmware's default/stale arm
# and carries state 0 (everything off), which is also what the routine writes before the first
# frame arrives. `grow_match_table` needs an even arm count; four is even.
BATT_HEAT_HEATER_WEIGHT = 1
BATT_HEAT_CONTACTOR_WEIGHT = 2
#                    match   heater on  contactor closed
BATT_HEAT_STATE_ARMS = ((0xFFFF, False, False),      # default + "nothing on yet"
                        (1,      True,  False),
                        (2,      False, True),
                        (3,      True,  True))
BATT_HEAT_STATE_MATCH_VALUES = tuple(m for m, _h, _c in BATT_HEAT_STATE_ARMS)
# A one-bit BMS status button (v49's Ignore Alarms): default/stale and 0 read off, 1 reads on.
BMS_BIT_STATE_ARMS = ((0xFFFF, False, False),
                      (1,      True,  False))


def contactor_glyph_dx(w: int) -> int:
    """The contactor glyph's x inset from the capsule origin for a `w`-wide button: 26 px in
    from the capsule's right edge, so it stays top-right at any width (92 at the stock 122)."""
    return (w - 2 * PUMP_OUTLINE_INSET) - 26


def build_batt_heat_button(donors: bytes, *, touch_id: int,
                           x: int = BATT_HEAT_X, y: int = BATT_HEAT_Y,
                           w: int = BATT_HEAT_W, h: int = BATT_HEAT_H,
                           text: str = BATT_HEAT_TEXT) -> list[bytes]:
    """The battery-heater button's record fragments, in flow order: an absolute anchor box, a
    solid-filled capsule button, its centred 'Batt Heat' caption, and a wtype-12 touch region
    carrying `touch_id` (the command group). Every fragment is cloned from a STOCK donor read
    from `donors` (the stock image) -- object 829's own records are the rebuilt compact frame by
    the time this runs, so the stock all-in-one 829 is the only stable source for its records.

    The button is a solid capsule (Low Volts box donor 285[0]) rather than the outlined section
    frame, so it reads as a pressable button like Pump Link / Low Volts, not a boxed label. Its
    fill is repointed off the live Low Volts cell (index 45) to a static colour, so it does not
    track any state.
    """
    inset = PUMP_OUTLINE_INSET
    # [0] absolute anchor + dark backing box: base-mode 0 on x and y (829[7] is the only absolute
    # box donor), which both places the button and establishes the flow origin the capsule below
    # inherits. Left-aligned so the donor's align-3 does not shift the column off its anchor.
    box = _clone_with_geom(donors, PUMP_FRAME_DONOR, PUMP_BOX_REC, {0: x, 1: y, 2: w, 3: h})
    _set_align(box, 1)
    # [1] a flow anchor insetting the capsule a couple px inside the backing box.
    anchor = _flow_anchor(inset, inset)
    # [2] the solid capsule button (285[0]), widened, fill repointed off cell 45 to a static colour.
    btn = _clone_with_geom(donors, PUMP_BUTTON_BODY_DONOR, PUMP_BUTTON_BOX_REC,
                           {2: w - 2 * inset, 3: h - 2 * inset})
    box_rec = _object_records(donors, PUMP_BUTTON_BODY_DONOR)[PUMP_BUTTON_BOX_REC]
    fill_off = box_rec.color_off[0]
    if fill_off is None:
        raise ValueError("Low Volts box donor 285[0] has no inline fillA colour to repoint")
    struct.pack_into("<H", btn, fill_off - box_rec.offset, BATT_HEAT_FILL_INDEX)
    # [3] the caption, widened and relabelled ('Low\nVolts' -> 'Batt Heat', 9 over 9).
    label = _clone_with_geom(donors, PUMP_BUTTON_BODY_DONOR, PUMP_BUTTON_LABEL_REC,
                             {2: w - 2 * inset, 3: h - 2 * inset})
    label = _relabel(donors, PUMP_BUTTON_BODY_DONOR, PUMP_BUTTON_LABEL_REC, text, label)
    # [4] touch: 315[9] widened to the box, carrying the command-group id.
    touch = _clone_with_geom(donors, CLUSTER_DONOR_OBJECT, PUMP_BUTTON_TOUCH_REC,
                             {2: w - 2 * inset, 3: h - 2 * inset})
    _set_touch_id(touch, touch_id)
    return [bytes(box), anchor, bytes(btn), bytes(label), bytes(touch)]


def _red_glyph_record(dx: int, dy: int, w: int, h: int,
                      bgr: tuple[int, int, int] = CONTACTOR_GLYPH_RED_BGR) -> bytes:
    """A flow-relative wtype-11 icon drawing the battery-heater-contactor glyph (font slot 23
    char '2') at `(dx, dy)` in a `w` x `h` box, tinted `bgr` (red by default; `battglyph` passes
    white) via a fillA mode-3 inline BGR.

    Same shape as `cornermark.build_mark_record` (flow-relative box, one-byte label, four declared
    coordinates) but with header byte 3 low bits = 3 so fillA is a mode-3 inline colour, the three
    BGR bytes injected immediately after the header (the colour block is read before geometry).
    `cornermark.build_mark_record` alone gives a mode-0 (inherit -> heuristic white) icon, which is
    why this is authored here."""
    rec = (bytes([0x0B, 0x55, 0x0F, 0x03, 0x00, 0x00, 0x00, 0x00])   # b3=0x03 -> fillA mode 3
           + bytes(bgr)
           + struct.pack("<hhhh", dx, dy, w, h)
           + bytes([0x01, CONTACTOR_GLYPH_FONT_SLOT, 0xFF, 0x02, 0x02, 0x0A, 0x00])
           + bytes([1]) + CONTACTOR_GLYPH_CHAR.encode("latin-1") + b"\x00")
    dec = records.decode_record(rec, 0)
    ink = None
    for role, mode, value in dec.colors:
        if role == "fillA":
            ink = (mode, value)
    if not (dec.wtype == 11 and dec.label == CONTACTOR_GLYPH_CHAR
            and dec.font == CONTACTOR_GLYPH_FONT_SLOT
            and dec.size == len(rec) and dec.base_modes[:4] == [1, 1, 1, 1]
            and layout._s16(dec.geom[0]) == dx and layout._s16(dec.geom[1]) == dy
            and dec.geom[2] == w and dec.geom[3] == h
            and ink == (3, bgr[0] | (bgr[1] << 8) | (bgr[2] << 16))):
        raise ValueError("authored contactor glyph record failed its self-decode: %s" % (rec.hex(),))
    return rec


def build_state_arm(donors: bytes, *, fill_index: int, label_record: bytes, glyph: bool,
                    w: int = BATT_HEAT_W, h: int = BATT_HEAT_H,
                    text: str = BATT_HEAT_TEXT) -> bytes:
    """One arm of the Batt Heat state switch: the WHOLE button as that state draws it.

    Always the opaque capsule (Low Volts box donor 285[0], widened, its fillA mode-7 index
    repointed to `fill_index`) followed by `label_record` -- the object's own 'Batt Heat' caption,
    passed in verbatim so the arms cannot drift away from the caption the button already draws.
    When `glyph`, the red contactor icon and the empty record that cancels the icon's own offset
    follow, leaving the arm flow-neutral.

    **The capsule and the caption are in EVERY arm on purpose.** The panel repaints only the
    selected arm, so anything an arm omits keeps the previous arm's pixels -- which is exactly how
    v31 lost its caption on enable and kept a lit glyph on disable. The capsule is opaque and
    covers the full button, so drawing it first erases whatever the outgoing arm left, including
    the glyph. `<count>` + records, padded to 4.
    """
    inset = PUMP_OUTLINE_INSET
    btn = _clone_with_geom(donors, PUMP_BUTTON_BODY_DONOR, PUMP_BUTTON_BOX_REC,
                           {2: w - 2 * inset, 3: h - 2 * inset})
    box_rec = _object_records(donors, PUMP_BUTTON_BODY_DONOR)[PUMP_BUTTON_BOX_REC]
    fill_off = box_rec.color_off[0]
    if fill_off is None:
        raise ValueError("Low Volts box donor 285[0] has no inline fillA colour to repoint")
    struct.pack_into("<H", btn, fill_off - box_rec.offset, fill_index)
    parts = [bytes(btn), label_record]
    if glyph:
        dx = contactor_glyph_dx(w)
        parts.append(_red_glyph_record(dx, CONTACTOR_GLYPH_DY,
                                       CONTACTOR_GLYPH_W, CONTACTOR_GLYPH_H))
        parts.append(cornermark.build_empty_record(-dx, -CONTACTOR_GLYPH_DY))
    payload = cornermark._object(parts)
    chk = records.object_records(payload, 0)
    if not (len(chk) == len(parts) and chk[0].wtype == 33
            and chk[0].colors[0] == ("fillA", 7, fill_index)
            and chk[0].geom[2:4] == [w - 2 * inset, h - 2 * inset]
            and chk[1].wtype == 11 and chk[1].label == text
            and any(r.font == CONTACTOR_GLYPH_FONT_SLOT for r in chk) == glyph):
        raise ValueError("Batt Heat state arm (fill %d, glyph %s) failed its self-decode"
                         % (fill_index, glyph))
    return payload


def _batt_heat_body_records(recs, object_index: int,
                            text: str = BATT_HEAT_TEXT) -> tuple[int, int]:
    """`(capsule_index, caption_index)` of the Batt Heat button inside object `object_index`'s
    record stream -- the static capsule the state switch replaces and the caption it absorbs.
    Both must be unique; object 829 also carries the compact AES frame, so a second match would
    mean the wrong record was about to be rewritten. The capsule is the static fill-35 one
    directly before the caption: v49 carries a second BMS button (Ignore Alarms) with the same
    static fill, so "the only fill-35 capsule" no longer names one button."""
    cap = [i for i, r in enumerate(recs[:-1])
           if r.wtype == 33 and r.colors and r.colors[0] == ("fillA", 7, BATT_HEAT_FILL_INDEX)
           and recs[i + 1].wtype == 11 and recs[i + 1].label == text]
    if len(cap) != 1:
        raise ValueError("expected exactly one batt-heat capsule (wtype-33 fill %d) before a %r "
                         "caption in object %d, found %s"
                         % (BATT_HEAT_FILL_INDEX, text, object_index, cap))
    lab = [i for i, r in enumerate(recs) if r.wtype == 11 and r.label == text]
    if len(lab) != 1:
        raise ValueError("expected exactly one %r caption in object %d, found %s"
                         % (text, object_index, lab))
    return cap[0], lab[0]


def add_batt_heat_state_switch(data: bytes, *, object_index: int, value_slot: int, donors: bytes,
                               off_index: int, on_index: int, w: int = BATT_HEAT_W,
                               h: int = BATT_HEAT_H, text: str = BATT_HEAT_TEXT,
                               arms: tuple = BATT_HEAT_STATE_ARMS,
                               grow: bool = True) -> tuple[bytes, dict]:
    """Replace the static batt-heat capsule on object `object_index` with ONE wtype-6 switch over
    the four whole-button arms of `BATT_HEAT_STATE_ARMS`, reading the combined-state cell
    `value_slot` (`bitcombine` folds both BMS bits into it: bit 0 heater, bit 1 contactor).

    `off_index` / `on_index` are the static mode-7 fill indices for the blue and cyan body
    colours; the caller resolves them from `BATT_HEAT_OFF_RGB` / `BATT_HEAT_ON_RGB` through
    `theme.resolve_fill_index` so the pair is the firmware's own palette on whatever build is
    being patched.

    The caption MOVES into the arms rather than being copied into them: a wtype-11 label is in the
    layout flow gate, so leaving the original in the record stream draws 'Batt Heat' twice, once at
    the pen the arm's own caption left (the first v32 render showed exactly that). Because the arm
    carries the caption record byte for byte, the flow it commits is the flow the stream record
    used to commit, so the touch region after the switch does not move. The capsule is wtype-33,
    which is not in the flow gate, and the glyph cancels its own offset, so nothing else shifts
    either. The switch carries the redraw-enroll bit (header[4] & 0x20) inherited from the stock
    enable-pill switch donor, which is what makes the panel repaint the arm live.
    """
    out = data
    gs, tab = guidata.guidata_extent(out)[0], guidata.object_tables(out)[1]
    recs = records.object_records(out, gs + tab[object_index])
    _cap, lab = _batt_heat_body_records(recs, object_index, text)
    lrec = recs[lab]
    label_record = out[lrec.offset:lrec.offset + lrec.size]

    arm_objects = [
        build_state_arm(donors, fill_index=on_index if heater_on else off_index,
                        label_record=label_record, glyph=contactor_on, w=w, h=h, text=text)
        for _match, heater_on, contactor_on in arms
    ]
    if grow:
        out, arms_base = switchbuild.append_arms(out, arm_objects, [m for m, _h, _c in arms])
    else:
        landed = []
        for arm in arm_objects:
            out, _e, i = insert._append_object(out, arm)
            landed.append(i)
        if landed != list(range(landed[0], landed[0] + len(arm_objects))):
            raise ValueError("Batt Heat state arms did not land contiguously (%s)" % landed)
        arms_base = landed[0]
    switch = switchbuild.author_switch(out, value_slot=value_slot, arms_base=arms_base,
                                       count=len(arms))

    gs, tab = guidata.guidata_extent(out)[0], guidata.object_tables(out)[1]
    recs = records.object_records(out, gs + tab[object_index])
    cap, lab = _batt_heat_body_records(recs, object_index, text)
    stream = [out[r.offset:r.offset + r.size] for r in recs]
    stream[cap] = switch
    del stream[lab]                                      # the caption now lives in every arm
    out, _ = relocate.replace_object_records(out, object_index, cornermark._object(stream))
    idxs = list(range(arms_base, arms_base + len(arm_objects)))
    return out, {"arms_base": arms_base, "arm_objects": idxs, "value_slot": value_slot,
                 "off_index": off_index, "on_index": on_index}


def build_link_arm(data: bytes, donor: int, text: str) -> bytes:
    """One Pump Link state arm: the enable-pill arm `donor` cloned verbatim, both records
    widened to the button's 220 x 44, the caption rebuilt as `text`. `<count=2>` + records,
    padded to 4. The capsule's fill index is the donor's own static colour (10 or 35)."""
    recs = _object_records(data, donor)
    if [r.wtype for r in recs] != [33, 11] or recs[0].colors[0][1] != 7:
        raise ValueError(
            f"object {donor} is not a (capsule, caption) arm with a mode-7 fill: "
            f"{[r.wtype for r in recs]}")
    box = _clone_with_geom(data, donor, LINK_ARM_BOX_REC, {2: PUMP_CONTENT_W, 3: PUMP_BUTTON_H})
    cap = _clone_with_geom(data, donor, LINK_ARM_LABEL_REC, {2: PUMP_CONTENT_W, 3: PUMP_BUTTON_H})
    cap = _relabel(data, donor, LINK_ARM_LABEL_REC, text, cap)
    payload = bytes([2]) + bytes(box) + bytes(cap)
    payload += b"\x00" * (-len(payload) % 4)
    chk = records.object_records(payload, 0)
    assert [r.wtype for r in chk] == [33, 11] and chk[1].label == text \
        and chk[0].geom[2:4] == [PUMP_CONTENT_W, PUMP_BUTTON_H], "link arm failed its self-decode"
    return payload


def build_readout_arms(data: bytes, readout_slot: int) -> tuple[bytes, bytes]:
    """The two Auto Off readout arms: `(disabled, number)`.

    The stock stepper draws its value as a pair -- `793[5]`, a wtype-8 numeric on the readout
    slot, then `793[6]`, the wtype-0 'Min' unit. Both arms are built from exactly those two
    donor records so the enabled arm is the pair verbatim and the disabled arm cannot drift
    away from it typographically:

      disabled (match 0)   a flow anchor carrying the NUMBER record's own `(x, y)` offset,
                           then `793[6]` relabelled 'Off'. Three characters over 'Min', so the
                           label is same-size and `_relabel` rebuilds nothing. The number is
                           simply absent -- there is no minute count to show when the timer is
                           off, and a '0 Min' that means "never" is the reading this arm exists
                           to remove.

                           The anchor is what puts 'Off' on the value line. In the enabled arm
                           'Min' inherits its position from the pen the wtype-8 left at
                           `(105, 70)`; with the number gone there is no such pen, and the
                           label lands at the switch origin -- 105 px left and 70 px high, which
                           is what the first build of this arm actually did. Reading the offset
                           off the donor record rather than writing the two numbers here keeps
                           the two arms on one line by construction.
      number   (default)   `793[5]` retargeted to `readout_slot` + `793[6]` verbatim, i.e. what
                           the section emitted before this switch existed.

    Both payloads are `<count>` + records, padded to 4 like every other appended object.
    """
    recs = _object_records(data, PUMP_STEPPER_DONOR)
    vrec = recs[PUMP_STEPPER_VALUE_REC]
    if vrec.wtype != 8 or vrec.value_slot != PUMP_STEPPER_STOCK_SLOT:
        raise ValueError(
            f"object {PUMP_STEPPER_DONOR} record {PUMP_STEPPER_VALUE_REC} is wtype {vrec.wtype} "
            f"on slot {vrec.value_slot}, not the stock numeric readout this route was derived "
            f"against")
    if recs[PUMP_STEPPER_UNIT_REC].wtype != 0 or recs[PUMP_STEPPER_UNIT_REC].label != "Min":
        raise ValueError(
            f"object {PUMP_STEPPER_DONOR} record {PUMP_STEPPER_UNIT_REC} is not the stock 'Min' "
            f"unit (wtype {recs[PUMP_STEPPER_UNIT_REC].wtype}, "
            f"label {recs[PUMP_STEPPER_UNIT_REC].label!r})")

    value = bytearray(_record_bytes(data, PUMP_STEPPER_DONOR, PUMP_STEPPER_VALUE_REC))
    struct.pack_into("<H", value, records.value_slot_offset(data, vrec) - vrec.offset,
                     readout_slot & 0xFFFF)
    unit = _record_bytes(data, PUMP_STEPPER_DONOR, PUMP_STEPPER_UNIT_REC)
    off = _relabel(data, PUMP_STEPPER_DONOR, PUMP_STEPPER_UNIT_REC, PUMP_READOUT_OFF_TEXT,
                   bytearray(unit))

    anchor = _flow_anchor(layout._s16(vrec.geom[0] or 0), layout._s16(vrec.geom[1] or 0))

    payloads = []
    for parts in ([anchor, bytes(off)], [bytes(value), bytes(unit)]):
        payload = bytes([len(parts)]) + b"".join(parts)
        payload += b"\x00" * (-len(payload) % 4)
        chk = records.object_records(payload, 0)
        assert len(chk) == len(parts), "readout arm failed its self-decode"
        payloads.append(payload)
    assert records.object_records(payloads[0], 0)[1].label == PUMP_READOUT_OFF_TEXT
    assert records.object_records(payloads[1], 0)[0].value_slot == readout_slot
    return payloads[0], payloads[1]


def append_readout_arms(data: bytes, readout_slot: int) -> tuple[bytes, int]:
    """Grow the match table by `READOUT_MATCH_VALUES` and append the NUMBER arm then the
    disabled arm. Returns `(out, arms_base)` -- the number arm is object `arms_base` (the
    `0xFFFF` default, which the firmware only honours at the base) and the disabled arm is
    `arms_base + 1`, the contiguous band a wtype-6 switch selects with `count = 2`."""
    out, _edits = relocate.grow_match_table(data, READOUT_MATCH_VALUES)
    disabled, number = build_readout_arms(data, readout_slot)
    out, _edits, num_idx = insert._append_object(out, number)
    out, _edits, off_idx = insert._append_object(out, disabled)
    if off_idx != num_idx + 1:
        raise ValueError(f"readout arms did not land contiguously ({num_idx}, {off_idx})")
    return out, num_idx


def append_link_arms(data: bytes) -> tuple[bytes, int]:
    """Grow the match table by [0, 1] and append the OFF arm (match 0) then the ON arm
    (match 1). Returns `(out, arms_base)`: the off arm is object `arms_base`, the on arm
    `arms_base + 1` -- a contiguous band a wtype-6 switch selects with `count = 2`."""
    out, _edits = relocate.grow_match_table(data, [0, 1])
    out, _edits, off_idx = insert._append_object(out, build_link_arm(data, LINK_ARM_OFF_DONOR,
                                                                       PUMP_BUTTON_TEXT))
    out, _edits, on_idx = insert._append_object(out, build_link_arm(data, LINK_ARM_ON_DONOR,
                                                                      PUMP_BUTTON_TEXT))
    if on_idx != off_idx + 1:
        raise ValueError(f"link arms did not land contiguously ({off_idx}, {on_idx})")
    return out, off_idx


def _guard_touch_id(data: bytes, index: int, j: int, expect: int) -> None:
    """Refuse to retarget a wtype-12 region that is not the stock donor this route was derived
    against (the same guard `build_cluster` applies to the pill and the gear)."""
    rec = _object_records(data, index)[j]
    got = data[rec.end - 2] | (data[rec.end - 1] << 8)
    if rec.wtype != 12 or got != expect:
        raise ValueError(
            f"object {index} record {j} is wtype {rec.wtype} carrying id {got}, expected a "
            f"wtype-12 region with the stock id {expect}")

def _cluster_container_record(child_index: int, y: int = CLUSTER_CONTAINER_Y) -> bytes:
    """A 14-byte wtype-5 container record that draws `child_index` UNCONDITIONALLY at the AGS
    cluster origin. Mirrors 830[5]'s placement (base-mode 0 for both coords, absent x + the y
    coord, in-register indices 1/1 so it inherits the same register-driven origin the AGS switch
    used) but is a plain container (no value_slot), not a slot-37 switch -- 829 renders only in
    AES, so the cluster is always drawn. Because records [0..4] mirror 830[0..4], the flow/register
    state entering this record equals 830[5]'s, so the child lands at the identical origin.

    Byte layout (decoded and asserted at build time): header b0=0x45 (wtype 5 + bit6 set so the
    in-register byte is consumed), b1=0x50 (base_modes 0,0,1,1), b2=0x02 (geom coord 1 present),
    rest 0; then a18=0x11 (in-regs 1,1), the y u16, the R38 font byte 0xff (inherit), and the
    child-index u16.
    """
    rec = bytes([0x45, 0x50, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
                 0x11, y & 0xFF, (y >> 8) & 0xFF, 0xFF,
                 child_index & 0xFF, (child_index >> 8) & 0xFF])
    dec = records.decode_record(rec, 0)
    assert (dec.wtype == 5 and dec.child_index == child_index and dec.value_slot is None
            and dec.geom[0] is None and dec.geom[1] == y
            and dec.base_modes[:2] == [0, 0] and dec.size == len(rec)), \
        "cluster container record failed its self-decode"
    return rec


def build_compact_frame(data: bytes, cluster_index: int,
                        pump_section: list[bytes] | None = None) -> bytes:
    """Assemble the faithful compact AES frame's object payload (`count` byte + records) as a
    structural mirror of the AGS compact frame (object 830). Returns the body with NO trailing pad
    -- `relocate.replace_object_records` re-pads it to the 4-byte object boundary.

    Seven records (wtypes [33, 11, 5, 11, 5, 5, 6]): the frame capsule, the 'House' label + its
    voltage container (-> obj 443), the 'Chassis' label + its voltage container (-> obj 440), the
    compact-cluster container (-> the cloned cluster `cluster_index`, drawn unconditionally) and
    the inverter switch (-> obj 1420 band, gated on slot 113). Every record but the cluster
    container is a byte-verbatim clone of the corresponding 830 record, so the donor geometry is
    preserved and the AGS-referenced child objects (443/440/1420) render identically.

    `pump_section` (default `None`) is `build_pump_section`'s list of fragments, APPENDED after
    the inverter switch. `None` emits exactly the seven records this frame has always had, so
    the built image is byte-for-byte what it was before Task 7. The section anchors itself
    absolutely (its first record is base-mode 0 on both coords) and every later coordinate in
    it is relative to that anchor, so appending it cannot move any of the seven.
    """
    fragments = [
        _record_bytes(data, AGS_COMPACT_FRAME, 0),   # frame capsule (box [200,60,460,80])
        _record_bytes(data, AGS_COMPACT_FRAME, 1),   # 'House' label
        _record_bytes(data, AGS_COMPACT_FRAME, 2),   # House voltage container (slot 209 -> obj 443)
        _record_bytes(data, AGS_COMPACT_FRAME, 3),   # 'Chassis' label
        _record_bytes(data, AGS_COMPACT_FRAME, 4),   # Chassis voltage container (slot 205 -> obj 440)
        _cluster_container_record(cluster_index),    # compact cluster (unconditional -> 315')
        _inverter_switch_record(data),               # inverter switch (slot 113 -> obj 1420 band)
    ]
    if pump_section:
        fragments.extend(pump_section)                # the outlined Water Pump section
    return bytes([len(fragments)]) + b"".join(fragments)


def _inverter_switch_record(data: bytes) -> bytes:
    """830[`INVERTER_SWITCH_REC`] with its absolute y advanced by `INVERTER_COLUMN_DY`, so the
    Inverter enable/disable block's top lands on the Engine control's row instead of a pixel above
    it. Same-size (an existing geom u16), and confined to our frame's own copy of the record."""
    rec = _object_records(data, AGS_COMPACT_FRAME)[INVERTER_SWITCH_REC]
    frag = bytearray(_record_bytes(data, AGS_COMPACT_FRAME, INVERTER_SWITCH_REC))
    if INVERTER_COLUMN_DY:
        off = records.geom_offset(data, rec, 1)
        if off is None:
            raise ValueError(
                f"object {AGS_COMPACT_FRAME} record {INVERTER_SWITCH_REC} has no absolute y to "
                "shift -- is this the stock compact frame?")
        struct.pack_into("<h", frag, off - rec.offset,
                         struct.unpack_from("<h", data, off)[0] + INVERTER_COLUMN_DY)
    return bytes(frag)


def swap_electrical_to_compact(data: bytes,
                               pump_section_ids: tuple[int, int, int, int] | None = None,
                               link_display_slot: int | None = None,
                               off_at_zero: bool = False
                               ) -> tuple[bytes, int, int]:
    """Rebuild object 829 as the faithful compact frame, cloning the cluster (with the AES
    retargets) as a new appended object. Returns `(out, aes_body_index, cluster_index)`.

    Steps (each threads the previous image; donor records are always read from the STOCK `data`,
    whose indices the top-appends never move):
      1. Clone object 829's 52-record all-in-one body out to a fresh object (`aes_body_index`) --
         the settings sub-page body `build_settings_screen` hangs off screen 28.
      2. Clone the real AES Engine control (`build_engine`: 353 + the invisible flow spacer) and
         append it (`engine_index`).
      2b. With `link_display_slot`, append the two Pump Link arms (`append_link_arms`) so the
          section's switch can name them.
      3. Clone the AGS cluster with the AES retargets (`build_cluster`, pointing the engine
         container at `engine_index`, the enable pill at the AES Notice action, and the gear
         label/id) and append it (`cluster_index`).
      4. Build the faithful 7-record frame (`build_compact_frame`, pointing the cluster container
         at `cluster_index`) and replace object 829's record stream in place with it
         (`relocate.replace_object_records`).

    No AGS-path object (5, 830, 315, 795, 45, 486-488) is written; 830/315 are read as donors and
    cloned, and 353 (with its 249/251 and 526-529 children) is CLONED by reference (the clone holds
    353's records verbatim; those children are referenced by index, never written) -- so the AGS
    branch stays byte-golden.

    `pump_section_ids` (default `None`) is the positional tuple
    `(link_touch_id, plus_touch_id, minus_touch_id, readout_slot)` -- the group-164 Pump Link
    touch, the two arrow groups `optionrow.build_config_stepper` created, and the variable-table
    slot the readout names. That slot is **NOT** re-typed: it is already variable type 1 (a
    single `ldrb`, which is exactly the width the routine writes), the
    `statusbind.set_variable_type(..., VARIABLE_TYPE_U16)` step in the plan was deliberately
    skipped, and `recipe_ops.py`'s `add_pump_auto_off.verify` asserts the type stays 1 -- so this
    function must be given an already-correctly-typed slot, never one it expects to be widened. Given it, the frame carries the
    outlined Water Pump section (`build_pump_section`). Left `None` this function emits the
    identical image it did before Task 7, byte for byte.

    `off_at_zero` (default `False`, v20) needs `pump_section_ids` too. It appends the two Auto
    Off readout arms (`append_readout_arms`) and has `build_pump_section` emit a wtype-6 switch
    on the readout slot in place of the (number, 'Min') pair, so a minute count of 0 -- the
    disabled setting since `pumpauto.MINUTES_MIN` became 0 -- reads 'Off' instead of '0 Min'.
    Left `False` the section keeps the plain numeric readout and the image is unchanged.

    `link_display_slot` (default `None`) needs `pump_section_ids` -- there is no Pump Link button
    without the section. When given, the two Pump Link state arms are appended FIRST
    (`append_link_arms`, before the frame is built, since the section's wtype-6 switch names their
    indices) and `build_pump_section` emits that switch (v16) instead of the static box+label pair
    (pre-v16). Left `None`, the section keeps the pre-v16 shape unchanged.
    """
    out, aes_body_index = clone_all_in_one(data)
    out, _edits, engine_index = insert._append_object(out, build_engine(data))
    out, _edits, cluster_index = insert._append_object(out, build_cluster(data, engine_index))
    pump_section = None
    link_arms_base = None
    if off_at_zero and pump_section_ids is None:
        raise ValueError("off_at_zero needs pump_section_ids (there is no readout without the "
                         "section)")
    if link_display_slot is not None:
        if pump_section_ids is None:
            raise ValueError(
                "link_display_slot needs pump_section_ids (there is no button without the section)")
        out, link_arms_base = append_link_arms(out)         # arms first: the switch names them
    readout_arms_base = None
    if pump_section_ids is not None:
        link_touch_id, plus_touch_id, minus_touch_id, readout_slot = pump_section_ids
        if off_at_zero:
            out, readout_arms_base = append_readout_arms(out, readout_slot)
        pump_section = build_pump_section(
            data, link_touch_id=link_touch_id, plus_touch_id=plus_touch_id,
            minus_touch_id=minus_touch_id, readout_slot=readout_slot,
            link_display_slot=link_display_slot, link_arms_base=link_arms_base,
            readout_arms_base=readout_arms_base)
    frame = build_compact_frame(data, cluster_index, pump_section=pump_section)
    out, _edits = relocate.replace_object_records(out, ELECTRICAL_AES_BODY, frame)
    return out, aes_body_index, cluster_index


def _touch_id_carriers(data: bytes, touch_id: int) -> list[int]:
    """Every object index carrying `touch_id` on a wtype-12 region, displayed or not."""
    gs, _ge = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    out = []
    for i in range(len(tab)):
        try:
            recs = records.object_records(data, gs + tab[i])
        except (IndexError, ValueError):
            continue
        for r in recs:
            if r.wtype == 12 and (data[r.end - 2] | (data[r.end - 1] << 8)) == touch_id:
                out.append(i)
                break
    return out


def displayed_carriers(data: bytes, touch_id: int) -> list[int]:
    """Every DISPLAYED object whose `wtype-12` region carries `touch_id`.

    "Displayed" = reachable from a screen root through the `child_index` graph
    (`layout.screen_object_map` roots, walked via `records.child_index_offset`). The hit test
    returns the LOWEST matching id, so if any displayed region shares `touch_id` with the gear it
    would shadow (or be shadowed by) it -- this is the collision gate for repurposing an orphaned
    action leaf. Stock 0x7DE is carried only by the non-displayed objects 1052/1053, so this
    returns `[]` for it.
    """
    from . import gestures

    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]

    def child_refs(i: int) -> set[int]:
        refs = set()
        for r in records.object_records(data, gs + table[i]):
            ci = records.child_index_offset(data, r)
            if ci is not None:
                v = data[ci] | (data[ci + 1] << 8)
                if v != 0xFFFF and v < len(table):
                    refs.add(v)
        return refs

    seen: set[int] = set()
    stack = list(layout.screen_object_map(data).values())
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        stack.extend(c for c in child_refs(x) if c not in seen)

    return [i for i in sorted(seen) if touch_id in gestures.touch_ids(data, i, table)]


def _reachable_from(data: bytes, root: int) -> set[int]:
    """`{root}` plus every object reachable from it through the `child_index` graph -- the AES
    page subtree. Used to tell the gear's own carrier (the cloned cluster, a descendant of the
    compact frame) apart from a genuine hit-test collision elsewhere."""
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    seen: set[int] = set()
    stack = [root]
    while stack:
        x = stack.pop()
        if x in seen or not (0 <= x < len(table)):
            continue
        seen.add(x)
        try:
            recs = records.object_records(data, gs + table[x])
        except (IndexError, ValueError):
            continue
        for r in recs:
            if r.child_index is not None and r.child_index not in seen:
                stack.append(r.child_index)
    return seen


def wire_settings_nav(data: bytes, touch_id: int) -> tuple[bytes, list]:
    """Repoint the 0x7DE action-dispatcher (tap) leaf from screen 14 to the AES settings screen (28).

    A same-size, GUARDED 2-byte GuiCode edit: `movs r3,#0xe` -> `movs r3,#0x1c` at
    `SETTINGS_LEAF_SITE`. This is the correct mechanism for a TAP on the gear -- the action
    dispatcher `FUN_0024ACB2`, the path the stock settings buttons use -- unlike the expired-deadline
    "hold" consumer, which never fires for a tap-registered id (see `SETTINGS_NAV_TOUCH_ID`).

    `touch_id` must be `SETTINGS_NAV_TOUCH_ID` (0x7DE): this route repurposes that one orphaned leaf,
    and `retarget_compact_settings_button` stamps the gear region with the same id, so the two agree.
    Refuses if any DISPLAYED region already carries the id (a hit-test collision) or if the leaf's
    stock bytes do not match (a drifted build). Only rewrites GuiCode bytes and does NOT reseal --
    the caller (Task 5's recipe engine) reseals once. This 2-byte edit is HARDWARE-AFFECTING and
    belongs in the `unverified` manifest; the coach is the final arbiter.
    """
    if touch_id != SETTINGS_NAV_TOUCH_ID:
        raise ValueError(
            f"wire_settings_nav repurposes the orphaned 0x7DE action leaf, so touch_id must be "
            f"{SETTINGS_NAV_TOUCH_ID:#06x}, got {touch_id:#06x}")

    # The gear region itself legitimately carries the id -- in rev4 it sits in the cloned cluster
    # (a descendant of the compact frame 829), stamped by `build_cluster`. That whole AES subtree
    # is the intended carrier; any displayed region OUTSIDE it on the same id is a real hit-test
    # collision (the test returns the lowest match).
    aes_subtree = _reachable_from(data, ELECTRICAL_AES_BODY)
    carriers = [c for c in displayed_carriers(data, touch_id) if c not in aes_subtree]
    if carriers:
        raise ValueError(
            f"touch id {touch_id:#06x} is carried by DISPLAYED object(s) {carriers}; repurposing "
            "its action leaf would collide (the hit test returns the lowest matching id)")

    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    off = section.offset + (SETTINGS_LEAF_SITE - base)
    got = bytes(data[off:off + 2])
    if got != SETTINGS_LEAF_STOCK:
        raise ValueError(
            f"0x7DE leaf screen constant: expected {SETTINGS_LEAF_STOCK.hex()} "
            f"(movs r3,#0xe) at {SETTINGS_LEAF_SITE:#x}, found {got.hex()}")

    new = thumb.movs_imm(3, SETTINGS_SCREEN_ID)
    thumb.assert_disassembles_to(new, SETTINGS_LEAF_SITE, [f"movs r3, #{SETTINGS_SCREEN_ID:#x}"])
    out = bytearray(data)
    out[off:off + 2] = new
    edits = [(off, got, bytes(new),
              f"0x7DE action leaf screen 14 -> {SETTINGS_SCREEN_ID} "
              f"(AES settings frame, object {AES_SETTINGS_ROOT})")]
    return bytes(out), edits


def retarget_inverter_gear(data: bytes, screen_id: int = INVERTER_SETTINGS_SCREEN_ID
                           ) -> tuple[bytes, list]:
    """Repoint the Inverter capsule's action leaf (id `INVERTER_GEAR_TOUCH_ID` = 0x7F7) from the
    read-only 'PD Inverter' readout (screen 39) to `screen_id`, default 14 -- object 17,
    'INVERTER / CHARGER SETTINGS', the page the HOME inverter's 3-second hold reaches.

    A same-size, GUARDED 2-byte GuiCode edit: `movs r3,#0x27` -> `movs r3,#<screen_id>` at
    `INVERTER_GEAR_LEAF_SITE`, inside the 0x7F7 action body the dispatcher branches to. Object
    1421 is the id's only carrier and renders on BOTH compact frames, so the change is global by
    construction -- the AGS page's inverter capsule follows the AES one. Refuses on any build whose
    leaf bytes are not the stock ones (already retargeted, or a drifted build) rather than
    corrupting them, and does NOT reseal: the caller reseals once.
    """
    if not (0 <= screen_id <= 0xFF):
        raise ValueError(f"screen id {screen_id} must fit `movs r3,#imm8` (0..255)")

    carriers = _touch_id_carriers(data, INVERTER_GEAR_TOUCH_ID)
    if carriers != [INVERTER_GEAR_BLOCK]:
        raise ValueError(
            f"touch id {INVERTER_GEAR_TOUCH_ID:#06x} is carried by {carriers}, expected only the "
            f"Inverter block {INVERTER_GEAR_BLOCK}; refusing to retarget an action whose carriers "
            "this route was not derived against")

    section = theme.guicode_section(data)
    base = theme.guicode_base(section)
    off = section.offset + (INVERTER_GEAR_LEAF_SITE - base)
    got = bytes(data[off:off + 2])
    if got != INVERTER_GEAR_LEAF_STOCK:
        raise ValueError(
            f"0x7f7 action leaf screen constant: expected {INVERTER_GEAR_LEAF_STOCK.hex()} "
            f"(movs r3,#0x27, screen 39) at {INVERTER_GEAR_LEAF_SITE:#x}, found {got.hex()}")

    new = thumb.movs_imm(3, screen_id)
    thumb.assert_disassembles_to(new, INVERTER_GEAR_LEAF_SITE, [f"movs r3, #{screen_id:#x}"])
    out = bytearray(data)
    out[off:off + 2] = new
    edits = [(off, got, bytes(new),
              f"0x7f7 action leaf (Inverter capsule, object {INVERTER_GEAR_BLOCK}) screen 39 "
              f"(PD Inverter, read-only) -> {screen_id} (INVERTER / CHARGER SETTINGS); shared with "
              "the AGS compact frame by design")]
    return bytes(out), edits
