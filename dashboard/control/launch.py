# dashboard/control/launch.py — the terminal-facing control machinery.
#
# The dashboard is READ-ONLY except a control plane that TYPES INTO a terminal
# and LAUNCHES sessions. This module owns the frontend/OS side of that: the
# active Frontend resolver, the live-window map (which sids have an open kitty
# tab), the new-session argv, and the post-launch macOS focus/appearance
# watches. Imported by the read model (which needs the live-window map) and the
# HTTP handlers (which drive the writes). The one reverse edge — _launch_wake
# nudging the notifier — is a lazy import, so this module stays a leaf.
import re
import subprocess
import sys
import time

import frontends
import plugins
from core import sessionapi as API
from core.noaudit import load_audit

A = load_audit()


def _frontend():
    """The active Frontend for a CONTROL-PLANE write, or None when no terminal
    control channel resolves. The dashboard may be started OUTSIDE kitty (its
    lifecycle is deliberately independent — docs/dashboard.md), so resolve=True
    lets kitty hunt for its socket beyond the env, and a frontend that isn't
    usable() degrades to None → the endpoint returns a clean 'no terminal'
    error, never a 500."""
    try:
        fe = frontends.get(resolve=True)
        return fe if fe.usable() else None
    except Exception:
        return None


_LIVE_WINS = {"ts": -1e9, "val": None}   # memo: {sid: win_id} tagged in a live pane
_LIVE_TTL = 5.0   # every consumer of the map is READ-side (the live→parked
#                   demotion + the stop-button display gate) — the control
#                   plane never trusts it (each POST re-scans via
#                   fe.window_for_session at action time) — so staleness only
#                   delays noticing a crashed/killed tab by a few seconds.
#                   That buys dropping the ~21ms `kitten @ ls` SUBPROCESS from
#                   ~1.25/s (the old 0.8, chosen to bound it under the 1s
#                   tick) to 0.2/s while any client keeps the payloads warm.


_LIVE_GRACE_S = 10.0   # a just-started session is EXEMPT from the missing-window
#                        demotion for this long. Its audit `sessions` row (with
#                        kitty_window_id) is written a beat BEFORE its pane is
#                        tagged claude_session=<sid> (split.cmd_open runs
#                        A.session_start then tag_window), and _live_windows is
#                        memoized up to _LIVE_TTL on top — so a fresh launch would
#                        momentarily miss the tagged-window map and get demoted to
#                        not-live, flashing "parked" on a brand-new session (and
#                        the detail header, whose meta is fetched once, froze on
#                        it — app.js updateHeadFromList now self-heals, but the
#                        flash itself is the "starts parked" half of the report).
#                        Comfortably covers boot + the memo TTL; the only cost is
#                        a session that dies within its first 10s showing live
#                        briefly, which the next tick past the grace corrects.


def _within_live_grace(row):
    """True while `row`'s session is inside the just-started grace (see
    _LIVE_GRACE_S) — used to SUPPRESS the missing-window demotion. A parked
    minimal row carries no started_at (None → False), but those never reach the
    demotion anyway (their base `live` is already False)."""
    st = row.get("started_at") or 0
    return bool(st) and (time.time() - st) < _LIVE_GRACE_S


def _live_windows():
    """{sid: window_id} for every kitty pane CURRENTLY tagged
    claude_session=<sid> — the authoritative 'which sessions have an OPEN tab'.
    One `kitten @ ls`, memoized for _LIVE_TTL. None when no frontend resolves
    OR the `ls` came back EMPTY (can't tell → callers keep the state-DB liveness
    signal rather than wrongly marking sessions dead).

    Why this exists: the audit row's kitty_window_id is a START-TIME snapshot,
    and 'the state DB file exists' only means the session was never PARKED — a
    tab closed WITHOUT a SessionEnd (crash / kill -9, or a leaked test DB)
    leaves both intact, so the session shows live with a window id that kitty
    has since reused for an unrelated tab. Keying on the live user-var tag is
    the only collision-proof truth.

    Why an EMPTY ls is treated as can't-tell, not authoritative: kitten_ls
    swallows EVERY failure (a timeout, rc≠0, a transient socket hiccup) into an
    empty list — indistinguishable from a genuinely empty desktop, and it never
    raises, so the `except` below can't catch it. But a running dashboard
    implies kitty HAS windows, so an empty tree is virtually always a failed
    `ls`, not an empty one. Trusting `{}` demoted every running session to
    not-live on one hiccup, flashing the cards to 'gone' (a session that is
    live-but-not-parked renders 'gone' — app.js). So an empty tree → None."""
    now = time.monotonic()
    if now - _LIVE_WINS["ts"] < _LIVE_TTL:
        return _LIVE_WINS["val"]
    fe = _frontend()
    val = None
    if fe is not None:
        try:
            tree = fe.ls()
            if tree:                       # empty/failed ls → None (can't tell)
                val = {}
                for _osw, _tab, w in fe.iter_windows(tree):
                    sid = (w.get("user_vars") or {}).get("claude_session")
                    if sid and w.get("id") is not None:
                        val.setdefault(sid, str(w["id"]))
        except Exception:
            val = None
    _LIVE_WINS["ts"], _LIVE_WINS["val"] = now, val
    return val


def demote_if_dead(row, live_wins, sid=None, target=None):
    """The SINGLE owner of the 'a state-DB-live session whose kitty window is
    gone is not really live' correction the list / session-detail / resume read
    payloads all apply. Clear the `live` flag when ALL four hold: the session
    claims live, we can actually enumerate windows (`live_wins is not None` —
    an empty/failed `ls` is can't-tell, keep the state-DB signal), it EVER had
    a window (a headless/daemon session legitimately has none), that window is
    no longer tagged `claude_session=<sid>` (`sid not in live_wins`), and it is
    PAST the just-started grace (`_within_live_grace` — a fresh launch's pane
    isn't tagged yet). Otherwise a tab closed without a SessionEnd (crash /
    kill -9, or a leaked DB) leaves the state DB intact and the session
    masquerades as running with a since-reused window id (docs/dashboard.md
    *Liveness = an OPEN tab*).

    `row` is the window/grace source (`kitty_window_id` + `started_at`); `sid`
    defaults to `row["sid"]`. `target` is the dict whose `live` is read and
    cleared — it defaults to `row` (the list/resume rows carry both), but the
    session-detail payload passes a SEPARATE target because its liveness comes
    from `API.session` while the window/started_at come from the audit
    `session_row` (which carries no `live` key)."""
    if sid is None:
        sid = row.get("sid")
    if target is None:
        target = row
    if (target.get("live") and live_wins is not None
            and row.get("kitty_window_id") and sid not in live_wins
            and not _within_live_grace(row)):
        target["live"] = False


def launch_argv(words, cmd="claude"):
    """The argv a web new-session launches — the interactive-login-shell
    wrapper now owned by plugins.claude_code.account.launch_argv (the
    rate-limit migration composes the SAME launch; the rationale — GUI kitty
    has no user PATH/aliases, `cmd` must be a registry-vetted bareword, the
    prompt/flags ride "$@" — lives with the owner). Reached through the
    plugins registry root, the dashboard's one sanctioned plugin door."""
    return plugins.launch_argv(words, cmd)


# --- macOS focus steal watch (audit-only) -----------------------------------------------
# A web launch used to make macOS activate kitty over the browser: the plain
# tab launch is innocent, but the new session's SessionStart opened its
# mirror/scorebar panes with kitty's `--keep-focus`, whose focus-restore
# raises the OS window whenever the app is in the background — i.e. exactly
# when launching from a browser (live-measured steals at 2.2s/3.0s/5.8s, one
# per pane op). That is fixed at the SOURCE: frontends/kitty.py launch_pane
# passes --keep-focus only while kitty is the frontmost app. This watch is
# the PASSIVE regression evidence for that fix: it records when the terminal
# app takes the frontmost spot during a launch's startup window and NEVER
# touches focus itself. (An active bounce-back shipped on 2026-07-18 and was
# reverted the same day: it cannot distinguish kitty stealing focus from the
# user deliberately switching to kitty, so it yanked the user back to the
# browser when they genuinely wanted the terminal. Do not re-add it.)
# `lsappinfo` is a plain LaunchServices query — no Apple-events /
# accessibility permission prompts, unlike System Events AppleScript.
STEALWATCH_POLL_S = 0.5            # frontmost-app poll cadence after a launch
STEALWATCH_POLLS = 60              # ~30s — outlives the whole session startup
                                   # (claude boot + SessionStart pane opens,
                                   # stragglers measured past 12s)


def _front_app():
    """The frontmost macOS app's bundle id, or "" (non-mac / any failure)."""
    if sys.platform != "darwin":
        return ""
    try:
        asn = subprocess.run(["lsappinfo", "front"], capture_output=True,
                             text=True, timeout=2).stdout.strip()
        if not asn:
            return ""
        out = subprocess.run(["lsappinfo", "info", "-only", "bundleid", asn],
                             capture_output=True, text=True, timeout=2).stdout
        m = re.search(r'"CFBundleIdentifier"\s*=\s*"([^"]+)"', out)
        return m.group(1) if m else ""
    except Exception:
        return ""


# macOS clipboard "flavor" codes that mean an IMAGE is on the board. Claude
# Code's TUI auto-attaches whatever image the clipboard holds to a message on
# ANY bracketed paste (and on an argv-prompt startup) — proven live: a web send
# with a screenshot on the clipboard arrived as "text[Image #1]" with the PNG,
# though baqylau attached nothing. There is no CC opt-out, so before any web
# send/launch we EMPTY an image clipboard so the grab finds nothing (the user
# chose auto-clear; a text-only clipboard is left alone). docs/dashboard.md
# *Clipboard-image guard*.
_CLIP_IMAGE_FLAVORS = ("PNGf", "TIFF", "8BPS", "jp2", "GIF", "JPEG", "picture")


def _clip_has_image():
    """True when the macOS clipboard currently holds an image flavor. Best-effort
    (`osascript -e 'clipboard info'`); False off macOS / on any failure / on a
    text-only clipboard — so we never clear a clipboard that has no image."""
    if sys.platform != "darwin":
        return False
    try:
        info = subprocess.run(["osascript", "-e", "clipboard info"],
                              capture_output=True, text=True, timeout=2).stdout or ""
    except Exception:
        return False
    return any(f in info for f in _CLIP_IMAGE_FLAVORS)


def _clear_clipboard_image():
    """If the macOS clipboard holds an IMAGE, empty it — so Claude Code can't
    auto-attach it to a web-delivered message (docs/dashboard.md *Clipboard-image
    guard*). Returns True iff it cleared. No-op (False) off macOS or on a
    text-only clipboard, so a plain text clipboard is preserved; best-effort,
    never raises into the caller."""
    if not _clip_has_image():
        return False
    try:
        subprocess.run(["osascript", "-e", 'set the clipboard to ""'],
                       capture_output=True, timeout=2)
        return True
    except Exception:
        return False


TUI_DRAFT_KEY = "tui-draft"      # state-DB kv: text WE left in the input box


def tui_draft(sid):
    """The text the web KNOWS is sitting in this session's `❯` input box
    because the web put it there — an interrupt that took a message back, or a
    rewind that restored one. "" when the box is (as far as we know) ours to
    paste into cleanly.

    This exists because the next send must REPLACE that text rather than paste
    after it. The page used to remember this in a per-view variable
    (`clearDraftNext`), which a RELOAD wiped while the TUI's draft survived —
    so the next send glued the two together and delivered `testingtesting2`
    (reported 2026-07-25). Server state outlives the page, and covers a send
    from a different device too. Read-only/best-effort: unreadable is ""."""
    try:
        sdb = API.state_db_for(sid)
        got = API.kv_at(sdb, TUI_DRAFT_KEY) if sdb else None
    except Exception:
        return ""
    return got if isinstance(got, str) else ""


def set_tui_draft(sid, text):
    """Record (or, with "", forget) the text we left in the input box. Called
    where the web CAUSES it — post_interrupt's take-back, post_rewind_to's
    restore — and cleared by the send that consumes it. A stale flag is benign:
    the clear it triggers is a Ctrl+U/Ctrl+K on an already-empty line.

    `kv_set_at`, never `kv_set`: this runs on a ThreadingHTTPServer request
    thread, and kv_set's cached connection is bound to the thread that opened
    it — from any other thread it raises inside its own swallow and writes
    NOTHING (see transcript.mark_taken_back, same 2026-07-25 bug). Returns the
    write's result so the caller can audit a failure instead of assuming."""
    from core import state as ST
    if not sid:
        return False
    try:
        sdb = API.state_db_for(sid)
        return bool(sdb) and bool(ST.kv_set_at(sdb, TUI_DRAFT_KEY, text or ""))
    except Exception:
        return False


TERMINAL_ORIGIN = "terminal"     # composer-draft `origin` for a synced box
SEND_QUIET_S = 4.0               # after OUR paste, ignore the box for this long
_LAST_SEND = {}                  # sid -> monotonic stamp of the last web send


def note_send(sid):
    """Stamp that the web just pasted into this session's input box, so the
    draft sync ignores it (see sync_terminal_draft). In-process only: the
    dashboard is one process, and a missed stamp costs a self-correcting
    flicker, not correctness."""
    if sid:
        _LAST_SEND[sid] = time.monotonic()


def sync_terminal_draft(sid, seen, last, stored):
    """Mirror what the user typed AT THE TERMINAL into the web composer draft,
    so a half-written prompt in the kitty tab can be finished on a phone
    (docs/dashboard.md, *Terminal draft sync*). Writes the SAME `composer-draft`
    kv the web composer uses — so it reaches every device through the machinery
    that already broadcasts and restores drafts, and needs no second store.

    `seen` is the box's real text now (None = we could not read it: no news,
    never a signal), `last` what the previous probe saw, `stored` the current
    draft record (or None). Returns the text written, or None when nothing was.

    The asymmetry is the point. A NON-EMPTY box means someone is typing at the
    terminal, so it wins. An EMPTY box does NOT mean "clear the draft": a draft
    typed on another device lives only in the kv, and the terminal box is empty
    for it ALWAYS — blindly syncing emptiness would wipe the phone's draft on
    the next tick. A clear rides through only when the STORED draft is one WE
    synced (`origin`), i.e. its text came from that now-empty box.

    Keying the clear on `origin` rather than on `last` is what makes it survive
    a reconnect: `last` is per-connection memo and starts empty, so a page that
    opens AFTER the terminal message was sent would see empty == empty and
    leave the stale draft forever ("the draft doesn't clear after I send from
    kitty", 2026-07-25). The stored record remembers where it came from; a
    freshly-connected page doesn't have to."""
    from core import state as ST
    if seen is None:
        return None                      # unreadable box — no news
    if time.monotonic() - _LAST_SEND.get(sid, 0) < SEND_QUIET_S:
        # our OWN paste is briefly in that box before its Enter — reading it
        # back would echo the outgoing message into every device's composer
        return None
    stale = bool(stored and stored.get("text")
                 and stored.get("origin") == TERMINAL_ORIGIN)
    if seen:
        if stored and stored.get("text") == seen:
            return None                  # the kv already says exactly this —
            #   without this, every page's FIRST probe re-writes the same text
            #   (one redundant push per open device, seen live as the same
            #   draft stored three times in four seconds)
        if last is not None and seen == last:
            # the terminal box hasn't CHANGED, so it has nothing new to say —
            # and re-pushing it every tick would overwrite an edit the user is
            # making to that same draft on the web, making it impossible to
            # touch a synced draft anywhere else. `last is None` is the first
            # probe of a connection: adopt the box then, which is what makes
            # "type in kitty, then open the dashboard" work.
            return None
    elif not stale:
        return None                      # someone else's draft — not ours to clear
    try:
        sdb = API.state_db_for(sid)
        if not sdb:
            return None
        # kv_set_at's seq-guarded sibling, like every composer-draft write: a
        # page's own save may be in flight (the CAS keeps the newest)
        res = ST.kv_cas_seq_at(sdb, "composer-draft",
                               {"text": seen, "origin": TERMINAL_ORIGIN,
                                "seq": int(time.time() * 1000)})
    except Exception:
        A.error(sid, "dashboard terminal draft (sync)", {"chars": len(seen)})
        return None
    if res != "written":
        return None
    # The box HOLDS that text, so the next web send must replace it rather than
    # paste after it — the same flag a take-back sets. Without this, sending
    # from the web the draft you typed in kitty delivers it TWICE over
    # (`abcabc`), since the composer now shows what the box already holds.
    set_tui_draft(sid, seen)
    # audited like every other composer-draft write, with its own action so the
    # trail says WHERE a draft came from (a page's save vs the terminal box)
    A.state_file(sid, sdb, "composer-draft",
                 {"action": "terminal", "chars": len(seen)})
    return seen


def type_command(fe, win, text):
    """Put a SLASH COMMAND into a session's input box and submit it. Returns
    (ok, cleared_clipboard_image).

    THE one way to do that — every site that puts a `/…` command in front of
    Claude Code goes through here, because raw keystrokes are NOT SAFE in that
    box. With `editorMode: vim` the input is MODAL, and anything that pressed
    Escape first (the interrupt presses up to `INTERRUPT_TRIES`) leaves it in
    NORMAL mode, where the characters are vim COMMANDS rather than text: `/`
    opens reverse history search, and Claude Code's own hint spells out the
    workaround — *"press Esc then i then / to open the command menu instead"*.
    Measured 2026-07-25: a web rewind ~14s after a web interrupt typed
    `/rewind` into a NORMAL-mode box, the checkpoint menu never opened, and the
    tail of the keystrokes was submitted into the conversation as the message
    `nd` (the first `web-rewind-to` `step: "open"` failure in the audit; the
    identical `nd` artifact recorded earlier in the Esc-gesture comment was
    blamed on a racing Escape, which now looks like the wrong diagnosis).

    A BRACKETED PASTE is mode-proof — Claude Code takes it as content, never as
    keystrokes — and it is already how `post_command`'s quick commands
    (`/compact`, `/model`, `/effort`) reach the TUI, which is why those kept
    working where the typed `/rewind` did not. The Enter rides outside the
    paste (kitten_send_text), so it still submits.

    The clipboard-image guard comes with it: a bracketed paste makes Claude
    Code attach whatever IMAGE is on the board (docs/dashboard.md
    *Clipboard-image guard*), so no caller may paste without it — folding the
    two together here is the point of the single owner."""
    clip = _clear_clipboard_image()
    return bool(fe.paste_text(win, text)), clip


def _steal_watch(before, terminal_app):
    """The post-launch focus watch (a daemon thread — the HTTP response never
    waits on it): record each TRANSITION of the frontmost app onto the
    terminal during the watch window, purely for the audit trail. Observes,
    never intervenes — the fix for the steal lives in the terminal frontend
    (launch_pane's conditional --keep-focus); a non-empty `steals` list on a
    current build means some launch path still activates the terminal and
    names the second it happened. One `web-launch-steal-watch` state_files
    row per watch (`steals` = seconds-into-watch of each takeover; [] =
    clean)."""
    t0 = time.time()
    steals, prev = [], before
    for _ in range(STEALWATCH_POLLS):
        time.sleep(STEALWATCH_POLL_S)
        now = _front_app()
        if not now:
            continue
        if now == terminal_app and prev != terminal_app:
            steals.append(round(time.time() - t0, 2))
        prev = now
    A.state_file("", "", "web-launch-steal-watch",
                 {"before": before, "terminal": terminal_app,
                  "steals": steals})


# --- post-launch SSE wake watch ------------------------------------------------------
# A web launch's session doesn't exist anywhere until claude finishes booting
# in the new tab and fires SessionStart (measured 1.4-2.1s across recent
# launches — the audit `web-launch` rows joined against the following
# SessionStart). Without a nudge the global SSE loop only notices the new
# sessions row on its next GLOBAL_TICK_S poll, adding up to a full second of
# dead air on top. This watch polls the sessions head at a fast cadence and,
# the moment the launched session appears, pushes a `wake` into NOTIFIER —
# the sse_global loops block on that queue, so every connected page both
# receives the `wake` (the launching page jumps straight to the sid it
# carries) and rebuilds/pushes the sessions snapshot NOW instead of at the
# tick. Matching: by kitty_window_id when the launch reported the new
# window's id (exact — covers fresh/resume/continue alike, since the audit's
# session upsert stamps the resumed row's new window too), else a session in
# the launch cwd whose started_at postdates the launch.
LAUNCHWAKE_POLL_S = 0.15           # sessions-head poll cadence after a launch
LAUNCHWAKE_MAX_S = 15.0            # claude boot measured ~2s; 15s covers a cold
#                                    machine without leaving a zombie poller


def _launch_wake(win, cwd, t0):
    """The post-launch appearance watch (a daemon thread — the HTTP response
    never waits on it). Ends with ONE `web-launch-wake` state_files row either
    way: found (`sid`, `waited_s` = launch→appearance latency, the dashboard's
    own share of it reconstructible next to the `web-launch` row) or timeout
    (`sid` empty). The `wake` push happens only on found — a timeout has
    nothing to hurry the loops for."""
    deadline = t0 + LAUNCHWAKE_MAX_S
    sid = ""
    while not sid and time.time() < deadline:
        try:
            for row in API.sessions(10):
                if ((win and str(row.get("kitty_window_id") or "") == win)
                        or (not win and row.get("cwd") == cwd
                            and (row.get("started_at") or 0) >= t0)):
                    sid = row["sid"]
                    break
        except Exception:
            A.error("", "dashboard launch wake")
            break
        if not sid:
            time.sleep(LAUNCHWAKE_POLL_S)
    if sid:
        from dashboard.notify.notifier import NOTIFIER  # the singleton lives here
        NOTIFIER.push("wake", {"sid": sid, "win": win, "cwd": cwd})
    A.state_file("", "", "web-launch-wake",
                 {"sid": sid, "win": win, "cwd": cwd, "ok": bool(sid),
                  "waited_s": round(time.time() - t0, 3)})
