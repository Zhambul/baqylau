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
import uuid

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


SUB_FG_EVENTS = [
    {"type": "assistant", "message": {
        "id": "smsg_1", "model": "claude-opus-4-8", "role": "assistant",
        "content": [{"type": "tool_use", "id": "tu_cp", "name": "Bash",
                     "input": {"command": "grep -r bug ."}}],
        "usage": {"input_tokens": 50, "output_tokens": 9,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "tu_cp",
         "content": "src/x.py: bug here"}]}},
]


def test_subagent_fg_block_copy_cmd_and_out(session, run_hook, test_env, tmp_path):
    """A subagent's foreground command block (rendered by the substream) is
    group-tagged by its tool_use_id, so ⧉cmd/⧉out copy the command and output —
    the same affordance the main session's fg blocks carry."""
    s = session.make()
    agent = "agent-" + uuid.uuid4().hex[:8]
    s.write_subagent_jsonl(agent, [])
    run_hook("claude-subagent-fmt.py", P.subagent_start(s, agent_id=agent),
             argv=("start",))
    s.write_subagent_jsonl(agent, SUB_FG_EVENTS)
    wait_until(lambda: "grep -r bug ." in s.ops_text(),
               desc="subagent fg command rendered")
    wait_until(lambda: "src/x.py: bug here" in s.ops_text(),
               desc="subagent fg output rendered")

    tagged = [op for op in s.ops() if op.get("g") == "tu_cp"]
    assert {"label", "code", "gut"} <= {op["t"] for op in tagged}

    clip = str(tmp_path / "clip.txt")
    _copy(run_hook, test_env, s.sid, "tu_cp", "cmd", clip)
    assert open(clip).read() == "grep -r bug ."
    _copy(run_hook, test_env, s.sid, "tu_cp", "out", clip)
    assert "src/x.py: bug here" in open(clip).read()


def test_subagent_message_block_copy_all(session, run_hook, test_env, tmp_path):
    """A body-only activity (a subagent's assistant message) is tagged with a fresh
    O.new_group() id + a single ⧉copy ('all') affordance, and claude-copy.py copies
    the message body from the block's gut op."""
    s = session.make()
    agent = "agent-" + uuid.uuid4().hex[:8]
    s.write_subagent_jsonl(agent, [])
    run_hook("claude-subagent-fmt.py", P.subagent_start(s, agent_id=agent),
             argv=("start",))
    s.write_subagent_jsonl(agent, [
        {"type": "assistant", "message": {
            "id": "smsg_1", "model": "claude-opus-4-8", "role": "assistant",
            "content": [{"type": "text", "text": "here is my analysis"}],
            "usage": {"input_tokens": 5, "output_tokens": 3,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
        # a following renderable event flushes the buffered message
        {"type": "assistant", "message": {
            "id": "smsg_2", "model": "claude-opus-4-8", "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu_x", "name": "Bash",
                         "input": {"command": "true"}}],
            "usage": {"input_tokens": 5, "output_tokens": 3,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
    ])
    wait_until(lambda: "here is my analysis" in s.ops_text(),
               desc="subagent message rendered")

    # find the message block's group: a label carrying the ⧉copy ('all') link spec
    msg_label = next(op for op in s.ops()
                     if op["t"] == "label" and op.get("lk") == [["all", "⧉copy"]]
                     and "message" in op.get("s", ""))
    gid = msg_label["g"]
    clip = str(tmp_path / "clip.txt")
    _copy(run_hook, test_env, s.sid, gid, "all", clip)
    assert "here is my analysis" in open(clip).read()


def test_renderer_paints_single_copy_link_from_lk(session, seed, reaper, test_env):
    """A label whose `lk` names one ('all', '⧉copy') affordance renders exactly that
    link — not the default cmd/out pair."""
    s = session.make()
    seed.py(
        "import claude_ops as O\n"
        "O.emit(%r, O.label('note', O.SLATE, g='gid-x', lk=O.COPY_ALL),\n"
        "       O.line('SENTINEL-LK'))" % s.log)
    out_path = s.log + ".render.out"
    with open(out_path, "wb") as out:
        proc = subprocess.Popen(
            [sys.executable, os.path.join(REPO, "claude-mirror.py"), s.log, "100"],
            stdout=out, stderr=subprocess.DEVNULL, env=dict(test_env), cwd=REPO)
    reaper.append(proc)
    try:
        wait_until(lambda: b"SENTINEL-LK" in open(out_path, "rb").read(),
                   desc="renderer painted the seeded ops")
    finally:
        proc.terminate()
    text = open(out_path, "rb").read().decode("utf-8", "replace")
    assert "claude-copy:///%s/gid-x/all" % s.sid in text
    assert "/cmd" not in text and "/out" not in text
    assert text.count("\x1b]8;;claude-copy") == 1


def _kv(s, key):
    import json
    rows = s.query_state("SELECT val FROM kv WHERE key=?", (key,))
    return json.loads(rows[0][0]) if rows else None


def test_file_op_line_carries_view_link_and_stash(session, run_hook, test_env):
    """A Read one-liner is itself a claude-copy:///…/view hyperlink: the line op
    carries the tool_use_id as 'v', and the pre-rendered content block (dim line
    numbers + the file text) is stashed under kv view:<tid> at hook time."""
    s = session.make()
    path = os.path.join(s.cwd, "shown.py")
    with open(path, "w") as f:
        f.write("def hello():\n    return 42\n")
    run_hook("claude-file-fmt.py", P.post_file(s, tool="Read", path=path))

    lop = next(op for op in s.ops() if op["t"] == "line" and "Read" in op["s"])
    assert lop.get("v") == "toolu_001"
    assert "claude-copy:///%s/toolu_001/view" % s.sid in lop["s"]

    stash = _kv(s, "view:toolu_001")
    assert stash and {"rule", "label", "gut"} <= {o["t"] for o in stash}
    # the body is stashed RAW + a paint-time lex/num spec — highlighting and
    # line-numbering are the renderer's job (the hook python may lack pygments)
    gutop = next(o for o in stash if o["t"] == "gut")
    assert gutop["s"] == "def hello():\n    return 42"
    assert gutop["lex"] == "python" and gutop["num"] == 1


def test_update_view_is_numbered_diff(session, run_hook, test_env):
    """An Update's stashed view block is delta-style: contiguous runs carry raw
    code + a paint-time lex/num spec (the renderer highlights it), removals on
    a soft red panel, additions on a soft green panel, context bare — with the
    OLD line number on the removal run and the NEW one elsewhere."""
    s = session.make()
    patch = [{"oldStart": 10, "oldLines": 2, "newStart": 10, "newLines": 2,
              "lines": [" ctx", "-gone", "+here"]}]
    run_hook("claude-file-fmt.py", P.post_file(s, tool="Edit", patch=patch))

    stash = _kv(s, "view:toolu_001")
    assert stash
    guts = [o for o in stash if o["t"] == "gut"]
    red = next(o for o in guts if o["s"] == "gone")
    green = next(o for o in guts if o["s"] == "here")
    ctx = next(o for o in guts if o["s"] == "ctx")
    assert red.get("bg") and green.get("bg") and red["bg"] != green["bg"]
    assert not ctx.get("bg")
    # example.py -> python lexer, highlighted at paint time; numbers per run
    assert red["lex"] == green["lex"] == ctx["lex"] == "python"
    assert (ctx["num"], red["num"], green["num"]) == (10, 11, 11)


def test_update_view_without_lexer_styles_inline(session, run_hook, test_env):
    """A diff on a file with no known lexer falls back to producer-styled
    red/green foreground rows with baked line numbers."""
    s = session.make()
    path = os.path.join(s.cwd, "notes.txt")
    patch = [{"oldStart": 3, "oldLines": 1, "newStart": 3, "newLines": 1,
              "lines": ["-old words", "+new words"]}]
    run_hook("claude-file-fmt.py", P.post_file(s, tool="Edit", path=path,
                                               patch=patch))
    stash = _kv(s, "view:toolu_001")
    guts = [o for o in stash if o["t"] == "gut"]
    red = next(o for o in guts if "old words" in o["s"])
    green = next(o for o in guts if "new words" in o["s"])
    assert "lex" not in red and "lex" not in green
    assert "38;2;224;108;117" in red["s"]          # RED removal text
    assert "38;2;152;195;121" in green["s"]        # GREEN addition text
    assert "    3 " in red["s"] and "    3 " in green["s"]


def test_view_click_toggles_in_place_expansion(session, run_hook, test_env):
    """Clicking the /view link flips the id in the `view-open` kv set (expand),
    and a second click removes it (collapse) — the renderer reflows on each."""
    s = session.make()
    path = os.path.join(s.cwd, "toggle.py")
    with open(path, "w") as f:
        f.write("x = 1\n")
    run_hook("claude-file-fmt.py", P.post_file(s, tool="Read", path=path))

    url = "claude-copy:///%s/toolu_001/view" % s.sid
    run_hook("claude-copy.py", raw_stdin="", argv=(url,))
    assert _kv(s, "view-open") == ["toolu_001"]
    run_hook("claude-copy.py", raw_stdin="", argv=(url,))
    assert _kv(s, "view-open") == []

    # a click on an id with no stash is a feedback no-op, not a toggle
    run_hook("claude-copy.py",
             raw_stdin="", argv=("claude-copy:///%s/toolu_nope/view" % s.sid,))
    assert _kv(s, "view-open") == []
    assert "nothing to show" in s.ops_text()


def test_renderer_expands_view_block_in_place(session, run_hook, seed, reaper,
                                              test_env):
    """With the id in `view-open`, the renderer paints the stashed block inline
    after the v-tagged line — and drops it again once the set empties."""
    s = session.make()
    path = os.path.join(s.cwd, "inline.py")
    with open(path, "w") as f:
        f.write("MAGIC_VIEW_BODY = True\n")
    run_hook("claude-file-fmt.py", P.post_file(s, tool="Read", path=path))
    url = "claude-copy:///%s/toolu_001/view" % s.sid
    run_hook("claude-copy.py", raw_stdin="", argv=(url,))

    out_path = s.log + ".render.out"
    with open(out_path, "wb") as out:
        proc = subprocess.Popen(
            [sys.executable, os.path.join(REPO, "claude-mirror.py"), s.log, "100"],
            stdout=out, stderr=subprocess.DEVNULL, env=dict(test_env), cwd=REPO)
    reaper.append(proc)
    try:
        wait_until(lambda: b"MAGIC_VIEW_BODY" in open(out_path, "rb").read(),
                   desc="renderer expanded the view block in place")
        text = open(out_path, "rb").read().decode("utf-8", "replace")
        assert "    1" in text                        # paint-time line numbers
        assert "38;2;198;120;221" in text             # pygments highlight applied
        # the renderer registered itself for the click handler's instant nudge
        assert _kv(s, "renderer-pid") == proc.pid
        # collapse: the repaint clears the screen and repaints without the body
        run_hook("claude-copy.py", raw_stdin="", argv=(url,))
        wait_until(lambda: b"MAGIC_VIEW_BODY" not in
                   open(out_path, "rb").read().rpartition(b"\033[2J")[2],
                   desc="renderer collapsed the view block")
    finally:
        proc.terminate()


SUB_EDIT_EVENTS = [
    {"type": "assistant", "message": {
        "id": "smsg_e1", "model": "claude-opus-4-8", "role": "assistant",
        "content": [{"type": "tool_use", "id": "tu_ed", "name": "Edit",
                     "input": {"file_path": "/tmp/agent_target.py",
                               "old_string": "a = 1", "new_string": "a = 2"}}],
        "usage": {"input_tokens": 5, "output_tokens": 3,
                  "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": 0}}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "tu_ed", "content": "ok"}]}},
]


def test_subagent_file_op_gets_view_stash(session, run_hook, test_env):
    """A SUBAGENT's Update line (rendered by the substream) carries the same
    click-to-view affordance: v-tagged gut op + kv stash, diff built from the
    input strings (the transcript result has no structuredPatch)."""
    s = session.make()
    agent = "agent-" + uuid.uuid4().hex[:8]
    s.write_subagent_jsonl(agent, [])
    run_hook("claude-subagent-fmt.py", P.subagent_start(s, agent_id=agent),
             argv=("start",))
    s.write_subagent_jsonl(agent, SUB_EDIT_EVENTS)
    wait_until(lambda: any(op.get("v") == "tu_ed" for op in s.ops()),
               desc="subagent file op rendered with a view tag")

    gutop = next(op for op in s.ops() if op.get("v") == "tu_ed")
    assert gutop["t"] == "gut"
    assert "claude-copy:///%s/tu_ed/view" % s.sid in gutop["s"]
    stash = _kv(s, "view:tu_ed")
    assert stash
    body = "\n".join(o.get("s", "") for o in stash)
    assert "a = 1" in body and "a = 2" in body


def _load_mirror(log, width=80):
    """Import claude-mirror.py as a module (argv-driven globals patched)."""
    import importlib.util
    old = sys.argv
    sys.argv = ["claude-mirror.py", log, str(width)]
    try:
        spec = importlib.util.spec_from_file_location(
            "cmirror_under_test", os.path.join(REPO, "claude-mirror.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.argv = old
    return mod


def test_viewport_anchor_restores_scroll_offset(session, test_env, monkeypatch):
    """locate_viewport recovers the viewport's top-line offset by matching the
    captured visible text against the rendered rows (global search) — the
    exact-restore ingredient for 'expand in place without moving my scroll'."""
    s = session.make()
    mod = _load_mirror(s.log)
    mod.OPS.extend({"t": "line", "s": "history row %03d" % i} for i in range(60))
    mod.OPS.append({"t": "line", "s": "Read(anchor.py)", "v": "gid-anchor"})
    mod.OPS.extend({"t": "line", "s": "later row %03d" % i} for i in range(40))

    pos, idx, total = mod.measure("gid-anchor")
    assert mod.OPS[pos].get("v") == "gid-anchor" and idx == 61  # banner + 60

    # what the user sees: a 24-row viewport whose top is 5 lines above the link
    rows = [mod.R.strip_ansi(mod.BANNER).rstrip()]
    for op in mod.OPS:
        for o in mod.expanded(op):
            rows.extend(r.rstrip() for r in
                        mod.R.strip_ansi(mod.render(o, 80)).split("\n"))
    j = idx - 5
    captured = "\n".join(rows[j:j + 24])

    class _FE:
        def get_text(self, win, extent="screen"):
            return captured
    import frontends
    monkeypatch.setenv("KITTY_WINDOW_ID", "42")
    monkeypatch.setattr(frontends, "get", lambda: _FE())

    assert mod.locate_viewport(80) == j
    # garbage capture -> no confident match -> None (fallback: line-at-top)
    monkeypatch.setattr(_FE, "get_text",
                        lambda self, win, extent="screen": "zzz\nyyy\nxxx")
    assert mod.locate_viewport(80) is None


def test_toggle_repaint_pins_top_line(session, test_env, monkeypatch):
    """The top-line anchor rule: after a toggle reflow the viewport's TOP LINE
    is exactly where it was — expand or collapse, any block size. (stdin is
    not a tty here, so the DSR handshake is skipped and the scroll falls back
    to the recorded frontend call.)"""
    import collections
    s = session.make()
    mod = _load_mirror(s.log)
    mod.OPS.extend({"t": "line", "s": "row %03d" % i} for i in range(100))
    mod.OPS.append({"t": "line", "s": "Update(x.py)", "v": "g1"})
    mod.OPS.extend({"t": "line", "s": "tail %03d" % i} for i in range(40))
    _, idx, _ = mod.measure("g1")

    TS = collections.namedtuple("ts", "columns lines")
    monkeypatch.setattr(mod.os, "get_terminal_size", lambda: TS(80, 24))
    monkeypatch.setenv("KITTY_WINDOW_ID", "7")
    calls = []

    class _FE:
        def scroll_window(self, win, up):
            calls.append(up)
    import frontends
    monkeypatch.setattr(frontends, "get", lambda: _FE())

    h, j0 = 24, idx - 5                     # viewport top sat 5 lines above the link

    # collapse (id not open): top stays at j0
    _, _, total = mod.measure("g1")
    mod.toggle_repaint("g1", j0)
    assert calls[-1] == total + 1 - h - j0

    # expand a 10-row block: top STILL at j0 (block unfolds below, in place)
    mod._VIEW_OPS["g1"] = [{"t": "line", "s": "body %02d" % i} for i in range(10)]
    mod.VIEW_OPEN.add("g1")
    _, _, total = mod.measure("g1")
    mod.toggle_repaint("g1", j0)
    assert calls[-1] == total + 1 - h - j0

    # expand a screen-filling block: top STILL at j0 — the frame never moves
    mod._VIEW_OPS["g1"] = [{"t": "line", "s": "big %02d" % i} for i in range(40)]
    for op in mod.OPS:                      # heights changed: drop caches
        op.pop("_c", None)
    _, _, total = mod.measure("g1")
    mod.toggle_repaint("g1", j0)
    assert calls[-1] == total + 1 - h - j0


def test_viewport_anchor_disambiguates_twin_content(session, test_env,
                                                    monkeypatch):
    """A buffer with near-identical repeated blocks (many expanded views of
    the same file) matches the capture at MULTIPLE offsets — the anchor must
    pick the copy nearest the `near` prior (the clicked line / restore
    target), not the first best-scorer, or restores teleport to the wrong
    twin while the verify confirms that same wrong twin (audit-perfect,
    user-visible jump — observed live)."""
    s = session.make()
    mod = _load_mirror(s.log)
    twin = [{"t": "line", "s": "twin row %02d" % i} for i in range(30)]
    mod.OPS.extend(twin)                                   # copy A: rows 1..30
    mod.OPS.extend({"t": "line", "s": "middle %03d" % i} for i in range(50))
    mod.OPS.extend(dict(o) for o in twin)                  # copy B: rows 81..110
    mod.OPS.extend({"t": "line", "s": "tail %03d" % i} for i in range(30))

    captured = "\n".join("twin row %02d" % i for i in range(24))

    class _FE:
        def get_text(self, win, extent="screen"):
            return captured
    import frontends
    monkeypatch.setenv("KITTY_WINDOW_ID", "42")
    monkeypatch.setattr(frontends, "get", lambda: _FE())

    assert mod.locate_viewport(80) == 1               # no prior: first copy
    assert mod.locate_viewport(80, near=85) == 81     # prior picks copy B
    assert mod.locate_viewport(80, near=10) == 1      # prior picks copy A


def test_toggle_repaint_follow_mode_targets_new_bottom(session, test_env,
                                                       monkeypatch):
    """follow=True (the viewport was AT the bottom before the click) restores
    to the NEW bottom instead of pinning an absolute offset — an at-bottom
    viewport pinned to a fixed line silently stops following the live tail."""
    import collections
    s = session.make()
    mod = _load_mirror(s.log)
    mod.OPS.extend({"t": "line", "s": "row %03d" % i} for i in range(100))
    mod.OPS.append({"t": "line", "s": "Update(x.py)", "v": "g1"})

    TS = collections.namedtuple("ts", "columns lines")
    monkeypatch.setattr(mod.os, "get_terminal_size", lambda: TS(80, 24))
    monkeypatch.setenv("KITTY_WINDOW_ID", "7")
    calls = []

    class _FE:
        def scroll_window_end(self, win):
            calls.append("end")
            return True

        def scroll_window(self, win, up):
            calls.append(up)
            return 0
    import frontends
    monkeypatch.setattr(frontends, "get", lambda: _FE())

    mod._VIEW_OPS["g1"] = [{"t": "line", "s": "body %02d" % i} for i in range(10)]
    mod.VIEW_OPEN.add("g1")
    res = mod.toggle_repaint("g1", 999, follow=True)   # j0 overridden by follow
    assert res["follow"] is True and res["up"] == 0
    assert calls == ["end"]                 # end only — no relative up-scroll


def test_viewport_anchor_failure_paths_are_audited(session, test_env,
                                                   monkeypatch):
    """EVERY null return of a tagged locate_viewport leaves an errors row —
    the no-capture path was silent once and hid a live jump-to-end bug."""
    s = session.make()
    mod = _load_mirror(s.log)
    mod.OPS.append({"t": "line", "s": "just one row"})

    class _FE:
        def get_text(self, win, extent="screen"):
            return None
    import frontends
    from core import audit as A
    monkeypatch.setenv("KITTY_WINDOW_ID", "42")
    monkeypatch.setenv("CLAUDE_AUDIT_DIR", test_env["CLAUDE_AUDIT_DIR"])
    # the audit module caches its connection per-process — point it at THIS
    # test's audit dir regardless of what earlier tests in the worker did
    monkeypatch.setattr(A, "_CONN", None)
    monkeypatch.setattr(A, "_FAILED", False)
    monkeypatch.setattr(frontends, "get", lambda: _FE())
    assert mod.locate_viewport(80, tag="anchor") is None

    audit_db = os.path.join(test_env["CLAUDE_AUDIT_DIR"], "audit.db")
    conn = sqlite3.connect("file:%s?mode=ro" % audit_db, uri=True, timeout=5)
    try:
        funcs = [r[0] for r in conn.execute("SELECT func FROM errors").fetchall()]
    finally:
        conn.close()
    assert any("viewport_anchor (no capture)" in f for f in funcs)


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


def test_renderer_neutralizes_executable_output(session, seed, reaper, test_env):
    """RAW escape sequences in recorded command output must not EXECUTE when
    the renderer replays them (a tee'd @kitty-cmd scroll-window DCS scrolled
    the mirror to the top on every repaint — the live bug). Everything but
    SGR styling and OSC 8 hyperlinks is stripped at paint time."""
    s = session.make()
    seed.py(
        "import claude_ops as O\n"
        "O.emit(%r,\n"
        "  O.gut('\\x1bP@kitty-cmd{\"cmd\":\"scroll-window\"}\\x1b\\\\\\\\poisoned', (1,2,3)),\n"
        "  O.line('\\x1b[2J\\x1b[38;2;9;9;9mstyled\\x1b[0m'),\n"
        "  O.line('SENTINEL-NEUT'))" % s.log)
    out_path = s.log + ".render.out"
    with open(out_path, "wb") as out:
        proc = subprocess.Popen(
            [sys.executable, os.path.join(REPO, "claude-mirror.py"), s.log, "100"],
            stdout=out, stderr=subprocess.DEVNULL, env=dict(test_env), cwd=REPO)
    reaper.append(proc)
    try:
        wait_until(lambda: b"SENTINEL-NEUT" in open(out_path, "rb").read(),
                   desc="renderer painted the poisoned ops")
    finally:
        proc.terminate()
    text = open(out_path, "rb").read().decode("utf-8", "replace")
    assert "poisoned" in text and "styled" in text
    assert "@kitty-cmd" not in text                    # the DCS did not survive
    # the op's embedded 2J (clear) was stripped; the renderer's own clears
    # remain (they start each repaint), so count: exactly the repaint ones
    assert "\x1b[38;2;9;9;9m" in text                  # SGR survived
