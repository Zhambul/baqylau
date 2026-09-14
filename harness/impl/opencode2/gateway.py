# Copyright (c) 2026 Zhambyl Yermagambet
"""Register native sessions through the existing hook endpoint."""

import re
import time
from pathlib import Path

from domain import ids
from harness.contract import HarnessHookGateway
from harness.impl.opencode2.records import NativeRecord
from harness.impl.opencode2.sources import HARNESS
from harness.models.hooks import HarnessHookRequest, HarnessHookResponse
from harness.models.raw_events import RawEvent


class OpenCodeHookGateway(HarnessHookGateway):
    """Accept saved native events and locate their recovery log."""

    def __init__(self, directory: Path) -> None:
        """Use the log directory set by the plugin configuration."""
        self.directory = directory

    def receive_hook(self, harness_hook_request: HarnessHookRequest) -> HarnessHookResponse:
        """Register one native event.

        Returns:
            The raw event and an empty reply.

        Raises:
            ValueError: If the native session identity is not valid.

        """
        native_record = NativeRecord.model_validate_json(harness_hook_request.payload)
        session_id = native_record.event.details.session_id
        if (
            session_id is None or not re.fullmatch(r"ses_[a-zA-Z0-9]+", session_id)
            or native_record.session.id != session_id
        ):
            message = "OpenCode2 event has an invalid session identity"
            raise ValueError(message)
        event_identity = native_record.event.id
        return HarnessHookResponse((RawEvent(
            raw_event_id=ids.RawEventId(f"opencode2:hook:{session_id}:{event_identity}"),
            harness=HARNESS,
            source_type="hook",
            source_name=str(self.directory / f"{session_id}.jsonl"),
            source_position=native_record.event.id,
            session_id=session_id,
            actor_id=ids.ActorId(f"{session_id}:lead"),
            parent_actor_id=None,
            observed_at=time.time(),
            encoding="json",
            payload=harness_hook_request.payload,
            source_identity=f"opencode2:hook:{session_id}",
            terminal_window_id=harness_hook_request.terminal_window_id,
            harness_process_id=harness_hook_request.harness_process_id,
        ),), b"")
