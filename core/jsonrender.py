# core/jsonrender.py — pretty-print + syntax-highlight JSON for the mirror.
#
# Sibling of core/mdrender.py: when a command streams a .json file, the mirror
# pretty-prints it (json.dumps indent=2) and colours it (keys blue, strings
# green, numbers orange, true/false/null magenta, punctuation cyan — reusing the
# render.COL palette). Colouring uses pygments' JsonLexer when available and
# degrades to a plain (still pretty-printed) block otherwise. No background panel
# (that's reserved for markdown fenced code) — just colour on the normal gutter.
#
# Unlike markdown, JSON CANNOT be rendered incrementally — a partial document is
# invalid — so JsonStreamer buffers the whole output and renders once at close().
# JSON Lines / NDJSON (one JSON value per line, `.jsonl`/`.ndjson`) is handled too:
# every non-blank line is pretty-printed, blank-line separated. If the buffer is
# neither a single value nor all-lines-valid JSONL (truncated by head/tail, plain
# log output), it falls back to the raw text verbatim, never raising.
import json

from core import render as R


def _pick(ttype):
    s = str(ttype)
    if s.startswith("Token.Name.Tag"):                          return R.COL["func"]   # object keys
    if s.startswith(("Token.Literal.String", "Token.String")):  return R.COL["str"]
    if s.startswith(("Token.Literal.Number", "Token.Number")):  return R.COL["num"]
    if s.startswith("Token.Keyword"):                           return R.COL["kw"]     # true/false/null
    if s.startswith(("Token.Punctuation", "Token.Operator")):   return R.COL["op"]
    return R.COL["def"]


def _pretty(obj):
    """Pretty-print (indent=2) + colour one parsed JSON value."""
    pretty = json.dumps(obj, indent=2, ensure_ascii=False)
    try:
        from pygments.lexers import JsonLexer
        out = [_pick(tt) + val for tt, val in JsonLexer().get_tokens(pretty)]
        return "".join(out).rstrip("\n") + R.RST
    except Exception:                               # pygments absent -> uncoloured, still pretty
        return R.COL["def"] + pretty + R.RST


def _render_jsonl(text):
    """JSON Lines / NDJSON: every non-blank line is its own JSON value. Render each
    pretty+coloured, blank-line separated. Returns None unless there are ≥2 lines
    and EVERY one parses (so a stray non-JSON line falls the whole thing back to
    verbatim rather than rendering a misleading partial view)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    docs = []
    for ln in lines:
        s = ln.strip()
        if s[0] not in "{[":
            return None
        try:
            docs.append(_pretty(json.loads(s)))
        except Exception:
            return None
    return "\n\n".join(docs)


def render_json(text):
    """Pretty-print + colour `text` as a single JSON value, or as JSON Lines /
    NDJSON (one value per line); None if it is neither."""
    stripped = (text or "").strip()
    if not stripped:
        return None
    if stripped[0] in "{[":                         # a single object/array?
        try:
            return _pretty(json.loads(stripped))
        except Exception:
            pass                                    # maybe JSON Lines — fall through
    return _render_jsonl(stripped)


class JsonStreamer:
    """Buffer a command's whole output, then at close() emit it as pretty,
    coloured JSON — or the raw text if it isn't valid JSON. Mirrors
    mdrender.MarkdownStreamer's feed()/close() -> list[(text, bg)] contract so the
    tailer drives both the same way. bg is always None (no panel)."""

    def __init__(self):
        self.buf = ""

    def feed(self, text):
        self.buf += text
        return []                                   # JSON only renders once whole

    def close(self):
        raw, self.buf = self.buf, ""
        body = render_json(raw)
        if body is None:
            body = R.emphasize(R.unescape(raw))      # not JSON -> verbatim
        body = body.rstrip("\n")
        return [(body, None)] if body.strip() else []
