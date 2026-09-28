# Copyright (c) 2026 Zhambyl Yermagambet
"""Split SDK client implementation."""

from __future__ import annotations

from pydantic import ValidationError

from sdk import application_models, sse, transport
from sdk.client_adapters import ERROR_FRAME


def _matches_delivered_prompt(
    entry: application_models.entry.EntryResponse,
    lower_cursor_bound: int,
    prompt: str,
) -> bool:
    if entry.cursor <= lower_cursor_bound:
        return False
    if not isinstance(entry.body, application_models.entry.MessageBodyResponse):
        return False
    if entry.body.role != "user" or entry.body.phase != "prompt":
        return False
    return entry.body.content.text.strip() == prompt


def _next_page_cursor(page: application_models.entry.EntryPageResponse, before: int | None) -> int:
    if not page.entries:
        msg = "the entry feed reports another page but returned no entries"
        raise transport.ApiFailureError(msg)
    next_before = page.oldest_cursor
    if before is not None and next_before >= before:
        msg = f"the entry feed did not move back from cursor {before} to {next_before}"
        raise transport.ApiFailureError(
            msg,
        )
    return next_before


def _stream_error_reason(path: str, sse_event: sse.SseEvent) -> str:
    try:
        return ERROR_FRAME.validate_json(sse_event.payload).error
    except ValidationError as error:
        msg = f"GET {path} returned an invalid error frame: {error}"
        raise transport.ApiFailureError(msg) from error


def _validate_entries(
    entries: tuple[application_models.entry.EntryResponse, ...],
    snapshot_cursor: int,
) -> None:
    # The feed is ordered by the fact that each row belongs to, then by the row.
    entry_ids = [entry.entry_id for entry in entries]
    keys = [(entry.commit_cursor, entry.cursor) for entry in entries]
    validation_results = (
        (len(entry_ids) != len(set(entry_ids)), "the entry feed returned a repeated entry id"),
        (keys != sorted(keys), "the entry feed did not return unique ascending cursors"),
        (len({key[1] for key in keys}) != len(keys), "the entry feed did not return unique ascending cursors"),
        (
            any(key[0] > snapshot_cursor for key in keys),
            f"the entry feed returned an entry newer than snapshot cursor {snapshot_cursor}",
        ),
    )
    for is_invalid, message in validation_results:
        if is_invalid:
            raise transport.ApiFailureError(message)


def _stream_cursor(sse_event: sse.SseEvent) -> int:
    if sse_event.event_id is None:
        msg = "sessionData stream frame has no event id"
        raise transport.ApiFailureError(msg)
    try:
        return int(sse_event.event_id)
    except ValueError as error:
        msg = f"sessionData stream frame has invalid event id {sse_event.event_id!r}"
        raise transport.ApiFailureError(
            msg,
        ) from error
