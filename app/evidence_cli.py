"""Command-line inspection of exact raw evidence and canonical interpretations."""

from __future__ import annotations

import base64
import json
import os
import sys

from app.data import data_directory
from domain.ids import RawEventId, SessionId
from runtime.event_store import EventStore
from runtime.evidence import EvidenceQueries, TranslationEvidence


def _document(store: EventStore, evidence: TranslationEvidence) -> dict:
    return {
        "raw_event_id": str(evidence.raw_event_id),
        "session_id": str(evidence.session_id),
        "harness": evidence.harness,
        "source_type": evidence.source_type,
        "source_name": evidence.source_name,
        "source_position": evidence.source_position,
        "actor_id": str(evidence.actor_id),
        "parent_actor_id": (
            str(evidence.parent_actor_id)
            if evidence.parent_actor_id is not None
            else None
        ),
        "observed_at": evidence.observed_at,
        "encoding": evidence.encoding,
        "payload_base64": base64.b64encode(evidence.payload).decode("ascii"),
        "translator_version": evidence.translator_version,
        "decision": evidence.decision,
        "reason": evidence.reason,
        "completed_at": evidence.completed_at,
        "canonical": [
            {
                "accepted_at": canonical.accepted_at,
                "event_order": canonical.event_order,
                "storage_result": canonical.storage_result,
                "event": json.loads(store.codec.encode(canonical.event)),
            }
            for canonical in evidence.canonical
        ],
    }


def _print(document: object) -> None:
    print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))


def main(arguments: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if len(arguments) != 2 or arguments[0] not in {"raw", "session"}:
        print("usage: baqylau-audit.py raw <raw_event_id> | session <session_id>", file=sys.stderr)
        return 2
    database_path = os.path.join(data_directory(), "events.db")
    if not os.path.isfile(database_path):
        print(f"event database does not exist: {database_path}", file=sys.stderr)
        return 1
    store = EventStore(database_path)
    evidence_queries = EvidenceQueries(store)
    command, identity = arguments
    if command == "raw":
        evidence = evidence_queries.raw_event(RawEventId(identity))
        if evidence is None:
            print(f"raw event does not exist: {identity}", file=sys.stderr)
            return 1
        _print(_document(store, evidence))
        return 0
    _print([
        _document(store, evidence)
        for evidence in evidence_queries.session(SessionId(identity))
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
