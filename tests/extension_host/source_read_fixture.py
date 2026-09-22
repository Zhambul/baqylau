# Copyright (c) 2026 Zhambyl Yermagambet
"""Build source calls against a real retained package and committed runtime."""

from dataclasses import dataclass
from pathlib import Path

from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models import source_results, sources
from baqylau_extension_api.sources.batches import source_read_binding

from extensions.interpretation_resources import InterpretationStores
from extensions.models.source_reads import SourceReadCommit, SourceReadProposal, source_key
from repository.impl.sqlite.interpretations import SqliteInterpretationRepository
from repository.impl.sqlite.source_reads import SqliteExtensionSourceRepository
from tests.extension_api import source_samples
from tests.extension_host import observation_fixture as originals


@dataclass(frozen=True)
class SourceCase:
    """Keep real original storage and its selected source context together."""

    original: originals.ObservationCase
    store: SqliteExtensionSourceRepository
    request: sources.SourceReadRequest


def installed(directory: Path, manifest: ExtensionManifest | None = None) -> SourceCase:
    """Retain the real source declaration without claiming a worker is running.

    Returns:
        A selected source and repositories for one real private database.

    """
    original = originals.installed(directory, manifest)
    settings = None
    if manifest is not None and manifest.settings is not None:
        settings = manifest.settings.defaults
    context = sources.SourceContext(binding=sources.SourceBinding(
        extension_id=original.request.extension_id, scope=original.request.scope,
        runtime_revision=original.request.runtime_revision, call_id="read-1",
    ), settings_revision=0, settings=settings)
    return SourceCase(
        original, SqliteExtensionSourceRepository(original.store.database),
        sources.SourceReadRequest(context=context, source=source_samples.descriptor()),
    )


def interpretation_stores(case: SourceCase) -> InterpretationStores:
    """Connect source originals to mixed interpretations in the same private database.

    Returns:
        Actual storage protocols for engine integration tests.

    """
    return InterpretationStores(case.original.store, SqliteInterpretationRepository(case.store.database))


def proposal(
    case: SourceCase, call_id: str = "read-1", position: str | None = "position-1", *, emit: bool = True,
) -> SourceReadCommit:
    """Capture actual progress and produce one source reply for it.

    Returns:
        A complete read with a new observation or an explicit empty result.

    """
    checkpoint = case.store.source_checkpoint(source_key(case.request))
    request = case.request.model_copy(update={
        "context": case.request.context.model_copy(update={
            "binding": case.request.context.binding.model_copy(update={"call_id": call_id}),
        }), "after_position": checkpoint.position,
    })
    response = source_results.SourceBatch(
        binding=source_read_binding(request), next_position=position,
        observations=_observations(case, call_id, position) if emit else (),
    )
    return SourceReadCommit(proposal=SourceReadProposal(
        manager_id=case.original.request.manager_id, checkpoint=checkpoint, request=request, response=response,
    ), observed_at=case.original.request.observed_at)


def with_request(commit: SourceReadCommit, request: sources.SourceReadRequest) -> SourceReadCommit:
    """Keep a changed request and the reply's selected binding together.

    Returns:
        A proposal for repository rejection tests, without bypassing the public boundary.

    """
    return commit.model_copy(update={"proposal": commit.proposal.model_copy(update={
        "request": request,
        "response": commit.proposal.response.model_copy(update={"binding": source_read_binding(request)}),
    })})


def _observations(
    case: SourceCase, call_id: str, position: str | None,
) -> tuple[source_results.PositionedObservation, ...]:
    assert position is not None
    positioned = case.original.request.observations[0]
    candidate = positioned.observation.model_copy(update={
        "observation_key": f"observation-{call_id}", "scope": case.request.context.binding.scope,
    })
    return (source_results.PositionedObservation(position=position, observation=candidate),)
