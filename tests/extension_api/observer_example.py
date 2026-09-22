# Copyright (c) 2026 Zhambyl Yermagambet
"""Own post-commit feature behavior using installed public SDK imports only."""

from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Literal

from baqylau_extension_api.contracts import lifecycle, observers, operations, plugin, processing, sources
from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.models import (
    lifecycle as lifecycle_models,
    observer_jobs,
    observer_results,
    queries,
    raw_transforms,
    transforms,
    translation_inputs,
    translation_results,
)
from baqylau_extension_api.models.directory import DirectoryRequest
from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.models.events import ExtensionFact
from baqylau_extension_api.models.observations import ObservationCandidate
from baqylau_extension_api.models.operations import QuerySnapshot
from pydantic import TypeAdapter

UNCERTAIN = Diagnostic(code="fixture.uncertain", message="The observer result needs proof.")


@dataclass
class ObserverJob:
    """Hold test evidence only; this is not durable host job storage."""

    request: observer_jobs.ObservationJobRequest
    stop: Event = field(default_factory=Event)
    finished: bool = False


class ObserverState:
    """Keep active test calls and proof behind a lock."""

    def __init__(self) -> None:
        """Start with no observer execution or stored proof."""
        self._lock = Lock()
        self._records: dict[str, ObserverJob] = {}
        self.started = Event()

    def begin(self, request: observer_jobs.ObservationJobRequest) -> ObserverJob:
        """Reject repeated execution instead of hiding a host retry bug.

        Returns:
            The new job and its stop signal.

        Raises:
            ValueError: If the same fixture job is run twice.

        """
        with self._lock:
            if request.binding.job_id in self._records:
                message = "observer fixture job ran twice"
                raise ValueError(message)
            record = ObserverJob(request)
            self._records[request.binding.job_id] = record
            self.started.set()
            return record

    def finish(self, record: ObserverJob) -> None:
        """Mark this fixture call as no longer active."""
        with self._lock:
            record.finished = True

    def cancel(self, binding: observer_jobs.ObservationJobBinding) -> Literal["requested", "not_running"]:
        """Signal only the exact active observer attempt.

        Returns:
            An acknowledgment, not a stored final job state.

        """
        with self._lock:
            record = self._records.get(binding.job_id)
            if record is None or record.finished or record.request.binding != binding:
                return "not_running"
            record.stop.set()
            return "requested"

    def counts(self) -> tuple[int, int]:
        """Read execution and active counts for the process test.

        Returns:
            Complete counts under the same lock as execution.

        """
        with self._lock:
            active = sum(not record.finished for record in self._records.values())
            return len(self._records), active

    def has_proof(self, request: observer_jobs.ObservationReconcileRequest) -> bool:
        """Inspect existing proof without calling observe again.

        Returns:
            True only for the recorded trigger and its exact receipt.

        """
        with self._lock:
            record = self._records.get(request.observation.binding.job_id)
            if record is None or not record.finished or record.stop.is_set():
                return False
            fact = record.request.event.fact
            if not isinstance(fact, ExtensionFact) or request.receipt != fact.document:
                return False
            binding = record.request.binding.model_copy(update={"call_id": request.observation.binding.call_id})
            return record.request.model_copy(update={"binding": binding}) == request.observation


def completed(request: observer_jobs.ObservationJobRequest) -> observer_results.ObservationSucceeded:
    """Return a source observation linked to the committed trigger.

    Returns:
        One original record with a job-scoped stable source key.

    """
    fact = request.event.fact
    assert isinstance(fact, ExtensionFact)
    binding = request.binding
    return observer_results.ObservationSucceeded(binding=request.binding, observations=(ObservationCandidate(
        observation_key="result", source_identity=f"job:{binding.job_id}",
        source_type=f"{binding.extension_id}.observation", scope=binding.scope,
        document=fact.document, causes=(request.binding.event_id,),
    ),))


@dataclass(frozen=True)
class ObserverHandler(observers.ExtensionObserver):
    """Run live fixture work with separate cancellation and proof lookup."""

    state: ObserverState
    host: ExtensionHostServices

    def observe(
        self, observation_request: observer_jobs.ObservationJobRequest,
    ) -> observer_results.ObservationJobResult:
        """Run once and allow a live directory callback from the observer lane.

        Returns:
            A complete observation set or explicit uncertainty or cancellation.

        """
        record = self.state.begin(observation_request)
        fact = observation_request.event.fact
        assert isinstance(fact, ExtensionFact)
        self.host.directory.list_extensions(DirectoryRequest(active_only=True))
        if fact.document.json_text == '"wait"':
            stopped = record.stop.wait(timeout=5)
            self.state.finish(record)
            if stopped:
                return observer_results.ObservationCanceled(binding=observation_request.binding)
            return observer_results.ObservationOutcomeUnknown(binding=observation_request.binding, diagnostic=UNCERTAIN)
        self.state.finish(record)
        if fact.document.json_text == '"unknown"':
            return observer_results.ObservationOutcomeUnknown(
                binding=observation_request.binding, diagnostic=UNCERTAIN, receipt=fact.document,
            )
        return completed(observation_request)

    def cancel_observation(
        self, cancel_request: observer_jobs.ObservationCancelRequest,
    ) -> observer_jobs.ObservationCancelResult:
        """Signal the active attempt without returning a false final result.

        Returns:
            A checked request acknowledgment.

        """
        return observer_jobs.ObservationCancelResult(
            binding=cancel_request.binding, status=self.state.cancel(cancel_request.binding),
        )

    def reconcile_observation(
        self, reconcile_request: observer_jobs.ObservationReconcileRequest,
    ) -> observer_results.ObservationJobResult:
        """Inspect proof; after a new worker starts this memory is absent.

        Returns:
            The recorded outcome or explicit uncertainty without re-execution.

        """
        if self.state.has_proof(reconcile_request):
            return completed(reconcile_request.observation)
        return observer_results.ObservationOutcomeUnknown(
            binding=reconcile_request.observation.binding, diagnostic=UNCERTAIN, receipt=reconcile_request.receipt,
        )


class ObserverProcessing(processing.ExtensionRawTransformer, sources.ExtensionTranslator):
    """Keep transforms available and preserve result inputs as audit-only records."""

    def transform(self, request: transforms.RawTransformRequest) -> raw_transforms.RawTransformResult:
        """Keep input while observer jobs use the live lane.

        Returns:
            One explicit keep per original input.

        """
        return raw_transforms.RawTransformResult(operations=tuple(
            transforms.Keep(input_id=source.input_id) for source in request.inputs
        ))

    def translate(
        self, translation_request: translation_inputs.ExtensionTranslationRequest,
    ) -> translation_results.ExtensionTranslationResult:
        """Keep this fixture's output in raw audit without making another trigger.

        Returns:
            Explicit ignored decisions, not missing or unsupported input.

        """
        return translation_results.ExtensionTranslationResult(
            context=translation_request.context, state_revision=translation_request.state.revision,
            decisions=tuple(translation_results.IgnoredInput(
                input_id=source.source.input_id, reason="Observer fixture results are audit-only records.",
            ) for source in translation_request.inputs),
        )


@dataclass(frozen=True)
class ObserverExample(plugin.ExtensionPlugin, lifecycle.ExtensionLifecycle, operations.ExtensionQueries):
    """Own the feature factory, shared state, and each capability in this package."""

    host: ExtensionHostServices
    observer: ObserverHandler

    @property
    def extension_info(self) -> lifecycle_models.ExtensionInfo:
        """The package identity selected by the host."""
        return self.host.environment.extension_info

    @property
    def capabilities(self) -> plugin.ExtensionCapabilities:
        """Live jobs and independent pure processing capabilities."""
        processing = ObserverProcessing()
        return plugin.ExtensionCapabilities(
            lifecycle=self, observer=self.observer, queries=self, translator=processing, raw_transformer=processing,
        )

    def activate(self, request: lifecycle_models.ActivationRequest) -> lifecycle_models.ActivationResult:
        """Confirm that the fixture is ready.

        Returns:
            The requested runtime revision.

        """
        return lifecycle_models.ActivationReady(runtime_revision=request.runtime_revision)

    def deactivate(self, request: lifecycle_models.DeactivationRequest) -> lifecycle_models.DeactivationResult:
        """Finish after the process test has stopped its jobs.

        Returns:
            A complete stop result.

        """
        return lifecycle_models.DeactivationResult(runtime_revision=request.runtime_revision)

    def query(self, query_request: queries.QueryRequest) -> queries.QueryResult:
        """Read fixture counts and peer state during a slow observer job.

        Returns:
            A typed count string used by the process test.

        """
        if query_request.arguments.json_text == '"wait_for_active"':
            self.observer.state.started.wait(timeout=2)
        counts = self.observer.state.counts()
        peers = self.host.directory.list_extensions(DirectoryRequest(active_only=True))
        report = f"executions:{counts[0]};active:{counts[1]}"
        report = f"{report};peers:{len(peers.entries)}"
        encoded = TypeAdapter(str).dump_json(report).decode()
        return queries.QueryReady(
            binding=query_request.binding, document=query_request.arguments.model_copy(update={"json_text": encoded}),
            snapshot=QuerySnapshot(state_revision=f"counts:{counts[0]}:{counts[1]}"),
        )


def build_extension(services: ExtensionHostServices) -> plugin.ExtensionPlugin:
    """Build one external observer package through the public factory.

    Returns:
        The feature and its shared test state.

    """
    return ObserverExample(services, ObserverHandler(ObserverState(), services))


FACTORY: plugin.ExtensionFactory = build_extension
