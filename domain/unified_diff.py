"""Facts extracted from canonical unified-diff content."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace


_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class DiffRow:
    kind: str
    number: int | None
    text: str
    changed_from: int | None = None
    changed_to: int | None = None


def _changed_range(before: str, after: str) -> tuple[tuple[int, int], tuple[int, int]]:
    prefix = 0
    limit = min(len(before), len(after))
    while prefix < limit and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    remaining = min(len(before) - prefix, len(after) - prefix)
    while suffix < remaining and before[-suffix - 1] == after[-suffix - 1]:
        suffix += 1
    return (prefix, len(before) - suffix), (prefix, len(after) - suffix)


def _mark_changed_text(rows: list[DiffRow]) -> tuple[DiffRow, ...]:
    marked = list(rows)
    index = 0
    while index < len(marked):
        if marked[index].kind != "removed":
            index += 1
            continue
        removed_start = index
        while index < len(marked) and marked[index].kind == "removed":
            index += 1
        added_start = index
        while index < len(marked) and marked[index].kind == "added":
            index += 1
        for offset in range(min(added_start - removed_start, index - added_start)):
            removed_index = removed_start + offset
            added_index = added_start + offset
            removed_range, added_range = _changed_range(
                marked[removed_index].text,
                marked[added_index].text,
            )
            marked[removed_index] = replace(
                marked[removed_index], changed_from=removed_range[0], changed_to=removed_range[1]
            )
            marked[added_index] = replace(
                marked[added_index], changed_from=added_range[0], changed_to=added_range[1]
            )
    return tuple(marked)


def diff_rows(unified_diff: str) -> tuple[DiffRow, ...]:
    """Return numbered code rows, excluding transport headers and metadata."""
    rows: list[DiffRow] = []
    # None until the first @@ header names the starting line numbers. That one
    # fact is also "are we inside a hunk yet" — it used to be tracked twice,
    # with a separate seen_hunk flag, and nothing tied the two together: the
    # counter bumps below are only reachable once a header has run, but that
    # was an invariant of the loop rather than anything a reader (or a type
    # checker) could confirm. One sentinel, checked once, states it.
    old_number: int | None = None
    new_number: int | None = None
    for line in unified_diff.splitlines():
        match = _HUNK.match(line)
        if match:
            if old_number is not None:
                rows.append(DiffRow("separator", None, "⋮"))
            old_number, new_number = map(int, match.groups())
            continue
        if old_number is None or new_number is None or line.startswith(("--- ", "+++ ")):
            continue
        if line == r"\ No newline at end of file":
            continue
        if line.startswith("-"):
            rows.append(DiffRow("removed", old_number, line[1:]))
            old_number += 1
        elif line.startswith("+"):
            rows.append(DiffRow("added", new_number, line[1:]))
            new_number += 1
        elif line.startswith(" "):
            rows.append(DiffRow("context", new_number, line[1:]))
            old_number += 1
            new_number += 1
    return _mark_changed_text(rows)
