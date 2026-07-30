# tests/test_l1j_facets.py — the SESSION-STATE FACETS, both hosts.
#
# `plugins.tasks` / `plugins.compacting` / `plugins.fg_running`: three things a
# session is DOING right now that the dashboard used to read as raw kv /
# hand-off rows off the state DB, by NAME, asking no host at all. This file pins
# the two halves of the fix:
#
#   · each host's own read half answers its OWN records (claude_code off the
#     hook-written kv, codex off the ones its dispatcher / rollout stream
#     write), and
#   · the fan-out ROUTES by ownership — a codex session must never be answered
#     by Claude's parser and vice versa, which is the whole reason these stopped
#     being `_first`.
#
# Seeded through the product writers (core.state hand_put/kv_set, the real codex
# dispatcher entry) — never by hand-building a row shape a test could get wrong
# in the same way the code does.
import os
import subprocess
import sys

from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

import plugins                                        # noqa: E402
from core import paths as P                           # noqa: E402
from core import state as S                           # noqa: E402
from plugins import claude_code as PCC                # noqa: E402
from plugins import codex as PCX                      # noqa: E402
from plugins.claude_code import cmd_pre as CP         # noqa: E402


def _pin_tmp(monkeypatch, tmp_path):
    """Point the mirror-log/park path derivation at this test's tmpdir, the way
    every state-DB test does — so a state DB is created here and nowhere near
    the developer's own /tmp."""
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    monkeypatch.setattr(P, "HISTORY_DIR", str(tmp_path / "park"))


# ------------------------------------------------- claude_code's own read halves

def test_claude_fg_running_reports_the_in_flight_command_block(monkeypatch,
                                                               tmp_path):
    """cmd_pre.fg_running() answers WHICH mirror block is executing and since
    when, off the take-once `fg-live` hand-off cmd_pre itself writes (its `tid`
    IS the block's copy-group id). It must PEEK — consuming here would strand
    PostToolUse's finish chip.

    This test moved here from test_l0_sessionapi with its subject: the body was
    core's until P4, when it went to live beside the writer whose protocol it
    spells out."""
    _pin_tmp(monkeypatch, tmp_path)
    log = P.mirror_log("fg-sess")
    assert CP.fg_running("nobody") is None          # no state DB, none conjured

    rec = {"src": log + ".out", "own": True, "pid": os.getpid(),
           "done": log + ".done", "tid": "toolu_42", "ts": 1000.0}
    assert S.hand_put(log, CP.HAND_KEY, rec)
    assert CP.fg_running("fg-sess") == {"g": "toolu_42", "start_ts": 1000.0}
    # a READER: the record survives for its real consumer (cmd_fmt's hand_take)
    assert CP.fg_running("fg-sess") == {"g": "toolu_42", "start_ts": 1000.0}
    assert S.hand_peek(log, CP.HAND_KEY) == rec

    # an ABANDONED record (a cancelled command fires no hook, so nothing takes
    # it) reads as not-running once its tailer pid is dead — same staleness
    # verdict cmd_pre reaches before it clears the record
    dead = subprocess.Popen(["true"])
    dead.wait()
    S.hand_put(log, CP.HAND_KEY, dict(rec, pid=dead.pid))
    assert CP.fg_running("fg-sess") is None
    # a pre-`ts` producer's record has no start to tick from
    S.hand_put(log, CP.HAND_KEY, {"pid": os.getpid(), "tid": "toolu_43"})
    assert CP.fg_running("fg-sess") is None
    S.hand_del(log, CP.HAND_KEY)
    assert CP.fg_running("fg-sess") is None


def test_claude_compacting_read_half_is_raw_and_the_ttl_is_the_readers(
        monkeypatch, tmp_path):
    """compact_fmt.compacting() hands back the latch VERBATIM — no expiry. The
    clock belongs to the dashboard (config.COMPACT_MAX_S) because the hook that
    armed the latch has exited and cannot retract it, and because BOTH hosts
    write this shape: a provider that aged its own record out would put the
    policy in two places and let them disagree."""
    from dashboard import config
    from dashboard.read.session import session_compacting
    from plugins.claude_code import compact_fmt as CF

    _pin_tmp(monkeypatch, tmp_path)
    log = P.mirror_log("cmp-sess")
    S.kv_set(log, CF.KEY, {"ts": 1.0, "trigger": "manual"})   # ancient
    assert CF.compacting("cmp-sess") == {"ts": 1.0, "trigger": "manual"}
    # …and the READER is the one that ages it out
    assert session_compacting("cmp-sess") is None
    assert config.COMPACT_MAX_S > 0


def test_claude_tasks_read_half_unwraps_the_kv_envelope(monkeypatch, tmp_path):
    """task_fmt.tasks() unwraps `{"tasks": [...]}` and treats an empty list as
    no card at all (None), which is what keeps the pinned card hidden."""
    from plugins.claude_code import task_fmt as TF

    _pin_tmp(monkeypatch, tmp_path)
    log = P.mirror_log("tsk-sess")
    assert TF.tasks("tsk-sess") is None            # no state DB
    S.kv_set(log, TF.KEY, {"tasks": []})
    assert TF.tasks("tsk-sess") is None            # empty == no card
    S.kv_set(log, TF.KEY, {"tasks": [{"id": "1", "status": "completed"}]})
    assert TF.tasks("tsk-sess") == [{"id": "1", "status": "completed"}]


# --------------------------------------------------------- the ownership routing

class _FakeRow:
    """Stand-in for the audit `sessions` row the sid-keyed fan-outs route on —
    the only thing _first_owner reads is its transcript_path."""

    def __init__(self, tpath):
        self.tpath = tpath

    def __call__(self, sid):
        return {"transcript_path": self.tpath}


def _route_to(monkeypatch, tpath):
    from core import sessionapi as API
    monkeypatch.setattr(API, "session_row", _FakeRow(tpath))


def test_the_facets_route_to_the_owning_host_not_the_first_one(monkeypatch,
                                                               tmp_path):
    """A facet read is a fact about ONE session, and a session has exactly one
    host — so the fan-out asks the OWNER. The regression this pins: with
    first-plugin-wins, claude_code's reader answered for a codex session (a
    None that looks exactly like "nothing is running")."""
    _pin_tmp(monkeypatch, tmp_path)
    asked = []

    def _spy(host, method):
        def answer(sid, sdb=None):
            asked.append((host, method))
            return "claude" if host == "claude_code" else "codex"
        return answer

    for name in ("tasks", "compacting", "fg_running"):
        monkeypatch.setattr(PCC, name, _spy("claude_code", name), raising=False)
        monkeypatch.setattr(PCX, name, _spy("codex", name), raising=False)

    _route_to(monkeypatch, "/x/.claude/projects/p/abc.jsonl")   # a Claude transcript
    assert plugins.tasks("s") == "claude"
    assert plugins.compacting("s") == "claude"
    assert plugins.fg_running("s") == "claude"
    assert {h for h, _ in asked} == {"claude_code"}

    asked.clear()
    _route_to(monkeypatch, "/x/.codex/sessions/2026/07/30/rollout-1-s.jsonl")
    assert plugins.tasks("s") == "codex"
    assert plugins.compacting("s") == "codex"
    assert plugins.fg_running("s") == "codex"
    assert {h for h, _ in asked} == {"codex"}


def test_an_unprovable_session_behaves_as_the_default_host(monkeypatch,
                                                           tmp_path):
    """Fail OPEN, exactly like session_caps: a session whose transcript path is
    empty or unclaimed is answered by the DEFAULT host. A daemon-origin session
    has no transcript yet and must keep working."""
    _pin_tmp(monkeypatch, tmp_path)
    seen = []
    monkeypatch.setattr(PCC, "tasks",
                        lambda sid, sdb=None: seen.append(sid) or ["t"],
                        raising=False)
    _route_to(monkeypatch, "")
    assert plugins.tasks("s") == ["t"]
    assert seen == ["s"]
    assert plugins.default_host() == "claude_code"


def test_the_host_hint_skips_the_routing_lookup(monkeypatch, tmp_path):
    """The SSE fast cadence passes the owner it already resolved. That hint must
    ROUTE — and must cost no session_row walk, which is the only reason it
    exists (two fast channels × one audit query per tick, to re-answer what the
    slow prologue settled)."""
    _pin_tmp(monkeypatch, tmp_path)
    from core import sessionapi as API

    def _boom(sid):
        raise AssertionError("session_row walked despite the host hint")

    monkeypatch.setattr(API, "session_row", _boom)
    monkeypatch.setattr(PCX, "compacting",
                        lambda sid, sdb=None: {"ts": 5.0, "trigger": "manual"},
                        raising=False)
    assert plugins.compacting("s", None, "codex") == {"ts": 5.0,
                                                      "trigger": "manual"}


def test_a_declining_host_answers_none_rather_than_the_other_hosts_shape(
        monkeypatch, tmp_path):
    """codex declines `tasks` (no task-list tool exists in its rollout
    vocabulary). The fan-out must then answer None — the card stays hidden —
    and must NOT fall through to the host that does have one."""
    _pin_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(PCC, "tasks",
                        lambda sid, sdb=None: [{"id": "1"}], raising=False)
    monkeypatch.delattr(PCX, "tasks", raising=False)
    _route_to(monkeypatch, "/x/.codex/sessions/2026/07/30/rollout-1-s.jsonl")
    assert plugins.tasks("s") is None


def test_core_no_longer_spells_the_fg_hand_off_protocol():
    """core/sessionapi.py is tool-agnostic by the dependency rule; fg_running was
    the deepest read-side violation of it (a hook's record shape, its take-once
    rule and its entry script's filename, all in core). It is gone, and the
    registry answers instead."""
    from core import sessionapi as API

    assert not hasattr(API, "fg_running")
    assert callable(plugins.fg_running)
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "core", "sessionapi.py")).read()
    assert 'hand_peek_at(sdb, "fg-live")' not in src


def test_every_facet_provider_takes_the_state_db_hint():
    """All three ride a cadence that already holds the resolved state-DB path.
    A provider that ignored the hint would re-walk the adopt sid_chain (an
    audit-DB query) per tick — see read/meta.session_kv's own note."""
    import inspect

    for name in ("tasks", "compacting", "fg_running"):
        for p in plugins.all_plugins():
            fn = plugins.provider(p, name)
            if fn is None:
                continue
            params = list(inspect.signature(fn).parameters)
            assert params[:2] == ["sid", "sdb"], (
                "%s.%s must accept (sid, sdb=None); got %s"
                % (p.__name__, name, params))


# ------------------------------------------------------------ the CODEX halves

def _codex_host(monkeypatch, tmp_path, sid, win="42"):
    """Mark `sid` as a recorded STANDALONE codex host in a throwaway tab DB —
    the dispatcher's nested gate — and give it a live state DB."""
    from core import tabs
    monkeypatch.setattr(tabs, "TABDB", str(tmp_path / "tab.db"))
    tabs._RO_CONNS.clear()
    tabs.codex_host_mark(sid, win)
    S.connect(P.mirror_log(sid))          # the live DB the latch is written into
    return tabs


def test_codex_compact_hooks_arm_and_clear_the_same_latch(monkeypatch, tmp_path):
    """codex's Pre/PostCompact reach the SAME latch shape claude_code writes, so
    ONE reader animates both hosts' ctx bars. Driven through the real dispatcher
    (dispatch.route), not the facet function, because the nested gate is part of
    what is being pinned."""
    from plugins.codex import dispatch as D
    from plugins.codex import facets as FX

    _pin_tmp(monkeypatch, tmp_path)
    tabs = _codex_host(monkeypatch, tmp_path, "cx1")
    log = P.mirror_log("cx1")
    monkeypatch.setattr(D, "_tab", lambda *a: "")     # the tab half is not under test

    D.route({"hook_event_name": "PreCompact", "session_id": "cx1",
             "trigger": "manual"})
    rec = S.kv_get(log, FX.COMPACT_KEY)
    assert isinstance(rec, dict) and rec["trigger"] == "manual" and rec["ts"] > 0

    D.route({"hook_event_name": "PostCompact", "session_id": "cx1",
             "trigger": "manual"})
    assert S.kv_get(log, FX.COMPACT_KEY) is None
    tabs._RO_CONNS.clear()


def test_codex_compact_latch_says_what_it_did_in_the_audit(monkeypatch,
                                                           tmp_path):
    """Every hook path audits a DECISION saying what it chose — the compact_fmt
    precedent, and the only record of a latch nobody can see afterwards. BOTH
    subscribers' decisions ride the one row."""
    from plugins.codex import dispatch as D

    _pin_tmp(monkeypatch, tmp_path)
    tabs = _codex_host(monkeypatch, tmp_path, "cx2")
    monkeypatch.setattr(D, "_tab", lambda *a: "tab: painted")
    said = []
    monkeypatch.setattr(D.A, "hook_event",
                        lambda p, handler=None, decision="": said.append(decision))

    D.route({"hook_event_name": "PreCompact", "session_id": "cx2",
             "trigger": "manual"})
    D.route({"hook_event_name": "PostCompact", "session_id": "cx2",
             "trigger": "manual"})
    assert "compacting armed (manual)" in said[0]
    assert "compacting cleared (manual" in said[1]
    assert "tab: painted" in said[0]
    tabs._RO_CONNS.clear()


def test_a_nested_codex_run_never_latches_the_hosts_state_db(monkeypatch,
                                                             tmp_path):
    """THE nested guard. A `codex exec` inside a Claude session fires these very
    hooks, and its LOG would be the CLAUDE host's state DB — where `compacting`
    belongs to Claude's own PreCompact. Latching there would breathe the host's
    ctx bar for a compaction that is not its own."""
    from core import tabs
    from plugins.codex import dispatch as D
    from plugins.codex import facets as FX

    _pin_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(tabs, "TABDB", str(tmp_path / "tab.db"))
    tabs._RO_CONNS.clear()
    log = P.mirror_log("nested1")
    S.connect(log)                        # the host's live DB exists…
    D.route({"hook_event_name": "PreCompact", "session_id": "nested1",
             "trigger": "manual"})        # …and this run is NOT a marked host
    assert S.kv_get(log, FX.COMPACT_KEY) is None
    tabs._RO_CONNS.clear()


def test_a_codex_subagents_compact_event_is_ignored(monkeypatch, tmp_path):
    """MAIN SESSION ONLY: an event carrying an agent_id is a child's, and the
    bar this would animate is the main thread's (compact_fmt's own rule)."""
    from plugins.codex import facets as FX

    _pin_tmp(monkeypatch, tmp_path)
    log = P.mirror_log("cx3")
    S.connect(log)
    note = FX.on_compact({"hook_event_name": "PreCompact", "agent_id": "kid"},
                         "cx3")
    assert "agent_id" in note
    assert S.kv_get(log, FX.COMPACT_KEY) is None


def test_codex_fg_record_is_written_by_the_stream_and_names_the_block(
        monkeypatch, tmp_path):
    """The codex chip's record carries the MIRROR BLOCK's copy group, which is
    why the rollout stream writes it and the hook cannot: the hook's
    `tool_use_id` (`exec-<uuid>`), the rollout's `call_id` (`call_<…>`) and the
    block's group (an ops.new_group integer) are three disjoint id spaces
    (measured 2026-07-31). A record stamped with either of the first two would
    name no block and the chip would tick on nothing."""
    from plugins.codex import facets as FX

    _pin_tmp(monkeypatch, tmp_path)
    log = P.mirror_log("cx4")
    S.connect(log)
    FX.fg_open(log, "7", ts=1000.0)
    assert FX.fg_running("cx4") == {"g": "7", "start_ts": 1000.0}
    # PEEKS — the stream's own close is the consumer
    assert FX.fg_running("cx4") == {"g": "7", "start_ts": 1000.0}
    FX.fg_close(log, "7")
    assert FX.fg_running("cx4") is None


def test_codex_fg_close_only_takes_its_own_block(monkeypatch, tmp_path):
    """Matched on the copy group, so a later block's close can never eat an
    earlier command's record (the cross-wiring cmd_pre's match guard exists to
    prevent, restated for the stream)."""
    from plugins.codex import facets as FX

    _pin_tmp(monkeypatch, tmp_path)
    log = P.mirror_log("cx5")
    S.connect(log)
    FX.fg_open(log, "7", ts=1000.0)
    FX.fg_close(log, "9")                       # a different block
    assert FX.fg_running("cx5") == {"g": "7", "start_ts": 1000.0}
    FX.fg_close(log, "7")
    assert FX.fg_running("cx5") is None


def test_a_dead_codex_stream_retires_the_chip(monkeypatch, tmp_path):
    """A codex turn ABORTED mid-exec writes no closing record and fires no hook
    (`turn_aborted` is a rollout note, not an event we can wait on). The tailer
    pid is the backstop — the same one Claude's fg record carries."""
    from plugins.codex import facets as FX

    _pin_tmp(monkeypatch, tmp_path)
    log = P.mirror_log("cx6")
    S.connect(log)
    dead = subprocess.Popen(["true"])
    dead.wait()
    S.hand_put(log, FX.FG_KEY, {"tid": "7", "ts": 1000.0, "pid": dead.pid})
    assert FX.fg_running("cx6") is None


def test_the_codex_stream_stamps_the_block_it_just_painted():
    """The wiring itself: the STANDALONE register's exec opener hands fg_open the
    very group id it stamped on the block, and its closer takes it back. Pinned
    against the stream module's own source so a refactor that stops passing
    `gid` — leaving the chip anchored to nothing — fails here rather than
    shipping a chip that never appears."""
    import inspect
    from plugins.codex import stream

    assert "FX.fg_open(LOG, gid" in inspect.getsource(stream.Renderer._ro_exec), \
        "the standalone exec opener must stamp the block it just painted"
    assert 'FX.fg_close(LOG, pend["gid"])' in \
        inspect.getsource(stream.Renderer._exec_close), \
        "the exec closer must retire the chip for the block it closes"


# ------------------------------------------------- bug 19: who answers an agent

def test_an_agent_keyed_read_may_cross_hosts(monkeypatch, tmp_path):
    """THE limit of ownership routing, and the case that made it a RULE rather
    than a sweep. A codex run SIDECAR'd inside a Claude session is a CODEX agent
    under a CLAUDE_CODE sid — routing an agent-keyed question by the SESSION's
    owner asks Claude about a codex rollout, it declines, and the agent's
    conversation is lost. So `conversation` routes by ownership only for the
    session's OWN thread; with an agent_id it stays first-wins, where the id
    itself discriminates."""
    _pin_tmp(monkeypatch, tmp_path)
    _route_to(monkeypatch, "/x/.claude/projects/p/abc.jsonl")     # a CLAUDE session
    monkeypatch.setattr(PCC, "conversation",
                        lambda sid, pos=0, agent_id="": None, raising=False)
    monkeypatch.setattr(PCX, "conversation",
                        lambda sid, pos=0, agent_id="":
                        ([{"kind": "prompt"}], 9) if agent_id else None,
                        raising=False)
    # the session's own thread: its host answers (and codex is never asked)
    assert plugins.conversation("s", 0) is None
    # …but a codex CHILD of that Claude session still resolves
    got = plugins.conversation("s", 0, "rollout-2026-07-30T10-00-00-x")
    assert got == ([{"kind": "prompt"}], 9)


def test_ask_preamble_no_longer_answers_for_a_host_that_isnt_asked(
        monkeypatch, tmp_path):
    """The concrete shadowing bug 19 named: claude_code's ask_preamble returns
    '' for ANY sid — a non-None result, which under first-plugin-wins IS the
    answer and ends the fan-out before the owner is reached. Harmless only while
    codex declines the provider; a decline is not a design."""
    _pin_tmp(monkeypatch, tmp_path)
    _route_to(monkeypatch, "/x/.codex/sessions/2026/07/30/rollout-1-s.jsonl")
    monkeypatch.setattr(PCC, "ask_preamble", lambda sid, tid: "", raising=False)
    monkeypatch.setattr(PCX, "ask_preamble",
                        lambda sid, tid: "codex framing", raising=False)
    assert plugins.ask_preamble("s", "tid") == "codex framing"


def test_the_composer_queue_and_take_back_read_the_owning_hosts_prompts(
        monkeypatch, tmp_path):
    """The two NON-mirror consumers of `conversation` bug 19 named: the queued
    chip drains against DELIVERED prompts, and an early interrupt prefills the
    composer with the last one. Both must see a codex session's own prompts."""
    from dashboard.read import session as RS

    _pin_tmp(monkeypatch, tmp_path)
    rollout = "/x/.codex/sessions/2026/07/30/rollout-1-s.jsonl"
    _route_to(monkeypatch, rollout)
    monkeypatch.setattr(PCC, "conversation",
                        lambda sid, pos=0, agent_id="": None, raising=False)
    monkeypatch.setattr(PCX, "conversation",
                        lambda sid, pos=0, agent_id="":
                        ([{"kind": "prompt", "text": "ship it", "uid": "u1"}], 4),
                        raising=False)
    monkeypatch.setattr(RS, "session_kv",
                        lambda sid, key, sdb=None:
                        {"items": [{"text": "ship it"}, {"text": "later"}]}
                        if key == "composer-queue" else None)
    # the delivered chip drains; the undelivered one survives
    assert RS.composer_queue("s")["items"] == [{"text": "later"}]
    # …and the take-back prefill finds the prompt, not ("", "")
    assert RS.last_prompt_rec("s") == ("ship it", "u1")


# --------------------------------------------- bug 20: the SessionStart fan-out

def test_a_standalone_codex_host_runs_the_plugin_fan_out(monkeypatch):
    """plugins.on_session_start had NEVER run for a codex host — session.py
    spawned its watcher directly, so every cross-cutting plugin (otel) was
    invisible to codex sessions and codex's own provider was dead code on that
    path. It goes through the fan-out now, and the watcher is still started
    EXACTLY ONCE: the provider reads the standalone-host mark and picks the
    standalone role itself."""
    import plugins as REG
    from plugins.codex import session as CS

    calls = []
    monkeypatch.setattr(REG, "on_session_start",
                        lambda log, cwd, sid: calls.append((log, cwd, sid)))
    CS.plugins_start("/tmp/l.log", "/cwd", "sid9")
    assert calls == [("/tmp/l.log", "/cwd", "sid9")]
    assert not hasattr(CS, "spawn_watcher"), \
        "the direct watcher spawn must be gone, or the watcher starts twice"


def test_the_codex_provider_picks_standalone_mode_from_the_host_mark(
        monkeypatch, tmp_path):
    """WHICH watcher role is not the caller's business — it follows from whether
    this sid is a recorded standalone host (watch.py's argv[4] HOST_PID selects
    the role). Both hosts therefore call the same one line."""
    from core import tabs
    from plugins.codex import session as CS

    monkeypatch.setattr(tabs, "TABDB", str(tmp_path / "tab.db"))
    tabs._RO_CONNS.clear()
    monkeypatch.setattr(CS, "codex_pid", lambda: 4242)
    seen = []
    monkeypatch.setattr(PCX.subprocess, "run", lambda argv, **kw: seen.append(list(argv)))
    monkeypatch.setattr(PCX.os.path, "isfile", lambda p: True)

    PCX.on_session_start("/tmp/l.log", "/cwd", "plain")      # not a codex host
    assert len(seen[0]) == 5, "the SECONDARY watcher takes no host pid"

    tabs.codex_host_mark("host1", "42")
    PCX.on_session_start("/tmp/l.log", "/cwd", "host1")
    assert seen[1][-1] == "4242", "a standalone host's watcher needs its pid"
    tabs._RO_CONNS.clear()


# --------------------------------------------------------------- SSE parity

def test_the_sse_tick_resolves_the_owning_host_once(monkeypatch):
    """`_Tick.host` is the tick context's one answer to "whose session is this".
    Three readers needed it and each had been getting it wrong or not at all:
    the prompt-bubble command vocabulary, and the two fast facet channels."""
    from dashboard.http.sse import _Tick

    rollout = ("/h/.codex/sessions/2026/07/30/rollout-2026-07-30T10-00-00-"
               "11111111-2222-3333-4444-555555555555.jsonl")
    assert _Tick("s", {"transcript_path": rollout}).host == "codex"
    assert _Tick("s", {"transcript_path": "/p/abc.jsonl"}).host == "claude_code"
    # fail OPEN, exactly like session_caps: an unprovable path is the default
    assert _Tick("s", {}).host == plugins.default_host()


def test_the_effort_channel_and_the_payload_share_one_owner(monkeypatch):
    """Bug 1. The SSE `effort` channel used to push `plugins.effort_default()`
    FLAT — a cwd-keyed lookup, which cannot be ownership-gated (survey finding
    22: first-TRUTHY-wins, so the default host's saved settings answer for any
    session opened in that directory). A codex session showed its real rollout
    effort on load and had Claude's saved default pushed over it one slow tick
    later. Both now go through read/meta.session_effort.

    CLAUDE's value must be unchanged — this fix moves codex's only."""
    from dashboard.read.meta import session_effort

    monkeypatch.setattr(plugins, "effort_default",
                        lambda cwd, slug="": "high")     # Claude's saved setting
    monkeypatch.setattr(plugins, "effort",
                        lambda tpath: "low" if "codex" in tpath else "")

    claude_tp = "/p/11111111-2222-3333-4444-555555555555.jsonl"
    rollout = ("/h/.codex/sessions/2026/07/30/rollout-2026-07-30T10-00-00-"
               "11111111-2222-3333-4444-555555555555.jsonl")
    # unchanged for Claude: no per-transcript level exists, so the saved default
    assert session_effort(claude_tp, "/cwd", "c1") == "high"
    assert session_effort("", "/cwd", "c1") == "high"     # unprovable => default host
    # …and a codex session keeps its OWN level instead of inheriting that "high"
    assert session_effort(rollout, "/cwd", "c1") == "low"


def test_the_effort_channel_never_borrows_the_default_hosts_level(monkeypatch):
    """…and when the non-default host knows NO level, the answer is "" — never
    the default host's. Showing no level beats showing a borrowed one."""
    from dashboard.read.meta import session_effort

    monkeypatch.setattr(plugins, "effort_default", lambda cwd, slug="": "high")
    monkeypatch.setattr(plugins, "effort", lambda tpath: "")
    rollout = ("/h/.codex/sessions/2026/07/30/rollout-2026-07-30T10-00-00-"
               "11111111-2222-3333-4444-555555555555.jsonl")
    assert session_effort(rollout, "/cwd", "c1") == ""


def test_the_live_tick_tints_the_owning_hosts_command_vocabulary():
    """Bug 2. `cmd_names(ctx.cwd)` was called with NO host, so the registry fell
    back to the default one: a codex session's LIVE prompt bubbles tinted
    Claude's `/goal` vocabulary while its reloaded BACKLOG — which goes through
    session_cmds, and does resolve the owner — tinted codex's `/plan`. Same
    session, same tick, two vocabularies.

    Pinned at the call site: the value is a per-tick local inside a streaming
    loop with no return, so the SOURCE is where the contract lives."""
    import inspect
    from dashboard.http import sse

    # CODE only — the comments above these call sites quote the old spellings to
    # say why they are wrong, and a grep that can't tell the two apart would
    # forbid explaining the bug it pins.
    src = "\n".join(ln for ln in inspect.getsource(sse._SseMixin).splitlines()
                    if not ln.lstrip().startswith("#"))
    assert "cmd_names(ctx.cwd, ctx.host)" in src, \
        "the live tick must tint the OWNING host's command vocabulary"
    assert "cmd_names(ctx.cwd)" not in src.replace("cmd_names(ctx.cwd, ctx.host)", "")
    assert "session_effort(ctx.tpath" in src, \
        "the effort channel must go through the one owner, not effort_default"
    assert "plugins.effort_default(" not in src, \
        "effort_default is cwd-keyed and must not be read here directly"


def test_the_fast_facet_channels_pass_the_tick_context(monkeypatch):
    """Both ownership-routed facets ride the 0.6s cadence, so they must take the
    path AND the host the tick already resolved — otherwise each adds a state-DB
    resolve plus an audit-DB routing walk per tick, per channel."""
    from dashboard.http import sse

    seen = []
    monkeypatch.setattr(sse.plugins, "fg_running",
                        lambda sid, sdb=None, host=None:
                        seen.append(("fg", sid, sdb, host)))
    monkeypatch.setattr(sse, "session_compacting",
                        lambda sid, sdb=None, host=None:
                        seen.append(("cmp", sid, sdb, host)))

    ctx = sse._Tick("s", {"transcript_path": "/p/abc.jsonl"})
    ctx.sdb = "/tmp/x.state.db"
    for chan in sse._FAST_CHANS:
        if chan.key in ("fgrun", "compacting"):
            chan.value(ctx)
    assert ("fg", "s", "/tmp/x.state.db", "claude_code") in seen
    assert ("cmp", "s", "/tmp/x.state.db", "claude_code") in seen
