# Copyright (c) 2026 Zhambyl Yermagambet
"""Store extension secret values in the macOS Keychain through the keyring library."""

from dataclasses import dataclass

import keyring
from keyring.errors import PasswordDeleteError

from extensions.secret_store_contract import SecretStore

SERVICE_PREFIX = "baqylau.extension"


@dataclass(frozen=True)
class KeychainSecretStore(SecretStore):
    """Use one Keychain service per owner and one account per reference name."""

    service_prefix: str = SERVICE_PREFIX

    def read_secret(self, owner: str, name: str) -> str | None:
        """Read one stored value.

        Returns:
            The value, or None when the user has stored none.

        """
        return keyring.get_password(self._service(owner), name)

    def write_secret(self, owner: str, name: str, secret: str) -> None:
        """Store or replace one value."""
        keyring.set_password(self._service(owner), name, secret)

    def delete_secret(self, owner: str, name: str) -> None:
        """Remove one value; a missing value is not an error."""
        try:
            keyring.delete_password(self._service(owner), name)
        except PasswordDeleteError:
            return

    def _service(self, owner: str) -> str:
        return f"{self.service_prefix}.{owner}"
