"""`fireflymcf.tankpatch` -- the standalone tank-suppression patcher.

This is the surface someone who has never read this repo touches, so the tests are about the
contract they meet: a bare run reports and writes nothing, a refusal explains itself and leaves
no file behind, and the bytes it produces on 13.6 are the bytes already flashed to the coach.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from fireflymcf import tankpatch
from fireflymcf import container, patch, tankbroadcast

REPO = Path(__file__).resolve().parents[1]
STOCK = REPO / "firefly-13.6/mcf.bin"
CORPUS = ("firefly-11.23/mcf.bin", "firefly-13.6/mcf.bin", "firefly-14.7 (Terrain)/mcf.bin")


def run(*argv, capsys=None):
    """`(exit_code, stdout+stderr)`."""
    code = tankpatch.main([str(a) for a in argv])
    return code


def g12(data: bytes) -> bytes:
    section = next(s for s in container.parse_container(data).sections
                   if s.name.startswith("G12"))
    return data[section.offset:section.offset + section.length]


# --- the report path -------------------------------------------------------------------
def test_a_bare_run_reports_and_writes_nothing(tmp_path, capsys):
    assert run(STOCK) == 0
    out = capsys.readouterr().out
    assert "fresh water" in out and "black waste" in out
    assert "PASS" in out
    assert not list(tmp_path.iterdir())


def test_the_report_runs_on_every_build_in_the_corpus(capsys):
    for name in CORPUS:
        assert run(REPO / name) == 0, name
        assert "PASS" in capsys.readouterr().out, name


def test_naming_no_tanks_is_not_an_error_but_says_what_to_do(capsys):
    assert run(STOCK) == 0
    assert "--all-water" in capsys.readouterr().out


# --- the patch path --------------------------------------------------------------------
def test_all_water_reproduces_the_image_on_the_coach(tmp_path, capsys):
    """The oracle: the recipe op's bytes, which are the bytes of v22 (`BUS-BASELINE.md` PC.62)."""
    out = tmp_path / "mcf-tanks.bin"
    assert run(STOCK, "--all-water", "--no-rename", "-o", out) == 0

    stock = STOCK.read_bytes()
    expected, _ = tankbroadcast.suppress(stock)
    expected, _ = patch.reseal(expected)
    assert out.read_bytes() == expected


@pytest.mark.skipif(not (REPO / "build/mcf-experiment-v22-notankbcast.bin").exists(),
                    reason="build/ is gitignored; run builds/experiment first")
def test_the_g12_section_matches_the_flashed_v22_image(tmp_path):
    out = tmp_path / "mcf-tanks.bin"
    assert run(STOCK, "--all-water", "--no-rename", "-o", out) == 0
    flashed = (REPO / "build/mcf-experiment-v22-notankbcast.bin").read_bytes()
    assert g12(out.read_bytes()) == g12(flashed)


def test_the_default_run_renames_the_build_so_the_flasher_can_tell_it_apart(tmp_path):
    out = tmp_path / "mcf-tanks.bin"
    assert run(STOCK, "--all-water", "-o", out) == 0
    before = patch.build_name(STOCK.read_bytes())
    after = patch.build_name(out.read_bytes())
    assert after != before
    assert before in after and "Tank" in after


def test_an_explicit_name_wins(tmp_path):
    out = tmp_path / "mcf-tanks.bin"
    assert run(STOCK, "--all-water", "--name", "My Coach No Tanks", "-o", out) == 0
    assert patch.build_name(out.read_bytes()) == "My Coach No Tanks"


def test_tanks_can_be_chosen_one_at_a_time(tmp_path):
    out = tmp_path / "mcf-tanks.bin"
    assert run(STOCK, "--fresh", "--grey", "-o", out) == 0
    assert tankbroadcast.suppressed_instances(out.read_bytes()) == [0, 2]


def test_the_lpg_tank_is_reachable_but_never_default(tmp_path):
    out = tmp_path / "mcf-tanks.bin"
    assert run(STOCK, "--all-water", "-o", out) == 0
    assert 3 not in tankbroadcast.suppressed_instances(out.read_bytes())

    out2 = tmp_path / "mcf-tanks2.bin"
    assert run(STOCK, "--lpg", "-o", out2) == 0
    assert tankbroadcast.suppressed_instances(out2.read_bytes()) == [3]


def test_an_arbitrary_instance_can_be_named(tmp_path):
    out = tmp_path / "mcf-tanks.bin"
    assert run(STOCK, "--instance", "18", "-o", out) == 0
    assert tankbroadcast.suppressed_instances(out.read_bytes()) == [18]


def test_every_corpus_build_patches_and_reseals(tmp_path):
    for name in CORPUS:
        out = tmp_path / f"mcf-{Path(name).parent.name}.bin"
        assert run(REPO / name, "--all-water", "-o", out) == 0, name
        data = out.read_bytes()
        assert tankbroadcast.suppressed_instances(data) == [0, 1, 2, 18], name
        assert patch.reseal(data)[1] == [], name          # nothing left to reseal


# --- the refusals ----------------------------------------------------------------------
def test_an_output_the_bootloader_would_not_list_is_refused(tmp_path, capsys):
    out = tmp_path / "patched.bin"
    assert run(STOCK, "--all-water", "-o", out) == 2
    assert "mcf-" in capsys.readouterr().err
    assert not out.exists()


def test_that_refusal_can_be_overridden(tmp_path):
    out = tmp_path / "patched.bin"
    assert run(STOCK, "--all-water", "-o", out, "--any-filename") == 0
    assert out.exists()


def test_a_second_pass_is_refused_and_writes_nothing(tmp_path, capsys):
    once = tmp_path / "mcf-a.bin"
    assert run(STOCK, "--all-water", "-o", once) == 0
    twice = tmp_path / "mcf-b.bin"
    assert run(once, "--all-water", "-o", twice) == 2
    assert "already" in capsys.readouterr().err.lower()
    assert not twice.exists()


def test_an_instance_this_build_does_not_broadcast_is_refused(tmp_path, capsys):
    out = tmp_path / "mcf-a.bin"
    assert run(REPO / "firefly-11.23/mcf.bin", "--lpg", "-o", out) == 2
    assert not out.exists()


def test_a_file_that_is_not_an_mcf_is_refused_with_a_readable_message(tmp_path, capsys):
    assert run(REPO / "firefly-13.6/cli.bin") == 2
    assert "mcf" in capsys.readouterr().err.lower()


def test_a_failed_audit_blocks_the_patch_until_forced(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tankbroadcast, "MIN_BLOB_COVERAGE", 0.99)
    out = tmp_path / "mcf-a.bin"
    assert run(STOCK, "--all-water", "-o", out) == 2
    assert "--force" in capsys.readouterr().err
    assert not out.exists()

    assert run(STOCK, "--all-water", "-o", out, "--force") == 0
    assert out.exists()


def test_refusing_to_overwrite_an_existing_file(tmp_path, capsys):
    out = tmp_path / "mcf-a.bin"
    out.write_bytes(b"keep me")
    assert run(STOCK, "--all-water", "-o", out) == 2
    assert out.read_bytes() == b"keep me"


# --- the post-write verification -------------------------------------------------------
def test_the_written_image_differs_from_its_input_only_where_it_should(tmp_path):
    out = tmp_path / "mcf-a.bin"
    assert run(STOCK, "--all-water", "--no-rename", "-o", out) == 0
    runs = patch.diff_ranges(STOCK.read_bytes(), out.read_bytes())
    g12_section = next(s for s in container.parse_container(STOCK.read_bytes()).sections
                       if s.name.startswith("G12"))
    for start, length in runs:
        end = start + length
        assert g12_section.offset <= start < end <= g12_section.offset + g12_section.length
    assert sum(length for _start, length in runs) == 4 * 2 + 2       # four NOPs + one checksum


def test_the_verifier_is_wired_and_can_fail(tmp_path, monkeypatch, capsys):
    """Stub the suppression to a no-op; the post-write check must catch it."""
    monkeypatch.setattr(tankbroadcast, "suppress", lambda data, instances: (data, []))
    out = tmp_path / "mcf-a.bin"
    assert run(STOCK, "--all-water", "-o", out) == 3
    assert not out.exists()


def test_the_run_reports_the_checksums_it_actually_moved(tmp_path, capsys):
    """Renaming reseals the header on its way out, so a per-step tally under-reports."""
    named = tmp_path / "mcf-a.bin"
    assert run(STOCK, "--all-water", "-o", named) == 0
    assert "header-checksum, section-checksum:G12" in capsys.readouterr().out

    plain = tmp_path / "mcf-b.bin"
    assert run(STOCK, "--all-water", "--no-rename", "-o", plain) == 0
    out = capsys.readouterr().out
    assert "resealed   : section-checksum:G12" in out


def test_the_blast_radius_check_is_wired_and_can_fail(tmp_path, monkeypatch, capsys):
    """Have the suppression scribble outside the G12 section; the verifier must catch it.

    The renamed path allows the header name field and the header checksum, and nothing else --
    'everything before the G12 section' would wave through an edit anywhere in GuiData.
    """
    real = tankbroadcast.suppress

    def scribble(data, instances):
        out, edits = real(data, instances)
        mangled = bytearray(out)
        mangled[0x40000] ^= 0xFF                 # inside GuiData, far from anything we touch
        return bytes(mangled), edits

    monkeypatch.setattr(tankbroadcast, "suppress", scribble)
    out = tmp_path / "mcf-a.bin"
    assert run(STOCK, "--all-water", "-o", out) == 3
    assert "outside the sections" in capsys.readouterr().err
    assert not out.exists()


# --------------------------------------------------------------------------------------------
# `--extract-g12`: hand back the section the device actually receives.
#
# The G12 section is loaded into the device over RV-C, not over USB -- the boot menu's `G12` row
# is an RV-C upload and the stick only carries the file. So the section is a useful thing to hold
# on its own, and `fireflymcf-g12flash` is what reads the device that consumes it.
# --------------------------------------------------------------------------------------------


def test_extracting_from_stock_gives_the_g12_section(tmp_path):
    out = tmp_path / "stock.g12"
    assert run(STOCK, "--extract-g12", out) == 0
    assert out.read_bytes() == g12(STOCK.read_bytes())


def test_extracting_alongside_a_patch_gives_the_PATCHED_section(tmp_path):
    out = tmp_path / "patched.g12"
    assert run(STOCK, "--all-water", "--no-rename", "--extract-g12", out) == 0

    expected, _ = tankbroadcast.suppress(STOCK.read_bytes())
    expected, _ = patch.reseal(expected)
    assert out.read_bytes() == g12(expected)
    assert out.read_bytes() != g12(STOCK.read_bytes())


def test_the_extracted_section_is_what_pc63_actually_transferred(tmp_path):
    """Two independent derivations of the same bytes: this file, and the wire.

    PC.63 captured the owner flashing exactly this section. Reconstructing it from CAN and
    extracting it from the MCF must agree, and neither path consults the other.
    """
    from fireflymcf import upload

    capture = REPO / "captures/pc63-g12-network-upload-2026-09-08.log"
    out = tmp_path / "v22.g12"
    assert run(STOCK, "--all-water", "--no-rename", "--extract-g12", out) == 0

    transfer = upload.decode(capture.read_text())
    assert not transfer.ambiguous
    assert transfer.matches(out.read_bytes())


def test_extract_satisfies_the_nowhere_to_write_refusal(tmp_path):
    """Naming tanks with only `--extract-g12` is legal: there IS somewhere to write."""
    out = tmp_path / "patched.g12"
    assert run(STOCK, "--all-water", "--extract-g12", out) == 0
    assert out.exists()


def test_naming_tanks_with_no_destination_at_all_is_still_refused(tmp_path, capsys):
    assert run(STOCK, "--all-water") == 2
    assert "nowhere to write" in capsys.readouterr().err


def test_the_mcf_filename_rule_does_not_apply_to_a_bare_section(tmp_path):
    """A section is not a flashable image, so the bootloader's `mcf-*.bin` rule is irrelevant."""
    out = tmp_path / "anything.g12"
    assert run(STOCK, "--all-water", "--extract-g12", out) == 0


def test_extracting_over_an_existing_file_is_refused(tmp_path, capsys):
    out = tmp_path / "taken.g12"
    out.write_bytes(b"do not clobber me")
    assert run(STOCK, "--extract-g12", out) == 2
    assert out.read_bytes() == b"do not clobber me"
    assert "already exists" in capsys.readouterr().err


def test_extraction_works_on_every_build_in_the_corpus(tmp_path):
    for i, name in enumerate(CORPUS):
        source = REPO / name
        out = tmp_path / f"corpus{i}.g12"
        assert run(source, "--extract-g12", out) == 0
        assert out.read_bytes() == g12(source.read_bytes())
        assert len(out.read_bytes()) > 0


def test_the_report_says_the_names_are_rvc_not_the_firmwares(capsys):
    """The instance numbers are read from the G12's rules; the names beside them are the RV-C
    standard's table. An agent must not take them for the firmware's own label."""
    assert run(STOCK) == 0
    out = capsys.readouterr().out
    assert "RV-C name" in out and "read from this file" in out
