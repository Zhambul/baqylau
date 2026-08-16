"""Translators for evidence our own machinery produces, one per core source type."""

from __future__ import annotations

import json
from typing import Literal

from harness.contract import CoreTranslator
from harness.models import RawEvent, TranslationResult, canonical_event
from domain.events import OperationOutputLocated, SessionFinished
from domain.ids import OperationId


# The directive's `until` is JSON off the wire, and the fact it becomes accepts
# exactly two values. Checked HERE, at the parse, because that is the only place
# the bad value is still attributable to the directive that carried it — one
# level further in it is an invalid canonical event with no way back to its
# source.
def _until(value: object) -> Literal["operation_finished", "session_finished"]:
    text = str(value)
    # Each branch returns the literal it matched rather than the parsed string:
    # comparing a str to a constant does not make it that constant's type, and
    # this is the form that needs no cast to say so.
    if text == "operation_finished":
        return "operation_finished"
    if text == "session_finished":
        return "session_finished"
    raise ValueError(f"unknown output-location boundary: {text!r}")


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
            until=_until(document["until"]),
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
