# Copyright (c) 2026 Zhambyl Yermagambet
"""Let enabled projection transforms change core rows before the one commit (P05-T01)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.schemas import SchemaSet

from domain.entries import EntryTypeName
from extensions import core_projection_transforms, pass_health
from extensions.models.registry import RuntimeSettings
from extensions.transformer_packages import ProjectionTransformerPackage
from tests import (
    canonical_sessiondata_fixtures as session_fixtures,
    canonical_sessiondata_loop_support as loop_support,
    canonical_sessiondata_values as session_values,
    core_transform_doubles as doubles,
)
from tests.canonical_sessiondata_components import domain as session_domain

if TYPE_CHECKING:
    from pathlib import Path

    from baqylau_extension_api.contracts import projection

    from repository.impl.sqlite.session_data import SqliteSessionDataRepository

STARTUP_AND_ACTION_EVENTS = 3


def package(transform: projection.ExtensionProjectionTransformer) -> ProjectionTransformerPackage:
    """Build one transform package that selects the shell start fact.

    Returns:
        The transform package.

    """
    return ProjectionTransformerPackage(
        extension_id=doubles.CORE_TRANSFORM_MANIFEST.extension_id,
        runtime_revision="runtime-one",
        settings=RuntimeSettings(),
        manifest=doubles.CORE_TRANSFORM_MANIFEST,
        schemas=SchemaSet(doubles.CORE_TRANSFORM_MANIFEST.schemas),
        transformer=transform,
    )


def drained(tmp_path: Path, *transforms: projection.ExtensionProjectionTransformer) -> tuple[
    SqliteSessionDataRepository, list[str],
]:
    """Drain one started session and one shell start through the core transforms.

    Returns:
        The read model and the recorded transform failures.

    """
    loop, read_model, _audit = loop_support.loop_over(tmp_path, (
        *session_fixtures.alive(),
        session_domain.event_shell.ShellStarted(
            session_values.PRIMARY_SHELL_ID,
            session_values.SHELL_COMMAND_CONTENT,
            session_domain.outcomes.ExecutionMode.FOREGROUND,
            None,
        ),
    ))
    failures: list[str] = []
    health = pass_health.AuditOnlyHealth(lambda _owner: failures.append("failed"))
    loop.drain(bool, core_projection_transforms.CoreProjectionTransforms(
        tuple(package(selected) for selected in transforms), health,
    ))
    return read_model, failures


def test_transform_replaces_a_core_feed_row(tmp_path: Path) -> None:
    """A replaced core row commits in place of the proposal; the actor state still commits."""
    read_model, failures = drained(tmp_path, doubles.SummaryTransformer())

    entries = read_model.entries_page(session_values.SESSION, limit=10).entries
    rows = [(entry.entry_type, entry.summary) for entry in entries]
    assert rows == [("shell_started", doubles.CHANGED_SUMMARY)]
    assert failures == []
    actors = session_fixtures.required_data(read_model).actors
    assert [actor.status for actor in actors] == [session_values.EXECUTING_STATE]


def test_transform_adds_a_row_in_the_same_commit(tmp_path: Path) -> None:
    """An inserted extension row follows the core row and shares its commit."""
    read_model, _failures = drained(tmp_path, doubles.ExtraEntryTransformer())

    entries = read_model.entries_page(session_values.SESSION, limit=10).entries
    assert [entry.entry_type for entry in entries] == ["shell_started", EntryTypeName.EXTENSION]
    delta = read_model.delta(session_values.SESSION, entries[0].cursor - 1)
    assert [entry.entry_type for entry in delta.entries] == ["shell_started", EntryTypeName.EXTENSION]


def test_failed_transform_keeps_the_core_proposal(tmp_path: Path) -> None:
    """A failing transform does not stop core progress; later transforms still run."""
    read_model, failures = drained(tmp_path, doubles.FailingTransformer(), doubles.SummaryTransformer())

    entries = read_model.entries_page(session_values.SESSION, limit=10).entries
    assert [entry.summary for entry in entries] == [doubles.CHANGED_SUMMARY]
    assert failures == ["failed"]
    assert read_model.progress() == STARTUP_AND_ACTION_EVENTS


def test_transform_suppresses_a_core_feed_row(tmp_path: Path) -> None:
    """A dropped core row is not stored, and the actor state of the same event still commits."""
    read_model, failures = drained(tmp_path, doubles.DropEntryTransformer())

    assert read_model.entries_page(session_values.SESSION, limit=10).entries == ()
    assert failures == []
    actors = session_fixtures.required_data(read_model).actors
    assert [actor.status for actor in actors] == [session_values.EXECUTING_STATE]
    assert read_model.progress() == STARTUP_AND_ACTION_EVENTS
