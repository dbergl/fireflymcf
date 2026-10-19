"""register.resolve_register / status_slot_addr — the reusable register primitive."""
from __future__ import annotations

import pytest

from fireflymcf import guidata, register


def test_status_slot_addr_is_index_split():
    assert register.status_slot_addr(67) == 0x2000804E    # ch25 status, low segment
    assert register.status_slot_addr(255) == 0x200081C6   # last low
    assert register.status_slot_addr(256) == 0x20008200   # first high
    assert register.status_slot_addr(289) == 0x20008242   # 0xEE selector, high segment


def test_resolve_status_register_by_instance(mcf_136):
    reg = register.resolve_register(mcf_136, dgn="DC_DIMMER_STATUS_3", instance=25)
    assert reg.dgn_low == guidata.DGN_DC_DIMMER_STATUS_3
    assert reg.value == 25
    assert reg.slot_addr == 0x2000804E


def test_resolve_config_register_by_selector_uses_high_slot(mcf_136):
    reg = register.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=0xEE)
    assert reg.dgn_low == guidata.DGN_CONFIG
    assert reg.value == 0xEE
    assert reg.slot_addr == 0x20008242            # the index-split (PC.44) case


def test_resolve_accepts_numeric_dgn(mcf_136):
    reg = register.resolve_register(mcf_136, dgn=0x15FCE, selector=0xEE)
    assert reg.slot_addr == 0x20008242


def test_resolve_refuses_unknown_subscription(mcf_136):
    with pytest.raises(register.RegisterError, match="no.*subscription|append"):
        register.resolve_register(mcf_136, dgn="DGN_CONFIG", selector=0x7F)


def test_resolve_refuses_both_instance_and_selector(mcf_136):
    with pytest.raises(register.RegisterError, match="instance.*selector|one of"):
        register.resolve_register(mcf_136, dgn="DGN_CONFIG", instance=1, selector=0xEE)
