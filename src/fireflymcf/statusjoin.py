"""The control -> status-variable join (MCF-FORMAT.md §5.4a / §10 item 11a, open thread 2).

`known_status_pairings` builds the validation oracle from the bytes; `predicted_status_slot`
is the recovered mechanism. RESOLVED 2026-08-26: the join is not a stored field and is not
encoded in the indicator widget (both searched, both negative) -- it is the shared G12 output
instance, so the paired status var is `instance_status_var(control's command channel)`. That
reproduces every known pairing on 13.6 and 14.7 (see MCF-FORMAT.md §10 item 11a).
"""
from __future__ import annotations

from . import guidata


def _object_index_for_offset(data: bytes, off_rel: int) -> int | None:
    """Object-table-1 index whose byte span contains section-relative `off_rel`."""
    table = sorted((start, idx) for idx, start in enumerate(guidata.object_tables(data)[1]))
    found = None
    for start, idx in table:
        if start > off_rel:
            break
        found = idx
    return found


def known_status_pairings(data: bytes) -> dict[int, int]:
    """`{object_index: variable_index}` for every located control whose on/off indicator's
    variable is unambiguous: the display-list variable filtering the control's own single
    command channel. The validation oracle -- derived independently of the join mechanism.
    """
    vt = guidata.variable_table(data)
    disp = guidata.status_list_variables(data)
    out: dict[int, int] = {}
    for control in guidata.find_control_groups(data):
        if len(control.channels) != 1:
            continue
        (channel,) = control.channels
        cands = [v.index for v in vt if v.channel == channel and v.index in disp]
        if len(cands) != 1:
            continue
        obj = _object_index_for_offset(data, control.offset)
        if obj is not None:
            out[obj] = cands[0]
    return out


def instance_status_var(channel: int) -> int:
    """The DC_DIMMER_STATUS_3 variable index for a G12 output instance (`MCF-FORMAT.md`
    §10 item 11a, `proven-from-code`, build-invariant across 13.6 and 14.7):
    `18 + (n-1)` for instances 1..23 and `66 + (n-24)` for 24..44."""
    return 18 + (channel - 1) if channel <= 23 else 66 + (channel - 24)


def predicted_status_slot(data: bytes, object_index: int) -> int | None:
    """The status variable a given control object is paired with, or None.

    RECOVERED 2026-08-26 (dig closed). The pairing is NOT a stored cross-reference and is
    NOT independently encoded in the indicator widget -- both were searched and both are
    negative (`MCF-FORMAT.md` §10 item 11a). The join is the shared **G12 output instance**:
    a control commands channel `n`, that pin broadcasts `DC_DIMMER_STATUS_3` instance `n`,
    and the paired status variable is `instance_status_var(n)`. So the pairing is derived
    from the control's OWN command channel via the instance law -- this reproduces every
    entry of `known_status_pairings` (including object 295 -> var 86, a command whose status
    var no widget even draws, so it could never come from an indicator).

    This is not fitting the oracle: it applies the §1928 instance→var law to the control's
    command channel. It returns None for an object carrying no single-channel control.
    """
    for control in guidata.find_control_groups(data):
        if len(control.channels) != 1:
            continue
        if _object_index_for_offset(data, control.offset) != object_index:
            continue
        (channel,) = control.channels
        return instance_status_var(channel)
    return None
