"""`guidata.object_table_offset` -- locating an object-pointer table's own position.

`object_tables` returns each table's ENTRIES. Rewriting an entry (repointing a glyph, say) needs
the table's POSITION, and hardcoding one is build-specific: any insertion below a table shifts it.
13.6's stock glyph table sits at +0x7503C, but on a build that had already had an object appended
it had moved 212 bytes, and repointing with the stock constant wrote into unrelated bytes -- the
glyph silently kept its old artwork and every structural gate still passed.

So the property under test is not "it returns 0x7503C"; it is "the offset it returns is where the
table actually is, in a shifted build as well as a stock one".
"""
import struct

import pytest

from fireflymcf import guidata

TABLES = (0, 1, 2, 3)


@pytest.mark.parametrize("index", TABLES)
def test_offset_points_at_the_table_itself(mcf_136, index):
    """The words at the returned offset are the table's own entries."""
    start, _ = guidata.guidata_extent(mcf_136)
    table = guidata.object_tables(mcf_136)[index]
    at = start + guidata.object_table_offset(mcf_136, index)
    for i, entry in enumerate(table[:16]):
        word = struct.unpack_from("<I", mcf_136, at + i * 4)[0]
        assert word == guidata.OBJECT_POINTER_BASE + entry, (
            f"table {index} entry {i}: {word:#x} != {guidata.OBJECT_POINTER_BASE + entry:#x}")


@pytest.mark.parametrize("index", TABLES)
def test_offsets_agree_across_builds(corpus, index):
    """Every build resolves, and each to its own position -- not to a shared constant."""
    seen = {}
    for version in ("11.23", "13.6", "14.7"):
        data = corpus[version]["mcf"]
        start, end = guidata.guidata_extent(data)
        off = guidata.object_table_offset(data, index)
        assert 0 <= off < end - start
        seen[version] = off
    assert len(seen) == 3


@pytest.mark.parametrize("index", TABLES)
def test_follows_the_table_through_an_insertion(mcf_136, index):
    """The real regression: after bytes are inserted below a table, the offset must move with it.

    A hardcoded constant passes the stock-build test above and fails this one.
    """
    relocate = pytest.importorskip("fireflymcf.relocate")
    stock_off = guidata.object_table_offset(mcf_136, index)
    start, end = guidata.guidata_extent(mcf_136)

    # Insert immediately below the lowest of the four tables, so all four shift.
    lowest = min(guidata.object_table_offset(mcf_136, i) for i in TABLES)
    payload = b"\x00" * 64
    shifted, _ = relocate.relocate(mcf_136, lowest, payload)

    moved_off = guidata.object_table_offset(shifted, index)
    assert moved_off == stock_off + len(payload), (
        f"table {index} offset did not follow the insertion: "
        f"{stock_off:#x} -> {moved_off:#x}, expected +{len(payload)}")

    # and it still points at the table's real entries in the shifted build
    new_start, _ = guidata.guidata_extent(shifted)
    table = guidata.object_tables(shifted)[index]
    at = new_start + moved_off
    for i, entry in enumerate(table[:8]):
        assert struct.unpack_from("<I", shifted, at + i * 4)[0] == \
            guidata.OBJECT_POINTER_BASE + entry


def test_rejects_an_out_of_range_index(mcf_136):
    with pytest.raises((IndexError, ValueError)):
        guidata.object_table_offset(mcf_136, 99)
