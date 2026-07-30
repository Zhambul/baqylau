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


def cmd_html(command):
    """A RAW command string -> the same highlighted `<pre class="oc">` block the
    mirror paints it as. '' for an empty command.

    The difference from `_code_block` is the PRETTY-PRINT: a `code` op was
    already reflowed by `core.ops.code()` at op-creation time (width-independent
    work belongs to the producer), so `_code_block` must not redo it. The
    monitors and jobs tabs read their command from a different place — the ops
    scan / the launch hook payload — where it is still the dense one-liner the
    agent typed, so they run `codefmt.format_code` here and get the identical
    result: breaks after top-level `&&`/`||`/`|`, `;` as a line break, embedded
    python reformatted in its own language.

    Which is the answer to "can this be generalised": it already is, and these
    two tabs simply weren't reaching it — a 200-character one-liner rendered as
    one unbroken grey line in the drill-down while the mirror three tabs over
    showed the same command highlighted across six readable lines."""
    if not (command or "").strip():
        return ""
    try:
        return _code_block(CF.format_code(command))
    except Exception:
        return _code_block(command)     # unformatted still beats unhighlighted


def _cq_pieces(op, key, text, role):
    """The two PIECES of a quiet command header (actclass.cmd_note) — its dim text span,
    with the note DOT when this op opens the block, and its ⧉ copy links. Kept apart
    because the page puts them in different SLOTS of the block header: the words beside
    the command, the links at the far right (the links are hover-only, and in the flow
    they reserved a ~90px hole between the dot and the command while invisible).
    Either piece may be '' — a foreground opener's whole word is muted, and an op
    without a copy group has no links."""
    body = ("<span class=\"cqt\">%s</span>" % html.escape(text)) if text else ""
    if role == actclass.CQ_OPEN:
        body = "<span class=\"anmark\">%s</span>%s" % (NOTE_GLYPH, body)
    g = op.get("g")
    links = _copy_links(key, g, op.get("lk")) if (g and key) else ""
    return body, links


def _wrap_outer(body, outer):
    """Wrap `body` in the shared `og` outer div carrying the border colour, or
    return it unchanged when the op has no `outer` — the one shape both the
    `label` and `gut` branches paint."""
    if outer is None:
        return body
    return ("<div class=\"og\" style=\"border-color:%s\">%s</div>"
            % (_rgb(outer), body))


def _v_attrs(op):
    """The ` data-v`/` data-mem` attribute string an op carries — shared by the
    `gut`, `line` and `label` branches (html-escaped). `data-mem` carries the
    memory FLAVOUR the producer stamped ("1" a note read, "search" a vault
    search — core/ops.label), not a bare presence flag: the page words the two
    differently, and the block-kind test only asks whether the attribute is
    there."""
    v = op.get("v")
    vattr = " data-v=\"%s\"" % html.escape(str(v), quote=True) if v else ""
    mem = op.get("mem")
    if mem:
        vattr += " data-mem=\"%s\"" % html.escape(str(mem), quote=True)
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
        # for (a subagent's brief or result, a team message's text), and a coloured
        # chip announcing `⇠ result  fable-5·high  ctx 22% · 225k/1M` — or a green
        # `◉ read · team-lead → rev-ui-util` — competes with that.
        note = op.get("note") or actclass.legacy_note(op)
        if note:
            # marker + text as separate spans, so the line can sit on the SAME grid
            # as a collapsed run's summary (`.vsum`: a 7px dot, an 8px gap, then the
            # words). One `⏺ …` string in a single div put the glyph and the text at
            # neither column, and the two line kinds read as a ragged pair.
            return ("<div class=\"anote\"><span class=\"anmark\">%s</span>"
                    "<span class=\"atext\">%s</span></div>"
                    % (NOTE_GLYPH, html.escape(note)))
        # …and a COMMAND-family chip goes quiet the same way (a foreground command, a
        # background job, a monitor — actclass.cmd_note). One row here, because a lone
        # op has no header to be a slot of; the STREAM path splits it (op_items).
        cq = actclass.cmd_note(op)
        if cq is not None:
            body, links = _cq_pieces(op, key, cq[0], cq[1])
            return _wrap_outer("<div class=\"ol\"%s>%s%s</div>"
                               % (_v_attrs(op), body, links), op.get("outer"))
        chip = ("<span class=\"chip\" style=\"background:%s\">%s</span>"
                % (_rgb(op.get("c")), ansi_html(op.get("s", ""))))
        g = op.get("g")
        if g and key:
            chip += _copy_links(key, g, op.get("lk"))
        body = "<div class=\"ol\"%s>%s</div>" % (_v_attrs(op), chip)
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


def _body_follows(ops, i):
    """Does the op after ops[i] carry its BODY — either inside its block (same `g`) or
    as the bare next row (the group-less shape team-mail history has)? Used to tell a
    mail row that holds a message from one that only reports on it."""
    nxt = ops[i + 1] if i + 1 < len(ops) else None
    if not isinstance(nxt, dict) or nxt.get("t") not in _BODY_OPS:
        return False
    g = ops[i].get("g")
    return nxt.get("g") == g if g else not nxt.get("g")


def _mail_holder(op):
    """Is this a mail chip that can HOLD a message — the `✉` sent row or a `●` arrival,
    but never a `◉ read · …` notice, which reports on a message and has no body of its
    own? The distinction is what keeps the synthetic group below honest: a read notice
    followed by some other producer's bare gutter would otherwise swallow it."""
    got = actclass.mail_pair(op)
    return bool(got) and got[2] != "read"


def _empty_body(ops, i, key):
    """Does the block opened at ops[i] have NOTHING behind its click — its body op
    present in this batch but rendering to nothing? Fails toward SHOWING: a body that
    isn't in the batch (a window cut between a header and its body) is unknown, not
    empty, so the header stays. Its one caller is the bodiless-note drop above."""
    nxt = ops[i + 1] if i + 1 < len(ops) else None
    if not isinstance(nxt, dict) or nxt.get("t") not in _BODY_OPS:
        return False
    if nxt.get("g") != ops[i].get("g"):
        return False
    return not op_html(nxt, key)

# The bullet a web NOTE line opens with — Claude Code's own marker for the same
# kind of one-line activity notice in its transcript. It stands in the summary
# line's DOT column (see the `.anote` rules), so no trailing space: the gap is CSS.
NOTE_GLYPH = "⏺"


def in_scope(op, scope=None):
    """THE producer-source rule: does this op belong in a mirror scoped to
    `scope`? One owner, because the render path and the block-window cut
    (read/mirror.py `_cut_blocks`) must agree exactly or a window's blocks and
    its contents disagree.

    `scope` None is the SESSION view: keep the main agent's own (unstamped) ops
    and drop every `src`-stamped one — agent and secondary-codex detail belongs
    to that agent, not the lead's stream — except the two `web`-stamped
    endpoints described in op_items. A scope — the SET of `src` strings that
    belong to one agent, e.g. {"sub:a1b2", "team:a1b2"} (read/mirror.agent_scope)
    — inverts it: keep only those, which is how the same pipeline renders ONE
    agent's mirror. A resolved set rather than a bare id because the stamps are
    not uniform: a codex run is stamped `codex:<label>` while its agent id is the
    rollout basename."""
    src = op.get("src") or ""
    if scope is None:
        return not src or bool(op.get("web"))
    return src in scope


def op_items(ops, key="", ids=None, carry=None, scope=None, codex_lead=False):
    """A batch of ops -> [{g, t, html}, …] for the SESSION STREAM: the app
    folds same-`g` items into one collapsible block (the label ops become the
    block's summary chips), so a finished command reads as one line instead
    of a wall. `rule`/`blank` ops are dropped here — they are terminal-width
    spacing, and the web's block cards separate themselves. Which ops survive at
    all is `in_scope` above — by default the WEB mirror is main-agent-only, and
    with a `scope` it is exactly one agent's (docs/dashboard.md *Agent scope*).

    What survives of an agent in the SESSION view is the main session's own
    record of it (the subagent_fmt launch header + finish chip, emitted by the
    hook process, unstamped) PLUS the two endpoints of the subagent's own
    contribution — its ⇢ prompt and ⇠ result blocks, which the substream stamps
    `web` to override the drop (core/ops.py's "web" field);
    everything in between belongs to that agent's own scope. Pre-stamp history
    (parked DBs) has no `src`, so old sessions render as before in the session
    view — an unstamped agent block still reads as one, because the ACTIVITY
    CLASS below is derived from the op itself and so classifies live and parked
    ops identically. The same gap makes an OLD session's agent scope empty:
    nothing there is stamped, so nothing can be attributed to an agent.

    Each item also carries that class: `act` (a token from actclass.ACTS, absent
    when the op names no kind — a body op inherits its block's, and a GROUP-LESS
    body op inherits the row it follows, which is the only block it has), `bad`
    (1 when the op reports a failed outcome) and, for a mutation one-liner, its
    `add`/`rem` line counts. The page reads them for the item kind and the view
    modes; it never re-derives them from the HTML it was handed. That inheritance
    is why the CALLERS batch consecutive ops into one call (read/mirror.py) — a
    per-op call has no row in front of it to inherit from.

    `ids` are the ops' state-DB row ids, when the caller has them (the history and
    backlog paths do — `_merge_order` carries them for its window cuts). They serve
    the ONE thing that needs an identity no op carries: pre-`mid` team mail, whose
    reconstructed subject key must be stable across batches and fetches (see below).
    Without them the key falls back to a per-call position, which is unique within
    the batch — enough for the live path, where every op has a real `mid`.

    `carry` is continuation state for ONE render pass, owned by the caller and handed
    to every batch of it (a render is many calls: a conversation record flushes the
    run). Today it holds pre-`mid` mail's open-subject map, so a read notice still
    finds its arrival when a message landed between them — without it that read
    opened a subject of its own and the summary counted a message too many."""
    out = []
    prev_act = None
    prev_mid = None
    head = None                 # index of the group-less header a body op belongs under
    cs = carry if carry is not None else {}     # this render's continuation state
    # pre-`mid` mail: pair -> the token of its open message, across this render's batches
    mail_n = cs.setdefault("mail", {})
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            continue
        # a globally stable token when the caller has row ids, else a per-call one
        oid = ids[i] if (ids and i < len(ids) and ids[i] is not None) \
            else "n%d" % (len(out) + 1)
        t = op.get("t")
        if t in ("rule", "blank") or not in_scope(op, scope):
            continue
        if scope is not None:
            # AGENT SCOPE, and the ONLY place it differs from the session view.
            # Two steps, both about making an agent's ops the same SHAPE the
            # lead's are, so that everything below here is identity-agnostic by
            # construction — no second rendering path, nothing downstream that
            # has to know a scope exists:
            #   1. normalise the op into the lead's vocabulary — the model/ctx
            #      tags, the agent palette, the outer gutter and (for ops written
            #      before `who` was a field) the name baked into the text all say
            #      "which agent", which the scope says once (actclass.as_lead);
            #   2. drop the agent's PROSE blocks (header + body, by copy group) —
            #      its conversation now comes from its own transcript through the
            #      same merge the lead's does (actclass.prose_block).
            # In that order: the drop recognises a block by what it OPENS with,
            # which is exactly what step 1 restores for history.
            #   0. FIRST drop a codex run's terminal-only CHROME — the `codex ▶`
            #      banner, the `⚙ model · effort` line, the `■ codex … ended` footer
            #      (actclass.codex_chrome). Its model + duration belong on the
            #      agent's card, not as inline ops (a Claude subagent scope has no
            #      such lines). BEFORE as_lead, which recolours the codex palette
            #      to the lead's SLATE and would defeat codex_chrome's palette gate.
            if actclass.codex_chrome(op):
                g = op.get("g") or None
                if g:
                    cs.setdefault("drop", set()).add(g)
                continue
            op = actclass.as_lead(op)
            t = op.get("t")             # as_lead may re-shape the op (gut -> line)
            g = op.get("g") or None
            # Drop a re-bubbled PROSE block: the producer-set `bubbled` flag is the
            # ONE unified signal (a Claude subagent's ⇢/✎/⇠/✉ and a codex sidecar's
            # ⇢/✎/⋯ alike — core/ops.py), so its conversation twin (plugins.
            # conversation) isn't doubled. `prose_block` stays as the LEGACY fallback
            # for parked pre-flag ops (no `bubbled` on disk).
            if op.get("bubbled") or actclass.prose_block(op, scope):
                if g:
                    cs.setdefault("drop", set()).add(g)
                continue
            if g and g in cs.get("drop", ()):
                continue                # this block's body, following its header
        elif codex_lead and actclass.is_codex(op):
            # STANDALONE codex HOST, session view: its own (unstamped) codex ops
            # ARE the session's activity, not a foldable sub-run. DROP the prose
            # ops (⇢/✎/⋯/⇠ header + body by group) — plugins.conversation
            # re-bubbles that prose exactly as the lead's is bubbled, so keeping
            # them here would DOUBLE the conversation AND fold it into "ran N
            # codex runs" (the "all I see is Ran 4 codex runs" bug). Command /
            # file / lifecycle ops stay (P2 renders commands inline).
            g = op.get("g") or None
            if actclass.codex_prose(op) or actclass.codex_chrome(op):
                if g:
                    cs.setdefault("drop", set()).add(g)
                continue
            if g and g in cs.get("drop", ()):
                continue
        if actclass.agent_header(op):
            # the main session's own `▶ <type> · <desc>` launch/resume header —
            # dropped here because the substream's ⇢ prompt block says the same
            # thing AND holds the brief behind the click (see agent_header)
            continue
        if actclass.agent_brief(op) and _empty_body(ops, i, key):
            # a ⇢ prompt / ⇠ result chip with NOTHING behind the click — the roster
            # <system-reminder> record a launch opens with, whose body op this layer
            # drops (see op_html's `web` branch). Live sessions no longer emit the
            # block at all (substream_render.render_prompt); this is the same drop for
            # ops ALREADY ON DISK, which no restart can re-stamp.
            continue
        h = op_html(op, key)
        if not h:
            continue
        it = {"g": op.get("g") or None, "t": t, "html": h}
        # A QUIET COMMAND HEADER (actclass.cmd_note) is handed over as the header's
        # PIECES rather than one row: the page owns the block header's layout and puts
        # the words, the closing duration and the ⧉ links in three different slots of it
        # (`quiet` names which — the CQ_* roles). `html` may come back EMPTY here (a
        # foreground opener's word is muted) and the item still matters: it is what
        # declares the block quiet and what carries its links.
        # The SPLIT is for a GROUPED op only: a lone quiet label (the `▷ backgrounded
        # (ctrl+b)` notice, which groups with nothing) has no block header to be a slot
        # of, so it keeps op_html's own one-row shape and only wears the flag.
        cq = actclass.cmd_note(op)
        if cq is not None:
            it["quiet"] = cq[1]
            if it["g"]:
                it["html"], links = _cq_pieces(op, key, cq[0], cq[1])
                # the `og` wrapper survives (a NESTED job's chip — see _wrap_outer): the
                # page reads that class to recognise a pre-`src` subagent's block, and
                # dropping it would silently re-file those blocks as the session's own
                it["html"] = _wrap_outer(it["html"], op.get("outer"))
                if links:
                    it["links"] = links
        # PRE-`mid` MAIL is two TOP-LEVEL rows on disk — a `● from → to` label and the
        # message body as a bare gutter, NEITHER carrying a copy group (the send-time
        # row that groups them did not exist yet). The page folds a block by its `g`,
        # so those two could never become one clickable line: the message sat open
        # under its own header instead of behind it ("the actual message should be
        # expandable from `Message team-lead → rev-ui-util`, following the pattern of
        # other stuff"). So the pair is handed a SYNTHETIC group here, `mail:<row id>`
        # — a shape no producer can mint (a copy group is `b<n>` or a tool_use_id), and
        # it stashes nothing: these ops carry no ⧉ links to resolve. Only the item
        # IMMEDIATELY after the label may claim it (`pending`, consumed on read) — the
        # body lookahead is what makes that safe, since a `◉ read` notice has no body
        # and the next group-less gutter could be anyone's. A cut BETWEEN the two ops
        # therefore leaves them ungrouped, as before: the pair is written in one
        # transaction and read in one id range, so the live path cannot split them, and
        # history cuts on conversation records.
        pending, cs["mailg"] = cs.pop("mailg", None), None
        if not it["g"] and t in _BODY_OPS and pending:
            # …and the group stays open for a RUN of body ops: one message's text is
            # one op today, but a second bare gutter behind the first is the same
            # message's continuation, and leaving it loose puts it back in the feed
            # (above its own block, since the feed reverses) — which is the artefact
            # this grouping exists to remove.
            cs["mailg"] = it["g"] = pending
        elif not it["g"] and _mail_holder(op) and _body_follows(ops, i):
            cs["mailg"] = it["g"] = "mail:%s" % oid
        # WHOSE agent block this is (`sub:<id>` / `team:<id>` — core/ops.py's src),
        # so the page can join it to the agents payload: the duration for a finish
        # note, and — the reason it matters more — counting DISTINCT AGENTS in a
        # collapsed run instead of agent-ish ROWS ("running 77 agents" for a session
        # with 21 of them: launch + prompt + result + resume, each counted once).
        src = op.get("src") or ""
        if ":" in src:
            it["agent"] = src.split(":", 1)[1]
        # …and the same for a MESSAGE (core/ops.py's `mid`): an arrival, its body and
        # its read notice are three rows about one message, and a run summary that
        # counted rows said "passed 4 messages" where two had been sent.
        if op.get("mid"):
            it["mid"] = str(op["mid"])
        else:
            # PRE-`mid` HISTORY has no message id anywhere in the op, so the subject
            # is reconstructed: the `<from> → <to>` PAIR off the chip, plus the token
            # of the ARRIVAL that opened it — `● X → Y` opens a message, and the
            # `◉ read · X → Y` after it belongs to that same one. The pair alone is
            # not enough (a teammate that reports twice had both messages collapse
            # into one — the reviewed session has exactly that shape), and a
            # per-batch counter is not either: `#1` in two batches of one render
            # collides, which is the same merge by another route. The arrival's ROW
            # ID is stable across every batch and fetch. Mail is chronological, so a
            # read always trails its arrival; one whose arrival fell outside this
            # batch opens its own subject rather than merging into whichever arrival
            # happens to come later.
            got = actclass.mail_pair(op)
            if got:
                frm, to, kind = got
                pair = "%s → %s" % (frm, to)
                if kind != "read" or pair not in mail_n:
                    mail_n[pair] = oid
                it["mid"] = "pair:%s#%s" % (pair, mail_n[pair])
        # …and WHICH KIND of mail row this is: the mail system reporting on a message
        # (delivered / read / a lifecycle frame) rather than the message itself. Those
        # are verbose-only on the web — see actclass.mail_plumbing. The body lookahead
        # is what spares HISTORY, where an arrival WITH a body is the only trace a real
        # message left (the send-time row did not exist yet).
        if actclass.mail_plumbing(op, _body_follows(ops, i)):
            it["plumb"] = 1
        if op.get("note") or actclass.legacy_note(op):
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
            # …and it inherits the SUBJECT too, so a legacy mail body is not counted
            # as a message of its own.
            if prev_mid and "mid" not in it:
                it["mid"] = prev_mid
            if head is not None:
                # …and it is placed UNDER its own header. The feed is newest-on-top,
                # so the page shows this list reversed — which put a group-less body
                # ABOVE the row it belongs to, wedged between that row and the next
                # one: a mail body appeared to belong to the `· read` notice above
                # its arrival ("I don't see the change" — the body was there, just
                # attributed to the wrong line). Inserting it at the header's index
                # reverses into "header, then its bodies, in order", which is how a
                # real block's card reads. Nothing else moves: a body op is only
                # ever adjacent to its own header.
                out.insert(head, it)
                prev_act = act
                continue
        head = len(out)             # the row a following group-less body sits under
        prev_act = act or prev_act
        prev_mid = it.get("mid") or prev_mid
        out.append(it)
    return out


def view_html(ops, key=""):
    """A click-to-view stash (the kv `view:<gid>` op list) -> one HTML block
    the app inserts under the clicked line."""
    return ("<div class=\"view-block\">%s</div>"
            % "".join(ops_html(ops, key)))
