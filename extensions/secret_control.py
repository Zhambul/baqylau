# Copyright (c) 2026 Zhambyl Yermagambet
"""Report, store, and clear a package's declared secret values; never return a value."""

from dataclasses import dataclass, field

from baqylau_extension_api.manifest.package import ExtensionManifest

from extensions.control_policy import ExtensionControlPolicy, require_extension_write
from extensions.secret_access import declared_secrets
from extensions.secret_store_contract import SecretStore
from repository.contract.extension_catalog import ExtensionCatalogRepository


class SecretNotDeclaredError(LookupError):
    """Reject a secret name that the discovered package does not declare."""


@dataclass(frozen=True)
class SecretStatus:
    """Tell whether the user stored a value for one declared reference, without the value."""

    name: str
    required: bool
    configured: bool


@dataclass(frozen=True)
class SecretControl:
    """Use the discovered package declaration, the secret store, and the write policy."""

    catalog: ExtensionCatalogRepository
    store: SecretStore
    policy: ExtensionControlPolicy = field(default_factory=ExtensionControlPolicy)

    def statuses(self, extension_id: str) -> tuple[SecretStatus, ...]:
        """Report each declared reference and whether it has a stored value.

        Returns:
            The statuses in manifest order.

        """
        settings = self._manifest(extension_id).settings
        references = () if settings is None else settings.secret_references
        return tuple(SecretStatus(
            name=reference.name, required=reference.required,
            configured=self.store.read_secret(extension_id, reference.name) is not None,
        ) for reference in references)

    def write(self, extension_id: str, name: str, secret: str) -> None:
        """Store or replace one declared secret value."""
        require_extension_write(self.policy)
        self._require_declared(extension_id, name)
        self.store.write_secret(extension_id, name, secret)

    def delete(self, extension_id: str, name: str) -> None:
        """Clear one declared secret value."""
        require_extension_write(self.policy)
        self._require_declared(extension_id, name)
        self.store.delete_secret(extension_id, name)

    def _require_declared(self, extension_id: str, name: str) -> None:
        if name not in declared_secrets(self._manifest(extension_id)):
            message = "the extension does not declare this secret reference"
            raise SecretNotDeclaredError(message)

    def _manifest(self, extension_id: str) -> ExtensionManifest:
        manifests = tuple(
            entry.manifest for entry in self.catalog.read_extension_catalog().entries
            if entry.manifest is not None and entry.issue is None and entry.manifest.extension_id == extension_id
        )
        if len(manifests) != 1:
            message = "the extension needs one valid discovered package"
            raise SecretNotDeclaredError(message)
        return manifests[0]
