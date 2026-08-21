"""Translators for raw events our own machinery produces, one per core source type."""

from __future__ import annotations


from harness.contract import CoreTranslator
from harness.models import RawEvent, TranslationResult, canonical_event
from domain.events import SessionFinished, ShellOutputLocated, TurnAborted
from domain.records import RecordedTranslationDecision
from domain.values import Outcome
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
        return TranslationResult(
            (canonical_event(raw_event, "shell", str(located.shell_id), "output_located", located),),
            RecordedTranslationDecision.TRANSLATED,
        )


class LivenessTranslator(CoreTranslator):
    """Liveness raw event ("the CLI process is gone") → `session.finished` — the
    SAME fact identity the harness's own end-of-session hook produces, so a
    clean exit and a kill converge on one fact."""

    def translate(self, raw_event: RawEvent) -> TranslationResult:
        finished = SessionFinished(Outcome.UNKNOWN, "process_exited")
        return TranslationResult(
            (canonical_event(raw_event, "session", str(raw_event.session_id), "finished", finished),),
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
