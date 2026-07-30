# plugins/claude_code/suggestion.py — read Claude Code's greyish "suggested
# answer" ghost
# text out of a live session's input box (docs/dashboard.md, *Web ghost
# suggestion*). Sibling of askdialog.py/plandialog.py and the same philosophy:
# the suggestion exists ONLY as live TUI pixels — Claude Code fires no hook for
# its own input-box suggestion — so the one way to surface it in the web
# composer is to READ THE SCREEN.
#
# Empirical capture facts this encodes (measured live, window 291):
#   - the input box is the region between the last two grey divider rules
#     (`\x1b[38:2:136:136:136m─…`) near the bottom of the screen;
#   - its first line is the prompt `❯` + a NBSP (U+00A0), then the content;
#   - a GHOST suggestion is rendered with the faint SGR attribute
#     (`\x1b[22;2m`, param 2 = dim); REAL typed/queued input on that line is
#     normal weight — so "all the input content is faint" is the tell that
#     distinguishes a suggestion from text the user actually put there.
#
# `parse()` is a pure function over the ANSI screen string (unit-tested);
# `probe()` wraps it with the get-text call + audit-before-swallow.
import re

from core.noaudit import load_audit
from core.render import ANSI_RE, strip_ansi

A = load_audit()

PROMPT = "❯"            # the input-box prompt marker (also the dialog cursor)
NBSP = "\xa0"           # the prompt-to-content separator the TUI uses
_WS = " \t" + NBSP     # whitespace to strip around the input content
_RULE = "─"            # box-drawing horizontal — the divider rules bounding the box
_RULE_MIN = 10          # a divider rule is a line with at least this many `─`
_SGR = re.compile(r"\x1b\[([0-9;:]*)m")   # an SGR escape (colours/intensity)


def _apply_sgr(faint, params):
    """Fold one SGR escape's params into the running faint state. `""` (the
    bare `\\x1b[m`) and `0` reset; `2` sets faint; `22` clears it. Colour codes
    like `38:2:r:g:b` (a colon-joined field) leave intensity untouched."""
    if params == "":
        return False
    for field in params.split(";"):
        code = field.split(":", 1)[0]
        if code in ("", "0", "22"):
            faint = False
        elif code == "2":
            faint = True
    return faint


def _faint_chars(s):
    """Walk `s` yielding (char, faint) for each VISIBLE char, tracking the SGR
    intensity state and skipping every escape sequence (SGR updates the state;
    OSC 8 hyperlinks / other CSI are stepped over)."""
    out = []
    i, n, faint = 0, len(s), False
    while i < n:
        if s[i] == "\x1b":
            m = ANSI_RE.match(s, i)
            if m:
                sgr = _SGR.fullmatch(m.group(0))
                if sgr:
                    faint = _apply_sgr(faint, sgr.group(1))
                i = m.end()
                continue
            i += 1
            continue
        out.append((s[i], faint))
        i += 1
    return out


def _is_rule(line):
    return strip_ansi(line).count(_RULE) >= _RULE_MIN


def _region(lines):
    """The input-box lines: between the last two divider rules. Falls back to
    the last `❯`-prompt line (through the following rule, if any) when the box
    isn't cleanly framed. [] when no input box is on screen."""
    rules = [i for i, ln in enumerate(lines) if _is_rule(ln)]
    if len(rules) >= 2:
        return lines[rules[-2] + 1:rules[-1]]
    # no clean frame — anchor on the last prompt line and stop at the next rule
    start = None
    for i, ln in enumerate(lines):
        if strip_ansi(ln).lstrip().startswith(PROMPT):
            start = i
    if start is None:
        return []
    end = start + 1
    while end < len(lines) and not _is_rule(lines[end]):
        end += 1
    return lines[start:end]


def _box_content(screen):
    """The input box's post-prompt `[(char, faint)]` list — the shared
    intermediate of `parse()` and `typed()`: the box region's visible chars with
    their SGR faint state, dropped through the first prompt marker (the box's
    `❯`). [] when there is no input box on screen. The two callers differ only
    in the faint predicate they filter this list with (and parse()'s all-faint
    validity check)."""
    if not screen:
        return []
    region = _region(screen.splitlines())
    if not region:
        return []
    chars = _faint_chars("\n".join(region))
    # drop through the first prompt marker (the box's `❯`)
    text = "".join(c for c, _ in chars)
    if PROMPT in text:
        chars = chars[text.index(PROMPT) + 1:]
    return chars


def norm(s):
    """Input-box text, whitespace-normalized — every run of whitespace (the
    box's own padding, and the line breaks a wrapped entry is captured with)
    collapsed to one space. The ONE normalization for box content: `parse`/
    `typed` return it, and post_interrupt's restore check compares a transcript
    prompt against a box read through it (docs/dashboard.md, *Interrupt*)."""
    return re.sub(r"\s+", " ", s).strip()


def cmp_key(s):
    """A box-text COMPARISON key: every whitespace character removed. Stronger
    than `norm` on purpose — a wrapped box is captured as separate lines that
    join with NO separator, so a box read and the same text from the transcript
    agree on their words but not on the spaces between them. Dropping
    whitespace altogether is what makes post_interrupt's restore check survive
    a wrap (docs/dashboard.md, *Interrupt*); nothing else may compare box text
    by hand."""
    return re.sub(r"\s+", "", s)


def parse(screen):
    """The greyish suggested-answer text from an ANSI screen capture, or None.
    None means: no input box, an empty box, or REAL input (any non-whitespace
    content that is NOT faint — the user's own typed/queued line, never a
    ghost). A non-None result is the faint suggestion, whitespace-normalized."""
    chars = _box_content(screen)
    # is every non-whitespace visible char faint? and is there any at all?
    body = [(c, f) for c, f in chars if c not in _WS and c != "\n"]
    if not body:
        return None
    if not all(f for _, f in body):
        return None                       # real input, not a ghost
    raw = "".join(c for c, _ in chars).replace(NBSP, " ")
    return norm(raw) or None


def typed(screen):
    """The REAL (non-faint) text the user has put in the input box — their own
    typed or queued line — or None when the box is empty, absent, or holds only
    a faint GHOST suggestion. The complement of `parse()`: text this returns is
    exactly the content `parse()` rejects as "real input, not a ghost". Used to
    detect the user composing a reply AT THE TERMINAL — the one trace that
    typing into the `❯` box leaves before submitting (it moves neither the tab
    nor the transcript), so the deferred Telegram alert can tell "still at the
    keyboard" from "walked away" (docs/dashboard.md, *Telegram alerts*) — AND,
    since an early interrupt makes Claude Code hand the prompt BACK to this
    box, to tell a restore from a plain stop (post_interrupt's
    `_restored_input`, docs/dashboard.md *Interrupt*)."""
    chars = _box_content(screen)
    real = "".join(c for c, f in chars if not f and c != "\n").replace(NBSP, " ")
    return norm(real) or None


def probe(fe, win, sid=""):
    """The audited screen probe: capture the ANSI viewport and parse the ghost
    suggestion. None on any failure (audited) or when there is no suggestion."""
    return probe_box(fe, win, sid)[0]


def probe_box(fe, win, sid=""):
    """ONE capture, BOTH readings: (ghost, typed) — the faint suggestion and the
    user's own real text. Exactly one of them can be non-None (they partition
    the box's content by intensity), and the two callers want opposite halves:
    the ghost feeds the composer's placeholder, the typed text feeds the
    terminal→web draft sync (docs/dashboard.md, *Terminal draft sync*). They run
    on the same SSE tick, so sharing the capture keeps that one `kitten @
    get-text` per tick instead of two.

    The typed half distinguishes "" (a box we READ and it is empty) from None
    (we could not read one — dead window, kitten failure, no box on screen).
    The sync treats an empty box as a signal and an unreadable one as no news,
    so collapsing the two would clear a draft every time a session's window
    goes away. (None, None) on any failure (audited)."""
    try:
        screen = fe.get_text(win, ansi=True)
    except Exception:
        A.error(sid, "dashboard suggestion probe", {"win": win})
        return None, None
    if not screen:
        return None, None
    return parse(screen), (typed(screen) or "")
