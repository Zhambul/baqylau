"""Named feed reads and independent feed checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from sdk.client import BaqylauClient
from tests.e2e.testkit.references import (
    FeedSnapshotRef,
    FeedSnapshots,
    GlobalStreamUpdates,
    SessionStreamUpdateRef,
    SessionStreamUpdates,
    Sessions,
    StreamCheckpointRef,
    StreamCheckpoints,
)


@when(parsers.parse(
    'I read feed snapshot "{snapshot_name}" for session "{session_name}" '
    "with page size {page_size:d}"
))
def read_feed_snapshot(
    client: BaqylauClient,
    sessions: Sessions,
    feed_snapshots: FeedSnapshots,
    snapshot_name: str,
    session_name: str,
    page_size: int,
) -> None:
    session = sessions.get(session_name)
    feed_snapshots.bind(
        snapshot_name,
        FeedSnapshotRef(
            session,
            client.sessions.read_snapshot(session, page_size=page_size),
        ),
    )


@when(parsers.parse(
    'I save stream checkpoint "{checkpoint_name}" from feed snapshot "{snapshot_name}"'
))
def save_stream_checkpoint(
    client: BaqylauClient,
    feed_snapshots: FeedSnapshots,
    stream_checkpoints: StreamCheckpoints,
    checkpoint_name: str,
    snapshot_name: str,
) -> None:
    snapshot = feed_snapshots.get(snapshot_name)
    stream_checkpoints.bind(
        checkpoint_name,
        StreamCheckpointRef(
            snapshot.session,
            snapshot.read.snapshot.cursor,
            client.sessions.list().cursor,
        ),
    )


@when(parsers.parse(
    'I read session stream update "{update_name}" after stream checkpoint '
    '"{checkpoint_name}"'
))
def read_session_stream_update(
    client: BaqylauClient,
    stream_checkpoints: StreamCheckpoints,
    session_stream_updates: SessionStreamUpdates,
    update_name: str,
    checkpoint_name: str,
) -> None:
    checkpoint = stream_checkpoints.get(checkpoint_name)
    session_stream_updates.bind(
        update_name,
        SessionStreamUpdateRef(
            checkpoint.session,
            client.streams.next_session_update(
                checkpoint.session,
                after_cursor=checkpoint.session_cursor,
            ),
        ),
    )


@when(parsers.parse(
    'I read global stream update "{update_name}" after stream checkpoint '
    '"{checkpoint_name}"'
))
def read_global_stream_update(
    client: BaqylauClient,
    stream_checkpoints: StreamCheckpoints,
    global_stream_updates: GlobalStreamUpdates,
    update_name: str,
    checkpoint_name: str,
) -> None:
    checkpoint = stream_checkpoints.get(checkpoint_name)
    global_stream_updates.bind(
        update_name,
        client.streams.next_global_update(after_cursor=checkpoint.global_cursor),
    )


@when(parsers.parse(
    'I reconnect session stream as update "{new_name}" after session stream update '
    '"{old_name}" with query cursor {query_cursor:d}'
))
def reconnect_session_stream(
    client: BaqylauClient,
    session_stream_updates: SessionStreamUpdates,
    new_name: str,
    old_name: str,
    query_cursor: int,
) -> None:
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


@then(parsers.parse('feed snapshot "{name}" uses more than one page'))
def feed_snapshot_uses_more_than_one_page(
    feed_snapshots: FeedSnapshots,
    name: str,
) -> None:
    assert feed_snapshots.get(name).read.page_count > 1


@then(parsers.parse('feed snapshot "{name}" has unique entries'))
def feed_snapshot_has_unique_entries(feed_snapshots: FeedSnapshots, name: str) -> None:
    entries = feed_snapshots.get(name).read.snapshot.entries
    identities = [entry.entry_id for entry in entries]
    assert len(identities) == len(set(identities))


@then(parsers.parse(
    'every entry in feed snapshot "{name}" is at or before its snapshot cursor'
))
def feed_snapshot_has_one_cursor(feed_snapshots: FeedSnapshots, name: str) -> None:
    snapshot = feed_snapshots.get(name).read.snapshot
    assert all(entry.cursor <= snapshot.cursor for entry in snapshot.entries)


@then(parsers.parse(
    'feed snapshot "{new_name}" extends "{old_name}" only with newer entries'
))
def feed_snapshot_extends_only_with_newer_entries(
    feed_snapshots: FeedSnapshots,
    new_name: str,
    old_name: str,
) -> None:
    old = feed_snapshots.get(old_name)
    new = feed_snapshots.get(new_name)
    assert old.session == new.session
    old_entries = {entry.entry_id: entry for entry in old.read.snapshot.entries}
    new_entries = {entry.entry_id: entry for entry in new.read.snapshot.entries}
    assert old_entries.keys() < new_entries.keys()
    assert all(new_entries[identity] == entry for identity, entry in old_entries.items())
    added = [entry for identity, entry in new_entries.items() if identity not in old_entries]
    assert all(entry.cursor > old.read.snapshot.cursor for entry in added)


@then(parsers.parse(
    'session stream update "{update_name}" contains activity after checkpoint '
    '"{checkpoint_name}"'
))
def session_stream_update_contains_new_activity(
    session_stream_updates: SessionStreamUpdates,
    stream_checkpoints: StreamCheckpoints,
    update_name: str,
    checkpoint_name: str,
) -> None:
    found = session_stream_updates.get(update_name)
    checkpoint = stream_checkpoints.get(checkpoint_name)
    assert found.session == checkpoint.session
    assert found.update.cursor > checkpoint.session_cursor
    frame = found.update.frame
    assert frame.session is not None or frame.actors or frame.entries
    if frame.session is not None:
        assert frame.session.session_id == found.session.session_id
    assert all(actor.session_id == found.session.session_id for actor in frame.actors)
    assert all(
        checkpoint.session_cursor < entry.cursor <= found.update.cursor
        for entry in frame.entries
    )


@then(parsers.parse(
    'global stream update "{update_name}" reports session "{session_name}" '
    'after checkpoint "{checkpoint_name}"'
))
def global_stream_update_reports_session(
    global_stream_updates: GlobalStreamUpdates,
    stream_checkpoints: StreamCheckpoints,
    sessions: Sessions,
    update_name: str,
    session_name: str,
    checkpoint_name: str,
) -> None:
    update = global_stream_updates.get(update_name)
    checkpoint = stream_checkpoints.get(checkpoint_name)
    session = sessions.get(session_name)
    assert update.cursor > checkpoint.global_cursor
    session_ids = {item.session_id for item in update.frame.sessions}
    session_ids.update(actor.session_id for actor in update.frame.actors)
    assert session.session_id in session_ids


@then(parsers.parse(
    'session stream update "{new_name}" is newer than "{old_name}" '
    "and has session title '{title}'"
))
def reconnected_stream_has_new_session_title(
    session_stream_updates: SessionStreamUpdates,
    new_name: str,
    old_name: str,
    title: str,
) -> None:
    old = session_stream_updates.get(old_name)
    new = session_stream_updates.get(new_name)
    assert new.session == old.session
    assert new.update.cursor > old.update.cursor
    assert new.update.frame.session is not None
    assert new.update.frame.session.session_id == new.session.session_id
    assert new.update.frame.session.title == title


@then(parsers.parse(
    'session stream update "{new_name}" repeats no entry from "{old_name}"'
))
def reconnected_stream_repeats_no_entry(
    session_stream_updates: SessionStreamUpdates,
    new_name: str,
    old_name: str,
) -> None:
    old_entries = {
        entry.entry_id for entry in session_stream_updates.get(old_name).update.frame.entries
    }
    new_entries = {
        entry.entry_id for entry in session_stream_updates.get(new_name).update.frame.entries
    }
    assert old_entries.isdisjoint(new_entries)
