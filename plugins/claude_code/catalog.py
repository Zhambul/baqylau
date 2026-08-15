"""Claude Code menu vocabulary that depends on WHERE the session is."""

from __future__ import annotations

from contracts.harness import (
    CommandOption,
    HarnessCatalog,
    HarnessCatalogSnapshot,
    QueryContext,
)
from plugins.claude_code import slashcmds

COMMAND_PROMPT_FLOORS = {"compact": 2, "rename": 1}


class ClaudeCodeCatalog(HarnessCatalog):
    def read(self, context: QueryContext) -> HarnessCatalogSnapshot:
        return HarnessCatalogSnapshot(
            commands=tuple(
                CommandOption(
                    command=row["name"],
                    description=row.get("desc") or "",
                    minimum_prompt_count=COMMAND_PROMPT_FLOORS.get(row["name"], 0),
                )
                for row in slashcmds.slash_commands(context.working_directory or "")
            ),
        )
