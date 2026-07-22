# plugins/claude_code/slashcmds.py — slash-command discovery for the web
# composer's "/" menu (docs/dashboard.md).
#
# The dashboard composer offers the same "/" autocomplete the Claude Code TUI
# does. The TUI stays AUTHORITATIVE — the composer only TYPES the command into
# the terminal (Frontend.send_text) and Claude Code's own palette executes it —
# so this list has to be good enough to complete against, never to validate:
# BUILTINS is a curated snapshot of the CLI's built-in commands (drift is
# harmless — an unknown or missing name still types fine), and the custom
# entries are discovered from the same ancestor-`.claude/` walk that
# agent/settings resolution uses (model.claude_dirs, env_pin=False — the
# lookup is for an ARBITRARY session's cwd, not this process's project):
# `commands/**/*.md` (namespaced by subdirectory, `gh/fix.md` -> `gh:fix`)
# and `skills/*/SKILL.md`.

import os

from plugins.claude_code.model import claude_dirs, config_dir

# Curated snapshot of the CLI's built-in slash commands. The composer's menu
# is a convenience layer over the TUI's own palette, so an entry the CLI
# dropped types harmlessly and an entry the CLI added is simply not offered
# until this list catches up.
BUILTINS = (
    ("add-dir", "add a new working directory"),
    ("agents", "manage agent configurations"),
    ("clear", "clear conversation history"),
    ("compact", "compact the conversation, keeping a summary"),
    ("config", "open the settings panel"),
    ("context", "visualize current context usage"),
    ("cost", "show session cost and duration"),
    ("doctor", "diagnose the Claude Code installation"),
    ("exit", "exit the session"),
    ("export", "export the conversation"),
    ("fast", "toggle fast mode"),
    ("goal", "set an autonomous completion goal Claude works toward"),
    ("help", "show help and available commands"),
    ("hooks", "manage hook configurations"),
    ("init", "initialize a CLAUDE.md for this project"),
    ("loop", "repeat a prompt until a condition is met"),
    ("mcp", "manage MCP servers"),
    ("memory", "edit memory files"),
    ("model", "switch the model for this session"),
    ("output-style", "set the output style"),
    ("permissions", "view or update permissions"),
    ("pr-comments", "get comments from a GitHub PR"),
    ("release-notes", "view release notes"),
    ("resume", "resume a previous conversation"),
    ("review", "review a pull request"),
    ("rewind", "rewind the conversation"),
    ("security-review", "security review of pending changes"),
    ("status", "show session status"),
    ("statusline", "set up the status line"),
    ("todos", "list current todo items"),
    ("usage", "show plan usage limits"),
    ("vim", "toggle vim editing mode"),
)

_HEAD = 4096          # how much of a command/skill file the description scan reads


def describe(path):
    """One display line for a command/skill file: the YAML frontmatter's
    `description:` when present, else the first non-empty body line (leading
    `#` heading marks stripped). Unreadable file -> '' (the entry still lists
    by name — same optional-file tolerance as session_title)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(_HEAD)
    except OSError:
        return ""
    lines = head.splitlines()
    body_at = 0
    if lines and lines[0].strip() == "---":
        body_at = len(lines)                    # unterminated frontmatter: no body
        for i in range(1, len(lines)):
            s = lines[i].strip()
            if s == "---":
                body_at = i + 1
                break
            if s.startswith("description:"):
                d = s[len("description:"):].strip().strip("'\"")
                if d:
                    return d[:120]
    for ln in lines[body_at:]:
        s = ln.strip()
        if s:
            return s.lstrip("#").strip()[:120]
    return ""


def _dir_label(cdir):
    return "user" if cdir == config_dir() else "project"


def slash_commands(cwd):
    """[{name, desc, src}, …] for a session rooted at `cwd`, sorted by name and
    name-deduped: built-ins first (the TUI resolves those names to itself no
    matter what a same-named custom file claims), then discovered entries in
    claude_dirs order (nearest-first — a project command shadows a user-level
    one of the same name). src: 'built-in' | 'project' | 'user' (+' skill').
    No cwd (a session with no recorded one) still gets built-ins + the
    user-level entries."""
    out, seen = [], set()

    def add(name, desc, src):
        if name and name not in seen:
            seen.add(name)
            out.append({"name": name, "desc": desc, "src": src})

    for name, desc in BUILTINS:
        add(name, desc, "built-in")
    dirs = claude_dirs(start=cwd, env_pin=False) if cwd else [config_dir()]
    for cdir in dirs:
        lbl = _dir_label(cdir)
        croot = os.path.join(cdir, "commands")
        for root, _dirs, files in os.walk(croot):
            for f in sorted(files):
                if not f.endswith(".md"):
                    continue
                rel = os.path.relpath(os.path.join(root, f), croot)[:-3]
                add(rel.replace(os.sep, ":"),
                    describe(os.path.join(root, f)), lbl)
        sroot = os.path.join(cdir, "skills")
        try:
            skills = sorted(os.listdir(sroot))
        except OSError:
            skills = []
        for sk in skills:
            sfile = os.path.join(sroot, sk, "SKILL.md")
            if os.path.isfile(sfile):
                add(sk, describe(sfile), lbl + " skill")
    out.sort(key=lambda c: c["name"])
    return out
