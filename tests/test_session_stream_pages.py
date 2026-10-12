# Copyright (c) 2026 Zhambyl Yermagambet
"""A session stream sends a large catch-up as several frames, one after the other."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from api.sessiondata import streams
from repository.impl.sqlite import session_entry_rows
from tests import (
    canonical_sessiondata_api_frame_parsing as frame_parsing,
    canonical_sessiondata_api_stream_models as stream_models,
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
    sqlite_test_migrations,
)

if TYPE_CHECKING:
    import pytest

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
# Each commit: its canonical cursor and the one entry that it adds.
FIRST_COMMIT = (20, ["a"])
SECOND_COMMIT = (21, ["b"])
LAST_COMMIT = (22, ["c"])
COMMITS = (FIRST_COMMIT, SECOND_COMMIT, LAST_COMMIT)


async def _read_three_frames(read_model: test_dependencies.SqliteSessionDataRepository) -> tuple[str, str, str]:
    reader = stream_models.FrameReader(
        streams.session_frames(
            streams.SessionStreamServices(read_model, stream_models.SilentAudit()),
            SESSION,
            streams.SessionStreamPosition(COMMITS[0][0] - 1),
            include_application=False,
        ),
    )
    # Each frame comes before the reader's timeout, with no change after the first read.
    first = await reader.next()
    second = await reader.next()
    last = await reader.next()
    await reader.aclose()
    return first, second, last


def _sent(frame: str) -> tuple[int, list[str]]:
    entries = frame_parsing.frame_body(frame)["entries"]
    return frame_parsing.frame_id(frame), [entry["entry_id"] for entry in entries]


def test_stream_sends_pages_without_a_notice(
    main: repository_dependencies.SqliteDatabase, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a page that stopped at its budget, the stream reads the next page at once."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    for cursor, entry_ids in COMMITS:
        entries = tuple(sqlite_test_migrations.an_entry(entry_id) for entry_id in entry_ids)
        store.apply(SESSION, repository_dependencies.SessionDataChanges(entries=entries), cursor)
    monkeypatch.setattr(session_entry_rows, "PAGE_CONTENT_BYTES", 1)

    frames = asyncio.run(_read_three_frames(store))

    assert [_sent(frame) for frame in frames] == list(COMMITS)
