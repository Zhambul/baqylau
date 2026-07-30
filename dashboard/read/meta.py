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

import plugins
from core import sessionapi as API
from dashboard import prefs
from dashboard.read.cache import MEMO_CAP, size_cached, ttl_cached

_TITLES = API.BoundedLRU(MEMO_CAP)   # transcript_path -> (size, sig, title): the
#                   list poll must not re-scan 50 transcript heads per tick. The
#                   file's size is most of the key; `sig` is the OWNING HOST's
#                   stamp for a name it keeps somewhere ELSE (codex's state
#                   index), which a growing-file key cannot see move


def session_kv(sid, key, sdb=None):
    """One READ-ONLY kv row off a session's state DB (live or parked), or None —
    the ONE owner of the `state_db_for` + `kv_at` pair the modal-dialog /
    draft / queue / tasks / account readers all need. None when the session has
    no state DB at all (never seen / evicted) OR the row is missing, which the
    callers treat identically ("no card").

    `state_db_for` resolves the live /tmp DB or its durable park and returns
    falsy when neither exists, and `kv_at` is mode=ro — so this can never CREATE
    the state DB whose mere existence is a liveness signal elsewhere.

    `sdb` short-circuits that resolution for a caller who ALREADY has the path.
    It is not an optimisation in general — it exists for the SSE tick, which
    resolves `ctx.sdb` once at the top of every pass and whose FAST channels
    then run at 0.6s: `state_db_for` walks the adopt `sid_chain`, which is an
    audit-DB query, so a fast channel that re-resolved would double the loop's
    per-tick DB work for a path already in hand. Pass it only when it came from
    `state_db_for` (an empty string means "no state DB", same as a failed
    resolve) — never a hand-built path, which is how the CREATE this function
    exists to prevent would get back in."""
    if sdb is None:
        sdb = API.state_db_for(sid)
    return API.kv_at(sdb, key) if sdb else None


def _title_sig(tpath):
    """The owning host's title-freshness stamp for `tpath` (HostControl.
    title_sig), or "" — the sibling of `_rename_override`'s `title_key` lookup,
    resolved through the same `plugins.host_of`. "" for an unowned path, which
    reads as "the file is the whole story" and is what every host but codex
    answers anyway."""
    try:
        host = plugins.host_of(tpath or "")
        return host.title_sig(tpath) if host is not None else ""
    except Exception:
        return ""                       # a stamp we can't read is no stamp


def evict_title(tpath):
    """Forget `tpath`'s memoised title — the INSTANT-echo half of a rename.

    The `sig` key already makes a codex rename visible on the next tick, but a
    rename is a gesture the user is watching: the write and the read happen in
    the same second, and codex's index has a coarse mtime. Dropping the row
    costs one small re-read and removes the whole question. Called by the one
    writer (post_rename) — a stale memo after OUR OWN write is the failure this
    exists to make impossible, not an optimisation."""
    try:
        _TITLES.pop(tpath, None)
    except Exception:
        pass                            # a memo we can't evict re-reads by sig


def _rename_override(tpath):
    """The durable web-rename override for a transcript (prefs `renamed-title`),
    or '' when absent / not one of the owning host's transcripts.

    The KEY comes from that host (`HostControl.title_key`) — the same derivation
    the parked rename's WRITE uses, so the two halves cannot drift and neither
    tier spells one host's filename convention. A host that declares no key has
    no override, which reads the same as "never renamed"."""
    host = plugins.host_of(tpath or "")
    key = host.title_key(tpath) if host is not None else ""
    return prefs.renamed_title(key) if key else ""


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
    # `sig`: a title is NOT always a fact about the transcript file (see
    # _title_sig / cache.size_cached). For Claude Code it is "" and this is the
    # (path, size) memo it always was.
    return size_cached(_TITLES, tpath, compute, empty="", sig=_title_sig(tpath))


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
    def probe():
        try:
            res = subprocess.run(
                ["git", "-c", "core.quotePath=false", "--no-optional-locks",
                 "status", "--porcelain"],
                cwd=cwd, capture_output=True, timeout=DIRTY_TIMEOUT_S)
            return bool(res.stdout.strip()) if res.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
    return ttl_cached(_DIRTY, cwd, DIRTY_TTL_S, probe)


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
    and group_dir. The stored sentinel is `False` = 'not yet resolved', kept
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


def group_dir(cwd):
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


def path_host(tpath):
    """The HostControl that OWNS one transcript/rollout path, falling back to the
    DEFAULT host for a path that is empty or that no plugin claims (a husk row, a
    companion-job .log no parser speaks). The ONE place the read model turns a
    PATH into a host's vocabulary — never a model-id grammar, which is what
    reading another tool's ids through Claude's spelling amounted to.

    Lives here rather than in read/session.py (where it was `row_host`) because
    the ctx/fallback probes below need it too, and meta.py is the module those
    two tiers share."""
    return (plugins.host_of(tpath or "")
            or plugins.host_named(plugins.default_host()))


def session_ctx(tpath, main=False):
    """plugins.context() (the {used, window, pct, model} saturation of the
    file's last turn) behind the (path, size) cache; None when unknown.

    Stamps `model_short` beside the raw `model`: the OWNING host's own display
    spelling of that id (HostControl.model_short — "claude-opus-4-8" →
    "opus-4.8", a codex id unchanged). The client shows it and matches the ✦
    picker's current row against it; it used to re-derive both from a two-host
    grammar in JS (`startsWith("gpt-")`, strip `claude-`), which is a model-id
    sniff for a fact the file's owner simply knows. The RAW id stays — the
    fallback gate compares it to `fallbackModel`, and a display string is not an
    id."""
    def probe():
        cx = plugins.context(tpath, main=main)
        if cx and cx.get("model"):
            cx["model_short"] = path_host(tpath).model_short(cx["model"])
        return cx
    return size_cached(_CTX, tpath, probe)


def session_effort(tpath, cwd="", slug="", ctx=None):
    """The session's reasoning-effort level, resolved for whichever host OWNS
    it — the ONE owner of that resolution (docs/styleguide.md single-owner
    table), called by the session payload, the resume picker, and the SSE tick.

    Precedence, most specific first:
      1. the ctx probe's own `effort`, when the caller already holds one (a
         codex rollout's last turn_context carries it; a Claude transcript
         never does),
      2. the path-keyed `plugins.effort` — the owning host's answer from its
         own file,
      3. the cwd-keyed `plugins.effort_default`, and ONLY when the DEFAULT host
         owns the session.

    That last gate is the whole point. `effort_default` is cwd-keyed, and a
    cwd-keyed fan-out cannot be ownership-gated — first-TRUTHY-wins means the
    default host's saved settings answer for ANY session opened in that
    directory, which is how `high` was shown on a `low` codex run. The session
    payload already had this branch; the resume picker (read/lists) did not and
    served Claude's saved level for every codex resume row, and the SSE effort
    channel did not either and OVERWROTE the correct value one slow tick after
    load. Three call sites, one rule, stated here once.

    Returns "" when nothing is known — the caller shows no level rather than a
    borrowed one."""
    eff = (ctx or {}).get("effort") or plugins.effort(tpath)
    if eff:
        return eff
    if (plugins.owns_by(tpath) or plugins.default_host()) != plugins.default_host():
        return ""
    return plugins.effort_default(cwd or "", slug)


_GOAL = API.BoundedLRU(MEMO_CAP)   # transcript_path -> (size, goal): same
#                   (path, size) cache key as _CTX — the active /goal only
#                   changes when the transcript grows, so the list/session
#                   polls must not re-scan every transcript tail per tick.


def session_goal(tpath):
    """plugins.goal() (the session's active `/goal` as {condition, met}, the
    pinned goal card's source) behind the (path, size) cache; None when there's
    no active goal / unknown."""
    return size_cached(_GOAL, tpath, lambda: plugins.goal(tpath))


_FALLBACK = API.BoundedLRU(MEMO_CAP)   # transcript_path -> (scanned_pos, last
#                   fallback record | None). NOT a (path, size) memo like
#                   _CTX/_GOAL: the record is written ONCE mid-file and never
#                   re-stamped, so a bounded tail probe misses it once the
#                   session grows past it — the value here is a byte CHECKPOINT
#                   for transcript.fallback_scan's forward scan, and each poll
#                   reads only the bytes the last one didn't (a getsize when
#                   nothing grew; the whole file exactly once per server life).


def session_fallback(tpath):
    """The session's model-refusal fallback ({from, to, category, reason, ts})
    — Claude Code's `model_refusal_fallback` system record, written when a
    safeguard refusal reroutes the session to another model (no hook fires) —
    shown ONLY while the session still RUNS the fallback model: the ctx
    probe's current model must equal `to`, so a later /model switch (either
    away or back to the original) retires the warning by itself. Backs the ⚠
    on the ✦ model button (docs/dashboard.md *Model fallback warning*). None
    otherwise; read-side, adds no audit rows (same as ctx/goal).

    Both model ids ride WITH the owning host's display spelling
    (`from_short`/`to_short`, HostControl.model_short) — the hover text says
    "fell back fable-5 → opus-4.8", and shortening it in the page meant a JS copy
    of one host's id grammar applied to whatever record it was handed."""
    if not tpath:
        return None
    try:
        size = os.path.getsize(tpath)
    except OSError:
        return None
    pos, fb = _FALLBACK.get(tpath) or (0, None)
    if size < pos:      # rewritten/truncated — no longer append-only: rescan
        pos, fb = 0, None
    if size > pos:
        got, pos = plugins.model_fallback(tpath, pos)
        fb = got or fb
        _FALLBACK[tpath] = (pos, fb)
    if not fb:
        return None
    cx = session_ctx(tpath, main=True)
    if not (cx and cx.get("model") == fb.get("to")):
        return None
    h = path_host(tpath)
    return dict(fb, from_short=h.model_short(fb.get("from") or ""),
                to_short=h.model_short(fb.get("to") or ""))


_PROMPTS = API.BoundedLRU(MEMO_CAP)   # transcript_path -> (size, count): the
#                   same (path, size) key as _CTX/_GOAL — the number of prompts
#                   you typed can only change when the file grows.


def session_prompts(tpath):
    """plugins.prompts() (how many prompts the HUMAN typed, capped) behind the
    (path, size) cache; None when there is nothing to conclude. The ⊜ compact
    button's gate — Claude Code refuses /compact on a conversation that has
    barely started (docs/dashboard.md *Header action bar*)."""
    return size_cached(_PROMPTS, tpath, lambda: plugins.prompts(tpath))


_CMDS = API.BoundedLRU(MEMO_CAP)   # cwd -> (monotonic expiry, frozenset(names)).
#                   TTL'd like _DIRTY, not size-keyed like the transcript memos:
#                   the input is a DIRECTORY WALK (every ancestor .claude's
#                   commands/skills), which has no cheap fingerprint, and a
#                   command file added mid-session must start tinting without a
#                   server restart. One walk per cwd per TTL instead of one per
#                   rendered bubble.
CMDS_TTL_S = 60.0      # command-set staleness bound (a new .md shows within it)


def cmd_names(cwd, host=None):
    """The set of REAL slash-command names available in `cwd` — the truth behind
    the prompt bubbles' `/command` tint (docs/dashboard.md, *The "/" menu*).
    Names only: the "/" menu fetches the full {name, desc, src} rows through the
    same `plugins.slash_commands` provider — this is its projection, so the tint
    and the menu can never disagree about what a real command is. `host` is the
    session's OWNING tool short name so the tint matches the host-scoped menu (a
    codex session tints /plan, not Claude's /goal); memoized per (cwd, host)."""
    if not cwd:
        return frozenset()

    def walk():
        try:
            return frozenset(c.get("name") or ""
                             for c in plugins.slash_commands(cwd, host))
        except Exception:
            return frozenset()       # discovery is best-effort: no tint, no failure
    return ttl_cached(_CMDS, (cwd, host or ""), CMDS_TTL_S, walk)


def session_cmds(sid):
    """cmd_names for a session's cwd — the one door the mirror/SSE/meta readers
    use, so none of them re-derives the cwd lookup. Resolves the session's
    OWNING host (owns_by over its transcript path) so the tint is the host's own
    vocabulary."""
    row = API.session_row(sid) or {}
    host = plugins.owns_by(row.get("transcript_path") or "") or None
    # canon_cwd, like every other cwd-keyed reader here — so the mirror's
    # lookup and session_payload's share ONE memo entry (and one walk)
    return cmd_names(canon_cwd(row.get("cwd") or ""), host)


def session_slug(sid):
    """The session's subscription-account slug from its statusline stash
    ('' for the default account / no stash) — resolves WHICH user-level
    settings the effort read consults."""
    return (session_kv(sid, "account") or {}).get("slug") or ""
