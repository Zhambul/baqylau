# Copyright (c) 2026 Zhambyl Yermagambet
"""Check capability, schema, and namespace agreement before activation."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.validation import validate_manifest
from pydantic import ValidationError

from tests.extension_api import full_manifest_sample, manifest_samples, samples


@pytest.mark.parametrize("field", ["assets", "e2e", "capabilities"])
def test_duplicate_package_ids_are_rejected(field: str) -> None:
    """Do not select duplicated declarations by file order."""
    manifest = full_manifest_sample.full_manifest()
    declared = getattr(manifest, field)
    invalid = manifest.model_copy(update={field: (*declared, declared[0])})
    with pytest.raises(ExtensionContractError, match="unique"):
        validate_manifest(invalid)


def test_capability_requires_its_backend() -> None:
    """Do not construct a worker for a web-only package declaration."""
    manifest = manifest_samples.backend_manifest().model_copy(update={"backend": None})
    with pytest.raises(ExtensionContractError, match="capability"):
        validate_manifest(manifest)


def test_callback_requires_registration() -> None:
    """Do not accept a callback name without its input selection."""
    manifest = manifest_samples.backend_manifest().model_copy(update={"capabilities": ("lifecycle", "raw_transformer")})
    with pytest.raises(ExtensionContractError, match="registered contributions"):
        validate_manifest(manifest)


def test_registrations_require_their_callback() -> None:
    """Reject a schema-defined source when its translator is absent."""
    manifest = full_manifest_sample.full_manifest().model_copy(update={"capabilities": ("lifecycle",)})
    with pytest.raises(ExtensionContractError, match="registered contributions"):
        validate_manifest(manifest)


def test_owned_event_requires_its_namespace() -> None:
    """Do not let a package register another extension's event name."""
    manifest = full_manifest_sample.full_manifest()
    foreign = manifest.contributions.event_types[0].model_copy(update={"name": "peer.event"})
    changed = manifest.contributions.model_copy(update={"event_types": (foreign,)})
    with pytest.raises(ExtensionContractError, match="namespace"):
        validate_manifest(manifest.model_copy(update={"contributions": changed}))


def test_settings_defaults_must_match_schema() -> None:
    """Reject invalid defaults before settings reach the dashboard or worker."""
    manifest = full_manifest_sample.full_manifest()
    assert manifest.settings is not None
    invalid = manifest.settings.model_copy(update={"defaults": samples.encoded_document("123")})
    with pytest.raises(ExtensionContractError, match="string"):
        validate_manifest(manifest.model_copy(update={"settings": invalid}))


def test_write_command_requires_reconciliation() -> None:
    """Make loss of a worker reply an explicit command contract concern."""
    manifest = full_manifest_sample.full_manifest()
    command = manifest.contributions.commands[0].model_copy(update={"reconciliation": False})
    changed = manifest.contributions.model_copy(update={"commands": (command,)})
    with pytest.raises(ValidationError, match="reconciliation"):
        validate_manifest(manifest.model_copy(update={"contributions": changed}))
