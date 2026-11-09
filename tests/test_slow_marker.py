"""The fast tier (`-m "not slow"`) is only as good as the marker: every emulator test must carry it."""


def test_every_emu_module_is_slow(request):
    emu = [i for i in request.session.items if i.path.parent.name == "emu"]
    unmarked = [i.nodeid for i in emu if i.get_closest_marker("slow") is None]
    assert not unmarked, unmarked[:5]
