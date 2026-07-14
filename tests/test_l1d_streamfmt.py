# L1d — core/streamfmt.py, the shared stream-renderer vocabulary.
#
# These helpers were extracted from two divergence-prone copies (the subagent
# substream renderer and the codex stream); the pins below are the exact shapes
# both sites rendered BEFORE the extraction — byte-identical ops/fragments, so
# a "cleanup" here shows up as a golden diff, not a silent restyle.
import os

from core import ops as O
from core import render as R
from core import state as S
from core import streamfmt as SF

RGB = (10, 20, 30)


# --- cap ---------------------------------------------------------------------

def test_cap_short_text_unchanged():
    assert SF.cap("a\nb\nc", 3) == "a\nb\nc"
    assert SF.cap("", 1) == ""


def test_cap_truncates_with_more_marker():
    assert SF.cap("a\nb\nc\nd", 2) == "a\nb\n… (2 more lines)"
    assert SF.cap("a\nb\nc", 2) == "a\nb\n… (1 more line)"   # singular


# --- chip --------------------------------------------------------------------

def test_chip_codex_shape():
    # The codex header: no tags — exactly the old O.label(f"codex {g} {k}", rgb).
    got = SF.chip("codex", "▶", "cmd", RGB, g="g1", lk=[["cmd", "⧉cmd"]])
    assert got == O.label("codex ▶ cmd", RGB, g="g1", lk=[["cmd", "⧉cmd"]])
    assert got["s"] == "codex ▶ cmd"


def test_chip_substream_tags():
    # The substream header: model tag + ctx ride as trailing double-spaced chips,
    # empties skipped — the old inline f-string concatenation, byte for byte.
    assert SF.chip("explore", "✎", "message", RGB,
                   tags=("opus", "ctx 42%"))["s"] == "explore ✎ message  opus  ctx 42%"
    assert SF.chip("explore", "✎", "message", RGB,
                   tags=("", "ctx 42%"))["s"] == "explore ✎ message  ctx 42%"
    assert SF.chip("explore", "✎", "message", RGB,
                   tags=("opus", ""))["s"] == "explore ✎ message  opus"
    assert SF.chip("explore", "✎", "message", RGB,
                   tags=("", ""))["s"] == "explore ✎ message"


# --- gutter / dim_gut ----------------------------------------------------------

def test_gutter_unescapes_into_gut_op():
    got = SF.gutter("body\\ntext", RGB, g="g2")
    assert got == O.gut(R.unescape("body\\ntext"), RGB, g="g2")
    assert got["t"] == "gut" and got["c"] == [10, 20, 30]


def test_dim_gut_wraps_in_dim():
    got = SF.dim_gut("thinking", RGB)
    assert got == O.gut(R.DIM + "thinking" + R.RST, RGB)


# --- file_line -----------------------------------------------------------------
# The file-op one-liner extracted from THREE hand-built copies (file_fmt main,
# the substream's render_file, the codex render_patch). Pins are the exact bytes
# each site painted before the extraction, per that caller's shape.

YELLOW = R.fg(*O.YELLOW)
BLUE = R.fg(*O.BLUE)
GREEN = R.fg(*O.GREEN)
RED = R.fg(*O.RED)
DEF = R.COL["def"]


def _head(col, verb, name):
    return col + verb + R.DIM + "(" + DEF + name + R.DIM + ")" + R.RST


def test_file_line_read_extent():
    # file_fmt's Read shape: verb(name) + dim extent, no counts/range.
    got = SF.file_line("Read", "a.py", O.BLUE, extent="120-160/400")
    assert got == _head(BLUE, "Read", "a.py") + "  " + R.DIM + "120-160/400" + R.RST


def test_file_line_whole_file_read_is_bare():
    assert SF.file_line("Read", "a.py", O.BLUE) == _head(BLUE, "Read", "a.py")


def test_file_line_update_counts_and_range():
    # The mutation shape shared by file_fmt and the substream: green +A, red -R,
    # then the dim structuredPatch range.
    got = SF.file_line("Update", "b.py", O.YELLOW, added=3, removed=1, rng="41-52")
    assert got == (_head(YELLOW, "Update", "b.py")
                   + "  " + GREEN + "+3" + R.RST + " " + RED + "-1" + R.RST
                   + "  " + R.DIM + "41-52" + R.RST)


def test_file_line_single_sided_counts():
    # Only one side present: no stray joiner space (codex render_patch shape).
    got = SF.file_line("Write", "c.py", O.GREEN, added=12)
    assert got == _head(GREEN, "Write", "c.py") + "  " + GREEN + "+12" + R.RST
    got = SF.file_line("Delete", "d.py", O.RED, removed=7)
    assert got == _head(RED, "Delete", "d.py") + "  " + RED + "-7" + R.RST


def test_file_line_failed_is_red_head_only():
    # A failed op: red verb(name), and every suffix suppressed even if passed —
    # the counts would claim lines never written. The ✗ mark stays caller-side.
    got = SF.file_line("Update", "e.py", O.YELLOW, failed=True,
                       extent="1-9/9", added=3, removed=1, rng="1-3")
    assert got == _head(RED, "Update", "e.py")


# --- tok_rollup ------------------------------------------------------------------

def test_tok_rollup_empty_when_no_tokens():
    assert SF.tok_rollup(0, 0, 0) == ""
    assert SF.tok_rollup(0, 0, 500) == ""       # cache alone doesn't show


def test_tok_rollup_substream_shape():
    # denominator defaults to fresh + cached (the substream's reads).
    assert SF.tok_rollup(124000, 3000, 124000) == \
        " · 124k in · 3k out · cache 50%"


def test_tok_rollup_no_cache_line_when_no_reads():
    # out-only (reads == 0): no cache % — matches both old gates.
    assert SF.tok_rollup(0, 900, 0) == " · 0 in · 900 out"


def test_tok_rollup_codex_reads_override():
    # codex passes reads=input_tokens (already cache-inclusive): 100k total input,
    # 80k cached -> fresh 20k, cache 80%.
    assert SF.tok_rollup(20000, 5000, 80000, reads=100000) == \
        " · 20k in · 5k out · cache 80%"


# --- state parked probe -----------------------------------------------------------

def test_parked_tracks_state_db_file(tmp_path):
    log = str(tmp_path / "claude-mirror-x.log")
    assert S.parked(log)                        # never existed -> parked
    db = S.db_path(log)
    open(db, "w").close()
    assert not S.parked(log)                    # file exists -> alive
    os.remove(db)                               # SessionEnd parks it away
    assert S.parked(log)
    # The probe itself must never CREATE the file (a connect would).
    assert not os.path.exists(db)
