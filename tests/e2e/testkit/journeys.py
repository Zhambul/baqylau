"""Start, continue, and resume sessions through real client origins."""

from __future__ import annotations

from dataclasses import dataclass

from domain.ids import AccountId, SessionId
from harness.impl import installed
from harness.models import LaunchRequest
from sdk.client import BaqylauClient, LaunchRef, SessionRef, wait_for
from terminal.contract import TerminalPlugin
from terminal.launch import launch_tab_request
from terminal.models import TabCloseRequest, TextSubmitMode, TextSubmitRequest
from terminal.models.values import WindowId
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.resume import SessionResumeSupport
from tests.e2e.testkit.references import (
    JourneyOrigin,
    SessionContinuationRef,
    SessionJourneyRef,
    SessionSpec,
    TurnRef,
)


@dataclass(frozen=True)
class JourneyTurn:
    journey: SessionJourneyRef
    turn: TurnRef


@dataclass(frozen=True)
class ResumedJourney:
    journey: SessionJourneyRef
    continuation: SessionContinuationRef
    turn: TurnRef


class JourneyDriver:
    def __init__(
        self,
        client: BaqylauClient,
        terminal: TerminalPlugin,
        workspace: str,
        application_port: int,
        wait_policy: WaitPolicy,
        launch_environment: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self._client = client
        self._terminal = terminal
        self._workspace = workspace
        self._application_port = application_port
        self._wait_policy = wait_policy
        self._launch_environment = launch_environment
        self._resume = SessionResumeSupport(client, wait_policy)
        self._plugins = {str(plugin.info.name): plugin for plugin in installed()}
        self._windows: set[WindowId] = set()

    def close(self) -> None:
        for window_id in tuple(self._windows):
            self._terminal.tabs.close_tab(TabCloseRequest(window_id))
            self._windows.discard(window_id)

    def start(
        self,
        spec: SessionSpec,
        origin: JourneyOrigin,
        prompt: str,
    ) -> JourneyTurn:
        workspace = spec.workspace or self._workspace
        known = frozenset(item.session.session_id for item in self._client.sessions.list().sessions)
        if origin == JourneyOrigin.DASHBOARD:
            launch = self._client.sessions.launch(
                spec.harness,
                workspace=workspace,
                prompt=prompt,
                model=spec.model,
                effort=spec.effort,
                account_id=spec.account_id,
            )
            window_id = WindowId(launch.window_id)
        else:
            window_id = self._open_terminal(spec, prompt, None)
            launch = LaunchRef(spec.harness, workspace, str(window_id), known)
        self._windows.add(window_id)
        session = self._client.sessions.wait_for_session(
            launch,
            self._wait_policy.session_announcement,
        )
        turn = selectors.launched_turn(
            self._client.sessions.watch(session),
            self._wait_policy.feed,
        )
        return JourneyTurn(SessionJourneyRef(session, origin, str(window_id)), turn)

    def continue_session(
        self,
        journey: SessionJourneyRef,
        origin: JourneyOrigin,
        prompt: str,
    ) -> JourneyTurn:
        before = self._client.sessions.snapshot(journey.session)
        lead = before.lead()
        if origin == JourneyOrigin.DASHBOARD:
            receipt = self._client.sessions.send(journey.session, prompt)
            if receipt.status_code != 200 or receipt.outcome.status != "acknowledged":
                raise AssertionError(f"dashboard continuation was not accepted: {receipt.outcome}")
            cursor_before = receipt.cursor_before
        else:
            window_id = self._terminal_window(journey.session)
            outcome = self._terminal.input.submit_text(
                TextSubmitRequest(
                    window_id,
                    prompt,
                    TextSubmitMode.PASTE,
                )
            )
            if not outcome.succeeded:
                raise AssertionError(f"terminal continuation was not delivered: {outcome.reason}")
            cursor_before = before.cursor
        turn = TurnRef(
            journey.session,
            prompt,
            cursor_before,
            lead.statistics.prompt_count + 1,
            actor_id=lead.actor_id,
        )
        return JourneyTurn(
            SessionJourneyRef(
                journey.session,
                journey.origin,
                journey.window_id,
            ),
            turn,
        )

    def stop_terminal(self, journey: SessionJourneyRef) -> None:
        window_id = WindowId(journey.window_id)
        outcome = self._terminal.tabs.close_tab(TabCloseRequest(window_id))
        if not outcome.succeeded:
            raise AssertionError(f"terminal did not close: {outcome.reason}")
        self._windows.discard(window_id)
        self._client.sessions.wait_until_finished(
            journey.session,
            self._wait_policy.cleanup,
        )

    def resume(
        self,
        journey: SessionJourneyRef,
        origin: JourneyOrigin,
        prompt: str,
    ) -> ResumedJourney:
        source = journey.session
        prepared = self._resume.prepare(source)
        spec = prepared.spec
        workspace = spec.workspace or self._workspace
        if origin == JourneyOrigin.DASHBOARD:
            launch = self._client.sessions.launch(
                spec.harness,
                workspace=workspace,
                prompt=prompt,
                model=spec.model,
                effort=spec.effort,
                account_id=spec.account_id,
                resume_session_id=source.session_id,
            )
            window_id = WindowId(launch.window_id)
        else:
            window_id = self._open_terminal(spec, prompt, source)
        self._windows.add(window_id)
        completed = self._resume.complete(prepared, prompt)
        return ResumedJourney(
            SessionJourneyRef(completed.turn.session, origin, str(window_id)),
            completed.continuation,
            completed.turn,
        )

    def _open_terminal(
        self,
        spec: SessionSpec,
        prompt: str,
        resume: SessionRef | None,
    ) -> WindowId:
        try:
            plugin = self._plugins[spec.harness]
        except KeyError as error:
            raise AssertionError(f"unknown harness {spec.harness!r}") from error
        if plugin.launcher is None:
            raise AssertionError(f"harness {spec.harness!r} cannot launch")
        plan = plugin.launcher.prepare(
            LaunchRequest(
                working_directory=spec.workspace or self._workspace,
                initial_text=prompt,
                model=spec.model,
                effort=spec.effort,
                account_id=AccountId(spec.account_id) if spec.account_id else None,
                resume_session_id=(SessionId(resume.session_id) if resume is not None else None),
            )
        )
        request = launch_tab_request(
            spec.workspace or self._workspace,
            (plan.command, *plan.arguments),
            title=plan.title,
            environment=(
                *plan.environment,
                *self._launch_environment,
                ("BAQYLAU_DASHBOARD_PORT", str(self._application_port)),
            ),
        )
        opened = self._terminal.tabs.open_tab(request)
        if not opened.succeeded or opened.window_id is None:
            raise AssertionError(f"terminal launch failed: {opened.reason}")
        return opened.window_id

    def _terminal_window(self, session: SessionRef) -> WindowId:
        def located() -> WindowId | None:
            state = self._client.preferences.session_state(session).terminal
            if state.window_id is None:
                return None
            return WindowId(state.window_id)

        return wait_for(
            f"session {session.session_id!r} terminal window",
            located,
            timeout=self._wait_policy.feed,
        )
