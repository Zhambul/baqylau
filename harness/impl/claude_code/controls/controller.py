"""Claude Code controls implemented inside the harness boundary."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping

from pydantic import TypeAdapter, ValidationError

from harness.contract import ControlHandler, HarnessController
from harness.models import (
    Background,
    AnswerDecision,
    AnswerQuestion,
    ApplyRewind,
    AutoNameSession,
    CloseSession,
    CommandResult,
    Compact,
    ConfirmationOutcome,
    ControlAcknowledgement,
    ControlContext,
    ControlName,
    ControlRequest,
    ControlResult,
    DecidePlan,
    DeliveryResult,
    Interrupt,
    OpenRewind,
    PlanChoice,
    PlanChoicesResult,
    ReadPlanChoices,
    RenameSession,
    RewindResult,
    SelectEffort,
    SelectModel,
    SendText,
)
from terminal.contract import TerminalPlugin
from terminal.models import (
    KeySendRequest,
    ScreenReadRequest,
    TabCloseRequest,
    TextSubmitMode,
    TextSubmitRequest,
)
from domain.events import QuestionAsked
from domain.ids import WindowId
# The terminal's own window id: `terminal/` may depend on nothing outside
# itself, so this module — the harness boundary that talks to a live
# terminal — converts explicitly wherever a domain `WindowId` reaches a
# terminal contract request.
from terminal.models.values import WindowId as NativeWindowId
from harness.impl.claude_code.canonical import records, transcript
from harness.impl.claude_code.controls import askdialog, confirmdialog, plandialog, rewindmenu, tui
from harness.impl.claude_code.probe import ClaudeCodeTerminalProbe


class _TerminalDriver:
    """Expose the small driver vocabulary used by Claude Code's screen modules."""

    def __init__(self, terminal_plugin: TerminalPlugin) -> None:
        self.terminal = terminal_plugin

    def get_text(self, window_id: WindowId, extent: str = "screen", ansi: bool = False) -> str | None:
        del extent
        response = self.terminal.viewport.read_screen(
            ScreenReadRequest(NativeWindowId(str(window_id)), ansi=ansi)
        )
        return response.text

    def send_key(self, window_id: WindowId, *keys: str) -> bool:
        native = NativeWindowId(str(window_id))
        return all(
            self.terminal.input.send_key(KeySendRequest(native, str(key))).succeeded
            for key in keys
        )

    def send_text(self, window_id: WindowId, text: str) -> bool:
        return self.terminal.input.submit_text(
            TextSubmitRequest(NativeWindowId(str(window_id)), str(text), TextSubmitMode.TYPE)
        ).succeeded

    def paste_text(self, window_id: WindowId, text: str) -> bool:
        return self.terminal.input.submit_text(
            TextSubmitRequest(NativeWindowId(str(window_id)), str(text), TextSubmitMode.PASTE)
        ).succeeded


def _screen_text(terminal_plugin: TerminalPlugin, window_id: WindowId) -> str | None:
    return terminal_plugin.viewport.read_screen(
        ScreenReadRequest(NativeWindowId(str(window_id)))
    ).text


def _result(request: ControlRequest, succeeded: bool, reason: str) -> ControlResult:
    return ControlResult(
        request.request_id,
        ControlAcknowledgement.ACKNOWLEDGED if succeeded else ControlAcknowledgement.INDETERMINATE,
        None if succeeded else reason,
    )


def _command(
    request: ControlRequest,
    control_context: ControlContext,
    text: str,
    *,
    confirm: bool = False,
) -> ControlResult:
    terminal = control_context.terminal
    window_id = control_context.terminal_window_id
    if window_id is None:
        return ControlResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
    succeeded, _cleared_image = tui.type_command(_TerminalDriver(terminal), window_id, text)
    if not succeeded:
        return _result(request, False, "terminal command was not delivered")
    if not confirm:
        return CommandResult(request.request_id, ControlAcknowledgement.ACKNOWLEDGED)
    try:
        confirmation = confirmdialog.confirm(_TerminalDriver(terminal), window_id)
    except confirmdialog.ConfirmError as error:
        return CommandResult(
            request.request_id,
            ControlAcknowledgement.INDETERMINATE,
            reason=str(error),
            confirmation=ConfirmationOutcome.FAILED,
        )
    return CommandResult(
        request.request_id,
        ControlAcknowledgement.ACKNOWLEDGED,
        confirmation=(
            ConfirmationOutcome.CONFIRMED
            if confirmation.dialog
            else ConfirmationOutcome.NOT_NEEDED
        ),
    )


class SendTextHandler(ControlHandler):
    def __call__(
        self,
        request: ControlRequest,
        control_context: ControlContext,
    ) -> DeliveryResult:
        terminal = control_context.terminal
        if not isinstance(request, SendText):
            raise TypeError("send_text handler requires SendText")
        window_id = control_context.terminal_window_id
        if window_id is None:
            return DeliveryResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
        driver = _TerminalDriver(terminal)
        if request.replace_terminal_draft:
            input_state = ClaudeCodeTerminalProbe().input_state(terminal.viewport, window_id)
            tui.clear_input(driver, window_id, (input_state.typed_text or "") if input_state else "")
        attachment_text = " ".join(f"@{attachment.local_path}" for attachment in request.attachments)
        message = attachment_text + ("\n" if attachment_text and request.text else "") + request.text
        succeeded, _cleared_image = tui.type_command(driver, window_id, message)
        return DeliveryResult(
            request.request_id,
            ControlAcknowledgement.ACKNOWLEDGED if succeeded else ControlAcknowledgement.INDETERMINATE,
            None if succeeded else "terminal message was not delivered",
            queued=False,
        )


class InterruptHandler(ControlHandler):
    def __call__(
        self,
        request: ControlRequest,
        control_context: ControlContext,
    ) -> DeliveryResult:
        terminal = control_context.terminal
        if not isinstance(request, Interrupt):
            raise TypeError("interrupt handler requires Interrupt")
        window_id = control_context.terminal_window_id
        if window_id is None:
            return DeliveryResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
        previous = _screen_text(terminal, window_id)
        if not terminal.input.send_key(KeySendRequest(NativeWindowId(str(window_id)), "escape")).succeeded:
            return DeliveryResult(
                request.request_id, ControlAcknowledgement.INDETERMINATE, "interrupt key was not delivered"
            )
        stopped: bool | None = None
        for _attempt in range(4):
            time.sleep(0.15)
            current = _screen_text(terminal, window_id)
            if previous is None or current is None:
                break
            if current == previous:
                stopped = True
                break
            stopped = False
            previous = current
            terminal.input.send_key(KeySendRequest(NativeWindowId(str(window_id)), "escape"))
        input_state = ClaudeCodeTerminalProbe().input_state(terminal.viewport, window_id)
        return DeliveryResult(
            request.request_id,
            ControlAcknowledgement.ACKNOWLEDGED if stopped is not False else ControlAcknowledgement.INDETERMINATE,
            None if stopped is not False else "session remained active after interrupt",
            restored_text=input_state.typed_text if input_state and input_state.typed_text else "",
        )


# What Claude Code prints when a running command can be moved to the background,
# with all whitespace removed — a TUI lays its words out on a grid, so the
# spacing between them is the emulator's decision and not the program's.
#
# This marker is the ONLY honest signal that the gesture will do anything.
# Measured in claude-code 2.1.233: the handler for the chord is registered and
# this hint is printed TOGETHER, 2000 ms into a foreground command. A chord sent
# before that lands in the composer as text, and is indistinguishable from one the
# harness received and ignored.
BACKGROUND_OFFER_MARKER = "runinbackground"
BACKGROUND_CHORD = "ctrl+b"
# Long enough to cover the harness's own 2 s delay with room for a slow screen
# read; short enough that a command which will never offer it (one that already
# finished, one the TUI is not blocked on) answers the caller promptly.
BACKGROUND_OFFER_TIMEOUT_SECONDS = 6.0
BACKGROUND_POLL_SECONDS = 0.2

WHITESPACE = re.compile(r"\s+")


def _flattened(screen: str | None) -> str:
    return WHITESPACE.sub("", (screen or "").lower())


class BackgroundHandler(ControlHandler):
    """Move the running command into the background, once the TUI will take it.

    Waits for the harness's own offer before pressing, which is the whole
    substance of this handler: the gesture is a keystroke, and a keystroke sent a
    second too early is silently swallowed into the composer. Waiting here rather
    than at the caller is what makes the gesture reliable for every caller —
    a browser click and a test both.
    """

    def __call__(self, request: ControlRequest, control_context: ControlContext) -> DeliveryResult:
        if not isinstance(request, Background):
            raise TypeError("background handler requires Background")
        terminal = control_context.terminal
        window_id = control_context.terminal_window_id
        if window_id is None:
            return DeliveryResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
        deadline = time.monotonic() + BACKGROUND_OFFER_TIMEOUT_SECONDS
        while BACKGROUND_OFFER_MARKER not in _flattened(_screen_text(terminal, window_id)):
            if time.monotonic() >= deadline:
                return DeliveryResult(
                    request.request_id,
                    ControlAcknowledgement.REJECTED,
                    "no command is offering to be backgrounded",
                )
            time.sleep(BACKGROUND_POLL_SECONDS)
        delivered = terminal.input.send_key(
            KeySendRequest(NativeWindowId(str(window_id)), BACKGROUND_CHORD)
        ).succeeded
        return DeliveryResult(
            request.request_id,
            ControlAcknowledgement.ACKNOWLEDGED if delivered else ControlAcknowledgement.INDETERMINATE,
            None if delivered else "backgrounding chord was not delivered",
            queued=False,
        )


class CloseSessionHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        terminal = control_context.terminal
        if not isinstance(request, CloseSession):
            raise TypeError("close_session handler requires CloseSession")
        window_id = control_context.terminal_window_id
        if window_id is None:
            return ControlResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
        result = terminal.tabs.close_tab(TabCloseRequest(NativeWindowId(str(window_id))))
        return _result(request, result.succeeded, result.reason or "terminal tab was not closed")


class RenameSessionHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        session = control_context.session
        if not isinstance(request, RenameSession):
            raise TypeError("rename_session handler requires RenameSession")
        if control_context.terminal_window_id is None:
            outcome = transcript.titles.set_title(session.source_reference, request.name)
            if outcome == "unsupported":
                return ControlResult(
                    request.request_id, ControlAcknowledgement.REJECTED, "session source is not renameable"
                )
            if outcome == "unavailable":
                return ControlResult(
                    request.request_id, ControlAcknowledgement.INDETERMINATE, "native title store is unavailable"
                )
            return ControlResult(request.request_id, ControlAcknowledgement.ACKNOWLEDGED)
        return _command(request, control_context, f"/rename {request.name}")


class AutoNameSessionHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        if not isinstance(request, AutoNameSession):
            raise TypeError("auto_name_session handler requires AutoNameSession")
        return _command(request, control_context, "/rename")


class OpenRewindHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        if not isinstance(request, OpenRewind):
            raise TypeError("open_rewind handler requires OpenRewind")
        return _command(request, control_context, "/rewind")


class ApplyRewindHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> RewindResult:
        terminal = control_context.terminal
        if not isinstance(request, ApplyRewind):
            raise TypeError("apply_rewind handler requires ApplyRewind")
        window_id = control_context.terminal_window_id
        if window_id is None:
            return RewindResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
        try:
            result = rewindmenu.drive(
                _TerminalDriver(terminal),
                window_id,
                request.target_text,
                request.mode,
                ups=request.newer_prompt_count + 1,
            )
        except rewindmenu.MenuError as error:
            return RewindResult(request.request_id, ControlAcknowledgement.INDETERMINATE, str(error))
        restored = request.target_text if request.mode in {"conversation", "both"} else ""
        return RewindResult(
            request.request_id,
            ControlAcknowledgement.ACKNOWLEDGED,
            restored_text=restored,
            degraded=result.degraded,
        )


class CompactHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        if not isinstance(request, Compact):
            raise TypeError("compact handler requires Compact")
        return _command(request, control_context, "/compact")


class SelectModelHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        if not isinstance(request, SelectModel):
            raise TypeError("select_model handler requires SelectModel")
        return _command(request, control_context, f"/model {request.model}", confirm=True)


class SelectEffortHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        if not isinstance(request, SelectEffort):
            raise TypeError("select_effort handler requires SelectEffort")
        return _command(request, control_context, f"/effort {request.effort}", confirm=True)


def _native_prompts(
    question_asked: QuestionAsked,
) -> list[records.Question]:
    return [
        records.Question(
            id=prompt.prompt_id,
            header=prompt.title or "",
            question=prompt.prompt,
            multiSelect=prompt.multiple,
            options=[
                records.QuestionOption(label=choice.label, description=choice.description or "")
                for choice in prompt.choices
            ],
        )
        for prompt in question_asked.questions
    ]


class AnswerQuestionHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        terminal = control_context.terminal
        if not isinstance(request, AnswerQuestion):
            raise TypeError("answer_question handler requires AnswerQuestion")
        if not isinstance(control_context.pending_attention, QuestionAsked):
            return ControlResult(request.request_id, ControlAcknowledgement.REJECTED, "no question is pending")
        window_id = control_context.terminal_window_id
        if window_id is None:
            return ControlResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
        try:
            answers = (
                TypeAdapter(list[askdialog.AnswerDraft]).validate_json(request.answers.json_text)
                if request.answers is not None
                else []
            )
        except ValidationError as error:
            return ControlResult(
                request.request_id, ControlAcknowledgement.REJECTED, f"malformed question answer: {error}"
            )
        driver = _TerminalDriver(terminal)
        try:
            askdialog.drive(
                driver,
                window_id,
                _native_prompts(control_context.pending_attention),
                answers,
                chat=request.decision == AnswerDecision.DISCUSS,
            )
        except askdialog.AskError as error:
            return ControlResult(request.request_id, ControlAcknowledgement.INDETERMINATE, str(error))
        if request.decision == AnswerDecision.DISCUSS and request.discussion:
            succeeded, _cleared_image = tui.type_command(driver, window_id, request.discussion)
            if not succeeded:
                return ControlResult(
                    request.request_id, ControlAcknowledgement.INDETERMINATE, "discussion text was not delivered"
                )
        return ControlResult(request.request_id, ControlAcknowledgement.ACKNOWLEDGED)


class ReadPlanChoicesHandler(ControlHandler):
    def __call__(
        self,
        request: ControlRequest,
        control_context: ControlContext,
    ) -> PlanChoicesResult:
        terminal = control_context.terminal
        if not isinstance(request, ReadPlanChoices):
            raise TypeError("read_plan_choices handler requires ReadPlanChoices")
        window_id = control_context.terminal_window_id
        if window_id is None:
            return PlanChoicesResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
        try:
            rows = plandialog.options(_TerminalDriver(terminal), window_id)
        except plandialog.PlanError as error:
            return PlanChoicesResult(request.request_id, ControlAcknowledgement.INDETERMINATE, str(error))
        return PlanChoicesResult(
            request.request_id,
            ControlAcknowledgement.ACKNOWLEDGED,
            choices=tuple(
                PlanChoice(row.digit, row.label, row.feedback)
                for row in rows
            ),
        )


class DecidePlanHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        terminal = control_context.terminal
        if not isinstance(request, DecidePlan):
            raise TypeError("decide_plan handler requires DecidePlan")
        window_id = control_context.terminal_window_id
        if window_id is None:
            return ControlResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
        driver = _TerminalDriver(terminal)
        try:
            if request.feedback is not None:
                plandialog.feedback(driver, window_id, request.feedback)
            elif request.decision == "dismiss":
                plandialog.dismiss(driver, window_id)
            else:
                rows = plandialog.options(driver, window_id)
                row = next((row for row in rows if row.digit == request.decision), None)
                if row is None:
                    return ControlResult(request.request_id, ControlAcknowledgement.REJECTED, "unknown plan decision")
                plandialog.decide(driver, window_id, row.digit, row.label)
        except plandialog.PlanError as error:
            return ControlResult(request.request_id, ControlAcknowledgement.INDETERMINATE, str(error))
        return ControlResult(request.request_id, ControlAcknowledgement.ACKNOWLEDGED)


HANDLERS: Mapping[ControlName, ControlHandler] = {
    ControlName.SEND_TEXT: SendTextHandler(),
    ControlName.INTERRUPT: InterruptHandler(),
    ControlName.BACKGROUND: BackgroundHandler(),
    ControlName.CLOSE_SESSION: CloseSessionHandler(),
    ControlName.RENAME_SESSION: RenameSessionHandler(),
    ControlName.AUTO_NAME_SESSION: AutoNameSessionHandler(),
    ControlName.OPEN_REWIND: OpenRewindHandler(),
    ControlName.APPLY_REWIND: ApplyRewindHandler(),
    ControlName.COMPACT: CompactHandler(),
    ControlName.SELECT_MODEL: SelectModelHandler(),
    ControlName.SELECT_EFFORT: SelectEffortHandler(),
    ControlName.ANSWER_QUESTION: AnswerQuestionHandler(),
    ControlName.READ_PLAN_CHOICES: ReadPlanChoicesHandler(),
    ControlName.DECIDE_PLAN: DecidePlanHandler(),
}

controller = HarnessController(HANDLERS)
