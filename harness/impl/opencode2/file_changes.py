# Copyright (c) 2026 Zhambyl Yermagambet
"""Read one saved native file change as a completed file operation."""

from pathlib import Path

from domain import event_resource, outcomes
from harness.impl.opencode2.file_records import NativeFileChange

ADDED_STATUS = "added"
DELETED_STATUS = "deleted"
DIFF_PATH_MARK = "Index: "


def payload(
    directory: str, outcome: outcomes.Outcome, native_file_change: NativeFileChange,
) -> event_resource.FileAccessed:
    """Read one file that a native tool wrote.

    Returns:
        The file fact with the native diff and counts.

    """
    path = str(Path(directory) / native_file_change.file)
    previous_path = _previous_path(directory, path, native_file_change.patch)
    return event_resource.FileAccessed(
        path,
        outcomes.FileAction.RENAMED if previous_path else _action(native_file_change.status),
        outcome,
        previous_path=previous_path,
        lines_added=native_file_change.additions,
        lines_removed=native_file_change.deletions,
        unified_diff=native_file_change.patch,
    )


def _action(status: str) -> outcomes.FileAction:
    if status == ADDED_STATUS:
        return outcomes.FileAction.CREATED
    if status == DELETED_STATUS:
        return outcomes.FileAction.DELETED
    return outcomes.FileAction.UPDATED


def _previous_path(directory: str, path: str, patch: str | None) -> str | None:
    """Read the path that a move took the file from.

    The native result names only the file that the patch wrote. A move keeps the
    source in the diff header, so a diff that names another file is a move. The
    header holds an absolute path from the patch tool and a relative one from
    the edit tool, so it is read against the directory of the session.

    Returns:
        The path before the move, or None when the patch moved nothing.

    """
    lines = (patch or "").splitlines()
    header = lines[0] if lines else ""
    if not header.startswith(DIFF_PATH_MARK):
        return None
    named = header[len(DIFF_PATH_MARK):].strip()
    source = str(Path(directory) / named) if named else ""
    return source if source and source != path else None
