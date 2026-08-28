"""Codex controls implemented inside the harness boundary."""

from __future__ import annotations

import os
import subprocess
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
    InterruptResult,
    Interrupt,
    MessageDeliveryResult,
    MessageDeliveryStatus,
    PlanChoice,
    PlanChoicesResult,
    ReadPlanChoices,
    RenameSession,
    RewindResult,
    SelectEffort,
    SelectModel,
    SendText,
)
from terminal.models import (
    KeySendRequest,
    TabCloseRequest,
    TextInputMode,
    TextSubmitRequest,
)
from domain.events import QuestionAsked
from core.process import process_executable

# The terminal's own window id: `terminal/` may depend on nothing outside
# itself, so this module — the harness boundary that talks to a live
# terminal — converts explicitly wherever a domain `WindowId` reaches a
# terminal contract request.
from terminal.models.values import WindowId as NativeWindowId
from harness.impl.codex.canonical import rollout, title
from harness.impl.codex.canonical.records import PromptRecord, RolloutRecord, TaskStartedRecord, TurnAbortedRecord
from harness.impl.codex.continuity import RewindContinuity
from harness.impl.codex.controls import backtrack, composer, dialog, modeldialog, plandialog
from harness.services.terminal_driver import TerminalDriver
from harness.services.composer import ComposerRestoreError, with_preserved_draft
from harness.runtime import HarnessRuntimeConfig, default_harness_runtime_configs
from domain.ids import HarnessName

NATIVE_TITLE_CONFIRM_TIMEOUT_SECONDS = 5.0
NATIVE_TITLE_CONFIRM_POLL_SECONDS = 0.05


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
        TextSubmitRequest(NativeWindowId(str(window_id)), text, TextInputMode.PASTE)
    )
    return _result(request, result.succeeded, result.reason or "terminal text was not delivered")


def _message(send_text: SendText) -> str:
    attachments = " ".join(attachment.local_path for attachment in send_text.attachments)
    return attachments + ("\n" if attachments and send_text.text else "") + send_text.text


def _queue(
    send_text: SendText,
    harness_process_id: int | None,
    harness_runtime_config: HarnessRuntimeConfig,
) -> ControlResult | MessageDeliveryResult:
    executable = (
        process_executable(harness_process_id)
        if harness_process_id is not None
        else None
    )
    if executable is None:
        return ControlResult(
            send_text.request_id,
            ControlAcknowledgement.REJECTED,
            "Codex process executable is unavailable",
        )
    command = [
        executable,
        "queue",
        "--thread",
        str(send_text.session_id),
        "--message",
        send_text.text,
    ]
    for attachment in send_text.attachments:
        command.extend(("--image", attachment.local_path))
    # The daemon can control a profile that is not its own default profile.
    # Give this Codex child the same configured home as the terminal session.
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(
        harness_runtime_config.configuration_directory
    )
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return ControlResult(
            send_text.request_id,
            ControlAcknowledgement.REJECTED,
            f"Codex did not accept the message: {error}",
        )
    if completed.returncode != 0:
        reason = (completed.stderr or completed.stdout).strip()
        return ControlResult(
            send_text.request_id,
            ControlAcknowledgement.REJECTED,
            reason or "Codex did not accept the message",
        )
    return MessageDeliveryResult(send_text.request_id, MessageDeliveryStatus.QUEUED)


class SendTextHandler(ControlHandler):
    def __init__(self, harness_runtime_config: HarnessRuntimeConfig) -> None:
        self.runtime = harness_runtime_config

    def __call__(
        self,
        request: ControlRequest,
        control_context: ControlContext,
    ) -> ControlResult | MessageDeliveryResult:
        if not isinstance(request, SendText):
            raise TypeError("send_text handler requires SendText")
        window_id = control_context.terminal_window_id
        if window_id is None:
            return ControlResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
        if control_context.lead_active:
            return _queue(
                request,
                control_context.session.harness_process_id,
                self.runtime,
            )
        try:
            composer.clear(TerminalDriver(control_context.terminal), window_id)
        except composer.ComposerError as error:
            return ControlResult(
                request.request_id,
                ControlAcknowledgement.REJECTED,
                str(error),
            )
        result = _submit(request, control_context, _message(request))
        if result.status != ControlAcknowledgement.ACKNOWLEDGED:
            return ControlResult(
                result.request_id,
                ControlAcknowledgement.REJECTED,
                result.reason,
            )
        return MessageDeliveryResult(result.request_id, MessageDeliveryStatus.SENT)


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
    ) -> InterruptResult:
        session = control_context.session
        terminal = control_context.terminal
        if not isinstance(request, Interrupt):
            raise TypeError("interrupt handler requires Interrupt")
        window_id = control_context.terminal_window_id
        if window_id is None:
            return InterruptResult(request.request_id, ControlAcknowledgement.REJECTED, "session is not live")
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
                    aborted, _queued = _rollout_abort_state(session.source_reference, position)
                    if aborted:
                        # The rollout already carries `turn_aborted`: the
                        # ordinary pull source will read this same record on
                        # its next tick and turn it canonical on its own —
                        # no fallback needed.
                        return InterruptResult(
                            request.request_id, ControlAcknowledgement.ACKNOWLEDGED, corroborated=True
                        )
        return InterruptResult(
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
    def __init__(self, titles: title.CodexThreadTitleRepository) -> None:
        self.titles = titles

    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        session = control_context.session
        if not isinstance(request, RenameSession):
            raise TypeError("rename_session handler requires RenameSession")
        if control_context.terminal_window_id is None:
            outcome = self.titles.set_title(session.source_reference, request.name)
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
        window_id = control_context.terminal_window_id
        driver = TerminalDriver(control_context.terminal)

        def rename() -> ControlResult:
            try:
                composer.CodexComposer().submit(
                    driver,
                    window_id,
                    f"/rename {request.name}",
                )
            except composer.ComposerError as error:
                return ControlResult(
                    request.request_id,
                    ControlAcknowledgement.INDETERMINATE,
                    str(error),
                )
            deadline = time.monotonic() + NATIVE_TITLE_CONFIRM_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                observed = self.titles.read_title(session.source_reference)
                if observed is not None and observed.text == request.name:
                    return ControlResult(
                        request.request_id,
                        ControlAcknowledgement.ACKNOWLEDGED,
                    )
                time.sleep(NATIVE_TITLE_CONFIRM_POLL_SECONDS)
            return ControlResult(
                request.request_id,
                ControlAcknowledgement.INDETERMINATE,
                "Codex did not confirm the title",
            )

        try:
            return with_preserved_draft(
                composer.CodexComposer(),
                driver,
                window_id,
                rename,
            )
        except ComposerRestoreError as error:
            return ControlResult(
                request.request_id,
                ControlAcknowledgement.INDETERMINATE,
                str(error),
            )


class CompactHandler(ControlHandler):
    def __call__(self, request: ControlRequest, control_context: ControlContext) -> ControlResult:
        if not isinstance(request, Compact):
            raise TypeError("compact handler requires Compact")
        window_id = control_context.terminal_window_id
        if window_id is None:
            return ControlResult(
                request.request_id,
                ControlAcknowledgement.REJECTED,
                "session is not live",
            )
        try:
            # A turn completion can reach the canonical feed just before the
            # TUI restores its prompt.  Verify the native composer is ready
            # before submitting the slash command; otherwise terminal input
            # can report successful delivery while Codex silently drops it.
            composer.clear(TerminalDriver(control_context.terminal), window_id)
        except composer.ComposerError as error:
            return ControlResult(
                request.request_id,
                ControlAcknowledgement.INDETERMINATE,
                str(error),
            )
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
                TerminalDriver(control_context.terminal),
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
                TerminalDriver(terminal),
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
                TerminalDriver(terminal),
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
                    TerminalDriver(terminal),
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
                    TerminalDriver(terminal),
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
            rows = plandialog.options(TerminalDriver(terminal), window_id)
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
        driver = TerminalDriver(terminal)
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
DEFAULT_RUNTIME_CONFIG = default_harness_runtime_configs().for_harness(
    HarnessName.CODEX
)

HANDLERS: Mapping[ControlName, ControlHandler] = {
    ControlName.SEND_TEXT: SendTextHandler(DEFAULT_RUNTIME_CONFIG),
    ControlName.INTERRUPT: InterruptHandler(),
    ControlName.CLOSE_SESSION: CloseSessionHandler(),
    ControlName.RENAME_SESSION: RenameSessionHandler(title.titles),
    ControlName.COMPACT: CompactHandler(),
    ControlName.APPLY_REWIND: ApplyRewindHandler(rewind_continuity),
    ControlName.SELECT_MODEL: SelectModelHandler(),
    ControlName.SELECT_EFFORT: SelectEffortHandler(),
    ControlName.ANSWER_QUESTION: AnswerQuestionHandler(),
    ControlName.READ_PLAN_CHOICES: ReadPlanChoicesHandler(),
    ControlName.DECIDE_PLAN: DecidePlanHandler(),
}


def build_controller(
    title_repository: title.CodexThreadTitleRepository,
    harness_runtime_config: HarnessRuntimeConfig,
) -> HarnessController:
    handlers: Mapping[ControlName, ControlHandler] = {
        **HANDLERS,
        ControlName.SEND_TEXT: SendTextHandler(harness_runtime_config),
        ControlName.RENAME_SESSION: RenameSessionHandler(title_repository),
    }
    return HarnessController(handlers)


controller = HarnessController(HANDLERS)
