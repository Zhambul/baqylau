# Copyright (c) 2026 Zhambyl Yermagambet
"""Start complete native input owners for source watch tests."""

from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from time import time

from core.input_events import InputEvents
from core.work_queue import WorkQueue
from engine.source_processing import EngineSourceReads


@dataclass
class WatchedSource:
    """Keep a real native watcher and its signal with the test source path."""

    path: Path
    changed: Event = field(default_factory=Event)
    inputs: InputEvents = field(init=False)

    def __post_init__(self) -> None:
        """Construct native resources before their test-owned lifetime starts."""
        self.inputs = InputEvents(self.changed.set, ())

    def select(self) -> None:
        """Use the actual engine path expansion and independent watch group."""
        reader = EngineSourceReads(self.inputs, WorkQueue(), time)
        reader.watch_sources(frozenset((self.path,)))
        self.changed.clear()

    def append(self, text: str = "next\n") -> None:
        """Flush a new write after a complete watch selection."""
        self.changed.clear()
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            assert self.changed.wait(5)

    def replace_link(self, target: Path) -> None:
        """Replace a test link, then select its new physical target."""
        self.changed.clear()
        self.path.unlink()
        self.path.symlink_to(target)
        assert self.changed.wait(5)
        self.select()


@contextmanager
def watching(path: Path) -> Iterator[WatchedSource]:
    """Keep all native descriptors inside the test lifetime.

    Yields:
        A started native source watcher.

    """
    source = WatchedSource(path)
    source.inputs.start()
    with closing(source.inputs):
        source.select()
        yield source
