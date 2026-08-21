"""Codex controls implemented inside the harness boundary."""

from __future__ import annotations

import json
import os
import time

from harness.contract import ControlHandler, HarnessController
from harness.models import (
    AnswerQuestion,
    CloseSession,
    Compact,
    ControlContext,
    ControlRequest,
    ControlResult,
    DecidePlan,
    DeliveryResult,
    Interrupt,
    PlanChoice,
    PlanChoicesResult,
    ReadPlanChoices,
    RenameSession,
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
    TextSubmitRequest,
)
from domain.events import QuestionAsked
from harness.impl.codex.canonical import rollout, title
from harness.impl.codex.controls import dialog, modeldialog, plandialog


class _TerminalDriver:
    """Expose the small driver vocabulary used by Codex's screen modules."""

    def __init__(self, terminal: TerminalPlugin) -> None:
        self.terminal = terminal

    def get_text(self, window_id: str, extent: str = "screen", ansi: bool = False) -> str | None:
        del extent
        response = self.terminal.viewport.read_screen(
            ScreenReadRequest(str(window_id), ansi=ansi)
        )
        return response.text

    def send_key(self, window_id: str, *keys: str) -> bool:
        return all(
            self.terminal.input.send_key(KeySendRequest(str(window_id), str(key))).succeeded
            for key in keys
        )

    def send_text(self, window_id: str, text: str) -> bool:
        return self.terminal.input.submit_text(
            TextSubmitRequest(str(window_id), str(text), "type")
        ).succeeded

    def paste_text(self, window_id: str, text: str) -> bool:
        return self.terminal.input.submit_text(
            TextSubmitRequest(str(window_id), str(text), "paste")
        ).succeeded


def _result(request: ControlRequest, succeeded: bool, reason: str) -> ControlResult:
    return ControlResult(
        request.request_id,
        "acknowledged" if succeeded else "indeterminate",
        None if succeeded else reason,
    )


def _submit(request: ControlRequest, context: ControlContext, text: str) -> ControlResult:
    window_id = context.terminal_window_id
    if window_id is None:
        return ControlResult(request.request_id, "rejected", "session is not live")
    result = context.terminal.input.submit_text(TextSubmitRequest(window_id, text, "paste"))
    return _result(request, result.succeeded, result.reason or "terminal text was not delivered")


class SendTextHandler(ControlHandler):
    def __call__(
        self,
        request: ControlRequest,
        context: ControlContext,
    ) -> DeliveryResult:
        if not isinstance(request, SendText):
            raise TypeError("send_text handler requires SendText")
        if request.replace_terminal_draft:
            return DeliveryResult(request.request_id, "rejected", "Codex draft replacement is unsupported")
        attachment_text = " ".join(attachment.local_path for attachment in request.attachments)
        message = attachment_text + ("\n" if attachment_text and request.text else "") + request.text
        result = _submit(request, context, message)
        return DeliveryResult(result.request_id, result.status, result.reason, queued=False)


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
    records: list[dict[str, object] | None] = []
    for line in lines:
        try:
            document = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            records.append(None)
            continue
        record = rollout.parse(document)
        records.append(record)
        if abort_index is None and record and record.get("kind") == "turn_aborted":
            abort_index = len(records) - 1
    if abort_index is None:
        return False, False
    queued = any(
        record and record.get("kind") in ("task_started", "prompt")
        for record in records[abort_index + 1:]
    )
    return True, queued


class InterruptHandler(ControlHandler):
    def __call__(
        self,
        request: ControlRequest,
        context: ControlContext,
    ) -> DeliveryResult:
        session = context.session
        terminal = context.terminal
        if not isinstance(request, Interrupt):
            raise TypeError("interrupt handler requires Interrupt")
        window_id = context.terminal_window_id
        if window_id is None:
            return DeliveryResult(request.request_id, "rejected", "session is not live")
        try:
            position = os.path.getsize(session.source_reference)
        except OSError:
            position = -1
        delivered = False
        for _attempt in range(2):
            delivered = terminal.input.send_key(KeySendRequest(window_id, "escape")).succeeded or delivered
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
                            request.request_id, "acknowledged", queued=queued, corroborated=True
                        )
        return DeliveryResult(
            request.request_id,
            "indeterminate" if delivered else "rejected",
            "turn_aborted was not observed" if delivered else "interrupt key was not delivered",
        )


class CloseSessionHandler(ControlHandler):
    def __call__(self, request: ControlRequest, context: ControlContext) -> ControlResult:
        terminal = context.terminal
        if not isinstance(request, CloseSession):
            raise TypeError("close_session handler requires CloseSession")
        window_id = context.terminal_window_id
        if window_id is None:
            return ControlResult(request.request_id, "rejected", "session is not live")
        result = terminal.tabs.close_tab(TabCloseRequest(window_id))
        return _result(request, result.succeeded, result.reason or "terminal tab was not closed")


class RenameSessionHandler(ControlHandler):
    def __call__(self, request: ControlRequest, context: ControlContext) -> ControlResult:
        session = context.session
        terminal = context.terminal
        if not isinstance(request, RenameSession):
            raise TypeError("rename_session handler requires RenameSession")
        if context.terminal_window_id is None:
            outcome = title.titles.set_title(session.source_reference, request.name)
            if outcome == "unsupported":
                return ControlResult(request.request_id, "rejected", "session source is not renameable")
            if outcome == "unavailable":
                return ControlResult(request.request_id, "indeterminate", "native title store is unavailable")
            return ControlResult(request.request_id, "acknowledged")
        result = _submit(request, context, f"/rename {request.name}")
        if result.status == "acknowledged":
            window_id = context.terminal_window_id
            if window_id is not None:
                terminal.tabs.rename_tab(TabRenameRequest(window_id, request.name))
        return result


class CompactHandler(ControlHandler):
    def __call__(self, request: ControlRequest, context: ControlContext) -> ControlResult:
        if not isinstance(request, Compact):
            raise TypeError("compact handler requires Compact")
        return _submit(request, context, "/compact")


class SelectModelHandler(ControlHandler):
    def __call__(self, request: ControlRequest, context: ControlContext) -> ControlResult:
        if not isinstance(request, SelectModel):
            raise TypeError("select_model handler requires SelectModel")
        terminal = context.terminal
        window_id = context.terminal_window_id
        if window_id is None:
            return ControlResult(request.request_id, "rejected", "session is not live")
        try:
            modeldialog.set_model_effort(
                _TerminalDriver(terminal),
                window_id,
                model=request.model_id,
                effort=context.current_effort,
            )
        except modeldialog.CodexModelError as error:
            return ControlResult(request.request_id, "indeterminate", str(error))
        return ControlResult(request.request_id, "acknowledged")


class SelectEffortHandler(ControlHandler):
    def __call__(self, request: ControlRequest, context: ControlContext) -> ControlResult:
        if not isinstance(request, SelectEffort):
            raise TypeError("select_effort handler requires SelectEffort")
        terminal = context.terminal
        window_id = context.terminal_window_id
        if window_id is None:
            return ControlResult(request.request_id, "rejected", "session is not live")
        try:
            modeldialog.set_model_effort(
                _TerminalDriver(terminal),
                window_id,
                effort=request.effort,
            )
        except modeldialog.CodexModelError as error:
            return ControlResult(request.request_id, "indeterminate", str(error))
        return ControlResult(request.request_id, "acknowledged")


def _native_prompts(attention: QuestionAsked) -> list[dialog.Prompt]:
    return [
        {
            "id": prompt.prompt_id,
            "header": prompt.title or "",
            "question": prompt.prompt,
            "options": [
                {"label": choice.label, "description": choice.description or ""}
                for choice in prompt.choices
            ],
        }
        for prompt in attention.questions
    ]


class AnswerQuestionHandler(ControlHandler):
    def __call__(self, request: ControlRequest, context: ControlContext) -> ControlResult:
        terminal = context.terminal
        if not isinstance(request, AnswerQuestion):
            raise TypeError("answer_question handler requires AnswerQuestion")
        if not isinstance(context.pending_attention, QuestionAsked):
            return ControlResult(request.request_id, "rejected", "no question is pending")
        window_id = context.terminal_window_id
        if window_id is None:
            return ControlResult(request.request_id, "rejected", "session is not live")
        answers = json.loads(request.answers.json_text) if request.answers is not None else []
        if not isinstance(answers, list):
            return ControlResult(request.request_id, "rejected", "question answers must be an array")
        try:
            if request.decision == "discuss":
                dialog.decline(
                    _TerminalDriver(terminal),
                    window_id,
                    _native_prompts(context.pending_attention),
                    request.discussion or "",
                )
            else:
                dialog.drive(
                    _TerminalDriver(terminal),
                    window_id,
                    _native_prompts(context.pending_attention),
                    answers,
                )
        except dialog.CodexAskError as error:
            return ControlResult(request.request_id, "indeterminate", str(error))
        return ControlResult(request.request_id, "acknowledged")


class ReadPlanChoicesHandler(ControlHandler):
    def __call__(
        self,
        request: ControlRequest,
        context: ControlContext,
    ) -> PlanChoicesResult:
        terminal = context.terminal
        if not isinstance(request, ReadPlanChoices):
            raise TypeError("read_plan_choices handler requires ReadPlanChoices")
        window_id = context.terminal_window_id
        if window_id is None:
            return PlanChoicesResult(request.request_id, "rejected", "session is not live")
        try:
            rows = plandialog.options(_TerminalDriver(terminal), window_id)
        except plandialog.CodexPlanError as error:
            return PlanChoicesResult(request.request_id, "indeterminate", str(error))
        return PlanChoicesResult(
            request.request_id,
            "acknowledged",
            choices=tuple(
                PlanChoice(str(row["digit"]), str(row["label"]))
                for row in rows
            ),
        )


class DecidePlanHandler(ControlHandler):
    def __call__(self, request: ControlRequest, context: ControlContext) -> ControlResult:
        terminal = context.terminal
        if not isinstance(request, DecidePlan):
            raise TypeError("decide_plan handler requires DecidePlan")
        if request.feedback is not None:
            return ControlResult(request.request_id, "rejected", "Codex plan decisions do not accept feedback")
        window_id = context.terminal_window_id
        if window_id is None:
            return ControlResult(request.request_id, "rejected", "session is not live")
        driver = _TerminalDriver(terminal)
        try:
            if request.decision == "dismiss":
                plandialog.dismiss(driver, window_id)
            else:
                rows = plandialog.options(driver, window_id)
                row = next((row for row in rows if str(row["digit"]) == request.decision), None)
                if row is None:
                    return ControlResult(request.request_id, "rejected", "unknown plan decision")
                plandialog.decide(driver, window_id, row["digit"], row["label"])
        except plandialog.CodexPlanError as error:
            return ControlResult(request.request_id, "indeterminate", str(error))
        return ControlResult(request.request_id, "acknowledged")


controller = HarnessController({
    "send_text": SendTextHandler(),
    "interrupt": InterruptHandler(),
    "close_session": CloseSessionHandler(),
    "rename_session": RenameSessionHandler(),
    "compact": CompactHandler(),
    "select_model": SelectModelHandler(),
    "select_effort": SelectEffortHandler(),
    "answer_question": AnswerQuestionHandler(),
    "read_plan_choices": ReadPlanChoicesHandler(),
    "decide_plan": DecidePlanHandler(),
})
