"""Canonical session statistics in the existing five-row terminal surface."""

from __future__ import annotations

from dataclasses import dataclass

from terminal.models import RGB
from engine.projections import ActivityStatistics, SessionSummary, UsageSummary
from terminal.mirror.blocks import TerminalBlock, TerminalLine, TerminalStyle, TerminalText, TerminalUpdate

MUTED = RGB(133, 143, 166)
VALUE = RGB(171, 178, 191)
FAILURE = RGB(224, 108, 117)
SUCCESS = RGB(152, 195, 121)
ORANGE = RGB(209, 154, 102)
SEPARATOR = " · "


@dataclass(frozen=True)
class ScoreboardSnapshot:
    session: SessionSummary
    usage: UsageSummary
    statistics: ActivityStatistics
    active_seconds: float


def _count(value: int) -> str:
    if value < 1_000:
        return str(value)
    if value < 1_000_000:
        return f"{value / 1_000:.0f}k"
    return f"{value / 1_000_000:.0f}M"


def _duration(seconds: float) -> str:
    whole_seconds = max(0, int(seconds))
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"


def _trimmed(parts: list[tuple[str, TerminalStyle]], available: int) -> list[tuple[str, TerminalStyle]]:
    visible = list(parts)
    while visible and sum(len(text) for text, _style in visible) + len(SEPARATOR) * (len(visible) - 1) > available:
        visible.pop()
    return visible


def _row(prefix: str, parts: list[tuple[str, TerminalStyle]], width: int) -> TerminalLine:
    prefix_text = f" {prefix} "
    visible = _trimmed(parts, max(0, width - len(prefix_text)))
    content: list[TerminalText] = []
    for index, (text, style) in enumerate(visible):
        if index:
            content.append(TerminalText(SEPARATOR, TerminalStyle(dim=True)))
        content.append(TerminalText(text, style))
    return TerminalLine(
        tuple(content),
        prefix=(TerminalText(prefix_text, TerminalStyle(dim=True)),),
        layout="truncate",
    )


class ScoreboardPresenter:
    def present(self, snapshot: ScoreboardSnapshot, width: int) -> TerminalUpdate:
        if width <= 0:
            raise ValueError("scoreboard width must be positive")
        statistics = snapshot.statistics
        usage = snapshot.usage.tokens
        cache_write_tokens = usage.cache_write_tokens + usage.one_hour_cache_write_tokens
        total_tokens = (
            usage.input_tokens
            + usage.output_tokens
            + usage.cache_read_tokens
            + cache_write_tokens
        )
        account = snapshot.session.account
        identity_parts = [(str(snapshot.session.session_id), TerminalStyle(VALUE))]
        if account is not None:
            identity_parts.append((f"◈ {account.display_name}", TerminalStyle(MUTED)))
        message_parts = [(f"{statistics.actor_message_count} msgs", TerminalStyle(MUTED))]
        activity_parts = [(f"{statistics.shell_command_count} cmds", TerminalStyle(MUTED))]
        if statistics.failed_shell_command_count:
            activity_parts.append(
                (f"{statistics.failed_shell_command_count}✗", TerminalStyle(FAILURE))
            )
        activity_parts.append((f"⏱ {_duration(snapshot.active_seconds)}", TerminalStyle(MUTED)))
        token_parts = [(f"{_count(total_tokens)} total", TerminalStyle(VALUE))]
        for value, label in (
            (usage.input_tokens, "in"),
            (usage.output_tokens, "out"),
            (usage.cache_read_tokens, "cache"),
            (cache_write_tokens, "write"),
        ):
            if value:
                token_parts.append((f"{_count(value)} {label}", TerminalStyle(MUTED)))
        if snapshot.usage.cost_in_usd is not None:
            token_parts.append((f"≈ ${snapshot.usage.cost_in_usd:.2f}", TerminalStyle(ORANGE)))
        detail_parts: list[tuple[str, TerminalStyle]] = []
        if statistics.file_count:
            noun = "file" if statistics.file_count == 1 else "files"
            detail_parts.append((f"{statistics.file_count} {noun}", TerminalStyle(MUTED)))
        if statistics.lines_added:
            detail_parts.append((f"+{statistics.lines_added}", TerminalStyle(SUCCESS)))
        if statistics.lines_removed:
            detail_parts.append((f"-{statistics.lines_removed}", TerminalStyle(FAILURE)))
        detail_parts.extend(
            (f"{name} {count}", TerminalStyle(MUTED))
            for name, count in sorted(
                statistics.operation_counts.items(),
                key=lambda item: (-item[1], item[0].lower()),
            )
        )
        rows = (
            _row("⬡", identity_parts, width),
            _row("✉", message_parts, width),
            _row("▪", activity_parts, width),
            _row("Σ", token_parts, width),
            _row(" ", detail_parts, width),
        )
        return TerminalUpdate(updated_blocks=(TerminalBlock("scoreboard", rows),))
