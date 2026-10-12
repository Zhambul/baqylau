# Copyright (c) 2026 Zhambyl Yermagambet
"""Model shell."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _model_base import ContentRecord
    from _model_entry import EntryBodyRecord, EntryRecord

STATUS_STREAM = "status"


@dataclass(eq=False)
class ShellFold:
    """One command: its start, every chunk it wrote, and how it ended.

    `mode == "replace"` is why the chunks cannot simply be concatenated — a
    harness that reports its whole output at once sends one replacing chunk, and
    appending it to what the file watch already streamed would double it.
    """

    shell_id: str
    command: str
    execution: str
    started_at: float
    output: str = ""
    status: str = ""
    state: str | None = None
    exit_code: int | None = None
    backgrounded: bool = False
    finished_at: float | None = None
    # The entries that this fold holds, so that an overlapping frame folds each one once.
    entry_ids: set[str] = field(default_factory=set)

    @classmethod
    def from_entry(cls, entry: EntryRecord) -> ShellFold:
        """Build a shell fold from its start entry.

        Returns:
            A shell fold from its start entry.

        """
        body = entry.body
        return cls(
            shell_id=body.shell_id,
            command=_text(body.command),
            execution=body.execution or "foreground",
            started_at=entry.occurred_at,
            entry_ids={entry.entry_id},
        )

    def fold(self, entry: EntryRecord) -> None:
        if entry.entry_id in self.entry_ids:
            return
        self.entry_ids.add(entry.entry_id)
        body = entry.body
        if entry.type == "shell_output":
            self._fold_output(body)
        elif entry.type == "shell_backgrounded":
            self.backgrounded = True
        elif entry.type == "shell_finished":
            self.state = body.state
            self.exit_code = body.exit_code
            self.finished_at = entry.occurred_at
            # A harness that streamed nothing reports the whole output here, and
            # it is folded exactly as a replacing chunk would be — because that
            # is what it is. Claude Code streams and leaves this empty; Codex
            # reports it once and streams nothing.
            result = _text(body.result)
            if result:
                self.output = result

    def _fold_output(self, body: EntryBodyRecord) -> None:
        text = _text(body.content)
        stream = STATUS_STREAM if body.stream == STATUS_STREAM else "output"
        current = self.status if stream == STATUS_STREAM else self.output
        next_output = text if body.mode == "replace" else current + text
        if stream == STATUS_STREAM:
            self.status = next_output
        else:
            self.output = next_output


def _text(content: ContentRecord | None) -> str:
    return "" if content is None else content.text
