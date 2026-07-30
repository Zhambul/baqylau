# core/tabpaint.py — the tool-AGNOSTIC tab PAINT engine.
#
# The tab-colour PAINT step, extracted from plugins/claude_code/tabstatus.py so a
# SECOND tab producer (standalone codex, a future hookless polled producer) reuses
# it instead of reimplementing the dedup + persist-only-on-rc==0 + audit rules — a
# second copy would drift and lose the rc==0 rule, the exact bug class the comment
# in paint() below documents (persisting a FAILED paint stranded a colour the tab
# never showed, and the dedup then suppressed every retry).
#
# The split between this and a producer plugin:
#   - the PLUGIN owns the DECISION (map its own events → a literal `(state, reason)`)
#     and the WINDOW RESOLUTION (which kitty window this session's tab belongs to);
#   - this module owns the PAINT — given a resolved frontend + window + (state,
#     reason), it dedups against the persisted tab-DB row, calls the frontend's
#     set_tab_color/clear_tab_color, persists the row ONLY on rc==0, and writes a
#     `tab_transitions` audit row on every applied/skipped/failed path.
# So a new producer contributes just a `{event → (state, reason)}` decision + a
# window resolver and gets the dedup/persist/audit for free (plugins/claude_code/
# tabstatus.py is the reference producer; docs/tab-colors.md).
#
# FRONTEND-INJECTED, exactly like core/hostpane.py: core imports no frontend, so
# the caller passes its `fe` (a frontends.Frontend). The caller also passes the
# audit identity (`sid` + the `dispatch` label) so the tab_transitions row keeps
# its per-producer vocabulary.
from core.noaudit import load_audit  # in-process; every write swallows + spools

A = load_audit()   # audit trail (real module, or an inert stub if it can't import)
from core.tabs import COLORS, tab_clear, tab_get, tab_set  # noqa: E402  (the tab vocabulary)


def agent_inner_event(payload):
    """THE main-tab doctrine, one owner: a hook event carrying an `agent_id` is a
    SUBAGENT's / TEAMMATE's own inner call, NOT the lead's — the tab tracks the
    MAIN session only, so a producer must NOT paint on it (the agent's own
    lifecycle is bracketed by the LEAD's tool events + its final Stop). Every tab
    producer consults this: the Claude handlers (`plugins/claude_code/tabstatus`)
    and the codex producer (`plugins/codex/tabstatus`). Codex re-implementing the
    tab as a stateless event→colour map WITHOUT this rule is what left a standalone
    codex host stuck magenta — a late `SubagentStop` (which carries the child's
    agent_id) repainted WORKING over the resting green after the turn's real Stop
    (docs/tab-colors.md *Main session only*)."""
    return bool((payload or {}).get("agent_id"))


def paint(fe, win, state, reason="", *, sid="", dispatch=""):
    """Paint `win`'s tab to `state` through frontend `fe`, with the engine's dedup
    + persist-on-rc==0 + `tab_transitions` audit. `state` is a literal tab state (a
    `core.tabs.COLORS` key) or one of clear/reset/"" (revert to the theme default);
    the caller has already resolved it — and the WHY, `reason` — from its own
    dispatch, and the window from its own anchoring. Never raises (a hook path):
    every audit write swallows, and a resolved-but-absent window / unusable
    frontend no-ops silently.

    `sid`/`dispatch` are the audit identity only — the session id and the dispatch
    label (`stop`/`pretool`/…) that name the tab_transitions row."""
    def tx(prev, new, applied, why):
        try:
            A.transition(sid, win, dispatch, prev, new, applied, why)
        except Exception:
            pass

    # Must be inside a controllable terminal (kitty: window id + remote-control
    # socket), else no-op silently. (Audited so the trail shows the hook fired
    # even where the tab can't be set.)
    if not win or not fe.available():
        tx("", state, 0, "skipped: not inside kitty / no remote-control socket")
        return

    # Skip the work entirely when the tab is ALREADY showing this state.
    # Tool-heavy turns fire many hooks that all resolve to the same colour (a run
    # of Read/Edit/MCP calls all become WORKING), and re-applying an identical
    # colour is a wasted socket round-trip. The persisted state row (written at
    # the end of every applied change) is our record of what's currently shown: if
    # it matches, there's nothing to do — bail before touching the socket.
    # (clear/reset deletes the row, so an empty prev_state means "already cleared".)
    prev_state = tab_get(win)
    if state in ("clear", "reset", ""):
        if not prev_state:
            return
    elif state == prev_state:
        tx(prev_state, state, 0, "skipped: colour already shown")
        return

    if not fe.usable():
        return

    if state in COLORS:
        rc = fe.set_tab_color(win, *COLORS[state])
    elif state in ("clear", "reset", ""):
        rc = fe.clear_tab_color(win)
    else:
        return

    # Persist the resolved state (tab DB row) so bg-recheck / bg-watch can tell
    # whether a finishing background job should flip the stale bg-running blue back
    # to green — but ONLY when the paint actually landed (rc == 0). Persisting a
    # failed paint made the DB claim a colour the tab never showed, and the
    # "colour already shown" dedup above then suppressed every retry of that same
    # state: one transient socket error stranded the old colour until a DIFFERENT
    # state came along. Leaving the row unchanged keeps the next same-state event
    # eligible to retry the paint.
    if rc == 0:
        tx(prev_state, state, 1, reason)
        if state in COLORS:
            tab_set(win, state)
        else:
            tab_clear(win)
    else:
        tx(prev_state, state, 0,
           (f"{reason} — " if reason else "")
           + f"kitten @ failed rc={rc} — state row unchanged")
