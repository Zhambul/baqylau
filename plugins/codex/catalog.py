"""Codex configuration vocabulary exposed through the harness contract."""

from __future__ import annotations

from contracts.harness import (
    CommandOption,
    EffortOption,
    HarnessCatalogSnapshot,
    ModelOption,
    QueryContext,
)
from plugins.codex import commands, modeldialog


class CodexCatalog:
    sections = frozenset({"models", "efforts", "commands"})

    def read(self, context: QueryContext) -> HarnessCatalogSnapshot:
        models = tuple(
            ModelOption(model_id, model_id, model_id == modeldialog.MODEL_CHOICES[0])
            for model_id in modeldialog.MODEL_CHOICES
        )
        efforts = tuple(
            EffortOption(effort, effort, effort == "low")
            for effort in modeldialog.EFFORT_CHOICES
        )
        command_options = tuple(
            CommandOption(
                command=row["name"],
                description=row.get("desc") or "",
                minimum_prompt_count=0,
            )
            for row in commands.slash_commands(context.working_directory or "")
        )
        return HarnessCatalogSnapshot(
            models=models,
            efforts=efforts,
            commands=command_options,
        )
