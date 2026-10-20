"""Project file store: *.ffproj.json under one root directory.

Safety: the name regex is the only gate between a URL path segment and the
filesystem, so it is strict (no dots, no separators, no leading dash); every
resolved path is additionally checked to be inside the root. Saves are atomic
(write temp + rename) with an mtime guard against a stale tab clobbering a
newer save, and the previous version is kept as `.bak`."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from fireflymcf import recipe


class StoreError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status

    def detail(self) -> dict:
        return {"code": self.code, "message": str(self)}


class ProjectStore:
    NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

    def __init__(self, root: str, base_sha256: str):
        self.root = Path(root)
        self.base_sha256 = base_sha256
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        if not self.NAME_RE.match(name or ""):
            raise StoreError("BAD_NAME", "project name must match %s"
                             % self.NAME_RE.pattern, status=422)
        p = (self.root / ("%s.ffproj.json" % name)).resolve()
        if self.root.resolve() not in p.parents:
            raise StoreError("BAD_NAME", "path escapes the projects root", status=422)
        return p

    def list(self) -> list:
        out = []
        for p in sorted(self.root.glob("*.ffproj.json")):
            out.append({"name": p.name[:-len(".ffproj.json")],
                        "mtime": p.stat().st_mtime})
        return out

    def create(self, name: str) -> dict:
        p = self._path(name)
        if p.exists():
            raise StoreError("EXISTS", "project %r already exists" % name, status=409)
        project = recipe.Project(base_sha256=self.base_sha256, build_name=None, ops=[])
        p.write_text(project.to_json(), encoding="utf-8")
        return {"name": name, "mtime": p.stat().st_mtime,
                "project": json.loads(project.to_json())}

    def load(self, name: str) -> dict:
        p = self._path(name)
        if not p.exists():
            raise StoreError("NOT_FOUND", "no project %r" % name, status=404)
        text = p.read_text(encoding="utf-8")
        recipe.Project.from_json(text)          # validate; raises RecipeError
        return {"name": name, "mtime": p.stat().st_mtime, "project": json.loads(text)}

    def load_project(self, name: str) -> recipe.Project:
        p = self._path(name)
        if not p.exists():
            raise StoreError("NOT_FOUND", "no project %r" % name, status=404)
        return recipe.Project.from_json(p.read_text(encoding="utf-8"))

    def save(self, name: str, project_doc: dict, expected_mtime: float | None) -> float:
        p = self._path(name)
        if not p.exists():
            raise StoreError("NOT_FOUND", "no project %r" % name, status=404)
        if expected_mtime is not None and abs(p.stat().st_mtime - expected_mtime) > 1e-6:
            raise StoreError("STALE_SAVE", "project changed on disk since it was "
                             "loaded (expected mtime %s, found %s)"
                             % (expected_mtime, p.stat().st_mtime), status=409)
        text = json.dumps(project_doc, indent=2)
        recipe.Project.from_json(text)          # refuse whole on invalid; raises RecipeError
        prev_text = p.read_text(encoding="utf-8")
        bak = p.with_suffix(p.suffix + ".bak")
        fd, tmp = tempfile.mkstemp(dir=str(self.root), suffix=".tmp")
        bak_fd, bak_tmp = tempfile.mkstemp(dir=str(self.root), suffix=".bak.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            with os.fdopen(bak_fd, "w", encoding="utf-8") as f:
                f.write(prev_text)
            os.replace(bak_tmp, bak)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
            if os.path.exists(bak_tmp):
                os.unlink(bak_tmp)
        return p.stat().st_mtime
