# api/common/content.py — canonical content resolution, shared by the SPA
# (full command output, diffs) and the terminal click handler
# (bin/baqylau-content.py).
from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse, PlainTextResponse

from api.dependencies import ApplicationGraph
from dashboard.render.diff import source_html, unified_diff_html
from domain.errors import MalformedRequest

router = APIRouter()


@router.get("/api/content/{content_reference:path}")
def content(
    content_reference: str,
    application: ApplicationGraph,
    view: str | None = None,
    path: str | None = None,
) -> Response:
    text = application.content.resolve(content_reference)
    if view in ("diff", "source"):
        if not path:
            raise MalformedRequest("path is required for file view")
        rendered = unified_diff_html(text, path) if view == "diff" else source_html(text, path)
        return HTMLResponse(rendered)
    return PlainTextResponse(text)
