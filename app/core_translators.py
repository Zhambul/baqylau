"""Translators for evidence our own machinery produces, one per core source type."""

from __future__ import annotations

import json

from harness.contract import CoreTranslator
from harness.models import RawEvent, TranslationResult, canonical_event
from domain.events import OperationOutputLocated, SessionFinished
from domain.ids import OperationId


class OperationOutputTranslator(CoreTranslator):
    """Output-location directive (recorded by a gateway) → the typed
    `operation.output_located` fact. This is where the directive's JSON is parsed."""

    def translate(self, raw_event: RawEvent) -> TranslationResult:
        document = json.loads(raw_event.payload)
        located = OperationOutputLocated(
            operation_id=OperationId(str(document["operation_id"])),
            source_path=str(document["source_path"]),
            chunk_source_type=str(document["chunk_source_type"]),
            delete_source=bool(document.get("delete_source")),
            initial_size=int(document.get("initial_size") or 0),
            initial_modified_at=int(document.get("initial_modified_at") or 0),
            wait_for_source_change=bool(document.get("wait_for_source_change")),
            until=str(document["until"]),
        )
        return TranslationResult(
            (canonical_event(raw_event, "operation", str(located.operation_id), "output_located", located),),
            "translated",
        )


class LivenessTranslator(CoreTranslator):
    """Liveness raw event ("the CLI process is gone") → `session.finished` — the
    SAME fact identity the harness's own end-of-session hook produces, so a
    clean exit and a kill converge on one fact."""

    def translate(self, raw_event: RawEvent) -> TranslationResult:
        finished = SessionFinished("unknown", "process_exited")
        return TranslationResult(
            (canonical_event(raw_event, "session", str(raw_event.session_id), "finished", finished),),
            "translated",
        )
