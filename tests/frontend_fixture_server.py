# Copyright (c) 2026 Zhambyl Yermagambet
"""Start a deterministic dashboard daemon for Playwright."""

from __future__ import annotations

from typing import Any, Unpack

from domain import event_base, ids as domain_ids
from tests import (
    frontend_fixture_conversation as conversation,
    frontend_fixture_host,
    frontend_fixture_operations as operations,
    frontend_fixture_recording as recording,
    frontend_fixture_system as system,
    frontend_fixture_values as fixture,
)
from tests.frontend_fixture_support import FixtureEventArguments, FixturePhaseContext, FixtureRepositoryQueries

REPOSITORY_ROOT = system.Path(__file__).resolve().parents[1]
PORT = int(system.os.environ.get("BAQYLAU_E2E_PORT", "8794"))
INITIAL_SOURCE_POSITION = "0"
FIXTURE_SOURCE_FILE = "fixture.jsonl"


class _FixtureEvents:
    """Build canonical events for the browser fixture."""

    def __init__(
        self,
        events: list[event_base.CanonicalEvent],
        harness: domain_ids.HarnessName,
        event_time: float,
        active_session: domain_ids.SessionId,
        active_actor: domain_ids.ActorId,
    ) -> None:
        self._events = events
        self._harness = harness
        self._event_time = event_time
        self._active_session = active_session
        self._active_actor = active_actor

    def add(
        self,
        name: str,
        payload: event_base.EventPayload,
        **arguments: Unpack[FixtureEventArguments],
    ) -> None:
        self._events.append(
            event_base.CanonicalEvent(
                event_id=domain_ids.CanonicalEventId(f"browser-fixture:{name}"),
                session_id=arguments.get("session_id") or self._active_session,
                actor_id=arguments.get("actor_id") or self._active_actor,
                turn_id=arguments.get("turn_id"),
                parent_actor_id=arguments.get("parent_actor_id"),
                harness=self._harness,
                occurred_at=self._event_time - arguments.get("seconds_ago", 0),
                terminal_window_id=None,
                harness_process_id=None,
                payload=payload,
            ),
        )


class _FixtureFactPhases(FixturePhaseContext):
    """Build deterministic fact groups for the browser fixture."""

    def _build_active_facts(self) -> None:
        """Build active session and task facts."""
        self._model = conversation.references.ModelReference("gpt-5.6-sol", "gpt-5.6-sol")
        self._account = conversation.references.AccountReference(
            domain_ids.AccountId("fixture-account"), "Fixture Account",
        )
        self._turn = domain_ids.TurnId("fixture-turn")
        self._events.add(
            "active-started",
            conversation.event_session.SessionStarted(
                self._working_directory,
                FIXTURE_SOURCE_FILE,
                None,
                "Frontend parity work",
                self._model,
                "high",
                self._account,
            ),
            seconds_ago=fixture.ACTIVE_SESSION_START_AGE_SECONDS,
        )
        self._events.add(
            "active-lead",
            conversation.event_actor.ActorStarted("Codex", conversation.messaging.ActorRole.LEAD),
            seconds_ago=fixture.ACTIVE_SESSION_START_AGE_SECONDS,
        )
        self._events.add(
            "active-model",
            conversation.event_session.ModelChanged(
                None, self._model, conversation.work_state.ModelChangeReason.REPORTED_BY_HARNESS,
            ),
            seconds_ago=fixture.ACTIVE_CONFIGURATION_AGE_SECONDS,
        )
        self._events.add(
            "active-effort",
            conversation.event_session.EffortChanged(
                None, "high", conversation.work_state.EffortChangeReason.REPORTED_BY_HARNESS,
            ),
            seconds_ago=fixture.ACTIVE_CONFIGURATION_AGE_SECONDS,
        )
        self._events.add(
            "active-goal",
            operations.event_work.GoalChanged(
                "Preserve the dashboard design", conversation.work_state.GoalState.ACTIVE, None,
            ),
            seconds_ago=fixture.ACTIVE_GOAL_AGE_SECONDS,
        )
        self._task = domain_ids.TaskId("task-frontend")
        self._events.add(
            "active-task",
            operations.event_work.TaskChanged(
                self._task,
                "Rewrite the frontend",
                "Keep every existing behavior and visual state.",
                conversation.work_state.TaskState.IN_PROGRESS,
                self._active_lead,
            ),
            seconds_ago=fixture.ACTIVE_TASK_AGE_SECONDS,
        )
        self._events.add(
            "active-task-list",
            operations.event_work.TaskListChanged(domain_ids.TaskListId("fixture-tasks"), (self._task,)),
            seconds_ago=fixture.ACTIVE_TASK_LIST_AGE_SECONDS,
        )
        self._prompt = domain_ids.MessageId("fixture-prompt")
        self._answer = domain_ids.MessageId("fixture-answer")
        self._events.add(
            "active-prompt",
            conversation.event_conversation.MessageCreated(
                self._prompt,
                conversation.messaging.MessageRole.USER,
                conversation.content.TextContent("Check the current frontend and preserve its design."),
                conversation.messaging.MessagePhase.PROMPT,
                None,
            ),
            turn_id=self._turn,
            seconds_ago=fixture.ACTIVE_PROMPT_AGE_SECONDS,
        )

    def _build_resource_facts(self) -> None:
        """Build conversation and resource facts."""
        self._events.add(
            "active-turn-started",
            conversation.event_conversation.TurnStarted(self._prompt),
            turn_id=self._turn,
            seconds_ago=fixture.ACTIVE_TURN_START_AGE_SECONDS,
        )
        self._events.add(
            "active-answer",
            conversation.event_conversation.MessageCreated(
                self._answer,
                conversation.messaging.MessageRole.ASSISTANT,
                conversation.content.TextContent(
                    "The rewrite uses **Svelte 5** with strict TypeScript and keeps the existing CSS.",
                    conversation.content.MediaType.TEXT_MARKDOWN,
                ),
                conversation.messaging.MessagePhase.END_TURN,
                None,
            ),
            turn_id=self._turn,
            seconds_ago=fixture.ACTIVE_ANSWER_AGE_SECONDS,
        )
        self._events.add(
            "active-file",
            operations.event_resource.FileAccessed(
                "dashboard/frontend/src/app/App.svelte",
                operations.outcomes.FileAction.UPDATED,
                operations.outcomes.Outcome.SUCCEEDED,
                previous_path=None,
                line_start=None,
                line_end=None,
                lines_added=24,
                lines_removed=7,
                unified_diff="@@ -1 +1 @@\n-old shell\n+typed shell\n",
                content=None,
            ),
            turn_id=self._turn,
            seconds_ago=fixture.ACTIVE_FILE_AGE_SECONDS,
        )

        self._long_command = (
            "python -m baqylau.audit --configuration "
            + "/a-very-long-directory-name/" * 8
            + "settings.toml --include every-frontend-operation"
        )
        self._foreground = domain_ids.ShellId("fixture-long-command")
        self._events.add(
            "active-long-command",
            operations.event_shell.ShellStarted(
                self._foreground,
                conversation.content.TextContent(self._long_command),
                operations.outcomes.ExecutionMode.FOREGROUND,
                None,
            ),
            turn_id=self._turn,
            seconds_ago=fixture.ACTIVE_SHELL_START_AGE_SECONDS,
        )
        self._events.add(
            "active-long-command-finished",
            operations.event_shell.ShellFinished(
                self._foreground,
                operations.outcomes.Outcome.SUCCEEDED,
                conversation.content.TextContent("done"),
                0,
            ),
            turn_id=self._turn,
            seconds_ago=fixture.ACTIVE_SHELL_FINISH_AGE_SECONDS,
        )
        self._events.add(
            "active-web-search",
            operations.event_resource.SearchPerformed(
                "WebSearch",
                conversation.content.TextContent("Svelte operation label contrast"),
                conversation.content.TextContent("one result"),
                operations.outcomes.Outcome.SUCCEEDED,
            ),
            turn_id=self._turn,
            seconds_ago=fixture.ACTIVE_WEB_SEARCH_AGE_SECONDS,
        )
        self._events.add(
            "active-tool-search",
            operations.event_resource.SearchPerformed(
                "ToolSearch",
                conversation.content.TextContent("select:Monitor,TaskOutput"),
                conversation.content.TextContent("→ loaded tool: Monitor\n→ loaded tool: TaskOutput"),
                operations.outcomes.Outcome.SUCCEEDED,
            ),
            turn_id=self._turn,
            seconds_ago=fixture.ACTIVE_TOOL_SEARCH_AGE_SECONDS,
        )

    def _build_runtime_facts(self) -> None:
        """Build shell and usage facts."""
        self._background = domain_ids.ShellId("fixture-background")
        self._events.add(
            "active-background",
            operations.event_shell.ShellStarted(
                self._background,
                conversation.content.TextContent("python -m baqylau.worker --watch"),
                operations.outcomes.ExecutionMode.BACKGROUND,
                "frontend worker",
            ),
            turn_id=self._turn,
            seconds_ago=fixture.ACTIVE_BACKGROUND_START_AGE_SECONDS,
        )
        self._events.add(
            "active-background-launched",
            operations.event_shell.ShellFinished(self._background, operations.outcomes.Outcome.SUCCEEDED, None, 0),
            turn_id=self._turn,
            seconds_ago=fixture.ACTIVE_BACKGROUND_LAUNCH_AGE_SECONDS,
        )
        self._events.add(
            "active-background-finished",
            operations.event_shell.ShellOutputFinished(self._background, operations.outcomes.Outcome.SUCCEEDED),
            turn_id=self._turn,
            seconds_ago=fixture.ACTIVE_BACKGROUND_FINISH_AGE_SECONDS,
        )
        self._shell = domain_ids.ShellId("fixture-monitor")
        self._events.add(
            "active-shell",
            operations.event_shell.ShellStarted(
                self._shell,
                conversation.content.TextContent("npm run check -- --watch"),
                operations.outcomes.ExecutionMode.MONITOR,
                "frontend type checks",
            ),
            turn_id=self._turn,
            seconds_ago=fixture.ACTIVE_MONITOR_START_AGE_SECONDS,
        )
        self._events.add(
            "active-shell-output",
            operations.event_shell.ShellProgressed(
                self._shell,
                1,
                operations.outcomes.ProgressStream.STATUS,
                conversation.content.TextContent("watching for changes"),
                operations.outcomes.OutputMode.REPLACE,
            ),
            turn_id=self._turn,
            seconds_ago=fixture.ACTIVE_MONITOR_OUTPUT_AGE_SECONDS,
        )
        self._events.add(
            "active-context",
            operations.event_telemetry.ContextReported(
                fixture.ACTIVE_CONTEXT_USED_TOKENS,
                fixture.ACTIVE_CONTEXT_WINDOW_TOKENS,
                self._model,
            ),
            seconds_ago=fixture.ACTIVE_CONTEXT_AGE_SECONDS,
        )
        self._events.add(
            "active-operations.usage",
            operations.event_telemetry.UsageReported(
                scope=operations.usage.UsageScope.ACTOR,
                subject_id=str(self._active_lead),
                model=self._model,
                account=self._account,
                tokens=operations.usage.TokenUsage(
                    input_tokens=fixture.ACTIVE_INPUT_TOKENS,
                    output_tokens=fixture.ACTIVE_OUTPUT_TOKENS,
                    cache_read_tokens=fixture.ACTIVE_CACHE_READ_TOKENS,
                ),
                cumulative=True,
                cost_in_usd=system.Decimal("0.42"),
            ),
            seconds_ago=fixture.ACTIVE_USAGE_AGE_SECONDS,
        )

    def _build_child_facts(self) -> None:
        """Build child actor facts."""
        self._events.add(
            "child-started",
            conversation.event_actor.ActorStarted("researcher", conversation.messaging.ActorRole.CHILD),
            actor_id=self._child_actor,
            parent_actor_id=self._active_lead,
            seconds_ago=fixture.CHILD_START_AGE_SECONDS,
        )
        self._events.add(
            "child-description",
            conversation.event_actor.ActorDescriptionChanged("Audit the old router"),
            actor_id=self._child_actor,
            parent_actor_id=self._active_lead,
            seconds_ago=fixture.CHILD_DESCRIPTION_AGE_SECONDS,
        )
        self._events.add(
            "child-model",
            conversation.event_session.ModelChanged(
                None, self._model, conversation.work_state.ModelChangeReason.REPORTED_BY_HARNESS,
            ),
            actor_id=self._child_actor,
            parent_actor_id=self._active_lead,
            seconds_ago=fixture.CHILD_MODEL_AGE_SECONDS,
        )
        self._events.add(
            "child-context",
            operations.event_telemetry.ContextReported(
                fixture.CHILD_CONTEXT_USED_TOKENS,
                fixture.ACTIVE_CONTEXT_WINDOW_TOKENS,
                self._model,
            ),
            actor_id=self._child_actor,
            parent_actor_id=self._active_lead,
            seconds_ago=fixture.CHILD_CONTEXT_AGE_SECONDS,
        )
        self._events.add(
            "child-message",
            conversation.event_conversation.MessageCreated(
                domain_ids.MessageId("child-message"),
                conversation.messaging.MessageRole.ASSISTANT,
                conversation.content.TextContent("The router has eleven route shapes and scoped drill-downs."),
                conversation.messaging.MessagePhase.INTERMEDIATE,
                None,
            ),
            actor_id=self._child_actor,
            parent_actor_id=self._active_lead,
            seconds_ago=fixture.CHILD_MESSAGE_AGE_SECONDS,
        )
        self._events.add(
            "child-finished",
            conversation.event_actor.ActorFinished(None),
            actor_id=self._child_actor,
            parent_actor_id=self._active_lead,
            seconds_ago=fixture.CHILD_FINISH_AGE_SECONDS,
        )

    def _build_question_facts(self) -> None:
        """Build question facts."""
        self._answered_attention = domain_ids.AttentionId("fixture-answered-attention")
        self._answered_questions = (
            conversation.attention.AttentionPrompt(
                prompt_id=domain_ids.QuestionId(INITIAL_SOURCE_POSITION),
                title=None,
                prompt="Which incidents do I close to Done?",
                multiple=False,
                choices=(
                    conversation.attention.AttentionChoice("All 120"),
                    conversation.attention.AttentionChoice("Only my 80"),
                ),
            ),
            conversation.attention.AttentionPrompt(
                prompt_id=domain_ids.QuestionId("1"),
                title=None,
                prompt="Add a comment on each closed incident?",
                multiple=False,
                choices=(
                    conversation.attention.AttentionChoice("No comment"),
                    conversation.attention.AttentionChoice("Add a short note"),
                ),
            ),
        )
        self._events.add(
            "answered-question-asked",
            operations.event_work.QuestionAsked(self._answered_attention, self._answered_questions),
            turn_id=self._turn,
            seconds_ago=fixture.ANSWERED_QUESTION_AGE_SECONDS,
        )
        self._events.add(
            "answered-question-resolved",
            operations.event_work.QuestionAnswered(
                self._answered_attention,
                (
                    conversation.attention.AttentionAnswer(
                        domain_ids.QuestionId(INITIAL_SOURCE_POSITION), ("All 120",),
                    ),
                    conversation.attention.AttentionAnswer(domain_ids.QuestionId("1"), ("No comment",)),
                ),
                None,
            ),
            turn_id=self._turn,
            seconds_ago=fixture.ANSWERED_RESOLUTION_AGE_SECONDS,
        )

        self._question = domain_ids.QuestionId("fixture-question")
        self._events.add(
            "active-question",
            operations.event_work.QuestionAsked(
                domain_ids.AttentionId("fixture-attention"),
                (
                    conversation.attention.AttentionPrompt(
                        prompt_id=self._question,
                        title="Migration mode",
                        prompt="How should the old entry be retired?",
                        multiple=False,
                        choices=(
                            conversation.attention.AttentionChoice("One served entry", "Switch in one branch."),
                            conversation.attention.AttentionChoice("Dual entry", "Keep both entries for a time."),
                        ),
                    ),
                ),
            ),
            turn_id=self._turn,
            seconds_ago=60,
        )

    def _build_waiting_facts(self) -> None:
        """Build waiting session facts."""
        self._waiting_turn = domain_ids.TurnId("waiting-turn")
        self._events.add(
            "waiting-started",
            conversation.event_session.SessionStarted(
                self._working_directory,
                FIXTURE_SOURCE_FILE,
                None,
                "Waiting for subagent",
                self._model,
                "low",
                None,
            ),
            session_id=self._waiting_session,
            actor_id=self._waiting_lead,
            seconds_ago=fixture.WAITING_SESSION_START_AGE_SECONDS,
        )
        self._events.add(
            "waiting-lead",
            conversation.event_actor.ActorStarted("Claude", conversation.messaging.ActorRole.LEAD),
            session_id=self._waiting_session,
            actor_id=self._waiting_lead,
            seconds_ago=fixture.WAITING_LEAD_START_AGE_SECONDS,
        )
        self._events.add(
            "waiting-title",
            conversation.event_session.SessionTitleChanged(
                "Waiting for subagent", conversation.work_state.TitleOrigin.AUTOMATIC,
            ),
            session_id=self._waiting_session,
            actor_id=self._waiting_lead,
            seconds_ago=fixture.WAITING_TITLE_AGE_SECONDS,
        )
        self._events.add(
            "waiting-assignment",
            conversation.event_actor.ActorAssignmentStarted(
                domain_ids.AssignmentId("fixture-running-assignment"),
                conversation.content.TextContent("Verify the result"),
                "Verifier",
                conversation.content.TextContent("Run the verification"),
            ),
            session_id=self._waiting_session,
            actor_id=self._waiting_lead,
            turn_id=self._waiting_turn,
            seconds_ago=fixture.WAITING_ASSIGNMENT_AGE_SECONDS,
        )
        self._events.add(
            "waiting-child",
            conversation.event_actor.ActorStarted("Verifier", conversation.messaging.ActorRole.CHILD),
            session_id=self._waiting_session,
            actor_id=self._waiting_child,
            parent_actor_id=self._waiting_lead,
            seconds_ago=fixture.WAITING_CHILD_AGE_SECONDS,
        )
        self._events.add(
            "waiting-turn-finished",
            conversation.event_conversation.TurnFinished(None, operations.outcomes.Outcome.SUCCEEDED),
            session_id=self._waiting_session,
            actor_id=self._waiting_lead,
            turn_id=self._waiting_turn,
            seconds_ago=fixture.WAITING_TURN_FINISH_AGE_SECONDS,
        )

    def _build_parked_facts(self) -> None:
        """Build parked session facts."""
        self._parked_turn = domain_ids.TurnId("parked-turn")
        self._events.add(
            "parked-started",
            conversation.event_session.SessionStarted(
                self._working_directory,
                FIXTURE_SOURCE_FILE,
                None,
                "Finished migration research",
                self._model,
                "medium",
                None,
            ),
            session_id=self._parked_session,
            actor_id=self._parked_lead,
            seconds_ago=fixture.PARKED_SESSION_START_AGE_SECONDS,
        )
        self._events.add(
            "parked-lead",
            conversation.event_actor.ActorStarted("Codex", conversation.messaging.ActorRole.LEAD),
            session_id=self._parked_session,
            actor_id=self._parked_lead,
            seconds_ago=fixture.PARKED_SESSION_START_AGE_SECONDS,
        )
        self._events.add(
            "parked-title",
            conversation.event_session.SessionTitleChanged(
                "Finished migration research", conversation.work_state.TitleOrigin.AUTOMATIC,
            ),
            session_id=self._parked_session,
            actor_id=self._parked_lead,
            seconds_ago=fixture.PARKED_TITLE_AGE_SECONDS,
        )
        self._events.add(
            "parked-goal",
            operations.event_work.GoalChanged(
                "Completed goal marker", conversation.work_state.GoalState.COMPLETED, None,
            ),
            session_id=self._parked_session,
            actor_id=self._parked_lead,
        )
        self._events.add(
            "parked-message",
            conversation.event_conversation.MessageCreated(
                domain_ids.MessageId("parked-message"),
                conversation.messaging.MessageRole.ASSISTANT,
                conversation.content.TextContent("The implementation map is complete."),
                conversation.messaging.MessagePhase.END_TURN,
                None,
            ),
            session_id=self._parked_session,
            actor_id=self._parked_lead,
            turn_id=self._parked_turn,
            seconds_ago=fixture.PARKED_MESSAGE_AGE_SECONDS,
        )
        self._events.add(
            "parked-turn-finished",
            conversation.event_conversation.TurnFinished(
                domain_ids.MessageId("parked-message"), operations.outcomes.Outcome.SUCCEEDED,
            ),
            session_id=self._parked_session,
            actor_id=self._parked_lead,
            turn_id=self._parked_turn,
            seconds_ago=fixture.PARKED_TURN_FINISH_AGE_SECONDS,
        )
        self._events.add(
            "parked-finished",
            conversation.event_session.SessionFinished(operations.outcomes.Outcome.SUCCEEDED, None),
            session_id=self._parked_session,
            actor_id=self._parked_lead,
            seconds_ago=fixture.PARKED_SESSION_FINISH_AGE_SECONDS,
        )


class _FixtureObservationPhase(FixturePhaseContext):
    """Build observed browser and compaction facts."""

    def _build_observation_facts(self) -> None:
        """Build browser and compaction facts."""
        self._events.add(
            "active-web-fetch",
            operations.event_resource.WebFetched(
                "https://example.com",
                conversation.content.TextContent("Example Domain page"),
                operations.outcomes.Outcome.SUCCEEDED,
            ),
            turn_id=self._turn,
            seconds_ago=fixture.ACTIVE_WEB_FETCH_AGE_SECONDS,
        )
        self._events.add(
            "active-browser",
            operations.event_resource.BrowserInteracted(
                "Refresh the fixture application",
                conversation.content.TextContent('- banner:\n  - link "baqylau"'),
                operations.outcomes.Outcome.SUCCEEDED,
            ),
            turn_id=self._turn,
            seconds_ago=fixture.ACTIVE_BROWSER_AGE_SECONDS,
        )
        self._events.add(
            "active-compaction",
            operations.event_telemetry.CompactionFinished(
                fixture.ACTIVE_CONTEXT_USED_TOKENS,
                fixture.COMPACTION_RECLAIMED_TOKENS,
                conversation.content.TextContent(
                    "Retained compacted context: amber circle, blue square",
                    conversation.content.MediaType.TEXT_MARKDOWN,
                ),
            ),
            turn_id=self._turn,
            seconds_ago=fixture.ACTIVE_COMPACTION_AGE_SECONDS,
        )


class _FixtureSeed(_FixtureFactPhases, _FixtureObservationPhase):
    """Build and record one deterministic browser fixture."""

    def __init__(self, data_directory: system.Path, port: int) -> None:
        system.os.environ["BAQYLAU_DATA_DIR"] = str(data_directory)
        system.os.environ["BAQYLAU_DASHBOARD_PORT"] = str(port)
        system.os.environ["BAQYLAU_DASHBOARD_NOTIFY_TELEGRAM"] = INITIAL_SOURCE_POSITION
        system.os.environ["BAQYLAU_DASHBOARD_NOTIFY_WEBPUSH"] = INITIAL_SOURCE_POSITION
        system.sys.path.insert(0, str(REPOSITORY_ROOT))

    def run(self) -> dict[Any, Any]:
        """Build and record the fixture application state.

        Returns:
            The provider instances for the fixture application.

        """
        self._initialize_identity()
        self._initialize_runtime()
        self._register_sessions()
        self._build_fixture_facts()
        self._record_fixture_facts()
        return self._instances

    def _build_fixture_facts(self) -> None:
        """Build all fixture fact phases."""
        self._build_active_facts()
        self._build_resource_facts()
        self._build_observation_facts()
        self._build_runtime_facts()
        self._build_child_facts()
        self._build_question_facts()
        self._build_waiting_facts()
        self._build_parked_facts()

    def _initialize_identity(self) -> None:
        """Initialize stable fixture identities."""
        self._now = 1_700_000_000.0
        self._harness = domain_ids.HarnessName.CODEX
        self._active_session = domain_ids.SessionId("fixture-active")
        self._active_lead = domain_ids.ActorId("fixture-active:lead")
        self._child_actor = domain_ids.ActorId("fixture-active:researcher")
        self._parked_session = domain_ids.SessionId("fixture-parked")
        self._parked_lead = domain_ids.ActorId("fixture-parked:lead")
        self._waiting_session = domain_ids.SessionId("fixture-waiting")
        self._waiting_lead = domain_ids.ActorId("fixture-waiting:lead")
        self._waiting_child = domain_ids.ActorId("fixture-waiting:child")
        self._active_window = domain_ids.WindowId("fixture-active-window")
        self._waiting_window = domain_ids.WindowId("fixture-waiting-window")
        self._working_directory = str(REPOSITORY_ROOT)

    def _initialize_runtime(self) -> None:
        """Initialize fixture providers and terminal state."""
        from tests import frontend_fixture_runtime as runtime  # noqa: PLC0415 -- Set the fixture environment first.

        self._instances = runtime.registry()
        self._instances[runtime.provider_runtime.repositories] = FixtureRepositoryQueries()
        self._fake_terminal = runtime.FakeTerminal((
            runtime.window(self._active_window, tags={runtime.SESSION_WINDOW_TAG: str(self._active_session)}),
            runtime.window(self._waiting_window, tags={runtime.SESSION_WINDOW_TAG: str(self._waiting_session)}),
        ))
        self._instances[runtime.provider_runtime.terminal_plugin.build] = self._fake_terminal.plugin()  # type: ignore[attr-defined]
        self._sessions = runtime.resolve(self._instances, runtime.provider_harness_sessions.sessions)
        self._raw_events = runtime.resolve(self._instances, runtime.provider_fact_storage.raw_events)
        self._canonical_events = runtime.resolve(self._instances, runtime.provider_fact_storage.canonical_events)
        self._reaction_loop = runtime.resolve(self._instances, runtime.provider_reaction_loop.reaction_loop)
        self._workspaces = runtime.resolve(self._instances, runtime.provider_session_storage.workspaces)

    def _register_sessions(self) -> None:
        """Register fixture sessions and initialize fact storage."""
        self._sessions.save(
            self._harness,
            recording.Session(
                self._active_session,
                self._active_lead,
                FIXTURE_SOURCE_FILE,
                self._working_directory,
                terminal_window_id=self._active_window,
                harness_process_id=system.os.getpid(),
            ),
        )
        self._sessions.save(
            self._harness,
            recording.Session(
                self._parked_session,
                self._parked_lead,
                FIXTURE_SOURCE_FILE,
                self._working_directory,
            ),
        )
        self._sessions.save(
            self._harness,
            recording.Session(
                self._waiting_session,
                self._waiting_lead,
                FIXTURE_SOURCE_FILE,
                self._working_directory,
                terminal_window_id=self._waiting_window,
            ),
        )

        self._facts: list[event_base.CanonicalEvent] = []
        self._events = _FixtureEvents(
            self._facts,
            self._harness,
            self._now,
            self._active_session,
            self._active_lead,
        )

    def _record_fixture_facts(self) -> None:
        """Record the built facts and initialize the queued composer message."""
        for index, fact in enumerate(self._facts):
            raw = recording.RawEvent(
                raw_event_id=domain_ids.RawEventId(f"browser-fixture:{index}"),
                harness=self._harness,
                source_type="fixture",
                source_name="browser-fixture",
                source_position=str(index),
                session_id=fact.session_id,
                actor_id=fact.actor_id,
                parent_actor_id=fact.parent_actor_id,
                observed_at=fact.occurred_at or self._now,
                encoding="json",
                payload=b"{}",
            )
            self._raw_events.record((raw,))
            self._canonical_events.record_translation(
                raw,
                "browser-fixture-1",
                recording.translated(fact),
                self._now,
            )
        self._reaction_loop.tick()
        self._workspaces.enqueue_composer_message(
            self._active_session,
            recording.composer.QueuedMessage(
                domain_ids.RequestId("browser-fixture-queued"),
                "show this complete queued message",
            ),
            "send",
        )


def _seed(data_directory: system.Path, port: int) -> dict[Any, Any]:
    return _FixtureSeed(data_directory, port).run()


def main() -> int:
    """Run the browser fixture server with a temporary data directory.

    Returns:
        Zero after the server exits.

    """
    with system.tempfile.TemporaryDirectory(prefix="baqylau-browser-") as temporary:
        server, bound_socket, port = frontend_fixture_host.fixture_server(temporary, PORT, _seed)
        system.sys.stdout.write(f"BAQYLAU_FIXTURE_URL=http://127.0.0.1:{port}\n")
        system.sys.stdout.flush()
        server.run(sockets=[bound_socket])
        return 0


if __name__ == "__main__":
    system.sys.exit(main())
