# Copyright (c) 2026 Zhambyl Yermagambet
"""Check external core display changes, owned feed replacement, and replay."""

import asyncio
from pathlib import Path

import pytest
from baqylau_extension_api.models import projection_changes as changes
from baqylau_extension_api.models.projection_transforms import ProjectionTransformRequest, ProjectionTransformResult
from baqylau_extension_api.models.record_changes import PutRecord
from baqylau_extension_api.runtime.models import ExtensionTransportError

from tests.extension_api import projection_transform_checks as checks, projection_transform_samples as fixtures
from tests.extension_api.projection_transform_process import running_transformer


def test_external_transform_changes_core_display(tmp_path: Path) -> None:
    """Apply an external package's typed operations without altering canonical facts."""
    request = fixtures.request()
    response = asyncio.run(_once(tmp_path, request))
    output = checks.apply(request, *response.operations)
    _check_core_display(output)
    _check_extension_display(output)
    assert request == fixtures.request()


async def _once(directory: Path, request: ProjectionTransformRequest) -> ProjectionTransformResult:
    async with running_transformer(directory) as transformer:
        return await asyncio.to_thread(transformer.transform, request)


def _check_core_display(output: tuple[changes.ProjectionChange, ...]) -> None:
    session = output[0]
    actor = output[1]
    assert isinstance(session, changes.CoreSessionChange)
    assert isinstance(actor, changes.CoreActorChange)
    assert session.session.title == "Extension title"
    assert actor.actor.name == "Extension actor"
    assert actor.actor.background == fixtures.actor_change().actor.background


def _check_extension_display(output: tuple[changes.ProjectionChange, ...]) -> None:
    assert isinstance(output[2], changes.ExtensionEntryChange)
    assert isinstance(output[3], changes.ExtensionEntryChange)
    record = output[4]
    assert isinstance(record, changes.ExtensionRecordChange)
    assert isinstance(record.write, PutRecord)
    assert record.write.document.json_text == '"Extension record"'


def test_external_transform_replays_after_restart(tmp_path: Path) -> None:
    """Keep logical output stable across worker restart and candidate history."""
    request = fixtures.request()
    response = asyncio.run(_once(tmp_path, request))
    binding = request.binding.model_copy(update={
        "context": request.binding.context.model_copy(update={"mode": "replay", "history_revision": "history-2"}),
        "snapshot": request.binding.snapshot.model_copy(update={
            "history_revision": "history-2", "projection_generation": "projection-2",
        }),
    })
    request = request.model_copy(update={"binding": binding})
    replayed = asyncio.run(_once(tmp_path, request))
    assert response.operations == replayed.operations
    expected = checks.apply(fixtures.request(), *response.operations)
    assert checks.apply(request, *replayed.operations) == expected


def test_external_transform_rejects_live_callback(tmp_path: Path) -> None:
    """Reject a live host call from the pure projection-transform lane."""
    request = fixtures.request().model_copy(update={"changes": ()})
    with pytest.raises(ExtensionTransportError):
        asyncio.run(_once(tmp_path, request))


def test_external_transform_rejects_stale_runtime(tmp_path: Path) -> None:
    """Check the inner runtime revision before feature code executes."""
    request = fixtures.request()
    context = request.binding.context.model_copy(update={"runtime_revision": "old"})
    binding = request.binding.model_copy(update={"context": context})
    with pytest.raises(ExtensionTransportError):
        asyncio.run(_once(tmp_path, request.model_copy(update={"binding": binding})))
