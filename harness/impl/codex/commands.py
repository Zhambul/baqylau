# harness/impl/codex/commands.py — slash-command vocabulary for a CODEX session's
# "/" menu (the codex twin of harness/impl/claude_code/slashcmds.py).
#
# The web composer offers the same "/" autocomplete the codex TUI does, but a
# codex session's commands are NOT Claude's: it has /plan, /approvals, /review,
# … and NOT /goal, /rewind, /agents. The old fan-out concatenated every
# plugin's list, which was only ever right because claude_code was the sole
# provider — so a codex session was offered Claude's vocabulary (the reported
# "/plan isn't recognized" gap). Now plugins.slash_commands is host-SCOPED (the
# session's OWNING host answers), and this is codex's answer.
#
# Same contract as slashcmds.py: the TUI stays AUTHORITATIVE — the composer only
# TYPES the command and codex's own palette executes it — so this list only has
# to be good enough to complete against, never to validate. BUILTINS is a
# curated snapshot (drift is harmless: an unknown or dropped name still types
# fine), and custom entries are discovered from codex's prompts directory
# ($CODEX_HOME/prompts/*.md — codex's user-defined `/name` prompts), the codex
# analog of Claude's .claude/commands walk.

import os
from dataclasses import dataclass

DESCRIPTION_READ_LIMIT = 4096


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str
    source: str


def describe(path: str) -> str:
    """Read the display description from one Codex prompt file."""
    try:
        with open(path, encoding="utf-8", errors="replace") as prompt_file:
            lines = prompt_file.read(DESCRIPTION_READ_LIMIT).splitlines()
    except OSError:
        return ""
    body_start = 0
    if lines and lines[0].strip() == "---":
        body_start = len(lines)
        for line_index, line in enumerate(lines[1:], start=1):
            stripped_line = line.strip()
            if stripped_line == "---":
                body_start = line_index + 1
                break
            if stripped_line.startswith("description:"):
                description = stripped_line.removeprefix("description:").strip().strip("'\"")
                if description:
                    return description[:120]
    for line in lines[body_start:]:
        stripped_line = line.strip()
        if stripped_line:
            return stripped_line.lstrip("#").strip()[:120]
    return ""

# Curated snapshot of the codex TUI's built-in slash commands (codex-cli 0.14x).
# Verified against the shipped binary's command table; descriptions match the
# TUI's own one-liners. Same drift tolerance as slashcmds.BUILTINS.
BUILTINS = (
    ("approvals", "choose what Codex can do without approval"),
    ("compact", "summarize the conversation to save context"),
    ("diff", "show git diff (including untracked files)"),
    ("init", "create an AGENTS.md with instructions for Codex"),
    ("logout", "log out of Codex"),
    ("mcp", "list configured MCP tools"),
    ("mention", "mention a file"),
    ("model", "choose the model and reasoning effort"),
    ("new", "start a new chat during a conversation"),
    ("plan", "collaborate on a plan before Codex writes code"),
    ("quit", "exit Codex"),
    ("review", "review my current changes and find issues"),
    ("skills", "list available skills"),
    ("status", "show the current session configuration"),
    ("undo", "restore the workspace to the last Codex snapshot"),
    ("usage", "show plan usage limits"),
)


def _prompts_dir() -> str:
    """codex's user-prompts directory — $CODEX_HOME/prompts, else ~/.codex/
    prompts. The codex analog of a user-level .claude/commands dir."""
    home = os.environ.get("CODEX_HOME") or os.path.join(
        os.path.expanduser("~"), ".codex")
    return os.path.join(home, "prompts")


def slash_commands(working_directory: str) -> list[SlashCommand]:
    """[{name, desc, src}, …] for a codex session, sorted by name and
    name-deduped: built-ins first (the TUI resolves those names to itself no
    matter what a same-named custom prompt claims), then codex's user prompts
    ($CODEX_HOME/prompts/*.md, one `/stem` each). `cwd` is accepted for the
    provider contract (parity with slashcmds.slash_commands) but codex prompts
    are global, not project-scoped, so it is unused today — a per-repo codex
    prompt convention would layer in here exactly as Claude's project walk
    does."""
    del working_directory
    commands: list[SlashCommand] = []
    command_names: set[str] = set()

    def add(command_name: str, description: str, source: str) -> None:
        if command_name and command_name not in command_names:
            command_names.add(command_name)
            commands.append(SlashCommand(command_name, description, source))

    for command_name, description in BUILTINS:
        add(command_name, description, "built-in")
    prompts_directory = _prompts_dir()
    try:
        files = sorted(os.listdir(prompts_directory))
    except OSError:
        files = []
    for filename in files:
        if filename.endswith(".md"):
            add(
                filename[:-3],
                describe(os.path.join(prompts_directory, filename)),
                "user",
            )
    commands.sort(key=lambda command: command.name)
    return commands
