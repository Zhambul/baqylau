# Copyright (c) 2026 Zhambyl Yermagambet
"""Subscribe to one extension's committed record changes."""

from dataclasses import dataclass
from urllib.parse import urlencode

from baqylau_extension_api.models.scopes import ExtensionScope
from pydantic import TypeAdapter

from api.extensions.change_models import ExtensionChangeFrame, ExtensionChangeReset
from sdk import sse, transport

CHANGE_FRAME = TypeAdapter(ExtensionChangeFrame)
CHANGE_RESET = TypeAdapter(ExtensionChangeReset)


@dataclass(frozen=True)
class ExtensionChangeUpdate:
    """Keep one change or reset frame and its stream cursor."""

    cursor: int
    frame: ExtensionChangeFrame | ExtensionChangeReset


class ExtensionChangesResource:
    """Read the next committed change for one extension."""

    def __init__(self, transport_handle: transport.HttpTransport) -> None:
        """Store the transport handle."""
        self.transport = transport_handle

    def next(
        self,
        extension_id: str,
        scope: ExtensionScope,
        history_revision: str = "default",
        projection_generation: str = "default",
        cursor: int = 0,
    ) -> ExtensionChangeUpdate:
        """Read the next change or reset frame.

        Returns:
            The next typed update and its cursor.

        Raises:
            ApiFailureError: If the stream reports an error or ends early.

        """
        query = urlencode((
            ("scope", scope.model_dump_json()),
            ("history_revision", history_revision),
            ("projection_generation", projection_generation),
            ("cursor", cursor),
        ))
        path = f"/api/extensions/{extension_id}/changes?{query}"
        with self.transport.event_stream(path) as lines:
            for event in sse.events(lines):
                if event.event == "changes":
                    frame = CHANGE_FRAME.validate_json(event.payload)
                    return ExtensionChangeUpdate(cursor=frame.cursor, frame=frame)
                if event.event == "reset":
                    reset = CHANGE_RESET.validate_json(event.payload)
                    return ExtensionChangeUpdate(cursor=reset.cursor, frame=reset)
                if event.event == "error":
                    msg = f"GET {path} stream failed"
                    raise transport.ApiFailureError(msg)
        msg = f"GET {path} ended before a change frame"
        raise transport.ApiFailureError(msg)
