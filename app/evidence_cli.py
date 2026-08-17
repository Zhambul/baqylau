"""Command-line inspection of exact raw evidence and canonical interpretations.

The ONE thing outside the daemon that builds repositories in-process, because
it is the tool you run when the daemon is the suspect. It opens READ-ONLY: the
forensic tool must not be able to create, migrate or alter the file it is
inspecting. It writes no SQL of its own — the joins it used to hand-roll are
`TranslationEvidenceRepository`'s.
"""

from __future__ import annotations

import base64
import json
import sys

from domain.codec import CanonicalEventCodec
from domain.ids import RawEventId, SessionId
from domain.records import TranslationEvidence
from repository.contract.facts import TranslationEvidenceRepository
from repository.impl.sqlite.databases import main_database, read_only
from repository.impl.sqlite.evidence import SqliteTranslationEvidenceRepository


def _document(codec: CanonicalEventCodec, evidence: TranslationEvidence) -> dict[str, object]:
    return {
        "raw_event_id": str(evidence.raw_event_id),
        "session_id": str(evidence.session_id),
        "harness": evidence.harness,
        "source_type": evidence.source_type,
        "source_name": evidence.source_name,
        "source_position": evidence.source_position,
        "actor_id": str(evidence.actor_id),
        "parent_actor_id": evidence.parent_actor_id,
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
                "event": json.loads(codec.encode(canonical.event)),
            }
            for canonical in evidence.canonical
        ],
    }


def _print(document: object) -> None:
    print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))


def evidence_repository() -> TranslationEvidenceRepository | None:
    database = read_only(main_database())
    if not database.exists():
        print(f"database does not exist: {database.path}", file=sys.stderr)
        return None
    return SqliteTranslationEvidenceRepository(database)


def main(arguments: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if len(arguments) != 2 or arguments[0] not in {"raw", "session"}:
        print("usage: baqylau-audit.py raw <raw_event_id> | session <session_id>", file=sys.stderr)
        return 2
    evidence_queries = evidence_repository()
    if evidence_queries is None:
        return 1
    codec = CanonicalEventCodec()
    command, identity = arguments
    if command == "raw":
        evidence = evidence_queries.evidence(RawEventId(identity))
        if evidence is None:
            print(f"raw event does not exist: {identity}", file=sys.stderr)
            return 1
        _print(_document(codec, evidence))
        return 0
    _print([
        _document(codec, evidence)
        for evidence in evidence_queries.evidence_for_session(SessionId(identity))
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
