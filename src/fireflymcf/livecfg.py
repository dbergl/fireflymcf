"""Read a coach's LIVE configuration off the RV-C bus, from a candump capture.

The G12 broadcasts a selector-keyed configuration status on DGN **0x15FCE** every ~5
seconds -- one frame per selector, cycling. That stream carries the coach's *actual*
configuration: the floorplan, every option, and the two counts (Go Power! controllers,
batteries). It is the answer to a question the file cannot answer, because option and
floorplan state lives in G12 non-volatile storage and reaches the panel over the bus
(`MCF-FORMAT.md` §10, `GUICODE.md` Finding 19) -- the MCF is a schema, not an instance.

**Frame layout** (`observed-on-hardware`; the selector meanings and the whole decoded
stream are `BUS-BASELINE.md` PC.1, "The coach's configuration, as broadcast"):

    195FCE9C # SS 00 vv vv 00 00 00 00
             |  |     `-- u16 LITTLE-ENDIAN value at payload bytes 2..3
             |  `-------- always 00 in the corpus of frames seen
             `----------- selector

The width is pinned by selector `0x31`, the AES "Start At" voltage: `FC 03` = 1020,
and 1020 x 0.05 V = **51.0 V**, which matches both the coach and the static template in
GuiCode's pool (`GUICODE.md` Finding 43). A byte-wide read gives 252 and a big-endian
one 64515; both are wrong.

**This module never opens a socket.** It takes text. A capture is something the owner can
produce by any means -- a bridge, a USB adapter, a laptop, a log someone sent them -- and
nothing here needs to know which. That is deliberate: not every coach has the same bridge.
"""
from __future__ import annotations

import dataclasses
import re

# DGN 0x15FCE, any source address. candump prints the 29-bit id as 8 hex digits.
_STATUS_RE = re.compile(r"\b(?P<id>[01][0-9A-F]5FCE(?P<src>[0-9A-F]{2}))#"
                        r"(?P<data>[0-9A-F]{8,16})\b")

# The generator mode is a radio on one selector, distinguished by the value written
# (`options._option_value`): 1 = AGS, 2 = AES, 3 = AES Single Output, 0 = none.
GENERATOR_SELECTOR = 0xEF


@dataclasses.dataclass(frozen=True)
class LiveConfig:
    """`{selector: value}` as last seen, plus who said it and how much was heard."""
    source: int | None
    frames: int
    selectors: dict


def decode_capture(text: str) -> LiveConfig:
    """Decode every 0x15FCE status frame in a candump log. Last value per selector wins.

    Last-wins matters: a capture taken while someone changes a setting contains both
    values, and the final one is the coach's state. Anything that is not a status frame
    -- other DGNs, blank lines, prose -- is ignored rather than raising, because the
    caller's input is a file a human produced and being handed the wrong one is normal.
    """
    selectors: dict[int, int] = {}
    source = None
    frames = 0
    for m in _STATUS_RE.finditer(text or ""):
        data = m.group("data")
        if len(data) < 8:
            continue
        sel = int(data[0:2], 16)
        selectors[sel] = int(data[6:8] + data[4:6], 16)     # u16 LE at bytes 2..3
        source = int(m.group("src"), 16)
        frames += 1
    return LiveConfig(source=source, frames=frames, selectors=selectors)


def to_preview(data: bytes, live: LiveConfig) -> dict:
    """`LiveConfig` -> the dict `project.preview` stores, for the image `data`.

    Selector -> option name comes from `guidata.find_option_bindings`; selector -> value
    slot for anything that is not a named option (the counts) comes from the OPTIONS-page
    bindings. A selector this build cannot name is dropped rather than guessed at.
    """
    from . import guidata, options as optmod

    out: dict = {}

    floorplans = optmod.floorplan_options(data)
    fp_value = live.selectors.get(0xF5)
    if fp_value in floorplans:
        out["floorplan"] = floorplans[fp_value]

    by_selector = {}
    for b in guidata.find_option_bindings(data):
        by_selector.setdefault(b.selector, []).append(b.name)

    chosen: list[str] = []
    for sel, names in sorted(by_selector.items()):
        value = live.selectors.get(sel)
        if not value:
            continue
        if len(names) > 1 or sel == GENERATOR_SELECTOR:
            # a radio: the name whose written value matches, not every name
            for n in names:
                if optmod._option_value(n) == value:
                    chosen.append(canonical_option(n))
                    break
        else:
            chosen.append(canonical_option(names[0]))
    # always present, even when empty: importing a coach with every option off must
    # CLEAR the picker, not silently leave the previous selection in place.
    out["options"] = chosen

    extra = _counts(data, live)
    if extra:
        out["extra_slots"] = extra
    return out


def canonical_option(name: str) -> str:
    """The name both option sources agree on: first line, stripped.

    13.6 stores the cargo light as `'Cargo / Bath Light\\n    (CH. 25)'` -- a channel hint
    on a second line -- while `guidata.find_option_defaults` names it
    `'Cargo / Bath Light'`. Joining on the first line is what makes the two line up.
    Lives here, in the tools layer, so the studio server can import it rather than
    restate it: two spellings of the same join would drift silently."""
    return name.split("\n")[0].strip()


# Selector -> the value slot its count drives, per firmware. None is an OPTIONS-page
# binding, so none can be derived from the row records; each is transcribed from that
# release's config-payload copy block (`GUICODE.md` Findings 41/42/45, 92):
#
#   13.6  0xD7 Go Power! count -> slot 39: payload +0x0B, `strb r2,[=0x2003a4e9]`
#         @0x00247C50, with the sibling setter at 0x002477D0 rejecting > 5.
#         0xD8 battery count -> slot 599: payload +0x18 -> 0x2003A804 (0x00247C84), passed
#         at 0x00247D5A-0x00247D5E to the setter at 0x002477E8, which accepts only 0 or 1
#         and stores 0x20039E58.
#   14.7  0xD7 -> slot 95: payload +0x0B stored to 0x2003A037 at 0x00247D06 (the same
#         clamp setter at 0x002477D0, store 0x002477DE).
#         0xD8 -> slot 458: payload +0x18 normalised to 0/1 (`adds r2,#0; it ne; movne
#         r2,#1`, 0x00247D32-0x00247D38) and stored straight to 0x20039A83 at 0x00247D3C.
#         The setter at 0x002477E8 survives but is handed its own register (0x00247DF6),
#         so it never changes it. The receive compare block joins selector 0xD8 to offset
#         +0x18 (0x0024A0FE, `movs r1,#0x18` @0x0024A2E6; `GUICODE.md` Finding 82).
#   11.23 none located: no count slot is emitted.
#
# The table is keyed by `firmware.identify`, not `options.located_derivation`: these
# facts live in the config-payload copy block and the setters, which the derivation
# guard does not cover, and the battery count is not a derivation input at all. A built
# image keeps its GuiCode name and LCD section, so it identifies as its stock.
#
# The value is emitted raw, as the bus carries it; neither firmware's clamps (Go Power!
# > 5 -> 1; 13.6's battery setter rejecting > 1; 14.7's nonzero -> 1) are modelled.
#
# The Go Power! count is one of the three sources `options.derived_slots` reads for the
# nav rail, so importing a live config is what makes an imported coach's icon column
# right for a floorplan whose burst turns controllers on.
_COUNT_SLOTS: dict = {
    "13.6": {0xD8: 599, 0xD7: 39},
    "14.7": {0xD8: 458, 0xD7: 95},
}


# The table is bound as a default argument, so monkeypatching _COUNT_SLOTS has no
# effect: a test passes `table=` instead.
def _counts(data: bytes, live: LiveConfig, table: dict = _COUNT_SLOTS) -> dict:
    from . import firmware

    slots = table.get(firmware.identify(data), {})
    return {str(slot): live.selectors[sel]
            for sel, slot in sorted(slots.items())
            if sel in live.selectors}
