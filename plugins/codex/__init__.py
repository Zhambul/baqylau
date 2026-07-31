# plugins/codex/ — the codex adapter, in BOTH of its roles: a secondary source
# inside a Claude session AND a standalone HOST of its own (docs/codex.md).
#
# As a SECONDARY source it has no hook system pointed at us: the plugin
# discovers every codex run from the two global directories all runs funnel
# through (companion job sidecars + native rollouts — see watch.py) and streams
# each into the HOSTING session's mirror. As a HOST it runs its own SessionStart
# (session.py), tab producer, hook dispatch and control gestures. Modules:
# launch.py (detach-fast launcher), watch.py (the one-per-session discovery
# watcher / standalone manager), stream.py (one tailer per run — the paint
# half), rollout.py (rollout-record parsing — the parse half of the split).
import os
import subprocess
import sys

from core.paths import BIN  # bin/, where the sibling ENTRY scripts live


def on_session_start(log, cwd, sid):
    """Attach codex discovery to a starting host session: run the launcher
    entry, which Popens the watcher DETACHED (start_new_session) and exits in a
    few ms — so SessionStart can never hang on it (the hard-won lesson in
    plugins/codex/launch.py). Invoked via the plugins registry from the host's
    SessionStart — claude_code's (split.py cmd_open) AND, since P4, codex's own
    (session.py main).

    TWO WATCHERS, ONE PROVIDER. The watcher has two roles, selected by whether a
    HOST_PID rides argv[4] (watch.py's header): the SECONDARY discovery watcher
    that streams codex runs into a hosting Claude session, and the STANDALONE
    manager that streams this session's own rollout and owns its teardown. Which
    one to start is not the caller's business — it follows from whether THIS sid
    is a recorded standalone codex host, which session.py has just stamped
    (tabs.codex_host_mark) by the time it calls the fan-out. So the choice lives
    here, and both hosts call the same one line.

    That is what fixes the bug this provider had all along: codex/session.py
    spawned its watcher DIRECTLY and never called plugins.on_session_start, so
    the fan-out ran for Claude sessions only — every cross-cutting plugin (otel
    today) was invisible to a standalone codex host, and this very provider was
    dead code on that path. Routing session.py through the fan-out instead would
    have spawned the watcher TWICE unless the provider knew the difference; now
    it does."""
    from core import tabs as T
    from core.noaudit import load_audit
    A = load_audit()
    launcher = os.path.join(BIN, "claude-codex-launch.py")
    if not os.path.isfile(launcher):
        return
    argv = [sys.executable or "python3", launcher, log, cwd, sid]
    try:
        if T.codex_host_win(sid) is not None:
            # STANDALONE: this sid IS a codex host. The watcher additionally
            # needs the codex process to watch for liveness — codex fires no
            # SessionEnd, so pid death IS the teardown signal (session.py).
            from plugins.codex import session
            argv.append(str(session.codex_pid()))
    except Exception:
        # The ROLE resolution failed, and the degrade is silent BY SHAPE: the
        # watcher still starts, just in the SECONDARY role — no rollout stream
        # for this session's own thread and, worse, no teardown owner, since the
        # standalone manager is the only thing that watches the codex pid. A
        # session that quietly lost half its watcher looks exactly like one that
        # never had a standalone role to begin with, so it gets its OWN row
        # rather than sharing the spawn failure's.
        A.error(log, "codex watcher role (standalone -> secondary)",
                {"sid": sid, "cwd": cwd})
    try:
        subprocess.run(argv, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except Exception:
        # …and the launcher itself. session.py audited this before the provider
        # took the spawn over; losing the row with the move is how a codex
        # session with NO mirror stream at all became undebuggable.
        A.error(log, "spawn codex watcher launcher",
                {"sid": sid, "cwd": cwd, "argv": len(argv)})


# NB codex exposes no `agent_usage` provider: a run's tokens are folded from its
# rollout at its footer and booked straight into the scoreboard (its own
# CODEX_PRICES — plugins/codex/stream.py), so there is no transcript for the web
# to re-price. The fan-out finding nothing here is the correct outcome, not a gap.


# --- the PROVIDER surface (docs/sessionapi.md; plugins.PROVIDERS declares each) ---
# codex is now a first-class HOST and read source: it OWNS its rollouts and
# answers the path-keyed read fan-outs for them (ctx/prompts/title/conversation/
# effort), the per-session limits/account/costs facets, the usage strip, and
# pending_dialog. Every one is a thin delegation to the concern module
# (rollout=parse, read=rollout read models, title=naming, hostctl=control,
# usage=limits+costs) — the same shape claude_code's __init__ providers take.
# Read-only: these add NO audit rows (like ctx/goal), EXCEPT the app-server
# account read behind usage_strip, which audits a degrade once (usage.py).


def owns(path):
    """The ownership provider (plugins.owns / owns_by / host_of, and the gate
    every path-keyed read fan-out applies through plugins._first_path) — True only
    for a codex rollout this plugin genuinely speaks. Without it, first-plugin-wins
    hands a codex rollout to a Claude parser whose bounded fast paths answer
    confidently about a file they never read. See rollout.owns."""
    from plugins.codex import rollout
    return rollout.owns(path)


def host():
    """The HOST-control provider (plugins.host_named / hosts / host_of) — codex's
    plugins.host.HostControl adapter. In P3 it drives NO gesture (caps all False),
    so the dashboard greys codex's control buttons until P5 wires the app-server
    transport. Imports `hostctl` (NOT `host` — this provider FUNCTION shadows a
    `host` submodule). See plugins/codex/hostctl.py."""
    from plugins.codex import hostctl
    return hostctl.get()


def runs(sid):
    """The NESTED-RUN provider (plugins.runs fan-out) — this session's codex
    runs as agent rows (kind 'codex'), which core.sessionapi.agents() splices in
    beside the Claude subagents/teammates. Includes the standalone host's OWN-run
    drop: its rollout IS the session, and listing it would mint a card whose
    scope matches no op. See nested.session_runs (the old core-side
    `sessionapi.codex_runs`, moved to the plugin that owns every fact in it)."""
    from plugins.codex import nested
    return nested.session_runs(sid)


# NB codex exposes no `nested_owners` provider: that fan-out reads a host's
# LAUNCH-HOOK payloads for background jobs and monitors, and codex writes no such
# rows — it has no bg-job or monitor concept at all (docs/codex.md). The declared
# zero is the honest answer; an empty implementation would only hide it.


def context(transcript_path, main=False):
    """The context-saturation provider (plugins.context fan-out) — a codex
    rollout's last-turn total over its context window, as {used, window, pct,
    model}; None for a fresh/unreadable rollout. See read.context."""
    from plugins.codex import read
    return read.context(transcript_path, main=main)


def prompts(transcript_path):
    """The human-prompt-count provider (plugins.prompts fan-out) — non-synthetic
    user turns in a codex rollout, capped; None when none. Backs the ⊜ compact
    gate for a codex session. See read.prompts."""
    from plugins.codex import read
    return read.prompts(transcript_path)


def conversation(sid, pos=0, agent_id=""):
    """The conversation provider (plugins.conversation fan-out) — ONE codex
    identity's prose bubbles from its rollout (a sidecar run by agent_id, the
    standalone host's own thread otherwise). THE core of codex sidecar → subagent
    parity (docs/codex.md, docs/dashboard.md *Agent scope*). See read.conversation."""
    from plugins.codex import read
    return read.conversation(sid, pos, agent_id)


def session_title(transcript_path):
    """The session-title provider (plugins.session_title fan-out) — a codex
    session's threads.title (state index), else its first user prompt. See
    title.session_title."""
    from plugins.codex import title
    return title.session_title(transcript_path)


def title_and_rename(transcript_path):
    """The title+tail-rename provider (plugins.title_and_rename fan-out) — codex
    keeps the name in its state index, not the rollout, so tail_rename is always
    "". See title.title_and_rename."""
    from plugins.codex import title
    return title.title_and_rename(transcript_path)


def renameable(transcript_path):
    """The rename-ownership provider (plugins.renameable fan-out) — True for a
    codex rollout this plugin owns (keeps a Claude /rename off a codex host's
    window and vice-versa). See title.renameable."""
    from plugins.codex import title
    return title.renameable(transcript_path)


def set_session_title(transcript_path, name):
    """The session-rename provider (plugins.set_session_title fan-out) — write
    threads.title for a codex session (the PARKED web-rename path; live rename is
    P5's HostControl.rename); None for a non-codex path. See title.set_session_title."""
    from plugins.codex import title
    return title.set_session_title(transcript_path, name)


# DELIBERATELY NO effort_default provider. That fan-out is cwd-keyed (not
# ownership-gated by owns(), because a cwd names no file to claim) and picks the
# first TRUTHY answer — so a codex provider reading the GLOBAL ~/.codex/config.toml
# `model_reasoning_effort` would answer for a CLAUDE session too, the moment Claude
# has no saved effort of its own, shadowing the model's default (a Claude opus
# agent card read "low" off this machine's codex config). Codex's own effort is not
# a cwd fact anyway: it lives per-turn in the rollout (turn_context.effort, surfaced
# by context()), and the ✧ effort button is capability-gated OFF for codex (no live
# /effort). So codex declines this fan-out entirely — read.codex_effort stays as the
# rollout-side reader context() uses, not a cwd-keyed provider.


def slash_commands(cwd):
    """The "/" menu vocabulary provider (plugins.slash_commands fan-out, now
    HOST-SCOPED — a codex session's composer completes against codex's own
    commands, not Claude's). [{name, desc, src}, …]. See commands.slash_commands."""
    from plugins.codex import commands
    return commands.slash_commands(cwd)


def effort(transcript_path):
    """The current-effort provider (plugins.effort fan-out) — a codex rollout's
    LAST turn_context reasoning level (low/medium/high/…), or "". The dashboard's
    ✧ button reads this so a codex session shows its REAL level, never Claude's
    cwd-keyed effort_default (which leaked e.g. `high` onto a `low` run). Works
    without a usage record, unlike context(). See read.codex_effort."""
    from plugins.codex import read
    return read.codex_effort(transcript_path)


def pending_dialog(sid):
    """The pending-dialog provider (plugins.pending_dialog fan-out) — a codex
    run's OPEN request_user_input question for the web ask card (P5 drives it),
    or None. See read.pending_dialog."""
    from plugins.codex import read
    return read.pending_dialog(sid)


def usage_strip(cache=None, limit=50):
    """The usage-strip provider (plugins.usage_strip fan-out) — ONE host-wide
    row (codex has no account switcher, so there is nothing to have one row per),
    built from `codex app-server` account/rateLimits/read. [] when codex is
    unconfigured / unreachable. See usage.usage_strip."""
    from plugins.codex import usage
    return usage.usage_strip(cache=cache, limit=limit)


def session_usage(sid):
    """The per-session usage provider (plugins.session_usage fan-out) — this
    run's last rate-limit reading, probed from its own ROLLOUT so a PARKED
    session still shows where its limits stood (the app server only answers for
    now). See usage.session_usage / read.usage."""
    from plugins.codex import usage
    return usage.session_usage(sid)


def session_account(sid):
    """The per-session account provider (plugins.session_account fan-out) — the
    minimal shape for a host with no switcher: no slug, just the rollout's plan
    ("codex · plus"), or {} when it names none. See usage.session_account."""
    from plugins.codex import usage
    return usage.session_account(sid)


def session_costs(sid):
    """The per-session cost provider (plugins.session_costs fan-out) — codex's
    own scoreboard counters, already priced by CODEX_PRICES when the stream
    folded them. codex never reaches the `otel` table the Claude side sums, so
    without this a codex session reports 0 for work it really did. See
    usage.session_costs."""
    from plugins.codex import usage
    return usage.session_costs(sid)


def corpus_costs():
    """The BULK cost provider (plugins.corpus_costs fan-out) — every codex
    session's token/cost totals in one pass, so the cross-session views stop
    reading codex as free (they sum `otel`, which codex never reaches). See
    usage.corpus_costs."""
    from plugins.codex import usage
    return usage.corpus_costs()


def compacting(sid, sdb=None):
    """The compaction-latch provider (plugins.compacting fan-out) — the RAW
    `{ts, trigger}` record codex's own Pre/PostCompact hooks arm and clear
    (dispatch.py → facets.on_compact), so a codex session's ctx bar breathes
    while it compacts exactly as a Claude one does. See facets.compacting."""
    from plugins.codex import facets
    return facets.compacting(sid, sdb)


def fg_running(sid, sdb=None):
    """The in-flight-foreground-command provider (plugins.fg_running fan-out) —
    the mirror block a standalone codex host is running a shell command in, and
    since when, written by the ROLLOUT STREAM rather than a hook (only the stream
    holds the block's copy group; the hook's ids name nothing paintable — see
    facets.py's measurement). See facets.fg_running."""
    from plugins.codex import facets
    return facets.fg_running(sid, sdb)


# NB `tasks` is DELIBERATELY ABSENT — codex has no task-list tool. Not in its
# rollout vocabulary (exec_command / write_stdin / wait / spawn_agent /
# wait_agent / send_message / apply_patch / exec, over an 80-rollout corpus), not
# in its hook `tool_name`s. There is no material, so the honest answer is the
# DECLINE: plugins.tasks() reads None and the pinned card stays hidden, rather
# than the dashboard being handed an empty list that renders an empty card.
