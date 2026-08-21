"""Codex's single public harness-plugin descriptor."""

from harness.contract import HarnessPlugin
from harness.models import EffortOption, HarnessInfo, ModelOption
from domain.codec import SCHEMA_VERSION
from domain.ids import ModelId
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from harness.impl.codex.canonical.sources import CodexRawEventSources
from harness.impl.codex.hooks.gateway import CLI_PROCESS_NAME, CodexHookGateway
from harness.impl.codex.catalog import CodexCatalog
from harness.impl.codex.controls.controller import controller
from harness.impl.codex.launcher import CodexLauncher
from harness.impl.codex.usage_rows import usage_reader
from harness.impl.codex.controls import modeldialog

# codex sets model and effort through ONE picker, and its reasoning levels are
# per-model: measured on codex-cli 0.147.0, gpt-5.6-luna's advanced sub-step
# holds Max alone, with no Ultra row. Only that model's list was read, so the
# others keep the full vocabulary rather than assume the level is gone.
LUNA_EFFORTS = tuple(effort for effort in modeldialog.EFFORT_CHOICES if effort != "ultra")


def _efforts(model_id: ModelId) -> tuple[EffortOption, ...]:
    values = LUNA_EFFORTS if model_id == "gpt-5.6-luna" else modeldialog.EFFORT_CHOICES
    return tuple(EffortOption(value, value, value == "low") for value in values)


MODELS = tuple(
    ModelOption(
        ModelId(model_id),
        model_id,
        model_id == modeldialog.MODEL_CHOICES[0],
        _efforts(ModelId(model_id)),
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
        # codex's session_start hook fires with the first prompt, not at startup
        # (measured: the SessionStart raw event lands in the same second as the
        # first UserPromptSubmit, and an idle TUI writes no rollout at all), so a
        # promptless launch is invisible to us. See HarnessInfo.
        requires_initial_message=True,
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
