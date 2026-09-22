# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject unsafe asset paths and invalid view declarations before loading."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.validation import validate_manifest
from baqylau_extension_api.paths import RelativePath
from pydantic import TypeAdapter, ValidationError

from tests.extension_api import manifest_samples


@pytest.mark.parametrize("path", [
    "/web/main.js", "../main.js", "web/../main.js", "web//main.js", "./web/main.js", ".",
    "web/", "https://example.test/main.js", r"web\main.js", "web/%2e%2e/main.js", "web/main.js?version=1",
])
def test_package_paths_cannot_escape(path: str) -> None:
    """Require canonical package file paths, not remote URLs or traversal."""
    with pytest.raises(ValidationError):
        TypeAdapter(RelativePath).validate_python(path)


def test_web_view_requires_declared_module() -> None:
    """Do not let a view request an unregistered file from the package."""
    manifest = manifest_samples.web_manifest().model_copy(update={"assets": ()})
    with pytest.raises(ExtensionContractError, match="declared asset"):
        validate_manifest(manifest)


def test_web_view_checks_module_media_type() -> None:
    """Require a JavaScript response type for an imported view module."""
    manifest = manifest_samples.web_manifest()
    asset = manifest.assets[0].model_copy(update={"media_type": "text/plain"})
    with pytest.raises(ExtensionContractError, match="media type"):
        validate_manifest(manifest.model_copy(update={"assets": (asset,)}))


def test_replacement_requires_named_feed_target() -> None:
    """Reject replacements for a whole host route or unnamed target."""
    manifest = manifest_samples.web_manifest()
    view = manifest.contributions.web[0].model_copy(update={"mode": "replace"})
    changed = manifest.contributions.model_copy(update={"web": (view,)})
    with pytest.raises(ExtensionContractError, match="named feed target"):
        validate_manifest(manifest.model_copy(update={"contributions": changed}))


def test_web_only_package_needs_no_worker() -> None:
    """Keep all web source and built files in an external package."""
    manifest = validate_manifest(manifest_samples.web_manifest())
    assert manifest.backend is None and not manifest.capabilities
    assert manifest.contributions.web[0].module == manifest.assets[0].path
