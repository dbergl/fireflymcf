"""Maximal runs of pointer-like words in an image: the scan both object-table readers share.

`guidata._object_tables` and `coverage._pointer_tables` each walk every aligned u32 of GuiData
looking for runs of `0x900XXXXX` words that point inside the section, and a compile calls them
hundreds of times (once per intermediate image). With numpy the scan is one vector pass; without
it (a plain `pip install fireflymcf`) the original Python loop runs. Both return the same runs,
and `tests/test_wordruns.py` holds them to that on every corpus build.
"""
from __future__ import annotations

import struct


def pointer_runs(data: bytes, start: int, n: int, base: int, mask: int,
                 span: int) -> list[tuple[int, list[int]]]:
    """Every maximal run of the `n` u32 words at `start` that satisfy
    `(word & mask) == base and word - base < span`, as `(first_word_index, [word - base, ...])`
    in order. Runs of any length; callers apply their own minimum."""
    if n <= 0:
        return []
    try:
        import numpy as np
    except ImportError:
        return _pointer_runs_py(data, start, n, base, mask, span)
    words = np.frombuffer(data, dtype="<u4", count=n, offset=start)
    rel = words - np.uint32(base)                    # wraps below base; masked out next
    ok = ((words & np.uint32(mask)) == np.uint32(base)) & (rel < np.uint32(span))
    edges = np.flatnonzero(np.diff(np.concatenate(([False], ok, [False])).astype(np.int8)))
    return [(int(a), rel[a:b].tolist()) for a, b in zip(edges[0::2], edges[1::2])]


def _pointer_runs_py(data: bytes, start: int, n: int, base: int, mask: int,
                     span: int) -> list[tuple[int, list[int]]]:
    words = struct.unpack_from(f"<{n}I", data, start)
    runs: list[tuple[int, list[int]]] = []
    run: list[int] = []
    first = 0
    for i, word in enumerate(words):
        if (word & mask) == base and word - base < span:
            if not run:
                first = i
            run.append(word - base)
            continue
        if run:
            runs.append((first, run))
            run = []
    if run:
        runs.append((first, run))
    return runs
