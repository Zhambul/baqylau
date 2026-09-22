# Copyright (c) 2026 Zhambyl Yermagambet
"""Read typed durable extension jobs."""

from http import HTTPStatus
from urllib.parse import urlencode

from baqylau_extension_api.models.scopes import ExtensionScope
from pydantic import TypeAdapter

from api.extensions.job_models import (
    ExtensionJobCancelRequest,
    ExtensionJobCancelResponse,
    ExtensionJobReconcileRequest,
    ExtensionJobResponse,
)
from sdk.transport import HttpTransport

JOB_RESPONSE = TypeAdapter(ExtensionJobResponse)
CANCEL_RESPONSE = TypeAdapter(ExtensionJobCancelResponse)


class ExtensionJobsResource:
    """Read one durable job."""

    def __init__(self, transport_handle: HttpTransport) -> None:
        """Store the transport handle."""
        self.transport = transport_handle

    def read(self, extension_id: str, job_id: str, scope: ExtensionScope) -> ExtensionJobResponse:
        """Read one stored job.

        Returns:
            The typed job response.

        """
        query = urlencode((("scope", scope.model_dump_json()),))
        return self.transport.get(f"/api/extensions/{extension_id}/jobs/{job_id}?{query}", JOB_RESPONSE)

    def cancel(
        self,
        extension_id: str,
        job_id: str,
        request: ExtensionJobCancelRequest,
    ) -> ExtensionJobCancelResponse:
        """Request cancellation of one stored job attempt.

        Returns:
            The checked cancellation request state.

        """
        _, response = self.transport.post(
            f"/api/extensions/{extension_id}/jobs/{job_id}/cancel",
            request,
            CANCEL_RESPONSE,
            {HTTPStatus.OK},
        )
        return response

    def reconcile(
        self,
        extension_id: str,
        job_id: str,
        request: ExtensionJobReconcileRequest,
    ) -> ExtensionJobResponse:
        """Inspect an uncertain job result without repeating the command.

        Returns:
            The stored job after the reconciled outcome.

        """
        _, response = self.transport.post(
            f"/api/extensions/{extension_id}/jobs/{job_id}/reconcile",
            request,
            JOB_RESPONSE,
            {HTTPStatus.OK},
        )
        return response
