# Copyright (c) 2026 Zhambyl Yermagambet
"""Call the host credential route from an extension worker, and register it on the host channel."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.credentials import ExtensionCredentialService
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.credentials import SecretRequest, SecretResult
from baqylau_extension_api.runtime import channel, codec, methods
from baqylau_extension_api.runtime.contract import RemoteCaller

SECRET_RESULT: TypeAdapter[SecretResult] = TypeAdapter(SecretResult)


@dataclass(frozen=True)
class RemoteCredentialService(ExtensionCredentialService):
    """Resolve a secret only in the live lane; pure calls cannot reach the host."""

    caller: RemoteCaller

    def resolve_secret(self, secret_request: SecretRequest) -> SecretResult:
        """Read one declared secret through the host.

        Returns:
            The value, or the missing state.

        Raises:
            ExtensionContractError: If the reply names another secret.

        """
        response = self.caller.invoke_typed(methods.CREDENTIAL_RESOLVE, secret_request, SECRET_RESULT)
        if response.name != secret_request.name:
            message = "secret reply changed its requested name"
            raise ExtensionContractError(message)
        return response


def register_credential_access(rpc: channel.RpcChannel, credentials: ExtensionCredentialService) -> None:
    """Register the host credential callback bound to one authenticated worker connection."""
    secret_handler = codec.ModelHandler(SecretRequest, SECRET_RESULT, credentials.resolve_secret)
    rpc.register(methods.CREDENTIAL_RESOLVE, secret_handler, "live")
