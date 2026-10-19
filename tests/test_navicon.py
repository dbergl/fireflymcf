"""Revealing the nav rail's sixth icon, and pointing it at a screen."""
from pathlib import Path

import pytest

from fireflymcf import navicon, navmap, patch

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def stock():
    return (ROOT / "firefly-13.6" / "mcf.bin").read_bytes()


def test_stock_never_selects_the_six_icon_rail(stock):
    rec = navicon._nav_switch_record(stock)
    assert (rec.child_index, rec.count) == (navicon.NAV_RAIL_STOCK_BASE,
                                            navicon.NAV_RAIL_STOCK_COUNT)


def test_revealing_the_icon_is_a_two_byte_same_size_edit(stock):
    out, edits = navicon.show_extra_nav_icon(stock)
    assert len(out) == len(stock)
    assert len(edits) == 1
    off, old, new, _why = edits[0]
    assert len(old) == len(new) == 2
    assert sum(a != b for a, b in zip(stock, out)) == 1   # only the high byte changes
    assert navicon._nav_switch_record(out).child_index == navicon.NAV_RAIL_SIX_ICON_BASE


def test_the_new_arms_carry_the_sixth_icon_and_lose_nothing(stock):
    """389/390 must be a superset of the stock arms, or revealing the icon costs an existing one."""
    import struct
    from fireflymcf import guidata, options

    gs, ge = guidata.guidata_extent(stock)
    table = guidata.object_tables(stock)[1]

    def ids(obj):
        out = []
        for rec in options._own_records(stock, gs, ge, table, obj):
            if rec.wtype == 12:
                out.append(struct.unpack_from("<H", stock,
                                              rec.end - guidata.GROUP_REFERENCE_TAIL)[0])
        return out

    assert navicon.SLIDES_TOUCH_ID in ids(390) and navicon.SLIDES_TOUCH_ID in ids(389)
    assert set(ids(385)) < set(ids(390))      # strict superset: six icons contain the five
    assert set(ids(386)) < set(ids(389))


def test_retargeting_moves_the_route_and_is_guarded(stock):
    out, edits = navicon.retarget_nav_icon(stock, navicon.SLIDES_LEVELLING_SCREEN)
    assert len(out) == len(stock) and len(edits) == 1
    assert [r.screen for r in navmap.routes(out) if r.touch_id == navicon.SLIDES_TOUCH_ID] == [29]
    # applying twice must refuse rather than stamp over an already-edited leaf
    with pytest.raises(ValueError, match="action leaf"):
        navicon.retarget_nav_icon(out)


def test_revealing_twice_refuses(stock):
    out, _ = navicon.show_extra_nav_icon(stock)
    with pytest.raises(ValueError, match="child_base|already applied|expected stock"):
        navicon.show_extra_nav_icon(out)


def test_an_out_of_range_screen_is_refused(stock):
    with pytest.raises(ValueError, match="imm8"):
        navicon.retarget_nav_icon(stock, 300)


def test_the_pair_reseals_and_touches_only_two_payload_bytes(stock):
    a, _ = navicon.show_extra_nav_icon(stock)
    b, _ = navicon.retarget_nav_icon(a)
    out, changed = patch.reseal(b)
    assert len(out) == len(stock)
    assert set(changed) == {"section-checksum:Lyra 13v6 GuiCode",
                            "section-checksum:Lyra 13v6 GuiData"}
    # two payload bytes + two 2-byte checksums
    assert sum(x != y for x, y in zip(stock, out)) == 6


def test_both_values_the_panel_can_write_still_draw_a_rail(stock):
    """The load-bearing property, and the one a plausible wrong base breaks.

    Slot 453 is only ever 0 or 1 (two stores, the complete writer set). After the edit BOTH must
    resolve to an arm that draws, or a coach on the other value loses the whole nav rail. Base 390
    passes every other test here and fails this one: 453=0 would match nothing, and 390's match
    value is 1 rather than 0xFFFF, so the firmware's scan renders NOTHING.
    """
    import struct
    from fireflymcf import guidata, layout, options

    out, _ = navicon.show_extra_nav_icon(stock)
    scr = layout.Screen(out)
    rec = navicon._nav_switch_record(out)
    gs, ge = guidata.guidata_extent(out)
    table = guidata.object_tables(out)[1]

    for value in (0, 1):
        arm = scr._switch_child(rec.child_index, value, rec.count)
        assert arm is not None, f"slot 453 = {value} draws no rail at all"
        ids = [struct.unpack_from("<H", out, r.end - guidata.GROUP_REFERENCE_TAIL)[0]
               for r in options._own_records(out, gs, ge, table, arm) if r.wtype == 12]
        assert navicon.SLIDES_TOUCH_ID in ids, f"453 = {value} draws a rail without the new icon"
        assert len(ids) >= 5, f"453 = {value} draws only {len(ids)} icons"
