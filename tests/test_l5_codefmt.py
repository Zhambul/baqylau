# L5 — core/codefmt.py: the bash/python tokenizer + pretty-printer, split out of
# core/render.py (render keeps the primitives + thin delegating aliases). Pure
# module-level tests — no subprocesses, no kitty.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import codefmt as CF
from core import render as R


def test_format_code_reflows_dense_bash_oneliner():
    assert CF.format_code("a && b || c; d") == "a &&\nb ||\nc\nd"


def test_format_code_leaves_multiline_and_strings_alone():
    multi = "a\nb && c"
    assert CF.format_code(multi) == multi
    assert CF.format_code('echo "a && b"') == 'echo "a && b"'


def test_format_code_pretty_prints_python_c():
    out = CF.format_code("python3 -c 'a=1; print(a)'")
    assert out == "python3 -c 'a = 1\nprint(a)'"


def test_split_heredocs_isolates_python_body():
    code = "python3 <<EOF\nx=1\nprint(x)\nEOF\n"
    segs = CF._split_heredocs(code)
    assert ("python", "x=1\nprint(x)\n") in segs
    assert "".join(t for _, t in segs) == code  # concatenation reproduces the command


def test_render_highlights_and_resets():
    out = CF.render("echo hi", 40)
    assert out.endswith(R.RST)
    assert "echo" in out and "hi" in out
    assert "\x1b[38;2;" in out  # truecolor SGR emitted


def test_render_wraps_at_width_with_hanging_indent():
    out = CF.render("command --with-a-long-flag --another-flag", 20)
    lines = [R.strip_ansi(l) for l in out.split("\n")]
    assert len(lines) > 1
    assert all(len(l) <= 20 for l in lines)
    assert lines[1].startswith("  ")  # hanging continuation indent


def test_render_aliases_delegate_byte_identical():
    # render.py keeps thin format_code/render aliases for historical call sites
    # (claude_render compat shim, old producers) — outputs must be byte-identical.
    cmd = "for f in *.py; do wc -l $f; done && echo ok"
    assert R.format_code(cmd) == CF.format_code(cmd)
    assert R.render(cmd, 30) == CF.render(cmd, 30)
    assert R.render(cmd, 30, ind="    ") == CF.render(cmd, 30, ind="    ")
