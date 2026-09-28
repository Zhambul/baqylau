# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep one committed observer runtime, its pass, its stores, and its observer for the observer tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from extensions import observer_pass as passes, pass_health
from extensions.observer_models import ObserverStores
from extensions.registry_package import RegistryPackage
from repository.impl.sqlite.connection import SqliteDatabase
from repository.impl.sqlite.observations import SqliteObservationRepository
from tests import (
    command_package_fixture as packages,
    job_executor_fixture,
    observer_doubles,
    observer_storage_doubles as storage_doubles,
)

if TYPE_CHECKING:
    from extensions.control_policy import ExtensionControlPolicy
    from extensions.models.observations import StoredObservation
    from repository.contract.extension_jobs import ExtensionJob

NO_HEALTH = pass_health.AuditOnlyHealth(lambda _owner: None)

MANY = 100


@dataclass(frozen=True)
class ObserverCase:
    """Keep one committed runtime, its pass, its stores, and its observer together."""

    database: SqliteDatabase
    observers: storage_doubles.FixedScopeObservers
    observer_pass: passes.ObserverPass
    stores: ObserverStores
    observer: observer_doubles.FakeObserver
    package: RegistryPackage
    runtime_revision: str

    def accept(self) -> int:
        """Accept the jobs of one pass and run none of them.

        Returns:
            The number of facts read.

        """
        return self.observer_pass.run_selected((self.package,), MANY, NO_HEALTH)

    def run(self, manager_id: str = observer_doubles.MANAGER, policy: ExtensionControlPolicy | None = None) -> int:
        """Accept jobs in one pass, then run them on a real job executor.

        Returns:
            The number of facts read.

        """
        total = self.accept()
        self.execute(self.package, manager_id, policy)
        return total

    def execute(
        self,
        package: RegistryPackage,
        manager_id: str = observer_doubles.MANAGER,
        policy: ExtensionControlPolicy | None = None,
    ) -> None:
        """Run every accepted job on a real job executor with one package set."""
        registry = packages.FakeRegistry((package,), self.runtime_revision)
        manager = storage_doubles.FakeManager(self.runtime_revision, manager_id)
        executor = job_executor_fixture.an_executor(registry, self.stores, manager, policy)
        executor.submit_accepted(MANY)
        executor.close()

    def job(self) -> ExtensionJob:
        """Read the one stored observer job of the fixture cause.

        Returns:
            The stored job.

        """
        with self.database.read() as connection:
            row = connection.execute("SELECT job_id FROM extension_jobs WHERE kind='observer'").fetchone()
        assert row is not None
        job_id = row["job_id"]
        job = self.stores.jobs.read(observer_doubles.OWNER, self.observers.scope, job_id)
        assert job is not None
        return job

    def pending_inputs(self) -> tuple[StoredObservation, ...]:
        """Read the stored inputs that wait for interpretation.

        Returns:
            The pending observations.

        """
        return SqliteObservationRepository(self.database).pending_observations(MANY)

    def cursor(self) -> int:
        """Read the observer cursor of the fixture scope.

        Returns:
            The committed cursor.

        """
        return self.observers.committed_cursor(
            observer_doubles.OWNER, self.observers.scope, storage_doubles.HISTORY_REVISION, storage_doubles.GENERATION,
        )
