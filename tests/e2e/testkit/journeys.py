"""Start, continue, and resume sessions through real client origins."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass

from domain.ids import HarnessName
from harness.runtime import HarnessRuntimeConfigs
from sdk.client import BaqylauClient, LaunchRef, SessionRef, wait_for
from terminal.contract import TerminalPlugin
from terminal.launch import launch_tab_request
from terminal.models import (
    KeySendRequest,
    TabCloseRequest,
    TextInputMode,
    TextInsertRequest,
    TextSubmitRequest,
)
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
        harness_runtime_configs: HarnessRuntimeConfigs,
        launch_environment: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self._client = client
        self._terminal = terminal
        self._workspace = workspace
        self._application_port = application_port
        self._wait_policy = wait_policy
        self._runtime_configs = harness_runtime_configs
        self._launch_environment = launch_environment
        self._resume = SessionResumeSupport(client, wait_policy)
        self._windows: set[WindowId] = set()

    @property
    def window_ids(self) -> frozenset[str]:
        return frozenset(str(window_id) for window_id in self._windows)

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
            if receipt.status_code != 200 or receipt.outcome.status not in ("sent", "queued"):
                raise AssertionError(f"dashboard continuation was not accepted: {receipt.outcome}")
            cursor_before = receipt.cursor_before
        else:
            window_id = self._terminal_window(journey.session)
            outcome = self._terminal.input.submit_text(
                TextSubmitRequest(
                    window_id,
                    prompt,
                    TextInputMode.PASTE,
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

    def submit_native_command(self, journey: SessionJourneyRef, command: str) -> None:
        """Submit one native CLI command to the session's real host window."""
        outcome = self._terminal.input.submit_text(
            TextSubmitRequest(
                WindowId(journey.window_id),
                command,
                TextInputMode.TYPE,
            )
        )
        if not outcome.succeeded:
            raise AssertionError(f"native command was not delivered: {outcome.reason}")

    def insert_terminal_draft(
        self,
        journey: SessionJourneyRef,
        text: str,
    ) -> None:
        outcome = self._terminal.input.insert_text(
            TextInsertRequest(
                WindowId(journey.window_id),
                text,
                TextInputMode.PASTE,
            )
        )
        if not outcome.succeeded:
            raise AssertionError(
                f"terminal draft was not inserted: {outcome.reason}"
            )

    def use_visual_editor_mode(self, journey: SessionJourneyRef) -> None:
        window_id = WindowId(journey.window_id)
        for key in ("escape", "v"):
            outcome = self._terminal.input.send_key(KeySendRequest(window_id, key))
            if not outcome.succeeded:
                raise AssertionError(
                    f"terminal editor mode key was not delivered: {outcome.reason}"
                )

    def interrupt_from_terminal(self, journey: SessionJourneyRef) -> None:
        """Press Escape twice without an HTTP control.

        The first event can leave the composer input mode. The second event
        then reaches the active-turn interrupt binding.
        """
        for _attempt in range(2):
            outcome = self._terminal.input.send_key(
                KeySendRequest(WindowId(journey.window_id), "escape")
            )
            if not outcome.succeeded:
                raise AssertionError(
                    f"terminal interrupt was not delivered: {outcome.reason}"
                )

    def start_new_native_session(
        self,
        journey: SessionJourneyRef,
        prompt: str,
    ) -> JourneyTurn:
        """Use native `/new` and send the first prompt in the same host tab."""
        before = self._client.sessions.snapshot(journey.session)
        known = frozenset(item.session.session_id for item in self._client.sessions.list().sessions)
        self.submit_native_command(journey, "/new")
        self.submit_native_command(journey, prompt)
        candidates: list[str] = []

        def announced() -> SessionRef | None:
            nonlocal candidates
            candidates = [
                item.session.session_id
                for item in self._client.sessions.list().sessions
                if item.session.session_id not in known
                and item.session.harness == before.data.session.harness
                and item.session.working_directory == before.data.session.working_directory
            ]
            if len(candidates) > 1:
                raise AssertionError(
                    f"native /new produced multiple sessions in window {journey.window_id!r}: {candidates}"
                )
            if candidates:
                return SessionRef(candidates[0])
            retry = self._terminal.input.send_key(KeySendRequest(WindowId(journey.window_id), "enter"))
            if not retry.succeeded:
                raise AssertionError(f"native /new prompt was not submitted: {retry.reason}")
            return None

        session = wait_for(
            lambda: f"native /new in window {journey.window_id!r} to announce one session; found {candidates}",
            announced,
            timeout=self._wait_policy.session_announcement,
        )
        turn = selectors.turn(
            self._client.sessions.watch(session),
            TurnRef(
                session=session,
                prompt=prompt,
                cursor_before=0,
                expected_prompt_count=1,
            ),
            self._wait_policy.feed,
        )
        return JourneyTurn(
            SessionJourneyRef(session, JourneyOrigin.TERMINAL, journey.window_id),
            turn,
        )

    def run_unattended_with_inherited_window(
        self,
        spec: SessionSpec,
        host: SessionJourneyRef,
        prompt: str,
    ) -> SessionRef:
        """Run a real non-interactive harness with a copied terminal variable.

        This is an invalid ownership claim but a valid process environment:
        commands started by an agent inherit the host's KITTY_WINDOW_ID. The
        harness must still run and report its native session. Baqylau must not
        treat that copied value as proof that the process owns the host tab.
        """
        environment = dict(os.environ)
        for name in (
            "CLAUDECODE",
            "CLAUDE_CODE_CHILD_SESSION",
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_ENTRYPOINT",
            "CLAUDE_CODE_EXECPATH",
            "CLAUDE_CODE_MESSAGING_SOCKET",
            "CLAUDE_CODE_MESSAGING_TOKEN",
            "CLAUDE_CODE_SSE_PORT",
            "CLAUDE_PID",
            "CLAUDE_EFFORT",
            "CLAUDE_OTEL_PORT",
            "CODEX_COMPANION_SESSION_ID",
            "BAQYLAU_LAUNCH_MODEL",
            "BAQYLAU_LAUNCH_EFFORT",
        ):
            environment.pop(name, None)
        # The root test fixture isolates application files. A real detached
        # Claude process must use the user's installed authentication and hook
        # settings, as a normal terminal command does.
        environment.pop("CLAUDE_CONFIG_DIR", None)
        environment.update(self._launch_environment)
        environment["BAQYLAU_DASHBOARD_PORT"] = str(self._application_port)
        environment["KITTY_WINDOW_ID"] = host.window_id

        workspace = spec.workspace or self._workspace
        command: tuple[str, ...]
        if spec.harness == "claude_code":
            command = (
                "claude",
                "--print",
                "--output-format",
                "json",
                "--model",
                spec.model,
                "--effort",
                spec.effort,
                prompt,
            )
        elif spec.harness == "codex":
            command = (
                "codex",
                "exec",
                "--json",
                "--skip-git-repo-check",
                "--model",
                spec.model,
                "--config",
                f'model_reasoning_effort="{spec.effort}"',
                "--cd",
                workspace,
                prompt,
            )
        else:
            raise AssertionError(f"unattended execution is not defined for {spec.harness!r}")

        completed = subprocess.run(
            command,
            cwd=workspace,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self._wait_policy.cleanup,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"unattended {spec.harness} exited with {completed.returncode}: {completed.stderr.strip()}"
            )
        session = SessionRef(self._unattended_session_id(spec.harness, completed.stdout))
        self._client.sessions.wait_until_finished(session, self._wait_policy.cleanup)
        return session

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
        harness = HarnessName(spec.harness)
        runtime = self._runtime_configs.for_harness(harness)
        if spec.account_id is not None:
            raise AssertionError(f"{spec.harness} has no account switcher")
        arguments: list[str] = []
        if harness == HarnessName.CLAUDE_CODE:
            if resume is not None:
                arguments.extend(("--resume", resume.session_id))
            if spec.model:
                arguments.extend(("--model", spec.model))
            if spec.effort:
                arguments.extend(("--effort", spec.effort))
        else:
            if resume is not None:
                arguments.extend(("resume", resume.session_id))
            arguments.extend(("-C", spec.workspace or self._workspace))
            if spec.model:
                arguments.extend(("-m", spec.model))
            if spec.effort:
                arguments.extend(("-c", f"model_reasoning_effort={spec.effort}"))
            arguments.extend(("-c", 'model_reasoning_summary="concise"'))
        if prompt.strip():
            arguments.append(prompt)
        environment = dict(self._launch_environment)
        environment["BAQYLAU_DASHBOARD_PORT"] = str(self._application_port)
        if harness == HarnessName.CLAUDE_CODE:
            environment["CLAUDE_CONFIG_DIR"] = str(runtime.configuration_directory)
            if runtime.settings_file is not None:
                environment["CLAUDE_CODE_MANAGED_SETTINGS_PATH"] = str(
                    runtime.settings_file
                )
        else:
            environment["CODEX_HOME"] = str(runtime.configuration_directory)
        request = launch_tab_request(
            spec.workspace or self._workspace,
            self._reusable_shell_command((runtime.executable, *arguments)),
            title="Claude Code" if harness == HarnessName.CLAUDE_CODE else "Codex",
            environment=tuple(environment.items()),
        )
        opened = self._terminal.tabs.open_tab(request)
        if not opened.succeeded or opened.window_id is None:
            raise AssertionError(f"terminal launch failed: {opened.reason}")
        return opened.window_id

    @staticmethod
    def _reusable_shell_command(command: tuple[str, ...]) -> tuple[str, ...]:
        """Run a harness as a command in a terminal shell.

        A person starts a harness from a shell. When the harness exits, that
        shell remains and can start another session. A tab whose first process
        is the harness itself has different lifecycle behavior and is not a
        valid terminal-origin journey.
        """
        invocation = shlex.join(command)
        return ("/bin/zsh", "-fc", f"{invocation}; exec /bin/zsh -fi")

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

    @staticmethod
    def _unattended_session_id(harness: str, output: str) -> str:
        if harness == "claude_code":
            try:
                session_id = json.loads(output)["session_id"]
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                raise AssertionError(f"Claude did not report a session id: {output!r}") from error
            return str(session_id)
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started" and event.get("thread_id"):
                return str(event["thread_id"])
        raise AssertionError(f"Codex did not report a thread id: {output!r}")
