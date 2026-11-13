"""The facts adding a screen rests on (GUICODE Finding 93), pinned on stock 13.6."""
import struct

import pytest

from fireflymcf import guidata, layout, navmap

from fireflymcf import screenadd

# Finding 93: every load of state+0x20A (the screen id) in 13.6 GuiCode.
READERS_136 = (0x247D3E, 0x248116, 0x24813E, 0x248276, 0x24C8B0, 0x24EE56, 0x24F860, 0x24F87A,
               0x2509C6)


def test_screen_switch_band(mcf_136):
    gs, _ = guidata.guidata_extent(mcf_136)
    off = guidata.find_match_table(mcf_136)
    sw = layout._screen_switch_record(mcf_136, off, gs)
    assert (sw.child_index, sw.count) == (3, 47)
    assert struct.unpack_from("<H", mcf_136, gs + off + 3 * 2)[0] == 0xFFFF   # the default arm


def test_screen_id_readers(mcf_136):
    assert screenadd.screen_id_readers(mcf_136) == READERS_136


def test_free_screen_id(mcf_136):
    assert screenadd.stored_screen_ids(mcf_136) >= {0, 1, 5, 6, 12, 29, 48, 96, 100}
    assert screenadd.free_screen_id(mcf_136) == 35


def test_free_action_leaves(mcf_136):
    free = screenadd.free_action_leaves(mcf_136)
    assert free == (0x7D5, 0x7D6, 0x7DD, 0x7E4, 0x7E5, 0x7E7, 0x7ED, 0x7F4, 0x7F5, 0x82A)
    assert 0x7D1 not in free                         # ELECTRICAL's rail leaf is carried
    for leaf in free:
        hits = [r for r in navmap.routes(mcf_136) if r.touch_id == leaf]
        assert len(hits) == 1
