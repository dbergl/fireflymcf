"""The scrub gate stays in the suite: no firmware, captures or PII in tracked files."""
import pytest
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


def test_ssh_placeholder_is_fine_but_a_real_target_on_its_line_is_not(tmp_path):
    p = tmp_path / "usage.md"
    p.write_text("--can ssh:USER@HOST:CHANNEL\n")
    assert scrub_check.scan([p], []) == []
    p.write_text("--can ssh:USER@HOST:CHANNEL, e.g. ssh:pi@box:can1\n")
    assert [f.text for f in scrub_check.scan([p], [])] == ["pi@box:"]


def test_build_names_and_numbers_are_flagged(tmp_path):
    # Adjacent literals keep the names themselves out of the tracked tree.
    bad = ["the Ha" "nk build", "HA" "NK SE", "header JUL" "IEMOBILE", "Jul" "ie's van",
           "the LV" "-mc probe", "lv" "-move", "lv" "-hold", "v54 Saf" "fron", "Har" "vest Maple",
           "the LC" "ARS2 home", "dan-v66",
           "Special Edition", "flashed in v17", "the v47 shape", "v34a", "(v16+v17)",
           "mcf-tankheat-v10.bin", "V22", "def _v20_shaped_mark", "V47_FRAME_WTYPES",
           "FIELDS_V37"]
    for n, text in enumerate(bad):
        p = tmp_path / f"bad{n}.md"
        p.write_text(text + "\n")
        assert [f.rule for f in scrub_check.scan([p], [])] == ["build-name"], text


def test_versions_and_names_that_are_not_builds_pass(tmp_path):
    ok = ["Thank you", "thanks", "shank", "v0.4.0", "git tag -a v0.3.0", "actions/checkout@v4",
          "ExtFwVer 0x0106 = v1.06", "PD-INV v1.23", "Jayco B-Van V13", "B-Van V14",
          "Jayco Ethos/Swift V11", "jayco/b_van/v13/config.json", "CMSIS-RTOS v1",
          "STM32F7 I2C (v2 IP)", "firmware 13.6", "PC.62", "01-Lyra_13v6_GuiCode.bin",
          "JBv13-042525", "lv-gear-centre", "level", "dev1", "env13"]
    for n, text in enumerate(ok):
        p = tmp_path / f"ok{n}.md"
        p.write_text(text + "\n")
        assert scrub_check.scan([p], []) == [], text


def test_allow_list_exempts(tmp_path):
    p = tmp_path / "b.md"
    p.write_text("coach2mqtt\n")
    assert scrub_check.scan([p], [f"{p.name}:pii-host"]) == []


@pytest.mark.slow
def test_tracked_tree_is_clean():
    assert scrub_check.scan(scrub_check.tracked_files(), scrub_check.load_allow()) == []
