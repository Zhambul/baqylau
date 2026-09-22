# Copyright (c) 2026 Zhambyl Yermagambet
"""Check complete processing with real storage and local controlled capability calls."""

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

from baqylau_extension_api.contracts import lifecycle, plugin, processing, services, sources
from baqylau_extension_api.models import canonical, directory, environment, raw_transforms, transforms

from extensions import (
    interpretation_checks,
    interpretation_contract,
    interpretation_pipeline,
    interpretation_resources,
    processing_batch,
    registry_snapshot,
    source_batch,
    source_selection,
)
from extensions.models import interpretations, observations
from extensions.registry_package import RegistryPackage
from tests.extension_api import source_example
from tests.extension_host import (
    interpretation_context_fixture as contexts,
    interpretation_fixture as storage,
    interpretation_transforms as declarations,
)


@dataclass(frozen=True)
class ProcessingProbes:
    """Keep each capability's call evidence separate."""

    raw: Mock
    canonical: Mock
    decoder: Mock
    core: Mock


@dataclass(frozen=True)
class PipelineCase:
    """Use actual runtime and schema checks without claiming worker process coverage."""

    original: storage.InterpretationCase
    stored: observations.StoredObservation
    probes: ProcessingProbes
    admission_limit: int = interpretations.MAX_INTERPRETATION_BYTES

    def run(self) -> interpretations.InterpretationCommit:
        """Build and independently validate one complete journal in the real transaction.

        Returns:
            Its stored processing evidence.

        """
        commit = self.proposal()
        self.original.store.record_interpretation(commit)
        return commit

    def proposal(self) -> interpretations.InterpretationCommit:
        """Build one complete journal without writing it.

        Returns:
            The checked proposal with its host completion time.

        """
        return interpretations.InterpretationCommit(proposal=self._pipeline().interpret(), completed_at=1000)

    def run_batch(self) -> int:
        """Let the production batch capture prior state and accept all pending input.

        Returns:
            The number of committed interpretations.

        """
        context = contexts.context(self.original, self.stored)
        snapshot = registry_snapshot.prepare_snapshot(1, context.binding.runtime_revision, (self._package(),))
        sources = Mock(
            spec=source_batch.SelectedSourceBatch,
            context=source_selection.SourceBatchContext(context.binding.manager_id, snapshot, (context.binding.scope,)),
            callbacks=Mock(clock=Mock(return_value=1000.0)),
        )
        batch = processing_batch.SelectedProcessingBatch(sources, interpretation_resources.InterpretationStores(
            self.original.original.store, self.original.store,
        ))
        return batch.interpret_pending(self.probes.core, bool)

    def _pipeline(self) -> interpretation_pipeline.InterpretationPipeline:
        context = contexts.context(self.original, self.stored)
        return interpretation_pipeline.InterpretationPipeline(
            interpretation_checks.InterpretationChecks(context, interpretation_resources.InterpretationStores(
                self.original.original.store, self.original.store,
            )), (self._package(),), self.probes.core,
            canonical.CoreStateSnapshot(after_cursor=context.binding.expected_canonical_cursor),
            admission_limit=self.admission_limit,
        )

    def _package(self) -> RegistryPackage:
        context = contexts.context(self.original, self.stored)
        package = next(iter(context.packages.values()))
        capabilities = plugin.ExtensionCapabilities(
            lifecycle=Mock(spec=lifecycle.ExtensionLifecycle), translator=self.probes.decoder,
            raw_transformer=self.probes.raw, canonical_transformer=self.probes.canonical,
            sources=Mock(spec=sources.ExtensionSources),
        )
        return RegistryPackage(
            manifest=package.manifest,
            entry=directory.DirectoryEntry(extension_info=package.selection.extension_info, state="enabled"),
            environment=environment.ExtensionEnvironment(
                extension_info=package.selection.extension_info, runtime_revision=context.binding.runtime_revision,
            ),
            plugin=Mock(
                spec=plugin.ExtensionPlugin, capabilities=capabilities, extension_info=package.selection.extension_info,
            ),
            settings=package.selection.settings,
        )


def installed(path: Path, admission_limit: int = interpretations.MAX_INTERPRETATION_BYTES) -> PipelineCase:
    """Install a declaration which selects both raw and canonical stages.

    Returns:
        Real originals plus typed, observable local call boundaries.

    """
    original = storage.installed(path, declarations.combined_manifest())
    stored = original.original.store.append_observations(original.original.request).accepted[0]
    return PipelineCase(original, stored, probes(), admission_limit)


def probes() -> ProcessingProbes:
    """Build observable capabilities for either a core or extension original.

    Returns:
        No-op transforms, a real extension decoder, and a core boundary probe.

    """
    return ProcessingProbes(
        Mock(spec=processing.ExtensionRawTransformer, transform=Mock(return_value=raw_transforms.RawTransformResult())),
        Mock(spec=processing.ExtensionCanonicalTransformer, transform=Mock(
            return_value=transforms.CanonicalTransformResult(),
        )),
        Mock(spec=sources.ExtensionTranslator, wraps=source_example.SourceTranslator(
            Mock(spec=services.ExtensionHostServices),
        )),
        Mock(spec=interpretation_contract.CoreInterpretation),
    )
