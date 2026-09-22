# Copyright (c) 2026 Zhambyl Yermagambet
"""Translators for raw events our own machinery produces, one per core source type."""

from __future__ import annotations

from typing import override

from domain import (
    event_actor,
    event_conversation,
    event_session,
    event_shell,
    messaging,
    outcomes,
    records,
    shells,
)
from engine.interpret import control_translator
from harness.contract import CoreTranslator
from harness.models import translation_stages as stages
from harness.models.control_observations import SessionRenameObservation
from harness.models.directives import (
    ProcessExit,
    SessionResumeObservation,
)
from harness.models.raw_event_builders import (
    CanonicalEventDraft,
    canonical_event,
    session_run_finished_event,
    session_run_started_events,
)
from harness.models.raw_events import (
    AUTOMATIC_TITLE_SOURCE_TYPE,
    RawEvent,
    TranslationResult,
)
from repository.mapper.documents import decode_document

ControlTranslator = control_translator.ControlTranslator


class ShellOutputTranslator(CoreTranslator):
    """Represent shell output translator.

    Output-location directive (recorded by a gateway) → the typed
        `shell.output_located` fact.

        The directive IS a `event_shell.ShellOutputLocated`, written by
        `harness/models/raw_events.py` and decoded here against that same
        declaration — including its `until` boundary, which is a `ShellFollowUntil`
        enum the mapper checks. Both halves used to be written by hand: a dict built
        from `asdict` at the writer, and eight `document[...]` reads plus a bespoke
        validator for that enum here.
    """

    @override
    def translate(
        self, raw_event: RawEvent, *, translation_stage: stages.TranslationStage = stages.TranslationStage.COMPLETE,
    ) -> TranslationResult:
        """Translate a shell output location.

        Returns:
            The translation result.

        """
        located = decode_document(event_shell.ShellOutputLocated, raw_event.payload)
        source_key = shells.shell_output_source_key(located.source_path)
        return stages.select_result(TranslationResult(
            (
                canonical_event(
                    raw_event,
                    CanonicalEventDraft(
                        "shell",
                        str(located.shell_id),
                        f"output_located:{source_key}",
                        located,
                    ),
                ),
            ),
            records.RecordedTranslationDecision.TRANSLATED,
        ), translation_stage)


class LivenessTranslator(CoreTranslator):
    """Represent liveness translator.

    Liveness raw event ("the CLI process is gone") → `session.finished` — the
        fact for THIS native run. A parked session can start again in a new terminal
        window, so its later exit must not deduplicate against the first run's exit.
    """

    @override
    def translate(
        self, raw_event: RawEvent, *, translation_stage: stages.TranslationStage = stages.TranslationStage.COMPLETE,
    ) -> TranslationResult:
        """Translate a process exit.

        Returns:
            The translation result.

        """
        observation = decode_document(ProcessExit, raw_event.payload)
        reason = "terminal_reassigned" if observation.state == "displaced" else "process_exited"
        finished = event_session.SessionFinished(outcomes.Outcome.UNKNOWN, reason)
        return stages.select_result(TranslationResult(
            (session_run_finished_event(raw_event, finished),),
            records.RecordedTranslationDecision.TRANSLATED,
        ), translation_stage)


class ResumeLivenessTranslator(CoreTranslator):
    """A resumed terminal window that closed finishes that resume run."""

    @override
    def translate(
        self, raw_event: RawEvent, *, translation_stage: stages.TranslationStage = stages.TranslationStage.COMPLETE,
    ) -> TranslationResult:
        """Translate a resumed process exit.

        Returns:
            The translation result.

        Raises:
            ValueError: If an input value is not valid.

        """
        if raw_event.terminal_window_id is None:
            message = "resume liveness has no terminal window"
            raise ValueError(message)
        finished = event_session.SessionFinished(outcomes.Outcome.UNKNOWN, "terminal_closed")
        return stages.select_result(TranslationResult(
            (session_run_finished_event(raw_event, finished),),
            records.RecordedTranslationDecision.TRANSLATED,
        ), translation_stage)


class SessionResumeTranslator(CoreTranslator):
    """A confirmed resume launch reopens the known session and lead actor."""

    @override
    def translate(
        self, raw_event: RawEvent, *, translation_stage: stages.TranslationStage = stages.TranslationStage.COMPLETE,
    ) -> TranslationResult:
        """Translate a session resume.

        Returns:
            The translation result.

        """
        observed = decode_document(SessionResumeObservation, raw_event.payload)
        started = event_session.SessionStarted(
            working_directory=observed.working_directory,
            source_reference=observed.source_reference,
            resumed_from=raw_event.session_id,
            title=None,
            model=None,
            effort=None,
            account=None,
        )
        return stages.select_result(TranslationResult(
            session_run_started_events(
                raw_event,
                started,
                event_actor.ActorStarted("lead", messaging.ActorRole.LEAD),
            ),
            records.RecordedTranslationDecision.TRANSLATED,
        ), translation_stage)


class InterruptTranslator(CoreTranslator):
    """Represent interrupt translator.

    Interrupt raw event (an acknowledged interrupt no native raw event
        corroborated within its grace period, see `engine/interpret/interrupts.py`)
        → `turn.aborted`. `subject_id` is the mark's own timestamp, so each
        interrupt occurrence is its own fact rather than colliding with the last.
    """

    @override
    def translate(
        self, raw_event: RawEvent, *, translation_stage: stages.TranslationStage = stages.TranslationStage.COMPLETE,
    ) -> TranslationResult:
        """Translate an interrupt.

        Returns:
            The translation result.

        """
        aborted = event_conversation.TurnAborted("interrupt acknowledged; no harness raw event confirmed it")
        return stages.select_result(TranslationResult(
            (
                canonical_event(
                    raw_event,
                    CanonicalEventDraft(
                        "turn",
                        raw_event.source_position,
                        "aborted",
                        aborted,
                    ),
                ),
            ),
            records.RecordedTranslationDecision.TRANSLATED,
        ), translation_stage)


class AutomaticTitleTranslator(CoreTranslator):
    """A generated title observation becomes a harness-independent fact."""

    @override
    def translate(
        self, raw_event: RawEvent, *, translation_stage: stages.TranslationStage = stages.TranslationStage.COMPLETE,
    ) -> TranslationResult:
        """Translate an automatic session title.

        Returns:
            The translation result.

        Raises:
            ValueError: If an input value is not valid.

        """
        if raw_event.source_type != AUTOMATIC_TITLE_SOURCE_TYPE:
            message = "automatic title translator received another source type"
            raise ValueError(message)
        observation = decode_document(SessionRenameObservation, raw_event.payload)
        changed = event_session.SessionTitleChanged(observation.title, observation.origin)
        return stages.select_result(TranslationResult(
            (
                canonical_event(
                    raw_event,
                    CanonicalEventDraft(
                        "session",
                        str(raw_event.session_id),
                        f"title:{observation.origin}:{raw_event.source_position}",
                        changed,
                    ),
                ),
            ),
            records.RecordedTranslationDecision.TRANSLATED,
        ), translation_stage)
