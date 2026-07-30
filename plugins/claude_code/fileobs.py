# plugins/claude_code/fileobs.py — the OBSERVER registry (file ops AND commands).
#
# A dashboard extension's hook-side PRODUCER half (docs/dashboard.md *Web
# extensions*): a declared row here gets to see every main-agent and subagent
# op and (a) bake a marker glyph into the mirror block and (b) snapshot what it
# touched into the session's state-DB kv AFTER the op's own emit has ensured the
# DB exists. The formatters loop over this table instead of hard-coding any one
# feature, so adding a producer is one Obs row — not an edit to four formatters
# (which is how the memory feature was wired before this table, and exactly the
# kind of multi-site enumeration the styleguide's single-owner rule exists for).
#
# TWO PLANES, because Claude Code has two ways to touch a file and a feature
# usually cares about both:
#   * the FILE plane (`match`/`record`, via matches()) — a Read/Write/Edit/…
#     TOOL call over a path. Painted by `file_fmt.py` (main agent) and
#     `substream_render.py` (subagent/teammate).
#   * the COMMAND plane (`cmd_match`/`cmd_record`, via cmd_matches()) — a BASH
#     command. Painted by `cmd_pre.py` (the header, which is where the marker
#     goes) + `cmd_fmt.py` (main agent) and `substream_render.py` (subagent).
#     This plane exists because the file plane was measurably the SMALLER half:
#     a session that read ten wiki notes and ran three vault searches recorded
#     none of them, having done every one through the shell (memcmd.py).
# A row may implement either plane or both (None = "this feature has nothing to
# say about commands").
#
# The row's fields:
#   key        — the feature's name; "memory" is special-cased by its callers
#                only where the vocabulary really is memory's own (`mem=`).
#   match      — (path, cwd) -> bool. `cwd` is the SESSION cwd when the caller
#                has one (the hook payload's), or ""/None meaning "this
#                process's cwd" (the substream tailer inherits the session
#                dir). Must not raise cheaply avoidable errors: it runs on
#                EVERY file op.
#   mark       — the one-glyph marker baked (dimmed) into the one-liner/header.
#   record     — (log, path, verb, agent) -> audit-fragment-or-None, called
#                AFTER the emit (the state DB must already exist — record() is
#                parked-guarded and never creates it). `agent` is the subagent
#                name, None for the main agent.
#   cmd_match  — (cmd, cwd) -> bool, the command plane's `match`. Runs on every
#                Bash PreToolUse, so it must stay cheap.
#   cmd_record — (log, cmd, cwd, output, agent) -> [audit-fragment, …], the
#                command plane's `record`. Gets the command's OUTPUT too (a
#                search's answer lives only there), so it runs at PostToolUse.
#
# Import-safe: no I/O at import (the rows hold functions, nothing runs).
import collections

from plugins.claude_code import memcmd as MEMCMD
from plugins.claude_code import memory as MEM

Obs = collections.namedtuple("Obs", "key match mark record cmd_match cmd_record")
Obs.__new__.__defaults__ = (None, None)      # both command-plane fields optional

OBSERVERS = (
    # A file op or shell command under the memory wiki (~/wiki/01) is a MEMORY op
    # — but ONLY for a session inside the enabled project (aggregator-adapters):
    # a wiki note touched from another project is a plain file op. memory.py
    # stays the vocabulary owner (memcmd.py its Bash plane); this row is just the
    # registration. Both planes apply the SAME in_scope() gate, so the feature
    # can't be half-on for a session.
    Obs("memory",
        lambda path, cwd: MEM.is_memory(path) and MEM.in_scope(cwd or None),
        MEM.MARK,
        MEM.record,
        lambda cmd, cwd: MEM.in_scope(cwd or None) and MEMCMD.touches(cmd, cwd),
        MEMCMD.record),
)


def matches(path, cwd=None):
    """The observers that claim this FILE op, in declaration order."""
    return tuple(o for o in OBSERVERS if o.match(path, cwd))


def cmd_matches(cmd, cwd=None):
    """The observers that claim this BASH command, in declaration order. Empty for
    a command no feature cares about, which is the overwhelming majority — the
    predicate is the cheap half of the plane (see Obs.cmd_match)."""
    if not (cmd or "").strip():
        return ()
    return tuple(o for o in OBSERVERS
                 if o.cmd_match and o.cmd_match(cmd, cwd))


def cmd_mem_flag(cmd, cwd, obs):
    """The `mem=` flag for a Bash block's header op (core/ops.label): False when no
    memory observer claimed the command, 1 when it READ notes, "search" when it only
    SEARCHED the vault. The one place the command plane leaks memory's own
    vocabulary — exactly as the file plane's callers test `o.key == "memory"` — and
    it is a flavour rather than a bool because the web words the two differently
    ("recalled 3 memories" vs "queried 2 memories"). A command that does both counts
    as a read: opening the note is the stronger act."""
    if not any(o.key == "memory" for o in obs):
        return False
    notes, searches = MEMCMD.plan(cmd, cwd)
    if notes:
        return 1
    return "search" if searches else False
