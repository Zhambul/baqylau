# Copyright (c) 2026 Zhambyl Yermagambet
"""Select the effort of a RUNNING session through the native command.

A running session does not read the native default again, so the state file
cannot change it. The native `/variants` command opens a list of the efforts
the current model has, and the footer names the selected one.
"""

from harness.contract import ControlHandler
from harness.impl.opencode2 import native_commands, native_screen, native_selection
from harness.models import controls

DIALOG_TITLE = "Select variant"
DIALOG_SECONDS = 5
FOOTER_SEPARATOR = "·"


class SelectEffortHandler(ControlHandler):
    """Open the native effort list and select one value."""

    def __call__(
        self, request: controls.ControlRequest, control_context: controls.ControlContext,
    ) -> controls.ControlResult:
        """Select the requested effort and read it back from the footer.

        Returns:
            An acknowledged selection, a rejected request, or an uncertain one.

        Raises:
            TypeError: If another control is dispatched here.

        """
        if not isinstance(request, controls.SelectEffort):
            message = "select_effort requires SelectEffort"
            raise TypeError(message)
        reason = native_selection.choose(control_context, native_commands.VARIANTS, DIALOG_TITLE, request.effort)
        if reason is not None:
            return controls.ControlResult(
                request.request_id, controls.ControlAcknowledgement.REJECTED, reason,
            )
        if native_screen.wait_for(
            control_context,
            lambda screen: DIALOG_TITLE not in screen and _footer_effort(screen) == request.effort,
            DIALOG_SECONDS,
        ):
            return controls.CommandResult(request.request_id, controls.ControlAcknowledgement.ACKNOWLEDGED)
        return controls.CommandResult(
            request.request_id,
            controls.ControlAcknowledgement.INDETERMINATE,
            "native effort was not selected",
        )


def _footer_effort(screen: str) -> str | None:
    """Read the effort the native footer names.

    Returns:
        The selected effort, or None when the footer names no effort.

    """
    lines = [line for line in screen.splitlines() if native_screen.is_composer_footer(line)]
    if not lines:
        return None
    named = lines[-1].rsplit(FOOTER_SEPARATOR, 1)
    return named[-1].strip() or None
