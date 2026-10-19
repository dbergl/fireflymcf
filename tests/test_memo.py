"""`memo.content_cached` -- the one memoisation primitive the readers share (2026-09-01).

Three properties matter, and each has bitten this project before in some form:
content-keying (an id-keyed cache went stale after an in-place bytearray edit),
copy-out (a caller mutating a shared result would corrupt every later caller), and
constant-keying (a test that monkeypatches `MIN_OBJECT_TABLE` must get a fresh scan,
not a cached one -- the first cut of this cache poisoned three unrelated tests).
"""
from __future__ import annotations

import pytest

from fireflymcf import guidata, memo


def test_a_mutated_bytearray_is_a_different_key():
    calls = []

    @memo.content_cached
    def scan(data):
        calls.append(bytes(data))
        return [data[0]]

    buf = bytearray(b"\x01\x02\x03")
    assert scan(buf) == [1]
    assert scan(buf) == [1]
    assert len(calls) == 1                       # same content: served from the cache
    buf[0] = 9                                   # in-place edit, id(buf) unchanged
    assert scan(buf) == [9]
    assert len(calls) == 2                       # new content: recomputed


def test_the_result_is_copied_out_so_a_caller_cannot_poison_the_cache():
    @memo.content_cached
    def tables(data):
        return [[1, 2], [3]]

    first = tables(b"x")
    first.append("junk")
    assert tables(b"x") == [[1, 2], [3]]

    @memo.content_cached(copy=set)
    def cells(data):
        return {1, 2}

    cells(b"y").add(3)
    assert cells(b"y") == {1, 2}


def test_extra_arguments_are_part_of_the_key():
    @memo.content_cached(copy=None)
    def f(data, n):
        return (data[0], n)

    assert f(b"a", 1) == (97, 1)
    assert f(b"a", 2) == (97, 2)
    assert f.cache_info().misses == 2


def test_object_tables_honours_a_monkeypatched_minimum(mcf_136, monkeypatch):
    """`object_tables` is memoised on content AND on `MIN_OBJECT_TABLE` read at call time, so a
    test that raises the minimum sees fewer tables and never leaves that answer behind for the
    next caller."""
    wide = guidata.object_tables(mcf_136)
    monkeypatch.setattr(guidata, "MIN_OBJECT_TABLE", 64)
    narrow = guidata.object_tables(mcf_136)
    assert len(narrow) < len(wide)
    monkeypatch.setattr(guidata, "MIN_OBJECT_TABLE", 4)
    assert guidata.object_tables(mcf_136) == wide


def test_the_cache_is_bounded():
    @memo.content_cached(maxsize=2, copy=None)
    def f(data):
        return len(data)

    for i in range(5):
        f(bytes([i]))
    assert f.cache_info().currsize == 2
