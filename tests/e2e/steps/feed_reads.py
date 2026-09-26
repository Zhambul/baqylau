# Copyright (c) 2026 Zhambyl Yermagambet
"""Steps that read feed snapshots and stream updates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import parsers, when

from tests.e2e.testkit.references import FeedSnapshotRef, SessionStreamUpdateRef, StreamCheckpointRef

if TYPE_CHECKING:
    from sdk.client import BaqylauClient
    from tests.e2e.testkit.observation_contexts import FeedReadContext
    from tests.e2e.testkit.references import (
        FeedSnapshots,
        GlobalStreamUpdates,
        SessionStreamUpdates,
        StreamCheckpoints,
    )


@when(parsers.parse('I read feed snapshot "{snapshot_name}" for session "{session_name}" with page size {page_size:d}'))
def read_feed_snapshot(
    feed_read_context: FeedReadContext,
    snapshot_name: str,
    session_name: str,
    page_size: int,
) -> None:
    """Read and name one paged session feed."""
    session = feed_read_context.sessions.get(session_name)
    feed_read_context.snapshots.bind(
        snapshot_name,
        FeedSnapshotRef(session, feed_read_context.client.sessions.read_snapshot(session, page_size=page_size)),
    )


@when(parsers.parse('I save stream checkpoint "{checkpoint_name}" from feed snapshot "{snapshot_name}"'))
def save_stream_checkpoint(
    client: BaqylauClient,
    feed_snapshots: FeedSnapshots,
    stream_checkpoints: StreamCheckpoints,
    checkpoint_name: str,
    snapshot_name: str,
) -> None:
    """Save the session and global cursors from a feed snapshot."""
    snapshot = feed_snapshots.get(snapshot_name)
    stream_checkpoints.bind(
        checkpoint_name,
        StreamCheckpointRef(
            snapshot.session,
            snapshot.read.snapshot.cursor,
            client.sessions.list().cursor,
            snapshot.read.snapshot.entry_cursor,
        ),
    )


@when(parsers.parse('I read session stream update "{update_name}" after stream checkpoint "{checkpoint_name}"'))
def read_session_stream_update(
    client: BaqylauClient,
    stream_checkpoints: StreamCheckpoints,
    session_stream_updates: SessionStreamUpdates,
    update_name: str,
    checkpoint_name: str,
) -> None:
    """Read and name the next session stream update."""
    checkpoint = stream_checkpoints.get(checkpoint_name)
    session_stream_updates.bind(
        update_name,
        SessionStreamUpdateRef(
            checkpoint.session,
            client.streams.next_session_update(checkpoint.session, after_cursor=checkpoint.session_cursor),
        ),
    )


@when(parsers.parse('I read global stream update "{update_name}" after stream checkpoint "{checkpoint_name}"'))
def read_global_stream_update(
    client: BaqylauClient,
    stream_checkpoints: StreamCheckpoints,
    global_stream_updates: GlobalStreamUpdates,
    update_name: str,
    checkpoint_name: str,
) -> None:
    """Read and name the next global stream update."""
    checkpoint = stream_checkpoints.get(checkpoint_name)
    global_stream_updates.bind(update_name, client.streams.next_global_update(after_cursor=checkpoint.global_cursor))


@when(
    parsers.parse(
        'I reconnect session stream as update "{new_name}" after session stream update '
        '"{old_name}" with query cursor {query_cursor:d}',
    ),
)
def reconnect_session_stream(
    client: BaqylauClient,
    session_stream_updates: SessionStreamUpdates,
    new_name: str,
    old_name: str,
    query_cursor: int,
) -> None:
    """Reconnect a session stream after one known update."""
    previous = session_stream_updates.get(old_name)
    session_stream_updates.bind(
        new_name,
        SessionStreamUpdateRef(
            previous.session,
            client.streams.next_session_update(
                previous.session,
                after_cursor=query_cursor,
                last_event_id=previous.update.cursor,
            ),
        ),
    )
