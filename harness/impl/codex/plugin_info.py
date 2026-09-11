# Copyright (c) 2026 Zhambyl Yermagambet
"""Define static Codex plug-in metadata."""

from domain.events import SCHEMA_VERSION
from domain.ids import HarnessName
from harness.impl.codex.controls import modeldialog
from harness.impl.codex.hooks.gateway import CLI_PROCESS_NAME
from harness.impl.codex.model import CodexModel
from harness.models.catalog import EffortOption, ModelOption, RewindModeOption
from harness.models.info import HarnessInfo

LUNA_EFFORTS = tuple(effort for effort in modeldialog.EFFORT_CHOICES if effort != "ultra")


def _efforts(codex_model: CodexModel) -> tuple[EffortOption, ...]:
    supported_efforts = LUNA_EFFORTS if codex_model == "gpt-5.6-luna" else modeldialog.EFFORT_CHOICES
    return tuple(EffortOption(effort, effort, effort == "low") for effort in supported_efforts)


MODELS = tuple(
    ModelOption(
        model_id,
        model_id,
        model_id == modeldialog.MODEL_CHOICES[0],
        _efforts(model_id),
    )
    for model_id in modeldialog.MODEL_CHOICES
)

HARNESS_INFO = HarnessInfo(
    name=HarnessName("codex"),
    display_name="Codex",
    plugin_version="9",
    canonical_version=SCHEMA_VERSION,
    cli_process_name=CLI_PROCESS_NAME,
    supports_attachments=True,
    supports_native_initial_naming=True,
    supports_native_automatic_renaming=False,
    requires_initial_message=True,
    rewind_modes=(RewindModeOption("conversation", "Restore conversation"),),
    models=MODELS,
)
