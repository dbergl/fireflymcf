"""The fast tier (`-m "not slow"`) is only as good as the marker: every emulator test must carry it."""


def test_every_emu_module_is_slow(request):
    emu = [i for i in request.session.items if i.path.parent.name == "emu"]
    unmarked = [i.nodeid for i in emu if i.get_closest_marker("slow") is None]
    assert not unmarked, unmarked[:5]


def test_an_item_outside_tests_is_left_alone(tmp_path):
    """`pytest tests src --doctest-modules` hands the hook items from outside tests/."""
    import types

    from tests import conftest

    marks = []
    outside = types.SimpleNamespace(path=tmp_path / "mod.py", add_marker=marks.append)
    conftest.pytest_collection_modifyitems([outside])
    assert marks == []
