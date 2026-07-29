# dashboard/http/post/session.py — the SESSION-LIFECYCLE POSTs: launch a new
# tab (also the parked "resume & send" path), migrate a session to another
# subscription account, and rename one.
import threading
import os
import time

import plugins
from core import paths as P
from core import sessionapi as API
from core import spawn as SP
from core import tabs
from core.noaudit import load_audit
from dashboard import (prefs)
from dashboard import config
from dashboard.config import (EFFORTS,
                              MODEL_OK, NAME_CTRL, QUEUE_TABS, SID_OK)
from dashboard.control import launch
from dashboard.control.launch import launch_argv

A = load_audit()


class _Steps:
    """A launch's per-step stopwatch — `mark(name)` closes the step that just
    ran and `ms` is the accumulated `{step: milliseconds, …}` map (plus `all`,
    the elapsed total) an audit row carries. Local to the launch handler on
    purpose: this is diagnostic instrumentation for ONE slow endpoint, not a
    shared timing vocabulary — the other control POSTs are single-call and get
    their latency from the client's `<gesture>.ok` clientlog row."""

    def __init__(self):
        self._t0 = self._last = time.time()
        self.ms = {}

    def mark(self, name):
        now = time.time()
        self.ms[name] = round((now - self._last) * 1000)
        self.ms["all"] = round((now - self._t0) * 1000)
        self._last = now


class _SessionMixin:
    """Launching, migrating and renaming whole sessions."""

    def post_new_session(self):
        """Launch a new session in a new tab (Frontend.launch_tab). 400 when the
        cwd isn't an existing directory or model/effort/resume/continue don't
        validate (model: one clean argv word; effort: the CLI's EFFORTS levels;
        resume: a clean session id, exclusive with continue); 503 when no
        terminal resolves; else the launch, with `--resume <sid>`/`--continue`
        and `--model`/`--effort` riding as positional "$@" words ahead of the
        prompt. The response carries the new tab's window id when the terminal
        reports one, and a launch_wake watcher thread hurries the session's
        SSE appearance (see its block). Audited as a `web-launch` state_files
        row (no session db exists yet, so its log/path are empty; `win` = the
        launched window; `ms` = the per-step latency breakdown, see _Steps)."""
        body = self._post_guard()
        if body is None:
            return
        cwd = body.get("cwd")
        if not isinstance(cwd, str) or not cwd or not os.path.isdir(cwd):
            return self._reject_input("web-launch", "bad cwd",
                                "cwd is not an existing directory",
                                {"cwd": cwd})
        model, effort = body.get("model"), body.get("effort")
        if model is not None and (
                not isinstance(model, str) or not MODEL_OK.match(model)):
            return self._reject_input("web-launch", "bad model", "invalid model",
                                {"model": model})
        if effort is not None and effort not in EFFORTS:
            return self._reject_input("web-launch", "bad effort", "invalid effort",
                                {"effort": effort})
        # resume / continue — the CLI's own conversation-pickup flags. resume
        # carries a session id (one clean argv word, same alphabet as our sid
        # routing); continue is a bare flag. Mutually exclusive, like the CLI.
        # A resumed conversation FORKS to a new sid; the existing adopt
        # machinery and the page's jump watch both handle that on their own.
        resume, cont = body.get("resume"), body.get("continue")
        if resume is not None and (
                not isinstance(resume, str) or not SID_OK.match(resume)):
            return self._reject_input("web-launch", "bad resume", "invalid resume id",
                                {"resume": resume})
        if cont not in (None, False, True):
            return self._reject_input("web-launch", "bad continue",
                                "invalid continue", {"continue": cont})
        if resume and cont:
            return self._reject_input("web-launch", "resume+continue",
                                "resume and continue are exclusive",
                                {"resume": resume})
        # account: the switcher slug to launch under (default `claude` when
        # absent). Resolved to a registry-vetted command word — never the raw
        # value flows into the launch shell string.
        acct = body.get("account")
        cmd = plugins.account_alias(acct) if acct else "claude"
        if cmd is None:
            return self._reject_input("web-launch", "bad account", "unknown account",
                                {"account": acct})
        prompt = body.get("prompt")
        prompt = prompt if isinstance(prompt, str) else ""
        # attachments ride the launch prompt as leading @-mentions, same as the
        # live composer (covers the new-session form AND the parked "resume &
        # send" path, which both route through here). With no typed prompt, the
        # mentions alone are a valid initial prompt.
        attachments = self._attachment_paths(body)
        prompt = self._with_attachments(prompt, attachments)
        words = ((["--resume", resume] if resume else [])
                 + (["--continue"] if cont else [])
                 + (["--model", model] if model else [])
                 + (["--effort", effort] if effort else [])
                 + ([prompt] if prompt.strip() else []))
        argv = launch_argv(words, cmd)
        opts = {"cwd": cwd, "model": model or "", "effort": effort or "",
                "resume": resume or "", "cont": bool(cont),
                "account": acct or "", "attachments": len(attachments)}
        # Per-STEP timings, folded into the row as `ms` (below). The handler runs
        # up to four subprocess round-trips before it answers — the osascript
        # clipboard probe (~150 ms measured), `lsappinfo` front-app, a resume's
        # `kitten @ ls`, and `kitten @ launch` — and the row used to record only
        # that it happened. That left "launch took 5 s" (a real `new.ok`
        # outlier) un-attributable from the DB: the client's clientlog bounds the
        # ROUND-TRIP, nothing said which step burned it. Each step is stamped as
        # it completes, so a partial row (a hung `kitten @ launch`, never
        # returning) still names the step it died in.
        step = _Steps()
        fe = launch.frontend()
        step.mark("fe")
        if fe is None:
            A.error("", "dashboard new-session (no terminal)", {"cwd": cwd})
            A.state_file("", "", "web-launch", dict(opts, ok=False, ms=step.ms))
            return self._json({"error": "no terminal available"}, 503)
        # Guard: never resume-launch a session that ALREADY has a live tab. A
        # second `claude --resume <sid>` would run a duplicate process against
        # the SAME transcript (two tabs, interleaved writes). The page issues a
        # resume-launch only when it believes the session is PARKED, but a
        # stale page (e.g. after the dashboard restarts and its SSE drops)
        # can misjudge a live session — this is the server-side backstop.
        # window_for_session is a fresh live kitten scan (authoritative over
        # any cached/page state); fresh and --continue launches are unaffected.
        # The page gets the live window back so it can focus/message it instead.
        if resume:
            # A resume target whose transcript .jsonl is GONE can't be resumed:
            # `claude --resume` finds no conversation and the freshly launched
            # tab exits at once — a silent dead tab the user reads as "resume
            # did nothing" (observed live on an aggregator session whose file
            # was never persisted, 2026-07-21). Reject up front when the sid's
            # KNOWN transcript path (its audit row) is absent on disk; an
            # unknown sid (no row / no path) is left to the CLI — we can't prove
            # it's broken. All accounts share ~/.claude/projects (the switcher
            # symlinks each configs/<slug>/projects to it), so the launch
            # account is irrelevant to this check.
            r_tpath = (API.session_row(resume) or {}).get("transcript_path") or ""
            if r_tpath and not os.path.isfile(r_tpath):
                step.mark("row")
                A.state_file("", "", "web-launch",
                             dict(opts, ok=False, why="transcript missing",
                                  ms=step.ms))
                return self._json(
                    {"error": "session transcript is gone — can't resume",
                     "sid": resume}, 410)
            step.mark("row")
            live_win = fe.window_for_session(resume) or ""
            step.mark("livewin")
            if live_win:
                A.state_file("", "", "web-launch",
                             dict(opts, ok=False, win=live_win, ms=step.ms))
                return self._json(
                    {"error": "session already live", "sid": resume,
                     "win": live_win}, 409)
        # the passive steal watch (see the block above the Handler class):
        # the frontmost app must be captured BEFORE the launch — a steal can
        # land before the kitten call returns. Skipped when the terminal was
        # ALREADY frontmost at click time (nothing to steal) or the frontend
        # has no OS app identity (the inert stub, off-mac).
        term = fe.app_id()
        before = launch.front_app() if term else ""
        step.mark("front")
        # a launch carrying a first prompt makes Claude Code's TUI read the
        # clipboard at startup and attach any image to that auto-submitted
        # message (docs/dashboard.md *Clipboard-image guard*) — empty an image
        # clipboard first so the startup grab finds nothing. Only when there's a
        # prompt (a bare launch auto-submits nothing, so nothing to attach to).
        clip = launch.clear_clipboard_image() if prompt.strip() else False
        step.mark("clip")
        # launch_tab: the new window's id on success when the terminal reports
        # one (kitty prints it), bare True when it doesn't, falsy on failure.
        got = fe.launch_tab(cwd, argv)
        win = got if isinstance(got, str) else ""
        step.mark("tab")
        A.state_file("", "", "web-launch",
                     dict(opts, ok=bool(got), win=win, clip=clip, ms=step.ms))
        if not got:
            A.error("", "dashboard new-session (launch failed)", {"cwd": cwd})
            return self._json({"error": "launch failed"}, 502)
        # the SSE wake watch (see the block above the Handler class): hurry
        # the launched session's appearance to every connected page — and hand
        # the launching page its sid — the moment SessionStart lands.
        threading.Thread(target=launch.launch_wake, args=(win, cwd, time.time()),
                         daemon=True, name="web-launch-wake").start()
        if before and before != term:
            threading.Thread(target=launch.steal_watch, args=(before, term),
                             daemon=True, name="web-launch-steal-watch").start()
        # `win` lets the page match the launched session exactly (its jump
        # watch compares kitty_window_id); "" when the terminal didn't report
        # an id — the page falls back to its cwd heuristic.
        return self._json({"ok": True, "win": win})

    def post_migrate(self, sid):
        """Manually migrate a session to another subscription account — the
        header's ⇆ migrate button (docs/relimit.md *Manual migrate*). Spawns
        the SAME detached migrator the automatic rate-limit path uses
        (bin/claude-relimit.py: close the tab → wait for the SessionEnd park
        → `<alias> claude --resume <sid>` in a new tab; the adopt machinery
        carries the mirror history and the status-line capture flips the
        account chip), with two manual-intent differences baked into `mode=
        manual`: no auto-continue nudge (nothing was cut off — the resumed
        session opens at the prompt) and no 90% usage ceiling on the target
        (plugins.migration_target(manual=True) — an explicit click outranks
        the refuge rule). It runs the SAME fable→opus→sonnet downgrade ladder
        the automatic path does (docs/relimit.md *Model-downgrade ladder*):
        same model on another account when one has quota, else a downgrade rung
        passed through to `--model` (the current model is read off the
        transcript via plugins.context). Immediate, no confirm (user request —
        like ■ stop). Works live AND parked: a parked session skips the close
        leg and just relaunches. 404 for a sid this machine has never seen (no
        audit row, no live/parked state DB — the migrator can't tell "parked"
        from "never existed", so an unknown sid would sail through its park
        check and launch a doomed --resume tab; caught live 2026-07-19); 409
        when no account (any rung) qualifies;
        503 when no terminal resolves. Every attempt is a `web-migrate`
        state_files row, failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        row, log, sdb = self._audit_target(sid)
        # The unknown-sid 404 deliberately runs BEFORE anything else (the
        # migrator can't tell "parked" from "never existed"), and files its row
        # with an empty PATH — a sid with no row and no DB has no state DB to
        # name, so the derived one would be a fiction.
        if not (row or os.path.isfile(P.state_db(log))
                or os.path.isfile(P.parked_db(log))):
            A.state_file(log, "", "web-migrate",
                         {"ok": False, "reason": "unknown sid"})
            return self._json({"error": "unknown session"}, 404)
        fe = launch.frontend()
        if fe is None:
            A.error(log, "dashboard migrate (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-migrate",
                         {"ok": False, "reason": "no terminal"})
            return self._json({"error": "no terminal available"}, 503)
        cur = (API.kv_at(sdb, "account") or {}).get("slug") or ""
        # The model the session is running (off its transcript) feeds the
        # downgrade ladder (docs/relimit.md *Model-downgrade ladder*): a manual
        # ⇆ now downgrades too when no account has the current model free.
        cur_model = (plugins.context(row.get("transcript_path") or "")
                     or {}).get("model") or ""
        # Capture the picker's FULL reasoning (per-account rung/eff5h/limit-hit/
        # reject) so a manual-migrate REFUSAL is reconstructible from the DB —
        # the same subtle gap the automatic path closed with `relimit-pick`
        # (docs/relimit.md *Audit trail*); a bare "no target" is undebuggable.
        pick = {}
        target = plugins.migration_target(cur, cur_model, manual=True,
                                          explain=pick)
        if target is None:
            A.state_file(log, sdb, "web-migrate",
                         {"ok": False, "reason": "no target", "from": cur,
                          "pick": pick})
            return self._json({"error": "no other account available"}, 409)
        # target["model"] is the downgrade rung (or "" for a same-model migrate);
        # pick_target already resolved same-vs-downgrade, so forward it verbatim.
        proc = SP.spawn_detached(
            os.path.join(P.BIN, "claude-relimit.py"),
            [log, sid, target["slug"], target["alias"],
             row.get("cwd") or "", "manual", target["model"]],
            log, purpose="relimit:%s (web)" % target["slug"])
        ok = proc is not None
        A.state_file(log, sdb, "web-migrate",
                     {"ok": ok, "from": cur, "to": target["slug"],
                      "model": target["model"], "eff": target["eff"],
                      "cwd": row.get("cwd") or "", "pick": pick})
        if not ok:                       # spawn failure already audited by SP
            return self._json({"error": "migrator spawn failed"}, 502)
        return self._json({"ok": True, "to": target["slug"]})

    def post_rename(self, sid):
        """Rename a session through the ONE channel that owns its name —
        Claude Code itself when the session is LIVE (`_rename_live`: paste
        `/rename <name>`), the transcript record + durable override when it is
        PARKED (`_rename_parked`). DELIBERATELY unlike post_message, no
        terminal / no live window is NOT an error: it selects the parked half,
        so a parked session (or a dashboard outside kitty) still renames.

        Why the live path may NOT write the record itself (the 2026-07-29
        bug, docs/session-naming-findings.md §4): Claude Code holds the session
        name IN MEMORY and re-emits it as an `agent-name` record at every turn
        boundary, so a record it did not write is overwritten within one turn —
        measured, a manual rename survived 4KB and was then re-clobbered 13
        times while `session_title` in the hook payloads and the OSC tab title
        both went on reporting the OLD name. Every reader (the picker, the tab,
        the dashboard, the payload) derives from that in-memory title, so the
        only durable way to rename a RUNNING session is to make Claude Code
        change its own mind. Every post-validation attempt is a `web-rename`
        state_files row whose `channel` names which half ran; failures also an
        A.error."""
        body = self._post_guard()
        if body is None:
            return
        name = body.get("name")
        if not isinstance(name, str):
            return self._reject_input("web-rename", "bad name", "empty name",
                                      {"type": type(name).__name__}, sid=sid)
        name = NAME_CTRL.sub(" ", name).strip()[:config.RENAME_MAX].strip()
        if not name:
            return self._reject_input("web-rename", "empty name", "empty name",
                                      {"raw": body.get("name")}, sid=sid)
        row, log, sdb = self._audit_target(sid)
        tpath = row.get("transcript_path") or ""
        if not tpath or not os.path.isfile(tpath):
            A.state_file(log, sdb, "web-rename",
                         {"win": "", "chars": len(name), "ok": False,
                          "reason": "no transcript"})
            return self._json({"error": "no transcript"}, 409)
        if not plugins.renameable(tpath):
            # no plugin owns the file (a codex standalone host's rollout): it
            # must receive neither a Claude `agent-name` record NOR a typed
            # `/rename` — and its window carries the same claude_session tag,
            # so this gate has to come BEFORE the live/parked branch
            A.state_file(log, sdb, "web-rename",
                         {"win": "", "chars": len(name), "ok": False,
                          "reason": "unsupported"})
            return self._json({"error": "unsupported session"}, 409)
        fe = launch.frontend()
        win = (fe.window_for_session(sid) or "") if fe else ""
        if win:
            tab = API.tab_states().get(win) or ""
            return self._rename_live(sid, name, log, sdb, fe, win, tab)
        return self._rename_parked(sid, name, log, sdb, tpath)

    def _rename_live(self, sid, name, log, sdb, fe, win, tab):
        """The LIVE half of post_rename: Claude Code's own `/rename <name>`,
        pasted through the one slash-command channel (launch.type_command —
        mode-proof against `editorMode: vim`, clipboard-image guarded). Claude
        Code then updates its in-memory title, writes the `agent-name` record
        itself and re-emits the OSC the kitty tab follows, so all four readers
        agree from ONE write.

        Mid-turn it lands in the TUI's message queue and applies at the turn
        boundary (`queued`, exactly like the ✦ auto button and the other quick
        commands); a RED tab is a 409 for the reason post_command refuses one —
        pasted text would land IN the open dialog, its digits deciding it.

        No `Frontend.set_tab_title` here on purpose: a sticky tab title would
        be a SECOND writer of the name, free to disagree with the one the
        session actually has (a queued rename the user then Escapes out of
        would leave the tab asserting a name nothing else knows) — which is the
        exact split the bug this replaced presented as."""
        if tab == tabs.AWAITING_COMMAND:
            A.state_file(log, sdb, "web-rename",
                         {"win": win, "chars": len(name), "ok": False,
                          "tab": tab, "channel": "tui",
                          "reason": "dialog open"})
            return self._json({"error": "a dialog is open — answer it first"},
                              409)
        ok, clip = launch.type_command(fe, win, "/rename " + name)
        queued = tab in QUEUE_TABS
        A.state_file(log, sdb, "web-rename",
                     {"win": win, "chars": len(name), "ok": ok, "tab": tab,
                      "channel": "tui", "queued": queued, "clip": clip})
        if not ok:
            A.error(log, "dashboard rename (send failed)",
                    {"sid": sid, "win": win})
            return self._json({"error": "send failed"}, 502)
        return self._json({"ok": True, "title": name, "channel": "tui",
                           "queued": queued})

    def _rename_parked(self, sid, name, log, sdb, tpath):
        """The PARKED half of post_rename: append the `agent-name` naming
        record ourselves (plugins.set_session_title) plus a durable prefs
        override. Safe precisely because nothing is running — no in-memory
        title to disagree with it, and no turn boundary to overwrite it — and
        `claude --resume` reads the file fresh, so the name is there when the
        session comes back.

        The DURABLE OVERRIDE is still needed on this path: that single record
        scrolls out of session_title's 64KB tail-window in a long session while
        Claude Code's re-emitted `ai-title` rows sit near EOF, and the dashboard
        title would roll back to the auto one (docs/dashboard.md *Web rename*).
        Best-effort like every prefs write — a failure just falls back to the
        transcript read."""
        try:
            appended = plugins.set_session_title(tpath, name)
        except OSError:
            A.error(log, "dashboard rename (append failed)", {"sid": sid})
            A.state_file(log, sdb, "web-rename",
                         {"win": "", "chars": len(name), "ok": False,
                          "channel": "transcript"})
            return self._json({"error": "append failed"}, 502)
        stem = os.path.basename(tpath)
        stem = stem[:-len(".jsonl")] if stem.endswith(".jsonl") else stem
        stored = prefs.set_renamed_title(stem, name)
        override_ok = isinstance(stored, dict) and stored.get(stem) == name
        A.state_file(log, sdb, "web-rename",
                     {"win": "", "chars": len(name), "ok": bool(appended),
                      "channel": "transcript", "override": override_ok})
        return self._json({"ok": True, "title": name,
                           "channel": "transcript"})
