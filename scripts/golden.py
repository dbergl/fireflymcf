#!/usr/bin/env python3
"""Byte-identity table: record and check the sha256 of every project's compiled image.

    golden.py record TABLE.json STOCK_DIR PROJECT.ffproj.json...
    golden.py check  TABLE.json STOCK_DIR PROJECT.ffproj.json...

Each project's stock image is the `mcf.bin` under STOCK_DIR (searched recursively) whose sha256
is the project's `base_sha256`. Unverified ops do not block the hash. A project that fails to
compile is recorded as "error: CODE", so `check` also flags a change from failing to passing.
`check` prints every mismatch and exits 1.
"""
import hashlib
import json
import sys
from pathlib import Path

from fireflymcf import recipe

_STOCK_CACHE: dict = {}


def _stocks(stock_dir: Path) -> dict:
    key = str(stock_dir.resolve())
    if key not in _STOCK_CACHE:
        found = {}
        for p in sorted(stock_dir.rglob("mcf.bin")):
            found.setdefault(hashlib.sha256(p.read_bytes()).hexdigest(), p)
        _STOCK_CACHE[key] = found
    return _STOCK_CACHE[key]


def compile_sha(project_path: Path, stock_dir: Path) -> str:
    project = recipe.Project.from_json(Path(project_path).read_text(encoding="utf-8"))
    stock = _stocks(Path(stock_dir)).get(project.base_sha256)
    if stock is None:
        raise SystemExit("%s: no mcf.bin under %s has sha256 %s"
                         % (project_path, stock_dir, project.base_sha256))
    res = recipe.compile_project(project, stock.read_bytes())
    if not res.ok:
        code = res.errors[0].get("code") if res.errors and isinstance(res.errors[0], dict) \
            else res.errors[0] if res.errors else "unknown"
        return "error: %s" % code
    return hashlib.sha256(res.data).hexdigest()


def main(argv: list) -> int:
    if len(argv) < 4 or argv[0] not in ("record", "check"):
        print(__doc__)
        return 2
    mode, table, stock_dir, projects = argv[0], Path(argv[1]), Path(argv[2]), argv[3:]
    got = {p: compile_sha(Path(p), stock_dir) for p in projects}
    if mode == "record":
        table.parent.mkdir(parents=True, exist_ok=True)
        table.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("recorded %d projects (%d errors) to %s"
              % (len(got), sum(v.startswith("error:") for v in got.values()), table))
        return 0
    want = json.loads(table.read_text(encoding="utf-8"))
    bad = 0
    for p, sha in got.items():
        if p not in want:
            print("MISSING from table: %s" % p)
            bad += 1
        elif want[p] != sha:
            print("MISMATCH: %s\n  table %s\n  now   %s" % (p, want[p], sha))
            bad += 1
    print("%d projects checked, %d differ" % (len(got), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
