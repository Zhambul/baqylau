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


# JSON tweaks over render.pick's core ladder: object keys (Name.Tag) get the
# function colour, and everything the core ladder would colour but JSON doesn't
# (other Name.*, comments — json.loads rejects them anyway) stays default.
_PRE = (("Token.Name.Tag", "func"),     # object keys
        ("Token.Name", "def"),
        ("Token.Comment", "def"))


def _pick(ttype):
    return R.pick(ttype, pre=_PRE)


def _pretty(obj):
    """Pretty-print (indent=2) + colour one parsed JSON value."""
    pretty = json.dumps(obj, indent=2, ensure_ascii=False)
    try:
        # render.lexer: singleton cache — _pretty runs once per JSONL LINE, so a
        # per-call JsonLexer() construction was paid per line, not per block.
        out = [_pick(tt) + val for tt, val in R.lexer("json").get_tokens(pretty)]
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


class JsonStreamer(R.BufferedStreamer):
    """Buffer a command's whole output, then at close() emit it as pretty,
    coloured JSON — or the raw text if it isn't valid JSON (the base's verbatim
    fallback)."""

    def render(self, raw):
        return render_json(raw)
