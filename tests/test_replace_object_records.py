"""Task 1: `relocate.replace_object_records` -- the shrink/replace relocation primitive.

The span helper reflects the CONFIRMED framing (checked against `records.object_records`
and `guidata.object_tables` in `src/fireflymcf/records.py`): object-table 1 stores the
offset of an object's **leading count byte**, so `object_records` is handed that offset and
reads `count = data[start]`, records beginning at `start + 1`. The full object payload
(count byte + records) is therefore `data[start:end]` with `end = recs[-1].end` -- NOT
`data[start-1:end]` as an earlier draft of this test assumed. The identity round-trip is the
ground truth that pins this: replacing object 830 with its own exact bytes must reproduce the
input byte-for-byte.
"""
import hashlib

from fireflymcf import relocate, guidata, records

STOCK = "firefly-13.6/mcf.bin"


def _obj_span(data, i):
    gs, _ = guidata.guidata_extent(data)
    table = guidata.object_tables(data)[1]
    start = gs + table[i]                       # offset of the leading count byte
    recs = records.object_records(data, start)
    end = recs[-1].end if recs else start
    return start, end


def test_identity_replace_is_byte_identical():
    data = open(STOCK, "rb").read()
    start, end = _obj_span(data, 830)          # small, stable AGS frame
    body = data[start:end]                      # count byte + records
    out, edits = relocate.replace_object_records(data, 830, body)
    assert hashlib.sha256(out).hexdigest() == hashlib.sha256(data).hexdigest()


def test_shrink_preserves_downstream_and_passes_gates():
    data = open(STOCK, "rb").read()
    start, end = _obj_span(data, 830)
    body = data[start:end]
    # drop the last record: a smaller-but-valid body (count byte decremented)
    recs = records.object_records(data, start)
    smaller = bytes([body[0] - 1]) + data[start + 1:recs[-2].end]
    out, _ = relocate.replace_object_records(data, 830, smaller)
    # object 830 now decodes to one fewer record; a downstream object still decodes
    assert len(records.object_records(out, _obj_span(out, 830)[0])) == len(recs) - 1
    assert records.object_records(out, _obj_span(out, 315)[0])       # no exception


def test_refuses_bad_index():
    import pytest
    data = open(STOCK, "rb").read()
    with pytest.raises(relocate.RelocationError):
        relocate.replace_object_records(data, 99999, b"\x00")
