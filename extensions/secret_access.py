# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve one worker's declared secret references from the host secret store."""

from dataclasses import dataclass

from baqylau_extension_api.contracts.credentials import ExtensionCredentialService
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.credentials import SecretAvailable, SecretMissing, SecretRequest, SecretResult

from extensions.secret_store_contract import SecretStore


@dataclass(frozen=True)
class HostCredentialService(ExtensionCredentialService):
    """Bind the secret store to one worker's manifest, so a worker reads only its own declared names."""

    manifest: ExtensionManifest
    store: SecretStore

    def resolve_secret(self, secret_request: SecretRequest) -> SecretResult:
        """Read one declared secret of this package.

        Returns:
            The value, or the missing state.

        Raises:
            ExtensionContractError: If the manifest does not declare the name.

        """
        checked = SecretRequest.model_validate(secret_request)
        if checked.name not in declared_secrets(self.manifest):
            message = "the package does not declare this secret reference"
            raise ExtensionContractError(message)
        secret = self.store.read_secret(self.manifest.extension_id, checked.name)
        if secret is None:
            return SecretMissing(name=checked.name)
        return SecretAvailable(name=checked.name, secret=secret)


def declared_secrets(manifest: ExtensionManifest) -> tuple[str, ...]:
    """Name the secret references that a manifest declares.

    Returns:
        The declared names, in manifest order.

    """
    settings = manifest.settings
    return () if settings is None else tuple(reference.name for reference in settings.secret_references)
