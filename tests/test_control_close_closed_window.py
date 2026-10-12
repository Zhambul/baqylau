# Copyright (c) 2026 Zhambyl Yermagambet
"""Close a session whose terminal window is already closed."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from domain import ids as domain_ids
from harness.models import controls as control_models
from harness.services import controls as control_services
from tests import (
    control_effect_sessions as control_sessions,
    control_effect_stores as stores,
    control_effect_values as control_values,
)
from tests.harness_names import CODEX_HARNESS

if TYPE_CHECKING:
    from harness.contracts import plugin as plugin_contracts

STORED_WINDOW = domain_ids.WindowId("7")
HARNESS_REFUSAL = "session is not live"


@pytest.mark.parametrize(
    ("stored_window", "open_windows", "acknowledged"),
    [
        # The window of the session closed.
        (STORED_WINDOW, (), True),
        # The window is open, but another session owns it.
        (STORED_WINDOW, (SimpleNamespace(window_id=STORED_WINDOW),), False),
        # The session never had a window.
        (None, (), False),
    ],
)
def test_close_of_a_closed_window_is_acknowledged(
    stored_window: domain_ids.WindowId | None,
    open_windows: tuple[SimpleNamespace, ...],
    *,
    acknowledged: bool,
) -> None:
    """Acknowledge a close when the window of the session is already closed.

    A snapshot uses the last terminal read, so the dashboard can show the window
    after it closed. Any other session without its own window goes to its harness.
    """
    controller = Mock()
    controller.execute.side_effect = lambda request, _context: control_models.ControlResult(
        request.request_id, control_models.ControlAcknowledgement.REJECTED, HARNESS_REFUSAL,
    )
    dependencies = _dependencies(stored_window, open_windows, controller)

    closed = control_services.HarnessControlService(dependencies).close_session(
        control_models.CloseSession(control_values.TEST_SESSION_ID, domain_ids.RequestId("close-one")),
    )

    assert (closed.status == control_models.ControlAcknowledgement.ACKNOWLEDGED) is acknowledged
    assert controller.execute.called is not acknowledged
    assert dependencies.control_effect_recorder.session_closed.called is acknowledged


def _dependencies(
    stored_window: domain_ids.WindowId | None,
    open_windows: tuple[SimpleNamespace, ...],
    controller: Mock,
) -> Mock:
    plugin = SimpleNamespace(harness_info=SimpleNamespace(name=CODEX_HARNESS), controller=controller)
    session = replace(
        control_sessions.codex_session(),
        terminal_window_id=stored_window,
        plugin=cast("plugin_contracts.HarnessPlugin", plugin),
    )
    dependencies = Mock()
    dependencies.session_repository = stores.Sessions(session)
    dependencies.terminal_adapter.window_for_session.return_value = None
    dependencies.terminal_adapter.windows.return_value = open_windows
    dependencies.session_data_repository.read.return_value = None
    return dependencies
