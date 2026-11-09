"""Fixtures for `test_compile_cache.py` that patch through `pytest.MonkeyPatch` from outside the
test module, the case the compile cache's old textual rule (the word "MonkeyPatch" in the test
module, or a `monkeypatch` fixture argument) could not see."""
import pytest

import fireflymcf.emu
from fireflymcf import recipe


@pytest.fixture
def engine_patched_elsewhere():
    """An engine function wrapped, as a spy or a fault injector would wrap it."""
    original = recipe._prepare

    def spy(*args, **kwargs):
        return original(*args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(recipe, "_prepare", spy)
        yield


@pytest.fixture
def emulator_patched_elsewhere():
    """A patch on a `fireflymcf.emu` object only, which the compile path never imports."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fireflymcf.emu, "_cache_test_marker", object(), raising=False)
        yield
