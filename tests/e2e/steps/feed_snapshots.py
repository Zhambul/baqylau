# Copyright (c) 2026 Zhambyl Yermagambet
"""Steps that check paged feed snapshots."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import parsers, then

if TYPE_CHECKING:
    from tests.e2e.testkit.references import FeedSnapshots


@then(parsers.parse('feed snapshot "{name}" uses more than one page'))
def feed_snapshot_uses_more_than_one_page(feed_snapshots: FeedSnapshots, name: str) -> None:
    """Verify a feed snapshot used multiple pages."""
    assert feed_snapshots.get(name).read.page_count > 1


@then(parsers.parse('feed snapshot "{name}" has unique entries'))
def feed_snapshot_has_unique_entries(feed_snapshots: FeedSnapshots, name: str) -> None:
    """Verify a feed snapshot has no repeated entries."""
    entries = feed_snapshots.get(name).read.snapshot.entries
    identities = [entry.entry_id for entry in entries]
    assert len(identities) == len(set(identities))


@then(parsers.parse('every entry in feed snapshot "{name}" is at or before its snapshot cursor'))
def feed_snapshot_has_one_cursor(feed_snapshots: FeedSnapshots, name: str) -> None:
    """Verify all feed entries are at or before the snapshot cursor."""
    snapshot = feed_snapshots.get(name).read.snapshot
    assert all(entry.cursor <= snapshot.cursor for entry in snapshot.entries)


@then(parsers.parse('feed snapshot "{new_name}" extends "{old_name}" only with newer entries'))
def feed_snapshot_extends_only_with_newer_entries(
    feed_snapshots: FeedSnapshots,
    new_name: str,
    old_name: str,
) -> None:
    """Verify a later snapshot preserves its earlier entries."""
    old = feed_snapshots.get(old_name)
    assert old.session == feed_snapshots.get(new_name).session
    old_entries = {entry.entry_id: entry for entry in old.read.snapshot.entries}
    new_entries = {entry.entry_id: entry for entry in feed_snapshots.get(new_name).read.snapshot.entries}
    assert old_entries.keys() < new_entries.keys()
    for identity, entry in old_entries.items():
        assert new_entries[identity] == entry
    assert all(
        entry.cursor > old.read.snapshot.entry_cursor
        for identity, entry in new_entries.items()
        if identity not in old_entries
    )
