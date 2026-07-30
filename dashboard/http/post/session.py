# dashboard/http/post/session.py — the SESSION-LIFECYCLE POSTs: launch a new
# tab (also the parked "resume & send" path), migrate a session to another
# subscription account, and rename one.
import threading
import os
import time

import plugins
from core import paths as P
from core import sessionapi as API
from core import tabs
from core.noaudit import load_audit
from dashboard import (prefs)
from dashboard import config
from dashboard.config import (EFFORTS,
                              MODEL_OK, NAME_CTRL, QUEUE_TABS, SID_OK)
from dashboard.control import launch
from dashboard.control.launch import launch_argv

A = load_audit()

# The host a new-session launch uses when the body names none, and the owner
# assumed for a resume whose transcript has no audit row (left to the CLI,
# unprovable), is `plugins.default_host()` — the registry's ONE owner of that
# name, read at the two call sites below rather than re-spelled here (this module
# held the third of four independent copies of the literal). A FRESH launch's
# `tool` picks the host directly; a RESUME resolves the OWNING host
# (plugins.owns_by) so a parked codex session comes back with `codex resume
# <sid>` and a claude one stays `claude --resume <sid>` — the per-tool launch
# provider that used to be a flat refusal.


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
        cwd isn't an existing directory or model/effort/resume/continue/tool
        don't validate (model: one clean argv word; effort: the CLI's EFFORTS
        levels; resume: a clean session id, exclusive with continue; tool: a
        LAUNCHABLE host, plugins.hosts); 409 when a resume's OWNING host can't be
        launched; 503 when no terminal resolves. The launching HOST composes the
        argv through its HostControl (launch_words + launch_cmd): a fresh launch
        uses the picked `tool` (claude_code → `claude --resume/--continue
        --model/--effort … prompt`; codex → `codex [resume <sid>] -C -m -c
        model_reasoning_effort= … prompt`), a resume the host that OWNS the parked
        conversation (plugins.owns_by), so a codex session resumes with `codex
        resume`. The response carries the new tab's window id when the terminal
        reports one, and a launch_wake watcher thread hurries the session's
        SSE appearance (see its block). Audited as a `web-launch` state_files
        row (no session db exists yet, so its log/path are empty; `tool` = the
        launching host; `win` = the launched window; `ms` = the per-step latency
        breakdown, see _Steps)."""
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
        # tool: which HOST to launch (the new-session form's tool picker).
        # Validated against the LAUNCHABLE hosts registry (plugins.hosts), so an
        # unknown/non-launchable tool 400s rather than composing an argv for a
        # host that isn't there. A resume overrides this with the owning host
        # below; a fresh launch uses it directly.
        tool = body.get("tool")
        tool = tool if isinstance(tool, str) and tool else plugins.default_host()
        launchable = {h["name"] for h in plugins.hosts() if h.get("launchable")}
        if tool not in launchable:
            return self._reject_input("web-launch", "bad tool", "unknown tool",
                                {"tool": tool})
        # account: the switcher slug to launch under (default `claude` when
        # absent). Resolved to a registry-vetted command word — never the raw
        # value flows into the launch shell string.
        acct = body.get("account")
        alias = plugins.account_alias(acct) if acct else "claude"
        if alias is None:
            return self._reject_input("web-launch", "bad account", "unknown account",
                                {"account": acct})
        prompt = body.get("prompt")
        prompt = prompt if isinstance(prompt, str) else ""
        # attachments ride the launch prompt as leading @-mentions, same as the
        # live composer (covers the new-session form AND the parked "resume &
        # send" path, which both route through here). With no typed prompt, the
        # mentions alone are a valid initial prompt.
        attachments = self._attachment_paths(body)
        # Resolve the launching HOST, then compose its argv through the one
        # HostControl seam (launch_words + launch_cmd) both hosts share. A FRESH
        # launch uses the picked `tool`; a RESUME uses the host that OWNS the
        # parked conversation (plugins.owns_by) — a codex rollout comes back with
        # `codex resume <sid>`, a claude transcript stays `claude --resume <sid>`
        # (byte-identical to before). An owner we can't launch (an unclaimed
        # transcript, or a tool with no host) is refused rather than run under the
        # wrong tool — the codex-aware successor to the old flat RESUME_TOOL 409.
        r_tpath, host_name = "", tool
        if resume:
            r_tpath = (API.session_row(resume) or {}).get("transcript_path") or ""
            host_name = ((plugins.owns_by(r_tpath) or "") if r_tpath
                         else plugins.default_host())
        # A resume target whose KNOWN transcript is GONE can't be resumed at all,
        # by any host: `claude --resume` / `codex resume` both open a tab that dies
        # at once (observed live, 2026-07-21). This check precedes the owner guard
        # DELIBERATELY: ownership is decided by parsing the file, so a missing file
        # is unowned by construction (owns() needs a readable file), and reporting
        # it as "unsupported tool" would bury the real cause — the conversation is
        # gone. An UNKNOWN sid (no row → r_tpath "") is left to the CLI. The live
        # window / owner guards below still apply to a present transcript.
        if r_tpath and not os.path.isfile(r_tpath):
            A.state_file("", "", "web-launch",
                         {"cwd": cwd, "resume": resume or "", "tool": host_name,
                          "ok": False, "why": "transcript missing"})
            return self._json(
                {"error": "session transcript is gone — can't resume",
                 "sid": resume}, 410)
        host = plugins.host_named(host_name)
        if host is None or not host.launchable:
            A.error("", "dashboard new-session (unsupported tool)",
                    {"sid": resume or "", "tool": host_name, "path": r_tpath})
            A.state_file("", "", "web-launch",
                         {"cwd": cwd, "resume": resume or "", "tool": host_name,
                          "ok": False, "why": "unsupported tool"})
            return self._json(
                {"error": ("resume not yet supported for this session's tool"
                           if resume else "unknown tool"),
                 "sid": resume or "", "tool": host_name}, 409)
        # attachments ride the launch prompt as leading mentions, same as the
        # live composer (covers the new-session form AND the parked "resume &
        # send" path, which both route through here) — in the LAUNCHING host's
        # own grammar, resolved just above. With no typed prompt, the mentions
        # alone are a valid initial prompt.
        prompt = self._with_attachments(prompt, attachments, host)
        # A resume's model/effort ride ONLY when the picked tool matches the owner
        # — a codex resume must never receive a claude `--model` (nor a claude
        # resume a codex `-c`); a mismatch keeps the session's own (the common
        # case — the "resume & send" composer sends neither anyway). A fresh
        # launch always carries them.
        match = (not resume) or (tool == host_name)
        words = host.launch_words(
            {"cwd": cwd, "resume": resume or "", "cont": bool(cont),
             "model": (model if match else "") or "",
             "effort": (effort if match else "") or "", "prompt": prompt})
        argv = launch_argv(words, host.launch_cmd(alias))
        opts = {"cwd": cwd, "model": model or "", "effort": effort or "",
                "resume": resume or "", "cont": bool(cont),
                "account": acct or "", "attachments": len(attachments),
                "tool": host.name}
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
            # The transcript-missing 410 is checked EARLY (before the owner guard,
            # above) — a gone conversation is host-common and its own cause. Here
            # only the live-window backstop remains: never resume-launch a session
            # that ALREADY has a live tab (a second launch would run a duplicate
            # process against the SAME transcript — two tabs, interleaved writes).
            # The page issues a resume only when it believes the session is PARKED,
            # but a stale page (dashboard restart + dropped SSE) can misjudge a
            # live one; window_for_session is a fresh authoritative kitten scan.
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
        # a launch carrying a first prompt makes a clipboard-grabbing TUI read
        # the board at startup and attach any image to that auto-submitted
        # message (docs/dashboard.md *Clipboard-image guard*) — empty an image
        # clipboard first so the startup grab finds nothing. Only when there's a
        # prompt (a bare launch auto-submits nothing, so nothing to attach to),
        # and only for a host that DECLARES the grab: the osascript round-trip is
        # ~150ms and codex's TUI does no such thing.
        clip = (launch.clear_clipboard_image()
                if prompt.strip() and host.paste_grabs_clipboard_image
                else False)
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
        # refuse when the owning host can't migrate (no-op for claude_code —
        # its `migrate` cap is True; byte-identical)
        if self._caps_guard(sid, "migrate", "web-migrate"):
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
        # the account vocabulary, the downgrade ladder and the migrator argv are
        # all the owning host's (plugins/claude_code — accounts are a claude_code
        # concept end to end); this endpoint owns the unknown-sid 404, the
        # no-terminal 503 and the mapping of its verdict.
        res = self._gesture_host(sid).migrate(sid, {
            "sid": sid, "log": log, "sdb": sdb, "row": row,
            "action": "web-migrate", "verb": "migrate"})
        if self._gesture_declined(res, sid, "web-migrate", "migrate"):
            return
        target = res.get("target")
        if target is None:
            return self._json({"error": "no other account available"}, 409)
        if not res.get("ok"):            # spawn failure already audited by SP
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
        # TWO different questions, both asked before the live/parked branch.
        # (1) CAN this session's host rename at all (the cap the client greys the
        # ✎ button on)? Without this a host with `renameable` True and the
        # `rename` cap False fell through to the base gesture and surfaced as a
        # 502 "send failed" — a capability answered as a malfunction (the P2 bug
        # list, item 4).
        if self._caps_guard(sid, "rename", "web-rename"):
            return
        # (2) Does a plugin OWN the file well enough to write into it? That is
        # the PARKED half's gate — the transcript append and the durable
        # override — and it is a different fact from the cap: a host may drive a
        # live `/rename` and own no record format at all. Its window carries the
        # same claude_session tag either way, so this too comes first.
        if not plugins.renameable(tpath):
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
        """The LIVE half of post_rename: the host's own `/rename <name>`, pasted
        through its one slash-command channel (mode-proof against `editorMode:
        vim`, clipboard-image guarded where the host declares it). Claude Code
        then updates its in-memory title, writes the `agent-name` record itself
        and re-emits the OSC the kitty tab follows, so all four readers agree
        from ONE write.

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
        queued = tab in QUEUE_TABS
        res = self._gesture_host(sid).rename(sid, name, {
            "sid": sid, "log": log, "sdb": sdb, "tab": tab, "fe": fe,
            "win": win, "action": "web-rename", "verb": "rename",
            "queueing": queued})
        if self._gesture_declined(res, sid, "web-rename", "rename",
                                  extra={"win": win, "chars": len(name)}):
            return
        if not res.get("ok"):
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
        # the override's KEY is the owning host's own filename convention
        # (HostControl.title_key), the same derivation read/meta applies on the
        # way back out — the `.jsonl` stem used to be spelled here, in a tier
        # that is not supposed to know one host's transcript naming from
        # another's. A host that declares none falls back to the basename, which
        # is what the old code did for a non-`.jsonl` path anyway.
        stem = (self._gesture_host(sid).title_key(tpath)
                or os.path.basename(tpath))
        stored = prefs.set_renamed_title(stem, name)
        override_ok = isinstance(stored, dict) and stored.get(stem) == name
        A.state_file(log, sdb, "web-rename",
                     {"win": "", "chars": len(name), "ok": bool(appended),
                      "channel": "transcript", "override": override_ok})
        return self._json({"ok": True, "title": name,
                           "channel": "transcript"})
