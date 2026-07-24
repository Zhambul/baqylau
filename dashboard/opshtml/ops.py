# dashboard/opshtml/ops.py — the paint-op vocabulary -> HTML.
#
# op_html/ops_html/op_items/view_html render core/ops.py's op shapes to the
# structured blocks the page paints, with the ⧉ copy / click-to-view OSC-8 links
# turned into <a data-cc> hooks. Builds on ansi.py for escaping/colour.
import html

from core import codefmt as CF
from core import render as R
from dashboard.opshtml.ansi import CODE_W, ansi_html, _esc, _rgb




# The default ⧉ pair a g-tagged label shows when it carries no "lk" spec —
# same fallback as the terminal renderer's _LINK_TXT (a command block).
_LINK_DEFAULT = (("cmd", "⧉cmd"), ("out", "⧉out"))


def _copy_links(key, g, lk):
    pairs = lk or _LINK_DEFAULT
    out = []
    for what, glyph in pairs:
        out.append("<a class=\"cc\" data-cc=\"%s/%s/%s\">%s</a>"
                   % (html.escape(str(key), quote=True),
                      html.escape(str(g), quote=True),
                      html.escape(str(what), quote=True),
                      _esc(str(glyph))))
    return "<span class=\"cl\">" + " ".join(out) + "</span>"


def _gutbody(op):
    """A gut op's paint text with its lex highlight + line numbers applied —
    the web twin of the mirror's viewbody() (pygments runs server-side here
    for the same reason it runs renderer-side there: the producer hook may
    have run a python without pygments)."""
    s = op.get("s", "")
    lex = op.get("lex")
    if lex:
        try:
            from core import coderender as C
            hi = C.render_code(s, lex)
            if hi is not None:
                s = hi
        except Exception:
            pass                           # unhighlighted is still correct
    num = op.get("num")
    if num is not None:
        s = "\n".join(R.DIM + "%5d " % (num + i) + R.RST + ln
                      for i, ln in enumerate(s.split("\n")))
    return s


def _code_block(text, ind="  "):
    """Source text -> a highlighted `<pre class="oc">` — the shared body of the
    `code` op branch and the Bash tool presenter (single owner of "how a command
    block looks in HTML"). Neutralised, then run through codefmt.render at the
    unwrapped CODE_W (the page owns wrapping) and ansi_html (which escapes)."""
    try:
        painted = CF.render(R.neutralize(text or ""), CODE_W, ind)
    except Exception:
        painted = R.neutralize(text or "")
    return "<pre class=\"oc\">%s</pre>" % ansi_html(painted)


def op_html(op, key=""):
    """One paint op -> one HTML block ('' for unknown/empty). `key` is the
    mirror-log key (paths.sid_from_log) the ⧉ copy links need; ops render
    fine without it (labels then just drop their copy affordances, like a
    very narrow pane does)."""
    t = op.get("t")
    if t == "blank":
        return "<div class=\"ob\"></div>"
    if t == "rule":
        return "<div class=\"orule\"></div>"
    if t == "label":
        chip = ("<span class=\"chip\" style=\"background:%s\">%s</span>"
                % (_rgb(op.get("c")), ansi_html(op.get("s", ""))))
        g = op.get("g")
        if g and key:
            chip += _copy_links(key, g, op.get("lk"))
        body = "<div class=\"ol\">%s</div>" % chip
        outer = op.get("outer")
        if outer is not None:
            return ("<div class=\"og\" style=\"border-color:%s\">%s</div>"
                    % (_rgb(outer), body))
        return body
    if t == "code":
        return _code_block(op.get("s", ""), op.get("ind", "  "))
    if t == "gut":
        s = _gutbody(op) if (op.get("lex") or op.get("num") is not None) \
            else op.get("s", "")
        cls = "ogut panel" if op.get("bg") is not None else "ogut"
        style = "border-left-color:%s" % _rgb(op.get("c"))
        if op.get("bg") is not None:
            style += ";background:%s" % _rgb(op.get("bg"))
        v = op.get("v")
        vattr = " data-v=\"%s\"" % html.escape(str(v), quote=True) if v else ""
        vattr += " data-mem=\"1\"" if op.get("mem") else ""
        body = ("<div class=\"%s\" style=\"%s\"%s><pre>%s</pre></div>"
                % (cls, style, vattr, ansi_html(s)))
        outer = op.get("outer")
        if outer is not None:
            return ("<div class=\"og\" style=\"border-color:%s\">%s</div>"
                    % (_rgb(outer), body))
        return body
    if t == "line":
        v = op.get("v")
        vattr = " data-v=\"%s\"" % html.escape(str(v), quote=True) if v else ""
        vattr += " data-mem=\"1\"" if op.get("mem") else ""
        return "<pre class=\"opl\"%s>%s</pre>" % (vattr,
                                                  ansi_html(op.get("s", "")))
    return ""


def ops_html(ops, key=""):
    """A batch of ops -> [html, …] (empty strings dropped — unknown op types
    paint nothing, same as the terminal renderer). Used for the click-to-view
    stashes, where the terminal block shape is wanted verbatim."""
    out = []
    for op in ops:
        if isinstance(op, dict):
            h = op_html(op, key)
            if h:
                out.append(h)
    return out


def op_items(ops, key=""):
    """A batch of ops -> [{g, t, html}, …] for the SESSION STREAM: the app
    folds same-`g` items into one collapsible block (the label ops become the
    block's summary chips), so a finished command reads as one line instead
    of a wall. `rule`/`blank` ops are dropped here — they are terminal-width
    spacing, and the web's block cards separate themselves. Ops carrying a
    producer-source stamp (`src` — sub:/team:/codex:, core/ops.py owns the
    vocabulary) are dropped too: the WEB mirror is main-agent-only — agent and
    secondary-codex detail lives in the per-agent drill-down, while the
    terminal mirror keeps painting everything. What survives of an agent here
    is the main session's own record of it (the subagent_fmt launch header +
    finish chip, emitted by the hook process, unstamped) PLUS the two endpoints
    of the subagent's own contribution — its ⇢ prompt and ⇠ result blocks, which
    the substream stamps `web` to override the drop (core/ops.py's "web" field);
    everything in between stays drill-down only. Pre-stamp history (parked DBs)
    has no `src`, so old sessions render as before — the client's heuristic
    `agents` filter chip still covers those (a prompt/result chip opens with the
    agent label, not a command glyph, so it classifies as `agents`)."""
    out = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        t = op.get("t")
        if t in ("rule", "blank") or (op.get("src") and not op.get("web")):
            continue
        h = op_html(op, key)
        if h:
            out.append({"g": op.get("g") or None, "t": t, "html": h})
    return out


def view_html(ops, key=""):
    """A click-to-view stash (the kv `view:<gid>` op list) -> one HTML block
    the app inserts under the clicked line."""
    return ("<div class=\"view-block\">%s</div>"
            % "".join(ops_html(ops, key)))
