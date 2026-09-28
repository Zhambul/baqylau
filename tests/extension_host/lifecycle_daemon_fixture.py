# Copyright (c) 2026 Zhambyl Yermagambet
"""Run core hook input through the actual daemon and an external canonical worker."""

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from domain.ids import RawEventId
from extensions.models import interpretations
from harness.impl.claude_code.canonical import records as claude_records
from harness.impl.claude_code.id_session_types import ClaudeCodeSessionId
from repository.impl.sqlite import databases, interpretations as fact_storage
from sdk.client import BaqylauClient
from tests import storage_reads, terminal_pty_waits
from tests.extension_host import (
    environment_fixture,
    lifecycle_daemon_manifest as declaration,
    lifecycle_http_fixture,
    package_fixture,
)

DATABASE_NAME = "main.db"
ENCODING = "utf-8"
STOP_HOOK = "Stop"
STOP_IDENTITY = "stop-one"
REQUIRED_KINDS = ("session.started", "actor.started")
DEFAULT_HISTORY = "default"
FACT_LIMIT = 1000
OK_STATUS = 200
HOOK_PATH = "/api/harnesses/claude_code/hooks"
OWNER_PREFIX = "test.lifecycle"
SESSION_ID = "session-one"
TRANSCRIPT_PATH = "/work/transcript.jsonl"
WORKING_DIRECTORY = "/work"
JSON_HEADERS = MappingProxyType({"Content-Type": "application/json"})
LifecycleAction = Literal["enable", "disable", "reload"]
ENABLE_ACTION: LifecycleAction = "enable"


@dataclass(frozen=True)
class LifecycleDaemon:
    """Keep one external canonical package and its private application directory."""

    directory: Path
    package: Path
    owner: str

    def change(self, client: BaqylauClient, action: LifecycleAction) -> None:
        """Complete a public lifecycle request before checking its processing effects."""
        request = lifecycle_http_fixture.lifecycle_request(client, self.owner, action, action)
        admitted = client.extensions.lifecycle.change(self.owner, request)
        outcome = lifecycle_http_fixture.wait_operation(client, admitted.operation.operation_id)
        assert outcome.status == "succeeded", outcome

    def raw_event_ids(self) -> tuple[RawEventId, ...]:
        """Read stored raw event identities in acceptance order.

        Returns:
            Every stored raw event identity.

        """
        database = databases.read_only(databases.main_database(str(self.directory / DATABASE_NAME)))
        with database.read() as connection:
            rows = connection.execute("SELECT raw_event_id FROM raw_events ORDER BY rowid").fetchall()
        return tuple(RawEventId(row["raw_event_id"]) for row in rows)

    def journals(self) -> tuple[interpretations.InterpretationCommit, ...]:
        """Read complete typed journals written by the private application process.

        Returns:
            Every completed interpretation in original arrival order.

        """
        database = databases.read_only(databases.main_database(str(self.directory / DATABASE_NAME)))
        store = fact_storage.SqliteInterpretationRepository(database)
        completed = (storage_reads.find_interpretation(
            store, DEFAULT_HISTORY, raw_event_id,
        ) for raw_event_id in self.raw_event_ids())
        return tuple(commit for commit in completed if commit is not None)

    def facts(self) -> tuple[interpretations.StoredCanonicalFact, ...]:
        """Read accepted canonical facts in canonical order.

        Returns:
            The stored fact page.

        """
        database = databases.read_only(databases.main_database(str(self.directory / DATABASE_NAME)))
        return fact_storage.SqliteInterpretationRepository(database).current_fact_page(0, FACT_LIMIT).facts

    def pending_count(self) -> int:
        """Count raw events which still wait for a verdict.

        Returns:
            The pending raw event count.

        """
        database = databases.read_only(databases.main_database(str(self.directory / DATABASE_NAME)))
        with database.read() as connection:
            row = connection.execute("SELECT COUNT(*) FROM pending_raw_events").fetchone()
        return int(row[0])


def installed(directory: Path, wheels: Path, behavior: str) -> LifecycleDaemon:
    """Build a real locked canonical package outside the host checkout.

    Returns:
        The unenabled package and its private application directory.

    """
    package = environment_fixture.write_package(directory, wheels)
    owner = f"{OWNER_PREFIX}-{behavior}"
    backend = package_fixture.read_manifest(package).backend
    assert backend is not None
    package_fixture.save_manifest(package, declaration.manifest(owner, backend))
    package_fixture.write_file(package, f"{declaration.BACKEND_NAME}.py", declaration.backend_source())
    return LifecycleDaemon(directory, package, owner)


def deliver_hook(client: BaqylauClient, hook: claude_records.HookPayload) -> None:
    """Record one native hook delivery through the daemon's public route."""
    response = client.transport.client.post(
        HOOK_PATH, content=hook.model_dump_json().encode(ENCODING), headers=JSON_HEADERS,
    )
    assert response.status_code == OK_STATUS, response.text


def hook(name: str, identity: str, session_id: str = SESSION_ID) -> claude_records.HookPayload:
    """Build one native hook which also carries session start fields.

    Returns:
        A complete hook payload for the private daemon.

    """
    return claude_records.HookPayload(
        hook_event_name=name,
        hook_event_id=identity,
        session_id=ClaudeCodeSessionId(session_id),
        transcript_path=TRANSCRIPT_PATH,
        cwd=WORKING_DIRECTORY,
    )


def core_kinds(case: LifecycleDaemon) -> tuple[str, ...]:
    """Read accepted core fact kinds in canonical order.

    Returns:
        The core payload kinds.

    """
    facts = (stored.fact for stored in case.facts())
    return tuple(fact.payload.kind for fact in facts if fact.kind == "core")


def extension_texts(case: LifecycleDaemon) -> tuple[str, ...]:
    """Read accepted extension fact documents in canonical order.

    Returns:
        The extension document texts.

    """
    facts = (stored.fact for stored in case.facts())
    return tuple(fact.document.json_text for fact in facts if fact.kind == "extension")


def require_core_facts(case: LifecycleDaemon, expected: tuple[str, ...]) -> None:
    """Wait for actual core acceptance, not ingestion alone."""
    terminal_pty_waits.wait_until(lambda: core_kinds(case) == expected)
    assert core_kinds(case) == expected
