"""Claude Code controls implemented inside the harness boundary."""

from __future__ import annotations

import json
import time

from harness.contract import ControlHandler, HarnessController
from harness.models import (
    AnswerQuestion,
    ApplyRewind,
    AutoNameSession,
    CloseSession,
    CommandResult,
    Compact,
    ControlContext,
    ControlRequest,
    ControlResult,
    DecidePlan,
    DeliveryResult,
    Interrupt,
    MigrateAccount,
    MigrationResult,
    OpenRewind,
    PlanChoicesResult,
    ReadPlanChoices,
    RenameSession,
    RewindResult,
    SelectEffort,
    SelectModel,
    SendText,
)
from terminal.contract import TerminalPlugin
from terminal.launch import launch_tab_request
from terminal.models import (
    KeySendRequest,
    ScreenReadRequest,
    TabCloseRequest,
    TextSubmitRequest,
)
from domain.events import AttentionRequested
from domain.values import AttentionChoice
from harness.impl.claude_code import account
from harness.impl.claude_code.canonical import transcript
from harness.impl.claude_code.controls import askdialog, confirmdialog, plandialog, rewindmenu, tui
from harness.impl.claude_code.probe import ClaudeCodeTerminalProbe


class _TerminalDriver:
    """Expose the small driver vocabulary used by Claude Code's screen modules."""

    def __init__(self, terminal: TerminalPlugin) -> None:
        self.terminal = terminal

    def get_text(self, window_id, extent="screen", ansi=False):
        del extent
        response = self.terminal.viewport.read_screen(
            ScreenReadRequest(str(window_id), ansi=ansi)
        )
        return response.text

    def send_key(self, window_id, *keys):
        return all(
            self.terminal.input.send_key(KeySendRequest(str(window_id), str(key))).succeeded
            for key in keys
        )

    def send_text(self, window_id, text):
        return self.terminal.input.submit_text(
            TextSubmitRequest(str(window_id), str(text), "type")
        ).succeeded

    def paste_text(self, window_id, text):
        return self.terminal.input.submit_text(
            TextSubmitRequest(str(window_id), str(text), "paste")
        ).succeeded


def _screen_text(terminal: TerminalPlugin, window_id: str) -> str | None:
    return terminal.viewport.read_screen(ScreenReadRequest(window_id)).text


def _result(request: ControlRequest, succeeded: bool, reason: str) -> ControlResult:
    return ControlResult(
        request.request_id,
        "acknowledged" if succeeded else "indeterminate",
        None if succeeded else reason,
    )


def _command(
    request: ControlRequest,
    context: ControlContext,
    text: str,
    *,
    confirm: bool = False,
) -> ControlResult:
    terminal = context.terminal
    window_id = context.terminal_window_id
    if window_id is None:
        return ControlResult(request.request_id, "rejected", "session is not live")
    succeeded, _cleared_image = tui.type_command(_TerminalDriver(terminal), window_id, text)
    if not succeeded:
        return _result(request, False, "terminal command was not delivered")
    if not confirm:
        return CommandResult(request.request_id, "acknowledged")
    try:
        confirmation = confirmdialog.confirm(_TerminalDriver(terminal), window_id)
    except confirmdialog.ConfirmError as error:
        return CommandResult(
            request.request_id,
            "indeterminate",
            reason=str(error),
            confirmation="failed",
        )
    return CommandResult(
        request.request_id,
        "acknowledged",
        confirmation="confirmed" if confirmation["dialog"] else "not_needed",
    )


class SendTextHandler(ControlHandler):
    def __call__(
        self,
        request: ControlRequest,
        context: ControlContext,
    ) -> DeliveryResult:
        terminal = context.terminal
        if not isinstance(request, SendText):
            raise TypeError("send_text handler requires SendText")
        window_id = context.terminal_window_id
        if window_id is None:
            return DeliveryResult(request.request_id, "rejected", "session is not live")
        driver = _TerminalDriver(terminal)
        if request.replace_terminal_draft:
            input_state = ClaudeCodeTerminalProbe().input_state(terminal.viewport, window_id)
            tui.clear_input(driver, window_id, input_state.typed_text if input_state else "")
        attachment_text = " ".join(f"@{attachment.local_path}" for attachment in request.attachments)
        message = attachment_text + ("\n" if attachment_text and request.text else "") + request.text
        succeeded, _cleared_image = tui.type_command(driver, window_id, message)
        return DeliveryResult(
            request.request_id,
            "acknowledged" if succeeded else "indeterminate",
            None if succeeded else "terminal message was not delivered",
            queued=False,
        )


class InterruptHandler(ControlHandler):
    def __call__(
        self,
        request: ControlRequest,
        context: ControlContext,
    ) -> DeliveryResult:
        terminal = context.terminal
        if not isinstance(request, Interrupt):
            raise TypeError("interrupt handler requires Interrupt")
        window_id = context.terminal_window_id
        if window_id is None:
            return DeliveryResult(request.request_id, "rejected", "session is not live")
        previous = _screen_text(terminal, window_id)
        if not terminal.input.send_key(KeySendRequest(window_id, "escape")).succeeded:
            return DeliveryResult(request.request_id, "indeterminate", "interrupt key was not delivered")
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
            terminal.input.send_key(KeySendRequest(window_id, "escape"))
        input_state = ClaudeCodeTerminalProbe().input_state(terminal.viewport, window_id)
        return DeliveryResult(
            request.request_id,
            "acknowledged" if stopped is not False else "indeterminate",
            None if stopped is not False else "session remained active after interrupt",
            restored_text=input_state.typed_text if input_state and input_state.typed_text else "",
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
        if not isinstance(request, RenameSession):
            raise TypeError("rename_session handler requires RenameSession")
        if context.terminal_window_id is None:
            try:
                renamed = transcript.set_session_title(session.source_reference, request.name)
            except OSError as error:
                return ControlResult(request.request_id, "indeterminate", str(error))
            if renamed is None:
                return ControlResult(request.request_id, "rejected", "session source is not renameable")
            return ControlResult(request.request_id, "acknowledged")
        return _command(request, context, f"/rename {request.name}")


class AutoNameSessionHandler(ControlHandler):
    def __call__(self, request: ControlRequest, context: ControlContext) -> ControlResult:
        if not isinstance(request, AutoNameSession):
            raise TypeError("auto_name_session handler requires AutoNameSession")
        return _command(request, context, "/rename")


class OpenRewindHandler(ControlHandler):
    def __call__(self, request: ControlRequest, context: ControlContext) -> ControlResult:
        if not isinstance(request, OpenRewind):
            raise TypeError("open_rewind handler requires OpenRewind")
        return _command(request, context, "/rewind")


class ApplyRewindHandler(ControlHandler):
    def __call__(self, request: ControlRequest, context: ControlContext) -> RewindResult:
        terminal = context.terminal
        if not isinstance(request, ApplyRewind):
            raise TypeError("apply_rewind handler requires ApplyRewind")
        window_id = context.terminal_window_id
        if window_id is None:
            return RewindResult(request.request_id, "rejected", "session is not live")
        try:
            result = rewindmenu.drive(
                _TerminalDriver(terminal),
                window_id,
                request.target_text,
                request.mode,
                ups=request.newer_prompt_count + 1,
            )
        except rewindmenu.MenuError as error:
            return RewindResult(request.request_id, "indeterminate", str(error))
        restored = request.target_text if request.mode in {"conversation", "both"} else ""
        return RewindResult(
            request.request_id,
            "acknowledged",
            restored_text=restored,
            degraded=bool(result["degraded"]),
        )


class MigrateAccountHandler(ControlHandler):
    def __call__(self, request: ControlRequest, context: ControlContext) -> MigrationResult:
        if not isinstance(request, MigrateAccount):
            raise TypeError("migrate_account handler requires MigrateAccount")
        if context.current_account is None:
            return MigrationResult(request.request_id, "rejected", "current account is unknown")
        target = account.migration_target(context.current_account.account_id)
        if target is None:
            return MigrationResult(request.request_id, "rejected", "no other account is available")
        session = context.session
        window_id = context.terminal_window_id
        if window_id is None:
            return MigrationResult(request.request_id, "rejected", "session is not live")
        closed = context.terminal.tabs.close_tab(TabCloseRequest(window_id))
        if not closed.succeeded:
            return MigrationResult(request.request_id, "indeterminate", closed.reason)
        arguments = ["--resume", str(session.session_id)]
        if context.current_model is not None:
            arguments.extend(("--model", context.current_model.native_id))
        # Launching is just running the CLI under the target account's alias;
        # the resumed session announces itself through its own hook evidence.
        launched = context.terminal.tabs.open_tab(launch_tab_request(
            session.working_directory or "",
            (target["alias"], *arguments),
            title="Claude Code",
        ))
        if not launched.succeeded:
            return MigrationResult(request.request_id, "indeterminate", launched.reason)
        return MigrationResult(
            request.request_id,
            "acknowledged",
            target_account_id=target["slug"],
        )


class CompactHandler(ControlHandler):
    def __call__(self, request: ControlRequest, context: ControlContext) -> ControlResult:
        if not isinstance(request, Compact):
            raise TypeError("compact handler requires Compact")
        return _command(request, context, "/compact")


class SelectModelHandler(ControlHandler):
    def __call__(self, request: ControlRequest, context: ControlContext) -> ControlResult:
        if not isinstance(request, SelectModel):
            raise TypeError("select_model handler requires SelectModel")
        return _command(request, context, f"/model {request.model_id}", confirm=True)


class SelectEffortHandler(ControlHandler):
    def __call__(self, request: ControlRequest, context: ControlContext) -> ControlResult:
        if not isinstance(request, SelectEffort):
            raise TypeError("select_effort handler requires SelectEffort")
        return _command(request, context, f"/effort {request.effort}", confirm=True)


def _native_prompts(attention: AttentionRequested) -> list[dict]:
    return [
        {
            "id": prompt.prompt_id,
            "header": prompt.title or "",
            "question": prompt.prompt,
            "multiSelect": prompt.multiple,
            "options": [
                {"label": choice.label, "description": choice.description or ""}
                for choice in prompt.choices
            ],
        }
        for prompt in attention.prompts
    ]


class AnswerQuestionHandler(ControlHandler):
    def __call__(self, request: ControlRequest, context: ControlContext) -> ControlResult:
        terminal = context.terminal
        if not isinstance(request, AnswerQuestion):
            raise TypeError("answer_question handler requires AnswerQuestion")
        if context.pending_attention is None:
            return ControlResult(request.request_id, "rejected", "attention request is not pending")
        window_id = context.terminal_window_id
        if window_id is None:
            return ControlResult(request.request_id, "rejected", "session is not live")
        answers = json.loads(request.answers.json_text) if request.answers is not None else []
        if not isinstance(answers, list):
            return ControlResult(request.request_id, "rejected", "question answers must be an array")
        driver = _TerminalDriver(terminal)
        try:
            askdialog.drive(
                driver,
                window_id,
                _native_prompts(context.pending_attention),
                answers,
                chat=request.decision == "discuss",
            )
        except askdialog.AskError as error:
            return ControlResult(request.request_id, "indeterminate", str(error))
        if request.decision == "discuss" and request.discussion:
            succeeded, _cleared_image = tui.type_command(driver, window_id, request.discussion)
            if not succeeded:
                return ControlResult(request.request_id, "indeterminate", "discussion text was not delivered")
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
        except plandialog.PlanError as error:
            return PlanChoicesResult(request.request_id, "indeterminate", str(error))
        return PlanChoicesResult(
            request.request_id,
            "acknowledged",
            choices=tuple(
                AttentionChoice(str(row["digit"]), str(row["label"]), "feedback" if row["feedback"] else None)
                for row in rows
            ),
        )


class DecidePlanHandler(ControlHandler):
    def __call__(self, request: ControlRequest, context: ControlContext) -> ControlResult:
        terminal = context.terminal
        if not isinstance(request, DecidePlan):
            raise TypeError("decide_plan handler requires DecidePlan")
        window_id = context.terminal_window_id
        if window_id is None:
            return ControlResult(request.request_id, "rejected", "session is not live")
        driver = _TerminalDriver(terminal)
        try:
            if request.feedback is not None:
                plandialog.feedback(driver, window_id, request.feedback)
            elif request.decision == "dismiss":
                plandialog.dismiss(driver, window_id)
            else:
                rows = plandialog.options(driver, window_id)
                row = next((row for row in rows if str(row["digit"]) == request.decision), None)
                if row is None:
                    return ControlResult(request.request_id, "rejected", "unknown plan decision")
                plandialog.decide(driver, window_id, row["digit"], row["label"])
        except plandialog.PlanError as error:
            return ControlResult(request.request_id, "indeterminate", str(error))
        return ControlResult(request.request_id, "acknowledged")


controller = HarnessController({
    "send_text": SendTextHandler(),
    "interrupt": InterruptHandler(),
    "close_session": CloseSessionHandler(),
    "rename_session": RenameSessionHandler(),
    "auto_name_session": AutoNameSessionHandler(),
    "open_rewind": OpenRewindHandler(),
    "apply_rewind": ApplyRewindHandler(),
    "migrate_account": MigrateAccountHandler(),
    "compact": CompactHandler(),
    "select_model": SelectModelHandler(),
    "select_effort": SelectEffortHandler(),
    "answer_question": AnswerQuestionHandler(),
    "read_plan_choices": ReadPlanChoicesHandler(),
    "decide_plan": DecidePlanHandler(),
})
