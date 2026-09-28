# Copyright (c) 2026 Zhambyl Yermagambet
"""Report, store, and clear declared extension secret values through the public API."""

from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import quote

from pydantic import TypeAdapter

from api.extensions.secret_models import ExtensionSecretsResponse, SecretWriteRequest
from sdk.transport import HttpTransport

SECRETS = TypeAdapter(ExtensionSecretsResponse)


@dataclass(frozen=True)
class ExtensionSecretsResource:
    """Write secret values; every read returns only whether a value is stored."""

    transport: HttpTransport

    def states(self, extension_id: str) -> ExtensionSecretsResponse:
        """Read each declared reference and whether it has a value.

        Returns:
            The reference states.

        """
        return self.transport.get(f"/api/extensions/{quote(extension_id, safe='')}/secrets", SECRETS)

    def store(self, extension_id: str, name: str, secret: str) -> ExtensionSecretsResponse:
        """Store or replace one declared value.

        Returns:
            The reference states after the change.

        """
        _, response = self.transport.put(
            _secret_path(extension_id, name), SecretWriteRequest(secret=secret), SECRETS, {HTTPStatus.OK},
        )
        return response

    def clear(self, extension_id: str, name: str) -> ExtensionSecretsResponse:
        """Clear one declared value.

        Returns:
            The reference states after the change.

        """
        path = _secret_path(extension_id, name)
        _, response = self.transport.delete(path, SECRETS, {HTTPStatus.OK})
        return response


def _secret_path(extension_id: str, name: str) -> str:
    return f"/api/extensions/{quote(extension_id, safe='')}/secrets/{quote(name, safe='')}"
