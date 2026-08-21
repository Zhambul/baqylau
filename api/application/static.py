# api/application/static.py — the SPA assets: the whitelist server and the
# BOOT_ID cache-busting stamp.
#
# This is policy, not plumbing, so it stays hand-written: no user-path
# resolution ever (a whitelist plus one shape rule), and index.html is
# rewritten on every serve so a restart's new BOOT_ID re-points every
# sub-resource URL at bytes nothing has cached — which is the whole of the
# cache-busting story, and why `v` is accepted and ignored below.
from __future__ import annotations

import os
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from api.dependencies import Policy
from api.responses import errors
from dashboard.config import STATIC, STATIC_DIR

router = APIRouter(responses=errors({
    404: "Not on the content-type whitelist, and not shaped like an app part.",
    500: "On the whitelist, but unreadable on disk.",
}))

# The SPA parts (app.NN-name.js) are admitted by SHAPE, not a per-file
# whitelist entry — still no user-path resolution (a strict basename pattern),
# just no dict bloat for the parts. The slot number takes an optional LETTER
# (app.00a-markup.js): load order IS the file name, so a part inserted between
# two existing ones needs a letter rather than a renumbering of every part
# after it.
_PART_NAME = r"app\.[0-9]{2}[a-z]?-[a-z-]+\.js"
_APP_PART = re.compile(rf"^{_PART_NAME}$")


def _stamped_index(data: bytes, boot_id: bytes) -> bytes:
    # CACHE-BUST the sub-resource URLs with BOOT_ID (bumped every restart).
    # The origin sends no-store, but that can't evict an already-cached
    # app.js/style.css in a remote browser — so a dashboard update left the
    # phone running old JS forever. index.html itself is served no-store AND
    # is the main document a reload always refetches, so a fresh ?v=<BOOT_ID>
    # reaches the browser and points at a URL nothing has cached.
    data = re.sub(rb"(/static/" + _PART_NAME.encode() + rb")",
                  rb"\1?v=" + boot_id, data)
    data = data.replace(b"/static/style.css", b"/static/style.css?v=" + boot_id)
    # ...and the ICONS, for the same reason: a REGENERATED icon is new bytes at
    # an unchanged URL, and mobile browsers keep a persistent favicon cache a
    # hard reload does not evict. The manifest URL is stamped too so a changed
    # icon list is re-read.
    data = re.sub(rb"(/static/(?:apple-touch-icon|icon-[a-z0-9-]+)\.png)",
                  rb"\1?v=" + boot_id, data)
    return data.replace(b"/static/manifest.webmanifest",
                        b"/static/manifest.webmanifest?v=" + boot_id)


def _serve(policy: Policy, name: str, version: str) -> Response:
    content_type = STATIC.get(name)
    if not content_type and _APP_PART.match(name):
        content_type = "text/javascript; charset=utf-8"
    if not content_type:
        raise HTTPException(404, "not found")
    try:
        with open(os.path.join(STATIC_DIR, name), "rb") as static_file:
            data = static_file.read()
    except OSError as error:
        raise HTTPException(500, "unreadable") from error
    if name == "index.html":
        data = _stamped_index(data, policy.boot_id.encode())
    if name == "manifest.webmanifest":
        # the manifest's own icon URLs — the installed-app glyph comes from
        # here, not from index.html.
        data = re.sub(rb"(/static/icon-[a-z0-9-]+\.png)",
                      rb"\1?v=" + policy.boot_id.encode(), data)
    # A fetch under the CURRENT boot's ?v=<BOOT_ID> stamp may be cached hard:
    # the URL changes on every restart, and the bytes behind it only change
    # via a restart (the "does NOT hot-reload" contract). index.html and
    # sw.js are fetched un-stamped, so they stay no-store.
    cache = policy.cache_static if version == policy.boot_id else "no-store"
    return Response(content=data, media_type=content_type,
                    headers={"Cache-Control": cache})


@router.get("/")
def index(policy: Policy, v: str = "") -> Response:
    return _serve(policy, "index.html", v)


@router.get("/static/{name}")
def static(name: str, policy: Policy, v: str = "") -> Response:
    return _serve(policy, name, v)


@router.get("/sw.js")
def service_worker(policy: Policy, v: str = "") -> Response:
    # the push service worker, served at the root so its scope is the whole
    # origin — not under /static/, which would scope it to /static/ and leave
    # every page outside that prefix unreachable by a push notification.
    return _serve(policy, "sw.js", v)


@router.get("/favicon.ico")
def favicon(policy: Policy, v: str = "") -> Response:
    # the raster fallback favicon, at the root path clients probe on their own
    # when the declared SVG icon is unusable. Undeclared on purpose — see
    # dashboard/config.py STATIC for the whitelist this is deliberately not in.
    return _serve(policy, "favicon.ico", v)
