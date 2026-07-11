# core/jsonrender.py — pretty-print + syntax-highlight JSON for the mirror.
#
# Sibling of core/mdrender.py: when a command streams a .json file, the mirror
# pretty-prints it (json.dumps indent=2) and colours it (keys blue, strings
# green, numbers orange, true/false/null magenta, punctuation cyan — reusing the
# render.COL palette), then emits it as a full-width CODE_BG panel like a fenced
# code block. Colouring uses pygments' JsonLexer when available and degrades to a
# plain (still pretty-printed) panel otherwise.
#
# Unlike markdown, JSON CANNOT be rendered incrementally — a partial document is
# invalid — so JsonStreamer buffers the whole output and renders once at close().
# If the buffer isn't valid JSON (truncated by head/tail, JSON Lines, plain log
# output), it falls back to the raw text verbatim, never raising.
import json

from core import render as R
from core.mdrender import CODE_BG          # one shared panel background


def _pick(ttype):
    s = str(ttype)
    if s.startswith("Token.Name.Tag"):                          return R.COL["func"]   # object keys
    if s.startswith(("Token.Literal.String", "Token.String")):  return R.COL["str"]
    if s.startswith(("Token.Literal.Number", "Token.Number")):  return R.COL["num"]
    if s.startswith("Token.Keyword"):                           return R.COL["kw"]     # true/false/null
    if s.startswith(("Token.Punctuation", "Token.Operator")):   return R.COL["op"]
    return R.COL["def"]


def render_json(text):
    """Pretty-print + colour `text` if it is a single JSON value, else None."""
    stripped = (text or "").strip()
    if not stripped or stripped[0] not in "{[":     # fast reject: not an object/array
        return None
    try:
        obj = json.loads(stripped)
    except Exception:
        return None
    pretty = json.dumps(obj, indent=2, ensure_ascii=False)
    try:
        from pygments.lexers import JsonLexer
        out = [_pick(tt) + val for tt, val in JsonLexer().get_tokens(pretty)]
        return "".join(out).rstrip("\n") + R.RST
    except Exception:                               # pygments absent -> uncoloured, still pretty
        return R.COL["def"] + pretty + R.RST


class JsonStreamer:
    """Buffer a command's whole output, then at close() emit it as a pretty,
    coloured JSON panel — or the raw text if it isn't valid JSON. Mirrors
    mdrender.MarkdownStreamer's feed()/close() -> list[(text, bg)] contract so the
    tailer drives both the same way."""

    def __init__(self):
        self.buf = ""

    def feed(self, text):
        self.buf += text
        return []                                   # JSON only renders once whole

    def close(self):
        raw, self.buf = self.buf, ""
        body = render_json(raw)
        if body is not None:
            return [(body, CODE_BG)]
        raw = raw.rstrip("\n")                       # not JSON -> verbatim, no panel
        return [(R.emphasize(R.unescape(raw)), None)] if raw.strip() else []
