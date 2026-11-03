#!/usr/bin/env python3
"""Scrub gate: no firmware, captures or PII in tracked files of the public repo.

    python scripts/scrub_check.py [--all-tracked | PATH...]

Exits 1 with one line per finding.  Exemptions live in scrub-allow.txt as
`path:rule  # why`.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import struct
import subprocess
import sys
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parent.parent
ALLOW_FILE = ROOT / "scrub-allow.txt"

FIRMWARE_EXTS = {".bin", ".mcf", ".s19", ".hex", ".elf", ".candump", ".log"}
# container.MAGIC (0xFD53B912), GuiCode magic 0x1A2B3C4D, cli.bin magic 0xC11B102E, little-endian
MAGICS = {
    "mcf container": struct.pack("<I", 0xFD53B912),
    "GuiCode": struct.pack("<I", 0x1A2B3C4D),
    "cli.bin": struct.pack("<I", 0xC11B102E),
}
BLOB_EXTS = {".py", ".md", ".json"}
BLOB_RE = re.compile(r"[A-Za-z0-9+/=]{512,}")
ALLOWED_EMAILS = {"dbergl@hotmail.com", "noreply@anthropic.com"}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b")  # TLD must be alphabetic (not pip @v0.3.0)
LINE_RULES = [
    ("pii-host", re.compile(r"coach2mqtt|\b\w+@[\w.-]+:")),
    ("pii-home", re.compile(r"/home/[a-z]|C:\\Users\\")),
    ("pii-ip", re.compile(
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b")),
]
# Documentation placeholders that look like what a rule forbids (an SSH target in a usage line).
PLACEHOLDERS = {"USER@HOST:"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int | None
    rule: str
    text: str

    def __str__(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else self.path
        return f"{loc}: {self.rule}: {self.text}"


def tracked_files() -> list[pathlib.Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True,
                         capture_output=True).stdout
    return [ROOT / p.decode() for p in out.split(b"\0") if p]


def load_allow() -> list[str]:
    if not ALLOW_FILE.exists():
        return []
    entries = []
    for raw in ALLOW_FILE.read_text().splitlines():
        entry = raw.split("#", 1)[0].strip()
        if entry:
            entries.append(entry)
    return entries


def _rel(p: pathlib.Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return p.name


def scan(paths: list[pathlib.Path], allow: list[str]) -> list[Finding]:
    allowed = set(allow)
    found: list[Finding] = []
    for p in paths:
        p = pathlib.Path(p)
        if not p.is_file():
            continue
        rel = _rel(p)
        keys = {rel, p.name}
        hits: list[Finding] = []
        if p.suffix.lower() in FIRMWARE_EXTS and not (
                p.suffix.lower() == ".log" and rel.startswith("tests/fixtures/")):
            hits.append(Finding(rel, None, "firmware-ext", p.suffix))
        data = p.read_bytes()
        for name, magic in MAGICS.items():
            if magic in data:
                hits.append(Finding(rel, None, "firmware-magic", f"{name} magic bytes"))
        text = data.decode("utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if p.suffix.lower() in BLOB_EXTS and BLOB_RE.search(line):
                hits.append(Finding(rel, n, "blob", "base64/hex literal >= 512 chars"))
            for rule, rx in LINE_RULES:
                m = next((m for m in rx.finditer(line) if m.group(0) not in PLACEHOLDERS), None)
                if m:
                    hits.append(Finding(rel, n, rule, m.group(0)))
            for m in EMAIL_RE.finditer(line):
                if m.group(0) not in ALLOWED_EMAILS:
                    hits.append(Finding(rel, n, "pii-email", m.group(0)))
        found += [h for h in hits if not any(f"{k}:{h.rule}" in allowed for k in keys)]
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all-tracked", action="store_true")
    ap.add_argument("paths", nargs="*", type=pathlib.Path)
    a = ap.parse_args(argv)
    paths = a.paths if a.paths and not a.all_tracked else tracked_files()
    findings = scan(paths, load_allow())
    for f in findings:
        print(f)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
