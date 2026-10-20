"""UTF-8 console output on every platform.

On Windows a piped stdout (every run under Claude Code, every `> file`) takes the ANSI code page,
cp1252, which has no `↔` or `•`; `doctor` raised `UnicodeEncodeError` on its first such line.
Each entry point calls `utf8()` before it prints.
"""
import sys


def utf8() -> None:
    """Re-encode stdout and stderr as UTF-8; a character the stream still refuses is replaced."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
