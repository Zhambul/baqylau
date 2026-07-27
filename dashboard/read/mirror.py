# dashboard/read/mirror.py — the MIRROR stream → HTML read model.
#
# The op stream + conversation merged into the paint the browser renders:
# backlog / history windows, the ops delta, the click-to-view stash, and the
# memory-note render. HTML-escaping happens in dashboard/opshtml/ (the
# neutralize() analog). Read-only (ops_at on the resolved DB path, never a
# connect that would fake a parked session's liveness).
import bisect
import os

import plugins
from core import paths as P
from core import sessionapi as API
from core import state as ST
from core.noaudit import load_audit
from dashboard import notehtml, opshtml
from dashboard.read.meta import session_cmds, session_kv
from plugins.claude_code import memory as MEM

A = load_audit()


def heal_stash(sid, log, sdb, key, step):
    """An endpoint's `open` bail means the dialog is GONE while the stash
    lingers (resolved in the terminal; the turn-boundary clear hasn't fired
    yet) — drop the stash so the page's card clears on the next SSE tick
    instead of sitting stale. Audited like ask_fmt's own removes."""
    if step != "open":
        return
    try:
        # kv_del_at, not kv_del: this runs on a request-handler THREAD, and
        # kv_del's cached connection is bound to whichever thread created it
        # (sqlite check_same_thread) — the delete would silently no-op
        if ST.kv_del_at(sdb, key):
            A.state_file(log, sdb, key,
                         {"action": "remove", "reason": "web open-bail"})
    except Exception:
        A.error(log, "dashboard stash heal (%s)" % key, {"sid": sid})


def conv_items(recs, cmds=()):
    """Conversation records -> stream items. Additively carry `kind`
    (prompt|message|teammsg|question|answer|recap) and, for prompts, the raw `text`:
    the page's queued-message chips match a DELIVERED prompt against what they
    sent — the transcript's prompt record is the one true delivery signal (tab
    transitions are useless: green flips busy again the instant a queued
    prompt starts processing). Every kind renders through opshtml.msg_html; only
    prompts need the raw text echoed back (queued-chip match + rewind picker)
    — plus `par`, the prompt's parentUuid, which the page's dropSuperseded
    matches siblings on (docs/dashboard.md, *Discarded prompts*). `cmds` is the
    session's real slash-command names, for the prompt bubbles' `/command` tint
    — resolved ONCE per render by the caller (a directory walk behind a TTL
    memo), never per bubble."""
    out = []
    for r in recs:
        # `act` is the ACTIVITY CLASS every stream item carries for the view
        # modes (opshtml/actclass.py); conversation text is always ACT_MSG — the
        # `kind` beside it is what the focus mode narrows on (prompts and the
        # turn's final reply survive, mid-turn prose does not).
        it = {"g": None, "t": "msg", "kind": r["kind"], "act": opshtml.ACT_MSG,
              "html": opshtml.msg_html(r["kind"], r.get("text", ""),
                                       r.get("sender", ""), r.get("qa"),
                                       r.get("par") or "", cmds,
                                       r.get("meta"))}
        if r["kind"] == "prompt":
            it["text"] = r.get("text", "")
            it["par"] = r.get("par") or ""
            # An INJECTED user turn, not something the human typed (Claude
            # Code's isMeta: a Stop hook's feedback, a loaded skill's body, a
            # resume nudge, another session's teammate mail). Verbose still
            # shows it — it is in the transcript — but as a ⚙ SYSTEM bubble
            # (msg_html's `meta`), never as YOU; the non-verbose modes hide it
            # outright (docs/dashboard.md, *View modes*).
            if r.get("meta"):
                it["meta"] = 1
        out.append(it)
    return out


def merge_live(ops, recs, key="", cmds=(), scope=None):
    """A LIVE SSE delta of new ops + new conversation recs -> ONE oldest->newest
    item list, interleaved by ts — the increment-side twin of _merge_order's
    placement rule. Without it the SSE loop emits ops and msgs as two separate
    events (ops first) that the client prepends in ARRIVAL order, so a message
    that preceded its command in the turn lands newer-than (above) the command
    in the newest-top feed — the "messages come after commands" inversion that
    only the live path shows (a reload re-runs the ts-merge and reads right).

    Both inputs are already ts-ordered (ops by id == emit time, recs in
    transcript order), so a two-pointer merge suffices. A rec is emitted before
    the next op only when its ts is STRICTLY less (op with ts == rec.ts sorts
    first — the rec lands AFTER it, matching _merge_order.place's `ots <= ts`).
    A ts-less op/rec (pre-migration edge; live always stamps both) falls to the
    tail in arrival order. Runs of consecutive ops go to op_items in ONE call, for
    the same reason _render_window batches them: a group-less body op inherits its
    class from the row in front of it, and a per-op call has none.

    `scope` is the agent scope (opshtml.in_scope) — in agent scope `recs` is
    always empty (the caller stops reading the main thread) and the ops are
    filtered to that agent, so a tick carrying only the lead's ops renders to
    nothing and sends no event."""
    items, i, j = [], 0, 0
    run = []                       # consecutive ops awaiting one batched render

    def flush():
        if run:
            items.extend(opshtml.op_items(run, key, scope=scope))
            run.clear()

    while i < len(ops) and j < len(recs):
        ot, rt = ops[i].get("_ts"), recs[j].get("ts")
        if rt is not None and (ot is None or rt < ot):
            flush()
            items.extend(conv_items([recs[j]], cmds))
            j += 1
        else:
            run.append(ops[i])
            i += 1
    run.extend(ops[i:])
    flush()
    if j < len(recs):
        items.extend(conv_items(recs[j:], cmds))
    return items


TAIL_BLOCKS = 80       # initial backlog: how many stream BLOCKS to paint at once
HISTORY_BLOCKS = 40    # /history default page size (blocks), when ?blocks absent


def _merge_order(sid, key, agent=None):
    """The full oldest->newest interleave of a session's ops and its main-thread
    conversation, WITHOUT rendering — a list of (slot_id, kind, obj) triples
    (kind 'op' -> obj is the op dict; 'msg' -> obj is a conversation record) so
    the block cut discards most ops before the costly op_html render runs. Also
    returns (last_op_id, transcript_pos).

    Interleave is by TIMESTAMP first: ops carry a wall-clock `_ts` (core.state)
    and conversation records carry the transcript line's `ts`
    (transcript.conversation) — when both are present a record lands after the
    last op that chronologically precedes it. Pre-migration history (no ts)
    falls back to the tool_use-id ANCHOR (ops carry `g`/`v`, records carry
    `anchor`; the record lands after that tool's last op). Records with neither
    keep their relative order at the head (pre-first-tool / anchor None) or tail
    (anchor never painted).

    The `slot_id` is what makes lazy-backlog cursors gap/overlap-free: it is the
    row id of the op an item belongs to (an op's own id; a conv record's is the
    id of the op it follows), 0 for the pre-first-tool HEAD group and last+1 for
    the never-painted TAIL group. Every window is a contiguous run of whole
    slots, and the op-id cursor names a slot boundary — see merged_backlog /
    history. Conversation is parsed in FULL here (cheap relative to op HTML —
    O(turns) text records vs O(thousands) ops, each op carrying a rendered,
    possibly large output block) and sliced by the merged window; the returned
    `mpos` is the whole-transcript end so the live SSE tail resumes correctly."""
    sdb = API.state_db_for(sid)
    last, ops = API.ops_at(sdb, 0) if sdb else (0, [])
    # In AGENT scope the main thread's conversation is not part of the stream —
    # the agent's own messages are already ops (the substream paints them), so
    # merging the lead's prompts/replies in would show a second conversation.
    got = None if agent else plugins.conversation(sid, 0)
    recs, mpos = got if got else ([], 0)
    # anchor -> last op index (the fallback placement); timestamped ops as
    # (ts, index) for the primary time-merge.
    lastpos = {}
    for i, op in enumerate(ops):
        for k in ("g", "v"):
            tid = op.get(k)
            if tid:
                lastpos[tid] = i
    ts_ops = [(op["_ts"], i) for i, op in enumerate(ops) if op.get("_ts") is not None]
    HEAD, TAIL = -1, len(ops)
    # The chronological placement below wants "the LAST op whose ts <= r.ts",
    # which a linear scan of ts_ops answered per record — O(ops x recs), and both
    # grow with the session: a long session's _merge_order (rebuilt on the
    # initial backlog AND on every /history page) burned most of its time here.
    # ts_ops is id-ordered, and op ids are assigned at emit time, so its ts
    # column is normally non-decreasing and a bisect finds the same index in
    # O(log n). Normally, not always: `_ts` is wall-clock, so an NTP step / DST
    # correction mid-session can leave one pair inverted, and then bisect and the
    # scan disagree. So check monotonicity ONCE (O(n), off the per-record path)
    # and keep the scan for that case — same answers, faster in the common one.
    _tscol = [t for t, _i in ts_ops]
    _sorted = all(a <= b for a, b in zip(_tscol, _tscol[1:]))

    def place(r):
        ts = r.get("ts")
        if ts is not None and ts_ops:          # primary: chronological
            if _sorted:
                k = bisect.bisect_right(_tscol, ts)
                return ts_ops[k - 1][1] if k else HEAD
            p = HEAD
            for ots, i in ts_ops:              # non-monotonic ts: last match wins
                if ots <= ts:
                    p = i
            return p
        a = r.get("anchor")                    # fallback: the tool-use anchor
        if a in lastpos:
            return lastpos[a]
        return HEAD if a is None else TAIL

    buckets = {}
    for r in recs:
        buckets.setdefault(place(r), []).append(r)
    tail_slot = (ops[-1].get("_id", 0) + 1) if ops else 1
    entries = [(0, "msg", r) for r in buckets.get(HEAD, [])]
    for i, op in enumerate(ops):
        oid = op.get("_id")
        entries.append((oid, "op", op))
        for r in buckets.get(i, []):
            entries.append((oid, "msg", r))
    entries.extend((tail_slot, "msg", r) for r in buckets.get(TAIL, []))
    return entries, last, mpos


def _cut_blocks(entries, blocks, scope=None):
    """Index into `entries` (oldest->newest) of the START of the newest-`blocks`
    stream blocks — 0 when they all fit. A block is a distinct non-null group
    `g` or a standalone item; `rule`/`blank` ops are spacing (dropped by
    op_items) and count as nothing, as do ops out of `scope` (dropped by
    op_items too — the SAME `opshtml.in_scope` predicate, so the window and its
    contents can't disagree), so a window of N blocks means N VISIBLE blocks even
    when agent streams dominate the tail. Approximate by design (the window size
    is a soft limit) — cursor correctness rides slot ids, not this count."""
    seen, count = set(), 0
    for i in range(len(entries) - 1, -1, -1):
        _slot, kind, obj = entries[i]
        if kind == "op":
            if obj.get("t") in ("rule", "blank") or not opshtml.in_scope(obj, scope):
                continue
            g = obj.get("g") or None
        else:
            g = None                           # a conv msg is a standalone block
        if g is None:
            count += 1
        elif g not in seen:
            seen.add(g)
            count += 1
        if count > blocks:
            return i + 1
    return 0


def _snap(entries, start):
    """Move `start` back to the beginning of its slot so a window contains only
    WHOLE slots (its first item is the slot's op, whose id is the cursor) — the
    guarantee that windows never split a slot across the load boundary. A
    `start` at/after the end (an empty window) needs no snap and must not index
    entries[start] — defence in depth against a bad cut index."""
    if start >= len(entries):
        return len(entries)
    while start > 0 and entries[start - 1][0] == entries[start][0]:
        start -= 1
    return start


def agent_scope(sid, agent):
    """The set of producer-source (`src`) stamps that belong to ONE agent of a
    session — what the mirror read path scopes on (opshtml.in_scope). None for a
    falsy `agent`, i.e. the ordinary main-agent-only session view.

    A subagent and a teammate are both named by their hook agent_id, so both
    stamp spellings are accepted for it. A CODEX run is the exception the set
    exists for: it is stamped `codex:<label>` (plugins/codex/watch.spawn) while
    its synthesized agent id is the rollout basename (sessionapi.codex_aid), so
    its label has to be looked up off the run's row — matching the id against
    the stamp would silently show an empty mirror."""
    if not agent:
        return None
    scope = {"sub:" + agent, "team:" + agent}
    for run in API.codex_runs(sid):
        if run.get("agent_id") == agent and run.get("desc"):
            scope.add("codex:" + run["desc"])
    return scope


def _render_window(entries, start, key, cmds=(), scope=None):
    """Render entries[start:] to stream items ({g, t, html}); op entries through
    op_items, msg entries through conv_items. Only the windowed slice is
    rendered — the whole point of the block cut.

    Consecutive op entries go to op_items in ONE call: a group-less body op takes
    its activity class from the row in front of it, and a per-op call has no row
    in front of it (this rendered every op alone, so the inheritance never fired
    and a team-mail body stayed unclassifiable — hence visible in focus). A
    conversation entry flushes the run, since a message is not any op's block."""
    out, pend, pids = [], [], []
    carry = {}                     # one render pass's cross-batch state (see op_items)

    def flush():
        if pend:
            # the ops' row ids ride along, and `carry` keeps what one batch learned:
            # pre-`mid` team mail reconstructs a subject key from the arrival's id,
            # and its read notice may well land in the NEXT batch (a conversation
            # record between them flushes the run) — where it would otherwise read as
            # a message of its own
            out.extend(opshtml.op_items(pend, key, pids, carry, scope))
            pend.clear()
            pids.clear()

    for slot, kind, obj in entries[start:]:
        if kind == "op":
            pend.append(obj)
            pids.append(slot)
            continue
        flush()
        out.extend(conv_items([obj], cmds))
    flush()
    return out


def merged_backlog(sid, key, blocks=TAIL_BLOCKS, agent=None):
    """The session view's INITIAL stream: the NEWEST `blocks` stream blocks of
    the op+conversation interleave, rendered to stream items ({g, t, html} — see
    _merge_order for the interleave rule). Returns
    (last_op_id, transcript_pos, oldest_op_id, [item, …]): `oldest` is the
    smallest op id painted — 0 when the whole history fits (nothing older to
    lazy-load), else the next cursor the client hands /history to load the
    previous blocks downward.

    `agent` renders that AGENT's mirror instead of the lead's (agent_scope /
    opshtml.in_scope). The main thread's conversation is left out there — an
    agent's own messages already arrive as substream ops, so re-merging the
    lead's prompts and replies would put another conversation in its stream."""
    entries, last, mpos = _merge_order(sid, key, agent)
    scope = agent_scope(sid, agent)
    start = _snap(entries, _cut_blocks(entries, blocks, scope))
    oldest = entries[start][0] if start > 0 else 0
    return last, mpos, oldest, _render_window(entries, start, key,
                                              session_cmds(sid), scope)


def history(sid, key, before, blocks, agent=None):
    """The `blocks` stream blocks immediately OLDER than op id `before` — the
    lazy-backlog page. Reuses _merge_order's merge core (one implementation), so
    the initial backlog + successive history pages concatenate to the unlimited
    merge with no gap and no overlap. Returns (oldest_op_id, [item, …]): the
    next cursor (0 when the head is reached — history exhausted). `before` names
    a slot boundary (a returned `oldest`), so the older universe is every whole
    slot below it. `agent` scopes it exactly as merged_backlog does."""
    if before <= 0:
        return 0, []
    entries, _last, _mpos = _merge_order(sid, key, agent)
    scope = agent_scope(sid, agent)
    bound = len(entries)
    for i, (slot, _kind, _obj) in enumerate(entries):
        if slot >= before:                     # slots are id-ordered ascending
            bound = i
            break
    universe = entries[:bound]
    start = _snap(universe, _cut_blocks(universe, blocks, scope))
    oldest = universe[start][0] if start > 0 else 0
    return oldest, _render_window(universe, start, key, session_cmds(sid), scope)


def ops_payload(sid, after, agent=None):
    """(last_id, [item, …]) — rendered server-side so the page never touches
    raw op bytes (items: {g, t, html}, see opshtml.op_items). Reads via
    ops_at on the RESOLVED path (live or parked), which can never create the
    live DB. `agent` scopes it to that agent's ops (agent_scope)."""
    sdb = API.state_db_for(sid)
    if not sdb:
        return after, []
    last, ops = API.ops_at(sdb, after)
    row = API.session_row(sid)
    key = P.sid_from_log(row["log"]) if row else sid
    return last, opshtml.op_items(ops, key, scope=agent_scope(sid, agent))


def view_payload(sid, gid):
    """A click-to-view stash rendered to HTML, or None when there is no stash
    (pre-feature line / failed stash write — same no-op the terminal shows)."""
    ops = session_kv(sid, "view:" + gid)
    ops = [o for o in (ops or []) if isinstance(o, dict)]
    if not ops:
        return None
    return opshtml.view_html(ops, sid)


def note_payload(path, stem):
    """A memory-wiki note rendered for the Memory-tab viewer, by absolute `path`
    (a tab row) OR bare `stem` (a followed [[wikilink]]). Resolves the stem
    through the vault index, reads the note (path-traversal-guarded to the memory
    root by MEM.read_note), and renders the body with clickable/backlink-aware
    HTML. Returns {name, path, frontmatter:[[k,v]…], html, backlinks:[stem…],
    missing:bool}; missing=True (empty html) for a dangling stem / a path outside
    the root / an unreadable note — the client shows a 'note not found' card."""
    p = path or (MEM.resolve(stem) or "")
    fm, body = MEM.read_note(p) if p else (None, None)
    if body is None:
        return {"name": stem or os.path.basename(path or "") or "?", "path": "",
                "frontmatter": [], "html": "", "backlinks": [], "missing": True}
    name = os.path.basename(p)
    if name.endswith(".md"):
        name = name[:-3]
    return {"name": name, "path": p,
            "frontmatter": notehtml.frontmatter_rows(fm),
            "html": notehtml.note_html(body, resolve=MEM.resolve),
            "backlinks": MEM.backlinks(p), "missing": False}
