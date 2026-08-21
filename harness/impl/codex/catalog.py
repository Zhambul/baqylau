"""Codex menu vocabulary that depends on WHERE the session is."""

from __future__ import annotations

from harness.contract import HarnessCatalog
from harness.models import CommandOption, HarnessCatalogSnapshot, QueryContext
from harness.impl.codex import commands


class CodexCatalog(HarnessCatalog):
    def read(self, query_context: QueryContext) -> HarnessCatalogSnapshot:
        return HarnessCatalogSnapshot(
            commands=tuple(
                CommandOption(
                    command=row["name"],
                    description=row.get("desc") or "",
                    minimum_prompt_count=0,
                )
                for row in commands.slash_commands(query_context.working_directory or "")
            ),
        )
