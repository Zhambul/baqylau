"""Codex's single public harness-plugin descriptor."""

from contracts.harness import EffortOption, HarnessInfo, HarnessPlugin, ModelOption
from domain.codec import SCHEMA_VERSION
from plugins.codex.canonical import (
    CodexCanonicalTranslator,
    CodexRawEventSources,
)
from plugins.codex.hooks import CLI_PROCESS_NAME, CodexHookGateway
from plugins.codex.catalog import CodexCatalog
from plugins.codex.controller import controller
from plugins.codex.launcher import CodexLauncher
from plugins.codex.usage_rows import usage_reader
from plugins.codex import modeldialog

# codex sets model and effort through ONE picker, and its reasoning levels are
# per-model: measured on codex-cli 0.147.0, gpt-5.6-luna's advanced sub-step
# holds Max alone, with no Ultra row. Only that model's list was read, so the
# others keep the full vocabulary rather than assume the level is gone.
LUNA_EFFORTS = tuple(effort for effort in modeldialog.EFFORT_CHOICES if effort != "ultra")


def _efforts(model_id: str) -> tuple[EffortOption, ...]:
    values = LUNA_EFFORTS if model_id == "gpt-5.6-luna" else modeldialog.EFFORT_CHOICES
    return tuple(EffortOption(value, value, value == "low") for value in values)


MODELS = tuple(
    ModelOption(
        model_id,
        model_id,
        model_id == modeldialog.MODEL_CHOICES[0],
        _efforts(model_id),
    )
    for model_id in modeldialog.MODEL_CHOICES
)

plugin = HarnessPlugin(
    info=HarnessInfo(
        name="codex",
        display_name="Codex",
        plugin_version="5",
        canonical_version=SCHEMA_VERSION,
        cli_process_name=CLI_PROCESS_NAME,
        supports_attachments=True,
        models=MODELS,
    ),
    hooks=CodexHookGateway(),
    sources=CodexRawEventSources(),
    translator=CodexCanonicalTranslator(),
    controller=controller,
    catalog=CodexCatalog(),
    usage=usage_reader,
    launcher=CodexLauncher(),
)
