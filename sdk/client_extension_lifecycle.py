# Copyright (c) 2026 Zhambyl Yermagambet
"""Use checked extension management requests through the public daemon API."""

from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import quote

from pydantic import TypeAdapter

from api.extensions.lifecycle_models import (
    ExtensionOperationResponse,
    ExtensionRuntimeResponse,
    LifecycleAdmissionResponse,
    LifecyclePlanResponse,
)
from api.extensions.lifecycle_requests import LifecycleChangeRequest, LifecyclePreviewRequest
from sdk.transport import HttpTransport


@dataclass(frozen=True)
class ExtensionLifecycleResource:
    """Keep runtime controls separate from data-only catalog discovery."""

    transport: HttpTransport

    def state(self) -> ExtensionRuntimeResponse:
        """Read active metadata, stored intent, and write admission policy.

        Returns:
            The current extension manager report with no private settings values.

        """
        return self.transport.get("/api/extensions/state", TypeAdapter(ExtensionRuntimeResponse))

    def operation(self, operation_id: str) -> ExtensionOperationResponse:
        """Read the retained result for one admitted operation.

        Returns:
            Pending or final state, including preparation failure.

        """
        selected = quote(operation_id, safe="")
        return self.transport.get(f"/api/extensions/operations/{selected}", TypeAdapter(ExtensionOperationResponse))

    def preview(
        self, extension_id: str, lifecycle_preview_request: LifecyclePreviewRequest,
    ) -> LifecyclePlanResponse:
        """Validate a proposed change before confirming dependent removal.

        Returns:
            Exact affected owner IDs, without accepting the change.

        """
        selected = quote(extension_id, safe="")
        _, reply = self.transport.post(
            f"/api/extensions/{selected}/lifecycle/preview", lifecycle_preview_request,
            TypeAdapter(LifecyclePlanResponse), {HTTPStatus.OK},
        )
        return reply

    def change(self, extension_id: str, lifecycle_change_request: LifecycleChangeRequest) -> LifecycleAdmissionResponse:
        """Admit a checked user request and keep its request ID for exact retry.

        Returns:
            The accepted or replayed operation; callers must read its final outcome.

        """
        selected = quote(extension_id, safe="")
        _, reply = self.transport.post(
            f"/api/extensions/{selected}/lifecycle", lifecycle_change_request, TypeAdapter(LifecycleAdmissionResponse),
            {HTTPStatus.ACCEPTED},
        )
        return reply


def preview_request(lifecycle_change_request: LifecycleChangeRequest) -> LifecyclePreviewRequest:
    """Select only preview fields from a complete client request.

    Returns:
        A checked request with no retry or dependent confirmation fields.

    """
    return LifecyclePreviewRequest.model_validate(lifecycle_change_request, from_attributes=True)
