# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide architecture test json."""

from __future__ import annotations

from tests import (
    architecture_project_dependencies as project_dependencies,
    architecture_standard_dependencies as standard_dependencies,
    architecture_test_databases,
    architecture_test_layers,
    architecture_test_syntax,
)

ROOT = project_dependencies.Path(__file__).resolve().parents[1]
PYTHON_FILE_PATTERN = "*.py"
IMPLEMENTATION_DIRECTORY_NAME = "impl"
HARNESS_PACKAGE = "harness"
DASHBOARD_PACKAGE = "dashboard"
HARNESS_ROOT = ROOT / HARNESS_PACKAGE
DASHBOARD_ROOT = ROOT / DASHBOARD_PACKAGE
HARNESS_IMPLEMENTATION_ROOT = HARNESS_ROOT / IMPLEMENTATION_DIRECTORY_NAME
DASHBOARD_CLI_PATH = "dashboard/cli.py"
BYTECODE_CACHE_DIRECTORY = "__pycache__"
SERVICES_DIRECTORY_NAME = "services"
FILE_ACCESS_ALLOWLIST = project_dependencies.MappingProxyType({
    "api/application/file_upload_storage.py": "writes staged attachment bytes",
    "dashboard/cli_server.py": "redirects daemon output to the selected log file",
    "dashboard/dictation_credentials.py": "reads the installed dictation key",
    "dashboard/frontend_build_inputs.py": "writes the generated frontend source stamp",
    "harness/impl/claude_code/canonical/message_child_session.py": "reads child session metadata",
    "harness/impl/claude_code/canonical/task_sources.py": "reads native task files",
    "harness/impl/claude_code/canonical/transcript_assignment_scan.py": "reads native assignment records",
    "harness/impl/claude_code/canonical/transcript_paths.py": "reads native session metadata",
    "harness/impl/claude_code/canonical/transcript_sources.py": "reads native session records",
    "harness/impl/claude_code/canonical/transcript_teammates.py": "reads native teammate records",
    "harness/impl/claude_code/canonical/transcript_titles.py": "reads and updates the native session title",
    "harness/impl/claude_code/controls/controller_interrupt_records.py": "reads native interrupt records",
    "harness/impl/claude_code/controls/controller_native_state.py": "reads native message records",
    "harness/impl/claude_code/controls/controller_rename.py": "updates the native title record",
    "harness/impl/codex/canonical/rollout_subagent_body.py": "reads the native child session prefix",
    "harness/impl/codex/canonical/rollout_subagent_metadata.py": "reads native child session metadata",
    "harness/impl/codex/canonical/source_catalog.py": "finds and reads native session files",
    "harness/impl/codex/canonical/translator_recovery.py": "reads native tool call records for recovery",
    "harness/impl/codex/canonical/translator_selection_events.py": "reads native model selection records",
    "harness/impl/codex/controls/controller_rollout.py": "reads native control completion records",
    "notify/channels/telegram_credentials.py": "reads the installed bot token and chat identifier",
    "dashboard/dictate.py": "the Deepgram API key and keyterms",
    "notify/channels/telegram.py": "the bot token and chat id",
    "harness/impl/claude_code/canonical/transcript.py": (
        "the transcript — read as evidence, appended to for a parked rename"
    ),
    "harness/impl/claude_code/canonical/sources.py": "transcripts and task files, read as evidence",
    "harness/impl/claude_code/canonical/messages.py": "a child actor's meta.json sidecar, read as evidence",
    "harness/impl/claude_code/controls/controller.py": "reads the transcript tail to confirm an interrupt landed",
    "harness/impl/claude_code/model.py": "the agent meta.json sidecar beside a transcript",
    "harness/impl/claude_code/slashcmds.py": "your .claude/commands and skills",
    "harness/impl/claude_code/hooks/foreground.py": "creates the tee file a command writes its output into",
    "harness/impl/claude_code/shell.py": "the tee file's directory",
    "harness/file_tail.py": "the common append-only harness source reader",
    "harness/impl/codex/canonical/rollout.py": "a subagent rollout's replayed-parent prefix, measured on the file",
    "harness/impl/codex/canonical/sources.py": "rollouts, read as evidence",
    "harness/impl/codex/canonical/translator.py": "backscans a rollout for the collaboration call an activity resolves",
    "harness/impl/codex/canonical/title.py": "globs codex's own state index",
    "harness/impl/codex/commands.py": "your $CODEX_HOME/prompts",
    "harness/impl/codex/controls/controller.py": "reads the rollout tail to confirm an interrupt landed",
    "harness/impl/opencode2/launch_variant.py": (
        "opencode2's own model state, where it reads the effort a new session starts with"
    ),
    "harness/impl/__init__.py": "plugin discovery globs its own directory",
    "harness/services/usage.py": "a run-scoped cross-process usage cache and its lock",
    "api/application/files.py": "stages an attachment; the harness is handed an @path",
    "engine/interpret/output_source.py": "reads a followed output file, and unlinks the tee we made",
    "core/clipboard.py": "the host pasteboard",
    "core/repository.py": "reads a .git file to resolve a worktree",
    "core/process.py": "/proc-style process inspection",
    "core/kernel_events.py": "opens harness input files read-only for native write notifications",
    "terminal/impl/kitty/remote.py": "finds the terminal's control SOCKET, not a file",
    "dashboard/paths.py": "resolves the uploads directory",
    DASHBOARD_CLI_PATH: "--log sends the daemon's own output to a file",
    "api/application/static.py": "serves the SPA's own files",
    "dashboard/frontend_build.py": "validates Vite's generated manifest and source stamp",
    "bin/retarget_python.py": "rewrites hook shebangs and the user hook configuration",
    "client/_handoff.py": "shares pane state with short-lived terminal click handlers",
    "client/_handoff_storage.py": "reads and writes the terminal handoff documents",
    "client/_handoff_lock.py": "holds and checks the terminal handoff lock",
    "inference/runner.py": "writes the output schema required by the Codex CLI",
})


def canonical_import_paths() -> project_dependencies.Iterator[project_dependencies.Path]:
    """Select source paths for canonical import checks.

    Yields:
        Top-level, shared harness, and dashboard service paths.

    """
    yield from architecture_test_layers.canonical_import_top_paths()
    yield from architecture_test_databases.canonical_harness_shared_paths()
    yield (DASHBOARD_ROOT / SERVICES_DIRECTORY_NAME)


def cross_plugin_importers(package_name: str, forbidden_prefix: str) -> project_dependencies.Iterator[str]:
    """Find imports from a forbidden package inside one harness implementation.

    Yields:
        A message with each importing file and forbidden module name.

    """
    plugin_path = HARNESS_IMPLEMENTATION_ROOT / package_name
    for path in plugin_path.rglob(PYTHON_FILE_PATTERN):
        for imported_path, imported in architecture_test_syntax.imports_under_path(path):
            if imported.startswith(forbidden_prefix):
                yield f"{imported_path.relative_to(ROOT)} imports {imported}"


def canonical_vocabulary_paths() -> project_dependencies.Iterator[project_dependencies.Path]:
    """Select source paths for canonical vocabulary checks.

    Yields:
        Top-level and shared harness paths.

    """
    yield from architecture_test_databases.canonical_vocabulary_top_paths()
    yield from architecture_test_databases.canonical_harness_shared_paths()


def bare_timestamp_ordering_violations(directory: str) -> list[str]:
    """Return bare event timestamp ordering from one package.

    Returns:
        Bare event timestamp ordering from one package.

    """
    return [
        violation
        for path in sorted((ROOT / directory).rglob(PYTHON_FILE_PATTERN))
        for violation in architecture_test_databases.bare_timestamp_ordering_lines(path)
    ]


def file_access_violations(
    path: project_dependencies.Path,
    markers: tuple[str, ...],
) -> project_dependencies.Iterator[str]:
    """Check non-exempt source files for direct file access.

    Yields:
        A message with the file path and all matching access markers.

    """
    relative_path = path.relative_to(ROOT).as_posix()
    if relative_path not in FILE_ACCESS_ALLOWLIST and not relative_path.startswith("repository/impl/sqlite/"):
        code = architecture_test_syntax.code_only(path)
        # The terminal contract also has read_text(). These receivers read a
        # screen, not a file. Keep all other calls in the same module checked.
        code = standard_dependencies.re.sub(
            r"\b(?:screen_driver|text_screen_driver|composer_driver|composer_control_driver|driver|kitty_remote)\.read_text\(",
            "read_screen(",
            code,
        )
        found_markers = [marker for marker in markers if standard_dependencies.re.search(marker, code)]
        if found_markers:
            yield architecture_test_syntax.contains_message(relative_path, found_markers)


def audit_floor_importer(path: project_dependencies.Path) -> str | None:
    """Return the path when it imports the audit-record floor.

    Returns:
        The path when it imports the audit-record floor.

    """
    if BYTECODE_CACHE_DIRECTORY in path.parts:
        return None
    relative_path = path.relative_to(ROOT).as_posix()
    if relative_path == "audit/record.py":
        return None
    if "from audit import record" not in architecture_test_syntax.code_only(path):
        return None
    return relative_path


def graph_name_violations(
    path: project_dependencies.Path,
    banned_names: tuple[str, ...],
    allowed_paths: set[str],
) -> project_dependencies.Iterator[str]:
    """Check non-exempt source files for forbidden graph names.

    Yields:
        A message for each forbidden name found in the source code.

    """
    relative_path = path.relative_to(ROOT).as_posix()
    if BYTECODE_CACHE_DIRECTORY not in path.parts and relative_path not in allowed_paths:
        source = architecture_test_syntax.code_only(path)
        yield from (f"{relative_path} names {name}" for name in banned_names if name in source)
