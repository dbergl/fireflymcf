"""Generator for `docs/RECIPE-OPS.md`: the reference for every recipe op.

The parameter tables come from each op class's `PARAMS` JSON schema and the error codes from
its docstring, so they cannot drift from the code. The one-line purpose, the edit class and
the hardware status are curated in `OP_NOTES` below; `tests/test_ops_doc.py` fails when an op
is registered without a note, and when the committed document differs from this output.

Regenerate (also writes `schema/ffproj.schema.json`, `project_schema`):

    python3 -m fireflymcf.ops_doc
"""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

from . import recipe

DOC_PATH = Path("docs/RECIPE-OPS.md")

# Edit classes, in the order the document presents them.
CLASSES = [
    ("same-size", "Same-size edits",
     "Bytes change in place. Nothing moves, so no pointer needs repair. Lowest risk."),
    ("structural", "Structural edits (GuiData relocation)",
     "Records are inserted or replaced and every shifted pointer is repaired by "
     "`relocate.py`. Five gates re-derive each repair from the output."),
    ("guicode", "GuiCode-growing edits (authored ARM code)",
     "A routine is appended to the GuiCode section and chained onto the per-tick hook. "
     "Each routine is byte-identical to a coach-proven form apart from its chain target."),
    ("g12", "G12 section edits",
     "Confined to the G12 section, which the bootloader flashes on its own."),
    ("handle", "Handles (no byte change)",
     "Name something in the stock image for later ops to reference. The image is unchanged."),
]

# name -> (class, purpose, hardware status, where the proof is)
OP_NOTES: dict[str, tuple[str, str, str, str]] = {
    "rename_label": ("same-size",
        "Replace a label's text. Same-size by default (padded to the field); a longer text "
        "relocates when `allow_grow` is set.",
        "Flashed. Grown label PC.18.", "`docs/HARDWARE-VERIFIED.md`, `BUS-BASELINE.md` PC.18"),
    "move_element": ("same-size",
        "Set one record's geometry fields (x, y, width, height) inside an object.",
        "Flashed (reference build v1 onward).", "the reference coach's build log (not published)"),
    "move_attach": ("same-size",
        "Move where a page attaches a child object (its wtype-5 attach record).",
        "Flashed (the reference build).", "the reference coach's build log (not published)"),
    "set_font": ("same-size",
        "Change a record's font slot. The selector is persistent: later records inherit it.",
        "Flashed.", "`docs/HARDWARE-VERIFIED.md`"),
    "set_icon": ("same-size",
        "Repoint an icon record's font slot and/or character; optionally blank the caption.",
        "Flashed (the Starlink button).", "the reference coach's build log (not published)"),
    "recolor": ("same-size",
        "Repoint a button box's fill index to another colour in the firmware palette.",
        "Flashed.", "`docs/HARDWARE-VERIFIED.md`"),
    "remap_channel": ("same-size",
        "Make a labelled control command a different G12 output channel.",
        "Flashed with `move_display_list` (PC.13-16, v9). The default indicator retarget "
        "is UNVERIFIED.", "`BUS-BASELINE.md` PC.13-PC.16; `CompileResult.unverified`"),
    "tank_heater_names": ("same-size",
        "Widen the tank-heater diagnostic-name whitelists so floorplan WD names all four rows. "
        "Trade: SY loses Grey, WT loses Black.",
        "Flashed. PC.18b.", "`GUICODE.md` Finding 35"),
    "remove_option_interlocks": ("same-size",
        "Stop an OPTIONS row writing another row's selector (13.6: Progressive Inverter "
        "and the generator mode). Refuses on 14.7; no-op on 11.23.",
        "Flashed (reference build v7 onward). PC.21 shows the G12 does not re-impose it.",
        "`GUICODE.md` Finding 50, `docs/SETTLED-FINDINGS.md`"),
    "split_hold_region": ("structural",
        "Split a control's touch region into a tap strip and a hold strip, the hold routed "
        "to a screen.",
        "Flashed. PC.22 (reference build v7/v8).", "`CONTROL-BEHAVIOR.md` §9, `docs/SETTLED-FINDINGS.md`"),
    "add_button": ("structural",
        "Add a boxed HOME button wired to a G12 pin: status cell bound, command group "
        "allocated on the target channel.",
        "Flashed 2026-08-17.", "`docs/HARDWARE-VERIFIED.md`"),
    "add_glyph_button": ("structural",
        "Clone a donor control into a bare button shell on a page, sized w x h, unwired.",
        "Flashed (the Starlink button). Must be followed by `wire_button`.",
        "the reference coach's build log (not published) \"The Starlink button\""),
    "add_voltage_battery": ("guicode",
        "Clone a stock battery graphic beside another and drive it from a bus voltage: "
        "isolate the clone's colour, gauge-level and value cells onto fresh SRAM, feed "
        "them from a per-tick routine that recolours the body green/red at a threshold "
        "and levels its fill over a voltage window, then reposition the pair and add "
        "the label the donor lacked. Ships as the coach 48V battery beside HOME's "
        "chassis 12V battery (object 902): sourced from `DC_SOURCE_STATUS_1` instance "
        "1, red below 50.4V / green at or above, fill empty at 48.0V to full at "
        "54.8V, labelled 'House'. `live_repaint` retargets the clone's enrolled wt5 "
        "container to watch the live coach cell so number+fill repaint live; "
        "`rescale_donor` repoints the ELECTRICAL coach battery (donor 443) onto the "
        "48V cells so it stops showing always-100%-green.",
        "VERIFIED. v25 flashed and verified 2026-09-12: number, fill and colour update "
        "live on HOME and the compact ELECTRICAL page.",
        "the reference coach's build log (not published) (v25); "
        "its design note (not published)"),
    "wire_button": ("structural",
        "Wire a button shell to a G12 pin: status cell, indicator, and its own toggle group.",
        "Flashed (the Starlink button).", "the reference coach's build log (not published) \"The Starlink button\""),
    "install_glyph": ("structural",
        "Write new artwork into an icon font as a glyph and point one character at it. "
        "Pixels travel in the project as hex rows.",
        "Flashed (the Starlink logo, the timer mark).", "`GUICODE.md` Findings 44, 46"),
    "set_gesture": ("same-size",
        "Convert a control's single toggle group to hold-to-toggle.",
        "Flashed 2026-08-19 (pins 28 and 29).", "`gestures.py`, `CONTROL-BEHAVIOR.md`"),
    "pin_filter_instance": ("same-size",
        "Narrow a receive-filter entry from any device to one RV-C instance (one byte).",
        "Flashed (v17 onward). Motivated by PC.46.", "`BUS-BASELINE.md` PC.46"),
    "remove_control": ("same-size",
        "Park a control off the 480 px canvas by rewriting its attach records' y. Bindings "
        "stay.",
        "UNVERIFIED. Never flashed.", "`CompileResult.unverified`"),
    "drop_from_master": ("same-size",
        "Remove one channel from a master control (its ON+OFF group pair), so the master no "
        "longer commands that load. Each naming binding gets DGN -> 0xFFFF and phase mask -> 0.",
        "Flashed 2026-09-14 (v26): the coach bath light (ch25) no longer follows Light Master.",
        "`mastergroup.py`, `CONTROL-BEHAVIOR.md` §3; the reference coach's build log (not published) \"Experimental v26\""),
    "add_low_volts_control": ("structural",
        "Copy the AGS Low Volts arm onto the AES ELECTRICAL view (records only, no recolour). "
        "Superseded by `add_aes_low_volts_trigger`; not in any flashed recipe.",
        "Superseded.", "the reference coach's build log (not published) \"The compact AES Electrical page\""),
    "follow_channel": ("guicode",
        "Watch one G12 output's status and mirror its ON/OFF onto another channel, gated by a "
        "repurposed OPTIONS row.",
        "Flashed 2026-08-31 (the light to pump follower).",
        "the reference coach's build log (not published) \"The gated light→pump follower\""),
    "add_option_follower": ("guicode",
        "Add a new OPTIONS row (a checkbox on a chosen config selector) that gates a "
        "channel follower.",
        "Flashed 2026-08-31. PC.45 (Pump Link).", "`BUS-BASELINE.md` PC.45"),
    "retarget_inverter_gear": ("same-size",
        "Send the Inverter capsule's gear to INVERTER / CHARGER SETTINGS (2-byte GuiCode edit).",
        "Flashed 2026-09-02 (v12).", "the reference coach's build log (not published) \"Experimental v12\""),
    "resolve_register": ("handle",
        "Name an existing bus-driven register (a variable-table slot) as a handle for later ops. "
        "Reuse-existing only.",
        "Tooling-verified; image unchanged.",
        "its design note (not published)"),
    "follow_register": ("guicode",
        "Watch any register for an edge and drive a channel, gated by register conditions.",
        "Routine shared with the flashed follower; no instance has been flashed. A coach check is "
        "warranted for a new watch register.",
        "its design note (not published)"),
    "add_register_readout": ("guicode",
        "Show any register as a live number on any page, or feed an existing readout's cell "
        "past a stock decode fence (`cell_slot` + `silence`).",
        "Flashed 2026-09-04 (v17, tier-2 cell) and 2026-09-05 (v18, `cell_slot`). Tier-3 "
        "(fresh SRAM) cells report unverified per instance.",
        "the reference coach's build log (not published) \"Experimental v17\", \"Experimental v18\"; PC.59, PC.60"),
    "swap_aes_electrical": ("structural",
        "Replace the AES ELECTRICAL page body with a compact frame mirroring the AGS one, "
        "and wire a settings sub-page.",
        "Flashed 2026-09-01.", "the reference coach's build log (not published) \"The compact AES Electrical page\""),
    "add_aes_low_volts_trigger": ("guicode",
        "Add a live, recolouring Low Volts arm indicator to the compact AES frame.",
        "Flashed 2026-09-01.", "the reference coach's build log (not published) \"The compact AES Electrical page\""),
    "add_pump_auto_off": ("guicode",
        "The Water Pump Auto Off feature: minutes stepper, readout, countdown routine, OFF frame.",
        "Flashed 2026-09-04 (v17) and 2026-09-06 (v21, `allow_disable`).",
        "the reference coach's build log (not published) \"Experimental v17\", \"Experimental v21\"; PC.59, PC.61"),
    "show_nav_slides_icon": ("same-size",
        "Reveal the nav rail's authored sixth icon and route it to a screen.",
        "Flashed 2026-09-04 (v17).", "`navicon.py`, the reference coach's build log (not published) \"Experimental v15\""),
    "pin_switch_arm": ("same-size",
        "Pin a type-6 switch to one arm regardless of its value slot (three u16s).",
        "Flashed 2026-09-05 (v18). PC.60.", "`switchpin.py`, `BUS-BASELINE.md` PC.60"),
    "fix_variant_gate": ("same-size",
        "Repair a control GuiCode disables every tick from a receive slot (13.6's "
        "`Shore Brkr Size` minus). Mode `selector` changes one GuiData byte.",
        "Flashed 2026-09-07 (v22), mode `selector`. PC.62. Other modes unverified.",
        "`GUICODE.md` Finding 68, `BUS-BASELINE.md` PC.62"),
    "add_corner_mark": ("structural",
        "Put a state-gated one-character mark in a corner of an existing button.",
        "Flashed 2026-09-06 (v21). PC.61.", "`cornermark.py`, the reference coach's build log (not published) \"Experimental v21\""),
    "append_filter": ("structural",
        "Subscribe to a DGN by appending a NEW receive entry (not retargeting one): table grown in "
        "the padding arena, count bumped in the descriptor and GuiCode header +0x20.",
        "Flashed 2026-09-22 (probe build \"v30 Probe New DGN\"): var 298 appended for 0x1FEA0, "
        "the HOME readout followed injected values 42 then 7.",
        "`src/fireflymcf/appendfilter.py`, `docs/HARDWARE-VERIFIED.md`"),
    "add_aes_batt_heat": ("structural",
        "Add a \"Batt Heat\" button to the compact AES frame: press-and-hold sends a 60-minute "
        "BMS heater enable, tap sends off (DGN `0x0EF70` to SA `0x70`).",
        "Flashed 2026-09-21. Coach-verified: frames captured on can1, heater engaged.",
        "its design note (not published)"),
    "add_alt_reboot": ("structural",
        "Add a \"REBOOT APS-500\" button beside CLEAR EEPROM on the ALTERNATOR SYSTEM page: a "
        "3-second hold sends the RV-C `GENERAL_RESET` reboot (`0x17F80`) to the APS-500 at SA "
        "`0x80`; a tap sends nothing.",
        "Coach-verified 2026-10-05 on v46: a 3-s hold sends one `GENERAL_RESET` and the APS-500 reboots.",
        "`src/fireflymcf/altreboot.py`; the hold nibble and held-only phase mask were proven "
        "in the hold-to-toggle trial (`docs/CONTROL-BEHAVIOR.md` §8)"),
    "add_aes_batt_heat_status": ("structural",
        "Show live BMS status on the \"Batt Heat\" button: blue/cyan body for the battery heater "
        "off/on, plus a red glyph for the battery's internal heater contactor (status DGN "
        "`0x18FF7070`), received on two APPENDED subscriptions folded per tick into one private "
        "display cell that drives one four-arm switch.",
        "Flashed 2026-09-22 (v32): injected 18FF70C0 frames transitioned through every "
        "heater/contactor edge; caption and glyph repaint correctly (receive half proven on v31).",
        "its design note (not published)"),
    "aes_card_layout": ("structural",
        "Lay the compact AES ELECTRICAL page out as HOME's 2x2 card grid -- Batteries, Inverter, "
        "Auto Engine Start, Water Pump -- with the controls at their stock sizes; pinned to the "
        "v47 shape of object 829 and the private AES cluster.",
        "Flashed 2026-10-15 (v48; its three draw defects fixed in v49, flashed the same day).",
        "the reference coach's build log (not published) \"v48's three coach defects\"; "
        "its design note (not published)"),
    "add_card_readouts": ("structural",
        "Draw captioned live readouts (caption over a right-justified value and its unit) inside an "
        "aes_card_layout card; each value names a draw:false add_register_readout cell or an "
        "existing display slot. Attached to 829 directly after the card titles.",
        "Flashed 2026-10-15 (v49, v50): the readouts are live on the coach.",
        "the reference coach's build log (not published) \"v49 and v50 on the coach\"; "
        "its design note (not published)"),
    "add_aes_bms_button_status": ("structural",
        "Light a BMS command button (an add_aes_batt_heat instance) from one bit of the 0x18FF7070 "
        "field the heater status already receives: blue while clear, cyan while set. One-source "
        "bitcombine fold into a fresh cell, one two-arm whole-button switch.",
        "Flashed 2026-10-15 (v49, Ignore Alarms): the button draws; its bit status and whether "
        "the BMS honours the command are not yet coach-checked.",
        "its design note (not published)"),
    "add_batt_heat_battery_glyph": ("structural",
        "Show the battery heater contactor on the House battery icons: a white wavy-lines glyph "
        "in the bottom-right corner while the contactor is closed, with the voltage number raised "
        "11 px in every state. Each battery becomes one switch on the Batt Heat state cell over "
        "four whole-battery arms.",
        "Flashed 2026-09-26 (v34a, glyph verified); style `outline` flashed in v47 and verified "
        "2026-10-10.",
        "`src/fireflymcf/battglyph.py`"),
    "add_level_page": ("structural",
        "Redraw screen 29 (the SLIDES/AWNINGS page the nav rail's slide icon reaches) as a LEVEL "
        "page: a top-down van with a tile per wheel, pitch and roll, a LEVEL banner and blocks "
        "needed/carried. Live, it appends 14 record-gated `0x0FFB0` subscriptions, 20 fresh "
        "display cells and one per-tick routine; `preview` draws the example state statically. "
        "`levelpage.recipe_ops` emits it with its title rename and arrow glyph.",
        "Flashed 2026-09-27 (v35): live page, orientation, blocks stepper and NO SENSOR "
        "verified; on DGN `0x1EFBF` since v37. Zero Level is unverified.",
        "`src/fireflymcf/levelpage.py`, `levelfeed.py`, "
        "its design note (not published)"),
    "suppress_tank_status": ("g12",
        "Stop the G12 broadcasting `1FFB7` TANK_STATUS for chosen tank instances (2-byte "
        "NOPs in the G12 rule program).",
        "Flashed 2026-09-07 (v22). PC.62.", "`docs/TANK-PATCHER.md`, `BUS-BASELINE.md` PC.62"),
}


def _error_codes(cls) -> list[tuple[str, str]]:
    """Parse the `Error codes:` section of an op docstring into (code, text) pairs."""
    doc = inspect.getdoc(cls) or ""
    m = re.search(r"Error codes?:\n(.*?)(?:\n\n|\Z)", doc, re.S)
    if not m:
        return []
    out: list[tuple[str, str]] = []
    for line in m.group(1).splitlines():
        s = line.strip()
        if not s:
            continue
        mm = re.match(r"([A-Z][A-Z0-9_]+)\s*[—–-]+\s*(.*)", s)
        if mm:
            out.append((mm.group(1), mm.group(2).strip()))
        elif out:
            out[-1] = (out[-1][0], (out[-1][1] + " " + s).strip())
    return out


def _type_text(schema: dict) -> str:
    t = schema.get("type")
    if isinstance(t, list):
        t = "|".join(t)
    if "enum" in schema:
        t = (t or "") + " " + "{" + ", ".join(json.dumps(v) for v in schema["enum"]) + "}"
    if "anyOf" in schema or "oneOf" in schema:
        alts = schema.get("anyOf") or schema.get("oneOf")
        t = " or ".join(_type_text(a) for a in alts)
    return (t or "any").strip()


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _params_table(cls) -> list[str]:
    props = cls.PARAMS.get("properties", {})
    required = set(cls.PARAMS.get("required", []))
    if not props:
        return ["No parameters."]
    lines = ["| parameter | type | required | default | meaning |", "|---|---|---|---|---|"]
    for name, sch in props.items():
        default = json.dumps(sch["default"]) if "default" in sch else ""
        lines.append("| `%s` | %s | %s | %s | %s |" % (
            name, _cell(_type_text(sch)), "yes" if name in required else "",
            _cell(default), _cell(sch.get("description", ""))))
    return lines


def entries(registry: dict | None = None) -> list[dict]:
    """Every op as data, in the document's order: what `fireflymcf ops` prints.

    `verified` is the class default of `hardware_verified`; some ops narrow it per instance
    (a parameter that selects an unflashed path), so the compile manifest has the last word."""
    registry = registry if registry is not None else recipe.REGISTRY
    missing = sorted(set(registry) - set(OP_NOTES))
    if missing:
        raise KeyError("ops without an OP_NOTES entry: %s" % missing)
    order = {c[0]: i for i, c in enumerate(CLASSES)}
    out = []
    for n in sorted(registry, key=lambda n: (order[OP_NOTES[n][0]], n)):
        klass, purpose, status, proof = OP_NOTES[n]
        cls = registry[n]
        out.append({"name": n, "class": klass, "purpose": purpose, "status": status,
                    "proof": proof, "verified": bool(getattr(cls, "hardware_verified", True)),
                    "params": cls.PARAMS,
                    "errors": [{"code": c, "meaning": t} for c, t in _error_codes(cls)]})
    return out


def render(registry: dict | None = None) -> str:
    registry = registry if registry is not None else recipe.REGISTRY
    ops = entries(registry)
    out: list[str] = []
    out.append("# Recipe ops — reference")
    out.append("")
    out.append("Generated by `python3 -m fireflymcf.ops_doc` from `recipe_ops.py`. "
               "Do not hand-edit; `tests/test_ops_doc.py` pins it.")
    out.append("")
    out.append("A project file (`*.ffproj.json`) is an ordered list of ops. `fireflymcf compile` "
               "applies them to the stock image named by `base_sha256`, reseals, runs the gates and "
               "writes `mcf-*.bin`. `docs/AGENT-GUIDE.md` is the procedure; this file is the op "
               "vocabulary.")
    out.append("")
    out.append("```json")
    out.append(json.dumps({"schema": recipe.SCHEMA, "base_sha256": "<sha256 of the stock mcf.bin>",
                           "build_name": "Jayco B-Van V13 my build",
                           "ops": [{"id": "pump-name", "type": "rename_label",
                                    "params": {"object": 4, "old": "Water Pump",
                                               "new": "Pump"},
                                    "enabled": True, "note": "why this change was made"}],
                           "preview": {}}, indent=2))
    out.append("```")
    out.append("")
    out.append("`id` must be unique; later ops reference earlier ones as `{\"ref\": \"<id>\"}` "
               "where a parameter says so. `enabled: false` keeps an op in the file without "
               "applying it. `note` is free text and never affects the bytes. Object indices "
               "are build-specific: find them on the owner's own firmware with `pages`, "
               "`labels` and `render`, never from another build.")
    out.append("")
    out.append("**Hardware status** says whether the op's mechanism has run on the reference coach "
               "(Jayco B-Van, firmware 13.6). \"Verified\" is the op's default: `no` means "
               "the compile manifest lists it under `unverified` and the build needs a coach check "
               "with stock on the stick; some ops narrow it by parameter. `fireflymcf ops` prints "
               "this reference from the installed package.")
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append("| op | class | purpose | hardware status | verified |")
    out.append("|---|---|---|---|---|")
    for e in ops:
        out.append("| [`%s`](#%s) | %s | %s | %s | %s |" % (
            e["name"], e["name"], e["class"], _cell(e["purpose"]), _cell(e["status"]),
            "yes" if e["verified"] else "**no**"))
    out.append("")
    names = [e["name"] for e in ops]
    for key, title, blurb in CLASSES:
        members = [n for n in names if OP_NOTES[n][0] == key]
        if not members:
            continue
        out.append("## %s" % title)
        out.append("")
        out.append(blurb)
        out.append("")
        for n in members:
            cls = registry[n]
            klass, purpose, status, proof = OP_NOTES[n]
            out.append("### %s" % n)
            out.append("")
            out.append(purpose)
            out.append("")
            out.append("- **Hardware status:** %s" % status)
            out.append("- **Proof:** %s" % proof)
            out.append("- **Implementation:** `recipe_ops.%s`" % cls.__name__)
            out.append("")
            out.extend(_params_table(cls))
            errs = _error_codes(cls)
            if errs:
                out.append("")
                out.append("Error codes:")
                out.append("")
                for code, text in errs:
                    out.append("- `%s` — %s" % (code, text))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


SCHEMA_PATH = Path("schema/ffproj.schema.json")


def project_schema(registry: dict | None = None) -> dict:
    """JSON Schema (draft 2020-12) for a `*.ffproj.json` project, one branch per op type
    with that op's own `PARAMS`. Written to `schema/ffproj.schema.json`; `fireflymcf schema`
    prints it. Unknown params are refused here as the engine refuses them."""
    registry = registry if registry is not None else recipe.REGISTRY
    branches = []
    for e in entries(registry):
        params = dict(e["params"])
        params.setdefault("properties", {})
        params = {"type": "object", "additionalProperties": False, **params}
        branches.append({
            "if": {"properties": {"type": {"const": e["name"]}}},
            "then": {"properties": {"params": params}},
        })
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/dbergl/fireflymcf/schema/ffproj.schema.json",
        "title": "fireflymcf project (*.ffproj.json)",
        "description": "An ordered list of recipe ops compiled against one stock mcf.bin. "
                       "Generated by `python3 -m fireflymcf.ops_doc`; `fireflymcf ops NAME` "
                       "describes each op.",
        "type": "object",
        "required": ["schema", "base_sha256", "build_name", "ops"],
        "additionalProperties": False,
        "properties": {
            "schema": {"const": recipe.SCHEMA},
            "base_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$",
                            "description": "sha256 of the owner's stock mcf.bin"},
            "build_name": {"type": "string", "minLength": 1,
                           "description": "the name the boot menu lists; distinct from stock"},
            "ops": {"type": "array", "items": {"$ref": "#/$defs/op"}},
            "preview": {"type": "object",
                        "description": "render-only state; never build input"},
        },
        "$defs": {"op": {
            "type": "object",
            "required": ["id", "type", "params"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "minLength": 1,
                       "description": "unique; later ops reference it as {\"ref\": id}"},
                "type": {"enum": [e["name"] for e in entries(registry)]},
                "params": {"type": "object"},
                "enabled": {"type": "boolean", "default": True},
                "note": {"type": "string", "description": "free text; never affects bytes"},
            },
            "allOf": branches,
        }},
    }


def main() -> int:
    DOC_PATH.write_text(render())
    print("wrote %s" % DOC_PATH)
    SCHEMA_PATH.parent.mkdir(exist_ok=True)
    SCHEMA_PATH.write_text(json.dumps(project_schema(), indent=2) + "\n")
    print("wrote %s" % SCHEMA_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

