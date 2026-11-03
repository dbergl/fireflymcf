"""The CLI's user-op paths: `compile`/`validate`/`verify --project` behind `--allow-user-ops`, the G12
acceptance record, the sidecar's `user_ops`, and `op install`/`op pack`/`op accept`.

R6: without `--allow-user-ops`, nothing reads an op's `op.py` as code. The marker-file tests
prove it: their op's top level writes a file when imported."""
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from fireflymcf import recipe, userop
from fireflymcf.cli import main

FIX = Path(__file__).resolve().parent / "fixtures" / "userops"
STOCK = "firefly-13.6/mcf.bin"


def _doc(stock: bytes, *ops, **extra) -> dict:
    return dict({"schema": recipe.SCHEMA, "base_sha256": hashlib.sha256(stock).hexdigest(),
                 "build_name": "Jayco B-Van V13 user op cli test",
                 "ops": [dict({"id": o[0], "type": o[1], "params": {}}, **(o[2] if len(o) > 2 else {}))
                         for o in ops],
                 "preview": {}}, **extra)


def _project(tmp_path: Path, stock: bytes, *ops, folders=("hook_nop",), manifest=None,
             op_py=None, **extra) -> Path:
    for n in folders:
        dst = tmp_path / "ops" / n
        shutil.copytree(FIX / n, dst)
        if manifest is not None:
            m = json.loads((dst / "manifest.json").read_text())
            m.update(manifest)
            (dst / "manifest.json").write_text(json.dumps(m))
        if op_py is not None:
            (dst / "op.py").write_text(op_py)
    pf = tmp_path / "p.ffproj.json"
    pf.write_text(json.dumps(_doc(stock, *ops, **extra)))
    return pf


def _marker_op_py(marker: Path) -> str:
    """hook_nop's op.py, plus a top level that leaves a marker when imported."""
    src = (FIX / "hook_nop" / "op.py").read_text()
    return f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n" + src


# --- compile / validate gates ------------------------------------------------------------------

def test_compile_needs_allow_user_ops(tmp_path, mcf_136, capsys):
    marker = tmp_path / "IMPORTED"
    pf = _project(tmp_path, mcf_136, ("nop", "user:hook-nop"), op_py=_marker_op_py(marker))
    out = tmp_path / "mcf-u.bin"
    assert main(["compile", str(pf), STOCK, "-o", str(out)]) == 1
    err = capsys.readouterr().err
    assert ("USER_OPS: user:hook-nop are user-written and have never been checked by anyone but "
            "their author. Nothing written. Rebuild with --allow-user-ops once the owner has been "
            "told what they change.") in err
    assert str((tmp_path / "ops" / "hook_nop").resolve()) in err
    assert not out.exists() and not Path(str(out) + ".json").exists()
    assert not marker.exists(), "compile without --allow-user-ops imported op.py"

    assert main(["compile", str(pf), STOCK, "-o", str(out), "--json"]) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is False and body["errors"][0]["code"] == "USER_OPS"
    assert [u["type"] for u in body["user_ops"]] == ["user:hook-nop"]
    assert not marker.exists()

    # validate and verify --project refuse the same way, before importing anything
    assert main(["validate", str(pf), STOCK]) == 1
    assert "USER_OPS" in capsys.readouterr().err and not marker.exists()
    build = tmp_path / "mcf-stock-copy.bin"
    build.write_bytes(mcf_136)
    assert main(["verify", str(build), STOCK, "--no-render", "--emulate",
                 "--project", str(pf)]) == 1
    assert "USER_OPS" in capsys.readouterr().err and not marker.exists()

    # with the flag it builds, imports, and names what runs before building
    assert main(["compile", str(pf), STOCK, "-o", str(out), "--allow-user-ops"]) == 0
    printed = capsys.readouterr().out
    sha = userop.folder_hash(tmp_path / "ops" / "hook_nop")
    assert "user:hook-nop" in printed and sha in printed
    assert printed.index(sha) < printed.index("compiled")
    assert marker.exists() and out.exists()


def test_allow_unverified_does_not_admit_user_ops(tmp_path, mcf_136, capsys):
    pf = _project(tmp_path, mcf_136, ("nop", "user:hook-nop"))
    out = tmp_path / "mcf-u.bin"
    assert main(["compile", str(pf), STOCK, "-o", str(out), "--allow-unverified"]) == 1
    assert "USER_OPS" in capsys.readouterr().err and not out.exists()

    # and the reverse: --allow-user-ops does not admit an unverified built-in
    pf2 = _project(tmp_path / "b", mcf_136, ("nop", "user:hook-nop"),
                   ("gone", "remove_control", {"params": {"object": 293}}))
    assert main(["compile", str(pf2), STOCK, "-o", str(out), "--allow-user-ops"]) == 1
    err = capsys.readouterr().err
    assert "UNVERIFIED" in err and "gone" in err and "'nop'" not in err.split("UNVERIFIED")[1]
    assert not out.exists()
    assert main(["compile", str(pf2), STOCK, "-o", str(out), "--allow-user-ops",
                 "--allow-unverified"]) == 0

    # a user op alone needs no --allow-unverified, though the sidecar lists it unverified
    out3 = tmp_path / "mcf-u3.bin"
    assert main(["compile", str(pf), STOCK, "-o", str(out3), "--allow-user-ops"]) == 0
    assert json.loads(Path(str(out3) + ".json").read_text())["unverified"] == ["nop"]


def test_validate_with_allow_user_ops_passes(tmp_path, mcf_136, capsys):
    pf = _project(tmp_path, mcf_136, ("nop", "user:hook-nop"))
    assert main(["validate", str(pf), STOCK, "--allow-user-ops", "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["passed"] and body["unverified"] == ["nop"]
    assert [u["type"] for u in body["user_ops"]] == ["user:hook-nop"]


def test_built_in_project_unchanged(tmp_path, mcf_136, capsys):
    """No op folders: no user-op output, no `user_ops` key in the sidecar."""
    pf = tmp_path / "p.ffproj.json"
    pf.write_text(json.dumps(_doc(mcf_136, ("tank", "tank_heater_names"))))
    out = tmp_path / "mcf-b.bin"
    assert main(["compile", str(pf), STOCK, "-o", str(out)]) == 0
    assert "user op " not in capsys.readouterr().out.replace("user op cli test", "")
    assert "user_ops" not in json.loads(Path(str(out) + ".json").read_text())


# --- G12 acceptance ----------------------------------------------------------------------------

def test_g12_op_needs_ack(tmp_path, mcf_136, capsys):
    marker = tmp_path / "IMPORTED"
    pf = _project(tmp_path, mcf_136, ("nop", "user:hook-nop"),
                  manifest={"touches": ["guicode", "g12"]}, op_py=_marker_op_py(marker))
    out = tmp_path / "mcf-g.bin"
    assert main(["compile", str(pf), STOCK, "-o", str(out), "--allow-user-ops"]) == 1
    err = capsys.readouterr().err
    assert "G12_ACK_REQUIRED" in err and "user:hook-nop" in err and "op accept" in err
    assert not out.exists() and not marker.exists()        # checked before import

    # without --g12 the acceptance of a G12 op is refused
    assert main(["op", "accept", str(pf), "user:hook-nop"]) == 1
    assert "--g12" in capsys.readouterr().err
    assert main(["op", "accept", str(pf), "user:hook-nop", "--g12"]) == 0
    rec = json.loads(pf.read_text())["user_op_acceptance"]["user:hook-nop"]
    assert rec["folder_sha256"] == userop.folder_hash(tmp_path / "ops" / "hook_nop")
    assert rec["g12"] is True and len(rec["date"]) == 10

    assert main(["compile", str(pf), STOCK, "-o", str(out), "--allow-user-ops"]) == 0
    side = json.loads(Path(str(out) + ".json").read_text())
    assert side["user_ops"][0]["accepted"] == rec


def test_acceptance_invalidated_by_edit(tmp_path, mcf_136, capsys):
    pf = _project(tmp_path, mcf_136, ("nop", "user:hook-nop"),
                  manifest={"touches": ["guicode", "g12"]})
    assert main(["op", "accept", str(pf), "user:hook-nop", "--g12"]) == 0
    op_py = tmp_path / "ops" / "hook_nop" / "op.py"
    op_py.write_text(op_py.read_text() + "\n# edited after acceptance\n")
    out = tmp_path / "mcf-g.bin"
    capsys.readouterr()
    assert main(["compile", str(pf), STOCK, "-o", str(out), "--allow-user-ops"]) == 1
    assert "G12_ACK_REQUIRED" in capsys.readouterr().err and not out.exists()


def test_accept_preserves_the_rest_of_the_project(tmp_path, mcf_136):
    pf = _project(tmp_path, mcf_136, ("nop", "user:hook-nop", {"note": "n", "enabled": False}),
                  ("tank", "tank_heater_names"),
                  preview={"floorplan": "WD"}, ext_ops=[],
                  user_op_sources={"user:other": {"source": "x", "folder_sha256": "0" * 64}},
                  user_op_acceptance={"user:other": {"folder_sha256": "1" * 64, "g12": True,
                                                     "date": "2026-01-01"}})
    before = json.loads(pf.read_text())
    assert main(["op", "accept", str(pf), "user:hook-nop"]) == 0
    after = json.loads(pf.read_text())
    rec = after["user_op_acceptance"].pop("user:hook-nop")
    assert rec["g12"] is False
    before.pop("ext_ops")                                   # empty: to_json omits it
    for o in before["ops"]:                                 # to_json spells out the defaults
        o.setdefault("enabled", True)
        o.setdefault("note", "")
    assert after == before


def test_accept_refuses_a_file_it_would_lose_keys_from(tmp_path, mcf_136, capsys):
    pf = _project(tmp_path, mcf_136, ("nop", "user:hook-nop"))
    doc = json.loads(pf.read_text())
    doc["ops"][0]["comment"] = "kept by hand"
    pf.write_text(json.dumps(doc))
    text = pf.read_text()
    assert main(["op", "accept", str(pf), "user:hook-nop"]) == 1
    assert "comment" in capsys.readouterr().err and pf.read_text() == text


# --- sidecar ------------------------------------------------------------------------------------

def test_sidecar_names_user_ops(tmp_path, mcf_136):
    pf = _project(tmp_path, mcf_136, ("nop", "user:hook-nop"),
                  user_op_sources={"user:hook-nop": {"source": "https://example.invalid/op.git",
                                                     "folder_sha256": "x"}})
    out = tmp_path / "mcf-s.bin"
    assert main(["compile", str(pf), STOCK, "-o", str(out), "--allow-user-ops"]) == 0
    side = json.loads(Path(str(out) + ".json").read_text())
    assert side["user_ops"] == [{
        "type": "user:hook-nop", "version": "1.0.0",
        "folder_sha256": userop.folder_hash(tmp_path / "ops" / "hook_nop"),
        "source": "https://example.invalid/op.git", "touches": ["guicode"], "accepted": None}]


# --- ops listing --------------------------------------------------------------------------------

def test_ops_lists_user_ops_without_importing(tmp_path, mcf_136, capsys):
    marker = tmp_path / "IMPORTED"
    pf = _project(tmp_path, mcf_136, op_py=_marker_op_py(marker))
    (tmp_path / "ops" / "hook_nop" / "README.md").write_text("# Hook nop\nReads nothing.\n")
    assert main(["ops", "--project", str(pf)]) == 0
    out = capsys.readouterr().out
    assert "User ops (this project)" in out
    assert out.index("rename_label") < out.index("User ops (this project)") < out.index(
        "user:hook-nop")
    assert main(["ops", "user:hook-nop", "--project", str(pf)]) == 0
    out = capsys.readouterr().out
    assert '"version": "1.0.0"' in out and "Reads nothing." in out
    assert main(["ops", "user:hook-nop", "--project", str(pf), "--json"]) == 0
    (e,) = json.loads(capsys.readouterr().out)
    assert e["name"] == "user:hook-nop" and e["verified"] is False and e["user_op"] is True
    assert "Reads nothing." in e["readme"]
    assert not marker.exists()
    assert main(["ops", "user:hook-nop"]) == 1              # no --project: unknown
    assert "--project" in capsys.readouterr().err


# --- install / pack -----------------------------------------------------------------------------

def _empty_project(tmp_path: Path, stock: bytes) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    pf = tmp_path / "p.ffproj.json"
    pf.write_text(json.dumps(_doc(stock)))
    return pf


def _marker_op_folder(where: Path, marker: Path) -> Path:
    shutil.copytree(FIX / "hook_nop", where)
    (where / "op.py").write_text(_marker_op_py(marker))
    return where


def test_install_from_zip_does_not_import(tmp_path, mcf_136, capsys):
    marker = tmp_path / "IMPORTED"
    src = _marker_op_folder(tmp_path / "src" / "hook_nop", marker)
    z = tmp_path / "share.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for p in sorted(src.rglob("*")):
            zf.write(p, "hook_nop/" + p.relative_to(src).as_posix())
    pf = _empty_project(tmp_path / "proj", mcf_136)
    assert main(["op", "install", str(pf), str(z)]) == 0
    out = capsys.readouterr().out
    dst = tmp_path / "proj" / "ops" / "hook_nop"
    assert (dst / "op.py").is_file() and not marker.exists()
    assert "user:hook-nop" in out and "op.py" in out and "manifest.json" in out
    sha = userop.folder_hash(dst)
    assert json.loads(pf.read_text())["user_op_sources"]["user:hook-nop"] == {
        "source": str(z), "folder_sha256": sha}

    # an existing name is refused unless --replace
    assert main(["op", "install", str(pf), str(z)]) == 1
    assert "--replace" in capsys.readouterr().err
    assert main(["op", "install", str(pf), str(z), "--replace", "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["folder_sha256"] == sha and "op.py" in body["files"]
    assert not marker.exists()


@pytest.mark.parametrize("member", ["/abs/evil.py", "../evil.py", "hook_nop/../../evil.py"])
def test_install_refuses_zip_slip(tmp_path, mcf_136, capsys, member):
    src = FIX / "hook_nop"
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                zf.write(p, "hook_nop/" + p.relative_to(src).as_posix())
        zf.writestr(member, "print('escaped')\n")
    pf = _empty_project(tmp_path / "proj", mcf_136)
    assert main(["op", "install", str(pf), str(z)]) == 1
    assert "unsafe" in capsys.readouterr().err
    assert not (tmp_path / "proj" / "ops").exists()
    assert not (tmp_path / "evil.py").exists() and not (tmp_path / "proj" / "evil.py").exists()


def test_install_needs_exactly_one_op_folder(tmp_path, mcf_136, capsys):
    two = tmp_path / "two"
    shutil.copytree(FIX / "hook_nop", two / "a")
    shutil.copytree(FIX / "hook_nop", two / "b")
    pf = _empty_project(tmp_path / "proj", mcf_136)
    assert main(["op", "install", str(pf), str(two)]) == 1
    assert "exactly one" in capsys.readouterr().err
    assert main(["op", "install", str(pf), str(tmp_path / "proj")]) == 1  # none
    assert "exactly one" in capsys.readouterr().err


def test_install_from_folder_and_git(tmp_path, mcf_136, capsys):
    marker = tmp_path / "IMPORTED"
    repo = _marker_op_folder(tmp_path / "repo", marker)          # manifest at the root
    git = ["git", "-c", "user.email=test", "-c", "user.name=t"]
    subprocess.run(git + ["init", "-q", str(repo)], check=True)
    subprocess.run(git + ["-C", str(repo), "add", "."], check=True)
    subprocess.run(git + ["-C", str(repo), "commit", "-q", "-m", "op"], check=True)

    pf = _empty_project(tmp_path / "proj", mcf_136)
    url = repo.resolve().as_uri()
    assert main(["op", "install", str(pf), url]) == 0
    dst = tmp_path / "proj" / "ops" / "hook_nop"
    assert not (dst / ".git").exists() and (dst / "op.py").is_file()
    assert userop.folder_hash(dst) == userop.folder_hash(repo)       # .git is not hashed
    assert json.loads(pf.read_text())["user_op_sources"]["user:hook-nop"]["source"] == url

    pf2 = _empty_project(tmp_path / "proj2", mcf_136)
    assert main(["op", "install", str(pf2), str(repo)]) == 0
    assert not (tmp_path / "proj2" / "ops" / "hook_nop" / ".git").exists()
    assert not marker.exists()
    capsys.readouterr()

    for bad in ["http://example.invalid/op.git", "ext::sh -c touch% /tmp/x", "-uhack"]:
        assert main(["op", "install", str(pf2), "--", bad]) == 1
        assert "git URL" in capsys.readouterr().err


def test_pack_install_roundtrip_hash(tmp_path, mcf_136, capsys):
    pf = _project(tmp_path / "a", mcf_136)
    (tmp_path / "a" / "ops" / "hook_nop" / "__pycache__").mkdir(exist_ok=True)
    (tmp_path / "a" / "ops" / "hook_nop" / "__pycache__" / "op.cpython.pyc").write_bytes(b"x")
    sha = userop.folder_hash(tmp_path / "a" / "ops" / "hook_nop")
    z = tmp_path / "hook_nop.zip"
    assert main(["op", "pack", str(pf), "hook_nop", "-o", str(z), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["folder_sha256"] == sha
    with zipfile.ZipFile(z) as zf:
        assert not any("__pycache__" in n or n.endswith(".pyc") for n in zf.namelist())
        assert json.loads(zf.comment)["folder_sha256"] == sha

    pf2 = _empty_project(tmp_path / "b", mcf_136)
    assert main(["op", "install", str(pf2), str(z)]) == 0
    assert userop.folder_hash(tmp_path / "b" / "ops" / "hook_nop") == sha
    assert json.loads(pf2.read_text())["user_op_sources"]["user:hook-nop"]["folder_sha256"] == sha

    # a zip whose files no longer match the hash it was packed with is refused
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(bad, "w") as dst:
        dst.comment = src.comment
        for n in src.namelist():
            body = src.read(n)
            dst.writestr(n, body + b"\n# changed\n" if n.endswith("op.py") else body)
    pf3 = _empty_project(tmp_path / "c", mcf_136)
    capsys.readouterr()
    assert main(["op", "install", str(pf3), str(bad)]) == 1
    assert "folder_sha256" in capsys.readouterr().err


# --- Studio -------------------------------------------------------------------------------------

def test_studio_refuses_user_ops(mcf_136):
    pytest.importorskip("fastapi")
    from studio.server.engine import Engine
    proj = recipe.Project.from_json(json.dumps(_doc(mcf_136, ("nop", "user:hook-nop"))))
    eng = Engine(mcf_136, proj.base_sha256)
    res, _ = eng.compile("p", proj)
    assert not res.ok and res.errors[0]["code"] == "USER_OPS"       # the CLI's code (M5)
    assert "--allow-user-ops" in res.errors[0]["message"] and res.errors[0]["op_id"] == "nop"


# --- verify --emulate --project -----------------------------------------------------------------

def test_verify_emulate_project_keys_by_type(tmp_path, mcf_136, monkeypatch, capsys):
    """Two ops' emu_test.py files are keyed by their types, not their (shared) stem; an op with
    no emu_test.py is recorded `not emulated`; an explicit --scenario still keys by stem."""
    pytest.importorskip("unicorn")
    from fireflymcf.emu import gate
    # d is beside the project but unused: its emu_test.py never runs (final review I4)
    for n, t in (("a", "user:a"), ("b", "user:b"), ("c", "user:c"), ("d", "user:d")):
        dst = tmp_path / "ops" / n
        shutil.copytree(FIX / "hook_nop", dst)
        m = json.loads((dst / "manifest.json").read_text())
        (dst / "manifest.json").write_text(json.dumps(dict(m, name=n, type=t)))
        if n != "c":
            (dst / "emu_test.py").write_text(f"def scenario(s):\n    pass  # {n}\n")
    extra = tmp_path / "extra_test.py"
    extra.write_text("def scenario(s):\n    pass\n")
    pf = tmp_path / "p.ffproj.json"
    pf.write_text(json.dumps(_doc(mcf_136, ("a", "user:a"), ("b", "user:b"), ("c", "user:c"))))
    seen, digests = {}, {}

    def fake(image, path, timeout_s=300, *, out_dir=None, key=None, sha256=None):
        seen[key] = Path(path).read_text()
        digests[key] = sha256
        return gate.EmuResult("passed")
    monkeypatch.setattr(gate, "run_scenario_file", fake)
    build = tmp_path / "mcf-x.bin"
    build.write_bytes(mcf_136)
    base_rc = main(["verify", str(build), STOCK, "--no-render"])     # stock's name: 1
    assert main(["verify", str(build), STOCK, "--no-render", "--emulate", "--project", str(pf),
                 "--allow-user-ops", "--scenario", str(extra)]) == base_rc
    emu = json.loads((tmp_path / "mcf-x.bin.json").read_text())["emulation"]
    assert set(emu) == {"user:a", "user:b", "user:c", "extra_test"}
    assert emu["user:a"]["status"] == emu["user:b"]["status"] == "passed"
    assert "# a" in seen["user:a"] and "# b" in seen["user:b"]
    # each op's test runs only with the bytes its folder hash covered; an explicit file is unbound
    assert digests["user:a"] == hashlib.sha256(seen["user:a"].encode()).hexdigest()
    assert digests["extra_test"] is None
    assert emu["user:c"]["status"] == "not emulated" and "emu_test.py" in emu["user:c"]["reason"]
    assert "emulated, not flashed" not in capsys.readouterr().out    # c was not emulated

    assert main(["verify", str(build), STOCK, "--no-render", "--project", str(pf)]) == 2


def test_pack_refuses_a_symlink(tmp_path, mcf_136, capsys):
    secret = tmp_path / "secret.key"
    secret.write_text("PRIVATE")
    pf = _project(tmp_path / "a", mcf_136)
    (tmp_path / "a" / "ops" / "hook_nop" / "id_rsa").symlink_to(secret)
    z = tmp_path / "out.zip"
    assert main(["op", "pack", str(pf), "hook_nop", "-o", str(z)]) == 1
    assert "symbolic link" in capsys.readouterr().err and not z.exists()


# --- the engine's gates refuse a user op as structured errors (final review I1) ------------------

def test_capability_gate_is_a_structured_refusal(tmp_path, mcf_136, capsys):
    """A guidata-only user op that changes a binding's command byte without declaring the gain:
    the capability gate refuses it as JSON with a code, and nothing is written."""
    pf = _project(tmp_path, mcf_136, ("flip", "user:cmd-flip"), folders=("cmd_flip",))
    out = tmp_path / "mcf-flip.bin"
    assert main(["compile", str(pf), STOCK, "-o", str(out), "--allow-user-ops", "--json"]) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is False
    err = body["errors"][0]
    assert err["code"] == "CAPABILITY_GATE" and "gained" in err["message"]
    assert not out.exists() and not Path(str(out) + ".json").exists()

    assert main(["validate", str(pf), STOCK, "--allow-user-ops", "--json"]) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["passed"] is False and body["errors"][0]["code"] == "CAPABILITY_GATE"


def test_gates_hold_under_python_O(tmp_path, mcf_136):
    """`python -O` strips `assert`: the gates must not be asserts, or the image is written."""
    pf = _project(tmp_path, mcf_136, ("flip", "user:cmd-flip"), folders=("cmd_flip",))
    out = tmp_path / "mcf-o.bin"
    stock = Path(__file__).resolve().parent.parent / STOCK
    proc = subprocess.run(
        [sys.executable, "-O", "-c", "import sys; from fireflymcf.cli import main; "
         "sys.exit(main(sys.argv[1:]))", "compile", str(pf), str(stock), "-o", str(out),
         "--allow-user-ops", "--json"],
        capture_output=True, text=True, cwd=tmp_path, timeout=600)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["errors"][0]["code"] == "CAPABILITY_GATE"
    assert not out.exists()


# --- only the op folders the project uses are gated and imported (final review I4) --------------

def _unused_folder(tmp_path: Path, name: str, type_: str, marker: Path, touches=("guicode",)):
    dst = tmp_path / "ops" / name
    shutil.copytree(FIX / "hook_nop", dst)
    m = json.loads((dst / "manifest.json").read_text())
    (dst / "manifest.json").write_text(json.dumps(dict(m, name=name, type=type_,
                                                       touches=list(touches))))
    (dst / "op.py").write_text(_marker_op_py(marker).replace("user:hook-nop", type_))


def test_unused_op_folders_are_left_alone(tmp_path, mcf_136, capsys):
    """A folder of projects can hold an op only some of them use: a project that does not name it
    is not refused, not asked for a G12 acceptance, and never runs its code."""
    pf = tmp_path / "p.ffproj.json"
    pf.write_text(json.dumps(_doc(mcf_136, ("tank", "tank_heater_names"))))
    marker = tmp_path / "IMPORTED"
    _unused_folder(tmp_path, "other", "user:other", marker, touches=("guicode", "g12"))
    broken = tmp_path / "ops" / "broken"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{not json")
    (broken / "op.py").write_text("raise SystemExit('never')\n")

    out = tmp_path / "mcf-b.bin"
    assert main(["compile", str(pf), STOCK, "-o", str(out)]) == 0
    assert "user_ops" not in json.loads(Path(str(out) + ".json").read_text())
    assert main(["validate", str(pf), STOCK]) == 0
    assert not marker.exists()

    # a project that uses hook-nop imports that folder only; the G12 neighbour asks for nothing
    shutil.copytree(FIX / "hook_nop", tmp_path / "ops" / "hook_nop")
    pf.write_text(json.dumps(_doc(mcf_136, ("nop", "user:hook-nop"))))
    out2 = tmp_path / "mcf-u.bin"
    capsys.readouterr()
    assert main(["compile", str(pf), STOCK, "-o", str(out2), "--allow-user-ops"]) == 0
    printed = capsys.readouterr().out
    assert "user:other" not in printed and not marker.exists()
    side = json.loads(Path(str(out2) + ".json").read_text())
    assert [u["type"] for u in side["user_ops"]] == ["user:hook-nop"]


def test_a_used_type_with_no_good_folder_reports_the_broken_one(tmp_path, mcf_136, capsys):
    pf = tmp_path / "p.ffproj.json"
    pf.write_text(json.dumps(_doc(mcf_136, ("nop", "user:hook-nop"))))
    broken = tmp_path / "ops" / "hook_nop"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{not json")
    (broken / "op.py").write_text("")
    assert main(["compile", str(pf), STOCK, "-o", str(tmp_path / "mcf-x.bin"),
                 "--allow-user-ops"]) == 1
    assert "BAD_USER_OP" in capsys.readouterr().err


# --- an op that changes what the panel sends needs the G12 acceptance (final review I2) ----------

def test_op_that_changes_what_the_panel_sends_needs_g12_ack(tmp_path, mcf_136, capsys):
    """Its manifest touches guidata only, but a control now sends a new command to the G12."""
    pf = _project(tmp_path, mcf_136, ("flip", "user:cmd-flip", {"params": {"declare": True}}),
                  folders=("cmd_flip",))
    out = tmp_path / "mcf-flip.bin"
    assert main(["compile", str(pf), STOCK, "-o", str(out), "--allow-user-ops", "--json"]) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["errors"][0]["code"] == "G12_ACK_REQUIRED"
    assert "user:cmd-flip gains (" in body["errors"][0]["message"]
    assert not out.exists() and not Path(str(out) + ".json").exists()
    assert main(["validate", str(pf), STOCK, "--allow-user-ops"]) == 1
    assert "G12_ACK_REQUIRED" in capsys.readouterr().err

    # accepting it without --g12 does not do; with --g12 it builds
    assert main(["op", "accept", str(pf), "user:cmd-flip"]) == 0
    assert main(["compile", str(pf), STOCK, "-o", str(out), "--allow-user-ops"]) == 1
    assert "G12_ACK_REQUIRED" in capsys.readouterr().err and not out.exists()
    assert main(["op", "accept", str(pf), "user:cmd-flip", "--g12"]) == 0
    assert main(["compile", str(pf), STOCK, "-o", str(out), "--allow-user-ops"]) == 0
    assert main(["validate", str(pf), STOCK, "--allow-user-ops"]) == 0


def test_compile_result_sends_names_the_measured_change(tmp_path, mcf_136):
    pf = _project(tmp_path, mcf_136, ("flip", "user:cmd-flip", {"params": {"declare": True}}),
                  folders=("cmd_flip",))
    proj = recipe.Project.from_json(pf.read_text())
    with userop.registered(userop.load_user_ops(pf, proj)):
        res = recipe.compile_project(proj, mcf_136)
    assert res.ok, res.errors
    (gained,) = sorted(recipe.capabilities(res.data) - recipe.capabilities(mcf_136))
    assert res.sends == {"flip": {"type": "user:cmd-flip", "lost": [], "gained": [gained]}}

    # a user op that sends nothing new has no entry
    pf2 = _project(tmp_path / "n", mcf_136, ("nop", "user:hook-nop"))
    proj2 = recipe.Project.from_json(pf2.read_text())
    with userop.registered(userop.load_user_ops(pf2, proj2)):
        assert recipe.compile_project(proj2, mcf_136).sends == {}


# --- an acceptance is the recipient's, never the sender's (final review I3) ----------------------

def _with_example_project(folder: Path, stock: bytes) -> dict:
    """An example project inside the op folder that carries its author's acceptance."""
    doc = _doc(stock, ("nop", "user:hook-nop"),
               user_op_acceptance={"user:hook-nop": {"folder_sha256": "0" * 64, "g12": True,
                                                     "date": "2026-01-01"}})
    (folder / "example.ffproj.json").write_text(json.dumps(doc))
    return doc


def test_pack_and_install_carry_no_acceptance(tmp_path, mcf_136, capsys):
    pf = _project(tmp_path / "a", mcf_136, ("nop", "user:hook-nop"))
    folder = tmp_path / "a" / "ops" / "hook_nop"
    doc = _with_example_project(folder, mcf_136)
    z = tmp_path / "hook_nop.zip"
    assert main(["op", "pack", str(pf), "hook_nop", "-o", str(z), "--json"]) == 0
    packed_sha = json.loads(capsys.readouterr().out)["folder_sha256"]
    with zipfile.ZipFile(z) as zf:
        packed = json.loads(zf.read("hook_nop/example.ffproj.json"))
    assert "user_op_acceptance" not in packed
    assert packed == {k: v for k, v in doc.items() if k != "user_op_acceptance"}

    pf2 = _empty_project(tmp_path / "b", mcf_136)
    assert main(["op", "install", str(pf2), str(z)]) == 0       # the pack's own hash checks out
    got = tmp_path / "b" / "ops" / "hook_nop"
    assert "user_op_acceptance" not in json.loads((got / "example.ffproj.json").read_text())
    assert userop.folder_hash(got) == packed_sha
    assert "user_op_acceptance" not in json.loads(pf2.read_text())

    # installing straight from the author's folder strips it as well
    pf3 = _empty_project(tmp_path / "c", mcf_136)
    assert main(["op", "install", str(pf3), str(folder)]) == 0
    got = tmp_path / "c" / "ops" / "hook_nop"
    assert "user_op_acceptance" not in json.loads((got / "example.ffproj.json").read_text())
    assert "user_op_acceptance" not in json.loads(pf3.read_text())


def test_ops_listing_shows_the_projects_acceptance(tmp_path, mcf_136, capsys):
    """A project file from someone else carries their acceptances; `ops --project` shows them."""
    pf = _project(tmp_path, mcf_136, ("nop", "user:hook-nop"))
    sha = userop.folder_hash(tmp_path / "ops" / "hook_nop")
    doc = json.loads(pf.read_text())
    rec = {"folder_sha256": sha, "g12": True, "date": "2026-01-01"}
    doc["user_op_acceptance"] = {"user:hook-nop": rec}
    pf.write_text(json.dumps(doc))
    assert main(["ops", "--project", str(pf), "--json"]) == 0
    (entry,) = [e for e in json.loads(capsys.readouterr().out) if e.get("user_op")]
    assert entry["accepted"] == rec
    assert main(["ops", "--project", str(pf)]) == 0
    assert "accepted 2026-01-01 (G12 included)" in capsys.readouterr().out


# --- a scenario from an op folder is that op's code (final review M1) ----------------------------

def test_scenario_from_an_op_folder_needs_allow_user_ops(tmp_path, mcf_136, capsys):
    marker = tmp_path / "RAN"
    folder = tmp_path / "ops" / "hook_nop"
    shutil.copytree(FIX / "hook_nop", folder)
    test = folder / "emu_test.py"
    test.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
                    "def scenario(s):\n    pass\n")
    build = tmp_path / "mcf-x.bin"
    build.write_bytes(mcf_136)

    assert main(["emulate", str(build), "--scenario", str(test)]) == 1
    err = capsys.readouterr().err
    assert "USER_OPS" in err and str(folder.resolve()) in err
    assert main(["emulate", str(build), "--scenario", str(test), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "USER_OPS"
    assert main(["verify", str(build), STOCK, "--no-render", "--emulate",
                 "--scenario", str(test)]) == 1
    assert "USER_OPS" in capsys.readouterr().err
    assert not marker.exists()


def test_json_refusals_have_the_compile_error_shape(tmp_path, mcf_136, capsys):
    """Every JSON refusal entry has `CompileResult.errors`' keys (final review M5)."""
    keys = {"op_id", "code", "message", "offers"}
    pf = _project(tmp_path, mcf_136, ("nop", "user:hook-nop"))
    out = tmp_path / "mcf-u.bin"
    assert main(["compile", str(pf), STOCK, "-o", str(out), "--json"]) == 1          # USER_OPS
    assert set(json.loads(capsys.readouterr().out)["errors"][0]) == keys
    assert main(["validate", str(pf), STOCK, "--json"]) == 1
    assert set(json.loads(capsys.readouterr().out)["errors"][0]) == keys
    doc = _doc(mcf_136, ("tank", "tank_heater_names"))
    doc["base_sha256"] = "0" * 64                                                    # a RecipeError
    pf.write_text(json.dumps(doc))
    assert main(["compile", str(pf), STOCK, "-o", str(out), "--json"]) == 1
    err = json.loads(capsys.readouterr().out)["errors"][0]
    assert set(err) == keys and err["code"] == "BASE_MISMATCH"
    assert main(["validate", str(pf), STOCK, "--json"]) == 1
    assert set(json.loads(capsys.readouterr().out)["errors"][0]) == keys


# --- op install / pack: limits and I/O failures are structured -----------------------------------

def test_install_refuses_a_zip_too_large_unpacked(tmp_path, mcf_136, capsys, monkeypatch):
    from fireflymcf import opshare
    pf = _project(tmp_path / "a", mcf_136)
    z = tmp_path / "hook_nop.zip"
    assert main(["op", "pack", str(pf), "hook_nop", "-o", str(z)]) == 0
    size = sum(i.file_size for i in zipfile.ZipFile(z).infolist())
    monkeypatch.setattr(opshare, "MAX_ZIP_BYTES", size - 1)
    pf2 = _empty_project(tmp_path / "b", mcf_136)
    capsys.readouterr()
    assert main(["op", "install", str(pf2), str(z), "--json"]) == 1
    err = json.loads(capsys.readouterr().out)["errors"][0]
    assert err["code"] == "UNSAFE_SOURCE" and "unpacks to" in err["message"]
    assert not (tmp_path / "b" / "ops").exists()


def test_op_io_failure_is_structured(tmp_path, mcf_136, capsys):
    pf = _project(tmp_path / "a", mcf_136)
    (tmp_path / "file").write_text("x")
    assert main(["op", "pack", str(pf), "hook_nop", "-o", str(tmp_path / "file" / "x.zip"),
                 "--json"]) == 1
    err = json.loads(capsys.readouterr().out)["errors"][0]
    assert err["code"] == "OP_IO_ERROR" and set(err) == {"op_id", "code", "message", "offers"}
    assert main(["op", "pack", str(pf), "hook_nop", "-o", str(tmp_path / "file" / "x.zip")]) == 1
    assert "OP_IO_ERROR" in capsys.readouterr().err
