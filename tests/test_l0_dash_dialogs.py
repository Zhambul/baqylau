# tests/test_l0_dash_dialogs.py — L0 dashboard: the screen-driven dialogs: rewind, ask, plan, terminal drafts.
#
# One subject out of the former 8468-line L0 dashboard monolith; the
# shared HTTP/audit helpers live in tests/dashkit.py and the in-process
# server fixture (`dash`) in tests/conftest.py.
import json
import os
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.request

import pytest
from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

import core.audit as A
from core import paths as P
from core import state as S
from dashboard import server as DS
from dashboard import suggestion as SUG



# ------------------------------------------------------------------ opshtml
from dashkit import (_FakeFE, _get_json, _inject_fe, _last_state_file, _post, _sf_rows_full, _tw)


# --- terminal -> web draft sync (docs/dashboard.md, *Terminal draft sync*) ----
# Type into the kitty tab's input box without sending, open the session on
# another device, and the half-written prompt is in the composer.
def _term_screen(text):
    rule = "\x1b[m\x1b[38:2:136:136:136m" + "\u2500" * 100
    return rule + "\n\x1b[m\u276f\xa0" + text + "\n" + rule + "\n"


def test_probe_box_reads_both_halves_from_one_capture():
    # the ghost and the typed text partition the box by INTENSITY, and the SSE
    # tick wants both — so it captures once
    calls = []

    class FE:
        def get_text(self, win, extent="screen", ansi=False):
            calls.append(ansi)
            return _term_screen("half a prompt")

    ghost, typed = SUG.probe_box(FE(), "1", "s")
    assert (ghost, typed) == (None, "half a prompt")
    assert calls == [True]                       # ONE capture, both readings
    ghostly = _term_screen("\x1b[22;2ma suggestion")
    assert SUG.parse(ghostly) == "a suggestion" and SUG.typed(ghostly) is None


def test_terminal_draft_sync_pushes_typing_and_ignores_a_still_box():
    A.session_start({"session_id": "ts1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("ts1")
    S.kv_set(log, "seed", 1)                     # the state DB must exist
    L = DS.launch
    assert L.sync_terminal_draft("ts1", "typed at the terminal", None, None) \
        == "typed at the terminal"
    d = S.kv_get(log, "composer-draft")
    assert d["text"] == "typed at the terminal" and d["origin"] == "terminal"
    # unchanged box -> no write at all: no seq churn, and (the reason it
    # matters) no overwriting an edit the user is making to that same draft on
    # the web while the box sits still
    assert L.sync_terminal_draft("ts1", "typed at the terminal",
                                 "typed at the terminal", d) is None
    # …and a FRESH connection (no memo) whose box already matches the kv writes
    # nothing either: otherwise every open device re-pushes the same text on
    # its first probe (three identical stores in four seconds, seen live)
    assert L.sync_terminal_draft("ts1", "typed at the terminal", None, d) is None
    # the box holding that text also arms clear_draft, or a web send of the
    # very draft we synced would paste after it and deliver it twice
    assert L.tui_draft("ts1") == "typed at the terminal"


def test_terminal_draft_sync_clears_after_a_send_on_a_fresh_connection():
    # "the draft doesn't clear after I send from the kitty tab": the clear used
    # to need the previous probe's memo to match, but a page that CONNECTS
    # after the send starts with an empty memo and an empty box — equal, so it
    # left the stale draft forever. The STORED record's origin is what says the
    # draft came from that now-empty box, and it survives reconnects.
    A.session_start({"session_id": "ts3", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("ts3")
    S.kv_set(log, "seed", 1)
    mine = {"text": "sent from kitty", "origin": "terminal", "seq": 1}
    S.kv_set(log, "composer-draft", mine)
    # fresh connection: no memo at all, box already empty
    assert DS.launch.sync_terminal_draft("ts3", "", None, mine) == ""
    assert S.kv_get(log, "composer-draft")["text"] == ""


def test_terminal_draft_sync_ignores_an_unreadable_box_and_our_own_send():
    # None = "we could not read a box" (dead window, kitten failure) — no news,
    # never a signal, or every session ending would wipe its draft.
    A.session_start({"session_id": "ts4", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("ts4")
    S.kv_set(log, "seed", 1)
    mine = {"text": "still typing", "origin": "terminal", "seq": 1}
    S.kv_set(log, "composer-draft", mine)
    L = DS.launch
    assert L.sync_terminal_draft("ts4", None, "still typing", mine) is None
    assert S.kv_get(log, "composer-draft")["text"] == "still typing"
    # and our OWN paste sits in that box for a beat before its Enter — reading
    # it back would echo the outgoing message into every device's composer
    L.note_send("ts4")
    assert L.sync_terminal_draft("ts4", "the message we just sent", None,
                                 mine) is None
    assert S.kv_get(log, "composer-draft")["text"] == "still typing"


def test_terminal_draft_sync_never_wipes_another_devices_draft():
    # THE asymmetry: an empty box is the NORMAL state for a draft typed on a
    # phone (it lives only in the kv), so emptiness must not propagate. A clear
    # only rides through when the box is emptying text we ourselves synced.
    A.session_start({"session_id": "ts2", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("ts2")
    S.kv_set(log, "seed", 1)
    L = DS.launch
    phone = {"text": "written on the iPad", "origin": "devA", "seq": 1}
    S.kv_set(log, "composer-draft", phone)
    # box empty, and the stored draft is NOT what the box last held -> hands off
    assert L.sync_terminal_draft("ts2", "", "something else", phone) is None
    assert S.kv_get(log, "composer-draft")["text"] == "written on the iPad"
    # but a terminal draft that WE synced, then sent at the terminal, clears
    mine = {"text": "mine", "origin": "terminal", "seq": 2}
    S.kv_set(log, "composer-draft", mine)
    assert L.sync_terminal_draft("ts2", "", "mine", mine) == ""
    assert S.kv_get(log, "composer-draft")["text"] == ""


def test_no_dashboard_code_calls_the_thread_bound_kv_set():
    """state.kv_set caches its connection per PROCESS but sqlite binds it to
    the creating THREAD, so from a ThreadingHTTPServer request it writes
    nothing and returns False (styleguide single-owner table). Dashboard-side
    writes must use kv_set_at. A plain `kv_set(` under dashboard/ is that bug
    coming back — it cost two rounds of "the take-back came back at random"."""
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pat = re.compile(r"(?<!_at)\bkv_set\s*\(")
    hits = []
    for dirpath, _dirs, files in os.walk(os.path.join(root, "dashboard")):
        for f in files:
            if not f.endswith(".py"):
                continue
            fp = os.path.join(dirpath, f)
            with open(fp, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if pat.search(line) and not line.lstrip().startswith("#"):
                        hits.append("%s:%d" % (os.path.relpath(fp, root), i))
    assert hits == [], hits


def test_take_back_stash_writes_from_another_thread():
    # THE bug behind "it came back at random" (2026-07-25): the dashboard is a
    # ThreadingHTTPServer, so every request runs on its own thread, and
    # state.kv_set's CACHED connection belongs to whichever thread opened it —
    # from any other thread it raises inside its own swallow and writes
    # nothing. The stash landed only when a request happened to hit the owning
    # thread. Both stashes go through kv_set_at (fresh connection per call).
    from plugins.claude_code import transcript as TR
    A.session_start({"session_id": "thr1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("thr1")
    S.kv_set(log, "seed", 1)          # binds the cached connection to THIS thread
    out = []
    t = threading.Thread(target=lambda: out.append(
        TR.mark_taken_back("thr1", "u-from-another-thread")))
    t.start()
    t.join()
    assert out == [True]                        # the write REPORTS honestly
    assert TR.taken_back("thr1") == ("u-from-another-thread",)   # and landed
    out2 = []
    t2 = threading.Thread(target=lambda: out2.append(
        DS.launch.set_tui_draft("thr1", "left in the box")))
    t2.start()
    t2.join()
    assert out2 == [True]
    assert DS.launch.tui_draft("thr1") == "left in the box"


def test_take_back_makes_the_next_send_replace_the_tui_draft(dash, monkeypatch):
    # The reload hole (2026-07-25): the take-back leaves the message in the TUI
    # input box, and the NEXT send has to replace it. The page used to remember
    # that in a per-view variable, which a reload wiped while the TUI's draft
    # survived — so the send pasted AFTER the leftover and delivered
    # "testingtesting2". The server owns the fact now, so a send that knows
    # nothing (no clear_draft in the body — a freshly reloaded page) still
    # clears the line first.
    fe = _FakeFE()
    fe.ansi_screen = ("\x1b[m\x1b[38:2:136:136:136m" + "\u2500" * 100 + "\n"
                      "\x1b[m\u276f\xa0testing\n"
                      "\x1b[m\x1b[38:2:136:136:136m" + "\u2500" * 100 + "\n")
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "95")
    A.session_start({"session_id": "td1", "cwd": "/w", "transcript_path": ""})
    S.kv_set(P.mirror_log("td1"), "seed", 1)   # the state DB must EXIST: the
    #   stash writes through kv_set_at, which never creates one
    monkeypatch.setattr(DS.session, "last_prompt_rec",
                        lambda sid: ("testing", "u-tb"))
    _post(dash + "/api/session/td1/interrupt", {})
    assert DS.launch.tui_draft("td1") == "testing"      # recorded server-side
    fe.keyed = []
    code, _b = _post(dash + "/api/session/td1/message", {"text": "testing2"})
    assert code == 200
    # the line was killed BOTH ways before the paste — no "testingtesting2"
    assert fe.keyed == [("95", ("ctrl+u",)), ("95", ("ctrl+k",))]
    assert fe.pasted[-1] == ("95", "testing2")
    row = _last_state_file("td1", "web-send")
    assert row["clear_draft"] is True and row["tui_draft"] is True
    assert DS.launch.tui_draft("td1") == ""             # consumed by the send


def test_post_interrupt_leaves_a_terminal_draft_alone(dash, monkeypatch):
    # The box holding something ELSE is the user's own terminal draft, not a
    # take-back — never echoed into the web composer.
    fe = _FakeFE()
    fe.ansi_screen = ("\x1b[m\x1b[38:2:136:136:136m" + "\u2500" * 100 + "\n"
                      "\x1b[m\u276f\xa0something I was typing here\n"
                      "\x1b[m\x1b[38:2:136:136:136m" + "\u2500" * 100 + "\n")
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "92")
    A.session_start({"session_id": "tb2", "cwd": "/w", "transcript_path": ""})
    S.kv_set(P.mirror_log("tb2"), "seed", 1)   # the state DB must EXIST: the
    #   stash writes through kv_set_at, which never creates one
    monkeypatch.setattr(DS.session, "last_prompt_rec",
                        lambda sid: ("an unrelated prompt", "u-other"))
    code, body = _post(dash + "/api/session/tb2/interrupt", {})
    assert code == 200 and json.loads(body)["restored"] == ""


def test_post_interrupt_empty_box_is_a_plain_stop(dash, monkeypatch):
    # Nothing in the box = the turn was interrupted with its work KEPT; there
    # is nothing to restore and no restore row.
    fe = _FakeFE()
    fe.ansi_screen = ("\x1b[m\x1b[38:2:136:136:136m" + "\u2500" * 100 + "\n"
                      "\x1b[m\u276f\xa0\n"
                      "\x1b[m\x1b[38:2:136:136:136m" + "\u2500" * 100 + "\n")
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "93")
    A.session_start({"session_id": "tb3", "cwd": "/w", "transcript_path": ""})
    S.kv_set(P.mirror_log("tb3"), "seed", 1)   # the state DB must EXIST: the
    #   stash writes through kv_set_at, which never creates one
    monkeypatch.setattr(DS.session, "last_prompt_rec",
                        lambda sid: ("a prompt that ran", "u-ran"))
    code, body = _post(dash + "/api/session/tb3/interrupt", {})
    assert code == 200 and json.loads(body)["restored"] == ""
    assert _last_state_file("tb3", "web-interrupt").get("phase") != "restore"


def test_no_slash_command_is_send_text_anywhere():
    """The slash-command channel is launch.type_command's (styleguide
    single-owner table): a bracketed paste, because raw keystrokes in a vim
    NORMAL-mode input box are COMMANDS, not text. A `send_text` of a `/…`
    literal anywhere in the dashboard is that bug coming back."""
    import os
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pat = re.compile(r"""send_text\(\s*\w+\s*,\s*["']/""")
    hits = []
    for dirpath, _dirs, files in os.walk(os.path.join(root, "dashboard")):
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dirpath, f)
            with open(p, encoding="utf-8", errors="replace") as fh:
                if pat.search(fh.read()):
                    hits.append(os.path.relpath(p, root))
    assert hits == [], hits


def test_slash_commands_never_reach_the_tui_as_keystrokes(dash, monkeypatch):
    # THE vim-mode regression (2026-07-25). With editorMode vim the input box is
    # MODAL, and anything that pressed Escape first — the interrupt presses up
    # to INTERRUPT_TRIES — leaves it in NORMAL mode, where "/rewind" is vim
    # COMMANDS, not text: the checkpoint menu never opened and the tail of the
    # keystrokes was submitted into the conversation as the message `nd`. A
    # bracketed paste is mode-proof (Claude Code reads it as content), so EVERY
    # slash command goes through launch.type_command and NOTHING types one.
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "94")
    A.session_start({"session_id": "sl1", "cwd": "/w", "transcript_path": ""})
    _post(dash + "/api/session/sl1/rewind", {})                    # the menu
    _post(dash + "/api/session/sl1/command", {"cmd": "compact"})   # quick cmds
    assert fe.sent == []                        # nothing typed, ever
    assert [t for _w, t in fe.pasted] == ["/rewind", "/compact"]


def test_post_rewind_busy_is_refused(dash, monkeypatch):
    # MID-TURN there is no rewinding: the endpoint used to FORK here into the
    # ⊘ cancel button's double-Escape "cancel + restore". post_interrupt does
    # that with ONE Escape (the outcome is the terminal's call, decided by WHEN
    # you press), so the fork is gone and a busy rewind 409s, pressing nothing —
    # a typed /rewind would queue as a message.
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "89")
    A.session_start({"session_id": "rew2", "cwd": "/w", "transcript_path": ""})
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"89": "working"})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/rew2/rewind", {})
    assert e.value.code == 409
    assert fe.keyed == [] and fe.sent == []


def test_post_interrupt_refuses_on_open_dialog(dash, monkeypatch):
    # a red "asking you" tab means a MODAL DIALOG is open (AskUserQuestion /
    # ExitPlanMode / a permission prompt). An Esc there DECLINES the dialog
    # rather than interrupting a turn — it once killed the answer the user was
    # giving via the web ask card ("User declined to answer questions",
    # 2026-07-20). Refuse with a 409 and press NO key; the card is the response.
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "90")
    A.session_start({"session_id": "intrd", "cwd": "/w", "transcript_path": ""})
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"90": "awaiting-command"})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/intrd/interrupt", {})
    assert e.value.code == 409
    assert fe.keyed == []                        # no Escape reached the dialog


def test_post_rewind_refuses_on_open_dialog(dash, monkeypatch):
    # the cancel-edit / rewind gesture must NOT fire on a red tab: its Esc-Esc
    # (cancel-edit) or typed /rewind would land in the open ask/plan/permission
    # dialog and dismiss or corrupt it. 409, and neither keys nor text sent.
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "91")
    A.session_start({"session_id": "rewd", "cwd": "/w", "transcript_path": ""})
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"91": "awaiting-command"})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/rewd/rewind", {})
    assert e.value.code == 409
    assert fe.keyed == [] and fe.sent == []


def test_post_rewind_to_refuses_on_open_dialog(dash, monkeypatch):
    # full web rewind on a red tab: a dialog is open, so /rewind must not be
    # typed into it (previously covered incidentally by the busy-tab guard; now
    # an explicit dialog refusal since awaiting-command left BUSY_TABS)
    fe = _MenuFE(prompts=["p"])
    _rewind_env(monkeypatch, "rwtd", "92", fe)
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"92": "awaiting-command"})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/rwtd/rewind-to",
              {"text": "p", "mode": "both", "ups": 1})
    assert e.value.code == 409
    assert fe.sent == [] and fe.state == "idle"     # nothing typed into the dialog


class _MenuFE(_FakeFE):
    """_FakeFE plus a tiny simulation of Claude Code's rewind menu, so
    rewindmenu.drive's SCREEN-VERIFIED navigation runs against reactive
    screens instead of a canned transcript of get_text results: `/rewind`
    opens the checkpoint list, up/down move the cursor (pegging at the
    edges like the real TUI), Enter opens the numbered confirm menu, a
    digit selects (recorded in .picked) and Escape backs out one level.
    Screen shapes copied from live captures (2026-07-18): indented menu
    cursor rows, a column-0 scrollback prompt echo that must NOT parse as
    the cursor, the "(current)" trailing entry, numbered confirm rows."""

    def __init__(self, prompts, options=("Restore code and conversation",
                                         "Restore conversation",
                                         "Restore code", "Never mind")):
        super().__init__()
        self.prompts = list(prompts)         # oldest-first menu first-lines
        self.options = list(options)
        self.state = "idle"                  # idle | menu | confirm
        self.cursor = len(self.prompts)      # start on "(current)"
        self.picked = None                   # (prompt index, option label)

    def paste_text(self, win, text):
        # /rewind arrives as a BRACKETED PASTE, never raw keystrokes: with
        # editorMode vim a NORMAL-mode box reads typed characters as commands
        ok = super().paste_text(win, text)
        if text == "/rewind" and self.state == "idle":
            self.state, self.cursor = "menu", len(self.prompts)
        return ok

    def send_key(self, win, *keys):
        ok = super().send_key(win, *keys)
        for k in keys:
            if self.state == "menu":
                if k == "up":
                    self.cursor = max(0, self.cursor - 1)
                elif k == "down":
                    self.cursor = min(len(self.prompts), self.cursor + 1)
                elif k == "enter" and self.cursor < len(self.prompts):
                    self.state = "confirm"
                elif k == "escape":
                    self.state = "idle"
            elif self.state == "confirm":
                if k == "escape":
                    self.state = "menu"
                elif k.isdigit() and 1 <= int(k) <= len(self.options):
                    self.picked = (self.cursor, self.options[int(k) - 1])
                    self.state = "idle"
        return ok

    def get_text(self, win, extent="screen"):
        if self.state == "menu":
            rows = ["❯ a scrollback prompt echo at column 0", "", "  Rewind",
                    "", "  Restore the code and/or conversation to the point…"]
            for i, p in enumerate(self.prompts + ["(current)"]):
                rows += [("  ❯ " if i == self.cursor else "    ") + p, ""]
            # v2.1.220 composes this footer as `<chord> to <action>` with a
            # LOWERCASE chord label — the drift that broke menu_open
            rows.append("  enter to continue · esc to cancel")
            return "\n".join(rows)
        if self.state == "confirm":
            # the real confirm screen states the code consequence — absent
            # code options always pair with "The code will be unchanged."
            has_code = any("code" in o.lower() for o in self.options)
            rows = ["", "  Rewind", "", "  Confirm you want to restore to the"
                    " point before you sent this message:", "",
                    "  The code will be restored +1 -1 in f.txt." if has_code
                    else "  The code will be unchanged.", ""]
            for i, o in enumerate(self.options):
                rows.append(("  ❯ " if i == 0 else "    ")
                            + "%d. %s" % (i + 1, o))
            return "\n".join(rows)
        return "❯ composer\n  -- INSERT --"


def _rewind_env(monkeypatch, sid, win, fe):
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS.rewindmenu, "POLL_S", 0.01)
    monkeypatch.setattr(DS.rewindmenu, "KEY_GAP_S", 0)
    monkeypatch.setenv("KITTY_WINDOW_ID", win)
    A.session_start({"session_id": sid, "cwd": "/w", "transcript_path": ""})


def test_post_rewind_to_drives_the_menu(dash, monkeypatch):
    # full web rewind: /rewind typed, the checkpoint list navigated to the
    # TARGET prompt (verified by its menu text — the entry is the prompt's
    # first line), the confirm option picked by LABEL, and the restored text
    # echoed back for the page's composer prefill
    fe = _MenuFE(prompts=["make alpha", "make beta"])
    _rewind_env(monkeypatch, "rwt1", "31", fe)
    code, body = _post(dash + "/api/session/rwt1/rewind-to",
                       {"text": "make beta\nsecond line the menu never shows",
                        "mode": "both", "ups": 1})
    assert code == 200
    assert json.loads(body) == {
        "ok": True, "mode": "both", "degraded": False,
        "restored": "make beta\nsecond line the menu never shows"}
    assert fe.picked == (1, "Restore code and conversation")
    assert ("31", "/rewind") in fe.pasted      # pasted, never typed
    assert fe.sent == []
    assert fe.state == "idle"                 # menu fully closed


def test_post_rewind_to_digit_follows_labels(dash, monkeypatch):
    # the confirm menu's NUMBERING SHIFTS with content (no code changes ⇒
    # "Restore conversation" is 1., not 2.) — the digit must come from the
    # parsed labels, never a hard-coded position
    fe = _MenuFE(prompts=["only prompt"],
                 options=("Restore conversation", "Summarize from here",
                          "Summarize up to here", "Never mind"))
    _rewind_env(monkeypatch, "rwt2", "32", fe)
    code, body = _post(dash + "/api/session/rwt2/rewind-to",
                       {"text": "only prompt", "mode": "conversation",
                        "ups": 1})
    assert code == 200 and json.loads(body)["ok"] is True
    assert fe.picked == (0, "Restore conversation")


def test_post_rewind_to_stale_hint_self_corrects(dash, monkeypatch):
    # a stale page hint (dead-branch bubbles the menu doesn't list) bursts to
    # the wrong entry — the text-verified scan walks up to the top, then back
    # down through the list, and still lands on the right checkpoint. Also
    # the code-only mode: no `restored` (the TUI composer got no draft).
    fe = _MenuFE(prompts=["p one", "p two", "p three"])
    _rewind_env(monkeypatch, "rwt3", "33", fe)
    code, body = _post(dash + "/api/session/rwt3/rewind-to",
                       {"text": "p three", "mode": "code", "ups": 3})
    assert code == 200
    assert json.loads(body) == {"ok": True, "mode": "code", "restored": "",
                                "degraded": False}
    assert fe.picked == (2, "Restore code")


def test_post_rewind_to_busy_is_409(dash, monkeypatch):
    # mid-turn the double-Esc gesture means CANCEL, and a typed /rewind would
    # queue as a message — the endpoint refuses outright
    fe = _MenuFE(prompts=["p"])
    _rewind_env(monkeypatch, "rwt4", "34", fe)
    monkeypatch.setattr(DS.API, "tab_states", lambda: {"34": "working"})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/rwt4/rewind-to",
              {"text": "p", "mode": "both", "ups": 1})
    assert e.value.code == 409
    assert fe.sent == [] and fe.state == "idle"     # nothing typed


def test_post_rewind_to_not_found_bails_closed(dash, monkeypatch):
    # a target the menu doesn't list (e.g. rewound away in kitty since the
    # page loaded) scans the whole list, then Escapes the menu shut — the
    # session is never left sitting inside an open menu
    fe = _MenuFE(prompts=["p one", "p two"])
    _rewind_env(monkeypatch, "rwt5", "35", fe)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/rwt5/rewind-to",
              {"text": "no such prompt", "mode": "both", "ups": 1})
    assert e.value.code == 409
    assert json.loads(e.value.read())["step"] == "find"
    assert fe.state == "idle" and fe.picked is None


def test_post_rewind_to_both_degrades_when_code_unchanged(dash, monkeypatch):
    # "restore code and conversation" at a checkpoint with NO code changes:
    # the code is already in the target state, Claude Code omits the code
    # options as no-ops — the driver degrades to "Restore conversation"
    # (verified against the screen's "The code will be unchanged." line)
    # instead of failing (reported live 2026-07-18)
    fe = _MenuFE(prompts=["p"],
                 options=("Restore conversation", "Summarize from here",
                          "Summarize up to here", "Never mind"))
    _rewind_env(monkeypatch, "rwt7", "37", fe)
    code, body = _post(dash + "/api/session/rwt7/rewind-to",
                       {"text": "p", "mode": "both", "ups": 1})
    assert code == 200
    assert json.loads(body) == {"ok": True, "mode": "both", "restored": "p",
                                "degraded": True}
    assert fe.picked == (0, "Restore conversation")


def test_post_rewind_to_missing_option_bails_closed(dash, monkeypatch):
    # asking for a code restore at a checkpoint with no code changes: the
    # option isn't on the confirm menu — back out (both menus closed), 409
    fe = _MenuFE(prompts=["p"],
                 options=("Restore conversation", "Summarize from here",
                          "Never mind"))
    _rewind_env(monkeypatch, "rwt6", "36", fe)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/rwt6/rewind-to",
              {"text": "p", "mode": "code", "ups": 1})
    assert e.value.code == 409
    err = json.loads(e.value.read())
    assert err["step"] == "option"
    # the bail explains WHY the option is absent (the screen said the code
    # is unchanged) — "rewind failed" alone sent the user to the audit
    assert "no code changes to revert" in err["error"]
    assert fe.state == "idle" and fe.picked is None


# real screen captures (live session, 2026-07-18; longest prompt lines
# shortened for the linter — shapes and prefixes untouched) the parsers pin
_MENU_SCREEN = """\
❯ Use the Write tool to write the word ALPHA into rewind-test.txt.

⏺ Done.

  Rewind

  Restore the code and/or conversation to the point before…

    Use the Write tool to write the word ALPHA into rewind-test.txt.
    rewind-test.txt +1

  ❯ Now use Write to overwrite /private/tmp/rewind-test.txt with the single word BETA. Reply with one word.
    rewind-test.txt +1 -1

    This is a deliberately very long first line meant to overflow the menu entry width and show truncation …
    No code changes

    (current)

  Enter to continue · Esc to cancel"""


_CONFIRM_SCREEN = """\
  Rewind

  Confirm you want to restore to the point before you sent this message:

  │ Now use Write to overwrite /private/tmp/rewind-test.txt with the single word BETA. Reply with one word.
  │ (52s ago)

  The conversation will be forked.
  The code will be restored +1 -1 in rewind-test.txt.

  ❯ 1. Restore code and conversation
    2. Restore conversation
    3. Restore code
    4. Summarize from here
  ↓ 5. Summarize up to here

  ⚠ Rewinding does not affect files edited manually or via bash."""


def test_rewindmenu_parsers_pin_the_real_screens():
    RM = DS.rewindmenu
    assert RM.menu_open(_MENU_SCREEN)
    assert not RM.confirm_open(_MENU_SCREEN)
    # the column-0 scrollback prompt echo is NOT the cursor; the indented
    # "  ❯ " row is
    assert RM.cursor_entry(_MENU_SCREEN) == ("Now use Write to overwrite "
        "/private/tmp/rewind-test.txt with the single word BETA. "
        "Reply with one word.")
    assert RM.confirm_open(_CONFIRM_SCREEN)
    assert not RM.menu_open(_CONFIRM_SCREEN)
    assert RM.confirm_options(_CONFIRM_SCREEN) == {
        "restore code and conversation": "1",
        "restore conversation": "2",
        "restore code": "3",
        "summarize from here": "4",
        "summarize up to here": "5",     # the ↓ scroll indicator is tolerated
    }
    assert not RM.menu_open("❯ composer\n  -- INSERT --")
    assert RM.menu_region("no menu here at all") == ""


def test_rewindmenu_entry_match_is_truncation_aware():
    RM = DS.rewindmenu
    long = ("This is a deliberately very long first line meant to overflow "
            "the rewind menu entry width and show me how truncation is "
            "rendered at the edge of the pane, if at all, in the checkpoint "
            "list.\nSecond line here.")
    trunc = ("This is a deliberately very long first line meant to overflow "
             "the rewind menu entry width and show me how truncation is "
             "rendered …")
    assert RM.entry_matches(trunc, long)             # ellipsis = prefix match
    assert RM.entry_matches("short prompt", "short prompt\nsecond line")
    assert not RM.entry_matches("short prompt", "short prompt but longer")
    assert not RM.entry_matches("(current)", "anything")
    assert not RM.entry_matches("other …", long)


class _AskFE(_FakeFE):
    """_FakeFE plus a reactive simulation of the v2.1.215 AskUserQuestion
    dialog (re-measured live 2026-07-19): a header-chip bar, one pane per
    question (numbered options; multiSelect checkboxes; a numbered "Type
    something" row; multiSelect an unnumbered "Next"/"Submit" advance row),
    "Chat about this" below a rule, and the review pane. Key semantics as
    measured: DIGITS ARE INERT — selection is up/down to a row + Enter; Enter
    on a single-select option selects+advances (a sole single-select question
    submits outright), Enter on a multiSelect option toggles, Enter on the
    multiSelect advance row moves to the next tab; typing goes into the focused
    Type row (send_text's CR commits it); left/right/Tab move tabs (left
    no-ops at the first); the review's "Submit answers" row + Enter submits.
    This renders the classic (no-preview) layout; the parser's handling of the
    side-by-side preview layout is pinned against real captures in
    test_askdialog_parsers_pin_the_real_screens."""

    def __init__(self, questions):
        super().__init__()
        self.questions = questions
        n = len(questions)
        self.tab = 0                    # question index; n = the review pane
        self.cursor = 0                 # row index on the current pane
        self.open = True
        self.single = {}                # qi -> answered label/text
        self.checks = [set() for _ in range(n)]
        self.typed = [""] * n
        self.submitted = None           # final {question: answer} on submit
        self.chatted = False

    def _labels(self, qi):
        q = self.questions[qi]
        return [o["label"] for o in q.get("options") or []]

    def _type_label(self, qi):
        return self.typed[qi] or ("Type something"
                                  + ("" if self.questions[qi].get("multiSelect")
                                     else "."))

    # cursor-navigable rows of a pane, as ("kind", payload) in screen order:
    # options, the Type row, (multi) the unnumbered advance row, then Chat
    def _kinds(self, qi):
        ks = [("opt", i) for i in range(len(self._labels(qi)))]
        ks.append(("type", None))
        if self.questions[qi].get("multiSelect"):
            ks.append(("advance", None))
        ks.append(("chat", None))
        return ks

    def _advance(self):
        self.tab += 1
        self.cursor = 0
        if self.tab >= len(self.questions) \
                and len(self.questions) == 1 \
                and not self.questions[0].get("multiSelect"):
            self._finish()              # sole single-select: no review pane

    def _finish(self):
        out = {}
        for qi, q in enumerate(self.questions):
            if q.get("multiSelect"):
                sel = [lb for lb in self._labels(qi)
                       if lb in self.checks[qi]]
                if self.typed[qi] and "__typed__" in self.checks[qi]:
                    sel.append(self.typed[qi])
                out[q["question"]] = ", ".join(sel)
            else:
                out[q["question"]] = self.single.get(qi, "")
        self.submitted = out
        self.open = False

    def send_text(self, win, text):
        ok = super().send_text(win, text)
        if not self.open or self.tab >= len(self.questions):
            return ok
        qi = self.tab
        kinds = self._kinds(qi)
        if self.cursor < len(kinds) and kinds[self.cursor][0] == "type":
            self.typed[qi] = text                    # types inline
            if self.questions[qi].get("multiSelect"):
                self.checks[qi].add("__typed__")     # the CR checks it
            else:
                self.single[qi] = text               # the CR selects+advances
                self._advance()
        return ok

    def send_key(self, win, *keys):
        ok = super().send_key(win, *keys)
        for k in keys:
            if not self.open:
                continue
            if self.tab >= len(self.questions):      # review pane
                if k == "up":
                    self.cursor = max(0, self.cursor - 1)
                elif k == "down":
                    self.cursor = min(1, self.cursor + 1)
                elif k == "enter" and self.cursor == 0:
                    self._finish()                   # "Submit answers"
                continue                    # left/right/Tab/digits all inert
            qi = self.tab
            q = self.questions[qi]
            kinds = self._kinds(qi)
            # FORWARD-ONLY: left/right/Tab do NOT switch questions in this build
            # (measured live 2026-07-22, session 3fd325d9 — inert from every
            # row); the only way forward is Enter (auto-advance / the "Next"
            # row). up/down move the row cursor.
            if k == "up":
                self.cursor = max(0, self.cursor - 1)
            elif k == "down":
                self.cursor = min(len(kinds) - 1, self.cursor + 1)
            elif k == "enter":
                kind, payload = kinds[self.cursor]
                if kind == "opt":
                    label = self._labels(qi)[payload]
                    if q.get("multiSelect"):
                        self.checks[qi] ^= {label}   # toggle
                    else:
                        self.single[qi] = label
                        self._advance()
                elif kind == "type":
                    if q.get("multiSelect"):
                        self.checks[qi] ^= {"__typed__"}
                    elif self.typed[qi]:
                        self.single[qi] = self.typed[qi]
                        self._advance()
                elif kind == "advance":
                    self._advance()                  # "Next"/"Submit"
                elif kind == "chat":
                    self.chatted = True
                    self.open = False
            # digits inert
        return ok

    def get_text(self, win, extent="screen"):
        if not self.open:
            return "❯ composer\n  -- INSERT --"
        chips = "  ".join(
            ("☒ " if (self.single.get(i) or self.checks[i]) else "☐ ")
            + (q.get("header") or "Q%d" % (i + 1))
            for i, q in enumerate(self.questions))
        bar = "←  %s  ✔ Submit  →" % chips
        if self.tab >= len(self.questions):
            return "\n".join([bar, "", "Review your answers", "",
                              "Ready to submit your answers?", "",
                              ("❯ " if self.cursor == 0 else "  ")
                              + "1. Submit answers",
                              ("❯ " if self.cursor == 1 else "  ")
                              + "2. Cancel"])
        qi, q = self.tab, self.questions[self.tab]
        labels = self._labels(qi)
        multi = q.get("multiSelect")
        # question text WRAPS like the real TUI's (the 555-char live ask)
        lines = [bar, ""] \
            + (textwrap.wrap(q.get("question") or "", 48) or [""]) + [""]
        for idx, (kind, payload) in enumerate(self._kinds(qi)):
            cur = "❯ " if idx == self.cursor else "  "
            if kind == "opt":
                lb = labels[payload]
                chk = ("[✔] " if lb in self.checks[qi] else "[ ] ") \
                    if multi else ""
                lines.append("%s%d. %s%s" % (cur, payload + 1, chk, lb))
            elif kind == "type":
                chk = ("[✔] " if "__typed__" in self.checks[qi] else "[ ] ") \
                    if multi else ""
                lines.append("%s%d. %s%s"
                             % (cur, len(labels) + 1, chk,
                                self._type_label(qi)))
            elif kind == "advance":
                lines.append("%s   %s"
                             % (cur, "Submit" if qi == len(self.questions) - 1
                                else "Next"))
            elif kind == "chat":
                lines += ["────────",
                          "%s%d. Chat about this" % (cur, len(labels) + 2)]
        lines += ["", "Enter to select · ↑/↓ to navigate · "
                  "Tab to switch questions · Esc to cancel"]
        return "\n".join(lines)


def _ask_env(monkeypatch, sid, win, fe, questions, tid="toolu_a1"):
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS.askdialog, "POLL_S", 0.01)
    monkeypatch.setattr(DS.askdialog, "KEY_GAP_S", 0)
    # the open-check polls up to STEP_TIMEOUT_S now (like every other step) —
    # keep the dialog-dismissed "open" bail from burning the full budget
    monkeypatch.setattr(DS.askdialog, "STEP_TIMEOUT_S", 0.1)
    monkeypatch.setattr(DS.askdialog, "SUBMIT_TIMEOUT_S", 0.1)
    monkeypatch.setenv("KITTY_WINDOW_ID", win)
    A.session_start({"session_id": sid, "cwd": "/w", "transcript_path": ""})
    S.kv_set(DS.P.mirror_log(sid), "ask-pending",
              {"tool_use_id": tid, "questions": questions})


_ASK_1S = [{"question": "Which fruit?", "header": "Fruit", "multiSelect": False,
            "options": [{"label": "Apple", "description": "crisp"},
                        {"label": "Banana", "description": "soft"},
                        {"label": "Cherry", "description": "tart"}]}]


_ASK_2Q = [{"question": "Pick a planet", "header": "Planet",
            "multiSelect": False,
            "options": [{"label": "Mars"}, {"label": "Venus"}]},
           {"question": "Pick metals", "header": "Metals", "multiSelect": True,
            "options": [{"label": "Iron"}, {"label": "Copper"},
                        {"label": "Zinc"}]}]


def test_post_answer_single_label(dash, monkeypatch):
    # one single-select question: cursor+Enter selects AND submits (no review)
    fe = _AskFE(_ASK_1S)
    _ask_env(monkeypatch, "ask1", "41", fe, _ASK_1S)
    code, body = _post(dash + "/api/session/ask1/answer",
                       {"tool_use_id": "toolu_a1",
                        "answers": [{"selected": ["Banana"], "other": ""}]})
    assert code == 200 and json.loads(body) == {"ok": True, "chat": False}
    assert fe.submitted == {"Which fruit?": "Banana"}


def test_post_answer_two_questions_mixed(dash, monkeypatch):
    # the live-verified shape: single label + multiSelect labels + custom
    # text, driven through the review pane ("1. Submit answers")
    fe = _AskFE(_ASK_2Q)
    _ask_env(monkeypatch, "ask2", "42", fe, _ASK_2Q)
    code, body = _post(dash + "/api/session/ask2/answer",
                       {"tool_use_id": "toolu_a1",
                        "answers": [{"selected": ["Venus"], "other": ""},
                                    {"selected": ["Iron", "Zinc"],
                                     "other": "titanium"}]})
    assert code == 200
    assert fe.submitted == {"Pick a planet": "Venus",
                            "Pick metals": "Iron, Zinc, titanium"}


# the exact failing shape of session 3fd325d9 (2026-07-22): a MIDDLE
# multiSelect answered with a custom "other", followed by a THIRD question —
# the pane must advance PAST the multiSelect to reach question 3. The old
# blind `right` advance was eaten by the custom-text row's edit focus, so
# question 3 "never became current"; _advance_multi uses the "Next" row.
_ASK_3Q_MID_MULTI = [
    {"question": "Teleport where?", "header": "Teleport", "multiSelect": False,
     "options": [{"label": "Beach"}, {"label": "City"}]},
    {"question": "Which snacks? (pick any)", "header": "Snacks",
     "multiSelect": True,
     "options": [{"label": "Coffee"}, {"label": "Fruit"}]},
    {"question": "Pick a superpower — or type your own.", "header": "Power",
     "multiSelect": False,
     "options": [{"label": "Flight"}, {"label": "Teleportation"}]}]


def test_post_answer_middle_multiselect_custom_advances(dash, monkeypatch):
    fe = _AskFE(_ASK_3Q_MID_MULTI)
    _ask_env(monkeypatch, "ask8", "48", fe, _ASK_3Q_MID_MULTI)
    code, body = _post(dash + "/api/session/ask8/answer",
                       {"tool_use_id": "toolu_a1",
                        "answers": [{"selected": ["City"], "other": ""},
                                    {"selected": ["Fruit"], "other": "test"},
                                    {"selected": ["Flight"], "other": ""}]})
    assert code == 200 and json.loads(body) == {"ok": True, "chat": False}
    assert fe.submitted == {"Teleport where?": "City",
                            "Which snacks? (pick any)": "Fruit, test",
                            "Pick a superpower — or type your own.": "Flight"}


def test_post_answer_recovers_dialog_stuck_midflow(dash, monkeypatch):
    # the 3fd325d9 RETRY: left/right/Tab don't switch questions in this build,
    # so a dialog already sitting on a LATER question (a prior half-answer, or
    # a terminal-side answer) cannot be walked back to question 1. The old
    # `left`×len normalize no-oped and the first wait bailed "question 1 never
    # became current"; the forward-only drive answers from the CURRENT question
    # instead, recovering it. Q1 keeps whatever already set it (no back-nav).
    fe = _AskFE(_ASK_3Q_MID_MULTI)
    fe.single[0] = "City"          # Q1 already answered (in the terminal)
    fe.tab = 2                     # dialog stuck on Q3
    _ask_env(monkeypatch, "ask9", "49", fe, _ASK_3Q_MID_MULTI)
    code, body = _post(dash + "/api/session/ask9/answer",
                       {"tool_use_id": "toolu_a1",
                        "answers": [{"selected": ["City"], "other": ""},
                                    {"selected": ["Fruit"], "other": ""},
                                    {"selected": ["Teleportation"], "other": ""}]})
    assert code == 200 and json.loads(body) == {"ok": True, "chat": False}
    # Q3 answered from the web; Q1 kept its terminal value; Q2 never became
    # current so it stays empty (the dialog was already past it)
    assert fe.submitted["Pick a superpower — or type your own."] == "Teleportation"
    assert fe.submitted["Teleport where?"] == "City"


_ASK_LONGQ = [{"question": "Which rename mechanism should the dashboard "
               "use? The research doc (docs/session-naming-findings.md) "
               "found two channels: appending a record to the session's "
               "transcript JSONL (works for live AND parked sessions, but "
               "the live kitty tab title won't change until next resume), "
               "or typing the command into the live TUI like the composer "
               "does (fully native, but only works for live sessions).",
               "header": "Mechanism",
               "multiSelect": False,
               "options": [{"label": "JSONL append"}, {"label": "TUI"}]},
              {"question": "Where should the rename affordance live?",
               "header": "Placement", "multiSelect": False,
               "options": [{"label": "Header"}, {"label": "Cards"}]}]


def test_post_answer_wrapped_long_question(dash, monkeypatch):
    # the live 2026-07-18 bail: a 555-char question WRAPS across screen
    # lines, and the old exact line-set match never saw it become current
    # ("question 1 never became current" at step `question`)
    fe = _AskFE(_ASK_LONGQ)
    _ask_env(monkeypatch, "ask7", "47", fe, _ASK_LONGQ)
    code, _ = _post(dash + "/api/session/ask7/answer",
                    {"tool_use_id": "toolu_a1",
                     "answers": [{"selected": ["JSONL append"], "other": ""},
                                 {"selected": ["Header"], "other": ""}]})
    assert code == 200
    assert fe.submitted == {_ASK_LONGQ[0]["question"]: "JSONL append",
                            _ASK_LONGQ[1]["question"]: "Header"}


def test_post_answer_multi_diffs_against_screen(dash, monkeypatch):
    # Enter TOGGLES the cursored box — boxes the user pre-checked in the
    # terminal must be reconciled (unwanted ones toggled OFF), never re-flipped
    fe = _AskFE(_ASK_2Q)
    fe.single[0] = "Mars"          # Q1 already answered in the TUI
    fe.tab = 1                     # dialog sitting on Q2
    fe.checks[1] = {"Copper"}      # an unwanted pre-toggle
    _ask_env(monkeypatch, "ask3", "43", fe, _ASK_2Q)
    code, _ = _post(dash + "/api/session/ask3/answer",
                    {"tool_use_id": "toolu_a1",
                     "answers": [{"selected": ["Mars"], "other": ""},
                                 {"selected": ["Zinc"], "other": ""}]})
    assert code == 200
    assert fe.submitted == {"Pick a planet": "Mars", "Pick metals": "Zinc"}


def test_post_answer_chat_about_this(dash, monkeypatch):
    fe = _AskFE(_ASK_1S)
    _ask_env(monkeypatch, "ask4", "44", fe, _ASK_1S)
    code, body = _post(dash + "/api/session/ask4/answer",
                       {"tool_use_id": "toolu_a1", "chat": True})
    assert code == 200 and json.loads(body) == {"ok": True, "chat": True}
    assert fe.chatted and fe.submitted is None


def test_post_answer_chat_delivers_message(dash, monkeypatch):
    """A TYPED answer on a preview-layout question is routed by the card through
    'Chat about this' AND carries the typed text as `message` — the server
    dismisses the dialog then delivers the text as a normal message so the
    custom answer reaches the session (docs/dashboard.md, *Web ask*)."""
    fe = _AskFE(_ASK_1S)
    _ask_env(monkeypatch, "askc", "47", fe, _ASK_1S)
    code, body = _post(dash + "/api/session/askc/answer",
                       {"tool_use_id": "toolu_a1", "chat": True,
                        "message": "figure out which ones are available"})
    assert code == 200 and json.loads(body)["message_sent"] is True
    assert fe.chatted
    assert fe.pasted == [("47", "figure out which ones are available")]


def test_post_answer_free_text_single(dash, monkeypatch):
    fe = _AskFE(_ASK_1S)
    _ask_env(monkeypatch, "ask5", "45", fe, _ASK_1S)
    code, _ = _post(dash + "/api/session/ask5/answer",
                    {"tool_use_id": "toolu_a1",
                     "answers": [{"selected": [], "other": "oolong tea"}]})
    assert code == 200
    assert fe.submitted == {"Which fruit?": "oolong tea"}


def test_post_answer_guards(dash, monkeypatch):
    # stale/missing stash and a wrong answers count are refused BEFORE any
    # key is pressed; a stash without a dialog on screen bails at "open"
    fe = _AskFE(_ASK_1S)
    _ask_env(monkeypatch, "ask6", "46", fe, _ASK_1S)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/ask6/answer",
              {"tool_use_id": "toolu_WRONG", "answers": []})
    assert e.value.code == 409 and "expired" in json.loads(e.value.read())["error"]
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/ask6/answer",
              {"tool_use_id": "toolu_a1", "answers": []})
    assert e.value.code == 400
    assert fe.keyed == [] and fe.submitted is None
    fe.open = False                       # dialog dismissed in the terminal
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/ask6/answer",
              {"tool_use_id": "toolu_a1",
               "answers": [{"selected": ["Apple"], "other": ""}]})
    assert e.value.code == 409
    assert json.loads(e.value.read())["step"] == "open"
    # no pending stash at all
    S.kv_del(DS.P.mirror_log("ask6"), "ask-pending")
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/ask6/answer",
              {"tool_use_id": "toolu_a1", "answers": []})
    assert e.value.code == 409
    assert "no pending" in json.loads(e.value.read())["error"]


# real screen captures (live session 2026-07-18) the askdialog parsers pin
_ASK_MULTI_SCREEN = """\
❯ a scrollback prompt echo at column 0
────
←  ☒ Toppings  ✔ Submit  →

Which toppings?

❯ 1. [ ] Cheese
  Melted cheese topping
  2. [✔] Olives
  Sliced black or green olives
  3. [ ] Onions
  Diced or sliced onions
  4. [✔] Peppers
  Bell or chili peppers
  5. [ ] Type something
     Submit
────
  6. Chat about this

Enter to select · ↑/↓ to navigate · Esc to cancel"""


_ASK_REVIEW_SCREEN = """\
←  ☒ Pets  ☒ Drink  ✔ Submit  →

Review your answers

 ● Cats or dogs?
   → Cats
 ● Tea or coffee?
   → Coffee

Ready to submit your answers?

❯ 1. Submit answers
  2. Cancel"""


# real v2.1.215 capture (2026-07-19): the SIDE-BY-SIDE preview layout an ask
# with option `preview`s renders — a box bleeds onto the option lines (rows()
# must strip it), a "Notes: press n" hint row is NOT a cursor stop, and "Chat
# about this" is UNNUMBERED (it carried a digit in the classic layout)
_ASK_PREVIEW_SCREEN = """\
────
←  ☒ Reappear trigger  ☐ Unhide UI  ✔ Submit  →

How do you want to unhide a directory manually / see what's hidden?

❯ 1. Collapsed 'Hidden (N)'       ┌──────────────────────────────────────────┐
    strip                         │ ───────────────                          │
  2. No manual UI needed          │ Hidden (2)                               │
                                  │   baqylau        ↩                       │
                                  └──────────────────────────────────────────┘

                                  Notes: press n to add notes

────
  Chat about this

Enter to select · ↑/↓ to navigate · n to add notes · Tab to switch questions · Esc to cancel"""


def test_askdialog_parsers_pin_the_real_screens():
    AD = DS.askdialog
    assert AD.dialog_open(_ASK_MULTI_SCREEN)
    assert not AD.review_open(_ASK_MULTI_SCREEN)
    rs = AD.rows(_ASK_MULTI_SCREEN)
    assert [(r["digit"], r["label"], r["check"]) for r in rs] == [
        ("1", "Cheese", False), ("2", "Olives", True),
        ("3", "Onions", False), ("4", "Peppers", True),
        ("5", "Type something", False), ("", "Submit", None),
        ("6", "Chat about this", None)]
    assert [r["cursor"] for r in rs] == [True] + [False] * 6
    qs = [{"question": "Which toppings?"}, {"question": "Other thing?"}]
    assert AD.current_question(_ASK_MULTI_SCREEN, qs) == 0
    # a long question WRAPS across screen lines — flattened match (the live
    # "question 1 never became current" bail, 2026-07-18)
    wrapped = _ASK_MULTI_SCREEN.replace(
        "Which toppings?",
        "Which toppings should the kitchen put on\nthe pizza tonight?")
    long_qs = [{"question": "Which toppings should the kitchen put on the "
                            "pizza tonight?"}]
    assert AD.current_question(wrapped, long_qs) == 0
    # the review pane's answer recap repeats the question texts — it must
    # still read as "no current question"
    review_qs = [{"question": "Cats or dogs?"}, {"question": "Tea or coffee?"}]
    assert AD.current_question(_ASK_REVIEW_SCREEN, review_qs) is None
    # the column-0 scrollback echo is outside the chip-bar region
    assert "scrollback" not in AD.region(_ASK_MULTI_SCREEN)
    assert AD.review_open(_ASK_REVIEW_SCREEN)
    assert not AD.dialog_open(_ASK_REVIEW_SCREEN)
    assert AD.current_question(_ASK_REVIEW_SCREEN, qs) is None
    assert not AD.dialog_open("❯ composer\n  -- INSERT --")
    # the side-by-side preview layout: labels stripped of the bled-in box, the
    # "Notes" hint dropped, and an UNNUMBERED "Chat about this" row surfaced
    assert AD.dialog_open(_ASK_PREVIEW_SCREEN)
    prs = AD.rows(_ASK_PREVIEW_SCREEN)
    assert [(r["digit"], r["label"]) for r in prs] == [
        ("1", "Collapsed 'Hidden (N)'"), ("2", "No manual UI needed"),
        ("", "Chat about this")]
    assert [r["cursor"] for r in prs] == [True, False, False]
    pv_qs = [{"question": "When should a hidden directory reappear on the "
                          "main page?"},
             {"question": "How do you want to unhide a directory manually / "
                          "see what's hidden?"}]
    assert AD.current_question(_ASK_PREVIEW_SCREEN, pv_qs) == 1


def test_askdialog_typed_answer_fails_fast_without_type_row():
    """The preview layout has no numbered "Type something" row, so a typed
    ('other') answer is undeliverable — the driver must fail FAST with step
    "type" instead of walking the cursor forever ("cursor never reached Type
    row", 2026-07-19). The web card routes typed answers via chat instead."""
    AD = DS.askdialog
    fe = _FakeFE()
    fe.screens = [_ASK_PREVIEW_SCREEN]
    # 2 options → the (absent) Type row would be digit 3
    with pytest.raises(AD.AskError) as e:
        AD._require_type_row(fe, "1", "3")
    assert e.value.step == "type"
    # a present option digit is fine (no raise)
    AD._require_type_row(fe, "1", "2")


def test_cursor_to_reaches_chat_in_two_cursor_preview_layout():
    """The preview layout bleeds the last option's ❯ onto the "Chat about this"
    row below it, so with the cursor genuinely ON Chat, BOTH rows render ❯
    (verified live 2026-07-20 — down from the last option lands on Chat). The
    old _cursor_to read only the FIRST cursor row (the option) and dead-looped
    ("cursor never reached Chat row"); checking EVERY cursored row fixes it
    without breaking option targeting (the down-from-top walk stops at the clean
    single-❯ option before descending into the two-❯ state)."""
    AD = DS.askdialog

    class _PreviewNavFE:
        # rows: options 1..3 then an unnumbered "Chat about this"; idx 3 = Chat.
        # On Chat, the LAST option (idx-2 row) ALSO shows ❯ — the render bleed.
        def __init__(self):
            self.idx = 2                       # start on the last option

        def send_key(self, win, *keys):
            for k in keys:
                if k == "down":
                    self.idx = min(3, self.idx + 1)
                elif k == "up":
                    self.idx = max(0, self.idx - 1)
            return True

        def get_text(self, win, extent="screen"):
            labels = ["Hide all", "Keep", "Keep stop"]
            lines = [" ☐ Q ", ""]
            for i, lb in enumerate(labels):
                on = self.idx == i or (self.idx == 3 and i == 2)   # bleed
                lines.append(("❯ " if on else "  ") + "%d. %s" % (i + 1, lb))
            lines += ["────",
                      ("❯ " if self.idx == 3 else "  ") + "Chat about this",
                      "Enter to select · ↑/↓ to navigate · Esc to cancel"]
            return "\n".join(lines)

    def nul(*_a, **_k):
        return None
    fe = _PreviewNavFE()
    screen = AD._cursor_to(fe, "1", lambda r: r["label"] == AD.CHAT_LABEL,
                           nul, "Chat row")
    assert fe.idx == 3                                     # landed on Chat
    assert any(r["label"] == "Chat about this" and r["cursor"]
               for r in AD.rows(screen))
    # option targeting still stops at the clean option row, NOT over into Chat
    fe2 = _PreviewNavFE()
    AD._cursor_to(fe2, "1", AD._by_digit("3"), nul, "opt 3")
    assert fe2.idx == 2                                    # option 3, not Chat


class _PlanFE(_FakeFE):
    """_FakeFE plus a reactive simulation of the ExitPlanMode approval dialog
    (live captures 2026-07-18): "Would you like to proceed?" + numbered rows,
    where a decision digit selects immediately, the "Tell Claude what to
    change" digit only FOCUSES its editable row (typed text + CR rejects with
    feedback), and Escape rejects outright."""

    OPTIONS = ("Yes, and bypass permissions", "Yes, manually approve edits",
               "No, refine with Ultraplan on Claude Code on the web",
               "Tell Claude what to change")

    def __init__(self, options=OPTIONS):
        super().__init__()
        self.options = list(options)
        self.open = True
        self.cursor = 0
        self.decided = None
        self.fb = None

    def send_key(self, win, *keys):
        ok = super().send_key(win, *keys)
        for k in keys:
            if not self.open:
                continue
            if k == "escape":
                self.decided, self.open = "esc", False
            elif k.isdigit() and 1 <= int(k) <= len(self.options):
                label = self.options[int(k) - 1]
                if label.startswith("Tell Claude"):
                    self.cursor = int(k) - 1          # focus, not select
                else:
                    self.decided, self.open = label, False
        return ok

    def send_text(self, win, text):
        ok = super().send_text(win, text)
        if self.open and self.options[self.cursor].startswith("Tell Claude"):
            self.fb, self.open = text, False
        return ok

    def get_text(self, win, extent="screen"):
        if not self.open:
            return "❯ composer\n  -- INSERT --"
        rows = ["scrollback noise", "",
                "   Claude has written up a plan and is ready to execute. "
                "Would you like to proceed?", ""]
        for i, o in enumerate(self.options):
            rows.append(("   ❯ " if i == self.cursor else "     ")
                        + "%d. %s" % (i + 1, o))
            if o.startswith("Tell Claude"):
                rows.append("        shift+tab to approve with this feedback")
        return "\n".join(rows)


_PLAN_PEND = {"tool_use_id": "toolu_p1", "plan": "# Plan\n1. do the thing",
              "planFilePath": "/tmp/plan.md"}


def _plan_env(monkeypatch, sid, win, fe):
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS.plandialog, "POLL_S", 0.01)
    monkeypatch.setenv("KITTY_WINDOW_ID", win)
    A.session_start({"session_id": sid, "cwd": "/w", "transcript_path": ""})
    S.kv_set(DS.P.mirror_log(sid), "plan-pending", dict(_PLAN_PEND))


def test_post_plan_options_reads_live_labels(dash, monkeypatch):
    # the option labels vary with the session's permission mode, so the card
    # fetches them from the LIVE screen — read-only, no key pressed
    fe = _PlanFE()
    _plan_env(monkeypatch, "pl1", "51", fe)
    code, body = _post(dash + "/api/session/pl1/plan-options",
                       {"tool_use_id": "toolu_p1"})
    assert code == 200
    opts = json.loads(body)["options"]
    assert [o["label"] for o in opts] == list(_PlanFE.OPTIONS)
    assert [o["feedback"] for o in opts] == [False, False, False, True]
    assert fe.keyed == [] and fe.decided is None


def test_post_plan_decide_verifies_the_label(dash, monkeypatch):
    fe = _PlanFE()
    _plan_env(monkeypatch, "pl2", "52", fe)
    # label drift (the dialog was replaced since the page fetched options):
    # refused, nothing pressed
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/pl2/plan-decision",
              {"tool_use_id": "toolu_p1", "digit": "1",
               "label": "Yes, and auto-accept edits"})
    assert e.value.code == 409
    assert json.loads(e.value.read())["step"] == "option"
    assert fe.decided is None
    # matching label: pressed, dialog resolves
    code, body = _post(dash + "/api/session/pl2/plan-decision",
                       {"tool_use_id": "toolu_p1", "digit": "2",
                        "label": "Yes, manually approve edits"})
    assert code == 200 and json.loads(body) == {"ok": True, "kind": "decide"}
    assert fe.decided == "Yes, manually approve edits"


def test_post_plan_feedback_and_dismiss(dash, monkeypatch):
    fe = _PlanFE()
    _plan_env(monkeypatch, "pl3", "53", fe)
    code, body = _post(dash + "/api/session/pl3/plan-decision",
                       {"tool_use_id": "toolu_p1",
                        "feedback": "shorter\nplease"})
    assert code == 200 and json.loads(body)["kind"] == "feedback"
    # newlines collapse — the row is a single-line editor and a raw CR
    # mid-text would submit early
    assert fe.fb == "shorter please"
    # a second dialog: dismiss = the TUI's own Esc reject
    fe2 = _PlanFE()
    _plan_env(monkeypatch, "pl4", "54", fe2)
    code, body = _post(dash + "/api/session/pl4/plan-decision",
                       {"tool_use_id": "toolu_p1", "dismiss": True})
    assert code == 200 and json.loads(body)["kind"] == "dismiss"
    assert fe2.decided == "esc"


def test_post_plan_guards_and_open_bail_heals(dash, monkeypatch):
    fe = _PlanFE()
    _plan_env(monkeypatch, "pl5", "55", fe)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/pl5/plan-decision",
              {"tool_use_id": "toolu_STALE", "dismiss": True})
    assert e.value.code == 409
    assert "expired" in json.loads(e.value.read())["error"]
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/pl5/plan-decision",
              {"tool_use_id": "toolu_p1"})
    assert e.value.code == 400
    # dialog resolved in the terminal → `open` bail 409 AND the stash is
    # self-healed so the page's card clears on the next SSE tick
    fe.open = False
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/pl5/plan-options",
              {"tool_use_id": "toolu_p1"})
    assert e.value.code == 409
    assert json.loads(e.value.read())["step"] == "open"
    assert S.kv_at(DS.P.state_db(DS.P.mirror_log("pl5")),
                   "plan-pending") is None
    # …and with the stash gone the next call is a clean "no pending plan"
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/pl5/plan-options",
              {"tool_use_id": "toolu_p1"})
    assert "no pending" in json.loads(e.value.read())["error"]


# the real captured plan dialog (live session 2026-07-18) the parsers pin
_PLAN_SCREEN = """\
   Here is Claude's plan:
  ╌╌╌╌╌╌╌╌
   Plan: Create /private/tmp/plan-test.txt

   Steps

   1. Write /private/tmp/plan-test.txt with the content PLANNED.
   2. Verify with cat /private/tmp/plan-test.txt.
  ╌╌╌╌╌╌╌╌

   Claude has written up a plan and is ready to execute. Would you like to proceed?

   ❯ 1. Yes, and bypass permissions
     2. Yes, manually approve edits
     3. No, refine with Ultraplan on Claude Code on the web
     4. Tell Claude what to change
        shift+tab to approve with this feedback

   ctrl+g to edit in Vim · ~/.config/plans/make-a-tiny-plan.md"""


def test_plandialog_parsers_pin_the_real_screen():
    PD = DS.plandialog
    assert PD.dialog_open(_PLAN_SCREEN)
    rs = PD.rows(_PLAN_SCREEN)
    # the plan's own numbered STEPS are above the proceed anchor — they must
    # not parse as decision rows (the region starts at the anchor)
    assert [(r["digit"], r["label"], r["feedback"]) for r in rs] == [
        ("1", "Yes, and bypass permissions", False),
        ("2", "Yes, manually approve edits", False),
        ("3", "No, refine with Ultraplan on Claude Code on the web", False),
        ("4", "Tell Claude what to change", True)]
    assert [r["cursor"] for r in rs] == [True, False, False, False]
    assert not PD.dialog_open("❯ composer\n  -- INSERT --")


def test_post_message_clear_draft_kills_then_pastes(dash, monkeypatch):
    # resending an edited message after a mid-turn cancel-edit: the TUI still
    # holds the restored draft, so clear_draft kills the line (ctrl+u to
    # start + ctrl+k to end) and delivers the text as a BRACKETED PASTE
    # (paste_text) — a raw send here drops leading bytes (the measured mangle)
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setattr(DS.config, "DRAFT_CLEAR_GAP_S", 0)
    monkeypatch.setenv("KITTY_WINDOW_ID", "71")
    A.session_start({"session_id": "cd1", "cwd": "/w", "transcript_path": ""})
    code, body = _post(dash + "/api/session/cd1/message",
                       {"text": "edited message", "clear_draft": True})
    assert code == 200 and json.loads(body)["ok"] is True
    assert fe.keyed == [("71", ("ctrl+u",)), ("71", ("ctrl+k",))]
    assert fe.pasted == [("71", "edited message")]    # atomic paste, not send
    assert fe.sent == []
    # a normal send also pastes (atomic), but with NO kill keys first
    fe.keyed.clear(); fe.pasted.clear()
    _post(dash + "/api/session/cd1/message", {"text": "plain"})
    assert fe.keyed == []
    assert fe.pasted == [("71", "plain")] and fe.sent == []


def test_post_interrupt_refuses_stale_or_missing_window(dash, monkeypatch):
    # same live-tag discipline as stop/message: an Escape into a reused
    # window id would interrupt an unrelated session
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.setenv("KITTY_WINDOW_ID", "12")
    A.session_start({"session_id": "intr2", "cwd": "/w", "transcript_path": ""})
    fe.wins["intr2"] = None                   # the claude_session tag is gone
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/intr2/interrupt", {})
    assert e.value.code == 409
    assert fe.keyed == []


def test_post_stop_no_window_is_409(dash, monkeypatch):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)   # headless session
    A.session_start({"session_id": "stop2", "cwd": "/w", "transcript_path": ""})
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/stop2/stop", {})
    assert e.value.code == 409
    assert fe.closed == []


def test_account_registry_and_alias(tmp_path, monkeypatch):
    from plugins.claude_code import account as ACC
    tsv = tmp_path / "accounts.tsv"
    tsv.write_text("c1\toboard\tsvc-1\nc2\tclaude-01\tsvc-2\n")
    monkeypatch.setattr(ACC, "ACCOUNTS_TSV", str(tsv))
    reg = ACC.registry()
    # no synthetic "default" — the plain-claude login duplicates a real account
    assert [a["slug"] for a in reg] == ["c1", "c2"]
    assert {"slug": "c2", "label": "claude-01", "alias": "c2"} in reg
    assert ACC.alias_for("c1") == "c1"
    # empty/claude still resolve to plain claude (the server's absent-account
    # fallback), even though the picker no longer offers them
    assert ACC.alias_for("") == "claude" and ACC.alias_for("claude") == "claude"
    assert ACC.alias_for("nope") is None          # unknown → caller 400s
    monkeypatch.setenv("CLAUDE_SUBSCRIPTION_SLUG", "c2")
    monkeypatch.setenv("CLAUDE_SUBSCRIPTION_LABEL", "claude-01")
    assert ACC.current() == {"slug": "c2", "label": "claude-01"}
    monkeypatch.delenv("CLAUDE_SUBSCRIPTION_SLUG", raising=False)
    monkeypatch.delenv("CLAUDE_SUBSCRIPTION_LABEL", raising=False)
    assert ACC.current() == {"slug": "", "label": "default"}


def test_statusline_shim_captures_and_delegates(tmp_path, monkeypatch):
    # the shim stashes account + usage into an EXISTING state DB, normalizes a
    # ms reset to seconds, and never creates the DB when it's absent
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    from core import hostpane as HP
    from plugins.claude_code import statusline as SL
    monkeypatch.setenv("CLAUDE_SUBSCRIPTION_SLUG", "c2")
    monkeypatch.setenv("CLAUDE_SUBSCRIPTION_LABEL", "claude-01")
    payload = {"session_id": "slcap", "rate_limits": {
        "five_hour": {"used_percentage": 10.6, "resets_at": 1784304000},
        "seven_day": {"used_percentage": 23, "resets_at": 1784500000000},
        # a model-scoped window (none exist as of CLI 2.1.215 — this is the
        # forward contract) is captured generically; garbage entries are not
        "seven_day_fable": {"used_percentage": 81, "resets_at": 1784500000},
        "Bad Key!": {"used_percentage": 50},
        "no_pct": {"resets_at": 1784500000},
        "not_a_dict": 7}}
    raw = json.dumps(payload).encode()
    log = P.mirror_log("slcap")
    SL.capture(raw)                              # no DB yet → must be a no-op
    assert not os.path.isfile(P.state_db(log))   # (kv_get would CREATE it — don't)
    HP.ensure_db(log)
    SL.capture(raw)
    assert S.kv_get(log, "account") == {"slug": "c2", "label": "claude-01"}
    u = S.kv_get(log, "usage")
    assert u["five_hour"] == 11 and u["seven_day"] == 23        # rounded pct
    assert u["seven_day_reset"] == 1784500000.0                 # ms → s
    # the model window rode along; the garbage entries did not
    assert u["seven_day_fable"] == 81
    assert u["seven_day_fable_reset"] == 1784500000.0
    assert "Bad Key!" not in u and "no_pct" not in u and "not_a_dict" not in u
    # account-wide pair first, then model windows (the bar display order —
    # dict order survives the kv's json round-trip)
    wins = [k for k in u if isinstance(u[k], int)]
    assert wins == ["five_hour", "seven_day", "seven_day_fable"]
    # a payload with no rate_limits leaves the last good usage in place
    SL.capture(json.dumps({"session_id": "slcap"}).encode())
    assert S.kv_get(log, "usage")["five_hour"] == 11
    # delegate runs with the same stdin and its exit code is returned
    assert SL.run(["sh", "-c", "cat >/dev/null; exit 3"], raw) == 3
    assert SL.run([], raw) == 0                                 # bare shim → 0


def _set_started(sid, ts, ended=None):
    """Stamp a controlled started_at (and optional ended_at) onto a seeded
    session row — session_start records wall-clock, but the heatmap/punch/window
    buckets need deterministic timestamps."""
    import sqlite3
    conn = sqlite3.connect(A.db_path())
    conn.execute("UPDATE sessions SET started_at=?, ended_at=? WHERE session_id=?",
                 (ts, ended, sid))
    conn.commit()
    conn.close()


def test_stats_payload_aggregates_cross_session(dash, monkeypatch):
    """GET /api/stats: whole-corpus aggregates (stats_payload over
    sessionapi.activity_stats). Sessions are the unit; per-project grouping,
    per-window pulse counts, daily heatmap buckets, and the day×hour punch card
    all fold from the audit sessions/otel/errors tables."""
    monkeypatch.setattr(DS.config, "STATS_TTL_S", 0)          # defeat the wall-clock memo
    now = time.time()
    # three sessions in /proj/alpha (one 40d old → outside the 7d/30d windows),
    # one in /proj/beta; one alpha session still open (no ended_at).
    seed = [("stA1", "/proj/alpha", now - 1 * 3600, now),         # today, ended
            ("stA2", "/proj/alpha", now - 2 * 86400, None),       # 2d ago, active
            ("stA3", "/proj/alpha", now - 40 * 86400, now),       # 40d ago, ended
            ("stB1", "/proj/beta",  now - 1 * 86400, now)]        # 1d ago, ended
    for sid, cwd, st, en in seed:
        A.session_start({"session_id": sid, "cwd": cwd, "transcript_path": ""})
        _set_started(sid, st, en)
    # stA2 is GENUINELY live (a /tmp state DB), not merely ended_at=NULL — the
    # pulse `active` counts real liveness (sessions_payload), so an open row
    # without a live DB (a stranded crash/kill) would NOT count.
    S.incr(P.mirror_log("stA2"), commands=1)
    # tokens + cost land on one alpha session
    A.otel("stA1", [{"metric": "token", "query_source": "main", "type": "input",
                     "value": 1000},
                    {"metric": "token", "query_source": "main", "type": "output",
                     "value": 500},
                    {"metric": "cost", "query_source": "main", "type": "", "value": 0.25}])
    A.error("stB1", "boom", {"where": "test"})          # one error under beta

    DS.lists._STATS_AGG.clear()                 # bypass the wall-clock memo
    d = _get_json(dash + "/api/stats")
    assert d["total_sessions"] == 4
    # windows: all=4, 30d=3 (drops the 40d-old alpha), 7d=3
    assert d["windows"]["all"]["sessions"] == 4
    assert d["windows"]["30d"]["sessions"] == 3
    assert d["windows"]["7d"]["sessions"] == 3
    # active = genuinely-live sessions (stA2 in every window that includes it)
    assert d["windows"]["7d"]["active"] == 1
    assert d["windows"]["30d"]["active"] == 1
    assert d["windows"]["all"]["active"] == 1
    assert d["windows"]["all"]["ended"] == 3
    # token/cost totals (summed across the otel rows)
    assert d["windows"]["all"]["tokens"] == 1500
    assert abs(d["windows"]["all"]["cost"] - 0.25) < 1e-9
    assert d["windows"]["all"]["errors"] == 1
    # per-project grouping (basename of the group_dir); alpha has 3, beta 1
    by = {p["name"]: p for p in d["projects"]}
    assert by["alpha"]["sessions"] == 3 and by["beta"]["sessions"] == 1
    assert by["alpha"]["tokens"] == 1500 and abs(by["alpha"]["cost"] - 0.25) < 1e-9
    assert by["beta"]["errors"] == 1
    # top-projects bar list in the pulse window, ranked by sessions
    top = d["windows"]["all"]["projects"]
    assert top[0]["name"] == "alpha" and top[0]["sessions"] == 3
    # heatmap daily buckets + punch-card triples are well-formed
    assert d["daily"] and all(len(x) == 2 and x[1] >= 1 for x in d["daily"])
    assert d["punch"] and all(0 <= dow <= 6 and 0 <= hr <= 23 and n >= 1
                              for dow, hr, n in d["punch"])


def test_accounts_payload_aggregates_usage(dash, monkeypatch, tmp_path):
    # /api/accounts returns the registry + newest usage per account slug
    monkeypatch.setattr(DS.plugins, "accounts", lambda: [
        {"slug": "", "label": "default", "alias": "claude"},
        {"slug": "c2", "label": "claude-01", "alias": "c2"}])
    A.session_start({"session_id": "accs1", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("accs1")
    S.kv_set(log, "account", {"slug": "c2", "label": "claude-01"})
    S.kv_set(log, "usage", {"five_hour": 40, "seven_day": 55,
                            "seven_day_fable": 80, "ts": 100})
    rows = _get_json(dash + "/api/accounts")
    by = {r["slug"]: r for r in rows}
    # the served usage is EFFECTIVE (sessionapi.effective_usage): ts=100 is
    # ancient with no resets → every window rolled over → zeroed (the
    # model-scoped fable window exactly like the account-wide pair), so a
    # stale snapshot can never render its old % with a 'resets now' countdown
    assert by["c2"]["usage"]["five_hour"] == 0
    assert by["c2"]["usage"]["seven_day"] == 0
    assert by["c2"]["usage"]["seven_day_fable"] == 0
    assert by[""]["usage"] is None                 # default has no captured usage
    # server-computed effective 5h and the limit-hit flag (none)
    assert by["c2"]["five_hour_eff"] == 0
    assert by["c2"]["limit_hit"] is None and by[""]["limit_hit"] is None


def test_accounts_payload_serves_fresh_eff_and_limit_hit(dash, monkeypatch):
    monkeypatch.setattr(DS.plugins, "accounts", lambda: [
        {"slug": "c1", "label": "oboard", "alias": "c1"}])
    A.session_start({"session_id": "accs2", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("accs2")
    now = time.time()
    S.kv_set(log, "account", {"slug": "c1", "label": "oboard"})
    S.kv_set(log, "usage", {"five_hour": 95, "five_hour_reset": now + 8000,
                            "ts": now})
    S.kv_set(log, "limit-hit", {"slug": "c1", "ts": now,
                                "resets_at": now + 8000, "msg": "limit"})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    assert by["c1"]["five_hour_eff"] == 95         # un-rolled → face value
    assert by["c1"]["limit_hit"]["msg"] == "limit"  # active stamp is served
    # an EXPIRED stamp is dropped from the payload (the pill must clear)
    S.kv_set(log, "limit-hit", {"slug": "c1", "ts": now - 9000,
                                "resets_at": now - 10, "msg": "old"})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    assert by["c1"]["limit_hit"] is None


def test_accounts_payload_serves_sched_signals(dash, monkeypatch):
    # the new-session picker's load-balancing signals ride the payload:
    # sched_score (weekly-quota perishability) + sched_ok (5h safety gate).
    monkeypatch.setattr(DS.plugins, "accounts", lambda: [
        {"slug": "c1", "label": "oboard", "alias": "c1"},
        {"slug": "c2", "label": "claude-01", "alias": "c2"}])
    now = time.time()
    for sid, slug, five, seven, reset in [
            ("scd1", "c1", 40, 30, now + 6 * 3600),      # quota left, resets soon
            ("scd2", "c2", 40, 30, now + 5 * 86400)]:    # same, resets far off
        A.session_start({"session_id": sid, "cwd": "/w", "transcript_path": ""})
        log = P.mirror_log(sid)
        S.kv_set(log, "account", {"slug": slug, "label": slug})
        S.kv_set(log, "usage", {"five_hour": five, "five_hour_reset": now + 8000,
                                "seven_day": seven, "seven_day_reset": reset,
                                "ts": now})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    # both clear the 5h gate; the soon-resetting account is more perishable
    assert by["c1"]["sched_ok"] is True and by["c2"]["sched_ok"] is True
    assert by["c1"]["sched_score"] > by["c2"]["sched_score"]


def test_accounts_payload_files_limit_hit_under_its_own_slug(dash, monkeypatch):
    # After a rate-limit migration the adopted session runs under the NEW
    # account (its `account` kv), but the limit-hit stamp in the same state DB
    # still describes the OLD one — it must surface on the blocked account's
    # pill, not the healthy one's (and the migration target picker keys off
    # the same aggregation).
    monkeypatch.setattr(DS.plugins, "accounts", lambda: [
        {"slug": "c1", "label": "oboard", "alias": "c1"},
        {"slug": "c2", "label": "claude-01", "alias": "c2"}])
    A.session_start({"session_id": "accs3", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("accs3")
    now = time.time()
    S.kv_set(log, "account", {"slug": "c1", "label": "oboard"})
    S.kv_set(log, "usage", {"five_hour": 10, "five_hour_reset": now + 8000,
                            "ts": now})
    S.kv_set(log, "limit-hit", {"slug": "c2", "ts": now, "model": "fable",
                                "resets_at": now + 8000, "msg": "limit"})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    assert by["c1"]["limit_hit"] is None           # the healthy account is clean
    assert by["c2"]["limit_hit"]["msg"] == "limit"  # the blocked one shows it
    assert by["c2"]["limit_hit"]["model"] == "fable"  # scope rides through
    assert by["c1"]["usage"]["five_hour"] == 10    # usage stays with the session


def test_accounts_payload_flags_a_logged_out_account(dash, monkeypatch):
    # A session on c1 died on error='authentication_failed' → relimit's
    # `logged-out` stamp. The payload flags the account (the dashboard's ⚠ badge
    # + the new-session auto-select skip); a usage snapshot more than
    # LOGGED_OUT_GRACE_S newer (a re-login `/login` session) clears it — the
    # dying session's own post-turn snapshot does not. docs/dashboard.md
    # *Logged-out accounts*.
    from core import sessionapi as SAPI
    monkeypatch.setattr(DS.plugins, "accounts", lambda: [
        {"slug": "c1", "label": "oboard", "alias": "c1"}])
    A.session_start({"session_id": "accs_lo", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("accs_lo")
    now = time.time()
    S.kv_set(log, "account", {"slug": "c1", "label": "oboard"})
    # the dying session's OWN status-line render, a beat AFTER the failed turn
    S.kv_set(log, "usage", {"five_hour": 10, "five_hour_reset": now + 8000,
                            "ts": now + 1.3})
    S.kv_set(log, "logged-out", {"slug": "c1", "ts": now + 1, "msg": "run /login"})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    assert by["c1"]["logged_out"] is True
    assert by["c1"]["logged_out_msg"] == "run /login"
    # a re-login: a snapshot past the grace margin supersedes the stamp → clears
    S.kv_set(log, "usage", {"five_hour": 10, "five_hour_reset": now + 8000,
                            "ts": now + SAPI.LOGGED_OUT_GRACE_S + 5})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    assert by["c1"]["logged_out"] is False
    assert by["c1"]["logged_out_msg"] is None


def test_accounts_payload_pegs_5h_to_100_on_account_wide_limit(dash, monkeypatch):
    # The reported bug: a c2 session hit its 5h SESSION limit and MIGRATED to c1,
    # so c2's state DB was re-stamped to c1 and c2's freshest snapshot is a
    # STALE, pre-limit capture (25% / 98 min old). Under an active ACCOUNT-WIDE
    # limit-hit the 5h bar must read 100% (the truth), not the frozen snapshot —
    # presentation-only, mirroring the model-scoped override. docs/dashboard.md.
    monkeypatch.setattr(DS.plugins, "accounts", lambda: [
        {"slug": "c2", "label": "claude-01", "alias": "c2"}])
    A.session_start({"session_id": "accs_5h", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("accs_5h")
    now = time.time()
    S.kv_set(log, "account", {"slug": "c2", "label": "claude-01"})
    # a stale, pre-limit snapshot: 5h and 7d both low (and coincidentally equal)
    S.kv_set(log, "usage", {"five_hour": 25, "five_hour_reset": now + 8000,
                            "seven_day": 25, "seven_day_reset": now + 400000,
                            "ts": now - 5000})
    S.kv_set(log, "limit-hit", {"slug": "c2", "ts": now, "model": None,
                                "resets_at": now + 8000, "msg": "session limit"})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    u = by["c2"]["usage"]
    assert u["five_hour"] == 100                    # pegged to the truth
    assert u["five_hour_reset"] == pytest.approx(now + 8000, abs=5)  # the limit's reset
    assert u["seven_day"] == 25                     # 7d untouched (only 5h is capped)
    assert by["c2"]["limit_hit"]["msg"] == "session limit"  # the chip still shows
    # a MODEL-scoped limit does NOT peg 5h (only that model is capped)
    S.kv_set(log, "limit-hit", {"slug": "c2", "ts": now, "model": "fable",
                                "resets_at": now + 8000, "msg": "fable limit"})
    u = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}["c2"]["usage"]
    assert u["five_hour"] == 25                     # untouched — Opus/Sonnet still run


def test_accounts_payload_merges_model_windows(dash, monkeypatch):
    # The per-model weekly windows (plugins.model_windows — the OAuth /usage
    # fetch) are MERGED into each account's usage alongside the tokenless 5h/7d
    # snapshot, so the generic bar renderer paints a third bar. five_hour_eff
    # keeps keying off the tokenless snapshot, never the merged-in window.
    monkeypatch.setattr(DS.plugins, "accounts", lambda: [
        {"slug": "c1", "label": "oboard", "alias": "c1"},
        {"slug": "c2", "label": "claude-01", "alias": "c2"}])
    monkeypatch.setattr(DS.plugins, "model_windows", lambda cache=None: {
        "c1": {"seven_day_fable": 91, "seven_day_fable_reset": time.time() + 8000},
        "c2": {"seven_day_fable": 100, "seven_day_fable_reset": time.time() + 8000}})
    A.session_start({"session_id": "accs4", "cwd": "/w", "transcript_path": ""})
    now = time.time()
    S.kv_set(P.mirror_log("accs4"), "account", {"slug": "c1", "label": "oboard"})
    S.kv_set(P.mirror_log("accs4"), "usage",
             {"five_hour": 14, "five_hour_reset": now + 8000,
              "seven_day": 62, "seven_day_reset": now + 8000, "ts": now})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    # c1 has a captured snapshot → third bar rides alongside the account-wide pair
    assert by["c1"]["usage"]["seven_day_fable"] == 91
    assert by["c1"]["usage"]["five_hour"] == 14
    assert by["c1"]["five_hour_eff"] == 14         # from the tokenless snapshot
    # c2 has NO captured snapshot, only the fetched model window → still shown
    assert by["c2"]["usage"]["seven_day_fable"] == 100


def test_accounts_payload_live_window_clears_model_limit_hit(dash, monkeypatch):
    # A MODEL-scoped limit-hit stamp has no reset epoch, so limit_hit_active
    # assumes a week of blockage — but the live per-model window is the fresher
    # truth: below 100% means the cap cleared (Anthropic mid-week resets), so
    # the pill drops; AT 100% the stamp stays.
    monkeypatch.setattr(DS.plugins, "accounts", lambda: [
        {"slug": "c2", "label": "claude-01", "alias": "c2"}])
    win = {"c2": {"seven_day_fable": 100,
                  "seven_day_fable_reset": time.time() + 8000}}
    monkeypatch.setattr(DS.plugins, "model_windows", lambda cache=None: win)
    A.session_start({"session_id": "accs5", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("accs5")
    now = time.time()
    S.kv_set(log, "account", {"slug": "c2", "label": "claude-01"})
    S.kv_set(log, "limit-hit", {"slug": "c2", "ts": now - 3600,
                                "model": "fable", "msg": "fable limit"})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    assert by["c2"]["limit_hit"]["msg"] == "fable limit"   # 100% → still blocked
    win["c2"]["seven_day_fable"] = 3                       # the cap reset mid-week
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    assert by["c2"]["limit_hit"] is None                   # live window wins
    assert by["c2"]["usage"]["seven_day_fable"] == 3
    # a NON-model (session-wide) stamp is never touched by model windows
    S.kv_set(log, "limit-hit", {"slug": "c2", "ts": now,
                                "resets_at": now + 8000, "msg": "5h limit"})
    by = {r["slug"]: r for r in _get_json(dash + "/api/accounts")}
    assert by["c2"]["limit_hit"]["msg"] == "5h limit"


def test_post_new_session_account_picker(dash, monkeypatch, tmp_path):
    from plugins.claude_code import account as ACC
    tsv = tmp_path / "accounts.tsv"
    tsv.write_text("c1\toboard\tsvc-1\nc2\tclaude-01\tsvc-2\n")
    monkeypatch.setattr(ACC, "ACCOUNTS_TSV", str(tsv))
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    # a known slug launches via its alias command word (c2 "$@")
    _post(dash + "/api/sessions/new", {"cwd": str(tmp_path), "account": "c2"})
    argv = fe.launched[-1][1]
    assert argv[2] == 'c2 "$@"' and argv[3] == "c2"
    # default / absent → plain claude
    _post(dash + "/api/sessions/new", {"cwd": str(tmp_path)})
    assert fe.launched[-1][1][3] == "claude"
    # an unknown account is 400, never launched
    n = len(fe.launched)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/sessions/new", {"cwd": str(tmp_path), "account": "evil; rm"})
    assert e.value.code == 400
    assert len(fe.launched) == n


def test_session_payload_carries_account_and_usage(dash, monkeypatch):
    monkeypatch.setenv("KITTY_WINDOW_ID", "88")
    A.session_start({"session_id": "acsess", "cwd": "/w", "transcript_path": ""})
    log = P.mirror_log("acsess")
    S.kv_set(log, "account", {"slug": "c1", "label": "oboard"})
    S.kv_set(log, "usage", {"five_hour": 5, "seven_day": 9, "ts": 1})
    ov = _get_json(dash + "/api/session/acsess")
    assert ov["account"] == {"slug": "c1", "label": "oboard"}
    assert ov["usage"]["seven_day"] == 9


def test_post_migrate_spawns_the_manual_migrator(dash, monkeypatch, tmp_path):
    """The header's ⇆ migrate button: POST /api/session/<sid>/migrate picks
    the other account (manual → no % ceiling) and spawns the relimit migrator
    in mode=manual. 409 when the registry holds no other account."""
    from plugins.claude_code import account as ACC
    tsv = tmp_path / "accounts.tsv"
    tsv.write_text("c1\toboard\tsvc-1\nc2\tclaude-01\tsvc-2\n")
    monkeypatch.setattr(ACC, "ACCOUNTS_TSV", str(tsv))
    A.session_start({"session_id": "migs1", "cwd": "/w", "transcript_path": ""})
    S.kv_set(P.mirror_log("migs1"), "account", {"slug": "c1", "label": "oboard"})
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    spawned = []
    monkeypatch.setattr(
        DS.SP, "spawn_detached",
        lambda path, argv, log, **kw: spawned.append((path, list(argv), kw))
        or object())
    code, body = _post(dash + "/api/session/migs1/migrate", {})
    assert code == 200 and json.loads(body) == {"ok": True, "to": "c2"}
    path, argv, kw = spawned[0]
    assert path.endswith("claude-relimit.py")
    # trailing "" is the model rung (empty here — no transcript model → keep the
    # current model; the ladder downgrade path is covered in test_l2_relimit)
    assert argv[1:] == ["migs1", "c2", "c2", "/w", "manual", ""]
    assert kw["purpose"] == "relimit:c2 (web)"
    # the success row carries the pick trace (chosen target + reasoning)
    migs = [c for (s, c) in _sf_rows_full("web-migrate") if s == "migs1"]
    assert migs[-1]["ok"] is True and migs[-1]["pick"]["chosen"]["slug"] == "c2"
    # no other account in the registry → 409, nothing spawned
    tsv.write_text("c1\toboard\tsvc-1\n")
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/migs1/migrate", {})
    assert e.value.code == 409 and len(spawned) == 1
    # the REFUSAL is now reconstructible — a `pick` trace naming every account
    # weighed and why (the subtle gap the automatic `relimit-pick` closed, now
    # on the manual ⇆ path too), not a bare "no target".
    migs = [c for (s, c) in _sf_rows_full("web-migrate") if s == "migs1"]
    assert migs[-1]["ok"] is False and migs[-1]["reason"] == "no target"
    assert migs[-1]["pick"]["chosen"] is None
    assert any(cand["slug"] == "c1" for cand in migs[-1]["pick"]["candidates"])
    # a sid this machine has never seen → 404, nothing spawned (the migrator
    # can't tell "parked" from "never existed" — an unknown sid would launch
    # a doomed --resume tab)
    tsv.write_text("c1\toboard\tsvc-1\nc2\tclaude-01\tsvc-2\n")
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/00000000-0000-0000-0000-000000000000"
                     "/migrate", {})
    assert e.value.code == 404 and len(spawned) == 1


def test_post_new_session_resume_continue(dash, monkeypatch, tmp_path):
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    sid = "85065b28-d9ea-4861-b209-bbc871e57357"
    _post(dash + "/api/sessions/new",
          {"cwd": str(tmp_path), "resume": sid, "prompt": "go on"})
    assert fe.launched[-1][1][4:] == ["--resume", sid, "go on"]
    _post(dash + "/api/sessions/new",
          {"cwd": str(tmp_path), "continue": True, "model": "opus"})
    assert fe.launched[-1][1][4:] == ["--continue", "--model", "opus"]
    # continue: false is a no-flag no-op, not an error
    _post(dash + "/api/sessions/new", {"cwd": str(tmp_path), "continue": False})
    assert fe.launched[-1][1][4:] == []
    # invalid: bad resume id / non-bool continue / both at once → 400, no launch
    n = len(fe.launched)
    for bad in ({"resume": "x y; z"}, {"resume": 7}, {"continue": "yes"},
                {"resume": sid, "continue": True}):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/sessions/new", dict({"cwd": str(tmp_path)}, **bad))
        assert e.value.code == 400
    assert len(fe.launched) == n


def test_post_new_session_refuses_resume_of_live_session(dash, monkeypatch,
                                                         tmp_path):
    """A resume-launch for a sid that ALREADY has a live tab is refused (409),
    not launched a second time — the duplicate-tab / two-processes-on-one-
    transcript guard. A stale page can resume-launch a live session; the
    server backstops it. The refusal lands a web-launch ok:False row carrying
    the live window so the page can focus/message it instead."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    sid = "16fdc14a-b64f-4243-8885-8888aaaa0e03a"
    fe.wins[sid] = "413"                       # simulate a live claude_session tag
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/sessions/new",
              {"cwd": str(tmp_path), "resume": sid, "prompt": "hi"})
    assert e.value.code == 409
    assert fe.launched == []                   # nothing was launched
    # a fresh (non-resume) launch in the same dir is unaffected
    _post(dash + "/api/sessions/new", {"cwd": str(tmp_path), "prompt": "new"})
    assert fe.launched and fe.launched[-1][1][-1] == "new"


def test_post_new_session_refuses_resume_of_missing_transcript(dash, monkeypatch,
                                                               tmp_path):
    """A resume-launch for a session whose transcript .jsonl is GONE is refused
    (410), not launched into a tab that would instantly die (`claude --resume`
    finds no conversation). The refusal lands a web-launch ok:False row with
    why=transcript missing. A sid with a PRESENT transcript resumes normally,
    and an UNKNOWN sid (no audit row / no known path) is left to the CLI."""
    fe = _FakeFE()
    _inject_fe(monkeypatch, fe)
    # session_start stamps kitty_window_id from the env; clear it so the seeded
    # rows don't read as live (window_for_session) and trip the 409 live guard.
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
    gone_sid = "9c1e2f34-aaaa-4bbb-8ccc-0123456789ab"
    A.session_start({"session_id": gone_sid, "cwd": str(tmp_path),
                     "transcript_path": str(tmp_path / "gone.jsonl")})  # never written
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/sessions/new",
              {"cwd": str(tmp_path), "resume": gone_sid, "prompt": "hi"})
    assert e.value.code == 410
    assert fe.launched == []                    # nothing was launched
    # a session WITH a present transcript resumes normally
    tp = _tw(tmp_path, "there.jsonl", {"type": "user", "message": {"content": "hi"}})
    ok_sid = "9c1e2f34-aaaa-4bbb-8ccc-0123456789ac"
    A.session_start({"session_id": ok_sid, "cwd": str(tmp_path), "transcript_path": tp})
    _post(dash + "/api/sessions/new",
          {"cwd": str(tmp_path), "resume": ok_sid, "prompt": "go"})
    assert fe.launched and fe.launched[-1][1][4:] == ["--resume", ok_sid, "go"]
    # an UNKNOWN sid (no row) is NOT pre-rejected — the CLI decides
    unk = "9c1e2f34-aaaa-4bbb-8ccc-0123456789ad"
    _post(dash + "/api/sessions/new",
          {"cwd": str(tmp_path), "resume": unk, "prompt": "x"})
    assert fe.launched[-1][1][4:] == ["--resume", unk, "x"]


def test_launch_argv_falls_back_to_zsh(monkeypatch):
    monkeypatch.setenv("SHELL", "/opt/homebrew/bin/fish")   # no POSIX "$@"
    assert DS.launch_argv([])[0] == "/bin/zsh"
    monkeypatch.delenv("SHELL", raising=False)
    assert DS.launch_argv([])[0] == "/bin/zsh"


def test_slash_commands_discovery(tmp_path, monkeypatch):
    from plugins.claude_code import slashcmds
    proj = tmp_path / "proj"
    (proj / ".claude" / "commands" / "gh").mkdir(parents=True)
    (proj / ".claude" / "commands" / "deploy.md").write_text(
        "---\ndescription: ship it\n---\nbody\n")
    (proj / ".claude" / "commands" / "gh" / "fix.md").write_text(
        "Fix a GitHub issue\n")
    skill = proj / ".claude" / "skills" / "audit-debug"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: audit-debug\ndescription: triage the audit\n---\n")
    user = tmp_path / "userclaude"
    (user / "commands").mkdir(parents=True)
    (user / "commands" / "deploy.md").write_text(
        "---\ndescription: user-level deploy\n---\n")
    (user / "commands" / "standup.md").write_text("# Daily standup notes\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user))
    # env pinning must NOT redirect an arbitrary-cwd lookup (env_pin=False):
    # the dashboard resolves OTHER sessions' cwds, whatever spawned it
    other = tmp_path / "other" / ".claude" / "commands"
    other.mkdir(parents=True)
    (other / "pinned.md").write_text("must not appear\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "other"))
    cmds = {c["name"]: c for c in slashcmds.slash_commands(str(proj))}
    assert cmds["compact"]["src"] == "built-in"
    assert cmds["deploy"] == {"name": "deploy", "desc": "ship it",
                              "src": "project"}    # project shadows user
    assert cmds["gh:fix"]["desc"] == "Fix a GitHub issue"   # namespaced, first line
    assert cmds["audit-debug"] == {"name": "audit-debug",
                                   "desc": "triage the audit",
                                   "src": "project skill"}
    assert cmds["standup"] == {"name": "standup",
                               "desc": "Daily standup notes", "src": "user"}
    assert "pinned" not in cmds
    names = [c["name"] for c in slashcmds.slash_commands(str(proj))]
    assert names == sorted(names)
    # no cwd → built-ins + user-level only (no getcwd fallback walk)
    cmds = {c["name"]: c for c in slashcmds.slash_commands("")}
    assert "standup" in cmds and "deploy" in cmds and "gh:fix" not in cmds
    assert cmds["deploy"]["desc"] == "user-level deploy"


def test_http_commands(dash, tmp_path, monkeypatch):
    # cwd-keyed (not sid-keyed): the new-session form completes for a
    # directory that has no session yet
    from urllib.parse import quote
    proj = tmp_path / "cproj"
    (proj / ".claude" / "commands").mkdir(parents=True)
    (proj / ".claude" / "commands" / "ship.md").write_text(
        "---\ndescription: ship\n---\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-such-claude"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    rows = _get_json(dash + "/api/commands?cwd=" + quote(str(proj)))
    byname = {c["name"]: c for c in rows}
    assert byname["ship"]["src"] == "project"
    assert byname["compact"]["src"] == "built-in"
    # a non-directory cwd degrades to built-ins (+ user-level), never an error
    for q in ("?cwd=/no/such/dir", ""):
        rows = _get_json(dash + "/api/commands" + q)
        assert any(c["name"] == "compact" for c in rows)
        assert not any(c["name"] == "ship" for c in rows)


def test_notifier_ignores_windowless_transitions(monkeypatch):
    n = DS.Notifier()
    n.winmap = {}                             # no session known for the window
    q = n.register()
    seq = [{"9": "working"}, {"9": "awaiting-command"}]
    monkeypatch.setattr(DS.API, "tab_states", lambda: seq.pop(0))
    n.scan(); n.scan()
    assert q.empty()
