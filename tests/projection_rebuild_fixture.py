# Copyright (c) 2026 Zhambyl Yermagambet
"""Project one fact live, then rebuild the owner's projection over the real generation store."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, cast

from baqylau_extension_api.contracts.plugin import ExtensionCapabilities, ExtensionPlugin
from baqylau_extension_api.models.directory import DirectoryEntry

from domain.ids import SessionId
from extensions import pass_health, projection_pass, projection_rebuild, registry_package
from repository.impl.sqlite import (
    candidate_records,
    connection,
    extension_projections,
    extension_records,
    projection_generations,
)
from tests import command_package_fixture as packages, projection_pass_fixture as fixture, sqlite_test_dependencies
from tests.extension_api import example, service_samples

NO_HEALTH = pass_health.AuditOnlyHealth(lambda _owner: None)

if TYPE_CHECKING:
    from baqylau_extension_api.contracts import projection

MAX_PASSES = 100
_HEAD_SQL = "INSERT INTO canonical_scope_heads(history_revision, scope, head_cursor) VALUES('default', ?, ?)"
_ENTRIES_SQL = (
    "SELECT entry_id, commit_cursor, position, summary, payload FROM extension_candidate_entries "
    "WHERE generation=? ORDER BY entry_id"
)


@dataclass(frozen=True)
class RebuildCase:
    """Keep the live pass, the rebuild, and the stores together."""

    database: connection.SqliteDatabase
    live: projection_pass.ProjectionPass
    rebuild: projection_rebuild.ProjectionRebuild
    generations: projection_generations.SqliteProjectionGenerationRepository

    def build(self, package: registry_package.RegistryPackage) -> int:
        """Run the rebuild stage until no generation is building.

        Returns:
            The number of recorded failures.

        Raises:
            AssertionError: When the rebuild does not finish in a bounded number of passes.

        """
        failures: list[str] = []
        health = pass_health.AuditOnlyHealth(failures.append)
        for _ in range(MAX_PASSES):
            if not self.rebuild.build_pending((package,), health):
                return len(failures)
        message = "the rebuild did not finish"
        raise AssertionError(message)

    def live_view(self) -> tuple[tuple[str | None, ...], str]:
        """Read the live feed summaries of the fixture session and the owner's live generation.

        Returns:
            The summaries in feed order, and the live generation.

        """
        store = sqlite_test_dependencies.SqliteSessionDataRepository(self.database)
        page = store.entries_page(SessionId(fixture.SCOPE.session_id), limit=10)
        summaries = tuple(entry.summary for entry in page.entries)
        return summaries, self.generations.active_generation(fixture.OWNER)

    def candidate_entries(self, generation: str) -> list[tuple[object, ...]]:
        """Read the stored candidate feed rows of one generation.

        Returns:
            The rows in entry order.

        """
        with self.database.read() as read:
            rows = read.execute(_ENTRIES_SQL, (generation,)).fetchall()
        return [tuple(row) for row in rows]


def a_package(projector: projection.ExtensionProjector) -> registry_package.RegistryPackage:
    """Build the owner's enabled package with one projector.

    Returns:
        The registry package.

    """
    environment = service_samples.environment(fixture.OWNER)
    capabilities = ExtensionCapabilities(lifecycle=example.SampleLifecycle(), projector=projector)
    plugin = packages.FakePlugin(extension_info=environment.extension_info, capabilities=capabilities)
    return registry_package.RegistryPackage(
        manifest=fixture.MANIFEST,
        entry=DirectoryEntry(extension_info=environment.extension_info, state="enabled"),
        environment=environment,
        plugin=cast("ExtensionPlugin", plugin),
    )


def a_case(main: connection.SqliteDatabase) -> RebuildCase:
    """Project one fact live, with the scope head that its canonical insert would store.

    Returns:
        The rebuild case after one live projection.

    """
    with main.write() as write:
        write.execute(_HEAD_SQL, (fixture.SCOPE.model_dump_json(), fixture.FACT_CURSOR))
    generations = projection_generations.SqliteProjectionGenerationRepository(main)
    live = projection_pass.ProjectionPass(
        facts=fixture.FakeFacts(stored=(fixture.fact(fixture.FACT_ID, fixture.FACT_CURSOR),)),
        record_reader=extension_records.SqliteExtensionRecordRepository(main),
        store=extension_projections.SqliteExtensionProjectionRepository(main),
        heads=generations,
    )
    package = a_package(fixture.LocalProjector())
    assert live.run_selected((package,), MAX_PASSES, NO_HEALTH) == 1
    rebuild = projection_rebuild.ProjectionRebuild(
        live=live,
        generations=generations,
        candidate_reader=partial(candidate_records.SqliteCandidateRecordReader, main),
    )
    return RebuildCase(main, live, rebuild, generations)
