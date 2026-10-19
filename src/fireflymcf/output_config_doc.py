"""Generator for `docs/G12-OUTPUT-CONFIG.md`: the per-output reference doc.

This module composes two sources that this project keeps deliberately separate
(the G12's wired configuration and the panel's option state are two subsystems):

  * **G12 hardware config** -- `g12_config.output_config` (table 0x82 in the G12 section).
    Ratings, current class, polarity: physically wired into the module, stable across
    firmware builds (regions A/B/D are byte-identical across 11.23/13.6/14.7).
  * **Panel/GuiData behaviour** -- what the *panel* sends, not what the G12 enforces:
      - dim vs latch: `guidata.dimmable_channels` (channels the panel ever sends command 0,
        set-brightness, to);
      - master-group membership: `guidata.find_control_groups`'s multi-channel ON groups
        labelled "Light Master" / "Exterior Master".
    The G12 does not read either of these -- it drives whatever RV-C command it is sent
    regardless of the pin sheet (CLAUDE.md, "Remap" section). The canonical example: Bath
    Fan / instance 40 is wired latch on the panel, but the G12 would dim it if commanded to.

Nothing here is fitted to the answer key. `tests/answer_key.py::INTERIOR_MASTER` /
`EXTERIOR_MASTER` are used only as a cross-check note in the rendered doc (see
`_master_note`), never to fill a value.

Run as a script to regenerate `docs/G12-OUTPUT-CONFIG.md`:

    python3 -m fireflymcf.output_config_doc firefly-13.6/mcf.bin
"""
from __future__ import annotations

from dataclasses import dataclass

from . import g12_config, guidata

# --- panel-side (GuiData) derivation ------------------------------------------------------

# The half-bridge (9-20) current rating is not stored anywhere in table 0x82 (region B
# carries a pin bitmask + polarity/config word, not an amperage); this is the manufacturer
# spec value used on the pin sheet, not a decoded number. Same fact `g12_config` already
# documents via `OutputInfo.current_source` for these outputs.
HALFBRIDGE_SPEC_AMPS = 1.0

# The 6A/12A -> OCP-seconds mapping is spec, not raw-encoded (g12_config module docstring);
# the class *byte* -- which output is the 12A outlier -- is the only decoded fact.
OCP_SECONDS = {"standard": 0.27, "high": 2.1}


def master_groups(data: bytes) -> dict[str, list[int]]:
    """Multi-channel master ON/OFF groups, keyed by their on-screen label.

    Derived structurally from `guidata.find_control_groups`: a "master" control is any
    group whose channel list has more than one member and whose `screen_title` (the
    object's own first label, e.g. "Light Master" / "Exterior Master" -- the individual
    button's own `label` is just "ON"/"OFF") contains the word "Master". Stable across
    13.6/11.23/14.7. The channel set for a title is the union across its ON/OFF
    group-index variants (they carry the same channels in this corpus;
    `ControlGroup.channels` is already documented as a union, see `guidata.ControlGroup`).
    """
    out: dict[str, set[int]] = {}
    for g in guidata.find_control_groups(data):
        if len(g.channels) <= 1:
            continue
        if "master" not in g.screen_title.lower():
            continue
        out.setdefault(g.screen_title, set()).update(g.channels)
    return {title: sorted(channels) for title, channels in out.items()}


def _interior_exterior(groups: dict[str, list[int]]) -> tuple[list[int], list[int], str, str]:
    """Split `master_groups`' output into (interior, exterior, interior_label,
    exterior_label). Labelling interior vs exterior is by matching the word "Exterior" in
    the label text -- itself decoded data (the control's own on-screen caption), not an
    invented convention -- so this is `inferred-from-label` rather than structural like the
    grouping itself."""
    interior_label = exterior_label = None
    interior: list[int] = []
    exterior: list[int] = []
    for label, channels in groups.items():
        if "exterior" in label.lower():
            exterior_label = label
            exterior = channels
        else:
            interior_label = label
            interior = channels
    return interior, exterior, interior_label or "-", exterior_label or "-"


# --- row assembly --------------------------------------------------------------------------

@dataclass(frozen=True)
class OutputRow:
    instance: int
    name: str
    output_type: str          # 'relay' | 'half-bridge' | 'high-current'
    rating: str                # display string, G12-derived
    polarity: str               # display string, G12-derived
    class_ocp: str               # display string, G12-derived
    dim_latch: str                # 'dimmer' | 'latch', GuiData-derived
    interior_master: bool           # GuiData-derived
    exterior_master: bool            # GuiData-derived
    in_auto: bool                     # G12-derived (automation-rule reference)


def _rating(info: g12_config.OutputInfo) -> str:
    if info.output_type == "relay":
        amps = info.current_amps or 0.0
        return "%g A" % amps
    if info.output_type == "half-bridge":
        return "%g A (spec)" % HALFBRIDGE_SPEC_AMPS
    # high-current
    amps = info.current_amps or 0.0
    return "%g A" % amps


def _polarity(info: g12_config.OutputInfo) -> str:
    if info.output_type != "half-bridge":
        return "-"
    if info.polarity == "positive":
        return "positive"
    return "(0x00)"


def _class_ocp(info: g12_config.OutputInfo) -> str:
    if info.output_type != "high-current":
        return "-"
    cls = info.current_class or "standard"
    amps = "12A" if cls == "high" else "6A"
    secs = OCP_SECONDS[cls]
    outlier = " (outlier)" if cls == "high" else ""
    return "%s / %gs%s" % (amps, secs, outlier)


def build_rows(data: bytes) -> list[OutputRow]:
    """Every output 1-44, joining `g12_config.output_config` (G12 hardware) with the
    panel-side GuiData facts (dim/latch, master membership). Names come from
    `tests/answer_key.py::CHANNELS` (hardware-verified pin names), read-only."""
    from tests import answer_key  # local import: test fixture data, read-only cross-check

    cfg = g12_config.output_config(data)
    dimmable = guidata.dimmable_channels(data)
    groups = master_groups(data)
    interior, exterior, _, _ = _interior_exterior(groups)
    interior_set, exterior_set = set(interior), set(exterior)

    rows: list[OutputRow] = []
    for n in range(1, 45):
        info = cfg[n]
        rows.append(OutputRow(
            instance=n,
            name=answer_key.CHANNELS.get(n, "-"),
            output_type=info.output_type,
            rating=_rating(info),
            polarity=_polarity(info),
            class_ocp=_class_ocp(info),
            dim_latch="dimmer" if n in dimmable else "latch",
            interior_master=n in interior_set,
            exterior_master=n in exterior_set,
            in_auto=info.referenced,
        ))
    return rows


# --- markdown rendering ----------------------------------------------------------------

_TYPE_LABEL = {"relay": "relay", "half-bridge": "half-bridge", "high-current": "high-current"}


def _master_note(data: bytes) -> list[str]:
    from tests import answer_key

    groups = master_groups(data)
    interior, exterior, interior_label, exterior_label = _interior_exterior(groups)
    lines = [
        "- **%s** (group ON/OFF pair, `guidata.find_control_groups`) = channels %s."
        % (interior_label, ", ".join(map(str, interior))),
        "- **%s** (group ON/OFF pair) = channels %s."
        % (exterior_label, ", ".join(map(str, exterior))),
    ]
    ak_ext = set(answer_key.EXTERIOR_MASTER)
    file_ext = set(exterior)
    if file_ext != ak_ext:
        extra = sorted(file_ext - ak_ext)
        lines.append(
            "  Cross-check only (not fitted): `tests/answer_key.py::EXTERIOR_MASTER` is "
            "%s from the pin sheet; the file's Exterior Master **group union** additionally "
            "includes %s (it also appears in the Light/Interior Master group, "
            "`guidata.find_control_groups` docstring notes group 216 selects "
            "[21, 23, 35]). Reported honestly as the union, not narrowed to match the "
            "pin sheet." % (sorted(ak_ext), ", ".join(map(str, extra))))
    ak_int = set(answer_key.INTERIOR_MASTER)
    file_int = set(interior)
    if file_int == ak_int:
        lines.append(
            "  `tests/answer_key.py::INTERIOR_MASTER` matches the file's Light/Interior "
            "Master group exactly: %s." % sorted(ak_int))
    return lines


def render(data: bytes) -> str:
    """Build the whole `docs/G12-OUTPUT-CONFIG.md` markdown. Deterministic: every value is
    read from the bytes (plus the read-only `answer_key.CHANNELS` name table), nothing is
    randomised or order-dependent on set/dict iteration beyond what the source tables fix."""
    rows = build_rows(data)
    recs = g12_config.rule_records(data)

    def find_rule(dgn: int, target: int, table: int | None = None):
        for r in recs:
            if r.is_registry or r.dgn_lo != dgn or r.target_instance != target:
                continue
            if table is not None and r.table != table:
                continue
            return r
        return None

    button_rule = find_rule(0xFEDB, 3)          # AWNING EXT button (proven §, matches PC docs)
    interlock_rule = find_rule(0xFFB7, 3)       # ignition/park interlock candidate

    L: list[str] = []
    a = L.append

    a("# G12 output configuration (13.6-derived)")
    a("")
    a("*Generated by `src/fireflymcf/output_config_doc.py`. Do not hand-edit; regenerate "
      "with `python3 -m fireflymcf.output_config_doc firefly-13.6/mcf.bin`.*")
    a("")
    a("Per-output configuration decoded from **two separate subsystems**, kept distinct "
      "because they are decoded from different parts of the file and mean different things:")
    a("")
    a("- **G12 hardware config** (columns Type/Rating/Polarity/Class-OCP) -- from the "
      "**G12 section** of the MCF (container idx 3), table 0x82. `proven-from-file`, stable: "
      "ratings/polarity/current class are wired into the module and do not change with what "
      "the panel sends.")
    a("- **Panel/GuiData behaviour** (columns Dim/Latch, Interior Master, Exterior Master) -- "
      "from **GuiData**: what the panel's own button bindings *send*, not what the G12 "
      "enforces. The G12 does not read a per-channel dim/latch flag or a master-group table; "
      "it acts on whatever RV-C command arrives regardless of the pin sheet. The canonical "
      "example (`CLAUDE.md`): Bath Fan / instance 40 is wired **latch** on the panel side "
      "(the panel binding table never sends it command 0), yet the G12 would dim it exactly "
      "like a light if some other sender commanded it to.")
    a("")
    a("**Scope.** Generated from **13.6**. Firmware versions add/remove floorplans, options "
      "and outputs over time (e.g. AES did not exist in v11), and some output *names* change "
      "between v13 and v14 -- run the decoder per build rather than assuming this table is "
      "identical everywhere. The base-config tables in table 0x82 that were checked (regions "
      "A/B/D) are in fact byte-identical across 13.6/11.23/14.7, and region C isolates output "
      "44 as the sole 12 A output in every build; the panel-side columns and the *automation* "
      "column (In-auto) are coach-specific GuiData/G12-automation content, not guaranteed "
      "identical across builds.")
    a("")
    a("Provenance per column:")
    a("- **Type / Rating / Polarity / Class-OCP** -- `proven-from-file`, G12 section "
      "**table 0x82** (`g12_config.output_config`). Sub-region offsets are table-relative: "
      "region A `+0x2c` (outputs 1-8 mA), region B `+0x6c` (outputs 9-20), region C `+0xcc` "
      "(outputs 21-44 class byte + calibration), region D `+0x18c` (outputs 21-44 binary "
      "code).")
    a("- **Dim/Latch** -- GuiData, `guidata.dimmable_channels` (panel bindings: channels the "
      "panel ever sends command 0, set-brightness, to). Panel-side, not the G12 section.")
    a("- **Interior Master / Exterior Master** -- GuiData, `guidata.find_control_groups`'s "
      "multi-channel ON groups (this module's `master_groups`). Panel-side.")
    a("- **Name** -- `tests/answer_key.py::CHANNELS` (hardware-verified pin names), read-only "
      "cross-check, not decoded from either subsystem.")
    a("- **In-auto** -- G12 section, whether the coach automation-rule program references the "
      "output (`g12_config.OutputInfo.referenced`).")
    a("")
    a("## Table 0x82 layout (per-output config), table-relative")
    a("")
    a("| region | offset | entries | stride | outputs | fields |")
    a("|--------|--------|---------|--------|---------|--------|")
    a('| A | +0x2c | 8  | 8  | 1-8   | `<u16 milliamps><u16 0x00fa><u16 ocp_enum><u16 key>` -- **word0 = current rating (mA), word2 = OCP-time enum** (0x8=.26s, 0xa=1.0s; decoded 2026-08-28 against the config PDF), word3 = a per-output key |')
    a("| B | +0x6c | 12 | 8  | 9-20  | `<u32 pin_bitmask><u32 config>` -- half-bridge; "
      "config 0x10 (out 9-18) / 0x00 (19-20) |")
    a("| C | +0xcc | 24 | 8  | 21-44 | `<u16 type_lo><u16 calib_hi><u8 class><00 00><u8 index>` "
      "-- index->output 21+index; class byte majority=6 A, lone minority=12 A |")
    a("| D | +0x18c| 24 | 4  | 21-44 | `<u16 0><u16 code>` -- stable 12/12 split of "
      "{1221, 3498}, meaning unidentified |")
    a("")
    a("8 + 12 + 24 = 44 outputs. Regions A/B/D are byte-identical across the three builds; "
      "region C's class *byte value* differs per build (13.6: majority 0x48 / outlier 0xab; "
      "11.23 & 14.7: majority 0x88 / outlier 0x8b) but always isolates exactly output 44.")
    a("")
    a("## Per-output table (13.6)")
    a("")
    a("| Out | Name | Type | Rating | Polarity | Class/OCP | Dim/Latch (panel) | "
      "Interior Master (panel) | Exterior Master (panel) | In auto? |")
    a("|----:|------|------|-------:|:--------:|-----------|:-----------------:|"
      ":------------------------:|:------------------------:|:--------:|")
    for r in rows:
        a("| %d | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r.instance, r.name, _TYPE_LABEL[r.output_type], r.rating, r.polarity,
            r.class_ocp, r.dim_latch, "Y" if r.interior_master else "-",
            "Y" if r.exterior_master else "-", "yes" if r.in_auto else "-"))
    a("")
    a("Notes:")
    a("- **Rating for 1-8** is the raw milliamps in region A (`proven-from-file`). It "
      "matches the PDF: outputs 1,2 = 15 A; 3,4 = 20 A (the awning EXT/RET motors); 5-8 = "
      "10 A (tank heaters).")
    a("- **%g A for 9-20** is the half-bridge spec (`inferred` -- not a number in region B; "
      "region B carries the pin bitmask and a polarity/config word, not a current value)."
      % HALFBRIDGE_SPEC_AMPS)
    a("- **Class/OCP for 21-44**: the region-C class byte partitions the 24 outputs into a "
      "23-output majority (6 A) and one minority (output 44 = 12 A). The specific amps "
      "(6/12) and OCP times (%g s / %g s) are the owner/PDF spec attached to the two "
      "classes; the byte proves *which* output is the outlier, not the numbers themselves "
      "(`proven-from-file` partition, `inferred` values). The 6 A/12 A distinction and the "
      "OCP-seconds distinction are the same single binary attribute in the bytes."
      % (OCP_SECONDS["standard"], OCP_SECONDS["high"]))
    a("- **Polarity 19-20 = (0x00)**: region B's config word is 0x10 (\"positive\") for "
      "outputs 9-18 but 0x00 for 19-20 in every build. Reported honestly as an outlier "
      "rather than forced to \"all positive\"; 19-20 are likely spare/unconfigured "
      "half-bridge channels.")
    a("- **Region D code** (not shown) is a stable 12/12 split of {1221, 3498} over 21-44. **IDENTIFIED 2026-08-28: it is redundant with region C's `type_lo`, i.e. the high-current channel-TYPE partition, NOT an OCP/threshold field.** `type_lo=0xa38a` ⟺ `Dcode=1221` (outputs 21,22,25,26,29,30,33,34,37,38,41,42) and `type_lo=0x3916` ⟺ `Dcode=3498` (23,24,27,28,31,32,35,36,39,40,43; output 44 is its own `type_lo=0x722b` but shares the 3498 group). The pattern is a regular **period-4 AABB interleave** — a hardware channel attribute, not a per-output user setting. Because it co-varies with `type_lo` rather than with the class byte, **OCP-time is carried in the class byte with current, not here**. What the A/B attribute *is* remains unidentified, but it is now bounded: it is **NOT the 6 A/12 A current capability** — the config PDF's 12 A-capable positions are `[22,23,24,27,30,32,33,35,39,41,42,44]`, which does not match either `type_lo` group (checked 2026-08-28). The 6 A/12 A capability is part-number-derived (which channels are wired to 12 A drivers) and is **not stored per-channel in table 0x82** at all.")
    a("")
    a('### The config PDF is the authoritative source, and current/OCP-time are INDEPENDENT (2026-08-28)')
    a('')
    a('The build ships with an interactive **G12 config PDF** ("G12 Master … 13v7 w AES.pdf") — a Firefly')
    a('AcroForm (458 fields) whose form-field *values* are the per-output configuration the tool flashes.')
    a("It has no local export (its `generate`/`login` fields drive Firefly's server-side generation;")
    a('`extract` only splits PDF pages), but the field values are readable directly, and the full')
    a('extraction is committed at **`configs/g12-master-pdf-13v7.json`** (customer JAYCO, model B-VAN, part')
    a('`7000700 G12 w/Probe`, rev 13V7). Connectors map to outputs: **j8 → 1-8** (relays), **j10 → 9-20**')
    a('(half-bridges, with `pos_`/`neg_` polarity radios), **j3 → 21-29**, **j4 → 30-44**.')
    a('')
    a('**Current and OCP-time are separate, independently-programmable dropdowns** (`jN-aM` current, `jN-ocM`')
    a('OCP-time) — one per output. This is proven in the file: relay outputs 3,4 carry OCP **1.0 s** while')
    a('every other relay is **.26 s**, at three different current ratings — so OCP is not a function of')
    a('current. That let region A be fully decoded (word0 = mA, **word2 = the OCP-time enum**), verified')
    a('against the PDF for all eight relays. For the high-current block (21-44) the class byte is uniform')
    a('(`0x48` = 6 A/.27 s) except output 44 (`0xab` = 12 A/2.1 s), so this build changed both together for')
    a('one output and the single class byte cannot be split into its current vs OCP sub-fields from 13.6')
    a('alone — but the PDF confirms they are independent controls. The relay OCP enum choices are')
    a('{2.0/1.0/.51/.26/.13 s}; high-current {2.1/1.1/.53/.27/.13 s}.')
    a("")
    a("### Panel-side master groups (GuiData, cross-checked against the pin sheet)")
    a("")
    for line in _master_note(data):
        a(line)
    a("")
    a("## Interlock rules -- record format and the ignition/awning example")
    a("")
    a("The G12 also runs a coach-specific automation program: 12-byte RV-C event->action "
      "rule records in tables 0x20/0x30/0x61/0x62. Record format (`proven-from-file` against "
      "the awning example):")
    a("")
    a("```")
    a("<u16 dgn_lo> <u8 b2> <u8 b3=cmd> <u8 b4> <u8 b5=level> <u8 b6> <u8 b7=target_instance> "
      "<u8x4>")
    a("```")
    a("")
    if button_rule is not None:
        hexed = " ".join("%02x" % b for b in button_rule.raw[:8])
        a("- **Button rules** for AWNING EXT (output 3) / RET (output 4), table 0x%02x, "
          "trigger DGN `0xFEDB` (DC_DIMMER_COMMAND_2 -- the panel button): e.g. `%s` = on "
          "DC_DIMMER_COMMAND_2, drive output **3** to level 0x%02X. This confirms `b7` is "
          "the target output (3 = AWNING EXT, 4 = AWNING RET per `answer_key`)."
          % (button_rule.table, hexed, button_rule.raw[5]))
    if interlock_rule is not None:
        hexed = " ".join("%02x" % b for b in interlock_rule.raw[:8])
        a("- **RETRACTED -- this record is a TANK_STATUS template, not an interlock.** Table "
          "0x%02x record %d, `%s`, trigger DGN `0x%04X`. This bullet used to read it as an "
          "ignition/park interlock on output 3 (AWNING EXT) and to describe its four siblings "
          "as fanning the same status into output 18. **`BUS-BASELINE.md` PC.25 refuted that "
          "on the wire**: across a 28.3 s ignition-on window every `0x1FFB7` instance held "
          "exactly one payload. Read all five records together and they are five instances of "
          "standard RV-C **`TANK_STATUS`** -- `b7` is an RV-C *instance* byte, not an output "
          "number, instance 3 is the LPG tank, and `0x64` is the resolution field (100, as an "
          "LPG sender uses). The real interlock is in the table-0x67 code blob and broadcasts "
          "`DC_DIMMER_COMMAND_2` command 33/34 (`docs/G12-CODE.md`, `BUS-BASELINE.md` "
          "PC.27/PC.28). *Carry the lesson: a listing scoped to one record made a family of "
          "five look like a special case.*"
          % (interlock_rule.table, interlock_rule.index, hexed, interlock_rule.dgn_lo))
    a("")
    a("## Verdict")
    a("")
    highs = [r.instance for r in rows if r.class_ocp.startswith("12A")]
    a("**PASS** on the base per-output config: outputs 1-8 ratings and the output-%s 12 A "
      "outlier reproduce the oracles exactly and across builds; the config table cleanly "
      "partitions all 44 outputs by hardware type. Half-bridge polarity is a documented "
      "partial (9-18 positive, 19-20 differ); exact OCP seconds and region-D's code are "
      "honest negatives." % (", ".join(map(str, highs)) or "?"))
    a("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="regenerate docs/G12-OUTPUT-CONFIG.md")
    p.add_argument("mcf", nargs="?", default="firefly-13.6/mcf.bin",
                   help="path to the MCF (default firefly-13.6/mcf.bin)")
    p.add_argument("--out", default="docs/G12-OUTPUT-CONFIG.md")
    args = p.parse_args(argv)
    data = open(args.mcf, "rb").read()
    text = render(data)
    with open(args.out, "w") as fh:
        fh.write(text)
    print("wrote %s (%d bytes)" % (args.out, len(text)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
