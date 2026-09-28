# Copyright (c) 2026 Zhambyl Yermagambet
"""Call the host session list from a worker, and register it on the host channel."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.session_lists import ExtensionSessionDirectory
from baqylau_extension_api.models.session_lists import RepositorySessionsReply, RepositorySessionsRequest
from baqylau_extension_api.runtime import channel, codec, methods
from baqylau_extension_api.runtime.contract import RemoteCaller

SESSIONS_REPLY: TypeAdapter[RepositorySessionsReply] = TypeAdapter(RepositorySessionsReply)


@dataclass(frozen=True)
class RemoteSessionDirectory(ExtensionSessionDirectory):
    """List sessions only in the live lane."""

    caller: RemoteCaller

    def repository_sessions(self, sessions_request: RepositorySessionsRequest) -> RepositorySessionsReply:
        """List the repository's sessions through the host.

        Returns:
            The sessions.

        """
        return self.caller.invoke_typed(methods.REPOSITORY_SESSIONS, sessions_request, SESSIONS_REPLY)


def register_session_access(rpc: channel.RpcChannel, sessions: ExtensionSessionDirectory) -> None:
    """Register the host session list callback bound to one worker connection."""
    session_handler = codec.ModelHandler(RepositorySessionsRequest, SESSIONS_REPLY, sessions.repository_sessions)
    rpc.register(methods.REPOSITORY_SESSIONS, session_handler, "live")
