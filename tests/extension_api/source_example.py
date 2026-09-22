# Copyright (c) 2026 Zhambyl Yermagambet
"""Read a small external journal and translate its captured bytes with SDK imports only."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, BinaryIO

from baqylau_extension_api.contracts import (
    lifecycle as life_contract,
    plugin,
    services as host_services,
    sources as source_contract,
)
from baqylau_extension_api.models import (
    documents,
    events,
    lifecycle,
    observations,
    source_results,
    sources,
    translation_inputs,
    translation_results,
)
from baqylau_extension_api.models.directory import DirectoryRequest
from baqylau_extension_api.sources.batches import source_read_binding
from baqylau_extension_api.translation.inputs import input_document
from baqylau_extension_api.translation_identity import TranslationIdentity, translated_event_id
from pydantic import Field, TypeAdapter

FIXTURE_LINE_BYTES = 4096
SourcePosition = Annotated[int, Field(ge=0, strict=True)]


def file_identity(stat: os.stat_result) -> str:
    """Identify the opened journal generation for this small fixture.

    Returns:
        A different source identity after a file replacement.

    """
    return f"file:{stat.st_dev}:{stat.st_ino}"


def read_file(request: sources.SourceReadRequest) -> source_results.SourceReadResult:
    """Read a bounded journal page without retaining an open file handle.

    Returns:
        Original documents and the last complete line's byte position.

    """
    state = request.source.state
    if state is None:
        return source_results.SourceReadFailed(
            binding=source_read_binding(request),
            diagnostic=documents.Diagnostic(code="source.state_missing", message="The journal path is missing."),
        )
    filename = Path(TypeAdapter(str).validate_json(state.json_text))
    with filename.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        if file_identity(stat) != request.source.source_identity:
            return source_results.SourceReadFailed(
                binding=source_read_binding(request),
                diagnostic=documents.Diagnostic(code="source.changed", message="The journal was replaced."),
            )
        position = 0
        if request.after_position is not None:
            position = TypeAdapter(SourcePosition).validate_json(request.after_position)
        if position > stat.st_size:
            return source_results.SourceReadFailed(
                binding=source_read_binding(request),
                diagnostic=documents.Diagnostic(code="source.position_missing", message="The journal became shorter."),
            )
        stream.seek(position)
        return collect_lines(stream, request)


def collect_lines(stream: BinaryIO, request: sources.SourceReadRequest) -> source_results.SourceReadResult:
    """Leave a partial final line behind the committed position.

    Returns:
        A bounded complete page, or the current partial-file checkpoint.

    """
    entries: list[source_results.PositionedObservation] = []
    checkpoint = request.after_position
    for _ in range(request.limit):
        line = stream.readline(FIXTURE_LINE_BYTES + 1)
        if len(line) > FIXTURE_LINE_BYTES:
            return source_results.SourceReadFailed(
                binding=source_read_binding(request),
                diagnostic=documents.Diagnostic(code="source.line_limit", message="The fixture line is too large."),
            )
        if not line.endswith(b"\n"):
            return source_results.SourceBatch(
                binding=source_read_binding(request), observations=tuple(entries), next_position=checkpoint,
            )
        checkpoint = str(stream.tell())
        entries.append(positioned_line(request, line, checkpoint))
    return source_results.SourceBatch(
        binding=source_read_binding(request), observations=tuple(entries), next_position=checkpoint,
        has_more=bool(stream.read(1)),
    )


def positioned_line(
    request: sources.SourceReadRequest, line: bytes, position: str,
) -> source_results.PositionedObservation:
    """Keep the exact JSON line bytes inside the original document.

    Returns:
        One source-owned observation and its resume position.

    Raises:
        ValueError: If the fixture source state is absent.

    """
    if request.source.state is None:
        message = "fixture source state is missing"
        raise ValueError(message)
    return source_results.PositionedObservation(position=position, observation=observations.ObservationCandidate(
        observation_key=f"byte:{position}", source_identity=request.source.source_identity,
        source_type=request.source.source_type, scope=request.context.binding.scope,
        document=documents.EncodedDocument(schema_ref=request.source.state.schema_ref, json_text=line.decode("utf-8")),
    ))


@dataclass(frozen=True)
class SourceTranslator(source_contract.ExtensionTranslator):
    """Decode only the recorded input; never reopen its original file."""

    host: host_services.ExtensionHostServices

    def translate(
        self, translation_request: translation_inputs.ExtensionTranslationRequest,
    ) -> translation_results.ExtensionTranslationResult:
        """Return stable facts and the unchanged captured decoder state.

        Returns:
            One checked decision per input in its original order.

        """
        return translation_results.ExtensionTranslationResult(
            context=translation_request.context, state_revision=translation_request.state.revision,
            next_state=translation_request.state.document,
            decisions=tuple(self._decision(translation_request, source) for source in translation_request.inputs),
        )

    def _decision(
        self, request: translation_inputs.ExtensionTranslationRequest, source: translation_inputs.TranslationInput,
    ) -> translation_results.TranslatedInput:
        document = input_document(request, source)
        key = TypeAdapter(str).validate_json(document.json_text)
        if key == "host_call":
            self.host.directory.list_extensions(DirectoryRequest(active_only=True))
        owner = request.context.extension_id
        identity = TranslationIdentity(
            extension_id=owner, scope=request.context.scope, fact_key=key,
        )
        fact = events.ExtensionFact(
            event_id=translated_event_id(identity), scope=source.source.scope,
            event_type=f"{owner}.fact", document=document,
            occurred_at=source.occurred_at, causes=source.causes,
        )
        return translation_results.TranslatedInput(
            input_id=source.source.input_id, facts=(translation_results.TranslatedFact(fact_key=key, fact=fact),),
        )


@dataclass(frozen=True)
class SourceExample(plugin.ExtensionPlugin, life_contract.ExtensionLifecycle, source_contract.ExtensionSources):
    """Expose owned journal reads and pure translation through typed capabilities."""

    host: host_services.ExtensionHostServices

    @property
    def extension_info(self) -> lifecycle.ExtensionInfo:
        """The host-selected package identity."""
        return self.host.environment.extension_info

    @property
    def capabilities(self) -> plugin.ExtensionCapabilities:
        """The source, translator, and lifecycle protocols for this feature."""
        return plugin.ExtensionCapabilities(lifecycle=self, sources=self, translator=SourceTranslator(self.host))

    def activate(self, request: lifecycle.ActivationRequest) -> lifecycle.ActivationResult:
        """Confirm readiness for the selected runtime.

        Returns:
            The unchanged runtime revision.

        """
        return lifecycle.ActivationReady(runtime_revision=request.runtime_revision)

    def deactivate(self, request: lifecycle.DeactivationRequest) -> lifecycle.DeactivationResult:
        """Finish with no background handles retained by this fixture.

        Returns:
            A complete lifecycle stop result.

        """
        return lifecycle.DeactivationResult(runtime_revision=request.runtime_revision)

    def describe(self, source_context: sources.SourceContext) -> source_results.SourcePlan:
        """Select a file generation and request change notices for its path.

        Returns:
            A single owned source with no idle polling deadline.

        Raises:
            ValueError: If no fixture journal path was configured.

        """
        if source_context.settings is None:
            message = "fixture source settings are missing"
            raise ValueError(message)
        filename = Path(TypeAdapter(str).validate_json(source_context.settings.json_text))
        owner = source_context.binding.extension_id
        source = sources.SourceDescriptor(
            source_identity=file_identity(filename.stat()),
            source_type=f"{owner}.observation",
            watch_paths=(str(filename),), state=source_context.settings,
        )
        return source_results.SourcePlan(binding=source_context.binding, sources=(source,))

    def read(self, source_request: sources.SourceReadRequest) -> source_results.SourceReadResult:
        """Read one page of complete original journal lines.

        Returns:
            A checked source proposal, with no host checkpoint write.

        """
        return read_file(source_request)

    def release(self, release_request: sources.SourceReleaseRequest) -> source_results.SourceReleaseResult:
        """Confirm release because each completed read already closed its file.

        Returns:
            The exact selected source or scope release.

        """
        return source_results.SourceReleaseResult(
            binding=release_request.binding, source_identity=release_request.source_identity, status="released",
        )


def build_extension(services: host_services.ExtensionHostServices) -> plugin.ExtensionPlugin:
    """Build the external fixture without importing the host application.

    Returns:
        Its typed source and translation feature.

    """
    return SourceExample(services)


FACTORY: plugin.ExtensionFactory = build_extension
