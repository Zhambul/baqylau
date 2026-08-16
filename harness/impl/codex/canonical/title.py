# harness/impl/codex/canonical/title.py — codex SESSION TITLE (read + parked rename).
#
# The single owner of "what is a codex session called, and how is it renamed off
# a hook" (docs/styleguide.md single-owner table) — the codex twin of the naming
# half of harness/impl/claude_code/canonical/transcript.py. Behind the plugins.session_title /
# title_and_rename / renameable / set_session_title fan-outs, gated by codex's
# `owns` so a Claude transcript never reaches here.
#
# WHERE the title lives: codex keeps it in a per-machine sqlite index,
# `~/.codex/state_<N>.sqlite`, table `threads`, column `title`, keyed by the
# thread uuid (== the rollout's uuid == the session id for a standalone host). The
# numbered filename is VERSION-FRAGILE (state_5 on the dev machine, 2026-07), so
# it is resolved by globbing and taking the highest N, and every read degrades to
# "" on any error — an absent/renamed/older index must never raise into a
# read-side dashboard call. When the index yields nothing, the title falls back to
# the first real user prompt in the rollout head (the codex analogue of
# session_title's first-prompt fallback).
#
# RENAME: set_session_title writes threads.title — the PARKED path, the one the
# dashboard's web rename uses for a codex session with nothing running to overwrite
# it. A LIVE rename belongs to P5's HostControl.rename (codex app-server
# `thread/name/set`, or pasting codex's own /rename), the same live-vs-parked split
# Claude has; this module owns only the durable write.
import glob
import os
import re
import sqlite3

_CODEX_DIR = os.path.join(os.path.expanduser("~"), ".codex")
_UUID = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                   r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")
TITLE_HEAD_LINES = 200   # rollout head lines the first-prompt fallback scans


_STATE_DB = {}          # _CODEX_DIR -> (deadline, resolved path)
STATE_DB_TTL_S = 60.0   # how long a resolved index path is trusted (see below)


def _state_db():
    """The newest codex `state_<N>.sqlite` index (highest N), or "" — resolved
    defensively because the numbered name drifts across codex versions."""
    cands = glob.glob(os.path.join(_CODEX_DIR, "state_*.sqlite"))
    best, best_n = "", -1
    for p in cands:
        m = re.search(r"state_(\d+)\.sqlite$", os.path.basename(p))
        n = int(m.group(1)) if m else 0
        if n >= best_n:
            best, best_n = p, n
    if best:
        return best
    plain = os.path.join(_CODEX_DIR, "state.sqlite")
    return plain if os.path.isfile(plain) else ""


def _thread_uuid(path):
    """The thread uuid a rollout path names (== the session id for a standalone
    host), or "" — read out of the `rollout-<ts>-<uuid>.jsonl` filename."""
    m = _UUID.search(os.path.basename(path or ""))
    return m.group(1) if m else ""


def renameable(path):
    """True for a codex rollout this plugin owns — the gate both the dashboard's
    live rename (P5) and set_session_title ask before naming a session. A
    standalone codex host's window carries the same `claude_session` tag as a
    Claude one, so this is what keeps a Claude `/rename` off it and vice-versa.
    Behind plugins.renameable."""
    from harness.impl.codex.canonical import rollout as RO
    return bool(RO.owns(path))


def set_session_title(path, name):
    """Write threads.title for a codex session — the PARKED web rename's write
    half. True on success; None when `path` is not a codex rollout (renameable),
    False when the index/row is missing (the dashboard then reports the failure).
    A user-facing request/reply path (dashboard post_rename), not a hook, so an
    unexpected sqlite error propagates like transcript.set_session_title's OSError.
    Behind plugins.set_session_title."""
    if not renameable(path):
        return None
    db = _state_db()
    uuid = _thread_uuid(path)
    if not db or not uuid:
        return False
    conn = sqlite3.connect(db, timeout=2.0)
    try:
        cur = conn.execute("UPDATE threads SET title=? WHERE id=?", (name, uuid))
        conn.commit()
    finally:
        conn.close()
    return True if cur.rowcount else False
