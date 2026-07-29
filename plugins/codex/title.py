# plugins/codex/title.py — codex SESSION TITLE (read + parked rename).
#
# The single owner of "what is a codex session called, and how is it renamed off
# a hook" (docs/styleguide.md single-owner table) — the codex twin of the naming
# half of plugins/claude_code/transcript.py. Behind the plugins.session_title /
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


def _thread_title(uuid):
    """threads.title for `uuid` from the codex state index, or "" (no index, no
    row, an unreadable/other-shaped DB — all degrade to "")."""
    db = _state_db()
    if not db or not uuid:
        return ""
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=1.0)
        try:
            row = conn.execute("SELECT title FROM threads WHERE id=?",
                               (uuid,)).fetchone()
        finally:
            conn.close()
    except Exception:
        return ""
    return (row[0] or "").strip() if row and row[0] else ""


def _first_prompt(path):
    """The first real user prompt in a rollout's head, one line, capped — the
    fallback title when the state index has none. Bounded to TITLE_HEAD_LINES."""
    from plugins.codex import rollout as RO
    import json
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, raw in enumerate(fh):
                if i >= TITLE_HEAD_LINES:
                    break
                raw = raw.strip()
                if not raw or ('"user_message"' not in raw and '"message"' not in raw):
                    continue
                try:
                    rec = RO.parse(json.loads(raw))
                except Exception:
                    continue
                if not rec:
                    continue
                if rec["kind"] == "prompt" and rec["text"].strip():
                    return rec["text"].strip().split("\n", 1)[0][:200]
                if rec["kind"] == "chat" and rec.get("role") == "user" \
                        and not rec.get("synthetic") and rec["text"].strip():
                    return rec["text"].strip().split("\n", 1)[0][:200]
    except OSError:
        return ""
    return ""


def session_title(path):
    """Display title for a codex rollout: threads.title from the state index,
    else the first real user prompt in the rollout head, else "". Behind
    plugins.session_title."""
    return _thread_title(_thread_uuid(path)) or _first_prompt(path)


def title_and_rename(path):
    """(title, tail_rename) — the display title plus any rename record still in a
    reconcilable window. codex keeps the name in its state index, NOT in the
    rollout, so there is no in-file rename to reconcile: tail_rename is always ""
    and the dashboard's durable web-rename override stands unchallenged. Behind
    plugins.title_and_rename."""
    return session_title(path), ""


def renameable(path):
    """True for a codex rollout this plugin owns — the gate both the dashboard's
    live rename (P5) and set_session_title ask before naming a session. A
    standalone codex host's window carries the same `claude_session` tag as a
    Claude one, so this is what keeps a Claude `/rename` off it and vice-versa.
    Behind plugins.renameable."""
    from plugins.codex import rollout as RO
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
