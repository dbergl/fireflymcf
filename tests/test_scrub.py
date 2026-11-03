"""The scrub gate stays in the suite: no firmware, captures or PII in tracked files."""
import importlib.util
import sys
import pathlib

_spec = importlib.util.spec_from_file_location(
    "scrub_check", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "scrub_check.py")
scrub_check = importlib.util.module_from_spec(_spec)
sys.modules["scrub_check"] = scrub_check  # dataclasses needs the module registered
_spec.loader.exec_module(scrub_check)


def test_flags_each_rule(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"\0")
    (tmp_path / "b.md").write_text("ssh pi@coach2mqtt:can1\nsee /home/dan/x\nmail me@example.org\n"
                                   "host 192.168.1.5\n")
    (tmp_path / "c.py").write_text("X = '" + "A" * 600 + "'\n")
    (tmp_path / "d.txt").write_bytes(b"xx\x12\xb9\x53\xfdyy")
    (tmp_path / "e.txt").write_bytes(b"\x4d\x3c\x2b\x1a")
    (tmp_path / "f.txt").write_bytes(b"\x2e\x10\x1b\xc1")
    found = scrub_check.scan(sorted(tmp_path.iterdir()), [])
    rules = {f.rule for f in found}
    assert {"firmware-ext", "firmware-magic", "pii-host", "pii-home", "pii-email",
            "pii-ip", "blob"} <= rules
    assert {f.path for f in found if f.rule == "firmware-magic"} == {"d.txt", "e.txt", "f.txt"}


def test_commit_email_and_hex_literal_are_fine(tmp_path):
    p = tmp_path / "ok.py"
    p.write_text("MAGIC = 0xFD53B912  # dbergl@hotmail.com noreply@anthropic.com\n")
    assert scrub_check.scan([p], []) == []


def test_allow_list_exempts(tmp_path):
    p = tmp_path / "b.md"
    p.write_text("coach2mqtt\n")
    assert scrub_check.scan([p], [f"{p.name}:pii-host"]) == []


def test_tracked_tree_is_clean():
    assert scrub_check.scan(scrub_check.tracked_files(), scrub_check.load_allow()) == []
