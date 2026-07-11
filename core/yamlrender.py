# core/yamlrender.py — syntax-highlight YAML for the mirror.
#
# Sibling of core/jsonrender.py: when a command streams a .yml/.yaml file, the
# mirror colours it (keys blue, strings green, numbers orange, true/false/null
# magenta, comments grey — reusing the render.COL palette) and emits it on the
# normal gutter (no background panel).
#
# Unlike JSON, YAML is NOT reparsed/reformatted — a round-trip through a YAML
# loader would drop comments and reorder keys, which is destructive for the hand-
# written config files this targets. We colour the raw text as-is via pygments'
# YamlLexer, preserving every byte of structure. Colour needs pygments; without
# it we fall back to the raw text verbatim. Like JSON we render once at close()
# (block scalars / nested context make partial colouring unreliable), and never
# raise.
from core import render as R


def _pick(ttype):
    s = str(ttype)
    if s.startswith("Token.Comment"):                           return R.COL["cmt"]
    if s.startswith("Token.Name.Tag"):                          return R.COL["func"]   # keys
    if s.startswith(("Token.Literal.String", "Token.String")):  return R.COL["str"]
    if s.startswith(("Token.Literal.Number", "Token.Number")):  return R.COL["num"]
    if s.startswith("Token.Keyword"):                           return R.COL["kw"]     # true/false/null
    if s.startswith(("Token.Punctuation", "Token.Operator")):   return R.COL["op"]
    if s.startswith("Token.Literal"):                           return R.COL["str"]    # scalars
    return R.COL["def"]


def render_yaml(text):
    """Colour `text` as YAML (raw, no reformat), else None if pygments is absent."""
    body = (text or "").rstrip("\n")
    if not body.strip():
        return None
    try:
        from pygments.lexers import YamlLexer
        out = [_pick(tt) + val for tt, val in YamlLexer().get_tokens(body)]
        return "".join(out).rstrip("\n") + R.RST
    except Exception:                               # pygments absent / lexer error
        return None


class YamlStreamer:
    """Buffer a command's whole output, then at close() emit it colour-highlighted
    (or verbatim if pygments is unavailable). Mirrors the feed()/close() ->
    list[(text, bg)] contract; bg is always None (no panel)."""

    def __init__(self):
        self.buf = ""

    def feed(self, text):
        self.buf += text
        return []                                   # render once whole

    def close(self):
        raw, self.buf = self.buf, ""
        body = render_yaml(raw)
        if body is None:
            body = R.emphasize(R.unescape(raw))
        body = body.rstrip("\n")
        return [(body, None)] if body.strip() else []
