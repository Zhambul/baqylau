"""Claude Code menu vocabulary that depends on WHERE the session is."""

from __future__ import annotations

from collections.abc import Mapping

from harness.contract import HarnessCatalog
from harness.models import CommandOption, HarnessCatalogSnapshot, QueryContext
from harness.impl.claude_code import slashcmds

COMMAND_PROMPT_FLOORS: Mapping[str, int] = {"compact": 2, "rename": 1}


def _minimum_prompt_count(command: str) -> int:
    return COMMAND_PROMPT_FLOORS.get(command, 0)


class ClaudeCodeCatalog(HarnessCatalog):
    def __init__(self, configuration_directory: str) -> None:
        self.configuration_directory = configuration_directory

    def read(self, query_context: QueryContext) -> HarnessCatalogSnapshot:
        return HarnessCatalogSnapshot(
            commands=tuple(
                CommandOption(
                    command=row.name,
                    description=row.description,
                    minimum_prompt_count=_minimum_prompt_count(row.name),
                )
                for row in slashcmds.slash_commands(
                    query_context.working_directory or "",
                    self.configuration_directory,
                )
            ),
        )
