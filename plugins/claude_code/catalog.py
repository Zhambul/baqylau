"""Claude Code configuration vocabulary exposed through the harness contract."""

from __future__ import annotations

import os

from contracts.harness import (
    AccountOption,
    CommandOption,
    EffortOption,
    HarnessCatalog,
    HarnessCatalogSnapshot,
    ModelOption,
    QueryContext,
    RewindModeOption,
)
from plugins.claude_code import account, model, rewindmenu, slashcmds

MODEL_IDS = ("fable", "opus", "sonnet", "haiku")
EFFORT_VALUES = ("low", "medium", "high", "xhigh", "max")
DEFAULT_MODEL_ID = MODEL_IDS[0]
DEFAULT_EFFORT = "high"
COMMAND_PROMPT_FLOORS = {"compact": 2, "rename": 1}
SPEECH_TERMS_FILE = "deepgram-keyterms"


class ClaudeCodeCatalog(HarnessCatalog):
    sections = frozenset(
        {
            "models",
            "efforts",
            "accounts",
            "commands",
            "rewind_modes",
            "speech_terms",
        }
    )

    def read(self, context: QueryContext) -> HarnessCatalogSnapshot:
        models = tuple(
            ModelOption(model_id, model_id, model_id == DEFAULT_MODEL_ID)
            for model_id in MODEL_IDS
        )
        efforts = tuple(
            EffortOption(effort, effort, effort == DEFAULT_EFFORT)
            for effort in EFFORT_VALUES
        )
        accounts = tuple(
            AccountOption(row["slug"], row["label"], True)
            for row in account.registry()
        )
        commands = tuple(
            CommandOption(
                command=row["name"],
                description=row.get("desc") or "",
                minimum_prompt_count=COMMAND_PROMPT_FLOORS.get(row["name"], 0),
            )
            for row in slashcmds.slash_commands(context.working_directory or "")
        )
        rewind_modes = tuple(
            RewindModeOption(mode, label)
            for mode, label in rewindmenu.MODE_LABELS.items()
        )
        speech_terms = _speech_terms(context.working_directory or "")
        return HarnessCatalogSnapshot(
            models=models,
            efforts=efforts,
            accounts=accounts,
            commands=commands,
            rewind_modes=rewind_modes,
            speech_terms=speech_terms,
        )


def _speech_terms(working_directory: str) -> tuple[str, ...]:
    terms = []
    seen = set()
    for configuration_directory in model.claude_dirs(
        working_directory,
        env_pin=False,
    ):
        path = os.path.join(configuration_directory, SPEECH_TERMS_FILE)
        try:
            with open(path, encoding="utf-8") as speech_file:
                lines = speech_file.read().splitlines()
        except OSError:
            continue
        for line in lines:
            term = line.strip()
            if term and not term.startswith("#") and term not in seen:
                seen.add(term)
                terms.append(term)
    return tuple(terms)
