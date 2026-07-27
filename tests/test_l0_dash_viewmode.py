# tests/test_l0_dash_viewmode.py — L0 dashboard: the view modes (verbose / default / focus).
#
# One subject out of the former 8468-line L0 dashboard monolith; the
# shared HTTP/audit helpers live in tests/dashkit.py and the in-process
# server fixture (`dash`) in tests/conftest.py.
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest
from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

import core.audit as A
from core import ops as O
from dashboard import opshtml
from dashboard import prefs
from dashboard import server as DS


# ------------------------------------------------------------------ opshtml
from dashkit import (_get, _get_json, _post, _state_rows)


def _lbl(text, rgb, **kw):
    return O.label(text, rgb, **kw)


def _css_rules(css):
    """The stylesheet as {selector: body}, keyed by the whole selector LIST *and* by
    each selector in it — a rule two registers share (`.stream > .blk[data-note],
    .stream > .blk[data-quiet] { … }`, the agent note's box and the quiet command
    header's, which must not drift apart) is then findable by either name."""
    out = {}
    for sels, body in re.findall(r"\n(\.[^\n{]+?)\s*\{([^}]*)\}", css):
        for key in [sels] + sels.split(","):
            out.setdefault(key.strip(), body)
    return out


def test_actclass_classifies_main_session_blocks():
    """The block-opening chip names the activity. The GLYPH alone can't: a
    subagent launch header is also `▶ …`, so `▶` means a shell command only in
    one of the semantic command colours (SLATE/ORANGE/RED) — a palette colour is
    an agent. A finish chip (`■`) names NO class: it closes a block whose class
    its opening chip already gave."""
    from core import slots
    from dashboard.opshtml import actclass as AC
    assert AC.classify(_lbl("▶ foreground", O.SLATE, g="t1")) == ("bash", False)
    assert AC.classify(_lbl("▷ background", O.ORANGE, g="t2")) == ("bg", False)
    assert AC.classify(_lbl("◉ monitor · npm", slots.color("monitor", 1))) \
        == ("monitor", False)
    # the ▶ ambiguity, both ways
    assert AC.classify(_lbl("▶ general-purpose · hunt", slots.color("sub", 0))) \
        == ("agent", False)
    assert AC.classify(_lbl("↻ Explore · sweep", slots.color("sub", 2))) \
        == ("agent", False)
    # a finish chip contributes only its outcome
    assert AC.classify(_lbl("■ finished · 3.2s", O.SLATE, g="t1")) == (None, False)
    # body ops inherit their block's class
    assert AC.classify(O.code("git status", g="t1")) == (None, False)
    assert AC.classify(O.gut("out", O.SLATE, g="t1")) == (None, False)


def test_actclass_reads_failure_from_the_semantic_colour():
    """`bad` is read STRUCTURALLY, from the shared semantic colours the
    producers paint with (ops.RED / ops.ORANGE), never from the chip's words —
    so a reworded chip can't silently stop reddening a collapsed run's dot. RED
    is a failure anywhere; ORANGE only on a FINISH chip (it is also the
    slot-less background header colour, which is no outcome at all)."""
    from core import slots
    from dashboard.opshtml import actclass as AC
    assert AC.classify(_lbl("■ failed (exit 1) · 0.4s", O.RED, g="t1")) == (None, True)
    assert AC.classify(_lbl("■ interrupted · 2.0s", O.ORANGE, g="t1")) == (None, True)
    assert AC.classify(_lbl("■ finished · 1s", O.SLATE, g="t1")) == (None, False)
    # ORANGE as a BG HEADER is not an outcome (the glyph is what separates them)
    assert AC.classify(_lbl("▷ background", O.ORANGE, g="t2")) == ("bg", False)
    # a monitor's red failure chip reddens its run too
    assert AC.classify(_lbl("■ monitor died", O.RED, g="m1"))[1] is True
    assert AC.classify(_lbl("◉ monitor", slots.color("monitor", 0)))[1] is False


def test_actclass_file_one_liners_and_diffstat():
    """File ops classify by the VERB, taken from its owner (tools.FILE_LABEL) —
    Read folds away in default mode while Update/Write stay visible, so the
    distinction is load-bearing. A mutation's `+A -R` is read here too (focus
    mode's edit summary sums them), off the op rather than out of rendered HTML,
    and a failed op is red with no counts to claim."""
    from core import streamfmt as SF
    from dashboard.opshtml import actclass as AC
    read = O.line(SF.file_line("Read", "mirror.py", O.BLUE, extent="1-40"), view="v1")
    upd = O.line(SF.file_line("Update", "ops.py", O.YELLOW, added=12, removed=3))
    wr = O.line(SF.file_line("Write", "new.py", O.GREEN, added=40))
    assert AC.classify(read) == ("read", False)
    assert AC.classify(upd) == ("edit", False)
    assert AC.classify(wr) == ("write", False)
    assert AC.diffstat(upd) == (12, 3)
    assert AC.diffstat(wr) == (40, 0)
    assert AC.diffstat(O.line(SF.file_line("Update", "a.py", O.YELLOW, removed=4))) \
        == (0, 4)
    assert AC.diffstat(read) == (0, 0)
    # a digit-bearing FILENAME is not a count (the parse anchors on the paren)
    assert AC.diffstat(O.line(SF.file_line(
        "Update", "app.05-session.js", O.YELLOW, added=7, removed=2))) == (7, 2)
    # a FAILED read: red verb at the head of the line, still classified
    assert AC.classify(O.line(SF.file_line("Read", "gone.py", O.BLUE,
                                           failed=True))) == ("read", True)


def test_actclass_team_mail_and_task_rows_have_their_own_classes():
    """Team mail and task rows are neither agents nor monitors, and reading them
    as either is not cosmetic: `◉ read · …` fell into the MONITOR class, so a
    lead session's focus summary announced "watched 7 monitors" that never
    existed. Both are keyed on their PRODUCER's glyphs (imported, not respelled),
    with `◉` disambiguated by colour exactly as `▶` is."""
    from core import slots
    from dashboard.opshtml import actclass as AC
    from plugins.claude_code import msgs as MSGS
    from plugins.claude_code import task_fmt as TASKS

    mail_sent = MSGS.sent_ops("lead", "rev-ui", "check the diff", "the diff is at…",
                              "m1", None)
    mail_new = MSGS.event_ops([("new", "lead", "rev-ui", "check the diff")])
    mail_read = MSGS.event_ops([("read", "lead", "rev-ui", "")])
    assert [AC.classify(o)[0] for o in mail_sent] == ["mail", None]  # chip + body
    assert AC.classify(mail_new[0]) == ("mail", False)
    assert AC.classify(mail_read[0]) == ("mail", False)
    # a substream's OWN ✉ chip wears a slot palette and is that agent's block, not
    # the session's mail — the same colour gate the ◉ ambiguity uses
    assert AC.classify(_lbl("✉ rev-ui → lead", slots.color("sub", 1)))[0] != "mail"
    # …and the ◉ ambiguity, both ways: mail wears the semantic colour, a monitor
    # block's chip wears its slot's palette
    assert AC.classify(_lbl("◉ monitor · npm", slots.color("monitor", 1))) \
        == ("monitor", False)

    for glyph in TASKS.GLYPHS:
        assert AC.classify(_lbl(glyph + " task #3 · ship it", O.AMBER)) \
            == ("task", False)


def test_a_groupless_body_op_inherits_the_row_it_follows():
    """"A body op inherits its block's class" has an edge the classifier alone
    cannot serve: a body op with NO `g` has no block — it lands as a top-level row
    of its own, unclassifiable, therefore never collapsible, therefore visible in
    every mode however strict. Team mail is exactly that shape (a `●` chip
    followed by the message body as a bare gutter), which is how a teammate's
    message text sat in the middle of focus mode. op_items resolves it against the
    item it follows — the only block it has."""
    from dashboard import opshtml
    from plugins.claude_code import msgs as MSGS

    # …a mail row and its message with no group between them (history's shape, and
    # any un-logged producer's) are GROUPED here instead — see the synthetic-group
    # test below — so the body is inside the block and needs no class of its own
    items = opshtml.op_items(MSGS.sent_ops("lead", "rev-ui", "the report",
                                           "please send your final report", "m1"))
    assert [it.get("act") for it in items] == ["mail", None]
    assert items[0]["g"] and items[0]["g"] == items[1]["g"]
    # …and a RUN of bodies behind one chip all land in that block (one message's text
    # is one op today; a second bare gutter is its continuation, not a feed row)
    chain = opshtml.op_items(MSGS.sent_ops("lead", "rev-ui", "s", "a", "m1")
                             + [O.gut("b", MSGS.MSG_NEW_RGB)])
    assert [it.get("act") for it in chain] == ["mail", None, None]
    assert chain[2]["g"] == chain[0]["g"], "a run of bodies stays in its block"
    # a GROUPED body still inherits from its own block, not from a neighbour…
    grouped = opshtml.op_items([_lbl("▶ foreground", O.SLATE, g="t1"),
                                O.gut("out", O.SLATE, g="t1")])
    assert [it.get("act") for it in grouped] == ["bash", None]
    # …and a body op with nothing before it stays unclassified (fail toward
    # showing, never inherit out of thin air)
    assert opshtml.op_items([O.gut("orphan", O.SLATE)])[0].get("act") is None


def test_a_mail_message_is_ONE_block_and_survives_a_per_op_render(dash, tmp_path):
    """Two halves of the same bug, and the reason the first fix didn't hold.

    The inheritance above only works WITHIN one op_items call — and the two render
    paths called it ONE OP AT A TIME, so it never fired in production while the
    unit test (a batch) passed. A teammate's report-delivery summary therefore
    still sat in the middle of focus mode with its own header hidden. So: the
    render paths batch consecutive ops, AND — the real fix — a message's chip and
    body share a copy-group at the source, which needs no adjacency at all.
    Only pre-grouping HISTORY leans on the inheritance."""
    from core import hostpane as HP
    from dashboard.read import mirror as M
    from plugins.claude_code import msgs as MSGS

    log = str(tmp_path / "claude-mirror-mail.log")
    HP.ensure_db(log)                      # a state DB to allocate the group from
    ops = MSGS.sent_ops("rev-observe", "team-lead", "4 BUGs",
                        "[to main] 4 BUGs, one high", "m1", log)
    assert len({o.get("g") for o in ops}) == 1 and ops[0].get("g"), \
        "the chip and its message are one block"
    # the poller's rows are one line each — no body, so no group is spent on them
    poll = MSGS.event_ops([("new", "a", "b", "s", "hi", "m1"),
                           ("read", "a", "b", "", "", "m1")], log)
    assert len(poll) == 2 and not any(o.get("g") for o in poll)

    # GROUPED, the body needs no class of its own: it is inside the block, whose
    # class comes from the chip — so it hides with it, whatever the render path
    # does or how the window happens to be cut
    live = M.merge_live(ops, [])
    assert [it.get("act") for it in live] == ["mail", None]
    assert live[0]["g"] == live[1]["g"] == ops[0]["g"]

    # LEGACY history (ops written before the grouping) is grouped by the READ side
    # instead (a synthetic `mail:<id>` — see the test below), which is why both render
    # paths must still BATCH consecutive ops: the lookahead that decides it, like the
    # inheritance before it, cannot see past one call
    old = MSGS.sent_ops("rev-observe", "team-lead", "4 BUGs", "[to main] 4 BUGs", "m1")
    assert not any(o.get("g") for o in old)
    merged = M.merge_live(old, [])
    assert [it.get("act") for it in merged] == ["mail", None]
    assert merged[0]["g"] == merged[1]["g"] and merged[0]["g"].startswith("mail:")
    window = M._render_window([(1, "op", old[0]), (2, "op", old[1])], 0, "")
    assert [it.get("act") for it in window] == ["mail", None]
    assert window[0]["g"] == window[1]["g"] == "mail:1"
    # a conversation record BETWEEN them still flushes the run — a message is no
    # op's block, and inheriting `msg` would make a mail body conversation text
    split = M._render_window([(1, "op", old[0]),
                              (1, "msg", {"kind": "message", "text": "hi"}),
                              (2, "op", old[1])], 0, "")
    assert [it.get("act") for it in split] == ["mail", "msg", None]


def test_actclass_warning_light_is_its_own_class():
    """The audit warning light's `⚠ audit: …` one-liner must never be swallowed
    by a collapse, so it classifies as its own act (which no mode folds)."""
    from core import render as R
    from dashboard.opshtml import actclass as AC
    from dashboard.opshtml.actclass import ACT_WARN
    line = O.line(R.DIM + "⚠ audit: claude-cmd-fmt.py: KeyError" + R.RST)
    assert AC.classify(line) == (ACT_WARN, False)


def test_actclass_never_raises_and_fails_toward_showing():
    """A classification gap answers (None, False) — "not collapsible" — so junk
    can only ever leave content VISIBLE, never hide it."""
    from dashboard.opshtml import actclass as AC
    for junk in ({}, {"t": "label"}, {"t": "line", "s": None},
                 {"t": "label", "s": 5, "c": "nope"}, {"t": "weird", "s": "x"}):
        assert AC.classify(junk) == (None, False)
        assert AC.diffstat(junk) == (0, 0)


def test_op_items_carry_act_bad_and_diffstat():
    """The stream items the page renders carry the classification — `act`, `bad`
    and (for mutations) `add`/`rem` — so the client never re-sniffs it out of the
    HTML it was handed."""
    from core import streamfmt as SF
    items = opshtml.op_items([
        O.label("▶ foreground", O.SLATE, g="g1"),
        O.code("ls", g="g1"),
        O.label("■ failed · 1s", O.RED, g="g1"),
        O.line(SF.file_line("Update", "x.py", O.YELLOW, added=5, removed=2)),
        O.line(SF.file_line("Read", "y.py", O.BLUE)),
    ], key="k")
    assert [it.get("act") for it in items] == ["bash", None, None, "edit", "read"]
    assert [it.get("bad") for it in items] == [None, None, 1, None, None]
    edit = items[3]
    assert (edit["add"], edit["rem"]) == (5, 2)
    assert "add" not in items[4] and "rem" not in items[4]   # a read has no counts


def test_conversation_items_carry_the_msg_act(dash):
    """Conversation text is stamped ACT_MSG with its `kind` beside it — focus
    mode narrows on the kind (prompts and each turn's final reply survive)."""
    from dashboard.read import mirror as M
    items = M.conv_items([
        {"kind": "prompt", "text": "hi", "ts": 1.0},
        {"kind": "message", "text": "yo", "ts": 2.0},
    ])
    assert [it["act"] for it in items] == [opshtml.ACT_MSG, opshtml.ACT_MSG]
    assert [it["kind"] for it in items] == ["prompt", "message"]


def test_view_mode_pref_is_per_session_and_defaults_to_default(dash):
    """The mode is stored per SESSION in the durable global prefs store, and an
    untouched session reads VIEW_DEFAULT — `default`, the mode Claude Code's own
    viewMode defaults to. Setting a session back to it DELETES the entry, so the
    map stays the small set of overridden sessions rather than one row per session
    ever opened."""
    assert prefs.view_mode("vm1") == "default" == prefs.VIEW_DEFAULT
    prefs.set_view_mode("vm1", "focus")
    assert prefs.view_mode("vm1") == "focus"
    assert prefs.view_mode("vm2") == "default"          # strictly per session
    prefs.set_view_mode("vm1", "verbose")               # an override IS stored…
    assert prefs.view_mode("vm1") == "verbose"
    assert prefs.get(prefs.VIEW_MODE_KEY, {}) == {"vm1": "verbose"}
    prefs.set_view_mode("vm1", "default")               # …and back is an absence
    assert prefs.get(prefs.VIEW_MODE_KEY, {}) == {}
    # junk in the store falls back to the default, never to a hidden-content mode
    prefs.set(prefs.VIEW_MODE_KEY, {"vm1": "nonsense"})
    assert prefs.view_mode("vm1") == prefs.VIEW_DEFAULT


def test_viewmode_endpoint_persists_serves_and_validates(dash):
    """POST /api/session/<sid>/viewmode stores the mode and the session payload
    serves it back (not live-gated — a parked session re-opens at the mode you
    left it in). A mode outside the vocabulary is a 400 input reject."""
    A.session_start({"session_id": "vmses", "cwd": "/w", "transcript_path": ""})
    assert _get_json(dash + "/api/session/vmses")["view_mode"] == "default"
    code, body = _post(dash + "/api/session/vmses/viewmode", {"mode": "verbose"})
    assert code == 200 and json.loads(body) == {"ok": True, "mode": "verbose"}
    assert _get_json(dash + "/api/session/vmses")["view_mode"] == "verbose"
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/vmses/viewmode", {"mode": "tiny"})
    assert e.value.code == 400
    assert _get_json(dash + "/api/session/vmses")["view_mode"] == "verbose"  # unchanged


def test_viewmode_endpoint_is_audited_and_guarded(dash, monkeypatch):
    """It is a control-plane write like every other: audited as a `web-viewmode`
    state_files row (the switch is invisible in the DB otherwise — "the dashboard
    was hiding my commands" needs a row saying who asked), behind _post_guard,
    and off in READONLY."""
    A.session_start({"session_id": "vmaud", "cwd": "/w", "transcript_path": ""})
    _post(dash + "/api/session/vmaud/viewmode", {"mode": "focus"})
    rows = _state_rows("web-viewmode")
    assert any(r.get("sid") == "vmaud" and r.get("mode") == "focus" for r in rows)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/vmaud/viewmode", {"mode": "focus"}, header=None)
    assert e.value.code == 403
    monkeypatch.setattr(DS.config, "READONLY", True)
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/session/vmaud/viewmode", {"mode": "focus"})
    assert e.value.code == 403


def test_act_vocabulary_matches_the_page_phrase_table(dash):
    """The page's fold sets + summary fragments are keyed by actclass.ACTS
    tokens. JS can't import the Python owner, so this is the seam that keeps the
    two halves honest: every act the page folds must have a fragment to be
    counted into (or map to one via VIEW_COUNTER), and no side may name a token
    the other doesn't know. A fold with no fragment collapses blocks into a line
    that says nothing about them."""
    from dashboard.opshtml.actclass import ACTS
    code, ses = _get(dash + "/static/app.05-session.js")
    assert code == 200

    def table(name):
        body = re.search(r"const %s = \{(.*?)\n\};" % name, ses, re.S).group(1)
        got = dict((m, set(re.findall(r'"([a-z-]+)"', b)))
                   for m, b in re.findall(r"\n  (verbose|default|focus): \[([^\]]*)\]",
                                          body))
        assert set(got) == {"verbose", "default", "focus"}, name
        return got

    folds = table("VIEW_FOLD")
    fragments = re.findall(r'\n  \["([a-z-]+)", "', ses)
    aliases = dict(re.findall(r'VIEW_COUNTER = \{ ([a-z]+): "([a-z-]+)" \}', ses))
    assert fragments and folds["default"] and not folds["verbose"]
    for mode, acts in folds.items():
        for act in acts:
            assert act in ACTS, "%s folds unknown act %r" % (mode, act)
            assert aliases.get(act, act) in fragments, \
                "%s folds %r with no summary fragment" % (mode, act)
    # the fragment keys are acts too (plus the two memory flavours, which are a
    # file op's act + the ❖ memory tag, not acts of their own)
    for key in fragments:
        assert key in ACTS or key in ("mem-read", "mem-write"), key

    # No act is ever dropped from the COUNTERS by a mode: whatever a mode collapses,
    # its summary still accounts for. Focus hid the team plumbing outright for a
    # while (no row, no fragment) and that was wrong on the summary's own terms —
    # a summary that omits work is a lie rather than a précis, and the rows it was
    # meant to suppress were being kept on screen by a CSS cascade bug anyway
    # (test_hiding_a_row_beats_its_own_layout_rule). Only INJECTED prompts are
    # dropped, and they are keyed on `data-injected`, never on an act.
    assert "VIEW_HIDE" not in ses, "a mode may collapse an act, never uncount it"
    # focus folds a SUPERSET of default: it is the stricter cut, always
    assert folds["default"] <= folds["focus"]
    # a MONITOR folds in DEFAULT too, asked for in those words ("also monitors should
    # be in the under summary in default mode") — while a BACKGROUND job still stands
    # there: it is work you are waiting on, whose output you came to read.
    assert "monitor" in folds["default"] and "bg" not in folds["default"]


def test_page_view_modes_match_the_pref_vocabulary(dash):
    """The three mode names are the wire vocabulary the endpoint validates
    against (prefs.VIEW_MODES) — the page must not invent a fourth, must list them
    in the same CONTROL order, and must agree on which one is the DEFAULT. The
    list order and the default are deliberately decoupled: the control reads
    densest-to-sparsest while an untouched session opens at `default`, so the page
    taking VIEW_MODES[0] for the default (as it first did) is now a bug."""
    code, ses = _get(dash + "/static/app.05-session.js")
    assert code == 200
    names = re.search(r"const VIEW_MODES = \[([^\]]*)\]", ses).group(1)
    assert tuple(re.findall(r'"([a-z]+)"', names)) == tuple(prefs.VIEW_MODES)
    page_default = re.search(r'const VIEW_DEFAULT = "([a-z]+)"', ses).group(1)
    assert page_default == prefs.VIEW_DEFAULT == "default"
    assert "VIEW_MODES[0]" not in ses, "the default is not the first mode"


def test_page_reads_the_served_act_instead_of_sniffing_glyphs(dash):
    """The page used to classify a block by regexing the mirror's chip GLYPHS
    (`CMD_GLYPH = /^\\s*[▶▷◉■]/`) out of the rendered HTML it had just been
    handed. That table now has one owner, server-side (opshtml/actclass.py), and
    the page reads its `act` stamp — so the glyph vocabulary must not reappear
    in the client."""
    code, index = _get(dash + "/")
    assert code == 200
    for part in sorted(set(re.findall(r"/static/(app\.\d\d-[a-z]+\.js)", index))):
        code, body = _get(dash + "/static/" + part)
        assert code == 200
        assert "CMD_GLYPH" not in body, part
        assert "▶▷◉" not in body, "%s re-encodes the chip glyph table" % part


def test_secondary_tab_sections_are_one_engine(tmp_path):
    """Monitors and background jobs, EXECUTED rather than grepped:
    tests/jsdom/sections.js drives the real SECTIONS engine in
    app.11-chrome.js over the shared DOM shim.

    They used to be fourteen near-identical function pairs 200 lines apart —
    sortedMonitors/sortedJobs byte-identical but for a parameter name, and the
    SECONDARY_POLL_MS constant already unified with a comment saying the rest had
    been written twice. Folding them onto one descriptor is only safe if BOTH
    still render what they rendered, and no Python test executes this file; a
    grep cannot catch a jobs grid that says "no monitors in this session", a
    breadcrumb pointing at the other list, or a poll left ticking for a section
    with nothing live. Skipped without `node` (docs/testing.md)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("no node on PATH")
    r = subprocess.run(
        [node, os.path.join(REPO, "tests", "jsdom", "sections.js"),
         os.path.join(REPO, "dashboard", "static", "app.11-chrome.js")],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)

    assert d["kinds"] == ["monitors", "jobs", "memory"]
    # each grid renders its own cards, in the shared order: live first, then
    # most-recently-started
    for kind, glyph in (("monitors", "◉"), ("jobs", "◷")):
        g = d["grids"][kind]
        assert g["cards"] == 2, kind
        assert g["order"][0].endswith("live"), (kind, g["order"])
        assert g["text"].startswith(glyph), (kind, g["text"])
    # …and its OWN wording, which a shared engine is exactly what could lose
    assert d["empty"] == {"monitors": "no monitors in this session",
                          "jobs": "no background jobs in this session"}
    assert d["crumbs"]["monitors"]["back"].endswith("/monitors")
    assert d["crumbs"]["jobs"]["back"].endswith("/jobs")
    assert d["crumbs"]["monitors"]["text"] == "◉ monitors›◉ watcher"
    assert d["crumbs"]["jobs"]["text"] == "◷ jobs›◷ make build"
    # the poll runs only while something is LIVE and that section is what you
    # are looking at — its tab, or one item's drill-down
    assert d["poll"] == {"liveOnTab": True, "liveOnDrill": True,
                         "liveElsewhere": False, "deadOnTab": False}
    # loadSection: one fetch on the section's own endpoint, the badge patched
    # from the fetched length (anchor text AND the cached meta field), the grid
    # painted. memory shares the fetch+badge half and repaints through its own
    # paintMemory, so it has no grid of its own here.
    assert d["fetched"] == {"monitors": ["/api/session/sid1/monitors"],
                            "jobs": ["/api/session/sid1/jobs"],
                            "memory": ["/api/session/sid1/memory"]}
    assert d["badges"]["monitors"] == {"count": "2", "meta": 2, "painted": 2}
    assert d["badges"]["jobs"] == {"count": "2", "meta": 2, "painted": 2}
    assert d["badges"]["memory"] == {"count": "1", "meta": 1, "painted": None}


def test_view_mode_engine_collapses_runs_and_words_them(dash):
    """The COLLAPSE ITSELF, executed rather than grepped: tests/jsdom/viewmode.js
    runs the real app.05-session.js engine over a DOM shim and reports what the
    stream became. Everything else about the view modes can be checked from
    Python, but "which adjacent items became one run", "what does the line say"
    and "which dot" live only in the page — and a grep test can't tell a correct
    run cut from an off-by-one. Skipped without `node` (the one JS-executing test
    in the suite, never a build requirement — docs/testing.md)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("no node on PATH")
    harness = os.path.join(REPO, "tests", "jsdom", "viewmode.js")
    app = os.path.join(REPO, "dashboard", "static", "app.05-session.js")
    r = subprocess.run([node, harness, app], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)

    # verbose hides nothing and summarizes nothing — today's dashboard, unchanged
    assert d["verbose"] == {"sums": 0, "shown": 11}
    # default: adjacent read/command/agent activity collapses; the EDIT stays
    # visible and BREAKS the run either side of it (the whole point — you always
    # see what was changed), as do the conversation messages
    assert d["default"]["sums"] == ["Read 1 file, ran 1 shell command",
                                    "Ran 2 shell commands",
                                    "Read 2 files, ran 1 shell command"]
    assert d["default"]["shown"] == ["msg", "edit", "msg", "msg"]
    # focus: your prompt + exactly ONE message — the one the turn ends on — and
    # the activity merged into a single summary line. A settled turn (this story's
    # tab is idle) shows that message at full weight; the mid-turn prose is gone.
    assert d["focus"]["sums"] == \
        ["Edited 1 file +12 -3, read 3 files, ran 4 shell commands"]
    assert d["focus"]["shown"] == ["msg", "msg"]

    # AGENTS are the one act the two modes disagree about. DEFAULT leaves them
    # standing as their own lines — who you dispatched and who came back IS the
    # shape of a lead session's turn, which is how Claude Code's own default
    # density prints it — and folds only the rest into its summary…
    assert d["teamDefault"]["shown"] == ["msg", "agent", "edit", "agent", "msg"]
    assert d["teamDefault"]["sums"] == [
        "Ran 1 shell command, tracked 1 task, passed 2 messages"]
    # …while FOCUS is one line for the whole turn, so they fold in with everything
    # else — and are still COUNTED there. Focus briefly dropped them from the
    # counters (no row, no fragment) and that was wrong on the summary's own terms:
    # a one-line account that omits the largest part of a turn is a lie about the
    # turn, not a précis of it. What kept those rows on screen was the CSS cascade
    # bug, not the counting.
    assert d["teamFocus"]["sums"] == [
        "Edited 1 file +12 -3, ran 2 agents, ran 1 shell command,"
        " tracked 1 task, passed 2 messages"]
    assert d["teamFocus"]["shown"] == ["msg", "msg"]
    # a focus session that is ONLY plumbing still gets its line — the work happened
    assert d["teamOnly"] == {"sums": 1, "shown": ["msg", "msg"]}
    # the AGENT counter counts AGENTS, not agent-ish ROWS. One subagent contributes
    # a launch note and a finish note (plus a resume, plus a second result if it
    # reports twice), so counting rows announced "ran 77 agents" for a session with
    # 21 of them. `data-agent` is the served src id; a row without one counts once
    # rather than dropping out — unattributable, never uncounted.
    assert d["agentCount"] == "Ran 2 agents"           # 2 agents, 4 rows
    assert d["agentCountNoId"] == "Ran 2 agents"       # 2 rows, no ids
    # …and the same rule for MAIL, keyed on the msg_id (`data-mid`): an arrival, its
    # body and its read notice are three rows about ONE message, which is how
    # "passed 4 messages" appeared for two that had been sent.
    assert d["mailCount"] == "Passed 1 message"        # 1 message, 2 rows
    assert d["mailCountNoId"] == "Passed 2 messages"   # 2 rows, no ids

    # EXPANDING that summary reveals every member it counted, agents and mail
    # included, under one rail
    assert d["teamExpanded"]["shown"] == \
        ["msg", "agent", "edit", "bash", "task", "mail", "mail", "agent", "msg"]
    assert d["teamExpanded"]["rail"] == 7
    assert d["teamExpanded"]["railLast"] == ["agent"]

    # …and while the turn is STILL RUNNING that newest message is PROVISIONAL —
    # greyed, because the result is still coming — going to full weight when the
    # tab settles. Same story, same items, only the tab state differs; the older
    # in-turn prose is hidden in both.
    assert d["focusRunning"] == ["msg:dim", "msg"]
    assert d["focusSettled"] == ["msg", "msg"]
    # a PREVIOUS turn's reply is never provisional, even while a new turn runs
    # (that turn has produced no message yet, so nothing there is in flight)
    assert d["focusOlderTurn"] == ["msg", "msg", "msg"]

    # Claude Code's wording, to the letter (docs/dashboard.md *View modes*):
    # singular/plural units, fragment ORDER, capitalized first fragment only…
    assert d["singular"] == "Read 1 file, ran 1 shell command"
    assert d["plural"] == "Read 2 files, ran 2 shell commands"
    # …the participle + trailing … while it runs, past tense when done…
    assert d["live"]["text"] == "Reading 1 file, running 1 shell command…"
    assert d["live"]["dot"] == "running" and d["live"]["timer"] == " · 30s"
    # …memory ops worded as memories, not file reads, and agents before commands
    assert d["memory"] == "Ran 1 agent, recalled 2 memories"
    # a Write counts as an edit (Claude Code's own editFileCount), diffstats summed
    assert d["editSummary"] == "Edited 2 files +52 -3, read 1 file, ran 1 shell command"

    # a failure inside a collapsed run still shows: the dot goes red
    assert d["failed"]["dot"] == "bad"

    # An agent NOTE's dot carries the same three states as that summary dot — grey
    # running, green finished, red not ("why is it grey and not green/red based on the
    # outcome?"). No op can say: a LAUNCH note is written before there is an outcome,
    # so the row is joined to the agents payload by `data-agent` and re-tinted on every
    # `agents` event. Scene: a1 finished (one of its two rows carrying a failing op, so
    # that row alone reddens), a2 still running, and a mail row that is nobody's agent.
    assert d["dots"] == [["-", "-"], ["a2", "run"], ["a1", "bad"], ["a1", "ok"]]

    # A COMMAND's header is the same kind of line, assembled from the pieces the server
    # splits out (opshtml.cmd_note): the dot + the kind word beside the command, the
    # closing duration AFTER it, the ⧉ links in their own slot. Its dot needs no agents
    # payload — the ops themselves say it: grey while the command runs, then green, or
    # red when the closer reports a failure.
    for got in (d["quietRun"], d["quietOk"], d["quietBad"]):
        assert got["quiet"] == "1" and got["links"] == 1
        assert got["sum"] == "make test"          # the command, from its own body op
        assert "anmark" in got["chips"]           # the dot, in the chips slot
        assert got["running"]["out"] == "run"     # …grey until the closer lands
        assert not got["tailInChips"], "the duration must sit AFTER the command"
    assert [d["quietRun"]["out"], d["quietOk"]["out"], d["quietBad"]["out"]] \
        == ["run", "ok", "bad"]
    assert [d["quietRun"]["tail"], d["quietOk"]["tail"]] == ["-", "cqt"]

    # A RUNNING foreground command shows its live ⏱, in the slot the final duration
    # lands in — and it must survive the order that killed it ("for running foreground
    # commands, I still want to see the live time"): the `fgrun` event arms the ticker
    # BEFORE the block's own ops arrive (a faster cadence, one hook run behind both), so
    # the opener lands with `fgRun.g` already matching and used to retire the chip
    # before it ever painted — permanently, since `fgEnded` refuses a resurrection.
    # Only the `■ finished` CLOSER may retire it. Both arrival orders are pinned.
    for got in (d["fgLiveArmedFirst"], d["fgLiveOpsFirst"]):
        assert got["live"]["text"] == "⏱ 64s", got
        assert got["live"]["inTail"], "the ⏱ belongs where the duration will land"
        assert not got["afterFinish"], "the finish chip retires the ticker"
        assert got["tail"] == "cqt", "…and replaces it with the real duration"

    # DEFAULT folds a monitor into the summary ("also monitors should be in the under
    # summary in default mode") and leaves a background job standing
    assert d["monitorDefault"] == {"sums": ["Watched 2 monitors"],
                                   "shown": ["msg", "msg"]}
    assert d["bgDefault"] == {"sums": [], "shown": ["msg", "bg", "msg"]}

    # a SKILL stands as its own `⏺ Skill(<name>)` line in DEFAULT and is counted into
    # the summary in FOCUS ("I want skills in default mode to appear like this ⏺
    # Skill(slack), and in focus mode in the summary to appear")
    assert d["skillDefault"] == {"sums": [],
                                 "shown": ["msg", "skill", "skill", "msg"]}
    assert d["skillFocus"] == {"sums": ["Used 2 skills"], "shown": ["msg", "msg"]}

    # the ⚠ audit warning never folds — and it splits the run it sits in
    assert d["warnBreaksRuns"] == {"sums": 2, "shown": ["warn", "msg"]}

    # An INJECTED prompt (a Stop hook's feedback, a loaded skill's body — the
    # transcript's isMeta) is not something you said: verbose keeps it (it IS in
    # the transcript), both non-verbose modes drop it, and it does NOT close the
    # turn — so focus still shows exactly ONE final reply, not one per hook
    # firing.
    assert d["injected"]["verbose"] == 5
    assert d["injected"]["default"] == ["msg", "msg", "msg"]   # prompt + 2 replies
    # prompt + THE reply: still exactly ONE, so the hook firing did not
    # manufacture a second turn-ending message
    assert d["injected"]["focus"] == ["msg", "msg"]

    # the summary is clickable BOTH ways (it stays put while expanded — it is the
    # only way back), and a redundant pass is a no-op (the signature guard, which
    # is what keeps a live stream from rebuilding the feed under a reader)
    assert d["idempotent"] is True
    # Expanding also MARKS what the run revealed, so the blocks read as belonging
    # to the summary above them rather than as loose feed activity: every member
    # carries the rail class ("R"), the OLDEST one closes it ("L"), and the
    # message outside the run is untouched. Collapsing clears every mark — a
    # stale one would draw a rail under a run that is no longer open.
    assert d["expanded"] == {"shown": ["read", "bash", "bash", "msg"],
                             "sums": ["1"],
                             "marks": ["R-", "R-", "RL", "--"],
                             # …and they arrive FOLDED (`data-open` 0): expanding
                             # a summary answers "which actions were these", not
                             # "dump every command's output" — which would be the
                             # wall the collapse exists to remove
                             "opens": ["0", "0", "0", "-"]}
    # a block the USER opened is left alone, so the fold can't fight a manual
    # toggle on the next pass (its `userset` mark is read off the DOM, because a
    # history block has no entry in S.ses.blocks to carry the flag)
    assert d["userOpened"] == ["-0", "U1", "-0", "--"]
    assert d["recollapsed"] == {"shown": ["msg"], "sums": ["0"],
                                "marks": ["--", "--", "--", "--"]}
    # a run absorbing new items keeps its identity, so an expansion survives it
    assert d["growth"] == {"sameKey": True, "stillOpen": True,
                           "text": "Ran 3 shell commands"}
    # and switching back leaves no residue
    assert d["backToVerbose"] == {"sums": 0, "shown": 3}


def test_conversation_text_is_not_in_a_nested_scroll_box(dash):
    """A message bubble grows to its content; only SKIMMED content gets a
    fixed-height scroller. Both halves of that asymmetry are pinned here because
    they are one decision made in two places: the server stopped eliding an
    agent's message/result by line count (docs/subagents.md), and a `max-height`
    + `overflow: auto` on the same text is that elision wearing a scrollbar —
    which is exactly how the "why do I still have to scroll long messages"
    report survived the server-side fix."""
    code, css = _get(dash + "/static/style.css")
    assert code == 200
    rules = _css_rules(css)

    # conversation text — the stream's message bubbles AND the drill-down's
    # entries, capped together by one rule — grows to its content
    conv = rules[".msg .md, .ent .bd .md"]
    assert "max-height: none" in conv and "overflow: visible" in conv
    # a subagent's ⇢ prompt / ⇠ result block body is conversation text too (in the
    # web mirror an agent block is only ever those two)
    agent = rules['.blk[data-act="agent"] > .bbody']
    assert "max-height: none" in agent and "overflow: visible" in agent
    # …while a generic block body (a command's output — skimmed) keeps its box
    generic = rules[".bbody"]
    assert "max-height: 480px" in generic and "overflow: auto" in generic


def test_a_subagent_block_reads_as_one_quiet_note_line(dash):
    """A subagent's two web-surfaced blocks render as `⏺ Agent "<name>" launched` /
    `… finished · 21m 31s` — the register of a collapsed run's summary, not the
    terminal's colour-coded chip (`… ⇠ result  fable-5·high  ctx 22% · 225k/1M`,
    which shouted an agent's bookkeeping at the weight of the conversation; those
    numbers already live on the agent's card). The WORDING is the producer's
    (core/ops.py's `note`), not a reformat of the chip here: parsing a chip back
    apart to reword it is exactly the sniffing actclass exists to have ended.

    The block is still a block, so the line stays clickable — its body is the
    agent's brief (launched) or its result (finished), which is what the reader
    wants from it."""
    from core import ops as O
    from core import slots
    from dashboard import opshtml

    def agent_op(text, note, g):
        op = O.label(text, slots.color("sub", 0), g=g, lk=O.COPY_ALL, web=True,
                     note=note)
        op["src"] = "sub:aexplore2-abc123"        # the substream's stamp
        return op

    prompt = agent_op("explore2 ⇢ prompt  opus-5·high", 'Agent "Symbol sweep" launched', "b1")
    result = agent_op("explore2 ⇠ result  opus-5·high  ctx 22% · 225k/1M",
                      'Agent "Symbol sweep" finished · 21m 31s', "b2")
    header = O.label("▶ explore2 · Symbol reference sweep", slots.color("sub", 0))

    items = opshtml.op_items([header, prompt, result], "sid")
    # the MAIN session's own launch header is dropped: the prompt block says the same
    # thing and carries the brief, so keeping both is two launch lines per launch
    assert len(items) == 2, [i["html"] for i in items]
    for it, verb in zip(items, ("launched", "finished · 21m 31s")):
        assert 'class="anote"' in it["html"]
        # marker and words in separate spans — that is what lets the line sit on the
        # summary line's grid (a 7px dot column, an 8px gap, then the text)
        assert '<span class="anmark">⏺</span>' in it["html"]
        assert ('<span class="atext">Agent &quot;Symbol sweep&quot; %s</span>' % verb) \
            in it["html"]
        # none of the chip's own vocabulary survives on this surface
        for gone in ("chip", "⇢", "⇠", "opus-5·high", "ctx 22%", "⧉"):
            assert gone not in it["html"], gone
        assert it["act"] == "agent" and it["note"] == 1
        assert it["agent"] == "aexplore2-abc123"    # the join key for the counter

    # a chip written BEFORE producers carried the wording gets it recovered from the
    # marker: a parked (or long-running) session's ops cannot be re-stamped, and would
    # otherwise show the terminal's chip forever. No duration — the chip never had one.
    old = O.label("Fix infra/vcs subprocess bugs ⇠ result  fable-5·high  ctx 22% · 225k/1M",
                  slots.color("sub", 2), g="b9", lk=O.COPY_ALL, web=True)
    old["src"] = "sub:afix-infra-vcs-1234"
    legacy = opshtml.op_items([old], "sid")[0]
    assert "Agent &quot;Fix infra/vcs subprocess bugs&quot; finished" in legacy["html"]
    assert "ctx 22%" not in legacy["html"] and legacy["note"] == 1

    # a label in NEITHER quiet register — no note of its own, and not command family
    # (a task row) — is untouched: the coloured chip is still the default
    plain = opshtml.op_items([O.label("✚ task · ship it", O.GREEN, g="t1")], "sid")[0]
    assert 'class="chip"' in plain["html"] and "anote" not in plain["html"]

    # …and the page knows to let the note BE the line (no first-body-line summary
    # crowding it), which it reads off the served item rather than the HTML
    code, ses = _get(dash + "/static/app.05-session.js")
    assert code == 200
    assert "b.noteOnly = true;" in ses and "!b.noteOnly" in ses
    # …and that a note block arrives CLOSED (the line is the point), without ever
    # re-closing one the reader opened
    assert 'if (!b.root.dataset.userset) b.root.dataset.open = "0";' in ses
    # and the card recedes to a plain line until it is opened
    code, css = _get(dash + "/static/style.css")
    assert code == 200
    rules = _css_rules(css)
    # the note line sits on the SUMMARY line's grid, to the pixel: same padding,
    # same 7px marker column, same 8px gap, same font. They are the same kind of
    # line, and a note indented differently from `Ran 2 shell commands` reads as a
    # ragged pair (the reported "not visually aligned").
    vsum, note = rules[".vsum"], rules[".anote"]
    head = rules[".stream > .blk[data-note] > .bhead"]
    for prop in ("gap: 8px", "font: 12px/1.5 var(--mono)", "align-items: center"):
        assert prop in vsum and prop in note, prop
    assert "padding: 5px 13px" in vsum and "padding: 5px 13px" in head
    # …and a note with NO block behind it (a mail read notice) wears that same box
    assert "padding: 5px 13px" in rules[".stream > .anote"]
    # …and the DOT is the same dot: every one-line notice's marker is `.vdot`'s CIRCLE,
    # not a glyph rendered small (a font's ⏺ drew visibly smaller than the circle beside
    # it — "all dots should be the same size"), so the geometry is asserted equal
    for prop in ("width: 7px", "height: 7px", "border-radius: 50%"):
        assert prop in rules[".vsum .vdot"] and prop in rules[".anmark"], prop
    # which means the marker's own character must NOT paint — the box does
    assert "font-size: 0" in rules[".anmark"]
    assert "box-shadow: none" in rules['.stream > .blk[data-note]']
    assert 'var(--card)' in rules['.stream > .blk[data-note][data-open="1"]']


def test_a_command_block_is_a_quiet_note_line_with_its_duration(dash):
    """A foreground command, a background job and a monitor read as ONE dim line —
    `⏺ make test · 0.6s` — instead of a coloured pill inside a panel card ("style
    foreground/background/monitors to the same style that we have established / I
    don't like those boxy blocks / also get rid of the colors / I still want the dot
    and the time info").

    So the producer's chip is re-cut into the note register: the glyph goes (it only
    ever disambiguated by COLOUR — `◉` is a monitor or a mail read notice depending on
    it — so it cannot survive un-coloured), `foreground` goes (the command says it),
    and what survives is the words the producer already wrote plus the duration. The
    pieces are served SEPARATELY because they land in different slots of the block
    header, which is the only way the duration can sit after the command."""
    from dashboard.opshtml import actclass as AC
    from core import slots

    mon_rgb = slots.color("monitor", 0)
    fg_open = _lbl("▶ foreground", O.SLATE, g="t1")
    fg_end = _lbl("■ finished · 0.6s", O.SLATE, g="t1")
    bad_end = _lbl("■ failed (exit 1) · 2.1s", O.RED, g="t1")
    bg_open = _lbl("▷ background", slots.color("bg", 0), g="t2")
    mon_open = _lbl("◉ monitor · watch tests · persistent", mon_rgb, g="t3")
    ws = _lbl("⇄ ws · wss://x/y", mon_rgb, g="t3")

    # the WORDS, and each op's ROLE in its line (which the page needs to place it)
    assert AC.cmd_note(fg_open) == ("", AC.CQ_OPEN)      # muted, NOT unrecognised
    assert AC.cmd_note(fg_end) == ("finished · 0.6s", AC.CQ_CLOSE)
    assert AC.cmd_note(bad_end) == ("failed (exit 1) · 2.1s", AC.CQ_CLOSE)
    assert AC.cmd_note(bg_open) == ("background", AC.CQ_OPEN)
    assert AC.cmd_note(mon_open) == ("monitor · watch tests · persistent", AC.CQ_OPEN)
    # a monitor's SUBJECT line is a second header row, not a second opener: only the
    # opener carries the line's dot, or a ws monitor would show two
    assert AC.cmd_note(ws) == ("ws · wss://x/y", AC.CQ_SUB)

    # colour-gated exactly like the classifier: a SUBAGENT's `■ … ended` footer is its
    # block's, not a command's, and a mail read notice is not a monitor
    sub_end = _lbl("■ explore ended · 3m", slots.color("sub", 0))
    assert AC.cmd_note(sub_end) is None
    assert AC.cmd_note(_lbl("◉ read · lead → rev", O.GREEN)) is None
    # an op already in the note register keeps its own wording
    assert AC.cmd_note(O.label("⇠ result", mon_rgb, note='Agent "x" finished')) is None

    items = opshtml.op_items([fg_open, O.code("make test", g="t1"),
                              O.gut("all green", O.SLATE, g="t1"), fg_end], "sid")
    head, end = items[0], items[-1]
    assert head["quiet"] == "open" and end["quiet"] == "close"
    # the dot is SERVED (one owner for the glyph — opshtml.NOTE_GLYPH), the muted word
    # leaves nothing else on the opener, and the closing duration is its own piece
    assert head["html"] == '<span class="anmark">⏺</span>'
    assert end["html"] == '<span class="cqt">finished · 0.6s</span>'
    # …and the ⧉ links come apart from the words, for the slot at the far right
    assert "⧉cmd" in head["links"] and "⧉cmd" not in head["html"]
    for it in items:
        assert "chip" not in it["html"] and "background:rgb" not in it["html"]
    # the block still classifies as a shell command (the fold/filter is unchanged)
    assert head["act"] == "bash"
    assert opshtml.op_items([bg_open], "sid")[0]["act"] == "bg"
    assert opshtml.op_items([mon_open], "sid")[0]["act"] == "monitor"

    # THE PAGE: four header slots, the quiet routing, and the dot's outcome
    code, ses = _get(dash + "/static/app.05-session.js")
    assert code == 200
    assert "head.append(chips, sum, tail, links);" in ses
    assert 'if (it.quiet === "close") {' in ses
    assert 'b.root.dataset.out =' in ses
    # the ticking ⏱ lands in the same slot as the final duration
    assert "(b.root.dataset.quiet ? b.tail : b.chips).append(c);" in ses

    code, css = _get(dash + "/static/style.css")
    assert code == 200
    rules = _css_rules(css)
    # the same box as an agent note's — ONE rule serving both, so they cannot drift
    for sel in ('.stream > .blk[data-quiet]',
                '.stream > .blk[data-quiet] > .bhead',
                '.stream > .blk[data-quiet][data-open="1"]'):
        assert sel in rules
    assert "box-shadow: none" in rules['.stream > .blk[data-quiet]']
    assert rules['.stream > .blk[data-quiet]'] \
        == rules['.stream > .blk[data-note]'], "one box, two registers"
    # …and the same grid as the note/summary line (gap, font, baseline)
    quiet_head = rules[".blk[data-quiet] > .bhead"]
    for prop in ("gap: 8px", "font: 12px/1.5 var(--mono)", "align-items: center"):
        assert prop in quiet_head, prop
    # the words are DIM, and nothing in the line is an accent colour
    assert "var(--dim)" in rules[".blk[data-quiet] .bsum, .blk[data-quiet] .btail,"
                                " .blk[data-quiet] .cqt"]
    assert "border: none" in rules[".blk[data-quiet] .chip.blive"]
    # the dot carries the outcome through the SAME rules an agent note's does — those
    # are keyed on `data-out` alone, so nothing new was needed for a command block
    assert '[data-out="ok"] .anmark { color: var(--green); }' in css
    assert '[data-out="bad"] .anmark { color: var(--red); }' in css


def test_a_skill_is_one_note_line_shown_in_default_and_counted_in_focus(dash):
    """`⏺ Skill(slack)`, expandable, in both modes — asked for in those words: *"I want
    skills in default mode to appear like this ⏺ Skill(slack), and in focus mode in the
    summary to appear, and in both places it is expandable"*. Nothing rendered skills at
    all before: the tool fires both tool hooks but had no formatter.

    It needs no new page machinery — it is a NOTE block like an agent's or a message's
    (the producer stamps the wording), which is what makes it one quiet line in default
    with its args behind the click. The only page-side facts are its act's three table
    rows: which filter chip it files under, which summary fragment counts it, and that
    FOCUS folds it while DEFAULT does not."""
    from dashboard.opshtml import actclass as AC
    from core import slots
    from plugins.claude_code import skill_fmt as SK

    row = _lbl("✦ skill · slack", O.VIOLET, g="sk1")
    row["note"] = "Skill(slack)"
    assert AC.classify(row) == (AC.ACT_SKILL, False)
    # a FAILED call is the same row in the shared failure colour — still a skill, and
    # now `bad`, so a collapsed run's dot reddens for it
    assert AC.classify(_lbl("✦ skill · logs", O.RED, g="sk2")) == (AC.ACT_SKILL, True)
    # …but a ✦ in a STREAM palette is not the session's own row (nothing paints one
    # today; the gate is what keeps that true)
    assert AC.classify(_lbl("✦ skill · x", slots.color("sub", 0)))[0] != AC.ACT_SKILL
    # the glyph and the wording have ONE owner each, in core (the producer stamps the
    # note, the classifier reads the marker) — never respelled on either side
    assert SK.SF.SKILL_MARK == "✦" and SK.SF.skill_note("slack") == "Skill(slack)"
    assert SK.SF.skill_note("logs", failed=True) == "Skill(logs) failed"

    item = opshtml.op_items([row], "sid")[0]
    assert item["act"] == "skill" and item["note"] == 1
    assert 'class="anote"' in item["html"] and "Skill(slack)" in item["html"]
    assert "chip" not in item["html"]

    code, ses = _get(dash + "/static/app.05-session.js")
    assert code == 200
    fold = re.search(r"const VIEW_FOLD = \{(.*?)\n\};", ses, re.S).group(1)
    modes = dict(re.findall(r"\n  (verbose|default|focus): \[([^\]]*)\]", fold))
    assert '"skill"' in modes["focus"], "focus folds it into the summary"
    assert '"skill"' not in modes["default"], "default keeps the line standing"
    assert '["skill", "using", "used", "skill", "skills"]' in ses
    assert re.search(r"skill: \"commands\"", ses)


def test_a_mail_row_is_a_quiet_note_holding_the_message(dash, tmp_path):
    """Team mail on the web reads like the agent notes beside it: `⏺ Message
    team-lead → rev-ui-util: …`, not a green `◉ read · team-lead → rev-ui-util`
    ("this thing should also not be coloured"). And the click has something behind
    it — the MESSAGE, not the 5-10 word preview that was all the row ever carried
    ("why when I click on Passed 4 messages can't I see the actual messages?").
    The terminal keeps its coloured chip; only the web wording and the body move."""
    from core import hostpane as HP
    from dashboard.opshtml import actclass as AC
    from plugins.claude_code import msgs as MSGS

    log = str(tmp_path / "claude-mirror-mail.log")
    HP.ensure_db(log)
    body = "Complete. `core.money_cycle` now owns the cycle.\nAll twelve providers use it."
    chip, gut = MSGS.sent_ops("fix-smoke", "team-lead", "Money-cycle dedup complete",
                              body, "m-42", log)
    arrive, = MSGS.event_ops([("new", "fix-smoke", "team-lead",
                              "Money-cycle dedup complete", body, "m-42")], log)
    read, = MSGS.event_ops([("read", "fix-smoke", "team-lead", "", "", "m-42")], log)
    # the PANE keeps its glyphs, its semantic colours and its classes
    assert chip["s"] == "✉ fix-smoke → team-lead" and chip["c"] == list(O.YELLOW)
    assert arrive["s"] == "● fix-smoke → team-lead · delivered"
    assert read["s"] == "◉ read · fix-smoke → team-lead" and read["c"] == list(O.GREEN)
    assert [AC.classify(o)[0] for o in (chip, gut, arrive, read)] == \
        ["mail", None, "mail", "mail"]
    # …and all four name the same MESSAGE, which is what the counter dedupes on
    assert chip["mid"] == gut["mid"] == arrive["mid"] == read["mid"] == "m-42"

    # the SENT row carries the message; the summary rides its note as the preview
    assert gut["s"] == body
    assert chip["note"] == "Message fix-smoke → team-lead: Money-cycle dedup complete"
    # …and the poller's rows are labelled MAIL, not Message: they report on a message
    # they do not carry, and a row that says `Message` with nothing behind it is the
    # whole complaint ("still can't see the message")
    assert arrive["note"] == "Mail fix-smoke → team-lead · delivered"
    assert read["note"] == "Mail fix-smoke → team-lead · read"
    assert len(MSGS.event_ops([("new", "a", "b", "s", body, "m1")], log)) == 1
    # a long report is capped — the pane paints this INLINE, where a wall is a wall
    long = MSGS.sent_ops("a", "b", "s", "x\n" * 200, "m1", log)[1]["s"]
    assert long.count("\n") == MSGS.CAP_TEXT and "more lines)" in long

    items = opshtml.op_items([chip, gut, arrive, read], "sid")
    assert [it.get("act") for it in items] == ["mail", None, "mail", "mail"]
    assert [it.get("mid") for it in items] == ["m-42"] * 4
    for it in (items[0], items[2], items[3]):
        assert 'class="anote"' in it["html"] and it["note"] == 1
        for gone in ('class="chip"', "✉", "●", "◉", "rgb(152,195,121)"):
            assert gone not in it["html"], gone
    assert "Message fix-smoke → team-lead: Money-cycle" in items[0]["html"]
    # the sent row and its body are ONE block, so the note line opens onto the message
    assert items[0]["g"] and items[0]["g"] == items[1]["g"]
    assert "core.money_cycle" in items[1]["html"]
    # THE MODE RULE: only the message is a message. The poller's two rows are marked
    # plumbing (default and focus drop them, verbose shows them labelled), the sent
    # row and its body are not.
    assert [it.get("plumb") for it in items] == [None, None, 1, 1]
    code, ses = _get(dash + "/static/app.05-session.js")
    assert code == 200 and 'if (elem.dataset.plumb) return "hide";' in ses

    # HISTORY (ops written before the wording existed, which no restart re-stamps)
    # gets it recovered from the chip, through the same owner
    old_new = _lbl("● lead → rev-ui", MSGS.MSG_NEW_RGB)
    old_body = O.gut("check the diff", MSGS.MSG_NEW_RGB)
    old_read = _lbl("◉ read · lead → rev-ui", MSGS.MSG_READ_RGB)
    assert AC.legacy_note(old_new) == "Message lead → rev-ui"
    assert AC.legacy_note(old_read) == "Mail lead → rev-ui · read"
    # …and an old arrival that DOES carry a message is not demoted to plumbing: it is
    # the only trace that message left, and hiding it would leave a pre-2026-07-27
    # session showing no mail at all outside verbose
    assert [i.get("plumb") for i in
            opshtml.op_items([old_new, old_body, old_read], "sid")] == [None, None, 1]
    assert opshtml.op_items([old_new, old_read], "sid")[0].get("plumb") == 1
    legacy = opshtml.op_items([old_new, old_body, old_read], "sid")
    # …and the arrival and its body are ONE BLOCK (the synthetic group below), so the
    # message opens from the note line instead of sitting under it — which is also what
    # settles the ORDER: a group-less body reverses ABOVE the row it belongs to (the
    # feed is newest-top), and it read as belonging to the `· read` notice of the
    # message before it
    assert [("body" if "ogut" in i["html"] else "note") for i in legacy] == \
        ["note", "body", "note"]
    assert legacy[0]["g"] == legacy[1]["g"]
    assert "Message lead → rev-ui</span>" in legacy[0]["html"]
    assert "check the diff" in legacy[1]["html"]
    # all three rows are ONE message: with no msg_id in pre-`mid` history the subject
    # is the `<from> → <to>` pair plus the ARRIVAL's row id, so the summary counts one
    # (the BODY needs no subject of its own — it is inside the arrival's block now, and
    # the summary counts top-level rows)
    ids = [11, 12, 13]
    keyed = opshtml.op_items([old_new, old_body, old_read], "sid", ids)
    assert [i.get("mid") for i in keyed] == \
        ["pair:lead → rev-ui#11", None, "pair:lead → rev-ui#11"]
    # …the arrival's own id, which is what keeps two messages the same way apart (the
    # pair alone collapsed both into one) AND survives the batch boundaries of one
    # render (a per-batch counter gave both a `#1` and merged them again)
    twice = opshtml.op_items([old_new, old_body, old_read, old_new, old_read],
                             "sid", [11, 12, 13, 14, 15])
    assert {i["mid"] for i in twice if i.get("mid")} == \
        {"pair:lead → rev-ui#11", "pair:lead → rev-ui#14"}
    # a read notice whose arrival is outside this batch opens its own subject, and can
    # never be merged into an arrival that only comes later
    orphan = opshtml.op_items([old_read, old_new, old_body], "sid", [20, 21, 22])
    assert orphan[0]["mid"] == "pair:lead → rev-ui#20"
    assert len({i["mid"] for i in orphan if i.get("mid")}) == 2
    # without ids (the live path, where every op carries a real mid) the key falls
    # back to a per-call position — still one subject for the trio
    assert len({i["mid"] for i in legacy if i.get("mid")}) == 1
    # A CONVERSATION RECORD BETWEEN an arrival and its read splits them across two
    # batches of the same render (a message is no op's block, so it flushes the run).
    # The `carry` dict is what keeps the association: the reviewed session has exactly
    # this shape, and without it that read counted as a second message.
    from dashboard.read import mirror as M
    split = M._render_window([(11, "op", old_new), (12, "op", old_body),
                              (12, "msg", {"kind": "message", "text": "hi"}),
                              (13, "op", old_read)], 0, "")
    mail = [i for i in split if i.get("act") == "mail"]
    assert len(mail) == 2 and len({i["mid"] for i in mail}) == 1
    # a MONITOR's ◉ is not mail — the colour decides, as in the classifier (mail wears
    # the semantic green, a monitor its slot palette). It has a quiet line of its own
    # (cmd_note), and the point is that it is NOT worded as a message.
    from core import slots
    mon = _lbl("◉ monitor · npm", slots.color("monitor", 1))
    assert AC.legacy_note(mon) is None
    assert AC.cmd_note(mon) == ("monitor · npm", AC.CQ_OPEN)
    assert "Message" not in opshtml.op_items([mon], "sid")[0]["html"]


def test_an_ungrouped_mail_message_is_still_ONE_expandable_block():
    """PRE-`mid` mail is two TOP-LEVEL rows on disk — a `●` chip and the message body
    as a bare gutter, neither carrying a copy group (the send-time row that groups them
    came later). The page folds by `g`, so the message SAT OPEN under its own header
    instead of behind it: "the actual message should be expandable from `Message
    team-lead → rev-ui-util`, following the pattern of other stuff". So the read side
    hands the pair a synthetic group — and only ever the pair."""
    from dashboard import opshtml
    from plugins.claude_code import msgs as MSGS

    arrival = _lbl("● lead → rev-ui", MSGS.MSG_NEW_RGB)
    body = O.gut("please send your final review report", MSGS.MSG_NEW_RGB)
    read = _lbl("◉ read · lead → rev-ui", MSGS.MSG_READ_RGB)

    items = opshtml.op_items([arrival, body], "sid", [41, 42])
    assert [i["g"] for i in items] == ["mail:41", "mail:41"], "one block, keyed by row"
    assert items[0]["note"] == 1 and "ogut" in items[1]["html"]

    # a group id no PRODUCER can mint, so it can never collide with a real copy group
    # (those are `b<n>` or a tool_use_id) — and it stashes nothing: there is no ⧉ link
    assert "mail:" not in str(MSGS.sent_ops("a", "b", "s", "text", "m1"))
    assert "data-cc" not in items[0]["html"] + items[1]["html"]

    # a READ NOTICE has no body, so it takes no group and cannot swallow the next row
    loose = O.gut("some other producer's bare gutter", O.SLATE)
    assert [i.get("g") for i in opshtml.op_items([read, loose], "sid", [43, 44])] \
        == [None, None]
    # …nor can a mail chip reach past the op right after it
    far = opshtml.op_items([arrival, read, body], "sid", [45, 46, 47])
    assert [i.get("g") for i in far] == [None, None, None]


def test_an_agent_notes_dot_is_tinted_by_its_agents_outcome(dash):
    """The dot's three states are DRAWN, and the outcome behind them has ONE owner.
    The engine only joins (`data-agent` -> the agents payload) and stamps `data-out`;
    the mapping from an agent row to running/ok/bad is `agentStatus` in the chrome
    file, the same function the rail's cards read — so a note and its card cannot
    disagree, and nothing here re-parses `end_reason`. The tint itself is executed in
    the jsdom harness (test_view_mode_engine_collapses_runs_and_words_them)."""
    code, ses = _get(dash + "/static/app.05-session.js")
    assert code == 200
    tint = re.search(r"function tintAgentNotes\(\) \{(.*?)\n\}", ses, re.S).group(1)
    assert "agentStatus(" in tint, "the outcome mapping must be the shared one"
    assert "end_reason" not in tint, "re-deriving the outcome here is the drift"
    assert 'dataset.out' in tint
    # it runs on new rows AND on every agents event (the launch note's only way to
    # learn that its agent ended — no op is written for that)
    assert ses.count("tintAgentNotes();") >= 2, "both append paths must tint"
    code, chrome = _get(dash + "/static/app.11-chrome.js")
    assert code == 200
    upd = re.search(r"function updateAgents\(\) \{(.*?)\n\}", chrome, re.S).group(1)
    assert "tintAgentNotes();" in upd

    code, css = _get(dash + "/static/style.css")
    assert code == 200
    assert '[data-out="ok"] .anmark' in css and "var(--green)" in css
    assert '[data-out="bad"] .anmark' in css
    # …and the dim default stays on `.anote` itself, so a row with no outcome (team
    # mail) is untinted rather than green
    assert re.search(r"\.anote \{[^}]*color: var\(--dim\)", css, re.S)


def test_a_teammate_is_not_worded_or_counted_as_a_subagent():
    """Claude Code has two registers and so must we: a Task-spawned subagent is
    `Agent "<type>"`, an agent-TEAM member is `Teammate @<name>` (its own TUI prints
    `⏺ Teammate @fix-smoke-dedup finished`). One word for both read as a bug — "I want
    a clear distinction Agent from Teammate in those summaries and message
    transcripts" — and they ARE different things: a named, mailable peer vs a one-shot
    delegate. Which one an op is comes from the `src` stamp it already wears, never
    from its name or its colour, so history is worded right too."""
    from core import slots
    from core import streamfmt as SF
    from dashboard import opshtml
    from dashboard.opshtml import actclass as AC

    def chip(who, src, palette, note=None):
        op = SF.chip(who, *SF.MARK_RESULT, slots.color(palette, 0), g="b1",
                     web=True, note=note)
        op["src"] = src
        return op

    # the PRODUCER's own wording, one builder for both registers
    assert SF.agent_note("Explore", "launched") == 'Agent "Explore" launched'
    assert SF.agent_note("fix-smoke-dedup", "finished", team=True, dur="21m 31s") \
        == "Teammate @fix-smoke-dedup finished · 21m 31s"

    # …recovered the same way for pre-`note` ops, off `src` (which is OLDER than `note`)
    assert AC.legacy_agent_note(chip("rev-ui-util", "team:a1", "team")) \
        == "Teammate @rev-ui-util finished"
    assert AC.legacy_agent_note(chip("Explore", "sub:a2", "sub")) \
        == 'Agent "Explore" finished'
    # an op older than the src stamp has nothing to go on and stays an Agent
    assert AC.legacy_agent_note(chip("Explore", "", "sub")) == 'Agent "Explore" finished'

    # …and a teammate COUNTS as its own kind, so a collapsed run says which it ran
    assert AC.classify(chip("rev-ui-util", "team:a1", "team")) == (AC.ACT_TEAM, False)
    assert AC.classify(chip("Explore", "sub:a2", "sub")) == (AC.ACT_AGENT, False)
    served = opshtml.op_items([chip("rev-ui-util", "team:a1", "team")], "sid")
    assert served[0]["act"] == "team" and served[0]["agent"] == "a1"


def test_most_team_mail_is_a_lifecycle_frame_and_says_so(tmp_path):
    """The thing that made "why can't I read the message itself?" so confusing:
    MOST of a team session's mail is not prose at all. Claude Code delivers teammate
    lifecycle events through the same inboxes as a JSON frame in the record's `text`
    — 10 of the 12 arrivals recorded in the reviewed lead session were idle
    notifications, which carry no summary, so the row painted no body and read as
    `Message rev-ui-util → team-lead` with nothing behind the click. There was no
    message. Painting the JSON would be worse, so a frame is named by its TYPE: these
    rows exist only here (nothing calls SendMessage for them), and naming them is what
    lets the collapsing modes drop them as the plumbing they are."""
    from core import hostpane as HP
    from plugins.claude_code import msgs as MSGS

    log = str(tmp_path / "claude-mirror-frames.log")
    HP.ensure_db(log)

    def row(text, summ=""):
        ops = MSGS.event_ops([("new", "rev-ui", "team-lead", summ, text, "m1")], log)
        assert len(ops) == 1, "a poller row is one line, never a body"
        return ops[0]

    idle = json.dumps({"type": "idle_notification", "from": "rev-ui",
                       "timestamp": "2026-07-27T05:57:45.886Z",
                       "idleReason": "available"})
    chip = row(idle)
    assert chip["note"] == "Mail rev-ui → team-lead · idle"
    assert chip["s"] == "● rev-ui → team-lead · idle"     # the pane says it too
    assert "idle_notification" not in json.dumps(chip)   # …and never the wire format
    assert MSGS.frame(idle)["idleReason"] == "available"
    # an UNUSUAL outcome is named (an ordinary one is not — every line would carry it)
    assert row(json.dumps({"type": "idle_notification", "from": "rev-ui",
                           "idleReason": "failed",
                           "failureReason": "worktree gone"}))["note"] \
        == "Mail rev-ui → team-lead · idle (failed)"
    for kind, phrase in MSGS.FRAME_PHRASE.items():
        assert row(json.dumps({"type": kind}))["note"] \
            == "Mail rev-ui → team-lead · " + phrase
    # an unknown frame type still gets a line — its own type, which is at least true
    assert row(json.dumps({"type": "weird_new_thing", "x": 1}))["note"] \
        == "Mail rev-ui → team-lead · weird_new_thing"
    # …and PROSE is never mistaken for a frame, whatever it starts with: its row is
    # the plain `delivered` transition, and the MESSAGE is the send-time row's job
    for prose in ("{ not json after all", '{"no": "type field"}', "plain words"):
        assert MSGS.frame(prose) is None
        assert row(prose, "the preview")["note"] \
            == "Mail rev-ui → team-lead · delivered"


def test_an_agents_brief_carries_no_injected_system_reminders(dash):
    """Claude Code injects `<system-reminder>` blocks into the text it hands an
    agent — the addressable-teammates roster and friends — so a subagent's brief
    opened with two nested reminders and a list of its peers before a word of the
    actual task ("why do I see system reminders of the subagents"). Stripped in
    transcript.py, which owns Claude Code's transcript text shapes, so the terminal
    pane loses them too."""
    from plugins.claude_code import transcript as TR

    real = ("<system-reminder>\n<system-reminder>\nOther agents active in this "
            "session, addressable via SendMessage({to: name, message}): main, "
            "rev-observe, rev-ui-util.\n</system-reminder>\n\n"
            "Review the clients subtree and report every bug you find.")
    assert TR.strip_reminders(real) == \
        "Review the clients subtree and report every bug you find."
    # a brief that is ONLY reminders collapses to nothing rather than to tag soup
    assert TR.strip_reminders("<system-reminder>noise</system-reminder>") == ""
    # ordinary text is untouched, and empties survive being passed through
    assert TR.strip_reminders("plain brief") == "plain brief"
    assert TR.strip_reminders("") == "" and TR.strip_reminders(None) is None
    # the renderer's prompt block goes through it (one call site, the ⇢ prompt body)
    src = open(os.path.join(REPO, "plugins", "claude_code",
                            "substream_render.py"), encoding="utf-8").read()
    assert "TR.strip_reminders(text)" in src

    # …AND the read side covers ops already on disk, which no restart re-stamps.
    # Only a subagent's own body (`web`) is touched — the strip must not roam over
    # command output or file content that happens to quote the tag.
    from core import ops as O
    from core import slots
    from dashboard import opshtml
    rgb = slots.color("sub", 0)
    brief = O.gut(real, rgb, g="b1", web=True)
    assert "system-reminder" not in opshtml.op_items([brief], "sid")[0]["html"]
    assert "Review the clients subtree" in opshtml.op_items([brief], "sid")[0]["html"]
    # a TEAMMATE's spawn record is nothing BUT reminders (its instructions arrive as
    # mail): the body op is dropped, so the launch note has no empty panel to open
    only = O.gut("<system-reminder>roster</system-reminder>", rgb, g="b2", web=True)
    assert opshtml.op_items([only], "sid") == []
    # an UNSTAMPED gut (a command's output) is left exactly as it is
    out = O.gut("<system-reminder>quoted in a grep hit</system-reminder>", rgb, g="b3")
    assert "system-reminder" in opshtml.op_items([out], "sid")[0]["html"]
    # …and the page does not offer a click that reveals nothing
    code, ses = _get(dash + "/static/app.05-session.js")
    assert code == 200
    assert "if (!b.body.childElementCount) return;" in ses


def test_a_bodiless_launch_note_is_dropped_from_history():
    """The other half of the roster record (see the producer's own test in L1c): a
    launch opens the agent's transcript with TWO user records, and the second is
    nothing but the addressable-teammates <system-reminder>. Live sessions no longer
    paint that block at all, but the ops of a PARKED or long-running session are on
    disk and no restart can re-stamp them — so the feed kept showing two identical
    `Agent "X" launched` notes, one of which expanded onto nothing ("why one is
    expandable where I can see the initial prompt and the other is not"): dropping
    the reminder BODY (the sibling test above) left the header behind.

    So the read side drops the header too, when the body beside it renders empty —
    and, deliberately, only then: a header whose body is not in this batch is
    UNKNOWN, not empty, and must survive."""
    from core import ops as O
    from core import slots
    from core import streamfmt as SF
    from dashboard import opshtml
    rgb = slots.color("sub", 0)

    def pair(text, g):
        return [SF.chip("Explore", *SF.MARK_PROMPT, rgb, g=g, web=True,
                        note='Agent "Explore" launched'),
                O.gut(text, rgb, g=g, web=True)]

    # the roster record: header AND body gone, so the launch is one note, not two
    assert opshtml.op_items(pair("<system-reminder>roster</system-reminder>", "b1"),
                            "sid") == []
    # the real brief keeps both halves — the note and the click that opens it
    items = opshtml.op_items(pair("Find every call site of pick().", "b2"), "sid")
    assert len(items) == 2
    assert 'Agent &quot;Explore&quot; launched' in items[0]["html"]
    assert "Find every call site" in items[1]["html"]
    # a header ALONE (its body cut off the end of this window) still shows: the drop
    # may never be a guess about an op it cannot see
    alone = opshtml.op_items(pair("Find every call site of pick().", "b3")[:1], "sid")
    assert len(alone) == 1 and 'launched' in alone[0]["html"]
    # …and the same for a ⇠ result, the other block whose point is its body
    res = [SF.chip("Explore", *SF.MARK_RESULT, rgb, g="b4", web=True,
                   note='Agent "Explore" finished'),
           O.gut("<system-reminder>nothing to report</system-reminder>", rgb,
                 g="b4", web=True)]
    assert opshtml.op_items(res, "sid") == []


def test_hiding_a_row_beats_its_own_layout_rule(dash):
    """The bug three JS fixes chased and none could reach: `.vhide`/`.fhide` are
    ONE-CLASS selectors, and so is every stream row's own rule — some of which set
    `display` (`.ol` is `display: flex`). Equal specificity means the CASCADE
    decided it by source order, and `.ol` is declared BELOW the hide classes, so a
    loose chip row was never hidden however correctly the page marked it: a
    subagent launch header and a `●` mail row stayed on screen in focus mode with
    `.vhide` on them, while `.blk` cards (no display of their own) vanished
    properly. The JS harness cannot catch this — it never applies CSS.

    So the rule is `display: none !important`, the one place this file allows it.
    Checked as a PROPERTY of the stylesheet rather than a string match on one
    selector: no row rule may out-cascade a hide, whichever is declared first."""
    code, css = _get(dash + "/static/style.css")
    assert code == 200
    rules = re.findall(r"\n(\.[^\n{]+?)\s*\{([^}]*)\}", css)

    hides = [(sel, body) for sel, body in rules
             if sel.strip() in (".fhide", ".vhide")]
    assert len(hides) == 2, "both hide axes must be declared"
    for sel, body in hides:
        assert "display: none !important" in body, \
            "%s must outrank any row's own display" % sel

    # …and the hazard is real, not hypothetical: at least one row class DOES set
    # display and IS declared after them (if that ever stops being true, the
    # !important is still what keeps the next one safe)
    order = {sel.strip(): i for i, (sel, _b) in enumerate(rules)}
    competing = [sel.strip() for sel, body in rules
                 if "display:" in body and re.fullmatch(r"\.[a-z0-9]+", sel.strip())
                 and "none" not in body]
    assert any(order[c] > order[".vhide"] for c in competing), \
        "expected a later row rule with its own display (the reason for !important)"


def test_the_run_rail_outranks_a_rows_own_margin(dash):
    """The same trap as the hide above, one axis over — and it shipped, because the
    rail's own comment said the collision was settled by source order. It was, for
    `.blk` (one class): `.stream > .vrun` is two and wins. But an AGENT NOTE's box is
    `.stream > .blk[data-note]` — THREE — and `[data-open="1"]` makes it four, so its
    `margin` SHORTHAND reset the rail's `margin-left` to 0 and reopened the vertical
    gaps it closes. Expanding a run of agent notes then put every note 13px left of
    every other member with the rail in disjoint segments ("the alignment of elements
    is off when I expand the summaries"). No JS bug: `.vrun` was on the right nodes
    all along, which is why only a CSS-level check can catch it.

    So the rail's geometry is `!important`, and this is the PROPERTY: no `.stream >`
    row rule may out-cascade it, however specific it is or wherever it sits."""
    code, css = _get(dash + "/static/style.css")
    assert code == 200
    rules = re.findall(r"\n(\.[^\n{]+?)\s*\{([^}]*)\}", css)

    rail = [body for sel, body in rules if sel.strip() == ".stream > .vrun"]
    assert len(rail) == 1, "the run rail needs exactly one owning rule"
    assert re.search(r"margin:[^;]*!important", rail[0]), \
        "the rail's margins must outrank any row's own box"
    # the run's oldest member closes it, and must outrank the (now important) rule above
    last = [body for sel, body in rules if sel.strip() == ".stream > .vrun.vrun-last"]
    assert len(last) == 1 and re.search(r"margin-bottom:[^;]*!important", last[0]), \
        "vrun-last's margin must be important too, or the rail rule swallows it"

    # …and the hazard is real, not hypothetical: at least one STREAM ROW rule sets a
    # margin at HIGHER specificity than the rail (counting classes + attributes, which
    # is what decides it here — no ids anywhere in this file)
    def spec(sel):
        return len(re.findall(r"\.[a-z0-9-]+|\[[^\]]+\]", sel))

    railspec = spec(".stream > .vrun")
    competing = [sel.strip() for sel, body in rules
                 if sel.strip().startswith(".stream >")
                 and re.search(r"(^|;|\s)margin(-left)?:", body)
                 and spec(sel) > railspec]
    assert competing, \
        "expected a stream row rule with its own margin at higher specificity " \
        "(the reason for !important)"


def test_system_bubble_is_styled_and_is_not_a_rewind_target(dash):
    """The ⚙ SYSTEM flavour has to be DRAWN — a distinct, deliberately neutral
    colour (core/ops.py's SLATE, one `--sys` var rather than a hex per rule), so
    an injected turn does not read as your gold prompt. And every affordance that
    treats a bubble as YOUR prompt must exclude it: the rewind picker's outline
    and click target, and the take-back's "newest prompt" lookup — which would
    otherwise delete a system bubble that happens to be newer."""
    code, css = _get(dash + "/static/style.css")
    assert code == 200
    assert re.search(r"--sys:\s*#", css), "the system hue needs one owner in :root"
    rules = _css_rules(css)
    assert "var(--sys)" in rules[".msg.prompt.sys"]
    assert "var(--sys)" in rules[".msg.prompt.sys .who"]
    # the picker's own outline skips a system bubble
    assert ".rwpick .msg.prompt:not(.sys)" in rules

    code, ctl = _get(dash + "/static/app.10-control.js")
    assert code == 200
    assert '.msg.prompt:not(.sys)"' in ctl
    assert ctl.count(".msg.prompt:not(.sys)") == 2, "take-back AND pick-mode click"


def test_injected_user_turns_are_flagged_not_rendered_as_yours(dash, tmp_path):
    """Claude Code writes some turns in the USER's shape without the human
    typing them, and marks them `isMeta`: a Stop hook's blocking feedback, a
    resume nudge, and — the noisiest — a SKILL LOAD, whose whole SKILL.md body
    arrives as an isMeta text block right after the Skill tool_result. They used
    to render as "YOU" bubbles (a hook's feedback, or an entire skill, attributed
    to the user). `<`-wrapped envelopes were already dropped; these are bare
    prose, so only the flag can tell them apart. Carried through parse_line →
    conversation → the wire item, where the view modes act on it."""
    from plugins.claude_code import transcript as TR

    def line(**kw):
        return json.dumps(kw)

    # parentUuid CHAINED: prompt-bearing records sharing one parent are a
    # re-parented fork, i.e. a discarded branch (*Discarded prompts*), and the
    # prune would legitimately drop all but the last.
    real = line(type="user", message={"role": "user", "content": "do the thing"},
                uuid="u1", timestamp="2026-07-25T10:00:00.000Z")
    hook = line(type="user", isMeta=True, uuid="u2", parentUuid="u1",
                timestamp="2026-07-25T10:00:01.000Z",
                message={"role": "user", "content": "Stop hook feedback:\nwiki check"})
    skill = line(type="user", isMeta=True, uuid="u3", parentUuid="u2",
                 timestamp="2026-07-25T10:00:02.000Z",
                 message={"role": "user", "content": [
                     {"type": "text",
                      "text": "Base directory for this skill: /x/.claude/skills/k\n\nbody"}]})
    # parse_line carries the flag on BOTH shapes (plain string, and the list
    # content a skill body arrives in)
    assert TR.parse_line(real)["meta"] is False
    assert TR.parse_line(hook)["meta"] is True
    assert TR.parse_line(skill)["meta"] is True

    tf = tmp_path / "t.jsonl"
    tf.write_text(real + "\n" + hook + "\n" + skill + "\n", encoding="utf-8")
    recs, _pos = TR.conversation(str(tf))
    got = [(r["kind"], r.get("meta"), r["text"].split("\n")[0][:34]) for r in recs]
    assert got == [
        ("prompt", None, "do the thing"),
        ("prompt", True, "Stop hook feedback:"),
        ("prompt", True, "Base directory for this skill: /x/"),
    ], got
    # …and it reaches the page on the wire item the view modes read
    items = DS.mirror.conv_items(recs)
    assert [it.get("meta") for it in items] == [None, 1, 1]
    # …AND the bubble itself says so: verbose is the one mode that shows these,
    # and there they read ⚙ SYSTEM in their own colour, never YOU
    assert 'class="msg prompt sys"' in items[1]["html"]
    assert "⚙ system" in items[1]["html"]
    assert 'class="msg prompt"' in items[0]["html"]      # yours is untouched


def test_another_sessions_teammate_mail_is_a_system_turn(dash, tmp_path):
    """Teammate mail — `Another Claude session sent a message:` wrapping a peer's
    <teammate-message> plus Claude Code's own "permission laundering" instruction
    — is written by Claude Code, not typed by the human. It reached the feed as a
    YOU bubble in EVERY mode (the report): it carries no isMeta, no interrupt id,
    nothing structural at all, so the envelope's anchored SHAPE is the mark
    (transcript._TEAM_ENVELOPE). End to end: parse → conversation → wire item →
    bubble."""
    from plugins.claude_code import transcript as TR

    env = ("Another Claude session sent a message:\n"
           '<teammate-message teammate_id="rev-observe" color="purple">\n'
           '{"type":"idle_notification","from":"rev-observe"}\n</teammate-message>\n\n'
           "This came from another Claude session — not typed by your user…")
    mine = json.dumps({"type": "user", "uuid": "u1",
                       "timestamp": "2026-07-27T02:00:00.000Z",
                       "message": {"role": "user", "content": "review the diff"}})
    mail = json.dumps({"type": "user", "uuid": "u2", "parentUuid": "u1",
                       "userType": "external",          # …as the real records are
                       "timestamp": "2026-07-27T02:14:05.000Z",
                       "message": {"role": "user", "content": env}})
    tf = tmp_path / "t.jsonl"
    tf.write_text(mine + "\n" + mail + "\n", encoding="utf-8")
    recs, _pos = TR.conversation(str(tf))
    assert [(r["kind"], r.get("meta")) for r in recs] == [
        ("prompt", None), ("prompt", True)]

    items = DS.mirror.conv_items(recs)
    assert [it.get("meta") for it in items] == [None, 1]   # default/focus hide it
    assert "⚙ system" in items[1]["html"]                  # verbose relabels it
    # and it is no longer a rewind target / a ↑-history entry (both read data-txt)
    assert "data-txt" not in items[1]["html"]


def test_load_older_keeps_its_promise_in_a_collapsing_mode(dash):
    """"load older · 40 more" must deliver 40 more things to READ, not 40 raw
    blocks that collapse to two lines — the reported bug. The server counts
    BLOCKS and cannot do better: what a page leaves visible depends on the mode
    and on runs that merge across the page boundary, both of which only the
    client knows. So `loadOlder` loops until the visible count has risen by its
    target, sizing each next page at the observed yield.

    Driven through the real loop in tests/jsdom/viewmode.js against a stubbed
    /history (see that file for the two content shapes and why)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("no node on PATH")
    r = subprocess.run(
        [node, os.path.join(REPO, "tests", "jsdom", "viewmode.js"),
         os.path.join(REPO, "dashboard", "static", "app.05-session.js")],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)

    # the page-size policy: aim at the shortfall at the observed yield, reach for
    # the ceiling when a page yielded nothing, never go below one page
    assert d["pageSize"] == {"yielded2of40": 400, "yielded10of40": 120,
                             "yieldedNothing": 400, "alreadyThere": 40}

    f = d["fills"]
    # VERBOSE is untouched: one page IS 40 visible items, so no extra request
    assert f["verbose"]["pages"] == 1 and f["verbose"]["gained"] == 40
    # FOCUS over realistic history reaches the full 40 — in 2 requests, because
    # the second is sized from the first's yield rather than creeping by 40
    assert f["focus"]["gained"] >= 40 and f["focus"]["pages"] <= 3
    assert f["focus"]["asked"][0] == 40 and f["focus"]["asked"][1] > 40
    # a PATHOLOGICAL all-commands stretch cannot raise the visible count at all
    # (every block merges into the run already at the boundary): the loop must
    # spend its budget and stop cleanly, never spin, and must leave the button
    # usable again
    assert f["allCommands"]["pages"] == 6      # == OLDER_TRIES
    assert f["allCommands"]["stuck"] is False
    # and exhausted history stops it BEFORE the budget, on the same clean exit
    assert f["exhausted"]["pages"] == 2 and f["exhausted"]["stuck"] is False



def test_switching_into_a_collapsing_mode_fills_the_window(dash):
    """Two different promises, often confused: the "load older · 40 more" BUTTON
    aims at 40 visible items, while switching modes only tops the window up to
    VIEW_FILL_MIN — collapsing a command-heavy tail can leave two lines on screen,
    and the switch pulls enough history to have something to read.

    Switching INTO verbose must pull nothing (it hides nothing, so the window is
    already as full as the loaded data allows). The floor is 15, measured: at 6 a
    switch to focus left ~11 visible on a real session (a third of a screen); 15
    costs one more request for ~25, and 20 buys nothing 15 didn't."""
    node = shutil.which("node")
    if not node:
        pytest.skip("no node on PATH")
    r = subprocess.run(
        [node, os.path.join(REPO, "tests", "jsdom", "viewmode.js"),
         os.path.join(REPO, "dashboard", "static", "app.05-session.js")],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)

    code, src = _get(dash + "/static/app.05-session.js")
    assert code == 200
    floor = int(re.search(r"const VIEW_FILL_MIN = (\d+);", src).group(1))
    assert floor >= 15, "a switch must leave more than a couple of lines"
    assert d["switchFocus"]["visible"] >= floor, d["switchFocus"]
    assert d["switchFocus"]["fills"] == 1, "one fill should be enough"
    # …and switching to verbose pulls nothing at all
    assert d["switchVerbose"] == {"visible": 2, "pages": 0, "fills": 0}

def test_expanded_run_rail_is_styled_as_one_group(dash):
    """The rail the engine marks has to be DRAWN, and drawn as a continuous
    group: the open summary is the header (rounded on top only, its bottom margin
    closed) and each revealed block indents under it sharing the rail colour,
    with the vertical gaps closed so the line does not break between cards. These
    rules must also come AFTER the `.stream > .opl/.ol/...` card rules they
    override — equal specificity, so source ORDER is what decides there. Order is
    NOT enough against a more specific row rule: that is the sibling property test,
    test_the_run_rail_outranks_a_rows_own_margin."""
    code, css = _get(dash + "/static/style.css")
    assert code == 200
    assert "--runrail:" in css, "the rail colour needs one owner in :root"
    head = re.search(r"\.stream > \.vsum\[data-open=\"1\"\] \{([^}]*)\}", css)
    run = re.search(r"\.stream > \.vrun \{([^}]*)\}", css)
    assert head and run
    assert "var(--runrail)" in head.group(1) and "var(--runrail)" in run.group(1)
    assert "margin: 7px 0 0" in head.group(1), "the header must close its gap"
    # one shorthand: the block-axis gaps closed AND the indent, in a single
    # declaration that a row rule's own `margin` shorthand cannot half-override
    assert re.search(r"margin:\s*0 0 0 13px", run.group(1)), \
        "the rail must close its gaps and indent in one shorthand"
    cards = css.index(".stream > .opl, .stream > .ol")
    assert cards < css.index(".stream > .vrun"), "the rail must override the cards"


def test_interrupt_annotation_is_flagged_by_its_id_not_its_text(dash):
    """`[Request interrupted by user]` (and the `… for tool use]` form) is another
    user-SHAPED record Claude Code writes itself, so it was rendering as a YOU
    bubble. It is NOT isMeta — measured across the corpus — but it does carry
    `interruptedMessageId`, the id of the message it cut off, and THAT is what
    flags it.

    Deliberately not matched on the annotation's text: a Read of a doc that
    mentions the marker, a grep hit, or a conversation about it is textually
    identical, which is the exact false-positive class that once flipped tab
    colours mid-turn (tabstatus.is_interrupt_line). An id-bearing field can't be
    quoted."""
    from plugins.claude_code import transcript as TR
    for content in ("[Request interrupted by user]",
                    [{"type": "text", "text": "[Request interrupted by user for tool use]"}]):
        rec = TR.parse_line(json.dumps({
            "type": "user", "interruptedMessageId": "msg_1",
            "message": {"role": "user", "content": content}}))
        assert rec["meta"] is True, content
    # a real message that merely QUOTES the marker stays yours
    quote = TR.parse_line(json.dumps({
        "type": "user",
        "message": {"role": "user",
                    "content": "why is [Request interrupted by user] shown?"}}))
    assert quote["kind"] == "prompt" and quote["meta"] is False


def test_compaction_summary_is_flagged_by_its_field_not_its_text(dash, tmp_path):
    """The post-/compact summary is the THIRD user-shaped record Claude Code
    writes itself: a `compact_boundary` system line, then a `user` line carrying
    the whole "This session is being continued…" recap as the new context. It is
    neither isMeta nor interrupt-flagged, so it rendered as a YOU bubble holding
    thousands of words — six of them, 11k-17k chars each, in one real session.
    `isCompactSummary` flags it, so the non-verbose modes drop it like any other
    injected turn (the flag reaches the page as the item's `meta`).

    Matched on the boolean field, never the recap's opening sentence: that
    sentence is ordinary English any conversation ABOUT compaction reproduces
    verbatim — the same false-positive class as the interrupt annotation."""
    from plugins.claude_code import transcript as TR
    recap = "This session is being continued from a previous conversation.\n\nSummary:\n1. …"

    def line(**kw):
        return json.dumps(kw)

    real = line(type="user", uuid="u1", timestamp="2026-07-25T10:00:00.000Z",
                message={"role": "user", "content": "do the thing"})
    bound = line(type="system", subtype="compact_boundary", uuid="u2",
                 timestamp="2026-07-25T10:00:01.000Z",
                 compactMetadata={"trigger": "manual", "preTokens": 9},
                 content=None)
    summ = line(type="user", isCompactSummary=True, uuid="u3", parentUuid="u2",
                timestamp="2026-07-25T10:00:02.000Z",
                message={"role": "user", "content": recap})
    assert TR.parse_line(real)["meta"] is False
    assert TR.parse_line(summ)["meta"] is True
    # the same shape as a skill body (list content) is flagged too
    assert TR.parse_line(line(type="user", isCompactSummary=True,
                              message={"role": "user", "content": [
                                  {"type": "text", "text": recap}]}))["meta"] is True
    # …and a message that merely QUOTES the recap's opening stays yours
    quote = TR.parse_line(line(type="user", message={"role": "user", "content":
                               "why does " + recap + " show as mine?"}))
    assert quote["kind"] == "prompt" and quote["meta"] is False

    tf = tmp_path / "t.jsonl"
    tf.write_text(real + "\n" + bound + "\n" + summ + "\n", encoding="utf-8")
    recs, _pos = TR.conversation(str(tf))
    # the boundary itself was never in this stream (only the drill-down timeline
    # renders it); the summary is, flagged
    assert [(r["kind"], r.get("meta")) for r in recs] == [
        ("prompt", None), ("prompt", True)], recs
    assert [it.get("meta") for it in DS.mirror.conv_items(recs)] == [None, 1]


def test_view_mode_syncs_to_an_open_page_on_another_device(dash):
    """The mode has always been stored SERVER-side and per-session, so opening
    the session anywhere picks it up (`view_mode` on the payload). What this pins
    is the other half: a page ALREADY open follows a switch made elsewhere, via a
    `view-mode` SSE event on the slow cadence — same shape as the global alerts
    toggle's `notify-config`. Without it, the phone's switch left the desktop on
    the old density until a reload."""
    A.session_start({"session_id": "vmsse", "cwd": "/w", "transcript_path": ""})
    prefs.set_view_mode("vmsse", "focus")
    seen = []
    r = urllib.request.urlopen(dash + "/events/session/vmsse", timeout=20)
    try:
        deadline = time.time() + 15
        event = None
        while time.time() < deadline:
            raw = r.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event == "view-mode":
                seen.append(json.loads(line.split(":", 1)[1]).get("mode"))
                if len(seen) == 1:            # the initial state…
                    prefs.set_view_mode("vmsse", "verbose")   # …then a switch
                if len(seen) == 2:
                    break
    finally:
        r.close()
    assert seen == ["focus", "verbose"], seen


def test_new_session_form_phases_hand_off_everything(tmp_path):
    """The new-session form, EXECUTED rather than grepped: tests/jsdom/
    newsession.js builds the real modal from app.09-newsession.js over the
    shared DOM shim, then drives the two gestures that reach across phases.

    openNewSession was one 344-line function — the longest in the repo by a
    factor of three, and the shape docs/styleguide.md forbids on the Python side
    ("long entry main()s are named phases"). Splitting it means the phases hand
    each other a context object instead of sharing one closure scope, and a
    missed hand-off is invisible to every other check: `node --check` sees
    syntax, not scope, and a grep cannot tell that nsDirField reads a
    `prefillCwd` nobody destructured. It is a ReferenceError that fires the
    first time a user opens the form — which is exactly what this caught while
    the split was being made (twice).

    So the assertions are the hand-offs: the form builds all seven field rows,
    the fresh/resume toggle (whose handler lives one phase back from the phase
    that calls it) runs both ways, and the launch — which reads dir, fresh,
    picker, prompt, dictation, the attachment tray, account, model and effort,
    i.e. five earlier phases at once — actually POSTs. Skipped without `node`
    (docs/testing.md)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("no node on PATH")
    r = subprocess.run(
        [node, os.path.join(REPO, "tests", "jsdom", "newsession.js"),
         os.path.join(REPO, "dashboard", "static", "app.09-newsession.js")],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["ok"], d["errors"]
    # every phase produced its rows: directory, resume, fresh, account,
    # model, effort, prompt
    assert d["rows"] == 7, d
    assert d["panel"] == 1 and d["prompt"] == 1 and d["actions"] == 1
    # …and the launch reached the endpoint with the directory the form opened on
    assert d["posted"] == ["/api/sessions/new"], d
    assert d["launch_cwd"] == "/tmp/proj", d
