# api/application/static.py — the SPA shell and its content-addressed assets.
#
# This is policy, not plumbing, so it stays hand-written: no user-path
# resolution ever. FastAPI owns the document and reads Vite's manifest to add
# content-addressed CSS and module tags. The unbundled icons and web manifest
# use their own content digests because Vite does not own those files.
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from api.dependencies import Policy
from api.responses import errors
from dashboard.config import STATIC, STATIC_DIR
from dashboard.frontend_build import (
    FrontendBuildError,
    build_asset_path,
    manifest_tags,
)

router = APIRouter(responses=errors({
    404: "Not on the static content-type whitelist.",
    500: "On the whitelist, but unreadable on disk.",
}))

_VITE_MARKER = b"<!-- vite-assets -->"
_BUILD_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
_INDEX_ICON_REFERENCE = re.compile(
    rb"(/static/(?P<name>(?:apple-touch-icon|icon-[a-z0-9-]+)\.png))"
)
_MANIFEST_ICON_REFERENCE = re.compile(
    rb"(/static/(?P<name>icon-[a-z0-9-]+\.png))"
)


def _read_static(name: str) -> bytes:
    try:
        with open(os.path.join(STATIC_DIR, name), "rb") as static_file:
            return static_file.read()
    except OSError as error:
        raise HTTPException(500, "unreadable") from error


def _content_version(data: bytes) -> bytes:
    return hashlib.sha256(data).hexdigest().encode("ascii")


def _versioned_static_reference(match: re.Match[bytes]) -> bytes:
    name = match.group("name").decode("ascii")
    return match.group(1) + b"?v=" + _content_version(_read_static(name))


def _manifest_document(data: bytes) -> bytes:
    # Chrome treats an icon URL change as an application identity change. The
    # URL must therefore change only when the icon bytes change, not each time
    # the daemon starts.
    return _MANIFEST_ICON_REFERENCE.sub(_versioned_static_reference, data)


def _stamped_index(data: bytes) -> bytes:
    # Persistent browser icon caches need a new URL when an icon changes. The
    # manifest version covers both its source and the versioned icon references
    # that the browser receives from it.
    data = _INDEX_ICON_REFERENCE.sub(_versioned_static_reference, data)
    manifest = _manifest_document(_read_static("manifest.webmanifest"))
    return data.replace(
        b"/static/manifest.webmanifest",
        b"/static/manifest.webmanifest?v=" + _content_version(manifest),
    )


def _index_document(data: bytes) -> bytes:
    if data.count(_VITE_MARKER) != 1:
        raise FrontendBuildError("index.html must contain one Vite asset marker")
    return _stamped_index(data.replace(_VITE_MARKER, manifest_tags()))


def _serve(policy: Policy, name: str, version: str) -> Response:
    content_type = STATIC.get(name)
    if not content_type:
        raise HTTPException(404, "not found")
    data = _read_static(name)
    if name == "index.html":
        try:
            data = _index_document(data)
        except FrontendBuildError as error:
            raise HTTPException(500, str(error)) from error
    if name == "manifest.webmanifest":
        data = _manifest_document(data)
    # A fetch under the content's current version may be cached for a year.
    # Unversioned files, index.html, and sw.js stay no-store.
    expected_version = _content_version(data).decode("ascii")
    cache = policy.cache_static if version and version == expected_version else "no-store"
    headers = {"Cache-Control": cache}
    return Response(content=data, media_type=content_type, headers=headers)


def _serve_build(policy: Policy, asset_name: str) -> Response:
    content_type = _BUILD_TYPES.get(Path(asset_name).suffix)
    if content_type is None:
        raise HTTPException(404, "not found")
    try:
        path = build_asset_path(asset_name)
        data = path.read_bytes()
    except FrontendBuildError as error:
        raise HTTPException(404, "not found") from error
    except OSError as error:
        raise HTTPException(500, "unreadable") from error
    headers = {"Cache-Control": policy.cache_static}
    return Response(
        content=data,
        media_type=content_type,
        headers=headers,
    )


@router.get("/")
def index(policy: Policy, v: str = "") -> Response:
    return _serve(policy, "index.html", v)


@router.get("/static/build/{asset_name:path}")
def build_asset(asset_name: str, policy: Policy) -> Response:
    return _serve_build(policy, asset_name)


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
