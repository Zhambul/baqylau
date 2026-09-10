# Copyright (c) 2026 Zhambyl Yermagambet
"""Define static Claude Code plug-in metadata."""

from domain.events import SCHEMA_VERSION
from domain.ids import HarnessName
from harness.impl.claude_code import model, model_names
from harness.impl.claude_code.controls import rewind_models
from harness.impl.claude_code.hooks.constants import CLI_PROCESS_NAME
from harness.models.catalog import EffortOption, ModelOption, RewindModeOption
from harness.models.info import HarnessInfo

MODEL_IDS = (*model.CLAUDE_CODE_MODELS[:4], model.ClaudeCodeModel.CLAUDE_OPUS_FOUR_SIX)
MODEL_ALIASES = tuple(model_id.value for model_id in MODEL_IDS)
EFFORT_VALUES = tuple(model.ClaudeCodeEffort)
DEFAULT_MODEL_ID = MODEL_IDS[0]
DEFAULT_EFFORT = "high"


def _effort_option(effort: model.ClaudeCodeEffort) -> EffortOption:
    return EffortOption(effort, effort, effort == DEFAULT_EFFORT)


def _rewind_modes() -> tuple[RewindModeOption, ...]:
    return tuple(
        RewindModeOption(rewind_mode.value, rewind_mode.label) for rewind_mode in rewind_models.ClaudeCodeRewindMode
    )


EFFORTS = tuple(_effort_option(effort) for effort in EFFORT_VALUES)
MODELS = tuple(
    ModelOption(
        model_id,
        model_names.alias_display(model_names.short_model(model_id)),
        model_id == DEFAULT_MODEL_ID,
        EFFORTS,
    )
    for model_id in MODEL_IDS
)
REWIND_MODES = _rewind_modes()

HARNESS_INFO = HarnessInfo(
    name=HarnessName.CLAUDE_CODE,
    display_name="Claude Code",
    plugin_version="3",
    canonical_version=SCHEMA_VERSION,
    cli_process_name=CLI_PROCESS_NAME,
    supports_attachments=True,
    default_for_launch=True,
    supports_accounts=False,
    supports_native_initial_naming=True,
    supports_native_automatic_renaming=True,
    supports_readable_compaction_context=True,
    models=MODELS,
    rewind_modes=REWIND_MODES,
)
