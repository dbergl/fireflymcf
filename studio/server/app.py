"""The Firefly Studio HTTP layer. Routes stay thin: parsing and HTTP concerns here,
everything else in projects.py / engine.py / reference.py. The factory takes its
paths as parameters so tests run against throwaway directories."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse

from studio.server.engine import (Engine, EngineError, parse_pairs,
                                  resolve_slots)
from studio.server.projects import ProjectStore, StoreError
from fireflymcf import cli, patch, recipe


def _export_filename(name: str) -> str:
    # The one place the "mcf-<name>.bin" naming rule lives -- the bootloader lists
    # nothing else, so every route that names or serves an export goes through this.
    return "mcf-%s.bin" % name


def _atomic_write(directory: str, path: str, data: bytes) -> None:
    """Write `data` to `path` without ever exposing a partial or interleaved file.
    Uses a per-call unique temp name in the same directory (tempfile.mkstemp, the
    same pattern ProjectStore.save uses) rather than a fixed `path + ".tmp"`: two
    concurrent writers to the same `path` each get their own tmp file, so neither
    can observe or promote the other's half-written bytes. `finally` unlinks the
    tmp file whenever it is still present (write raised, or os.replace raised) --
    a completed replace leaves nothing at `tmp` to clean up."""
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def create_app(stock_path: str = "firefly-13.6/mcf.bin",
               projects_root: str = "projects",
               build_root: str = "build") -> FastAPI:
    app = FastAPI(title="Firefly Studio", docs_url="/api/docs")
    # The server binds to loopback, but a page on any site can rebind its own hostname to
    # 127.0.0.1 and then reach this API as same-origin. Refusing every Host but loopback closes
    # that: a rebound request still carries the attacker's hostname.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(LOOPBACK_HOSTS))

    # Imported here rather than at module scope to keep PIL off the import path for
    # anything that only wants create_app's signature; hoisted above the route closures
    # because _page_root resolves the extra OPTIONS pages through it.
    from studio.server import reference

    with open(stock_path, "rb") as f:
        stock = f.read()
    base_sha256 = hashlib.sha256(stock).hexdigest()
    stock_build_name = patch.build_name(stock)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "stock_build_name": stock_build_name,
                "base_sha256": base_sha256}

    @app.get("/api/schemas")
    def schemas():
        out = {}
        for name, cls in sorted(recipe.REGISTRY.items()):
            doc = (cls.__doc__ or name).strip().splitlines()[0]
            out[name] = {"params": cls.PARAMS, "doc": doc,
                         "hardware_verified": cls.hardware_verified}
        return out

    # HTTPException is raised directly by several routes below (export_file,
    # ref_font_specimen), so it stays imported at module scope for them.
    store = ProjectStore(projects_root, base_sha256)
    engine = Engine(stock, base_sha256)

    @app.exception_handler(StoreError)
    def _store_error(_, exc: StoreError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail()})

    @app.exception_handler(recipe.RecipeError)
    def _recipe_error(_, exc: recipe.RecipeError):
        return JSONResponse(status_code=422, content={"detail": exc.to_json()})

    @app.exception_handler(EngineError)
    def _engine_error(_, exc: EngineError):
        # exc.payload is either a dict of extra detail fields (e.g. NO_SUCH_PAGE's
        # {"page", "choices"}) or a list of per-op errors (COMPILE_FAILED). Branch on
        # type so both surface naturally: detail.choices for the former,
        # detail.errors[0].code for the latter.
        if isinstance(exc.payload, dict):
            body = {"code": exc.code, **exc.payload}
        else:
            body = {"code": exc.code, "errors": exc.payload}
        return JSONResponse(status_code=exc.status, content={"detail": body})

    @app.exception_handler(AssertionError)
    def _gate_error(_, exc: AssertionError):
        return JSONResponse(status_code=500,
                            content={"detail": {"code": "GATE_FAILED",
                                                "message": str(exc)}})

    @app.exception_handler(Exception)
    def _unhandled_error(_, exc: Exception):
        # The catch-all: nothing above this point covers every code path (a bad
        # project shape reaching a handler that assumes well-formed data, e.g.), so
        # this is the backstop that keeps such a bug a structured 500 instead of an
        # unhandled traceback rendered as plain text.
        return JSONResponse(status_code=500,
                            content={"detail": {"code": "INTERNAL",
                                                "message": str(exc)}})

    @app.get("/api/projects")
    def list_projects():
        return store.list()

    @app.post("/api/projects", status_code=201)
    def create_project(body: dict = Body(...)):
        return store.create(body.get("name", ""))

    @app.get("/api/projects/{name}")
    def get_project(name: str):
        return store.load(name)

    @app.put("/api/projects/{name}")
    def put_project(name: str, body: dict = Body(...)):
        mtime = store.save(name, body.get("project") or {},
                           body.get("expected_mtime"))
        return {"name": name, "mtime": mtime}

    @app.post("/api/projects/{name}/compile")
    def compile_project(name: str):
        project = store.load_project(name)
        result, tag = engine.compile(name, project)
        return {"ok": result.ok, "errors": result.errors,
                "manifest": result.manifest, "unverified": result.unverified,
                "size": len(result.data) if result.data else None, "etag": tag}

    # Compile-etag-keyed memo of `reference.options_page_names` results: the walk is
    # 0.36 s and _page_root runs on EVERY render/placements request for the three
    # OPTIONS pages, 304s included. Keyed by the compile etag because the walk reads
    # only `data`, which the etag determines; bounded like the engine's memos.
    _options_names_memo: dict = {}

    def _options_names(data: bytes, tag: str | None) -> dict:
        if tag is None:
            return reference.options_page_names(data)
        if tag not in _options_names_memo:
            while len(_options_names_memo) >= 32:
                _options_names_memo.pop(next(iter(_options_names_memo)))
            _options_names_memo[tag] = reference.options_page_names(data)
        return _options_names_memo[tag]

    def _page_root(data: bytes, page: str, root: int | None,
                   tag: str | None = None) -> int:
        if root is not None:
            return root
        try:
            return cli._resolve_page_root(data, page)
        except ValueError:
            pass
        # A page is found by its header LABEL, so renaming that header makes the page
        # unfindable by name -- '   HOME' -> '   MY COACH' is a change this editor
        # exists to make (it is in reference build v1), and after it the canvas
        # could no longer show the very page being edited. Object indices do not move
        # under any op the engine has (relocation repairs the tables), so resolving the
        # name against STOCK and using that root is both stable and honest: same page,
        # whatever its header now reads.
        try:
            return cli._resolve_page_root(engine.stock, page)
        except ValueError:
            pass
        # Only now: the extra OPTIONS pages. `cli._resolve_page_root` matches on the
        # header label and all of them read "OPTIONS", so it can only ever return the
        # first -- without this the names `reference.pages` advertises would 404. Kept
        # in the fallback rather than tried first because the walk is the expensive
        # path -- see `_options_names`, which memoises it per compile etag.
        extra = _options_names(data, tag)
        if page in extra:
            return extra[page]
        raise EngineError("NO_SUCH_PAGE",
                          {"page": page,
                           "choices": sorted(set(cli._PAGE_NAMES) | set(extra))},
                          status=404)

    def _knob_tag(tag: str, resolved_root: int, tank_level, slots, switches) -> str:
        # The response ETag must cover the compile etag AND the preview knobs, so
        # different knobs give different tags (and a 304 only fires when both the
        # compiled bytes and the requested preview are unchanged). Task 7 reuses this
        # exact recipe for the placements route -- kept as a standalone helper for that.
        # Hashes the RESOLVED root, not the raw (possibly-omitted) `root` query param:
        # two page names that resolve to the same root must share a tag, and two
        # different pages (both defaulting root=None) must NOT collide on one.
        return hashlib.sha256(("%s|%s|%s|%s|%s" % (
            tag, resolved_root, tank_level, slots, switches)).encode()).hexdigest()

    @app.get("/api/projects/{name}/render/{page}")
    def render_page(name: str, page: str, request: Request,
                    root: int | None = None, tank_level: float = 0.0,
                    slots: str = "", switches: str = ""):
        project = store.load_project(name)
        data, tag = engine.image(name, project)
        # Resolve the page to its root BEFORE the 304 check: an unknown page must 404
        # rather than 304, and the resolved root is what the etag hashes (see
        # _knob_tag). Resolution is cheap; the expensive render_png still sits after
        # the 304 return below.
        r = _page_root(data, page, root, tag)
        slots = resolve_slots(data, project.preview, slots)
        knob_tag = _knob_tag(tag, r, tank_level, slots, switches)
        if request.headers.get("if-none-match") == knob_tag:
            # RFC 7232 4.1: a 304 must repeat the validator a 200 would have sent.
            return Response(status_code=304, headers={"ETag": knob_tag})
        png = engine.render_png(data, r, tank_level,
                                parse_pairs(slots), parse_pairs(switches),
                                tag=knob_tag)
        return Response(content=png, media_type="image/png",
                        headers={"ETag": knob_tag})

    @app.get("/api/projects/{name}/placements/{page}")
    def placements(name: str, page: str, request: Request, root: int | None = None,
                   tank_level: float = 0.0, slots: str = "", switches: str = ""):
        project = store.load_project(name)
        data, tag = engine.image(name, project)
        # Same ordering as render_page: resolve the page to its root BEFORE hashing, and
        # share _knob_tag so the two routes' etags are computed by construction identically
        # for the same inputs -- they can never drift from each other or from the pixels.
        r = _page_root(data, page, root, tag)
        slots = resolve_slots(data, project.preview, slots)
        knob_tag = _knob_tag(tag, r, tank_level, slots, switches)
        if request.headers.get("if-none-match") == knob_tag:
            return Response(status_code=304, headers={"ETag": knob_tag})
        body = engine.placements(data, r, parse_pairs(slots), parse_pairs(switches),
                                 tag=knob_tag)
        body.update(etag=knob_tag, page=page, root=r)
        return JSONResponse(content=body, headers={"ETag": knob_tag})

    @app.post("/api/projects/{name}/preview/import")
    def import_live(name: str, body: dict = Body(...)):
        """Propose a coach config decoded from a candump capture. Writes nothing.

        Optional by construction: it takes TEXT, so a capture can come from any bridge,
        adapter or forwarded log, and Studio never needs to know which. A capture with no
        0x15FCE status frames -- the wrong log, another bus, no bridge at all -- is a
        plain message and an empty diff, never an error: the picker simply stays as it is.
        """
        from fireflymcf import livecfg

        project = store.load_project(name)
        live = livecfg.decode_capture(body.get("capture") or "")
        if not live.frames:
            return {"preview": {}, "diff": [], "frames": 0, "source": None,
                    "message": "no 0x15FCE configuration-status frames in that capture "
                               "-- nothing to import, the coach config is unchanged"}

        proposed = livecfg.to_preview(stock, live)
        current = project.preview or {}
        diff = []
        for field in ("floorplan", "options", "extra_slots"):
            if field not in proposed:
                continue
            was, now = current.get(field), proposed[field]
            if isinstance(was, list) or isinstance(now, list):
                same = sorted(was or []) == sorted(now or [])
            else:
                same = (was or None) == (now or None)
            if not same:
                diff.append({"field": field, "from": was, "to": now})
        return {"preview": proposed, "diff": diff, "frames": live.frames,
                "source": "%02X" % live.source if live.source is not None else None,
                "message": ("nothing to change -- the project already matches the coach"
                            if not diff else
                            "%d change(s) from the coach" % len(diff))}

    @app.post("/api/projects/{name}/export")
    def export(name: str):
        project = store.load_project(name)
        data, tag = engine.image(name, project)     # 409 via EngineError if not ok
        result, _ = engine.compile(name, project)
        filename = _export_filename(name)            # name already store-validated
        os.makedirs(build_root, exist_ok=True)
        path = os.path.join(build_root, filename)
        sha = hashlib.sha256(data).hexdigest()
        _atomic_write(build_root, path, data)
        os.chmod(path, 0o644)   # written to be copied to a USB stick, not kept 0600
        # The sidecar is what makes a served file self-describing: it records the
        # etag this export was compiled from, so a later GET can tell a stale file
        # (the project changed since) from a current one without re-compiling. It
        # is written right after the primary file, both via the same atomic
        # primitive, so a reader never sees a half-written sidecar either.
        sidecar = {"etag": tag, "sha256": sha, "size": len(data),
                   "build_name": project.build_name}
        sidecar_path = path + ".json"
        _atomic_write(build_root, sidecar_path, json.dumps(sidecar).encode())
        os.chmod(sidecar_path, 0o644)
        return {"filename": filename, "path": path, "size": len(data),
                "sha256": sha, "manifest": result.manifest,
                "unverified": result.unverified, "etag": tag,
                "download": "/api/projects/%s/export/file" % name}

    @app.get("/api/projects/{name}/export/file")
    def export_file(name: str):
        # store.load_project (not store.load) because staleness needs the live
        # project to compute the CURRENT etag below; it 404s an unknown name the
        # same way store.load did.
        project = store.load_project(name)
        path = os.path.join(build_root, _export_filename(name))
        if not os.path.exists(path):
            raise HTTPException(status_code=404,
                                detail={"code": "NOT_EXPORTED", "name": name})
        recorded_etag = None
        sidecar_path = path + ".json"
        if os.path.exists(sidecar_path):
            try:
                with open(sidecar_path) as f:
                    recorded_etag = json.load(f).get("etag")
            except (OSError, ValueError):
                # Corrupt sidecar (truncated write, manual edit, a future format
                # change) -- json.JSONDecodeError is a ValueError subclass. Degrade
                # exactly like the missing-sidecar case below: recorded_etag stays
                # None, which the comparison below reports as stale. Never let a
                # bad sidecar 500 a route whose whole job is to keep serving the
                # last good build.
                recorded_etag = None
        current_etag = engine.etag(project)
        # A missing/unreadable/corrupt sidecar can't prove freshness, so it counts
        # as stale rather than silently reporting "false" -- see FINDING 2: staleness
        # must be visible, never a false negative.
        stale = recorded_etag != current_etag
        headers = {"X-Firefly-Stale": "true" if stale else "false"}
        if recorded_etag is not None:
            headers["ETag"] = recorded_etag
        return FileResponse(path, media_type="application/octet-stream",
                            filename=_export_filename(name), headers=headers)

    _ref_cache: dict = {}

    def _ref(key, fn):
        if key not in _ref_cache:
            _ref_cache[key] = fn(stock)
        return _ref_cache[key]

    @app.get("/api/reference/pins")
    def ref_pins():
        return _ref("pins", reference.pins)

    @app.get("/api/reference/colors")
    def ref_colors():
        return _ref("colors", reference.colors)

    @app.get("/api/reference/channels")
    def ref_channels():
        return _ref("channels", reference.channels)

    @app.get("/api/reference/fonts")
    def ref_fonts():
        return _ref("fonts", reference.fonts)

    @app.get("/api/reference/fonts/{slot}/specimen")
    def ref_font_specimen(slot: int):
        # Cached by slot like the other four reference routes -- computed once, not
        # re-run (find_glyph_table + descriptor scan + decode) on every request. An
        # unknown slot's 404 is NOT cached: only a successful render occupies a key.
        cache_key = ("specimen", slot)
        if cache_key not in _ref_cache:
            try:
                _ref_cache[cache_key] = reference.font_specimen(stock, slot)
            except KeyError:
                raise HTTPException(status_code=404,
                                    detail={"code": "NO_SUCH_FONT", "slot": slot})
        return Response(content=_ref_cache[cache_key], media_type="image/png")

    @app.get("/api/reference/glyph")
    def ref_glyph(slot: int, char: str):
        # Query params, not a path segment: `char` is arbitrary and includes '/' and '.'
        # in some icon fonts, which a path would swallow or reroute.
        cache_key = ("glyph", slot, char)
        if cache_key not in _ref_cache:
            try:
                _ref_cache[cache_key] = reference.glyph(stock, slot, char)
            except KeyError:
                raise HTTPException(status_code=404,
                                    detail={"code": "NO_SUCH_GLYPH", "slot": slot,
                                            "char": char})
        return _ref_cache[cache_key]

    @app.get("/api/reference/config")
    def reference_config():
        return _ref("config", reference.config)

    @app.get("/api/reference/pages")
    def ref_pages():
        return _ref("pages", reference.pages)

    # Serve the built web bundle when it exists, so an end user needs only Python.
    # Mounted last so it can never shadow an /api route. In dev the Vite server
    # proxies /api here instead and this mount is simply absent.
    web_dist = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "dist")
    if os.path.isdir(web_dist):
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")

    return app
