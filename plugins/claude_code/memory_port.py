"""Claude Code implementation of the optional memory capability."""

from __future__ import annotations

import os

from contracts.harness import HarnessMemory, HarnessMemorySnapshot, MemoryDocument
from domain.ids import SessionId
from plugins.claude_code import memory
from plugins.claude_code import memory_state


class ClaudeCodeMemory(HarnessMemory):
    def enabled(self, working_directory: str) -> bool:
        return memory.in_scope(working_directory)

    def snapshot(self, session_id: SessionId) -> HarnessMemorySnapshot:
        return memory_state.snapshot(session_id)

    def item_count(self, session_id: SessionId) -> int:
        return memory_state.item_count(session_id)

    def document(self, path: str | None, stem: str | None) -> MemoryDocument:
        document_path = path or (memory.resolve(stem) if stem else None)
        frontmatter, body = memory.read_note(document_path) if document_path else (None, None)
        if body is None:
            return MemoryDocument(
                name=stem or os.path.basename(path or "") or "?",
                path="",
                frontmatter=(),
                body=None,
                backlinks=(),
            )
        name = os.path.basename(document_path)
        if name.endswith(".md"):
            name = name[:-3]
        return MemoryDocument(
            name=name,
            path=document_path,
            frontmatter=tuple(
                (str(key), str(value)) for key, value in (frontmatter or {}).items()
            ),
            body=body,
            backlinks=tuple(memory.backlinks(document_path)),
        )

memory_reader = ClaudeCodeMemory()
