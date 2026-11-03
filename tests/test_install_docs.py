"""Every install command the docs and the plugin give an owner names this repository.

fireflymcf is not on PyPI, so `pip install fireflymcf` by name fails today and would install
someone else's package the day another project registers the name. The build-firmware skill
said exactly that until 2026-10-20.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")),
        *sorted((ROOT / "plugin").rglob("*.md"))]
PIP = re.compile(r"pip install\s+(?:-\S+\s+)*['\"]?(fireflymcf[^\s'\"`]*)")


def _by_name(text: str) -> list[str]:
    return [m.group(0) for m in PIP.finditer(text)
            if "git+" not in text[m.start():m.start() + 200].split("\n")[0]]


def test_no_doc_installs_fireflymcf_by_name():
    hits = [f"{p.relative_to(ROOT)}: {cmd}" for p in DOCS
            for cmd in _by_name(p.read_text(encoding="utf-8"))]
    assert hits == []


def test_the_check_catches_a_by_name_install():
    assert _by_name("run `pip install 'fireflymcf[full]'` first") != []
    assert _by_name('pip install "fireflymcf[full] @ git+https://github.com/x/fireflymcf.git"') == []
    assert _by_name("pip install -e '.[full,test]'") == []


# --- every command a skill names exists -------------------------------------------------------
# A skill is followed literally by an agent that has nothing else: a command it names that the CLI
# lacks (renamed, never added, `slotsource` for `slot-source`) is a dead end mid-build. Both
# skills write `fireflymcf CMD` or `$F CMD` (F = the path the plugin's hook printed) in code.

SKILLS = sorted((ROOT / "plugin" / "skills").glob("*/SKILL.md"))
_CODE = re.compile(r"```[^\n]*\n(.*?)```|`([^`]+)`", re.S)
_CMD = re.compile(r"(?:^|(?<=[\s;(|&]))(?:fireflymcf|\$F)[ \t]+([a-z][a-z0-9-]*)"
                  r"(?:[ \t]+([a-z][a-z0-9-]*))?([^\n;|&]*)", re.M)
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")


def _code_spans(text: str) -> list[str]:
    return [m.group(1) or m.group(2) for m in _CODE.finditer(text)]


def named_commands(text: str) -> set[tuple[str, str | None, tuple]]:
    """{(command, op subcommand or None, flags)} for every `fireflymcf CMD ...` in code."""
    out = set()
    for span in _code_spans(text):
        for m in _CMD.finditer(span):
            cmd, sub, rest = m.group(1), m.group(2), m.group(3) or ""
            rest = re.split(r"\s#", rest, maxsplit=1)[0]          # a shell comment is prose
            if cmd != "op":
                rest, sub = ((sub or "") + rest), None
            out.add((cmd, sub, tuple(sorted(set(_FLAG.findall(rest))))))
    return out


def _help(argv: list[str]) -> str | None:
    """The CLI's help text for `argv`, or None when argparse rejects it."""
    import contextlib
    import io

    from fireflymcf.cli import main
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        try:
            main([*argv, "--help"])
        except SystemExit as e:
            return buf.getvalue() if e.code == 0 else None
    return buf.getvalue()


def test_skills_exist():
    assert {p.parent.name for p in SKILLS} >= {"build-firmware", "write-new-op"}


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_every_command_a_skill_names_exists(skill):
    named = named_commands(skill.read_text(encoding="utf-8"))
    assert named, f"{skill} names no fireflymcf command"
    missing = []
    for cmd, sub, flags in sorted(named, key=str):
        argv = [cmd] + ([sub] if sub else [])
        text = _help(argv)
        if text is None:
            missing.append(" ".join(argv))
            continue
        missing += [f"{' '.join(argv)} {f}" for f in flags if f not in text]
    assert missing == [], f"{skill.relative_to(ROOT)} names what the CLI lacks: {missing}"


def test_the_command_check_catches_a_missing_command():
    named = named_commands("run `$F slotsource MCF` then\n```\nfireflymcf op frobnicate P\n"
                           "fireflymcf compile P S -o x --allow-user-opz  # not --frob\n```\n")
    assert ("slotsource", None, ()) in named and ("op", "frobnicate", ()) in named
    assert _help(["slotsource"]) is None and _help(["op", "frobnicate"]) is None
    assert ("compile", None, ("--allow-user-opz",)) in named
    assert "--allow-user-opz" not in _help(["compile"])
    assert "--allow-user-ops" in _help(["compile"])


RISK_PANEL = ("This changes what your panel shows or sends. Worst case the panel draws wrongly or a "
              "button misbehaves; you can always flash your stock mcf.bin from USB to undo it.")
RISK_G12 = ("This changes your G12 configuration, which drives real loads: pumps, slides, heaters, "
            "lights. A mistake can switch something on or off when you don't expect it. Do you "
            "want to go ahead?")


@pytest.mark.parametrize("name", ["build-firmware", "write-new-op"])
def test_skills_carry_the_risk_statements_verbatim(name):
    text = " ".join((ROOT / "plugin" / "skills" / name / "SKILL.md")
                    .read_text(encoding="utf-8").split())
    assert RISK_PANEL in text and RISK_G12 in text
    for phrase in ("emulated, not flashed", "not emulated: <reason>",
                   "emulator test FAILED: <reason>", "--allow-user-ops"):
        assert phrase in text, phrase


# --- every file a skill points at ships with the plugin ----------------------------------------
# The marketplace installs `plugin/` only (`.claude-plugin/marketplace.json` `source`), and the
# package ships no docs, so a skill's `docs/X.md` is a dead pointer on an owner's machine. A
# reference must resolve inside the plugin tree; the only exceptions are named citations of the
# repository's evidence docs, which must exist here.

PLUGIN = ROOT / "plugin"
SKILL_FILES = sorted(p for p in (PLUGIN / "skills").rglob("*")
                     if p.suffix in (".md", ".py") and "__pycache__" not in p.parts)
REPO_CITATIONS = {"docs/CHANGE-CATALOG.md", "docs/BUS-BASELINE.md", "docs/GUICODE.md",
                  "BUS-BASELINE.md", "GUICODE.md", "CONTROL-BEHAVIOR.md"}
_LINK = re.compile(r"\]\(([^)\s]+)\)")
_PATHISH = re.compile(r"`([^`\s]+)`|(?<![\w/.`-])((?:\.\./|\./|examples/|docs/|plugin/)[\w./-]+"
                      r"|[\w-]+\.md)(?![\w/`-])")
_GENERIC = {"README.md", "SKILL.md"}          # an op folder's own README, any skill's SKILL.md


def file_refs(text: str) -> set[str]:
    refs = {m.group(1) for m in _LINK.finditer(text)
            if not re.match(r"[a-z]+:|#", m.group(1))}
    for m in _PATHISH.finditer(text):
        tok = (m.group(1) or m.group(2)).rstrip('"\'.,;:()')
        tok = tok.split("#")[0]
        if tok in _GENERIC or "<" in tok or "*" in tok:
            continue
        if tok.endswith(".md") or tok.startswith(("examples/", "docs/", "plugin/", "../", "./")):
            refs.add(tok)
    return refs


def _dangling(path: Path) -> list[str]:
    bad = []
    for ref in sorted(file_refs(path.read_text(encoding="utf-8"))):
        if ref in REPO_CITATIONS:
            name = ref if ref.startswith("docs/") else "docs/" + ref
            if not (ROOT / name).is_file():
                bad.append(f"{ref} (cited, but not in the repository)")
            continue
        target = (path.parent / ref).resolve()
        if not (target.exists() and target.is_relative_to(PLUGIN.resolve())):
            bad.append(ref)
    return bad


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: str(p.relative_to(PLUGIN)))
def test_skill_file_references_ship_with_the_plugin(path):
    assert _dangling(path) == []


def test_the_reference_check_catches_a_dead_pointer(tmp_path):
    f = PLUGIN / "skills" / "write-new-op" / "SKILL.md"
    assert "docs/USER-OPS.md" in file_refs("read `docs/USER-OPS.md` first")
    assert "../../USER-OPS.md" in file_refs('see ../../USER-OPS.md "Primitives"')
    assert "USER-OPS.md" in file_refs("see [the guide](USER-OPS.md)")
    probe = f.parent / "probe-not-shipped.md"
    try:
        probe.write_text("see `docs/USER-OPS.md` and [x](missing.md) and USER-OPS.md\n")
        assert _dangling(probe) == ["docs/USER-OPS.md", "missing.md"]
    finally:
        probe.unlink()


def test_the_cli_names_this_repository_when_the_emulator_is_missing():
    from fireflymcf import cli
    assert "git+https://github.com/dbergl/fireflymcf.git" in cli.EMU_MISSING
    assert _by_name(cli.EMU_MISSING) == []


# --- a skill that cites the repository's docs says where to read them --------------------------
# The package ships no docs (see above), so a cited `docs/X.md` must come with the public URL.
# Run 1 of the 2026-11-02 acceptance went looking for the docs on the local disk instead.
DOCS_URL = "https://github.com/dbergl/fireflymcf/tree/main/docs"
RAW_URL = "https://raw.githubusercontent.com/dbergl/fireflymcf/main/docs/"


@pytest.mark.parametrize("path", [p for p in SKILL_FILES if p.suffix == ".md"],
                         ids=lambda p: str(p.relative_to(PLUGIN)))
def test_a_file_citing_repository_docs_names_where_they_are(path):
    cited = file_refs(path.read_text(encoding="utf-8")) & REPO_CITATIONS
    if not cited:
        return
    text = path.read_text(encoding="utf-8")
    assert DOCS_URL in text and RAW_URL in text, (sorted(cited), "no docs URL")


def test_the_scenario_calls_the_skill_lists_exist():
    """Every `s.NAME(` the write-new-op skill and guide list is a public Scenario member."""
    import inspect

    pytest.importorskip("unicorn")
    from fireflymcf.emu.scenario import Scenario
    for name in ("SKILL.md", "USER-OPS.md"):
        text = (PLUGIN / "skills" / "write-new-op" / name).read_text(encoding="utf-8")
        calls = set(re.findall(r"`s\.([a-z][a-z0-9_]*)", text))
        assert "set_g12_selector" in calls, name
        src = Path(inspect.getsourcefile(Scenario)).read_text(encoding="utf-8")
        missing = sorted(c for c in calls
                         if not hasattr(Scenario, c) and f"self.{c} = " not in src)
        assert missing == [], (name, missing)
