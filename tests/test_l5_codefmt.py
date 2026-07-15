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


# --- pygments lexer singletons (render.lexer) -------------------------------------
# Lexer construction compiles token-table regexes; instances are stateless per
# get_tokens call, so render.lexer caches ONE per name per process — and the
# rendered output must be byte-identical to a fresh-lexer render (the goldens
# above already pin format/render output shapes; this pins the cache itself).

def test_render_lexer_is_a_singleton_and_render_is_stable():
    assert R.lexer("bash") is R.lexer("bash")
    assert R.lexer("python") is R.lexer("python")
    assert R.lexer("bash") is not R.lexer("python")
    cmd = "for f in *.py; do python3 -c 'print(1); print(2)'; done"
    first = CF.render(cmd, 60)
    assert CF.render(cmd, 60) == first          # reuse changes nothing
    from pygments.lexers import BashLexer
    fresh = list(BashLexer().get_tokens_unprocessed("echo hi | wc -l"))
    cached = list(R.lexer("bash").get_tokens_unprocessed("echo hi | wc -l"))
    assert fresh == cached                      # cached lexer tokenises identically
