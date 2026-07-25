# dashboard/read/session.py — ONE session's detail payload + its modal cards.
#
# session_payload and everything it composes: the agent scoreboard helpers, the
# pending AskUserQuestion / ExitPlanMode / composer-draft / ghost-suggestion /
# tasks / queue cards. Read-only; the per-session metadata comes from
# read/meta.py, the live window from control/launch.py.
import os

import plugins
from core import sessionapi as API
from core import tabs
from dashboard import opshtml, prefs, suggestion
from dashboard.control import launch
from dashboard.read.meta import (canon_cwd, cmd_names, git_info, session_ctx,
                                 session_goal, session_kv, session_title,
                                 _session_slug)
from plugins.claude_code import accounting as ACC
from plugins.claude_code import memory as MEM
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


def _stamp_agent_cost(tl):
    """Stamp a subagent drill-down payload with `cost` — approximate USD for its
    OWN token rollup, priced from `usage` + the run's last model via the shared
    accountant (the web per-agent scoreboard's ≈cost, docs/dashboard.md *Subagent
    scoreboard swap*). None for an unknown/empty model (codex runs, husk reads) —
    the client just omits the ≈cost chip. This transcript pricing is the ONLY
    per-agent cost figure: OTEL `costs()` is aggregate by query_source
    (main/subagent/auxiliary), never attributable to a single agent_id."""
    u = tl.get("usage") or {}
    if not u:
        return
    tl["cost"] = ACC.cost_usd(tl.get("model"), u.get("in", 0), u.get("out", 0),
                              u.get("cache", 0), u.get("create", 0),
                              u.get("create_1h", 0))


def memory_scope(cwd):
    """True when a session working in `cwd` gets the Memory tab at all — the
    feature is deliberately scoped to one project (plugins/claude_code/memory
    `in_scope`), though the wiki it reads is shared across code/01."""
    return MEM.in_scope(canon_cwd(cwd))


def memory_count(sid, cwd):
    """The Memory tab badge's number, 0 off-scope. The gate lives HERE, with one
    owner, because the badge has TWO readers: this module's overview payload and
    the SSE stream's live patch. They disagreed — the stream pushed the real
    count for an off-scope session, at a tab the page never builds there (benign
    on screen, but a per-tick kv read for nobody, and two readings of one rule)."""
    return API.memory_count(sid) if memory_scope(cwd) else 0


def session_payload(sid):
    """One session's overview — session() plus the error count the ⚠ badge
    shows (full rows stay behind /errors) and the display title."""
    data = API.session(sid)
    data["agents"] = agents_ctx(visible_agents(data.get("agents") or []))
    data["error_count"] = API.error_count(sid)
    data["monitor_count"] = API.monitor_count(sid)   # the monitors tab badge
    data["job_count"] = API.job_count(sid)           # the jobs tab badge
    # the Memory tab is SCOPED: only sessions inside the enabled project
    # (aggregator-adapters) get it. The flag gates the tab client-side (hidden
    # off-scope); the count still rides along (0 off-scope — nothing recorded).
    data["memory_scope"] = memory_scope(data.get("cwd") or "")
    data["memory_count"] = memory_count(sid, data.get("cwd") or "")
    data["title"] = session_title(data.get("transcript_path") or "")
    # Whether the session's transcript .jsonl is GONE (known path, absent on
    # disk) — the composer's resume-&-send door is dead for it (`claude
    # --resume` finds no conversation, the launched tab exits at once). An
    # empty/unknown path is NOT flagged: we can't prove it's broken, so the
    # CLI decides (docs/dashboard.md *Resume & send*).
    _tp = data.get("transcript_path") or ""
    data["transcript_missing"] = bool(_tp) and not os.path.isfile(_tp)
    data["ctx"] = session_ctx(data.get("transcript_path") or "", main=True)
    data["cwd"] = canon_cwd(data.get("cwd") or "")   # collapse the /kitty symlink
    data["git"] = git_info(data["cwd"])
    # the effort quick-button's label (docs/dashboard.md, *Web quick
    # commands*): the SAVED effort level — every /effort persists itself
    # there, so it is the last applied value; per-session effort is readable
    # from nowhere else. Resolved for the session's ACCOUNT (its statusline-
    # stashed slug picks the config dir — accounts each carry their own
    # settings.json)
    data["effort"] = plugins.effort_default(data.get("cwd") or "",
                                            _session_slug(sid))
    # the agent cards' per-agent model·effort — reuses the ctx just stamped, so
    # the session effort resolved above is its inherit-default
    agents_model_effort(data["agents"], data["effort"])
    data["running"] = API.running(sid)
    # Correct `live` to require an OPEN tab and gate the control plane on the
    # LIVE window (the pane currently tagged claude_session=<sid>), NOT the
    # audit row's start-time id — kitty reuses window ids, so a leaked/parked
    # "live" session would otherwise show a stop button that closes an
    # unrelated tab (see _live_windows). A session whose state DB lingers but
    # whose window is gone (closed without a SessionEnd) is demoted to not-live.
    live_wins = launch._live_windows()
    row = API.session_row(sid) or {}
    # the live flag lives on `data` (API.session), but the window id + grace
    # come from the audit `row` (session_row carries no `live` key) — so the
    # shared demotion reads/clears `data` while checking `row`'s window.
    launch.demote_if_dead(row, live_wins, sid, target=data)
    data["kitty_window_id"] = (live_wins or {}).get(sid, "") if data.get("live") else ""
    data["ask"] = _ask_wire(sid, _ask_pending(sid)) if data.get("live") else None
    data["ask_draft"] = _ask_draft(sid, data["ask"]) if data.get("ask") else None
    data["plan"] = _plan_pending(sid) if data.get("live") else None
    # deliberately NOT live-gated: the `tasks` kv survives park (Claude Code
    # deletes the on-disk task files at session end — the stash is the only
    # record left), so a parked session still shows its final task list
    data["tasks"] = _session_tasks(sid)
    # deliberately NOT live-gated: the active /goal lives in the transcript
    # (which persists past park, unlike the task files), so a parked session
    # still shows its final/achieved goal — read-side, no hook (docs/dashboard.md
    # *Web goal*)
    data["goal"] = session_goal(data.get("transcript_path") or "")
    # deliberately NOT live-gated: the composer stays usable on a PARKED
    # session (the resume-&-send door), so its draft must restore there too
    data["composer_draft"] = _composer_draft(sid)
    data["composer_queue"] = _composer_queue(sid)
    # deliberately NOT live-gated: the Telegram-alert opt-out is a dashboard
    # pref (docs/dashboard.md, *Telegram alerts*), so the header toggle reflects
    # + flips it live AND parked
    data["notify_muted"] = prefs.notify_muted(sid)
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


def _ask_pending(sid):
    return _dialog_pending(sid, "ask-pending")


def _ask_wire(sid, ask):
    """The pending ask ENRICHED for the page: `preamble_html` — Claude's prose
    LEAD-IN to the question (the text framing it, which the terse dialog stash
    omits; plugins.ask_preamble over the transcript), rendered with the
    msg-bubble md_html (escape-first, the neutralize() analog). So the "why"
    Claude gave rides ON the ask card, not just as a detached stream bubble
    (docs/dashboard.md, *Web ask*). Kept OUT of _ask_pending — that is the
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


def _ask_draft(sid, ask=None):
    """The unsubmitted ask answers (the `ask-draft` kv — written by the web
    ask card so a device switch / reopen restores in-progress selections),
    but ONLY when it still matches the OPEN ask: a draft left over from a
    replaced/answered question is ignored (ask_fmt.py clears it on the turn
    boundary anyway). Read-only (kv_at). None when there's no ask, no draft,
    or a tool_use_id mismatch."""
    ask = ask if ask is not None else _ask_pending(sid)
    if not ask:
        return None
    draft = session_kv(sid, "ask-draft")
    if not isinstance(draft, dict):
        return None
    if (draft.get("tool_use_id") or "") != (ask.get("tool_use_id") or ""):
        return None
    return draft


def _composer_draft(sid):
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
_SUGGEST_TABS = (tabs.AWAITING_RESPONSE, tabs.IDLE)


def _input_box(sid):
    """A LIVE session's input box, read straight off the TUI screen (no hook
    fires for either half): (ghost, typed) — the faint pre-filled 'suggested
    answer' Claude Code shows when a turn settles (docs/dashboard.md, *Web ghost
    suggestion*), and the REAL text the user has typed there (*Terminal draft
    sync*). At most one is non-None; (None, None) when no frontend/live window
    resolves or the box is empty. The CALLER gates on a settled tab + no pending
    ask/plan so we only screen-scrape when the box is worth reading — this just
    resolves the authoritative live window (the memoized claude_session=<sid>
    map, never a reused start-time id) and probes it ONCE for both."""
    fe = launch._frontend()
    if fe is None:
        return None, None
    win = (launch._live_windows() or {}).get(sid)
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


def _chip_delivered(text, delivered):
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


def _composer_queue(sid):
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
    kept = [it for it in items if not _chip_delivered((it or {}).get("text"), delivered)]
    if not kept:
        return None
    out = dict(q)
    out["items"] = kept
    return out


def _session_tasks(sid):
    """The session's task-list snapshot — the `tasks` kv task_fmt.py re-reads
    from Claude Code's on-disk task dir on every task-touching hook (docs/
    dashboard.md, *Web tasks*). A list of task records ({id, subject, status,
    …}, id-sorted), or None when the session never had tasks / the list is
    empty — None keeps the card hidden. Read-only (kv_at)."""
    stash = session_kv(sid, "tasks")
    tasks = stash.get("tasks") if isinstance(stash, dict) else None
    return tasks if isinstance(tasks, list) and tasks else None


def _plan_pending(sid):
    """The pending plan, ENRICHED for the page: `plan_html` (the markdown
    rendered server-side, the msg-bubble md_html — escape-first)."""
    pending = _dialog_pending(sid, "plan-pending")
    if pending and "plan_html" not in pending:
        pending = dict(pending)
        pending["plan_html"] = opshtml.md_html(pending.get("plan") or "")
    return pending


def _last_prompt_rec(sid):
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


def _last_prompt(sid):
    """The last prompt's TEXT alone (see _last_prompt_rec)."""
    return _last_prompt_rec(sid)[0]
