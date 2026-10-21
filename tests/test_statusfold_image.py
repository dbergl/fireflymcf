"""`statusfold.resolve` on stock 13.6: it reads the hook state off the image and chains on it."""
import pytest

from fireflymcf import followchannel, statusfold

STOCK = "firefly-13.6/mcf.bin"
SLOT = 0x20008100
CELL = 0x2003B000


def _stock():
    try:
        return open(STOCK, "rb").read()
    except FileNotFoundError:
        pytest.skip("needs the stock 13.6 image")


def _light(**kw):
    args = dict(slot_addr=SLOT, mask=0x0F, shift=0, kind="eq", value=1, valid_max=None,
                cell_addr=CELL, variant_group=None)
    args.update(kw)
    return statusfold.Light(**args)


def test_resolve_on_stock_reads_the_stock_hook():
    plan = statusfold.resolve(_stock(), [_light(variant_group=162)])
    assert plan.hook_at == followchannel.HOOK_OFF
    assert plan.hook_old == followchannel.HOOK_STOCK
    assert plan.tail_target == followchannel.TAIL_TARGET
    assert plan.setter_addr == followchannel.VARIANT_SETTER
    load = followchannel.STOCK_APPEND_POINT
    blob, dis = statusfold.build(plan, load)
    at, old, new = statusfold.detour_edit(plan, load)
    assert (at, old) == (followchannel.HOOK_OFF, followchannel.HOOK_STOCK) and len(new) == 4


def test_resolve_refuses_on_stock_before_reading_the_hook():
    with pytest.raises(statusfold.StatusFoldError, match="duplicate cell"):
        statusfold.resolve(_stock(), [_light(), _light(slot_addr=SLOT + 2)])
