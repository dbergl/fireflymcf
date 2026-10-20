#!/usr/bin/env bash
# Link the private research repository's data into this checkout so the full test suite runs.
#
# The tests read the firmware corpus, coach captures (and their transcription, answer_key.py) and
# render configurations at repo-relative paths. (The tests that pin the reference coach's builds
# live in the research repository itself, under tests/engine/.)
# None of that is distributable, so it is never committed here: this script symlinks it in under
# the same names, and .gitignore keeps the links out of git. Without the links, the tests that
# need them skip (tests/conftest.py).
#
#   scripts/link-research.sh [RESEARCH_REPO]     # default: ../firefly-firmware
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
src="$(cd "${1:-$here/../firefly-firmware}" && pwd)"
for name in "firefly-11.23" "firefly-13.6" "firefly-14.7 (Terrain)" captures configs; do
  [ -e "$src/$name" ] || { echo "missing in $src: $name" >&2; exit 1; }
  ln -sfn "$src/$name" "$here/$name"
done
mkdir -p "$here/build"
ln -sfn "$src/build/coach-wd.json" "$here/build/coach-wd.json"
ln -sfn "$src/tests/answer_key.py" "$here/tests/answer_key.py"
echo "linked research data from $src"
