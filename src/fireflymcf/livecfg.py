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


# Selector -> the value slot its count drives. None is an OPTIONS-page binding, so none
# can be derived from the row records; all were established on the bus and in GuiCode
# (the battery count reaches slot 599 via config payload +0x18 -> 0x2003A804 -> the
# setter at 0x002477E8, which accepts only 0 or 1; the Go Power! controller count
# reaches slot 39 via 0x2003A4E9 -- `strb r2,[=0x2003a4e9]` @0x00247C50 -- with the
# sibling clamp at 0x002477D0 rejecting > 5, `GUICODE.md` Findings 41/42).
#
# The Go Power! count is one of the three sources `options.derived_slots` reads for the
# nav rail, so importing a live config is what makes an imported coach's icon column
# right for a floorplan whose burst turns controllers on.
_COUNT_SLOTS = {0xD8: 599, 0xD7: 39}


def _counts(data: bytes, live: LiveConfig) -> dict:
    return {str(slot): live.selectors[sel]
            for sel, slot in sorted(_COUNT_SLOTS.items())
            if sel in live.selectors}
