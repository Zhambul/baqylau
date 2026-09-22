# Copyright (c) 2026 Zhambyl Yermagambet
"""Check external selection, complete projection, replay, and pure-call failures."""

import asyncio
from pathlib import Path

import pytest
from baqylau_extension_api.models.projections import ProjectionRequest, ProjectionResult
from baqylau_extension_api.projection.selection import capture_projection_request
from baqylau_extension_api.runtime.models import ExtensionTransportError

from tests.extension_api import projection_checks, projection_process, projection_samples


def test_external_projector_selects_and_projects(tmp_path: Path) -> None:
    """Keep record-key rules and multi-row output entirely inside the feature."""
    asyncio.run(_project_external(tmp_path))


async def _project_external(directory: Path) -> None:
    async with projection_process.running_projector(directory) as projector:
        selected = await asyncio.to_thread(projector.select_records, projection_samples.selection_request())
        request = capture_projection_request(
            projection_samples.selection_request(), selected, (projection_samples.stored_record(),),
        )
        response = await asyncio.to_thread(projector.project, request)
        assert response == projection_samples.result()
        assert projection_checks.validate_result(request, response) == response
        assert request == projection_samples.request()


def test_external_projector_replays_after_restart(tmp_path: Path) -> None:
    """Keep rows and record changes stable across a new worker and candidate history."""
    response = asyncio.run(_once(tmp_path, projection_samples.request()))
    request = projection_samples.request()
    binding = request.binding.model_copy(update={
        "context": request.binding.context.model_copy(update={"mode": "replay", "history_revision": "history-2"}),
        "snapshot": request.binding.snapshot.model_copy(update={
            "history_revision": "history-2", "projection_generation": "projection-2",
        }),
    })
    request = request.model_copy(update={"binding": binding})
    replayed = asyncio.run(_once(tmp_path, request))
    assert response.entries == replayed.entries
    assert response.record_changes == replayed.record_changes


async def _once(directory: Path, request: ProjectionRequest) -> ProjectionResult:
    async with projection_process.running_projector(directory) as projector:
        return await asyncio.to_thread(projector.project, request)


@pytest.mark.parametrize("mode", ["host_call", "stale", "runtime"])
def test_external_projector_rejects_invalid_work(tmp_path: Path, mode: str) -> None:
    """Reject a live callback or stale record write through the real worker."""
    request = projection_process.input_mode(mode)
    if mode == "runtime":
        context = request.binding.context.model_copy(update={"runtime_revision": "another"})
        request = request.model_copy(update={
            "binding": request.binding.model_copy(update={"context": context}),
        })
    with pytest.raises(ExtensionTransportError):
        asyncio.run(_once(tmp_path, request))


@pytest.mark.parametrize("mode", ["drop", "delete"])
def test_external_projector_suppresses_or_deletes(tmp_path: Path, mode: str) -> None:
    """A feature can suppress feed output or delete a captured owned record."""
    request = projection_process.input_mode(mode)
    response = asyncio.run(_once(tmp_path, request))
    assert projection_checks.validate_result(request, response) == response
    if mode == "drop":
        assert response.entries == ()
    else:
        assert response.record_changes[0].operation == "delete"


def test_external_projector_allows_empty_output(tmp_path: Path) -> None:
    """An empty projection still returns the exact input progress boundary."""
    request = projection_samples.request().model_copy(update={"events": (), "prior_records": ()})
    response = asyncio.run(_once(tmp_path, request))
    assert response == ProjectionResult(binding=request.binding)
