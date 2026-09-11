# Copyright (c) 2026 Zhambyl Yermagambet
"""Start, continue, and resume sessions through real client origins."""

from __future__ import annotations

import os
import subprocess  # noqa: S404 -- Run real harness commands for unattended E2E cases.
from functools import partial
from http import HTTPStatus

from domain.ids import HarnessName
from sdk import client as sdk_client, state as sdk_state, wait_states
from terminal import launch as terminal_launch, models as terminal_models
from tests.e2e.testkit import journey_launch, journey_models, references, resume, selector_turns
from tests.harness_names import CLAUDE_CODE_HARNESS


class _JourneyDriverState:
    """Store shared journey driver state."""

    def __init__(
        self,
        client: sdk_client.BaqylauClient,
        environment: journey_models.JourneyEnvironment,
    ) -> None:
        """Initialize the object."""
        self._client = client
        self._terminal = environment.terminal
        self._workspace = environment.workspace
        self._application_port = environment.application_port
        self._wait_policy = environment.wait_policy
        self._runtime_configs = environment.harness_runtime_configs
        self._launch_environment = environment.launch_environment
        self._resume = resume.SessionResumeSupport(client, environment.wait_policy)
        self._windows: set[terminal_models.values.WindowId] = set()

    @property
    def window_ids(self) -> frozenset[str]:
        """Process window identifiers."""
        return frozenset(str(window_id) for window_id in self._windows)

    def close(self) -> None:
        """Close close."""
        for window_id in tuple(self._windows):
            self._terminal.tabs.close_tab(terminal_models.tabs.TabCloseRequest(window_id))
            self._windows.discard(window_id)


class _JourneyTerminalInput(_JourneyDriverState):
    """Control terminal input for one journey."""

    def stop_terminal(self, journey: references.SessionJourneyRef) -> None:
        """Stop terminal."""
        window_id = terminal_models.values.WindowId(journey.window_id)
        outcome = self._terminal.tabs.close_tab(terminal_models.tabs.TabCloseRequest(window_id))
        message = f"terminal did not close: {outcome.reason}"
        assert outcome.succeeded, message
        self._windows.discard(window_id)
        self._client.sessions.wait_until_finished(
            journey.session,
            self._wait_policy.cleanup,
        )

    def submit_native_command(self, journey: references.SessionJourneyRef, command: str) -> None:
        """Submit one native CLI command to the session's real host window."""
        outcome = self._terminal.input.submit_text(
            terminal_models.input.TextSubmitRequest(
                terminal_models.values.WindowId(journey.window_id),
                command,
                terminal_models.input.TextInputMode.TYPE,
            ),
        )
        message = f"native command was not delivered: {outcome.reason}"
        assert outcome.succeeded, message

    def insert_terminal_draft(
        self,
        journey: references.SessionJourneyRef,
        text: str,
    ) -> None:
        """Paste a draft into the journey's terminal window.

        Raises:
            AssertionError: If the terminal rejects the text insertion.

        """
        outcome = self._terminal.input.insert_text(
            terminal_models.input.TextInsertRequest(
                terminal_models.values.WindowId(journey.window_id),
                text,
                terminal_models.input.TextInputMode.PASTE,
            ),
        )
        if not outcome.succeeded:
            message = f"terminal draft was not inserted: {outcome.reason}"
            raise AssertionError(
                message,
            )

    def use_visual_editor_mode(self, journey: references.SessionJourneyRef) -> None:
        """Send Escape and the visual-mode key to the terminal.

        Raises:
            AssertionError: If either key cannot be delivered.

        """
        window_id = terminal_models.values.WindowId(journey.window_id)
        for key in ("escape", "v"):
            outcome = self._terminal.input.send_key(terminal_models.input.KeySendRequest(window_id, key))
            if not outcome.succeeded:
                message = f"terminal editor mode key was not delivered: {outcome.reason}"
                raise AssertionError(
                    message,
                )

    def interrupt_from_terminal(self, journey: references.SessionJourneyRef) -> None:
        """Press Escape twice without an HTTP control.

        The first event can leave the composer input mode. The second event
        then reaches the active-turn interrupt binding.

        Raises:
            AssertionError: If either Escape key cannot be delivered.

        """
        for _ in range(2):
            outcome = self._terminal.input.send_key(
                terminal_models.input.KeySendRequest(terminal_models.values.WindowId(journey.window_id), "escape"),
            )
            if not outcome.succeeded:
                message = f"terminal interrupt was not delivered: {outcome.reason}"
                raise AssertionError(
                    message,
                )


class _JourneyTerminalLaunch(_JourneyTerminalInput):
    """Find and launch terminal windows."""

    def _located_terminal_window(self, session: sdk_client.SessionRef) -> terminal_models.values.WindowId | None:
        state = self._client.preferences.session_state(session).terminal
        if state.window_id is None:
            return None
        return terminal_models.values.WindowId(state.window_id)

    def _open_terminal(
        self,
        spec: references.SessionSpec,
        prompt: str,
        resume: sdk_client.SessionRef | None,
    ) -> terminal_models.values.WindowId:
        harness = HarnessName(spec.harness)
        runtime = self._runtime_configs.for_harness(harness)
        if spec.account_id is not None:
            msg = f"{spec.harness} has no account switcher"
            raise AssertionError(msg)
        environment = journey_launch.launch_environment(
            harness,
            runtime,
            self._application_port,
            self._launch_environment,
        )
        opened = self._terminal.tabs.open_tab(
            terminal_launch.launch_tab_request(
                spec.workspace or self._workspace,
                journey_launch.reusable_shell_command(
                    (
                        runtime.executable,
                        *journey_launch.launch_arguments(harness, spec, resume, self._workspace, prompt),
                    ),
                ),
                title=("Claude Code" if harness == CLAUDE_CODE_HARNESS else "Codex"),
                environment=environment,
            ),
        )
        if not opened.succeeded or opened.window_id is None:
            msg = f"terminal launch failed: {opened.reason}"
            raise AssertionError(msg)
        return opened.window_id

    def _terminal_window(self, session: sdk_client.SessionRef) -> terminal_models.values.WindowId:
        return sdk_client.wait_for(
            f"session {session.session_id!r} terminal window",
            partial(self._located_terminal_window, session),
            timeout=self._wait_policy.feed,
        )


class _JourneyUnattended(_JourneyTerminalLaunch):
    """Run a harness without an owned terminal window."""

    def run_unattended_with_inherited_window(
        self,
        spec: references.SessionSpec,
        host: references.SessionJourneyRef,
        prompt: str,
    ) -> sdk_client.SessionRef:
        """Run a real non-interactive harness with a copied terminal variable.

        This is an invalid ownership claim but a valid process environment:
        commands started by an agent inherit the host's KITTY_WINDOW_ID. The
        harness must still run and report its native session. Baqylau must not
        treat that copied value as proof that the process owns the host tab.

        Returns:
            The native session reference after the unattended process has finished.

        """
        workspace = spec.workspace or self._workspace
        environment = self._unattended_environment(host)
        command = self._unattended_command(spec, prompt, workspace)
        completed = subprocess.run(  # noqa: S603 -- Pass test prompts as separate arguments to the selected harness, without a shell.
            command,
            cwd=workspace,
            env=environment,
            text=True,
            capture_output=True,
            timeout=self._wait_policy.cleanup,
            check=False,
        )
        self._assert_unattended_success(spec, completed)
        session = sdk_client.SessionRef(journey_launch.unattended_session_id(spec.harness, completed.stdout))
        self._client.sessions.wait_until_finished(session, self._wait_policy.cleanup)
        return session

    def _unattended_environment(self, host: references.SessionJourneyRef) -> dict[str, str]:
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
            "CLAUDE_CONFIG_DIR",
        ):
            environment.pop(name, None)
        environment.update(
            {launch_variable.name: launch_variable.content for launch_variable in self._launch_environment},
        )
        environment["BAQYLAU_DASHBOARD_PORT"] = str(self._application_port)
        environment["KITTY_WINDOW_ID"] = host.window_id
        return environment

    def _unattended_command(
        self,
        spec: references.SessionSpec,
        prompt: str,
        workspace: str,
    ) -> tuple[str, ...]:
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
            message = f"unattended execution is not defined for {spec.harness!r}"
            raise AssertionError(message)
        return command

    def _assert_unattended_success(
        self,
        spec: references.SessionSpec,
        completed: subprocess.CompletedProcess[str],
    ) -> None:
        harness = spec.harness
        return_code = completed.returncode
        error_text = completed.stderr.strip()
        message = f"unattended {harness} exited with {return_code}: {error_text}"
        assert completed.returncode == 0, message


class _JourneyFlow(_JourneyUnattended):
    """Start, continue, and resume session journeys."""

    def start(
        self,
        spec: references.SessionSpec,
        origin: references.JourneyOrigin,
        prompt: str,
    ) -> journey_models.JourneyTurn:
        """Launch a session from the dashboard or terminal.

        Returns:
            The session journey and its observed first turn.

        """
        workspace = spec.workspace or self._workspace
        known = frozenset(
            session_summary.session.session_id for session_summary in self._client.sessions.list().sessions
        )
        if origin == references.JourneyOrigin.DASHBOARD:
            launch = self._client.sessions.launch(
                sdk_client.SessionLaunchRequest(
                    spec.harness,
                    workspace=workspace,
                    prompt=prompt,
                    model=spec.model,
                    effort=spec.effort,
                    account_id=spec.account_id,
                ),
            )
            window_id = terminal_models.values.WindowId(launch.window_id)
        else:
            window_id = self._open_terminal(spec, prompt, None)
            launch = sdk_client.LaunchRef(spec.harness, workspace, str(window_id), known)
        self._windows.add(window_id)
        session = self._client.sessions.wait_for_session(
            launch,
            self._wait_policy.session_announcement,
        )
        return journey_models.JourneyTurn(
            references.SessionJourneyRef(session, origin, str(window_id)),
            selector_turns.launched_turn(
                self._client.sessions.watch(session),
                self._wait_policy.feed,
            ),
        )

    def continue_session(
        self,
        journey: references.SessionJourneyRef,
        origin: references.JourneyOrigin,
        prompt: str,
    ) -> journey_models.JourneyTurn:
        """Send a new prompt to an existing session.

        Returns:
            The existing journey and a reference for the new turn.

        """
        before = self._client.sessions.snapshot(journey.session)
        lead = before.lead()
        cursor_before = self._continuation_cursor(journey, origin, prompt, before)
        turn = references.TurnRef(
            journey.session,
            prompt,
            cursor_before,
            lead.statistics.prompt_count + 1,
            actor_id=lead.actor_id,
        )
        return journey_models.JourneyTurn(
            references.SessionJourneyRef(
                journey.session,
                journey.origin,
                journey.window_id,
            ),
            turn,
        )

    def start_new_native_session(
        self,
        journey: references.SessionJourneyRef,
        prompt: str,
    ) -> journey_models.JourneyTurn:
        """Use native `/new` and send the first prompt in the same host tab.

        Returns:
            The new session journey and its observed first turn.

        """
        before = self._client.sessions.snapshot(journey.session)
        known = frozenset(
            session_summary.session.session_id for session_summary in self._client.sessions.list().sessions
        )
        self.submit_native_command(journey, "/new")
        self.submit_native_command(journey, prompt)
        session_candidates = wait_states.SessionCandidates()

        session = sdk_client.wait_for(
            lambda: (
                f"native /new in window {journey.window_id!r} to announce one session; "
                f"found {session_candidates.session_ids}"
            ),
            partial(
                self._announced_session,
                journey,
                before,
                known,
                session_candidates,
            ),
            timeout=self._wait_policy.session_announcement,
        )
        turn = selector_turns.turn(
            self._client.sessions.watch(session),
            references.TurnRef(
                session=session,
                prompt=prompt,
                cursor_before=0,
                expected_prompt_count=1,
            ),
            self._wait_policy.feed,
        )
        return journey_models.JourneyTurn(
            references.SessionJourneyRef(session, references.JourneyOrigin.TERMINAL, journey.window_id),
            turn,
        )

    def resume(
        self,
        journey: references.SessionJourneyRef,
        origin: references.JourneyOrigin,
        prompt: str,
    ) -> journey_models.ResumedJourney:
        """Resume a stored session from the dashboard or terminal.

        Returns:
            The resumed journey with its new window and continuation details.

        """
        source = journey.session
        prepared = self._resume.prepare(source)
        spec = prepared.spec
        if origin == references.JourneyOrigin.DASHBOARD:
            window_id = terminal_models.values.WindowId(
                self._client.sessions.launch(
                    sdk_client.SessionLaunchRequest(
                        spec.harness,
                        workspace=spec.workspace or self._workspace,
                        prompt=prompt,
                        model=spec.model,
                        effort=spec.effort,
                        account_id=spec.account_id,
                        resume_session_id=source.session_id,
                    ),
                ).window_id,
            )
        else:
            window_id = self._open_terminal(spec, prompt, source)
        self._windows.add(window_id)
        completed = self._resume.complete(prepared, prompt)
        return journey_models.ResumedJourney(
            references.SessionJourneyRef(completed.turn.session, origin, str(window_id)),
            completed.continuation,
            completed.turn,
        )

    def _continuation_cursor(
        self,
        journey: references.SessionJourneyRef,
        origin: references.JourneyOrigin,
        prompt: str,
        before: sdk_state.SessionSnapshot,
    ) -> int:
        if origin == references.JourneyOrigin.DASHBOARD:
            receipt = self._client.sessions.send(journey.session, prompt)
            message = f"dashboard continuation was not accepted: {receipt.outcome}"
            assert receipt.status_code == HTTPStatus.OK, message
            assert receipt.outcome.status in {"sent", "queued"}, message
            return receipt.cursor_before
        window_id = self._terminal_window(journey.session)
        outcome = self._terminal.input.submit_text(
            terminal_models.input.TextSubmitRequest(
                window_id,
                prompt,
                terminal_models.input.TextInputMode.PASTE,
            ),
        )
        message = f"terminal continuation was not delivered: {outcome.reason}"
        assert outcome.succeeded, message
        return before.cursor

    def _announced_session(
        self,
        journey: references.SessionJourneyRef,
        before: sdk_state.SessionSnapshot,
        known: frozenset[str],
        session_candidates: wait_states.SessionCandidates,
    ) -> sdk_client.SessionRef | None:
        session_candidates.session_ids = [
            session_summary.session.session_id
            for session_summary in self._client.sessions.list().sessions
            if session_summary.session.session_id not in known
            and session_summary.session.harness == before.session_data.session.harness
            and session_summary.session.working_directory == before.session_data.session.working_directory
        ]
        message = (
            f"native /new produced multiple sessions in window {journey.window_id!r}: {session_candidates.session_ids}"
        )
        assert len(session_candidates.session_ids) <= 1, message
        if session_candidates.session_ids:
            return sdk_client.SessionRef(session_candidates.session_ids[0])
        retry = self._terminal.input.send_key(
            terminal_models.input.KeySendRequest(
                terminal_models.values.WindowId(journey.window_id),
                "enter",
            ),
        )
        if not retry.succeeded:
            message = f"native /new prompt was not submitted: {retry.reason}"
            raise AssertionError(message)
        return None


class JourneyDriver(_JourneyFlow):
    """Represent journey driver."""
