"""The release gate: the tag, `pyproject.toml` and the plugin manifest name one version.

    python scripts/check_version.py v0.2.0    # exit 0 only if all three say 0.2.0
    python scripts/check_version.py           # exit 0 if the two files agree

The release workflow runs it before building, so a tag pushed without bumping both files fails
before anything is published. Regex, not `tomllib`, so it runs on Python 3.10.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def versions(root: Path = ROOT) -> dict:
    py = (root / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', py)
    manifest = json.loads((root / "plugin" / ".claude-plugin" / "plugin.json")
                          .read_text(encoding="utf-8"))
    return {"pyproject.toml": m.group(1) if m else None,
            "plugin/.claude-plugin/plugin.json": manifest.get("version")}


def problems(tag: str | None, found: dict) -> list[str]:
    out = []
    if len(set(found.values())) != 1 or None in found.values():
        out.append("versions disagree: " + ", ".join("%s %s" % kv for kv in found.items()))
    if tag is not None:
        want = next(iter(found.values()))
        if tag != "v%s" % want:
            out.append("tag %s does not match version %s (expected tag v%s)" % (tag, want, want))
    return out


def main(argv: list[str]) -> int:
    found = versions()
    errors = problems(argv[0] if argv else None, found)
    for e in errors:
        print("error: " + e, file=sys.stderr)
    if not errors:
        print("version %s" % next(iter(found.values())))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
