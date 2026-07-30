# dashboard/read/cache.py — the read model's shared memo vocabulary.
#
# One home for the caching primitives every read-side payload builder leans on,
# so a memo cap / fingerprint / freshness rule isn't re-encoded per module.
# Every path-keyed memo is a process-lifetime cache in a days-long singleton —
# bounded with API.BoundedLRU so the KEY set (transcript/state-DB paths, cwds —
# one per session ever seen) can't grow without limit. The cap is far above the
# live working set (SESSIONS_LIMIT sessions + their agents), so an active session
# never thrashes; only paths that scrolled out of discovery age out, and their
# re-derivable values just re-read once if seen again.
#
# THREE freshness rules live here, one per kind of input:
#
#   db_cached   (path, db_sig)  — a sqlite state DB, fingerprinted by the stat of
#                                 the file AND its -wal sidecar. Owned by
#                                 core/sessionapi.py (the accounts read model
#                                 needed it too); aliased below.
#   size_cached (path, size)    — a transcript, which only changes by GROWING, so
#                                 os.path.getsize is a complete fingerprint and a
#                                 miss costs one stat. Its optional `sig` covers
#                                 the one value that is NOT wholly the file's: a
#                                 session title a host keeps somewhere else.
#   ttl_cached  (key, wall TTL) — an input with NO cheap fingerprint: a directory
#                                 WALK, a `git status` subprocess, a whole-corpus
#                                 aggregate. Bounded staleness is the only rule
#                                 available, so the TTL stays per-caller and named.
#
# The last two were hand-rolled: size_cached lived in read/meta.py (private to
# the module that happened to need it first) and the TTL rule was written out
# twice IN THAT FILE (_git_dirty, cmd_names) plus once more in read/lists.py's
# stats aggregate — four spellings of "memoize with a freshness rule" behind a
# module header that already promised to own exactly that.
import os
import time

from core import sessionapi as API

MEMO_CAP = 8192

# The (path, sig) memo + fingerprint live in core/sessionapi.py (db_sig/
# db_cached — the accounts read model needed them too); these aliases keep the
# call sites reading as before.
_db_sig = API.db_sig
_db_cached = API.db_cached


def size_cached(cache, path, compute, empty=None, sig=""):
    """Read-through a (path, size[, sig]) memo: `empty` for a falsy path or an
    unstatable file, the cached value while the file's size is unchanged, else
    `compute()` (a zero-arg callable) stored under the new size.

    Valid only for an APPEND-ONLY file — a transcript. A file that can be
    rewritten in place keeps its size and would serve a stale value forever;
    that is what db_cached's stat-plus-WAL fingerprint is for.

    `sig` is an OUT-OF-BAND freshness stamp folded into the key, for a value
    derived from the file PLUS something else that can move on its own. The one
    caller is the session TITLE: a codex session's name lives in codex's state
    index, not in its rollout, so a rename left the transcript byte-identical
    and the (path, size) key served the old title forever (the confirmed bug —
    the list page never updated). The stamp is the OWNING HOST's answer
    (`HostControl.title_sig`), so the tier that caches a host's fact does not
    have to know what makes that fact stale. "" is the honest default: the file
    is the whole story, which is exactly true for an append-only transcript."""
    if not path:
        return empty
    try:
        size = os.path.getsize(path)
    except OSError:
        return empty
    hit = cache.get(path)
    if hit and hit[0] == size and hit[1] == sig:
        return hit[2]
    v = compute()
    cache[path] = (size, sig, v)
    return v


def ttl_cached(cache, key, ttl_s, compute):
    """Read-through a wall-clock TTL memo: the cached value while it is younger
    than `ttl_s`, else `compute()` (a zero-arg callable) stored with a fresh
    deadline. For inputs with no cheap fingerprint, where bounded staleness is
    the only freshness rule available.

    `None` and other FALSY values are cached like any other result — both
    original callers depend on that (an UNKNOWN `git status` outcome is None and
    must not re-run the subprocess every tick; an empty command set is a real
    answer). So the deadline alone decides a hit, never the value.

    Racing SSE threads at worst duplicate one compute; every value here is
    re-derivable, so a duplicate costs time, never correctness."""
    now = time.monotonic()
    hit = cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    v = compute()
    cache[key] = (now + ttl_s, v)
    return v
