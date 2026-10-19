"""`aescompact.frame_roles` finds the compact frame's records by what they are, so a later op
may prepend records (the v48 cards) without breaking swap_aes_electrical's verify."""
import pytest

from fireflymcf import aescompact, guidata, records

V47 = "builds/mcf-13.6-BattOutline-v47.bin"


def _frame(data):
    gs, tab = guidata.guidata_extent(data)[0], guidata.object_tables(data)[1]
    return records.object_records(data, gs + tab[aescompact.ELECTRICAL_AES_BODY])


def test_v47_roles():
    data = open(V47, "rb").read()
    assert aescompact.frame_roles(data, _frame(data)) == {
        "frame": 0, "cluster": 5, "inverter": 6, "pump": 7}


def test_roles_shift_with_prepended_records():
    data = open(V47, "rb").read()
    recs = _frame(data)
    shifted = [recs[7], recs[21], recs[7]] + recs        # three wtype-32 stand-ins up front
    assert aescompact.frame_roles(data, shifted) == {
        "frame": 3, "cluster": 8, "inverter": 9, "pump": 10}


def test_missing_cluster_raises():
    data = open(V47, "rb").read()
    recs = [r for i, r in enumerate(_frame(data)) if i != 5]
    with pytest.raises(ValueError):
        aescompact.frame_roles(data, recs)
