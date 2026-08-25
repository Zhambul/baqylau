"""Codex controls implemented inside the harness boundary."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping

from pydantic import TypeAdapter, ValidationError

from harness.contract import ControlHandler, HarnessController
from harness.models import (
    AnswerDecision,
    AnswerQuestion,
    ApplyRewind,
    CloseSession,
    Compact,
    ControlAcknowledgement,
    ControlContext,
    ControlName,
    ControlRequest,
    ControlResult,
    DurableTitleResult,
    DecidePlan,
    DeliveryResult,
    Interrupt,
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
    TabRenameRequest,
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
from harness.impl.codex.canonical import rollout, title
from harness.impl.codex.canonical.records import PromptRecord, RolloutRecord, TaskStartedRecord, TurnAbortedRecord
from harness.impl.codex.continuity import RewindContinuity
from harness.impl.codex.controls import backtrack, composer, dialog, modeldialog, plandialog
from harness.impl.codex.controls.dialog import Driver


class _TerminalDriver(Driver):
    """Expose the small driver vocabulary used by Codex's screen modules."""

    def __init__(self, terminal_plugin: TerminalPlugin) -> None:
        self.terminal = terminal_plugin

    def get_text(self, window_id: WindowId, extent: str = "screen", ansi: bool = False) -> str | None:
        del extent
        response = self.terminal.viewport.read_screen(ScreenReadRequest(NativeWindowId(str(window_id)), ansi=ansi))
        return response.text

    def send_key(self, window_id: WindowId, *keys: str) -> bool:
        native = NativeWindowId(str(window_id))
        return all(self.terminal.input.send_key(KeySendRequest(native, str(key))).succeeded for key in keys)

    def send_text(self, window_id: WindowId, text: str) -> bool:
        return self.terminal.input.submit_text(
            TextSubmitRequest(NativeWindowId(str(window_id)), str(text), TextSubmitMode.TYPE)
        ).succeeded

    def paste_text(self, window_id: WindowId, text: str) -> bool:
        return self.terminal.input.submit_text(
            TextSubmitRequest(NativeWindowId(str(window_id)), str(text), TextSubmitMode.PASTE)
        ).succeeded


def _result(request: ControlRequest, succeeded: bool, reason: str) -> ControlResult:
    return ControlResult(
        request.request_id,
        ControlAcknowledgement.ACKNOWLEDGED if succeeded else ControlAcknowledgement.INDETERMINATE,
        None if succeeded else reason,
    )


def _submit(request: ControlRequest, control_context: ControlContext, text: str) -> ControlResult:
    window_id = control_context.terminal_window_id
    if window_id is None:
        return ControlResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
    result = control_context.terminal.input.submit_text(
        TextSubmitRequest(NativeWindowId(str(window_id)), text, TextSubmitMode.PASTE)
    )
    return _result(request, result.succeeded, result.reason or "terminal text was not delivered")


class SendTextHandler(ControlHandler):
    def __call__(
        self,
        request: ControlRequest,
        control_context: ControlContext,
    ) -> DeliveryResult:
        if not isinstance(request, SendText):
            raise TypeError("send_text handler requires SendText")
        window_id = control_context.terminal_window_id
        if window_id is None:
            return DeliveryResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
        if request.replace_terminal_draft:
            try:
                composer.clear(_TerminalDriver(control_context.terminal), window_id)
            except composer.ComposerError as error:
                return DeliveryResult(
                    request.request_id,
                    ControlAcknowledgement.INDETERMINATE,
                    str(error),
                )
        attachment_text = " ".join(attachment.local_path for attachment in request.attachments)
        message = attachment_text + ("\n" if attachment_text and request.text else "") + request.text
        result = _submit(request, control_context, message)
        return DeliveryResult(
            result.request_id,
            result.status,
            result.reason,
            queued=control_context.lead_active,
        )


def _rollout_abort_state(path: str, position: int) -> tuple[bool, bool]:
    try:
        with open(path, "rb") as source:
            source.seek(position)
            lines = source.read().split(b"\n")[:-1]
    except OSError:
        return False, False
    abort_index = None
    # None marks a line that would not parse. The slot is KEPT rather than
    # skipped because abort_index is an index into this list, and dropping
    # unparseable lines would silently shift every position after one.
    records: list[RolloutRecord | None] = []
    for line in lines:
        try:
            record = rollout.parse_line(line.decode())
        except (UnicodeDecodeError, ValidationError):
            records.append(None)
            continue
        records.append(record)
        if abort_index is None and isinstance(record, TurnAbortedRecord):
            abort_index = len(records) - 1
    if abort_index is None:
        return False, False
    queued = any(isinstance(record, (TaskStartedRecord, PromptRecord)) for record in records[abort_index + 1 :])
    return True, queued


class InterruptHandler(ControlHandler):
    def __call__(
        self,
        request: ControlRequest,
        control_context: ControlContext,
    ) -> DeliveryResult:
        session = control_context.session
        terminal = control_context.terminal
        if not isinstance(request, Interrupt):
            raise TypeError("interrupt handler requires Interrupt")
        window_id = control_context.terminal_window_id
        if window_id is None:
            return DeliveryResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
        try:
            position = os.path.getsize(session.source_reference)
        except OSError:
            position = -1
        delivered = False
        native_window_id = NativeWindowId(str(window_id))
        for _attempt in range(2):
            sent = terminal.input.send_key(KeySendRequest(native_window_id, "escape")).succeeded
            delivered = sent or delivered
            if not delivered:
                break
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                time.sleep(0.1)
                if position >= 0:
                    aborted, queued = _rollout_abort_state(session.source_reference, position)
                    if aborted:
                        # The rollout already carries `turn_aborted`: the
                        # ordinary pull source will read this same record on
                        # its next tick and turn it canonical on its own —
                        # no fallback needed.
                        return DeliveryResult(
                            request.request_id, ControlAcknowledgement.ACKNOWLEDGED, queued=queued, corroborated=True
                        )
        return DeliveryResult(
            request.request_id,
            ControlAcknowledgement.INDETERMINATE if delivered else ControlAcknowledgement.REJECTED,
            "turn_aborted was not observed" if delivered else "interrupt key was not delivered",
        )


class CloseSessionHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        terminal = control_context.terminal
        if not isinstance(request, CloseSession):
            raise TypeError("close_session handler requires CloseSession")
        window_id = control_context.terminal_window_id
        if window_id is None:
            return ControlResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
        if control_context.lead_active:
            InterruptHandler()(
                Interrupt(request.session_id, request.request_id),
                control_context,
            )
        result = terminal.tabs.close_tab(TabCloseRequest(NativeWindowId(str(window_id))))
        if not result.succeeded:
            return _result(request, False, result.reason or "terminal tab was not closed")
        return _result(request, True, "terminal tab was not closed")


class RenameSessionHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        session = control_context.session
        terminal = control_context.terminal
        if not isinstance(request, RenameSession):
            raise TypeError("rename_session handler requires RenameSession")
        if control_context.terminal_window_id is None:
            outcome = title.titles.set_title(session.source_reference, request.name)
            if outcome == "unsupported":
                return ControlResult(
                    request.request_id, ControlAcknowledgement.REJECTED, "session source is not renameable"
                )
            if outcome == "unavailable":
                return ControlResult(
                    request.request_id, ControlAcknowledgement.INDETERMINATE, "native title store is unavailable"
                )
            return DurableTitleResult(
                request.request_id,
                ControlAcknowledgement.ACKNOWLEDGED,
            )
        result = _submit(request, control_context, f"/rename {request.name}")
        if result.status == ControlAcknowledgement.ACKNOWLEDGED:
            durable = title.titles.set_title(session.source_reference, request.name)
            if durable == "unavailable":
                return ControlResult(
                    request.request_id,
                    ControlAcknowledgement.INDETERMINATE,
                    "native title store is unavailable",
                )
            if durable == "unsupported":
                return ControlResult(
                    request.request_id,
                    ControlAcknowledgement.REJECTED,
                    "session source is not renameable",
                )
            window_id = control_context.terminal_window_id
            if window_id is not None:
                terminal.tabs.rename_tab(TabRenameRequest(NativeWindowId(str(window_id)), request.name))
        return result


class CompactHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        if not isinstance(request, Compact):
            raise TypeError("compact handler requires Compact")
        return _submit(request, control_context, "/compact")


class ApplyRewindHandler(ControlHandler):
    def __init__(self, rewind_continuity: RewindContinuity) -> None:
        self._rewind_continuity = rewind_continuity

    def __call__(self, request: ControlRequest, control_context: ControlContext) -> RewindResult:
        if not isinstance(request, ApplyRewind):
            raise TypeError("apply_rewind handler requires ApplyRewind")
        if request.mode != "conversation":
            return RewindResult(
                request.request_id,
                ControlAcknowledgement.REJECTED,
                "Codex supports conversation rewind only",
            )
        window_id = control_context.terminal_window_id
        if window_id is None:
            return RewindResult(
                request.request_id,
                ControlAcknowledgement.REJECTED,
                "session is not live",
            )
        try:
            backtrack.drive(
                _TerminalDriver(control_context.terminal),
                window_id,
                request.target_text,
                newer_prompt_count=request.newer_prompt_count,
            )
        except backtrack.BacktrackError as error:
            return RewindResult(
                request.request_id,
                ControlAcknowledgement.INDETERMINATE,
                str(error),
            )
        self._rewind_continuity.expect(request.session_id, window_id)
        return RewindResult(
            request.request_id,
            ControlAcknowledgement.ACKNOWLEDGED,
            restored_text=request.target_text,
        )


class SelectModelHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        if not isinstance(request, SelectModel):
            raise TypeError("select_model handler requires SelectModel")
        terminal = control_context.terminal
        window_id = control_context.terminal_window_id
        if window_id is None:
            return ControlResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
        try:
            modeldialog.set_model_effort(
                _TerminalDriver(terminal),
                window_id,
                model=request.model,
                effort=control_context.current_effort,
            )
        except modeldialog.CodexModelError as error:
            return ControlResult(request.request_id, ControlAcknowledgement.INDETERMINATE, str(error))
        return ControlResult(request.request_id, ControlAcknowledgement.ACKNOWLEDGED)


class SelectEffortHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        if not isinstance(request, SelectEffort):
            raise TypeError("select_effort handler requires SelectEffort")
        terminal = control_context.terminal
        window_id = control_context.terminal_window_id
        if window_id is None:
            return ControlResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
        try:
            modeldialog.set_model_effort(
                _TerminalDriver(terminal),
                window_id,
                effort=request.effort,
            )
        except modeldialog.CodexModelError as error:
            return ControlResult(request.request_id, ControlAcknowledgement.INDETERMINATE, str(error))
        return ControlResult(request.request_id, ControlAcknowledgement.ACKNOWLEDGED)


def _native_prompts(question_asked: QuestionAsked) -> list[dialog.Prompt]:
    return [
        dialog.Prompt(
            id=prompt.prompt_id,
            header=prompt.title or "",
            question=prompt.prompt,
            options=tuple(dialog.PromptChoice(choice.label, choice.description or "") for choice in prompt.choices),
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
                TypeAdapter(list[dialog.Answer]).validate_json(request.answers.json_text)
                if request.answers is not None
                else []
            )
        except ValidationError:
            return ControlResult(
                request.request_id, ControlAcknowledgement.REJECTED, "question answers must be an array"
            )
        try:
            if request.decision == AnswerDecision.DISCUSS:
                dialog.decline(
                    _TerminalDriver(terminal),
                    window_id,
                    _native_prompts(control_context.pending_attention),
                    "Continue in chat.",
                )
                if request.discussion:
                    delivered = _submit(request, control_context, request.discussion)
                    if delivered.status != ControlAcknowledgement.ACKNOWLEDGED:
                        return delivered
            else:
                dialog.drive(
                    _TerminalDriver(terminal),
                    window_id,
                    _native_prompts(control_context.pending_attention),
                    answers,
                )
        except dialog.CodexAskError as error:
            return ControlResult(request.request_id, ControlAcknowledgement.INDETERMINATE, str(error))
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
        except plandialog.CodexPlanError as error:
            return PlanChoicesResult(request.request_id, ControlAcknowledgement.INDETERMINATE, str(error))
        return PlanChoicesResult(
            request.request_id,
            ControlAcknowledgement.ACKNOWLEDGED,
            choices=tuple(PlanChoice(row.digit, row.label) for row in rows),
        )


class DecidePlanHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        terminal = control_context.terminal
        if not isinstance(request, DecidePlan):
            raise TypeError("decide_plan handler requires DecidePlan")
        if request.feedback is not None:
            return ControlResult(
                request.request_id, ControlAcknowledgement.REJECTED, "Codex plan decisions do not accept feedback"
            )
        window_id = control_context.terminal_window_id
        if window_id is None:
            return ControlResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
        driver = _TerminalDriver(terminal)
        try:
            if request.decision == "dismiss":
                plandialog.dismiss(driver, window_id)
            else:
                rows = plandialog.options(driver, window_id)
                row = next((row for row in rows if row.digit == request.decision), None)
                if row is None:
                    return ControlResult(request.request_id, ControlAcknowledgement.REJECTED, "unknown plan decision")
                plandialog.decide(driver, window_id, row.digit, row.label)
        except plandialog.CodexPlanError as error:
            return ControlResult(request.request_id, ControlAcknowledgement.INDETERMINATE, str(error))
        return ControlResult(request.request_id, ControlAcknowledgement.ACKNOWLEDGED)


rewind_continuity = RewindContinuity()

HANDLERS: Mapping[ControlName, ControlHandler] = {
    ControlName.SEND_TEXT: SendTextHandler(),
    ControlName.INTERRUPT: InterruptHandler(),
    ControlName.CLOSE_SESSION: CloseSessionHandler(),
    ControlName.RENAME_SESSION: RenameSessionHandler(),
    ControlName.COMPACT: CompactHandler(),
    ControlName.APPLY_REWIND: ApplyRewindHandler(rewind_continuity),
    ControlName.SELECT_MODEL: SelectModelHandler(),
    ControlName.SELECT_EFFORT: SelectEffortHandler(),
    ControlName.ANSWER_QUESTION: AnswerQuestionHandler(),
    ControlName.READ_PLAN_CHOICES: ReadPlanChoicesHandler(),
    ControlName.DECIDE_PLAN: DecidePlanHandler(),
}

controller = HarnessController(HANDLERS)
