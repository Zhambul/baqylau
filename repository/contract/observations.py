# Copyright (c) 2026 Zhambyl Yermagambet
"""Own original extension writes and ordered reads of both raw input branches."""

from typing import Protocol

from domain.ids import RawEventId
from extensions.models.observations import ObservationAppend, ObservationAppendOutcome, StoredObservation


class ObservationRepository(Protocol):
    """Use one raw store and pending queue without weakening core record types."""

    def find_observation(self, raw_event_id: RawEventId) -> StoredObservation | None:
        """Read a core or extension observation by its unchanged raw identity."""
        ...

    def append_observations(self, observation_append: ObservationAppend) -> ObservationAppendOutcome:
        """Validate and append one owner's new originals in one transaction."""
        ...

    def pending_observations(self, limit: int) -> tuple[StoredObservation, ...]:
        """Read both branches from the same durable queue in arrival order."""
        ...
