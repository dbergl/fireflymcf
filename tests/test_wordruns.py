"""`wordruns.pointer_runs`: the numpy scan and the pure-Python fallback find the same runs."""
import random
import struct

import pytest

from fireflymcf import guidata, wordruns

BASE, MASK = 0x90000000, 0xFFF00000


@pytest.mark.parametrize("build", ["13.6", "11.23", "14.7"])
def test_numpy_and_python_agree_on_the_corpus(corpus, build):
    pytest.importorskip("numpy")
    data = corpus[build]["mcf"]
    gs, ge = guidata.guidata_extent(data)
    n = (ge - gs) // 4
    runs = wordruns.pointer_runs(data, gs, n, BASE, MASK, ge - gs)
    assert runs == wordruns._pointer_runs_py(data, gs, n, BASE, MASK, ge - gs)
    assert sum(len(r) >= 4 for _, r in runs) == 4      # the four object tables


def test_numpy_and_python_agree_on_random_runs():
    pytest.importorskip("numpy")
    rng = random.Random(7)
    for _ in range(200):
        words = [rng.choice([BASE + rng.randrange(0x2000), rng.randrange(1 << 32),
                             BASE - 1, BASE + 0x1FFF, 0xFFFFFFFF, 0])
                 for _ in range(rng.randrange(0, 60))]
        blob = b"\xAA" * 8 + struct.pack(f"<{len(words)}I", *words)
        got = wordruns.pointer_runs(blob, 8, len(words), BASE, MASK, 0x1000)
        assert got == wordruns._pointer_runs_py(blob, 8, len(words), BASE, MASK, 0x1000)


def test_a_run_at_either_end_is_found():
    words = [BASE + 4, BASE + 8, 7, BASE + 12]
    blob = struct.pack("<4I", *words)
    assert wordruns.pointer_runs(blob, 0, 4, BASE, MASK, 0x100) == [(0, [4, 8]), (3, [12])]
