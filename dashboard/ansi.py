# dashboard/ansi.py — ANSI/SGR -> HTML + the html.escape() security core.
#
# text_presentation, escape_html, ansi_html (the neutralize() analog: every byte that
# reaches the page is escaped here), the SGR state machine, and the 256/16-colour
# fallbacks. The lowest layer every other presenter builds on.
import html
import re
from dataclasses import dataclass



# Effectively-unwrapped width for codefmt.render: the web page owns wrapping
# (CSS pre-wrap / overflow-x), so the ANSI renderer must not hard-wrap first.
# Not maxsize — codefmt's column arithmetic stays in sane integer range.
CODE_WIDTH = 4000

# 16-color SGR fallbacks (30-37 / 90-97). The repo's own producers emit only
# truecolor (render.COL / ops colour table), but tailed command output can
# carry anything — One Dark-ish values so foreign output blends in. The four
# hues that exist in the semantic table come FROM it (single-owner rule).
_BASIC = [(40, 44, 52), (224, 108, 117), (152, 195, 121), (229, 192, 123),
          (97, 175, 239), (198, 120, 221), (86, 182, 194), (171, 178, 191)]
_BRIGHT = [(92, 99, 112), (224, 108, 117), (152, 195, 121), (229, 192, 123),
           (97, 175, 239), (198, 120, 221), (86, 182, 194), (255, 255, 255)]


def _xterm_color(color_index):
    """xterm 256-color index -> (r, g, b)."""
    if color_index < 8:
        return _BASIC[color_index]
    if color_index < 16:
        return _BRIGHT[color_index - 8]
    if color_index < 232:
        color_index -= 16
        steps = (0, 95, 135, 175, 215, 255)
        return (
            steps[color_index // 36],
            steps[color_index // 6 % 6],
            steps[color_index % 6],
        )
    gray = 8 + (color_index - 232) * 10
    return (gray, gray, gray)


def _apply_style(style_state, parameter_text):
    """Fold one SGR parameter string into the style state (keys: fg/bg
    (r,g,b) tuples, bold/dim/italic/underline flags)."""
    try:
        parameters = [
            int(parameter)
            for parameter in re.split(r"[;:]", parameter_text)
            if parameter
        ] or [0]
    except ValueError:
        return
    parameter_index = 0
    while parameter_index < len(parameters):
        parameter = parameters[parameter_index]
        if parameter == 0:
            style_state.clear()
        elif parameter == 1:
            style_state["bold"] = True
        elif parameter == 2:
            style_state["dim"] = True
        elif parameter == 3:
            style_state["italic"] = True
        elif parameter == 4:
            style_state["underline"] = True
        elif parameter in (21, 22):
            style_state.pop("bold", None)
            style_state.pop("dim", None)
        elif parameter == 23:
            style_state.pop("italic", None)
        elif parameter == 24:
            style_state.pop("underline", None)
        elif parameter == 39:
            style_state.pop("fg", None)
        elif parameter == 49:
            style_state.pop("bg", None)
        elif parameter in (38, 48):
            key = "fg" if parameter == 38 else "bg"
            if parameter_index + 4 < len(parameters) and parameters[parameter_index + 1] == 2:
                style_state[key] = tuple(parameters[parameter_index + 2:parameter_index + 5])
                parameter_index += 4
            elif parameter_index + 2 < len(parameters) and parameters[parameter_index + 1] == 5:
                style_state[key] = _xterm_color(parameters[parameter_index + 2] % 256)
                parameter_index += 2
            else:
                break                      # malformed extended colour — stop
        elif 30 <= parameter <= 37:
            style_state["fg"] = _BASIC[parameter - 30]
        elif 90 <= parameter <= 97:
            style_state["fg"] = _BRIGHT[parameter - 90]
        elif 40 <= parameter <= 47:
            style_state["bg"] = _BASIC[parameter - 40]
        elif 100 <= parameter <= 107:
            style_state["bg"] = _BRIGHT[parameter - 100]
        parameter_index += 1


def _style_css(style_state):
    """Inline CSS for a style dict; '' when default."""
    parts = []
    if "fg" in style_state:
        parts.append("color:rgb(%d,%d,%d)" % style_state["fg"])
    if "bg" in style_state:
        parts.append("background:rgb(%d,%d,%d)" % style_state["bg"])
    if style_state.get("bold"):
        parts.append("font-weight:600")
    if style_state.get("dim"):
        parts.append("opacity:.55")
    if style_state.get("italic"):
        parts.append("font-style:italic")
    if style_state.get("underline"):
        parts.append("text-decoration:underline")
    return ";".join(parts)


# SGR + OSC 8 — the exact two survivors of render.neutralize(); anything else
# was already stripped before this pattern runs.
# NO EMOJI (docs/dashboard.md, *No emoji*) — the text-presentation pass.
# Several symbols the terminal producers paint are EMOJI-CAPABLE codepoints:
# their DEFAULT presentation is text (that is how they render in kitty), but a
# browser whose page fonts lack the glyph falls back to the system COLOUR-emoji
# font, so the same `⚠ audit:` line the terminal shows in amber monochrome
# sprouted a colour emoji on the page. U+FE0E (VARIATION SELECTOR-15) is the
# standard "render this as text" request and pins them monochrome.
#
# It lives HERE, in the presenter, and not at the producers: these glyphs are
# single-owned audited vocabulary (`⚠ audit: <script>: <exception>` is asserted
# verbatim by the tests and quoted by docs/audit.md), and the terminal has no
# problem to fix. Same reason the module html-escapes here rather than upstream.
# The twin of this set lives in app.js (`tp()`) for the glyphs the PAGE writes.
_VS15 = "\ufe0e"
_EMOJI_CAPABLE = re.compile(
    "([\u203c\u2049\u2194\u21a9\u21aa\u2328\u23f1\u23f2\u25aa\u25ab"
    "\u25b6\u25c0\u2600\u2601\u260e\u2611\u2618\u2699\u26a0\u26d3"
    "\u2702\u2709\u2714\u2716\u2733\u2734\u2744\u2747\u27a1])"
    "(?![\ufe0e\ufe0f])")


def text_presentation(text):
    """Pin every emoji-capable symbol in `text` to its TEXT glyph (see above).
    Idempotent — a codepoint that already carries a variation selector is left
    alone, so re-rendering never stacks selectors."""
    return _EMOJI_CAPABLE.sub("\\1" + _VS15, text)


def escape_html(text, quote=False):
    """html.escape + the text-presentation pass — the escape leaf every path
    that puts TEXT on the page goes through."""
    return html.escape(text_presentation(text), quote=quote)


_ANSI_TOKEN = re.compile(r"\x1b\[[0-9;:]*m|\x1b\]8;;[^\x1b\x07]*(?:\x07|\x1b\\)")
_CONTROL = re.compile(
    r"\x1b\[[0-9;:?]*[ -/]*[@-~]"
    r"|\x1b\][^\x1b\x07]*(?:\x07|\x1b\\)"
    r"|\x1b[PX^_][^\x1b]*(?:\x1b\\|\x07)?"
    r"|\x1b[@-Z\\-_]"
)
_UNSAFE_ESCAPE = re.compile(r"\x1b(?!\[[0-9;:]*m|\]8;|\\)")


def _neutralize(value):
    def keep(match):
        sequence = match.group(0)
        if sequence.startswith("\x1b[") and sequence.endswith("m"):
            return sequence
        if sequence.startswith("\x1b]8;"):
            return sequence
        return ""

    return _UNSAFE_ESCAPE.sub("", _CONTROL.sub(keep, value))


@dataclass(frozen=True)
class AnsiActionLink:
    scheme: str
    css_class: str
    data_attribute: str


def ansi_html(value, action_link: AnsiActionLink | None = None):
    """ANSI-styled text -> HTML: every character html-escaped, SGR runs as
    <span style=…>, and safe OSC 8 links become anchors. Input is neutralized
    first, so unknown escapes never reach the escape step as invisible control
    bytes."""
    value = _neutralize(value or "")
    output, style_state, active_link = [], {}, None
    position = 0

    def flush(text):
        if not text:
            return
        css = _style_css(style_state)
        body = escape_html(text)
        output.append("<span style=\"%s\">%s</span>" % (css, body) if css else body)

    for token in _ANSI_TOKEN.finditer(value):
        flush(value[position:token.start()])
        position = token.end()
        sequence = token.group(0)
        if sequence.endswith("m"):                       # SGR
            _apply_style(style_state, sequence[2:-1])
            continue
        url = sequence[5:-2] if sequence.endswith("\x1b\\") else sequence[5:-1]
        if active_link is not None:
            output.append("</a>")
            active_link = None
        if url:
            action_prefix = f"{action_link.scheme}:" if action_link is not None else None
            if action_prefix is not None and url.startswith(action_prefix):
                action_value = url[len(action_prefix):].strip("/")
                output.append(
                    "<a class=\"%s\" %s=\"%s\">"
                    % (
                        html.escape(action_link.css_class, quote=True),
                        html.escape(action_link.data_attribute, quote=True),
                        html.escape(action_value, quote=True),
                    )
                )
                active_link = url
            elif url.startswith(("http://", "https://")):
                # http(s) ONLY — the same scheme gate _md_inline applies. Op
                # text is RAW command output and OSC 8 is one of the two
                # survivors of neutralize(), so an attacker-printed
                # `\x1b]8;;javascript:…` (or data:) would otherwise become a
                # clickable href in the dashboard origin (XSS-on-click the
                # terminal mirror can't have — a terminal has no href). Any
                # other scheme opens NO anchor; the link's visible label still
                # renders as plain escaped text via flush().
                output.append("<a href=\"%s\" target=\"_blank\" rel=\"noopener\">"
                              % html.escape(url, quote=True))
                active_link = url
    flush(value[position:])
    if active_link is not None:
        output.append("</a>")
    return "".join(output)


def rgb_css(color, fallback=(120, 132, 158)):
    try:
        red, green, blue = color
        return "rgb(%d,%d,%d)" % (int(red), int(green), int(blue))
    except Exception:
        return "rgb(%d,%d,%d)" % fallback
