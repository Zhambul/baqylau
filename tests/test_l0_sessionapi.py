# L0 — core/sessionapi.py, the read-side session-data API. In-process tests:
# audit rows are seeded through the REAL core.audit write API (the same calls
# adopt.py / stream_lifecycle / the OTLP receiver make — schema changes must
# break these loudly) under the autouse hermetic CLAUDE_AUDIT_DIR; state DBs
# through core.state's product functions on tmp-path mirror-log keys.
import json
import os
import sys

from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

import core.audit as A
from core import paths as P
from core import sessionapi as API
from core import state as S


def _log(tmp_path, sid):
    return str(tmp_path / ("claude-mirror-%s.log" % sid))


def _adopt(old, new):
    # Exactly what plugins/claude_code/adopt.py records at a sid fork.
    A.state_file(P.mirror_log(new), "db", "adopt", {"from": old, "moved": ["db"]})


# ------------------------------------------------------------------ sid_chain

def test_sid_chain_identity_without_audit_rows():
    assert API.sid_chain("lonely") == ["lonely"]


def test_sid_chain_resolves_multi_hop_forks(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    _adopt("sidA", "sidB")
    _adopt("sidB", "sidC")
    for sid in ("sidA", "sidB", "sidC"):
        assert API.sid_chain(sid) == ["sidA", "sidB", "sidC"], sid


def test_sid_chain_survives_a_cycle(monkeypatch, tmp_path):
    # Malformed audit data (a cycle) must terminate, not hang.
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    _adopt("x1", "x2")
    _adopt("x2", "x1")
    chain = API.sid_chain("x1")
    assert set(chain) == {"x1", "x2"} and len(chain) == 2


# ---------------------------------------------------------------- discovery

def test_sessions_joins_audit_rows_with_liveness(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    monkeypatch.setattr(P, "HISTORY_DIR", str(tmp_path / "park"))
    A.session_start({"session_id": "live1", "cwd": "/w",
                     "transcript_path": "/w/t.jsonl"})
    assert S.connect(P.mirror_log("live1")) is not None    # a live state DB
    rows = API.sessions()
    row = next(r for r in rows if r["sid"] == "live1")
    assert row["live"] is True and row["parked"] is False
    assert row["cwd"] == "/w" and row["transcript_path"] == "/w/t.jsonl"


def test_sessions_lists_parked_orphans(monkeypatch, tmp_path):
    # A parked DB the audit never saw (auditing off at the time) still shows up.
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    monkeypatch.setattr(P, "HISTORY_DIR", str(tmp_path / "park"))
    log = P.mirror_log("ghost")
    assert S.connect(log) is not None
    os.makedirs(P.HISTORY_DIR, exist_ok=True)
    os.replace(P.state_db(log), P.parked_db(log))          # what park_db does
    row = next(r for r in API.sessions() if r["sid"] == "ghost")
    assert row["parked"] is True and row["live"] is False


def test_session_row_walks_the_fork_chain(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    A.session_start({"session_id": "old9", "cwd": "/p",
                     "transcript_path": "/p/t.jsonl"})
    _adopt("old9", "new9")
    row = API.session_row("new9")
    assert row and row["sid"] == "old9" and row["transcript_path"] == "/p/t.jsonl"


def test_state_db_for_prefers_live_then_parked_across_chain(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    monkeypatch.setattr(P, "HISTORY_DIR", str(tmp_path / "park"))
    _adopt("oldp", "newp")
    log_old = P.mirror_log("oldp")
    assert S.connect(log_old) is not None
    os.makedirs(P.HISTORY_DIR, exist_ok=True)
    os.replace(P.state_db(log_old), P.parked_db(log_old))
    # No DB under the new sid; the parked one under the OLD sid is found.
    assert API.state_db_for("newp") == P.parked_db(log_old)


# ------------------------------------------------------------------ read model

def test_agents_merges_streams_with_state_records(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    monkeypatch.setattr(P, "HISTORY_DIR", str(tmp_path / "park"))
    log = P.mirror_log("ag-sess")
    rid = A.stream_start(log, "subagent", agent_id="ag1",
                         src_path="/tp/subagents/agent-ag1.jsonl")
    A.stream_end(rid, "stoppedByUser (manual cancel)", lines_emitted=4)
    S.agent_set(log, "ag1", desc="fix the tests", done=1, slot=2)
    agents = API.agents("ag-sess")
    assert len(agents) == 1
    a = agents[0]
    assert a["agent_id"] == "ag1" and a["kind"] == "subagent"
    assert a["transcript"] == "/tp/subagents/agent-ag1.jsonl"
    assert a["end_reason"] == "stoppedByUser (manual cancel)"
    assert a["desc"] == "fix the tests" and a["done"] is True and a["slot"] == 2
    assert API.agent_transcript("ag-sess", "ag1") == "/tp/subagents/agent-ag1.jsonl"
    assert API.agent_transcript("ag-sess", "nope") == ""


def test_agents_includes_codex_runs(monkeypatch, tmp_path):
    """kind='codex' streams rows ride the agents list in the SAME row shape
    (additive read model): agent_id is the synthesized codex_aid (codex
    tailers record no hook agent_id), desc is the run label (task_id)."""
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    monkeypatch.setattr(P, "HISTORY_DIR", str(tmp_path / "park"))
    log = P.mirror_log("cx-sess")
    src = "/h/.codex/sessions/2026/07/06/rollout-2026-07-06T10-00-00-abcd.jsonl"
    rid = A.stream_start(log, "codex", task_id="cli", src_path=src)
    A.stream_end(rid, "task-complete", lines_emitted=7)
    agents = API.agents("cx-sess")
    assert len(agents) == 1
    a = agents[0]
    assert a["agent_id"] == "rollout-2026-07-06T10-00-00-abcd"
    assert a["kind"] == "codex" and a["desc"] == "cli"
    assert a["transcript"] == src
    assert a["end_reason"] == "task-complete" and a["tools"] == 7
    assert API.codex_aid(src) == a["agent_id"]
    # a src-less codex row can't be named — never listed
    A.stream_start(log, "codex", task_id="ghost", src_path="")
    assert len(API.agents("cx-sess")) == 1


def test_costs_are_fork_aware(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    _adopt("cost-old", "cost-new")
    A.otel("cost-old", [
        {"metric": "token", "query_source": "main", "type": "input", "value": 100},
        {"metric": "token", "query_source": "main", "type": "output", "value": 40},
        {"metric": "cost", "query_source": "main", "value": 0.25},
    ])
    A.otel("cost-new", [
        {"metric": "token", "query_source": "subagent", "type": "input", "value": 7},
        {"metric": "cost", "query_source": "subagent", "value": 0.05},
    ])
    c = API.costs("cost-new")                     # pre-fork rows must be included
    assert c["tokens"]["main"]["input"] == 100
    assert c["tokens"]["subagent"]["input"] == 7
    assert abs(c["total_usd"] - 0.30) < 1e-9
    assert c == API.costs("cost-old")             # any sid in the chain, same answer


def test_errors_are_fork_aware_and_ordered(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    _adopt("err-old", "err-new")
    A.error(P.mirror_log("err-old"), "first thing", {"n": 1})
    A.error(P.mirror_log("err-new"), "second thing", {"n": 2})
    errs = API.errors("err-new")
    assert [e["func"] for e in errs] == ["first thing", "second thing"]


def test_error_count_is_fork_aware(monkeypatch, tmp_path):
    """The cheap COUNT twin of errors() spans the whole fork chain (pre-fork
    rows live under the OLD sid) and matches len(errors()) exactly — any sid in
    the chain gives the same number."""
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    assert API.error_count("nobody") == 0             # no audit rows -> 0
    _adopt("ec-old", "ec-new")
    A.error(P.mirror_log("ec-old"), "first thing", {"n": 1})
    A.error(P.mirror_log("ec-new"), "second thing", {"n": 2})
    assert API.error_count("ec-new") == 2
    assert API.error_count("ec-old") == 2             # any sid in the chain
    assert API.error_count("ec-new") == len(API.errors("ec-new"))


def test_live_at_and_running_group_only_alive_rows(monkeypatch, tmp_path):
    """live_at surfaces every `live` slot row with a pid_alive verdict; running()
    resolves the state DB and returns only alive rows grouped by kind. Slots are
    seeded via the product writers (core.slots) so a schema change breaks here."""
    import subprocess

    from core import slots
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    monkeypatch.setattr(P, "HISTORY_DIR", str(tmp_path / "park"))
    log = P.mirror_log("run-sess")
    # a live fg command tailer + a live per-agent substream pid (this process
    # owns both -> pid_alive True)
    _, fg_tok = slots.claim("fg", log)
    assert fg_tok is not None
    slots.set_owner(fg_tok, os.getpid())
    slots.pid_set(log, "agentX", os.getpid())
    # a stale/leaked bg slot whose owning pid is dead — a reader must NOT trust it
    # as running (set_owner is a product path; no direct SQL seeding needed)
    _, bg_tok = slots.claim("bg", log)
    dead = subprocess.Popen(["true"])
    dead.wait()
    slots.set_owner(bg_tok, dead.pid)

    sdb = API.state_db_for("run-sess")
    rows = {(r["kind"], r["key"]): r for r in S.live_at(sdb)}
    assert rows[("fg", "0")]["alive"] is True and rows[("fg", "0")]["slot"] == 0
    assert rows[("sub.pid", "agentX")]["alive"] is True
    assert rows[("bg", "0")]["alive"] is False           # dead owner

    run = API.running("run-sess")
    assert set(run) == {"fg", "sub.pid"}                 # the dead bg row is dropped
    assert run["fg"][0]["key"] == "0"
    assert run["sub.pid"][0]["key"] == "agentX"
    # a session with no state DB (never created) -> empty, no DB conjured
    assert API.running("nobody") == {}
    assert S.live_at(str(tmp_path / "claude-mirror-nobody.log.state.db")) == []


def test_session_overview_composes(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    monkeypatch.setattr(P, "HISTORY_DIR", str(tmp_path / "park"))
    A.session_start({"session_id": "ov1", "cwd": "/q",
                     "transcript_path": "/q/t.jsonl"})
    log = P.mirror_log("ov1")
    S.incr(log, tool="Bash", commands=2)
    ov = API.session("ov1")
    assert ov["chain"] == ["ov1"] and ov["live"] is True
    assert ov["stats"].get("commands") == 2
    assert ov["costs"]["total_usd"] == 0 and ov["agents"] == []


# --------------------------------------------------- *_at readers (parked path)

def test_stats_and_ops_at_read_a_parked_db(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    monkeypatch.setattr(P, "HISTORY_DIR", str(tmp_path / "park"))
    log = P.mirror_log("pk1")
    S.incr(log, tool="Bash", commands=3, failed=1)
    assert S.ops_append(log, [{"t": "label", "s": "hello", "c": [1, 2, 3]},
                              {"t": "line", "s": "world"}])
    S.evict(log)                                   # drop the cached writer conn
    os.makedirs(P.HISTORY_DIR, exist_ok=True)
    os.replace(P.state_db(log), P.parked_db(log))
    for suf in ("-wal", "-shm"):                   # park moves the sidecars too
        if os.path.exists(P.state_db(log) + suf):
            os.replace(P.state_db(log) + suf, P.parked_db(log) + suf)
    db = P.parked_db(log)
    st = S.stats_at(db)
    assert st.get("commands") == 3 and st.get("failed") == 1
    assert st.get("tools", {}).get("Bash") == 1
    last, ops = S.ops_at(db)
    assert last == 2 and [o["t"] for o in ops] == ["label", "line"]
    _, tail = S.ops_at(db, after_id=1)
    assert [o["s"] for o in tail] == ["world"]
    # the live path must NOT have been recreated by any of those reads
    assert not os.path.exists(P.state_db(log))


def test_at_readers_never_create_the_db(tmp_path):
    missing = str(tmp_path / "claude-mirror-none.log.state.db")
    assert S.stats_at(missing) == {}
    assert S.ops_at(missing) == (0, [])
    assert S.agents_at(missing) == {}
    assert not os.path.exists(missing)


# ------------------------------------------------------------- the single door

def test_pane_renderers_read_through_sessionapi():
    """The mirror/scorebar consume ONLY the sessionapi door (styleguide
    single-owner table) — a direct core.state import there reopens the side
    door the API exists to close."""
    for name in ("claude-mirror.py", "claude-scorebar.py"):
        src = open(os.path.join(REPO, "bin", name), encoding="utf-8").read()
        assert "from core import sessionapi" in src, name
        assert "from core import state" not in src, name


def test_presentation_channel_is_the_same_functions():
    """Delegations, not wrappers — identical behavior by construction."""
    assert API.ops_after is S.ops_after
    assert API.stats is S.stats
    assert API.version is S.version
    assert API.parked is S.parked
    assert API.kv_get is S.kv_get and API.kv_set is S.kv_set
    assert API.db_path is S.db_path and API.evict is S.evict
    assert API.tab_state is S.tab_state


# ------------------------------------------------------- plugins.activity glue

def test_activity_falls_back_to_layout_derivation(monkeypatch, tmp_path):
    """No streams row (audit was off when the streamer ran): the provider
    derives the agent transcript from the sessions row's transcript_path via
    transcript.agent_paths."""
    monkeypatch.setattr(P, "PREFIX", str(tmp_path) + "/claude-mirror-")
    tp = tmp_path / "sess.jsonl"
    tp.write_text("", encoding="utf-8")
    A.session_start({"session_id": "act1", "cwd": str(tmp_path),
                     "transcript_path": str(tp)})
    sub = tmp_path / "sess" / "subagents"
    os.makedirs(sub)
    (sub / "agent-agX.jsonl").write_text(
        json.dumps({"type": "user", "message": {"content": "sub prompt"}}) + "\n",
        encoding="utf-8")
    import plugins
    tl = plugins.activity("act1", "agX")
    assert tl and tl["entries"] == [{"t": "prompt", "text": "sub prompt"}]
    # main-thread view reads the parent transcript itself (empty -> no entries);
    # `pos` is the additive live-resume byte cursor (0 for an empty transcript)
    tl_main = plugins.activity("act1")
    assert tl_main == {"entries": [], "model": None, "bad_lines": 0, "tools": 0,
                       "pos": 0,
                       "usage": {"in": 0, "out": 0, "cache": 0, "create": 0,
                                 "create_1h": 0}}
    # unknown pair -> no provider claims it
    assert plugins.activity("act1", "missing-agent") is None
