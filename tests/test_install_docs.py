"""Every install command the docs and the plugin give an owner names this repository.

fireflymcf is not on PyPI, so `pip install fireflymcf` by name fails today and would install
someone else's package the day another project registers the name. The build-firmware skill
said exactly that until 2026-10-20.
"""
import re
from pathlib import Path

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
