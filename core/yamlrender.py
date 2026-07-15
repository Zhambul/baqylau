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


# YAML tweaks over render.pick's core ladder: keys (Name.Tag) get the function
# colour, other Name.* (anchors/aliases) stay default, and any remaining
# Token.Literal (plain scalars) falls back to the string colour — AFTER the core
# ladder so Literal.String/Literal.Number keep their own colours.
_PRE  = (("Token.Name.Tag", "func"),    # keys
         ("Token.Name", "def"))
_POST = (("Token.Literal", "str"),)     # plain scalars


def _pick(ttype):
    return R.pick(ttype, pre=_PRE, post=_POST)


def render_yaml(text):
    """Colour `text` as YAML (raw, no reformat), else None if pygments is absent."""
    body = (text or "").rstrip("\n")
    if not body.strip():
        return None
    try:
        out = [_pick(tt) + val for tt, val in R.lexer("yaml").get_tokens(body)]
        return "".join(out).rstrip("\n") + R.RST
    except Exception:                               # pygments absent / lexer error
        return None


class YamlStreamer(R.BufferedStreamer):
    """Buffer a command's whole output, then at close() emit it colour-highlighted
    (or verbatim — the base's fallback — if pygments is unavailable)."""

    def render(self, raw):
        return render_yaml(raw)
