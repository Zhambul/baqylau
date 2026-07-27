# dashboard/opshtml/ops.py — the paint-op vocabulary -> HTML.
#
# op_html/ops_html/op_items/view_html render core/ops.py's op shapes to the
# structured blocks the page paints, with the ⧉ copy / click-to-view OSC-8 links
# turned into <a data-cc> hooks. Builds on ansi.py for escaping/colour.
import html

from core import codefmt as CF
from core import render as R
from dashboard.opshtml import actclass
from dashboard.opshtml.ansi import CODE_W, ansi_html, _esc, _rgb
from plugins.claude_code import transcript as TR




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


def _wrap_outer(body, outer):
    """Wrap `body` in the shared `og` outer div carrying the border colour, or
    return it unchanged when the op has no `outer` — the one shape both the
    `label` and `gut` branches paint."""
    if outer is None:
        return body
    return ("<div class=\"og\" style=\"border-color:%s\">%s</div>"
            % (_rgb(outer), body))


def _v_attrs(op):
    """The ` data-v`/` data-mem` attribute string a click-to-view op carries —
    shared by the `gut` and `line` branches (html-escaped, data-mem only when
    the op is memory-tagged)."""
    v = op.get("v")
    vattr = " data-v=\"%s\"" % html.escape(str(v), quote=True) if v else ""
    vattr += " data-mem=\"1\"" if op.get("mem") else ""
    return vattr


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
        # A web-facing NOTE (core/ops.py) replaces the chip entirely: one quiet
        # `⏺ …` line in the register of a collapsed run's summary, no stream colour,
        # no model/ctx tags, no ⧉ links. The block's BODY is what the reader clicks
        # for (a subagent's brief, or its result), and a coloured chip announcing
        # `⇠ result  fable-5·high  ctx 22% · 225k/1M` competes with that.
        note = op.get("note") or actclass.legacy_agent_note(op)
        if note:
            # marker + text as separate spans, so the line can sit on the SAME grid
            # as a collapsed run's summary (`.vsum`: a 7px dot, an 8px gap, then the
            # words). One `⏺ …` string in a single div put the glyph and the text at
            # neither column, and the two line kinds read as a ragged pair.
            return ("<div class=\"anote\"><span class=\"anmark\">%s</span>"
                    "<span class=\"atext\">%s</span></div>"
                    % (NOTE_GLYPH, html.escape(note)))
        chip = ("<span class=\"chip\" style=\"background:%s\">%s</span>"
                % (_rgb(op.get("c")), ansi_html(op.get("s", ""))))
        g = op.get("g")
        if g and key:
            chip += _copy_links(key, g, op.get("lk"))
        body = "<div class=\"ol\">%s</div>" % chip
        return _wrap_outer(body, op.get("outer"))
    if t == "code":
        return _code_block(op.get("s", ""), op.get("ind", "  "))
    if t == "gut":
        s = _gutbody(op) if (op.get("lex") or op.get("num") is not None) \
            else op.get("s", "")
        if op.get("web"):
            # A SUBAGENT's brief or result (the only gut ops carrying `web` —
            # core/ops.py). Claude Code injects <system-reminder> blocks into the
            # text it hands an agent, so a brief opened with the roster of every
            # addressable teammate instead of the task ("when I click on launch I see
            # a system reminder and not the actual first prompt"). Producers strip it
            # now (transcript.strip_reminders); this covers the ops already on disk,
            # which no restart can re-stamp. An empty result drops the op entirely —
            # a TEAMMATE's spawn record is nothing BUT reminders (its real
            # instructions arrive as mail), and an empty panel is worse than none.
            s = TR.strip_reminders(s)
            if not s.strip():
                return ""
        cls = "ogut panel" if op.get("bg") is not None else "ogut"
        style = "border-left-color:%s" % _rgb(op.get("c"))
        if op.get("bg") is not None:
            style += ";background:%s" % _rgb(op.get("bg"))
        vattr = _v_attrs(op)
        body = ("<div class=\"%s\" style=\"%s\"%s><pre>%s</pre></div>"
                % (cls, style, vattr, ansi_html(s)))
        return _wrap_outer(body, op.get("outer"))
    if t == "line":
        vattr = _v_attrs(op)
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


# Body-op kinds — the paint ops carrying a block's CONTENT rather than its
# identity (a label/line op is what names a class).
_BODY_OPS = ("gut", "code")

# The bullet a web NOTE line opens with — Claude Code's own marker for the same
# kind of one-line activity notice in its transcript. It stands in the summary
# line's DOT column (see the `.anote` rules), so no trailing space: the gap is CSS.
NOTE_GLYPH = "⏺"


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
    has no `src`, so old sessions render as before — an unstamped agent block
    still reads as one, because the ACTIVITY CLASS below is derived from the op
    itself and so classifies live and parked ops identically.

    Each item also carries that class: `act` (a token from actclass.ACTS, absent
    when the op names no kind — a body op inherits its block's, and a GROUP-LESS
    body op inherits the row it follows, which is the only block it has), `bad`
    (1 when the op reports a failed outcome) and, for a mutation one-liner, its
    `add`/`rem` line counts. The page reads them for the kind filter and the view
    modes; it never re-derives them from the HTML it was handed. That inheritance
    is why the CALLERS batch consecutive ops into one call (read/mirror.py) — a
    per-op call has no row in front of it to inherit from."""
    out = []
    prev_act = None
    for op in ops:
        if not isinstance(op, dict):
            continue
        t = op.get("t")
        if t in ("rule", "blank") or (op.get("src") and not op.get("web")):
            continue
        if actclass.agent_header(op):
            # the main session's own `▶ <type> · <desc>` launch/resume header —
            # dropped here because the substream's ⇢ prompt block says the same
            # thing AND holds the brief behind the click (see agent_header)
            continue
        h = op_html(op, key)
        if not h:
            continue
        it = {"g": op.get("g") or None, "t": t, "html": h}
        # WHOSE agent block this is (`sub:<id>` / `team:<id>` — core/ops.py's src),
        # so the page can join it to the agents payload: the duration for a finish
        # note, and — the reason it matters more — counting DISTINCT AGENTS in a
        # collapsed run instead of agent-ish ROWS ("running 77 agents" for a session
        # with 21 of them: launch + prompt + result + resume, each counted once).
        src = op.get("src") or ""
        if ":" in src:
            it["agent"] = src.split(":", 1)[1]
        if op.get("note") or actclass.legacy_agent_note(op):
            it["note"] = 1          # this header IS the whole line (see op_html)
        # The ACTIVITY CLASS the view modes collapse a run of items on
        # (docs/dashboard.md, *View modes*) — classified here, once, instead of
        # sniffed back out of the rendered HTML by the page.
        act, bad = actclass.classify(op)
        if act:
            it["act"] = act
        if bad:
            it["bad"] = 1
        if act in (actclass.ACT_EDIT, actclass.ACT_WRITE):
            add, rem = actclass.diffstat(op)     # the collapsed edit summary sums these
            if add:
                it["add"] = add
            if rem:
                it["rem"] = rem
        elif not act and not it["g"] and t in _BODY_OPS and prev_act:
            # A GROUP-LESS body op inherits the class of the row it follows.
            # "A body op inherits its block's class" is the classifier's rule, but
            # a body op with no `g` has no block to inherit from and lands as a
            # top-level row of its own — unclassifiable, therefore never
            # collapsible, therefore visible in EVERY view mode. Team mail is
            # exactly that shape (a `● from → to` label followed by the message
            # body as a bare gutter), which is how a teammate's report-delivery
            # summary sat in the middle of focus mode.
            it["act"] = act = prev_act
        prev_act = act or prev_act
        out.append(it)
    return out


def view_html(ops, key=""):
    """A click-to-view stash (the kv `view:<gid>` op list) -> one HTML block
    the app inserts under the clicked line."""
    return ("<div class=\"view-block\">%s</div>"
            % "".join(ops_html(ops, key)))
