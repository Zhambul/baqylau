# Copyright (c) 2026 Zhambyl Yermagambet
"""An open change stream holds its scope, and a closed stream releases it (P10-T02)."""

import asyncio
from contextlib import suppress
from unittest.mock import Mock

from baqylau_extension_api.models import scopes

from api.extensions.change_frames import change_frames
from api.extensions.change_models import ExtensionChangeQuery
from api.extensions.change_stream import ChangeStream
from core.change_signal import ChangeSignal
from extensions.source_scopes import ActiveExtensionScopes
from repository.contract.extension_records import ExtensionRecordChanges, ExtensionRecordRepository

OWNER = "test.owner"
REPOSITORY = scopes.RepositoryScope(repository_id="repo-1", worktree="/work/repo", git_directory="/work/repo/.git")
DEFAULT = "default"
SETTLE_SECONDS = 0.05
HOLD_AND_RELEASE = 2


def idle_stream(held: ActiveExtensionScopes) -> ChangeStream:
    """Build a stream with no record changes in the default generation.

    Returns:
        The stream services.

    """
    repository = Mock(spec=ExtensionRecordRepository)
    repository.record_changes.return_value = ExtensionRecordChanges((), 0)
    heads = Mock()
    heads.active_generation.return_value = DEFAULT
    return ChangeStream(records=repository, changes=ChangeSignal(), history_revision=DEFAULT, heads=heads, scopes=held)


async def _check_hold() -> None:
    changed = Mock()
    held = ActiveExtensionScopes(changed)
    query = ExtensionChangeQuery(scope=REPOSITORY.model_dump_json())
    frames = change_frames(idle_stream(held), OWNER, REPOSITORY, query)
    pending = asyncio.create_task(anext(frames))
    await asyncio.sleep(SETTLE_SECONDS)

    assert REPOSITORY in held.source_scopes()
    pending.cancel()
    with suppress(asyncio.CancelledError):
        await pending
    await frames.aclose()

    assert REPOSITORY not in held.source_scopes()
    assert changed.call_count == HOLD_AND_RELEASE


def test_open_stream_holds_its_scope() -> None:
    """The scope's sources are planned while the stream is open, and are released when it closes."""
    asyncio.run(_check_hold())
