# Copyright (c) 2026 Zhambyl Yermagambet
"""Build a committed observer runtime and its doubles for the observer pass tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from extensions.observer_models import ObserverStores
from extensions.observer_pass import ObserverPass
from tests import (
    observer_case,
    observer_doubles,
    observer_storage_doubles as storage_doubles,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_fixtures as core,
)
from tests.extension_api import observer_samples
from tests.extension_host import canonical_history_fixture, observation_fixture

if TYPE_CHECKING:
    from pathlib import Path

    from baqylau_extension_api.manifest.package import ExtensionManifest

    from extensions.projection_models import ProjectionFacts
    from repository.impl.sqlite.connection import SqliteDatabase


def a_case(
    directory: Path,
    event_type: str = observer_samples.EVENT_TYPE,
    manifest: ExtensionManifest | None = None,
) -> observer_case.ObserverCase:
    """Commit an observer runtime and record its committed trigger.

    Returns:
        A pass over real storage with the trigger already stored.

    """
    installed = observation_fixture.installed(directory, manifest or observer_samples.manifest())
    database = installed.store.database
    _record_trigger(database)
    observers = storage_doubles.FixedScopeObservers(database, observer_samples.request().binding.scope)
    observer = observer_doubles.FakeObserver()
    jobs = repository_dependencies.SqliteExtensionJobRepository(database)
    return observer_case.ObserverCase(
        database=database,
        observers=observers,
        observer_pass=ObserverPass(
            facts=cast("ProjectionFacts", storage_doubles.FakeFacts(storage_doubles.a_page(event_type))),
            observers=observers, jobs=jobs,
        ),
        stores=ObserverStores(observers=observers, jobs=jobs),
        observer=observer,
        package=observer_doubles.an_observer_package(
            observer, installed.request.runtime_revision, manifest or observer_samples.manifest(),
        ),
        runtime_revision=installed.request.runtime_revision,
    )


def _record_trigger(database: SqliteDatabase) -> None:
    trigger = core.a_started_event(observer_samples.request().binding.event_id)
    canonical_history_fixture.insert_core(database, trigger, storage_doubles.HISTORY_REVISION)
