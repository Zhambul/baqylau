# dashboard/notehtml.py — a memory-wiki NOTE rendered to safe HTML for the
# dashboard's Memory-tab note viewer (docs/dashboard.md, *Memory tab*).
#
# A note is markdown with YAML frontmatter and bare [[wikilinks]]. Body markdown
# reuses opshtml.md_html (the same escape-FIRST, dependency-free subset the message
# bubbles use — no markdown library, per the no-deps rule). The ONE thing md_html
# doesn't do is [[wikilink]] linkification, so this module protects those spans
# BEFORE md_html (a control-byte sentinel md_html passes through untouched — same
# trick md_html's own _md_inline uses for code spans) and restores them as anchors
# AFTER, so a stem's underscores can't be eaten by emphasis and nothing raw ever
# reaches the page. The anchors carry the stem in data-note (no href); the client
# fetches the linked note on click. An optional `resolve(stem)->path|None` marks a
# dangling link (the wiki keeps those on purpose) with a `dead` class.
import html
import re

from dashboard import opshtml

# [[stem]] / [[stem#section]] / [[stem|alias]] — group 1 stem, group 2 alias.
_LINK_RE = re.compile(r"\[\[\s*([^\]|#]+?)\s*(?:#[^\]|]*)?(?:\|\s*([^\]]+?)\s*)?\]\]")
_SENT_RE = re.compile("\x02(\\d+)\x02")


def note_html(body, resolve=None):
    """Render a note body (markdown + [[wikilinks]]) to safe HTML. `resolve` (if
    given) maps a stem to a path or None; an unresolvable stem's anchor gets the
    `dead` class. Never raises — md_html has its own outer guard, and a bad link
    match just renders as its escaped literal."""
    stash = []

    def grab(m):
        stash.append((m.group(1).strip(), (m.group(2) or "").strip()))
        return "\x02%d\x02" % (len(stash) - 1)

    protected = _LINK_RE.sub(grab, body or "")
    rendered = opshtml.md_html(protected)

    def put(m):
        stem, alias = stash[int(m.group(1))]
        label = alias or stem
        dead = " dead" if (resolve is not None and not resolve(stem)) else ""
        return ("<a class=\"wl%s\" data-note=\"%s\">%s</a>"
                % (dead, html.escape(stem, quote=True),
                   html.escape(label, quote=False)))

    return _SENT_RE.sub(put, rendered)


def frontmatter_rows(fm):
    """Frontmatter dict -> [(key, value)] escaped for a compact header table.
    Kept separate from the body render so the client can lay it out (a note
    without frontmatter yields [])."""
    return [(html.escape(str(k), quote=False), html.escape(str(v), quote=False))
            for k, v in (fm or {}).items()]
