# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve only declared secret names, check the reply name, and clear a missing value safely."""

from dataclasses import dataclass

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.settings import SecretSetting, SettingsDefinition
from baqylau_extension_api.models.credentials import SecretAvailable, SecretMissing, SecretRequest
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.runtime.credential_access import RemoteCredentialService

from extensions.impl.keychain_secrets import KeychainSecretStore
from extensions.secret_access import HostCredentialService
from tests.extension_api import manifest_samples, service_samples

OWNER = service_samples.ALPHA
NAME = "token"
STORED_TEXT = "stored-value"


MANIFEST = manifest_samples.backend_manifest(OWNER).model_copy(update={
    "schemas": (service_samples.schema(OWNER),),
    "settings": SettingsDefinition(
        defaults=EncodedDocument(schema_ref=service_samples.schema(OWNER).reference, json_text='"ordinary"'),
        scopes=("installation",), secret_references=(SecretSetting(name=NAME),),
    ),
})


@dataclass(frozen=True)
class RenamingCaller:
    """Reply for another secret name than the one requested."""

    def invoke_typed(self, _method: str, _request: object, _adapter: object) -> SecretMissing:
        """Return a reply for another name.

        Returns:
            A missing reply for another name.

        """
        return SecretMissing(name="other")


def test_host_resolves_only_declared_names() -> None:
    """A declared name reads the store; an undeclared name is refused before any store read."""
    store = KeychainSecretStore(service_prefix="baqylau.test.access")
    store.write_secret(OWNER, NAME, STORED_TEXT)
    service = HostCredentialService(MANIFEST, store)

    expected = SecretAvailable(name=NAME, secret=STORED_TEXT)
    assert service.resolve_secret(SecretRequest(name=NAME)) == expected
    with pytest.raises(ExtensionContractError, match="does not declare"):
        service.resolve_secret(SecretRequest(name="other"))


def test_cleared_value_is_missing() -> None:
    """Clearing a missing value is not an error, and the host then reports the name missing."""
    store = KeychainSecretStore(service_prefix="baqylau.test.clear")
    store.delete_secret(OWNER, NAME)
    service = HostCredentialService(MANIFEST, store)

    assert service.resolve_secret(SecretRequest(name=NAME)) == SecretMissing(name=NAME)


def test_worker_rejects_a_reply_for_another_name() -> None:
    """The worker proxy refuses a host reply that names another secret."""
    proxy = RemoteCredentialService(RenamingCaller())  # type: ignore[arg-type]

    with pytest.raises(ExtensionContractError, match="changed its requested name"):
        proxy.resolve_secret(SecretRequest(name=NAME))
