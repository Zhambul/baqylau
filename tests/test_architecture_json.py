# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test architecture json."""

from __future__ import annotations

from tests import (
    architecture_project_dependencies as project_dependencies,
    architecture_standard_dependencies as standard_dependencies,
    architecture_test_json,
    architecture_test_protocols,
    architecture_test_syntax,
)

ROOT = project_dependencies.Path(__file__).resolve().parents[1]
TEXT_ENCODING = "utf-8"
CORE_PACKAGE = "core"
AUDIT_PACKAGE = "audit"
IMPLEMENTATION_DIRECTORY_NAME = "impl"
HARNESS_PACKAGE = "harness"
APP_PACKAGE = "app"
DASHBOARD_PACKAGE = "dashboard"
ENGINE_PACKAGE = "engine"
NOTIFY_PACKAGE = "notify"
TERMINAL_PACKAGE = "terminal"
HARNESS_ROOT = ROOT / HARNESS_PACKAGE
DASHBOARD_ROOT = ROOT / DASHBOARD_PACKAGE
HARNESS_IMPLEMENTATION_ROOT = HARNESS_ROOT / IMPLEMENTATION_DIRECTORY_NAME
CLAUDE_CODE_PACKAGE = "claude_code"
DASHBOARD_CLI_PATH = "dashboard/cli.py"
CODEX_PACKAGE = "codex"
TIMESTAMP_ORDERING_PACKAGES = (
    APP_PACKAGE,
    ENGINE_PACKAGE,
    DASHBOARD_PACKAGE,
    TERMINAL_PACKAGE,
    CORE_PACKAGE,
    HARNESS_PACKAGE,
    AUDIT_PACKAGE,
    NOTIFY_PACKAGE,
)
FILE_ACCESS_ALLOWLIST = project_dependencies.MappingProxyType({
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


def test_canon_shared_code_contains_no_concrete() -> None:
    """Verify canonical shared code contains no concrete harness vocabulary."""
    assert not architecture_test_protocols.concrete_vocabulary_violations()


def test_no_read_path_orders_on_bare_occurred_at() -> None:
    """`occurred_at` is nullable BY DESIGN -- it is when the SOURCE said the fact happened.

    Sources that carry no timestamp of their own honestly leave it NULL, so every read
    path must fall back to `accepted_at` (when we recorded it). Ordering on the bare
    column sorts those events arbitrarily, which silently reorders a conversation.
    """
    timestamp_ordering_violations = [
        violation
        for directory in TIMESTAMP_ORDERING_PACKAGES
        for violation in architecture_test_json.bare_timestamp_ordering_violations(directory)
    ]
    assert not timestamp_ordering_violations


def test_dashboard_browser_code_has_no_concrete() -> None:
    """Verify dashboard browser code has no concrete harness or old names."""
    frontend_vocabulary_violations: list[str] = []
    for path in sorted((DASHBOARD_ROOT / "static").glob("app.*.js")):
        source = path.read_text(encoding=TEXT_ENCODING)
        source = standard_dependencies.re.sub(
            r"/\*.*?\*/",
            "",
            source,
            flags=standard_dependencies.re.DOTALL,
        )
        source = standard_dependencies.re.sub("//.*$", "", source, flags=standard_dependencies.re.MULTILINE)
        frontend_vocabulary_violations.extend(
            f"{path.relative_to(ROOT)} contains {word}"
            for word in ("claude", CODEX_PACKAGE, "anthropic", "openai")
            if standard_dependencies.re.search(f"\\b{word}\\b", source, flags=standard_dependencies.re.IGNORECASE)
        )
        frontend_vocabulary_violations.extend(
            f"{path.relative_to(ROOT)} contains abbreviated {abbreviation}"
            for abbreviation in ("sid", "ses", "op", "ops")
            if standard_dependencies.re.search(f"\\b{abbreviation}\\b", source)
        )
    assert not frontend_vocabulary_violations


def test_session_lifecycle_has_no_per_harness() -> None:
    """Verify session lifecycle has no per harness implementation.

    Pane open/close is harness-agnostic and lives in the interpreter's react
        step; a reappearing per-plugin lifecycle module means the split regressed.
    """
    assert not (HARNESS_IMPLEMENTATION_ROOT / CODEX_PACKAGE / "lifecycle.py").exists()
    assert not (HARNESS_IMPLEMENTATION_ROOT / CLAUDE_CODE_PACKAGE / "lifecycle.py").exists()


def test_the_launch_wrappers_are_gone() -> None:
    """Launching is just running the CLI.

    The recorder that used to build the application graph — the OTLP receiver —
    is a stdlib-only client now, and that it builds nothing is checked where it
    lives (tests/test_canonical_clients.py). What remains here is the absence of
    the two wrappers that once wrapped a launch.
    """
    assert not (HARNESS_IMPLEMENTATION_ROOT / CLAUDE_CODE_PACKAGE / "command.py").exists()
    assert not (HARNESS_IMPLEMENTATION_ROOT / CODEX_PACKAGE / "command.py").exists()


def test_no_module_outside_allowlist_reads() -> None:
    """Everything we own is a row. The exceptions are named, with their reason.

    Two classes survive: CREDENTIALS the user installs and we only trade, and
    SOURCE FILES a harness writes or you author — a transcript, a rollout, a
    slash-command definition. Both are things we do not own. The third entry is
    the one place we write bytes rather than a row, and even that now has a row
    beside it: an attachment reaches the harness as an `@path`, so a real file
    has to exist.
    """
    markers = (
        r"\bopen\(",
        r"\bos\.makedirs\b",
        r"\bos\.listdir\b",
        r"\bos\.scandir\b",
        r"\bglob\.glob\b",
        r"\.write_text\(",
        r"\.write_bytes\(",
        r"\.read_text\(",
    )
    direct_file_access_violations = [
        violation
        for path in architecture_test_syntax.owned_python_paths()
        for violation in architecture_test_json.file_access_violations(path, markers)
    ]
    assert not direct_file_access_violations


def test_file_access_allowlist_has_no_stale() -> None:
    """An allowlist may not outlive its reason — the same rule the type ratchet has."""
    stale = [relative for relative in FILE_ACCESS_ALLOWLIST if not (ROOT / relative).is_file()]
    assert not stale
