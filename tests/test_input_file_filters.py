# Copyright (c) 2026 Zhambyl Yermagambet
"""Check input formats without coupling the watcher to a database name."""

from contextlib import closing
from pathlib import Path
from threading import Event

import pytest
from watchdog.events import FileCreatedEvent, FileModifiedEvent

from core.input_events import InputEvents


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/profile/session.jsonl", True),
        ("/profile/tasks/item.json", True),
        ("/profile/teams/item.json", True),
        ("/profile/state_5.sqlite-wal", False),
        ("/profile/logs_2.sqlite", False),
        ("/profile/settings.json", False),
        ("/profile/debug.log", False),
    ],
)
def test_input_filter(path: str, *, expected: bool) -> None:
    """Signal source formats, but ignore unrelated profile writes."""
    changed = Event()
    watcher = InputEvents(changed.set, ())
    watcher.start()
    with closing(watcher):
        watcher.on_any_event(FileModifiedEvent(path))
        assert changed.is_set() is expected


def test_named_database_signals(tmp_path: Path) -> None:
    """A database that a source names signals when its harness makes it again."""
    write_log = tmp_path / "state_5.sqlite-wal"
    changed = Event()
    watcher = InputEvents(changed.set, ())
    watcher.start()
    with closing(watcher):
        watcher.watch_files({write_log})
        watcher.on_any_event(FileCreatedEvent(str(write_log)))
        assert changed.is_set()
