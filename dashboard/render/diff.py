"""Dashboard presentation for canonical unified diffs."""

from __future__ import annotations

from dashboard.render.ansi import ansi_html, escape_html
from dashboard.render.highlight import source_ansi_for_path
from domain.unified_diff import DiffRow, diff_rows


def _highlight(text: str, path: str) -> str:
    highlighted = source_ansi_for_path(text, path)
    return escape_html(text) if highlighted is None else ansi_html(highlighted)


def _code(row: DiffRow, path: str) -> str:
    if row.changed_from is None or row.changed_to is None:
        return _highlight(row.text, path)
    before = _highlight(row.text[: row.changed_from], path)
    changed = _highlight(row.text[row.changed_from : row.changed_to], path)
    after = _highlight(row.text[row.changed_to :], path)
    return f'{before}<mark class="changed">{changed}</mark>{after}'


def unified_diff_html(unified_diff: str, path: str) -> str:
    """Render code rows; unified headers remain available only in raw content."""
    body = []
    for row in diff_rows(unified_diff):
        if row.kind == "separator":
            body.append('<div class="dl sep"><span class="ln"></span><span class="tx">⋮</span></div>')
            continue
        body.append(
            f'<div class="dl {row.kind}"><span class="ln">{row.number}</span>'
            f'<span class="tx">{_code(row, path)}</span></div>'
        )
    return '<div class="tdiff">' + "".join(body) + "</div>"


def source_html(source: str, path: str) -> str:
    """Render a captured file body with stable line numbers and syntax colors."""
    rows = (
        f'<div class="dl context"><span class="ln">{number}</span>'
        f'<span class="tx">{_highlight(line, path)}</span></div>'
        for number, line in enumerate(source.splitlines(), 1)
    )
    return '<div class="tdiff">' + "".join(rows) + "</div>"
