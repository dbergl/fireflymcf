#!/usr/bin/env bash
# The plugin's SessionStart hook: run ensure_fireflymcf.py under the first Python 3.10+ found.
# Claude Code runs a hook command in bash (Git Bash on Windows). `python3` comes first, then
# `python`, then the Windows launcher `py -3`; each is probed for 3.10+ first, which also skips
# the Windows Store's `python3` stub (it exits non-zero without running anything). Prints one
# line on stdout, which Claude reads, and always exits 0 so a session never fails to start.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for py in python3 python "py -3"; do
  # $py unquoted on purpose: "py -3" is a command and an argument
  if $py -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    $py "$here/ensure_fireflymcf.py"
    exit 0
  fi
done
echo "fireflymcf plugin hook: no Python 3.10+ found (tried python3, python, py -3), so" \
     "fireflymcf is not installed. Install Python 3.10+, then run:" \
     "pip install \"fireflymcf[full] @ git+https://github.com/dbergl/fireflymcf.git\""
exit 0
