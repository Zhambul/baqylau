"""Claude Code's single public harness-plugin descriptor."""

from harness.contract import HarnessPlugin
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
from harness.impl.claude_code.probe import ClaudeCodeTerminalProbe
from harness.impl.claude_code.usage.rows import usage_reader
from harness.impl.claude_code.controls import rewindmenu

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

plugin = HarnessPlugin(
    info=HarnessInfo(
        name=HarnessName.CLAUDE_CODE,
        display_name="Claude Code",
        plugin_version="3",
        canonical_version=SCHEMA_VERSION,
        cli_process_name=CLI_PROCESS_NAME,
        supports_attachments=True,
        default_for_launch=True,
        supports_accounts=False,
        supports_native_automatic_renaming=True,
        supports_native_text_queue=True,
        models=MODELS,
        rewind_modes=REWIND_MODES,
    ),
    hooks=ClaudeHookGateway(),
    telemetry=ClaudeTelemetryGateway(),
    sources=ClaudeRawEventSources(),
    translator=ClaudeCanonicalTranslator(),
    # No automatic account migration: a rate limit leaves the session where it
    # is. Switching accounts relaunches the CLI under the same session id, and a
    # resumed session's `session.started` is deduplicated against the first
    # run's, so the first run's `session.finished` keeps it out of
    # `watchable()` for good (see docs/html/resume-tombstone.html).
    reactors=(ClaudeOtelCanonicalEventReactor(),),
    controller=controller,
    catalog=ClaudeCodeCatalog(),
    model_display=model.display_model,
    usage=usage_reader,
    launcher=ClaudeCodeLauncher(),
    terminal_probe=ClaudeCodeTerminalProbe(),
)
