# dashboard/read/meta.py — per-session METADATA the list and header chips show.
#
# The small, path-keyed derivations for one session: its display title (with the
# web-rename override), its git checkout state (branch/worktree/root/dirty), its
# grouping directory, and its context/goal probes. All memoized on a (path, size)
# or _db_sig key (see read/cache.py) because the list poll must not re-scan 50
# transcript heads or re-walk 50 .git dirs per tick. File-reads + one sanctioned
# `git status` (dirty); no control writes. Also the single owner of the
# read-only per-session kv read (session_kv) every card/draft reader shares.
import os
import subprocess
import time

import plugins
from core import sessionapi as API
from dashboard import prefs
from dashboard.read.cache import MEMO_CAP

_TITLES = API.BoundedLRU(MEMO_CAP)   # transcript_path -> (size, title): a title
#                   only changes when the file grows, so (path, size) is the
#                   natural cache key — the list poll must not re-scan 50
#                   transcript heads per tick


def session_kv(sid, key):
    """One READ-ONLY kv row off a session's state DB (live or parked), or None —
    the ONE owner of the `state_db_for` + `kv_at` pair the modal-dialog /
    draft / queue / tasks / account readers all need. None when the session has
    no state DB at all (never seen / evicted) OR the row is missing, which the
    callers treat identically ("no card").

    `state_db_for` resolves the live /tmp DB or its durable park and returns
    falsy when neither exists, and `kv_at` is mode=ro — so this can never CREATE
    the state DB whose mere existence is a liveness signal elsewhere."""
    sdb = API.state_db_for(sid)
    return API.kv_at(sdb, key) if sdb else None


def _rename_override(tpath):
    """The durable web-rename override for a transcript (prefs `renamed-title`,
    keyed by the .jsonl stem), or '' when absent / not a session transcript."""
    base = os.path.basename(tpath or "")
    if not base.endswith(".jsonl"):
        return ""
    return prefs.renamed_title(base[:-len(".jsonl")])


def _size_cached(cache, tpath, compute, empty=None):
    """Read-through a (path, size) memo (a read/cache.py BoundedLRU): `empty`
    for a falsy path or an unstatable file, the cached value while the file's
    size is unchanged, else `compute()` (a zero-arg callable) stored under the
    new size. The one owner of the getsize/OSError guard + size-match hit that
    session_title/session_ctx/session_goal share."""
    if not tpath:
        return empty
    try:
        size = os.path.getsize(tpath)
    except OSError:
        return empty
    hit = cache.get(tpath)
    if hit and hit[0] == size:
        return hit[1]
    v = compute()
    cache[tpath] = (size, v)
    return v


def session_title(tpath):
    def compute():
        title, tail_named = plugins.title_and_rename(tpath)
        title = title or ""
        if not tail_named:
            # The web-rename `agent-name` record can scroll out of the
            # transcript's 64KB title tail-window in a long session while Claude
            # Code keeps re-emitting `ai-title` near EOF — the rename would
            # visually "roll back" to the auto title (the confirmed bug). The
            # durable override stands in until a FRESH in-tail rename (which
            # sets tail_named) supersedes it.
            override = _rename_override(tpath)
            if override:
                title = override
        return title
    return _size_cached(_TITLES, tpath, compute, empty="")


_GIT = API.BoundedLRU(MEMO_CAP)   # cwd -> the _git_resolve result (None = not a
#                   checkout). The ancestor walk + gitdir indirection is stable
#                   for a cwd, so it caches until LRU-evicted; HEAD itself is
#                   re-read on every call (one tiny file) so a branch switch
#                   shows on the next poll.

_DIRTY = API.BoundedLRU(MEMO_CAP)  # cwd -> (monotonic expiry, True|False|None).
#                   The dirty probe is the ONE sanctioned `git` subprocess
#                   here — worktree/index
#                   dirtiness is not derivable from .git metadata (detecting it
#                   IS `git status`'s stat-cache job), so it can't be a file
#                   read like the rest of git_info. The TTL cache bounds it to
#                   one probe per checkout per DIRTY_TTL_S instead of per row
#                   per tick; racing SSE threads at worst duplicate one probe.
DIRTY_TTL_S = 10.0     # dirty staleness bound (matches the slow SSE cadence ~3s
#                        polls: a flip shows within TTL + one tick)
DIRTY_TIMEOUT_S = 1.0  # a huge/network-mounted repo must not stall a poll tick;
#                        timeout -> None (unknown) cached like any other result


def _git_dirty(cwd):
    """Whether the checkout at cwd has uncommitted changes — the status-line
    dirty `*` (claude-hud: any `git status --porcelain` output counts, staged/
    unstaged/untracked alike). --no-optional-locks keeps this read-only
    observer from touching the index; None = unknown (no git, timeout, or a
    broken/fake checkout), which renders as no marker."""
    now = time.monotonic()
    hit = _DIRTY.get(cwd)
    if hit and hit[0] > now:
        return hit[1]
    try:
        res = subprocess.run(
            ["git", "-c", "core.quotePath=false", "--no-optional-locks",
             "status", "--porcelain"],
            cwd=cwd, capture_output=True, timeout=DIRTY_TIMEOUT_S)
        dirty = bool(res.stdout.strip()) if res.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        dirty = None
    _DIRTY[cwd] = (now + DIRTY_TTL_S, dirty)
    return dirty


def _git_resolve(cwd):
    """Walk up from cwd to its checkout: (gitdir, worktree_name, root) — gitdir
    the directory holding HEAD, worktree_name the linked-worktree name when
    `.git` is a FILE pointing into .../worktrees/<name> (a `git worktree add` /
    EnterWorktree checkout), and root the MAIN checkout owning that worktree
    (gitdir is <root>/.git/worktrees/<name>); both None for a main checkout.
    None when cwd is in no checkout. File reads only — never a `git`
    subprocess (this runs per row per poll)."""
    d = cwd
    while d and os.path.isdir(d):
        dotgit = os.path.join(d, ".git")
        if os.path.isdir(dotgit):
            return dotgit, None, None
        if os.path.isfile(dotgit):
            try:
                with open(dotgit, encoding="utf-8", errors="replace") as fh:
                    first = fh.readline().strip()
            except OSError:
                return None
            if not first.startswith("gitdir:"):
                return None
            gd = first[len("gitdir:"):].strip()
            if not os.path.isabs(gd):
                gd = os.path.normpath(os.path.join(d, gd))
            if (os.sep + "worktrees" + os.sep) in gd:
                wt = os.path.basename(gd)
                root = os.path.dirname(os.path.dirname(os.path.dirname(gd)))
            else:
                wt = root = None
            return gd, wt, root
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return None


def _resolve_git(cwd):
    """The _git_resolve result for cwd behind the _GIT memo, shared by git_info
    and _group_dir. The stored sentinel is `False` = 'not yet resolved', kept
    DISTINCT from a legitimately cached None/{} so a genuine no-checkout result
    is cached (not re-resolved every call)."""
    hit = _GIT.get(cwd, False)
    if hit is False:
        hit = _git_resolve(cwd)
        _GIT[cwd] = hit
    return hit


def canon_cwd(cwd):
    """Resolve a session cwd's symlinks, so the list groups one PROJECT under
    one entry. The 2026-07-19 baqylau rename left ~/code/personal/kitty as a
    symlink to .../baqylau; sessions started before the move (or through the
    old path) record the /kitty spelling — Claude Code reports the logical path
    and a live session re-stamps it on every event — so without canonicalising,
    the list splits one repo into a stale 'kitty' group and a 'baqylau' group.
    realpath collapses them. '' is returned as-is: realpath('') would be the
    dashboard process's OWN cwd, which is never a session's."""
    if not cwd:
        return cwd
    try:
        return os.path.realpath(cwd)
    except OSError:
        return cwd


def git_info(cwd):
    """The checkout state of a session's cwd, for the git chips: {"branch",
    "worktree", "root", "dirty"} — branch the HEAD ref's short name (a 7-char
    sha when detached), worktree the linked-worktree name or None for a main
    checkout, root the MAIN checkout directory owning a linked worktree (None
    for a main checkout — the list page groups sessions by root||cwd, so a
    worktree session files under its project, not its worktree dir), dirty
    the uncommitted-changes flag behind the branch chip's `*` (True/
    False/None-unknown — _git_dirty). None when cwd isn't inside a git
    checkout (or its worktree was removed)."""
    if not cwd:
        return None
    hit = _resolve_git(cwd)
    if not hit:
        return None
    gitdir, wt, root = hit
    try:
        with open(os.path.join(gitdir, "HEAD"), encoding="utf-8",
                  errors="replace") as fh:
            head = fh.read().strip()
    except OSError:
        return None
    if head.startswith("ref:"):
        ref = head[4:].strip()
        branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
    else:
        branch = head[:7] or "?"
    return {"branch": branch, "worktree": wt, "root": root,
            "dirty": _git_dirty(cwd)}


def _group_dir(cwd):
    """The directory a session GROUPS under on the list page: its linked-
    worktree OWNER (so N worktrees of one repo aggregate under the main
    checkout, as git_info's `root` did), else `cwd` itself. Fed the session's
    start_cwd (the frozen ORIGINAL cwd), NOT the live cwd, so a mid-session cd
    can never move a card between groups. File-read-only (_git_resolve + the
    _GIT cache, shared with git_info) — deliberately NOT the `dirty`
    subprocess, which grouping doesn't need."""
    if not cwd:
        return cwd
    hit = _resolve_git(cwd)
    root = hit[2] if hit else None
    return root or cwd


_CTX = API.BoundedLRU(MEMO_CAP)   # transcript_path -> (size, ctx): same
#                   (path, size) cache key
#                   as _TITLES — saturation only changes when the file grows, and
#                   the list/agents polls must not re-read every transcript tail
#                   per tick. The main= flag is per-path-constant (a path is
#                   always a main transcript or always an agent's), so it stays
#                   out of the key.


def session_ctx(tpath, main=False):
    """plugins.context() (the {used, window, pct, model} saturation of the
    file's last turn) behind the (path, size) cache; None when unknown."""
    return _size_cached(_CTX, tpath, lambda: plugins.context(tpath, main=main))


_GOAL = API.BoundedLRU(MEMO_CAP)   # transcript_path -> (size, goal): same
#                   (path, size) cache key as _CTX — the active /goal only
#                   changes when the transcript grows, so the list/session
#                   polls must not re-scan every transcript tail per tick.


def session_goal(tpath):
    """plugins.goal() (the session's active `/goal` as {condition, met}, the
    pinned goal card's source) behind the (path, size) cache; None when there's
    no active goal / unknown."""
    return _size_cached(_GOAL, tpath, lambda: plugins.goal(tpath))


def _session_slug(sid):
    """The session's subscription-account slug from its statusline stash
    ('' for the default account / no stash) — resolves WHICH user-level
    settings the effort read consults."""
    return (session_kv(sid, "account") or {}).get("slug") or ""
