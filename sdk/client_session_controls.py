# Copyright (c) 2026 Zhambyl Yermagambet
"""Split SDK client implementation."""

from __future__ import annotations

import uuid
from http import HTTPStatus
from typing import TYPE_CHECKING

from sdk import control_models

if TYPE_CHECKING:
    from collections.abc import Callable

from sdk.client_adapters import (
    AUTOMATIC_NAME_TIMEOUT_SECONDS,
    CONTROL,
    CONTROL_TIMEOUT_SECONDS,
)
from sdk.client_models import (
    ActionReceipt,
    SessionRef,
)
from sdk.client_session_snapshots import _SessionsPromptOwners


class _SessionsControlTransport(_SessionsPromptOwners):
    """Send one typed control request."""

    def _control(
        self,
        session: SessionRef,
        control_name: str,
        build_request: Callable[[str], control_models.control_request.ControlRequestBody],
        *,
        timeout: float | None = None,
    ) -> ActionReceipt:
        cursor = self.snapshot(session).entry_cursor
        request_id = f"e2e-{control_name}-{uuid.uuid4()}"
        path = f"/api/sessions/{session.path_segment}/controls/{control_name}"
        status, outcome = self._post_control(
            path,
            build_request(request_id),
            timeout,
        )
        return ActionReceipt(request_id, status, outcome, cursor)

    def _post_control(
        self,
        path: str,
        body: control_models.control_request.ControlRequestBody,
        timeout: float | None,
    ) -> tuple[int, control_models.control_outcome_response.ControlOutcomeResponse]:
        return self.transport.post(
            path,
            body,
            CONTROL,
            {HTTPStatus.OK, HTTPStatus.ACCEPTED, HTTPStatus.CONFLICT},
            timeout=CONTROL_TIMEOUT_SECONDS if timeout is None else timeout,
        )


class _SessionsBasicControls(_SessionsControlTransport):
    """Control basic session operations."""

    def send(
        self,
        session: SessionRef,
        text: str,
        *,
        attachments: tuple[control_models.attachment_reference.AttachmentReferenceBody, ...] = (),
        replace_terminal_draft: bool = False,
    ) -> ActionReceipt:
        """Send send.

        Returns:
            The action receipt.

        """
        return self._control(
            session,
            "send-text",
            lambda request_id: control_models.send_text_request.SendTextRequest(
                request_id=request_id,
                text=text,
                attachments=attachments,
                replace_terminal_draft=replace_terminal_draft,
            ),
        )

    def interrupt(self, session: SessionRef) -> ActionReceipt:
        """Interrupt.

        Returns:
            The action receipt.

        """
        return self._control(
            session,
            "interrupt",
            lambda request_id: control_models.interrupt_request.InterruptRequest(request_id=request_id),
        )

    def background(self, session: SessionRef) -> ActionReceipt:
        """Return the background.

        Returns:
            Background.

        """
        return self._control(
            session,
            "background",
            lambda request_id: control_models.background_request.BackgroundRequest(request_id=request_id),
        )

    def close(self, session: SessionRef) -> ActionReceipt:
        """Close close.

        Returns:
            The action receipt.

        """
        return self._control(
            session,
            "close-session",
            lambda request_id: control_models.close_session_request.CloseSessionRequest(request_id=request_id),
        )

    def rename(self, session: SessionRef, name: str) -> ActionReceipt:
        """Rename rename.

        Returns:
            The action receipt.

        """
        return self._control(
            session,
            "rename-session",
            lambda request_id: control_models.rename_session_request.RenameSessionRequest(
                request_id=request_id, name=name,
            ),
        )

    def auto_name(self, session: SessionRef) -> ActionReceipt:
        """Auto name.

        Returns:
            The action receipt.

        """
        return self._control(
            session,
            "auto-name-session",
            lambda request_id: control_models.auto_name_session_request.AutoNameSessionRequest(request_id=request_id),
            timeout=AUTOMATIC_NAME_TIMEOUT_SECONDS,
        )
