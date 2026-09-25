# Copyright (c) 2026 Zhambyl Yermagambet
"""List the active web views and serve their declared assets from retained package copies."""

from http import HTTPStatus
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response

from api.extensions import web_models
from api.extensions.scope_documents import OptionalScopeQuery, request_scope_or_installation
from app.provider_extension_artifacts import Artifacts
from app.provider_extension_registry import Registry
from app.provider_scope_relations import Relations
from extensions.web_assets import AssetNotFoundError, WebAssets
from extensions.web_contributions import ActiveWebView, active_web_views

router = APIRouter()
ASSET_ROUTE = "/extensions"
CACHE_CONTROL = "Cache-Control"
IMMUTABLE = "public, max-age=31536000, immutable"


def web_assets(artifacts: Artifacts) -> WebAssets:
    """Serve declared assets from the retained package copies.

    Returns:
        The asset reader.

    """
    return WebAssets(artifacts)


@router.get("/api/extension-web/views")
def extension_web_views(registry: Registry, response: Response) -> web_models.WebViewsResponse:
    """List the web views of the enabled packages in the published runtime.

    Returns:
        The views in slot, order, owner, and view order.

    """
    response.headers[CACHE_CONTROL] = "no-store"
    with registry.read_snapshot() as read:
        snapshot = read.snapshot
    views = tuple(_view(active) for active in active_web_views(snapshot))
    return web_models.WebViewsResponse(runtime_revision=snapshot.directory.runtime_revision, views=views)


@router.get("/api/extension-web/views/{extension_id}/settings")
def extension_view_settings(
    extension_id: str, registry: Registry, response: Response, scope: OptionalScopeQuery = None,
) -> web_models.ViewSettingsResponse:
    """Read the settings that the published runtime resolves for one package and scope.

    Returns:
        The owner settings revision and the effective document, if any.

    Raises:
        HTTPException: If the package is not enabled in the published runtime.

    """
    response.headers[CACHE_CONTROL] = "no-store"
    selected = request_scope_or_installation(scope)
    with registry.read_snapshot() as read:
        package = next((
            entry for entry in read.snapshot.packages
            if entry.manifest.extension_id == extension_id and entry.entry.state == "enabled"
        ), None)
        if package is None:
            raise HTTPException(HTTPStatus.NOT_FOUND, "the extension is not enabled")
        settings = package.resolved_settings
        return web_models.ViewSettingsResponse(
            settings_revision=settings.revision, settings=settings.for_scope(selected),
        )


@router.get("/api/extension-web/related-scopes")
def extension_related_scopes(
    relations: Relations, response: Response, scope: OptionalScopeQuery = None,
) -> web_models.RelatedScopesResponse:
    """Name the scopes that one scope relates to, such as the workspace of a session.

    Returns:
        The related scopes, most specific first.

    """
    response.headers[CACHE_CONTROL] = "no-store"
    return web_models.RelatedScopesResponse(scopes=relations.related_scopes(request_scope_or_installation(scope)))


@router.get(f"{ASSET_ROUTE}/{{extension_id}}/{{package_digest}}/{{asset_path:path}}")
def extension_web_asset(
    extension_id: str, package_digest: str, asset_path: str, assets: Annotated[WebAssets, Depends(web_assets)],
) -> Response:
    """Serve one declared asset of one exact package; the digest URL never changes content.

    Returns:
        The asset bytes with their declared media type.

    Raises:
        HTTPException: If the package does not declare the asset or its bytes do not match.

    """
    try:
        asset = assets.read_asset(extension_id, package_digest, asset_path)
    except AssetNotFoundError as error:
        raise HTTPException(HTTPStatus.NOT_FOUND, str(error)) from error
    served = Response(asset.content, media_type=asset.media_type)
    served.headers[CACHE_CONTROL] = IMMUTABLE
    return served


def _view(active: ActiveWebView) -> web_models.WebViewResponse:
    view = active.view
    styles = tuple(_asset_url(active, style) for style in view.styles)
    return web_models.WebViewResponse(
        extension_id=active.extension_id,
        package_digest=active.package_digest,
        view_id=view.view_id,
        title=view.title,
        slot=view.slot,
        scopes=view.scopes,
        mode=view.mode,
        target=view.target,
        order=view.order,
        module_url=_asset_url(active, view.module),
        style_urls=styles,
    )


def _asset_url(active: ActiveWebView, path: str) -> str:
    return f"{ASSET_ROUTE}/{quote(active.extension_id)}/{active.package_digest}/{quote(path)}"
