# Copyright (c) 2026 Zhambyl Yermagambet
"""Group typed storage for one ordered mixed interpretation writer."""

from dataclasses import dataclass

from repository.contract.interpretations import InterpretationRepository
from repository.contract.observations import ObservationRepository


@dataclass(frozen=True)
class InterpretationStores:
    """Use the same originals and canonical history as source ingestion and core readers."""

    observations: ObservationRepository
    facts: InterpretationRepository
