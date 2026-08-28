"""Claude Code's single public harness-plugin descriptor."""

from audit.recorder import AuditRecorder
from harness.contract import HarnessPlugin, SessionResumeRecorder
from harness.models import EffortOption, HarnessInfo, ModelOption, RewindModeOption
from domain.events import SCHEMA_VERSION
from domain.ids import HarnessName
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.impl.claude_code.canonical.sources import ClaudeRawEventSources
from harness.impl.claude_code.hooks.gateway import CLI_PROCESS_NAME, ClaudeHookGateway
from harness.impl.claude_code.otel.gateway import ClaudeTelemetryGateway
from harness.impl.claude_code import model
from harness.impl.claude_code.catalog import ClaudeCodeCatalog
from harness.impl.claude_code.controls.controller import controller
from harness.impl.claude_code.launcher import ClaudeCodeLauncher
from harness.impl.claude_code.reactors import ClaudeOtelCanonicalEventReactor
from harness.impl.claude_code.probe import ClaudeCodeComposer
from harness.impl.claude_code.usage.rows import ClaudeCodeUsage
from harness.impl.claude_code.controls import rewindmenu
from harness.runtime import HarnessRuntimeConfig, default_harness_runtime_configs
from terminal.contract import TerminalPlugin

# The models the ✦ menu offers, each with the reasoning levels IT supports.
# Claude Code's levels do not currently vary by model, so every model carries the
# same list -- the nesting is faithful to today's behaviour and leaves room for
# the per-model truth once it is measured, as it already had to be for codex.
# (`model.model_default_effort` is NOT usable here: it matches resolved ids like
# `opus-5`, while this menu speaks the picker's bare aliases.)
MODEL_IDS = tuple(model.ClaudeCodeModel(member) for member in ("fable", "opus", "sonnet", "haiku"))
EFFORT_VALUES = tuple(model.ClaudeCodeEffort)
DEFAULT_MODEL_ID = MODEL_IDS[0]
DEFAULT_EFFORT = "high"

EFFORTS = tuple(
    EffortOption(effort, effort, effort == DEFAULT_EFFORT) for effort in EFFORT_VALUES
)
MODELS = tuple(
    # value = the alias the harness's /model takes; label = the ONE display
    # name (model.ALIAS_DISPLAY), so the picker says what the actor row says.
    ModelOption(
        model_id,
        model.alias_display(model_id),
        model_id == DEFAULT_MODEL_ID,
        EFFORTS,
    )
    for model_id in MODEL_IDS
)
REWIND_MODES = tuple(
    RewindModeOption(mode.value, label) for mode, label in rewindmenu.MODE_LABELS.items()
)

INFO = HarnessInfo(
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
    return HarnessPlugin(
        info=INFO,
        hooks=ClaudeHookGateway(),
        telemetry=ClaudeTelemetryGateway(),
        sources=ClaudeRawEventSources(configuration_directory),
        translator=ClaudeCanonicalTranslator(),
        # A rate limit must not relaunch the CLI. A resumed session uses the
        # same native session identity and the first finished event stays true.
        reactors=(ClaudeOtelCanonicalEventReactor(),),
        controller=controller,
        catalog=ClaudeCodeCatalog(configuration_directory),
        model_display=model.display_model,
        usage=ClaudeCodeUsage(harness_runtime_config),
        launcher=(
            ClaudeCodeLauncher(
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
        composer=ClaudeCodeComposer(),
    )


plugin = build_plugin(
    default_harness_runtime_configs().for_harness(HarnessName.CLAUDE_CODE)
)
