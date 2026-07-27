# core/coderender.py — syntax-highlight source files for the mirror.
#
# Generic sibling of jsonrender/yamlrender: when a command streams a source file
# (.py / .kt / .java / …), the mirror colours it via the matching pygments lexer,
# reusing render.pick (the same token→colour map markdown fenced code uses:
# keywords magenta, builtins cyan, function names blue, strings green, numbers
# orange, comments grey). Colour in place — source is never reformatted — and no
# background panel (that stays reserved for markdown fenced code).
#
# Adding a language is one line in LANGS. Colour needs pygments; without it we
# fall back to the raw text verbatim. Rendered once at close() (multi-line strings
# / comments make partial colouring unreliable), and never raises.
from core import render as R

# File extension -> pygments lexer name. Extend here to support more languages.
#
# Two consumers match an extension by SUFFIX rather than by splitext (tools.
# _lexer_match, opshtml.tools._lexer_for: first entry whose ext the word ends
# with wins), so no key may be a suffix of another or insertion order would
# decide. The dot in each key is what keeps the families apart — ".cjs" does not
# end with ".js", ".tsx" does not end with ".ts", ".kts" does not end with ".ts"
# — so every member of a family needs its own row.
LANGS = {
    ".py": "python", ".pyi": "python",
    ".kt": "kotlin", ".kts": "kotlin",
    ".java": "java",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
    ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "scss", ".less": "less",
}


def render_code(text, lexer_name):
    """Colour `text` with the named pygments lexer, else None (pygments/lexer
    absent)."""
    body = (text or "").rstrip("\n")
    if not body.strip():
        return None
    try:
        out = [R.pick(str(tt)) + val for tt, val in R.lexer(lexer_name).get_tokens(body)]
        return "".join(out).rstrip("\n") + R.RST
    except Exception:
        return None


class CodeStreamer(R.BufferedStreamer):
    """Buffer a source file's whole output, then at close() emit it colour-
    highlighted with the given lexer (or verbatim — the base's fallback — if
    pygments is unavailable)."""

    def __init__(self, lexer_name):
        super().__init__()
        self.lexer = lexer_name

    def render(self, raw):
        return render_code(raw, self.lexer)
