# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare the backend, schema, views, and assets of a new package with the SDK's manifest models."""

import hashlib
from pathlib import Path

from baqylau_extension_api.manifest.contributions import Contributions
from baqylau_extension_api.manifest.metadata import BackendEntry, BackendEnvironment, PackageAsset
from baqylau_extension_api.manifest.operations import QueryDefinition
from baqylau_extension_api.manifest.views import TerminalView, WebView
from baqylau_extension_api.models.documents import SchemaDefinition, SchemaRef

from baqylau_dev.template_parts import WEB_MODULE


def backend(module: str) -> BackendEntry:
    """Declare the backend module and its locked offline environment.

    Returns:
        The backend entry.

    """
    return BackendEntry(
        module=f"{module}.backend",
        environment=BackendEnvironment(requirements="requirements.lock", wheelhouse="wheels"),
    )


def text_schema(owner: str, text: str) -> SchemaDefinition:
    """Declare the package's text schema by the digest of its bytes.

    Returns:
        The schema.

    """
    digest = hashlib.sha256(text.encode()).hexdigest()
    reference = SchemaRef(owner=owner, name="text", version=1, digest=digest)
    return SchemaDefinition(reference=reference, json_text=text)


def contributions(owner: str, *, web: bool, terminal: bool) -> Contributions:
    """Declare the greeting query and the chosen views.

    Returns:
        The contributions.

    """
    schema = text_schema(owner, '{"type":"string"}').reference
    greeting = QueryDefinition(
        name=f"{owner}.greeting", scopes=("installation",), arguments=schema, result=schema,
    )
    pages = _pages(owner, web=web)
    statuses = _statuses(owner, terminal=terminal)
    return Contributions(queries=(greeting,), web=pages, terminal=statuses)


def _pages(owner: str, *, web: bool) -> tuple[WebView, ...]:
    if not web:
        return ()
    return (WebView(
        view_id=f"{owner}.page", title="Page", slot="workspace_page", scopes=("workspace",), module=WEB_MODULE,
    ),)


def _statuses(owner: str, *, terminal: bool) -> tuple[TerminalView, ...]:
    if not terminal:
        return ()
    return (TerminalView(view_id=f"{owner}.status", title="Status", scopes=("installation",), pane="status"),)


def assets(directory: Path) -> tuple[PackageAsset, ...]:
    """Declare the web module by the digest of its bytes.

    Returns:
        The assets.

    """
    digest = hashlib.sha256((directory / WEB_MODULE).read_bytes()).hexdigest()
    return (PackageAsset(path=WEB_MODULE, digest=digest, media_type="text/javascript"),)
