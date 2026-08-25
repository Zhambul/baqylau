"""Start a deterministic dashboard daemon for Playwright."""

from __future__ import annotations

import os
import socket
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("BAQYLAU_E2E_PORT", "8794"))


def _seed(data_directory: Path, port: int) -> dict[Any, Any]:
    os.environ["BAQYLAU_DATA_DIR"] = str(data_directory)
    os.environ["BAQYLAU_DASHBOARD_PORT"] = str(port)
    os.environ["BAQYLAU_DASHBOARD_NOTIFY_TELEGRAM"] = "0"
    os.environ["BAQYLAU_DASHBOARD_NOTIFY_WEBPUSH"] = "0"

    sys.path.insert(0, str(REPOSITORY_ROOT))

    from app import providers
    from app.injection import registry, resolve
    from core.repository import RepositoryQueries, RepositoryStatus
    from domain.events import (
        ActorAssignmentStarted,
        ActorDescriptionChanged,
        ActorFinished,
        ActorStarted,
        CanonicalEvent,
        ContextReported,
        EffortChanged,
        FileAccessed,
        GoalChanged,
        MessageCreated,
        ModelChanged,
        QuestionAnswered,
        QuestionAsked,
        SearchPerformed,
        SessionFinished,
        SessionStarted,
        SessionTitleChanged,
        ShellFinished,
        ShellOutputFinished,
        ShellProgressed,
        ShellStarted,
        TaskChanged,
        TaskListChanged,
        TurnFinished,
        TurnStarted,
        UsageReported,
    )
    from domain.ids import (
        AccountId,
        ActorId,
        AssignmentId,
        AttentionId,
        CanonicalEventId,
        HarnessName,
        MessageId,
        QuestionId,
        RawEventId,
        RequestId,
        SessionId,
        ShellId,
        TaskId,
        TaskListId,
        TurnId,
        WindowId,
    )
    from domain.records import RecordedTranslationDecision
    from domain.workspace import QueuedMessage
    from domain.values import (
        AccountReference,
        ActorRole,
        AttentionAnswer,
        AttentionChoice,
        AttentionPrompt,
        EffortChangeReason,
        ExecutionMode,
        FileAction,
        GoalState,
        MediaType,
        MessagePhase,
        MessageRole,
        ModelChangeReason,
        ModelReference,
        Outcome,
        OutputMode,
        ProgressStream,
        TaskState,
        TextContent,
        TitleOrigin,
        TokenUsage,
        UsageScope,
    )
    from harness.models import RawEvent, Session, TranslationResult
    from fake_terminal import FakeTerminal, window
    from terminal.models import SESSION_WINDOW_TAG

    class FixtureRepositoryQueries(RepositoryQueries):
        """Keep the browser fixture independent of the source checkout."""

        @classmethod
        def status(cls, working_directory: str) -> RepositoryStatus | None:
            del working_directory
            return RepositoryStatus("main", None, False)

    instances = registry()
    instances[providers.repositories] = FixtureRepositoryQueries()
    now = 1_700_000_000.0
    harness = HarnessName.CODEX
    active_session = SessionId("fixture-active")
    active_lead = ActorId("fixture-active:lead")
    child_actor = ActorId("fixture-active:researcher")
    parked_session = SessionId("fixture-parked")
    parked_lead = ActorId("fixture-parked:lead")
    waiting_session = SessionId("fixture-waiting")
    waiting_lead = ActorId("fixture-waiting:lead")
    waiting_child = ActorId("fixture-waiting:child")
    active_window = WindowId("fixture-active-window")
    waiting_window = WindowId("fixture-waiting-window")
    working_directory = str(REPOSITORY_ROOT)
    fake_terminal = FakeTerminal((
        window(
            active_window,
            tags={SESSION_WINDOW_TAG: str(active_session)},
        ),
        window(
            waiting_window,
            tags={SESSION_WINDOW_TAG: str(waiting_session)},
        ),
    ))
    instances[providers.terminal_plugin.build] = fake_terminal.plugin()  # type: ignore[attr-defined]
    sessions = resolve(instances, providers.sessions)
    raw_events = resolve(instances, providers.raw_events)
    canonical_events = resolve(instances, providers.canonical_events)
    reaction_loop = resolve(instances, providers.reaction_loop)
    workspaces = resolve(instances, providers.workspaces)

    sessions.save(
        harness,
        Session(
            active_session,
            active_lead,
            "fixture.jsonl",
            working_directory,
            terminal_window_id=active_window,
            harness_process_id=os.getpid(),
        ),
    )
    sessions.save(
        harness,
        Session(parked_session, parked_lead, "fixture.jsonl", working_directory),
    )
    sessions.save(
        harness,
        Session(
            waiting_session,
            waiting_lead,
            "fixture.jsonl",
            working_directory,
            terminal_window_id=waiting_window,
        ),
    )

    facts: list[CanonicalEvent] = []

    def add(
        name: str,
        payload: object,
        *,
        session_id: SessionId = active_session,
        actor_id: ActorId = active_lead,
        parent_actor_id: ActorId | None = None,
        turn_id: TurnId | None = None,
        seconds_ago: float = 0,
    ) -> None:
        facts.append(
            CanonicalEvent(
                event_id=CanonicalEventId("browser-fixture:" + name),
                session_id=session_id,
                actor_id=actor_id,
                turn_id=turn_id,
                parent_actor_id=parent_actor_id,
                harness=harness,
                occurred_at=now - seconds_ago,
                terminal_window_id=None,
                harness_process_id=None,
                payload=payload,
            )
        )

    model = ModelReference("gpt-5.6-sol", "gpt-5.6-sol")
    account = AccountReference(AccountId("fixture-account"), "Fixture Account")
    turn = TurnId("fixture-turn")
    add(
        "active-started",
        SessionStarted(
            working_directory,
            "fixture.jsonl",
            None,
            "Frontend parity work",
            model,
            "high",
            account,
        ),
        seconds_ago=900,
    )
    add(
        "active-lead",
        ActorStarted("Codex", ActorRole.LEAD),
        seconds_ago=900,
    )
    add(
        "active-model",
        ModelChanged(None, model, ModelChangeReason.REPORTED_BY_HARNESS),
        seconds_ago=899,
    )
    add(
        "active-effort",
        EffortChanged(None, "high", EffortChangeReason.REPORTED_BY_HARNESS),
        seconds_ago=899,
    )
    add(
        "active-goal",
        GoalChanged("Preserve the dashboard design", GoalState.ACTIVE, None),
        seconds_ago=880,
    )
    task = TaskId("task-frontend")
    add(
        "active-task",
        TaskChanged(
            task,
            "Rewrite the frontend",
            "Keep every existing behavior and visual state.",
            TaskState.IN_PROGRESS,
            active_lead,
        ),
        seconds_ago=875,
    )
    add(
        "active-task-list",
        TaskListChanged(TaskListId("fixture-tasks"), (task,)),
        seconds_ago=874,
    )
    prompt = MessageId("fixture-prompt")
    answer = MessageId("fixture-answer")
    add(
        "active-prompt",
        MessageCreated(
            prompt,
            MessageRole.USER,
            TextContent("Check the current frontend and preserve its design."),
            MessagePhase.PROMPT,
            None,
        ),
        turn_id=turn,
        seconds_ago=840,
    )
    add(
        "active-turn-started",
        TurnStarted(prompt),
        turn_id=turn,
        seconds_ago=839,
    )
    add(
        "active-answer",
        MessageCreated(
            answer,
            MessageRole.ASSISTANT,
            TextContent(
                "The rewrite uses **Svelte 5** with strict TypeScript and keeps the existing CSS.",
                MediaType.TEXT_MARKDOWN,
            ),
            MessagePhase.END_TURN,
            None,
        ),
        turn_id=turn,
        seconds_ago=700,
    )
    add(
        "active-file",
        FileAccessed(
            "dashboard/frontend/src/app/App.svelte",
            FileAction.UPDATED,
            Outcome.SUCCEEDED,
            previous_path=None,
            line_start=None,
            line_end=None,
            lines_added=24,
            lines_removed=7,
            unified_diff="@@ -1 +1 @@\n-old shell\n+typed shell\n",
            content=None,
        ),
        turn_id=turn,
        seconds_ago=680,
    )
    long_command = (
        "python -m baqylau.audit --configuration "
        + "/a-very-long-directory-name/" * 8
        + "settings.toml --include every-frontend-operation"
    )
    foreground = ShellId("fixture-long-command")
    add(
        "active-long-command",
        ShellStarted(
            foreground,
            TextContent(long_command),
            ExecutionMode.FOREGROUND,
            None,
        ),
        turn_id=turn,
        seconds_ago=675,
    )
    add(
        "active-long-command-finished",
        ShellFinished(foreground, Outcome.SUCCEEDED, TextContent("done"), 0),
        turn_id=turn,
        seconds_ago=674,
    )
    add(
        "active-web-search",
        SearchPerformed(
            "WebSearch",
            TextContent("Svelte operation label contrast"),
            TextContent("one result"),
            Outcome.SUCCEEDED,
        ),
        turn_id=turn,
        seconds_ago=673,
    )
    background = ShellId("fixture-background")
    add(
        "active-background",
        ShellStarted(
            background,
            TextContent("python -m baqylau.worker --watch"),
            ExecutionMode.BACKGROUND,
            "frontend worker",
        ),
        turn_id=turn,
        seconds_ago=672,
    )
    add(
        "active-background-launched",
        ShellFinished(background, Outcome.SUCCEEDED, None, 0),
        turn_id=turn,
        seconds_ago=671,
    )
    add(
        "active-background-finished",
        ShellOutputFinished(background, Outcome.SUCCEEDED),
        turn_id=turn,
        seconds_ago=670,
    )
    shell = ShellId("fixture-monitor")
    add(
        "active-shell",
        ShellStarted(
            shell,
            TextContent("npm run check -- --watch"),
            ExecutionMode.MONITOR,
            "frontend type checks",
        ),
        turn_id=turn,
        seconds_ago=650,
    )
    add(
        "active-shell-output",
        ShellProgressed(
            shell,
            1,
            ProgressStream.STATUS,
            TextContent("watching for changes"),
            OutputMode.REPLACE,
        ),
        turn_id=turn,
        seconds_ago=640,
    )
    add(
        "active-context",
        ContextReported(82_000, 200_000, model),
        seconds_ago=620,
    )
    add(
        "active-usage",
        UsageReported(
            UsageScope.ACTOR,
            str(active_lead),
            model,
            account,
            TokenUsage(
                input_tokens=42_000,
                output_tokens=8_500,
                cache_read_tokens=30_000,
            ),
            True,
            Decimal("0.42"),
        ),
        seconds_ago=610,
    )
    add(
        "child-started",
        ActorStarted("researcher", ActorRole.CHILD),
        actor_id=child_actor,
        parent_actor_id=active_lead,
        seconds_ago=540,
    )
    add(
        "child-description",
        ActorDescriptionChanged("Audit the old router"),
        actor_id=child_actor,
        parent_actor_id=active_lead,
        seconds_ago=539,
    )
    add(
        "child-model",
        ModelChanged(None, model, ModelChangeReason.REPORTED_BY_HARNESS),
        actor_id=child_actor,
        parent_actor_id=active_lead,
        seconds_ago=538,
    )
    add(
        "child-context",
        ContextReported(35_000, 200_000, model),
        actor_id=child_actor,
        parent_actor_id=active_lead,
        seconds_ago=520,
    )
    add(
        "child-message",
        MessageCreated(
            MessageId("child-message"),
            MessageRole.ASSISTANT,
            TextContent("The router has eleven route shapes and scoped drill-downs."),
            MessagePhase.INTERMEDIATE,
            None,
        ),
        actor_id=child_actor,
        parent_actor_id=active_lead,
        seconds_ago=500,
    )
    add(
        "child-finished",
        ActorFinished(None),
        actor_id=child_actor,
        parent_actor_id=active_lead,
        seconds_ago=490,
    )
    answered_attention = AttentionId("fixture-answered-attention")
    answered_questions = (
        AttentionPrompt(
            QuestionId("0"),
            None,
            "Which incidents do I close to Done?",
            False,
            (AttentionChoice("All 120"), AttentionChoice("Only my 80")),
        ),
        AttentionPrompt(
            QuestionId("1"),
            None,
            "Add a comment on each closed incident?",
            False,
            (AttentionChoice("No comment"), AttentionChoice("Add a short note")),
        ),
    )
    add(
        "answered-question-asked",
        QuestionAsked(answered_attention, answered_questions),
        turn_id=turn,
        seconds_ago=80,
    )
    add(
        "answered-question-resolved",
        QuestionAnswered(
            answered_attention,
            (
                AttentionAnswer(QuestionId("0"), ("All 120",)),
                AttentionAnswer(QuestionId("1"), ("No comment",)),
            ),
            None,
        ),
        turn_id=turn,
        seconds_ago=70,
    )

    question = QuestionId("fixture-question")
    add(
        "active-question",
        QuestionAsked(
            AttentionId("fixture-attention"),
            (
                AttentionPrompt(
                    question,
                    "Migration mode",
                    "How should the old entry be retired?",
                    False,
                    (
                        AttentionChoice("One served entry", "Switch in one branch."),
                        AttentionChoice("Dual entry", "Keep both entries for a time."),
                    ),
                ),
            ),
        ),
        turn_id=turn,
        seconds_ago=60,
    )

    waiting_turn = TurnId("waiting-turn")
    add(
        "waiting-started",
        SessionStarted(
            working_directory,
            "fixture.jsonl",
            None,
            "Waiting for subagent",
            model,
            "low",
            None,
        ),
        session_id=waiting_session,
        actor_id=waiting_lead,
        seconds_ago=120,
    )
    add(
        "waiting-lead",
        ActorStarted("Claude", ActorRole.LEAD),
        session_id=waiting_session,
        actor_id=waiting_lead,
        seconds_ago=119,
    )
    add(
        "waiting-title",
        SessionTitleChanged("Waiting for subagent", TitleOrigin.AUTOMATIC),
        session_id=waiting_session,
        actor_id=waiting_lead,
        seconds_ago=118.5,
    )
    add(
        "waiting-assignment",
        ActorAssignmentStarted(
            AssignmentId("fixture-running-assignment"),
            TextContent("Verify the result"),
            "Verifier",
            TextContent("Run the verification"),
        ),
        session_id=waiting_session,
        actor_id=waiting_lead,
        turn_id=waiting_turn,
        seconds_ago=118,
    )
    add(
        "waiting-child",
        ActorStarted("Verifier", ActorRole.CHILD),
        session_id=waiting_session,
        actor_id=waiting_child,
        parent_actor_id=waiting_lead,
        seconds_ago=117,
    )
    add(
        "waiting-turn-finished",
        TurnFinished(None, Outcome.SUCCEEDED),
        session_id=waiting_session,
        actor_id=waiting_lead,
        turn_id=waiting_turn,
        seconds_ago=116,
    )

    parked_turn = TurnId("parked-turn")
    add(
        "parked-started",
        SessionStarted(
            working_directory,
            "fixture.jsonl",
            None,
            "Finished migration research",
            model,
            "medium",
            None,
        ),
        session_id=parked_session,
        actor_id=parked_lead,
        seconds_ago=7_200,
    )
    add(
        "parked-lead",
        ActorStarted("Codex", ActorRole.LEAD),
        session_id=parked_session,
        actor_id=parked_lead,
        seconds_ago=7_200,
    )
    add(
        "parked-title",
        SessionTitleChanged("Finished migration research", TitleOrigin.AUTOMATIC),
        session_id=parked_session,
        actor_id=parked_lead,
        seconds_ago=7_199,
    )
    add(
        "parked-message",
        MessageCreated(
            MessageId("parked-message"),
            MessageRole.ASSISTANT,
            TextContent("The implementation map is complete."),
            MessagePhase.END_TURN,
            None,
        ),
        session_id=parked_session,
        actor_id=parked_lead,
        turn_id=parked_turn,
        seconds_ago=7_000,
    )
    add(
        "parked-turn-finished",
        TurnFinished(MessageId("parked-message"), Outcome.SUCCEEDED),
        session_id=parked_session,
        actor_id=parked_lead,
        turn_id=parked_turn,
        seconds_ago=6_999,
    )
    add(
        "parked-finished",
        SessionFinished(Outcome.SUCCEEDED, None),
        session_id=parked_session,
        actor_id=parked_lead,
        seconds_ago=6_998,
    )

    for index, fact in enumerate(facts):
        raw = RawEvent(
            raw_event_id=RawEventId(f"browser-fixture:{index}"),
            harness=harness,
            source_type="fixture",
            source_name="browser-fixture",
            source_position=str(index),
            session_id=fact.session_id,
            actor_id=fact.actor_id,
            parent_actor_id=fact.parent_actor_id,
            observed_at=fact.occurred_at or now,
            encoding="json",
            payload=b"{}",
        )
        raw_events.record((raw,))
        canonical_events.record_translation(
            raw,
            "browser-fixture-1",
            TranslationResult(
                (fact,), RecordedTranslationDecision.TRANSLATED
            ),
            now,
        )
    reaction_loop.tick()
    workspaces.enqueue_composer_message(
        active_session,
        QueuedMessage(
            RequestId("browser-fixture-queued"),
            "show this complete queued message",
        ),
        "send",
    )
    return instances


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="baqylau-browser-") as temporary:
        bound_socket = socket.create_server(("127.0.0.1", PORT))
        address = bound_socket.getsockname()
        port = int(address[1])
        data_directory = Path(temporary)
        instances = _seed(data_directory, port)
        from api import dependencies
        from api.app import build_web_application
        from api.server import build_server
        from app.injection import resolve

        policy = resolve(instances, dependencies.policy)
        bound_socket.listen(policy.request_queue_size)
        server = build_server(
            build_web_application(instances, run_background_workers=False),
            policy.graceful_shutdown_seconds,
        )
        print(f"BAQYLAU_FIXTURE_URL=http://127.0.0.1:{port}", flush=True)
        server.run(sockets=[bound_socket])
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
