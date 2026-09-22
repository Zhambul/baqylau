# Copyright (c) 2026 Zhambyl Yermagambet
"""Read a bounded source page sequence and accept each reply through one transaction."""

from collections.abc import Callable
from dataclasses import dataclass, replace

from baqylau_extension_api.models.source_results import SourceBatch, SourceReadFailed

from audit.failures import FailureContext
from extensions.models.source_processing import SourcePolicy, SourceProgress, SourceScopeKey
from extensions.models.source_reads import SourceKey, SourceReadCommit, SourceReadProposal
from extensions.source_calls import SourceCalls
from extensions.source_resources import SourceCallbacks
from repository.contract.source_reads import ExtensionSourceRepository


@dataclass(frozen=True)
class SourceReader:
    """Borrow calls inside one runtime and keep successful progress in SQLite."""

    calls: SourceCalls
    repository: ExtensionSourceRepository
    manager_id: str
    callbacks: SourceCallbacks
    policy: SourcePolicy

    def read(
        self, key: SourceScopeKey, progress: SourceProgress, stopped: Callable[[], bool],
        *, notified: bool,
    ) -> SourceProgress:
        """Keep unrelated sources and core stages available after a source failure.

        Returns:
            The next data-only schedule, including bounded continuation or retry.

        """
        if not progress.ready(self.callbacks.clock(), notified=notified):
            return progress
        for _ in range(self.policy.batches_per_source):
            if stopped():
                return progress
            try:
                progress = self._read_page(key, progress)
            except Exception:  # noqa: BLE001 -- Preserve the checkpoint and continue unrelated input.
                self.report_failure("read", key, progress.source.source_identity)
                return replace(progress, retry_at=self.callbacks.clock() + self.policy.retry_seconds)
            if not progress.has_more:
                return progress
        return progress

    def report_failure(self, operation: str, key: SourceScopeKey, source_identity: str | None = None) -> None:
        """Use the existing coalesced audit with owner and runtime context."""
        runtime = self.calls.provider.environment.runtime_revision
        self.callbacks.failures.record(f"extension source {operation}", FailureContext(
            source=f"{key.extension_id}@{runtime}", source_identity=source_identity,
        ))

    def _read_page(self, key: SourceScopeKey, progress: SourceProgress) -> SourceProgress:
        checkpoint = self.repository.source_checkpoint(SourceKey(
            extension_id=key.extension_id, scope=key.scope, source_identity=progress.source.source_identity,
        ))
        reply = self.calls.read(checkpoint, progress.source)
        if isinstance(reply.response, SourceReadFailed):
            code = reply.response.diagnostic.code
            message = f"extension source read failed: {code}"
            raise SourceReadError(message)
        self.repository.record_source_read(SourceReadCommit(
            proposal=SourceReadProposal(
                manager_id=self.manager_id, checkpoint=checkpoint, request=reply.request, response=reply.response,
            ), observed_at=self.callbacks.clock(),
        ))
        return self._progress(progress, reply.response)

    def _progress(self, progress: SourceProgress, response: SourceBatch) -> SourceProgress:
        due = response.next_due_at
        if response.has_more:
            due = self.callbacks.clock() + self.policy.continuation_seconds
        return replace(progress, read_once=True, has_more=response.has_more, next_due_at=due, retry_at=None)


class SourceReadError(RuntimeError):
    """Record the declared failure code without copying feature payloads into an error message."""
