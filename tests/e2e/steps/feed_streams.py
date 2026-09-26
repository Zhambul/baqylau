# Copyright (c) 2026 Zhambyl Yermagambet
"""Steps that check session and global stream updates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import parsers, then

if TYPE_CHECKING:
    from tests.e2e.testkit.observation_contexts import GlobalStreamObservationContext
    from tests.e2e.testkit.references import (
        SessionStreamUpdateRef,
        SessionStreamUpdates,
        StreamCheckpointRef,
        StreamCheckpoints,
    )


@then(parsers.parse('session stream update "{update_name}" contains activity after checkpoint "{checkpoint_name}"'))
def session_stream_update_contains_new_activity(
    session_stream_updates: SessionStreamUpdates,
    stream_checkpoints: StreamCheckpoints,
    update_name: str,
    checkpoint_name: str,
) -> None:
    """Verify a session stream update follows its checkpoint."""
    found = session_stream_updates.get(update_name)
    checkpoint = stream_checkpoints.get(checkpoint_name)
    assert found.session == checkpoint.session
    assert found.update.cursor > checkpoint.session_cursor
    assert_session_update_frame(found, checkpoint)


def assert_session_update_frame(found: SessionStreamUpdateRef, checkpoint: StreamCheckpointRef) -> None:
    """Verify that session frame items follow a checkpoint."""
    frame = found.update.frame
    assert frame.session is not None or frame.actors or frame.entries
    if frame.session is not None:
        assert frame.session.session_id == found.session.session_id
    assert all(actor.session_id == found.session.session_id for actor in frame.actors)
    for entry in frame.entries:
        assert entry.cursor > checkpoint.entry_cursor


@then(
    parsers.parse(
        'global stream update "{update_name}" reports session "{session_name}" '
        'after checkpoint "{checkpoint_name}"',
    ),
)
def global_stream_update_reports_session(
    global_stream_observation_context: GlobalStreamObservationContext,
    update_name: str,
    session_name: str,
    checkpoint_name: str,
) -> None:
    """Verify a global update reports the named session."""
    update = global_stream_observation_context.updates.get(update_name)
    checkpoint = global_stream_observation_context.checkpoints.get(checkpoint_name)
    session = global_stream_observation_context.sessions.get(session_name)
    assert update.cursor > checkpoint.global_cursor
    session_ids = {session_record.session_id for session_record in update.frame.sessions}
    session_ids.update(actor.session_id for actor in update.frame.actors)
    assert session.session_id in session_ids


@then(parsers.parse('session stream update "{new_name}" is newer than "{old_name}" and has session title \'{title}\''))
def reconnected_stream_has_new_session_title(
    session_stream_updates: SessionStreamUpdates,
    new_name: str,
    old_name: str,
    title: str,
) -> None:
    """Verify a reconnect returns a newer titled session state."""
    old = session_stream_updates.get(old_name)
    new = session_stream_updates.get(new_name)
    assert new.session == old.session
    assert new.update.cursor > old.update.cursor
    assert new.update.frame.session is not None
    session = new.update.frame.session
    assert (session.session_id, session.title) == (new.session.session_id, title)


@then(parsers.parse('session stream update "{new_name}" repeats no entry from "{old_name}"'))
def reconnected_stream_repeats_no_entry(
    session_stream_updates: SessionStreamUpdates,
    new_name: str,
    old_name: str,
) -> None:
    """Verify a reconnected stream does not repeat entries."""
    old_entries = {entry.entry_id for entry in session_stream_updates.get(old_name).update.frame.entries}
    new_entries = {entry.entry_id for entry in session_stream_updates.get(new_name).update.frame.entries}
    assert old_entries.isdisjoint(new_entries)
