# Copyright (c) 2026 Zhambyl Yermagambet
"""Supply a complete query and command feature without private host imports."""

from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Literal

from baqylau_extension_api.contracts import lifecycle, operations, plugin, processing, services as host_services
from baqylau_extension_api.models import (
    command_results,
    commands as command_models,
    lifecycle as lifecycle_models,
    queries,
    raw_transforms,
    transforms,
)
from baqylau_extension_api.models.directory import DirectoryRequest
from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.models.operations import QuerySnapshot
from pydantic import TypeAdapter

UNCERTAIN = Diagnostic(code="fixture.uncertain", message="The result needs a proof check.")


@dataclass
class FixtureJob:
    """Record one execution and its stop request; this is not durable host storage."""

    request: command_models.CommandRequest
    stop: Event = field(default_factory=Event)
    finished: bool = False
    proven: bool = False


class OperationState:
    """Protect fixture state shared by query and command threads."""

    def __init__(self) -> None:
        """Start with no accepted fixture jobs."""
        self._lock = Lock()
        self._records: dict[str, FixtureJob] = {}
        self.started = Event()

    def begin(self, request: command_models.CommandRequest) -> FixtureJob:
        """Count each execution and reject a repeated job dispatch.

        Returns:
            The new record and its cancellation signal.

        Raises:
            ValueError: If execute is called again for the same fixture job.

        """
        with self._lock:
            if request.binding.job_id in self._records:
                message = "fixture job was executed more than once"
                raise ValueError(message)
            record = FixtureJob(request)
            self._records[request.binding.job_id] = record
            self.started.set()
            return record

    def finish(self, record: FixtureJob, *, proven: bool) -> None:
        """Record that execution stopped, with separate proof of success."""
        with self._lock:
            record.finished = True
            record.proven = proven

    def cancel(self, binding: command_models.CommandBinding) -> Literal["requested", "not_running"]:
        """Signal only the exact active attempt.

        Returns:
            A request acknowledgment, never an unproven final outcome.

        """
        with self._lock:
            record = self._records.get(binding.job_id)
            if record is None or record.finished or record.request.binding != binding:
                return "not_running"
            record.stop.set()
            return "requested"

    def snapshot(self) -> tuple[int, int]:
        """Read the execution and active counts under one lock.

        Returns:
            The total execution count and current active count.

        """
        with self._lock:
            active = sum(not record.finished for record in self._records.values())
            return len(self._records), active

    def has_proof(self, request: command_models.CommandReconcileRequest) -> bool:
        """Inspect recorded evidence without repeating execution.

        Returns:
            True only for matching input, receipt, and stable job identity.

        """
        with self._lock:
            record = self._records.get(request.command.binding.job_id)
            if record is None or not record.proven or request.receipt != record.request.arguments:
                return False
            binding = record.request.binding.model_copy(update={"call_id": request.command.binding.call_id})
            expected = record.request.model_copy(update={"binding": binding})
            return expected == request.command


@dataclass(frozen=True)
class OperationCommands(operations.ExtensionCommands):
    """Use memory only to test protocol flow, not restart recovery."""

    state: OperationState

    def execute(self, command_request: command_models.CommandRequest) -> command_results.CommandResult:
        """Run once and keep the stop acknowledgment separate from completion.

        Returns:
            A proven stopped or completed result, or explicit uncertainty.

        """
        record = self.state.begin(command_request)
        mode = TypeAdapter(str).validate_json(command_request.arguments.json_text)
        if mode == "wait":
            stopped = record.stop.wait(timeout=5)
            self.state.finish(record, proven=False)
            if stopped:
                return command_results.CommandCanceled(binding=command_request.binding)
            return command_results.CommandOutcomeUnknown(binding=command_request.binding, diagnostic=UNCERTAIN)
        self.state.finish(record, proven=True)
        if mode == "unknown":
            return command_results.CommandOutcomeUnknown(
                binding=command_request.binding, diagnostic=UNCERTAIN, receipt=command_request.arguments,
            )
        return command_results.CommandSucceeded(binding=command_request.binding, document=command_request.arguments)

    def cancel(self, cancel_request: command_models.CommandCancelRequest) -> command_models.CommandCancelResult:
        """Set a cancellation signal for the identified running attempt.

        Returns:
            The exact attempt's request acknowledgment.

        """
        return command_models.CommandCancelResult(
            binding=cancel_request.binding, status=self.state.cancel(cancel_request.binding),
        )

    def reconcile(self, reconcile_request: command_models.CommandReconcileRequest) -> command_results.CommandResult:
        """Check the fixture's proof without calling execute.

        Returns:
            The proven result, or uncertainty when evidence does not match.

        """
        if self.state.has_proof(reconcile_request):
            return command_results.CommandSucceeded(
                binding=reconcile_request.command.binding, document=reconcile_request.command.arguments,
            )
        return command_results.CommandOutcomeUnknown(
            binding=reconcile_request.command.binding, diagnostic=UNCERTAIN, receipt=reconcile_request.receipt,
        )


class OperationRawTransformer(processing.ExtensionRawTransformer):
    """Test event processing while the feature's live calls are active."""

    def transform(self, request: transforms.RawTransformRequest) -> raw_transforms.RawTransformResult:
        """Keep the immutable input while live jobs are active.

        Returns:
            An explicit keep operation for each original input.

        """
        return raw_transforms.RawTransformResult(operations=tuple(
            transforms.Keep(input_id=source.input_id) for source in request.inputs
        ))


@dataclass(frozen=True)
class OperationExample(
    plugin.ExtensionPlugin, lifecycle.ExtensionLifecycle, operations.ExtensionQueries,
):
    """Share feature state through typed capability objects."""

    host: host_services.ExtensionHostServices
    commands: OperationCommands

    @property
    def extension_info(self) -> lifecycle_models.ExtensionInfo:
        """The host-selected package identity."""
        return self.host.environment.extension_info

    @property
    def capabilities(self) -> plugin.ExtensionCapabilities:
        """The fixture's lifecycle, raw, query, and command protocols."""
        return plugin.ExtensionCapabilities(
            lifecycle=self, raw_transformer=OperationRawTransformer(), queries=self, commands=self.commands,
        )

    def activate(self, request: lifecycle_models.ActivationRequest) -> lifecycle_models.ActivationResult:
        """Confirm the selected runtime revision.

        Returns:
            The loaded fixture's readiness.

        """
        return lifecycle_models.ActivationReady(runtime_revision=request.runtime_revision)

    def deactivate(self, request: lifecycle_models.DeactivationRequest) -> lifecycle_models.DeactivationResult:
        """Finish the fixture after its test jobs have stopped.

        Returns:
            A complete stop result.

        """
        return lifecycle_models.DeactivationResult(runtime_revision=request.runtime_revision)

    def query(self, query_request: queries.QueryRequest) -> queries.QueryResult:
        """Read shared job state and a live peer catalog in one worker call.

        Returns:
            A typed text document with counts and a state revision.

        """
        if query_request.arguments.json_text == '"wait_for_active"':
            self.commands.state.started.wait(timeout=2)
        snapshot = self.commands.state.snapshot()
        peers = self.host.directory.list_extensions(DirectoryRequest(active_only=True))
        counts = f"executions:{snapshot[0]};active:{snapshot[1]}"
        counts = f"{counts};peers:{len(peers.entries)}"
        encoded = TypeAdapter(str).dump_json(counts).decode()
        document = query_request.arguments.model_copy(update={"json_text": encoded})
        return queries.QueryReady(
            binding=query_request.binding, document=document,
            snapshot=QuerySnapshot(state_revision=f"state-{snapshot[0]}-{snapshot[1]}"),
        )


def build_extension(services: host_services.ExtensionHostServices) -> plugin.ExtensionPlugin:
    """Build the external feature from public host services.

    Returns:
        A complete isolated test feature.

    """
    return OperationExample(services, OperationCommands(OperationState()))


FACTORY: plugin.ExtensionFactory = build_extension
