# Copyright (c) 2026 Zhambyl Yermagambet
"""Combine and divide styled terminal spans."""

from __future__ import annotations

from _render_styles import Color, Span, _SpanSplit, spans_width
from _render_width import cut, text_width


def _merged(spans: list[Span]) -> list[Span]:
    """Adjacent spans that look the same, joined.

    The word wrap works on one atom per word, so a paragraph would otherwise be
    painted with a full escape sequence around every word — several times the
    bytes of the text itself, on a surface that repaints from scratch.

    Returns:
        Result items.

    """
    merged: list[Span] = []
    for span in spans:
        if merged and merged[-1].style() == span.style():
            previous = merged[-1]
            merged[-1] = previous.sized(previous.text + span.text)
            continue
        merged.append(span)
    return merged


def _painted(spans: list[Span], width: int, background: Color | None) -> str:
    if background is None:
        return "".join(span.ansi() for span in _merged(spans))
    filled = [
        Span(
            span.text,
            span.color,
            span.background or background,
            bold=span.bold,
            dim=span.dim,
            link=span.link,
        )
        for span in spans
    ]
    remaining = max(0, width - spans_width(filled))
    if remaining:
        filled.append(Span(" " * remaining, background=background))
    return "".join(span.ansi() for span in _merged(filled))


def _take(spans: list[Span], width: int) -> _SpanSplit:
    """Take.

    The first `width` columns, and what is left. A span is cut mid-text when
        it has to be, keeping its style on both halves.

    Returns:
        Result items.

    """
    taken: list[Span] = []
    remaining = list(spans)
    available = max(0, width)
    while remaining and available:
        span = remaining.pop(0)
        if text_width(span.text) <= available:
            taken.append(span)
            available -= text_width(span.text)
            continue
        taken.extend(_head(span, available))
        rest = span.text[cut(span.text, available):]
        remaining.insert(0, span.sized(rest))
        available = 0
    return _SpanSplit(taken, remaining)


def _head(span: Span, columns: int) -> list[Span]:
    fitting = cut(span.text, columns)
    return [span.sized(span.text[:fitting])] if fitting else []
