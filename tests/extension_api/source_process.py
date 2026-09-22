# Copyright (c) 2026 Zhambyl Yermagambet
"""Connect an external journal feature and capture its input for pure replay."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from baqylau_extension_api.models import content, events, sources, translation_inputs
from baqylau_extension_api.models.scopes import RepositoryScope
from baqylau_extension_api.models.source_results import PositionedObservation, SourceBatch
from baqylau_extension_api.runtime import bridge, methods, worker_models
from baqylau_extension_api.runtime.sources import RemoteSources
from pydantic import TypeAdapter

from tests.extension_api import operation_samples, process_fixture, samples, source_samples


@dataclass(frozen=True)
class SourceProcess:
    """Expose a typed caller and the host-selected repository context."""

    caller: bridge.RpcBridge
    context: sources.SourceContext


@asynccontextmanager
async def running_source(directory: Path, journal: Path) -> AsyncIterator[SourceProcess]:
    """Run the SDK and feature outside the host source tree.

    Yields:
        A loaded source worker in repository scope, with no fake actor.

    """
    context = journal_context(journal)
    request = worker_models.WorkerLoadRequest(
        manifest=source_samples.manifest(context.settings), environment=samples.worker_environment(),
    )
    async with process_fixture.running_worker(directory) as worker:
        ready = await worker.channel.call(methods.LOAD, request, TypeAdapter(worker_models.WorkerReady))
        assert ready.capabilities == ("lifecycle", "sources", "translator")
        caller = bridge.RpcBridge(worker.channel, asyncio.get_running_loop(), 3)
        yield SourceProcess(caller, context)


def journal_context(journal: Path) -> sources.SourceContext:
    """Capture a source path as declared settings for one repository scope.

    Returns:
        A typed scope for protocol tests; no real Git metadata is claimed.

    """
    scope = RepositoryScope(
        repository_id="fixture", worktree=str(journal.parent), git_directory=str(journal.parent / ".git"),
    )
    context = source_samples.context()
    encoded = TypeAdapter(str).dump_json(str(journal)).decode()
    return context.model_copy(update={
        "binding": context.binding.model_copy(update={"scope": scope}),
        "settings": operation_samples.query_request(encoded).arguments,
    })


async def describe(worker: SourceProcess) -> sources.SourceReadRequest:
    """Read the complete source plan and select its single fixture journal.

    Returns:
        A bounded read request with its exact source generation.

    """
    plan = await asyncio.to_thread(RemoteSources(worker.caller).describe, worker.context)
    assert len(plan.sources) == 1
    assert plan.sources[0].next_due_at is None
    return sources.SourceReadRequest(context=worker.context, source=plan.sources[0])


async def read_all(worker: SourceProcess) -> SourceBatch:
    """Read all lines from the small fixture journal in one declared batch.

    Returns:
        The ready fixture source batch.

    """
    request = await describe(worker)
    response = await asyncio.to_thread(RemoteSources(worker.caller).read, request)
    assert isinstance(response, SourceBatch)
    return response


def capture_input(context: sources.SourceContext, batch: SourceBatch) -> translation_inputs.ExtensionTranslationRequest:
    """Supply recorded original bytes with immutable source metadata.

    Returns:
        A complete decoder request; this fixture does not write a host database.

    """
    recorded = tuple(_recorded_input(batch, positioned) for positioned in batch.observations)
    blobs = {blob.reference.content_id: blob for _, blob in recorded}
    processing = events.ProcessingContext(
        extension_id=context.binding.extension_id, scope=context.binding.scope,
        runtime_revision=context.binding.runtime_revision, history_revision="history-1", input_cursor=1,
        settings_revision=context.settings_revision, settings=context.settings,
    )
    return translation_inputs.ExtensionTranslationRequest(
        context=processing, state=translation_inputs.TranslationState(revision=0),
        inputs=tuple(source for source, _ in recorded),
        content_snapshot=content.ContentBundle(blobs=tuple(blobs.values())),
    )


def _recorded_input(
    batch: SourceBatch, positioned: PositionedObservation,
) -> tuple[translation_inputs.TranslationInput, content.ContentBlob]:
    observation = positioned.observation
    blob = content.encode_content(observation.document.json_text.encode("utf-8"), "application/json")
    source_identity = batch.binding.source_identity
    raw_id = f"recorded:{source_identity}:{positioned.position}"
    raw = events.RawInput(
        input_id=raw_id, scope=observation.scope, source_type=observation.source_type,
        source=events.SourceReference(
            raw_event_id=raw_id, source_identity=observation.source_identity, source_position=positioned.position,
        ), content=blob.reference, origin="extension", owner=batch.binding.call.extension_id,
    )
    return translation_inputs.TranslationInput(
        source=raw, schema_ref=observation.document.schema_ref,
        occurred_at=observation.occurred_at, causes=observation.causes,
    ), blob
