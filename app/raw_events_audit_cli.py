"""Command-line inspection of exact raw events and canonical interpretations.

The ONE thing outside the daemon that builds repositories in-process, because
it is the tool you run when the daemon is the suspect. It opens READ-ONLY: the
forensic tool must not be able to create, migrate or alter the file it is
inspecting. It writes no SQL of its own — the joins it used to hand-roll are
`RawEventAuditRepository`'s.
"""

from __future__ import annotations

import base64
import json
import sys

from domain.ids import RawEventId, SessionId
from harness.models import RawEventAudit
from repository.contract.facts import RawEventAuditRepository
from repository.impl.sqlite.databases import main_database, read_only
from repository.impl.sqlite.raw_event_audits import SqliteRawEventAuditRepository
from repository.mapper import facts as mapper


def _document(
    raw_event_audit: RawEventAudit,
) -> dict[str, object]:  # loose: raw JSON from the audit CLI, wave 2 gives it a real shape
    raw_event = raw_event_audit.raw_event
    interpretation = raw_event_audit.interpretation
    return {
        "raw_event_id": str(raw_event.raw_event_id),
        "session_id": str(raw_event.session_id),
        "harness": raw_event.harness,
        "source_type": raw_event.source_type,
        "source_name": raw_event.source_name,
        "source_position": raw_event.source_position,
        "actor_id": str(raw_event.actor_id),
        "parent_actor_id": raw_event.parent_actor_id,
        "observed_at": raw_event.observed_at,
        "encoding": raw_event.encoding,
        "payload_base64": base64.b64encode(raw_event.payload).decode("ascii"),
        "translator_version": interpretation.translator_version if interpretation else "",
        "decision": interpretation.decision if interpretation else "untranslated",
        "reason": interpretation.reason if interpretation else None,
        "completed_at": interpretation.completed_at if interpretation else 0.0,
        "canonical": [
            {
                "accepted_at": canonical.accepted_at,
                "event_order": canonical.event_order,
                "storage_result": canonical.storage_result,
                "event": json.loads(mapper.encode_canonical_event(canonical.event)),
            }
            for canonical in (interpretation.events if interpretation else ())
        ],
    }


def _print(document: object) -> None:  # loose: raw JSON from the audit CLI, wave 2 gives it a real shape
    print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))


def raw_event_audit_repository() -> RawEventAuditRepository | None:
    database = read_only(main_database())
    if not database.exists():
        print(f"database does not exist: {database.path}", file=sys.stderr)
        return None
    return SqliteRawEventAuditRepository(database)


def main(arguments: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if len(arguments) != 2 or arguments[0] not in {"raw", "session"}:
        print(
            "usage: baqylau-raw-events-audit.py raw <raw_event_id> | session <session_id>",
            file=sys.stderr,
        )
        return 2
    audits = raw_event_audit_repository()
    if audits is None:
        return 1
    command, identity = arguments
    if command == "raw":
        audit = audits.audit(RawEventId(identity))
        if audit is None:
            print(f"raw event does not exist: {identity}", file=sys.stderr)
            return 1
        _print(_document(audit))
        return 0
    _print([
        _document(audit)
        for audit in audits.audits_for_session(SessionId(identity))
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
