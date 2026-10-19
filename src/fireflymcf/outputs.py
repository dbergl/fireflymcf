"""The panel's G12 OUTPUTS diagnostic list: which load each output pin is named for.

NETWORK DIAGNOSTICS → G12 OUTPUTS draws one row per G12 output, `"NN - NAME"` (13.6:
`"12 - A/C Fan Low"`, `"28 - "` for an output with no name). The rows are static labels in one
object's own record list, the same on every floorplan, so they are read straight from the file.

A name is the build's statement of what the output drives on its coaches. An owner's installer
may number a different module's outputs, so a button wired to a named output switches that load
unless the coach differs; `add_button` and `wire_button` refuse one without
`allow_named_output`.
"""
from __future__ import annotations

import re

from . import guidata, options

# A numbered G12 output row, e.g. "44 - Water Pump" or "20 -" (no name wired). Both
# 13.6/14.7 ("01 - ") and 11.23 ("03 - AWNING EXT") use this "NN - NAME" convention.
ROW_RE = re.compile(r"^(\d{1,2})\s*-\s*(.*)$", re.S)


def outputs_root(data: bytes) -> int:
    """The object-table index for the G12 OUTPUTS diagnostics grid.

    Header text alone is ambiguous ("NETWORK DIAGNOSTICS" is shared with the
    DEVICES/FAULTS tab), so this resolves structurally instead: among every object
    whose OWN (non-recursive) record list -- the same convention `cli._resolve_page_root`
    and `options._find_options_pages` use -- contains a label that strips to exactly
    "G12 OUTPUTS", pick the one with the most rows matching the "NN - NAME" numbered
    output convention (`ROW_RE`); ties broken by lowest object index. Verified
    unique per build with no tie-break needed: 13.6 -> object 35 (44 numbered rows,
    including "44 - Water Pump"), 11.23 -> object 675 (11 rows, including
    "44 - WATER PUMP"), 14.7 -> object 32 (44 rows). The DEVICES/FAULTS tab (13.6
    object 14) has no "G12 OUTPUTS" label at all in its own record list, so it is
    never a candidate.
    """
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    best: tuple[int, int] | None = None  # (numbered_row_count, object_index)
    for i in range(len(table)):
        recs = options._own_records(data, gs, ge, table, i)
        labels = [r.label.strip() for r in recs if r.label is not None]
        if "G12 OUTPUTS" not in labels:
            continue
        numbered = sum(1 for label in labels if ROW_RE.match(label))
        candidate = (numbered, -i)
        if best is None or candidate > best:
            best = candidate
    if best is None:
        raise ValueError(
            "could not resolve --page 'outputs' (no object's own record list carries "
            "a 'G12 OUTPUTS' label) on this build; pass --root N explicitly instead")
    return -best[1]


def output_names(data: bytes) -> dict[int, str]:
    """`{output pin: name}` from the G12 OUTPUTS list; an unnamed row maps to ""."""
    gs, ge = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    out: dict[int, str] = {}
    for r in options._own_records(data, gs, ge, table, outputs_root(data)):
        m = ROW_RE.match(r.label.strip()) if r.label is not None else None
        if m:
            out.setdefault(int(m.group(1)), " ".join(m.group(2).split()))
    return out
