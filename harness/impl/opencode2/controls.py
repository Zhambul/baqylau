# Copyright (c) 2026 Zhambyl Yermagambet
"""Close an OpenCode2 session through its owned terminal."""

from types import MappingProxyType

from harness import contract
from harness.impl.opencode2 import select_effort, select_model
from harness.impl.opencode2.answer_question import AnswerQuestionHandler
from harness.impl.opencode2.background_control import BackgroundHandler
from harness.impl.opencode2.compact import CompactHandler
from harness.impl.opencode2.interrupt import InterruptHandler
from harness.impl.opencode2.rename import RenameSessionHandler
from harness.impl.opencode2.rewind import ApplyRewindHandler
from harness.impl.opencode2.send_text import SendTextHandler
from harness.models import controls
from terminal.models import tabs, values


class CloseSessionHandler(contract.ControlHandler):
    """Handle the native session close control."""

    def __call__(
        self, request: controls.ControlRequest, control_context: controls.ControlContext,
    ) -> controls.ControlResult:
        """Close only the terminal assigned to this session.

        Returns:
            The terminal action result. Process liveness supplies the finished fact.

        """
        window_id = control_context.terminal_window_id
        if window_id is None:
            return controls.ControlResult(
                request.request_id, controls.ControlAcknowledgement.REJECTED, "session has no terminal",
            )
        close_request = tabs.TabCloseRequest(values.WindowId(str(window_id)))
        result = control_context.terminal.tabs.close_tab(close_request)
        status = controls.ControlAcknowledgement.REJECTED
        if result.succeeded:
            status = controls.ControlAcknowledgement.ACKNOWLEDGED
        return controls.ControlResult(request.request_id, status, result.reason)


HANDLERS: MappingProxyType[controls.ControlName, contract.ControlHandler] = MappingProxyType({
    controls.ControlName.APPLY_REWIND: ApplyRewindHandler(),
    controls.ControlName.BACKGROUND: BackgroundHandler(),
    controls.ControlName.CLOSE_SESSION: CloseSessionHandler(),
    controls.ControlName.COMPACT: CompactHandler(),
    controls.ControlName.SEND_TEXT: SendTextHandler(),
    controls.ControlName.ANSWER_QUESTION: AnswerQuestionHandler(),
    controls.ControlName.INTERRUPT: InterruptHandler(),
    controls.ControlName.RENAME_SESSION: RenameSessionHandler(),
    controls.ControlName.SELECT_EFFORT: select_effort.SelectEffortHandler(),
    controls.ControlName.SELECT_MODEL: select_model.SelectModelHandler(),
})
controller = contract.HarnessController(HANDLERS)
