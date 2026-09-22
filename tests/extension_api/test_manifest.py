# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify data-only manifests and their exported public wire schema."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.validation import require_compatible_api, validate_manifest
from baqylau_extension_api.models.documents import EncodedDocument, SchemaIdentity
from baqylau_extension_api.schema_documents import export_schema
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.versions import API_VERSION
from pydantic import TypeAdapter, ValidationError

from tests.extension_api import full_manifest_sample, manifest_samples


@pytest.mark.parametrize("manifest", [
    manifest_samples.backend_manifest(), manifest_samples.web_manifest(), full_manifest_sample.full_manifest(),
])
def test_manifest_round_trip(manifest: ExtensionManifest) -> None:
    """Support backend-only, web-only, and combined packages without imports."""
    assert validate_manifest(ExtensionManifest.model_validate_json(manifest.model_dump_json())) == manifest
    require_compatible_api(manifest)


def test_manifest_schema_checks_full_example() -> None:
    """Export registration schema from the same typed model used by the host."""
    schema = export_schema(
        TypeAdapter(ExtensionManifest), SchemaIdentity(owner="baqylau", name="manifest", version=1),
    )
    SchemaSet((schema,)).validate(EncodedDocument(
        schema_ref=schema.reference, json_text=full_manifest_sample.full_manifest().model_dump_json(),
    ))


@pytest.mark.parametrize("required", [">=1,<2", "==0.0.1", ">=0.1,<0.2"])
def test_incompatible_api_is_rejected(required: str) -> None:
    """Do not import a package with an incompatible API or excluded prerelease."""
    manifest = manifest_samples.backend_manifest().model_copy(update={"api_requires": required})
    with pytest.raises(ExtensionContractError, match="host API"):
        require_compatible_api(manifest)


def test_package_and_api_versions_are_separate() -> None:
    """Do not use package release or private storage versions as API versions."""
    manifest = manifest_samples.backend_manifest().model_copy(update={"package_version": "19.0.0"})
    require_compatible_api(manifest, API_VERSION)
    assert validate_manifest(manifest).package_version == "19.0.0"


@pytest.mark.parametrize("field", ["e2e", "capabilities", "manifest_version"])
def test_manifest_rejects_invalid_required_fields(field: str) -> None:
    """Require E2E declarations and reject unknown required protocol features."""
    invalid: dict[str, object] = {"e2e": (), "capabilities": ("unknown",), "manifest_version": 99}
    manifest = manifest_samples.backend_manifest().model_copy(update={field: invalid[field]})
    with pytest.raises(ValidationError):
        validate_manifest(manifest)


@pytest.mark.parametrize("requirement", ["", " ", "^1.0", "not a range"])
def test_manifest_rejects_invalid_api_range(requirement: str) -> None:
    """Use standard PEP 440 ranges, not a second version parser."""
    manifest = manifest_samples.backend_manifest().model_copy(update={"api_requires": requirement})
    with pytest.raises(ValidationError):
        validate_manifest(manifest)
