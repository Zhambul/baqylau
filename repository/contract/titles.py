"""A session's NATIVE title — the parked rename.

The dashboard renames a LIVE session by typing into its terminal. A parked one
has nothing running to type into, so the name has to be written where the
harness itself keeps it. Where that is differs completely per harness: one keeps
it in its own SQLite index, another appends a naming record to the transcript.

Two implementations, both plugin-side, because a shared package may not contain
a harness's name.
"""

from __future__ import annotations

from typing import Protocol

from harness.models import TitleWriteOutcome


class NativeSessionTitleRepository(Protocol):
    def renameable(self, source_reference: str) -> bool:
        """True for a source this harness owns — the gate that keeps one
        harness's rename off another's session."""
        ...

    def set_title(self, source_reference: str, title: str) -> TitleWriteOutcome: ...
