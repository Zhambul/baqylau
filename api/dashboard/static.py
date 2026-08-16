# api/dashboard/static.py — the SPA assets: the whitelist server and the
# BOOT_ID cache-busting stamp.
#
# This is policy, not plumbing, so it stays hand-written: no user-path
# resolution ever (a whitelist plus one shape rule), and index.html is
# rewritten on every serve so a restart's new BOOT_ID re-points every
# sub-resource URL at bytes nothing has cached (docs/dashboard.md
# *Cache-busting*).
from __future__ import annotations

import os
import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from api.config import BOOT_ID, CACHE_STATIC
from dashboard.config import STATIC, STATIC_DIR

router = APIRouter()

# The SPA parts (app.NN-name.js) are admitted by SHAPE, not a per-file
# whitelist entry — still no user-path resolution (a strict basename pattern),
# just no dict bloat for the parts.
_APP_PART = re.compile(r"^app\.[0-9]{2}-[a-z-]+\.js$")


def _stamped_index(data: bytes) -> bytes:
    # CACHE-BUST the sub-resource URLs with BOOT_ID (bumped every restart).
    # The origin sends no-store, but that can't evict an already-cached
    # app.js/style.css in a remote browser — so a dashboard update left the
    # phone running old JS forever. index.html itself is served no-store AND
    # is the main document a reload always refetches, so a fresh ?v=<BOOT_ID>
    # reaches the browser and points at a URL nothing has cached.
    data = re.sub(rb"(/static/app\.[0-9]{2}-[a-z-]+\.js)",
                  rb"\1?v=" + BOOT_ID.encode(), data)
    data = data.replace(b"/static/style.css", b"/static/style.css?v=" + BOOT_ID.encode())
    # ...and the ICONS, for the same reason: a REGENERATED icon is new bytes at
    # an unchanged URL, and mobile browsers keep a persistent favicon cache a
    # hard reload does not evict. The manifest URL is stamped too so a changed
    # icon list is re-read.
    data = re.sub(rb"(/static/(?:apple-touch-icon|icon-[a-z0-9-]+)\.png)",
                  rb"\1?v=" + BOOT_ID.encode(), data)
    return data.replace(b"/static/manifest.webmanifest",
                        b"/static/manifest.webmanifest?v=" + BOOT_ID.encode())


def _serve(name: str, version: str) -> Response:
    content_type = STATIC.get(name)
    if not content_type and _APP_PART.match(name):
        content_type = "text/javascript; charset=utf-8"
    if not content_type:
        return JSONResponse({"error": "not found"}, 404)
    try:
        with open(os.path.join(STATIC_DIR, name), "rb") as static_file:
            data = static_file.read()
    except OSError:
        return JSONResponse({"error": "unreadable"}, 500)
    if name == "index.html":
        data = _stamped_index(data)
    if name == "manifest.webmanifest":
        # the manifest's own icon URLs — the installed-app glyph comes from
        # here, not from index.html.
        data = re.sub(rb"(/static/icon-[a-z0-9-]+\.png)",
                      rb"\1?v=" + BOOT_ID.encode(), data)
    # A fetch under the CURRENT boot's ?v=<BOOT_ID> stamp may be cached hard:
    # the URL changes on every restart, and the bytes behind it only change
    # via a restart (the "does NOT hot-reload" contract). index.html and
    # sw.js are fetched un-stamped, so they stay no-store.
    cache = CACHE_STATIC if version == BOOT_ID else "no-store"
    return Response(content=data, media_type=content_type,
                    headers={"Cache-Control": cache})


@router.get("/")
def index(v: str = "") -> Response:
    return _serve("index.html", v)


@router.get("/static/{name}")
def static(name: str, v: str = "") -> Response:
    return _serve(name, v)


@router.get("/sw.js")
def service_worker(v: str = "") -> Response:
    # the push service worker, served at the root so its scope is the whole
    # origin (docs/dashboard.md *Web push*) — not under /static/, which would
    # scope it to /static/.
    return _serve("sw.js", v)


@router.get("/favicon.ico")
def favicon(v: str = "") -> Response:
    # the raster fallback favicon, at the root path clients probe on their own
    # when the declared SVG icon is unusable. Undeclared on purpose — see
    # dashboard/config.py STATIC and docs/dashboard.md *Favicon fallback*.
    return _serve("favicon.ico", v)
