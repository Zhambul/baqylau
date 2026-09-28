# Copyright (c) 2026 Zhambyl Yermagambet
"""Commit observer jobs with their consumer cursor."""

from __future__ import annotations

from domain.ids import CanonicalEventId, ExtensionJobId
from repository.contract.extension_jobs import ObserverJobRequest
from repository.contract.extension_observers import ObserverAcceptance, ObserverCursor
from tests import sqlite_repository_dependencies as repository_dependencies
from tests.extension_api import observer_samples

HISTORY_REVISION = "default"
GENERATION = "default"
FIRST_CURSOR = 7
SECOND_CURSOR = 8


def a_job(consumer_cursor: int) -> ObserverJobRequest:
    """Build one host observer job from the SDK sample.

    Returns:
        The host observer job.

    """
    sample = observer_samples.request()
    return ObserverJobRequest(
        owner=sample.binding.extension_id,
        scope=sample.binding.scope,
        job_id=ExtensionJobId(sample.binding.job_id),
        cause_event_id=CanonicalEventId(sample.binding.event_id),
        binding=sample.binding.model_dump_json(),
        request=sample.model_dump_json(),
        consumer_cursor=consumer_cursor,
    )


def an_acceptance(consumer_cursor: int) -> ObserverAcceptance:
    """Build one acceptance for the sample cause and scope.

    Returns:
        The observer acceptance.

    """
    job = a_job(consumer_cursor)
    return ObserverAcceptance(ObserverCursor(
        owner=job.owner,
        scope=job.scope,
        history_revision=HISTORY_REVISION,
        generation=GENERATION,
        commit_cursor=consumer_cursor,
    ), job)


def test_accept_stores_the_job_and_moves_cursor(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """An accepted observer job commits with its cursor."""
    repository = repository_dependencies.SqliteExtensionObserverRepository(main)
    acceptance = an_acceptance(FIRST_CURSOR)

    assert repository.committed_cursor(
        acceptance.cursor.owner, acceptance.cursor.scope, HISTORY_REVISION, GENERATION,
    ) == 0

    job = repository.accept(acceptance)

    assert job.kind == "observer"
    assert repository.committed_cursor(
        acceptance.cursor.owner, acceptance.cursor.scope, HISTORY_REVISION, GENERATION,
    ) == FIRST_CURSOR


def test_repeated_cause_keeps_one_job(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A repeated cause returns the first job and advances the cursor."""
    repository = repository_dependencies.SqliteExtensionObserverRepository(main)
    first = repository.accept(an_acceptance(FIRST_CURSOR))
    second = repository.accept(an_acceptance(SECOND_CURSOR))

    assert first.job_id == second.job_id
    assert repository.committed_cursor(
        second.owner, second.scope, HISTORY_REVISION, GENERATION,
    ) == SECOND_CURSOR
