# dashboard/opshtml/tools.py — Claude's built-in tool payloads -> HTML.
#
# tool_html / tool_output_html / answer_html / msg_html and the per-tool
# renderers (bash/edit/write/read/deflist). Builds on ansi (escape/ANSI) and
# markdown (md_html).
import html
import json

from dashboard.opshtml.ansi import ansi_html, _esc
from dashboard.opshtml.markdown import md_html
from dashboard.opshtml.ops import _code_block




def answer_html(pairs):
    """The STRUCTURED AskUserQuestion answer bubble body: one section per
    answered question (its optional header chip + question text) with the CHOSEN
    value(s) HIGHLIGHTED — mirrors the question bubble's per-question layout, each
    picked value its OWN chip in the --done hue (a multiSelect answer reads as
    separate values, not one lumped string) instead of the flat one-line recap
    (docs/dashboard.md, *Web ask*). `pairs` is transcript._answer_pairs output
    ([{q, header, values:[…]}]). Returns None when there's nothing structured to show
    (→ msg_html falls back to the flat recap markdown). Escape-first, like every
    leaf here — the neutralize() analog."""
    rows = []
    for p in pairs or []:
        if not isinstance(p, dict):
            continue
        q = (p.get("q") or "").strip()
        values = [v.strip() for v in (p.get("values") or [])
                  if isinstance(v, str) and v.strip()]
        if not q and not values:
            continue
        head = ""
        hdr = (p.get("header") or "").strip()
        if hdr:
            head += "<span class=\"anshdr\">%s</span>" % _esc(hdr)
        if q:
            head += "<span class=\"ansqt\">%s</span>" % html.escape(q, quote=False)
        chips = "".join("<span class=\"ansv\">%s</span>"
                        % html.escape(v, quote=False) for v in values) \
            or "<span class=\"ansv none\">—</span>"
        rows.append("<div class=\"ansq\"><div class=\"ansqh\">%s</div>"
                    "<div class=\"ansvs\">%s</div></div>" % (head, chips))
    return "<div class=\"ansqa\">%s</div>" % "".join(rows) if rows else None


def msg_html(kind, text, sender="", qa=None):
    """A main-thread CONVERSATION block for the merged web stream — not an op
    (the terminal mirror deliberately omits main-agent messages: the main
    pane already shows them; the web has no main pane, so the dashboard
    interleaves them — docs/dashboard.md). kind: prompt | message | teammsg |
    question (the AskUserQuestion Claude asked — its text + offered options) |
    answer (the answer the user submitted — the "my answer didn't appear" fix;
    both are `you`/`claude` bubbles WITHOUT the rewind affordance, since neither
    is a re-runnable prompt) | recap (Claude Code's away-summary — a
    system-generated bubble, not a re-runnable prompt either). The body rides
    md_html (readable markdown), which is escape-first like everything else
    here — the neutralize() analog. `qa` (answer only) is the structured
    [{q, header, answer}] pairs from transcript._answer_pairs: when present it
    renders the highlighted per-question card (answer_html) instead of the flat
    recap text."""
    who = {"prompt": "you", "message": "claude",
           "question": "claude ▸ asks you", "answer": "you ▸ answered",
           "recap": "↩ recap"} \
        .get(kind) or ("✉ " + (sender or "team"))
    extra = ""
    if kind == "prompt":
        # the web rewind picker needs the prompt's RAW text (the rendered
        # markdown is lossy): data-txt is what the page POSTs to /rewind-to
        # and prefills the composer with after a restore; the ↶ button is
        # hover/pick-mode revealed by CSS and handled by feed delegation
        extra = " data-txt=\"%s\"" % html.escape(text or "", quote=True)
        who = ("%s<button class=\"rw\" title=\"rewind to here\">↶</button>"
               % html.escape(who))
    else:
        who = html.escape(who)
    # a submitted answer renders as a STRUCTURED card (per-question sections with
    # the picked answer highlighted) when the transcript gave us the pairs;
    # otherwise it degrades to the flat recap markdown below like any other kind
    if kind == "answer":
        inner = answer_html(qa)
        if inner is not None:
            return ("<div class=\"msg answer\"><span class=\"who\">%s</span>%s</div>"
                    % (who, inner))
    return ("<div class=\"msg %s\"%s><span class=\"who\">%s</span>"
            "<div class=\"md\">%s</div></div>"
            % (html.escape(kind, quote=True), extra, who, md_html(text)))


# --- rich tool rendering (tool_html / tool_output_html) -----------------------
# The drill-down timeline lists every tool CALL; a raw JSON dump of its input is
# unscannable. These presenters render the INPUT of Claude Code's well-known
# built-in tools as structured HTML, reusing the single owners of those payload
# shapes rather than re-encoding them: plugins.claude_code.tools (the built-in
# tool payload owner — diff_rows / read_extent / FILE_RGB), core.codefmt (the
# command highlighter behind `code` ops), core.streamfmt (the file-op one-liner
# vocabulary), and core.coderender (the lexer table + highlighter). Unknown
# tools return None so the server keeps the existing escaped-JSON fallback.
# Escape discipline is unchanged — every leaf rides ansi_html / html.escape.
WRITE_CAP = 200                    # Write content lines shown before an elision
_DEFLIST_TOOLS = ("Grep", "Glob", "WebFetch", "WebSearch", "Task", "SendMessage")
_EDIT_TOOLS = ("Edit", "MultiEdit", "NotebookEdit")


def _first_line(s, n=200):
    """First line of `s`, capped at n chars — for a definition-list value whose
    full text (a Task prompt, a SendMessage body) would be a wall."""
    s = (s or "").strip()
    nl = s.find("\n")
    if nl >= 0:
        s = s[:nl]
    return s[:n] + "…" if len(s) > n else s


def _lexer_for(path):
    """Pygments lexer name for a file path's extension via coderender.LANGS (the
    single owner of the ext->lexer table), or None."""
    try:
        from core.coderender import LANGS
    except Exception:
        return None
    low = (path or "").lower()
    for ext, lexer in LANGS.items():
        if low.endswith(ext):
            return lexer
    return None


def _bash_html(inp):
    cmd = inp.get("command") or ""
    out = _code_block(cmd)
    desc = inp.get("description")
    if desc:
        out += "<div class=\"tdesc\">%s</div>" % html.escape(str(desc), quote=False)
    return out


def _edit_html(tool_name, inp):
    """Edit/MultiEdit/NotebookEdit input as a line-numbered red/green diff via
    tools.diff_rows (the single owner; empty result dict makes it fall back to a
    difflib diff over the input's old/new strings — all we have at input time)."""
    from plugins.claude_code import tools as T
    rows = T.diff_rows(tool_name, inp, {})
    if not rows:
        return None
    out = []
    if inp.get("replace_all"):
        out.append("<div class=\"tflag\">replace_all</div>")
    lines = []
    for sign, no, text in rows:
        if sign == "@":
            lines.append("<div class=\"dl sep\"><span class=\"tx\">⋮</span></div>")
            continue
        cls = {"+": "added", "-": "removed"}.get(sign, "ctx")
        ln = "" if no is None else str(no)
        lines.append("<div class=\"dl %s\"><span class=\"ln\">%s</span>"
                     "<span class=\"tx\">%s</span></div>"
                     % (cls, html.escape(ln), html.escape(text, quote=False)))
    out.append("<div class=\"tdiff\">%s</div>" % "".join(lines))
    return "".join(out)


def _write_html(inp):
    path = inp.get("file_path") or ""
    content = inp.get("content") or ""
    head = ("<div class=\"tfile\">%s</div>" % html.escape(path, quote=False)
            if path else "")
    all_lines = content.split("\n")
    shown = "\n".join(all_lines[:WRITE_CAP])
    lexer = _lexer_for(path)
    body = None
    if lexer:
        try:
            from core import coderender as C
            hi = C.render_code(shown, lexer)
            if hi is not None:
                body = "<pre class=\"oc\">%s</pre>" % ansi_html(hi)
        except Exception:
            body = None
    if body is None:
        body = "<pre class=\"oc\">%s</pre>" % html.escape(shown, quote=False)
    more = len(all_lines) - WRITE_CAP
    if more > 0:
        body += ("<div class=\"telide\">… (%d more line%s)</div>"
                 % (more, "" if more == 1 else "s"))
    return head + body


def _read_html(inp):
    """Read input as streamfmt's `verb(name)[  extent]` one-liner (the single
    owner of that shape), coloured via SGR and run through ansi_html. file_display
    resolves location against the dashboard process cwd (no session cwd here), so
    a file outside it shows its dim abbreviated dir — informative, not a bug."""
    from core import streamfmt as SF
    from plugins.claude_code import tools as T
    path = inp.get("file_path") or ""
    if not path:
        return None
    disp, _ = SF.file_display(path)
    extent = T.read_extent(None, inp)
    line = SF.file_line("Read", disp, T.FILE_RGB["Read"], extent=extent)
    return "<div class=\"tline\">%s</div>" % ansi_html(line)


def _deflist_html(inp):
    rows = []
    for k, v in inp.items():
        vs = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        rows.append("<dt>%s</dt><dd>%s</dd>"
                    % (html.escape(str(k), quote=False),
                       html.escape(_first_line(vs), quote=False)))
    if not rows:
        return None
    return "<dl class=\"tdl\">%s</dl>" % "".join(rows)


def tool_html(tool_name, inp):
    """Rich HTML for a well-known tool's INPUT, or None (unknown tool / bad
    shape) so the caller keeps its escaped-JSON fallback. Escape-first like
    everything here — every leaf rides ansi_html or html.escape."""
    if not isinstance(inp, dict) or not inp:
        return None
    # A malformed input dict must degrade to None (the caller's escaped-JSON
    # fallback), never raise out of the timeline enrichment: the sub-presenters
    # reach into single-owner shape helpers (tools.diff_rows / read_extent,
    # streamfmt.file_line) that can throw on an unexpected shape, and only some
    # of them guard locally. Honour the docstring's contract uniformly here.
    try:
        if tool_name == "Bash":
            return _bash_html(inp)
        if tool_name in _EDIT_TOOLS:
            return _edit_html(tool_name, inp)
        if tool_name == "Write":
            return _write_html(inp)
        if tool_name == "Read":
            return _read_html(inp)
        if tool_name in _DEFLIST_TOOLS:
            return _deflist_html(inp)
    except Exception:
        return None
    return None


def tool_output_html(text, failed=False, tool_name=""):
    """Rich HTML for a tool's OUTPUT, or None when a plain escaped <pre> (the
    caller's default) already suffices. Only Bash differs: its output can carry
    ANSI (transcripts usually strip it, but harmless), so it rides ansi_html so
    preserved SGR colours render. `failed` is accepted for symmetry with the
    caller; the failure styling stays on the caller's label."""
    if tool_name == "Bash" and text:
        return "<pre class=\"oc\">%s</pre>" % ansi_html(text)
    return None
