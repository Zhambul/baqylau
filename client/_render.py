# client/_render.py — the pane's painter: a model and a width in, ANSI out.
#
# This is what the daemon used to do in `terminal/mirror/` and
# `terminal/scoreboard.py`, moved to the process that owns the screen. Moving it
# made it smaller, and the reason is the width: the daemon held ONE block model
# per session and re-wrapped it for every connection's width, so it needed block
# identity, replacement, reflow and an update vocabulary. A pane knows its own
# width, so the paint is a pure function of (model, width) and a resize is
# calling it again. Nothing here is stateful and nothing here is diffed.
#
# The cost of the move, decided and accepted: no syntax highlighting. `pygments`
# is not the standard library, and a client that imported it would stop being a
# file you can copy next to a terminal.
from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from _model import SessionModel, ShellFold

RESET = "\033[0m"
CLEAR = "\033[H\033[2J\033[3J"
# The mirror's standing title. Painted through the same wrap as everything else
# so that it obeys the pane's width: the daemon emitted it as a fixed string,
# which over-ran a narrow pane by twelve columns.
HEADER_TEXT = " ◧ command mirror — waiting for commands… "
HEADER_COLOR = (128, 128, 128)
# The mirror is a scrollback surface: it paints the whole feed and lets the
# terminal keep the history. Past this many rows the oldest are dropped, because
# a repaint that writes a hundred thousand lines is felt.
SCROLLBACK_ROWS = 4800

# The palette, unchanged from the daemon's (`terminal/mirror/presenter.py`) so
# that a person who looks at both panes and the browser sees one product.
TEXT = (170, 185, 210)
MUTED = (133, 143, 166)
DIM = (92, 99, 112)
USER = (97, 175, 239)
SUCCESS = (152, 195, 121)
FAILURE = (224, 108, 117)
WORKING = (209, 154, 102)
MODIFIED = (229, 192, 123)
DARK = (24, 26, 30)
VALUE = (171, 178, 191)
SEPARATOR = " · "
# The backgrounds a diff is read by. Plain colour and no intra-line highlight:
# that needed pygments, which is not the standard library and so is not something
# a file you can copy next to a terminal may import.
REMOVED_BACKGROUND = (55, 31, 36)
ADDED_BACKGROUND = (29, 50, 38)

# What a click on a pane link launches. Both are the terminal's own
# configuration — a scheme it maps to a program — and both carry the session and
# the pane KIND, because two panes of one session publish their own links and a
# click has to land on the one that drew it.
COPY_SCHEME = "baqylau-content://%s/%s/%s"
VIEW_SCHEME = "baqylau-view://%s/%s/%s"

Color = tuple[int, int, int]
# A link builder: an id in, the URI a click on it should carry out. Passed in
# rather than built here because it needs the session and the pane kind, which are
# the PROCESS's identity and not the renderer's business.
Links = Callable[[str], str]


class Span:
    """A run of text and how it is painted. The whole style vocabulary the two
    surfaces use — there is no italic or underline here because nothing the pane
    draws asks for one."""

    __slots__ = ("text", "color", "background", "bold", "dim", "link")

    def __init__(
        self,
        text: str,
        color: Color | None = None,
        background: Color | None = None,
        bold: bool = False,
        dim: bool = False,
        link: str | None = None,
    ) -> None:
        self.text = text
        self.color = color
        self.background = background
        self.bold = bold
        self.dim = dim
        # An OSC 8 target. The terminal turns the run into something clickable and
        # launches the program its own configuration names for the scheme — which
        # is how a click reaches `terminal_content.py` / `terminal_view.py`.
        self.link = link

    def style(self) -> tuple[Color | None, Color | None, bool, bool, str | None]:
        return self.color, self.background, self.bold, self.dim, self.link

    def sized(self, text: str) -> "Span":
        return Span(text, self.color, self.background, self.bold, self.dim, self.link)

    def ansi(self) -> str:
        text = self.text
        if self.link is not None:
            text = "\033]8;;%s\033\\%s\033]8;;\033\\" % (self.link, text)
        codes = []
        if self.color is not None:
            codes.append("38;2;%d;%d;%d" % self.color)
        if self.background is not None:
            codes.append("48;2;%d;%d;%d" % self.background)
        if self.bold:
            codes.append("1")
        if self.dim:
            codes.append("2")
        if not codes:
            return text
        return "\033[%sm%s%s" % (";".join(codes), text, RESET)


def spans_width(spans: Iterable[Span]) -> int:
    return sum(len(span.text) for span in spans)


def _merged(spans: list[Span]) -> list[Span]:
    """Adjacent spans that look the same, joined.

    The word wrap works on one atom per word, so a paragraph would otherwise be
    painted with a full escape sequence around every word — several times the
    bytes of the text itself, on a surface that repaints from scratch.
    """
    merged: list[Span] = []
    for span in spans:
        if merged and merged[-1].style() == span.style():
            merged[-1] = merged[-1].sized(merged[-1].text + span.text)
            continue
        merged.append(span)
    return merged


def _painted(spans: list[Span], width: int, background: Color | None) -> str:
    if background is None:
        return "".join(span.ansi() for span in _merged(spans))
    filled = [
        Span(span.text, span.color, span.background or background, span.bold,
             span.dim, span.link)
        for span in spans
    ]
    remaining = max(0, width - spans_width(filled))
    if remaining:
        filled.append(Span(" " * remaining, background=background))
    return "".join(span.ansi() for span in _merged(filled))


def _take(spans: list[Span], width: int) -> tuple[list[Span], list[Span]]:
    """The first `width` columns, and what is left. A span is cut mid-text when
    it has to be, keeping its style on both halves."""
    taken: list[Span] = []
    remaining = list(spans)
    available = max(0, width)
    while remaining and available:
        span = remaining.pop(0)
        if len(span.text) <= available:
            taken.append(span)
            available -= len(span.text)
            continue
        taken.append(span.sized(span.text[:available]))
        remaining.insert(0, span.sized(span.text[available:]))
        available = 0
    return taken, remaining


_ATOMS = re.compile(r"[ \t]+|[^ \t]+")


def rows(
    spans: Iterable[Span],
    width: int,
    prefix: Iterable[Span] = (),
    continuation: Iterable[Span] | None = None,
    layout: str = "word_wrap",
    background: Color | None = None,
) -> list[str]:
    """One logical line as the screen rows it occupies.

    Three layouts, and each exists for something the pane draws: `word_wrap` for
    prose and command output, `truncate` for a label, a chip and the scoreboard's
    rows (a status row that wraps is not a status row), and `verbatim` for output
    that already has columns and must not be re-flowed.

    A newline inside a span is a new logical line, handled here rather than at
    the nine callers that hold text a harness wrote: everything the mirror draws
    is somebody else's multi-line string, and a wrap that counted "a\nb" as three
    columns would mis-lay every one of them.
    """
    content = list(spans)
    first = list(prefix)
    rest = list(first if continuation is None else continuation)
    if layout != "verbatim" and any("\n" in span.text for span in content):
        logical_rows: list[str] = []
        for index, logical in enumerate(_split_lines(content)):
            logical_rows.extend(rows(
                logical,
                width,
                prefix=first if index == 0 else rest,
                continuation=rest,
                layout=layout,
                background=background,
            ))
        return logical_rows
    if layout == "verbatim":
        return [_painted(first + content, width, background)]
    if layout == "truncate":
        visible, _dropped = _take(first + content, width)
        return [_painted(visible, width, background)]
    atoms: list[Span] = [
        span.sized(atom) for span in content for atom in _ATOMS.findall(span.text)
    ]
    painted: list[str] = []
    current: list[Span] = []
    line_prefix = first
    while atoms or not painted:
        available = max(1, width - spans_width(line_prefix))
        while atoms:
            atom = atoms[0]
            if atom.text.isspace() and not current and painted:
                atoms.pop(0)                       # no leading space on a wrap
                continue
            if current and spans_width(current) + len(atom.text) > available:
                break
            atoms.pop(0)
            room = available - spans_width(current)
            if len(atom.text) <= room:
                current.append(atom)
                continue
            head, tail = _take([atom], room)       # a word longer than the line
            current.extend(head)
            atoms[:0] = tail
            break
        while atoms and current and current[-1].text.isspace():
            current.pop()
        painted.append(_painted(line_prefix + current, width, background))
        line_prefix = rest
        current = []
    return painted


def _split_lines(spans: list[Span]) -> list[list[Span]]:
    logical: list[list[Span]] = [[]]
    for span in spans:
        parts = span.text.split("\n")
        for index, part in enumerate(parts):
            if index:
                logical.append([])
            if part:
                logical[-1].append(span.sized(part))
    return logical


def screen(painted: list[str], header: Iterable[str] = ()) -> str:
    """A whole pane: clear, an optional header, and the rows that fit."""
    return CLEAR + "\n".join([*header, *painted[-SCROLLBACK_ROWS:]]) + RESET


def duration(seconds: float) -> str:
    whole = max(0, int(seconds))
    hours, remainder = divmod(whole, 3600)
    minutes, rest = divmod(remainder, 60)
    if hours:
        return "%dh%02dm" % (hours, minutes)
    if minutes:
        return "%dm%02ds" % (minutes, rest)
    return "%ds" % rest


def count(value: int) -> str:
    if value < 1_000:
        return str(value)
    if value < 1_000_000:
        return "%.0fk" % (value / 1_000)
    return "%.0fM" % (value / 1_000_000)


# -- the scoreboard -----------------------------------------------------------
# Five rows, rebuilt whole. The numbers are per-ACTOR in the read model, and this
# surface is about the SESSION, so they are summed — the same sum the browser's
# list rows take, for the same reason: a session with four actors has four
# scoreboards and no reason to prefer one.

_STATISTIC_FIELDS = (
    "prompt_count",
    "shell_command_count",
    "failed_shell_command_count",
    "file_count",
    "lines_added",
    "lines_removed",
    "actor_message_count",
)


def session_statistics(model: SessionModel) -> dict[str, Any]:
    totals: dict[str, Any] = {name: 0 for name in _STATISTIC_FIELDS}
    totals["active_seconds"] = 0.0
    tools: dict[str, int] = {}
    for actor in model.actors.values():
        statistics = actor.get("statistics") or {}
        for name in _STATISTIC_FIELDS:
            totals[name] += statistics.get(name) or 0
        for row in statistics.get("tool_counts") or ():
            tools[row["tool"]] = tools.get(row["tool"], 0) + row["count"]
    # The clock is the LEAD's, not a sum: two actors working at once are one
    # stretch of a person's time, and adding them would report more time than
    # the session has existed. It CARRIES FORWARD while the interval is open —
    # the daemon measured `active_seconds` when it built the frame, and frames
    # arrive on change, so a working session would otherwise show a clock that
    # sits still for minutes on a surface somebody is watching.
    lead_statistics = model.lead().get("statistics") or {}
    totals["active_seconds"] = (lead_statistics.get("active_seconds") or 0.0) + (
        model.elapsed_since_frame() if lead_statistics.get("active") else 0.0
    )
    totals["tool_counts"] = tools
    return totals


def session_usage(model: SessionModel) -> tuple[dict[str, int], float | None]:
    tokens: dict[str, int] = {}
    cost: float | None = None
    for actor in model.actors.values():
        usage = actor.get("usage") or {}
        for name, value in (usage.get("tokens") or {}).items():
            tokens[name] = tokens.get(name, 0) + (value or 0)
        if usage.get("cost_in_usd") is not None:
            cost = (cost or 0.0) + float(usage["cost_in_usd"])
    return tokens, cost


def _row(marker: str, parts: list[tuple[str, Color]], width: int) -> list[str]:
    """One scoreboard row: a marker, then as many ` · `-joined parts as fit.

    Parts are dropped from the RIGHT when they do not fit, never truncated: the
    row is ordered most important first, and half a number is worse than none.
    """
    prefix = " %s " % marker
    available = max(0, width - len(prefix))
    visible = list(parts)
    while visible and sum(
        len(text) for text, _color in visible
    ) + len(SEPARATOR) * (len(visible) - 1) > available:
        visible.pop()
    content: list[Span] = []
    for index, (text, color) in enumerate(visible):
        if index:
            content.append(Span(SEPARATOR, dim=True))
        content.append(Span(text, color))
    return rows(
        content,
        width,
        prefix=(Span(prefix, dim=True),),
        layout="truncate",
    )


def scoreboard(model: SessionModel, width: int) -> str:
    """The five status rows: who, how much said, what was done, what it cost."""
    statistics = session_statistics(model)
    tokens, cost = session_usage(model)
    cache_write = (tokens.get("cache_write_tokens") or 0) + (
        tokens.get("one_hour_cache_write_tokens") or 0
    )
    total = (
        (tokens.get("input_tokens") or 0)
        + (tokens.get("output_tokens") or 0)
        + (tokens.get("cache_read_tokens") or 0)
        + cache_write
    )
    identity = [(model.session.get("session_id") or "", VALUE)]
    account = model.session.get("account")
    if account:
        identity.append(("◈ " + (account.get("display_name") or ""), MUTED))
    messages = [("%d msgs" % statistics["actor_message_count"], MUTED)]
    activity = [("%d cmds" % statistics["shell_command_count"], MUTED)]
    if statistics["failed_shell_command_count"]:
        activity.append(("%d✗" % statistics["failed_shell_command_count"], FAILURE))
    activity.append(("⏱ " + duration(statistics["active_seconds"]), MUTED))
    usage_parts = [("%s total" % count(total), VALUE)]
    for value, label in (
        (tokens.get("input_tokens") or 0, "in"),
        (tokens.get("output_tokens") or 0, "out"),
        (tokens.get("cache_read_tokens") or 0, "cache"),
        (cache_write, "write"),
    ):
        if value:
            usage_parts.append(("%s %s" % (count(value), label), MUTED))
    if cost is not None:
        usage_parts.append(("≈ $%.2f" % cost, WORKING))
    detail: list[tuple[str, Color]] = []
    if statistics["file_count"]:
        noun = "file" if statistics["file_count"] == 1 else "files"
        detail.append(("%d %s" % (statistics["file_count"], noun), MUTED))
    if statistics["lines_added"]:
        detail.append(("+%d" % statistics["lines_added"], SUCCESS))
    if statistics["lines_removed"]:
        detail.append(("-%d" % statistics["lines_removed"], FAILURE))
    detail.extend(
        ("%s %d" % (tool, tool_count), MUTED)
        for tool, tool_count in sorted(
            statistics["tool_counts"].items(), key=lambda item: (-item[1], item[0].lower())
        )
    )
    painted = [
        *_row("⬡", identity, width),
        *_row("✉", messages, width),
        *_row("▪", activity, width),
        *_row("Σ", usage_parts, width),
        *_row(" ", detail, width),
    ]
    return screen(painted)


# --- the task list ------------------------------------------------------------
# A STANDING panel, not feed rows, and that is not a style choice: the task list
# is aggregate state now. It has no place in an append-only feed, because it does
# not happen — it IS, and it changes. The mirror used to draw a line per change
# ("✚ task · subject") from a `task.changed` event, which meant a list of five
# items that moved twice read as ten lines of history.

TASK_MARKERS = {
    "completed": ("✓", SUCCESS),
    "in_progress": ("▸", WORKING),
    "pending": ("·", MUTED),
}
# How many rows the panel may take. A model that writes a forty-item plan must
# not push the work off the screen; what a reader wants from a pane is what is
# happening now and what is next.
TASK_ROWS = 6


def task_rows(model: SessionModel, width: int) -> list[str]:
    """What is being worked on, in as few rows as it takes.

    Deleted tasks are dropped and completed ones are counted rather than listed:
    a finished item is not work, and the count is the only thing about it a reader
    still needs.
    """
    tasks = [
        task for task in (model.session.get("tasks") or ())
        if task.get("state") != "deleted"
    ]
    if not tasks:
        return []
    done = sum(1 for task in tasks if task.get("state") == "completed")
    live = [task for task in tasks if task.get("state") != "completed"]
    heading = "tasks %d/%d" % (done, len(tasks))
    painted = rows(
        [Span(" %s " % heading, DARK, MUTED, bold=True)], width, layout="truncate"
    )
    for task in live[:TASK_ROWS]:
        marker, color = TASK_MARKERS.get(task.get("state") or "", ("·", MUTED))
        painted.extend(rows(
            [Span(task.get("subject") or "")],
            width,
            prefix=(Span(" %s " % marker, color),),
            continuation=(Span("   "),),
            layout="truncate",
        ))
    remaining = len(live) - TASK_ROWS
    if remaining > 0:
        painted.extend(rows(
            [Span("… %d more" % remaining, DIM)], width, layout="truncate"
        ))
    return painted


def _shell_marker(fold: ShellFold) -> tuple[str, Color, str]:
    if fold.execution == "monitor":
        return "▷", WORKING, "monitor"
    if fold.execution == "background" or fold.backgrounded:
        return "▷", WORKING, "background"
    return "▶", TEXT, "foreground"


# -- the mirror ---------------------------------------------------------------
# The feed, drawn oldest at the top, the way the daemon's presenter drew it. Two
# shapes carry everything: a LINE (a label, then what was said or done) and a
# BLOCK (a chip, what was asked for, what came back, and how it ended). Which
# one an entry gets is the whole decision table below.


def _label(text: str, color: Color) -> list[Span]:
    return [Span(" %s " % text, DARK, color, bold=True)]


def _rule(width: int) -> str:
    return Span("─" * width, DIM).ansi()


def _said(label: str, text: str, width: int, color: Color = TEXT) -> list[str]:
    return rows(
        [Span(label + "  ", color, bold=True), Span(text)],
        width,
        continuation=(Span("  "),),
    )


def _block(
    chip: str,
    chip_color: Color,
    summary: str,
    output: str,
    status: str,
    finish: list[Span] | None,
    width: int,
    links: list[Span] | None = None,
) -> list[str]:
    """The command shape: a chip and what was asked for, then what came back
    behind a rail, then how it ended. Every tool that produces output uses it —
    a search and a shell differ in their chip and in nothing else."""
    heading = [Span(" %s " % chip, DARK, chip_color, bold=True)]
    for index, link in enumerate(links or ()):
        heading.extend((Span("  " if index == 0 else " "), link))
    painted = ["", _rule(width), *rows(heading, width, layout="truncate")]
    if summary:
        painted.extend(rows([Span(summary)], width, continuation=(Span("  "),)))
    painted.append(_rule(width))
    rail = (Span("│ ", chip_color),)
    for stream in (status, output):
        for line in stream.splitlines():
            painted.extend(
                rows([Span(line.rstrip())], width, prefix=rail, continuation=rail)
            )
    if finish is not None:
        painted.append(_rule(width))
        painted.extend(rows(finish, width, layout="truncate"))
        painted.append(_rule(width))
    return painted


FILE_VERBS = {
    "read": ("Read", USER),
    "created": ("Write", SUCCESS),
    "updated": ("Update", MODIFIED),
    "deleted": ("Delete", FAILURE),
    "renamed": ("Move", MODIFIED),
}
PLAN_DECISIONS = {
    "approved": "APPROVED",
    "changes_requested": "CHANGES REQUESTED",
    "rejected": "REJECTED",
}


def _shell_rows(
    fold: ShellFold, running: bool, copy: Links, width: int
) -> list[str]:
    marker, color, heading = _shell_marker(fold)
    finish: list[Span] | None = None
    if not running and fold.state is not None:
        elapsed = (
            duration(max(0.0, fold.finished_at - fold.started_at))
            if fold.finished_at is not None
            else ""
        )
        if fold.state == "succeeded":
            text = "■ finished"
            finish_color = SUCCESS if heading != "foreground" else TEXT
        else:
            text = "■ " + fold.state
            if fold.exit_code:
                text += " (exit %d)" % fold.exit_code
            finish_color = FAILURE
        if elapsed:
            text += " · " + elapsed
        finish = _label(text, finish_color)
    links = []
    if fold.command:
        links.append(Span("⧉cmd", DIM, link=copy(copy_target(fold.shell_id, "cmd"))))
    if fold.output or fold.status:
        links.append(Span("⧉out", DIM, link=copy(copy_target(fold.shell_id, "out"))))
    return _block(
        "%s %s" % (marker, heading),
        color,
        fold.command,
        fold.output,
        fold.status,
        finish,
        width,
        links=links,
    )


def copy_target(shell_id: str, half: str) -> str:
    """The name a copy link carries, and the key the pane publishes it under.

    Two halves per command because they are two things a person copies: what was
    RUN and what came BACK. They were two content references before and they are
    two names now; nothing else changed.
    """
    return "sh:%s:%s" % (shell_id, half)


def _file_rows(entry: dict[str, Any], view: Links, width: int, open_now: bool) -> list[str]:
    """One line per file, and its content beneath when the reader expanded it.

    The whole line is the link, not a marker beside it: the target is the file,
    and a two-character affordance on a line this narrow is a worse click than
    the words themselves.
    """
    body = entry["body"]
    verb, color = FILE_VERBS.get(body["action"], ("Touch", VALUE))
    if body.get("state") == "failed":
        color = FAILURE
    target = view(entry["entry_id"]) if body.get("content") else None
    marker = "▾" if open_now else ("▸" if target else " ")
    spans = [
        Span(marker + " ", DIM, link=target),
        Span(verb, color, link=target),
        Span("(", dim=True, link=target),
        Span(body.get("path") or "", link=target),
        Span(")", dim=True, link=target),
    ]
    counts = []
    if body.get("lines_added"):
        counts.append(Span("+%d" % body["lines_added"], SUCCESS))
    if body.get("lines_removed"):
        counts.append(Span("-%d" % body["lines_removed"], FAILURE))
    for index, span in enumerate(counts):
        spans.extend((Span("  " if index == 0 else " "), span))
    painted = rows(spans, width, layout="truncate")
    if open_now:
        painted.extend(_content_rows(_entry_text(body.get("content")), body["action"], width))
    return painted


def _content_rows(content: str, action: str, width: int) -> list[str]:
    """A file's own text, laid out verbatim.

    Verbatim and not wrapped: this is source or a diff, its columns mean
    something, and re-flowing it is how a diff stops being readable. A row wider
    than the pane is cut rather than folded, for the same reason.
    """
    lines = content.splitlines()
    number_width = max(1, len(str(len(lines))))
    changed = action != "read"
    painted = []
    for number, line in enumerate(lines, 1):
        background = None
        if changed and line.startswith("-"):
            background = REMOVED_BACKGROUND
        elif changed and line.startswith("+"):
            background = ADDED_BACKGROUND
        painted.extend(rows(
            [Span(line)],
            width,
            prefix=(Span(" %*d " % (number_width, number), DIM),),
            layout="verbatim",
            background=background,
        ))
    return painted


def _tool_rows(entry: dict[str, Any], width: int) -> list[str]:
    """A search, a fetch, a worktree move or a skill — the block shape with the
    tool's own name on the chip. These arrived as one operation kind each in the
    old vocabulary and drew as commands; they still do."""
    body = entry["body"]
    kind = entry["type"]
    if kind == "search":
        chip, summary = body.get("tool") or "search", _entry_text(body.get("query"))
    elif kind == "web":
        chip, summary = "WebFetch", body.get("url") or ""
    elif kind == "worktree":
        chip = "EnterWorktree" if body.get("action") == "entered" else "ExitWorktree"
        summary = _entry_text(body.get("arguments"))
    elif kind == "skill_started":
        chip, summary = "Skill", body.get("name") or ""
    else:
        chip, summary = "Skill finished", ""
    state = body.get("state")
    finish = None if state in (None, "succeeded") else _label("■ " + state, FAILURE)
    return _block(
        chip,
        TEXT,
        summary,
        _entry_text(body.get("result")),
        "",
        finish,
        width,
    )


def _entry_text(content: dict[str, Any] | None) -> str:
    return (content or {}).get("text") or ""


def _message_rows(entry: dict[str, Any], model: SessionModel, width: int) -> list[str]:
    body = entry["body"]
    name = model.actor_name(entry["actor_id"])
    if body.get("phase") == "recap":
        label, color = "RECAP", TEXT
    elif body.get("role") == "user":
        label, color = "YOU", USER
    elif body.get("role") == "parent":
        label, color = "PARENT", TEXT
    elif body.get("recipient_actor_id"):
        label = "%s → %s" % (name.upper(), model.actor_name(body["recipient_actor_id"]).upper())
        color = USER
    else:
        label, color = name.upper(), TEXT
    return _said(label, _entry_text(body.get("content")), width, color)


def _attention_rows(entry: dict[str, Any], width: int) -> list[str]:
    kind = entry["type"]
    if kind == "question_asked":
        blocks = []
        for question in entry["body"].get("questions") or ():
            lines = [question["question"]] if question.get("question") else []
            lines.extend(
                "- " + choice["label"]
                for choice in question.get("choices") or ()
                if choice.get("label")
            )
            if lines:
                blocks.append("\n".join(lines))
        return _said("QUESTION", "\n\n".join(blocks), width, WORKING)
    if kind == "plan_proposed":
        return _said("PLAN", _entry_text(entry["body"].get("plan")), width, WORKING)
    if kind == "question_answered":
        answered = "\n".join(
            label
            for answer in entry["body"].get("answers") or ()
            for label in answer.get("labels") or ()
        )
        return _said("ANSWERED", entry["body"].get("feedback") or answered, width, WORKING)
    decision = PLAN_DECISIONS.get(entry["body"].get("state") or "", "DECIDED")
    return _said(decision, entry["body"].get("feedback") or "", width, WORKING)


def _note_rows(entry: dict[str, Any], width: int) -> list[str]:
    """The one-line ⟳ and ⇢ forms: something happened that is worth a line and
    no more."""
    body = entry["body"]
    kind = entry["type"]
    if kind == "compaction_started":
        text = "compacting the context…"
    elif kind == "compaction_finished":
        before, after = body.get("before_tokens"), body.get("after_tokens")
        text = "compacted"
        if before and after:
            text += " · %s → %s tokens" % (count(before), count(after))
    elif kind == "model_change":
        text = "model " + _transition(body)
        if body.get("automatic"):
            text += " (chosen for you)"
    else:
        text = "effort " + _transition(body)
    return rows([Span("⟳ ", MODIFIED), Span(text)], width)


def _transition(body: dict[str, Any]) -> str:
    previous, current = body.get("previous"), body.get("current") or ""
    return "%s → %s" % (previous, current) if previous else current


def _assignment_rows(entry: dict[str, Any], width: int) -> list[str]:
    body = entry["body"]
    if entry["type"] == "assignment_started":
        marker = "⇢"
        text = entry.get("summary") or _entry_text(body.get("prompt"))
        name = body.get("assigned_actor_name")
        if name:
            text = "%s: %s" % (name, text) if text else name
    else:
        marker = "⇠"
        text = _entry_text(body.get("result"))
        if body.get("state") != "succeeded":
            text = "%s · %s" % (body.get("state"), text) if text else body.get("state") or ""
    return rows(
        [Span(marker + " ", WORKING, bold=True), Span(text or "agent")],
        width,
        continuation=(Span("  "),),
    )


# What the mirror is FOR: watching the work, not re-reading the conversation you
# are already having. So the lead's own talking and thinking stay off it and a
# child actor's stay on — the rule the daemon's `terminal/mirror/visibility.py`
# applied, preserved here unchanged.
QUIET_FOR_THE_LEAD = frozenset({"message", "reasoning"})


def visible(entry: dict[str, Any], lead_actor_id: str) -> bool:
    if entry["type"] not in QUIET_FOR_THE_LEAD:
        return True
    if (entry["body"] or {}).get("role") == "system":
        return False
    return bool(entry["actor_id"] != lead_actor_id)


def entry_rows(
    entry: dict[str, Any],
    model: SessionModel,
    width: int,
    view: Links,
    opened: frozenset[str],
) -> list[str]:
    kind = entry["type"]
    if kind == "message":
        return _message_rows(entry, model, width)
    if kind == "reasoning":
        return _said("THINK", _entry_text(entry["body"].get("content")), width, MUTED)
    if kind == "file":
        return _file_rows(entry, view, width, entry["entry_id"] in opened)
    if kind in ("search", "web", "worktree", "skill_started", "skill_finished"):
        return _tool_rows(entry, width)
    if kind in ("question_asked", "question_answered", "plan_proposed", "plan_resolved"):
        return _attention_rows(entry, width)
    if kind in ("assignment_started", "assignment_finished"):
        return _assignment_rows(entry, width)
    if kind in (
        "compaction_started",
        "compaction_finished",
        "model_change",
        "effort_change",
    ):
        return _note_rows(entry, width)
    # The two turn markers, and anything a newer daemon serves that this client
    # has never heard of. Both are grouping facts rather than lines, and an
    # unknown one is a client that is simply older than its daemon.
    return []


def mirror(
    model: SessionModel,
    width: int,
    *,
    copy: Links = lambda name: "",
    view: Links = lambda name: "",
    opened: frozenset[str] = frozenset(),
) -> str:
    """The whole feed at this width. Called on every frame, on every resize and
    on every expand — a repaint is the only update this surface has.

    The two link builders default to producing nothing, so a caller that wants
    the text and not the clicks (a test, a diff of two paints) gets a clean
    screen without opting out of anything.
    """
    lead_actor_id = model.lead_actor_id()
    painted: list[str] = []
    for item in model.feed():
        if isinstance(item, ShellFold):
            painted.extend(
                _shell_rows(item, model.running_shell(item.shell_id), copy, width)
            )
        elif visible(item, lead_actor_id):
            painted.extend(entry_rows(item, model, width, view, opened))
    # The task panel goes LAST, and that is the whole of its placement decision.
    # This surface is newest-at-the-bottom, so the end of the paint is what a
    # reader is actually looking at; and the pane clears its scrollback on every
    # repaint, so there is exactly one copy of it rather than one per frame. The
    # scoreboard would have been the other candidate, but the daemon pins that
    # pane to a fixed height (terminal/adapter.py SCOREBOARD_HEIGHT), and content
    # whose size is decided by a constant in another process is a trap.
    tasks = task_rows(model, width)
    if tasks:
        painted.extend(["", _rule(width), *tasks])
    return screen(painted, rows([Span(HEADER_TEXT, HEADER_COLOR)], width, layout="truncate"))


def copy_targets(model: SessionModel) -> dict[str, str]:
    """Every copy link's text, for the pane to publish.

    Built from the MODEL rather than collected while painting, so that what a
    click can reach is exactly what the feed holds — a link the paint dropped
    because the screen filled up still copies, and a target for something the
    model never had cannot exist.
    """
    targets: dict[str, str] = {}
    for item in model.feed():
        if not isinstance(item, ShellFold):
            continue
        if item.command:
            targets[copy_target(item.shell_id, "cmd")] = item.command
        output = "".join(part for part in (item.status, item.output) if part)
        if output:
            targets[copy_target(item.shell_id, "out")] = output
    return targets
