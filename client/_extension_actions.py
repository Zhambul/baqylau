# Copyright (c) 2026 Zhambyl Yermagambet
"""Ask the daemon to run the action of the focused item; the daemon finds the command itself."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import uuid4

import _http
from _daemon_exchange import connection, post_exchange
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from _extension_focus import FocusItem
    from _extension_pane import ExtensionPaneTarget

REQUEST_TIMEOUT_SECONDS = 5.0
SENT = "Sent: %s"
REFUSED = "The daemon refused the action (%s)."
DOWN = "The daemon is not available."


class ActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    extension_id: str
    view_id: str
    action_id: str
    scope: str
    columns: int
    rows: int
    block_id: str
    item_id: str
    request_key: str

    def json_bytes(self) -> bytes:
        """Encode the request body.

        Returns:
            The JSON bytes.

        """
        return self.model_dump_json().encode("utf-8")


def send_action(target: ExtensionPaneTarget, focus: FocusItem, size: tuple[int, int]) -> str:
    """Send the focused item's action once; a slow command runs later, so the pane stays usable.

    Returns:
        The status line for the pane.

    """
    columns, rows = size
    body = ActionBody(
        extension_id=target.extension_id, view_id=target.view_id, action_id=focus.action_id or "",
        scope=target.scope, columns=columns, rows=rows,
        block_id=focus.block_id, item_id=focus.item_id, request_key=uuid4().hex,
    )
    status = _post(target, body)
    if status is None:
        return DOWN
    return SENT % focus.item_id if status == HTTPStatus.ACCEPTED else REFUSED % status


def _post(target: ExtensionPaneTarget, body: ActionBody) -> int | None:
    active_connection = connection(target.host, target.port, REQUEST_TIMEOUT_SECONDS)
    try:
        status, _ = post_exchange(active_connection, _http.EXTENSION_ACTIONS_PATH, body.json_bytes(), None)
    except OSError:
        return None
    finally:
        active_connection.close()
    return status
