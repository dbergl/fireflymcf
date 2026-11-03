"""The emulator gate: run an op's scenario against a build in a subprocess, record the result."""
import json
import textwrap

import pytest

pytest.importorskip("unicorn")

from fireflymcf import cli  # noqa: E402
from fireflymcf.emu import gate  # noqa: E402

NEEDS_RESEARCH_DATA = "boots the 13.6 corpus image in the emulator"


def _scn(tmp_path, body, name="emu_test.py"):
    p = tmp_path / name
    p.write_text("def scenario(s):\n" + textwrap.indent(textwrap.dedent(body), "    "))
    return p


def test_passing_scenario(mcf_136, tmp_path):
    p = _scn(tmp_path, """
        s.boot()
        assert s.page == "HOME"
    """)
    res = gate.run_scenario_file(mcf_136, p, timeout_s=240)
    assert res.status == "passed", res.reason
    assert res.screenshots and all(__import__("os").path.exists(x) for x in res.screenshots)


def test_failing_assert_is_failed(mcf_136, tmp_path):
    p = _scn(tmp_path, """
        s.boot()
        assert s.page == "NOPE", "page was not NOPE"
    """)
    res = gate.run_scenario_file(mcf_136, p, timeout_s=240)
    assert res.status == "failed"
    assert "page was not NOPE" in res.reason


def test_bare_assert_names_line(mcf_136, tmp_path):
    p = _scn(tmp_path, """
        s.boot()
        assert s.page == "NOPE"
    """)
    res = gate.run_scenario_file(mcf_136, p, timeout_s=240)
    assert res.status == "failed" and "emu_test.py" in res.reason and "NOPE" in res.reason


def test_scenario_timeout_is_failure(mcf_136, tmp_path):
    p = _scn(tmp_path, """
        s.boot()
        while True:
            s.run(1)
    """)
    res = gate.run_scenario_file(mcf_136, p, timeout_s=5)
    assert res.status == "failed" and res.reason.startswith("timeout")


def test_worker_crash_is_failure(mcf_136, tmp_path):
    p = _scn(tmp_path, "import os\nos._exit(3)\n")
    res = gate.run_scenario_file(mcf_136, p, timeout_s=60)
    assert res.status == "failed" and "worker" in res.reason


def test_unsupported_build_not_emulated(mcf_147, tmp_path):
    p = _scn(tmp_path, "s.boot()\n")
    res = gate.run_scenario_file(mcf_147, p, timeout_s=60)
    assert res.status == "not emulated" and "13.6 only" in res.reason


def test_verify_emulate_writes_sidecar(mcf_136, tmp_path, capsys):
    build = tmp_path / "mcf-x.bin"
    build.write_bytes(mcf_136)
    (tmp_path / "mcf-x.bin.json").write_text(json.dumps({"sha256": "abc", "size": 1}))
    p = _scn(tmp_path, "s.boot()\nassert s.page == 'HOME'\n", "pass_test.py")
    q = _scn(tmp_path, "s.boot()\nassert False, 'boom'\n", "fail_test.py")
    base_rc = cli.main(["verify", str(build), str(build), "--no-render", "--json"])
    capsys.readouterr()
    rc = cli.main(["verify", str(build), str(build), "--no-render", "--emulate",
                   "--scenario", str(p), "--scenario", str(q), "--json"])
    out = capsys.readouterr().out
    side = json.loads((tmp_path / "mcf-x.bin.json").read_text())
    assert side["sha256"] == "abc"
    assert side["emulation"]["pass_test"]["status"] == "passed"
    assert side["emulation"]["fail_test"]["status"] == "failed"
    assert "boom" in side["emulation"]["fail_test"]["reason"]
    assert rc == base_rc                             # emulation does not change verify's exit
    assert "emulated, not flashed" not in out


def test_verify_emulate_all_pass_message(mcf_136, tmp_path, capsys):
    build = tmp_path / "mcf-y.bin"
    build.write_bytes(mcf_136)
    p = _scn(tmp_path, "s.boot()\nassert s.page == 'HOME'\n", "ok_test.py")
    cli.main(["verify", str(build), str(build), "--no-render", "--emulate", "--scenario", str(p)])
    assert "emulated, not flashed" in capsys.readouterr().out
    assert json.loads((tmp_path / "mcf-y.bin.json").read_text())["emulation"]["ok_test"]


def test_worker_runs_the_source_not_a_stale_pyc(mcf_136, tmp_path):
    """The worker compiles the scenario's bytes; a planted `__pycache__` of other source with
    the same size and mtime (what the bytecode cache validates) does not run (final review M2)."""
    import importlib.util
    import os
    import py_compile
    p = tmp_path / "emu_test.py"
    p.write_text("def scenario(s):\n    assert True, 'pass'\n")
    st = p.stat()
    py_compile.compile(str(p), cfile=importlib.util.cache_from_source(str(p)))
    p.write_text("def scenario(s):\n    assert 0, 'source!'\n")
    assert p.stat().st_size == st.st_size
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    res = gate.run_scenario_file(mcf_136, p, timeout_s=60)
    assert res.status == "failed" and "source" in res.reason, res


def test_worker_ignores_the_callers_directory(mcf_136, tmp_path, monkeypatch):
    """A stray module in the caller's working directory does not shadow one the worker imports."""
    marker = tmp_path / "SHADOWED"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "json.py").write_text(f"open({str(marker)!r}, 'w').write('x')\nraise ImportError\n")
    p = _scn(tmp_path, "pass\n")
    monkeypatch.chdir(cwd)
    res = gate.run_scenario_file(mcf_136, p, timeout_s=60)
    assert res.status == "passed", res.reason
    assert not marker.exists()


def test_worker_refuses_a_file_changed_since_it_was_hashed(mcf_136, tmp_path):
    import hashlib
    p = _scn(tmp_path, "pass\n")
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    p.write_text(p.read_text() + "# edited\n")
    res = gate.run_scenario_file(mcf_136, p, timeout_s=60, sha256=sha)
    assert res.status == "failed" and "changed" in res.reason, res
