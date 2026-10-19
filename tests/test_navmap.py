"""The ACTION dispatcher's touch-id -> screen map."""
from pathlib import Path

import pytest

from fireflymcf import navmap

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def stock():
    return (ROOT / "firefly-13.6" / "mcf.bin").read_bytes()


def test_both_leaf_shapes_are_read(stock):
    """`beq -> store` and `bne -> fall through to store` must both resolve, or half the map is lost."""
    r = {x.touch_id: x.screen for x in navmap.routes(stock)}
    assert r[0x7D1] == 1     # bne shape: the store is in the fall-through
    assert r[0x7DC] == 12    # beq shape: the store is at the branch target
    assert r[0x7DE] == 14    # the leaf the compact-AES work repurposed
    assert r[0x7D2] == 2 and r[0x7D3] == 3


def test_the_two_slides_pages_have_exactly_one_leaf_each(stock):
    assert [x.touch_id for x in navmap.routes_to(stock, 12)] == [0x7DC]
    assert [x.touch_id for x in navmap.routes_to(stock, 29)] == [0x7ED]


def test_screen_29_is_orphaned_because_nothing_carries_its_id(stock):
    """The reason SLIDES/AWNINGS (levelling) is unreachable: a leaf exists, no region calls it."""
    assert 0x7ED not in navmap.displayed_touch_ids(stock)
    assert 29 in navmap.orphaned(stock)
    # and the sixth nav icon's id IS defined, just never drawn in stock
    assert 0x7DC in navmap.displayed_touch_ids(stock)


def test_the_map_is_not_vacuous(stock):
    routes = navmap.routes(stock)
    assert len(routes) > 25
    assert len({r.screen for r in routes}) > 20
    # a map that resolved everything would make `orphaned` empty and be useless as a check
    assert navmap.orphaned(stock)
    assert len(navmap.reachable_routes(stock)) < len(routes)
