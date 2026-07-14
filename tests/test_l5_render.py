# L5 — renderer goldens.
#
# The whole producer/renderer contract in one pin: a canonical op sequence
# (built through claude_ops, highlighting included) rendered by the real
# claude-mirror.py at two FIXED_WIDTHs. Width 100 vs 60 is the reflow pin —
# the same width-INDEPENDENT ops must wrap/gutter differently at paint time.
# Regenerate with UPDATE_GOLDEN=1 after an intentional renderer change.
import os
import signal
import subprocess
import sys
import time

import pytest

from conftest import REPO, wait_until

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")

SEED_OPS = r'''
import claude_ops as O
import claude_render as R
log = %(log)r
O.emit(log,
       O.blank(), O.rule(), O.label("▶ foreground", O.SLATE),
       O.code("for i in $(seq 3); do echo line-$i; done"),
       O.rule(),
       O.gut("line-1\nline-2\nline-3", O.SLATE),
       O.rule(), O.label("■ finished · 1.2s", O.SLATE), O.rule())
O.emit(log,
       O.blank(), O.rule(), O.label("▷ background", O.ORANGE),
       O.code("python3 train.py --epochs 10 --batch-size 32 --learning-rate 0.0003"),
       O.rule(),
       O.gut("epoch 1/10 loss 2.31\nepoch 2/10 loss 1.94 " + "x" * 80, O.ORANGE),
       O.rule(), O.label("■ failed (exit 1) · 3m07s", O.RED), O.rule())
O.emit(log,
       O.line("%(sentinel)s"))
'''


def build_ops(env, log, sentinel):
    p = subprocess.run([sys.executable, "-c", SEED_OPS % {"log": log,
                                                          "sentinel": sentinel}],
                       env=dict(env), cwd=REPO, capture_output=True, text=True,
                       timeout=20)
    assert p.returncode == 0, p.stderr


def render_at(env, reaper, log, width, sentinel):
    """Run the real renderer non-tty at a fixed width until the sentinel op
    has been painted, then stop it and return everything it wrote."""
    out_path = log + ".render-%d.out" % width
    with open(out_path, "wb") as out:
        proc = subprocess.Popen(
            [sys.executable, os.path.join(REPO, "claude-mirror.py"), log,
             str(width)],
            stdout=out, stderr=subprocess.DEVNULL, env=dict(env), cwd=REPO)
    reaper.append(proc)
    try:
        wait_until(lambda: sentinel.encode() in open(out_path, "rb").read(),
                   desc="renderer painted the sentinel op")
        time.sleep(0.3)                       # let the paint flush fully
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
    with open(out_path, "rb") as f:
        return f.read()


def check_golden(name, data):
    path = os.path.join(GOLDEN_DIR, name)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        pytest.skip("golden %s regenerated" % name)
    assert os.path.exists(path), \
        "golden %s missing — run UPDATE_GOLDEN=1 pytest %s" % (name, __file__)
    with open(path, "rb") as f:
        want = f.read()
    assert data == want, \
        ("renderer output diverged from golden %s (if the change is "
         "intentional: UPDATE_GOLDEN=1)" % name)


SENTINEL = "END-OF-CANON"


@pytest.mark.parametrize("width", [100, 60])
def test_canonical_ops_golden(test_env, session, reaper, width):
    s = session.make(sid="golden-render")        # fixed sid: byte-stable paths
    build_ops(test_env, s.log, SENTINEL)
    data = render_at(test_env, reaper, s.log, width, SENTINEL)
    assert SENTINEL.encode() in data
    check_golden("mirror-w%d.ansi" % width, data)


def test_same_ops_reflow_differently_by_width(test_env, session, reaper):
    """The reflow property itself (independent of golden bytes): the long
    gutter line must occupy more painted lines at 60 columns than at 100."""
    s = session.make(sid="golden-reflow")
    build_ops(test_env, s.log, SENTINEL)
    wide = render_at(test_env, reaper, s.log, 100, SENTINEL)
    narrow = render_at(test_env, reaper, s.log, 60, SENTINEL)
    assert wide != narrow
    assert narrow.count(b"\n") > wide.count(b"\n"), \
        "narrower pane should need more wrapped lines"


def test_semantic_color_table_not_reencoded_by_producers():
    """CLAUDE.md invariant: the semantic colour table in core/ops.py is shared
    vocabulary producers must not re-encode. Grep every .py outside core/ops.py
    (and core/render.py's own palette) for raw occurrences of those triplets."""
    import re
    from core import ops as O
    assert O.GREEN == (152, 195, 121)
    assert O.RED == (224, 108, 117)
    triplets = [O.SLATE, O.ORANGE, O.RED, O.GREEN, O.YELLOW, O.BLUE, O.AMBER]
    pats = [re.compile(r"\b%d,\s*%d,\s*%d\b" % t) for t in triplets]
    offenders = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in
                   (".git", "__pycache__", "tests", ".claude")]
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            rel = os.path.relpath(path, REPO)
            if rel in ("core/ops.py", "core/render.py"):
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    for p in pats:
                        if p.search(line):
                            offenders.append("%s:%d: %s" % (rel, i, line.strip()))
    assert not offenders, \
        "raw semantic-table RGB triplets re-encoded (use core.ops constants):\n" \
        + "\n".join(offenders)


# ---------------------------------------------------------------------------
# _ANSI / _CTRL shared-fragment pins. Both regexes are composed from the named
# fragments _CSI_RE/_OSC_RE/_C1_RE (plus _CTRL's own _DCS_RE) so the wrap/strip
# path and the security-critical neutralize() replay-safety path cannot drift.


def test_ansi_ctrl_patterns_equal_historic_literals():
    """The composed patterns must be character-identical to the pre-refactor
    literals (behaviour-preserving refactor pin)."""
    from core import render as R
    old_ansi = (r"\x1b\[[0-9;:?]*[ -/]*[@-~]"
                r"|\x1b\][^\x1b\x07]*(?:\x07|\x1b\\)"
                r"|\x1b[@-Z\\-_]")
    old_ctrl = (r"\x1b\[[0-9;:?]*[ -/]*[@-~]"
                r"|\x1b\][^\x1b\x07]*(?:\x07|\x1b\\)"
                r"|\x1b[PX^_][^\x1b]*(?:\x1b\\|\x07)?"
                r"|\x1b[@-Z\\-_]")
    assert R._ANSI.pattern == old_ansi
    assert R._CTRL.pattern == old_ctrl
    assert R._ANSI.flags == R._CTRL.flags


def test_strip_ansi_behaviour():
    from core.render import strip_ansi
    # CSI (SGR and non-SGR)
    assert strip_ansi("\x1b[31mred\x1b[0m") == "red"
    assert strip_ansi("a\x1b[2Jb") == "ab"
    # OSC with BEL and with ST terminators (incl. OSC 8 hyperlinks — zero-width
    # for wrapping, so strip removes them entirely)
    assert strip_ansi("\x1b]0;title\x07text") == "text"
    assert strip_ansi("\x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\") == "link"
    # 2-char C1
    assert strip_ansi("a\x1bMb") == "ab"
    assert strip_ansi("plain") == "plain"


def test_neutralize_behaviour():
    from core.render import neutralize
    # SGR styling survives
    assert neutralize("\x1b[31mred\x1b[0m") == "\x1b[31mred\x1b[0m"
    # OSC 8 hyperlinks survive (the mirror's copy/view links)
    link = "\x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\"
    assert neutralize(link) == link
    # Non-SGR CSI is executable — stripped
    assert neutralize("a\x1b[2Jb") == "ab"
    assert neutralize("a\x1b[5;5Hb") == "ab"
    # Non-8 OSC stripped (BEL and ST forms)
    assert neutralize("\x1b]0;title\x07t") == "t"
    assert neutralize("\x1b]52;c;YWJj\x1b\\t") == "t"
    # The documented live bug: a tee'd @kitty-cmd DCS must not reach the pane
    dcs = "\x1bP@kitty-cmd{\"cmd\":\"scroll-window\"}\x1b\\"
    assert neutralize("before" + dcs + "after") == "beforeafter"
    # SOS/PM/APC and bare 2-char C1
    assert neutralize("a\x1b_apc payload\x1b\\b") == "ab"
    assert neutralize("a\x1bMb") == "ab"
    # Malformed/truncated sequences: no raw ESC may survive except one opening
    # a sanctioned form
    out = neutralize("trunc\x1b[12;")
    assert all(c != "\x1b" or out[i:i + 2] in ("\x1b[", "\x1b]", "\x1b\\")
               for i, c in enumerate(out))
