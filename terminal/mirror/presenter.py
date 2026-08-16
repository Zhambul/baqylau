"""Canonical activity to the existing terminal visual vocabulary."""

from __future__ import annotations

import json

from terminal.models import RGB
from domain.values import Content, StructuredContent, TextContent
from engine.projections import (
    Activity,
    AttentionActivity,
    ActorAssignmentActivity,
    CompactionActivity,
    FileActivity,
    MessageActivity,
    OperationActivity,
    ActorMessageActivity,
    ReasoningActivity,
    TaskActivity,
)
from terminal.mirror.highlight import highlighted_lines
from terminal.mirror.highlight import highlighted_source
from terminal.mirror.blocks import (
    TerminalBlank,
    TerminalBlock,
    TerminalLine,
    TerminalRule,
    TerminalStyle,
    TerminalText,
    TerminalUpdate,
)

TEXT = RGB(170, 185, 210)
MUTED = RGB(133, 143, 166)
DIM = RGB(92, 99, 112)
USER = RGB(97, 175, 239)
SUCCESS = RGB(152, 195, 121)
FAILURE = RGB(224, 108, 117)
WORKING = RGB(209, 154, 102)
MODIFIED = RGB(229, 192, 123)
DARK = RGB(24, 26, 30)
REMOVED_BACKGROUND = RGB(55, 31, 36)
ADDED_BACKGROUND = RGB(29, 50, 38)
REMOVED_CHANGED_BACKGROUND = RGB(103, 42, 50)
ADDED_CHANGED_BACKGROUND = RGB(43, 87, 58)


def _plain(content: Content | None) -> str:
    if content is None:
        return ""
    if isinstance(content, TextContent):
        return content.text
    if isinstance(content, StructuredContent):
        parsed = json.loads(content.json_text)
        return json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True)
    raise TypeError(f"unsupported content: {type(content).__name__}")


def _line(*parts: TerminalText) -> TerminalLine:
    return TerminalLine(parts)


def _operation_text(activity: OperationActivity) -> str:
    return activity.output_text()


def _operation_command(activity: OperationActivity) -> str:
    return activity.command_text()


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remaining_seconds = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{remaining_seconds:02d}s"
    hours, remaining_minutes = divmod(minutes, 60)
    return f"{hours}h{remaining_minutes:02d}m"


def _label(text: str, color: RGB) -> TerminalLine:
    return _line(TerminalText(
        f" {text} ",
        TerminalStyle(foreground=DARK, background=color, bold=True),
    ))


def _operation_rows(activity: OperationActivity) -> tuple:
    execution = activity.execution or "foreground"
    running_marker = "▷" if execution in {"background", "monitor"} else "▶"
    running_color = WORKING if execution in {"background", "monitor"} else TEXT
    heading = "background" if execution == "background" else (
        "monitor" if execution == "monitor" else "foreground"
    )
    arguments = _operation_command(activity)
    rule = TerminalRule(TerminalStyle(DIM))
    command_reference = (
        f"{activity.context.source_event_ids[0]}:operation_command"
        if activity.arguments is not None
        else None
    )
    output_reference = (
        f"{activity.content_event_id}:operation_output"
        if activity.content_event_id is not None
        and (activity.result is not None or activity.progress)
        else None
    )
    heading_parts = [
        TerminalText(
            f" {running_marker} {heading} ",
            TerminalStyle(foreground=DARK, background=running_color, bold=True),
        ),
    ]
    if command_reference is not None:
        heading_parts.extend((
            TerminalText("  "),
            TerminalText("⧉cmd", TerminalStyle(DIM), command_reference),
        ))
    if output_reference is not None:
        heading_parts.extend((
            TerminalText("  " if command_reference is None else " "),
            TerminalText("⧉out", TerminalStyle(DIM), output_reference),
        ))
    rows = [TerminalBlank(), rule, TerminalLine(tuple(heading_parts))]
    for line in highlighted_lines(arguments):
        rows.append(TerminalLine(
            line,
            continuation_prefix=(TerminalText("  "),),
            layout="word_wrap",
        ))
    rows.append(rule)
    output = _operation_text(activity)
    for line in output.splitlines():
        rows.append(TerminalLine(
            (TerminalText(line.rstrip()),),
            prefix=(TerminalText("│ ", TerminalStyle(running_color)),),
            continuation_prefix=(TerminalText("│ ", TerminalStyle(running_color)),),
            layout="word_wrap",
        ))
    if activity.state == "finished":
        rows.append(rule)
        duration = None
        if activity.context.started_at is not None and activity.context.finished_at is not None:
            duration = _duration(max(0, activity.context.finished_at - activity.context.started_at))
        if activity.outcome == "succeeded":
            finish = "■ finished"
            finish_color = SUCCESS if execution != "foreground" else TEXT
        else:
            finish = "■ failed"
            if activity.exit_code is not None:
                finish += f" (exit {activity.exit_code})"
            finish_color = FAILURE
        if duration:
            finish += f" · {duration}"
        rows.append(_label(finish, finish_color))
        rows.append(rule)
    return tuple(rows)


def _file_row(activity: FileActivity) -> TerminalLine:
    file = activity.file
    verb, color = {
        "read": ("Read", USER),
        "created": ("Write", SUCCESS),
        "updated": ("Update", MODIFIED),
        "deleted": ("Delete", FAILURE),
        "renamed": ("Move", MODIFIED),
    }[file.action]
    if activity.outcome == "failed":
        color = FAILURE
    content_reference = (
        f"{activity.content_event_id}:{activity.content_field}"
        if activity.content_event_id is not None and activity.content_field is not None
        else None
    )
    content = [
        TerminalText(verb, TerminalStyle(color), content_reference, "view"),
        TerminalText("(", TerminalStyle(dim=True), content_reference, "view"),
        TerminalText(file.path, link_target=content_reference, link_action="view"),
        TerminalText(")", TerminalStyle(dim=True), content_reference, "view"),
    ]
    has_line_count = False
    if file.lines_added:
        content.extend(
            (
                TerminalText("  "),
                TerminalText(f"+{file.lines_added}", TerminalStyle(SUCCESS)),
            )
        )
        has_line_count = True
    if file.lines_removed:
        content.extend(
            (
                TerminalText(" " if has_line_count else "  "),
                TerminalText(f"-{file.lines_removed}", TerminalStyle(FAILURE)),
            )
        )
    return TerminalLine(tuple(content), layout="truncate")


def _diff_code(row, path: str, row_background: RGB | None) -> tuple[TerminalText, ...]:
    if row.changed_from is None or row.changed_to is None:
        return highlighted_source(row.text, path, row_background)
    changed_background = (
        REMOVED_CHANGED_BACKGROUND if row.kind == "removed" else ADDED_CHANGED_BACKGROUND
    )
    return (
        *highlighted_source(row.text[: row.changed_from], path, row_background),
        *highlighted_source(
            row.text[row.changed_from : row.changed_to], path, changed_background
        ),
        *highlighted_source(row.text[row.changed_to :], path, row_background),
    )


def _file_diff_rows(activity: FileActivity, unified_diff: str) -> tuple[TerminalLine, ...]:
    from domain.unified_diff import diff_rows

    parsed = diff_rows(unified_diff)
    number_width = max((len(str(row.number)) for row in parsed if row.number is not None), default=1)
    rendered = []
    for row in parsed:
        if row.kind == "separator":
            rendered.append(_line(
                TerminalText(" " * (number_width + 2), TerminalStyle(DIM)),
                TerminalText("⋮", TerminalStyle(DIM)),
            ))
            continue
        background = (
            REMOVED_BACKGROUND if row.kind == "removed"
            else ADDED_BACKGROUND if row.kind == "added"
            else None
        )
        rendered.append(TerminalLine(
            _diff_code(row, activity.file.path, background),
            prefix=(TerminalText(f" {row.number:>{number_width}} ", TerminalStyle(DIM)),),
            continuation_prefix=(TerminalText(" " * (number_width + 2), TerminalStyle(DIM)),),
            background=background,
            layout="verbatim",
        ))
    return tuple(rendered)


def _file_source_rows(activity: FileActivity, source: str) -> tuple[TerminalLine, ...]:
    lines = source.splitlines()
    number_width = max(1, len(str(len(lines))))
    return tuple(
        TerminalLine(
            highlighted_source(line, activity.file.path),
            prefix=(TerminalText(f" {number:>{number_width}} ", TerminalStyle(DIM)),),
            continuation_prefix=(TerminalText(" " * (number_width + 2), TerminalStyle(DIM)),),
            layout="verbatim",
        )
        for number, line in enumerate(lines, 1)
    )


class TerminalPresenter:
    def present(self, activity: Activity, expanded_content: str | None = None) -> TerminalUpdate:
        block_id = activity.context.activity_id
        rows: tuple[TerminalLine, ...]
        if isinstance(activity, MessageActivity):
            label = (
                "YOU"
                if activity.role == "user"
                else "PARENT"
                if activity.role == "parent"
                else (activity.context.actor_name or str(activity.context.actor_id)).upper()
            )
            if activity.phase == "recap":
                label = "RECAP"
            color = USER if activity.role == "user" else TEXT
            rows = (
                _line(
                    TerminalText(f"{label}  ", TerminalStyle(color, bold=True)),
                    TerminalText(_plain(activity.content)),
                ),
            )
        elif isinstance(activity, ReasoningActivity):
            rows = (
                _line(
                    TerminalText("THINK  ", TerminalStyle(MUTED, italic=True)),
                    TerminalText(_plain(activity.content)),
                ),
            )
        elif isinstance(activity, OperationActivity):
            rows = _operation_rows(activity)
        elif isinstance(activity, FileActivity):
            rows = (_file_row(activity),)
            if expanded_content is not None and activity.content_field == "unified_diff":
                rows += _file_diff_rows(activity, expanded_content)
            elif expanded_content is not None and activity.content_field == "content":
                rows += _file_source_rows(activity, expanded_content)
        elif isinstance(activity, AttentionActivity):
            if activity.phase == "requested":
                blocks = []
                for prompt in activity.prompts:
                    lines = [prompt.prompt] if prompt.prompt else []
                    lines.extend(f"- {choice.label}" for choice in prompt.choices if choice.label)
                    if lines:
                        blocks.append("\n".join(lines))
                text = "\n\n".join(blocks)
                label = "PLAN" if activity.attention_type == "plan" else "QUESTION"
            else:
                if activity.decision is None:
                    raise ValueError("resolved attention requires a decision")
                text = activity.feedback or "\n".join(
                    value for answer in activity.answers for value in answer.values
                )
                label = activity.decision.upper().replace("_", " ")
            rows = (
                _line(
                    TerminalText(f"{label}  ", TerminalStyle(WORKING, bold=True)),
                    TerminalText(text),
                ),
            )
        elif isinstance(activity, TaskActivity):
            text = f"task #{activity.change.label}"
            if activity.change.subject:
                text += f" · {activity.change.subject}"
            rows = (
                _line(
                    TerminalText("✓ " if activity.change.state == "completed" else "✚ "),
                    TerminalText(text),
                ),
            )
        elif isinstance(activity, CompactionActivity):
            rows = (_line(TerminalText("⟳ ", TerminalStyle(MODIFIED)), TerminalText("compacted")),)
        elif isinstance(activity, ActorAssignmentActivity):
            marker = "⇢" if activity.state == "running" else "⇠"
            body = (
                _plain(activity.brief)
                if activity.state == "running"
                else activity.reason or _plain(activity.result)
            )
            rows = (
                _line(
                    TerminalText(f"{marker} ", TerminalStyle(WORKING, bold=True)),
                    TerminalText(body or "actor assignment"),
                ),
            )
        elif isinstance(activity, ActorMessageActivity):
            rows = (_line(
                TerminalText("✉ ", TerminalStyle(USER, bold=True)),
                TerminalText(_plain(activity.content) or "message sent"),
            ),)
        else:
            raise TypeError(f"unsupported activity: {type(activity).__name__}")
        return TerminalUpdate(updated_blocks=(TerminalBlock(block_id, rows),))
