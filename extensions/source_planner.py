# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate plans and release removed resources without discarding failed cleanup."""

from dataclasses import dataclass, replace

from extensions.models.source_processing import SourceScopePlan
from extensions.source_plan_state import replace_plan, requires_plan
from extensions.source_reader import SourceReader


@dataclass(frozen=True)
class SourcePlanner:
    """Keep complete source selection and acknowledged cleanup separate from reads."""

    reader: SourceReader

    def prepare(self, previous: SourceScopePlan, *, refresh: bool) -> SourceScopePlan:
        """Retain the last watch plan after failure and retry only at its deadline.

        Returns:
            A checked plan; a retry deadline prevents reads of a failed selection.

        """
        if not requires_plan(previous, self.reader.callbacks.clock(), refresh=refresh):
            return previous
        previous = self._release_pending(previous)
        if previous.release_pending:
            return previous
        try:
            selected = self.reader.calls.describe(previous.key.scope)
        except Exception:  # noqa: BLE001 -- Keep prior watch paths until a complete replacement is available.
            return self._failed(previous, "describe")
        return self._release_pending(replace_plan(previous, selected))

    def retire(self, previous: SourceScopePlan) -> SourceScopePlan | None:
        """Retain an unacknowledged whole-scope release for the next retry.

        Returns:
            No plan after release, or data-only cleanup state while release is pending.

        """
        previous = replace(previous, retiring=True)
        if previous.retry_at is not None and previous.retry_at > self.reader.callbacks.clock():
            return previous
        try:
            complete = self.reader.calls.release(previous.key.scope, None)
        except Exception:  # noqa: BLE001 -- A failed call is not proof that source resources stopped.
            return self._failed(previous, "release")
        return None if complete else self._retry(previous)

    def _release_pending(self, previous: SourceScopePlan) -> SourceScopePlan:
        while previous.release_pending:
            try:
                complete = self.reader.calls.release(previous.key.scope, previous.release_pending[0])
            except Exception:  # noqa: BLE001 -- Keep only source releases not already acknowledged.
                return self._failed(previous, "release")
            if not complete:
                return self._retry(previous)
            previous = replace(previous, release_pending=previous.release_pending[1:])
        return replace(previous, retry_at=None)

    def _failed(self, previous: SourceScopePlan, operation: str) -> SourceScopePlan:
        self.reader.report_failure(operation, previous.key)
        return self._retry(previous)

    def _retry(self, previous: SourceScopePlan) -> SourceScopePlan:
        retry_at = self.reader.callbacks.clock() + self.reader.policy.retry_seconds
        return replace(previous, retry_at=retry_at)
