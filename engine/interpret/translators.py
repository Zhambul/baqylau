"""Translators for raw events our own machinery produces, one per core source type."""

from __future__ import annotations


from harness.contract import CoreTranslator
from harness.models import (
    AUTOMATIC_TITLE_SOURCE_TYPE,
    RawEvent,
    TranslationResult,
    canonical_event,
    plan_resolution_phase,
    session_run_finished_event,
    session_run_started_events,
)
from domain.events import (
    ActorStarted,
    ActorAssignmentFinished,
    EffortChanged,
    ModelChanged,
    PlanResolved,
    SessionFinished,
    SessionStarted,
    SessionTitleChanged,
    ShellFinished,
    ShellOutputLocated,
    TurnAborted,
)
from domain.shells import shell_output_source_key
from domain.ids import AssignmentId, ShellId
from domain.records import RecordedTranslationDecision
from domain.values import ActorRole, OpenWorkKind, Outcome
from domain.values import EffortChangeReason, ModelChangeReason, ModelReference
from harness.models.directives import (
    EffortSelectionObservation,
    ModelSelectionObservation,
    PlanDecisionObservation,
    ProcessExit,
    SessionCloseWorkObservation,
    SessionRenameObservation,
    SessionResumeObservation,
)
from repository.mapper.documents import decode_document


class ShellOutputTranslator(CoreTranslator):
    """Output-location directive (recorded by a gateway) → the typed
    `shell.output_located` fact.

    The directive IS a `ShellOutputLocated`, written by
    `harness/models/raw_events.py` and decoded here against that same
    declaration — including its `until` boundary, which is a `ShellFollowUntil`
    enum the mapper checks. Both halves used to be written by hand: a dict built
    from `asdict` at the writer, and eight `document[...]` reads plus a bespoke
    validator for that enum here."""

    def translate(self, raw_event: RawEvent) -> TranslationResult:
        located = decode_document(ShellOutputLocated, raw_event.payload)
        source_key = shell_output_source_key(located.source_path)
        return TranslationResult(
            (canonical_event(
                raw_event,
                "shell",
                str(located.shell_id),
                f"output_located:{source_key}",
                located,
            ),),
            RecordedTranslationDecision.TRANSLATED,
        )


class LivenessTranslator(CoreTranslator):
    """Liveness raw event ("the CLI process is gone") → `session.finished` — the
    fact for THIS native run. A parked session can start again in a new terminal
    window, so its later exit must not deduplicate against the first run's exit."""

    def translate(self, raw_event: RawEvent) -> TranslationResult:
        observation = decode_document(ProcessExit, raw_event.payload)
        reason = "terminal_reassigned" if observation.state == "displaced" else "process_exited"
        finished = SessionFinished(Outcome.UNKNOWN, reason)
        return TranslationResult(
            (session_run_finished_event(raw_event, finished),),
            RecordedTranslationDecision.TRANSLATED,
        )


class ResumeLivenessTranslator(CoreTranslator):
    """A resumed terminal window that closed finishes that resume run."""

    def translate(self, raw_event: RawEvent) -> TranslationResult:
        if raw_event.terminal_window_id is None:
            raise ValueError("resume liveness has no terminal window")
        finished = SessionFinished(Outcome.UNKNOWN, "terminal_closed")
        return TranslationResult(
            (session_run_finished_event(raw_event, finished),),
            RecordedTranslationDecision.TRANSLATED,
        )


class SessionResumeTranslator(CoreTranslator):
    """A confirmed resume launch reopens the known session and lead actor."""

    def translate(self, raw_event: RawEvent) -> TranslationResult:
        observed = decode_document(SessionResumeObservation, raw_event.payload)
        started = SessionStarted(
            working_directory=observed.working_directory,
            source_reference=observed.source_reference,
            resumed_from=raw_event.session_id,
            title=None,
            model=None,
            effort=None,
            account=None,
        )
        return TranslationResult(
            session_run_started_events(
                raw_event,
                started,
                ActorStarted("lead", ActorRole.LEAD),
            ),
            RecordedTranslationDecision.TRANSLATED,
        )


class InterruptTranslator(CoreTranslator):
    """Interrupt raw event (an acknowledged interrupt no native raw event
    corroborated within its grace period, see `engine/interpret/interrupts.py`)
    → `turn.aborted`. `subject_id` is the mark's own timestamp, so each
    interrupt occurrence is its own fact rather than colliding with the last."""

    def translate(self, raw_event: RawEvent) -> TranslationResult:
        aborted = TurnAborted("interrupt acknowledged; no harness raw event confirmed it")
        return TranslationResult(
            (canonical_event(raw_event, "turn", raw_event.source_position, "aborted", aborted),),
            RecordedTranslationDecision.TRANSLATED,
        )


class ControlTranslator(CoreTranslator):
    """A confirmed control effect becomes the same fact as a native event."""

    def translate(self, raw_event: RawEvent) -> TranslationResult:
        if raw_event.source_name == "session_finish":
            return self._session_finish(raw_event)
        if raw_event.source_name == "session_close":
            return self._session_close(raw_event)
        if raw_event.source_name == "session_rename":
            rename_observation = decode_document(
                SessionRenameObservation,
                raw_event.payload,
            )
            return TranslationResult(
                (
                    canonical_event(
                        raw_event,
                        "session",
                        str(raw_event.session_id),
                        f"title:{rename_observation.origin}:{raw_event.source_position}",
                        SessionTitleChanged(
                            rename_observation.title,
                            rename_observation.origin,
                        ),
                    ),
                ),
                RecordedTranslationDecision.TRANSLATED,
            )
        if raw_event.source_name == "model_selection":
            model_observation = decode_document(ModelSelectionObservation, raw_event.payload)
            model_changed = ModelChanged(
                None,
                ModelReference(model_observation.model, model_observation.model),
                ModelChangeReason.SELECTED,
            )
            return TranslationResult(
                (
                    canonical_event(
                        raw_event,
                        "model",
                        str(raw_event.actor_id),
                        f"selected:{raw_event.source_position}",
                        model_changed,
                    ),
                ),
                RecordedTranslationDecision.TRANSLATED,
            )
        if raw_event.source_name == "effort_selection":
            effort_observation = decode_document(EffortSelectionObservation, raw_event.payload)
            effort_changed = EffortChanged(
                None,
                effort_observation.effort,
                EffortChangeReason.SELECTED,
            )
            return TranslationResult(
                (
                    canonical_event(
                        raw_event,
                        "effort",
                        str(raw_event.actor_id),
                        f"selected:{raw_event.source_position}",
                        effort_changed,
                    ),
                ),
                RecordedTranslationDecision.TRANSLATED,
            )
        plan_observation = decode_document(PlanDecisionObservation, raw_event.payload)
        resolved = PlanResolved(
            plan_observation.attention_id,
            plan_observation.state,
            plan_observation.feedback,
            plan_observation.edited,
        )
        return TranslationResult(
            (
                canonical_event(
                    raw_event,
                    "plan",
                    str(plan_observation.attention_id),
                    plan_resolution_phase(resolved),
                    resolved,
                    turn_id=plan_observation.turn_id,
                ),
            ),
            RecordedTranslationDecision.TRANSLATED,
        )
    @staticmethod
    def _session_close(raw_event: RawEvent) -> TranslationResult:
        observed = decode_document(SessionCloseWorkObservation, raw_event.payload)
        if observed.kind == OpenWorkKind.TURN:
            event = canonical_event(
                raw_event,
                "turn",
                str(observed.subject_id),
                "aborted",
                TurnAborted("session closed"),
                turn_id=observed.turn_id,
            )
        elif observed.kind == OpenWorkKind.SHELL:
            shell_id = ShellId(observed.subject_id)
            event = canonical_event(
                raw_event,
                "shell",
                str(observed.subject_id),
                "finished",
                ShellFinished(shell_id, Outcome.CANCELLED, None, None),
                turn_id=observed.turn_id,
            )
        else:
            assignment_id = AssignmentId(observed.subject_id)
            event = canonical_event(
                raw_event,
                "actor_assignment",
                str(observed.subject_id),
                "finished",
                ActorAssignmentFinished(
                    assignment_id,
                    Outcome.CANCELLED,
                    None,
                    "session closed",
                ),
                turn_id=observed.turn_id,
            )
        return TranslationResult(
            (event,),
            RecordedTranslationDecision.TRANSLATED,
        )

    @staticmethod
    def _session_finish(raw_event: RawEvent) -> TranslationResult:
        # Decode the recorder-owned document so writer/reader schema drift is
        # still rejected even though the close fact only needs its existence.
        decode_document(ProcessExit, raw_event.payload)
        finished = SessionFinished(Outcome.UNKNOWN, "session_closed")
        return TranslationResult(
            (
                canonical_event(
                    raw_event,
                    "session_control",
                    raw_event.source_position,
                    "finished",
                    finished,
                ),
            ),
            RecordedTranslationDecision.TRANSLATED,
        )


class AutomaticTitleTranslator(CoreTranslator):
    """A generated title observation becomes a harness-independent fact."""

    def translate(self, raw_event: RawEvent) -> TranslationResult:
        if raw_event.source_type != AUTOMATIC_TITLE_SOURCE_TYPE:
            raise ValueError("automatic title translator received another source type")
        observation = decode_document(SessionRenameObservation, raw_event.payload)
        changed = SessionTitleChanged(observation.title, observation.origin)
        return TranslationResult(
            (
                canonical_event(
                    raw_event,
                    "session",
                    str(raw_event.session_id),
                    f"title:{observation.origin}:{raw_event.source_position}",
                    changed,
                ),
            ),
            RecordedTranslationDecision.TRANSLATED,
        )
