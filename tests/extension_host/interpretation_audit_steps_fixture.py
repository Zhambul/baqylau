# Copyright (c) 2026 Zhambyl Yermagambet
"""Build hand-checked stored journal steps for audit read tests."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from baqylau_extension_api.models import documents, transforms

from domain import ids, records
from extensions.models import interpretation_steps as steps
from extensions.models.interpretation_bodies import BodyStore
from extensions.models.interpretation_storage_steps import store_step
from harness.models.raw_events import RawEventAudit
from repository.impl.sqlite import databases, raw_event_audits
from tests import sqlite_migration_events as events, sqlite_test_fixtures as raw_fixtures
from tests.extension_host.interpretation_large_fixture import canonical_request

OWNER = "test.owner"
FAKE_BYTE_LENGTH = 12_345
DIGEST_LENGTH = 64
FAKE_DIGEST = "a" * DIGEST_LENGTH
DEFAULT_HISTORY = "default"
CREATED_AT = float(0)
_JOURNAL_HEADER = '{"format_version": 2}'
_STEP_SQL = (
    "INSERT INTO interpretation_journal_steps(history_revision, raw_event_id, step_index, stage, step) "
    "VALUES('default', ?, 0, ?, ?)"
)
_JOURNAL_SQL = (
    "INSERT INTO interpretation_journals(history_revision, raw_event_id, proposal, codec_version) "
    "VALUES('default', ?, ?, 2)"
)
_BODY_SQL = (
    "INSERT OR IGNORE INTO interpretation_bodies(digest, kind, byte_length, body, created_at) "
    "VALUES(?, ?, ?, ?, ?)"
)
_BODY_LINK_SQL = (
    "INSERT INTO interpretation_journal_bodies(history_revision, raw_event_id, digest) VALUES(?, ?, ?)"
)


@dataclass(frozen=True)
class RejectedEvidence:
    """Keep one hand-built rejection step with its declared evidence."""

    step: steps.CanonicalTransformStep
    byte_length: int
    digest: str
    diagnostic_code: str


@dataclass(frozen=True)
class StepCase:
    """Keep one private database and its single audited raw event."""

    database_path: str
    raw_event_id: ids.RawEventId

    def audit(self) -> RawEventAudit:
        """Read the complete bounded audit of this case.

        Returns:
            The stored audit.

        Raises:
            ValueError: If the case has no stored audit.

        """
        repository = raw_event_audits.SqliteRawEventAuditRepository(databases.main_database(self.database_path))
        stored = repository.audit(self.raw_event_id)
        if stored is None:
            message = "step case has no stored audit"
            raise ValueError(message)
        return stored

    def audit_steps(self) -> tuple[records.InterpretationAuditStep, ...]:
        """Read the bounded audit steps of this case.

        Returns:
            The stored step metadata.

        Raises:
            ValueError: If the case has no stored interpretation.

        """
        stored = self.audit()
        if stored.interpretation is None:
            message = "step case has no stored interpretation"
            raise ValueError(message)
        return stored.interpretation.steps


def rejected_evidence() -> RejectedEvidence:
    """Build one canonical rejection record with exact declared evidence.

    Returns:
        The step and its declared size, digest, and diagnostic code.

    """
    diagnostic = documents.Diagnostic(code="journal_limit", message="The reply exceeds the journal budget")
    outcome = steps.RejectedStep(
        diagnostic=diagnostic, observed_byte_length=FAKE_BYTE_LENGTH, observed_digest=FAKE_DIGEST,
    )
    return RejectedEvidence(
        step=steps.CanonicalTransformStep(request=canonical_request(), outcome=outcome),
        byte_length=FAKE_BYTE_LENGTH,
        digest=FAKE_DIGEST,
        diagnostic_code=diagnostic.code,
    )


def applied_step() -> steps.CanonicalTransformStep:
    """Build one applied canonical step with keep and drop operations.

    Returns:
        The complete applied step.

    """
    operations = (
        transforms.Keep(input_id="input-a"),
        transforms.Drop(input_id="input-b", reason="Intentional drop"),
    )
    return steps.CanonicalTransformStep(
        request=canonical_request(),
        outcome=steps.AppliedStep(reply=transforms.CanonicalTransformResult(operations=operations)),
    )


def step_case(directory: Path, step: steps.InterpretationStep) -> StepCase:
    """Store one hand-built step in a fresh core database.

    Returns:
        The private database and the audited raw event identity.

    """
    database = databases.main_database(str(directory / "main.db"))
    events.populate(database)
    raw_event_id = raw_fixtures.a_raw_event().raw_event_id
    store = BodyStore()
    document = store_step(step, store).model_dump_json()
    with database.write() as connection:
        connection.execute(_JOURNAL_SQL, (str(raw_event_id), _JOURNAL_HEADER))
        connection.execute(_STEP_SQL, (str(raw_event_id), step.stage, document))
        _write_bodies(connection, raw_event_id, store)
    return StepCase(database_path=database.path, raw_event_id=raw_event_id)


def stored_body_bytes(case: StepCase) -> int:
    """Read the total stored body bytes of one case.

    Returns:
        The total stored body bytes.

    """
    with databases.main_database(case.database_path).read() as connection:
        row = connection.execute("SELECT COALESCE(SUM(byte_length), 0) FROM interpretation_bodies").fetchone()
    return int(row[0])


def _write_bodies(connection: sqlite3.Connection, raw_event_id: ids.RawEventId, store: BodyStore) -> None:
    for body in store.bodies():
        ref = body.ref
        text = body.encoded.decode("utf-8")
        connection.execute(_BODY_SQL, (ref.digest, ref.kind, ref.byte_length, text, CREATED_AT))
        connection.execute(_BODY_LINK_SQL, (DEFAULT_HISTORY, str(raw_event_id), ref.digest))
