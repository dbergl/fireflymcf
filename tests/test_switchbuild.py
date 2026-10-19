from fireflymcf import aescompact, records, switchbuild

V33 = "builds/mcf-13.6-BattHeatGlyph-v33.bin"


def test_flow_relative_is_byte_identical_to_aescompact():
    d = open(V33, "rb").read()
    assert switchbuild.author_switch(d, value_slot=700, arms_base=1500, count=4) == \
        aescompact._author_n_arm_switch(d, 700, 1500, 4)


def test_absolute_switch_decodes_at_xy():
    d = open(V33, "rb").read()
    raw = switchbuild.author_switch(d, value_slot=701, arms_base=1510, count=3, at=(120, 88))
    r = records.decode_record(raw, 0)
    assert (r.wtype, r.value_slot, r.child_index, r.count) == (6, 701, 1510, 3)
    assert r.base_modes[:4] == [0, 0, 1, 1] and r.geom[:2] == [120, 88]
    assert r.header[4] & 0x20, "switch must stay enrolled for live redraw"
