# Copyright (c) 2026 Zhambyl Yermagambet
"""Own original extension writes and ordered reads of both raw input branches."""

from typing import Protocol

from baqylau_extension_api.models.scopes import ExtensionScope

from domain.ids import RawEventId
from extensions.models.observations import ObservationAppend, ObservationAppendOutcome, StoredObservation


class ObservationRepository(Protocol):
    """Use one raw store and pending queue without weakening core record types."""

    def append_observations(self, request: ObservationAppend) -> ObservationAppendOutcome:
        """Validate and append all input, or roll back the whole request.

        Repeated source keys retain the first original row. Changed bytes or
        identity fields raise EventIdentityConflictError. No source checkpoint
        advances through this method.
        """
        ...

    def find_observation(self, raw_event_id: RawEventId) -> StoredObservation | None:
        """Read a core or extension observation by its unchanged raw identity."""
        ...

    def pending_observations(self, limit: int) -> tuple[StoredObservation, ...]:
        """Read both branches from the same durable queue in arrival order."""
        ...

    def observations_for_scope(
        self, scope: ExtensionScope, after_cursor: int, limit: int,
    ) -> tuple[StoredObservation, ...]:
        """Page original input for one exact scope by host arrival cursor."""
        ...
