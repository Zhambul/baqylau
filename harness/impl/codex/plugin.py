"""Codex's single public harness-plugin descriptor."""

from audit.recorder import AuditRecorder
from harness.contract import HarnessPlugin, SessionResumeRecorder
from harness.models import EffortOption, HarnessInfo, ModelOption, RewindModeOption
from domain.events import SCHEMA_VERSION
from domain.ids import HarnessName
from harness.impl.codex.model import CodexModel
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from harness.impl.codex.canonical.sources import CodexRawEventSources
from harness.impl.codex.hooks.gateway import CLI_PROCESS_NAME, CodexHookGateway
from harness.impl.codex.catalog import CodexCatalog
from harness.impl.codex.controls.controller import build_controller, rewind_continuity
from harness.impl.codex.canonical.title import CodexThreadTitleRepository
from harness.impl.codex.launcher import CodexLauncher
from harness.impl.codex.usage_rows import CodexUsage
from harness.impl.codex.resume import CodexResumeLocator
from harness.impl.codex.controls import modeldialog
from harness.runtime import HarnessRuntimeConfig, default_harness_runtime_configs
from terminal.contract import TerminalPlugin

# codex sets model and effort through ONE picker, and its reasoning levels are
# per-model: measured on codex-cli 0.147.0, gpt-5.6-luna's advanced sub-step
# holds Max alone, with no Ultra row. Only that model's list was read, so the
# others keep the full vocabulary rather than assume the level is gone.
LUNA_EFFORTS = tuple(effort for effort in modeldialog.EFFORT_CHOICES if effort != "ultra")


def _efforts(codex_model: CodexModel) -> tuple[EffortOption, ...]:
    values = LUNA_EFFORTS if codex_model == "gpt-5.6-luna" else modeldialog.EFFORT_CHOICES
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

INFO = HarnessInfo(
        name=HarnessName.CODEX,
        display_name="Codex",
        plugin_version="9",
        canonical_version=SCHEMA_VERSION,
        cli_process_name=CLI_PROCESS_NAME,
        supports_attachments=True,
        supports_native_initial_naming=True,
        supports_native_automatic_renaming=False,
        # codex's session_start hook fires with the first prompt, not at startup
        # (measured: the SessionStart raw event lands in the same second as the
        # first UserPromptSubmit, and an idle TUI writes no rollout at all), so a
        # promptless launch is invisible to us. See HarnessInfo.
        requires_initial_message=True,
        rewind_modes=(RewindModeOption("conversation", "Restore conversation"),),
        models=MODELS,
)


def build_plugin(
    harness_runtime_config: HarnessRuntimeConfig,
    terminal_plugin: TerminalPlugin | None = None,
    session_resume_recorder: SessionResumeRecorder | None = None,
    audit_recorder: AuditRecorder | None = None,
    launch_environment: tuple[tuple[str, str], ...] = (),
) -> HarnessPlugin:
    configuration_directory = str(
        harness_runtime_config.configuration_directory
    )
    title_repository = CodexThreadTitleRepository(configuration_directory)
    return HarnessPlugin(
        info=INFO,
        hooks=CodexHookGateway(),
        sources=CodexRawEventSources(configuration_directory, title_repository),
        translator=CodexCanonicalTranslator(rewind_continuity),
        controller=build_controller(title_repository, harness_runtime_config),
        catalog=CodexCatalog(configuration_directory),
        usage=CodexUsage(harness_runtime_config),
        launcher=(
            CodexLauncher(
                harness_runtime_config,
                terminal_plugin,
                session_resume_recorder,
                audit_recorder,
                launch_environment,
            )
            if terminal_plugin is not None
            and session_resume_recorder is not None
            and audit_recorder is not None
            else None
        ),
        resume_locator=CodexResumeLocator(),
    )


plugin = build_plugin(
    default_harness_runtime_configs().for_harness(HarnessName.CODEX)
)
