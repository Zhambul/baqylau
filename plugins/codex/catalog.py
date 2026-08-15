"""Codex menu vocabulary that depends on WHERE the session is."""

from __future__ import annotations

from contracts.harness import (
    CommandOption,
    HarnessCatalog,
    HarnessCatalogSnapshot,
    QueryContext,
)
from plugins.codex import commands


class CodexCatalog(HarnessCatalog):
    def read(self, context: QueryContext) -> HarnessCatalogSnapshot:
        return HarnessCatalogSnapshot(
            commands=tuple(
                CommandOption(
                    command=row["name"],
                    description=row.get("desc") or "",
                    minimum_prompt_count=0,
                )
                for row in commands.slash_commands(context.working_directory or "")
            ),
        )
