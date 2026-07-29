# plugins/claude_code/fileobs.py — the FILE-OP OBSERVER registry.
#
# A dashboard extension's hook-side PRODUCER half (docs/dashboard.md *Web
# extensions*): a declared row here gets to see every main-agent and subagent
# file op — Read/Write/Edit/… over a path — and (a) bake a marker glyph into
# the mirror one-liner and (b) snapshot the touched file into the session's
# state-DB kv AFTER the op's own emit has ensured the DB exists. The two
# formatters that paint file ops (`file_fmt.py` for the main agent,
# `substream_render.py` for a subagent/teammate) loop over matches() instead of
# hard-coding any one feature, so adding a producer is one Obs row — not an
# edit to both formatters (which is how the memory feature was wired before
# this table, and exactly the kind of two-site enumeration the styleguide's
# single-owner rule exists for).
#
# The row's fields:
#   key    — the feature's name; "memory" is special-cased by its callers only
#            where the vocabulary really is memory's own (the `mem=` op flag).
#   match  — (path, cwd) -> bool. `cwd` is the SESSION cwd when the caller has
#            one (the hook payload's), or ""/None meaning "this process's cwd"
#            (the substream tailer inherits the session dir). Must not raise
#            cheaply avoidable errors: it runs on EVERY file op.
#   mark   — the one-glyph marker baked (dimmed) into the one-liner.
#   record — (log, path, verb, agent) -> audit-fragment-or-None, called AFTER
#            the emit (the state DB must already exist — record() is
#            parked-guarded and never creates it). `agent` is the subagent
#            name, None for the main agent.
#
# Import-safe: no I/O at import (the rows hold functions, nothing runs).
import collections

from plugins.claude_code import memory as MEM

Obs = collections.namedtuple("Obs", "key match mark record")

OBSERVERS = (
    # A file op under the memory wiki (~/wiki/01) is a MEMORY op — but ONLY for
    # a session inside the enabled project (aggregator-adapters): a wiki note
    # touched from another project is a plain file op. memory.py stays the
    # vocabulary owner; this row is just its registration.
    Obs("memory",
        lambda path, cwd: MEM.is_memory(path) and MEM.in_scope(cwd or None),
        MEM.MARK,
        MEM.record),
)


def matches(path, cwd=None):
    """The observers that claim this file op, in declaration order."""
    return tuple(o for o in OBSERVERS if o.match(path, cwd))
