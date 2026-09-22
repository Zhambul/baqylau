# Copyright (c) 2026 Zhambyl Yermagambet
"""Submit typed durable extension commands."""

from http import HTTPStatus

from pydantic import TypeAdapter

from api.extensions.command_models import ExtensionCommandRequest
from api.extensions.job_models import ExtensionJobResponse
from sdk.transport import HttpTransport

JOB_RESPONSE = TypeAdapter(ExtensionJobResponse)


class ExtensionCommandsResource:
    """Submit one durable command and read its stored job."""

    def __init__(self, transport_handle: HttpTransport) -> None:
        """Store the transport handle."""
        self.transport = transport_handle

    def submit(
        self,
        extension_id: str,
        command_id: str,
        request: ExtensionCommandRequest,
    ) -> ExtensionJobResponse:
        """Submit one command.

        Returns:
            The stored job after its final state.

        """
        _, response = self.transport.post(
            f"/api/extensions/{extension_id}/commands/{command_id}",
            request,
            JOB_RESPONSE,
            {HTTPStatus.OK},
        )
        return response
