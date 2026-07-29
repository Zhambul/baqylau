# dashboard/read/session.py — ONE session's detail payload + its modal cards.
#
# session_payload and everything it composes: the agent scoreboard helpers, the
# pending AskUserQuestion / ExitPlanMode / composer-draft / ghost-suggestion /
# tasks / queue cards. Read-only; the per-session metadata comes from
# read/meta.py, the live window from control/launch.py.
import collections
import os
import time

import plugins
from core import sessionapi as API
from core import tabs
from dashboard import config, ext, opshtml, prefs, suggestion
from dashboard.control import launch
from dashboard.read.meta import (canon_cwd, cmd_names, git_info, session_ctx,
                                 session_fallback, session_goal, session_kv,
                                 session_prompts, session_title, session_slug)
from plugins.claude_code import accounting as ACC
from plugins.claude_code import model as M


def visible_agents(agents):
    """Drop HIDDEN-agent bookkeeping rows: a SubagentStop with no
    SubagentStart (Claude Code's hidden auxiliary agents — the subagent
    finaliser's 'never started (hidden agent)' path) leaves an agents-table
    row with EVERY field empty. Zero user-facing signal, so the dashboard
    filters them; any row with at least one real field (kind, desc, slot,
    transcript, a start time) stays. The API keeps reporting them — this is
    presentation policy, not truth policy."""
    return [a for a in agents
            if a.get("kind") or a.get("desc") or a.get("transcript")
            or a.get("slot") is not None or a.get("started_at")]


def agents_ctx(agents):
    """Stamp each agent row with its own transcript's context saturation
    (session_ctx over the streams-keystone src_path — an agent transcript is
    its sidechain turns, so main=False). Rows whose file yields nothing (husk
    rows, codex rollouts — no codex context provider yet) stay unstamped."""
    for a in agents:
        ctx = session_ctx(a.get("transcript") or "")
        if ctx:
            a["ctx"] = ctx
    return agents


def agents_model_effort(agents, effort):
    """Stamp each agent row with the short model id + effort level it runs — the
    web card's echo of the terminal mirror's `opus-4.8·high` op tag
    (substream.op_tag). The model rides FREE on the ctx probe agents_ctx already
    stamped (ctx["model"] is the raw id of the agent's last assistant turn, from
    transcript.context_probe), so no extra file read; effort mirrors the
    substream's `EFFORT_CFG or model_default_effort()` — the session's saved
    effort, else the running model's default (a frontmatter/env per-agent effort
    override, the substream's higher-precedence source, isn't readable here and
    is the one divergence). Rows with no ctx (husks, not-yet-started agents) stay
    unstamped, exactly as their ctx bar does."""
    for a in agents:
        raw = (a.get("ctx") or {}).get("model") or ""
        if not raw:
            continue
        a["model"] = M.short_model(raw)
        eff = effort or M.model_default_effort(raw)
        if eff:
            a["effort"] = eff
    return agents


def agent_usage(sid, agent):
    """ONE agent's {model, usage, cost} for the agent-scope scoreboard, or None
    when no plugin has a transcript for it (a codex run declines — it prices its
    own tokens at its footer). `cost` is approximate USD for that token rollup,
    priced from the run's last model via the shared accountant, and is omitted
    for an unknown/empty model — the client just drops the ≈cost chip.

    This transcript pricing is the ONLY per-agent cost figure available: OTEL
    `costs()` is aggregate by query_source (main/subagent/auxiliary) and can
    never be attributed to a single agent_id."""
    tl = plugins.agent_usage(sid, agent)
    if not tl:
        return None
    u = tl.get("usage") or {}
    if u:
        tl["cost"] = ACC.cost_usd(tl.get("model"), u.get("in", 0), u.get("out", 0),
                                  u.get("cache", 0), u.get("create", 0),
                                  u.get("create_1h", 0))
    return tl


def ext_scope(scope, cwd):
    """One extension's project gate, applied to a session's RAW cwd (the gate
    predicate itself receives it canonical — canonicalized once, HERE, so an
    extension never re-encodes canon_cwd). A gate-less extension is always in
    scope. This module is the one APPLICATION point of the gate because its
    facts have TWO readers each: the overview payload and the SSE stream's live
    patch. They disagreed once — the stream pushed the real memory count for an
    off-scope session, at a tab the page never builds there (benign on screen,
    but a per-tick kv read for nobody, and two readings of one rule)."""
    return bool(scope(canon_cwd(cwd))) if scope else True


def _ext_badge(row):
    """One extension's badge as a BADGES row (a named factory, not an inline
    lambda in the table build — the closure must bind THIS row, ruff B023):
    payload field `<name>_count`, SSE event `<name>`, count gated to 0
    off-scope by the same ext_scope the payload's `<name>_scope` flag uses."""
    return _Badge(row.name, row.name + "_count",
                  lambda sid, cwd, agent: (row.badge(sid, agent)
                                           if ext_scope(row.scope, cwd) else 0),
                  row.scoped)


# --- the secondary tabs' BADGE COUNTS: one row per badge, one owner ---------------
# Each badge is a CHEAP count (an audit COUNT / the streams keystone / a kv read
# — never a transcript parse), so both the overview payload and the per-tick SSE
# can carry it; the full detail behind every one stays on its REST endpoint,
# fetched when the tab opens (/errors, /monitors, /jobs, /memory).
#
# The row carries BOTH names the fact travels under, because they differ: the
# payload field is `<thing>_count` (the page reads meta.error_count) while the
# SSE event is the bare `<thing>` (the page listens for "errors"). That
# divergence is a wire fact, not a choice worth re-litigating — the page's
# SECTIONS table already bridges it with a `countField`. What it is NOT worth is
# two server-side enumerations of the same four facts: session_payload set the
# `_count` keys and http/sse.py's own table produced the events, so adding a
# badge meant editing both, in different vocabularies, with nothing to say so.
# Here the pair is declared once and both sides derive from it.
#
# Values are (sid, cwd, agent) callables, not bound API functions: the lookup has
# to happen at CALL time so a patched sessionapi moves the served number too (the
# module-qualified read rule), and `memory` needs a different owner than the
# others — its badge is project-SCOPED, and that gate belongs to this read model
# (which the overview payload and the SSE badge table must not each re-apply).
#
# `scoped` says whether the badge follows AGENT SCOPE, and it is the same split
# the tab strip already declares out loud: monitors and jobs are one agent's work
# and re-point with the view, while errors (a script's) and memory (the team's)
# stay session-wide and are labelled "session-wide" on the page. Without it every
# badge counted the LEAD's, in every scope — an agent with 19 background jobs
# read `jobs 1` and one with 8 monitors had no badge at all, so its background
# work looked like it wasn't there ("I still can't see the background job shells,
# and their outputs. For the subagents.").
_Badge = collections.namedtuple("_Badge", "event field count scoped")

BADGES = (
    # ⚠ swallowed errors — the web sibling of the scorebar's errwatch chip;
    # a COUNT, no tracebacks
    _Badge("errors", "error_count",
           lambda sid, cwd, agent: API.error_count(sid), False),
    # distinct monitors — a new Monitor launch bumps it
    _Badge("monitors", "monitor_count",
           lambda sid, cwd, agent: API.monitor_count(sid, agent), True),
    # distinct background jobs — a new bg launch bumps it
    _Badge("jobs", "job_count",
           lambda sid, cwd, agent: API.job_count(sid, agent), True),
    # …plus every EXTENSION's badge (dashboard/ext — memory's "distinct
    # wiki notes touched" is the first), scope-gated by _ext_badge.
) + tuple(_ext_badge(r) for r in ext.badge_rows())


def _with_cmd_html(rows):
    """Stamp each secondary-tab row with `cmd_html` — its command as the SAME
    highlighted, pretty-printed block the mirror paints (opshtml.cmd_html).

    Both list kinds go through here because they are one kind of thing: a
    long-running command with a lifecycle. They were two hand-written detail
    panels showing the same facts in different shapes, and neither highlighted
    anything — the raw one-liner as a single grey run, three tabs away from the
    mirror showing that exact command coloured and broken across lines. `command`
    is left ALONE beside it: the cards' titles and the crumb use it as text, and
    a reflowed copy would put a line break in a card title."""
    for r in rows:
        if isinstance(r, dict) and r.get("command"):
            r["cmd_html"] = opshtml.cmd_html(r["command"])
    return rows


def jobs_payload(sid, agent=""):
    """The background-jobs tab's rows — the LEAD's own, or one agent's."""
    return _with_cmd_html(API.jobs(sid, agent))


def monitors_payload(sid, agent=""):
    """The monitors tab's rows, filtered to a scope. Every row is ATTRIBUTED
    (sessionapi.nested_owners), so the filter is a scope check, not a guess —
    and the filtering lives here rather than in the handler, beside the jobs
    twin it has to stay consistent with."""
    rows = [m for m in (plugins.monitors(sid) or [])
            if (m.get("agent_id") or "") == agent]
    return _with_cmd_html(rows)


def badge_count(badge, sid, cwd, agent=""):
    """One badge's number for a view — THE one place a badge meets a scope, so
    the overview payload and the SSE badge channel cannot answer differently for
    the same tab. An unscoped badge is handed "" whatever the view is, which is
    also what `agent_id`-empty means to the counters: the LEAD's own."""
    return badge.count(sid, cwd, agent if badge.scoped else "")


def session_payload(sid, agent=""):
    """One session's overview — session() plus the secondary tabs' badge counts
    (BADGES; the full rows stay behind /errors, /monitors, /jobs, /memory) and
    the display title.

    `agent` scopes the badges that HAVE an agent dimension (BADGES `scoped`:
    monitors and jobs — the two tabs that re-point with the view) and stamps
    `agent_usage` — that ONE agent's token rollup and priced cost, for the
    scoreboard the page swaps in under agent scope (docs/dashboard.md *Agent
    scope*). The usage is per-request rather than a field on every agents row
    because it folds a whole transcript: paying that for all of a 28-agent
    session's rows on every overview would be absurd, and only the scoped one is
    ever shown."""
    data = API.session(sid)
    if agent:
        data["agent_usage"] = agent_usage(sid, agent)
    data["agents"] = agents_ctx(visible_agents(data.get("agents") or []))
    # Each extension tab is SCOPED (memory: only sessions inside
    # aggregator-adapters get it). The `<name>_scope` flag gates the tab
    # client-side (hidden off-scope); the badge count still rides along in the
    # BADGES loop below (0 off-scope — the same gate). An extension's own
    # `payload` hook may stamp further fields.
    for e in ext.all_ext():
        data[e.NAME + "_scope"] = ext_scope(ext.provider(e, "scope"),
                                            data.get("cwd") or "")
        fn = ext.provider(e, "payload")
        if fn:
            fn(data, sid)
    for b in BADGES:
        data[b.field] = badge_count(b, sid, data.get("cwd") or "", agent)
    data["title"] = session_title(data.get("transcript_path") or "")
    # Whether the session's transcript .jsonl is GONE (known path, absent on
    # disk) — the composer's resume-&-send door is dead for it (`claude
    # --resume` finds no conversation, the launched tab exits at once). An
    # empty/unknown path is NOT flagged: we can't prove it's broken, so the
    # CLI decides (docs/dashboard.md *Resume & send*).
    _tp = data.get("transcript_path") or ""
    data["transcript_missing"] = bool(_tp) and not os.path.isfile(_tp)
    data["ctx"] = session_ctx(data.get("transcript_path") or "", main=True)
    # is the conversation being compacted right now — the ctx bar's animation
    # (docs/dashboard.md, *Compaction on the ctx bar*). Seeds a page opened
    # mid-compaction; the `compacting` SSE event carries the transitions.
    data["compacting"] = session_compacting(sid)
    data["cwd"] = canon_cwd(data.get("cwd") or "")   # collapse the /kitty symlink
    data["git"] = git_info(data["cwd"])
    # the effort quick-button's label (docs/dashboard.md, *Web quick
    # commands*): the SAVED effort level — every /effort persists itself
    # there, so it is the last applied value; per-session effort is readable
    # from nowhere else. Resolved for the session's ACCOUNT (its statusline-
    # stashed slug picks the config dir — accounts each carry their own
    # settings.json)
    data["effort"] = plugins.effort_default(data.get("cwd") or "",
                                            session_slug(sid))
    # the agent cards' per-agent model·effort — reuses the ctx just stamped, so
    # the session effort resolved above is its inherit-default
    agents_model_effort(data["agents"], data["effort"])
    data["running"] = API.running(sid)
    # the in-flight foreground command ({g, start_ts}) — seeds the mirror's
    # live elapsed chip on RELOAD, so a page opened mid-command starts ticking
    # from the real start instead of waiting for the block to finish
    data["fg_running"] = API.fg_running(sid)
    # Correct `live` to require an OPEN tab and gate the control plane on the
    # LIVE window (the pane currently tagged claude_session=<sid>), NOT the
    # audit row's start-time id — kitty reuses window ids, so a leaked/parked
    # "live" session would otherwise show a stop button that closes an
    # unrelated tab (see live_windows). A session whose state DB lingers but
    # whose window is gone (closed without a SessionEnd) is demoted to not-live.
    live_wins = launch.live_windows()
    row = API.session_row(sid) or {}
    # the live flag lives on `data` (API.session), but the window id + grace
    # come from the audit `row` (session_row carries no `live` key) — so the
    # shared demotion reads/clears `data` while checking `row`'s window.
    launch.demote_if_dead(row, live_wins, sid, target=data)
    data["kitty_window_id"] = (live_wins or {}).get(sid, "") if data.get("live") else ""
    data["ask"] = ask_wire(sid, ask_pending(sid)) if data.get("live") else None
    data["ask_draft"] = ask_draft(sid, data["ask"]) if data.get("ask") else None
    data["plan"] = plan_pending(sid) if data.get("live") else None
    # deliberately NOT live-gated: the `tasks` kv survives park (Claude Code
    # deletes the on-disk task files at session end — the stash is the only
    # record left), so a parked session still shows its final task list. The
    # card's ✕ dismissal rides alongside it (tasks_card) — the flag is what
    # makes the hide cross-device, and it survives park for the same reason
    card = tasks_card(sid)
    data["tasks"] = card["tasks"]
    data["tasks_hidden"] = card["hidden"]
    # deliberately NOT live-gated: the active /goal lives in the transcript
    # (which persists past park, unlike the task files), so a parked session
    # still shows its final/achieved goal — read-side, no hook (docs/dashboard.md
    # *Web goal*)
    data["goal"] = session_goal(data.get("transcript_path") or "")
    # the ✦ model button's ⚠ — a safeguard refusal rerouted the session to a
    # fallback model; served only while the ctx model still IS that fallback
    # model, so a /model switch retires it (docs/dashboard.md *Model fallback
    # warning*). Not live-gated for the same reason as goal: the record lives
    # in the transcript, and a parked session's header should still say it
    data["fallback"] = session_fallback(data.get("transcript_path") or "")
    # how many prompts YOU typed (capped; None = nothing to conclude) — the ⊜
    # compact button's gate, since Claude Code refuses /compact on a
    # conversation that has barely started (docs/dashboard.md *Header action
    # bar*). Not live-gated: the header shows every action for a parked session
    # too, greyed with the reason.
    data["prompts"] = session_prompts(data.get("transcript_path") or "")
    # deliberately NOT live-gated: the composer stays usable on a PARKED
    # session (the resume-&-send door), so its draft must restore there too
    data["composer_draft"] = composer_draft(sid)
    data["composer_queue"] = composer_queue(sid)
    # deliberately NOT live-gated: the Telegram-alert opt-out is a dashboard
    # pref (docs/dashboard.md, *Telegram alerts*), so the header toggle reflects
    # + flips it live AND parked
    data["notify_muted"] = prefs.notify_muted(sid)
    # deliberately NOT live-gated either: the mirror's view mode is a reading
    # preference over the stream, which a PARKED session still has
    # (docs/dashboard.md, *View modes*)
    data["view_mode"] = prefs.view_mode(sid)
    # the session's real slash-command NAMES — the server tints them inside the
    # prompt bubbles it renders, and the page needs the same truth for the two
    # bubbles it builds ITSELF (the optimistic stand-in + the ⧗ queued chip),
    # which never pass through msg_html. Shipped here rather than fetched: one
    # source, so the two renderers can't disagree about what a real command is.
    data["commands"] = sorted(cmd_names(data.get("cwd") or ""))
    return data


def _dialog_pending(sid, key):
    """A pending modal-dialog stash (`ask-pending` / `plan-pending`), or None
    — the kv rows plugins/claude_code/ask_fmt.py maintains (write on
    PreToolUse, cleared on answer/turn-boundary). Read-only (kv_at — never
    creates the state DB). The endpoints verify the DIALOG on screen anyway,
    so a stale stash can never mis-answer."""
    pending = session_kv(sid, key)
    return pending if isinstance(pending, dict) else None


def ask_pending(sid):
    return _dialog_pending(sid, "ask-pending")


def ask_wire(sid, ask):
    """The pending ask ENRICHED for the page: `preamble_html` — Claude's prose
    LEAD-IN to the question (the text framing it, which the terse dialog stash
    omits; plugins.ask_preamble over the transcript), rendered with the
    msg-bubble md_html (escape-first, the neutralize() analog). So the "why"
    Claude gave rides ON the ask card, not just as a detached stream bubble
    (docs/dashboard.md, *Web ask*). Kept OUT of ask_pending — that is the
    per-tick SSE change-detection poll and must stay a cheap kv read; the
    transcript is touched only when the ask actually changes / on session open.
    Defensive: a preamble read that fails must never block the question from
    rendering, so it degrades to "". None passes through (the ask cleared)."""
    if not ask:
        return ask
    try:
        pre = plugins.ask_preamble(sid, ask.get("tool_use_id") or "") or ""
    except Exception:
        pre = ""
    ask = dict(ask)
    ask["preamble_html"] = opshtml.md_html(pre) if pre else ""
    return ask


def ask_draft(sid, ask=None):
    """The unsubmitted ask answers (the `ask-draft` kv — written by the web
    ask card so a device switch / reopen restores in-progress selections),
    but ONLY when it still matches the OPEN ask: a draft left over from a
    replaced/answered question is ignored (ask_fmt.py clears it on the turn
    boundary anyway). Read-only (kv_at). None when there's no ask, no draft,
    or a tool_use_id mismatch."""
    ask = ask if ask is not None else ask_pending(sid)
    if not ask:
        return None
    draft = session_kv(sid, "ask-draft")
    if not isinstance(draft, dict):
        return None
    if (draft.get("tool_use_id") or "") != (ask.get("tool_use_id") or ""):
        return None
    return draft


def session_compacting(sid, sdb=None):
    """Whether this session is compacting RIGHT NOW — {"since", "trigger"} or
    None. The `compacting` kv latch (compact_fmt.py: armed on PreCompact,
    cleared on PostCompact) behind the ctx bar's animation (docs/dashboard.md,
    *Compaction on the ctx bar*). Read-only (kv_at — never creates the state
    DB). `sdb` is the SSE tick's already-resolved path (see session_kv): this
    rides the FAST cadence, so it must not re-walk the adopt chain per tick.

    AGED OUT past config.COMPACT_MAX_S: a compaction that died on an API error
    or was interrupted fires no PostCompact, and the hook that armed the latch
    has long exited, so this read — re-evaluated every tick — is the only place
    that can end it. A stale latch must read as "not compacting", never as a
    bar that animates for the rest of the session.

    Deliberately NOT live-gated in the sense the dialogs are: a session that
    parked mid-compaction has no animation to show anyway (the latch dies with
    the arm's age), and gating on `live` would need a second lookup here for a
    question the expiry already answers."""
    rec = session_kv(sid, "compacting", sdb)
    if not isinstance(rec, dict):
        return None
    try:
        since = float(rec.get("ts") or 0)
    except (TypeError, ValueError):
        return None
    if since <= 0 or time.time() - since > config.COMPACT_MAX_S:
        return None
    return {"since": since, "trigger": rec.get("trigger") or ""}


def composer_draft(sid):
    """The UNSENT composer text (the `composer-draft` kv — written by the web
    composer so a device switch / reopen / return-to-session restores the
    half-typed message, docs/dashboard.md, *Web composer draft*). Read-only
    (kv_at — never creates the state DB; resolves the parked copy for a parked
    session, so a resume-&-send draft survives too). None when there's no draft
    or the stored text is empty — None keeps the composer blank."""
    draft = session_kv(sid, "composer-draft")
    if not isinstance(draft, dict) or not (draft.get("text") or "").strip():
        return None
    return draft


# the tab states where Claude has SETTLED and its input box may hold a ghost
# suggestion — green (done, your turn) and grey (idle). A busy/asking tab never
# shows one, so we don't screen-scrape then.
SUGGEST_TABS = (tabs.AWAITING_RESPONSE, tabs.IDLE)


def input_box(sid):
    """A LIVE session's input box, read straight off the TUI screen (no hook
    fires for either half): (ghost, typed) — the faint pre-filled 'suggested
    answer' Claude Code shows when a turn settles (docs/dashboard.md, *Web ghost
    suggestion*), and the REAL text the user has typed there (*Terminal draft
    sync*). At most one is non-None; (None, None) when no frontend/live window
    resolves or the box is empty. The CALLER gates on a settled tab + no pending
    ask/plan so we only screen-scrape when the box is worth reading — this just
    resolves the authoritative live window (the memoized claude_session=<sid>
    map, never a reused start-time id) and probes it ONCE for both."""
    fe = launch.frontend()
    if fe is None:
        return None, None
    win = (launch.live_windows() or {}).get(sid)
    if not win:
        return None, None
    return suggestion.probe_box(fe, win, sid)


def _delivered_prompts(sid):
    """The trimmed text of every prompt already DELIVERED into sid's transcript
    (kind == "prompt" from the main-thread conversation, which surfaces the
    TUI's delivered `queued_command` attachment among plain replies). The
    reconciliation source for the composer queue's ⧗ chips."""
    got = plugins.conversation(sid, 0)
    recs = got[0] if got else []
    return [(r.get("text") or "").strip() for r in recs
            if r.get("kind") == "prompt" and (r.get("text") or "").strip()]


def chip_delivered(text, delivered):
    """True when a queued ⧗ chip's / optimistic bubble's text matches a
    DELIVERED prompt. THE match rule for reconciling web-composer stand-ins
    against the transcript — owner of the fact, mirrored in app.js
    `promptMatches` (drainQueue + drainPending), which JS cannot import.

    A SUFFIX match, not exact: what the composer sent can arrive with anything
    prepended, and both known prefixes are real.
      · attachments prepend `@path` mentions + '\\n' (server _with_attachments);
      · text ALREADY IN THE TUI INPUT BOX is glued on with NO separator — a
        terminal-side Escape can hand the previous message back there
        and the page can't know (its `clear_draft` never fires), so the paste
        lands after it: `testing` + the sent text arrived as one prompt and the
        old '\\n'-only tolerance missed, pinning the chip forever (session
        bdeca061, 2026-07-25).
    So the separator is not required. Empty chip text never matches (it would
    match every prompt)."""
    c = (text or "").strip()
    return bool(c) and any(d.endswith(c) for d in delivered)


def composer_queue(sid):
    """The still-PENDING queued messages (the `composer-queue` kv — the ⧗ chips
    the composer shows for messages typed mid-turn that the TUI queued and has
    not yet delivered). Browser memory alone lost these on a reload (the "gone
    even from the queue after refresh" report, 2026-07-19), so the page mirrors
    its chip list here; a delivered message is reconciled out client-side when
    its prompt lands in the stream.

    But that client-side drain only reconciles NEW stream items — never the
    already-loaded history — so a chip persisted here by a client that then
    closed / reloaded BEFORE its message was delivered re-seeded from the kv
    FOREVER (buildQueueBar restores it, the delivered prompt is already in the
    backlog, and no fresh item ever arrives to drain it — the "queued chip stuck
    after the message was delivered" report). So reconcile against the transcript
    HERE too: drop any chip whose prompt already landed. Read-only (kv_at / a
    transcript parse) — the kv itself isn't rewritten (mode=ro); the client's
    next saveQueue prunes the stale rows once this filtered list seeds it.
    {"items": [{text}, …], "origin": …} or None when empty (docs/dashboard.md,
    *Web composer queue*)."""
    q = session_kv(sid, "composer-queue")
    items = q.get("items") if isinstance(q, dict) else None
    if not items:
        return None
    delivered = _delivered_prompts(sid)
    kept = [it for it in items if not chip_delivered((it or {}).get("text"), delivered)]
    if not kept:
        return None
    out = dict(q)
    out["items"] = kept
    return out


def session_tasks(sid):
    """The session's task-list snapshot — the `tasks` kv task_fmt.py re-reads
    from Claude Code's on-disk task dir on every task-touching hook (docs/
    dashboard.md, *Web tasks*). A list of task records ({id, subject, status,
    …}, id-sorted), or None when the session never had tasks / the list is
    empty — None keeps the card hidden. Read-only (kv_at)."""
    stash = session_kv(sid, "tasks")
    tasks = stash.get("tasks") if isinstance(stash, dict) else None
    return tasks if isinstance(tasks, list) and tasks else None


def tasks_done(tasks):
    """True when `tasks` is a non-empty list in which EVERY task is completed —
    the one gate on the card's ✕ (docs/dashboard.md, *Web tasks*). The predicate
    lives here, not in the button: the page disables the ✕, the POST rejects a
    409, and both must mean the same thing. A junk (non-dict) entry counts as
    not-completed, so it can never make a live list look finished."""
    return bool(tasks) and all(isinstance(t, dict) and t.get("status") == "completed"
                               for t in tasks)


def tasks_hidden(sid, tasks):
    """True when the user dismissed the tasks card AND that dismissal still
    applies to `tasks` — i.e. the list is still finished and still the SAME set
    of ids that was dismissed (dashboard/prefs.py, tasks-hidden). A new task, or
    a completed one re-opened, fails one of those and the card comes back on its
    own; there is deliberately no un-hide gesture. Purely visual — nothing here
    reads or writes a task's real state."""
    if not tasks_done(tasks):
        return False
    hidden = set(prefs.tasks_hidden_ids(sid))
    return bool(hidden) and all(str(t.get("id")) in hidden for t in tasks)


def tasks_card(sid):
    """The pinned tasks card's whole wire state — `{"tasks": <list|None>,
    "hidden": <bool>}`. ONE value so the SSE channel diffs the list and the
    dismissal TOGETHER: hiding the card on the phone changes no task, so a
    tasks-only diff would leave the desktop's copy pinned until the next
    TaskUpdate (the cross-device half of the gesture)."""
    tasks = session_tasks(sid)
    return {"tasks": tasks, "hidden": tasks_hidden(sid, tasks)}


def plan_pending(sid):
    """The pending plan, ENRICHED for the page: `plan_html` (the markdown
    rendered server-side, the msg-bubble md_html — escape-first)."""
    pending = _dialog_pending(sid, "plan-pending")
    if pending and "plan_html" not in pending:
        pending = dict(pending)
        pending["plan_html"] = opshtml.md_html(pending.get("plan") or "")
    return pending


def last_prompt_rec(sid):
    """The session's LAST main-thread user prompt as (text, uuid) — what an
    early interrupt hands back into the input, so the page can prefill its
    composer with it and the server can FLAG that record as taken back
    (transcript.mark_taken_back; without the flag the bubble reappears on the
    next full read, since a taken-back prompt has no sibling until the
    replacement message arrives). Best-effort: a read failure yields ("", "")
    — the take-back still happened in the terminal."""
    try:
        got = plugins.conversation(sid)
        if not got:
            return "", ""
        recs, _ = got
        for r in reversed(recs):
            if r.get("kind") == "prompt":
                return r.get("text") or "", r.get("uid") or ""
    except Exception:
        pass
    return "", ""


def last_prompt(sid):
    """The last prompt's TEXT alone (see last_prompt_rec)."""
    return last_prompt_rec(sid)[0]
