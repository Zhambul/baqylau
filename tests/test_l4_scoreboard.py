# L4 — scoreboard / accounting goldens.
#
# Drives claude-stop-fmt.py (the sanctioned Stop-time fold — a real subprocess,
# real transcript bytes) and pins the numbers claude_ops computes: the
# per-message.id dedup (the famous ~2.2x inflation bug), the PRICES table,
# the cache read/write premiums, the byte cursor, and reconcile_spend's
# crashed-streamer recovery.
import os
import signal
import time
import uuid

import pytest

import oracle
import payloads as P
from conftest import wait_until

STOP = "claude-stop-fmt.py"


def usage(i=0, o=0, read=0, create=0):
    return {"input_tokens": i, "output_tokens": o,
            "cache_read_input_tokens": read,
            "cache_creation_input_tokens": create}


def fold(run_hook, s):
    run_hook(STOP, P.stop(s))
    return s.counters()


# ------------------------------------------------------------ basic + dedup

def test_single_message_tokens_and_cost(run_hook, session):
    s = session.make()
    s.add_assistant("m1", model="claude-opus-4-8", usage=usage(i=100, o=50))
    c = fold(run_hook, s)
    assert c["tokens"] == 150                       # fresh input + output
    assert c["tk_in"] == 100 and c["tk_out"] == 50
    assert c["cost"] == pytest.approx((100 * 5 + 50 * 25) / 1e6)


def test_multi_block_message_counts_once(run_hook, session):
    """THE dedup pin: one message = one JSONL line per content block, usage
    repeated with a growing output snapshot — only the last counts."""
    s = session.make()
    for out in (10, 20, 30):
        s.add_assistant("m1", usage=usage(i=100, o=out))
    c = fold(run_hook, s)
    assert c["tokens"] == 130, "multi-block message inflated (the 2.2x bug)"
    assert c["tk_out"] == 30


def test_same_message_straddling_two_folds_deltas_only(run_hook, session):
    """The txlast carry: a message continuing past a fold boundary must add
    only the per-field delta on the next fold."""
    s = session.make()
    s.add_assistant("m1", usage=usage(i=100, o=10))
    assert fold(run_hook, s)["tokens"] == 110
    s.add_assistant("m1", usage=usage(i=100, o=30))     # same id, grown output
    c = fold(run_hook, s)
    assert c["tokens"] == 130, "straddling message re-counted, not delta'd"


def test_sidechain_and_non_assistant_ignored(run_hook, session):
    s = session.make()
    s.add_user("hello")
    s.add_assistant("side1", usage=usage(i=500, o=500), sidechain=True)
    s.add_line({"type": "system", "subtype": "whatever"})
    s.add_assistant("m1", usage=usage(i=10, o=5))
    c = fold(run_hook, s)
    assert c["tokens"] == 15, "sidechain / non-assistant lines leaked into totals"


# ------------------------------------------------------------------ pricing

OPUS48 = (5.0, 25.0)
PRICING_CASES = [
    ("claude-opus-4-8", 5.0, 25.0),
    ("claude-opus-4-5", 5.0, 25.0),
    ("claude-opus-4-1-20250805", 15.0, 75.0),
    ("claude-opus-4-20250514", 15.0, 75.0),
    ("claude-3-opus-20240229", 15.0, 75.0),
    ("claude-haiku-4-5-20251001", 1.0, 5.0),
    ("claude-fable-5", 10.0, 50.0),
]


@pytest.mark.parametrize("model,pin,pout", PRICING_CASES, ids=[c[0] for c in PRICING_CASES])
def test_prices_substring_mapping(run_hook, session, model, pin, pout):
    s = session.make()
    s.add_assistant("m1", model=model, usage=usage(i=1000, o=100))
    c = fold(run_hook, s)
    assert c["cost"] == pytest.approx((1000 * pin + 100 * pout) / 1e6), model


def test_sonnet5_intro_rate_window(run_hook, session):
    """Sonnet 5's 2/10 intro rate through 2026-08-31, 3/15 sticker after —
    picked at (hook) import time."""
    s = session.make()
    s.add_assistant("m1", model="claude-sonnet-5", usage=usage(i=1000, o=100))
    c = fold(run_hook, s)
    pin, pout = (2.0, 10.0) if time.time() < 1788220800 else (3.0, 15.0)
    assert c["cost"] == pytest.approx((1000 * pin + 100 * pout) / 1e6)


def test_unknown_model_counts_tokens_but_no_cost(run_hook, session):
    s = session.make()
    s.add_assistant("m1", model="totally-made-up-9000", usage=usage(i=100, o=10))
    c = fold(run_hook, s)
    assert c["tokens"] == 110
    assert not c.get("cost"), "unknown model must not guess a price"


def test_cache_read_write_premiums(run_hook, session):
    """fin = input + cache_creation at the input rate; creation adds +0.25x;
    cache reads bill 0.1x; the Σ split lands in tk_in/out/read/create."""
    s = session.make()
    s.add_assistant("m1", model="claude-opus-4-8",
                    usage=usage(i=100, o=10, read=1000, create=200))
    c = fold(run_hook, s)
    assert c["tk_in"] == 100 and c["tk_create"] == 200 and c["tk_read"] == 1000
    assert c["tokens"] == 310                       # (100+200) fresh + 10 out
    want = (300 * 5 + 200 * 5 * 0.25 + 1000 * 5 * 0.1 + 10 * 25) / 1e6
    assert c["cost"] == pytest.approx(want)


# ------------------------------------------------------------- byte cursor

def test_cursor_never_double_counts(run_hook, session):
    s = session.make()
    s.add_assistant("m1", usage=usage(i=100, o=10))
    fold(run_hook, s)
    s.add_assistant("m2", usage=usage(i=50, o=5))
    c = fold(run_hook, s)
    assert c["tokens"] == 165
    c = fold(run_hook, s)                            # no new lines
    assert c["tokens"] == 165, "re-fold with no new turns moved the numbers"
    assert c["txpos"] == os.path.getsize(s.transcript)


def test_transcript_shrink_restarts_cursor(run_hook, session):
    """A rotated/replaced transcript (size < cursor) restarts from byte 0
    without negative or double counting."""
    s = session.make()
    s.add_assistant("m1", usage=usage(i=100, o=10))
    fold(run_hook, s)
    with open(s.transcript, "w"):                    # rotate: truncate to empty
        pass
    s.add_assistant("m2", usage=usage(i=7, o=3))     # fresh id in the new file
    c = fold(run_hook, s)
    assert c["tokens"] == 120, "shrunk transcript mis-counted after restart"


# --------------------------------------------------------- reconcile_spend

def test_reconcile_recovers_crashed_streamer_spend(run_hook, test_env, session):
    """A subagent streamer killed before its footer leaves its token tail
    un-bumped; SubagentStop's reconcile_spend must fold the true total."""
    s = session.make()
    agent = "agent-" + uuid.uuid4().hex[:8]
    s.write_subagent_jsonl(agent, [])
    run_hook("claude-subagent-fmt.py", P.subagent_start(s, agent_id=agent),
             argv=("start",))
    s.write_subagent_jsonl(agent, [
        {"type": "assistant", "message": {
            "id": "am1", "model": "claude-opus-4-8", "role": "assistant",
            "content": [{"type": "text", "text": "working"}],
            "usage": usage(i=400, o=100)}},
    ])
    pid = [p for _, p, purpose in oracle.spawns(test_env, s.sid)
           if purpose.startswith("stream:subagent")][0]
    os.kill(int(pid), signal.SIGKILL)                # crash before the footer
    wait_until(lambda: not _alive(pid), desc="streamer dead")
    run_hook("claude-subagent-fmt.py", P.subagent_stop(s, agent_id=agent),
             argv=("stop",))
    c = s.counters()
    assert c.get("tokens", 0) >= 500, \
        "crashed streamer's spend not reconciled: %s" % c
    actions = [r[1] for r in oracle.state_files(test_env, s.sid)]
    assert "reconcile" in actions or "bump-agent" in actions


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
