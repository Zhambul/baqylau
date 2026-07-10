# L9 — the ⧉ copy links (core/copy.py + the renderer's OSC 8 affordances).
#
# The copy pipeline in one file: producers stamp a copy-group id ("g") on a
# block's ops, the renderer paints g-tagged labels with claude-copy://
# hyperlinks, and claude-copy.py (the open-actions handler) re-reads the group
# from the state DB and pipes command/output text to the clipboard —
# CLAUDE_COPY_CMD stands in for pbcopy so the suite never touches the real
# clipboard.
import os
import sqlite3
import subprocess
import sys
import time

import payloads as P
from conftest import REPO, wait_until


def _copy(run_hook, env, key, gid, what, clip_path):
    e = dict(env)
    e["CLAUDE_COPY_CMD"] = "cat > %s" % clip_path
    url = "claude-copy:///%s/%s/%s" % (key, gid, what)
    return run_hook("claude-copy.py", raw_stdin="", argv=(url,), env=e)


def test_fg_block_copy_cmd_and_out(session, run_hook, test_env, tmp_path):
    """A finished foreground block (no live tailer) is fully group-tagged, and
    claude-copy.py copies the command and the ANSI-stripped output."""
    s = session.make()
    cmd = "echo hello-copy"
    run_hook("claude-cmd-fmt.py", P.post_bash(s, cmd, tid="toolu_cp1",
                                              stdout="line-a\nline-b\n"))
    tagged = [op for op in s.ops() if op.get("g") == "toolu_cp1"]
    kinds = {op["t"] for op in tagged}
    assert {"label", "code", "gut"} <= kinds

    clip = str(tmp_path / "clip.txt")
    _copy(run_hook, test_env, s.sid, "toolu_cp1", "cmd", clip)
    assert open(clip).read() == cmd

    _copy(run_hook, test_env, s.sid, "toolu_cp1", "out", clip)
    text = open(clip).read()
    assert "line-a" in text and "line-b" in text
    assert "\x1b" not in text                      # ANSI styling stripped

    # the click leaves an audit trail (state_files action "copy") + feedback op
    assert "⧉ copied" in s.ops_text()


def test_copy_cmd_is_wysiwyg_pretty_printed(session, run_hook, test_env, tmp_path):
    """format_code reflows `a; b` one-liners for display — ⧉cmd copies the text
    AS DISPLAYED (the code op's `s`), not the original one-liner (owner's call:
    WYSIWYG; the reflowed form is equivalent runnable bash)."""
    s = session.make()
    cmd = "echo one; echo two && echo three"
    run_hook("claude-cmd-fmt.py", P.post_bash(s, cmd, tid="toolu_cp2"))
    shown = next(op["s"] for op in s.ops()
                 if op["t"] == "code" and op.get("g") == "toolu_cp2")
    assert shown != cmd and "\n" in shown          # the reflow actually happened
    clip = str(tmp_path / "clip.txt")
    _copy(run_hook, test_env, s.sid, "toolu_cp2", "cmd", clip)
    assert open(clip).read() == shown


def test_live_fg_pre_tags_header_and_group_env(session, run_hook, test_env):
    """The live-fg path: claude-cmd-pre.py stamps the header/code ops with the
    tool_use_id group so the tailer's output joins the same copy group."""
    s = session.make()
    run_hook("claude-cmd-pre.py", P.pre_bash(s, "echo live", tid="toolu_cp3"))
    tagged = [op for op in s.ops() if op.get("g") == "toolu_cp3"]
    assert {"label", "code"} <= {op["t"] for op in tagged}


def test_renderer_paints_copy_hyperlinks(session, seed, reaper, test_env):
    """A g-tagged label renders with claude-copy:// OSC 8 links; an untagged
    label renders none."""
    s = session.make()
    seed.py(
        "import claude_ops as O\n"
        "O.emit(%r, O.label('tagged', O.SLATE, g='gid-1'),\n"
        "       O.label('plain', O.SLATE), O.line('SENTINEL-L9'))" % s.log)
    out_path = s.log + ".render.out"
    with open(out_path, "wb") as out:
        proc = subprocess.Popen(
            [sys.executable, os.path.join(REPO, "claude-mirror.py"), s.log, "100"],
            stdout=out, stderr=subprocess.DEVNULL, env=dict(test_env), cwd=REPO)
    reaper.append(proc)
    try:
        wait_until(lambda: b"SENTINEL-L9" in open(out_path, "rb").read(),
                   desc="renderer painted the seeded ops")
    finally:
        proc.terminate()
    text = open(out_path, "rb").read().decode("utf-8", "replace")
    assert "claude-copy:///%s/gid-1/cmd" % s.sid in text
    assert "claude-copy:///%s/gid-1/out" % s.sid in text
    assert text.count("\x1b]8;;claude-copy") == 2   # the plain label got no links


def test_copy_after_session_end_never_creates_db(session, run_hook, test_env, tmp_path):
    """A click on a dead session's link must NOT create a state DB (its
    file-existence is the session-alive signal) — audited, not fatal."""
    s = session.make()                              # never written: no DB exists
    clip = str(tmp_path / "clip.txt")
    _copy(run_hook, test_env, s.sid, "toolu_gone", "cmd", clip)
    assert not os.path.exists(s.state_db)
    assert not os.path.exists(clip)
    audit_db = os.path.join(test_env["CLAUDE_AUDIT_DIR"], "audit.db")
    conn = sqlite3.connect("file:%s?mode=ro" % audit_db, uri=True, timeout=5)
    try:
        funcs = [r[0] for r in conn.execute("SELECT func FROM errors").fetchall()]
    finally:
        conn.close()
    assert any("state DB gone" in f for f in funcs)
