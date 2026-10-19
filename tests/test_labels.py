import pytest
from fireflymcf import guidata, labels, patch, records, relocate

STOCK_PATH = "firefly-13.6/mcf.bin"

@pytest.fixture(scope="module")
def stock():
    with open(STOCK_PATH, "rb") as f:
        return f.read()

def _labels_of(data, obj_index):
    gs, _ = guidata.guidata_extent(data)
    tab = guidata.object_tables(data)[1]
    return {getattr(r, "label", None)
            for r in records.object_records(data, gs + tab[obj_index])}

def test_grow_label_grows_and_pads(stock):
    # Row 28 on the G12 OUTPUTS screen (object 35) is a static row — the same class
    # of field the hardware-confirmed PC.18 growth used.
    out, padded, edits, at = labels.grow_label(stock, 35, "28 - ", "28 - Overflow Valve")
    out, _ = patch.reseal(out)
    assert len(padded) % 4 == len("28 - ") % 4 == 1  # pad preserves 4-alignment of the DELTA
    assert (len(padded) - len("28 - ")) % 4 == 0
    assert padded.startswith("28 - Overflow Valve")
    assert padded in _labels_of(out, 35)
    assert "28 - " not in _labels_of(out, 35)
    assert len(out) == len(stock) + (len(padded) - len("28 - "))
    ok, reason = relocate._boundary_oracle(out)
    assert ok, reason
    assert edits and at > 0

def test_grow_label_refuses_same_size(stock):
    with pytest.raises(AssertionError):
        labels.grow_label(stock, 35, "28 - ", "28-  ")

def test_grow_label_refuses_wrong_old(stock):
    with pytest.raises(StopIteration):
        labels.grow_label(stock, 35, "no such label", "no such label grown")
