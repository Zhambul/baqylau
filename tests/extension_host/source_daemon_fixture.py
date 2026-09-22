# Copyright (c) 2026 Zhambyl Yermagambet
"""Run an SDK-only source package through the actual daemon graph."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from baqylau_extension_api.models import documents, scopes
from pydantic import TypeAdapter

from extensions.models import interpretations, observations as original_models, source_reads
from repository.impl.sqlite import databases, interpretations as fact_storage, observations, session_data
from sdk.client import BaqylauClient
from tests import terminal_pty_waits
from tests.extension_api import operation_samples, source_example, source_samples
from tests.extension_host import environment_fixture, lifecycle_http_fixture, package_fixture

ENCODING = "utf-8"
DATABASE_NAME = "main.db"


@dataclass(frozen=True)
class SourceDaemon:
    """Keep test package files separate from the daemon's retained artifact and storage."""

    directory: Path
    package: Path
    journal: Path

    def change(self, client: BaqylauClient, action: Literal["enable", "disable", "reload"]) -> None:
        """Complete a public lifecycle request before checking its source effects."""
        request = lifecycle_http_fixture.lifecycle_request(client, operation_samples.OWNER, action, action)
        admitted = client.extensions.lifecycle.change(operation_samples.OWNER, request)
        outcome = lifecycle_http_fixture.wait_operation(client, admitted.operation.operation_id)
        assert outcome.status == "succeeded", outcome

    def originals(self) -> tuple[original_models.ExtensionObservation, ...]:
        """Read exact stored inputs without opening a writable connection.

        Returns:
            Installation-scoped extension originals in their accepted order.

        """
        database = databases.read_only(databases.main_database(str(self.directory / DATABASE_NAME)))
        store = observations.SqliteObservationRepository(database)
        return tuple(
            row.observation for row in store.observations_for_scope(scopes.InstallationScope(), 0, 1000)
            if isinstance(row.observation, original_models.ExtensionObservation)
        )

    def require_text(self, expected: tuple[str, ...]) -> None:
        """Wait for exact original bytes, not only an HTTP operation state."""
        terminal_pty_waits.wait_until(lambda: self.texts() == expected)
        assert self.texts() == expected

    def texts(self) -> tuple[str, ...]:
        """Read original JSON text with its original newline.

        Returns:
            Exact source documents in accepted order.

        """
        return tuple(original.candidate.document.json_text for original in self.originals())

    def reads(self, runtime_revision: str) -> tuple[source_reads.SourceReadProposal, ...]:
        """Inspect typed read evidence from one actual daemon runtime.

        Returns:
            Complete proposals in call acceptance order.

        """
        database = databases.read_only(databases.main_database(str(self.directory / DATABASE_NAME)))
        with database.read() as connection:
            rows = connection.execute(
                "SELECT proposal FROM extension_source_reads WHERE runtime_revision=? ORDER BY rowid",
                (runtime_revision,),
            ).fetchall()
        return tuple(source_reads.SourceReadProposal.model_validate_json(row["proposal"]) for row in rows)

    def facts(self) -> tuple[str, ...]:
        """Read fact documents accepted by the actual engine.

        Returns:
            Exact extension fact documents, separate from original observations.

        """
        database = databases.read_only(databases.main_database(str(self.directory / DATABASE_NAME)))
        store = fact_storage.SqliteInterpretationRepository(database)
        page = store.current_fact_page(0, 1000)
        facts = (stored.fact for stored in page.facts)
        return tuple(fact.document.json_text for fact in facts if fact.kind == "extension")

    def append(self, text: str) -> None:
        """Flush new original input without changing the source generation."""
        with self.journal.open("a", encoding=ENCODING) as stream:
            stream.write(text)


def installed(directory: Path, wheels: Path, text: str = '"first"\n') -> SourceDaemon:
    """Build a real locked source package outside the host checkout.

    Returns:
        The unenabled package and its independent journal path.

    """
    source = environment_fixture.write_package(directory, wheels)
    journal = directory / "journal.log"
    journal.write_text(text, encoding=ENCODING)
    _configure(source, journal)
    assert source_example.__file__ is not None
    package_fixture.write_file(source, "source_backend.py", Path(source_example.__file__).read_bytes())
    return SourceDaemon(directory, source, journal)


def _configure(source: Path, journal: Path) -> None:
    backend = package_fixture.read_manifest(source).backend
    assert backend is not None
    manifest = source_samples.manifest(documents.EncodedDocument(
        schema_ref=operation_samples.schema_definition().reference,
        json_text=TypeAdapter(str).dump_json(str(journal)).decode(ENCODING),
    ))
    package_fixture.save_manifest(source, manifest.model_copy(update={
        "backend": backend.model_copy(update={"module": "source_backend"}),
    }))


def require_facts(case: SourceDaemon, expected: tuple[str, ...]) -> None:
    """Wait for actual canonical acceptance, not source ingestion alone."""
    terminal_pty_waits.wait_until(lambda: case.facts() == expected)
    assert case.facts() == expected


def journals(case: SourceDaemon) -> tuple[interpretations.InterpretationCommit, ...]:
    """Read complete typed journals written by the private application process.

    Returns:
        Every completed interpretation in original arrival order.

    """
    database = databases.read_only(databases.main_database(str(case.directory / DATABASE_NAME)))
    store = fact_storage.SqliteInterpretationRepository(database)
    completed = (store.find_interpretation("default", original.raw_event_id) for original in case.originals())
    return tuple(commit for commit in completed if commit is not None)


def core_progress(case: SourceDaemon) -> int:
    """Inspect the real core consumer without changing its database.

    Returns:
        Its durable canonical checkpoint.

    """
    database = databases.read_only(databases.main_database(str(case.directory / DATABASE_NAME)))
    return session_data.SqliteSessionDataRepository(database).progress()


def require_core_progress(case: SourceDaemon, expected: int) -> None:
    """Wait for mixed consumption and require no fake display rows for extension-only input."""
    terminal_pty_waits.wait_until(lambda: core_progress(case) == expected)
    database = databases.read_only(databases.main_database(str(case.directory / DATABASE_NAME)))
    view = session_data.SqliteSessionDataRepository(database)
    assert view.progress() == expected
    assert view.high_water_cursor() == 0 and not view.visible()
