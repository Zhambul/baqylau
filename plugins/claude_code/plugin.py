"""Claude Code's single public harness-plugin descriptor."""

from contracts.harness import (
    EffortOption,
    HarnessInfo,
    HarnessPlugin,
    ModelOption,
    RewindModeOption,
)
from domain.codec import SCHEMA_VERSION
from plugins.claude_code.canonical import (
    ClaudeCanonicalTranslator,
    ClaudeRawEventSources,
)
from plugins.claude_code.hooks import CLI_PROCESS_NAME, ClaudeHookGateway
from plugins.claude_code.catalog import ClaudeCodeCatalog
from plugins.claude_code.controller import controller
from plugins.claude_code.launcher import ClaudeCodeLauncher
from plugins.claude_code.reactors import (
    ClaudeAccountMigrationCanonicalEventReactor,
    ClaudeOtelCanonicalEventReactor,
)
from plugins.claude_code.terminal_probe import ClaudeCodeTerminalProbe
from plugins.claude_code.usage_rows import usage_reader
from plugins.claude_code import rewindmenu

# The models the ✦ menu offers, each with the reasoning levels IT supports.
# Claude Code's levels do not currently vary by model, so every model carries the
# same list -- the nesting is faithful to today's behaviour and leaves room for
# the per-model truth once it is measured, as it already had to be for codex.
# (`model.model_default_effort` is NOT usable here: it matches resolved ids like
# `opus-5`, while this menu speaks the picker's bare aliases.)
MODEL_IDS = ("fable", "opus", "sonnet", "haiku")
EFFORT_VALUES = ("low", "medium", "high", "xhigh", "max")
DEFAULT_MODEL_ID = MODEL_IDS[0]
DEFAULT_EFFORT = "high"

EFFORTS = tuple(
    EffortOption(effort, effort, effort == DEFAULT_EFFORT) for effort in EFFORT_VALUES
)
MODELS = tuple(
    ModelOption(model_id, model_id, model_id == DEFAULT_MODEL_ID, EFFORTS)
    for model_id in MODEL_IDS
)
REWIND_MODES = tuple(
    RewindModeOption(mode, label) for mode, label in rewindmenu.MODE_LABELS.items()
)

plugin = HarnessPlugin(
    info=HarnessInfo(
        name="claude_code",
        display_name="Claude Code",
        plugin_version="3",
        canonical_version=SCHEMA_VERSION,
        cli_process_name=CLI_PROCESS_NAME,
        supports_attachments=True,
        default_for_launch=True,
        supports_accounts=True,
        models=MODELS,
        rewind_modes=REWIND_MODES,
    ),
    hooks=ClaudeHookGateway(),
    sources=ClaudeRawEventSources(),
    translator=ClaudeCanonicalTranslator(),
    reactors=(
        ClaudeOtelCanonicalEventReactor(),
        ClaudeAccountMigrationCanonicalEventReactor(),
    ),
    controller=controller,
    catalog=ClaudeCodeCatalog(),
    usage=usage_reader,
    launcher=ClaudeCodeLauncher(),
    terminal_probe=ClaudeCodeTerminalProbe(),
)
